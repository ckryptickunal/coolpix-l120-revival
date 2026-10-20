"""Plug-and-go: when the L120 is connected, copy new photos/videos to the Mac.

Run once:        python -m l120.autoimport
Watch forever:   python -m l120.autoimport --watch
Install as a login agent (opt-in):  python -m l120.autoimport --install-agent

Never deletes anything from the card. Optionally also imports into Apple Photos
(so photos reach the iPhone via iCloud) and sets the camera clock from the Mac.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import usb.core

from .features import stamp_mtime as _stamp_mtime
from .camera import L120, L120_PID, NIKON_VID, CameraError, _backend

DEFAULT_DEST = Path.home() / "Pictures" / "Nikon L120"
STATE = Path.home() / ".l120-autoimport.json"
AGENT = Path.home() / "Library" / "LaunchAgents" / "com.l120.autoimport.plist"


def camera_present() -> bool:
    return usb.core.find(idVendor=NIKON_VID, idProduct=L120_PID, backend=_backend) is not None


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"imported": {}}


def import_new(dest: Path = DEFAULT_DEST, to_photos=False, sync_clock=True) -> dict:
    state = _load_state()
    seen = state["imported"]
    new_files, clock_note = [], None
    with L120() as cam:
        if sync_clock and cam.clock()[:4] < "2015":
            clock_note = f"camera clock was {cam.clock()}, set to {cam.set_clock()}"
        names = cam.folders()
        for info in cam.list_files():
            key = f"{names.get(info.parent, '?')}/{info.filename}"
            if seen.get(key) == info.size:
                continue
            target = dest / key
            if target.exists() and target.stat().st_size == info.size:
                seen[key] = info.size
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = cam.download(info.handle)
            if len(data) != info.size:
                continue  # incomplete transfer; retry next time
            target.write_bytes(data)
            _stamp_mtime(target, info.captured)
            seen[key] = info.size
            new_files.append(target)
            STATE.write_text(json.dumps(state))  # checkpoint after every file
    if to_photos and new_files:
        files = ", ".join(f'POSIX file "{p}"' for p in new_files)
        subprocess.run(["osascript", "-e",
                        f'tell application "Photos" to import {{{files}}} skip check duplicates false'],
                       capture_output=True)
    if new_files:
        subprocess.run(["osascript", "-e",
                        f'display notification "{len(new_files)} new photo(s) imported" with title "Nikon L120"'],
                       capture_output=True)
    return {"new": len(new_files), "dest": str(dest), "clock": clock_note}


def watch(dest: Path, to_photos: bool, sync_clock: bool, every_s=20):
    was_present = False
    while True:
        present = camera_present()
        if present and not was_present:
            time.sleep(3)  # let the camera settle after power-on
            try:
                print(import_new(dest, to_photos, sync_clock), flush=True)
            except (CameraError, usb.core.USBError) as e:
                print(f"import failed, will retry on next connect: {e}", flush=True)
                present = False
        was_present = present
        time.sleep(every_s)


def install_agent(dest: Path, to_photos: bool):
    repo = Path(__file__).resolve().parent.parent
    args = [sys.executable, "-m", "l120.autoimport", "--watch", "--dest", str(dest)] + (["--photos"] if to_photos else [])
    prog = "".join(f"<string>{a}</string>" for a in args)
    AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.l120.autoimport</string>
<key>ProgramArguments</key><array>{prog}</array>
<key>WorkingDirectory</key><string>{repo}</string>
<key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>{repo}</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>/tmp/l120-autoimport.log</string>
<key>StandardErrorPath</key><string>/tmp/l120-autoimport.log</string>
</dict></plist>""")
    subprocess.run(["launchctl", "unload", str(AGENT)], capture_output=True)
    subprocess.run(["launchctl", "load", str(AGENT)], check=True)
    print(f"Installed {AGENT}. Remove with: launchctl unload {AGENT} && rm {AGENT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--photos", action="store_true", help="also import new files into Apple Photos")
    ap.add_argument("--no-clock", action="store_true", help="don't fix a reset camera clock")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--install-agent", action="store_true")
    a = ap.parse_args()
    if a.install_agent:
        install_agent(a.dest, a.photos)
    elif a.watch:
        watch(a.dest, a.photos, not a.no_clock)
    else:
        print(import_new(a.dest, a.photos, not a.no_clock))


if __name__ == "__main__":
    main()
