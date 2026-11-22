# Slideshow Recorder

**English** | [简体中文](<README(ch).md>)

Turn one narration recording plus a folder of static images into a small MP4.

The recorder shows one image at a time in full screen. While you speak, you move
between images with the arrow keys, `Home`/`End`, or by typing a page number.
The program records **which image is on screen at every moment**, then combines
the images, the audio, and that timeline into a video.

The output is guaranteed to be at most **2 × (total image bytes + audio bytes)**.
Audio is copied without re-encoding when possible, and only the image changes
become keyframes, so still segments cost almost nothing.

---

## Requirements

| Component | Requirement |
|---|---|
| Python | 3.10 or newer |
| ffmpeg | `ffmpeg` and `ffprobe` available on `PATH` |
| Tkinter | Needed for the recording window (bundled with most Python installers) |
| sounddevice / PortAudio | Needed for microphone recording |
| Hardware | A microphone and a graphical desktop for `record` |

On Linux, install the system packages for Tkinter and PortAudio, for example:

```bash
sudo apt install python3-tk libportaudio2 ffmpeg
```

On macOS, install ffmpeg with Homebrew:

```bash
brew install ffmpeg
```

On Windows, install ffmpeg with `winget install Gyan.FFmpeg`, or put
`ffmpeg.exe` / `ffprobe.exe` in `C:\ffmpeg\bin\` or in a `ffmpeg\` folder next
to the project.

---

## Install

From the repository root:

```bash
python -m pip install .
```

For an isolated command without touching the system environment:

```bash
pipx install .
```

For development (edits take effect immediately):

```bash
python -m pip install -e .
```

You can also run it without installing anything:

```bash
python -m slideshow --help
```

After installation, the command is available anywhere:

```bash
slideshow --version
```

---

## Quick start

1. Put the images in one folder. They are sorted naturally by filename
   (`s01.png`, `s02.png`, … `s10.png`). Supported formats: PNG, JPEG, WebP,
   BMP, TIFF.
2. Start recording:

```bash
slideshow record --images path/to/slides
```

3. In the full-screen window:
   - Press `Space` to start (3-second countdown).
   - Press `Space` again to pause, and again to resume. Paused time is cut out
     of both the audio and the video.
   - Use `←` / `→` for the previous/next image, `Home` / `End` for the first/last.
   - Type a page number and press `Enter` to jump directly.
   - Press `Esc` to finish. The video is built automatically.
4. The result is here:

```
output/<timestamp>/final.mp4
```

If you do not pass `--images`, the program looks for `samples/`, `slides/`, or
`images/` in the current directory and next to the package.

---

## Rebuild without recording again

The timeline is a normal file. You can rebuild the video, change quality, or
reuse an existing narration:

```bash
# Rebuild from an existing timeline
slideshow build output/2026-11-20_1200/timeline.json -o new.mp4

# Estimate the size before encoding
slideshow check output/2026-11-20_1200/timeline.json

# Reuse an existing audio file; no microphone is used
slideshow record --images path/to/slides --audio narration.m4a
```

In `--audio` mode, pausing is disabled because the audio length is fixed.

---

## Commands

### `record` — record and build

```bash
slideshow record --images path/to/slides
```

| Option | Meaning |
|---|---|
| `--images PATH` | Image folder or a single image. Default: auto-detect `samples/`, `slides/`, `images/` |
| `--out PATH` | Output folder. Default: `output/<timestamp>/` |
| `--audio PATH` | Reuse an existing audio file instead of recording |
| `--device N` | Microphone index from `slideshow devices` |
| `--width N` | Canvas width, default `1920` |
| `--height N` | Canvas height, default `1080` |
| `--fps N` | Frame rate, default `12` |
| `--windowed` | Windowed mode instead of full screen (debugging) |
| `--no-build` | Record only; do not encode |
| `--crf N` | Starting CRF, default `20` |
| `--aspect R` | Aspect ratio; currently only `16:9` |
| `--no-verify` | Skip the post-build checks |

### `build` — build from a timeline

```bash
slideshow build TIMELINE.json [-o OUTPUT.mp4]
```

Also accepts `--crf`, `--height`, `--fps`, `--aspect`, and `--no-verify`.

### `check` — size estimate only

```bash
slideshow check TIMELINE.json
```

Prints image/audio totals, the 2× budget, and the predicted output size without
encoding anything.

### `devices` — list microphones

```bash
slideshow devices
```

Use the printed index with `--device`.

### `recover` — recover an interrupted recording

```bash
slideshow recover output/2026-11-20_1200/
slideshow recover output/2026-11-20_1200/ --build
```

A recording writes `timeline.partial.jsonl` after every image change and keeps
`audio.wav` while recording. If the process is killed, this command combines
them, writes `timeline.json`, and optionally builds the video.

---

## Keyboard controls

| Key | Idle | Recording |
|---|---|---|
| `Space` | Start (3-second countdown) | Pause / resume |
| `Esc` | Quit | Finish and build (or cancel page input) |
| `←` / `→` | — | Previous / next image |
| `Home` / `End` | — | First / last image |
| `0`–`9` | — | Start typing a page number |
| `Backspace` | — | Delete one digit |
| `Enter` | — | Jump to the typed page |

Page input times out after 3 seconds without a key press.

---

## Output folder

```
output/<timestamp>/
  audio.m4a              recorded audio (or the reused audio file)
  timeline.json          authoritative timeline (timestamp, image, SHA-256)
  timeline.txt           ffmpeg concat list, human-readable
  timeline.partial.jsonl crash-recovery journal (removed after success)
  final.mp4              finished video
  report.txt             build and verification report
```

The temporary `audio.wav` is deleted after it is converted to `audio.m4a`.

---

## How it works

- Each image change is stored as a **state**: `time + image + SHA-256`, not as a
  key event. The timeline is therefore absolute and verifiable.
- The audio track is stream-copied when the source is AAC/MP3, so it is
  byte-identical to the recording. Other formats are converted to AAC.
- A keyframe is forced at each image change. Everything between changes is a
  cheap P frame, which is why the video stays close to the size of the images.
- If the result is over budget, the encoder automatically retries with higher
  CRF and lower resolution until it fits.
- After building, it checks size, duration, audio integrity, per-segment frame
  matching, cut accuracy, and decodability.

---

## Platform support

| Platform | `record` (GUI + microphone) | `build` / `check` / `recover` | Notes |
|---|---|---|---|
| **Windows 10/11** | ✅ Tested | ✅ Tested | `record.bat` and `slideshow.cmd` are Windows-only launchers |
| **macOS** | ✅ Supported | ✅ Supported | Use `slideshow` or `python -m slideshow`; install ffmpeg via Homebrew; Tkinter is included in python.org builds |
| **Linux** | ✅ Supported | ✅ Supported | Needs `python3-tk`, `libportaudio2`, ffmpeg, and a desktop session (X11/Wayland) |
| **Other Unix** | ⚠️ Should work | ✅ Should work | Requires Python 3.10+, Tkinter, PortAudio, and ffmpeg |
| **Headless server / WSL without GUI** | ❌ No display or microphone | ✅ Yes | Build, check, and recover do not need a GUI or microphone |

The Python code and the FFmpeg pipeline are cross-platform. Only the two
Windows batch launchers (`record.bat`, `slideshow.cmd`) are Windows-specific;
on macOS/Linux use the `slideshow` command or `python -m slideshow`.

The recording UI text is Chinese. On systems without Chinese fonts, install a
CJK font (for example `fonts-noto-cjk` on Linux) so the labels render correctly.

---

## Notes and limitations

- Output is 16:9 only. `--aspect 9:16` and `--aspect 1:1` are reserved and
  return exit code 3.
- There are no transitions between images. Transitions would turn still
  segments into real motion and greatly increase the file size.
- The default frame rate is 12 fps. Extremely short segments may snap to one
  frame.
- One video has one audio track. Multi-track and multi-language output are not
  supported.
- The timeline stores image paths relative to `timeline.json`. If you move the
  assets, rebuild the timeline; the SHA-256 check prevents silent mismatches.
- If even the lowest quality setting exceeds the 2× budget, the build stops
  with an error. Shorten the audio or reduce the image sizes.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ffmpeg` not found | Install ffmpeg and add it to `PATH`. On Windows you can also use `C:\ffmpeg\bin\` or a local `ffmpeg\` folder |
| No microphone / cannot open device | Run `slideshow devices`, then pass `--device N` |
| `No module named tkinter` (Linux) | `sudo apt install python3-tk` |
| PortAudio error (Linux) | `sudo apt install libportaudio2` |
| Output exceeds the budget | Shorten the narration or use smaller images; the encoder already lowers quality automatically |
| Chinese labels show as boxes | Install a CJK font for your system |

---

## License

MIT. See [LICENSE](LICENSE).
