# L120 Feature Catalog

What the community camera-hacking world has built, and what of it can be brought to a COOLPIX L120 (firmware V1.1).
Researched 2026-10-20. **⚠️ = unconfirmed** (inferred, not verified on this camera or in a primary source).

Effort key: **S** = an afternoon · **M** = 1–3 days · **L** = 1–2 weeks · **XL** = months / research project.

## Facts this catalog relies on

| Fact | Source |
|---|---|
| USB exposes only standard PTP ops; capture, file download/delete/**upload**, battery level (0x5001), clock (0x5011). No live view, zoom or exposure control. | `probe/summary.txt`, `probe/deviceinfo.json` |
| libgphoto2 lists the L120 (`0x04b0:0x0185`) with `PTP_CAP` (remote capture supported). | [libgphoto2 ptp2/library.c](https://github.com/gphoto/libgphoto2/blob/master/camlibs/ptp2/library.c) |
| **No Eye-Fi menu.** The setup menu has 16 items (Welcome screen … Firmware version); none is Eye-Fi upload. | L120 User's Manual p.115 ([mirror](https://www.manualsdir.com/manuals/191667/nikon-coolpix-l120.html?page=115)) |
| TV settings: NTSC/PAL, HDMI Auto/480p/720p/1080i, **HDMI device control** (CEC) default On. | Manual p.131 ([mirror](https://www.manualsdir.com/manuals/191667/nikon-coolpix-l120.html?page=131)) |
| CEC: the TV remote works **during playback only**: select images, play/pause movies, switch full-frame/4-thumbnail view. | Manual p.103 ([mirror](https://www.manualsdir.com/manuals/191667/nikon-coolpix-l120.html?page=103)) |
| The manual describes TV output only for playback. Whether HDMI/AV mirrors the shooting screen is **not stated**. ⚠️ | Manual p.102 |
| Nikon's last firmware is V1.1 (2011-06-21). | [Nikon Download Center](https://downloadcenter.nikonimglib.com/en/products/37/COOLPIX_L120.html) |
| Nikon Hacker work covers EXPEED 2/3 DSLRs; nothing published for Coolpix compacts. | [dslrbodies hacks page](https://dslrbodies.com/cameras/general-nikon-camera-info/nikon-camera-hacks.html), [nikon-firmware-tools](https://github.com/simeonpilgrim/nikon-firmware-tools) |
| Only EXPEED C2 patch lead: P7000 SCN-mode firmware, flashed via the normal SD update path, offline-validated only. | [huwanduf/P7000-SCN-Firmware](https://github.com/huwanduf/P7000-SCN-Firmware) |

---

## Tier A: doable now on the Mac side (stock firmware, no camera changes)

| # | Feature | What it is | Comes from | Why it matters to the owner | Effort |
|---|---|---|---|---|---|
| A1 | Remote shutter + auto-import | Fire over PTP, pull JPEG to the Mac | [gphoto2](http://gphoto.org/), [digiCamControl](https://digicamcontrol.com/), [Entangle](https://entangle-photo.org/) | Already built (`l120/camera.py`). Base for everything else. | done |
| A2 | Phone/web remote over Wi-Fi | Small web page served from the Mac (or a Pi, see Connectivity) with Shoot / Timelapse / Stack buttons and the last shot | [qDslrDashboard](https://dslrdashboard.info/), CHDK [chdkptp](https://app.assembla.com/spaces/chdkptp/wiki) GUI | Fixes "connectivity" with zero hardware: control from the phone across the room. | S |
| A3 | Simulated long exposure / stacking | Merge N frames (lighten, mean, median) | CHDK long-exposure scripts; [Siril](https://siril.org/), [Sequator](https://sites.google.com/view/sequator/) | Already built. Replaces the missing bulb mode for trails and smooth water. | done |
| A4 | Aligned stacking (astro / handheld noise reduction) | Register frames (OpenCV ECC/star alignment) before mean/median | Siril, Sequator, DeepSkyStacker | Cleaner night shots from a tiny 1/2.3" CCD. Mean of 8 frames ≈ √8 ≈ 2.8× less noise. | M |
| A5 | Multi-frame super-resolution | Align a handheld burst, merge to higher detail | Google HDR+/Handheld Super-Res idea | More detail at full 21× zoom where the lens is soft. ⚠️ gains depend on JPEG compression. | M |
| A6 | Timelapse deflicker + video | Intervalometer, then brightness-smoothing before encoding | CHDK intervalometer; [LRTimelapse](https://lrtimelapse.com/) deflicker | Already shoots timelapses (min ≈5 s). Auto exposure flickers; deflicker fixes that. | S |
| A7 | Post-shot histogram / clipping (zebra) report | After each capture, show histogram and blown-highlight overlay on the Mac | CHDK & [Magic Lantern](https://www.magiclantern.fm/) zebra/histogram | The camera's auto mode overexposes skies; this tells you when to use exposure comp on the body. | S |
| A8 | Guided exposure bracketing → HDR | Mac prompts "set EV −2 / 0 / +2 on the camera", shoots each, fuses (Mertens) | CHDK/ML bracketing; [Hugin enfuse](https://hugin.sourceforge.io/) | HDR without firmware. EV is set by hand because PTP can't set it. ⚠️ check that the auto mode allows exposure comp. | S |
| A9 | Panorama stitching | Shoot a sequence (or use the camera's Panorama assist scene), stitch on the Mac | [Hugin](https://hugin.sourceforge.io/) | Big scenes from the 21× lens; "gigapixel" panos at long zoom. | S |
| A10 | Sound / external trigger | Mac mic (clap, thunder) or a keyboard/MIDI/Shortcuts trigger fires the shutter | CHDK USB-remote & motion-detect scripts | Hands-free selfies/groups. ~1–4 s PTP latency rules out lightning. ⚠️ latency not measured per trigger. | S |
| A11 | Geotagging | Write GPS from an iPhone track (GPX) into EXIF after import | [ExifTool geotag](https://exiftool.org/geotag.html) | Camera has no GPS; this adds maps to the photo library. | S |
| A12 | Clock fix + auto-sync | Set camera date/time from the Mac on every connect | PTP 0x5011 | Camera is stuck at 2011-01-01; wrong dates break photo sorting. Built, not yet run. | S |
| A13 | Battery monitor (AA) | Read PTP 0x5001 and warn on low battery during long timelapses | PTP standard | The camera eats AAs; stops timelapses from dying half-way. | S |
| A14 | Lightroom / Photos hot-folder | Auto-import with keywords, ratings, presets | Lightroom tethering, digiCamControl | One-step workflow into the existing library (Lightroom MCP is available). | S |
| A15 | AI culling / auto-captioning via MCP | Claude rates, captions and picks keepers from a session | this repo's MCP server | Fits the existing `nikon-l120` MCP; "shoot 30, keep the 5 sharp ones". | S |
| A16 | Video post-processing | ffmpeg stabilisation (vidstab), denoise, upscale the 720p clips | [ffmpeg vidstab](https://github.com/georgmartius/vid.stab) | Makes 720p handheld zoom clips watchable. | S |
| A17 | Push files to the card over USB | PTP SendObject (camera advertises File Upload) | PTP standard | Could put a test `firmware.bin` on the card without a card reader during patch work. ⚠️ the camera may only accept DCIM files. | S |
| A18 | TV slideshow driven by the Mac | Mac sends CEC key presses through a USB-CEC adapter while the camera is on HDMI | [libCEC](https://github.com/Pulse-Eight/libcec) + [Pulse-Eight USB-CEC](https://www.pulse-eight.com/p/104/usb-hdmi-cec-adapter) | Uses the stock "HDMI device control". **Playback only** (manual p.103), so no shutter over CEC. ⚠️ that the camera accepts CEC from a non-TV source. | M |
| A19 | HDMI/AV capture as webcam or stream | HDMI → USB capture dongle → OBS → virtual cam / YouTube | Common "camera as webcam" setups ([OBS](https://obsproject.com/)) | Only works if the camera mirrors the shooting screen on HDMI/AV. The manual only describes playback. ⚠️ **test first** with a cheap dongle. Playback/slideshow streaming works regardless. | S (+hw) |
| A20 | Wi-Fi SD card transfer | ez Share / FlashAir / Eye-Fi card serves files over its own hotspot | [ez Share](http://www.ezshare.com/), [FlashAir](https://www.kioxia.com/), Eye-Fi | Wireless transfer with no cable. No Eye-Fi menu (p.115), so the card runs standalone and the camera's Auto off can cut its power. ⚠️ not tested on this body. | S (+hw) |

## Tier B: needs a firmware patch

Patching is high-risk (brick risk, see README roadmap). Nikon firmware is checksummed, and Nikon has warned that unofficial update methods can kill a camera. Every item depends on first understanding `firmware.bin` (CPU, load address, checksum).

| # | Feature | What it is | Comes from | Why it matters | Effort |
|---|---|---|---|---|---|
| B1 | Longer shutter / bulb | Raise the 4 s Fireworks cap (e.g. 8–30 s); bulb via a held button | CHDK long-exposure override; ML bulb | Real night/astro photos, the owner's #1 missing feature. ⚠️ how long the CCD runs before amp glow/heat dominate is unknown. | L (after RE) |
| B2 | Manual / shutter / ISO priority | Expose locked exposure parameters in a menu | CHDK overrides; Nikon Hacker (manual ISO/shutter in LV) | Creative control the stock auto mode lacks, while keeping the stock HUD. | XL |
| B3 | Patched scene-mode tables | Change values inside existing scene modes (e.g. Fireworks shutter, Night ISO) without new UI | [P7000-SCN-Firmware](https://github.com/huwanduf/P7000-SCN-Firmware) approach | Smallest useful patch: no new menus, so the stock HUD and buttons stay. Best first real patch. | L |
| B4 | RAW dump | Save the sensor buffer to the card next to the JPEG | CHDK RAW/DNG; old Coolpix 2100/3100/3700 RAW hacks | Latitude for editing and true stacking. ⚠️ needs finding the raw buffer in RAM. | XL |
| B5 | PTP vendor extension | Add custom PTP opcodes: set exposure, zoom, live-view frames, run code | [CHDK PTP extension / chdkptp](https://app.assembla.com/spaces/chdkptp/wiki); ML [usb.mo](https://github.com/petabyt/usb.mo) | Unlocks USB live view, real webcam, remote zoom, full Mac control. Highest value but hardest. | XL |
| B6 | Shooting-screen on HDMI / clean HDMI | Mirror the live view to HDMI without overlays | Nikon Hacker clean HDMI | Proper webcam/streaming via capture dongle. Only needed if A19 test fails. | XL |
| B7 | On-camera intervalometer | Timelapse without a Mac | CHDK, ML | Leave the camera out alone overnight. | L |
| B8 | Motion-detect trigger | Fire when a region of the frame changes | CHDK motion detection (lightning, wildlife) | Lightning/wildlife shots; impossible from the Mac due to latency. | XL |
| B9 | Scripting (Lua/uBASIC-like) | Run user scripts on the camera | CHDK | Turns every other feature into a script instead of a patch. | XL |
| B10 | On-LCD histogram / zebra / grid | Overlays on the stock HUD | CHDK, ML | Exposure feedback while framing. | L |
| B11 | Higher JPEG quality / NR off | Lower compression; disable noise reduction / hot-pixel suppression | Nikon Hacker ("Optimal quality", star-eater off) | Better files for stacking and astro. | L |
| B12 | Auto-off disabled on USB / finer battery gauge | Small behaviour tweaks | P7000-SCN-Firmware (5-level battery indicator) | Long tethered sessions; AA battery planning. Easy "hello world" patch candidate. | M (after RE) |
| B13 | Movie limit / bitrate | Remove recording time cap; raise 720p bitrate | Nikon Hacker, [OpenMemories-Tweak](https://github.com/ma1co/OpenMemories-Tweak) | Longer, cleaner clips. ⚠️ SD write speed and encoder limits unknown. | L |

## Tier C: not physically possible on this hardware

| Feature | Comes from | Why not |
|---|---|---|
| Built-in Wi-Fi / Bluetooth / NFC | Modern cameras, Sony PMCA apps | No radio in the body. Use Tier A connectivity instead. |
| GPS tagging in-camera | GPS models | No receiver. A11 is the substitute. |
| 1080p/4K video, RAW video | Magic Lantern RAW video | 720p is the ceiling of this sensor readout, encoder and SD bus. ⚠️ 1080p "maybe with luck" is not supported by any evidence. |
| Dual ISO | Magic Lantern | Depends on CMOS line-by-line gain; the L120 has a CCD. |
| USB webcam class (UVC) | Modern webcams | Would need a new USB stack in firmware; practically impossible. B5's PTP live view is the realistic route. |
| External mic / hot-shoe flash / remote port | DSLR hacks | No connectors for them. |
| Manual focus by ring / interchangeable lens | — | No focus ring or mount. ⚠️ motorised MF via firmware is possibly B-tier but unknown. |
| USB power or charging | — | Runs on AA batteries; power via the EH-67 AC adapter, not USB. ⚠️ |

---

## Connectivity options (ranked)

Prices are approximate retail in October 2026; INR at about ₹88/USD.

| Rank | Option | What you get | What you don't | Cost |
|---|---|---|---|---|
| 1 | **Raspberry Pi Zero 2 W as a Wi-Fi bridge** (camera USB → Pi running gphoto2/this driver + web UI) | Wireless remote shutter, timelapse, auto-upload to Mac/iCloud/Drive, from any phone. Camera can sit anywhere. | Live view (stock firmware has none). | ~₹2,500–3,500 / ~$30–40 (board $15 + SD + OTG cable + power) ⚠️ India prices not checked |
| 2 | **Mac-hosted web remote** (A2) | Same remote control from a phone on the home Wi-Fi; no new hardware. | Camera stays cabled to the Mac. | Free |
| 3 | **Wi-Fi SD card** (ez Share best available; FlashAir is discontinued, leftover stock only; Eye-Fi X2 servers shut down Sept 2016) | Wireless file transfer to phone/Mac. | Any camera control. No Eye-Fi menu, so the card is unmanaged and Auto off may cut transfers. | ez Share 32 GB ≈ $50–57 (eBay), £66 UK; ⚠️ about ₹4,000–5,000 in India, not confirmed |
| 4 | **HDMI → USB capture dongle + OBS** | TV-quality playback/slideshow into OBS for streaming; webcam **if** the camera mirrors the shooting screen (test). | Guaranteed live view. | ₹1,500–2,100 / $12–25 (generic USB 3.0 / MS2109-class) |
| 5 | **Composite AV → USB (EasyCap-type)** | SD (480i/576i) feed via the bundled AV cable; same mirroring caveat. | HD. Mac drivers are flaky on older units. | ~₹500–1,000 / $8–15 |
| 6 | **Pulse-Eight USB-CEC adapter + libCEC** | Mac drives playback (next/prev, play/pause) over HDMI. | Shutter, zoom or shooting control (playback only). Low value for the price. | ~$44 (Pulse-Eight site) |

**Recommended sequence:** do A2 (free), buy a ₹1,500 HDMI dongle to settle the live-view-on-HDMI question (A19), then build the Pi bridge (rank 1) if wireless matters more than having the Mac nearby.

## Sources
- L120 manual pages: [p.102](https://www.manualsdir.com/manuals/191667/nikon-coolpix-l120.html?page=102), [p.103](https://www.manualsdir.com/manuals/191667/nikon-coolpix-l120.html?page=103), [p.115](https://www.manualsdir.com/manuals/191667/nikon-coolpix-l120.html?page=115), [p.131](https://www.manualsdir.com/manuals/191667/nikon-coolpix-l120.html?page=131)
- Eye-Fi X2 end of support: [Australian Photography](https://www.australianphotography.com/news/eye-fi-ceases-support-for-x2-cards-older-services), [TidBITS](https://tidbits.com/2016/08/15/eye-fi-gives-x2-card-owners-a-reprieve/)
- FlashAir discontinuation: [Kioxia notice, Dec 2025](https://europe.kioxia.com/de-de/personal/news/2025/20251211-1.html)
- ez Share pricing: [YPC UK](https://www.ypc.co.uk/product/wifi-sd-memory-card-32gb-class-10-2nd-generation-ez-share/), [eBay](https://www.ebay.com/itm/112582018409)
- Capture cards in India: [gamingnation.in roundup](https://gamingnation.in/best-capture-cards-under-3000/), [getpc.co.in](https://getpc.co.in/parts/capture-cards/generic-hdmi-capture-1080p)
- CEC adapter: [Pulse-Eight product](https://pulse-eight.com/p/104), [manual](https://support.pulse-eight.com/support/solutions/articles/30000048842-usb-cec-adapter-user-manual)
- OpenMemories / Sony PMCA: [OpenMemories-Tweak](https://github.com/ma1co/OpenMemories-Tweak)
- CHDK PTP live view clients: [CHDK-PTP-Java](https://github.com/acamilo/CHDK-PTP-Java), [chdkptp crate](https://docs.rs/chdkptp)
