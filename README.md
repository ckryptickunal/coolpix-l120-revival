# L120 Revival

Bringing a 2011 Nikon COOLPIX L120 back to life: Mac-side control, an MCP server, and a path toward custom firmware.

## The camera (official specs)

| | |
|---|---|
| Processor | Nikon EXPEED C2 (silicon vendor unknown; likely ARM) |
| Sensor | 1/2.3" CCD, 14.1 MP effective |
| Lens | 21x NIKKOR, 25–525 mm equiv., f/3.1–5.8, sensor-shift + electronic VR, macro from 1 cm |
| Screen | 3" 921k-dot LCD |
| ISO | 80–6400 |
| Shutter | 1/1000–1 s normally; **4 s max, Fireworks scene only**; no manual mode |
| Video | 720p HD with stereo sound |
| Ports | Hi-Speed USB 2.0 (PTP) + composite AV out (shared port, cable UC-E6 / EG-CP16), **mini-HDMI out with CEC** |
| Storage | SD/SDHC/SDXC + ~120 MB internal |
| Power | 4× AA (alkaline ~330 shots, lithium ~890, NiMH ~520), AC adapter EH-67 |
| Weight / size | ~431 g, 110 × 77 × 78 mm; announced 9 Feb 2011 |

Source: [Nikon press release](https://www.nikon.com/company/news/2011/0209_l120l23_03/). Controls: shutter with zoom ring, side zoom rocker on the barrel, power, movie-record, SCENE, playback, multi-selector (flash / self-timer / macro / exposure comp / OK), MENU, delete, pop-up flash.

Full researched feature roadmap (what other camera hacks such as CHDK and Magic Lantern do, sorted into "doable now", "needs firmware" and "impossible"): [docs/FEATURE_CATALOG.md](docs/FEATURE_CATALOG.md).

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

- **Webcam over USB: not possible** on stock firmware (no live view). Best bet: the **mini-HDMI port → a USB HDMI
  capture dongle** (~₹1,500), which macOS sees as a webcam. Untested: whether the camera sends its live shooting screen
  over HDMI or only playback (the manual documents playback only). The composite AV cable + EasyCap dongle is the SD fallback.
- **TV remote over HDMI-CEC** controls playback (browse photos, play movies) but not the shutter.
- **No Eye-Fi menu**, so a Wi-Fi SD card (e.g. ez Share) works only as a standalone transfer card.
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

## L120 Studio (web app)

A local web app that makes the camera pleasant to use from the laptop.

```bash
cd ~/Desktop/l120 && .venv/bin/python -m studio      # then open http://localhost:8120
```

| Panel | What it does |
|---|---|
| Status bar | Battery, free shots / space on the card, firmware, camera clock (orange when wrong) with **Sync to Mac** |
| Shoot (`1`, `Space`) | Big shutter button; the last shot shows full size; filmstrip of this session. *Keep on card* is on by default |
| Long exposure (`2`) | N frames merged as lighten / mean / darken / median, with a per-frame progress strip and a live merged preview. Stop early keeps what was shot |
| Timelapse (`3`) | Count, interval (≥ 5 s), fps → MP4, showing each new frame as it lands |
| Card (`4`) | Thumbnail grid of the card (folder shown on every tile: 100NIKON and 101NIKON reuse filenames). Click to open full size, ⌘/Shift-click or `A` to select, **Import to Mac** copies to `captures/card-import/<folder>/`. Nothing is ever deleted from the card |
| Live view (`5`) | Shows the camera's HDMI/AV output through a USB capture dongle (lists AVFoundation devices via ffmpeg), with a how-to-connect card |

How it behaves:
- One camera operation at a time. Long jobs run in the background and stream progress to the page (Server-Sent Events); `Esc` stops a stack, timelapse or import after the current frame/file.
- It opens the USB connection per operation and lets go ~3 s after (`L120_STUDIO_IDLE_S`), so the MCP server and scripts can use the camera in between. If another program has the camera, the page says so instead of hanging.
- `L120_STUDIO_FAKE=1 .venv/bin/python -m studio --port 8121` runs it against a simulated camera (output goes to `captures/fake/`), for UI work without the L120.
- No new dependencies: Python's standard-library HTTP server plus the existing `l120` package.

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
- `studio/` – L120 Studio web app (`server.py` backend, `static/` page, `fake.py` simulated camera)
- `tools/` – probe scripts; `probe/` – probe results
