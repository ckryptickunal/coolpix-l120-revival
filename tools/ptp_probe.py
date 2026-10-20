"""Read-only PTP probe for the Nikon COOLPIX L120.

Only sends GetDeviceInfo, OpenSession, GetDevicePropDesc, GetStorageInfo and
CloseSession. Never writes to the camera.
"""
import json
import struct
import sys

import usb.backend.libusb1
import usb.core
import usb.util

backend = usb.backend.libusb1.get_backend(find_library=lambda _: "/opt/homebrew/lib/libusb-1.0.dylib")
dev = usb.core.find(idVendor=0x04B0, backend=backend)
if dev is None:
    sys.exit("Nikon device not found")

info = {"usb": {"idVendor": hex(dev.idVendor), "idProduct": hex(dev.idProduct),
                "bcdDevice": hex(dev.bcdDevice), "bcdUSB": hex(dev.bcdUSB)}}
try:
    dev.set_configuration()
except usb.core.USBError:
    pass
cfg = dev.get_active_configuration()
intf = cfg[(0, 0)]
usb.util.claim_interface(dev, 0)
ep_out = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
ep_in = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
info["usb"]["interface"] = {"class": intf.bInterfaceClass, "subclass": intf.bInterfaceSubClass,
                            "protocol": intf.bInterfaceProtocol,
                            "endpoints": [hex(e.bEndpointAddress) for e in intf]}

tid = 0


def transact(code, params=()):
    """Send a command, collect optional data phase, return (resp_code, data, resp_params)."""
    global tid
    tid += 1
    pkt = struct.pack("<IHHI", 12 + 4 * len(params), 1, code, tid) + b"".join(struct.pack("<I", p) for p in params)
    ep_out.write(pkt)
    data = b""
    while True:
        buf = bytes(ep_in.read(512 * 1024, timeout=5000))
        length, ctype, rcode, _ = struct.unpack("<IHHI", buf[:12])
        while len(buf) < length:
            buf += bytes(ep_in.read(512 * 1024, timeout=5000))
        if ctype == 2:
            data += buf[12:length]
        elif ctype == 3:
            rparams = [struct.unpack("<I", buf[i:i + 4])[0] for i in range(12, length, 4)]
            return rcode, data, rparams


class R:
    def __init__(self, b):
        self.b, self.o = b, 0

    def u(self, fmt):
        v = struct.unpack_from("<" + fmt, self.b, self.o)
        self.o += struct.calcsize(fmt)
        return v[0]

    def s(self):
        n = self.u("B")
        if n == 0:
            return ""
        v = self.b[self.o:self.o + 2 * n].decode("utf-16-le").rstrip("\x00")
        self.o += 2 * n
        return v

    def arr(self, fmt="H"):
        return [self.u(fmt) for _ in range(self.u("I"))]


rc, data, _ = transact(0x1001)
r = R(data)
di = {"StandardVersion": r.u("H"), "VendorExtensionID": hex(r.u("I")), "VendorExtensionVersion": r.u("H"),
      "VendorExtensionDesc": r.s(), "FunctionalMode": r.u("H"),
      "OperationsSupported": [hex(x) for x in r.arr()], "EventsSupported": [hex(x) for x in r.arr()],
      "DevicePropertiesSupported": [hex(x) for x in r.arr()], "CaptureFormats": [hex(x) for x in r.arr()],
      "ImageFormats": [hex(x) for x in r.arr()], "Manufacturer": r.s(), "Model": r.s(),
      "DeviceVersion": r.s(), "SerialNumber": r.s()}
info["DeviceInfo"] = di

rc, _, _ = transact(0x1002, (1,))  # OpenSession
info["OpenSession"] = hex(rc)

TYPES = {1: "b", 2: "B", 3: "h", 4: "H", 5: "i", 6: "I", 7: "q", 8: "Q"}


def val(r, t):
    if t == 0xFFFF:
        return r.s()
    if t in TYPES:
        return r.u(TYPES[t])
    if t & 0x4000 and (t & 0xFF) in TYPES:
        return [r.u(TYPES[t & 0xFF]) for _ in range(r.u("I"))]
    raise ValueError(hex(t))


props = {}
for p in di["DevicePropertiesSupported"]:
    rc, d, _ = transact(0x1014, (int(p, 16),))
    if rc != 0x2001:
        props[p] = {"error": hex(rc)}
        continue
    r = R(d)
    code, t, rw = r.u("H"), r.u("H"), r.u("B")
    e = {"type": hex(t), "rw": bool(rw), "default": val(r, t), "current": val(r, t)}
    form = r.u("B")
    if form == 1:
        e["range"] = [val(r, t) for _ in range(3)]
    elif form == 2:
        e["enum"] = [val(r, t) for _ in range(r.u("H"))]
    props[p] = e
info["DeviceProps"] = props

rc, d, _ = transact(0x1004)  # GetStorageIDs
ids = R(d).arr("I") if rc == 0x2001 else []
info["StorageIDs"] = [hex(i) for i in ids]

transact(0x1003)  # CloseSession
usb.util.release_interface(dev, 0)
print(json.dumps(info, indent=2, ensure_ascii=False))
