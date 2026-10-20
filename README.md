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

## What it can do now

**Camera control over USB** (tested on the real camera, firmware V1.1)

| Feature | Status |
|---|---|
| Remote shutter from the Mac, full 14 MP JPEG | ✅ 4.6–4.8 s per shot (gphoto2: 18.6 s) |
| Simulated long exposure (light trails, silky water, remove passers-by) | ✅ |
| Timelapse → MP4 | ✅ min interval ≈ 5 s |
| Card browser, thumbnails, import (per folder, never deletes) | ✅ |
| Automatic clock fix: every time the camera connects, its clock is set from the Mac if it is off by more than a minute (it resets to 2011-01-01 on every battery swap). Log: `/tmp/l120-clock.log`; opt out with `L120_NO_CLOCK_SYNC=1` | ✅ tested with a simulated reset |
| Plug-and-go auto-import (+ optional Apple Photos → iPhone) | ✅ 315 files / 1.7 GB in 4 min 21 s, size-checked, files keep the camera's dates |

**Lab: new photo powers on the Mac** (tested on real L120 frames; see `docs/lab/`)

| Feature | What it gives the old camera |
|---|---|
| Night mode | Handheld multi-frame align + merge, like a phone's night mode (−31 % noise on 3 real frames, −57 % on 6) |
| Aligned stacks | Light/star trails and smooth water without a tripod |
| HDR fusion | Merge shots taken at different exposure compensation (set EV on the multi-selector) |
| Panorama | Stitch a sweep of shots |
| Super-resolution | 2× multi-frame upscale (modest gain, mostly cleaner) |
| Deflickered timelapse | Removes auto-exposure flicker |
| Shot report | CHDK-style histogram, blown-highlight zebra, sharpness and noise after each shot |
| Smart cull | Flags blurry, badly exposed and duplicate shots; picks the best of each burst |
| Date repair | Fixes the 2011 clock dates on *copies* of your photos (never the originals) |
| Film looks | 8 presets (Kodachrome, Portra, Velvia, classic chrome, B&W red filter, teal-orange, CCD glow, faded) + the classic orange date stamp |
| Video tools | Web-ready MP4, stabilisation, smooth slow-mo, GIFs, best-frame grab |
| Memories | Self-contained HTML album, Ken Burns slideshow with music, "On this day" |
| Smart auto-rotate | The L120 has no orientation sensor; face + eye detection turns sideways portraits upright in albums/slideshows (19/21 right on a real card, skips when unsure) |

**MCP server `nikon-l120`** (registered in Claude Code): 17 tools, from `take_photo`, `night_mode`, `long_exposure` and
`timelapse` to `list_card`, `import_card`, `merge_photos`, `analyze_photo`, `cull_photos`, `apply_film_look`, `video_tool`
and `memories`. Ask Claude things like "take a night-mode shot with the L120", "turn my card photos into an album",
"make a 30-frame light-trail long exposure".

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

Full analysis: [docs/firmware/ANALYSIS.md](docs/firmware/ANALYSIS.md) (independently re-verified). **Nothing has been flashed.**

- Nikon's v1.1 `firmware.bin` is **unencrypted 32-bit ARM**, protected only by four simple per-section byte sums
  that `tools/firmware/fwcontainer.py` recomputes. No signature.
- The camera already contains a hidden **Manual / Shutter-priority exposure engine (30 s … 1/8000 s, plus Bulb)**,
  used only by factory test paths.
- The 4 s Fireworks limit is a single constant, so 8/15/30 s is a 4-byte patch (sensor behaviour past 4 s untested).
- A factory RAW dumper exists (RAW format not decoded yet).

| Feature | Verdict |
|---|---|
| Longer Fireworks exposure (8–30 s) | ❌ First attempt (8 s) caused "System error" on half-press on real hardware; recovered. The value is used on the normal metering path too. Redesign needed |
| Manual shutter/ISO | Feasible: engine exists, needs a UI hook |
| Bulb / 60 s | Feasible later |
| RAW | Promising |
| HDMI live view | Unknown, test stock first |
| USB remote exposure/zoom | Not in stock; a later code patch could add it |

**Milestone, 2026-10-20: the first modified firmware runs on a real L120.** The step-1 patch (EXIF/PTP version
text → `L120 REVIVAL 0.1`) was flashed through the camera's own update menu and works, which proves the rebuilt
image and checksums are accepted. The fixed 8 s long-exposure patch was tried next and failed safely ("System error" on half-press, recovered by reflashing). Long exposure is being redesigned to change exposure only at capture time.

**How it was done (for other owners):** flash the harmless step-1 test patch, which changes only the EXIF "Software"
text, to prove the camera accepts a rebuilt image. Then the long-exposure patch. Exact steps and the rollback
are in [docs/firmware/FLASHING.md](docs/firmware/FLASHING.md). Nikon's firmware is never included in this repo:
download it from Nikon.

## L120 Studio (web app)

A local web app that makes the camera pleasant to use from the laptop.

```bash
cd ~/Desktop/l120 && .venv/bin/python -m studio      # then open http://localhost:8120
```

| Panel | What it does |
|---|---|
| Status bar | Battery, free shots / space on the card, firmware, camera clock (orange when wrong) with **Sync to Mac**, **Phone** remote |
| Shoot (`1`, `Space`) | Big shutter button; the last shot shows full size; filmstrip of this session. *Keep on card* is on by default |
| Info strip (every shot) | CHDK-style check from `lab.analyze.shot_report`: RGB + brightness histogram, % blown / crushed, mean, noise, EXIF line, a **sharp / soft / blurry** badge, and **Zebra** (`Z`) stripes over clipped areas |
| Night (`2`) | Shoots a burst, releases the camera, then aligns and merges it with `lab.merge.night_mode` (handheld noise reduction) |
| Long exposure (`3`) | N frames merged as lighten / mean / darken / median, with a per-frame progress strip and a live merged preview. Stop early keeps what was shot |
| Timelapse (`4`) | Count, interval (≥ 5 s), fps → MP4, showing each new frame as it lands |
| Card (`5`) | Thumbnail grid of the card (folder shown on every tile: 100NIKON and 101NIKON reuse filenames). Click to open full size, ⌘/Shift-click or `A` to select, **Import to Mac** copies to `captures/card-import/<folder>/`. Nothing is ever deleted from the card |
| Darkroom (`6`) | Every photo in `captures/`. Open one for the 8 film looks as live thumbnails (+ the orange date stamp) and save full size; select several for **HDR fusion**, **Panorama** or **Night merge**. Originals are never changed; results go to `captures/darkroom/` |
| Memories (`7`) | **On this day** from an imported folder, a self-contained **HTML album**, and a Ken Burns **slideshow** MP4 (whole folder or just the on-this-day photos). Output in `captures/memories/` |
| Live view (`8`) | Shows the camera's HDMI/AV output through a USB capture dongle (lists AVFoundation devices via ffmpeg), with a how-to-connect card |

How it behaves:
- One camera operation at a time. Long jobs run in the background and stream progress to the page (Server-Sent Events); `Esc` stops a stack, timelapse, night burst or import after the current frame/file. Darkroom and Memories work runs on a second job runner, so it never blocks shooting.
- It opens the USB connection per operation and lets go ~3 s after (`L120_STUDIO_IDLE_S`), so the MCP server and scripts can use the camera in between. If another program has the camera, the page says so instead of hanging.
- `L120_STUDIO_FAKE=1 .venv/bin/python -m studio --port 8121` runs it against a simulated camera (output goes to `captures/fake/`), for UI work without the L120.
- Only answers `localhost` by default, and refuses requests whose Host header isn't local (blocks DNS-rebinding pages) and cross-site form posts.

### Phone remote (opt-in)

```bash
.venv/bin/python -m studio --lan
```

Serves on your Wi-Fi as well, behind a random access key that changes every start. The terminal and the **Phone** button show a QR code with the link; scan it on the phone (same network) and you get a phone layout with a big shutter and every tab except Live view. Requests from outside private network ranges are refused, and the key is traded for a cookie on first visit so it leaves the address bar. Without `--lan`, nothing is reachable from other devices.

## Usage

```bash
cd ~/Desktop/l120 && .venv/bin/python -c "from l120 import L120, features; c=L120().connect(); print(features.take_photo(c))"
```

Or just ask Claude: "take a photo with the L120", "do a 20-frame light-trail long exposure", "timelapse 60 shots every 10 s".

Output lands in `captures/`. Notes:
- **Camera broker:** macOS's Image Capture service grabs the camera whenever a program lets go and then keeps it
  busy for ~60 s with a card listing (this also wedged it). So one background process (`l120/broker.py`) holds
  the camera and every client (Studio, MCP, scripts) talks to it; it starts on demand and lets go after 20 idle
  minutes. Result: MCP calls went from 65–205 s to 0–23 s. `L120_DIRECT=1` bypasses it.
- End-to-end check of all MCP tools on the real camera: `.venv/bin/python tools/mcp_smoke.py`.
- A whole-card listing takes ~60 s on this camera; the driver lists folder by folder instead (instant).
- Plug-and-go: `python -m l120.autoimport --watch` (or `--install-agent` to run at login; add `--photos` for Apple Photos).
- Set the camera to shooting mode (not playback) and disable Auto off in the Setup menu for long sessions.

## Layout
- `l120/camera.py` – USB PTP driver
- `l120/features.py` – stacking, timelapse, import
- `l120/mcp_server.py` – MCP server
- `l120/autoimport.py` – plug-and-go import
- `l120/lab/` – night mode, HDR, panorama, analysis, film looks, video, memories
- `studio/` – L120 Studio web app (`server.py` HTTP + camera jobs, `lab.py` darkroom/memories/reports, `remote.py` phone access, `static/` page, `fake.py` simulated camera)
- `tools/` – probe scripts; `probe/` – probe results
