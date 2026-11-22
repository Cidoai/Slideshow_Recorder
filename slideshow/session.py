"""录制会话状态机：与 UI、与声卡完全解耦，时钟可注入。

唯一真源是 current_index。界面渲染和日志写入都从它派生，
因此「屏幕上显示哪张」与「时间轴记了哪张」不可能不一致。

时钟是「可暂停秒表」：
    elapsed = base + (now - run_start)     运行中
    elapsed = base                         暂停中
暂停期间的墙钟时间不会被计入，所以成品里没有死寂空档，
音频也因为暂停时停止收音而自动与时间轴对齐。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .timeline import Segment


class Session:
    def __init__(self, images: list[Path], *, clock: Callable[[], float] = time.perf_counter,
                 min_segment: float = 1 / 12, partial_path: Path | None = None):
        if not images:
            raise ValueError("没有图片")
        self.images = list(images)
        self.clock = clock
        self.min_segment = min_segment
        self.partial_path = partial_path

        self.current = 0                 # 0-based
        self.state = "idle"              # idle | running | paused | stopped
        self.segments: list[Segment] = []
        self._base = 0.0
        self._run_start: float | None = None
        self._warned: list[str] = []
        self.pause_count = 0

    # ---- 生命周期 ----

    def begin(self, t0: float | None = None) -> None:
        if self.state in ("running", "paused"):
            return
        self._run_start = self.clock() if t0 is None else t0
        self._base = 0.0
        self.state = "running"
        self.segments = [Segment(t=0.0, index=self.current,
                                 image=self._rel(self.images[self.current]))]
        self.pause_count = 0
        self._flush_partial()

    def pause(self) -> bool:
        if self.state != "running":
            return False
        self._base = self.elapsed
        self._run_start = None
        self.state = "paused"
        self.pause_count += 1
        return True

    def resume(self, t0: float | None = None) -> bool:
        if self.state != "paused":
            return False
        self._run_start = self.clock() if t0 is None else t0
        self.state = "running"
        return True

    def stop(self) -> list[Segment]:
        if self.state == "running":
            self._base = self.elapsed
        self._run_start = None
        self.state = "stopped"
        return list(self.segments)

    # ---- 时间 ----

    @property
    def elapsed(self) -> float:
        if self.state == "running" and self._run_start is not None:
            return self._base + max(0.0, self.clock() - self._run_start)
        return self._base

    @property
    def current_duration(self) -> float:
        """当前这张图已经显示了多久（不含暂停）。"""
        if not self.segments:
            return 0.0
        return max(0.0, self.elapsed - self.segments[-1].t)

    # ---- 状态变更（唯一入口） ----

    def set_page(self, page: int) -> bool:
        """跳到第 page 页（1-based）。返回是否真的发生了切换。

        暂停期间也允许切换：时间冻结在暂停点，所以恢复后画面直接从新图开始。
        """
        if self.state not in ("running", "paused"):
            return False
        n = len(self.images)
        if page < 1 or page > n:
            return False
        idx = page - 1
        if idx == self.current:
            return False

        t = self.elapsed
        self.current = idx

        last = self.segments[-1]
        if t - last.t < self.min_segment:
            self._warned.append(f"t={t:.3f}s 的切换过短，已合并")
            self.segments[-1] = Segment(t=last.t, index=idx,
                                        image=self._rel(self.images[idx]))
        else:
            self.segments.append(Segment(t=t, index=idx,
                                         image=self._rel(self.images[idx])))
        self._flush_partial()
        return True

    def next_page(self) -> bool:
        return self.set_page(self.current + 2)

    def prev_page(self) -> bool:
        return self.set_page(self.current)

    def first_page(self) -> bool:
        return self.set_page(1)

    def last_page(self) -> bool:
        return self.set_page(len(self.images))

    # ---- 内部 ----

    def _rel(self, p: Path) -> str:
        return Path(p).as_posix()

    def _flush_partial(self) -> None:
        """每次状态变更立刻落盘，崩溃时最多丢最后一条。"""
        if not self.partial_path:
            return
        try:
            self.partial_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.partial_path, "w", encoding="utf-8", newline="\n") as f:
                for s in self.segments:
                    f.write(json.dumps({"t": round(s.t, 3), "index": s.index,
                                        "image": s.image}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @property
    def warnings(self) -> list[str]:
        return list(self._warned)
