"""时间轴数据模型：状态式记录、JSON 读写、concat 清单生成。"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .util import ensure_dir, probe_duration, rel_or_abs, sha256_file

DEFAULT_CANVAS = {"width": 1920, "height": 1080, "fps": 12, "aspect": "16:9"}


@dataclass
class Segment:
    """一条状态记录：从 t 秒起，屏幕上是第 index 张图。"""

    t: float
    index: int
    image: str          # 相对 timeline.json 的路径（正斜杠）
    sha256: str = ""

    def as_dict(self) -> dict:
        return {"t": round(self.t, 3), "index": self.index,
                "image": self.image, "sha256": self.sha256}


@dataclass
class Timeline:
    segments: list[Segment]
    audio: dict
    canvas: dict = field(default_factory=lambda: dict(DEFAULT_CANVAS))
    created: str = ""
    totals: dict = field(default_factory=dict)
    version: int = 1

    # ---- 派生数据 ----

    def starts(self) -> list[float]:
        return [s.t for s in self.segments]

    def durations(self) -> list[float]:
        """每段时长 = 下一条记录的时刻 - 本条；最后一段延伸到音频结束。"""
        total = float(self.audio.get("duration") or 0.0)
        out: list[float] = []
        for i, seg in enumerate(self.segments):
            end = self.segments[i + 1].t if i + 1 < len(self.segments) else max(total, seg.t)
            out.append(max(0.0, end - seg.t))
        return out

    def images_bytes(self) -> int:
        base = self.base_dir
        total = 0
        for seg in self.segments:
            p = base / seg.image
            if p.exists():
                total += p.stat().st_size
        return total

    def budget(self) -> int:
        """体积上限 = 2 ×（去重后的图片字节 + 音频字节）。"""
        seen: dict[str, int] = {}
        base = self.base_dir
        for seg in self.segments:
            if seg.image in seen:
                continue
            p = base / seg.image
            if p.exists():
                seen[seg.image] = p.stat().st_size
        return 2 * (sum(seen.values()) + int(self.audio.get("bytes") or 0))

    # ---- 路径 ----

    @property
    def base_dir(self) -> Path:
        return getattr(self, "_base_dir", Path.cwd())

    def image_path(self, seg: Segment) -> Path:
        p = Path(seg.image)
        return p if p.is_absolute() else (self.base_dir / p)

    # ---- 序列化 ----

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "created": self.created or _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "canvas": self.canvas,
            "audio": self.audio,
            "segments": [s.as_dict() for s in self.segments],
            "totals": {
                "images_bytes": self.images_bytes(),
                "segment_count": len(self.segments),
            },
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        ensure_dir(path.parent)
        path.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "Timeline":
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        segs = [Segment(**{k: v for k, v in s.items() if k in {"t", "index", "image", "sha256"}})
                for s in raw.get("segments", [])]
        tl = cls(
            segments=segs,
            audio=raw.get("audio", {}),
            canvas=raw.get("canvas", dict(DEFAULT_CANVAS)),
            created=raw.get("created", ""),
            totals=raw.get("totals", {}),
            version=raw.get("version", 1),
        )
        tl._base_dir = path.parent.resolve()
        tl.validate()
        return tl

    # ---- 校验 ----

    def validate(self) -> None:
        if not self.segments:
            raise ValueError("时间轴为空：没有任何图片记录")
        ts = [s.t for s in self.segments]
        if ts != sorted(ts):
            raise ValueError("时间戳不是递增的")
        if ts[0] > 0.5:
            raise ValueError(f"第一时间戳 {ts[0]:.3f}s 偏离 0 太远")
        missing = [s.image for s in self.segments if not self.image_path(s).exists()]
        if missing:
            raise FileNotFoundError("以下图片不存在：\n  " + "\n  ".join(sorted(set(missing))))
        # 素材是否被替换
        changed = []
        for s in self.segments:
            p = self.image_path(s)
            if s.sha256 and sha256_file(p) != s.sha256:
                changed.append(s.image)
        if changed:
            raise ValueError("以下图片内容已变化（sha256 不匹配）：\n  "
                             + "\n  ".join(sorted(set(changed))))

    # ---- concat ----

    def concat_text(self) -> str:
        """ffmpeg concat 清单。必须无 BOM、LF 换行。"""
        durs = self.durations()
        lines: list[str] = []
        for seg, d in zip(self.segments, durs):
            lines.append(f"file '{self.image_path(seg).as_posix()}'")
            lines.append(f"duration {d:.3f}")
        # ffmpeg 的怪癖：最后一张必须再写一次，且不带 duration
        lines.append(f"file '{self.image_path(self.segments[-1]).as_posix()}'")
        return "\n".join(lines) + "\n"

    def keyframe_times(self) -> list[float]:
        return [seg.t for seg in self.segments]

    def summary_rows(self) -> list[dict]:
        """给校对界面/报告用的逐段信息。"""
        durs = self.durations()
        rows = []
        for i, (seg, d) in enumerate(zip(self.segments, durs)):
            rows.append({
                "no": i + 1,
                "index": seg.index,
                "image": Path(seg.image).name,
                "start": seg.t,
                "end": seg.t + d,
                "duration": d,
            })
        return rows


def build_timeline(segments: list[Segment], audio_path: Path, timeline_dir: Path,
                   canvas: dict | None = None) -> Timeline:
    """把录制产生的 (t, index) 记录补全成完整时间轴。"""
    audio_path = Path(audio_path)
    from .util import probe_streams

    info = probe_streams(audio_path)
    a = info.get("audio", {})

    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    audio = {
        "file": rel_or_abs(audio_path, timeline_dir),
        "codec": a.get("codec_name", ""),
        "bitrate": _int(a.get("bit_rate")),
        "sample_rate": _int(a.get("sample_rate")),
        "channels": _int(a.get("channels")),
        "duration": probe_duration(audio_path),
        "bytes": audio_path.stat().st_size,
    }
    tl = Timeline(segments=segments, audio=audio,
                  canvas=dict(canvas or DEFAULT_CANVAS),
                  created=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    tl._base_dir = Path(timeline_dir).resolve()
    return tl
