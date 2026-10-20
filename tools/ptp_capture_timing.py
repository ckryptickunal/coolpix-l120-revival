"""Time each phase of a PTP capture on the L120 (InitiateCapture -> events -> GetObject)."""
import struct
import sys
import time

import usb.backend.libusb1
import usb.core
import usb.util

backend = usb.backend.libusb1.get_backend(find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib")
dev = usb.core.find(idVendor=0x04B0, backend=backend)
try:
    dev.set_configuration()
except usb.core.USBError:
    pass
usb.util.claim_interface(dev, 0)
print("usb speed code:", dev.speed)  # 2=full(12M) 3=high(480M)
OUT, IN, INT = 0x01, 0x82, 0x83
tid = 0


def transact(code, params=(), want_data=True):
    global tid
    tid += 1
    dev.write(OUT, struct.pack("<IHHI", 12 + 4 * len(params), 1, code, tid) + b"".join(struct.pack("<I", p) for p in params))
    data = bytearray()
    while True:
        buf = bytearray(dev.read(IN, 4 * 1024 * 1024, timeout=60000))
        length, ctype, rcode, _ = struct.unpack("<IHHI", buf[:12])
        while len(buf) < length:
            buf += dev.read(IN, 4 * 1024 * 1024, timeout=60000)
        if ctype == 2:
            data += buf[12:length]
        elif ctype == 3:
            return rcode, bytes(data), [struct.unpack("<I", buf[i:i + 4])[0] for i in range(12, length, 4)]


t0 = time.time()
transact(0x1002, (1,))
mark = lambda s: print(f"{time.time() - t0:6.2f}s {s}")
mark("session open")
rc, _, _ = transact(0x100E, (0, 0))
mark(f"InitiateCapture response {hex(rc)}")
handle = None
deadline = time.time() + 60
while time.time() < deadline:
    try:
        ev = bytes(dev.read(INT, 64, timeout=1000))
    except usb.core.USBTimeoutError:
        continue
    _, _, code, _ = struct.unpack("<IHHI", ev[:12])
    p1 = struct.unpack("<I", ev[12:16])[0] if len(ev) >= 16 else None
    mark(f"event {hex(code)} param {hex(p1) if p1 is not None else '-'}")
    if code == 0x4002:
        handle = p1
    if code == 0x400D:  # CaptureComplete
        break
if handle is None:
    sys.exit("no ObjectAdded")
rc, data, _ = transact(0x1009, (handle,))
mark(f"GetObject {hex(rc)} {len(data)} bytes ({len(data) / 1e6:.1f} MB)")
open(sys.argv[1] if len(sys.argv) > 1 else "timing.jpg", "wb").write(data)
transact(0x1003)
usb.util.release_interface(dev, 0)
