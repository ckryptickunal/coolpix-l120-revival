"""A stand-in camera for working on the Studio UI without the L120 attached.

Enabled with L120_STUDIO_FAKE=1. It mimics the driver's public API with
synthetic images and timings; it never touches USB.
"""
from __future__ import annotations

import datetime as dt
import io
import random
import time

import numpy as np
from PIL import Image, ImageDraw

from l120.camera import ObjectInfo


def _jpeg(seed: int, w=1600, h=1200, label="") -> bytes:
    rng = random.Random(seed)
    im = Image.new("RGB", (w, h), tuple(rng.randrange(20, 90) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(12):
        x, y, r = rng.randrange(w), rng.randrange(h), rng.randrange(40, 300)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rng.randrange(40, 255) for _ in range(3)))
    if label:
        d.text((40, 40), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


class FakeL120:
    def __init__(self):
        self._lockf = None
        self.n = 0
        self.objs: dict[int, ObjectInfo] = {0x10: ObjectInfo(0x10, "100NIKON", 0, 0x3001, 0, "", 0, 0),
                                            0x11: ObjectInfo(0x11, "101NIKON", 0, 0x3001, 0, "", 0, 0)}
        self.data: dict[int, bytes] = {}
        for i in range(40):
            h = 0x100 + i
            folder = 0x10 if i < 28 else 0x11
            name = f"DSCN{(i % 28) + 1:04d}.JPG"  # duplicate names across folders, like the real card
            day = dt.datetime(2012, 5, 1) + dt.timedelta(days=i * 3)
            self._add(h, name, folder, day, _jpeg(i, label=f"{name} #{h:x}"))

    def _add(self, h, name, parent, when, data):
        self.objs[h] = ObjectInfo(h, name, len(data), 0x3801, parent, when.strftime("%Y%m%dT%H%M%S"), 1600, 1200)
        self.data[h] = data

    def connect(self, **_):
        time.sleep(0.3)
        return self

    def close(self):
        pass

    def device_info(self):
        return {"manufacturer": "Nikon", "model": "COOLPIX L120", "firmware": "V1.1 (fake)", "serial": "000"}

    def battery(self):
        return 66

    def clock(self):
        return "20110101T003015"

    def set_clock(self, when=None):
        return self.clock()

    def storage(self):
        return {"card": True, "capacity_mb": 3800, "free_mb": 2100, "free_shots": 812, "read_only": False}

    def list_files(self):
        time.sleep(2)
        return [o for o in self.objs.values() if o.format != 0x3001]

    def object_info(self, h):
        return self.objs[h]

    def thumbnail(self, h):
        im = Image.open(io.BytesIO(self.data[h]))
        im.thumbnail((160, 120))
        buf = io.BytesIO()
        im.save(buf, "JPEG")
        time.sleep(0.05)
        return buf.getvalue()

    def download(self, h):
        time.sleep(0.4)
        return self.data[h]

    def capture(self, keep_on_card=True, timeout_s=30):
        time.sleep(3)
        self.n += 1
        h = max(self.objs) + 1
        rng = np.random.default_rng(self.n)
        base = np.full((900, 1200, 3), 25, np.uint8)
        x = 100 + self.n * 60
        base[300:360, x:x + 60] = (255, 220, 120)  # a moving light, for stacking
        base = np.clip(base + rng.integers(0, 30, base.shape, dtype=np.uint8), 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(base).save(buf, "JPEG", quality=90)
        data = buf.getvalue()
        name = f"DSCN{900 + self.n:04d}.JPG"
        info = ObjectInfo(h, name, len(data), 0x3801, 0x11, dt.datetime.now().strftime("%Y%m%dT%H%M%S"), 1200, 900)
        if keep_on_card:
            self.objs[h], self.data[h] = info, data
        return data, info
