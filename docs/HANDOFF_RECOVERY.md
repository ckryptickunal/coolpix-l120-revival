# Nikon COOLPIX L120: bricked by a firmware patch. Full handoff for recovery

**Goal for the next agent:** get this camera back to working (stock Nikon v1.1 firmware, or the known-good
"0.1" build) without a Nikon service centre. The owner says no service centre will touch a 15-year-old
L120, is willing to open the camera, and accepts risk. Suggest the best option with reasoning.

Everything below is grounded in tool output from the working session (2026-10-20/21) unless marked
**inferred** or **unverified**.

---

## 1. Current state (as of 2026-10-21)

- The camera powers on, then shows **"System error"** (red text with a "!" icon) and shuts off, usually within
  **1–7 s**. This happens with or without an SD card, and also when powered on via the playback (▶) button.
- The running firmware is **build "0.2"** (our modification). Confirmed several times over USB: PTP DeviceInfo
  `DeviceVersion = "L120 REVIVAL 0.2"`, read in the ~1 s window after power-on (last check 23:29 on 2026-10-20).
- **Reaching the on-camera updater is possible but it never completes.** In plain **Auto** mode the camera
  survives about 5 s, enough to press MENU → Setup → Firmware version → Update → Yes. The camera shows
  "Please wait / do not turn off", then the **screen goes black** and the buttons go dead. The owner waited
  10 minutes untouched before pulling the batteries. Afterwards it still reads 0.2, so the update never applied.
- **USB:** with the cable connected immediately after power-on (holding MENU+OK+shutter while pressing power,
  then plugging in), the camera entered PTP mode and **stayed alive for about 90 s** once. It normally drops
  within about 1–2 s once "System error" triggers.
- **Photos are safe:** 316 files on the SD card, all backed up at `~/Pictures/Nikon L120/` (315 originals,
  size-verified; plus 1 accidental shot).
- **Hidden-opcode scan (2026-10-21 00:08):** `tools/ptp_opscan.py` sent 257 unadvertised opcodes
  (0x1000–0x10FF minus the advertised ones, then 0x9000–0x9013). **Every one answered 0x2005
  (OperationNotSupported).** Then the camera stopped answering at about 4.4 s (the System error crash). To resume,
  start from opcode 0x9014. So far this matches the static analysis: no hidden ops.

---

## 2. The device

| Item | Value |
|---|---|
| Model | Nikon COOLPIX L120 (announced 9 Feb 2011), 14.1 MP 1/2.3" CCD, 21x lens, 4×AA |
| Processor | Nikon "EXPEED C2"; the firmware shows it is a **Sanyo-built platform** (contains `SANYO`, Sanyo SXC schema URL, `Cam*` service property names) |
| Firmware codename | `SG582` (container headers `SG582`, `SG582IP`, `VER=v582-76`, `DVR=1.11`) |
| USB | 04b0:0185, PTP class 6/1/1, endpoints 0x01 out, 0x82 in, 0x83 interrupt (8-byte packets) |
| Ports | USB/AV multi-connector, mini-HDMI (CEC) |
| Stock firmware | v1.1, the only update Nikon released. `F-L120-V11M.dmg` SHA-256 `2d0962aeda72ba41cc240bad6d3e8aea30ef6d1ad7d7c7310b2664410d7e5f95`; `firmware.bin` SHA-256 `e48fc17b37011802f58f0a11d40f8bd95fae1088775dad892406f49008e0c4eb`, 11,831,692 bytes |
| Local copies | `~/Desktop/l120/firmware/vendor/` (gitignored): `dl/F-L120-V11M.dmg`, `extracted/L120Update/firmware/firmware.bin` (stock), `out/firmware.bin` (0.1), `out/firmware_8s.bin` (0.2) |

Stock PTP DeviceInfo: vendor extension `microsoft.com: 1.0`. Operations
0x1001–0x100F, 0x1014–0x1016, 0x101B and MTP 0x9801–0x9803 and 0x9805. Properties 0x5001 (battery),
0x5011 (date/time, read-write), 0xD005, 0xD407, 0xD303.

---

## 3. Firmware container format (confirmed)

```
file off   section  length                 SUM (decimal byte-sum of payload)
0x000000   header   "LENGTH=6553600 SG582 VER=v582-76 DVR=1.11 SUM=664635594 NPROG NDATA NLIPI\r\n" (0x60 bytes, space padded)
0x000060   MAIN     6,553,600 (0x640000)   664,635,594
0x640060   header   NPROG ...
0x6400C0   NPROG    2,117,632 (0x205000)    63,787,078
0x8450C0   header   NDATA ...
0x845120   NDATA    3,160,064 (0x303800)   295,279,575
0xB48920   header   NLIPI ...
0xB48980   NLIPI    12                          427
```

- SUM is the plain 8-bit byte sum, in decimal. There is no encryption and no signature. MAIN is ARM
  little-endian, loaded at base 0x0 (Ghidra `ARM:LE:32:v5t`).
- The tools are in `tools/firmware/`: `fwcontainer.py` (info/split/resum), `build_patch.py` (refuses
  non-stock input; `--fireworks-seconds` is now **disabled**), `xref.py`, `strings_at.py`, `find_seq.py`,
  `find_imm.py`, `field_writers.py`, `map_dump_fields.py`, and `ghidra/DecompileAt.java`.
- Ghidra project: `/private/tmp/claude-501/-Users-Kunal-Desktop-l120--claude-worktrees-firmware-analysis-d50c68/a6d28513-5ca3-46bd-8993-8bbad27b613d/scratchpad/ghidra`
  (project `l120`, program `main.bin`). This is a temporary location and may disappear.

---

## 4. What was flashed (exact diffs vs stock)

| Build | Changes | SHA-256 | Result on hardware |
|---|---|---|---|
| **0.1** | MAIN `0x56EA60`: `COOLPIX L120V1.1` → `L120 REVIVAL 0.1` (EXIF Software / PTP DeviceVersion string) and the MAIN header SUM digit. 15 bytes total | `6b1225e3a99cf360df3619b60f960108466c4756d70c3fec6bbb69d713b9747b` | ✅ Flashed via Setup → Firmware version. Booted and shot normally; the new string showed up in EXIF and PTP. **Proves rebuilt images are accepted.** |
| **0.2** | 0.1's string change (as `0.2`) **plus** MAIN `0x163A08`: `00 09 3D 00` (4,000,000 µs) → `00 12 7A 00` (8,000,000 µs), and 3 header SUM digits. 20 bytes total | `46a8d3c146e4451f3f70acfb536d01d001371e35378ee695c6243d60781d2a8a` | ❌ Flashed OK. Booted, then "System error" plus power-off on the first shutter half-press (in Auto). Later it errored after a few seconds idle, and now at 1–7 s after every boot. |

File-offset diff for 0.2 (MAIN offset = file offset − 0x60): `0x34–0x36` (header SUM digits), `0x163A69–0x163A6A`,
`0x56EAC0–0x56EACF`.

**Why 0.2 was believed safe, and why that was wrong:** a static scan found exactly one ARM `ldr`-literal
reference to `0x163A08`, at `0x163190` inside `FUN_00163168`. There were no Thumb references and no absolute
pointers. `FUN_00163168` is called from the auto-exposure routine `FUN_001631B8` when `cam->scene == 1`. The
analysis *assumed* scene 1 was Fireworks (stock's fixed 4 s mode). Later tracing showed the mode byte
(UI state `0x57EF3908+0x16`, copied to cam+0x54) is set by a switch at `0x345094`, where **scene-menu position 12
maps to mode 1**. So the assumption was unsupported, and the crash shows the value is reached on the normal
half-press and idle AE path.

---

## 5. Timeline of everything tried

| # | Attempt | Result |
|---|---|---|
| 1 | Flash 0.1 (EXIF string) | ✅ Works |
| 2 | Flash 0.2 (8 s) | Boots; System error on half-press |
| 3 | Battery reset | Boots fine until half-press, then error and shutdown |
| 4 | Reflash 0.1 via menu (shooting mode) | Error appeared on the firmware page or during the update. Not applied (still 0.2) |
| 5 | Power on via ▶ (playback) | System error within 1–2 s |
| 6 | Power on without SD card | System error within about 1 s |
| 7 | Button sweep at power-on (MENU, OK, MENU+OK, Delete, SCENE, Movie, zoom T/W, half-shutter, MENU+▶, OK+▶, MENU+Delete, up, down) | All showed System error. No hidden mode |
| 8 | Sanyo "compulsive boot" combos (A: MENU+OK+shutter+power; B: MENU+Left+▶) | A: the camera took a photo instantly; with USB plugged in right after, **no error for 10 s and PTP alive about 90 s**. Normal PTP only (same DeviceInfo, 04b0:0185); no service-mode descriptor. B: nothing new |
| 9 | Locked SD card (stock firmware at `/FIRMWARE/FIRMWARE.BIN`) | "Memory card is write protected", then off or System error. The boot-time updater gate is not met |
| 10 | Bright light into the lens + menu update | Reached Update → "Please wait" → System error or black screen |
| 11 | Plain Auto mode (not Easy Auto) + fast menu presses | ~5 s window. Reached Update → Yes → "Please wait" → **black screen, unresponsive**. Waited 10 min (10:59–11:09 pm), pulled batteries: still 0.2 |
| 12 | Repeated update attempts | Still 0.2 at each USB check |
| 13 | USB PTP probing | Alive about 1 s normally, about 90 s with the combo-A + quick-plug trick. Only standard ops answer |
| 14 | Hidden-opcode scan (`tools/ptp_opscan.py`, unadvertised opcodes only, no params, stops at first non-0x2005) | 257 opcodes scanned (0x1000–0x10FF, 0x9000–0x9013), all 0x2005; the camera crashed at 4.4 s. Resume from 0x9014 |

Other observations:
- **"Battery exhausted"** appears intermittently even with brand-new batteries. The owner says it has always
  done this and considers it noise.
- **The camera turns on by itself** when the USB cable is connected, and macOS's Image Capture daemons
  (`ptpcamerad`, `mscamerad-xpc`) prod it.
- **The camera clock** resets to 2011-01-01 on battery swaps; the clock backup cell is likely dead.

---

## 6. Static-analysis findings relevant to recovery

### 6.1 "System error" (from `docs/firmware/RECOVERY.md`)
- It is drawn by `FUN_002f7238`, reached via the shared message routine `FUN_002cbab0`.
- The fatal setter is `FUN_002ab3d4(code)`, with 68 callers.
- Every CPU exception vector raises it (codes 0xFB–0xFE), so **any crash** shows "System error".
- Other sources: NAND read failure (0x173), calibration-partition load failure (0x172), MAIN write failure in
  the updater (0x171), and the NDATA graphics loader `FUN_0032dab0` (0xC).
- Corrupt NV settings are not a cause: `FUN_0040cd64` silently loads defaults.
- The last fatal code is stored at NV+0x48.

### 6.2 The updater
- It lives entirely in **MAIN**, so it only runs once the main firmware is up. The first-stage bootloader is
  not in `firmware.bin` (NLIPI is only 12 bytes) and is never overwritten.
- The MAIN write is `FUN_001337dc`. It marks the boot descriptor (partition 0x65, word 3) with `0x0BADC0DE`
  during the write and clears it on success. What the loader does with a leftover marker is **unknown**.
- NPROG and NDATA are skipped when unchanged, so our flashes only rewrote MAIN.
- **Inferred:** in attempts 4 and 11, 0.2's fault fired during update preparation and killed it. The version
  stayed 0.2 and the camera still boots MAIN, so a partial MAIN write is not evident.

### 6.3 Boot-time card update (no menu)
- `FUN_003e5cf8` runs early in boot init (`FUN_00374b08`). It flashes `/FIRMWARE/FIRMWARE.BIN` with no UI and
  shows `FIRM UP` / `FIRM UP OK` / `FIRM UP ERR`.
- **Gates:** NV byte `0x57EF0408+0x155 == 3` (production-line mode, set when the NV partition is blank), power-on
  cause power-button or playback, card mount returns −2 (**inferred** to mean the lock switch), a valid file,
  and enough battery.
- Attempt 9 showed "write protected", so the gate isn't met on this retail unit (byte is probably 1).

### 6.4 Key-triggered update prompt
- A key index 0x10 (bit 0x800 in the key word from the sub-MCU) sets flag `0x57F02469`. Mode init then checks
  for FIRMWARE.BIN and shows the update prompt immediately (`FUN_0032b8d4`, `FUN_0032b348`).
- The physical button is **unidentified**, and one more gate (`0x57EF9C34`) is unresolved.
- It runs after boot init, so it still races the AE crash.
- Strings: "FIRMWARE   "/"FIRMWAREBIN" (8.3 FAT names) at 0x54CBB8/0x54CBE4/0x54CBF0, referenced by
  `FUN_002aa4e4` and `FUN_002aa6a8`. "FIRMWARE.BIN" at 0x54D330 is referenced by `FUN_002bc814`.

### 6.5 Factory adjustment mode (SG582.ADJ)
- It needs NV+0x155 ∈ {2, 0x10} **and** a card file `SG582.ADJ`. The check is at the end of boot init.
- The script interpreter chain is `FUN_002ad350 → FUN_002adf3c → FUN_002ae0e0 → FUN_002afaf4`, dispatching
  to `FUN_002a2cf8` (about 70 commands, 0x16–0x1EF).
- Packet format: `ESC, 'C'|'D'|'E', len_lo, len_hi, op, cmd, payload`.
- Commands include: update firmware from card ('E'/0x1A), reset NV to defaults (0x1F6), read last error
  ('E'/0x14), set mode byte ('C'/0xFF), and set exposure (0x66, no maximum).
- **It can't be entered on a retail unit**: the script can only set the byte once it is already running.
- `Cam*` names (CamDownloadFirmwareHandle=0x1D, CamWriteDataOp=0x1FB, CamWriteOp=0x207, CamDiagOp=0x1F6)
  belong to this card-script protocol.

### 6.6 USB (from `docs/firmware/USB_SERVICE.md`)
- The PTP dispatcher is `FUN_0011a4cc`. Handled opcodes are **exactly** the advertised set; everything else
  returns 0x2005.
- There is no vendor op, no firmware write, and no NV write.
- SetDevicePropValue on 0xD005/0xD407/0xD303 is inert.
- SendObjectInfo (`FUN_001190d0`) is generic FAT file creation. It has no FIRMWARE.BIN trigger and rejects
  folders at the card root (observed 0x200F/0x2002).
- FormatStore (0x100F) and DeleteObject (0x100B) are reachable and destructive. Avoid them.

### 6.7 Exposure engine (from `docs/firmware/ANALYSIS.md`)
- The hidden manual/shutter-priority engine is real: shutter table 30 s … 1/8000 s, Bulb (−1).
- Caps in `FUN_0015a33c`: 16 s, 24 s, or 60 s depending on the program.
- The normal slow limit comes from the table at `0x53CF10`, max id 52 = 4 s.
- **Open question:** why does 8,000,000 at 0x163A08 crash on half-press or idle? Candidates are a CCD timing
  limit, a watchdog, or a downstream check raising a fatal code. It is unknown whether it also runs during
  the updater's preparation.

---

## 7. Internet research (four passes, all negative for an owner-level fix)

- Searched: Nikon FAQ/manuals, DPReview, Reddit (Arctic Shift full history across r/Nikon, r/Cameras,
  r/AskPhotography, r/camerahelp, r/Coolpix), eevblog, Russian/Polish/Chinese repair forums, iFixit,
  last30days (Reddit and HN failed with an SSL certificate error in that run; YouTube had nothing relevant).
- No public COOLPIX L-series recovery exists: no button combo, no auto-update file, no USB recovery, no
  service software leak. Every failed-update thread ends at "send to Nikon" (e.g. dpreview threads
  3256276 and 1274422).
- Sanyo service manuals (VPC-HD1000, VPC-S60, VPC-HD2) describe a "compulsive boot" (button combo + USB) used
  with the Sanyo tool `DscCalDi.exe` / `camApi32.dll`, which can overwrite firmware. **Unverified** for Nikon,
  and the tool is not public. On this camera, combo A only gave normal PTP.
- Reddit: a COOLPIX P520 thread asking how to reflash a dead camera got no answers. "System error" threads are
  overwhelmingly Panasonic or Sony lens faults (irrelevant).
- No L120 teardown with board or chip IDs was found. iFixit has no board details.

---

## 8. Constraints and preferences

- **The owner:** a sentimental camera; no service centre available; willing to open it; open to brute force.
  Wants the stock HUD, zoom and buttons preserved if features are ever added again.
- **Mac disk is almost full** (under 1 GB free). Avoid large downloads and outputs.
- **Safety:** the flash capacitor holds about 300 V. Discharge it with a 1–2 kΩ, ≥2 W resistor in insulated
  pliers before touching the board.
- **Never** run FormatStore/DeleteObject on the card. Photos are backed up but the card is the original.
- Nikon firmware binaries must not be committed to the public repo (ckryptickunal/coolpix-l120-revival).

---

## 9. Open options (for the next agent to rank)

1. **Win the race on-camera.** Find a way to stop the AE path that reads 0x163A08 from running during the
   updater's preparation. Ideas not yet tried:
   - a scene mode that bypasses AE;
   - the updater reached from playback-boot plus a menu;
   - covering or uncovering the lens;
   - holding a button that freezes AE (half-press lock?);
   - temperature;
   - finding the updater's exact preparation steps and their duration (decompile `FUN_001337dc` and its callers).
   Also identify the physical button for key index 0x10 (§6.4).
2. **Explain the black screen.** Decompile the updater path to see whether "Please wait" → black could be
   normal (display off during the write) or a crash. Possibly read NV+0x48 (last fatal code) if any path
   exposes it.
3. **Hardware: UART.** Look for a serial console on the board. Search MAIN for printf/UART init to infer
   pins and baud. Sanyo boards often have test pads. A bootloader console might allow loading or flashing.
4. **Hardware: NAND reflash.** Desolder or clip the main NAND, dump it with a TSOP48 programmer (or have a
   phone-repair shop do it), locate MAIN in the dump (spare-area and ECC layout to be derived), restore the
   20 bytes to 0.1 or stock values, recompute ECC for the touched pages, write back. Since the dump will contain
   0.2 MAIN, matching `firmware.bin` MAIN gives a perfect reference for the layout.
5. **JTAG** on the ARM core, if pads exist: halt the CPU, patch RAM or flash, or run the updater from a debugger.
6. **Donor main board** from another L120 (eBay/OLX). Swapping boards may need calibration data from the
   original board.

---

## 10. Files in the repo (`~/Desktop/l120`, public GitHub: ckryptickunal/coolpix-l120-revival)

- `docs/firmware/ANALYSIS.md` (full RE), `FLASHING.md`, `RECOVERY.md` (boot paths, factory mode),
  `USB_SERVICE.md` (PTP dispatcher), `SERVICE_NOTE.md` (technician summary)
- `l120/camera.py` (PTP driver), `l120/broker.py` (USB owner process), `l120/mcp_server.py` (17-tool MCP),
  `l120/lab/*` (photo tools)
- `tools/ptp_probe.py`, `tools/ptp_opscan.py` (hidden-opcode scan), `tools/mcp_smoke.py`
- `firmware/vendor/` (gitignored): Nikon DMG, stock bin, 0.1 bin, 0.2 bin
