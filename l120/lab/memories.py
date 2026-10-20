"""Memories: Ken Burns slideshows, a self-contained HTML album, and "on this day".

    python -m l120.lab.memories slideshow a.jpg b.jpg c.jpg -o show.mp4 [--music song.mp3]
    python -m l120.lab.memories album ~/Pictures/L120 -o ~/Pictures/L120-album
    python -m l120.lab.memories onthisday ~/Pictures/L120 [--date 2026-10-20] [--slideshow otd.mp4]

Dates come from EXIF DateTimeOriginal when the camera clock was set, otherwise
from the file's modification time (see docs/lab/CREATIVE.md for the caveat).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .video import FFMPEG, FFPROBE, H264, convert

PHOTO_EXT = {".jpg", ".jpeg"}
VIDEO_EXT = {".mov", ".mp4", ".avi"}


# ---------------------------------------------------------------- dates

def media_datetime(path) -> tuple[dt.datetime, str]:
    """(datetime, source) where source is 'exif', 'video-tag' or 'mtime'."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in PHOTO_EXT:
        try:
            ex = Image.open(path).getexif()
            raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)
            if raw and not str(raw).startswith("0000"):
                return dt.datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S"), "exif"
        except Exception:
            pass
    elif ext in VIDEO_EXT:
        try:
            r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format_tags=creation_time",
                                "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
            raw = r.stdout.strip()
            if raw and not raw.startswith(("0000", "1904", "1970")):
                return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None), "video-tag"
        except Exception:
            pass
    return dt.datetime.fromtimestamp(os.path.getmtime(path)), "mtime"


def scan(folder) -> list[dict]:
    folder = Path(folder).expanduser()
    items = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in PHOTO_EXT | VIDEO_EXT:
            when, src = media_datetime(p)
            items.append({"path": p, "kind": "video" if p.suffix.lower() in VIDEO_EXT else "photo",
                          "when": when, "date_source": src})
    items.sort(key=lambda d: (d["when"], d["path"].name))
    return items


# ---------------------------------------------------------------- slideshow

def _ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(max(t, 0.0), 1.0))


@dataclass
class _Shot:
    src: np.ndarray          # pre-scaled RGB canvas, already output aspect
    z0: float; z1: float     # zoom start/end (>=1)
    c0: tuple; c1: tuple     # centre start/end, normalised 0..1
    headroom: float = 1.18


def _prepare(path, W: int, H: int, rng: random.Random, headroom: float = 1.18) -> _Shot:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    sup = 1.15  # supersample so the tightest zoom is still >= 1:1 (no upscaling blur)
    cw, ch = int(W * headroom * sup), int(H * headroom * sup)
    ar, tar = im.width / im.height, W / H
    if ar < 1.0:  # portrait: blurred cover background + whole photo on top (4:3 landscape is cover-cropped)
        bg = ImageOps.fit(im, (cw, ch), Image.LANCZOS).filter(ImageFilter.GaussianBlur(cw / 40))
        bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.35)
        fg = im.copy(); fg.thumbnail((cw, int(ch * 0.94)), Image.LANCZOS)
        bg.paste(fg, ((cw - fg.width) // 2, (ch - fg.height) // 2))
        canvas, zoom_range = bg, (1.0, 1.06)
    else:
        canvas, zoom_range = ImageOps.fit(im, (cw, ch), Image.LANCZOS), (1.0, 1.15)
    a = np.asarray(canvas, np.float32)
    zin = rng.random() < 0.6
    z0, z1 = (zoom_range[0], zoom_range[1]) if zin else (zoom_range[1], zoom_range[0])
    ang = rng.uniform(0, 2 * math.pi)
    d = 0.06 * rng.uniform(0.4, 1.0)
    c0 = (0.5 - d * math.cos(ang), 0.5 - d * math.sin(ang))
    c1 = (0.5 + d * math.cos(ang), 0.5 + d * math.sin(ang))
    return _Shot(a, z0, z1, c0, c1, headroom)


def _frame(shot: _Shot, t: float, W: int, H: int) -> np.ndarray:
    import cv2
    e = _ease(t)
    sh, sw = shot.src.shape[:2]
    z = shot.z0 + (shot.z1 - shot.z0) * e
    # The canvas is `headroom` x the output size; at z=1 we show an output-sized
    # window of it, which leaves margin for the pan. Higher z = tighter window.
    win_w, win_h = sw / shot.headroom / z, sh / shot.headroom / z
    cx = shot.c0[0] + (shot.c1[0] - shot.c0[0]) * e
    cy = shot.c0[1] + (shot.c1[1] - shot.c0[1]) * e
    x0 = np.clip(cx * sw - win_w / 2, 0, sw - win_w)
    y0 = np.clip(cy * sh - win_h / 2, 0, sh - win_h)
    k = W / win_w
    M = np.float32([[k, 0, -x0 * k], [0, k, -y0 * k]])
    return cv2.warpAffine(shot.src, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def slideshow(paths, out_mp4, seconds_per_photo: float = 3.0, width: int = 1920, music=None,
              fps: int = 30, fade: float = 0.8, seed: int = 7, crf: int = 19) -> Path:
    """Ken Burns slideshow with crossfades; optional music (looped/trimmed, faded out)."""
    paths = [Path(p).expanduser() for p in paths if Path(p).suffix.lower() in PHOTO_EXT]
    if not paths:
        raise ValueError("no JPEG photos given")
    W = width - width % 2
    H = int(round(W * 9 / 16)) // 2 * 2
    out = Path(out_mp4).expanduser().resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    fade = min(fade, seconds_per_photo / 2)
    seg = int(round(seconds_per_photo * fps))     # frames between photo starts
    fl = int(round(fade * fps))                   # crossfade frames
    dur_f = seg + fl                              # frames each shot is on screen
    total = seg * (len(paths) - 1) + dur_f
    total_s = total / fps
    rng = random.Random(seed)

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(Path(music).expanduser())]
        afo = max(0.0, total_s - 2.5)
        cmd += ["-map", "0:v", "-map", "1:a", "-af", f"afade=t=in:d=1,afade=t=out:st={afo:.2f}:d=2.5",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total_s:.3f}"]
    enc = H264.copy(); enc[enc.index("-crf") + 1] = str(crf)
    cmd += [*enc, "-r", str(fps), str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    cache: dict[int, _Shot] = {}

    def shot(i):
        if i not in cache:
            for k in [k for k in cache if k < i - 1]:
                del cache[k]
            cache[i] = _prepare(paths[i], W, H, rng)
        return cache[i]

    try:
        for f in range(total):
            i = min(f // seg, len(paths) - 1)
            local = f - i * seg
            img = _frame(shot(i), local / dur_f, W, H)
            if i > 0 and local < fl:  # still fading from the previous shot
                prev = _frame(shot(i - 1), (local + seg) / dur_f, W, H)
                a = _ease((local + 1) / (fl + 1))
                img = prev * (1 - a) + img * a
            if f < fl:  # fade in from black at the start, out at the end
                img = img * ((f + 1) / fl)
            elif f >= total - fl:
                img = img * ((total - f) / fl)
            proc.stdin.write(np.clip(img + 0.5, 0, 255).astype(np.uint8).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err}")
    return out


# ---------------------------------------------------------------- on this day

def on_this_day(folder, date: dt.date | str | None = None, window_days: int = 0) -> list[dict]:
    """Photos/videos from the same month/day in *other* years (optionally +-window_days).

    Returns items sorted oldest first, each with 'years_ago'.
    """
    if isinstance(date, str):
        date = dt.date.fromisoformat(date)
    date = date or dt.date.today()
    hits = []
    for it in scan(folder):
        d = it["when"].date()
        if d.year == date.year:
            continue
        try:
            anniv = d.replace(year=date.year)
        except ValueError:  # Feb 29
            anniv = dt.date(date.year, 3, 1)
        if abs((anniv - date).days) <= window_days:
            hits.append({**it, "years_ago": date.year - d.year})
    return hits


# ---------------------------------------------------------------- album

def _thumb(src: Path, dst: Path, size: int, quality: int) -> tuple[int, int]:
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        with Image.open(dst) as im:
            return im.size
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    im.thumbnail((size, size), Image.LANCZOS)
    im.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
    return im.size


def _video_assets(src: Path, media: Path, stem: str) -> dict:
    mp4 = media / f"{stem}.mp4"
    poster = media / f"{stem}_poster.jpg"
    thumb = media / f"{stem}_t.jpg"
    if not mp4.exists() or mp4.stat().st_mtime < src.stat().st_mtime:
        convert(src, mp4, crf=23, max_width=1280)
    if not poster.exists():
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(mp4)], capture_output=True, text=True)
        mid = max(0.0, float(r.stdout.strip() or 0) / 3)
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{mid:.2f}", "-i", str(mp4),
                        "-frames:v", "1", "-q:v", "3", str(poster)], check=True)
    tw, th = _thumb(poster, thumb, 480, 80)
    return {"src": f"media/{mp4.name}", "poster": f"media/{poster.name}", "thumb": f"media/{thumb.name}",
            "w": tw, "h": th}


def album(folder, out_dir, title: str = "COOLPIX L120", display_size: int = 2560,
          videos: bool = True, workers: int = 6) -> Path:
    """Build a self-contained static HTML album (no CDN, works from file://)."""
    out = Path(out_dir).expanduser().resolve()
    media = out / "media"; media.mkdir(parents=True, exist_ok=True)
    items = [it for it in scan(folder) if videos or it["kind"] == "photo"]
    used = set()

    def stem_for(p: Path) -> str:
        s = p.stem
        n = s; k = 1
        while n in used:
            k += 1; n = f"{s}_{k}"
        used.add(n)
        return n

    jobs = [(it, stem_for(it["path"])) for it in items]

    def build(job):
        it, stem = job
        p = it["path"]
        if it["kind"] == "photo":
            tw, th = _thumb(p, media / f"{stem}_t.jpg", 480, 80)
            _thumb(p, media / f"{stem}.jpg", display_size, 88)
            a = {"src": f"media/{stem}.jpg", "thumb": f"media/{stem}_t.jpg", "w": tw, "h": th}
        else:
            a = _video_assets(p, media, stem)
        return {"kind": it["kind"], "name": p.name, "date": it["when"].isoformat(timespec="minutes"),
                "dsrc": it["date_source"], **a}

    with ThreadPoolExecutor(workers) as ex:
        entries = list(ex.map(build, jobs))
    (out / "index.html").write_text(_ALBUM_HTML.replace("__TITLE__", html.escape(title))
                                    .replace("__DATA__", json.dumps(entries).replace("</", "<\\/")),
                                    encoding="utf-8")
    return out / "index.html"


_ALBUM_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0e0e10;--panel:#17171a;--ink:#ecebe7;--mute:#8d8b86;--line:#26262a;--accent:#ff9a3c}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:5;background:rgba(14,14,16,.86);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.bar{max-width:1500px;margin:0 auto;padding:14px 20px;display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.01em}
.count{color:var(--mute);font-size:13px}
nav{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto}
nav a{color:var(--mute);text-decoration:none;font-size:13px;padding:3px 9px;border-radius:999px;font-variant-numeric:tabular-nums}
nav a:hover{color:var(--ink);background:var(--panel)}
main{max-width:1500px;margin:0 auto;padding:8px 20px 80px}
.otd{margin:22px 0 6px;padding:18px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,#1b1712,#141313)}
.otd h2{margin:0 0 12px;font-size:14px;font-weight:600;color:var(--accent);letter-spacing:.04em;text-transform:uppercase}
section.year>h2{font-size:44px;font-weight:700;letter-spacing:-.02em;margin:46px 0 4px;font-variant-numeric:tabular-nums;scroll-margin-top:70px}
h3{font-size:13px;font-weight:600;color:var(--mute);margin:20px 0 10px;text-transform:uppercase;letter-spacing:.08em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px}
.tile{position:relative;aspect-ratio:4/3;overflow:hidden;border-radius:6px;background:var(--panel);cursor:zoom-in;border:0;padding:0;display:block;width:100%}
.tile img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .35s cubic-bezier(.2,.7,.2,1),opacity .4s;opacity:0}
.tile img.ok{opacity:1}
.tile:hover img{transform:scale(1.035)}
.tile:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.play{position:absolute;left:8px;bottom:8px;width:26px;height:26px;border-radius:50%;background:rgba(0,0,0,.55);display:grid;place-items:center}
.play:after{content:"";border-left:9px solid #fff;border-top:6px solid transparent;border-bottom:6px solid transparent;margin-left:2px}
.lb{position:fixed;inset:0;background:#060607;z-index:20;display:none;flex-direction:column}
.lb.on{display:flex}
.stage{flex:1;display:flex;align-items:center;justify-content:center;min-height:0;padding:16px 64px 0}
.stage img,.stage video{max-width:100%;max-height:100%;object-fit:contain;border-radius:3px;box-shadow:0 20px 60px rgba(0,0,0,.6)}
.cap{display:flex;justify-content:space-between;gap:16px;padding:12px 22px 16px;color:var(--mute);font-size:13px;font-variant-numeric:tabular-nums}
.cap b{color:var(--ink);font-weight:500}
.btn{position:absolute;top:50%;transform:translateY(-50%);width:46px;height:46px;border-radius:50%;border:0;background:rgba(255,255,255,.07);color:#fff;font-size:22px;cursor:pointer}
.btn:hover{background:rgba(255,255,255,.16)}
.prev{left:12px}.next{right:12px}
.close{position:absolute;top:12px;right:14px;transform:none;font-size:20px}
.empty{color:var(--mute);padding:40px 0}
@media (max-width:640px){.grid{grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:3px}.stage{padding:8px 4px 0}.btn.prev,.btn.next{display:none}section.year>h2{font-size:32px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}
</style></head><body>
<header><div class="bar"><h1>__TITLE__</h1><span class="count" id="count"></span><nav id="years"></nav></div></header>
<main id="main"></main>
<div class="lb" id="lb" role="dialog" aria-modal="true" aria-label="Viewer">
  <div class="stage" id="stage"></div>
  <div class="cap"><span id="capL"></span><span id="capR"></span></div>
  <button class="btn prev" id="prev" aria-label="Previous">&#8249;</button>
  <button class="btn next" id="next" aria-label="Next">&#8250;</button>
  <button class="btn close" id="close" aria-label="Close">&#10005;</button>
</div>
<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
const fmt = d => { const x = new Date(d); return x.getDate()+" "+MONTHS[x.getMonth()]+" "+x.getFullYear()+" · "+String(x.getHours()).padStart(2,"0")+":"+String(x.getMinutes()).padStart(2,"0"); };
const main = $("#main");
const photos = DATA.filter(d => d.kind === "photo").length;
$("#count").textContent = photos + " photos · " + (DATA.length - photos) + " videos";
const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { const i = e.target; i.src = i.dataset.src; i.onload = () => i.classList.add("ok"); io.unobserve(i); } }), {rootMargin: "600px"});
function tile(idx) {
  const d = DATA[idx], b = document.createElement("button");
  b.className = "tile"; b.setAttribute("aria-label", d.name + ", " + fmt(d.date));
  const im = document.createElement("img"); im.alt = ""; im.dataset.src = d.thumb; io.observe(im);
  b.append(im); if (d.kind === "video") { const p = document.createElement("span"); p.className = "play"; b.append(p); }
  b.onclick = () => open(idx); return b;
}
// On this day
const now = new Date();
const otd = DATA.map((d, i) => [d, i]).filter(([d]) => { const x = new Date(d.date); return x.getMonth() === now.getMonth() && x.getDate() === now.getDate() && x.getFullYear() !== now.getFullYear(); });
if (otd.length) {
  const s = document.createElement("div"); s.className = "otd";
  s.innerHTML = "<h2>On this day</h2>"; const g = document.createElement("div"); g.className = "grid";
  otd.forEach(([, i]) => g.append(tile(i))); s.append(g); main.append(s);
}
// Years -> months
const byYear = {};
DATA.forEach((d, i) => { const x = new Date(d.date); ((byYear[x.getFullYear()] ??= {})[x.getMonth()] ??= []).push(i); });
const years = Object.keys(byYear).sort((a, b) => b - a);
if (!years.length) main.innerHTML = '<p class="empty">No photos found.</p>';
years.forEach(y => {
  const sec = document.createElement("section"); sec.className = "year"; sec.id = "y" + y;
  sec.innerHTML = "<h2>" + y + "</h2>";
  Object.keys(byYear[y]).sort((a, b) => b - a).forEach(m => {
    const h = document.createElement("h3"); h.textContent = MONTHS[m] + " · " + byYear[y][m].length; sec.append(h);
    const g = document.createElement("div"); g.className = "grid";
    byYear[y][m].forEach(i => g.append(tile(i))); sec.append(g);
  });
  main.append(sec);
  const a = document.createElement("a"); a.href = "#y" + y; a.textContent = y; $("#years").append(a);
});
// Lightbox (navigates in the same order as the page: newest year first)
const order = years.flatMap(y => Object.keys(byYear[y]).sort((a, b) => b - a).flatMap(m => byYear[y][m]));
let cur = -1, lastFocus = null;
function show(i) {
  cur = i; const d = DATA[order[i]], st = $("#stage"); st.innerHTML = "";
  if (d.kind === "video") { const v = document.createElement("video"); v.src = d.src; v.poster = d.poster; v.controls = true; v.autoplay = true; v.playsInline = true; st.append(v); }
  else { const im = document.createElement("img"); im.src = d.src; im.alt = d.name; st.append(im); }
  $("#capL").innerHTML = "<b>" + fmt(d.date) + "</b>" + (d.dsrc === "mtime" ? " (file date)" : "");
  $("#capR").textContent = d.name + "  ·  " + (i + 1) + " / " + order.length;
  [order[i - 1], order[i + 1]].forEach(j => { if (j !== undefined && DATA[j].kind === "photo") new Image().src = DATA[j].src; });
}
function open(idx) { lastFocus = document.activeElement; $("#lb").classList.add("on"); document.body.style.overflow = "hidden"; show(order.indexOf(idx)); $("#close").focus(); }
function close() { $("#lb").classList.remove("on"); $("#stage").innerHTML = ""; document.body.style.overflow = ""; cur = -1; if (lastFocus) lastFocus.focus(); }
const step = k => { if (cur >= 0) show((cur + k + order.length) % order.length); };
$("#prev").onclick = () => step(-1); $("#next").onclick = () => step(1); $("#close").onclick = close;
$("#stage").onclick = e => { if (e.target.id === "stage") close(); };
document.addEventListener("keydown", e => {
  if (cur < 0) return;
  if (e.key === "ArrowRight") step(1); else if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "Escape") close(); else if (e.key === "Home") show(0); else if (e.key === "End") show(order.length - 1);
});
let tx = null;
$("#lb").addEventListener("touchstart", e => tx = e.touches[0].clientX, {passive: true});
$("#lb").addEventListener("touchend", e => { if (tx === null) return; const dx = e.changedTouches[0].clientX - tx; if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1); tx = null; });
</script></body></html>
"""


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m l120.lab.memories", description="Slideshows, albums, on-this-day")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("slideshow", help="Ken Burns MP4 from photos")
    s.add_argument("photos", nargs="+"); s.add_argument("-o", "--out", required=True)
    s.add_argument("--seconds", type=float, default=3.0); s.add_argument("--width", type=int, default=1920)
    s.add_argument("--music"); s.add_argument("--fade", type=float, default=0.8)
    s.add_argument("--shuffle", action="store_true")
    a = sub.add_parser("album", help="static HTML album from a folder")
    a.add_argument("folder"); a.add_argument("-o", "--out-dir", required=True)
    a.add_argument("--title", default="COOLPIX L120"); a.add_argument("--no-video", action="store_true")
    o = sub.add_parser("onthisday", help="same month/day in other years")
    o.add_argument("folder"); o.add_argument("--date", help="YYYY-MM-DD (default today)")
    o.add_argument("--window", type=int, default=0, help="+- days"); o.add_argument("--slideshow")
    o.add_argument("--music")
    args = p.parse_args(argv)
    if args.cmd == "slideshow":
        ph = list(args.photos)
        if args.shuffle:
            random.shuffle(ph)
        else:
            ph.sort(key=lambda x: media_datetime(x)[0])
        print(slideshow(ph, args.out, args.seconds, args.width, args.music, fade=args.fade))
    elif args.cmd == "album":
        print(album(args.folder, args.out_dir, args.title, videos=not args.no_video))
    elif args.cmd == "onthisday":
        hits = on_this_day(args.folder, args.date, args.window)
        for h in hits:
            print(f"{h['years_ago']:>2}y ago  {h['when']:%Y-%m-%d %H:%M}  [{h['date_source']}]  {h['path']}")
        if not hits:
            print("nothing from this day in other years")
        photos = [h["path"] for h in hits if h["kind"] == "photo"]
        if args.slideshow and photos:
            print(slideshow(photos, args.slideshow, music=args.music))


if __name__ == "__main__":
    main()
