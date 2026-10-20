"""Scan the L120 for hidden (unadvertised) PTP operations during its USB window.

Non-destructive by design:
- only sends opcodes that are NOT in the camera's advertised list (so never
  DeleteObject 0x100B, FormatStore 0x100F, SendObject*, etc.)
- no parameters, no data phase
- stops at the first response other than OperationNotSupported (0x2005) and
  reports it, without sending anything further.

Usage: python tools/ptp_opscan.py   (then power the camera on with USB plugged in)
"""
import struct
import subprocess
import sys
import time

import usb.backend.libusb1
import usb.core
import usb.util

be = usb.backend.libusb1.get_backend(find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib")
ADVERTISED = {*range(0x1001, 0x1010), 0x1014, 0x1015, 0x1016, 0x101B, 0x9801, 0x9802, 0x9803, 0x9805}
RANGES = [range(0x1000, 0x1100), range(0x9000, 0xA000), range(0xC000, 0xC100), range(0xD000, 0xD100),
          range(0xF000, 0xF100)]
CANDIDATES = [op for r in RANGES for op in r if op not in ADVERTISED]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def wait_device(timeout=900):
    t = time.time()
    while time.time() - t < timeout:
        d = usb.core.find(idVendor=0x04B0, backend=be)
        if d is not None:
            return d
        time.sleep(0.05)
    return None


def main():
    log(f"waiting for camera; {len(CANDIDATES)} unadvertised opcodes queued")
    d = wait_device()
    if d is None:
        sys.exit("camera never appeared")
    for n in ("mscamerad-xpc", "ptpcamerad"):
        subprocess.run(["pkill", "-9", "-f", n], capture_output=True)
    try:
        d.set_configuration()
    except usb.core.USBError:
        pass
    usb.util.claim_interface(d, 0)
    tid = 0

    def send(op, params=()):
        nonlocal tid
        tid += 1
        d.write(0x01, struct.pack("<IHHI", 12 + 4 * len(params), 1, op, tid)
                + b"".join(struct.pack("<I", p) for p in params), timeout=2000)
        while True:
            b = bytes(d.read(0x82, 1 << 16, timeout=4000))
            if len(b) >= 12:
                ln, typ, code, _ = struct.unpack("<IHHI", b[:12])
                if typ == 3:
                    return code
                # data phase from a handled op: drain it
                while len(b) < ln:
                    b += bytes(d.read(0x82, 1 << 16, timeout=4000))

    rc = send(0x1002, (1,))
    log("OpenSession ->", hex(rc))
    t0, done = time.time(), 0
    for op in CANDIDATES:
        try:
            rc = send(op)
        except usb.core.USBError as e:
            log(f"camera stopped answering after {done} opcodes ({time.time() - t0:.1f}s): {e}")
            log(f"next opcode to resume from: {hex(op)}")
            return
        done += 1
        if rc != 0x2005:
            log(f"!!! opcode {hex(op)} answered {hex(rc)} (not 'unsupported'). Stopping scan here.")
            return
    log(f"scan complete: all {done} unadvertised opcodes answered 0x2005 (not supported) in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
