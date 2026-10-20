# COOLPIX L120: USB / PTP service-path analysis (recovery over the 90 s window)

Static analysis only, of the stock v1.1 `firmware.bin` MAIN section (base 0x0; file offset = MAIN addr + 0x60).
Nothing was run and no USB was touched. **C** = confirmed from decompiled code / bytes, **I** = inferred, **?** = unknown.
Addresses are MAIN addresses. Companion docs: [ANALYSIS.md](ANALYSIS.md) (patching), [RECOVERY.md](RECOVERY.md) (boot/card recovery).

## Bottom line

**There is no USB-reachable way to reflash firmware, reset NV, or fix the patched literal on this camera.**
The PTP stack handles only the operations it advertises; it has no vendor firmware/write operation. All the
`Cam*` firmware-download / write / NV-reset capabilities live behind the Sanyo **SXC factory protocol**, whose
only feeder is the **card-script interpreter** reading `SG582.ADJ` — not USB — and which is further gated by an
NV factory byte that USB cannot set. Over USB you can enumerate, read properties, take a picture, delete/format,
and write ordinary files to the card's FAT filesystem; none of these reach the updater. Recovery therefore
belongs to the card + boot-path approach in RECOVERY.md (or service/JTAG), not to USB.

---

## 1. The PTP operation dispatcher (question 1) — C

Single dispatcher **`FUN_0011a4cc`** (MAIN `0x11a4cc`), called from the USB command task via `0x112898`.
It parses the 12-byte PTP command container: opcode = `local_82` (u16 at container+6), transaction id at +8,
parameters 1–5 at +0xC,+0x10,+0x14,+0x18,+0x1C.

Dispatch logic (C):
- **Bit 15 set** (`(opcode & 0x9000) >> 15 == 1`, i.e. opcode ≥ 0x8000): routed to `FUN_0011d5d8` →
  `FUN_00126098` (the MTP operation sub-dispatcher). See §1b.
- Otherwise: `switch(opcode − DAT_0011b3d8)` where **`DAT_0011b3d8 = 0x1001`** (`0x11b3d8`, C). Gaps fall to
  `default:` which returns the not-supported response.

Response codes used: OK = `0x2001` (`DAT_0011b3b4`), SessionNotOpen = `0x2003`, **OperationNotSupported =
`0x2005`** (`DAT_0011b3d8`-area sentinels). Any opcode not in the table below is answered `0x2005`.

### 1a. Standard operations (switch base 0x1001), every handled opcode — C

| Opcode | PTP name | Case | Handler(s) |
|---|---|---|---|
| 0x1001 | GetDeviceInfo | 0 | `FUN_00114cc0` → `FUN_001135a4` → `FUN_001295fc` → `FUN_0012a85c` |
| 0x1002 | OpenSession | 1 | inline; `FUN_00129e3c` (stores session id in `DAT_0011b3d4[0]`) |
| 0x1003 | CloseSession | 2 | `FUN_0011d794` / `FUN_00129e58` |
| 0x1004 | GetStorageIDs | 3 | `FUN_001176fc` |
| 0x1005 | GetStorageInfo | 4 | `FUN_00117a1c` |
| 0x1006 | GetNumObjects | 5 | `FUN_0011dcd0` / `FUN_001259a0` |
| 0x1007 | GetObjectHandles | 6 | `FUN_00114da0` / `FUN_00114e70` |
| 0x1008 | GetObjectInfo | 7 | `FUN_001153b8` / `FUN_00115450` / `FUN_00115618` / `FUN_001156d4` |
| 0x1009 | GetObject | 8 | `FUN_00115a08` / `FUN_00115d70` / `FUN_00115e58` / `FUN_00115ef8` / `FUN_00115f7c` |
| 0x100A | GetThumb | 9 | `FUN_00116ab8` / `FUN_00116e68` |
| 0x100B | DeleteObject | 0xA | `FUN_00118674` / `FUN_00122d54` |
| 0x100C | SendObjectInfo | 0xB | `FUN_001190d0` |
| 0x100D | SendObject | 0xC | `FUN_0011a138` / `FUN_0011966c` |
| 0x100E | InitiateCapture | 0xD | `FUN_0011a260` / `FUN_0011a294` |
| 0x100F | FormatStore | 0xE | `FUN_0011a3c8` / `FUN_0011a3fc` |
| 0x1010–0x1013 | — | default | **not supported (0x2005)** |
| 0x1014 | GetDevicePropDesc | 0x13 | `FUN_00117cf8` |
| 0x1015 | GetDevicePropValue | 0x14 | `FUN_0011815c` |
| 0x1016 | SetDevicePropValue | 0x15 | `FUN_001185cc` (→ §3) |
| 0x1017–0x101A | — | default | **not supported (0x2005)** |
| 0x101B | GetPartialObject | 0x1A | `FUN_00116f54` / `FUN_0011731c` / `FUN_0011743c` |

### 1b. Vendor / MTP operations (bit-15 path) — C

`FUN_00126098` keys off `opcode − DAT_00126f2c` where **`DAT_00126f2c = 0x9801`** (`0x126f2c`, C):

| Opcode | MTP name | Handler |
|---|---|---|
| 0x9801 | GetObjectPropsSupported | `FUN_001261ec` |
| 0x9802 | GetObjectPropDesc | `FUN_001262d8` |
| 0x9803 | GetObjectPropValue | `FUN_00126410` |
| 0x9805 | GetObjectPropList | `FUN_00126668` |
| **everything else** (incl. 0x9804 SetObjectPropValue, 0x9806+, 0x90xx, 0xF0xx, any 0xDxxx-as-operation) | — | falls through → `FUN_001290dc` returns **0x2005** |

**Conclusion (C): the set of handled operations is exactly the set advertised in DeviceInfo**
(0x1001–0x100F, 0x1014–0x1016, 0x101B, 0x9801–0x9803, 0x9805). There are **no hidden vendor operations** —
no 0x90xx, no 0xFxxx, no firmware/write opcode. The "microsoft.com: 1.0" extension string (`0x56F6E0`) is only
the MTP VendorExtensionDesc; it does not unlock extra ops. DeviceInfo is built by `FUN_0012a85c` (`0x12a85c`)
from two capability sets (mode 0 at `0x535908`, mode 3 at `0x5358d8`); both advertise the **identical** operation
list, so no USB "mode" exposes more ops.

## 2. The `Cam*` names: Sanyo SXC factory protocol, not USB (question 2) — C

The `Cam*` strings (`0x54BBF0`–`0x54C470`) and the schema `http://www.sanyo.co.jp/DSC/sxc/schema/` (`0x548A63`)
are Sanyo's **SXC** (eXtended camera) property model. Two 24-byte-record registries reference them:

- Group-2 registry at **`0x549C88`**: `{group=2, id, name_ptr, type=0x9B, 0xFFFFFFFF, 0xFFFFFFFF}` — 120 records.
- Group-1 registry at **`0x54A7C8`**: `{group=1, id, name_ptr, type, 0xFFFFFFFF, parent_ptr}`.

The property **ids are a Sanyo namespace, not PTP codes**: e.g. CamRes=0x01, CamDownloadFirmwareHandle=0x1D,
CamExposureMode=0x66, CamSecretKey=0xC6, CamUsbInitMode=0xA5, CamPcCamera=0x6D, CamDebugUsbMode (string
`0x54C424`), and the "Op" actions CamDiagOp=**0x1F6**, CamHostPowerOff=**0x1F4**, CamWriteDataOp=**0x1FB**,
CamWriteOp=**0x207**. **These ids are identical to the factory-script "execute-op" codes in RECOVERY.md §4**
(0x1F6 = NV/diag, 0x207 = update-from-RAM-buffer, etc.).

Where the protocol is fed (C): the SXC/factory command dispatcher `FUN_002a2cf8` has exactly one caller, the
packet parser `FUN_002afaf4`, which has exactly one caller, the script interpreter `FUN_002ae0e0`, whose chain is
`FUN_002adf3c` → `FUN_002ad350` — the **`SG582.ADJ` card-script** path (RECOVERY.md §4). No reference to the SXC
registries or `FUN_002a2cf8` exists anywhere in the USB/PTP module (0x11xxxx–0x12xxxx); the PTP dispatcher
`FUN_0011a4cc` never calls into it. A whole-image scan found **no absolute pointer to the registry base** at all,
consistent with purely internal (script-interpreter) use.

So, answering the sub-questions: there is **no PTP vendor op** carrying SXC, **no SendObject-of-a-magic-file**
hook (see §4), and `CamDebugUsbMode` / `CamUsbInitMode` / `CamPcCamera` are merely entries in this registry —
settable only through the card script, and no USB-descriptor switch keyed on them is reachable over PTP. A host
cannot "enter" the factory/SXC mode over USB; `CamSecretKey` (id 0xC6) is a property inside a mode you cannot
reach from USB in the first place.

## 3. USB paths that write firmware or NV? None (question 3) — C

- **No PTP operation writes firmware.** `CamDownloadFirmwareHandle` (id 0x1D), `CamWriteDataOp` (0x1FB) and
  `CamWriteOp` (0x207) are SXC ops reached only through the card-script factory protocol (§2), not USB. The
  actual card-triggered updater (`FUN_002AA4E4` etc.) lives behind the menu / boot-card paths (ANALYSIS §2,
  RECOVERY §2).
- **SetDevicePropValue (0x1016 → `FUN_001185cc`) writes nothing dangerous.** It receives ≤16000 bytes then splits:
  vendor props (bit 15, `FUN_0011d750` → table `0x5D42C0`) and standard props (`FUN_001184d4`).
  - Vendor props **0xD407 → `FUN_00128F1C`**, **0xD303 → `FUN_00128F4C`**, **0xD005 → `FUN_00129528`** are
    trivial descriptor-fillers (they set a datatype/default and return); **they do not change mode, touch NV, or
    flash** (C). Unknown vendor codes return AccessDenied/NotSupported via `FUN_00129380`.
  - Standard props: only 0x5011 (date/time) is writable; 0x5001 (battery) is read-only. No NV-reset property.

## 4. Simpler standard-PTP alternatives (question 4) — C

- **SendObjectInfo (`FUN_001190d0`)** is generic FAT object creation: it parses the ObjectInfo dataset, builds a
  path from the storage + parent handle, upper-cases the 8.3 name, and creates a file/association via
  `FUN_00118840` / `FUN_00118D80` / `FUN_00118D90`. **There is no special-casing of `FIRMWARE.BIN` or a
  `FIRMWARE` folder** anywhere in this handler — uploading such a file over PTP just drops it on the card; it does
  **not** invoke the updater. (The updater only runs from the menu or the boot card-paths, RECOVERY §1.) This
  matches the earlier observation that creating a folder at card root returned 0x200F AccessDenied: `FUN_001190d0`
  rejects root-level directory associations, so you cannot even create the `/FIRMWARE` folder over PTP.
- **SetDevicePropValue on 0xD005 / 0xD303 / 0xD407** does nothing useful (§3). No hidden property changes mode.
- **FormatStore (0x100F)** and **InitiateCapture (0x100E)** work but neither helps recovery (format erases the
  card; capture needs a working imaging path, which is what is faulting).

## 5. What the 90 s USB window is actually good for

Honestly, very little for *fixing* the camera:
- It confirms MAIN still boots far enough to run the USB task (the fault is later, per RECOVERY §3).
- `GetDeviceInfo` / `GetDevicePropValue` give identity and a few benign values. The stored fatal code (NV+0x48)
  is **not** exposed over PTP — only the card-script 'E'/0x14 command reads it, which USB can't reach.
- Writing `/FIRMWARE/FIRMWARE.BIN` is better done with the card in a PC card reader anyway (the PTP file-write
  can't create the needed folder, and offers no advantage over a reader).

**Recommended path is RECOVERY.md's card + boot-path approach, not USB.**

## 6. Confirmed vs inferred

Confirmed (decompiler/byte evidence): the operation dispatcher and its complete opcode table; base opcodes
0x1001 and 0x9801; that handled == advertised ops; the vendor/standard SetDevicePropValue split and the three
inert vendor-prop handlers; that SendObjectInfo has no firmware trigger; that the SXC/`Cam*` protocol's only
feeder is the card-script interpreter and the PTP module never calls it; no absolute pointer to the SXC registry
base exists in the image.

Inferred / unknown: whether the PTP file layer can write the card at all while the camera is in the post-error
state (**?**, would need a live test, which is out of scope here); exact meaning of SXC ids beyond those matched
to RECOVERY §4; that mode-3 DeviceInfo is a separate USB configuration (**I**; its op list is identical to mode 0).
