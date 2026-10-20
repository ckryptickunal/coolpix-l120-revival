"""Smart auto-rotate for photos the L120 saved without an orientation tag.

The L120 has no orientation sensor, so portrait shots are stored sideways with
EXIF Orientation=1. We detect upright faces at 0°, 90° and 270° and pick the
rotation where faces are found. Conservative: if nothing is found upright or
sideways, or it's ambiguous, the photo is left as is. Never edits originals;
callers apply the returned rotation to their own copies.

CLI: python -m l120.lab.autorotate <folder> [--apply OUT_DIR]
"""
from __future__ import annotations

import argparse
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

_tls = threading.local()  # OpenCV cascades are not thread-safe: one set per thread


def _detectors():
    if not hasattr(_tls, "faces"):
        base = Path(cv2.data.haarcascades)
        _tls.faces = [cv2.CascadeClassifier(str(base / "haarcascade_frontalface_default.xml"))]
        _tls.eyes = cv2.CascadeClassifier(str(base / "haarcascade_eye.xml"))
    return _tls.faces


def _faces(gray: np.ndarray) -> int:
    n = 0
    for c in _detectors():
        # only clear, reasonably large frontal faces count (dark/light-painting
        # scenes produce many small false positives)
        found = c.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=9,
                                   minSize=(max(40, min(gray.shape) // 7),) * 2)
        n += len(found)
    return n


def _validated_faces(gray: np.ndarray) -> int:
    """Faces that also show at least one eye in the upper half of the face box.
    Rejects texture false positives (clouds, light trails) and upside-down hits."""
    n = 0
    for c in _detectors():
        for (x, y, w, h) in c.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=9,
                                               minSize=(max(40, min(gray.shape) // 7),) * 2):
            roi = gray[y:y + h, x:x + w]
            eyes = _tls.eyes.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=6, minSize=(w // 10, w // 10))
            if any(ey + eh / 2 < h * 0.55 for (_, ey, _, eh) in eyes):
                n += 1
    return n


def guess_rotation(img: Image.Image | str | Path) -> int:
    """Degrees to rotate counter-clockwise (PIL `rotate` convention: 0, 90 or 270) to make it upright."""
    im = Image.open(img) if isinstance(img, (str, Path)) else img
    if isinstance(img, (str, Path)) and im.getexif().get(274, 1) != 1:
        return 0  # camera/user already set an orientation: trust it
    small = im.convert("L").copy()
    small.thumbnail((640, 640))
    raw = np.asarray(small)
    if raw.mean() < 55:  # too dark to judge reliably (night / light painting)
        return 0
    g = cv2.equalizeHist(raw)
    upright = _faces(g)
    if upright:
        return 0
    scores = {90: _validated_faces(np.ascontiguousarray(np.rot90(g, 1))),   # rot90 k=1 == PIL rotate(90)
              270: _validated_faces(np.ascontiguousarray(np.rot90(g, 3)))}
    best = max(scores, key=scores.get)
    other = 270 if best == 90 else 90
    if scores[best] >= 1 and scores[other] == 0:
        return best
    return 0


def open_upright(path: str | Path) -> Image.Image:
    """Open a photo applying the EXIF orientation, then smart auto-rotate if untagged."""
    im = ImageOps.exif_transpose(Image.open(path))
    r = guess_rotation(path)
    return im.rotate(r, expand=True) if r else im


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--apply", type=Path, help="write rotated copies here (originals untouched)")
    a = ap.parse_args(argv)
    files = sorted(p for p in a.folder.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg"))
    changed = 0
    for p in files:
        r = guess_rotation(p)
        if r:
            changed += 1
            print(f"{p.relative_to(a.folder)}: rotate {r}°")
            if a.apply:
                out = a.apply / p.relative_to(a.folder)
                out.parent.mkdir(parents=True, exist_ok=True)
                ImageOps.exif_transpose(Image.open(p)).rotate(r, expand=True).save(out, quality=95)
    print(f"{changed} of {len(files)} photos need rotating")


if __name__ == "__main__":
    main()
