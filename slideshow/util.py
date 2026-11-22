"""通用工具：ffmpeg 定位、子进程调用、哈希、媒体探测。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_CANDIDATE_DIRS = [
    Path(r"C:\ffmpeg\bin"),
    PROJECT_ROOT / "ffmpeg" / "bin",
    PROJECT_ROOT / "ffmpeg",
]


class FFmpegMissing(RuntimeError):
    pass


def _locate(name: str) -> str:
    """先查 PATH，再查常见安装位置。"""
    found = shutil.which(name)
    if found:
        return found
    for d in _CANDIDATE_DIRS:
        for candidate in (d / name, d / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    raise FFmpegMissing(
        f"找不到 {name}。请先安装 ffmpeg 并加入 PATH"
        f"（Windows 也可放到 C:\\ffmpeg\\bin\\ 或项目下的 ffmpeg\\ 目录）。"
    )


def ffmpeg() -> str:
    return _locate("ffmpeg")


def ffprobe() -> str:
    return _locate("ffprobe")


def run(cmd: list[str], *, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    """静默执行命令，返回 CompletedProcess（文本模式）。"""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"命令失败 ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}"
        )
    return proc


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def probe_duration(path: Path) -> float:
    proc = run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path)])
    return float(proc.stdout.strip())


def probe_streams(path: Path) -> dict[str, dict]:
    """返回 {'video': {...}, 'audio': {...}, '_format': {...}}，缺失的流不出现。"""
    proc = run([ffprobe(), "-v", "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,bit_rate,sample_rate,channels",
                "-show_entries", "format=duration,size,bit_rate",
                "-of", "json", str(path)])
    raw = json.loads(proc.stdout or "{}")
    out: dict[str, dict] = {}
    for st in raw.get("streams", []):
        kind = st.get("codec_type")
        if kind and kind not in out:
            out[kind] = st
    out["_format"] = raw.get("format", {})
    return out


def audio_stream_md5(path: Path) -> str:
    """对音轨做流级 MD5，用于证明音频未被重编码。"""
    proc = run([ffmpeg(), "-v", "error", "-i", str(path),
                "-map", "0:a:0", "-c", "copy", "-f", "md5", "-"])
    return (proc.stdout + proc.stderr).strip()


def human_mb(n: int | float) -> str:
    return f"{n / (1024 * 1024):.2f} MB"


def fmt_time(seconds: float, with_tenths: bool = True) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    if with_tenths:
        return f"{int(m):02d}:{s:04.1f}"
    return f"{int(m):02d}:{int(s):02d}"


def write_text_no_bom(path: Path, text: str) -> None:
    """写纯文本：UTF-8 无 BOM、LF 换行。

    ffmpeg 的 concat 解析器遇到 BOM 会把首行读成 '\\ufefffile' 而报 unknown keyword。
    """
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def rel_or_abs(target: Path, base: Path) -> str:
    """尽量用相对路径（跨盘符时退回绝对路径），统一用正斜杠。"""
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError:
        return target.resolve().as_posix()
