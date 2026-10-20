# COOLPIX L120: recovery analysis for the boot-time "System error"

Static analysis only, of the stock v1.1 `firmware.bin` (MAIN at base 0x0). Nothing was run and no USB was touched.
Addresses are MAIN addresses. **C** = confirmed from decompiled code or bytes; **I** = inferred; **?** = unknown.

Situation: stock → "0.1" (EXIF string, worked) → "0.2" (adds 8,000,000 at 0x163A08; "System error" on half-press,
later after a few seconds idle) → reflash of 0.1 from Setup → Firmware version showed "System error" on that page →
now "System error" 1–2 s after every power-on, with or without a card, also when started with the playback button.

## 0. What to try first (ranked)

1. **Locked-card auto-update at power-on (free test, works only if a factory NV byte happens to be 3).**
   Format a card in a computer (FAT32), put stock v1.1 at `/FIRMWARE/FIRMWARE.BIN`, **slide the card's lock switch to
   LOCK**, fit fresh batteries, press the power button. If the gate is open you get a plain-text `FIRM UP` →
   `FIRM UP OK` (or `FIRM UP ERR` / `BATTERY LOW`) with no menu, before normal start-up (section 1A). If nothing
   different happens, the byte is not 3 and nothing was changed.
2. **Hold-a-button power-on → firmware prompt.** Same card, but **unlocked**. Power on while holding one button at a
   time (keep holding for a few seconds). One key (key index 0x10, sub-micro key-word bit 0x800; the physical button
   was not identified) makes the start-up code look for `FIRMWARE.BIN` and show the "Update firmware?" prompt
   straight away (section 1B). Extra gates apply, so a failure doesn't rule this path out. It runs after the
   boot-init code that the locked-card path uses, so if the error comes from boot init this path won't help.
3. If neither works: the error happens before any card path in MAIN runs, or the fault is in code both paths
   need. Then recovery needs the pre-MAIN loader (section 2), which isn't in this file. In practice that means
   Nikon service, a NAND dump/reflash, or JTAG.

Avoid: repeated power-cycling with a nearly flat battery. Every fatal error writes the NV block back to NAND
(`FUN_002ab244` → `FUN_0040ce58` → `FUN_0040bef4`), and NAND writes at low voltage are how damage spreads.

## 1. Card-triggered paths at boot (question 1)

**Reset path (C).** 0x0 vectors → 0x20 sets the mode stacks → 0x60 clears BSS 0x57EE0000–0x57F3F018 →
`FUN_000003f0` sets up the MMU and caches → `FUN_00007018` starts the RTOS. The undefined-instruction, SWI,
prefetch-abort and data-abort vectors (0x248/0x260/0x278/0x290) call the fatal setter `FUN_002ab3d4` with
0xFE/0xFD/0xFC/0xFB. **A CPU crash anywhere therefore shows "System error".** No SD access happens before the RTOS.

Main task `FUN_002a79bc` (I: a task entry, since it has no callers):
`FUN_002a8078` → `FUN_003727d8` (NV/adjust load, section 5) → waits for the first sub-micro message (power-on
cause) → `FUN_002a7e24` → **`FUN_00374b08`** (boot init).

### 1A. Silent boot auto-update: `FUN_003e5cf8`, called near the start of `FUN_00374b08` (C)
Called when byte 0x57F02452 == 0 (BSS default 0, so normally yes). It updates without any UI when **all** of these hold:
- NV byte `0x57EF0408+0x155 == 3` (C). Values seen: 1 = retail (`FUN_002ab500`), 2/0x10 = adjustment mode,
  3 = this auto-update mode.
- Power-on cause bytes `[2]==1` and `[3]` ∈ {0x0B, 0x09, 0x08} (C). That these are power button / playback button
  is I, from `FUN_003da560`.
- `FUN_004088b0()` returns −2, meaning card mount status bit 0x20 (`FUN_00408634`) (C). **That bit 0x20 is the
  write-protect switch is I.**
- `FUN_002aa6a8(0)` accepts `/FIRMWARE/FIRMWARE.BIN`, with model `SG582` and a size check (C).
- Battery: if S+0x54 == 0 and S+0x50 < 0x50, it shows `BATTERY LOW` and hangs (C; strings at 0x56FAE4…0x56FAFC).

It then calls `FUN_002aa4e4(0)`, the full updater, and powers off. **How the byte becomes 3:** only factory
command 'C'/0xFF (`FUN_00298904`), or `FUN_0040c214` when the NV image in NAND is invalid **and** its byte 0x155 is
0xFF (erased NAND, a fresh production unit) (C). Retail units are probably 1 (I). The test costs nothing.

### 1B. Key-held firmware prompt: `FUN_0032b8d4` ← `FUN_0033e6c8` (mode-init handler, event 9 of `FUN_00340c0c`) (C)
- `FUN_0032b884` requires 0x57EF9C34 == 1, plus either 0x57F02440 == 1 (sub-micro status byte 6 bit 3,
  `FUN_003d2be4`) or **0x57F02469 == 1**.
- 0x57F02469 = bit 2 of `FUN_003de0d4()` (set by `FUN_003de2a0` in `FUN_00374b08`). It is set when **key index
  0x10 (mask 0x800)** is held, either now (sub-micro query 3,3) or at power-on (3,4). Tables are at 0x56FA2C
  (key → flag) and 0x56F9D8 (key → mask) (C). Other boot flags: 0x1 = key 0x1B (mask 1), 0x2 = key 0x1A (mask 2),
  0x8 = key 0x14, 0x80 = key 0x16. Keys 0x1B/0x1A are probably shutter full/half (I, from event codes 0x70–0x73 in
  `FUN_003e0bf4`).
- 0x57EF9C34 is set to 1 only in `FUN_0033d15c`, and only when 0x57F024B2 == 1. That byte is copied from 0x57F024B1
  in `FUN_00374b08`. **The exact condition was not resolved (?).**
- `FUN_0032b348`: if the key flag is set **or the NDATA header is invalid**, it probes `FIRMWARE.BIN`. If S+0x50 > 0x41
  (battery), it draws the update prompt (`FUN_002bc814(1)`) and sets UI state 0xBBE. Otherwise it shows message 0x19
  (I: battery). The user then confirms as in the normal menu.

Other card hooks (C): `FUN_002ad878` checks for `SG582.ADJ` (section 4). The menu updater is `FUN_0032b1d0` /
`FUN_003e5cb0` / `FUN_003e4acc`. `FUN_0032ad00` writes `FIRMVER.TXT` when a key bit is held (diagnostic, not recovery).

## 2. Where the updater lives, and what survives (question 2)

- **The updater is entirely in MAIN** (`FUN_002aa4e4`, header parser `FUN_002a9e64`, writers `FUN_002a9b30` and
  `FUN_00133b04`→`FUN_0013391c`→`FUN_001337dc`) (C). Every card path above needs MAIN to run.
- **A separate pre-MAIN loader exists but isn't in `firmware.bin`** (C for its existence):
  - MAIN starts at RAM 0 with a vector table.
  - MAIN only *reads* a hand-off block at 0x57FFFC00. Byte +4 is the status of the NV partition load and byte +5
    the status of the adjust partition load (`FUN_00134418`); the only reference is at 0x13441C.
  - The NV and adjust images are already in RAM at 0x47FF0000 and 0x47FD0000 when `FUN_003727d8` runs.
- NAND partitions (STL ids, C):
  - 0x65 = boot descriptor.
  - 0x66 = alternate image slot, chosen when the header has `LOADADDRESS=0`.
  - 0x67 = adjustment/calibration (0x100 sectors; backup copy in 0x69 at sector 0x7F00).
  - 0x68 = NV settings (0x80 sectors).
  - 0x69 = MAIN at the descriptor's start sector, NDATA at 0x4000, NPROG at 0x6400, adjust backup at 0x7F00.
  - 0x64/100, 0x6A–0x6C exist.
  - MAIN has **no write call to partition 0x64/100** (all direct `FUN_001349cc`/`FUN_0013355c` sites checked). The
    loader probably sits there or in the raw boot block (I), and a normal update never touches it.
- **MAIN commit sequence (C), `FUN_001337dc`:**
  1. Read descriptor 0x65.
  2. Set word 3 = **0x0BADC0DE**, write the descriptor.
  3. Write the image to 0x69, in chunks of up to 8 sectors.
  4. Store the length and valid byte, clear word 3, write the descriptor.
  5. Status 0x57F25E2C becomes 0x10211021. Any other non-zero value makes `FUN_002aa4e4` raise **0x171** ("System
     error").
- **NPROG and NDATA are skipped when unchanged** (C). NPROG is skipped if its header `VER=` matches the running
  version (`FUN_0032b690`). NDATA is skipped if its first 512 bytes match NAND (`FUN_0032b524`). Stock/0.1/0.2 share
  both, so **only MAIN and the descriptor were rewritten in each of these flashes**.
- If a MAIN write is interrupted: the descriptor keeps 0x0BADC0DE. With STL's logical-sector writes, MAIN is then a
  prefix of the new image plus the old image's remaining sectors (I). 0.1 and 0.2 differ only at 0x163A08 (about
  22 % in) and the EXIF string (0x56EA60, about 87 % in), so a clean prefix/suffix mix still runs (I). What the loader
  does when it sees 0x0BADC0DE is **?** (it may try slot 0x66).
- "System error" is drawn from **MAIN's own string at 0x54FAA0** (`FUN_002f7238`, screen 1), not from NPROG. Seeing
  it proves MAIN runs, not that NPROG is intact.

## 3. What can raise "System error" this early (question 3)

`FUN_00347048` (fatal handler) maps codes in `FUN_00347578`:
- {0x21, 0x22, 0x24, 0x46, 0x51, 0x53–0x5E, 0x61, 0x63} → screen 0xD.
- 100 → screen 4.
- 0x15/0x16 → screen 0xC.
- 0xB5 → screen 6.
- 200 → silent power-off.
- **Every other code → screen 1, "System error"** (C).

The handler first stores the code at NV+0x48 and the time at NV+0x4C, then writes NV (`FUN_002ab244`) (C).

Boot-time candidates that give screen 1:

| Code | Raised by | When |
|---|---|---|
| 0xFB–0xFE | CPU exception vectors | Any crash, for example executing a damaged or erased MAIN sector (I) |
| 0x172 | `FUN_0040d5a4` ← `FUN_003727d8` | Loader reports adjust partition 0x67 status 1. Status 3 is auto-repaired from the 0x69:0x7F00 backup |
| 0x173 | `FUN_0040be80` / 0x40D2F0 | Uncorrectable NAND read (status 2) in partition 0x69 or 0x68 |
| 0x0C | `FUN_0032dab0` (0x32DCDC), plus seven other 0xC sites (0x32B4A4, 0x32B4EC, 0x3756B8, 0x3CE710, 0x3D96B0, 0x3D971C, 0x3E4B18; not traced) | **This is the NDATA resource loader, not settings.** The header check is NDATA bytes 2–3 = 0x0582 (`FUN_0032cb14`). Fails only on a block read error; an invalid header silently zero-fills |
| 0xD2–0xD8, 0xC8 | `FUN_002a817c` / `FUN_002a80ac` | RTOS object creation at start-up (0xC8 powers off without the screen) |
| 0x171 | `FUN_002aa4e4` | MAIN write failed (on the update page only) |

- **NV corruption alone can't cause it** (C). `FUN_0040cd64` silently loads defaults (`FUN_0040c6a0(0)`) when the NV
  image fails its checksum (`FUN_00133498`) or tag (word 1 ≠ 0xFFFFFFF0). If the stored version at NV+0x106 differs,
  it resets user settings but keeps calibration (`FUN_0040c6a0(1)`).
- **The 0.2 literal.** The only reader is `FUN_00163168` (0x163190) ← `FUN_001631b8` ← 0x158630/0x1589F8/0x15A3AC,
  all inside the AE module and entered from the AE task dispatchers 0x15156C/0x151760 (C). No direct call from
  boot init was found. Whether the AE task gets a message during a playback start-up is **?**.
- **Most consistent story (I).** 0.2 faulted within seconds of idling. The reflash ran under 0.2, so the 0.2 fault
  probably fired during or just before the MAIN write, and the fatal handler powered the camera off mid-update.
  That leaves 0x0BADC0DE in the descriptor and a 0.1/0.2 mix (or damaged sectors) in partition 0x69. Which code
  fires now can't be read statically: NV+0x48 holds it, but is only readable through adjustment command 'E'/0x14.

## 4. Factory adjustment mode, SG582.ADJ (question 4)

- **Entry (C):** `FUN_002ad878` runs at the **very end** of `FUN_00374b08`, after the card mount in `FUN_00373ba8`.
  It needs NV+0x155 ∈ {2, 0x10} **and** a root-directory entry `SG582   ADJ`. That is later than path 1A and very
  likely after the point where the error fires (I).
- **Can the script set the flag?** Only from inside the mode. Command 'C'/0xFF (`FUN_00298904`) sets 0x155 to
  1/2/3/0x10 and saves NV, and 'C'/0x6D sets 1 (C). Packets come only from the card script interpreter
  (`FUN_002ae0e0` → `FUN_002afaf4`) (C). **No outside way to set 0x155 was found.**
- **Relevant commands (C).** Packet format: ESC, 'C'/'D'/'E', len, op, cmd. Command index = cmd + group × 0x100.
  - 'E' 0x1A → `FUN_002b0e3c`: firmware update from card `FIRMWARE.BIN`.
  - Execute-op 0x207 → `FUN_0029df74`: update from the RAM buffer.
  - Execute-op 0x1F6 → `FUN_002a3e48`, sub-case 5: NV defaults (`FUN_0040c214`).
  - 'E' 0x14 → `FUN_002b0c54` → `FUN_003e95b4`: reports the last fatal code and time (NV+0x48/0x4C).
  - 'C'/0xFF → set the NV mode byte.
- Not usable on this camera unless its NV byte is already 2 or 0x10.

## 5. NV "initialise to defaults" at boot (question 5)

- `FUN_003727d8` → `FUN_0040cd64` (C). If the NV image is invalid → full defaults. Version mismatch → partial
  reset. The new NV is written back via `FUN_0040cf0c` → `FUN_0040d314` (partition 0x68). If the NV *and* adjust
  images are both all-0xFF, extra init runs (`FUN_00405f74` / `FUN_004042c8`).
- **Defaults set NV+0x155 = 3 when the NAND copy of that byte is 0xFF** (`FUN_0040c214`). An erased NV partition
  therefore enables the silent auto-update in 1A on the next boot (C). That is the production-line design (I).
- No magic card file that triggers an NV reset was found. The resets reachable from the card are the adjustment
  commands only.

## Open items

- Which physical button is key index 0x10 (mask 0x800).
- The condition behind 0x57F024B1/0x57F024B2 that sets 0x57EF9C34.
- The loader's handling of descriptor word 3 = 0x0BADC0DE and of slot 0x66.
- Whether card mount status bit 0x20 is the write-protect switch.
- Which code is now stored at NV+0x48.
