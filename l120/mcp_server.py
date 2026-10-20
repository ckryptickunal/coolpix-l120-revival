"""MCP server: control the Nikon COOLPIX L120 from Claude (or any MCP client) over USB."""
from __future__ import annotations

import base64
import io
import threading

from mcp.server.mcpserver import MCPServer, Image as McpImage
from PIL import Image

from . import features
from .camera import L120, CameraError

mcp = MCPServer("nikon-l120")
_lock = threading.Lock()  # one USB conversation at a time


def _run(fn):
    with _lock:
        with L120() as cam:
            return fn(cam)


def _preview(data: bytes, max_px=1024) -> McpImage:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return McpImage(data=buf.getvalue(), format="jpeg")


@mcp.tool()
def camera_status() -> dict:
    """Battery, card space, firmware, clock and the USB commands the camera supports."""
    def f(cam):
        info = cam.device_info()
        return {"model": info["model"], "firmware": info["firmware"], "serial": info["serial"],
                "battery_pct": cam.battery(), "camera_clock": cam.clock(), "card": cam.storage()}
    return _run(f)


@mcp.tool()
def sync_clock() -> str:
    """Set the camera's date/time to the Mac's current time (fixes wrong photo dates)."""
    return _run(lambda cam: f"Camera clock now {cam.set_clock()}")


@mcp.tool()
def take_photo(keep_on_card: bool = True) -> list:
    """Fire the shutter (camera autofocuses and meters itself) and save the full 14MP JPEG on the Mac.
    Returns the saved path and a preview. Zoom/mode must be set on the camera body."""
    def f(cam):
        data, info = cam.capture(keep_on_card=keep_on_card)
        out = features._session_dir("photo") / info.filename
        out.write_bytes(data)
        return [f"Saved {out} ({len(data) // 1024} KB)", _preview(data)]
    return _run(f)


@mcp.tool()
def long_exposure(frames: int = 10, mode: str = "lighten") -> list:
    """Simulated long exposure by shooting `frames` shots (~5 s each) and merging them.
    mode: lighten (light/star trails), mean (smooth water, less noise), darken, median (remove passers-by).
    Keep the camera on a tripod."""
    def f(cam):
        r = features.stack(cam, frames, mode)
        return [str(r), _preview(open(r["result"], "rb").read())]
    return _run(f)


@mcp.tool()
def timelapse(count: int = 30, interval_seconds: float = 10, fps: int = 24) -> dict:
    """Intervalometer: shoot `count` photos every `interval_seconds` (min ~5 s) and build an MP4."""
    return _run(lambda cam: features.timelapse(cam, count, interval_seconds, fps))


@mcp.tool()
def list_card(limit: int = 50) -> dict:
    """List photos and videos on the SD card (newest last)."""
    def f(cam):
        files = cam.list_files()
        return {"total": len(files), "files": [
            {"name": i.filename, "kb": i.size // 1024, "taken": i.captured, "handle": i.handle}
            for i in files[-limit:]]}
    return _run(f)


@mcp.tool()
def card_thumbnail(name: str) -> list:
    """Show the thumbnail of a file on the card by filename (e.g. DSCN0290.JPG)."""
    def f(cam):
        for i in cam.list_files():
            if i.filename.upper() == name.upper():
                return [i.filename, _preview(cam.thumbnail(i.handle), 320)]
        raise CameraError(f"{name} not on card")
    return _run(f)


@mcp.tool()
def import_card(only_new: bool = True) -> dict:
    """Copy photos and videos from the SD card to the Mac (captures/card-import). Never deletes from the card."""
    return _run(lambda cam: features.import_card(cam, only_new=only_new, delete_after=False))


@mcp.tool()
def delete_from_card(name: str, confirm: bool = False) -> str:
    """Permanently delete one file from the SD card. Requires confirm=true."""
    if not confirm:
        return "Not deleted: pass confirm=true to permanently delete this file from the card."
    def f(cam):
        for i in cam.list_files():
            if i.filename.upper() == name.upper():
                cam.delete(i.handle)
                return f"Deleted {i.filename}"
        raise CameraError(f"{name} not on card")
    return _run(f)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
