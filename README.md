# L120 Revival

Bringing a 2011 Nikon COOLPIX L120 back to life: Mac-side control, an MCP server, and a path toward custom firmware.

## What works today (tested on the real camera, firmware V1.1)

| Feature | How | Status |
|---|---|---|
| Remote shutter from the Mac | USB PTP `InitiateCapture` | ✅ ~4 s per shot, full 14 MP JPEG |
| Simulated long exposure | Shoot N frames, merge (lighten / mean / darken / median) | ✅ |
| Timelapse + MP4 | Intervalometer + ffmpeg | ✅ min interval ≈ 5 s |
| Card browser / import / thumbnails | PTP object ops | ✅ |
| Fix camera clock (stuck at 2011-01-01) | PTP property 0x5011 | built, not yet run |
| MCP server (`nikon-l120`) | registered in Claude Code, user scope | ✅ connected |

## Hard limits of the stock firmware over USB

The camera advertises only the standard PTP/MTP command set (probe: `probe/deviceinfo.json`):
no Nikon vendor commands, no live view, no zoom, no exposure/ISO/shutter control, no video start/stop.
Settable from the Mac: shutter, clock. Everything else lives on the camera body.

- **Webcam over USB: not possible** on stock firmware (no live view). Options: the AV-out cable into a USB
  capture dongle (SD quality), or your iPhone via Continuity Camera.
- **Real long exposure (seconds-long single shot): not possible over USB.** The camera picks shutter speed itself.
  It needs firmware changes (see below). The stacking mode is the stopgap: it simulates trails/smoothing, with
  ~4 s gaps between frames.

## Custom firmware: where it stands

- Nikon's only update is **v1.1** (`firmware/firmware.bin` inside `F-L120-V11M.dmg`); your camera already runs it.
- Chip: Nikon "EXPEED C2" (vendor unconfirmed). Indirect evidence says ARM.
- Nobody has published hacks for the L120. A one-day-old, unverified project patches the firmware of the
  COOLPIX P7000 (same EXPEED C2 generation) through the normal SD-card update path:
  https://github.com/huwanduf/P7000-SCN-Firmware — the best lead.
- `rea` (morluto/rea) is an LLM-agent wrapper around Ghidra; usable once we know the CPU (ARM LE) and load address.
  Ghidra directly is the more flexible starting point.

### Roadmap
1. Download the official v1.1 file and analyse `firmware.bin`: encryption/entropy, checksums, ARM code, load address. (No risk to the camera.)
2. Find the exposure tables (max shutter time per scene mode, e.g. Fireworks) and the menu code.
3. Only if the file is understood and checksums can be recomputed: a minimal "hello" patch (e.g. change a menu string), flashed via SD card.
   **This step can brick the camera.** It needs your explicit go-ahead.
4. Then real features: longer shutter limits, manual exposure, RAW dump, intervalometer on-camera.

## Usage

```bash
cd ~/Desktop/l120 && .venv/bin/python -c "from l120 import L120, features; c=L120().connect(); print(features.take_photo(c))"
```

Or just ask Claude: "take a photo with the L120", "do a 20-frame light-trail long exposure", "timelapse 60 shots every 10 s".

Output lands in `captures/`. Notes:
- macOS's Image Capture daemon grabs the camera; the driver kills it on connect (it respawns, harmless).
- Listing the card is slow (~60 s) the first time per connection while the camera indexes ~300 files.
- Set the camera to shooting mode (not playback) and disable Auto off in the Setup menu for long sessions.

## Layout
- `l120/camera.py` – USB PTP driver
- `l120/features.py` – stacking, timelapse, import
- `l120/mcp_server.py` – MCP server
- `tools/` – probe scripts; `probe/` – probe results
