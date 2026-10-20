"""Camera broker: one long-lived process owns the L120's USB connection.

Why: whenever a process lets go of the camera, macOS's Image Capture service
(mscamerad-xpc / ptpcamerad) grabs it and asks for a full card listing, which
keeps the L120 busy for ~60 s and can wedge it. Holding the connection in one
process avoids that, and lets Studio, the MCP server and scripts share it.

Clients don't use this module directly: `L120().connect()` starts the broker if
needed and transparently talks to it. Set L120_DIRECT=1 to bypass the broker.
The broker releases the camera and exits after IDLE_S seconds without requests.

Protocol (Unix socket, one request per connection-turn): request = one JSON
line {"m": method, "a": [args]}; response = one JSON line {"ok", "r" | "e",
"bin": n} followed by n raw bytes when the result carries binary data.
"""
from __future__ import annotations

import dataclasses
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

SOCK = "/tmp/nikon-l120.sock"
IDLE_S = float(os.environ.get("L120_BROKER_IDLE_S", 1200))
METHODS = {"device_info", "battery", "clock", "set_clock", "storage", "folders", "shooting_folder",
           "list_files", "object_info", "download", "thumbnail", "delete", "capture", "ping"}


# ---------------------------------------------------------------- wire format
def _send(f, header: dict, blob: bytes = b""):
    header["bin"] = len(blob)
    f.write((json.dumps(header) + "\n").encode())
    if blob:
        f.write(blob)
    f.flush()


def _recv(f) -> tuple[dict, bytes]:
    line = f.readline()
    if not line:
        raise ConnectionError("broker closed the connection")
    header = json.loads(line)
    blob = f.read(header.get("bin", 0)) if header.get("bin") else b""
    return header, blob


# ---------------------------------------------------------------- server side
class _Server:
    def __init__(self):
        self.cam = None
        self.lock = threading.Lock()
        self.last = time.time()

    def _camera(self):
        from .camera import L120
        if self.cam is None:
            self.cam = L120()
            self.cam._direct = True
            self.cam.connect()
        return self.cam

    def _reset(self):
        if self.cam is not None:
            try:
                self.cam.close()
            except Exception:
                pass
        self.cam = None

    def call(self, method: str, args: list):
        from .camera import CameraError
        if method == "ping":
            return {"pid": os.getpid()}, b""
        if method not in METHODS:
            raise CameraError(f"unknown method {method}")
        with self.lock:
            self.last = time.time()
            for attempt in (1, 2):
                try:
                    res = getattr(self._camera(), method)(*args)
                    break
                except CameraError:
                    raise
                except Exception as e:  # USB-level failure: reconnect once
                    self._reset()
                    if attempt == 2:
                        raise CameraError(f"USB error talking to the camera: {e}") from e
            self.last = time.time()
        if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], bytes):  # capture
            return {"info": dataclasses.asdict(res[1])}, res[0]
        if isinstance(res, bytes):
            return None, res
        if isinstance(res, list):
            return [dataclasses.asdict(x) if dataclasses.is_dataclass(x) else x for x in res], b""
        if dataclasses.is_dataclass(res):
            return dataclasses.asdict(res), b""
        if isinstance(res, dict):
            return {str(k): v for k, v in res.items()}, b""
        return res, b""

    def handle(self, conn: socket.socket):
        f = conn.makefile("rwb")
        try:
            while True:
                try:
                    req, _ = _recv(f)
                except (ConnectionError, ValueError):
                    return
                try:
                    r, blob = self.call(req["m"], req.get("a", []))
                    _send(f, {"ok": True, "r": r}, blob)
                except Exception as e:
                    _send(f, {"ok": False, "e": str(e), "type": type(e).__name__})
        finally:
            f.close()
            conn.close()

    def serve(self):
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        srv.listen(8)
        srv.settimeout(5)
        print(f"l120 broker pid {os.getpid()} on {SOCK}", flush=True)
        while time.time() - self.last < IDLE_S or self.lock.locked():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            self.last = time.time()
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()
        print("idle, releasing camera", flush=True)
        self._reset()
        srv.close()
        os.unlink(SOCK)


# ---------------------------------------------------------------- client side
def _alive() -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(SOCK)
        f = s.makefile("rwb")
        _send(f, {"m": "ping"})
        ok = _recv(f)[0].get("ok")
        s.close()
        return bool(ok)
    except OSError:
        return False


def ensure_broker(timeout_s: float = 20):
    if _alive():
        return
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(root)}
    subprocess.Popen([sys.executable, "-m", "l120.broker"], cwd=root, env=env, start_new_session=True,
                     stdout=open("/tmp/l120-broker.log", "a"), stderr=subprocess.STDOUT)
    t = time.time()
    while time.time() - t < timeout_s:
        if _alive():
            return
        time.sleep(0.2)
    from .camera import CameraError
    raise CameraError("Camera broker did not start; see /tmp/l120-broker.log")


class Client:
    def __init__(self):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(SOCK)
        self.f = self.s.makefile("rwb")

    def call(self, method, *args):
        from .camera import CameraError
        _send(self.f, {"m": method, "a": list(args)})
        h, blob = _recv(self.f)
        if not h["ok"]:
            raise CameraError(h["e"])
        return h["r"], blob

    def close(self):
        try:
            self.f.close()
            self.s.close()
        except OSError:
            pass


if __name__ == "__main__":
    _Server().serve()
