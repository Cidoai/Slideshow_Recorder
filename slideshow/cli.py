"""命令行入口：record / build / check / recover / devices。"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from . import __version__
from .audio import wav_to_m4a
from .encoder import SizeBudgetExceeded, build, estimate
from .timeline import Segment, Timeline, build_timeline
from .util import ensure_dir, human_mb, sha256_file
from .verify import render_report, verify

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PKG_ROOT = Path(__file__).resolve().parent.parent   # 开发时的项目目录（pip 安装后是 site-packages）


# ------------------------------------------------------------------ helpers

def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def search_roots() -> list[Path]:
    """优先当前目录，其次包所在目录（兼容「双击 bat」和「装成命令后用」两种姿势）。"""
    cwd = Path.cwd()
    return [cwd] if cwd == PKG_ROOT else [cwd, PKG_ROOT]


def default_images_dir() -> Path:
    """没给 --images 时，自动找图片目录。"""
    for root in search_roots():
        for name in ("samples", "slides", "images", "图片"):
            p = root / name
            if p.is_dir() and any(f.suffix.lower() in IMAGE_EXT
                                  for f in p.iterdir() if f.is_file()):
                return p
    raise SystemExit(
        "未指定 --images，且在 samples\\、slides\\、images\\ 里都没找到图片。\n"
        "用法：slideshow record --images <图片目录>"
    )


def collect_images(src: Path) -> list[Path]:
    src = Path(src)
    if src.is_file() and src.suffix.lower() in IMAGE_EXT:
        return [src]
    if not src.is_dir():
        raise SystemExit(f"图片路径不存在：{src}")
    imgs = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]
    if not imgs:
        raise SystemExit(f"目录里没有图片：{src}")
    return sorted(imgs, key=natural_key)


def timestamp_dir(base: Path | None = None) -> Path:
    return (base or Path.cwd() / "output") / time.strftime("%Y-%m-%d_%H%M%S")


# ------------------------------------------------------------------ commands

def cmd_record(args) -> int:
    from .recorder import RecorderApp

    images = collect_images(Path(args.images) if args.images else default_images_dir())
    out_dir = Path(args.out) if args.out else timestamp_dir()
    ensure_dir(out_dir)
    print(f"图片 {len(images)} 张，输出目录 {out_dir}")

    app = RecorderApp(
        images, out_dir,
        audio_file=Path(args.audio) if args.audio else None,
        device=args.device,
        width=args.width, height=args.height, fps=args.fps,
        windowed=args.windowed,
    )
    tl_path = app.run()
    if not tl_path:
        print("已取消，没有生成时间轴。")
        return 1

    print(f"时间轴已保存：{tl_path}")
    if args.no_build:
        return 0
    return _build_and_report(tl_path, out_dir / "final.mp4", args)


def cmd_build(args) -> int:
    tl = Timeline.load(Path(args.timeline))
    out = Path(args.output) if args.output else tl.base_dir / "final.mp4"
    return _build_and_report(Path(args.timeline), out, args, timeline=tl)


def _build_and_report(tl_path: Path, out: Path, args, timeline: Timeline | None = None) -> int:
    tl = timeline or Timeline.load(tl_path)
    try:
        info = build(tl, out, crf=args.crf, height=args.height, fps=args.fps,
                     aspect=args.aspect)
    except NotImplementedError as exc:
        print(f"[未实现] {exc}")
        return 3
    except SizeBudgetExceeded as exc:
        print(f"[失败] {exc}")
        return 4

    results = []
    if not args.no_verify:
        results = verify(tl, out, budget=info["budget"], fps=info["fps"])

    report = render_report(tl, out, results, info)
    print("\n" + report)
    (tl.base_dir / "report.txt").write_text(report + "\n", encoding="utf-8", newline="\n")
    return 0 if all(r["ok"] for r in results or [{"ok": True}]) else 5


def cmd_check(args) -> int:
    tl = Timeline.load(Path(args.timeline))
    e = estimate(tl)
    print("=" * 52)
    print("体积预估（未编码）")
    print("=" * 52)
    print(f"图片       {e['images']} 张   {human_mb(e['images_bytes'])}")
    print(f"音频       {e['audio_duration']:.3f}s   {human_mb(e['audio_bytes'])}")
    print(f"输入合计   {human_mb(e['input_total'])}")
    print(f"上限 2x    {human_mb(e['budget'])}")
    print(f"预估成品   {human_mb(e['predicted'])}   ({e['predicted_ratio']:.3f} x)")
    print(f"余量       {human_mb(e['headroom'])}")
    print(f"分段       {e['segments']}")
    print()
    print("预计可以通过" if e["predicted"] <= e["budget"] else "预计会超限，需要降画质或缩短音频")
    return 0


def cmd_recover(args) -> int:
    folder = Path(args.dir)
    partial = folder / "timeline.partial.jsonl"
    if not partial.exists():
        print(f"没有找到 {partial}")
        return 1
    rows = [json.loads(l) for l in partial.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("partial 文件为空")
        return 1

    wav = folder / "audio.wav"
    m4a = folder / "audio.m4a"
    if m4a.exists():
        audio = m4a
    elif wav.exists():
        print("正在把 audio.wav 转成 m4a…")
        audio = wav_to_m4a(wav, m4a)
    else:
        print("既没有 audio.m4a 也没有 audio.wav，无法恢复音频")
        return 1

    segs = [Segment(t=float(r["t"]), index=int(r["index"]), image=r["image"]) for r in rows]
    tl = build_timeline(segs, audio, folder, {"width": 1920, "height": 1080,
                                              "fps": args.fps, "aspect": "16:9"})
    cache = {}
    for seg in tl.segments:
        p = tl.image_path(seg)
        if seg.image not in cache:
            cache[seg.image] = sha256_file(p)
        seg.sha256 = cache[seg.image]
    tl.validate()
    path = tl.save(folder / "timeline.json")
    print(f"已恢复 {len(segs)} 段 → {path}")
    if args.build:
        return _build_and_report(path, folder / "final.mp4", args, timeline=tl)
    return 0


def cmd_devices(args) -> int:
    from .audio import list_input_devices
    print("可用的录音设备：")
    for d in list_input_devices():
        print(f"  [{d['index']:>2}] {d['name']}  ({d['channels']} ch, {d['default_sr']:.0f} Hz)")
    print("\n用 --device 序号 指定，例如：--device 15")
    return 0


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slideshow", description="静态图 + 音频 → 小体积视频")
    p.add_argument("--version", action="version", version=f"slideshow {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--crf", type=int, default=20, help="起始 CRF，默认 20（自动降档仍生效）")
        sp.add_argument("--height", type=int, default=None, help="画布高度，默认 1080")
        sp.add_argument("--fps", type=int, default=None, help="帧率，默认 12")
        sp.add_argument("--aspect", default="16:9",
                        help="画幅，默认 16:9（9:16 / 1:1 为预留参数，暂未实现）")
        sp.add_argument("--no-verify", action="store_true", help="跳过合成后校验")

    r = sub.add_parser("record", help="录制并合成")
    r.add_argument("--images", default=None,
                   help="图片目录或单张图片，默认自动查找 samples\\ 或 slides\\")
    r.add_argument("--out", default=None, help="输出目录，默认 output\\<时间戳>")
    r.add_argument("--audio", default=None, help="复用已有音频，跳过录音")
    r.add_argument("--device", type=int, default=None, help="麦克风设备序号（见 devices）")
    r.add_argument("--width", type=int, default=1920)
    r.add_argument("--windowed", action="store_true", help="窗口模式（调试用）")
    r.add_argument("--no-build", action="store_true", help="只录制，不合成")
    add_common(r)
    r.set_defaults(func=cmd_record)

    b = sub.add_parser("build", help="从时间轴合成")
    b.add_argument("timeline", help="timeline.json 路径")
    b.add_argument("-o", "--output", default=None)
    add_common(b)
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="只算体积，不编码")
    c.add_argument("timeline")
    c.set_defaults(func=cmd_check)

    v = sub.add_parser("recover", help="从崩溃残留恢复")
    v.add_argument("dir")
    v.add_argument("--build", action="store_true", help="恢复后立即合成")
    v.add_argument("--fps", type=int, default=12)
    v.add_argument("--height", type=int, default=None)
    v.add_argument("--crf", type=int, default=20)
    v.add_argument("--aspect", default="16:9")
    v.add_argument("--no-verify", action="store_true")
    v.set_defaults(func=cmd_recover)

    d = sub.add_parser("devices", help="列出录音设备")
    d.set_defaults(func=cmd_devices)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] {exc}")
        return 1
