#!/usr/bin/env python3
"""Build a patched COOLPIX L120 v1.1 firmware.bin on the Mac (never touches the camera).

Every patch checks the stock bytes before changing them, the input must be the
stock v1.1 image (SHA-256 below), and all four section byte sums are recomputed
and re-verified afterwards. See docs/firmware/ANALYSIS.md for the evidence behind
each patch and the order in which they should be tried.

usage:
  build_patch.py STOCK.bin OUT.bin --exif "L120 REVIVAL 0.1"      # step 1, harmless
  build_patch.py STOCK.bin OUT.bin --fireworks-seconds 8          # step 2
  build_patch.py STOCK.bin OUT.bin --raw-dump                     # step 3 (experimental)
Options can be combined, but test them one at a time on the camera.
"""
import argparse
import hashlib
import struct
import subprocess
import sys
from pathlib import Path

STOCK_SHA256 = 'e48fc17b37011802f58f0a11d40f8bd95fae1088775dad892406f49008e0c4eb'
MAIN_HDR = 0x60   # MAIN payload starts at file offset 0x60; MAIN offset == runtime address


def patch(data, main_off, old, new, what):
    off = MAIN_HDR + main_off
    if bytes(data[off:off + len(old)]) != old:
        raise SystemExit(f'{what}: stock bytes not found at MAIN 0x{main_off:x}')
    if len(new) != len(old):
        raise SystemExit(f'{what}: length change not allowed')
    data[off:off + len(new)] = new
    print(f'{what}: MAIN 0x{main_off:06x}  {old.hex(" ")}  ->  {new.hex(" ")}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('stock')
    ap.add_argument('out')
    ap.add_argument('--exif', metavar='TEXT16',
                    help='replace EXIF Software "COOLPIX L120V1.1" (exactly 16 ASCII chars)')
    ap.add_argument('--fireworks-seconds', type=int, metavar='N',
                    help='Fireworks show fixed exposure (stock 4); 1..60')
    ap.add_argument('--raw-dump', action='store_true',
                    help='after each capture, also write the factory RAW+TXT dump to the card')
    a = ap.parse_args()

    src = Path(a.stock)
    data = bytearray(src.read_bytes())
    sha = hashlib.sha256(data).hexdigest()
    if sha != STOCK_SHA256:
        raise SystemExit(f'input is not stock L120 v1.1 firmware.bin (sha256 {sha})')

    if a.exif is not None:
        new = a.exif.encode('ascii')
        if len(new) != 16 or not all(0x20 <= c < 0x7F for c in new):
            raise SystemExit('--exif needs exactly 16 printable ASCII characters')
        # string returned by the one-line getter at 0x32F104
        patch(data, 0x56EA60, b'COOLPIX L120V1.1', new, 'exif-software')

    if a.fireworks_seconds is not None:
        s = a.fireworks_seconds
        if not 1 <= s <= 60:
            raise SystemExit('--fireworks-seconds must be 1..60')
        # literal pool word loaded only by FUN_00163168 (Fireworks branch of the AE routine)
        patch(data, 0x163A08, struct.pack('<I', 4_000_000), struct.pack('<I', s * 1_000_000),
              f'fireworks-{s}s')

    if a.raw_dump:
        # in the post-capture routine FUN_001b0b60: `bl FUN_0016ba30` -> `mov r0, #1`
        patch(data, 0x1B0CB4, bytes.fromhex('5debfeeb'), bytes.fromhex('0100a0e3'), 'raw-dump')

    if bytes(data) == src.read_bytes():
        raise SystemExit('no patch selected')

    out = Path(a.out)
    tmp = out.with_suffix('.unsummed')
    tmp.write_bytes(data)
    tool = Path(__file__).with_name('fwcontainer.py')
    subprocess.run([sys.executable, '-I', str(tool), 'resum', str(tmp), str(out)], check=True)
    tmp.unlink()

    stock, built = src.read_bytes(), out.read_bytes()
    changed = [i for i, (x, y) in enumerate(zip(stock, built)) if x != y]
    print(f'{len(changed)} bytes differ from stock '
          f'({sum(1 for i in changed if i < 0x60)} in the MAIN header SUM field)')
    print('sha256', hashlib.sha256(built).hexdigest())
    return 0


if __name__ == '__main__':
    sys.exit(main())
