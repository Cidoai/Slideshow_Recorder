# 图文录制器

[English](README.md) | **简体中文**

把「一段讲解录音 + 若干张静态图」压成一个小体积 MP4。

录制时全屏显示当前图片，你一边讲一边用方向键、`Home`/`End` 或页码跳转切图。
程序记录**每一刻屏幕上显示的是哪张图**，最后把图片、音频和这份时间表合成视频。

成品体积保证不超过 **2 ×（图片总字节 + 音频字节）**。音频尽量直接复制、不重编码；
只在切图处插入关键帧，静止画面几乎不占码率。

---

## 环境要求

| 组件 | 要求 |
|---|---|
| Python | 3.10 或更高 |
| ffmpeg | `ffmpeg` 和 `ffprobe` 能在 `PATH` 中找到 |
| Tkinter | 录制窗口需要（大多数 Python 安装包自带） |
| sounddevice / PortAudio | 麦克风录制需要 |
| 硬件 | 录制需要麦克风和图形桌面 |

Linux 上通常需要安装：

```bash
sudo apt install python3-tk libportaudio2 ffmpeg
```

macOS 上安装 ffmpeg：

```bash
brew install ffmpeg
```

Windows 上可以执行 `winget install Gyan.FFmpeg`，也可以把 `ffmpeg.exe`、
`ffprobe.exe` 放到 `C:\ffmpeg\bin\`，或放到项目下的 `ffmpeg\` 目录。

---

## 安装

在项目根目录执行：

```bash
python -m pip install .
```

如果不想影响系统 Python 环境，可以用 pipx：

```bash
pipx install .
```

开发模式（改代码立即生效）：

```bash
python -m pip install -e .
```

也可以完全不安装，直接在项目根目录运行：

```bash
python -m slideshow --help
```

安装完成后，可以在任意目录使用 `slideshow` 命令：

```bash
slideshow --version
```

---

## 快速开始

1. 把图片放进一个文件夹，程序按文件名自然排序（`s01.png`、`s02.png`……
   `s10.png`）。支持 PNG、JPEG、WebP、BMP、TIFF。
2. 开始录制：

```bash
slideshow record --images 图片目录
```

3. 在全屏窗口中操作：
   - 按 `空格` 开始（3 秒倒计时）。
   - 再按 `空格` 暂停，再按一次继续。暂停期间不收音，这段时间不会进入成品。
   - `←` / `→` 上一张 / 下一张，`Home` / `End` 第一张 / 最后一张。
   - 输入页码后按 `Enter` 直接跳页。
   - 按 `Esc` 结束，程序自动合成视频。
4. 成品位置：

```
output/<时间戳>/final.mp4
```

如果不传 `--images`，程序会自动查找当前目录和程序目录下的 `samples/`、
`slides/`、`images/`。

---

## 不重录，直接重新合成

时间轴是普通文件，可以反复使用。也可以复用已有录音，不打开麦克风：

```bash
# 从已有时间轴重新合成
slideshow build output/2026-11-20_1200/timeline.json -o 新版.mp4

# 只估算体积，不编码
slideshow check output/2026-11-20_1200/timeline.json

# 复用已有音频，跳过录音
slideshow record --images 图片目录 --audio 讲解.m4a
```

使用 `--audio` 时不能暂停，因为音频长度已经固定。

---

## 命令说明

### `record` — 录制并合成

```bash
slideshow record --images 图片目录
```

| 参数 | 说明 |
|---|---|
| `--images PATH` | 图片目录或单张图片；省略时自动查找 `samples/`、`slides/`、`images/` |
| `--out PATH` | 输出目录，默认 `output/<时间戳>/` |
| `--audio PATH` | 复用已有音频，不录音 |
| `--device N` | 麦克风序号，用 `slideshow devices` 查看 |
| `--width N` | 画布宽度，默认 `1920` |
| `--height N` | 画布高度，默认 `1080` |
| `--fps N` | 帧率，默认 `12` |
| `--windowed` | 窗口模式，调试用 |
| `--no-build` | 只录制，不合成 |
| `--crf N` | 起始 CRF，默认 `20` |
| `--aspect R` | 画幅，目前只支持 `16:9` |
| `--no-verify` | 跳过合成后校验 |

### `build` — 从时间轴合成

```bash
slideshow build TIMELINE.json [-o 成品.mp4]
```

同样支持 `--crf`、`--height`、`--fps`、`--aspect`、`--no-verify`。

### `check` — 只算体积

```bash
slideshow check TIMELINE.json
```

不编码，直接打印图片/音频总量、2 倍上限和预估成品大小。

### `devices` — 列出录音设备

```bash
slideshow devices
```

把输出中的序号填给 `--device`。

### `recover` — 崩溃恢复

```bash
slideshow recover output/2026-11-20_1200/
slideshow recover output/2026-11-20_1200/ --build
```

录制过程中每次切图都会写 `timeline.partial.jsonl`，录音则保存在 `audio.wav`。
如果程序中途被杀掉，这条命令会把两者接起来，生成 `timeline.json`，并可选择
立即合成。

---

## 键盘操作

| 按键 | 未开始 | 录制中 |
|---|---|---|
| `空格` | 开始（3 秒倒计时） | 暂停 / 继续 |
| `Esc` | 退出 | 结束并合成（或取消页码输入） |
| `←` / `→` | — | 上一张 / 下一张 |
| `Home` / `End` | — | 第一张 / 最后一张 |
| `0`–`9` | — | 开始输入页码 |
| `Backspace` | — | 删除一位 |
| `Enter` | — | 跳到输入的页码 |

页码输入 3 秒无操作会自动退出输入状态。

---

## 输出目录

```
output/<时间戳>/
  audio.m4a              录制的音频（或复用的音频）
  timeline.json          权威时间轴（时刻、图片、SHA-256）
  timeline.txt           ffmpeg 可读的 concat 清单
  timeline.partial.jsonl 崩溃恢复日志（成功后删除）
  final.mp4              成品视频
  report.txt             合成与校验报告
```

临时文件 `audio.wav` 在转成 `audio.m4a` 后会自动删除。

---

## 工作原理

- 每次切图记录的是**状态**：`时刻 + 图片 + SHA-256`，不是「按了哪个键」。
  因此时间轴是绝对的、可校验的。
- 源音频是 AAC/MP3 时直接复制音轨，成品音轨与录音逐字节相同；其他格式转成 AAC。
- 每个切点强制一个关键帧，切点之间都是廉价的 P 帧，所以视频体积接近图片总量。
- 超过体积上限时，程序会自动提高 CRF、降低分辨率重试，直到满足上限。
- 合成后检查六项：体积、时长、音频完整性、逐段画面、切点精度、可解码性。

---

## 平台支持

| 平台 | `record`（图形界面 + 麦克风） | `build` / `check` / `recover` | 说明 |
|---|---|---|---|
| **Windows 10/11** | ✅ 已实测 | ✅ 已实测 | `record.bat`、`slideshow.cmd` 是 Windows 专用启动器 |
| **macOS** | ✅ 支持 | ✅ 支持 | 用 `slideshow` 或 `python -m slideshow`；ffmpeg 可用 Homebrew 安装；python.org 安装包自带 Tkinter |
| **Linux** | ✅ 支持 | ✅ 支持 | 需要 `python3-tk`、`libportaudio2`、ffmpeg，以及图形桌面（X11/Wayland） |
| **其他 Unix** | ⚠️ 理论可用 | ✅ 理论可用 | 需要 Python 3.10+、Tkinter、PortAudio、ffmpeg |
| **无图形界面的服务器 / 无 GUI 的 WSL** | ❌ 没有显示器和麦克风 | ✅ 可以 | 合成、估算、恢复不需要图形界面 |

Python 代码和 FFmpeg 管线本身是跨平台的。只有两个 Windows 批处理启动器
（`record.bat`、`slideshow.cmd`）仅限 Windows；macOS/Linux 上请使用
`slideshow` 命令或 `python -m slideshow`。

录制界面文字是中文。系统没有中文字体时，请安装 CJK 字体（例如 Linux 上的
`fonts-noto-cjk`），否则标签可能显示为方框。

> 说明：Windows 上的录制、合成和校验已实际跑通；macOS/Linux 由 Tkinter、
> PortAudio、FFmpeg 的跨平台能力支持，但作者未在每个桌面环境逐一实测。
> 如果遇到问题，优先检查 Tkinter、PortAudio 和音频设备。

---

## 已知限制

- 只支持 16:9。`--aspect 9:16` 和 `--aspect 1:1` 是预留参数，会返回退出码 3。
- 图片之间没有转场。转场会让静止段变成真实运动，体积会大幅上升。
- 默认 12 fps。极短的中间段可能被吸附到一帧。
- 一条视频只有一条音轨，不支持多音轨、多语言。
- 时间轴里的图片路径相对于 `timeline.json`。移动素材后需要重新生成时间轴；
  SHA-256 校验会阻止静默错配。
- 如果最低画质仍然超过 2 倍上限，合成会报错。此时需要缩短音频或减小图片。

---

## 常见问题

| 问题 | 处理 |
|---|---|
| 找不到 `ffmpeg` | 安装 ffmpeg 并加入 `PATH`；Windows 也可放到 `C:\ffmpeg\bin\` 或项目下的 `ffmpeg\` |
| 没有麦克风 / 打不开设备 | 运行 `slideshow devices`，再用 `--device N` 指定 |
| Linux 报 `No module named tkinter` | `sudo apt install python3-tk` |
| Linux 报 PortAudio 错误 | `sudo apt install libportaudio2` |
| 成品超过体积上限 | 缩短讲解或减小图片；程序已经会自动降画质 |
| 中文标签显示成方框 | 安装系统中文字体 |

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
