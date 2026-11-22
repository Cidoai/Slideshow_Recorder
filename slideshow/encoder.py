"""合成器：把时间轴编译成 MP4，并强制满足体积上限。

核心是「静止段零成本」：只在每段起点插关键帧，其余靠跳过帧。
因此视频流体积 ≈ 图片总量，与视频时长几乎无关。
"""

from __future__ import annotations

import time
from pathlib import Path

from .timeline import Timeline
from .util import ensure_dir, ffmpeg, probe_duration, run, write_text_no_bom

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}
COPYABLE_AUDIO = {"aac", "mp3", "ac3", "eac3"}

CRF_LADDER = [20, 22, 24, 26, 29, 32]
HEIGHT_LADDER = [1080, 900, 720]


class SizeBudgetExceeded(RuntimeError):
    def __init__(self, best_size: int, budget: int):
        super().__init__(
            f"即使压到最低画质仍然超出上限：最小 {best_size/2**20:.2f} MB > 上限 {budget/2**20:.2f} MB"
        )
        self.best_size = best_size
        self.budget = budget


def canvas_size(height: int, aspect: str = "16:9") -> tuple[int, int]:
    if aspect not in ASPECTS:
        raise ValueError(f"未知画幅 {aspect}，可选：{', '.join(ASPECTS)}")
    if aspect != "16:9":
        # 预留：竖屏/方屏需要重新排版（安全区、页码框位置），暂不实现
        raise NotImplementedError(
            f"画幅 {aspect} 为预留参数，当前只支持 16:9。合成逻辑已支持任意画布，"
            f"缺的是竖屏排版规则。"
        )
    w, h = ASPECTS[aspect]
    width = int(round(height * w / h / 2) * 2)
    return width, int(round(height / 2) * 2)


def build(timeline: Timeline, out_path: Path, *,
          crf: int = 20, height: int | None = None, fps: int | None = None,
          aspect: str = "16:9", log=print) -> dict:
    out_path = Path(out_path)
    ensure_dir(out_path.parent)

    canvas = timeline.canvas or {}
    height = height or int(canvas.get("height", 1080))
    fps = fps or int(canvas.get("fps", 12))
    width, height = canvas_size(height, aspect)

    audio_path = (timeline.base_dir / timeline.audio["file"]).resolve()
    audio_dur = float(timeline.audio.get("duration") or probe_duration(audio_path))
    codec = (timeline.audio.get("codec") or "").lower()

    starts = timeline.keyframe_times()
    durs = timeline.durations()
    if len(starts) != len(durs):
        raise RuntimeError("时间轴内部不一致")

    # 末段对齐到音频长度
    tail = audio_dur - starts[-1]
    if tail <= 1.0 / fps:
        raise ValueError(f"最后一段只有 {tail:.3f}s，太短；请延长最后一张图的停留时间")
    durs[-1] = tail

    if any(d <= 0 for d in durs[:-1]):
        raise ValueError("存在非正时长的分段")

    concat_path = ensure_dir(out_path.parent) / "timeline.txt"
    lines: list[str] = []
    for seg, d in zip(timeline.segments, durs):
        lines.append(f"file '{timeline.image_path(seg).as_posix()}'")
        lines.append(f"duration {d:.3f}")
    lines.append(f"file '{timeline.image_path(timeline.segments[-1]).as_posix()}'")
    write_text_no_bom(concat_path, "\n".join(lines) + "\n")

    kf = ",".join(f"{t:.3f}" for t in starts)

    img_bytes = 0
    seen: set[str] = set()
    for seg in timeline.segments:
        if seg.image in seen:
            continue
        seen.add(seg.image)
        p = timeline.image_path(seg)
        if p.exists():
            img_bytes += p.stat().st_size
    aud_bytes = int(timeline.audio.get("bytes") or audio_path.stat().st_size)
    budget = 2 * (img_bytes + aud_bytes)

    ladder = [(h, c) for h in ([height] + [x for x in HEIGHT_LADDER if x < height])
              for c in CRF_LADDER]
    # 先试用户给的 crf，再沿阶梯上升
    ladder = [(h, c) for (h, c) in ladder if c >= crf]

    best: int | None = None
    used: tuple[int, int] | None = None
    elapsed_total = 0.0
    for h, c in ladder:
        w, hh = canvas_size(h, aspect)
        cmd = _ffmpeg_cmd(concat_path, audio_path, out_path, w, hh, fps, c,
                          kf, codec, timeline.audio)
        t0 = time.perf_counter()
        run(cmd)
        elapsed_total += time.perf_counter() - t0
        size = out_path.stat().st_size
        best = size if best is None else min(best, size)
        used = (h, c)
        if size <= budget:
            break
    else:
        raise SizeBudgetExceeded(best or 0, budget)

    return {
        "output": out_path,
        "size": out_path.stat().st_size,
        "budget": budget,
        "ratio": out_path.stat().st_size / max(1, img_bytes + aud_bytes),
        "crf": used[1] if used else crf,
        "height": used[0] if used else height,
        "fps": fps,
        "canvas": (width, height),
        "images_bytes": img_bytes,
        "audio_bytes": aud_bytes,
        "encode_seconds": elapsed_total,
        "concat": concat_path,
        "audio_duration": audio_dur,
        "audio_copied": codec in COPYABLE_AUDIO,
    }


def _ffmpeg_cmd(concat: Path, audio: Path, out: Path, w: int, h: int, fps: int,
                crf: int, keyframes: str, audio_codec: str, audio_meta: dict) -> list[str]:
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    cmd = [
        ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(audio),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-tune", "stillimage",
        "-g", "100000", "-keyint_min", "100000", "-sc_threshold", "0",
        "-force_key_frames", keyframes,
    ]
    if audio_codec in COPYABLE_AUDIO:
        cmd += ["-c:a", "copy"]
    else:
        bitrate = int(audio_meta.get("bitrate") or 192000)
        cmd += ["-c:a", "aac", "-b:a", str(min(bitrate, 192000))]
    cmd += ["-movflags", "+faststart", "-shortest", str(out)]
    return cmd


def estimate(timeline: Timeline) -> dict:
    """不编码，只按时间轴算体积账（check 子命令用）。"""
    seen: set[str] = set()
    img_bytes = 0
    for seg in timeline.segments:
        if seg.image in seen:
            continue
        seen.add(seg.image)
        p = timeline.image_path(seg)
        if p.exists():
            img_bytes += p.stat().st_size
    aud_bytes = int(timeline.audio.get("bytes") or 0)
    total_in = img_bytes + aud_bytes
    budget = 2 * total_in
    audio_dur = float(timeline.audio.get("duration") or 0.0)
    return {
        "images": len(seen),
        "segments": len(timeline.segments),
        "images_bytes": img_bytes,
        "audio_bytes": aud_bytes,
        "audio_duration": audio_dur,
        "input_total": total_in,
        "budget": budget,
        "predicted": aud_bytes + img_bytes,   # 静止段零成本时的保守估计
        "predicted_ratio": (aud_bytes + img_bytes) / max(1, total_in),
        "headroom": budget - (aud_bytes + img_bytes),
    }
