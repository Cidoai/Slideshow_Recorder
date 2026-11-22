"""麦克风采集：sounddevice(PortAudio) → WAV → AAC m4a。

t0 的定义：音频第 0 个采样进入麦克风的时刻（用首包回调时刻减去设备延迟估算）。
时间轴的 0 秒与音频第 0 个采样严格对应，因此不存在固定偏移。
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np

from .util import ffmpeg, probe_duration, run


class AudioUnavailable(RuntimeError):
    pass


def _sd():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - 环境相关
        raise AudioUnavailable(
            "未安装 sounddevice，无法录音。请运行：python -m pip install sounddevice"
        ) from exc
    return sd


def list_input_devices() -> list[dict]:
    sd = _sd()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append({"index": i, "name": d["name"], "hostapi": d["hostapi"],
                        "channels": d["max_input_channels"],
                        "default_sr": d["default_samplerate"]})
    return out


def default_input_index() -> int | None:
    sd = _sd()
    try:
        idx, _ = sd.default.device
        return idx if idx is not None and idx >= 0 else None
    except Exception:
        return None


class MicRecorder:
    """边录边攒 chunk；stop() 时一次性写 WAV。"""

    def __init__(self, device: int | None = None, samplerate: int = 48000,
                 channels: int | None = None, blocksize: int = 1024):
        self.device = device
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.channels = channels
        self.t0: float | None = None
        self._segments: list[list[np.ndarray]] = []
        self._stream = None
        self.latency = 0.0
        self.overflow = 0

    # ---- 采集 ----

    def start(self) -> None:
        self._segments = []
        self.t0 = None
        self.latency = 0.0
        self._open_stream()

    def _open_stream(self) -> None:
        sd = _sd()
        dev = self.device if self.device is not None else default_input_index()
        try:
            info = sd.query_devices(dev if dev is not None else sd.default.device[0])
        except Exception as exc:
            raise AudioUnavailable(f"无法查询设备信息（设备 {dev}）：{exc}") from exc

        max_ch = int(info.get("max_input_channels") or 1)
        default_sr = int(round(float(info.get("default_samplerate") or 48000)))
        chans = self.channels or min(2, max_ch)

        # MME/DirectSound 的默认设备常常只认 44100，硬写 48000 会直接开不了
        attempts: list[tuple[int, int]] = []
        for sr in (self.samplerate, 48000, 44100, default_sr):
            for ch in (chans, 1, 2):
                if ch <= max_ch and (sr, ch) not in attempts:
                    attempts.append((sr, ch))

        self.t0 = None
        self._segments.append([])      # 每次打开麦克风就是新的一段
        self._stream = None
        last_err: Exception | None = None
        for sr, ch in attempts:
            try:
                stream = sd.InputStream(
                    device=dev, samplerate=sr, channels=ch,
                    dtype="int16", blocksize=self.blocksize, callback=self._cb,
                )
                stream.start()
                self._stream = stream
                self.samplerate = sr
                self.channels = ch
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        if self._stream is None:
            self._segments.pop()
            raise AudioUnavailable(
                f"无法打开麦克风（设备 {dev}）：{last_err}。"
                f"可以试试 --device 换一个设备，或用 devices 子命令查看列表。"
            )
        try:
            lat = self._stream.latency
            self.latency = float(lat[0] if isinstance(lat, (tuple, list)) else lat)
        except Exception:
            self.latency = 0.0

    def _cb(self, indata, frames, time_info, status) -> None:
        if status:
            self.overflow += 1
        if self.t0 is None:
            # 首包回调：此刻减去设备延迟 ≈ 第 0 个采样真正进入麦克风的时刻
            self.t0 = time.perf_counter() - self.latency
        if self._segments:
            self._segments[-1].append(indata.copy())

    def wait_t0(self, timeout: float = 1.0) -> float:
        deadline = time.perf_counter() + timeout
        while self.t0 is None and time.perf_counter() < deadline:
            time.sleep(0.005)
        if self.t0 is None:
            raise AudioUnavailable("麦克风没有送出任何数据，请检查设备是否被占用")
        return self.t0

    # ---- 暂停 / 继续 ----

    def pause(self) -> None:
        """停止收音，但已录的部分保留。暂停期间的静音不会进入成品。"""
        self._close_stream()

    def resume(self) -> None:
        """重开收音，作为新的一段追加到同一份音频里。"""
        if self.state == "recording":
            return
        self._open_stream()

    @property
    def state(self) -> str:
        return "recording" if self._stream is not None else "paused"

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._stream = None

    def stop(self, wav_path: Path) -> Path:
        self._close_stream()
        if not any(self._segments):
            raise AudioUnavailable("没有录到任何音频")
        data = self._join_with_fades()
        wav_path = Path(wav_path)
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(self.channels or 1)
            w.setsampwidth(2)
            w.setframerate(self.samplerate)
            w.writeframes(data.tobytes())
        self._segments = []
        return wav_path

    def _join_with_fades(self) -> np.ndarray:
        """把各段拼起来，接缝处做 5ms 淡入淡出，避免咔哒声。"""
        n_fade = max(1, int(0.005 * self.samplerate))
        parts: list[np.ndarray] = []
        for si, chunks in enumerate(self._segments):
            if not chunks:
                continue
            seg = np.concatenate(chunks, axis=0).astype(np.int32)
            if len(seg) > 2 * n_fade and si > 0:
                ramp = np.linspace(0.0, 1.0, n_fade, dtype=np.float32)[:, None]
                seg[:n_fade] = (seg[:n_fade] * ramp).astype(np.int32)
            if len(seg) > 2 * n_fade and si < len(self._segments) - 1:
                ramp = np.linspace(1.0, 0.0, n_fade, dtype=np.float32)[:, None]
                seg[-n_fade:] = (seg[-n_fade:] * ramp).astype(np.int32)
            parts.append(seg.astype(np.int16))
        if not parts:
            raise AudioUnavailable("没有录到任何音频")
        return np.concatenate(parts, axis=0)


def wav_to_m4a(wav: Path, m4a: Path, bitrate: int = 192000) -> Path:
    """WAV → AAC m4a。这是「原始录音」，也是体积基准。"""
    m4a = Path(m4a)
    run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(wav), "-c:a", "aac", "-b:a", str(bitrate),
         "-ar", "48000", str(m4a)])
    return m4a


def wav_duration(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def audio_info(path: Path) -> dict:
    return {"file": path.name, "duration": probe_duration(path),
            "bytes": Path(path).stat().st_size}
