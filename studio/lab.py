"""Studio side of l120.lab: the Mac photo library, shot reports, darkroom and memories.

Everything here works on files already on the Mac (under captures/); nothing
talks to the camera. Inputs are never modified: every result is a new file.
Long work is written as job functions fn(update, stop) for the lab job runner.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from l120.lab import analyze, looks, memories, merge

ROOT: Path = Path(".")           # set by init(); the captures folder
CACHE: Path = Path(".")
SKIP_DIRS = {".studio-cache", "lab-report", "memories", "media"}
JPEG = {".jpg", ".jpeg"}


def init(root: Path):
    global ROOT, CACHE
    ROOT = root.resolve()
    CACHE = ROOT / ".studio-cache"


# ---------------------------------------------------------------- paths

def safe(rel: str) -> Path:
    """captures-relative path -> absolute path, refusing anything outside captures/."""
    p = (ROOT / (rel or "").lstrip("/")).resolve()
    if not p.is_relative_to(ROOT):
        raise ValueError("path outside captures/")
    return p


def rel(p: Path | str) -> str:
    return str(Path(p).resolve().relative_to(ROOT))


def url(p: Path | str | None) -> str | None:
    return "/files/" + rel(p) if p else None


def _key(p: Path, *extra) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}|{extra}".encode()).hexdigest()[:20]


def _unique(p: Path) -> Path:
    k, out = 1, p
    while out.exists():
        k += 1
        out = p.with_name(f"{p.stem}_{k}{p.suffix}")
    return out


def _check_jpegs(rels: list[str], lo: int, hi: int) -> list[Path]:
    paths = [safe(r) for r in rels]
    if not lo <= len(paths) <= hi:
        raise ValueError(f"Select {lo}–{hi} photos.")
    for p in paths:
        if not p.is_file() or p.suffix.lower() not in JPEG:
            raise ValueError(f"Not a JPEG on the Mac: {p.name}")
    return paths


# ---------------------------------------------------------------- library

def library() -> list[dict]:
    """Every JPEG under captures/ (except caches, reports and album media), newest first."""
    out = []
    if not ROOT.exists():
        return out
    for p in ROOT.rglob("*"):
        if p.suffix.lower() not in JPEG or not p.is_file():
            continue
        parts = p.relative_to(ROOT).parts
        if any(x in SKIP_DIRS or x.startswith(".") for x in parts):
            continue
        st = p.stat()
        out.append({"path": rel(p), "name": p.name, "folder": str(Path(*parts[:-1])) if len(parts) > 1 else "",
                    "mtime": st.st_mtime, "size": st.st_size})
    out.sort(key=lambda e: (-e["mtime"], e["path"]))
    return out


def thumb(rel_path: str, size: int = 360) -> bytes:
    p = safe(rel_path)
    size = max(64, min(int(size), 2400))
    c = CACHE / "thumbs" / f"{_key(p, size)}.jpg"
    if not c.exists():
        im = Image.open(p)
        im.draft("RGB", (size, size))           # fast JPEG downscale on decode
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)
        c.parent.mkdir(parents=True, exist_ok=True)
        im.save(c, "JPEG", quality=82)
    return c.read_bytes()


# ---------------------------------------------------------------- shot report

def report(rel_path: str) -> dict:
    """analyze.shot_report, cached; trimmed to what the info strip shows."""
    p = safe(rel_path)
    d = CACHE / "reports" / _key(p)
    j = d / f"{p.stem}_report.json"
    r = json.loads(j.read_text()) if j.exists() else analyze.shot_report(p, out_dir=d)
    x = r.get("exif") or {}
    return {
        "path": rel_path,
        "size_px": r["size_px"],
        "exposure": r["exposure"],
        "sharpness": r["sharpness"],
        "noise_sigma": r["noise_sigma"],
        "exif": {k: x.get(k) for k in ("shutter", "f_number", "iso", "focal_length_35mm", "zoom_x",
                                       "flash", "flash_fired", "scene", "datetime_original", "clock_state",
                                       "exposure_bias_ev")},
        "histogram": url(r["paths"]["histogram"]),
        "zebra": url(r["paths"]["zebra"]),
        "summary": r["summary"],
    }


# ---------------------------------------------------------------- darkroom

def look_list() -> list[dict]:
    return [{"name": "original", "title": "Original", "blurb": "As shot"}] + [
        {"name": lk.name, "title": lk.title, "blurb": lk.blurb} for lk in looks.LOOKS.values()]


def look_preview(rel_path: str, look: str, size: int = 240, datestamp: bool = False) -> bytes:
    """A small rendering of one look, cached. look='original' gives the plain image."""
    p = safe(rel_path)
    if look != "original" and look not in looks.LOOKS:
        raise ValueError("unknown look")
    size = max(120, min(int(size), 1800))
    c = CACHE / "looks" / f"{_key(p, size, look, datestamp)}.jpg"
    if not c.exists():
        src, arr = looks._load(p, size)
        im = src if look == "original" else Image.fromarray(
            (looks.render(arr, looks.LOOKS[look]) * 255 + 0.5).astype(np.uint8))
        if datestamp:
            im = looks.date_stamp(im, looks.photo_date(p))
        c.parent.mkdir(parents=True, exist_ok=True)
        im.save(c, "JPEG", quality=86)
    return c.read_bytes()


def job_apply_look(rel_path: str, look: str, datestamp: bool):
    """Full-size render of a look (or, with look='original', just the date stamp) into darkroom/."""
    src = safe(rel_path)
    if look not in looks.LOOKS and not (look == "original" and datestamp):
        raise ValueError("Pick a look (or turn on the date stamp) first.")
    title = looks.LOOKS[look].title if look in looks.LOOKS else "Date stamp"

    def fn(update, stop):
        update(message=f"Rendering {title} at full size…")
        out = _unique(ROOT / "darkroom" / f"{src.stem}_{look if look != 'original' else 'dated'}"
                                          f"{'_date' if datestamp and look != 'original' else ''}.jpg")
        if look == "original":
            im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
            out.parent.mkdir(parents=True, exist_ok=True)
            looks.date_stamp(im, looks.photo_date(src)).save(out, "JPEG", quality=93, subsampling=0,
                                                             exif=Image.open(src).getexif())
        else:
            looks.apply_look(src, look, out, datestamp=datestamp)
        return {"photo": url(out), "path": rel(out), "label": f"{title} · {src.name}"}
    return fn


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def job_fusion(rels: list[str]):
    paths = _check_jpegs(rels, 2, 9)

    def fn(update, stop):
        update(message=f"Aligning and fusing {len(paths)} exposures…")
        out = ROOT / "darkroom" / f"hdr_{_stamp()}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = merge.exposure_fusion([str(p) for p in paths], out)
        return {"photo": url(out), "path": rel(out), "label": f"HDR fusion · {len(paths)} frames"}
    return fn


def job_panorama(rels: list[str]):
    paths = _check_jpegs(rels, 2, 16)

    def fn(update, stop):
        update(message=f"Stitching {len(paths)} frames… (can take a minute)")
        out = ROOT / "darkroom" / f"pano_{_stamp()}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        msg = "Couldn't stitch these: pan across the scene with about a third overlap and some texture in each frame."
        try:
            r = merge.panorama([str(p) for p in paths], out)
        except Exception as e:   # e.g. near-identical frames leave nothing after cropping
            raise RuntimeError(msg) from e
        if not r.get("output"):
            raise RuntimeError(msg)
        return {"photo": url(out), "path": rel(out), "label": f"Panorama · {len(paths)} frames"}
    return fn


def job_night_merge(rels: list[str]):
    paths = _check_jpegs(rels, 2, 30)

    def fn(update, stop):
        update(message=f"Aligning and merging {len(paths)} frames…")
        out = ROOT / "darkroom" / f"night_{_stamp()}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = night_merge(paths, out)
        return {**r, "photo": url(out), "path": rel(out)}
    return fn


def night_merge(paths: list[Path], out: Path) -> dict:
    r = merge.night_mode([str(p) for p in paths], out)
    gain = (r["noise_ref"] / r["noise_merged"]) if r.get("noise_merged") else None
    return {"label": f"Night · {r['frames_merged']}/{r['frames_in']} frames merged"
                     + (f" · noise ÷{gain:.1f}" if gain else ""),
            "frames_merged": r["frames_merged"], "frames_in": r["frames_in"], "seconds": r["seconds"]}


# ---------------------------------------------------------------- memories

def memory_folders() -> list[dict]:
    """Top-level folders of captures/ that hold photos or videos (card-import first)."""
    out = []
    if not ROOT.exists():
        return out
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIRS or d.name.startswith("."):
            continue
        n = sum(1 for p in d.rglob("*") if p.suffix.lower() in memories.PHOTO_EXT | memories.VIDEO_EXT
                and "lab-report" not in p.parts and "media" not in p.parts)
        if n:
            out.append({"path": rel(d), "count": n})
    out.sort(key=lambda f: (f["path"] != "card-import", f["path"]))
    return out


def _media(folder_rel: str) -> Path:
    d = safe(folder_rel)
    if not d.is_dir() or d == ROOT:
        raise ValueError("Pick a folder inside captures/.")
    return d


def on_this_day(folder_rel: str, date: str | None, window: int) -> dict:
    d = _media(folder_rel)
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    hits = memories.on_this_day(d, day, window_days=max(0, min(int(window), 183)))
    items = [{"path": rel(h["path"]), "name": h["path"].name, "kind": h["kind"], "when": h["when"].isoformat(),
              "date_source": h["date_source"], "years_ago": h["years_ago"]} for h in hits
             if "lab-report" not in h["path"].parts]
    return {"date": day.isoformat(), "window": window, "items": items}


def job_album(folder_rel: str, title: str):
    d = _media(folder_rel)

    def fn(update, stop):
        update(message="Building the album (resizing photos, converting videos)…")
        slug = re.sub(r"[^\w.-]+", "_", folder_rel)
        out_dir = ROOT / "memories" / f"album-{slug}"
        index = memories.album(d, out_dir, title=title or "COOLPIX L120")
        return {"album": url(index), "label": f"Album · {folder_rel}"}
    return fn


def job_slideshow(folder_rel: str, seconds: float, rels: list[str] | None = None):
    d = _media(folder_rel)

    def fn(update, stop):
        if rels:
            photos = [safe(r) for r in rels]
        else:
            photos = [it["path"] for it in memories.scan(d) if it["kind"] == "photo"
                      and "lab-report" not in it["path"].parts]
        if not photos:
            raise ValueError("No photos in that folder.")
        photos = photos[:240]
        update(message=f"Rendering a slideshow of {len(photos)} photos…")
        out = ROOT / "memories" / f"slideshow_{_stamp()}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        memories.slideshow(photos, out, seconds_per_photo=seconds)
        return {"video": url(out), "label": f"Slideshow · {len(photos)} photos"}
    return fn
