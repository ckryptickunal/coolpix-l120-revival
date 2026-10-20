"""End-to-end check of the nikon-l120 MCP server over stdio (real camera).

Usage: .venv/bin/python tools/mcp_smoke.py
Calls each tool the way an MCP client does and prints a one-line result per tool.
Shots taken here are deleted from the card again (keep_on_card=False paths only).
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
proc = subprocess.Popen([sys.executable, "-m", "l120.mcp_server"], cwd=ROOT, env={**os.environ, "PYTHONPATH": ROOT},
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
_id = 0


def rpc(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notify:
        _id += 1
        msg["id"] = _id
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if notify:
        return None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server exited")
        m = json.loads(line)
        if m.get("id") == _id:
            return m


def call(name, **args):
    t = time.time()
    m = rpc("tools/call", {"name": name, "arguments": args})
    res = m.get("result", {})
    content = res.get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    imgs = sum(1 for c in content if c.get("type") == "image")
    ok = "ERROR" if res.get("isError") or "error" in m else "ok"
    detail = (texts[0] if texts else json.dumps(m.get("error")))[:160].replace("\n", " ")
    print(f"{ok:5} {name:16} {time.time() - t:6.1f}s  imgs={imgs}  {detail}", flush=True)
    return texts


rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}})
rpc("notifications/initialized", notify=True)
print("tools:", len(rpc("tools/list")["result"]["tools"]))

steps = sys.argv[1:] or ["status", "photo", "long", "timelapse", "night", "card", "lab"]
if "status" in steps:
    call("camera_status")
if "photo" in steps:
    call("take_photo", keep_on_card=False)
if "long" in steps:
    call("long_exposure", frames=3, mode="lighten")
if "timelapse" in steps:
    call("timelapse", count=3, interval_seconds=6, fps=2)
if "night" in steps:
    call("night_mode", frames=4)
if "card" in steps:
    t = call("list_card", limit=2, folder="100NIKON")
    path = json.loads(t[0])["files"][-1]["path"] if t and t[0].startswith("{") else "100NIKON/DSCN0320.JPG"
    call("card_thumbnail", path=path)
    call("download_from_card", path=path)
if "lab" in steps:
    import glob
    shots = sorted(glob.glob(os.path.join(ROOT, "captures", "*-night", "frame_*.jpg")))
    if shots:
        call("analyze_photo", path=shots[0])
        call("apply_film_look", path=shots[0], look="kodachrome", date_stamp=True)
        call("merge_photos", paths=shots, kind="mean")
proc.terminate()
