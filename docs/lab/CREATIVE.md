# Creative lab: looks, video, memories

Mac-side tools for what the L120 already shot. None of them touch the camera.
Run from the repo root with the venv: `.venv/bin/python -m l120.lab.<module> ...`
Needs numpy, pillow, opencv-python-headless (in `requirements.txt`) and ffmpeg (Homebrew build with vid.stab).

## Film looks — `l120/lab/looks.py`

Eight recipes on top of the L120's JPEG. Each one combines a channel mixer, tone curves, saturation, split toning, optional highlight glow, grain and a vignette. All of it is plain numpy.

| name | feel |
|---|---|
| `kodachrome` | deep reds, dense shadows, warm contrast |
| `portra` | soft contrast, warm skin, creamy highlights |
| `velvia` | saturated slide film, deep blacks |
| `chrome` | muted documentary colour, cool shadows |
| `bw-red` | monochrome as if shot through a red filter |
| `teal-orange` | cinematic grade |
| `ccd-glow` | the 2008 digicam look: bright, cool, blooming highlights |
| `faded` | lifted blacks, warm cast, like an old print |

```sh
python -m l120.lab.looks list
python -m l120.lab.looks grid DSCN0300.JPG -o grid.jpg [--datestamp]   # every look side by side
python -m l120.lab.looks apply DSCN0300.JPG -l portra -o out.jpg --datestamp          # date from EXIF/mtime
python -m l120.lab.looks apply DSCN0300.JPG -l faded  -o out.jpg --datestamp 2011-07-23
python -m l120.lab.looks batch *.JPG -l chrome -o out_dir/
```

- **Date stamp.** The classic orange `'11 7 23` stamp, drawn as skewed seven-segment digits with a soft glow. It needs no font file.
- **Speed.** A full 14 MP render takes about 5 seconds. Pass `--max-size 2000` for quick results.
- **Metadata.** The original EXIF is copied into the output.

## Video — `l120/lab/video.py`

```sh
python -m l120.lab.video info      DSCN0123.MOV
python -m l120.lab.video convert   DSCN0123.MOV -o clip.mp4            # H.264/AAC, faststart, ~6x smaller
python -m l120.lab.video stabilize DSCN0123.MOV -o steady.mp4          # 2-pass vid.stab (+ light sharpen)
python -m l120.lab.video speed     DSCN0123.MOV -o slow.mp4 --factor 0.5   # motion-interpolated slow-mo
python -m l120.lab.video speed     DSCN0123.MOV -o fast.mp4 --factor 3
python -m l120.lab.video gif       DSCN0123.MOV -o loop.gif --start 2 --dur 3 --width 480
python -m l120.lab.video bestframe DSCN0123.MOV -o still.jpg           # sharpest, well-exposed frame
```

- **Stabilise gotcha.** The L120 records MJPEG, which ffmpeg decodes as `yuvj422p`. On that format `vidstabdetect` silently finds no motion, and the "stabilised" output comes out identical to the input. The code converts to `format=yuv420p` before both passes, which fixes it. For very dark, flat scenes, pass `--mincontrast 0.05`.
- **Slow-mo cost.** Slow motion uses `minterpolate` (motion-compensated), which is slow. A 6 s clip at 720p took about 21 s at 0.5x and 55 s at 0.25x. Fast motion or busy scenes can show warping artifacts. Use `--no-smooth` for plain frame repetition.
- **How `bestframe` scores frames.** It measures the variance of the Laplacian (a sharpness score) and weights it by an exposure penalty. On long clips it samples up to 300 frames.

## Memories — `l120/lab/memories.py`

```sh
python -m l120.lab.memories slideshow a.JPG b.JPG c.JPG -o show.mp4 [--music song.m4a] [--seconds 3] [--shuffle]
python -m l120.lab.memories album ~/Pictures/L120 -o ~/Pictures/L120-album [--title "..."] [--no-video]
python -m l120.lab.memories onthisday ~/Pictures/L120 [--date 2026-10-20] [--window 3] [--slideshow otd.mp4]
```

- **Slideshow.** Output is 1920x1080, 30 fps. Each photo gets an eased Ken Burns move (a random zoom in or out plus a pan), with crossfades between photos and fades from and to black at the ends. Landscape 4:3 photos are cover-cropped to 16:9. Portrait photos sit on a blurred copy of themselves. Music loops or trims to fit and fades out at the end. Photos are sorted by date unless you pass `--shuffle`. Rendering 5 photos took about 8 s.
- **Album.** Creates `index.html` plus a `media/` folder, with no CDN and no build step.
  - **Layout.** Dark theme, an "On this day" strip, and photos grouped by year then month, with year jump links.
  - **Lightbox.** Navigate with ← and →, Esc, Home and End, or swipe. Videos play inline.
  - **Media.** Thumbnails are lazy-loaded. Photos are resized to a 2560 px long edge. MOVs are converted to 1280 px MP4s with poster frames.
  - **Re-runs.** Only changed files are rebuilt. Serve the folder over HTTP (`python3 -m http.server`) or open the file directly.
- **On this day.** Finds photos and videos from the same month and day in other years, within an optional ±window of days.

### ⚠ Dates

Dates come from EXIF `DateTimeOriginal` when it is valid. Otherwise the file's modification time is used, and the album caption marks those as "(file date)".

**This unit's clock is unset:** frames captured today carry `0000:00:00 00:00:00` in EXIF. For older photos on the card, check a few EXIF dates before trusting the year grouping. Also, if files were copied off the card in a way that didn't preserve modification times, every photo will show the copy date. Copy with `cp -p` or `rsync -t`, or the camera's USB download, if it keeps times.

## Verification done (2026-10-20)

All of this ran on real L120 frames from `captures/*/`. Outputs are in `captures/lab-tests/{looks,video,memories}` (gitignored).

- **Looks.** Rendered the grid on an L120 frame and on a colourful phone photo of the camera body. All 8 looks are visibly distinct. Checked the date stamp at full resolution.
- **Video.** Built a 6 s, 1280x720 MJPEG + PCM MOV from L120 frames, with added shake and some blurred frames. Results:
  - **Stabilise.** Frame-to-frame jitter went from about 12 px RMS to under 0.1 px, and the intended pan was kept.
  - **Speed.** 0.5x gave 12 s, 3x gave 2 s, and 0.25x (muted) gave 23.8 s, all at 30 fps.
  - **Best frame.** It picked a frame that was not blurred.
  - **Other outputs.** The GIF and MP4 conversion were produced and checked.
- **Memories.**
  - **Slideshow.** Checked frames for the pan and zoom, the crossfade, portrait handling and the end fade. The music fades out.
  - **Album.** Opened it in a browser over HTTP and tested the grid, lightbox, keyboard navigation and video playback.
  - **On this day.** Returned the expected matches on a folder with synthetic dates.
- **Not tested.**
  - A real camera MOV. The test clip was synthetic, so the camera's own audio codec and rotation tags were not exercised.
  - The real 315-file card.
  - Music longer than the slideshow (the code loops it with `-stream_loop`).
