"""Direct USB (PTP) driver for the Nikon COOLPIX L120.

What the L120 exposes over USB (probed on firmware V1.1):
  shutter (InitiateCapture), file list/download/delete/upload, thumbnails,
  battery level, and the camera clock. Nothing else: no zoom, exposure,
  live view or video control.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import fcntl
import struct
import subprocess
import time
from dataclasses import dataclass

import usb.backend.libusb1
import usb.core
import usb.util

NIKON_VID, L120_PID = 0x04B0, 0x0185
LOCK_PATH = "/tmp/nikon-l120.lock"
OK = 0x2001
_backend = usb.backend.libusb1.get_backend(
    find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib")

# PTP object formats -> extension
FORMATS = {0x3801: "jpg", 0x300D: "mov", 0x3001: "dir", 0x3006: "dpof", 0x3000: "bin", 0x3800: "img"}


class CameraError(RuntimeError):
    pass


@dataclass
class ObjectInfo:
    handle: int
    filename: str
    size: int
    format: int
    parent: int
    captured: str
    width: int
    height: int


class _Reader:
    def __init__(self, b: bytes):
        self.b, self.o = b, 0

    def u(self, fmt: str):
        v = struct.unpack_from("<" + fmt, self.b, self.o)[0]
        self.o += struct.calcsize(fmt)
        return v

    def s(self) -> str:
        n = self.u("B")
        v = self.b[self.o:self.o + 2 * n].decode("utf-16-le", "replace").rstrip("\x00")
        self.o += 2 * n
        return v

    def arr(self, fmt="H"):
        return [self.u(fmt) for _ in range(self.u("I"))]


def _ptp_str(s: str) -> bytes:
    s += "\x00"
    return struct.pack("<B", len(s)) + s.encode("utf-16-le")


def _release_macos_daemons():
    """macOS's Image Capture daemons grab PTP cameras; stop them so we can claim USB."""
    for name in ("mscamerad-xpc", "ptpcamerad"):
        subprocess.run(["pkill", "-9", "-f", name], capture_output=True)


class L120:
    OUT, IN, INT = 0x01, 0x82, 0x83

    def __init__(self):
        self.dev = None
        self._tid = 0
        self._evbuf = bytearray()
        self._folder: int | None = None  # current shooting folder handle

    # ---------- connection ----------
    def connect(self, attempts: int = 8, lock_timeout_s: float = 600):
        # By default go through the broker process, which keeps the USB claim so
        # macOS's Image Capture service can't grab (and stall) the camera between
        # operations. L120_DIRECT=1 talks to USB from this process instead.
        if not getattr(self, "_direct", False) and os.environ.get("L120_DIRECT") != "1":
            from .broker import Client, ensure_broker
            ensure_broker()
            self.__class__ = RemoteL120
            self._client = Client()
            return self
        # Only one process can talk to the camera; serialize across processes.
        self._lockf = open(LOCK_PATH, "w")
        start = time.time()
        while True:
            try:
                fcntl.flock(self._lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - start > lock_timeout_s:
                    raise CameraError("Camera is busy in another process (lock held)")
                time.sleep(0.5)
        last = None
        for _ in range(attempts):
            self.dev = usb.core.find(idVendor=NIKON_VID, backend=_backend)
            if self.dev is None:
                raise CameraError("L120 not found on USB. Is it switched on and plugged in?")
            _release_macos_daemons()
            try:
                try:
                    self.dev.set_configuration()
                except usb.core.USBError:
                    pass
                usb.util.claim_interface(self.dev, 0)
                break
            except usb.core.USBError as e:
                last = e
                time.sleep(0.4)
        else:
            raise CameraError(f"Could not claim the camera: {last}")
        # The camera may still be busy serving macOS's card listing (~60 s): wait it out.
        rc, _, _ = self._transact(0x1002, (1,), timeout=120000)  # OpenSession
        if rc not in (OK, 0x201E):  # 0x201E = session already open
            raise CameraError(f"OpenSession failed: {hex(rc)}")
        self.drain_events()
        if os.environ.get("L120_NO_CLOCK_SYNC") != "1":
            self.auto_sync_clock()
        return self

    def auto_sync_clock(self, tolerance_s: float = 60) -> str | None:
        """Set the camera clock from the Mac whenever it's off by more than `tolerance_s`.
        The L120 loses its clock on every battery swap (back to 2011-01-01), which is why
        so many old photos carry wrong dates. Returns a note when it changed the clock."""
        try:
            cam_t = dt.datetime.strptime(self.clock()[:15], "%Y%m%dT%H%M%S")
        except (ValueError, CameraError):
            cam_t = None
        now = dt.datetime.now()
        if cam_t is not None and abs((now - cam_t).total_seconds()) <= tolerance_s:
            return None
        before = cam_t.isoformat() if cam_t else "unreadable"
        after = self.set_clock(now)
        note = f"camera clock was {before}, set to {after}"
        try:
            with open("/tmp/l120-clock.log", "a") as f:
                f.write(f"{now.isoformat(timespec='seconds')} {note}\n")
        except OSError:
            pass
        return note

    def close(self):
        if self.dev is None:
            return
        try:
            self._transact(0x1003)
        except Exception:
            pass
        try:
            usb.util.release_interface(self.dev, 0)
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
        self.dev = None
        if getattr(self, "_lockf", None):
            fcntl.flock(self._lockf, fcntl.LOCK_UN)
            self._lockf.close()
            self._lockf = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *a):
        self.close()

    # ---------- PTP transport ----------
    def _transact(self, code, params=(), data_out: bytes | None = None, timeout=60000):
        self._tid += 1
        tid = self._tid
        self.dev.write(self.OUT, struct.pack("<IHHI", 12 + 4 * len(params), 1, code, tid)
                       + b"".join(struct.pack("<I", p) for p in params), timeout=timeout)
        if data_out is not None:
            self.dev.write(self.OUT, struct.pack("<IHHI", 12 + len(data_out), 2, code, tid) + data_out,
                           timeout=timeout)
        data = bytearray()
        while True:
            buf = bytearray(self.dev.read(self.IN, 8 * 1024 * 1024, timeout=timeout))
            if len(buf) < 12:
                continue
            length, ctype, rcode, _ = struct.unpack("<IHHI", buf[:12])
            while len(buf) < length:
                buf += self.dev.read(self.IN, 8 * 1024 * 1024, timeout=timeout)
            if ctype == 2:
                data += buf[12:length]
            elif ctype == 3:
                rp = [struct.unpack("<I", buf[i:i + 4])[0] for i in range(12, length, 4)]
                return rcode, bytes(data), rp

    def _ok(self, code, params=(), data_out=None, what=""):
        rc, data, rp = self._transact(code, params, data_out)
        if rc != OK:
            raise CameraError(f"{what or hex(code)} failed: PTP response {hex(rc)}")
        return data, rp

    # ---------- events ----------
    def _read_events(self, timeout_ms: int):
        # Interrupt endpoint has 8-byte packets, so one event can span several reads.
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            try:
                self._evbuf += bytes(self.dev.read(self.INT, 64, timeout=max(1, int((deadline - time.time()) * 1000))))
            except usb.core.USBTimeoutError:
                break
            if len(self._evbuf) >= 4 and len(self._evbuf) >= struct.unpack("<I", self._evbuf[:4])[0]:
                break
        events = []
        while len(self._evbuf) >= 12:
            length = struct.unpack("<I", self._evbuf[:4])[0]
            if length < 12 or length > 64:  # garbage; resync
                self._evbuf.clear()
                break
            if len(self._evbuf) < length:
                break
            pkt, self._evbuf = bytes(self._evbuf[:length]), self._evbuf[length:]
            _, _, code, _ = struct.unpack("<IHHI", pkt[:12])
            params = [struct.unpack("<I", pkt[i:i + 4])[0] for i in range(12, length, 4)]
            events.append((code, params))
        return events

    def drain_events(self):
        for _ in range(5):
            if not self._read_events(50):
                break
        self._evbuf.clear()

    # ---------- info ----------
    def device_info(self) -> dict:
        r = _Reader(self._ok(0x1001)[0])
        r.u("H"); r.u("I"); r.u("H"); r.s(); r.u("H")
        ops, events, props, capfmt, imgfmt = r.arr(), r.arr(), r.arr(), r.arr(), r.arr()
        return {"manufacturer": r.s(), "model": r.s(), "firmware": r.s(), "serial": r.s(),
                "operations": [hex(x) for x in ops], "events": [hex(x) for x in events],
                "properties": [hex(x) for x in props]}

    def _prop(self, code):
        data, _ = self._ok(0x1015, (code,))
        return data

    def battery(self) -> int:
        return self._prop(0x5001)[0]

    def clock(self) -> str:
        return _Reader(self._prop(0x5011)).s()

    def set_clock(self, when: dt.datetime | None = None) -> str:
        when = dt.datetime.fromisoformat(when) if isinstance(when, str) else (when or dt.datetime.now())
        self._ok(0x1016, (0x5011,), _ptp_str(when.strftime("%Y%m%dT%H%M%S")), "Set clock")
        return self.clock()

    def storage(self) -> dict:
        ids = _Reader(self._ok(0x1004)[0]).arr("I")
        if not ids:
            return {"card": False}
        r = _Reader(self._ok(0x1005, (ids[0],))[0])
        stype, fstype, access, cap, free, free_imgs = r.u("H"), r.u("H"), r.u("H"), r.u("Q"), r.u("Q"), r.u("I")
        return {"card": True, "storage_id": hex(ids[0]), "capacity_mb": cap // 2**20,
                "free_mb": free // 2**20, "free_shots": free_imgs, "read_only": access != 0}

    # ---------- files ----------
    def object_info(self, handle: int) -> ObjectInfo:
        r = _Reader(self._ok(0x1008, (handle,))[0])
        r.u("I"); fmt = r.u("H"); r.u("H"); size = r.u("I"); r.u("H"); r.u("I"); r.u("I"); r.u("I")
        w, h = r.u("I"), r.u("I"); r.u("I"); parent = r.u("I"); r.u("H"); r.u("I"); r.u("I")
        name = r.s(); captured = r.s()
        return ObjectInfo(handle, name, size, fmt, parent, captured, w, h)

    def _handles(self, parent: int = 0, fmt: int = 0) -> list[int]:
        """parent=0: whole card (SLOW on the L120, 40-60 s). parent=<folder>: one folder (instant).
        parent=0xFFFFFFFF: card root only."""
        return _Reader(self._ok(0x1007, (0xFFFFFFFF, fmt, parent))[0]).arr("I")

    def shooting_folder(self) -> int:
        """Handle of the folder new shots go to (highest xxxNIKON under DCIM), found without a full listing."""
        dcim = [h for h in self._handles(0xFFFFFFFF) if self.object_info(h).filename.upper() == "DCIM"]
        if not dcim:
            raise CameraError("No DCIM folder on card")
        folders = [(self.object_info(h).filename, h) for h in self._handles(dcim[0])]
        folders = [f for f in folders if re.fullmatch(r"\d{3}\w{5}", f[0])]
        if not folders:
            raise CameraError("No image folder under DCIM")
        return max(folders)[1]

    def folders(self) -> dict[int, str]:
        """Image folders under DCIM, {handle: name}. Fast (no full-card listing)."""
        dcim = [h for h in self._handles(0xFFFFFFFF) if self.object_info(h).filename.upper() == "DCIM"]
        if not dcim:
            return {}
        return {h: self.object_info(h).filename for h in self._handles(dcim[0])
                if self.object_info(h).format == 0x3001}

    def list_files(self) -> list[ObjectInfo]:
        """All photos/videos, walked folder by folder (the whole-card query takes ~60 s on the L120)."""
        out = []
        for fh in self.folders():
            out += [i for i in (self.object_info(h) for h in self._handles(fh)) if i.format != 0x3001]
        return out

    def download(self, handle: int) -> bytes:
        return self._ok(0x1009, (handle,), what="Download")[0]

    def thumbnail(self, handle: int) -> bytes:
        return self._ok(0x100A, (handle,), what="Thumbnail")[0]

    def delete(self, handle: int):
        self._ok(0x100B, (handle, 0), what="Delete")

    # ---------- shooting ----------
    def capture(self, keep_on_card: bool = True, timeout_s: float = 40) -> tuple[bytes, ObjectInfo]:
        """Fire the shutter (camera does its own AF + metering), return the JPEG.

        The L120 never delivers PTP events on macOS here, and a full card listing
        takes 40-60 s, so we watch only the current shooting folder (instant).
        SAFETY: the camera reuses object handles after deletes, so a handle counts
        as our capture only if it was absent from a snapshot taken right before
        the shutter, and exactly one new file appeared.
        """
        if self._folder is None:
            self._folder = self.shooting_folder()
        before = set(self._handles(self._folder))
        folders_before = None
        self._ok(0x100E, (0, 0), what="Capture")
        handle, start = None, time.time()
        while time.time() - start < timeout_s and handle is None:
            time.sleep(0.5)
            new = [h for h in self._handles(self._folder) if h not in before]
            if len(new) > 1:
                raise CameraError(f"Ambiguous: {len(new)} new files appeared; not touching any")
            handle = new[0] if new else None
            if handle is None and time.time() - start > 15 and folders_before is None:
                # camera may have rolled over to a new folder (e.g. 102NIKON)
                folders_before = self._folder
                nf = self.shooting_folder()
                if nf != self._folder:
                    self._folder = nf
                    before = set()  # brand-new folder: everything in it is ours
        if handle is None:
            raise CameraError("Camera fired but no new photo appeared (card full, or camera in playback mode?)")
        info = self.object_info(handle)
        data = self.download(handle)
        if len(data) != info.size:
            raise CameraError(f"Short download: {len(data)} of {info.size} bytes")
        if not keep_on_card:
            self.delete(handle)
        return data, info


class RemoteL120(L120):
    """Same API as L120, served by the broker process (see broker.py)."""

    def close(self):
        if getattr(self, "_client", None):
            self._client.close()
            self._client = None

    def _c(self, method, *args):
        return self._client.call(method, *args)

    def device_info(self):
        return self._c("device_info")[0]

    def battery(self):
        return self._c("battery")[0]

    def clock(self):
        return self._c("clock")[0]

    def set_clock(self, when=None):
        return self._c("set_clock")[0] if when is None else self._c("set_clock", when.isoformat())[0]

    def storage(self):
        return self._c("storage")[0]

    def folders(self):
        return {int(k): v for k, v in self._c("folders")[0].items()}

    def shooting_folder(self):
        return self._c("shooting_folder")[0]

    def object_info(self, handle):
        return ObjectInfo(**self._c("object_info", handle)[0])

    def list_files(self):
        return [ObjectInfo(**d) for d in self._c("list_files")[0]]

    def download(self, handle):
        return self._c("download", handle)[1]

    def thumbnail(self, handle):
        return self._c("thumbnail", handle)[1]

    def delete(self, handle):
        self._c("delete", handle)

    def capture(self, keep_on_card=True, timeout_s=40):
        r, data = self._c("capture", keep_on_card, timeout_s)
        return data, ObjectInfo(**r["info"])
