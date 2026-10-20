#!/usr/bin/env python3
"""Search an image for a sequence of integers stored as u8/u16/u32 (LE), any gap-free order.
usage: find_seq.py IMAGE v1 v2 v3 ..."""
import struct, sys
d = open(sys.argv[1], 'rb').read()
vals = [int(x, 0) for x in sys.argv[2:]]
for fmt, sz in (('<H', 2), ('<I', 4), ('<B', 1)):
    if max(vals) >= 1 << (8 * sz):
        continue
    pat = b''.join(struct.pack(fmt, v) for v in vals)
    i = d.find(pat)
    while i != -1:
        print(f'{fmt} at {i:08x}')
        i = d.find(pat, i + 1)
