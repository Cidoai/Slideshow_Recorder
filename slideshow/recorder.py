"""录制界面：全屏显示 + 页码框 + 状态机驱动。

设计要点：
- 页码框不用原生 Entry（方向键会被拿去移光标、中文输入法会截走数字），
  而是 Canvas 自绘 + 窗口级按键捕获。
- 所有切图入口（← →、Home/End、数字+Enter、点击页码框）都走 Session.set_page，
  因此不存在「某种切图方式没被记录」的可能。
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, messagebox

from PIL import Image, ImageTk

from .audio import AudioUnavailable, MicRecorder, wav_to_m4a
from .session import Session
from .timeline import build_timeline
from .util import sha256_file

BG = "#1e1e22"
FG = "#e8e8ea"
MUTED = "#8a8a92"
RED = "#e5484d"
BLUE = "#4c8dff"
AMBER = "#f5a524"

COUNTDOWN_SEC = 3
INPUT_TIMEOUT = 3.0


class RecorderApp:
    def __init__(self, images: list[Path], out_dir: Path, *,
                 audio_file: Path | None = None, device: int | None = None,
                 width: int = 1920, height: int = 1080, fps: int = 12,
                 windowed: bool = False):
        self.images = [Path(p).resolve() for p in images]
        self.out_dir = Path(out_dir)
        self.audio_file = Path(audio_file) if audio_file else None
        self.device = device
        width = width or 1920
        height = height or 1080
        fps = fps or 12
        self.canvas_cfg = {"width": width, "height": height, "fps": fps, "aspect": "16:9"}
        self.windowed = windowed

        self.session = Session(self.images, min_segment=1.0 / fps,
                               partial_path=self.out_dir / "timeline.partial.jsonl")
        self.mic: MicRecorder | None = None
        self.state = "idle"          # idle | countdown | running | closing
        self.input_buf = ""
        self._input_deadline = 0.0
        self._esc_deadline = 0.0
        self._count = 0
        self._flash_until = 0.0
        self._flash_text = ""
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._thumbs: dict[int, ImageTk.PhotoImage] = {}
        self.result: Path | None = None
        self.error: str | None = None

        self.root = tk.Tk()
        self.root.title("图文录制器")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        if windowed:
            self.root.geometry(f"{min(width, 1280)}x{min(height, 720)}")
        else:
            self.root.attributes("-fullscreen", True)
        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()
        if windowed:
            self.sw, self.sh = min(width, 1280), min(height, 720)

        self.canvas = tk.Canvas(self.root, width=self.sw, height=self.sh,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.f_big = tkfont.Font(family="Microsoft YaHei UI", size=28, weight="bold")
        self.f_mid = tkfont.Font(family="Microsoft YaHei UI", size=18)
        self.f_small = tkfont.Font(family="Microsoft YaHei UI", size=13)
        self.f_box = tkfont.Font(family="Consolas", size=22, weight="bold")

        self.root.bind("<Key>", self._on_key)
        self.canvas.bind("<Button-1>", self._on_click)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.canvas.focus_set()
        self.root.after(50, self.root.focus_force)

        self._redraw_all()
        self.root.after(100, self._tick)

    # ---------------------------------------------------------------- 绘制

    TOP = 96
    BOTTOM = 150

    def _area(self) -> tuple[int, int]:
        return self.sw - 80, self.sh - self.TOP - self.BOTTOM

    def _photo_for(self, idx: int) -> ImageTk.PhotoImage:
        idx = max(0, min(idx, len(self.images) - 1))
        cached = self._photos.get(idx)
        if cached is not None:
            return cached
        aw, ah = self._area()
        with Image.open(self.images[idx]) as im:
            im = im.convert("RGB")
            scale = min(aw / im.width, ah / im.height)
            size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
            im = im.resize(size, Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
        if len(self._photos) > 4:
            self._photos.clear()
        self._photos[idx] = photo
        return photo

    def _thumb_for(self, idx: int, height: int = 84) -> ImageTk.PhotoImage:
        cached = self._thumbs.get(idx)
        if cached is not None:
            return cached
        with Image.open(self.images[idx]) as im:
            im = im.convert("RGB")
            w = max(1, int(im.width * height / im.height))
            im = im.resize((w, height), Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
        if len(self._thumbs) > 6:
            self._thumbs.clear()
        self._thumbs[idx] = photo
        return photo

    def _redraw_all(self) -> None:
        c = self.canvas
        c.delete("all")
        cur = self.session.current
        n = len(self.images)
        aw, ah = self._area()
        cx, cy = self.sw // 2, self.TOP + ah // 2

        c.create_rectangle(0, 0, self.sw, self.sh, fill=BG, width=0)
        c.create_image(cx, cy, image=self._photo_for(cur))

        # 左上：录制指示
        if self.state == "running":
            c.create_oval(28, 26, 44, 42, fill=RED, width=0)
            hud = "REC 00:00.0"
        elif self.state == "paused":
            c.create_oval(28, 26, 44, 42, fill=AMBER, width=0)
            hud = "PAUSED 00:00.0"
        else:
            c.create_oval(28, 26, 44, 42, outline=MUTED, width=2)
            hud = "未开始"
        self._hud = c.create_text(54, 34, anchor="w", text=hud, fill=FG, font=self.f_mid)

        # 右上：页码框
        x1, y1, x2, y2 = self._box_rect()
        self._box_bg = c.create_rectangle(x1, y1, x2, y2, fill="#26262c",
                                          outline="#3a3a42", width=2)
        self._box_text = c.create_text((x1 + x2) / 2 - 12, (y1 + y2) / 2,
                                       text=self._box_label(), fill=FG, font=self.f_box)
        self._box_enter = c.create_text(x2 - 18, (y1 + y2) / 2, text="", fill=BLUE,
                                        font=self.f_mid)
        c.create_text((x1 + x2) / 2, y2 + 14, text="点击或按数字键跳页",
                      fill=MUTED, font=self.f_small)

        # 右下：下一张预览（放在底部预留带里，绝不压主图）
        nxt = min(cur + 1, n - 1)
        if n > 1:
            thumb = self._thumb_for(nxt, height=96)
            c.create_image(self.sw - 28, self.sh - 40, anchor="se", image=thumb)
            c.create_text(self.sw - 28, self.sh - 146, anchor="se", fill=MUTED,
                          text=f"下一张 {nxt + 1}/{n}", font=self.f_small)

        # 左下：当前页已显示时长
        self._elapsed = c.create_text(30, self.sh - 62, anchor="sw", text="", fill=MUTED,
                                      font=self.f_mid)
        # 底部：操作提示
        c.create_text(self.sw // 2, self.sh - 22, text=(
            "空格 开始/暂停   Esc 结束   ← → 切图   Home/End 首尾   数字+Enter 跳页"),
            fill=MUTED, font=self.f_small)
        # 中央提示（倒数/错误）
        self._center = c.create_text(self.sw // 2, cy, text="", fill=AMBER, font=self.f_big)

        # 顶部徽标：暂停态用，避免压住演示内容
        self._badge_bg = c.create_rectangle(0, 0, 0, 0, fill="#2a2a30",
                                            outline=AMBER, width=1, state="hidden")
        self._badge = c.create_text(self.sw // 2, 46, text="", fill=AMBER,
                                    font=self.f_mid, state="hidden")

        self._refresh_box()

    def _box_rect(self) -> tuple[int, int, int, int]:
        return self.sw - 226, 14, self.sw - 24, 76

    def _box_label(self) -> str:
        n = len(self.images)
        if self.state in ("running", "paused") and self.input_buf:
            return f"{self.input_buf}_ / {n}"
        cur = self.session.current + 1
        return f"{cur} / {n}"

    def _refresh_box(self) -> None:
        if not hasattr(self, "_box_text"):
            return
        c = self.canvas
        editing = self.state in ("running", "paused") and bool(self.input_buf)
        color = BLUE if editing else ("#3a3a42" if self.state not in ("running", "paused") else "#4a4a54")
        c.itemconfigure(self._box_bg, outline=color)
        c.itemconfigure(self._box_text, text=self._box_label())
        if hasattr(self, "_box_enter"):
            c.itemconfigure(self._box_enter, text="⏎" if editing else "")

    # ---------------------------------------------------------------- 时钟

    def _tick(self) -> None:
        if self.state in ("running", "paused"):
            if self.state == "running":
                self.canvas.itemconfigure(
                    self._hud, text=f"REC {_mmss(self.session.elapsed)}")
            else:
                self.canvas.itemconfigure(
                    self._hud, text=f"PAUSED {_mmss(self.session.elapsed)}")
            self.canvas.itemconfigure(
                self._elapsed, text=f"本页已显示 {self.session.current_duration:0.1f}s")
            if self.input_buf and time.perf_counter() > self._input_deadline:
                self.input_buf = ""
                self._refresh_box()
        if self._flash_text and time.perf_counter() > self._flash_until:
            self._flash_text = ""
            if self.state == "running":
                self.canvas.itemconfigure(self._center, text="")
        self.root.after(100, self._tick)

    # ---------------------------------------------------------------- 输入

    def _on_key(self, e) -> None:
        k = e.keysym
        if self.state == "idle":
            if k == "space":
                self._begin_countdown()
            elif k == "Escape":
                self._quit()
            return

        if self.state == "countdown":
            if k == "Escape":
                self._cancel_countdown()
            return

        if self.state not in ("running", "paused"):
            return

        # Esc：先退输入态，其次才是结束录制
        if k == "Escape":
            if self.input_buf:
                self._clear_input()
            else:
                self._stop()
            return

        if k == "space":
            if self.state == "running":
                self._pause()
            else:
                self._resume()
            return

        if k in ("Left", "Right", "Home", "End"):
            self._clear_input()
            moved = {
                "Left": self.session.prev_page,
                "Right": self.session.next_page,
                "Home": self.session.first_page,
                "End": self.session.last_page,
            }[k]()
            self._after_nav(moved)
            return

        if k == "BackSpace":
            if self.input_buf:
                self.input_buf = self.input_buf[:-1]
                self._input_deadline = time.perf_counter() + INPUT_TIMEOUT
                self._refresh_box()
            return

        if k in ("Return", "KP_Enter"):
            self._commit_page()
            return

        digit = None
        if len(k) == 1 and k.isdigit():
            digit = k
        elif k.startswith("KP_") and k[3:].isdigit():
            digit = k[3:]
        if digit is not None:
            if len(self.input_buf) >= 4:
                self.input_buf = ""
            self.input_buf += digit
            self._input_deadline = time.perf_counter() + INPUT_TIMEOUT
            self._refresh_box()

    def _on_click(self, e) -> None:
        if self.state not in ("running", "paused"):
            return
        x1, y1, x2, y2 = self._box_rect()
        if x1 <= e.x <= x2 and y1 <= e.y <= y2:
            self.input_buf = ""
            self._input_deadline = time.perf_counter() + INPUT_TIMEOUT
            self._refresh_box()
            self._flash("输入页码后按 Enter")

    def _show_badge(self, text: str) -> None:
        if not hasattr(self, "_badge"):
            return
        c = self.canvas
        c.itemconfigure(self._badge, text=text, state="normal")
        c.update_idletasks()
        x1, y1, x2, y2 = c.bbox(self._badge)
        c.coords(self._badge_bg, x1 - 16, y1 - 7, x2 + 16, y2 + 7)
        c.itemconfigure(self._badge_bg, state="normal")
        c.tag_raise(self._badge_bg)
        c.tag_raise(self._badge)

    def _hide_badge(self) -> None:
        if not hasattr(self, "_badge"):
            return
        self.canvas.itemconfigure(self._badge, state="hidden")
        self.canvas.itemconfigure(self._badge_bg, state="hidden")

    def _pause(self) -> None:
        if self.state != "running":
            return
        if self.audio_file is not None:
            self._flash("复用音频模式下不能暂停（音频长度已固定）")
            return
        self.session.pause()
        if self.mic is not None:
            self.mic.pause()
        self.state = "paused"
        self._clear_input()
        self._redraw_all()
        self._show_badge("已暂停 · 空格继续")

    def _resume(self) -> None:
        if self.state != "paused":
            return
        t0 = time.perf_counter()
        if self.mic is not None:
            try:
                self.mic.resume()
                t0 = self.mic.wait_t0(1.5)
            except AudioUnavailable as exc:
                messagebox.showerror("无法继续录音", str(exc))
                return
        self.session.resume(t0)
        self.state = "running"
        self._redraw_all()

    def _after_nav(self, moved: bool) -> None:
        if moved:
            self._redraw_all()

    def _clear_input(self) -> None:
        if self.input_buf:
            self.input_buf = ""
            self._refresh_box()

    def _commit_page(self) -> None:
        if not self.input_buf:
            return
        try:
            page = int(self.input_buf)
        except ValueError:
            self._clear_input()
            return
        n = len(self.images)
        if page < 1 or page > n:
            self._flash(f"没有第 {page} 页（共 {n} 页）")
            self.input_buf = ""
            self._refresh_box()
            return
        self.input_buf = ""
        if self.session.set_page(page):
            self._redraw_all()
        else:
            self._refresh_box()

    def _flash(self, text: str, seconds: float = 1.6) -> None:
        self._flash_text = text
        self._flash_until = time.perf_counter() + seconds
        if hasattr(self, "_center"):
            self.canvas.itemconfigure(self._center, text=text)

    # ---------------------------------------------------------------- 录制

    def _begin_countdown(self) -> None:
        self.state = "countdown"
        self._count = COUNTDOWN_SEC
        self._show_countdown()

    def _show_countdown(self) -> None:
        if self.state != "countdown":
            return
        if self._count > 0:
            self.canvas.itemconfigure(self._center, text=str(self._count), fill="#ffffff")
            self._count -= 1
            self.root.after(1000, self._show_countdown)
        else:
            self.canvas.itemconfigure(self._center, text="开始", fill=AMBER)
            self.root.after(500, self._start_audio)

    def _cancel_countdown(self) -> None:
        self.state = "idle"
        self.canvas.itemconfigure(self._center, text="")
        self._redraw_all()

    def _start_audio(self) -> None:
        if self.state != "countdown":
            return
        try:
            if self.audio_file is not None:
                t0 = time.perf_counter()
            else:
                self.mic = MicRecorder(device=self.device)
                self.mic.start()
                t0 = self.mic.wait_t0(1.5)
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self.session.begin(t0)
        except AudioUnavailable as exc:
            self.state = "idle"
            self.mic = None
            self.canvas.itemconfigure(self._center, text="")
            messagebox.showerror("麦克风不可用", str(exc))
            self._redraw_all()
            return
        self.state = "running"
        self.canvas.itemconfigure(self._center, text="")
        self._redraw_all()

    def _stop(self) -> None:
        if self.state not in ("running", "paused"):
            return
        self.state = "closing"
        elapsed = self.session.elapsed
        if elapsed < 1.5:
            messagebox.showwarning("录制太短", f"只录了 {elapsed:.1f} 秒，已取消。")
            self.session.stop()
            self.root.destroy()
            return

        try:
            if self.mic is not None:
                wav = self.out_dir / "audio.wav"
                self.mic.stop(wav)
                m4a = wav_to_m4a(wav, self.out_dir / "audio.m4a")
            else:
                m4a = self.audio_file
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存音频失败", str(exc))
            self.root.destroy()
            return

        segs = self.session.stop()
        tl = build_timeline(segs, m4a, self.out_dir, self.canvas_cfg)
        cache: dict[str, str] = {}
        for seg in tl.segments:
            p = tl.image_path(seg)
            if seg.image not in cache:
                cache[seg.image] = sha256_file(p)
            seg.sha256 = cache[seg.image]
        path = tl.save(self.out_dir / "timeline.json")
        self.result = path

        # 正式时间轴已落盘，partial 不再需要
        partial = self.out_dir / "timeline.partial.jsonl"
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass

        wav = self.out_dir / "audio.wav"
        if wav.exists():
            try:
                wav.unlink()
            except OSError:
                pass
        self.root.destroy()

    def _quit(self) -> None:
        if self.state in ("running", "paused"):
            if not messagebox.askyesno("退出", "正在录制，确定放弃吗？"):
                return
            self.session.stop()
            if self.mic is not None:
                try:
                    self.mic.stop(self.out_dir / "audio.wav")
                except Exception:  # noqa: BLE001
                    pass
        self.root.destroy()

    def run(self) -> Path | None:
        self.root.mainloop()
        return self.result


def _mmss(sec: float) -> str:
    m, s = divmod(max(0.0, sec), 60)
    return f"{int(m):02d}:{s:04.1f}"
