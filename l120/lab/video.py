"""Video tools for the L120's 720p MOV clips (Motion-JPEG video + PCM audio).

All heavy lifting is ffmpeg; this module just picks good settings.

    python -m l120.lab.video convert DSCN0123.MOV -o clip.mp4
    python -m l120.lab.video stabilize DSCN0123.MOV -o steady.mp4
    python -m l120.lab.video speed DSCN0123.MOV -o slowmo.mp4 --factor 0.5
    python -m l120.lab.video gif DSCN0123.MOV -o loop.gif --start 2 --dur 3 --width 480
    python -m l120.lab.video bestframe DSCN0123.MOV -o still.jpg
    python -m l120.lab.video info DSCN0123.MOV
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

# Web-friendly H.264 + AAC: plays in every browser, phone and messaging app.
H264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
AAC = ["-c:a", "aac", "-b:a", "160k"]


def _run(args: list[str], cwd: str | None = None) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr.strip()}")


def _out(p) -> Path:
    p = Path(p).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def probe(path) -> dict:
    """Return {width, height, fps, duration, has_audio, vcodec}."""
    r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(num) else 30.0
    return {
        "width": int(v["width"]), "height": int(v["height"]), "fps": round(fps, 3),
        "duration": float(j["format"].get("duration") or v.get("duration") or 0),
        "has_audio": any(s["codec_type"] == "audio" for s in j["streams"]),
        "vcodec": v.get("codec_name"),
    }


def convert(mov, out_mp4, crf: int = 20, max_width: int | None = None) -> Path:
    """MOV (MJPEG/PCM) -> MP4 (H.264/AAC, faststart). Typically ~10x smaller."""
    out = _out(out_mp4)
    vf = [f"scale='min({max_width},iw)':-2"] if max_width else []
    enc = H264.copy(); enc[enc.index("-crf") + 1] = str(crf)
    _run(["-i", str(mov), *(["-vf", ",".join(vf)] if vf else []), *enc, *AAC, str(out)])
    return out


def stabilize(mov, out, smoothing: int = 30, shakiness: int = 6, zoom: int = 0,
              mincontrast: float = 0.15) -> Path:
    """Two-pass vid.stab: detect camera shake, then smooth the path (+ light sharpen)."""
    out = _out(out)
    src = str(Path(mov).expanduser().resolve())
    with tempfile.TemporaryDirectory() as td:
        # Run in the temp dir so the .trf path needs no filter-graph escaping.
        # format=yuv420p matters: on the L120's MJPEG (yuvj422p) vidstabdetect
        # silently finds zero motion fields and the "stabilised" output is unchanged.
        _run(["-i", src, "-vf", f"format=yuv420p,vidstabdetect=shakiness={shakiness}:accuracy=15:"
              f"mincontrast={mincontrast}:result=t.trf",
              "-f", "null", "-"], cwd=td)
        vf = (f"format=yuv420p,vidstabtransform=input=t.trf:smoothing={smoothing}:optzoom=1:zoom={zoom}:interpol=bicubic,"
              "unsharp=5:5:0.6:3:3:0.3")
        _run(["-i", src, "-vf", vf, *H264, *AAC, str(out)], cwd=td)
    return out


def _atempo(factor: float) -> str:
    """atempo only accepts 0.5..2.0 per instance on older builds; chain to be safe."""
    parts, f = [], factor
    while f < 0.5:
        parts.append("atempo=0.5"); f /= 0.5
    while f > 2.0:
        parts.append("atempo=2.0"); f /= 2.0
    parts.append(f"atempo={f:.5f}")
    return ",".join(parts)


def speed(mov, out, factor: float, smooth: bool = True, keep_audio: bool = True) -> Path:
    """Change playback speed. factor<1 = slow motion (0.5 = half speed), >1 = fast.

    With smooth=True, slow-mo synthesises in-between frames with motion-compensated
    interpolation (minterpolate), so 30fps footage at 0.25x still plays at 30fps
    instead of stuttering. It is slow (~1-3x realtime per output second at 720p).
    """
    if factor <= 0:
        raise ValueError("factor must be > 0")
    out = _out(out)
    info = probe(mov)
    fps = info["fps"] or 30
    vf = [f"setpts=PTS/{factor}"]
    if factor < 1 and smooth:
        vf.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
    elif factor > 1:
        vf.append(f"fps={fps}")
    args = ["-i", str(mov), "-vf", ",".join(vf), *H264]
    if keep_audio and info["has_audio"]:
        args += ["-af", _atempo(factor), *AAC]
    else:
        args += ["-an"]
    _run([*args, str(out)])
    return out


def to_gif(mov, out, start: float = 0.0, dur: float = 3.0, width: int = 480, fps: int = 15) -> Path:
    """High-quality GIF via a per-clip palette (palettegen/paletteuse)."""
    out = _out(out)
    fg = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=256:stats_mode=diff[p];"
          "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    _run(["-ss", str(start), "-t", str(dur), "-i", str(mov), "-filter_complex", fg, "-loop", "0", str(out)])
    return out


def extract_best_frame(mov, out, max_samples: int = 300) -> dict:
    """Save the sharpest well-exposed frame as a JPEG.

    Score = variance of the Laplacian (focus/motion-blur measure) on a 640px
    grey copy, multiplied by an exposure penalty for very dark or blown frames.
    Returns {"path", "time", "score", "frames_scored"}.
    """
    import cv2
    import numpy as np

    out = _out(out)
    cap = cv2.VideoCapture(str(mov))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {mov}")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, n // max_samples) if n else 1
    best = (-1.0, None, 0)
    i = scored = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                small = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])),
                                   interpolation=cv2.INTER_AREA)
                g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                sharp = cv2.Laplacian(g, cv2.CV_64F).var()
                m = g.mean() / 255
                clip = (g > 250).mean() + (g < 5).mean()
                expo = max(0.05, 1 - 2.5 * abs(m - 0.45) ** 1.5) * max(0.05, 1 - 2 * clip)
                score = sharp * expo
                scored += 1
                if score > best[0]:
                    best = (score, frame.copy(), i)
        i += 1
    cap.release()
    if best[1] is None:
        raise RuntimeError("no frames decoded")
    rgb = cv2.cvtColor(best[1], cv2.COLOR_BGR2RGB)
    from PIL import Image
    Image.fromarray(rgb).save(out, "JPEG", quality=95)
    return {"path": str(out), "time": round(best[2] / fps, 3), "score": round(best[0], 1),
            "frames_scored": scored}


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m l120.lab.video", description="L120 video tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="MOV -> web MP4 (H.264/AAC)")
    c.add_argument("mov"); c.add_argument("-o", "--out", required=True)
    c.add_argument("--crf", type=int, default=20); c.add_argument("--max-width", type=int)
    s = sub.add_parser("stabilize", help="2-pass vid.stab stabilisation")
    s.add_argument("mov"); s.add_argument("-o", "--out", required=True)
    s.add_argument("--smoothing", type=int, default=30); s.add_argument("--shakiness", type=int, default=6)
    s.add_argument("--mincontrast", type=float, default=0.15, help="lower for dark, flat scenes")
    sp = sub.add_parser("speed", help="slow-mo / timelapse")
    sp.add_argument("mov"); sp.add_argument("-o", "--out", required=True)
    sp.add_argument("--factor", type=float, required=True, help="0.5 = half speed, 2 = double")
    sp.add_argument("--no-smooth", action="store_true", help="skip motion interpolation")
    sp.add_argument("--mute", action="store_true")
    g = sub.add_parser("gif", help="clip -> GIF")
    g.add_argument("mov"); g.add_argument("-o", "--out", required=True)
    g.add_argument("--start", type=float, default=0); g.add_argument("--dur", type=float, default=3)
    g.add_argument("--width", type=int, default=480); g.add_argument("--fps", type=int, default=15)
    b = sub.add_parser("bestframe", help="save the sharpest frame")
    b.add_argument("mov"); b.add_argument("-o", "--out", required=True)
    i = sub.add_parser("info", help="print stream info")
    i.add_argument("mov")
    a = p.parse_args(argv)
    if a.cmd == "convert":
        print(convert(a.mov, a.out, a.crf, a.max_width))
    elif a.cmd == "stabilize":
        print(stabilize(a.mov, a.out, a.smoothing, a.shakiness, mincontrast=a.mincontrast))
    elif a.cmd == "speed":
        print(speed(a.mov, a.out, a.factor, smooth=not a.no_smooth, keep_audio=not a.mute))
    elif a.cmd == "gif":
        print(to_gif(a.mov, a.out, a.start, a.dur, a.width, a.fps))
    elif a.cmd == "bestframe":
        print(json.dumps(extract_best_frame(a.mov, a.out), indent=2))
    elif a.cmd == "info":
        print(json.dumps(probe(a.mov), indent=2))


if __name__ == "__main__":
    main()
