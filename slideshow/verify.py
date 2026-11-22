"""成品校验：六项断言，全部通过才算成功。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .timeline import Timeline
from .encoder import COPYABLE_AUDIO
from .util import audio_stream_md5, ffmpeg, probe_streams, run

DIFF_TOL = 0.02          # 平均绝对差阈值（0–1）
PROBE_WINDOW = 0.2       # 切点前后探测距离（秒）
THUMB = (320, 180)


def _grab(mp4: Path, t: float, dst: Path) -> Path:
    run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{max(0.0, t):.3f}", "-i", str(mp4), "-frames:v", "1",
         "-vf", f"scale={THUMB[0]}:-1", str(dst)])
    return dst


def _diff(a: Path, b: Path) -> float:
    ia = np.asarray(Image.open(a).convert("RGB").resize(THUMB, Image.LANCZOS), dtype=np.int16)
    ib = np.asarray(Image.open(b).convert("RGB").resize(THUMB, Image.LANCZOS), dtype=np.int16)
    return float(np.abs(ia - ib).mean() / 255.0)


def verify(timeline: Timeline, mp4: Path, *, budget: int | None = None,
           fps: int | None = None, log=print) -> list[dict]:
    mp4 = Path(mp4)
    fps = fps or int((timeline.canvas or {}).get("fps", 12))
    budget = budget or timeline.budget()
    results: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        results.append({"name": name, "ok": ok, "detail": detail})
        log(f"  [{'OK ' if ok else 'BAD'}] {name}: {detail}")

    # 1 体积
    size = mp4.stat().st_size
    add("体积上限", size <= budget,
        f"{size/2**20:.2f} MB ≤ {budget/2**20:.2f} MB (余量 {(budget-size)/2**20:.2f} MB)")

    # 2 时长
    info = probe_streams(mp4)
    fmt = info.get("_format", {})
    vid = info.get("video", {})
    aud = info.get("audio", {})
    dur = float(fmt.get("duration") or 0.0)
    audio_dur = float(timeline.audio.get("duration") or 0.0)
    tol = 1.0 / fps + 0.05
    add("时长对齐", abs(dur - audio_dur) <= tol,
        f"成品 {dur:.3f}s / 音频 {audio_dur:.3f}s (容差 {tol*1000:.0f}ms)")

    # 3 音频未被重编码
    codec = (timeline.audio.get("codec") or "").lower()
    if codec in COPYABLE_AUDIO:
        src = (timeline.base_dir / timeline.audio["file"]).resolve()
        md5a = audio_stream_md5(src)
        md5b = audio_stream_md5(mp4)
        add("音频完整性", md5a == md5b,
            "音轨逐字节相同" if md5a == md5b else f"{md5a} != {md5b}")
    else:
        add("音频完整性", True, f"源为 {codec or '未知'}，已转码为 AAC（直通不适用）")

    # 4 / 5 画面
    tmp = Path(tempfile.mkdtemp(prefix="ssverify_"))
    try:
        rows = timeline.summary_rows()
        bad_mid: list[str] = []
        worst = 0.0
        for i, row in enumerate(rows):
            t = row["start"] + row["duration"] / 2
            f = _grab(mp4, t, tmp / f"m{i}.png")
            d = _diff(f, timeline.image_path(timeline.segments[i]))
            worst = max(worst, d)
            if d > DIFF_TOL:
                bad_mid.append(f"第{row['no']}段 t={t:.1f}s 差 {d:.3f}")
        add("逐段画面匹配", not bad_mid,
            f"{len(rows)}/{len(rows)} 命中，最大差 {worst:.4f}" if not bad_mid
            else "; ".join(bad_mid))

        bad_cut: list[str] = []
        probed = 0
        for i in range(1, len(rows)):
            if rows[i]["duration"] < PROBE_WINDOW * 3 or rows[i - 1]["duration"] < PROBE_WINDOW * 3:
                continue
            cut = rows[i]["start"]
            probed += 1
            before = _grab(mp4, cut - PROBE_WINDOW, tmp / "b.png")
            after = _grab(mp4, cut + PROBE_WINDOW, tmp / "a.png")
            d_prev = _diff(before, timeline.image_path(timeline.segments[i - 1]))
            d_next = _diff(after, timeline.image_path(timeline.segments[i]))
            if d_prev > DIFF_TOL or d_next > DIFF_TOL:
                bad_cut.append(f"切点 {cut:.2f}s")
        add("切点精度", not bad_cut,
            f"{probed}/{probed} 个切点前后各 {PROBE_WINDOW}s 命中正确画面" if not bad_cut
            else "; ".join(bad_cut))

        # 6 可解码
        ok_codec = vid.get("codec_name") == "h264" and aud.get("codec_name") in {"aac", "mp3"}
        add("可解码性", ok_codec,
            f"{vid.get('codec_name')} {vid.get('width')}x{vid.get('height')} "
            f"@ {vid.get('r_frame_rate')} + {aud.get('codec_name')}")
    finally:
        for p in tmp.glob("*.png"):
            p.unlink(missing_ok=True)
        tmp.rmdir()

    return results


def render_report(timeline: Timeline, mp4: Path, results: list[dict],
                  build_info: dict | None = None) -> str:
    lines = ["=" * 52, "图文录制器 — 成品报告", "=" * 52, ""]
    if build_info:
        lines.append(f"图片       {build_info['images_bytes']/2**20:8.2f} MB")
        lines.append(f"音频       {build_info['audio_bytes']/2**20:8.2f} MB")
        lines.append(f"上限       {build_info['budget']/2**20:8.2f} MB")
        lines.append(f"成品       {build_info['size']/2**20:8.2f} MB")
        lines.append(f"倍率       {build_info['ratio']:8.3f} x")
        lines.append(f"编码       crf={build_info['crf']}  "
                     f"{build_info['height']}p  {build_info['fps']}fps  "
                     f"{build_info['encode_seconds']:.1f}s")
        lines.append("")
    passed = sum(1 for r in results if r["ok"])
    lines.append(f"校验       {passed}/{len(results)} 通过")
    for r in results:
        lines.append(f"  [{'OK ' if r['ok'] else 'BAD'}] {r['name']}: {r['detail']}")
    lines.append("")
    lines.append(f"输出       {mp4}")
    return "\n".join(lines)
