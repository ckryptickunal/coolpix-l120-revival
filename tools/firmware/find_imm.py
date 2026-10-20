#!/usr/bin/env python3
"""List ARM instructions in [START,END) whose immediate operand equals one of VALUES.
usage: find_imm.py IMAGE START END VALUE [VALUE...]"""
import sys, re
import capstone
d = open(sys.argv[1], 'rb').read()
s, e = int(sys.argv[2], 0), int(sys.argv[3], 0)
vals = {int(v, 0) for v in sys.argv[4:]}
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
md.skipdata = True
for ins in md.disasm(d[s:e], s):
    if ins.mnemonic in ('mov', 'cmp', 'movs', 'mvn', 'sub', 'add', 'subs', 'adds', 'movhi', 'movlo', 'movls', 'movhs', 'movgt', 'movlt', 'movge', 'movle') or ins.mnemonic.startswith(('mov', 'cmp')):
        m = re.search(r'#(0x[0-9a-f]+|\d+)(?:, #(\d+))?$', ins.op_str)
        if m:
            v = int(m.group(1), 0)
            if m.group(2):
                r = int(m.group(2)); v = ((v >> r) | (v << (32 - r))) & 0xFFFFFFFF
            if v in vals:
                print(f'{ins.address:08x}: {ins.mnemonic} {ins.op_str}   ; = 0x{v:x}')
