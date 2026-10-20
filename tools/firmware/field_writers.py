#!/usr/bin/env python3
"""Find functions that load a global struct pointer (literal pool) and store to a given field offset.
Heuristic: within WINDOW bytes after an `ldr rX,=BASE`, look for str/strb/strh [rX, #OFF].
usage: field_writers.py [--read] IMAGE BASE OFF [OFF...]  (--read finds loads instead of stores)"""
import struct, sys
import capstone
d = open([a for a in sys.argv[1:] if a != "--read"][0], 'rb').read()
MODE = 'ldr' if '--read' in sys.argv else 'str'
args = [a for a in sys.argv[1:] if a != '--read']
base = int(args[1], 0); offs = [int(x, 0) for x in args[2:]]
n = len(d) // 4
W = struct.unpack('<%dI' % n, d[:n * 4])
pools = {i * 4 for i, w in enumerate(W) if w in (base, base & 0x0FFFFFFF | 0x50000000)}
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM); md.detail = True
hits = []
for i, w in enumerate(W):
    if (w & 0x0F7F0000) == 0x051F0000:
        imm = w & 0xFFF
        t = i * 4 + 8 + (imm if w & 0x00800000 else -imm)
        if t in pools:
            rt = (w >> 12) & 0xF
            for ins in md.disasm(d[i * 4 + 4:i * 4 + 4 + 0x60], i * 4 + 4):
                if ins.mnemonic.startswith(('ldr', 'mov', 'add')) and ins.op_str.startswith(f'r{rt},') :
                    break
                if ins.mnemonic.startswith(MODE):
                    for o in offs:
                        if f'[r{rt}, #{hex(o)}]' in ins.op_str or (o == 0 and f'[r{rt}]' in ins.op_str):
                            hits.append((ins.address, ins.mnemonic, ins.op_str))
for h in hits:
    print(f'{h[0]:08x}: {h[1]} {h[2]}')
