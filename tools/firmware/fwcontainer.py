#!/usr/bin/env python3
"""Parse, verify, split and re-checksum Nikon COOLPIX (Sanyo-platform "SGxxx")
SD-card firmware.bin containers (COOLPIX L120 = SG582, P7000 = SG487).

Container layout (all headers are 0x60 bytes of ASCII, space padded, ending CRLF):

    0x000000  outer header  "LENGTH=<n> SG582 VER=.. DVR=.. SUM=<s> NPROG NDATA NLIPI\r\n"
    0x000060  MAIN image    <n> bytes, ARM code, base address 0x00000000
    then for each of NPROG, NDATA, NLIPI:
              header  "<NAME> LENGTH=<n> SG582IP VER=.. [DVR=..] SUM=<s>\r\n"
              payload <n> bytes

SUM is the plain unsigned sum of all payload bytes, written in decimal.

usage:
    fwcontainer.py info   firmware.bin
    fwcontainer.py split  firmware.bin OUTDIR          # write main.bin, nprog.bin, ...
    fwcontainer.py resum  in.bin out.bin               # recompute every SUM= field
This tool never talks to a camera.
"""
import re
import sys
from pathlib import Path

HDR = 0x60


def parse(data: bytes):
    sections = []
    off = 0
    first = True
    while off < len(data):
        hdr = data[off:off + HDR]
        text = hdr.split(b'\r\n')[0].decode('ascii')
        m_len = re.search(r'LENGTH=(\d+)', text)
        m_sum = re.search(r'SUM=(\d+)', text)
        if not (m_len and m_sum):
            raise ValueError(f'no header at 0x{off:x}: {hdr[:32]!r}')
        name = 'MAIN' if first else text.split()[0]
        length = int(m_len.group(1))
        start = off + HDR
        sections.append(dict(name=name, hdr_off=off, start=start, length=length,
                             stored_sum=int(m_sum.group(1)), header=text,
                             sum_span=m_sum.span(1)))
        off = start + length
        first = False
    if off != len(data):
        raise ValueError(f'container ends at 0x{off:x}, file is 0x{len(data):x}')
    return sections


def byte_sum(b: bytes) -> int:
    return sum(b)


def cmd_info(path):
    data = Path(path).read_bytes()
    ok = True
    for s in parse(data):
        actual = byte_sum(data[s['start']:s['start'] + s['length']])
        good = actual == s['stored_sum']
        ok &= good
        print(f"{s['name']:6s} hdr@0x{s['hdr_off']:07x} data@0x{s['start']:07x} "
              f"len={s['length']:8d} (0x{s['length']:06x}) SUM={s['stored_sum']:10d} "
              f"computed={actual:10d} {'OK' if good else 'MISMATCH'}")
        print(f"       {s['header']}")
    print('ALL CHECKSUMS OK' if ok else 'CHECKSUM ERRORS')
    return 0 if ok else 1


def cmd_split(path, outdir):
    data = Path(path).read_bytes()
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    for s in parse(data):
        (out / f"{s['name'].lower()}.bin").write_bytes(data[s['start']:s['start'] + s['length']])
    print('written to', out)


def cmd_resum(src, dst):
    """Recompute SUM= fields. The decimal width may change, so the header is
    rebuilt and re-padded with spaces to 0x60 bytes (CRLF kept at the end of the text)."""
    data = bytearray(Path(src).read_bytes())
    for s in parse(bytes(data)):
        new_sum = byte_sum(data[s['start']:s['start'] + s['length']])
        hdr = bytes(data[s['hdr_off']:s['hdr_off'] + HDR])
        text, _, _ = hdr.partition(b'\r\n')
        text = re.sub(rb'SUM=\d+', b'SUM=%d' % new_sum, text)
        new_hdr = (text + b'\r\n').ljust(HDR, b' ')
        if len(new_hdr) != HDR:
            raise ValueError('header overflow')
        data[s['hdr_off']:s['hdr_off'] + HDR] = new_hdr
        print(f"{s['name']:6s} SUM {s['stored_sum']} -> {new_sum}")
    Path(dst).write_bytes(bytes(data))
    return cmd_info(dst)


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['info'] and len(a) == 2:
        sys.exit(cmd_info(a[1]))
    if a[:1] == ['split'] and len(a) == 3:
        sys.exit(cmd_split(a[1], a[2]))
    if a[:1] == ['resum'] and len(a) == 3:
        sys.exit(cmd_resum(a[1], a[2]))
    print(__doc__)
    sys.exit(2)
