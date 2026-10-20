"""Multi-frame merges that give the COOLPIX L120 "new powers".

The camera can only fire the shutter and hand back full JPEGs (~4-5 s per shot,
auto-exposure, max 4 s shutter). Everything here runs on the Mac afterwards, on
lists of JPEG paths, and never touches the USB camera.

Functions (each returns a JSON-friendly dict summary):

    night_mode(paths, out)               handheld multi-frame noise reduction
    aligned_stack(paths, out, mode)      aligned lighten/mean/median/darken stack
    exposure_fusion(paths, out)          Mertens HDR-look fusion of bracketed frames
    panorama(paths, out)                 OpenCV stitcher + black-border crop
    super_resolution(paths, out, scale)  multi-frame upscaling
    deflicker_timelapse(folder, out_mp4) luminance-normalised H.264 timelapse

CLI:  python -m l120.lab.merge <command> <inputs...> -o <out>   (see --help)

Memory: a 14 MP frame is 42 MB as uint8 and 168 MB as float32. Aligned frames
are written once to a uint8 memory-mapped temp file and merged in horizontal
strips, so peak RAM stays around a few hundred MB regardless of frame count.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ALIGN_SIDE = 1200          # long side (px) of the grayscale proxy used for alignment
STRIP_ROWS = 256           # rows per strip when merging the memmapped stack
JPEG_QUALITY = 95
IMG_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


# --------------------------------------------------------------------------- I/O

def _load(path) -> np.ndarray:
    """Read an image as BGR uint8 (OpenCV order)."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"cannot read image: {path}")
    return img


def _exif(path):
    try:
        return Image.open(path).info.get("exif")
    except Exception:
        return None


def _save(img_bgr: np.ndarray, out, exif_from=None) -> str:
    """Save BGR uint8; JPEGs keep the reference frame's EXIF (camera model, date)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".jpg", ".jpeg"):
        exif = _exif(exif_from) if exif_from else None
        kw = {"exif": exif} if exif else {}
        Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)).save(out, quality=JPEG_QUALITY, **kw)
    else:
        cv2.imwrite(str(out), img_bgr)
    return str(out)


def _to_u8(x: np.ndarray) -> np.ndarray:
    return np.clip(x + 0.5, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ measurements

def noise_sigma(img: np.ndarray, block=64, pct=10) -> float:
    """Noise estimate (DN, 0-255): std of the high-pass luminance in the flattest blocks.

    The image is split into 64x64 blocks; the 10th-percentile block std of
    (luma - Gaussian blur σ=2) is reported, so edges and texture mostly don't
    count. Lower = cleaner. Compare images of the same scene only.
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = g.astype(np.float32)
    hp = g - cv2.GaussianBlur(g, (0, 0), 2)
    h, w = (g.shape[0] // block) * block, (g.shape[1] // block) * block
    blocks = hp[:h, :w].reshape(h // block, block, w // block, block).std(axis=(1, 3))
    lum = g[:h, :w].reshape(h // block, block, w // block, block).mean(axis=(1, 3))
    usable = blocks[(lum > 10) & (lum < 245)]          # skip crushed blacks / clipped whites
    return float(np.percentile(usable if usable.size else blocks, pct))


def sharpness(img: np.ndarray) -> float:
    """Variance of the Laplacian on luminance. Higher = more fine detail (noise counts too)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    return float("inf") if mse == 0 else 10 * math.log10(255 ** 2 / mse)


# --------------------------------------------------------------------- alignment

def _proxy(img: np.ndarray, side=ALIGN_SIDE):
    """Downscaled grayscale proxy + the factor full_res / proxy."""
    h, w = img.shape[:2]
    s = max(h, w) / side if max(h, w) > side else 1.0
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if s > 1:
        g = cv2.resize(g, (round(w / s), round(h / s)), interpolation=cv2.INTER_AREA)
    return g, s


def _scale_h(H: np.ndarray, s: float) -> np.ndarray:
    """Convert a homography estimated on proxies (factor s smaller) to full resolution."""
    S = np.diag([s, s, 1.0])
    return S @ H @ np.linalg.inv(S)


def _orb_homography(ref_g, mov_g):
    """Feature-based estimate. Returns (H ref->mov or None, inlier count)."""
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))   # helps on dark L120 frames
    orb = cv2.ORB_create(nfeatures=5000, fastThreshold=7)
    k1, d1 = orb.detectAndCompute(clahe.apply(ref_g), None)
    k2, d2 = orb.detectAndCompute(clahe.apply(mov_g), None)
    if d1 is None or d2 is None or len(k1) < 12 or len(k2) < 12:
        return None, 0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [m[0] for m in matches if len(m) == 2 and m[0].distance < 0.8 * m[1].distance]
    if len(good) < 12:
        return None, len(good)
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    H, inl = cv2.findHomography(p1, p2, cv2.RANSAC, 3.0)
    n = int(inl.sum()) if inl is not None else 0
    return (H if n >= 12 else None), n


def _ecc(ref_g, mov_g, H0, iters=100, eps=1e-6):
    """Photometric refinement (ECC, homography). Returns (H ref->mov, correlation) or (H0, None)."""
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, eps)
    try:
        cc, H = cv2.findTransformECC(ref_g.astype(np.float32), mov_g.astype(np.float32),
                                     H0.astype(np.float32), cv2.MOTION_HOMOGRAPHY, crit, None, 5)
        return H.astype(np.float64), float(cc)
    except cv2.error:
        return H0, None


def _ncc(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    a = a[mask].astype(np.float32)
    b = b[mask].astype(np.float32)
    if a.size < 100:
        return 0.0
    a -= a.mean()
    b -= b.mean()
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def _detail(g: np.ndarray) -> np.ndarray:
    """Band-pass (DoG) image: keeps edges/texture, drops lighting and gradients.

    NCC on this is far more discriminative than NCC on raw intensity, which
    any dark-top/bright-bottom frame scores ~0.9 on.
    """
    g = g.astype(np.float32)
    return cv2.GaussianBlur(g, (0, 0), 1.0) - cv2.GaussianBlur(g, (0, 0), 4.0)


def _plausible(H: np.ndarray, w: int, h: int) -> bool:
    """Reject wild homographies: a handheld re-shot should be a small motion."""
    A = H[:2, :2] / H[2, 2]
    sx, sy = np.linalg.svd(A, compute_uv=False)
    persp = abs(H[2, 0]) * w + abs(H[2, 1]) * h
    c = H @ np.array([w / 2, h / 2, 1.0])
    shift = math.hypot(c[0] / c[2] - w / 2, c[1] / c[2] - h / 2)
    return bool(0.85 < sy <= sx < 1.18 and persp < 0.15 and shift < 0.25 * max(w, h))


def align_to(ref_g, mov_g, refine_g=None):
    """Estimate H (ref coords -> mov coords, proxy scale) and quality info.

    ORB+RANSAC gives a robust initial guess even for large shifts; ECC then
    refines it to sub-pixel accuracy. If ORB fails (dark, featureless frames),
    ECC starts from identity, which works for small motion only.
    """
    H, inliers = _orb_homography(ref_g, mov_g)
    method = "orb+ecc" if H is not None else "ecc"
    H, cc = _ecc(ref_g, mov_g, H if H is not None else np.eye(3))
    if cc is None and method == "orb+ecc":
        method = "orb"
    h, w = ref_g.shape
    warped = cv2.warpPerspective(mov_g, H, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    valid = cv2.warpPerspective(np.full_like(mov_g, 255), H, (w, h),
                                flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
    ncc = _ncc(ref_g, warped, valid)
    dncc = _ncc(_detail(ref_g), _detail(warped), cv2.erode(valid.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    c = H @ np.array([w / 2, h / 2, 1.0])
    info = {"method": method, "orb_inliers": inliers, "ecc_cc": None if cc is None else round(cc, 4),
            "ncc": round(ncc, 4), "detail_ncc": round(dncc, 4), "shift_px_proxy": round(math.hypot(c[0] / c[2] - w / 2, c[1] / c[2] - h / 2), 2),
            "rotation_deg": round(math.degrees(math.atan2(H[1, 0], H[0, 0])), 3),
            "overlap": round(float(valid.mean()), 4), "plausible": _plausible(H, w, h)}
    return H, info


def _pick_reference(proxies) -> int:
    """Sharpest frame among those that look like the rest of the burst.

    Sharpness alone is unsafe: an accidental shot of a different, more
    textured scene would win. So we first keep frames whose heavily blurred
    64 px thumbnail is close to the burst's median thumbnail, then take the
    one with the highest Laplacian variance (least motion blur).
    """
    if len(proxies) <= 2:
        return int(np.argmax([cv2.Laplacian(g, cv2.CV_32F).var() for g in proxies]))
    th = [cv2.GaussianBlur(cv2.resize(g, (64, 48), interpolation=cv2.INTER_AREA).astype(np.float32), (0, 0), 2)
          for g in proxies]
    med = np.median(np.stack(th), axis=0)
    dist = np.array([float(np.abs(t - med).mean()) for t in th])
    cand = np.where(dist <= 1.5 * np.median(dist) + 1.0)[0]
    sharp = [cv2.Laplacian(proxies[i], cv2.CV_32F).var() for i in cand]
    return int(cand[int(np.argmax(sharp))])


_GAMMA = 2.2


def _match_exposure(img: np.ndarray, ref: np.ndarray, sample=300_000):
    """Per-channel gain in (approximately) linear light so `img` matches `ref`'s brightness.

    Uses well-exposed pixels (both values in 8..245) and applies the gain via a
    256-entry LUT, so no float copy of the frame is needed.
    """
    rng = np.random.default_rng(0)
    h, w = ref.shape[:2]
    ys, xs = rng.integers(0, h, sample), rng.integers(0, w, sample)
    a, b = img[ys, xs].astype(np.float32), ref[ys, xs].astype(np.float32)
    v = np.arange(256, dtype=np.float64) / 255
    lin = v ** _GAMMA
    out, gains = img.copy(), []
    for c in range(3):
        ok = (a[:, c] > 8) & (a[:, c] < 245) & (b[:, c] > 8) & (b[:, c] < 245)
        if ok.sum() < 1000:
            gains.append(1.0)
            continue
        g = float(lin[b[ok, c].astype(int)].sum() / max(lin[a[ok, c].astype(int)].sum(), 1e-6))
        g = float(np.clip(g, 0.25, 4.0))
        gains.append(round(g, 3))
        lut = np.clip(np.clip(lin * g, 0, 1) ** (1 / _GAMMA) * 255 + 0.5, 0, 255).astype(np.uint8)
        out[..., c] = cv2.LUT(img[..., c], lut)
    return out, gains


def _align_stack(paths, ref_idx=None, min_ncc=0.5, refine_side=None, tmpdir=None, match_exposure=True):
    """Align every frame to a reference and write them to a uint8 memmap.

    Returns (memmap [N,H,W,3] with reference first, used_paths, per-frame report,
    reference path, fraction of border pixels filled from the reference).
    Rejected frames are not written. Border pixels a warped frame does not cover
    are filled from the reference, so merges never see black edges.
    """
    paths = [str(p) for p in paths]
    proxies, scale = [], 1.0
    for p in paths:
        g, scale = _proxy(_load(p))
        proxies.append(g)
    if ref_idx is None:
        ref_idx = _pick_reference(proxies)
    ref = _load(paths[ref_idx])
    H_img, W_img = ref.shape[:2]
    order = [ref_idx] + [i for i in range(len(paths)) if i != ref_idx]

    report, keep = [], []
    for i in order:
        if i == ref_idx:
            report.append({"frame": Path(paths[i]).name, "reference": True, "accepted": True})
            keep.append((i, np.eye(3)))
            continue
        H, info = align_to(proxies[ref_idx], proxies[i])
        Hf = _scale_h(H, scale)
        if refine_side:  # optional second ECC pass at higher resolution (used by super-resolution)
            rg, rs = _proxy(ref, refine_side)
            mg, _ = _proxy(_load(paths[i]), refine_side)
            Hr, cc2 = _ecc(rg, mg, _scale_h(Hf, 1 / rs), iters=60, eps=1e-7)
            if cc2 is not None:
                Hf = _scale_h(Hr, rs)
                info["ecc_cc_refined"] = round(cc2, 4)
        ok = info["plausible"] and info["detail_ncc"] >= min_ncc
        info.update(frame=Path(paths[i]).name, accepted=bool(ok))
        if not ok:
            info["why_rejected"] = "implausible motion" if not info["plausible"] else f"detail_ncc<{min_ncc}"
        report.append(info)
        if ok:
            keep.append((i, Hf))

    tmpdir = Path(tmpdir or tempfile.mkdtemp(prefix="l120lab-"))
    mm = np.lib.format.open_memmap(tmpdir / "stack.npy", mode="w+", dtype=np.uint8,
                                   shape=(len(keep), H_img, W_img, 3))
    filled = 0.0
    for k, (i, Hf) in enumerate(keep):
        if i == ref_idx:
            mm[k] = ref
            continue
        src = _load(paths[i])
        warped = cv2.warpPerspective(src, Hf, (W_img, H_img),
                                     flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT)
        valid = cv2.warpPerspective(np.full(src.shape[:2], 255, np.uint8), Hf, (W_img, H_img),
                                    flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
        valid = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0  # drop interpolated edge
        np.copyto(warped, ref, where=~valid[..., None])
        filled = max(filled, float(1 - valid.mean()))
        if match_exposure:  # the L120 re-meters every shot; equalise brightness before merging
            warped, gains = _match_exposure(warped, ref)
            next(r for r in report if r["frame"] == Path(paths[i]).name)["exposure_gain_bgr"] = gains
        mm[k] = warped
    mm.flush()
    return mm, [paths[i] for i, _ in keep], report, paths[ref_idx], round(filled, 4)


def _cleanup(mm):
    p = Path(mm.filename)
    del mm
    shutil.rmtree(p.parent, ignore_errors=True)


# ------------------------------------------------------------------------ merging

def _noise_floor(mm, sample=400_000) -> float:
    """Per-frame noise sigma (DN) from differences between aligned frames and the reference."""
    n = mm.shape[0]
    if n < 2:
        return 2.0
    rng = np.random.default_rng(0)
    H, W = mm.shape[1:3]
    ys, xs = rng.integers(0, H, sample), rng.integers(0, W, sample)
    ref = mm[0][ys, xs].astype(np.float32)
    sig = []
    for k in range(1, n):
        d = mm[k][ys, xs].astype(np.float32) - ref
        sig.append(1.4826 * np.median(np.abs(d - np.median(d))) / math.sqrt(2))
    return float(max(np.median(sig), 1.0))


def _merge(mm, mode: str, k_sigma=2.5, floor=None) -> np.ndarray:
    """Merge a [N,H,W,3] uint8 stack strip by strip.

    modes: mean, median, lighten, darken, clipped (sigma-clipped mean around the
    per-pixel median; sigma = max(1.4826*MAD, global noise floor) so it also
    works with only 3 frames, where per-pixel statistics are meaningless).
    """
    n, H, W, C = mm.shape
    out = np.empty((H, W, C), np.uint8)
    if mode == "clipped" and floor is None:
        floor = _noise_floor(mm)
    for y0 in range(0, H, STRIP_ROWS):
        y1 = min(H, y0 + STRIP_ROWS)
        if mode == "lighten":
            out[y0:y1] = mm[:, y0:y1].max(axis=0)
            continue
        if mode == "darken":
            out[y0:y1] = mm[:, y0:y1].min(axis=0)
            continue
        d = mm[:, y0:y1].astype(np.float32)
        if mode == "mean":
            r = d.mean(axis=0)
        elif mode == "median":
            r = np.median(d, axis=0)
        elif mode == "clipped":
            med = np.median(d, axis=0)
            dev = np.abs(d - med)
            sigma = np.maximum(1.4826 * np.median(dev, axis=0), floor)
            keep = dev <= k_sigma * sigma
            cnt = keep.sum(axis=0)
            r = np.where(cnt > 0, (d * keep).sum(axis=0) / np.maximum(cnt, 1), med)
        else:
            raise ValueError(f"unknown merge mode {mode}")
        out[y0:y1] = _to_u8(r)
    return out


def _unsharp(img: np.ndarray, amount=0.4, radius=1.2) -> np.ndarray:
    """Mild unsharp mask, strip-free but in float32 (≈170 MB for 14 MP)."""
    if amount <= 0:
        return img
    blur = cv2.GaussianBlur(img, (0, 0), radius)
    return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)


# ------------------------------------------------------------------ 1. night mode

def night_mode(paths, out, sharpen=0.4, min_ncc=0.5, k_sigma=2.5) -> dict:
    """Handheld multi-frame noise reduction, like a phone night mode.

    1. Pick the sharpest frame as reference.
    2. Align every other frame to it (ORB homography on a ~1200 px grayscale
       proxy, refined by ECC), then warp the full-resolution frame.
    3. Reject frames whose motion is implausible or whose aligned correlation
       (NCC) with the reference is below `min_ncc`.
    4. Merge with a sigma-clipped mean (outliers such as moving people, cars,
       ghosts are dropped per pixel), then apply a mild unsharp mask.

    When to use: dim scenes where the L120's 4 s cap / high ISO leaves noise,
    shot handheld or on a wobbly surface. Shoot 5-10 frames; gain ~sqrt(N)
    in theory, less on the L120 because its JPEG engine already smooths noise.
    """
    t0 = time.time()
    mm, used, report, ref_path, filled = _align_stack(paths, min_ncc=min_ncc)
    try:
        floor = _noise_floor(mm)
        merged = _merge(mm, "clipped", k_sigma=k_sigma, floor=floor)
        ref_img = np.array(mm[0])
    finally:
        _cleanup(mm)
    result = _unsharp(merged, sharpen)
    _save(result, out, exif_from=ref_path)
    return {"output": str(out), "frames_in": len(paths), "frames_merged": len(used), "reference": Path(ref_path).name,
            "noise_floor_dn": round(floor, 2), "border_filled_from_ref": filled,
            "noise_ref": round(noise_sigma(ref_img), 3), "noise_merged": round(noise_sigma(merged), 3),
            "noise_out_sharpened": round(noise_sigma(result), 3),
            "sharpness_ref": round(sharpness(ref_img), 1), "sharpness_out": round(sharpness(result), 1),
            "alignment": report, "seconds": round(time.time() - t0, 1)}


# --------------------------------------------------------------- 2. aligned stack

def aligned_stack(paths, out, mode="lighten", min_ncc=0.5, align=True) -> dict:
    """Align frames to the first one, then merge with lighten / mean / median / darken.

    Improves on l120.features.stack (which merges unaligned frames): a nudged
    camera no longer doubles edges. For star trails keep the first frame as the
    reference (we align on the foreground; stars still move = trails).
    mode: lighten (light/star trails), mean (smooth water, less noise),
          median (remove passers-by), darken (remove bright moving things).
    align=False reproduces the old behaviour for comparison.
    """
    if mode not in ("lighten", "mean", "median", "darken"):
        raise ValueError("mode must be lighten, mean, median or darken")
    t0 = time.time()
    if align:
        mm, used, report, ref_path, filled = _align_stack(paths, ref_idx=0, min_ncc=min_ncc)
    else:
        first = _load(paths[0])
        tmp = Path(tempfile.mkdtemp(prefix="l120lab-"))
        mm = np.lib.format.open_memmap(tmp / "stack.npy", mode="w+", dtype=np.uint8, shape=(len(paths),) + first.shape)
        for k, p in enumerate(paths):
            mm[k] = first if k == 0 else _load(p)
        used, report, ref_path, filled = [str(p) for p in paths], [], str(paths[0]), 0.0
    try:
        merged = _merge(mm, mode)
    finally:
        _cleanup(mm)
    _save(merged, out, exif_from=ref_path)
    rg, _ = _proxy(_load(ref_path))
    og, _ = _proxy(merged)
    ghost = _ncc(_detail(rg), _detail(og), np.ones(rg.shape, bool))
    return {"output": str(out), "mode": mode, "aligned": align, "frames_in": len(paths), "frames_merged": len(used),
            "border_filled_from_ref": filled, "detail_ncc_vs_reference": round(ghost, 4),
            "alignment": report, "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------- 3. exposure fusion

def exposure_fusion(paths, out, align=True, contrast=1.0, saturation=1.0, exposure=1.0) -> dict:
    """Mertens exposure fusion: HDR look without an HDR file or tone mapping.

    Shoot the same scene at different exposure-compensation settings (e.g.
    -2, 0, +2 EV via the L120's multi-selector between shots). Frames are
    translation-aligned with median-threshold bitmaps (cv2.AlignMTB, robust to
    exposure differences), then fused by per-pixel weights for contrast,
    saturation and well-exposedness. Needs all frames in RAM: ~1.5-2 GB peak
    for three 14 MP frames.
    """
    t0 = time.time()
    imgs = [_load(p) for p in paths]
    shifts = []
    if align and len(imgs) > 1:
        # (AlignMTB.process() can't fill a Python list, so apply the shifts ourselves)
        mtb = cv2.createAlignMTB(max_bits=6, exclude_range=4, cut=True)
        ref_g = cv2.cvtColor(imgs[len(imgs) // 2], cv2.COLOR_BGR2GRAY)
        for k, im in enumerate(imgs):
            sh = mtb.calculateShift(ref_g, cv2.cvtColor(im, cv2.COLOR_BGR2GRAY))
            shifts.append([int(sh[0]), int(sh[1])])
            imgs[k] = mtb.shiftMat(im, sh)
        # crop to the area every shifted frame covers (shiftMat fills the rest with black)
        xs, ys = [d[0] for d in shifts], [d[1] for d in shifts]
        h, w = imgs[0].shape[:2]
        x0, x1 = max(0, max(xs)), w + min(0, min(xs))
        y0, y1 = max(0, max(ys)), h + min(0, min(ys))
        imgs = [im[y0:y1, x0:x1] for im in imgs]
    means = [round(float(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).mean()), 1) for im in imgs]
    fused = cv2.createMergeMertens(contrast, saturation, exposure).process(imgs)
    del imgs
    res = _to_u8(fused * 255)
    _save(res, out, exif_from=paths[len(paths) // 2])
    clip = lambda im: round(float(((im <= 2) | (im >= 253)).all(axis=2).mean() * 100), 2)  # noqa: E731
    mid = _load(paths[len(paths) // 2])
    return {"output": str(out), "frames": len(paths), "mtb_shifts_xy_to_middle": shifts, "frame_mean_luma": means,
            "clipped_pct_middle_frame": clip(mid), "clipped_pct_fused": clip(res),
            "seconds": round(time.time() - t0, 1)}


# -------------------------------------------------------------------- 4. panorama

def _crop_black(img: np.ndarray, thresh=1):
    """Largest axis-aligned rectangle without empty (black) pixels, found by greedy edge shrinking."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    s = max(g.shape) / 1000 if max(g.shape) > 1000 else 1.0
    mask = (cv2.resize(g, (round(g.shape[1] / s), round(g.shape[0] / s)), interpolation=cv2.INTER_NEAREST) > thresh)
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
    ys, xs = np.where(mask)
    t, b, l, r = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    while t < b and l < r:
        sub = ~mask[t:b, l:r]
        if not sub.any():
            break
        bad = {"t": sub[0].mean(), "b": sub[-1].mean(), "l": sub[:, 0].mean(), "r": sub[:, -1].mean()}
        e = max(bad, key=bad.get)
        if e == "t": t += 1
        elif e == "b": b -= 1
        elif e == "l": l += 1
        else: r -= 1
    t, b, l, r = (int(math.ceil(t * s)) + 1, int(b * s) - 1, int(math.ceil(l * s)) + 1, int(r * s) - 1)
    return img[t:b, l:r], (l, t, r - l, b - t)


def panorama(paths, out, mode="panorama", work_megapix=0.6, crop=True) -> dict:
    """Stitch overlapping frames with cv2.Stitcher, then crop the black borders.

    Shoot by rotating the camera about its lens (not walking sideways), with
    ~30-40 % overlap and a fixed exposure if possible (the L120 is auto-only,
    so expect some seam blending to do the work). mode="panorama" assumes a
    rotating camera (spherical warp); mode="scans" suits flat subjects shot
    by sliding the camera (documents, murals). If panorama mode fails, scans
    mode is tried automatically.
    """
    t0 = time.time()
    imgs = [_load(p) for p in paths]
    tried = []
    pano = None
    for m in ([mode, "scans"] if mode == "panorama" else [mode]):
        st = cv2.Stitcher_create(cv2.Stitcher_PANORAMA if m == "panorama" else cv2.Stitcher_SCANS)
        st.setRegistrationResol(work_megapix)
        status, pano = st.stitch(imgs)
        tried.append({"mode": m, "status": int(status), "ok": status == cv2.Stitcher_OK})
        if status == cv2.Stitcher_OK:
            break
        pano = None
    del imgs
    if pano is None:
        return {"output": None, "error": "stitching failed (need more overlap / texture)", "tried": tried,
                "seconds": round(time.time() - t0, 1)}
    raw_shape = pano.shape[:2]
    rect = None
    if crop:
        pano, rect = _crop_black(pano)
    _save(pano, out, exif_from=paths[0])
    return {"output": str(out), "frames": len(paths), "tried": tried, "stitched_hw": list(raw_shape),
            "cropped_hw": list(pano.shape[:2]), "crop_rect_xywh": rect, "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------ 5. super-resolution

def super_resolution(paths, out, scale=2, sharpen=0.6, min_ncc=0.5, roi=None) -> dict:
    """Simple multi-frame super-resolution ("shift-and-add").

    Each frame is aligned to the sharpest one (ORB + ECC, then a second ECC
    pass at ~2400 px for sub-pixel accuracy) and warped *directly* onto an
    upscaled grid with Lanczos interpolation; the warped frames are averaged
    and lightly sharpened. Real gain needs genuine sub-pixel jitter between
    shots (handheld is better than a tripod here) and ideally 6+ frames.
    roi=(x, y, w, h) in source pixels limits work to a crop (a full 14 MP x2
    result is 56 MP: ~2.2 GB peak RAM measured).
    Reports sharpness/noise vs. a single Lanczos upscale of the reference.
    """
    t0 = time.time()
    paths = [str(p) for p in paths]
    proxies, s = [], 1.0
    for p in paths:
        g, s = _proxy(_load(p))
        proxies.append(g)
    ref_idx = _pick_reference(proxies)
    ref = _load(paths[ref_idx])
    rg, rs = _proxy(ref, 2400)
    x0, y0, w, h = roi or (0, 0, ref.shape[1], ref.shape[0])
    W2, H2 = w * scale, h * scale
    # output pixel (u,v) -> reference pixel ((u+0.5)/scale - 0.5 + x0, ...)
    U = np.array([[1 / scale, 0, (0.5 / scale - 0.5) + x0], [0, 1 / scale, (0.5 / scale - 0.5) + y0], [0, 0, 1]])
    acc = np.zeros((H2, W2, 3), np.float32)
    n, report = 0, []
    for i, p in enumerate(paths):
        if i == ref_idx:
            Hf, info = np.eye(3), {"frame": Path(p).name, "reference": True, "accepted": True}
        else:
            H, info = align_to(proxies[ref_idx], proxies[i])
            mg, _ = _proxy(_load(p), 2400)
            Hr, cc2 = _ecc(rg, mg, _scale_h(_scale_h(H, s), 1 / rs), iters=80, eps=1e-7)
            Hf = _scale_h(Hr, rs) if cc2 is not None else _scale_h(H, s)
            info["ecc_cc_refined"] = None if cc2 is None else round(cc2, 4)
            ok = info["plausible"] and info["detail_ncc"] >= min_ncc
            info.update(frame=Path(p).name, accepted=bool(ok))
            c = Hf @ np.array([ref.shape[1] / 2, ref.shape[0] / 2, 1.0])
            info["subpixel_offset_xy"] = [round((c[0] / c[2] - ref.shape[1] / 2) % 1, 3),
                                          round((c[1] / c[2] - ref.shape[0] / 2) % 1, 3)]
            if not ok:
                report.append(info)
                continue
        src = ref if i == ref_idx else _load(p)
        M = Hf @ U  # output grid -> source frame pixels
        acc += cv2.warpPerspective(src, M, (W2, H2), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
                                   borderMode=cv2.BORDER_REFLECT)
        n += 1
        report.append(info)
    acc /= n
    sr = _unsharp(_to_u8(acc), sharpen, radius=1.0 * scale)
    del acc
    single = cv2.resize(ref[y0:y0 + h, x0:x0 + w], (W2, H2), interpolation=cv2.INTER_LANCZOS4)
    single_sharp = _unsharp(single, sharpen, radius=1.0 * scale)
    _save(sr, out, exif_from=paths[ref_idx])
    return {"output": str(out), "scale": scale, "frames_used": n, "out_wh": [W2, H2],
            "sharpness_sr": round(sharpness(sr), 1), "sharpness_single_upscale_same_sharpen": round(sharpness(single_sharp), 1),
            "noise_sr": round(noise_sigma(sr), 3), "noise_single_upscale_same_sharpen": round(noise_sigma(single_sharp), 3),
            "alignment": report, "seconds": round(time.time() - t0, 1)}


# --------------------------------------------------------- 6. deflicker timelapse

def _frame_list(folder):
    folder = Path(folder)
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXTS and not p.name.startswith("."))


def deflicker_timelapse(folder, out_mp4, fps=24, window=9, width=1920, crf=18, gamma=2.2, frames=None) -> dict:
    """Remove auto-exposure flicker from a timelapse and render H.264.

    The L120 meters every frame itself, so brightness jumps frame to frame.
    For each frame we measure mean luminance in linear light (on a small
    proxy), take a centred rolling median over `window` frames as the target
    (smoothed again by a running mean; slow changes like a sunset are kept,
    jitter is removed), and apply the
    gain in linear light with a per-frame 256-entry lookup table (cheap, no
    float copies of the image). Frames are written to a temp folder, scaled
    to `width` and encoded with ffmpeg/libx264 (yuv420p, crf 18).
    """
    t0 = time.time()
    files = [Path(f) for f in frames] if frames else _frame_list(folder)
    if not files:
        raise FileNotFoundError(f"no images in {folder}")
    v = np.arange(256, dtype=np.float64) / 255
    lin = v ** gamma
    means = []
    for f in files:
        g, _ = _proxy(_load(f), 400)
        means.append(float(lin[g].mean()))
    means = np.array(means)
    half = window // 2
    med = np.array([np.median(means[max(0, i - half):i + half + 1]) for i in range(len(means))])
    # a running median of a jittery series still steps; a box mean on top smooths those steps
    target = np.array([med[max(0, i - half):i + half + 1].mean() for i in range(len(med))])
    gains = target / np.maximum(means, 1e-6)
    tmp = Path(tempfile.mkdtemp(prefix="l120lab-dfk-"))
    after = []
    try:
        for i, (f, gn) in enumerate(zip(files, gains)):
            lut = _to_u8(np.clip(lin * gn, 0, 1) ** (1 / gamma) * 255).reshape(256, 1)
            img = cv2.LUT(_load(f), lut)
            g, _ = _proxy(img, 400)
            after.append(float(lin[g].mean()))
            if width and img.shape[1] > width:
                img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1] / 2) * 2),
                                 interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(tmp / f"f_{i:06d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        out_mp4 = Path(out_mp4)
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error",
                        "-framerate", str(fps), "-i", str(tmp / "f_%06d.jpg"),
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-crf", str(crf),
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_mp4)], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    def flicker(m):  # mean absolute frame-to-frame change, in % of mean level
        m = np.asarray(m)
        return round(float(np.abs(np.diff(m)).mean() / m.mean() * 100), 2) if len(m) > 1 else 0.0

    return {"output": str(out_mp4), "frames": len(files), "fps": fps, "window": window,
            "flicker_before_pct": flicker(means), "flicker_after_pct": flicker(after),
            "gain_range": [round(float(gains.min()), 3), round(float(gains.max()), 3)],
            "seconds": round(time.time() - t0, 1)}


# -------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m l120.lab.merge", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_, inputs="images"):
        p = sub.add_parser(name, help=help_)
        p.add_argument("inputs", nargs="+", help=inputs)
        p.add_argument("-o", "--out", required=True)
        return p

    p = add("night", "handheld multi-frame noise reduction")
    p.add_argument("--sharpen", type=float, default=0.4)
    p.add_argument("--min-ncc", type=float, default=0.5)
    p = add("stack", "aligned lighten/mean/median/darken stack")
    p.add_argument("--mode", default="lighten", choices=["lighten", "mean", "median", "darken"])
    p.add_argument("--no-align", action="store_true")
    add("fuse", "Mertens exposure fusion of EV-bracketed frames")
    p = add("pano", "panorama stitch with black-border crop")
    p.add_argument("--mode", default="panorama", choices=["panorama", "scans"])
    p = add("sr", "multi-frame super-resolution")
    p.add_argument("--scale", type=int, default=2)
    p.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    p = add("deflicker", "deflickered H.264 timelapse", inputs="a folder of frames (or the frames themselves)")
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--window", type=int, default=9)
    p.add_argument("--width", type=int, default=1920)
    a = ap.parse_args(argv)

    if a.cmd == "night":
        r = night_mode(a.inputs, a.out, sharpen=a.sharpen, min_ncc=a.min_ncc)
    elif a.cmd == "stack":
        r = aligned_stack(a.inputs, a.out, a.mode, align=not a.no_align)
    elif a.cmd == "fuse":
        r = exposure_fusion(a.inputs, a.out)
    elif a.cmd == "pano":
        r = panorama(a.inputs, a.out, mode=a.mode)
    elif a.cmd == "sr":
        r = super_resolution(a.inputs, a.out, scale=a.scale, roi=tuple(a.roi) if a.roi else None)
    else:
        if len(a.inputs) == 1 and Path(a.inputs[0]).is_dir():
            r = deflicker_timelapse(a.inputs[0], a.out, fps=a.fps, window=a.window, width=a.width)
        else:
            r = deflicker_timelapse(None, a.out, fps=a.fps, window=a.window, width=a.width, frames=a.inputs)
    print(json.dumps(r, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


if __name__ == "__main__":
    main()
