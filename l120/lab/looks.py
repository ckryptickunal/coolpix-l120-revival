"""Film simulations tuned for the L120's 14MP CCD.

The L120's CCD renders clean, slightly cool, contrasty JPEGs with hard
highlight clipping. Each look below is a small recipe: a 3x3 channel mixer,
per-channel tone curves, saturation, split toning, optional highlight glow,
grain and vignette. Everything is plain numpy on float32 RGB in [0, 1].

    python -m l120.lab.looks apply IMG.jpg --look portra -o out.jpg [--datestamp]
    python -m l120.lab.looks grid IMG.jpg -o grid.jpg
    python -m l120.lab.looks list
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

Curve = list[tuple[float, float]]


@dataclass
class Look:
    name: str
    title: str
    blurb: str
    mixer: np.ndarray | None = None          # 3x3, rows = output R,G,B
    curve: Curve | None = None               # master curve (all channels)
    curve_r: Curve | None = None
    curve_g: Curve | None = None
    curve_b: Curve | None = None
    saturation: float = 1.0
    shadow_tint: tuple[float, float, float] = (0, 0, 0)     # added in shadows
    highlight_tint: tuple[float, float, float] = (0, 0, 0)  # added in highlights
    bw: tuple[float, float, float] | None = None  # luminance weights => monochrome
    glow: float = 0.0          # highlight bloom strength
    grain: float = 0.0         # std-dev of grain (0..~0.06)
    grain_size: float = 1.0    # grain blur radius in px at 4320px width
    vignette: float = 0.0      # 0..1 corner darkening
    extra: dict = field(default_factory=dict)


def _m(*rows) -> np.ndarray:
    return np.array(rows, dtype=np.float32)


LOOKS: dict[str, Look] = {l.name: l for l in [
    Look("kodachrome", "Kodachrome-ish",
         "Deep reds, dense shadows, warm punchy contrast.",
         mixer=_m([1.10, -0.05, -0.05], [-0.04, 1.04, 0.00], [-0.02, -0.08, 1.10]),
         curve=[(0, 0), (0.15, 0.10), (0.5, 0.52), (0.85, 0.90), (1, 0.98)],
         curve_r=[(0, 0), (0.5, 0.53), (1, 1)],
         curve_b=[(0, 0.02), (0.5, 0.47), (1, 0.95)],
         saturation=1.18, shadow_tint=(0.01, 0.0, -0.01), grain=0.018, vignette=0.25),
    Look("portra", "Portra-ish",
         "Soft contrast, warm skin, pastel greens, creamy highlights.",
         mixer=_m([1.02, 0.02, -0.04], [0.00, 0.98, 0.02], [-0.02, 0.04, 0.98]),
         curve=[(0, 0.04), (0.25, 0.25), (0.5, 0.52), (0.8, 0.80), (1, 0.95)],
         curve_r=[(0, 0.01), (0.5, 0.52), (1, 1)],
         curve_b=[(0, 0.03), (0.5, 0.48), (1, 0.94)],
         saturation=0.88, highlight_tint=(0.02, 0.01, -0.015), grain=0.014, vignette=0.15),
    Look("velvia", "Velvia-ish",
         "Saturated slide film: electric greens and blues, deep blacks.",
         mixer=_m([1.12, -0.08, -0.04], [-0.06, 1.14, -0.08], [-0.04, -0.10, 1.14]),
         curve=[(0, 0), (0.2, 0.13), (0.5, 0.50), (0.8, 0.87), (1, 1)],
         saturation=1.35, shadow_tint=(-0.005, 0, 0.01), grain=0.010, vignette=0.2),
    Look("chrome", "Classic Chrome",
         "Muted, documentary: tamed saturation, hard shadows, cool-teal blues.",
         mixer=_m([0.96, 0.06, -0.02], [0.02, 0.96, 0.02], [0.00, 0.08, 0.92]),
         curve=[(0, 0.02), (0.2, 0.15), (0.5, 0.50), (0.8, 0.84), (1, 0.97)],
         saturation=0.72, shadow_tint=(-0.005, 0.005, 0.012), highlight_tint=(0.01, 0.005, -0.01),
         grain=0.012, vignette=0.2),
    Look("bw-red", "B&W + red filter",
         "Monochrome as through a red #25 filter: dark skies, glowing skin.",
         bw=(0.90, 0.20, -0.10),
         curve=[(0, 0), (0.15, 0.08), (0.5, 0.52), (0.85, 0.92), (1, 1)],
         grain=0.030, grain_size=1.4, vignette=0.35),
    Look("teal-orange", "Cinematic teal-orange",
         "Blockbuster grade: teal shadows, warm skin and highlights.",
         curve=[(0, 0.03), (0.2, 0.16), (0.5, 0.50), (0.85, 0.88), (1, 0.97)],
         curve_r=[(0, 0), (0.3, 0.27), (0.7, 0.74), (1, 1)],
         curve_b=[(0, 0.06), (0.3, 0.33), (0.7, 0.66), (1, 0.93)],
         saturation=1.08, shadow_tint=(-0.03, 0.01, 0.035), highlight_tint=(0.035, 0.012, -0.03),
         grain=0.010, vignette=0.3),
    Look("ccd-glow", "2000s digicam CCD glow",
         "The 2008 point-and-shoot feel: bright, cool, blooming highlights, crunchy.",
         mixer=_m([1.00, 0.00, 0.00], [0.00, 1.02, 0.00], [0.02, 0.02, 1.06]),
         curve=[(0, 0.0), (0.3, 0.34), (0.6, 0.70), (0.85, 0.94), (1, 1)],
         curve_b=[(0, 0.03), (0.5, 0.53), (1, 1)],
         saturation=1.15, glow=0.45, grain=0.008, grain_size=0.6, vignette=0.1,
         extra={"sharpen": 1.0}),
    Look("faded", "Faded nostalgic",
         "Old drugstore print found in a shoebox: lifted blacks, warm cast, soft.",
         mixer=_m([1.00, 0.04, 0.00], [0.02, 0.96, 0.02], [0.04, 0.06, 0.86]),
         curve=[(0, 0.12), (0.3, 0.34), (0.6, 0.62), (0.9, 0.86), (1, 0.90)],
         curve_r=[(0, 0.02), (0.5, 0.53), (1, 1)],
         curve_b=[(0, 0.0), (0.5, 0.46), (1, 0.92)],
         saturation=0.75, shadow_tint=(0.02, 0.0, 0.02), highlight_tint=(0.03, 0.015, -0.02),
         glow=0.12, grain=0.022, grain_size=1.2, vignette=0.35),
]}


# ---------------------------------------------------------------- primitives

def _curve(x: np.ndarray, pts: Curve) -> np.ndarray:
    """Monotone-ish tone curve via smoothed piecewise-linear interpolation."""
    xs, ys = zip(*pts)
    lut_x = np.linspace(0, 1, 1024, dtype=np.float32)
    lut = np.interp(lut_x, xs, ys).astype(np.float32)
    # smooth the LUT so control points don't produce visible kinks
    k = np.hanning(61).astype(np.float32); k /= k.sum()
    pad = np.pad(lut, 30, mode="edge")
    lut = np.convolve(pad, k, mode="valid")
    return np.interp(x, lut_x, lut).astype(np.float32)


def _dot3(a: np.ndarray, w) -> np.ndarray:
    """a[...,0]*w0 + a[...,1]*w1 + a[...,2]*w2 (avoids spurious BLAS FP warnings on macOS)."""
    return a[..., 0] * np.float32(w[0]) + a[..., 1] * np.float32(w[1]) + a[..., 2] * np.float32(w[2])


def _luma(a: np.ndarray) -> np.ndarray:
    return _dot3(a, (0.2126, 0.7152, 0.0722))


def _blur(a: np.ndarray, radius: float) -> np.ndarray:
    import cv2
    if radius <= 0:
        return a
    return cv2.GaussianBlur(a, (0, 0), radius)


def _grain(shape, amount: float, size: float, rng) -> np.ndarray:
    h, w = shape[:2]
    n = rng.standard_normal((h, w)).astype(np.float32)
    if size > 0.3:
        n = _blur(n, size)
        n /= max(n.std(), 1e-6)
    return n * amount


def render(img: np.ndarray, look: Look, seed: int = 0) -> np.ndarray:
    """Apply a look to float32 RGB [0,1] array (HxWx3). Returns new array."""
    a = img.astype(np.float32, copy=True)
    scale = a.shape[1] / 4320.0  # effects are specified at full L120 resolution

    if look.bw is not None:
        g = np.clip(_dot3(a, look.bw), 0, 1)
        a = np.repeat(g[..., None], 3, axis=2)
    elif look.mixer is not None:
        a = np.clip(np.stack([_dot3(a, row) for row in look.mixer], axis=-1), 0, 1)

    if look.curve:
        a = _curve(a, look.curve)
    for i, c in enumerate((look.curve_r, look.curve_g, look.curve_b)):
        if c:
            a[..., i] = _curve(a[..., i], c)

    if look.saturation != 1.0 and look.bw is None:
        y = _luma(a)[..., None]
        a = y + (a - y) * look.saturation

    if any(look.shadow_tint) or any(look.highlight_tint):
        y = np.clip(_luma(a), 0, 1)[..., None]
        sh = (1 - y) ** 2
        hi = y ** 2
        a = a + sh * np.array(look.shadow_tint, np.float32) + hi * np.array(look.highlight_tint, np.float32)

    a = np.clip(a, 0, 1)

    if look.glow > 0:  # bloom: blur the brightest parts and screen them back
        y = _luma(a)[..., None]
        bright = a * np.clip((y - 0.6) / 0.4, 0, 1)
        bloom = _blur(bright, 18 * scale + 1)
        a = 1 - (1 - a) * (1 - bloom * look.glow)

    if look.extra.get("sharpen"):
        soft = _blur(a, 1.2 * scale + 0.4)
        a = np.clip(a + (a - soft) * look.extra["sharpen"], 0, 1)

    if look.vignette > 0:
        h, w = a.shape[:2]
        yy, xx = np.ogrid[-1:1:complex(0, h), -1:1:complex(0, w)]
        r2 = (xx ** 2 + yy ** 2) / 2.0
        v = 1 - look.vignette * np.clip(r2, 0, 1) ** 1.5
        a = a * v[..., None].astype(np.float32)

    if look.grain > 0:  # luminance-weighted grain: strongest in the midtones
        rng = np.random.default_rng(seed)
        n = _grain(a.shape, look.grain, look.grain_size * scale, rng)
        y = _luma(a)
        a = a + (n * (0.4 + 2.4 * y * (1 - y)))[..., None]

    return np.clip(a, 0, 1)


# ---------------------------------------------------------------- date stamp

#   segments: a top, b upper-right, c lower-right, d bottom, e lower-left, f upper-left, g middle
_SEG = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
    "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", " ": "", "-": "g",
}


def _seven_seg(draw: ImageDraw.ImageDraw, x: float, y: float, h: float, ch: str, fill) -> float:
    """Draw one seven-segment character with top-left at (x,y), height h. Returns advance."""
    w = h * 0.58
    t = h * 0.13  # segment thickness
    sk = h * 0.10  # italic skew, like the classic LCD imprint

    def seg(x0, y0, x1, y1):
        g, q = t * 0.30, t / 2  # end gap, half-thickness (pointed ends)
        if abs(y1 - y0) < 1e-6:
            pts = [(x0 + g, y0), (x0 + g + q, y0 - q), (x1 - g - q, y1 - q), (x1 - g, y1),
                   (x1 - g - q, y1 + q), (x0 + g + q, y0 + q)]
        else:
            pts = [(x0, y0 + g), (x0 + q, y0 + g + q), (x1 + q, y1 - g - q), (x1, y1 - g),
                   (x1 - q, y1 - g - q), (x0 - q, y0 + g + q)]
        # skew: shift x by how far up the glyph the point is
        pts = [(px + sk * (1 - (py - y) / h), py) for px, py in pts]
        draw.polygon(pts, fill=fill)

    if ch == "'":
        draw.polygon([(x + sk + t * 0.9, y), (x + sk + t * 1.9, y), (x + sk + t * 0.6, y + h * 0.28),
                      (x + sk, y + h * 0.28)], fill=fill)
        return t * 2.6
    if ch == " ":
        return w * 0.45
    L, R, T, M, B = x + t / 2, x + w - t / 2, y + t / 2, y + h / 2, y + h - t / 2
    s = _SEG.get(ch, "")
    if "a" in s: seg(L, T, R, T)
    if "g" in s: seg(L, M, R, M)
    if "d" in s: seg(L, B, R, B)
    if "f" in s: seg(L, T, L, M)
    if "b" in s: seg(R, T, R, M)
    if "e" in s: seg(L, M, L, B)
    if "c" in s: seg(R, M, R, B)
    return w + t * 1.3


def date_stamp(im: Image.Image, when: dt.date | None = None, text: str | None = None) -> Image.Image:
    """Burn in a classic orange LCD-style date ("'11 7 23") bottom-right."""
    when = when or dt.date.today()
    text = text or f"'{when:%y} {when.month} {when.day:>2}".replace("  ", " ")
    im = im.convert("RGB")
    W, H = im.size
    h = max(14, int(H * 0.042))
    layer = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(layer)
    # measure, then draw right-aligned with margin
    tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
    width = sum(_seven_seg(tmp, 0, 0, h, c, 0) + h * 0.08 for c in text)
    x = W - width - W * 0.045
    y = H - h - H * 0.05
    for c in text:
        x += _seven_seg(d, x, y, h, c, 255) + h * 0.08
    core = np.asarray(layer.filter(ImageFilter.GaussianBlur(h * 0.02)), np.float32) / 255
    halo = np.asarray(layer.filter(ImageFilter.GaussianBlur(h * 0.25)), np.float32) / 255
    base = np.asarray(im, np.float32) / 255
    orange = np.array([1.0, 0.55, 0.12], np.float32)
    hot = np.array([1.0, 0.82, 0.45], np.float32)
    out = 1 - (1 - base) * (1 - halo[..., None] * 0.55 * orange)       # screen glow
    out = out * (1 - core[..., None]) + core[..., None] * (orange * 0.75 + hot * 0.25)
    return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8))


def photo_date(path: str | Path) -> dt.date:
    """EXIF DateTimeOriginal if the camera clock was set, else file mtime."""
    try:
        ex = Image.open(path).getexif()
        raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        if raw and not raw.startswith("0000"):
            return dt.datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S").date()
    except Exception:
        pass
    return dt.date.fromtimestamp(os.path.getmtime(path))


# ---------------------------------------------------------------- public API

def _load(path, max_size: int | None) -> tuple[Image.Image, np.ndarray]:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if max_size and max(im.size) > max_size:
        im.thumbnail((max_size, max_size), Image.LANCZOS)
    return im, np.asarray(im, np.float32) / 255


def apply_look(path, look: str, out, datestamp: bool | str = False, max_size: int | None = None,
               quality: int = 93) -> Path:
    """Render `look` onto the photo at `path` and save to `out` (JPEG).

    datestamp: False, True (EXIF/mtime date), or a "YYYY-MM-DD" string.
    """
    if look not in LOOKS:
        raise KeyError(f"unknown look {look!r}; choose from {', '.join(LOOKS)}")
    src, arr = _load(path, max_size)
    res = Image.fromarray((render(arr, LOOKS[look]) * 255 + 0.5).astype(np.uint8))
    if datestamp:
        when = dt.date.fromisoformat(datestamp) if isinstance(datestamp, str) else photo_date(path)
        res = date_stamp(res, when)
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.open(path).getexif()
    res.save(out, "JPEG", quality=quality, subsampling=0, exif=exif)
    return out


def _font(size: int):
    for f in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
              "/Library/Fonts/Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def preview_grid(path, out, cell: int = 640, cols: int = 3, datestamp: bool = False) -> Path:
    """Contact sheet: original + every look, labelled."""
    src, arr = _load(path, cell)
    tiles = [("Original", src)]
    for lk in LOOKS.values():
        t = Image.fromarray((render(arr, lk) * 255 + 0.5).astype(np.uint8))
        if datestamp:
            t = date_stamp(t, photo_date(path))
        tiles.append((lk.title, t))
    tw, th = src.size
    pad, label = 16, 34
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label + pad) + pad), (18, 18, 20))
    d = ImageDraw.Draw(sheet)
    f = _font(20)
    for i, (name, t) in enumerate(tiles):
        x = pad + (i % cols) * (tw + pad)
        y = pad + (i // cols) * (th + label + pad)
        sheet.paste(t, (x, y))
        d.text((x + 2, y + th + 7), name, fill=(225, 225, 225), font=f)
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=90)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m l120.lab.looks", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="apply one look")
    a.add_argument("image"); a.add_argument("--look", "-l", required=True, choices=list(LOOKS))
    a.add_argument("-o", "--out", required=True)
    a.add_argument("--datestamp", nargs="?", const=True, default=False,
                   help="burn in orange LCD date (optionally YYYY-MM-DD)")
    a.add_argument("--max-size", type=int, default=None, help="downscale long edge first")
    g = sub.add_parser("grid", help="all looks side by side")
    g.add_argument("image"); g.add_argument("-o", "--out", required=True)
    g.add_argument("--cell", type=int, default=640); g.add_argument("--datestamp", action="store_true")
    b = sub.add_parser("batch", help="apply a look to many images into a folder")
    b.add_argument("images", nargs="+"); b.add_argument("--look", "-l", required=True, choices=list(LOOKS))
    b.add_argument("-o", "--out-dir", required=True); b.add_argument("--datestamp", action="store_true")
    sub.add_parser("list", help="list looks")
    args = p.parse_args(argv)
    if args.cmd == "list":
        for lk in LOOKS.values():
            print(f"{lk.name:12} {lk.title:26} {lk.blurb}")
    elif args.cmd == "apply":
        print(apply_look(args.image, args.look, args.out, args.datestamp, args.max_size))
    elif args.cmd == "grid":
        print(preview_grid(args.image, args.out, args.cell, datestamp=args.datestamp))
    elif args.cmd == "batch":
        for img in args.images:
            o = Path(args.out_dir) / f"{Path(img).stem}_{args.look}.jpg"
            print(apply_look(img, args.look, o, args.datestamp))


if __name__ == "__main__":
    main()
