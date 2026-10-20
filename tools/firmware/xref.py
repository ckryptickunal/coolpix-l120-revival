#!/usr/bin/env python3
"""Find code references to an address in a base-0 ARM image.
Finds 32-bit literal-pool words equal to TARGET (or TARGET|0x50000000 alias) and
the ARM `ldr rX, [pc, #imm]` instructions that load them.
usage: xref.py IMAGE TARGET [TARGET...]"""
import struct, sys
d = open(sys.argv[1], 'rb').read()
n = len(d) // 4
W = struct.unpack('<%dI' % n, d[:n * 4])
def ldr_refs(pool):
    out = []
    for i in range(max(0, pool // 4 - 1024), pool // 4):
        w = W[i]
        # LDR (literal), ARM: cond 0101 U001 1111 Rt imm12
        if (w & 0x0F7F0000) == 0x051F0000:
            imm = w & 0xFFF
            tgt = i * 4 + 8 + (imm if w & 0x00800000 else -imm)
            if tgt == pool:
                out.append(i * 4)
    return out
for t in sys.argv[2:]:
    t = int(t, 0)
    for alias in (t, t | 0x50000000):
        for i, w in enumerate(W):
            if w == alias:
                print(f'target {t:08x}: pool word at {i*4:08x} (value {w:08x}) loaded by', [hex(x) for x in ldr_refs(i * 4)])
