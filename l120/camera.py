"""Direct USB (PTP) driver for the Nikon COOLPIX L120.

What the L120 exposes over USB (probed on firmware V1.1):
  shutter (InitiateCapture), file list/download/delete/upload, thumbnails,
  battery level, and the camera clock. Nothing else: no zoom, exposure,
  live view or video control.
"""
from __future__ import annotations

import datetime as dt
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
        self._known: set[int] | None = None  # handles present on the card

    # ---------- connection ----------
    def connect(self, attempts: int = 8, lock_timeout_s: float = 600):
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
        rc, _, _ = self._transact(0x1002, (1,))  # OpenSession
        if rc not in (OK, 0x201E):  # 0x201E = session already open
            raise CameraError(f"OpenSession failed: {hex(rc)}")
        self.drain_events()
        return self

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
                       + b"".join(struct.pack("<I", p) for p in params), timeout=5000)
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
        when = when or dt.datetime.now()
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

    def _handles(self) -> list[int]:
        return _Reader(self._ok(0x1007, (0xFFFFFFFF, 0, 0))[0]).arr("I")

    def list_files(self) -> list[ObjectInfo]:
        infos = [self.object_info(h) for h in self._handles()]
        return [i for i in infos if i.format != 0x3001]  # skip folders

    def download(self, handle: int) -> bytes:
        return self._ok(0x1009, (handle,), what="Download")[0]

    def thumbnail(self, handle: int) -> bytes:
        return self._ok(0x100A, (handle,), what="Thumbnail")[0]

    def delete(self, handle: int):
        self._ok(0x100B, (handle, 0), what="Delete")
        if self._known is not None:
            self._known.discard(handle)

    # ---------- shooting ----------
    def capture(self, keep_on_card: bool = True, timeout_s: float = 30) -> tuple[bytes, ObjectInfo]:
        """Fire the shutter (camera does its own AF + metering), return the JPEG."""
        # Primary signal: the ObjectAdded event (arrives ~4 s after the shutter).
        # The L120's event pipe sometimes drops packets, so if it stays silent we
        # fall back to the object list: new objects always get the highest handle.
        # (Listing is slow the first time per session: the camera indexes the card.)
        # SAFETY: the camera reuses object handles after deletes, so "highest
        # handle" is NOT proof of a new file. A handle counts as our capture only
        # if it was absent from a snapshot taken right before the shutter.
        # (The first snapshot per session is slow, ~60 s, while the camera
        # indexes the card; later ones are fast.)
        if self._known is None:
            self._known = set(self._handles())
        before = set(self._known)
        self.drain_events()
        self._ok(0x100E, (0, 0), what="Capture")
        handle, start = None, time.time()
        while time.time() - start < timeout_s and handle is None:
            for code, params in self._read_events(500):
                if code == 0x4002 and params and params[0] not in before:
                    handle = params[0]
            if handle is None and time.time() - start > 3:
                new = [h for h in self._handles() if h not in before]
                handle = new[0] if len(new) == 1 else None
                if len(new) > 1:
                    raise CameraError(f"Ambiguous: {len(new)} new files appeared; not touching any")
        if handle is None:
            raise CameraError("Camera fired but no new photo appeared (card full, or camera in playback mode?)")
        self._known.add(handle)
        info = self.object_info(handle)
        data = self.download(handle)
        if len(data) != info.size:
            raise CameraError(f"Short download: {len(data)} of {info.size} bytes")
        if not keep_on_card:
            self.delete(handle)
            self._known.discard(handle)
        return data, info
