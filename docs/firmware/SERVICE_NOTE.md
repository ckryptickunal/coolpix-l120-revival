# Service note: COOLPIX L120 with modified firmware (for the repair technician)

**Symptom:** "System error" a few seconds after every power-on (also when powered on with the playback button,
with or without an SD card). The Setup > Firmware version updater can be reached, but the update aborts
("Please wait", then a black screen) before it completes.

**Cause:** the owner flashed a modified copy of Nikon's official v1.1 firmware through the normal
Setup > Firmware version menu. The camera is running that build now; over USB it reports the version string
`L120 REVIVAL 0.2` (stock reports `COOLPIX L120V1.1`).

**Exactly what differs from stock v1.1 `firmware.bin`** (stock SHA-256 `e48fc17b37011802f58f0a11d40f8bd95fae1088775dad892406f49008e0c4eb`):

| MAIN offset | Stock | Modified | Meaning |
|---|---|---|---|
| `0x163A08` | `00 09 3D 00` (4,000,000) | `00 12 7A 00` (8,000,000) | an exposure-time constant (µs) used by auto-exposure |
| `0x56EA60` | `COOLPIX L120V1.1` | `L120 REVIVAL 0.2` | version / EXIF Software string |
| header | `SUM=664635594` | `SUM=664635605` | MAIN byte-sum updated to match |

Nothing else was changed: same lengths, NPROG/NDATA/NLIPI identical to stock, no bootloader changes. The
exposure-constant change makes the auto-exposure path raise a fatal error, which also interrupts the
on-camera updater.

**What fixes it:** reflash stock Nikon COOLPIX L120 firmware v1.1 (or restore the main firmware region)
using service equipment that does not depend on the camera's own menu. The previous working state was
stock v1.1.

**Owner's data:** photos are on the SD card and backed up; formatting is fine if needed.
