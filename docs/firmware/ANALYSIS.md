# COOLPIX L120 firmware v1.1: reverse-engineering findings

Analysis of Nikon's official COOLPIX L120 firmware update 1.1 (the only update Nikon released), done to
find out whether new features can be patched in. **Nothing was ever written to the camera.** All work was
static: reading the update file on a Mac.

## Verdict

**Patching is feasible, and the long-exposure goal looks realistic.**

- The update file is plain, unencrypted 32-bit ARM code. Its only integrity check is a simple byte sum per
  section, which we can recompute. There is no signature.
- The camera's exposure engine already contains a full **Manual / Shutter-priority** system: shutter
  speeds from 30 s down to 1/8000 s, plus Bulb. Only factory test paths use it; the retail menus never
  switch it on.
- The 4-second Fireworks exposure is a single 4-byte constant that only the Fireworks code reads.

| Feature | Verdict | Effort / risk |
|---|---|---|
| Longer Fireworks exposure (8 s, 15 s, 30 s) | **Likely works**: 4-byte data patch | Very small patch, low risk; sensor behaviour beyond 4 s untested |
| Manual shutter/ISO (P/S/M-style) | **Feasible**: engine exists, needs a new UI hook | Medium: needs a small code patch and free space |
| Bulb / up to 60 s | **Feasible later**: engine has Bulb (−1) and 60 s caps | Medium-high: needs the manual hook plus a way to end the exposure |
| RAW capture | **Promising**: a factory RAW dumper exists; 4-byte patch enables it | Medium: RAW file format still undecoded; side effects unknown |
| Intervalometer | Already available: stock menu (per Nikon manual) plus this repo's Mac-side timelapse | None needed |
| HDMI live view | **Unknown**: no decisive code found | Test the stock behaviour first, no patch |
| USB remote exposure control | **Not in stock**: PTP stack is storage-only, no vendor ops | A later code patch could add it |
| Wi-Fi / Eye-Fi-style connectivity | Out of scope for firmware: needs hardware such as a Wi-Fi SD card | None |

The lowest-risk first step is to change the EXIF "Software" string in photos (step 1 below). It proves
the whole patch-and-flash pipeline without touching any camera behaviour.

---

## 1. Source file and how to reproduce

| Item | Value |
|---|---|
| Download | `F-L120-V11M.dmg` (Mac updater) from Nikon's download centre, page `downloadcenter.nikonimglib.com/en/download/fw/30.html` |
| DMG SHA-256 | `2d0962aeda72ba41cc240bad6d3e8aea30ef6d1ad7d7c7310b2664410d7e5f95` (5,048,700 bytes) |
| Payload | `L120Update/firmware/firmware.bin`, 11,831,692 bytes |
| firmware.bin SHA-256 | `e48fc17b37011802f58f0a11d40f8bd95fae1088775dad892406f49008e0c4eb` |

Nikon binaries live only in `firmware/vendor/` (gitignored). Tools are in `tools/firmware/`:

| Tool | Purpose |
|---|---|
| `fwcontainer.py info/split/resum` | Parse the container, verify all four sums, split sections, recompute sums |
| `build_patch.py` | Build patched images (`--exif`, `--fireworks-seconds`, `--raw-dump`), with stock-byte checks |
| `xref.py` | Find code that loads a given address (literal-pool cross-references) |
| `field_writers.py [--read]` | Find stores/loads to `struct+offset` for a global struct pointer |
| `map_dump_fields.py` | Map the factory debug dumper's `name=0x%08x` prints to RAM fields |
| `strings_at.py`, `find_seq.py`, `find_imm.py` | String, integer-sequence and immediate searches |
| `ghidra/DecompileAt.java` | Headless Ghidra script: decompile the function at each address and list its callers |

Ghidra 12.1.4 project settings: processor `ARM:LE:32:v5t`, raw binary, base address `0x0`, MAIN payload only.

## 2. Container format and checksums (confirmed)

```
file off   section  length (bytes)        stored SUM    computed
0x000000   header   "LENGTH=6553600 SG582 VER=v582-76 DVR=1.11 SUM=664635594 NPROG NDATA NLIPI\r\n"
0x000060   MAIN     6,553,600 (0x640000)  664,635,594   664,635,594 OK
0x640060   header   "NPROG LENGTH=2117632 SG582IP VER=v582-76 DVR=1.11 SUM=63787078\r\n"
0x6400C0   NPROG    2,117,632 (0x205000)   63,787,078    63,787,078 OK
0x8450C0   header   "NDATA LENGTH=3160064 SG582IP VER=v582-76 DVR=1.11 SUM=295279575\r\n"
0x845120   NDATA    3,160,064 (0x303800)  295,279,575   295,279,575 OK
0xB48920   header   "NLIPI LENGTH=12  SG582IP VER=v582-76 SUM=427\r\n"
0xB48980   NLIPI    12                          427           427 OK
```

- Every header is 0x60 bytes of ASCII, padded with spaces.
- **SUM is the plain sum of all payload bytes, written in decimal.** I tried sum16/sum32/CRC32 and only the
  8-bit byte sum matches, for all four sections.
- `fwcontainer.py resum` on the unmodified file reproduces it byte for byte.
- Entropy: MAIN is about 5.9 bits/byte (normal ARM code and data). NPROG is about 2.6 (padded text tables).
  NDATA starts around 7.8 (compressed graphics) and drops lower later. **Nothing is encrypted.** The
  NikonHacker XOR scheme for DSLRs does not apply.

What the sections hold:

- **MAIN**: the camera program, ARM code. It starts with an ARM exception vector table (`06 00 00 ea …`
  at file offset 0x60).
- **NPROG**: despite the name, this is the multi-language message catalogue. Fixed-width UTF strings, one
  block per language, about 0x14000 bytes each; English is the first block. It contains strings for features the L120
  does not have (NRW/RAW, GPS, HDR), so it is a catalogue shared across a camera family.
- **NDATA**: graphics, fonts and icon resources (not analysed in detail).
- **NLIPI**: a 12-byte record `30 49 4C 4E 0C 00 00 00 0C 00 80 00` (magic `0x4E4C4930`).

### The camera's update routine (confirmed by decompiling)

| Address (MAIN) | What it does |
|---|---|
| `0x2AA4E4` | Update entry. Loads the file (8.3 name `FIRMWARE.BIN` in folder `FIRMWARE`, strings at `0x54CBB8`/`0x54CBF0`), parses it, checks the total size, then writes the sections. |
| `0x2A9E64` | Header parser. `strstr("LENGTH=")` and `strstr("SUM=")`, then adds every MAIN byte starting at offset 0x60 and compares with SUM (loop at `0x2A9F30`). Mismatch means reject. An optional `LOADADDRESS=` must be absent or zero (default 0x20000). Then it locates NPROG/NDATA/NLIPI by name, using the table at `0x54CB28`. |
| `0x2AA7E0`… | Model check: the header's model token must equal `SG582`. `DVR=` is only copied into a buffer for display; **no downgrade or same-version check was seen**. |
| `0x2A9B30` | Per-section writer. NPROG (type 0) goes to NAND starting at sector 0x6400, at most 0x1800 sectors (3 MiB). NDATA (type 1) goes to sector 0x4000, at most 0x2000 sectors (4 MiB). Each is compared, erased, written and read back. |
| `0x2A9DB8` | NLIPI (type 2): checks magic `0x4E4C4930` and records a size value. |
| `0x2AAB08` | After writing: shuts the camera down (infinite loop until power-off). |

Only MAIN's sum is checked in the parser; I did not see NPROG/NDATA sums being checked. We recompute all
four anyway. There is no signature, hash or encryption anywhere in this path.

The flash stack is Samsung XSR/STL NAND (`UpdatePIExt`, `UpdateLPCA/UPCA`, `EraseBlk()` strings near
`0x52B000`–`0x535600`). A separate **"VR FirmUP"** path (`SUB_FIRMHEX`, `VR FirmUP Error`)
updates the vibration-reduction micro-controller (string at `0x54D310`). Any future patch must leave that path alone.

## 3. CPU, load address, memory map

| Fact | Evidence | Status |
|---|---|---|
| 32-bit ARM mode, ARMv5 (ARM926-class) | Top bytes `e5/e1/e3/eb` dominate; vector table and `msr cpsr_c` stack setup at MAIN 0x20–0x4C; no Thumb found in hot paths | Confirmed |
| Load/base address **0x00000000** | String-pointer cross-reference: base 0x0 gives 1,688 pointer hits; next best is 0x50000000 with 608 | Confirmed |
| DRAM at 0x50000000, mirrored at 0x0 | The 0x5xxxxxxx pointers are the same image through an alias; globals live at 0x57EE0000–0x57FD0000; stacks at 0x57FCA000–0x57FCF000 (literals at MAIN 0xFC–0x118) | Strongly inferred |
| Image buffers at 0x40000000 | RAW dumper writes from `0x40000000 + start*4` | Inferred |
| End of MAIN image (0x620408) = start of runtime heap | Code references `0x50620408`; MAIN 0x620408–0x640000 (about 127 KB) is zeros in the file but **used as RAM at runtime** | Confirmed reference; **not a free code cave** |

Platform: `SG582` is the platform code (the P7000 uses `SG487` with the identical container). The shared
string catalogue, the `NikonS560-…` strings and the Sanyo-style factory protocol suggest an ODM platform
shared with other COOLPIX models (inferred).

## 4. The exposure system (the heart of the long-exposure question)

### 4.1 Key RAM structures

The factory debug dumper prints these fields (see `map_dump_fields.py`; strings at MAIN `0x540F94`–`0x5413FC`).

| Address | Name (from firmware strings or behaviour) | Meaning |
|---|---|---|
| `0x57EE5064` | AE/camera struct ("cam") | |
| cam+`0x4C` | `cam_exp_program` | Exposure program: 1 = Manual, 4 = Shutter-priority, 2/3 = automatic programs |
| cam+`0x54` | `cam_scene` | AE scene; value 1 takes the fixed-4 s path (see 4.3) |
| cam+`0x70` | (manual shutter) | Shutter in µs used when the program is 1 or 4; −1 means Bulb |
| cam+`0x140` | `shutter_acq` | **Capture exposure time in microseconds** (linear, 32-bit) |
| cam+`0x148` | `sens_acq` | Sensor gain/ISO for the capture |
| cam+`0x150` | `Fno` | Aperture |
| `0x57EF3968` | UI settings struct ("S") | |
| S+`0xB0` | exposure mode | Retail boot sets 2 (`FUN_00374b08`). Factory paths set 1 (manual), 3, 4 or 5 |
| S+`0x0C` | manual shutter (µs) | |
| S+`0x14` | manual ISO | |

**Shutter units.** The EXIF conversion `FUN_00166068` (MAIN 0x166068) uses `FUN_001605f4`, which turns a
linear value into log2×16. Working back from the third-stop EXIF table at `0x53CCC0` (600, 500, … 10 for
60″ to 1″ in tenths of a second, then 13 … 80000 for 1/1.3 to 1/8000 as denominator×10) shows that
`shutter_acq` is a plain **microsecond count**. A 32-bit field can hold up to about 71 minutes.

### 4.2 A complete manual-shutter table already exists

`FUN_00341d14` (MAIN 0x341D14) turns a shutter index into microseconds and stores it in S+0x0C. Its literal
pool (0x342604–0x3426AC) contains:

```
idx 0  -> 0xFFFFFFFF  (Bulb / Time)
idx 1  -> 30,000,000  (30 s)   idx 2 -> 25 s   idx 3 -> 20 s   idx 4 -> 15 s   idx 5 -> 13 s
idx 6  -> 10 s   idx 7 -> 8 s   idx 8 -> 6 s   idx 9 -> 5 s   idx 10 -> 4 s   idx 11 -> 3 s ...
idx 16 -> 1 s  ...  idx 0x2E -> 1/1000   idx 0x32 -> 1/2500   idx 0x37 -> 125 µs (1/8000)
```

`FUN_0034217c` does the reverse mapping. The on-screen display code at `0x2FA680` can draw
`TIME30S / TIME 1M / TIME 3M / TIME 5M / TIME10M / BULB10M` (strings at `0x54FAB8`–`0x54FAE0`), choosing
one by a field at UI struct `0x57EF3908+0x50`. Retail code only ever writes 0 to that field (`0x3200C8`,
`0x322528`, `0x3D3A50`).

### 4.3 How the capture exposure is chosen

`FUN_001631b8` (MAIN 0x1631B8) is the capture auto-exposure routine:

```c
if (cam->scene == 1) {            // fixed-exposure scene
    FUN_00163168();               // ISO = FUN_00167430() (returns base gain 0xB3, or 0xE7 in one mode), shutter_acq = *(u32*)0x163A08 (= 4,000,000)
    return;                       // no slow-shutter clamp on this path
}
... normal AE ...
shutter_acq = min(shutter_acq, slow_limit());  // slow limit from table 0x53CF10, max entry 4 s
```

- `FUN_00163168` is the only reader of the 4,000,000 µs literal at **MAIN 0x163A08**. The xref is
  confirmed: one `ldr` at `0x163190`.
- That `cam_scene == 1` means **Fireworks show** is inferred. It is the only stock L120 mode with a fixed
  4 s shutter, and this branch also forces base ISO, as Nikon documents for Fireworks. Patch step 2
  confirms it from the EXIF of a test shot.
- The normal-mode slow limit comes from `FUN_001649a4`. It reads the 32-byte-record table at `0x53CF10`
  (ids 0, 44–52, ending with id 52 = 4 s). The AE program table at `0x53B040` (33 records × 28 bytes)
  only selects ids 44–50 (1/60 to 1 s). That is why ordinary modes stop at 1 s.

### 4.4 Manual mode is wired into capture

- `FUN_00294878` (capture start): if S+0xB0 == 1, the capture uses **S+0x0C (manual µs)** directly
  instead of the AE result.
- `FUN_003b4eac`: if S+0xB0 is 1 or 4, the manual shutter is also stored for EXIF.
- The UI-to-AE message builders `FUN_003c52ac` and `FUN_0040ebd8` put S+0xB0 into message slot 6 and S+0x0C
  into slot 0xC. The AE handler `FUN_00155034` stores slot 6 at cam+0x4C and slot 0xC at cam+0x70. All
  three use the struct pointers 0x57EF3968 and 0x57EE5064.
- `FUN_0015a33c` caps a time value (apparently exposure) per program: 24 s for programs 2/3, **60 s for program 4**, 16 s otherwise.
  `FUN_00166408` reports Bulb (program 1 with +0x70 = −1) as 60 s.
- `FUN_00157410` keeps `shutter_acq` within 81–123 % of cam+0x70 in manual programs.

**Who can set manual mode today?** Only factory code paths:

- `FUN_002a3a3c` (preset: mode 1, fixed 1/4 s).
- `FUN_003eac7c`, the handler for **factory-protocol command 0x66** "set exposure". It takes mode (1–5),
  ISO and shutter µs from a packet, with a minimum of 250 µs and **no maximum**.

## 5. Factory adjustment mode and RAW dump (inferred from code, not exercised)

- Command packets have the form `ESC, 'C'|'D'|'E', len_lo, len_hi, op, cmd, payload` (`FUN_002afaf4`). The
  dispatcher `FUN_002a2cf8` handles about 70 commands (0x16–0x1EF); 0x66 is the exposure command above.
- Packets come from a numbered step script (`P%03d`/`S%03d`, status `RDY/RUN/ERR/FIN`) loaded from the card
  file **`SG582.ADJ`** (`FUN_002ac0c4`, name built from `SG582` + `ADJ`).
- Entry requires a factory flag byte (NV area `0x57EF0408+0x155` must be 2 or 0x10) **and** that file
  (`FUN_002ad878`). A retail camera should never enter this mode.
- **Not recommended as a user path.** This mode can rewrite CCD calibration (`CcdAdjRam`, `CamAdjRam`
  strings at `0x58D740`), and the file format (product number, several sub-blocks) is only partly understood.
- **RAW dumper.** `FUN_001b4b4c` writes a `DATAn.TXT` exposure log plus a `.RAW` file. `FUN_001b4ae0`
  writes the raw image buffer from 0x40000000. It runs after each capture **only when a debug value at
  `0x57EE5898+0xFA` is 0x11 or 0x44** (`FUN_0016ba30`). Retail cameras have it off.

## 6. USB / PTP (confirmed)

DeviceInfo lists are built by `FUN_0012a85c` from two table sets, selected by a mode argument (0 or 3). Their operation lists (`0x535908`, `0x5358D8`) are identical:

- Operations: 0x1001–0x100F, 0x1014–0x1016, 0x101B and MTP 0x9801–0x9803/0x9805. Includes InitiateCapture
  0x100E.
- Events: 0x4001–0x400D (subset).
- Device properties: 0x5001 (battery), 0x5011 (date/time), 0xD005, 0xD303, 0xD407.
- Capture format 0x3801 (JPEG).

The handler names in the stack (`NK_MTP_*`, `NK_APLI_MtpObjProp*`) are all storage and object-property
functions. No vendor operation codes, exposure properties or live-view strings were found. This matches the
earlier USB probe. Remote exposure control over USB would need a new code patch.

## 7. HDMI, intervalometer, connectivity

- **HDMI**: menu strings exist (`HDMI`, `Auto/480p/720p/1080i`, `HDMI device control` for CEC). The code
  region has only sensor-mode names such as `monit_4h2v30_720p_bayer`, so whether the shooting-mode
  display reaches HDMI was not settled statically. **Test first, without any patch**: plug in HDMI while
  in shooting mode and see whether the picture appears.
- **Intervalometer**: the L120 has interval-timer shooting in its Continuous menu (per Nikon's manual; not
  re-verified here). This repo's Mac-side timelapse already covers more. No patch needed.
- **Connectivity**: USB 2.0 PTP/MTP only. Wireless would need hardware (an Eye-Fi/FlashAir-style card);
  firmware cannot add a radio.

## 8. Brick risk

- **The reflash path lives inside MAIN.** "Update to new firmware?" is reached from the camera's menu,
  which runs inside MAIN. A patch that crashes during boot would remove the normal way to reflash. Whether
  the bootloader has an independent SD-card recovery is **unknown**; the bootloader is not in this file.
  NLIPI is only a 12-byte record.
- Therefore:
  1. **Only patch code or data that runs after boot and only in a specific mode or action.** All three
     candidates qualify:
     - EXIF string: read by the EXIF tag builder (`FUN_003f9018`) and by one other routine (`FUN_00370af0`),
       probably the USB DeviceInfo version string. Neither runs during boot.
     - Fireworks literal: read only in the Fireworks capture branch.
     - RAW hook: runs only after a capture.
  2. Never change lengths, section sizes, `LOADADDRESS`, NLIPI, the VR sub-firmware, or boot/init code.
  3. Always build from the verified stock image with `build_patch.py`. It refuses anything else and
     re-verifies all sums.
  4. Keep the official v1.1 `firmware.bin` on a second card as the rollback image. Reinstalling stock
     should restore everything these patches change: the updater takes MAIN whole, and NPROG/NDATA are
     compared and rewritten section by section. How MAIN is committed to flash (`FUN_0013350c`…`FUN_00133530`)
     was not traced in detail.
  5. Use fresh, full AA batteries (lithium preferred) for every flash. Never interrupt an update.
- Corroboration from the P7000 project (same container): users install modified `SG487` images that pass
  only these four byte sums. Same platform family; still unproven on an L120.

## 9. Patch plan, lowest risk first

Each step is a separate flash. Do not combine steps until each has passed on its own.

### Step 1: EXIF Software string (no behaviour change)

- **Change:** MAIN `0x56EA60`, `COOLPIX L120V1.1` → `L120 REVIVAL 0.1` (16 bytes, same length, null
  terminator untouched).
- **Why it's safe:** the string is returned by a one-line getter (`0x32F104`: `ldr r0,=0x56EA60; bx lr`).
  Real L120 photos carry exactly this text in their EXIF Software tag; checked against a photo in
  `captures/`. The getter has a second caller (`FUN_00370af0`), probably the USB DeviceInfo version string,
  so that text may change over USB too.
- **Build:**
  ```bash
  python3 -I tools/firmware/build_patch.py firmware/vendor/extracted/L120Update/firmware/firmware.bin \
      firmware/vendor/out/firmware.bin --exif "L120 REVIVAL 0.1"
  ```
- **Result:** 15 bytes differ from stock: 14 string bytes (two characters happen to match) plus one digit
  of MAIN's `SUM=`. The output SHA-256 is printed by the tool
  (`6b1225e3a99cf360df3619b60f960108466c4756d70c3fec6bbb69d713b9747b`).
- **Flash (owner only, see [FLASHING.md](FLASHING.md)):**
  1. Format a card in the camera.
  2. Copy the file to `<card>/firmware/firmware.bin`.
  3. Fit fresh batteries.
  4. Go to Setup → Firmware version → Update, and follow the prompts until the camera says to power off.
- **Verify:**
  - Take a photo and read its EXIF Software tag (the repo's USB driver can fetch it). It should say
    `L120 REVIVAL 0.1`.
  - Everything else should behave exactly as stock.
  - The Setup menu still shows version 1.1, because `DVR=` is unchanged.
- **Rollback:** flash the stock file the same way.

### Step 2: Fireworks show exposure 4 s → 8 s

- **Change:** MAIN `0x163A08`, `00 09 3D 00` (4,000,000 µs) → `00 12 7A 00` (8,000,000 µs).
  Build with `--fireworks-seconds 8`.
- **Verify:**
  - In Fireworks show, the shot should take about twice as long.
  - EXIF ExposureTime should read 8 s; the EXIF table reaches 60 s.
  - Other modes should be unchanged.
- **Main risk:** the CCD driver or timing generator may have limits beyond 4 s, for example a hang
  (fix: pull the batteries, then reflash) or a black frame.
- **If it works:** step up to 15 s, then 30 s. Watch for hot pixels and any noise-reduction pass that
  doubles the wait.

### Step 3: Factory RAW dump (experimental)

- **Change:** MAIN `0x1B0CB4`, `bl FUN_0016ba30` (`5d eb fe eb`) → `mov r0,#1` (`01 00 a0 e3`).
  Build with `--raw-dump`.
- **Scope:** this changes only the post-capture decision in `FUN_001b0b60`. The shared helper is left
  alone, because its other callers alter capture flow.
- **Expect:** a `.RAW` file and a `DATA*.TXT` next to each JPEG. Sizes are unknown, probably 20–30 MB.
- **Next work:** decode the RAW layout on the Mac (bit depth, packing, Bayer order). The TXT has the
  exposure values.

### Step 4 (later): user-selectable manual shutter and ISO

- **Goal:** reuse the existing engine. When the user is in a chosen mode (for example a repurposed scene),
  set S+0xB0 = 1 (or 4) and S+0x0C/S+0x14 from a small menu. This keeps the stock HUD, zoom and buttons.
- **Needs:**
  - A code cave: the zero tail of MAIN is runtime heap, so candidates are unused factory or test functions,
    or growing MAIN if the loader allows it, which is unverified.
  - Hooks into the scene menu.
  - Reuse of `FUN_00341d14`'s shutter list and the existing OSD strings.

## 10. Comparison with the P7000 project

[huwanduf/P7000-SCN-Firmware](https://github.com/huwanduf/P7000-SCN-Firmware) (v3.4.3, P7000 firmware 1.2):

- Its released `firmware.bin` uses the **same container**: `LENGTH=8126464 SG487 VER=v487-82 DVR=1.2 …
  NPROG NDATA NLIPI`.
- `fwcontainer.py info` verifies all four of its byte sums.
- That project keeps `DVR=1.2` and installs over the same version through Setup → Firmware version,
  consistent with the L120 code not checking versions.
- Its approach (patch in place, keep sizes, recompute four sums) applies directly to the L120 file.
- Its warning about leaving clocks, init and calibration alone matches the brick-risk rules above.

## 11. Confirmed versus inferred

**Confirmed** (byte or decompiler evidence in this document):
- Container layout and the byte-sum checksums.
- No encryption or signature in the update path.
- ARM mode with base address 0.
- The update routine's checks.
- The PTP lists.
- The manual shutter table and its readers.
- The capture-start manual branch.
- The 4 s literal's single reader.
- The factory protocol command 0x66.
- The RAW dumper's gate and file writes.
- The EXIF string getter, and that real photos carry the string.

**Inferred (not yet proven):**
- `cam_scene == 1` is Fireworks show.
- The sensor driver handles exposures longer than 4 s.
- The DRAM alias layout.
- Absence of a bootloader SD-card recovery path. Unknown, so treat a boot crash as a possible brick.
- HDMI shooting-mode output.
- The meaning of exposure modes 3 and 5.
- That the factory ADJ mode is unreachable on retail units.
- The interval-timer menu item (from Nikon's manual).
