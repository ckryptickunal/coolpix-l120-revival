#!/usr/bin/env python3
"""Map the factory debug dumper's 'name=0x%08x' printf calls to RAM addresses/fields.
Pattern per field:  ldr r8,=STRUCT ; ldr{h,b,sh} r2,[r8,#off] ; ldr r1,=FMT ; ... bl sprintf
usage: map_dump_fields.py IMAGE START END"""
import struct, sys, re
import capstone
d = open(sys.argv[1], 'rb').read()
start, end = int(sys.argv[2], 0), int(sys.argv[3], 0)
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
u32 = lambda a: struct.unpack('<I', d[a:a + 4])[0]
def cstr(a):
    return d[a:d.index(b'\0', a)].decode('latin1')
regs = {}
for ins in md.disasm(d[start:end], start):
    m = re.match(r'(r\d+|ip|sb|sl|fp), \[pc, #(-?0x[0-9a-f]+)\]', ins.op_str)
    if ins.mnemonic == 'ldr' and m:
        regs[m.group(1)] = u32(ins.address + 8 + int(m.group(2), 16)); continue
    m = re.match(r'r2, \[(r\d+)(?:, #(0x[0-9a-f]+))?\]', ins.op_str)
    if ins.mnemonic.startswith('ldr') and m and m.group(1) in regs:
        regs['r2src'] = (regs[m.group(1)], int(m.group(2) or '0', 16), ins.mnemonic)
    if ins.mnemonic == 'bl' and 'r1' in regs and 'r2src' in regs:
        f = regs['r1']
        if 0x500000 < f < len(d) and '%' in cstr(f):
            b, o, mn = regs['r2src']
            print(f'{ins.address:08x}  {cstr(f).strip():32s} <- {mn:6s} [0x{b:08x} + 0x{o:x}] = 0x{b+o:08x}')
        regs.pop('r2src', None)
