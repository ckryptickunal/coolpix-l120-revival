# `l120.lab.analyze`: shot reports, culling, date repair, lens stats

All of this runs on JPEGs that are already on the Mac. None of it touches the camera.
Dependencies: numpy, pillow, opencv-python-headless and piexif (all listed in `requirements.txt`).

```bash
PY=~/Desktop/l120/.venv/bin/python
$PY -m l120.lab.analyze report    PHOTO.JPG [...]       --out DIR
$PY -m l120.lab.analyze cull      FOLDER_OR_FILES [...] --out DIR [--burst-gap 2] [--dup-threshold 6]
$PY -m l120.lab.analyze dates     FOLDER_OR_FILES [...] --out DIR            # writes DIR/date_plan.json
$PY -m l120.lab.analyze fix-dates FILES... --out COPIES --reference F --true "2019-05-04 14:30:00" [--apply]
$PY -m l120.lab.analyze fix-dates FILES... --out COPIES --offset +3045d14h30m [--apply]
$PY -m l120.lab.analyze fix-dates --plan DIR/date_plan.json --out COPIES [--apply]
$PY -m l120.lab.analyze stats     FOLDER_OR_FILES [...] --out DIR
```

When you pass a folder, it is scanned recursively for `.jpg` and `.jpeg` files, and the `--out` folder is skipped. Point the tools at the real photo folders, not at `captures/` as a whole, because `captures/lab-tests/` holds generated images that would otherwise be analysed too.

## 1. `shot_report(path, out_dir)`: CHDK-style feedback after each shot

For each photo it writes three files:

- `<stem>_hist.png` is a 512×200 histogram. The R, G and B channels are additive fills, so where all three overlap the fill is grey, and luma is drawn as a white line. A red bar on the left edge means more than 1% of the shadows are crushed. A red bar on the right edge means more than 0.5% of the highlights are blown.
- `<stem>_zebra.jpg` is a 1440 px preview. Areas where any channel is at 254 or above get red and black diagonal stripes. Crushed shadows (luma ≤ 5) get blue stripes.
- `<stem>_report.json` holds the numbers below, plus a one-line `summary`.

| Field | Meaning |
|---|---|
| `exposure.highlights_clipped_pct` | Percentage of pixels with luma ≥ 250 |
| `exposure.any_channel_clipped_pct` | Percentage of pixels where any channel is ≥ 254 |
| `exposure.shadows_clipped_pct` | Percentage of pixels with luma ≤ 5 |
| `exposure.verdict` | One of `ok`, `ok (some highlights blown)`, `high contrast (highlights blown)`, `overexposed` or `underexposed` |
| `sharpness.global` / `.subject` | Variance of the Laplacian, measured at a fixed 2160 px long edge (half the L120's 4320 px width) so values compare across image sizes. `subject` is the 90th percentile of a 6×8 tile grid, so it stays high when only the subject is in focus. Below 60 is labelled blurry, below 150 soft. |
| `noise_sigma` | Standard deviation of the fine-detail residual (the image minus a σ=2 Gaussian blur), averaged over the flattest 10% of 32×32 tiles. Clipped tiles are skipped. This is **relative**: in-camera noise reduction and JPEG compression push it down, so use it to compare shots, not as a sensor figure. |
| `exif` | Shutter, f-number, ISO, focal length, 35 mm equivalent (the EXIF value, or focal length × 5.56 if missing), zoom ×, flash (decoded), scene type, exposure program, metering, white balance and clock state |

## 2. `cull(folder_or_paths, out_dir)`: rank and de-duplicate

- Every photo is decoded at half size using JPEG DCT scaling, then scored for sharpness and exposure and given a 64-bit perceptual hash (DCT pHash).
- **Bursts** are consecutive shots no more than `--burst-gap` seconds apart, when the camera clock is valid. When it is not, consecutive shots in file order with a pHash distance of 12 or less count as a burst.
- **Near-duplicates** are any two photos with a pHash distance of 6 or less (`--dup-threshold`). Bursts and duplicates are merged into groups, and the merging is transitive.
- Score = 10·log10(subject sharpness), minus penalties for bad exposure and for blown highlights. The best photo in each group is the keeper.
- Each photo gets one decision:
  - `keep`: it is the best of its group, or it is alone in its group, and it has no serious flag.
  - `review`: it is blurry, overexposed or underexposed.
  - `dupe`: it is in a group and is not the best photo of that group.
- Output: `cull.json` and `contact_sheet.html`. The contact sheet is a single self-contained file with base64 thumbnails, badges, and a dark mode.

## 3. Date repair: `guess_date_ranges` and `fix_dates`

**Originals are never modified.** The tool:

1. Reads the original and copies its bytes into memory.
2. Finds the three fixed-width, 20-byte EXIF date fields by walking the TIFF IFDs: `DateTime` (ModifyDate), `DateTimeOriginal`, and `DateTimeDigitized` (CreateDate).
3. Overwrites only those bytes. Nothing is re-encoded, and the MakerNote, embedded thumbnail and image data stay byte-identical.
4. Writes the result to `--out` through a temporary file.

Before the copy is kept, three checks must pass:

- The only bytes that differ from the original are inside the patched date fields.
- The new date reads back correctly.
- The original's SHA-256 is unchanged.

If any check fails, the copy is deleted. The tool also:

- refuses an `--out` folder that contains the originals;
- refuses to overwrite existing copies unless you pass `--overwrite`;
- sets each copy's file modification time to its corrected date;
- writes `fix_dates_manifest_*.json`, recording the old date, new date, reason and SHA-256 of every file.

**Without `--apply` it is a dry run.**

`dates` (`guess_date_ranges`) sorts the photos by DSCN file number, or by file modification time if any photo lacks a number. It starts a new group when any of these happen:

- the clock state changes between `unset` (`0000:00:00`), `reset` (within the first 31 days after 2011-01-01 00:00) and `plausible`;
- the camera clock jumps backwards by more than 60 s;
- the file number goes backwards.

Fill in `true_start` for each group in `date_plan.json`, then run `fix-dates --plan`. Photos in a group keep their relative spacing, offset so the group's first photo lands on `true_start`.

**Photos with an unset clock (`0000:00:00`) have no time to shift.** You can only give them an *estimate*: `true_start` plus index × `sequence_step_s` with `--plan`, or `--sequence-step` combined with `--reference`/`--true`, which spaces photos by DSCN number. Both the console output and the manifest label these as `ESTIMATE`.

## 4. `lens_stats(folder_or_paths, out_dir)`: usage analytics

Reads EXIF only, so it is fast. It writes `lens_stats.json` and `lens_stats.html`. The HTML uses inline SVG bars and no CDN. It covers:

- focal length, in 35 mm-equivalent bins from 25 to 525 mm;
- mean zoom, and the percentage of photos at the widest setting and at 20× zoom or more;
- ISO, shutter speed, flash, EXIF scene type and clock state;
- time of day, counting only photos whose camera clock is `plausible`.

## Test results (2026-10-20, real L120 JPEGs; outputs in `captures/lab-tests/analyze/`)

- **Real photos tested.** There were 14 real frames, from `captures/2026*/` and `probe/remote_1.JPG`. Every one is 25 mm-equivalent, ISO 400, flash off and scene "standard", and **every one has `0000:00:00 00:00:00` dates**: the camera's clock is currently unset, not reset to 2011. The stats report is therefore technically correct but flat.
- **Shot report.** Sharpness ranges from 150 to 195 for tripod-like daylight frames and from 65 to 95 for 1/4 s night frames, which are labelled "soft". The zebra overlay correctly marked the blown white tray in `DSCN0300`. Noise is about 1.15 for single frames and 0.86 for the 3-frame mean stack, the expected direction of change.
- **Synthetic blur.** A Gaussian blur of σ=1 on `remote_1` dropped subject sharpness from 194 to 22, and it was flagged as blurry. **The blur thresholds are calibrated on only about 10 distinct real scenes.** Retune `BLUR_THRESHOLD` and `SOFT_THRESHOLD` once a real collection is available.
- **Cull.** The 14 frames formed 4 groups: the day stack and timelapse frames, the night stack frames, and two singletons. A mean-stacked image loses "best of group" to a single frame because averaging lowers the Laplacian variance. That loss is expected, but the result is not necessarily what you want.
- **Date repair.** The real files have no reset dates, so **the clock-reset scenario was tested on synthetic copies**: real L120 files whose date bytes were patched to 2011-01-01 runs, an unset run and a plausible run (`captures/lab-tests/analyze/dates_src`). The four groups were detected correctly. The reference, offset, sequence and plan modes all wrote correct copies. Pixels, MakerNote and thumbnail were verified identical, Pillow reads back the new dates, and the SHA-256 of every source was unchanged after all runs.
- **Not verified.** I did not check how Apple Photos, Lightroom or exiftool interpret the patched copies.
