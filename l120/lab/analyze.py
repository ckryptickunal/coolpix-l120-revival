"""Post-shot analysis, culling, EXIF date repair and usage stats for COOLPIX L120 JPEGs.

Everything here works on files already on the Mac; nothing talks to the camera.

    python -m l120.lab.analyze report  PHOTO.JPG [...]            --out DIR
    python -m l120.lab.analyze cull    FOLDER_OR_FILES [...]       --out DIR
    python -m l120.lab.analyze dates   FOLDER_OR_FILES [...]       --out DIR
    python -m l120.lab.analyze fix-dates FILES... --out DIR --reference F --true "2019-05-04 14:30:00" [--apply]
    python -m l120.lab.analyze fix-dates FILES... --out DIR --offset +2954d14h [--apply]
    python -m l120.lab.analyze fix-dates --plan DIR/date_plan.json --out DIR [--apply]
    python -m l120.lab.analyze stats   FOLDER_OR_FILES [...]       --out DIR

Date repair NEVER modifies originals: it copies each file into --out and patches
only the fixed-width date strings inside the copy's EXIF block (no re-encode, the
MakerNote and image data stay byte-identical). Without --apply it is a dry run.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import os
import re
import struct
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import piexif
from PIL import Image

CROP_FACTOR = 5.56            # 1/2.3" sensor: 4.5–94.5 mm -> 25–525 mm equiv.
STD_LONG_EDGE = 2160          # sharpness is measured at this size (half of 14MP width)
CLOCK_EPOCH = datetime(2011, 1, 1, 0, 0, 0)   # L120 clock after a battery-swap reset
JPEG_EXT = {".jpg", ".jpeg"}
EXIF_DT = "%Y:%m:%d %H:%M:%S"

# Calibrated on real L120 frames (see docs/lab/ANALYZE.md); sharpness = Laplacian variance @2160px.
BLUR_THRESHOLD = 60.0
SOFT_THRESHOLD = 150.0


# --------------------------------------------------------------------------- helpers

def _num(v):
    if isinstance(v, tuple) and len(v) == 2:
        return v[0] / v[1] if v[1] else None
    return v


def _s(v):
    if isinstance(v, bytes):
        return v.split(b"\x00")[0].decode("ascii", "replace").strip()
    return v


def parse_exif_dt(v) -> datetime | None:
    """EXIF date string -> datetime, or None for unset ('0000:00:00 00:00:00') / garbage."""
    s = _s(v) if v is not None else ""
    if not s or s.startswith("0000"):
        return None
    try:
        return datetime.strptime(s[:19], EXIF_DT)
    except ValueError:
        return None


def clock_state(dt: datetime | None) -> str:
    """'unset' (0000 dates), 'reset' (clock restarted at 2011-01-01), or 'plausible'."""
    if dt is None:
        return "unset"
    if CLOCK_EPOCH <= dt < CLOCK_EPOCH + timedelta(days=31):
        return "reset"
    return "plausible"


def file_number(path: Path) -> int | None:
    m = re.match(r"^DSC[NF_]?(\d{4})", path.name, re.I)
    return int(m.group(1)) if m else None


def expand_inputs(items, exclude: list[Path] | None = None) -> list[Path]:
    exclude = [e.resolve() for e in (exclude or [])]
    out: list[Path] = []
    seen: set[Path] = set()
    for it in items:
        p = Path(it).expanduser()
        cands = sorted(q for q in p.rglob("*") if q.is_file()) if p.is_dir() else [p]
        for q in cands:
            if q.suffix.lower() not in JPEG_EXT or q.name.startswith("._"):
                continue
            rq = q.resolve()
            if any(rq == e or e in rq.parents for e in exclude):
                continue
            if rq not in seen:
                seen.add(rq)
                out.append(q)
    return out


def read_exif(path) -> dict:
    """Readable EXIF summary (never raises; missing fields are None)."""
    try:
        e = piexif.load(str(path))
    except Exception as ex:  # noqa: BLE001 - non-EXIF JPEGs are fine
        return {"error": f"no EXIF: {ex}"}
    z, x = e.get("0th", {}), e.get("Exif", {})
    exp = x.get(33434)
    fnum = _num(x.get(33437))
    focal = _num(x.get(37386))
    f35 = x.get(41989) or (round(focal * CROP_FACTOR) if focal else None)
    flash = x.get(37385)
    dto = parse_exif_dt(x.get(36867))
    return {
        "make": _s(z.get(271)), "model": _s(z.get(272)), "software": _s(z.get(305)),
        "datetime_original_raw": _s(x.get(36867)),
        "datetime_original": dto.isoformat(sep=" ") if dto else None,
        "clock_state": clock_state(dto),
        "exposure_time_s": _num(exp) if exp else None,
        "shutter": (f"1/{round(exp[1] / exp[0])}" if exp and exp[0] and exp[1] / exp[0] >= 1
                    else (f"{_num(exp):g}s" if exp else None)),
        "f_number": round(fnum, 1) if fnum else None,
        "iso": x.get(34855),
        "focal_length_mm": round(focal, 1) if focal else None,
        "focal_length_35mm": f35,
        "zoom_x": round(focal / 4.5, 1) if focal else None,
        "exposure_bias_ev": _num(x.get(37380)),
        "exposure_program": {0: "n/a", 1: "manual", 2: "auto/program", 3: "aperture-priority",
                             4: "shutter-priority", 5: "creative", 6: "action", 7: "portrait",
                             8: "landscape"}.get(x.get(34850), x.get(34850)),
        "metering": {1: "average", 2: "center-weighted", 3: "spot", 4: "multi-spot",
                     5: "matrix", 6: "partial"}.get(x.get(37383), x.get(37383)),
        "flash_raw": flash,
        "flash_fired": bool(flash & 1) if flash is not None else None,
        "flash": _flash_text(flash),
        "scene": {0: "standard", 1: "landscape", 2: "portrait", 3: "night"}.get(x.get(41990), x.get(41990)),
        "white_balance": {0: "auto", 1: "manual"}.get(x.get(41987), x.get(41987)),
        "digital_zoom": _num(x.get(41988)) or None,
        "orientation": z.get(274),
    }


def _flash_text(v):
    if v is None:
        return None
    mode = {0: "", 1: "forced on", 2: "forced off", 3: "auto"}[(v >> 3) & 3]
    parts = ["fired" if v & 1 else "did not fire"]
    if mode:
        parts.append(mode)
    if v & 0x40:
        parts.append("red-eye")
    if v & 0x20:
        parts.append("no flash function")
    return ", ".join(parts)


def _load_rgb(path, max_long: int | None = None) -> np.ndarray:
    im = Image.open(path)
    if max_long:
        im.draft("RGB", (max_long, max_long))   # fast DCT-domain downscale
    im = im.convert("RGB")
    if max_long and max(im.size) > max_long:
        im.thumbnail((max_long, max_long), Image.LANCZOS)
    return np.asarray(im)


def _gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def sharpness(gray: np.ndarray) -> dict:
    """Variance of Laplacian at a fixed 2160px long edge, so numbers compare across sizes.

    `global` is the whole-frame value; `subject` is the 90th-percentile of a 6x8 tile
    grid, which stays high when only the subject (not the background) is in focus.
    """
    h, w = gray.shape
    s = STD_LONG_EDGE / max(h, w)
    if s < 1:
        gray = cv2.resize(gray, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    th, tw = lap.shape[0] // 6, lap.shape[1] // 8
    tiles = [lap[r * th:(r + 1) * th, c * tw:(c + 1) * tw].var() for r in range(6) for c in range(8)]
    return {"global": round(float(lap.var()), 1), "subject": round(float(np.percentile(tiles, 90)), 1)}


def sharpness_label(sub: float) -> str:
    return "blurry" if sub < BLUR_THRESHOLD else ("soft" if sub < SOFT_THRESHOLD else "sharp")


def noise_sigma(gray: np.ndarray) -> float | None:
    """Visible grain: std-dev (grey levels, 0-255) of the fine-detail residual
    (image minus a sigma-2 Gaussian blur) in the flattest 10% of a 32x32 tile grid.
    Clipped tiles are skipped because they look falsely noise-free. In-camera noise
    reduction + JPEG lower this, so treat it as relative (compare shots), not absolute."""
    g = gray.astype(np.float32)
    hp = g - cv2.GaussianBlur(g, (0, 0), 2)
    th, tw = g.shape[0] // 32, g.shape[1] // 32
    vals = []
    for i in range(32):
        for j in range(32):
            t = gray[i * th:(i + 1) * th, j * tw:(j + 1) * tw]
            if t.min() <= 4 or t.max() >= 251:
                continue
            vals.append(float(hp[i * th:(i + 1) * th, j * tw:(j + 1) * tw].std()))
    if not vals:
        return None
    vals.sort()
    return round(float(np.mean(vals[: max(1, len(vals) // 10)])), 2)


def exposure_stats(rgb: np.ndarray) -> dict:
    luma = _gray(rgb)
    n = luma.size
    blown_any = (rgb.max(axis=2) >= 254)
    stats = {
        "mean_luma": round(float(luma.mean()), 1),
        "highlights_clipped_pct": round(100 * float((luma >= 250).sum()) / n, 2),
        "any_channel_clipped_pct": round(100 * float(blown_any.sum()) / n, 2),
        "shadows_clipped_pct": round(100 * float((luma <= 5).sum()) / n, 2),
    }
    stats["verdict"] = exposure_verdict(stats)
    return stats


def exposure_verdict(s: dict) -> str:
    if s["highlights_clipped_pct"] > 3 and s["mean_luma"] < 110:
        return "high contrast (highlights blown)"
    if s["highlights_clipped_pct"] > 3 or s["mean_luma"] > 200:
        return "overexposed"
    if s["mean_luma"] < 35 or (s["shadows_clipped_pct"] > 25 and s["mean_luma"] < 70):
        return "underexposed"
    if s["highlights_clipped_pct"] > 0.5:
        return "ok (some highlights blown)"
    return "ok"


# --------------------------------------------------------------------------- 1. shot report

def _histogram_png(rgb: np.ndarray, dest: Path, w=512, h=200):
    luma = _gray(rgb)
    hists = [np.bincount(rgb[..., c].ravel(), minlength=256) for c in range(3)]
    hl = np.bincount(luma.ravel(), minlength=256)
    peak = max(np.percentile(np.concatenate(hists + [hl]), 99.5), 1)
    canvas = np.full((h, w, 3), 24, np.float32)
    xs = np.arange(w) * 256 // w
    for gx in (64, 128, 192):                               # quarter gridlines
        canvas[:, gx * w // 256] = 55
    yy = np.arange(h)[:, None]
    for c, hc in enumerate(hists):                           # R, G, B as additive fills (overlap -> grey)
        v = np.minimum(hc[xs] / peak, 1) * (h - 1)
        canvas[..., c] += np.where((h - 1 - yy) <= v[None, :], 165, 0)
    lv = (h - 1 - np.minimum(hl[xs] / peak, 1) * (h - 1)).astype(int)
    for dx in range(w):                                      # luma as a white line
        lo = lv[dx - 1] if dx else lv[0]
        a, b = sorted((lo, lv[dx]))
        canvas[a:b + 2, dx] = 255
    canvas[:, :3] = [255, 60, 60] if hl[:6].sum() / luma.size > 0.01 else canvas[:, :3]
    canvas[:, -3:] = [255, 60, 60] if hl[250:].sum() / luma.size > 0.005 else canvas[:, -3:]
    Image.fromarray(canvas.clip(0, 255).astype(np.uint8)).save(dest)


def _zebra(rgb: np.ndarray, dest: Path, width=1440):
    """Diagonal red/black stripes on blown highlights, blue stripes on crushed shadows."""
    h, w = rgb.shape[:2]
    s = width / w
    size = (width, round(h * s))
    small = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    blown = cv2.resize((rgb.max(axis=2) >= 254).astype(np.float32), size, interpolation=cv2.INTER_AREA) > 0.35
    crushed = cv2.resize((_gray(rgb) <= 5).astype(np.float32), size, interpolation=cv2.INTER_AREA) > 0.5
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    stripe = ((xx + yy) // 6) % 2 == 0
    stripe2 = ((xx - yy) // 6) % 2 == 0
    out = small.copy()
    out[blown & stripe] = [255, 0, 0]
    out[blown & ~stripe] = [0, 0, 0]
    out[crushed & stripe2] = [40, 110, 255]
    Image.fromarray(out.astype(np.uint8)).save(dest, quality=88)
    return int(blown.sum()), int(crushed.sum())


def shot_report(path, out_dir=None) -> dict:
    """CHDK-style post-shot feedback. Writes <stem>_hist.png, <stem>_zebra.jpg, <stem>_report.json."""
    path = Path(path)
    out_dir = Path(out_dir) if out_dir else path.parent / "lab-report"
    out_dir.mkdir(parents=True, exist_ok=True)
    rgb = _load_rgb(path)
    gray = _gray(rgb)
    exif = read_exif(path)
    sh = sharpness(_gray(_load_rgb(path, STD_LONG_EDGE)))   # same decode path as cull()
    rep = {
        "file": str(path),
        "size_px": [rgb.shape[1], rgb.shape[0]],
        "exposure": exposure_stats(rgb),
        "sharpness": {**sh, "label": sharpness_label(sh["subject"])},
        "noise_sigma": noise_sigma(gray),
        "exif": exif,
    }
    stem = path.stem
    hist_p, zebra_p, json_p = out_dir / f"{stem}_hist.png", out_dir / f"{stem}_zebra.jpg", out_dir / f"{stem}_report.json"
    _histogram_png(rgb, hist_p)
    _zebra(rgb, zebra_p)
    rep["paths"] = {"histogram": str(hist_p), "zebra": str(zebra_p), "json": str(json_p)}
    rep["summary"] = _one_line(rep)
    json_p.write_text(json.dumps(rep, indent=2))
    return rep


def _one_line(r):
    e, x = r["exposure"], r["exif"]
    return (f"{Path(r['file']).name}: {x.get('shutter')} f/{x.get('f_number')} ISO {x.get('iso')} "
            f"{x.get('focal_length_35mm')}mm-eq, flash {x.get('flash')} | {e['verdict']}, "
            f"blown {e['highlights_clipped_pct']}% crushed {e['shadows_clipped_pct']}% | "
            f"sharpness {r['sharpness']['subject']} ({r['sharpness']['label']}) | noise σ {r['noise_sigma']}")


# --------------------------------------------------------------------------- 2. cull

def phash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(small)[:8, :8].ravel()[1:]
    bits = d > np.median(d)
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _thumb_b64(rgb: np.ndarray, size=360) -> str:
    im = Image.fromarray(rgb)
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def cull(folder_or_paths, out_dir, burst_gap_s: float = 2.0, dup_threshold: int = 6,
         burst_hash_threshold: int = 12) -> dict:
    """Rank photos, flag blurry / badly exposed / near-duplicate, pick best per burst.

    Bursts: consecutive shots <= burst_gap_s apart (when the camera clock is valid) or,
    when timestamps are unusable, consecutive in file order with pHash distance <=
    burst_hash_threshold. Near-duplicates: any pair with pHash distance <= dup_threshold.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = folder_or_paths if isinstance(folder_or_paths, (list, tuple)) else [folder_or_paths]
    paths = expand_inputs(items, exclude=[out_dir])
    recs = []
    for p in paths:
        rgb = _load_rgb(p, STD_LONG_EDGE)
        gray = _gray(rgb)
        ex = read_exif(p)
        sh = sharpness(gray)
        es = exposure_stats(rgb)
        recs.append({"file": str(p), "name": p.name, "exif": ex, "sharpness": sh,
                     "sharp_label": sharpness_label(sh["subject"]), "exposure": es,
                     "phash": phash(gray), "number": file_number(p),
                     "dt": parse_exif_dt(ex.get("datetime_original_raw")),
                     "mtime": p.stat().st_mtime, "thumb": _thumb_b64(rgb)})
    # order: valid camera time first, else file number, else mtime
    recs.sort(key=lambda r: (r["dt"] or datetime.max, r["number"] if r["number"] is not None else 1 << 30,
                             r["mtime"], r["name"]))
    # union-find over burst + duplicate links
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(a)] = find(b)

    for i in range(1, len(recs)):
        a, b = recs[i - 1], recs[i]
        d = hamming(a["phash"], b["phash"])
        if a["dt"] and b["dt"]:
            if abs((b["dt"] - a["dt"]).total_seconds()) <= burst_gap_s and d <= burst_hash_threshold * 2:
                union(i - 1, i)
        elif d <= burst_hash_threshold:
            union(i - 1, i)
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            if hamming(recs[i]["phash"], recs[j]["phash"]) <= dup_threshold:
                union(i, j)
    for r in recs:
        flags = []
        if r["sharp_label"] == "blurry":
            flags.append("blurry")
        elif r["sharp_label"] == "soft":
            flags.append("soft")
        v = r["exposure"]["verdict"]
        if v in ("overexposed", "underexposed"):
            flags.append(v)
        r["flags"] = flags
        pen = {"overexposed": 1.5, "underexposed": 1.5}.get(v, 0) + r["exposure"]["highlights_clipped_pct"] * 0.1
        r["score"] = round(math.log10(max(r["sharpness"]["subject"], 1)) * 10 - pen * 3, 2)
    groups: dict[int, list[int]] = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(i)
    out_groups = []
    for gid, (root, idx) in enumerate(sorted(groups.items(), key=lambda kv: min(kv[1]))):
        best = max(idx, key=lambda i: recs[i]["score"])
        for i in idx:
            r = recs[i]
            r["group"] = gid
            r["group_size"] = len(idx)
            r["best_in_group"] = i == best
            if len(idx) > 1 and i != best:
                r["flags"].append("duplicate")
                r["duplicate_of"] = recs[best]["name"]
            fatal = {"blurry", "overexposed", "underexposed"} & set(r["flags"])
            r["decision"] = ("dupe" if (len(idx) > 1 and i != best) else
                             "review" if fatal else "keep")
        out_groups.append({"group": gid, "files": [recs[i]["name"] for i in idx], "best": recs[best]["name"]})
    for r in recs:
        r["phash"] = f"{r['phash']:016x}"
        r["datetime"] = r.pop("dt").isoformat(sep=" ") if r["dt"] else None
    result = {"count": len(recs), "groups": out_groups,
              "summary": dict(Counter(r["decision"] for r in recs)),
              "photos": [{k: v for k, v in r.items() if k not in ("thumb", "mtime")} for r in recs]}
    jp, hp = out_dir / "cull.json", out_dir / "contact_sheet.html"
    jp.write_text(json.dumps(result, indent=2, default=str))
    hp.write_text(_contact_sheet_html(recs, result))
    result["paths"] = {"json": str(jp), "html": str(hp)}
    return result


_BASE_CSS = """
:root{--bg:#f6f5f2;--fg:#1c1c1c;--muted:#6b6b6b;--card:#fff;--line:#e2e0da;--keep:#1f8a4c;--review:#b7791f;--reject:#c0392b;--accent:#2b6cb0}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#ececec;--muted:#9a9a9a;--card:#1f1f1f;--line:#333;--accent:#7fb3ff}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}.muted{color:var(--muted)}
main{max-width:1200px;margin:0 auto}
"""


def _contact_sheet_html(recs, result):
    cards = []
    for r in recs:
        badges = "".join(f'<span class="b {f}">{f}</span>' for f in r["flags"])
        if r["best_in_group"] and r["group_size"] > 1:
            badges = '<span class="b best">best of group</span>' + badges
        x = r["exif"]
        cards.append(f"""<figure class="c {r['decision']}"><img src="data:image/jpeg;base64,{r['thumb']}" alt="">
<figcaption><div class="top"><b>{html.escape(r['name'])}</b><span class="d {r['decision']}">{r['decision']}</span></div>
<div class="muted">{html.escape(str(Path(r['file']).parent.name))} · group {r['group']} ({r['group_size']})</div>
<div>{x.get('shutter')} · f/{x.get('f_number')} · ISO {x.get('iso')} · {x.get('focal_length_35mm')}mm-eq</div>
<div>sharp {r['sharpness']['subject']:.0f} · luma {r['exposure']['mean_luma']:.0f} · blown {r['exposure']['highlights_clipped_pct']}% · score {r['score']}</div>
<div class="badges">{badges}</div></figcaption></figure>""")
    s = result["summary"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>L120 Contact Sheet</title><style>{_BASE_CSS}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-top:16px}}
.c{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
.c.dupe{{opacity:.55}}.c img{{width:100%;display:block;aspect-ratio:4/3;object-fit:cover;background:#000}}
figcaption{{padding:8px 10px;font-size:12px}}.top{{display:flex;justify-content:space-between;font-size:13px}}
.d{{font-weight:600;text-transform:uppercase;font-size:11px}}.d.keep{{color:var(--keep)}}.d.review{{color:var(--review)}}.d.dupe{{color:var(--muted)}}
.badges{{margin-top:4px}}.b{{display:inline-block;padding:1px 6px;margin:2px 4px 0 0;border-radius:10px;font-size:11px;border:1px solid var(--line)}}
.b.blurry,.b.overexposed,.b.underexposed{{background:var(--reject);color:#fff;border-color:transparent}}
.b.soft{{background:var(--review);color:#fff;border-color:transparent}}.b.duplicate{{color:var(--muted)}}
.b.best{{background:var(--keep);color:#fff;border-color:transparent}}
</style></head><body><main><h1>Contact sheet</h1>
<div class="muted">{result['count']} photos · {s.get('keep', 0)} keep · {s.get('review', 0)} review · {s.get('dupe', 0)} dupe · {len(result['groups'])} groups.
Sharpness = Laplacian variance at {STD_LONG_EDGE}px (blurry &lt; {BLUR_THRESHOLD:.0f}, soft &lt; {SOFT_THRESHOLD:.0f}).</div>
<div class="grid">{''.join(cards)}</div></main></body></html>"""


# --------------------------------------------------------------------------- 3. dates

_DATE_TAGS = {("0th", 306): "DateTime (ModifyDate)", ("Exif", 36867): "DateTimeOriginal",
              ("Exif", 36868): "DateTimeDigitized (CreateDate)"}


def _exif_date_offsets(data: bytes) -> dict:
    """Locate the 20-byte ASCII date fields inside the JPEG's EXIF APP1. Returns {(ifd, tag): file_offset}."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise ValueError("bad JPEG marker structure")
        marker = data[pos + 1]
        if marker in (0xD9, 0xDA):
            break
        seglen = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\x00\x00":
            tiff = pos + 10
            bo = "<" if data[tiff:tiff + 2] == b"II" else ">"
            seg_end = pos + 2 + seglen
            found = {}

            def walk(ifd_off, ifd_name):
                base = tiff + ifd_off
                n = struct.unpack(bo + "H", data[base:base + 2])[0]
                sub = None
                for k in range(n):
                    e = base + 2 + 12 * k
                    tag, typ, cnt, val = struct.unpack(bo + "HHII", data[e:e + 12])
                    if (ifd_name, tag) in _DATE_TAGS and typ == 2 and cnt == 20:
                        off = tiff + val
                        if off + 20 > seg_end:
                            raise ValueError("date field outside EXIF segment")
                        found[(ifd_name, tag)] = off
                    if ifd_name == "0th" and tag == 34665:
                        sub = val
                return sub

            ifd0 = struct.unpack(bo + "I", data[tiff + 4:tiff + 8])[0]
            exif_ifd = walk(ifd0, "0th")
            if exif_ifd:
                walk(exif_ifd, "Exif")
            return found
        pos += 2 + seglen
    return {}


def _camera_dt(path) -> datetime | None:
    try:
        return parse_exif_dt(piexif.load(str(path))["Exif"].get(36867))
    except Exception:  # noqa: BLE001
        return None


def parse_offset(s) -> timedelta:
    """'+2954d14h3m', '-90m', '3600' (seconds) -> timedelta."""
    if isinstance(s, timedelta):
        return s
    if isinstance(s, (int, float)):
        return timedelta(seconds=s)
    t = str(s).strip()
    sign = -1 if t.startswith("-") else 1
    t = t.lstrip("+-")
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return sign * timedelta(seconds=float(t))
    m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if not m or not any(m.groups()):
        raise ValueError(f"cannot parse offset {s!r} (use e.g. +2954d14h30m or seconds)")
    d, h, mi, se = (int(g or 0) for g in m.groups())
    return sign * timedelta(days=d, hours=h, minutes=mi, seconds=se)


def _parse_true(s) -> datetime:
    if isinstance(s, datetime):
        return s
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt)
        except ValueError:
            pass
    raise ValueError(f"cannot parse datetime {s!r} (use 'YYYY-MM-DD HH:MM:SS', local time)")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _plan_one(p: Path, new_dt: datetime | None, reason: str) -> dict:
    cam = _camera_dt(p)
    return {"source": str(p), "camera_datetime": cam.isoformat(sep=" ") if cam else None,
            "new_datetime": new_dt.isoformat(sep=" ") if new_dt else None, "reason": reason}


def _write_fixed_copies(plan: list[dict], out_dir: Path, apply: bool, overwrite: bool) -> dict:
    out_dir = Path(out_dir).expanduser().resolve()
    srcs = [Path(e["source"]).resolve() for e in plan]
    for s in srcs:
        if s.parent == out_dir:
            raise ValueError(f"output folder {out_dir} contains originals; choose a separate folder")
    names = Counter(Path(e["source"]).name for e in plan)
    results = []
    if apply:
        out_dir.mkdir(parents=True, exist_ok=True)
    for e, src in zip(plan, srcs):
        name = src.name if names[src.name] == 1 else f"{src.parent.name}__{src.name}"
        dest = out_dir / name
        r = dict(e, dest=str(dest))
        if e["new_datetime"] is None:
            r["status"] = "skipped"
            results.append(r)
            continue
        if dest.resolve() == src:
            raise ValueError("refusing to write over an original")
        if dest.exists() and not overwrite:
            r["status"] = "skipped: destination exists (use --overwrite)"
            results.append(r)
            continue
        orig = src.read_bytes()
        offs = _exif_date_offsets(orig)
        if ("Exif", 36867) not in offs:
            r["status"] = "skipped: no DateTimeOriginal field to patch"
            results.append(r)
            continue
        new_b = datetime.fromisoformat(e["new_datetime"]).strftime(EXIF_DT).encode() + b"\x00"
        buf = bytearray(orig)
        for off in offs.values():
            buf[off:off + 20] = new_b
        r["patched_fields"] = [_DATE_TAGS[k] for k in offs]
        r["source_sha256"] = _sha256(orig)
        if not apply:
            r["status"] = "dry-run"
            results.append(r)
            continue
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(bytes(buf))
        # verify: only the date bytes changed, EXIF parses, new date reads back, original untouched
        chk = tmp.read_bytes()
        diff = [i for i in range(len(orig)) if orig[i] != chk[i]] if len(chk) == len(orig) else None
        allowed = {o + k for o in offs.values() for k in range(20)}
        ok = (diff is not None and set(diff) <= allowed
              and _camera_dt(tmp) == datetime.fromisoformat(e["new_datetime"])
              and _sha256(src.read_bytes()) == r["source_sha256"])
        if not ok:
            tmp.unlink()
            r["status"] = "FAILED verification (no file written)"
            results.append(r)
            continue
        os.replace(tmp, dest)
        ts = datetime.fromisoformat(e["new_datetime"]).timestamp()
        os.utime(dest, (ts, ts))
        r["dest_sha256"] = _sha256(chk)
        r["status"] = "written"
        results.append(r)
    summary = dict(Counter(r["status"] for r in results))
    rep = {"applied": apply, "out_dir": str(out_dir), "summary": summary, "files": results}
    if apply:
        mp = out_dir / f"fix_dates_manifest_{datetime.now():%Y%m%d-%H%M%S}.json"
        mp.write_text(json.dumps(rep, indent=2))
        rep["manifest"] = str(mp)
    return rep


def fix_dates(paths, *, out_dir, reference_file=None, true_datetime=None, offset=None,
              sequence_step_s: float | None = None, apply: bool = False, overwrite: bool = False) -> dict:
    """Shift DateTimeOriginal / CreateDate / ModifyDate in COPIES written to out_dir.

    Give either `offset` (e.g. '+2954d14h') or `reference_file` + `true_datetime`
    (the real local time that reference photo was taken; offset = true - camera clock).
    Files whose camera date is unset (0000:00:00) can't be shifted; they are skipped
    unless `sequence_step_s` is given, in which case they get true_datetime +
    (file_number - reference_number) * step — an estimate, and marked as such.
    Dry run unless apply=True. Originals are only ever read.
    """
    paths = expand_inputs(paths if isinstance(paths, (list, tuple)) else [paths])
    if (offset is None) == (true_datetime is None):
        raise ValueError("give exactly one of offset or (reference_file + true_datetime)")
    ref_dt = ref_num = true = None
    if offset is not None:
        delta = parse_offset(offset)
    else:
        if reference_file is None:
            raise ValueError("true_datetime needs reference_file")
        true = _parse_true(true_datetime)
        ref_dt = _camera_dt(reference_file)
        ref_num = file_number(Path(reference_file))
        delta = (true - ref_dt) if ref_dt else None
        if delta is None and sequence_step_s is None:
            raise ValueError(f"{reference_file} has no camera date (unset clock), so no offset can be derived. "
                             "Use sequence_step_s to assign estimated times by file number, or give an offset.")
    plan = []
    for p in paths:
        cam = _camera_dt(p)
        if cam is not None and delta is not None:
            plan.append(_plan_one(p, cam + delta, f"camera clock {'+' if delta >= timedelta(0) else ''}{delta}"))
        elif cam is None and sequence_step_s is not None and true is not None:
            n = file_number(p)
            if n is None or ref_num is None:
                plan.append(_plan_one(p, None, "unset clock and no DSCN file number to sequence by"))
            else:
                plan.append(_plan_one(p, true + timedelta(seconds=(n - ref_num) * sequence_step_s),
                                      f"ESTIMATE: unset clock, sequenced by file number ({sequence_step_s:g}s/frame)"))
        else:
            plan.append(_plan_one(p, None, "unset camera clock; cannot shift" if cam is None else "no offset"))
    return _write_fixed_copies(plan, out_dir, apply, overwrite)


def guess_date_ranges(folder_or_paths, out_dir=None, backward_tolerance_s: float = 60) -> dict:
    """Group photos into camera-clock runs so each run can be given a real start date.

    A new run starts when the clock state changes (unset / reset-to-2011 / plausible),
    when the camera clock jumps backwards (a reset happened between shots), or when the
    DSCN file number goes backwards (new card / folder). Writes date_plan.json: fill in
    `true_start` (real local time of the run's FIRST photo) per group, then run
    fix-dates --plan.
    """
    items = folder_or_paths if isinstance(folder_or_paths, (list, tuple)) else [folder_or_paths]
    paths = expand_inputs(items, exclude=[Path(out_dir)] if out_dir else None)
    recs = []
    for p in paths:
        dt = _camera_dt(p)
        recs.append({"path": p, "num": file_number(p), "dt": dt, "state": clock_state(dt),
                     "mtime": p.stat().st_mtime})
    have_nums = recs and all(r["num"] is not None for r in recs)
    recs.sort(key=(lambda r: (r["num"], r["path"].name)) if have_nums else (lambda r: (r["mtime"], r["path"].name)))
    groups, cur = [], []
    for r in recs:
        if cur:
            prev = cur[-1]
            last_dt = next((x["dt"] for x in reversed(cur) if x["dt"]), None)
            brk = (r["state"] != prev["state"]
                   or (r["dt"] and last_dt and (last_dt - r["dt"]).total_seconds() > backward_tolerance_s)
                   or (have_nums and r["num"] < prev["num"]))
            if brk:
                groups.append(cur)
                cur = []
        cur.append(r)
    if cur:
        groups.append(cur)
    hint = {"unset": "camera clock was never set: dates are 0000:00:00. Only an absolute date (estimated) can be assigned.",
            "reset": "clock restarted at 2011-01-01 00:00 after a battery swap: give the real time of the first photo and the rest keep their relative spacing.",
            "plausible": "clock looks set; probably fine (check timezone)."}
    out = []
    for i, g in enumerate(groups):
        dts = [r["dt"] for r in g if r["dt"]]
        out.append({"group": i, "count": len(g), "clock_state": g[0]["state"],
                    "first_file": g[0]["path"].name, "last_file": g[-1]["path"].name,
                    "file_numbers": [g[0]["num"], g[-1]["num"]] if have_nums else None,
                    "camera_first": dts[0].isoformat(sep=" ") if dts else None,
                    "camera_last": dts[-1].isoformat(sep=" ") if dts else None,
                    "camera_span": str(dts[-1] - dts[0]) if dts else None,
                    "hint": hint[g[0]["state"]],
                    "true_start": None,
                    "sequence_step_s": 60 if g[0]["state"] == "unset" else None,
                    "files": [str(r["path"]) for r in g]})
    res = {"ordered_by": "file number" if have_nums else "file mtime", "groups": out,
           "instructions": "Set true_start ('YYYY-MM-DD HH:MM:SS', local) for each group you know; leave null to skip. "
                           "For 'unset' groups, times are estimated as true_start + index*sequence_step_s. "
                           "Then: python -m l120.lab.analyze fix-dates --plan date_plan.json --out FOLDER [--apply]"}
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        pp = Path(out_dir) / "date_plan.json"
        pp.write_text(json.dumps(res, indent=2))
        res["plan_path"] = str(pp)
    return res


def fix_dates_from_plan(plan_path, *, out_dir, apply=False, overwrite=False) -> dict:
    """Apply a filled-in date_plan.json (from guess_date_ranges) to COPIES in out_dir."""
    data = json.loads(Path(plan_path).read_text())
    plan = []
    for g in data["groups"]:
        files = [Path(f) for f in g["files"]]
        if not g.get("true_start"):
            plan += [_plan_one(f, None, f"group {g['group']}: no true_start given") for f in files]
            continue
        true = _parse_true(g["true_start"])
        first_cam = next((d for d in (_camera_dt(f) for f in files) if d), None)
        for k, f in enumerate(files):
            cam = _camera_dt(f)
            if cam and first_cam:
                plan.append(_plan_one(f, true + (cam - first_cam), f"group {g['group']}: offset {true - first_cam}"))
            elif g.get("sequence_step_s"):
                plan.append(_plan_one(f, true + timedelta(seconds=k * float(g["sequence_step_s"])),
                                      f"group {g['group']}: ESTIMATE, unset clock, {g['sequence_step_s']}s/frame"))
            else:
                plan.append(_plan_one(f, None, f"group {g['group']}: unset clock and no sequence_step_s"))
    return _write_fixed_copies(plan, out_dir, apply, overwrite)


# --------------------------------------------------------------------------- 4. lens stats

def _svg_bars(data: list[tuple[str, int]], title: str, unit: str = "photos", w=560, bar_h=22) -> str:
    if not data:
        return f'<section><h2>{html.escape(title)}</h2><p class="muted">No data.</p></section>'
    mx = max(v for _, v in data) or 1
    lab_w, h = 110, bar_h * len(data) + 8
    rows = []
    for i, (k, v) in enumerate(data):
        y = 4 + i * bar_h
        bw = (w - lab_w - 60) * v / mx
        rows.append(f'<text x="{lab_w - 8}" y="{y + bar_h * .68:.1f}" text-anchor="end" class="l">{html.escape(str(k))}</text>'
                    f'<rect x="{lab_w}" y="{y + 3}" width="{max(bw, 1 if v else 0):.1f}" height="{bar_h - 6}" rx="3" class="bar"/>'
                    f'<text x="{lab_w + bw + 6:.1f}" y="{y + bar_h * .68:.1f}" class="v">{v}</text>')
    return (f'<section><h2>{html.escape(title)}</h2><svg viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'aria-label="{html.escape(title)}">{"".join(rows)}</svg></section>')


def lens_stats(folder_or_paths, out_dir) -> dict:
    """Usage analytics across a collection (EXIF only, fast). Writes lens_stats.json + lens_stats.html."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = folder_or_paths if isinstance(folder_or_paths, (list, tuple)) else [folder_or_paths]
    paths = expand_inputs(items, exclude=[out_dir])
    rows = [read_exif(p) for p in paths]
    rows = [r for r in rows if "error" not in r]
    bins = [(25, 28, "25–27 (wide)"), (28, 35, "28–34"), (35, 50, "35–49"), (50, 85, "50–84 (normal)"),
            (85, 135, "85–134 (portrait)"), (135, 250, "135–249"), (250, 400, "250–399"), (400, 10_000, "400–525 (max)")]
    focal = Counter()
    for r in rows:
        f = r.get("focal_length_35mm")
        if f:
            for lo, hi, lab in bins:
                if lo <= f < hi or (lo == 25 and f < 25):
                    focal[lab] += 1
    iso = Counter(r.get("iso") for r in rows if r.get("iso"))
    shut = Counter(r.get("shutter") for r in rows if r.get("shutter"))
    flash = Counter("fired" if r.get("flash_fired") else "not fired" for r in rows if r.get("flash_fired") is not None)
    scene = Counter(r.get("scene") for r in rows)
    clock = Counter(r.get("clock_state") for r in rows)
    hours = Counter()
    for r in rows:
        if r.get("clock_state") == "plausible":
            h = datetime.fromisoformat(r["datetime_original"]).hour
            hours[("night 0–5" if h < 6 else "morning 6–11" if h < 12 else "afternoon 12–16" if h < 17
                   else "evening 17–20" if h < 21 else "night 21–23")] += 1
    zooms = [r["zoom_x"] for r in rows if r.get("zoom_x")]
    stats = {
        "photos": len(rows), "skipped_no_exif": len(paths) - len(rows),
        "focal_35mm_bins": {lab: focal[lab] for _, _, lab in bins},
        "zoom_x": {"mean": round(float(np.mean(zooms)), 2) if zooms else None,
                   "at_wide_end_pct": round(100 * sum(z <= 1.05 for z in zooms) / len(zooms), 1) if zooms else None,
                   "at_tele_end_pct": round(100 * sum(z >= 20 for z in zooms) / len(zooms), 1) if zooms else None},
        "iso": dict(sorted(iso.items())), "shutter": dict(shut.most_common()), "flash": dict(flash),
        "scene": dict(scene), "clock_state": dict(clock), "time_of_day": dict(hours),
    }
    order_tod = ["morning 6–11", "afternoon 12–16", "evening 17–20", "night 21–23", "night 0–5"]

    def _shut_key(s):
        return 1 / float(s[2:]) if s.startswith("1/") else float(s.rstrip("s"))

    secs = [
        _svg_bars([(lab, focal[lab]) for _, _, lab in bins], "Focal length (35mm-equivalent, mm)"),
        _svg_bars([(f"ISO {k}", v) for k, v in sorted(iso.items())], "ISO"),
        _svg_bars(sorted(shut.items(), key=lambda kv: _shut_key(kv[0])), "Shutter speed"),
        _svg_bars(list(flash.items()), "Flash"),
        _svg_bars(list(scene.items()), "Scene capture type (EXIF)"),
        _svg_bars([(k, hours[k]) for k in order_tod if k in hours] if hours else [],
                  "Time of day (only photos with a plausible camera clock)"),
        _svg_bars(list(clock.items()), "Camera clock state"),
    ]
    z = stats["zoom_x"]
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>L120 Lens Stats</title><style>{_BASE_CSS}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 16px 12px;margin-top:12px;max-width:640px}}
.bar{{fill:var(--accent)}}.l,.v{{font-size:12px;fill:var(--fg)}}.v{{fill:var(--muted)}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}}.k{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px}}
.k b{{display:block;font-size:20px}}</style></head><body><main><h1>Lens &amp; usage stats</h1>
<div class="muted">COOLPIX L120 · 4.5–94.5 mm (×{CROP_FACTOR} = 25–525 mm equiv.) · {stats['photos']} photos</div>
<div class="kpis"><div class="k"><b>{stats['photos']}</b>photos</div><div class="k"><b>{z['mean'] if z['mean'] else '–'}×</b>mean zoom</div>
<div class="k"><b>{z['at_wide_end_pct'] if z['at_wide_end_pct'] is not None else '–'}%</b>at widest</div>
<div class="k"><b>{z['at_tele_end_pct'] if z['at_tele_end_pct'] is not None else '–'}%</b>at ≥20× zoom</div>
<div class="k"><b>{flash.get('fired', 0)}</b>flash fired</div></div>
{''.join(secs)}</main></body></html>"""
    jp, hp = out_dir / "lens_stats.json", out_dir / "lens_stats.html"
    jp.write_text(json.dumps(stats, indent=2))
    hp.write_text(page)
    stats["paths"] = {"json": str(jp), "html": str(hp)}
    return stats


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m l120.lab.analyze", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("report", help="histogram, clipping, zebra, sharpness, noise, EXIF for each photo")
    a.add_argument("paths", nargs="+")
    a.add_argument("--out", default=None, help="output folder (default: <photo dir>/lab-report)")
    a = sub.add_parser("cull", help="rank photos, flag blurry/badly exposed/duplicates, contact sheet")
    a.add_argument("paths", nargs="+")
    a.add_argument("--out", required=True)
    a.add_argument("--burst-gap", type=float, default=2.0, help="seconds between shots in a burst")
    a.add_argument("--dup-threshold", type=int, default=6, help="pHash Hamming distance for near-duplicates")
    a = sub.add_parser("dates", help="group photos by camera-clock runs; writes date_plan.json")
    a.add_argument("paths", nargs="+")
    a.add_argument("--out", required=True)
    a = sub.add_parser("fix-dates", help="write date-corrected COPIES (dry run unless --apply)")
    a.add_argument("paths", nargs="*")
    a.add_argument("--out", required=True, help="folder for corrected copies (must not hold the originals)")
    a.add_argument("--reference", help="photo whose real capture time you know")
    a.add_argument("--true", dest="true_dt", help="real local time of --reference, 'YYYY-MM-DD HH:MM:SS'")
    a.add_argument("--offset", help="explicit shift, e.g. +2954d14h30m or -3600")
    a.add_argument("--sequence-step", type=float, default=None,
                   help="for unset (0000) clocks: seconds per file number from the reference (ESTIMATE)")
    a.add_argument("--plan", help="filled-in date_plan.json from the 'dates' command")
    a.add_argument("--apply", action="store_true", help="actually write copies (default: dry run)")
    a.add_argument("--overwrite", action="store_true", help="replace existing files in --out")
    a = sub.add_parser("stats", help="focal length / ISO / flash / scene / time-of-day report")
    a.add_argument("paths", nargs="+")
    a.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "report":
        for p in expand_inputs(args.paths):
            r = shot_report(p, args.out)
            print(r["summary"])
            print("   ", r["paths"]["histogram"], r["paths"]["zebra"])
    elif args.cmd == "cull":
        r = cull(args.paths, args.out, burst_gap_s=args.burst_gap, dup_threshold=args.dup_threshold)
        for ph in r["photos"]:
            print(f"{ph['decision']:7} {ph['name']:28} g{ph['group']:<3} sharp {ph['sharpness']['subject']:7.1f} "
                  f"{ph['exposure']['verdict']:26} {','.join(ph['flags'])}")
        print(r["summary"], "->", r["paths"]["html"])
    elif args.cmd == "dates":
        r = guess_date_ranges(args.paths, args.out)
        for g in r["groups"]:
            print(f"group {g['group']}: {g['count']} files {g['first_file']}..{g['last_file']} "
                  f"[{g['clock_state']}] camera {g['camera_first']} .. {g['camera_last']}")
        print("plan:", r["plan_path"])
    elif args.cmd == "fix-dates":
        if args.plan:
            r = fix_dates_from_plan(args.plan, out_dir=args.out, apply=args.apply, overwrite=args.overwrite)
        else:
            if not args.paths:
                ap.error("fix-dates needs photo paths (or --plan)")
            r = fix_dates(args.paths, out_dir=args.out, reference_file=args.reference, true_datetime=args.true_dt,
                          offset=args.offset, sequence_step_s=args.sequence_step, apply=args.apply,
                          overwrite=args.overwrite)
        for f in r["files"]:
            print(f"{f['status']:10} {Path(f['source']).name:28} {f['camera_datetime']} -> {f['new_datetime']}  ({f['reason']})")
        print(r["summary"], "" if r["applied"] else "\nDRY RUN — nothing written. Re-run with --apply.")
        if r.get("manifest"):
            print("manifest:", r["manifest"])
    elif args.cmd == "stats":
        r = lens_stats(args.paths, args.out)
        print(json.dumps({k: v for k, v in r.items() if k != "paths"}, indent=1))
        print("->", r["paths"]["html"])


if __name__ == "__main__":
    sys.exit(main())
