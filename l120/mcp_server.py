"""MCP server: control the Nikon COOLPIX L120 from Claude (or any MCP client) over USB."""
from __future__ import annotations

import base64
import io
import threading

from mcp.server.mcpserver import MCPServer, Image as McpImage
from PIL import Image

from . import features
from .camera import L120, CameraError

mcp = MCPServer("nikon-l120")
_lock = threading.Lock()  # one USB conversation at a time


def _run(fn):
    with _lock:
        with L120() as cam:
            return fn(cam)


def _preview(data: bytes, max_px=1024) -> McpImage:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return McpImage(data=buf.getvalue(), format="jpeg")


@mcp.tool()
def camera_status() -> dict:
    """Battery, card space, firmware, clock and the USB commands the camera supports."""
    def f(cam):
        info = cam.device_info()
        return {"model": info["model"], "firmware": info["firmware"], "serial": info["serial"],
                "battery_pct": cam.battery(), "camera_clock": cam.clock(), "card": cam.storage()}
    return _run(f)


@mcp.tool()
def sync_clock() -> str:
    """Set the camera's date/time to the Mac's current time (fixes wrong photo dates)."""
    return _run(lambda cam: f"Camera clock now {cam.set_clock()}")


@mcp.tool()
def take_photo(keep_on_card: bool = True) -> list:
    """Fire the shutter (camera autofocuses and meters itself) and save the full 14MP JPEG on the Mac.
    Returns the saved path and a preview. Zoom/mode must be set on the camera body."""
    def f(cam):
        data, info = cam.capture(keep_on_card=keep_on_card)
        out = features._session_dir("photo") / info.filename
        out.write_bytes(data)
        return [f"Saved {out} ({len(data) // 1024} KB)", _preview(data)]
    return _run(f)


@mcp.tool()
def long_exposure(frames: int = 10, mode: str = "lighten") -> list:
    """Simulated long exposure by shooting `frames` shots (~5 s each) and merging them.
    mode: lighten (light/star trails), mean (smooth water, less noise), darken, median (remove passers-by).
    Keep the camera on a tripod."""
    def f(cam):
        r = features.stack(cam, frames, mode)
        return [str(r), _preview(open(r["result"], "rb").read())]
    return _run(f)


@mcp.tool()
def timelapse(count: int = 30, interval_seconds: float = 10, fps: int = 24) -> dict:
    """Intervalometer: shoot `count` photos every `interval_seconds` (min ~5 s) and build an MP4."""
    return _run(lambda cam: features.timelapse(cam, count, interval_seconds, fps))


def _card_files(cam):
    """[(path 'FOLDER/NAME', info)] — paths are unique even when folders share filenames."""
    names = cam.folders()
    return [(f"{names.get(i.parent, '?')}/{i.filename}", i) for i in cam.list_files()]


def _find(cam, path: str):
    path = path.upper()
    hits = [(p, i) for p, i in _card_files(cam) if p.upper() == path or (
        "/" not in path and i.filename.upper() == path)]
    if not hits:
        raise CameraError(f"{path} not on card")
    if len(hits) > 1:
        raise CameraError(f"{path} is ambiguous, use one of: {[p for p, _ in hits]}")
    return hits[0]


@mcp.tool()
def list_card(limit: int = 50, folder: str = "") -> dict:
    """List photos and videos on the SD card as FOLDER/NAME (e.g. 101NIKON/DSCN0291.JPG), newest folder last."""
    def f(cam):
        files = [(p, i) for p, i in _card_files(cam) if not folder or p.upper().startswith(folder.upper())]
        return {"total": len(files), "files": [
            {"path": p, "kb": i.size // 1024, "taken": i.captured} for p, i in files[-limit:]]}
    return _run(f)


@mcp.tool()
def card_thumbnail(path: str) -> list:
    """Show the thumbnail of a card file, e.g. 101NIKON/DSCN0291.JPG (bare filename works if unique)."""
    def f(cam):
        p, i = _find(cam, path)
        return [p, _preview(cam.thumbnail(i.handle), 320)]
    return _run(f)


@mcp.tool()
def download_from_card(path: str) -> list:
    """Download one full-resolution file from the card to the Mac and preview it."""
    def f(cam):
        p, i = _find(cam, path)
        out = features.OUT_ROOT / "card-import" / p
        out.parent.mkdir(parents=True, exist_ok=True)
        data = cam.download(i.handle)
        out.write_bytes(data)
        res = [f"Saved {out}"]
        if i.format == 0x3801:
            res.append(_preview(data))
        return res
    return _run(f)


@mcp.tool()
def import_card(only_new: bool = True) -> dict:
    """Copy all photos and videos from the SD card to the Mac (captures/card-import/<folder>/). Never deletes from the card."""
    return _run(lambda cam: features.import_card(cam, only_new=only_new, delete_after=False))


@mcp.tool()
def delete_from_card(path: str, confirm: bool = False) -> str:
    """Permanently delete one file from the SD card, e.g. 101NIKON/DSCN0291.JPG. Requires confirm=true."""
    if not confirm:
        return "Not deleted: pass confirm=true to permanently delete this file from the card."
    def f(cam):
        p, i = _find(cam, path)
        cam.delete(i.handle)
        return f"Deleted {p}"
    return _run(f)


# ---------------------------------------------------------------------------
# Lab: computational photography + creative tools (work on files on the Mac)
# ---------------------------------------------------------------------------
from pathlib import Path as _P

_LAB_OUT = features.OUT_ROOT / "lab"


def _paths(paths: list[str] | str) -> list[str]:
    """Accept a list of files, a folder, or a glob."""
    if isinstance(paths, str):
        p = _P(paths).expanduser()
        if p.is_dir():
            return sorted(str(x) for x in p.iterdir() if x.suffix.lower() in (".jpg", ".jpeg"))
        return sorted(str(x) for x in _P("/").glob(str(p).lstrip("/"))) or [str(p)]
    return [str(_P(x).expanduser()) for x in paths]


def _out(name: str) -> str:
    _LAB_OUT.mkdir(parents=True, exist_ok=True)
    import datetime as _dt
    return str(_LAB_OUT / f"{_dt.datetime.now():%Y%m%d-%H%M%S}-{name}")


@mcp.tool()
def night_mode(frames: int = 8) -> list:
    """Handheld night mode: shoot `frames` photos, align them and merge to cut noise (like a phone's night mode)."""
    from .lab import merge
    def f(cam):
        d = features._session_dir("night")
        paths = []
        for i in range(frames):
            data, _ = cam.capture(keep_on_card=False)
            p = d / f"frame_{i + 1:03d}.jpg"
            p.write_bytes(data)
            paths.append(str(p))
        out = str(d / "night_mode.jpg")
        r = merge.night_mode(paths, out)
        return [str(r), _preview(open(out, "rb").read())]
    return _run(f)


@mcp.tool()
def merge_photos(paths: list[str] | str, kind: str = "night") -> list:
    """Merge existing photos (list, folder or glob). kind: night (align+denoise), lighten/mean/median/darken
    (aligned stack), hdr (exposure fusion of shots at different EV), panorama, superres (2x multi-frame)."""
    from .lab import merge
    ps = _paths(paths)
    out = _out(f"{kind}.jpg")
    if kind == "night":
        r = merge.night_mode(ps, out)
    elif kind in ("lighten", "mean", "median", "darken"):
        r = merge.aligned_stack(ps, out, mode=kind)
    elif kind == "hdr":
        r = merge.exposure_fusion(ps, out)
    elif kind == "panorama":
        r = merge.panorama(ps, out)
    elif kind == "superres":
        r = merge.super_resolution(ps, out)
    else:
        raise ValueError("kind must be night, lighten, mean, median, darken, hdr, panorama or superres")
    return [str(r), _preview(open(out, "rb").read())]


@mcp.tool()
def analyze_photo(path: str) -> list:
    """CHDK-style shot report: histogram, clipped highlights/shadows (zebra), sharpness, noise, EXIF."""
    from .lab import analyze
    r = analyze.shot_report(path, _out("report"))
    imgs = [_preview(open(v, "rb").read(), 800) for k, v in r.items()
            if isinstance(v, str) and v.lower().endswith((".png", ".jpg")) and _P(v).exists()][:2]
    return [str({k: v for k, v in r.items()})] + imgs


@mcp.tool()
def cull_photos(folder: str) -> dict:
    """Rank a folder of photos: flag blurry/badly exposed/near-duplicates, pick the best of each burst; writes an HTML contact sheet."""
    from .lab import analyze
    return analyze.cull(folder, _out("cull"))


@mcp.tool()
def apply_film_look(path: str, look: str = "", date_stamp: bool = False) -> list:
    """Apply a film simulation to a photo. Empty look = list available looks and render a comparison grid."""
    from .lab import looks
    if not look:
        out = _out("looks-grid.jpg")
        looks.preview_grid(path, out)
        return [f"Looks: {', '.join(looks.LOOKS)}", _preview(open(out, 'rb').read())]
    out = _out(f"{look}.jpg")
    looks.apply_look(path, look, out, datestamp=date_stamp)
    return [f"Saved {out}", _preview(open(out, "rb").read())]


@mcp.tool()
def video_tool(path: str, action: str = "convert", factor: float = 0.5, start: float = 0.0, seconds: float = 3.0) -> str:
    """Work on a camera MOV: convert (web MP4), stabilize, speed (factor<1 = smooth slow-mo), gif, best_frame."""
    from .lab import video
    if action == "convert":
        return str(video.convert(path, _out("video.mp4")))
    if action == "stabilize":
        return str(video.stabilize(path, _out("stabilized.mp4")))
    if action == "speed":
        return str(video.speed(path, _out(f"speed{factor}.mp4"), factor))
    if action == "gif":
        return str(video.to_gif(path, _out("clip.gif"), start, seconds))
    if action == "best_frame":
        return str(video.extract_best_frame(path, _out("best.jpg")))
    raise ValueError("action must be convert, stabilize, speed, gif or best_frame")


@mcp.tool()
def memories(folder: str, action: str = "album", date: str = "") -> str:
    """From a folder of camera photos/videos: album (self-contained HTML album by year), slideshow (Ken Burns MP4),
    on_this_day (photos from this day in past years; date YYYY-MM-DD optional)."""
    from .lab import memories as m
    if action == "album":
        return str(m.album(folder, _out("album")))
    if action == "slideshow":
        return str(m.slideshow(_paths(folder), _out("slideshow.mp4")))
    if action == "on_this_day":
        return str(m.on_this_day(folder, date or None))
    raise ValueError("action must be album, slideshow or on_this_day")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
