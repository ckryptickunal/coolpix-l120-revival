# Firmware analysis tools

Static-analysis helpers for the COOLPIX L120 v1.1 update file. None of them talk to the camera.
Findings are written up in [docs/firmware/ANALYSIS.md](../../docs/firmware/ANALYSIS.md).

Nikon's files are never committed. Put them under `firmware/vendor/` (gitignored):

```bash
mkdir -p firmware/vendor/dl && curl -L -o firmware/vendor/dl/F-L120-V11M.dmg \
  "https://crossgate.nikonimglib.com/dsd_redirect/redirect.do?P=c294k86&R=1qljv19&L=u0xYo00&O=E9asR00"
```

Mount the DMG read-only, copy out `L120Update/firmware/firmware.bin`, then:

```bash
python3 -I tools/firmware/fwcontainer.py info firmware/vendor/extracted/L120Update/firmware/firmware.bin
python3 -I tools/firmware/fwcontainer.py split firmware/vendor/extracted/L120Update/firmware/firmware.bin firmware/vendor
```

| Script | Use |
|---|---|
| `fwcontainer.py` | `info` (verify sums), `split` (main/nprog/ndata/nlipi .bin), `resum` (recompute SUM fields) |
| `build_patch.py` | Build a patched image from verified stock: `--exif`, `--fireworks-seconds N`, `--raw-dump` |
| `xref.py IMAGE ADDR…` | Literal-pool references and the `ldr` instructions that load them |
| `field_writers.py [--read] IMAGE STRUCT OFF…` | Stores (or loads) to `STRUCT+OFF` near loads of the struct pointer |
| `map_dump_fields.py IMAGE START END` | Map the factory debug dumper's printf fields to RAM addresses |
| `strings_at.py IMAGE START END` | Printable strings in a range |
| `find_seq.py IMAGE v1 v2…` | Integer sequences stored as u8/u16/u32 |
| `find_imm.py IMAGE START END VAL…` | Instructions with a given immediate |
| `ghidra/DecompileAt.java` | Headless Ghidra: decompile functions at addresses and list callers |

`field_writers.py`, `map_dump_fields.py` and `find_imm.py` need `capstone` (`pip install capstone`).

Ghidra setup used for the analysis:

```bash
analyzeHeadless <projdir> l120 -import firmware/vendor/main.bin \
  -processor ARM:LE:32:v5t -loader BinaryLoader -loader-baseAddr 0x0
analyzeHeadless <projdir> l120 -process main.bin -noanalysis -readOnly \
  -scriptPath tools/firmware/ghidra -postScript DecompileAt.java 0x1631b8 0x341d14
```
