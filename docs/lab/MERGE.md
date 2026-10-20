# Multi-frame merges (`l120/lab/merge.py`)

The L120 can only fire and return JPEGs (~4-5 s/shot, auto exposure, 4 s max shutter). These functions combine several of those JPEGs on the Mac. Nothing here touches the USB camera.

```
python -m l120.lab.merge night     frames/*.jpg            -o night.jpg
python -m l120.lab.merge stack     frames/*.jpg --mode lighten|mean|median|darken [--no-align] -o out.jpg
python -m l120.lab.merge fuse      ev-2.jpg ev0.jpg ev+2.jpg -o hdr.jpg
python -m l120.lab.merge pano      left.jpg mid.jpg right.jpg -o pano.jpg [--mode scans]
python -m l120.lab.merge sr        frames/*.jpg --scale 2 [--roi X Y W H] -o big.jpg
python -m l120.lab.merge deflicker timelapse_folder/ --fps 24 -o tl.mp4
```
Each command prints a JSON summary. The same functions can be imported from Python, and each returns a dict.

## How the alignment works (shared by night, stack and sr)
1. A grayscale proxy of each frame is made, 1200 px on the long side.
2. ORB features (after CLAHE, which helps on the dark L120 frames) are matched and a RANSAC homography is fitted. ECC then refines it to sub-pixel accuracy. If ORB finds too few features, ECC starts from identity instead, which only handles small motion.
3. The full-resolution frame is warped with Lanczos interpolation and written to a uint8 memory-mapped temp file. Merging then runs in 256-row strips, so peak RAM stays at about 200-400 MB whatever the frame count.
4. Frame rejection uses two checks: the motion must be plausible (scale 0.85-1.18, small perspective, shift under 25 % of the frame), and the **detail NCC** must be at least 0.5. Detail NCC is the correlation of band-pass images. Raw-intensity NCC was useless for this: a frame of a different scene still scored 0.80-0.91 because it had the same dark-top, bright-bottom layout. Its detail NCC was 0.003, against 0.95-0.99 for good frames.
5. Each aligned frame's brightness is matched to the reference with a per-channel gain in linear light. This is needed because the L120 re-meters every shot (the gains measured on real frames were up to 1.036).
6. The night-mode reference is the sharpest frame among those that look like the rest of the burst. Picking by sharpness alone chose an accidental wrong-scene frame in testing.

## The functions

| Function | What it does | When to use it |
|---|---|---|
| `night_mode` | Aligns, rejects bad frames, takes a sigma-clipped mean around the per-pixel median (sigma = max(MAD, global noise floor), so it works with only 3 frames), then applies a mild unsharp mask | Dim scenes shot handheld. Shoot 5-10 frames. |
| `aligned_stack` | Aligns to the first frame, then merges with lighten, mean, median or darken | Light or star trails, smooth water, or removing people, when the camera isn't on a tripod. `--no-align` gives the old `features.stack` behaviour. |
| `exposure_fusion` | AlignMTB translation alignment, crop to the common area, then Mertens fusion | Bracket by changing EV on the multi-selector between shots (-2 / 0 / +2). Hold the camera still, because only shifts are corrected. |
| `panorama` | `cv2.Stitcher` in PANORAMA mode (SCANS as fallback), then a greedy largest-rectangle crop of black borders | Rotate the camera about the lens with 30-40 % overlap. Use `--mode scans` for flat subjects. |
| `super_resolution` | Aligns (plus a second ECC pass at 2400 px), warps each frame straight onto a 2x grid, averages, then sharpens. Reports results against a single Lanczos upscale with the same sharpening. | Handheld bursts of 6 or more frames (you need sub-pixel jitter). Use `--roi` to keep it fast. |
| `deflicker_timelapse` | Measures per-frame linear luminance, sets a target with a rolling median plus a running mean, applies a gain per frame with a lookup table, then encodes H.264 (yuv420p, crf 18, 1920 wide) | Any L120 timelapse, since it auto-exposes every frame |

## Measured results (2026-10-20, M-series Mac)
Real inputs: `captures/20261020-182216-stack-lighten` (3 tripod-ish frames of a charger on a desk), `…180708-stack-lighten`, and `…180733-timelapse` (3 frames). **Synthetic inputs** are generated from those real frames and stored in `captures/lab-tests/synthetic/`:
- **handheld:** 6 frames with ±1.2° rotation, ±60 px shift, ±1.5 % scale and added σ=4 noise, plus one frame from a different scene
- **bracket:** EV -2/0/+2 made by scaling one JPEG in linear light, with 5-7 px shifts
- **pano:** three 50 %-wide crops, slightly rotated
- **sr:** eight half-resolution, sub-pixel-shifted copies of a 1600×1200 crop, plus noise
- **flicker:** 40 frames with random ±20 % gain on top of a slow ramp

Outputs are in `captures/lab-tests/` (gitignored), and every output was checked visually.

| Test | Result | Time / peak RAM |
|---|---|---|
| night, real 3 frames | All 3 accepted (detail NCC 0.97-0.98, shift under 0.2 px). Flat-area noise went from 0.955 to 0.661 DN (−31 %; the ideal for 3 frames is −42 %). No ghosting. | 3.9 s / 253 MB |
| night, synthetic handheld 7 frames | 6 accepted. The wrong-scene frame was rejected (detail NCC 0.003). Noise went from 2.95 to 1.28 DN (−57 %; the ideal for 6 frames is −59 %), or 1.60 after sharpening. Clean and ghost-free. | 10.2 s / 400 MB |
| stack lighten, synthetic handheld | Detail NCC against the reference was 0.990 aligned versus **0.105 unaligned**. The unaligned result is visibly smeared six times over; the aligned one is clean. | 2.8 s / 186 MB |
| stack, real frames | All accepted. Detail NCC 0.990 (lighten), 0.994 (median). | 1.4-2.4 s |
| fusion, synthetic bracket | MTB recovered the applied shifts exactly. The output looks natural with slightly lifted shadows. Clipped pixels went *up* (0.02 % to 1.14 %) because the Mertens output overshoots 1.0. Synthetic brackets from one JPEG contain no extra highlight detail, so this test checks the plumbing, not real HDR gain. | 1.3 s / **2.0 GB** |
| pano, synthetic crops | Stitched on the first try with no visible seams; the crop removed 60 px of border. This is an easy case (planar crops, large overlap). Not yet tested on real rotated shots. | 0.4 s / 283 MB |
| sr, synthetic (8 frames, ground truth known) | PSNR 38.64 dB versus 37.26 for a single upscale (+1.4 dB). With sharpening both ways: 37.12 versus 35.12. Visually it is mostly **cleaner, not more detailed**: edges are about the same and the Laplacian variance is lower. Iterative back-projection was tried and lowered PSNR, so it was dropped. | 2.3 s / 129 MB |
| sr, real full frame 3 frames | 8640×6480 output, noise 0.72 versus 0.98 DN for a single upscale. The tripod frames have little sub-pixel diversity, so there is no real resolution gain. | 5.7 s / **2.2 GB** |
| deflicker, synthetic 40 frames | Frame-to-frame flicker went from 4.2 % to 0.87 % (the remainder is partly real scene change between the 3 source frames). The slow ramp is preserved. | 2.7 s / 56 MB |
| deflicker, real 3 frames | Flicker was already 0.03 % and stayed 0.03 %. Valid H.264 output at 1920×1440. | 0.7 s |

## Honest caveats
- The L120's JPEG engine already smooths noise heavily, so night-mode gains on real frames are below the √N ideal. The gains are largest on dark, high-ISO shots.
- Rolling-shutter or parallax motion isn't modelled; alignment is one homography per frame. Close subjects shot handheld can ghost. The clipping step drops some of it, but not all.
- `exposure_fusion` and full-frame `super_resolution` load all frames or a 56 MP accumulator, so they need about 2 GB of RAM.
- Panorama, fusion and SR have only been tested on synthetic inputs so far. The next step is to shoot real rotated panoramas, real EV brackets and handheld bursts.
