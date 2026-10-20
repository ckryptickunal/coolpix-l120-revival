"""Features built on the Mac side, on top of the camera's USB shutter."""
from __future__ import annotations

import datetime as dt
import io
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .camera import L120

OUT_ROOT = Path(__file__).resolve().parent.parent / "captures"


def _session_dir(kind: str) -> Path:
    d = OUT_ROOT / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{kind}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def take_photo(cam: L120, keep_on_card=True) -> Path:
    data, info = cam.capture(keep_on_card=keep_on_card)
    out = _session_dir("photo") / info.filename
    out.write_bytes(data)
    return out


def stack(cam: L120, frames: int, mode: str = "lighten", keep_frames=True) -> dict:
    """Simulated long exposure: shoot N frames and merge them.

    mode: lighten = light trails / star trails (brightest pixel wins)
          mean    = smooth water / crowds vanish / less noise
          darken  = remove bright moving things
          median  = remove people/objects that move through the scene
    """
    if mode not in ("lighten", "mean", "darken", "median"):
        raise ValueError("mode must be lighten, mean, darken or median")
    d = _session_dir(f"stack-{mode}")
    acc, kept, t0, exif = None, [], time.time(), None
    for i in range(frames):
        data, _ = cam.capture(keep_on_card=False)
        if keep_frames:
            (d / f"frame_{i + 1:03d}.jpg").write_bytes(data)
        im = Image.open(io.BytesIO(data))
        exif = exif or im.info.get("exif")
        a = np.asarray(im.convert("RGB"))
        if mode == "median":
            kept.append(a)
        elif acc is None:
            acc = a.astype(np.float32) if mode == "mean" else a.copy()
        elif mode == "mean":
            acc += a
        elif mode == "lighten":
            np.maximum(acc, a, out=acc)
        else:
            np.minimum(acc, a, out=acc)
    if mode == "median":
        result = np.median(np.stack(kept), axis=0).astype(np.uint8)
    elif mode == "mean":
        result = np.clip(acc / frames, 0, 255).astype(np.uint8)
    else:
        result = acc
    out = d / f"stacked_{mode}_{frames}f.jpg"
    Image.fromarray(result).save(out, quality=95, **({"exif": exif} if exif else {}))
    return {"result": str(out), "frames": frames, "seconds": round(time.time() - t0, 1), "folder": str(d)}


def timelapse(cam: L120, count: int, interval_s: float, fps: int = 24, make_video=True) -> dict:
    """Intervalometer. Minimum real interval is ~5 s (camera's own shot-to-shot time)."""
    d = _session_dir("timelapse")
    t0 = time.time()
    for i in range(count):
        start = time.time()
        data, _ = cam.capture(keep_on_card=False)
        (d / f"tl_{i + 1:05d}.jpg").write_bytes(data)
        wait = interval_s - (time.time() - start)
        if wait > 0 and i < count - 1:
            time.sleep(wait)
    result = {"frames": count, "folder": str(d), "seconds": round(time.time() - t0, 1)}
    if make_video and count > 1:
        video = d / "timelapse.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(d / "tl_%05d.jpg"),
                        "-vf", "scale=1920:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
        result["video"] = str(video)
    return result


def import_card(cam: L120, only_new=True, delete_after=False) -> dict:
    """Copy photos/videos from the card into captures/card-import, skipping ones already copied."""
    dest = OUT_ROOT / "card-import"
    dest.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], 0
    for info in cam.list_files():
        target = dest / info.filename
        if only_new and target.exists() and target.stat().st_size == info.size:
            skipped += 1
            continue
        target.write_bytes(cam.download(info.handle))
        copied.append(info.filename)
        if delete_after and target.stat().st_size == info.size:
            cam.delete(info.handle)
    return {"copied": len(copied), "skipped": skipped, "folder": str(dest), "files": copied[:50]}
