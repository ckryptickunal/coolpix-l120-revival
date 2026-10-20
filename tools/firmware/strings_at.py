#!/usr/bin/env python3
"""Print printable strings (with offsets) inside a byte range of a firmware image.
usage: strings_at.py IMAGE START END [MINLEN]"""
import re, sys
img, start, end = sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0)
minlen = int(sys.argv[4]) if len(sys.argv) > 4 else 3
data = open(img, 'rb').read()[start:end]
for m in re.finditer(rb'[\x20-\x7e\r\n\t]{%d,}' % minlen, data):
    print(f'{start + m.start():08x}  {m.group().decode()!r}')
