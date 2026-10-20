# Flashing the step-1 test patch (and rolling back)

> **✅ Confirmed on real hardware (2026-10-20):** the step-1 EXIF patch was flashed to a COOLPIX L120 through the
> normal Setup → Firmware version menu. The camera accepted the rebuilt image, now reports `L120 REVIVAL 0.1` as its
> PTP DeviceVersion and in EXIF Software, and shoots normally (14 MP, same timing, same HUD/zoom/buttons).
> This proves the container format and checksum recomputation are right and that there is no signature check.


> **Owner's go-ahead required.** Nothing here has been done yet. Only the camera's owner should flash
> firmware, and only after reading [ANALYSIS.md](ANALYSIS.md) §8 (brick risk). No agent or script flashes
> the camera. Every step below is done by hand, using the camera's own Setup menu.

Step 1 changes one thing: the EXIF "Software" text written into each photo, from `COOLPIX L120V1.1` to
`L120 REVIVAL 0.1`. The HUD, menus, zoom, buttons and image processing are all untouched. It exists to prove
that the camera accepts a re-checksummed image before any real feature patch is tried.

## Before you start

- **Power:** use fresh AA batteries, ideally lithium (Energizer Ultimate Lithium), or freshly charged
  NiMH. **Never use half-used alkalines.** The battery indicator must show full.
- **Never interrupt an update.** If power fails part-way through, the camera may not start again.
  - Don't open the battery or card door while updating.
  - Don't press buttons.
  - Don't disconnect anything.
- **Two SD cards:**
  - Card A: for the test patch.
  - Card B: holds the stock v1.1 rollback file. Prepare this one first and keep it ready.
- **Back up your photos** from the cards you will format.
- Have the stock file's hash on hand. `firmware.bin` from Nikon's v1.1 package should have SHA-256
  `e48fc17b37011802f58f0a11d40f8bd95fae1088775dad892406f49008e0c4eb`.

## 1. Build the files on the Mac

From the repo root, with Nikon's updater already downloaded and extracted into `firmware/vendor/` (see
[tools/firmware/README.md](../../tools/firmware/README.md)):

```bash
python3 -I tools/firmware/build_patch.py firmware/vendor/extracted/L120Update/firmware/firmware.bin firmware/vendor/out/firmware.bin --exif "L120 REVIVAL 0.1"
```

```bash
python3 -I tools/firmware/fwcontainer.py info firmware/vendor/out/firmware.bin
```

The second command must end with `ALL CHECKSUMS OK`. The patched file's SHA-256 should be
`6b1225e3a99cf360df3619b60f960108466c4756d70c3fec6bbb69d713b9747b`:

```bash
shasum -a 256 firmware/vendor/out/firmware.bin
```

If either check fails, stop.

## 2. Prepare card B (rollback) first

1. Insert card B in the camera and format it: Setup menu → Format card.
2. On the Mac, create a folder named `firmware` at the card's root.
3. Copy the **stock** `firmware/vendor/extracted/L120Update/firmware/firmware.bin` into it, so the path is
   `<card B>/firmware/firmware.bin`.
4. Eject the card properly and label it "L120 stock 1.1".

## 3. Flash the test patch (card A)

1. Format card A in the camera.
2. On the Mac, copy `firmware/vendor/out/firmware.bin` to `<card A>/firmware/firmware.bin`. The file name
   must be exactly `firmware.bin`, inside a folder named `firmware` at the card root.
3. Eject the card and insert it in the camera. Fit fresh batteries.
4. Turn the camera on. Go to Setup menu → Firmware version → Update.
5. Confirm with **Yes**. Wait. Don't touch anything until the camera says the update is complete and asks
   you to turn it off.
6. Turn the camera off. Remove the card.

If the camera refuses the file (for example, it reports an error before updating), nothing was written. Note
the exact message and stop.

## 4. Check it worked

1. Turn on with a normal card. The Setup → Firmware version screen still shows **1.1**; that is expected.
2. Check that the HUD, zoom, scene modes and playback all behave exactly as before.
3. Take one photo. Read its EXIF Software tag, either with the repo's USB driver or after copying the photo
   to the Mac:
   ```bash
   sips -g software /path/to/DSCN0001.JPG
   ```
   It should show `L120 REVIVAL 0.1`. If it still shows `COOLPIX L120V1.1`, the update did not take effect.

## 5. Roll back to stock v1.1

Use card B and repeat section 3 with the stock file: Setup → Firmware version → Update, using the same
precautions. Afterwards, new photos show `COOLPIX L120V1.1` again.

## If something goes wrong

- **Camera hangs while taking a photo** (not expected for step 1):
  1. Remove the batteries for 30 seconds.
  2. Power on.
  3. Roll back with card B.
- **Camera does not start at all after an update:** stop. Don't keep cycling power with weak batteries.
  Record exactly what happened. Recovery options are unknown (ANALYSIS.md §8).
