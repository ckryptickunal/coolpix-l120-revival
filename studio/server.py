"""L120 Studio backend: a small stdlib HTTP server around the l120 package.

One camera conversation at a time. Long work (shoot, stack, timelapse, card
listing, import) runs as a single background job whose progress is pushed to
the browser over Server-Sent Events. The USB connection (and the cross-process
lock) is released a few seconds after each operation, so other programs (the
MCP server, scripts) can use the camera between Studio operations.

Work on files already on the Mac (darkroom, memories, merges) runs on a second
job runner ("lab") so it never blocks shooting; see studio/lab.py.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import io
import json
import mimetypes
import os
import re
import subprocess
import threading
import time
import traceback
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import usb.core
from PIL import Image

from l120 import features
from l120.camera import FORMATS, L120, CameraError

from . import lab
from .remote import Remote

STATIC = Path(__file__).resolve().parent / "static"
CAPTURES = features.OUT_ROOT
# Grace period so a burst of thumbnail requests shares one connection.
IDLE_RELEASE_S = float(os.environ.get("L120_STUDIO_IDLE_S", "3"))
FAKE = os.environ.get("L120_STUDIO_FAKE") == "1"
IMPORT_DIR = CAPTURES / "card-import"
if FAKE:  # keep simulated output away from real photos
    features.OUT_ROOT = CAPTURES / "fake"
    IMPORT_DIR = features.OUT_ROOT / "card-import"
lab.init(CAPTURES)
remote = Remote(False, 8120)   # replaced by serve()


class Busy(Exception):
    """The camera is in use (by a Studio job or by another program)."""


class CardChanged(Exception):
    """A cached card entry no longer matches what the camera reports."""


# ---------------------------------------------------------------- events

class Hub:
    """Shared state pushed to every browser over SSE."""

    def __init__(self):
        self.cond = threading.Condition()
        self.version = 0
        idle = {"id": 0, "kind": None, "status": "idle", "done": 0, "total": 0,
                "message": "", "result": None, "error": None, "preview": None}
        self.state = {
            "job": dict(idle),       # camera job
            "lab": dict(idle),       # on-Mac processing job
            "status": None,          # last camera status reading
            "connected": False,      # USB session currently open
            "card": {"stamp": 0, "count": None, "listing": False},
        }

    def update(self, section: str, **kw):
        with self.cond:
            if isinstance(self.state[section], dict):
                self.state[section].update(kw)
            else:
                self.state[section] = kw.get("value")
            self.version += 1
            self.cond.notify_all()

    def snapshot(self):
        with self.cond:
            return self.version, json.loads(json.dumps(self.state))

    def wait(self, version: int, timeout: float):
        with self.cond:
            self.cond.wait_for(lambda: self.version != version, timeout)
        return self.snapshot()


hub = Hub()


# ---------------------------------------------------------------- camera

class CameraHost:
    """Owns the L120 connection; serialises every camera call in this process."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cam: L120 | None = None
        self.last_used = 0.0
        self.device = None
        threading.Thread(target=self._idle_release, daemon=True).start()

    def _connect(self):
        if FAKE:
            from .fake import FakeL120
            self.cam = getattr(self, "_fake", None) or FakeL120()
            self._fake = self.cam
            hub.update("connected", value=True)
            self.device = self.cam.device_info()
            return
        cam = L120()
        try:
            cam.connect(lock_timeout_s=2)
        except CameraError as e:
            self._drop(cam)
            if "busy" in str(e).lower():
                raise Busy("The camera is being used by another program. Try again in a moment.")
            raise
        except Exception:
            self._drop(cam)
            raise
        self.cam = cam
        hub.update("connected", value=True)
        if self.device is None:
            self.device = cam.device_info()

    @staticmethod
    def _drop(cam: L120):
        try:
            cam.close()
        except Exception:
            pass
        lf = getattr(cam, "_lockf", None)  # close() skips the lock if USB never opened
        if lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_UN)
                lf.close()
            except Exception:
                pass
            cam._lockf = None

    def release(self):
        if self.cam is not None:
            self._drop(self.cam)
            self.cam = None
            hub.update("connected", value=False)

    @contextmanager
    def use(self, wait: float = 20):
        if not self.lock.acquire(timeout=wait):
            raise Busy("The camera is busy with another task.")
        try:
            if self.cam is None:
                self._connect()
            try:
                yield self.cam
            except (usb.core.USBError, CameraError) as e:
                # A dropped USB link (auto-off, cable) leaves the session useless.
                if isinstance(e, usb.core.USBError) or "PTP response" not in str(e):
                    self.release()
                raise
        finally:
            self.last_used = time.time()
            self.lock.release()

    def release_soon(self):
        """Let go of the camera now (used before slow post-processing)."""
        if self.cam is not None and self.lock.acquire(blocking=False):
            try:
                self.release()
            finally:
                self.lock.release()

    def _idle_release(self):
        while True:
            time.sleep(1)
            if self.cam is not None and time.time() - self.last_used > IDLE_RELEASE_S:
                if self.lock.acquire(blocking=False):
                    try:
                        if time.time() - self.last_used > IDLE_RELEASE_S:
                            self.release()
                    finally:
                        self.lock.release()


camera = CameraHost()


def friendly_error(e: Exception) -> str:
    if isinstance(e, (Busy, ValueError)):
        return str(e)
    if isinstance(e, usb.core.USBTimeoutError):
        return ("The camera isn't answering over USB. If it's on, switch it off and on again "
                "(and turn off Auto off in the Setup menu).")
    if isinstance(e, usb.core.USBError):
        return ("Lost contact with the camera over USB. Is it still switched on? "
                "(Turn off Auto off in the camera's Setup menu.)")
    return str(e) or e.__class__.__name__


def parse_clock(s: str) -> str | None:
    m = re.match(r"(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)", s or "")
    return dt.datetime(*map(int, m.groups())).isoformat() if m else None


def read_status(cam: L120) -> dict:
    clock = cam.clock()
    return {
        "model": camera.device["model"], "firmware": camera.device["firmware"],
        "serial": camera.device["serial"], "battery": cam.battery(),
        "storage": cam.storage(), "clock_raw": clock, "clock": parse_clock(clock),
        "read_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- card cache

class Card:
    def __init__(self):
        self.files: dict[int, dict] = {}
        self.folders: dict[int, str] = {}
        self.thumbs: dict[tuple, bytes] = {}

    def folder_name(self, cam: L120, parent: int) -> str:
        if parent not in self.folders:
            try:
                self.folders[parent] = cam.object_info(parent).filename if parent else ""
            except Exception:
                self.folders[parent] = f"folder-{parent:x}"
        return self.folders[parent]

    def entry(self, cam: L120, info) -> dict:
        folder = self.folder_name(cam, info.parent)
        local = IMPORT_DIR / folder / info.filename
        return {"handle": info.handle, "name": info.filename, "folder": folder, "size": info.size,
                "captured": parse_clock(info.captured), "width": info.width, "height": info.height,
                "type": FORMATS.get(info.format, "file"),
                "on_mac": local.exists() and local.stat().st_size == info.size}

    def checked(self, cam: L120, handle: int) -> dict:
        """The cached entry, after confirming the handle still points at the same file."""
        e = self.files.get(handle)
        if e is None:
            raise CardChanged("Unknown file. Refresh the card list.")
        info = cam.object_info(handle)
        if info.filename != e["name"] or info.size != e["size"]:
            raise CardChanged("The card changed since it was listed. Refresh the card list.")
        return e

    def local_path(self, e: dict) -> Path:
        return IMPORT_DIR / e["folder"] / e["name"]


card = Card()


def card_changed():
    hub.update("card", stamp=hub.state["card"]["stamp"] + 1, count=len(card.files))


# ---------------------------------------------------------------- jobs

class Jobs:
    """One background job at a time per runner. section = hub key ("job" or "lab").

    Camera jobs: fn(cam) runs while holding the camera; post(result), if given,
    runs after the camera is released (e.g. merging frames). Lab jobs: fn(update, stop).
    """

    def __init__(self, section: str = "job", needs_camera: bool = True):
        self.section = section
        self.needs_camera = needs_camera
        self.lock = threading.Lock()
        self.running = False
        self.stop = threading.Event()
        self.preview: bytes | None = None
        self.next_id = 1

    def update(self, **kw):
        hub.update(self.section, **kw)

    def start(self, kind: str, fn, total: int = 0, message: str = "", post=None):
        with self.lock:
            if self.running:
                what = "camera" if self.needs_camera else "processing"
                raise Busy(f"Busy: {hub.state[self.section]['kind']} in progress ({what}).")
            self.running = True
            jid, self.next_id = self.next_id, self.next_id + 1
        self.stop.clear()
        self.preview = None
        self.update(id=jid, kind=kind, status="running", done=0, total=total,
                    message=message, result=None, error=None, preview=None, started=time.time())

        def run():
            try:
                if self.needs_camera:
                    with camera.use(wait=60) as cam:
                        result = fn(cam)
                        try:
                            hub.update("status", value=read_status(cam))
                        except Exception:
                            pass
                    camera.release_soon()
                    if post:
                        result = post(result)
                else:
                    result = fn(self.update, self.stop.is_set)
                self.update(status="cancelled" if self.stop.is_set() else "done", result=result, message="")
            except Exception as e:
                traceback.print_exc()
                self.update(status="error", error=friendly_error(e), message="")
            finally:
                with self.lock:
                    self.running = False

        threading.Thread(target=run, daemon=True).start()
        return jid

    def set_preview(self, img_array, max_px=1600):
        im = Image.fromarray(img_array) if not isinstance(img_array, Image.Image) else img_array
        im = im.copy()
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
        self.preview = buf.getvalue()


jobs = Jobs("job", needs_camera=True)
labjobs = Jobs("lab", needs_camera=False)


def rel_url(p: str | Path | None) -> str | None:
    if not p:
        return None
    return "/files/" + str(Path(p).resolve().relative_to(CAPTURES.resolve()))


def job_shoot(keep_on_card: bool):
    def fn(cam: L120):
        hub.update("job", message="Focusing & shooting…")
        data, info = cam.capture(keep_on_card=keep_on_card)
        out = features._session_dir("photo") / info.filename
        out.write_bytes(data)
        if keep_on_card and card.files:
            card.files[info.handle] = card.entry(cam, info)
            card_changed()
        hub.update("job", done=1, total=1)
        return {"photo": rel_url(out), "path": lab.rel(out), "name": info.filename, "size": len(data),
                "width": info.width, "height": info.height, "kept_on_card": keep_on_card}
    return fn


def job_stack(frames: int, mode: str):
    def fn(cam: L120):
        hub.update("job", message=f"Frame 1 of {frames}…")

        def progress(done, total, merged):
            jobs.set_preview(merged())
            hub.update("job", done=done, total=total, preview=f"/api/preview?n={done}",
                       message="Merging…" if done == total else f"Frame {done + 1} of {total}…")

        r = features.stack(cam, frames, mode, progress=progress, should_stop=jobs.stop.is_set)
        return {**r, "photo": rel_url(r["result"]), "path": lab.rel(r["result"]) if r["result"] else None,
                "folder": rel_url(r["folder"]), "mode": mode}
    return fn


def job_timelapse(count: int, interval: float, fps: int):
    def fn(cam: L120):
        hub.update("job", message=f"Frame 1 of {count}…")

        def progress(done, total, frame):
            hub.update("job", done=done, total=total, preview=rel_url(frame),
                       message="Rendering MP4…" if done == total else
                       f"Frame {done} of {total} saved. Next in ≤{interval:g} s")

        r = features.timelapse(cam, count, interval, fps=fps, progress=progress,
                               should_stop=jobs.stop.is_set)
        if jobs.stop.is_set() and r["frames"] > 1:
            hub.update("job", message="Rendering MP4 from the frames so far…")
        return {**r, "video": rel_url(r.get("video")), "folder": rel_url(r["folder"])}
    return fn


def job_night(frames: int):
    """Shoot a burst for night mode; the merge runs after the camera is released."""
    def fn(cam: L120):
        d = features._session_dir("night")
        paths = []
        for i in range(frames):
            if jobs.stop.is_set():
                break
            hub.update("job", message=f"Frame {i + 1} of {frames}… hold still")
            data, _ = cam.capture(keep_on_card=False)
            p = d / f"frame_{i + 1:02d}.jpg"
            p.write_bytes(data)
            paths.append(p)
            hub.update("job", done=i + 1, preview=rel_url(p))
        return paths

    def post(paths):
        if len(paths) < 2:
            return {"photo": rel_url(paths[0]) if paths else None, "path": lab.rel(paths[0]) if paths else None,
                    "label": "Night · too few frames to merge"}
        hub.update("job", message=f"Aligning and merging {len(paths)} frames… (camera is free again)")
        out = paths[0].parent / "night.jpg"
        r = lab.night_merge(paths, out)
        hub.update("job", done=frames + 1)
        return {**r, "photo": rel_url(out), "path": lab.rel(out)}
    return fn, post


def job_list_card():
    def fn(cam: L120):
        hub.update("card", listing=True)
        hub.update("job", message="Reading the card folder by folder…")
        try:
            infos = cam.list_files()
            files = {}
            for i, info in enumerate(infos):
                files[info.handle] = card.entry(cam, info)
                hub.update("job", done=i + 1, total=len(infos))
            card.files = files
        finally:
            hub.update("card", listing=False)
        card_changed()
        return {"count": len(card.files)}
    return fn


def job_import(handles: list[int]):
    def fn(cam: L120):
        copied, skipped, failed = [], 0, []
        for i, h in enumerate(handles):
            if jobs.stop.is_set():
                break
            e = card.files.get(h)
            name = f"{e['folder']}/{e['name']}" if e else str(h)
            hub.update("job", done=i, total=len(handles), message=f"Copying {name}…")
            try:
                e = card.checked(cam, h)
                target = card.local_path(e)
                if target.exists() and target.stat().st_size == e["size"]:
                    skipped += 1
                else:
                    data = cam.download(h)  # read-only; nothing on the card is touched
                    if len(data) != e["size"]:
                        raise CameraError("short download")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(target.suffix + ".part")
                    tmp.write_bytes(data)
                    tmp.rename(target)
                    copied.append(name)
                e["on_mac"] = True
            except (usb.core.USBError,):
                raise
            except Exception as ex:
                failed.append(f"{name}: {ex}")
        hub.update("job", done=len(handles))
        card_changed()
        return {"copied": len(copied), "skipped": skipped, "failed": failed,
                "folder": str(IMPORT_DIR.relative_to(CAPTURES.parent))}
    return fn


# ---------------------------------------------------------------- live view

def video_devices() -> list[dict]:
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                           capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return []
    out, devices, section = p.stderr, [], None
    for line in out.splitlines():
        if "video devices" in line:
            section = "video"
        elif "audio devices" in line:
            section = "audio"
        else:
            m = re.search(r"\] \[(\d+)\] (.+)$", line)
            if m and section == "video":
                name = m.group(2).strip()
                devices.append({"index": int(m.group(1)), "name": name,
                                "likely_capture": not re.search(
                                    r"FaceTime|MacBook|Desk View|Capture screen|iPhone|Camera$", name, re.I)
                                    or bool(re.search(r"USB|HDMI|Video|Capture|Cam Link|UVC|AV", name)
                                            and "Capture screen" not in name)})
    return devices


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "L120Studio/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("L120_STUDIO_LOG"):
            super().log_message(fmt, *args)

    # -- helpers
    def send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})

    def error_json(self, e: Exception):
        code = 400 if isinstance(e, ValueError) else 409 if isinstance(e, (Busy, CardChanged)) else 503 if isinstance(e, usb.core.USBError) else 500
        self.json({"error": friendly_error(e), "busy": isinstance(e, Busy)}, code)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def send_file(self, path: Path, download_name: str | None = None):
        if not path.is_file():
            return self.json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        extra = {"Cache-Control": "no-cache"}
        if download_name:
            extra["Content-Disposition"] = f'attachment; filename="{download_name}"'
        self.send(200, path.read_bytes(), ctype, extra)

    def q(self, name: str, default: str = "") -> str:
        return parse_qs(urlparse(self.path).query).get(name, [default])[0]

    def gate(self) -> bool:
        """Access control: loopback with a local Host header, or (with --lan) the phone token."""
        ok, set_cookie = remote.check(self.client_address[0], self.headers.get("Host", ""), self.path,
                                      self.headers.get("Cookie"))
        if not ok:
            self.send(403, b"Forbidden: open the link from the Studio's phone QR code.", "text/plain")
            return False
        if set_cookie:   # trade the ?t= token for a cookie and clean the address bar
            self.send(303, b"", "text/plain", {"Location": urlparse(self.path).path or "/",
                                               "Set-Cookie": remote.cookie()})
            return False
        return True

    # -- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.gate():
            return
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/index.html"):
                return self.send_file(STATIC / "index.html")
            if path.startswith("/static/"):
                return self.safe_file(STATIC, path[len("/static/"):])
            if path.startswith("/files/"):
                return self.safe_file(CAPTURES, path[len("/files/"):])
            if path == "/api/state":
                return self.json(hub.snapshot()[1])
            if path == "/api/events":
                return self.events()
            if path == "/api/preview":
                if not jobs.preview:
                    return self.json({"error": "no preview"}, 404)
                return self.send(200, jobs.preview, "image/jpeg", {"Cache-Control": "no-store"})
            if path == "/api/card":
                files = sorted(card.files.values(), key=lambda e: (e["captured"] or "", e["handle"]), reverse=True)
                return self.json({"files": files, "stamp": hub.state["card"]["stamp"]})
            m = re.fullmatch(r"/api/card/(thumb|file)/(\d+)", path)
            if m:
                return self.card_object(m.group(1), int(m.group(2)))
            if path == "/api/liveview/devices":
                return self.json({"devices": video_devices()})
            # ---- on-Mac library, reports, darkroom, memories
            if path == "/api/library":
                return self.json({"files": lab.library()})
            if path == "/api/thumb":
                return self.send(200, lab.thumb(self.q("path"), int(self.q("size", "360"))), "image/jpeg",
                                 {"Cache-Control": "max-age=600"})
            if path == "/api/report":
                return self.json(lab.report(self.q("path")))
            if path == "/api/looks":
                return self.json({"looks": lab.look_list()})
            if path == "/api/look":
                return self.send(200, lab.look_preview(self.q("path"), self.q("look", "original"),
                                                       int(self.q("size", "240")), self.q("date") == "1"),
                                 "image/jpeg", {"Cache-Control": "max-age=600"})
            if path == "/api/memories/folders":
                return self.json({"folders": lab.memory_folders()})
            if path == "/api/memories/onthisday":
                return self.json(lab.on_this_day(self.q("folder", "card-import"), self.q("date") or None,
                                                 int(self.q("window", "0"))))
            if path == "/api/remote":
                local = ipaddress_is_loopback(self.client_address[0])
                return self.json({"enabled": remote.enabled, "url": remote.url if local else None,
                                  "qr": remote.qr_svg() if local else None, "phone": not local})
            return self.json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self.error_json(e)

    def do_POST(self):
        if not self.gate():
            return
        path = urlparse(self.path).path
        # JSON only: browsers can't send that cross-site without a CORS preflight we never grant.
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.json({"error": "JSON body required"}, 415)
        try:
            b = self.body()
            if path == "/api/status":
                if jobs.running:
                    raise Busy("The camera is busy with a job; status will refresh when it finishes.")
                with camera.use(wait=10) as cam:
                    st = read_status(cam)
                hub.update("status", value=st)
                return self.json(st)
            if path == "/api/clock/sync":
                if jobs.running:
                    raise Busy("Wait for the current job to finish.")
                with camera.use(wait=10) as cam:
                    cam.set_clock(dt.datetime.now())
                    st = read_status(cam)
                hub.update("status", value=st)
                return self.json(st)
            if path == "/api/release":
                if jobs.running:
                    raise Busy("A job is using the camera.")
                with camera.lock:
                    camera.release()
                return self.json({"ok": True})
            if path == "/api/shoot":
                return self.json({"job": jobs.start("shoot", job_shoot(bool(b.get("keep_on_card", True))), 1)})
            if path == "/api/stack":
                frames = int(b.get("frames", 10))
                mode = b.get("mode", "lighten")
                if not 2 <= frames <= 200:
                    return self.json({"error": "Frames must be 2–200."}, 400)
                if mode not in ("lighten", "mean", "darken", "median"):
                    return self.json({"error": "Unknown mode."}, 400)
                return self.json({"job": jobs.start("stack", job_stack(frames, mode), frames)})
            if path == "/api/timelapse":
                count, interval, fps = int(b.get("count", 30)), float(b.get("interval", 10)), int(b.get("fps", 24))
                if not 2 <= count <= 5000 or interval < 5 or not 1 <= fps <= 60:
                    return self.json({"error": "Count 2–5000, interval ≥ 5 s, fps 1–60."}, 400)
                return self.json({"job": jobs.start("timelapse", job_timelapse(count, interval, fps), count)})
            if path == "/api/night":
                frames = int(b.get("frames", 6))
                if not 2 <= frames <= 20:
                    return self.json({"error": "Frames must be 2–20."}, 400)
                fn, post = job_night(frames)
                return self.json({"job": jobs.start("night", fn, frames + 1, post=post)})
            if path == "/api/lab/look":
                return self.json({"job": labjobs.start("look", lab.job_apply_look(
                    b.get("path", ""), b.get("look", ""), bool(b.get("datestamp"))))})
            if path == "/api/lab/fusion":
                return self.json({"job": labjobs.start("hdr", lab.job_fusion(b.get("paths", [])))})
            if path == "/api/lab/panorama":
                return self.json({"job": labjobs.start("panorama", lab.job_panorama(b.get("paths", [])))})
            if path == "/api/lab/night":
                return self.json({"job": labjobs.start("night merge", lab.job_night_merge(b.get("paths", [])))})
            if path == "/api/memories/album":
                return self.json({"job": labjobs.start("album", lab.job_album(
                    b.get("folder", "card-import"), b.get("title", "")))})
            if path == "/api/memories/slideshow":
                secs = float(b.get("seconds", 3))
                if not 1 <= secs <= 15:
                    return self.json({"error": "Seconds per photo must be 1–15."}, 400)
                return self.json({"job": labjobs.start("slideshow", lab.job_slideshow(
                    b.get("folder", "card-import"), secs, b.get("paths") or None))})
            if path == "/api/card/list":
                return self.json({"job": jobs.start("card", job_list_card())})
            if path == "/api/card/import":
                handles = [int(h) for h in b.get("handles", []) if int(h) in card.files]
                if not handles:
                    return self.json({"error": "Nothing selected."}, 400)
                return self.json({"job": jobs.start("import", job_import(handles), len(handles))})
            if path == "/api/cancel":
                jobs.stop.set()
                if jobs.running:
                    hub.update("job", message="Stopping after the current frame…")
                return self.json({"ok": True})
            return self.json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            if not isinstance(e, Busy):
                traceback.print_exc()
            self.error_json(e)

    def safe_file(self, root: Path, rel: str):
        p = (root / rel).resolve()
        if not p.is_relative_to(root.resolve()):
            return self.json({"error": "forbidden"}, 403)
        return self.send_file(p)

    def card_object(self, kind: str, handle: int):
        e = card.files.get(handle)
        if e is None:
            return self.json({"error": "Unknown file. Refresh the card list."}, 404)
        if kind == "file":
            local = card.local_path(e)
            if not (local.exists() and local.stat().st_size == e["size"]):
                if jobs.running:
                    raise Busy("The camera is busy; try again when the job finishes.")
                with camera.use(wait=30) as cam:
                    e = card.checked(cam, handle)
                    data = cam.download(handle)  # read-only
                if len(data) != e["size"]:
                    raise CameraError("Short download; try again.")
                local.parent.mkdir(parents=True, exist_ok=True)
                tmp = local.with_suffix(local.suffix + ".part")
                tmp.write_bytes(data)
                tmp.rename(local)
                e["on_mac"] = True
            q = urlparse(self.path).query
            return self.send_file(local, f"{e['folder']}_{e['name']}" if "download" in q else None)
        key = (handle, e["name"], e["size"])
        if key not in card.thumbs:
            if jobs.running:
                raise Busy("busy")
            with camera.use(wait=30) as cam:
                card.checked(cam, handle)
                card.thumbs[key] = cam.thumbnail(handle)
        self.send(200, card.thumbs[key], "image/jpeg", {"Cache-Control": "max-age=3600"})

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        version, state = hub.snapshot()
        try:
            self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
            self.wfile.flush()
            while True:
                new_version, state = hub.wait(version, 15)
                if new_version == version:
                    self.wfile.write(b": ping\n\n")
                else:
                    version = new_version
                    self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True


def ipaddress_is_loopback(ip: str) -> bool:
    import ipaddress
    a = ipaddress.ip_address(ip.split("%")[0])
    return a.is_loopback or bool(getattr(a, "ipv4_mapped", None) and a.ipv4_mapped.is_loopback)


def serve(port=8120, lan=False):
    global remote
    CAPTURES.mkdir(parents=True, exist_ok=True)
    remote = Remote(lan, port)
    httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    print(f"L120 Studio running at http://localhost:{port}{'  [FAKE CAMERA]' if FAKE else ''}  (Ctrl+C to stop)",
          flush=True)
    remote.print_banner()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera.release()
