import ctypes
import math
import queue
import sys
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from voice_core import DATA_DIR, LOG_PATH, MODELS, Engine, display_name

BG = "#262f45"
CARD = "#4b5772"
PANEL = "#38425c"
WHITE = "#ffffff"
MUTED = "#aab4c8"
CORAL = "#ff6b6b"
OFF = "#7d8aa6"
FONT = "Segoe UI"
W, H = 400, 672
CX, CY = 200, 172
BAR_Y = 388
BAR_COUNT = 36


def load_fonts():
    if sys.platform != "win32":
        return
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    gdi = ctypes.windll.gdi32
    for path in (base / "fonts").glob("*.ttf"):
        gdi.AddFontResourceExW(str(path), 0x10, 0)


def choose_font(root):
    return "Montserrat" if "Montserrat" in tkfont.families(root) else "Segoe UI"


def rounded(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def draw_mic(c, cx, cy, s, color, tag):
    lw = 4 if s > 50 else 2
    cy = cy - s * 0.14
    w = s * 0.36
    rounded(c, cx - w / 2, cy - s * 0.42, cx + w / 2, cy + s * 0.14, w / 2,
            fill=color, outline="", tags=tag)
    c.create_arc(cx - s * 0.3, cy - s * 0.02, cx + s * 0.3, cy + s * 0.58,
                 start=180, extent=180, style="arc", outline=color, width=lw, tags=tag)
    c.create_line(cx, cy + s * 0.58, cx, cy + s * 0.7, fill=color, width=lw, tags=tag)
    c.create_line(cx - s * 0.14, cy + s * 0.7, cx + s * 0.14, cy + s * 0.7, fill=color, width=lw, tags=tag)


def draw_model(c, cx, cy, s, color, tag):
    lw = 2
    for dy, kx in ((-0.3, -0.18), (0.0, 0.2), (0.3, -0.05)):
        y = cy + dy * s
        c.create_line(cx - 0.4 * s, y, cx + 0.4 * s, y, fill=color, width=lw, tags=tag)
        r = 0.09 * s
        c.create_oval(cx + kx * s - r, y - r, cx + kx * s + r, y + r, fill=color, outline="", tags=tag)


def draw_keys(c, cx, cy, s, color, tag):
    lw = 2
    rounded(c, cx - 0.46 * s, cy - 0.3 * s, cx + 0.46 * s, cy + 0.3 * s, 0.08 * s,
            fill="", outline=color, width=lw, tags=tag)
    for row in (-0.13, 0.02):
        for k in range(-3, 4):
            x, y, r = cx + k * 0.1 * s, cy + row * s, 0.035 * s
            c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="", tags=tag)
    c.create_line(cx - 0.2 * s, cy + 0.17 * s, cx + 0.2 * s, cy + 0.17 * s, fill=color, width=lw, tags=tag)


class App:
    def __init__(self, root):
        self.root = root
        self.engine = Engine()
        self.anim_running = False
        self.phase = 0.0
        self.last_text = ""
        self.update_url = None
        self.settings = None
        root.title("Голосовой ввод")
        root.resizable(False, False)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.setup_style()
        self.build()
        self.engine.start()
        self.draw_power()
        self.draw_bars(0.0)
        self.poll()

    def setup_style(self):
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", WHITE)
        self.root.option_add("*TCombobox*Listbox.selectBackground", CORAL)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=WHITE,
                        arrowcolor=WHITE, selectbackground=CARD, selectforeground=WHITE, borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", WHITE)],
                  selectbackground=[("readonly", CARD)])

    def build(self):
        c = self.canvas = tk.Canvas(self.root, width=W, height=H, bg=BG, highlightthickness=0)
        c.pack()
        c.create_text(24, 26, text="VOICE INPUT", anchor="w", fill=WHITE, font=(FONT, 13, "bold"))
        self.status_text = c.create_text(24, 50, anchor="w", fill=MUTED, font=(FONT, 9), width=352, tags="status")
        c.tag_bind("status", "<Button-1>", self.open_update)

        for r in (112, 92, 72):
            c.create_oval(CX - r, CY - r, CX + r, CY + r, outline=PANEL, width=2)
        self.pill_bg = rounded(c, CX - 74, 290, CX + 74, 318, 14, fill=WHITE, outline="", tags="pill")
        self.pill_text = c.create_text(CX, 304, fill=OFF, font=(FONT, 9, "bold"), tags="pill")
        self.hint_text = c.create_text(CX, 336, fill=MUTED, font=(FONT, 9))
        self.clickable("mic", self.on_power_click)
        self.clickable("pill", self.on_power_click)

        self.bars, self.bar_x = [], []
        for i in range(BAR_COUNT):
            x = 40 + i * (320 / (BAR_COUNT - 1))
            self.bar_x.append(x)
            self.bars.append(c.create_line(x, BAR_Y, x, BAR_Y, width=4, capstyle="round", fill=OFF))

        rounded(c, 24, 420, 376, 584, 20, fill=CARD, outline="")
        c.create_text(44, 440, text="ПОСЛЕДНИЙ РЕЗУЛЬТАТ", anchor="w", fill=CORAL, font=(FONT, 8, "bold"))
        self.copy_bg = rounded(c, 262, 432, 356, 458, 13, fill=WHITE, outline="", tags="copy")
        self.copy_text = c.create_text(309, 445, text="КОПИРОВАТЬ", fill=CORAL, font=(FONT, 8, "bold"), tags="copy")
        self.clickable("copy", self.copy_result)
        self.result_box = tk.Text(c, width=38, height=5, wrap="word", relief="flat", bd=0,
                                  highlightthickness=0, bg=CARD, fg=WHITE, insertbackground=WHITE,
                                  font=(FONT, 10), state="disabled")
        c.create_window(40, 470, anchor="nw", window=self.result_box)

        for tag, x, icon, label, fn in (
            ("btn_model", 120, draw_model, "Модель", self.open_models),
            ("btn_mic", 200, draw_mic, "Микрофон", self.open_mics),
            ("btn_keys", 280, draw_keys, "Клавиши", self.open_settings),
        ):
            c.create_oval(x - 26, 592, x + 26, 644, fill=CARD, outline="", tags=tag)
            icon(c, x, 618, 28, CORAL, tag)
            c.create_text(x, 658, text=label, fill=MUTED, font=(FONT, 9), tags=tag)
            self.clickable(tag, fn)

    def clickable(self, tag, fn):
        c = self.canvas
        c.tag_bind(tag, "<Button-1>", lambda e: fn(e))
        c.tag_bind(tag, "<Enter>", lambda e: c.config(cursor="hand2"))
        c.tag_bind(tag, "<Leave>", lambda e: c.config(cursor=""))

    def draw_power(self):
        c = self.canvas
        c.delete("mic")
        on = self.engine.enabled
        color = CORAL if on else OFF
        draw_mic(c, CX, CY, 96, color, "mic")
        self.clickable("mic", self.on_power_click)
        c.itemconfig(self.pill_text, text="ВКЛЮЧЕНО" if on else "ВЫКЛЮЧЕНО", fill=color)
        hold = display_name(self.engine.hotkeys["hold"])
        toggle = display_name(self.engine.hotkeys["toggle"])
        hint = f"Удерживайте {hold} для записи" if on else f"Нажмите микрофон или {toggle}"
        c.itemconfig(self.hint_text, text=hint)

    def draw_bars(self, level):
        self.phase += 0.25
        active = self.engine.recording
        for i, item in enumerate(self.bars):
            wave = abs(math.sin(i * 0.55 + self.phase))
            amp = 3 + (min(level * 260, 34) * (0.35 + 0.65 * wave) if active else 0)
            x = self.bar_x[i]
            self.canvas.coords(item, x, BAR_Y - amp, x, BAR_Y + amp)
            self.canvas.itemconfig(item, fill=CORAL if active else OFF)

    def animate(self):
        if self.engine.recording:
            self.draw_bars(self.engine.level)
            self.root.after(45, self.animate)
        else:
            self.anim_running = False
            self.draw_bars(0.0)

    def set_message(self, text):
        self.canvas.itemconfig(self.status_text, text=text)

    def show_result(self, text):
        self.last_text = text
        self.result_box.config(state="normal")
        self.result_box.delete("1.0", "end")
        self.result_box.insert("1.0", text)
        self.result_box.config(state="disabled")

    def show_update(self, version, url):
        self.update_url = url
        self.set_message(f"Доступна версия {version}. Нажмите здесь, чтобы скачать")
        self.canvas.itemconfig(self.status_text, fill=CORAL)

    def open_update(self, e=None):
        if self.update_url:
            webbrowser.open(self.update_url)

    def on_power_click(self, e=None):
        self.engine.set_enabled(not self.engine.enabled)

    def copy_result(self, e=None):
        if not self.last_text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_text)
        self.set_message("Скопировано в буфер обмена")

    def open_models(self, e=None):
        self.popup(e, MODELS, self.engine.cfg["model"], self.engine.set_model)

    def open_mics(self, e=None):
        self.popup(e, [d[1] for d in self.engine.devices], self.engine.device[1], self.engine.set_device)

    def popup(self, e, names, current, command):
        menu = tk.Menu(self.root, tearoff=0, bg=CARD, fg=WHITE, activebackground=CORAL,
                       activeforeground=WHITE, font=(FONT, 9))
        var = tk.StringVar(value=current)
        for name in names:
            menu.add_radiobutton(label=name, value=name, variable=var, command=lambda n=name: command(n))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def open_settings(self, e=None):
        if self.settings is not None:
            self.settings.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Настройки")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", self.close_settings)
        frame = tk.Frame(win, bg=BG, padx=24, pady=20)
        frame.pack()

        def label(row, text):
            tk.Label(frame, text=text, bg=BG, fg=WHITE, font=(FONT, 10)).grid(
                row=row, column=0, sticky="w", pady=8, padx=(0, 16))

        def key_button(row, target):
            btn = tk.Button(frame, width=26, bg=CARD, fg=CORAL, activebackground=CARD, activeforeground=CORAL,
                            relief="flat", font=(FONT, 10, "bold"), cursor="hand2",
                            command=lambda: self.start_capture(target))
            btn.grid(row=row, column=1, sticky="ew", pady=8)
            return btn

        label(0, "Микрофон")
        mic = ttk.Combobox(frame, state="readonly", width=30, values=[d[1] for d in self.engine.devices])
        mic.set(self.engine.device[1])
        mic.grid(row=0, column=1, sticky="ew", pady=8)
        mic.bind("<<ComboboxSelected>>", lambda e: self.engine.set_device(mic.get()))

        label(1, "Модель")
        model = ttk.Combobox(frame, state="readonly", width=30, values=MODELS)
        model.set(self.engine.cfg["model"])
        model.grid(row=1, column=1, sticky="ew", pady=8)
        model.bind("<<ComboboxSelected>>", lambda e: self.engine.set_model(model.get()))

        label(2, "Удержание для записи")
        self.settings_hold = key_button(2, "hold")
        label(3, "Включить / выключить")
        self.settings_toggle = key_button(3, "toggle")

        tk.Label(frame, text="Левая и правая кнопки мыши не назначаются. Esc — отмена.",
                 bg=BG, fg=MUTED, font=(FONT, 9), wraplength=340, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 14))
        tk.Button(frame, text="ГОТОВО", bg=CORAL, fg=WHITE, activebackground=CORAL, activeforeground=WHITE,
                  relief="flat", font=(FONT, 10, "bold"), padx=18, pady=4, cursor="hand2",
                  command=self.close_settings).grid(row=5, column=0, columnspan=2, sticky="e")

        self.settings = win
        self.refresh_settings()

    def close_settings(self):
        if self.settings is not None:
            self.settings.destroy()
            self.settings = None
        self.engine.capturing = None

    def refresh_settings(self, capturing=None):
        if self.settings is None:
            return
        for target, btn in (("hold", self.settings_hold), ("toggle", self.settings_toggle)):
            text = "Нажмите клавишу..." if capturing == target else display_name(self.engine.hotkeys[target])
            btn.config(text=text)

    def start_capture(self, target):
        self.engine.start_capture(target)
        self.set_message("Нажмите клавишу или боковую кнопку мыши. Esc — отмена")
        self.refresh_settings(target)

    def poll(self):
        while True:
            try:
                ev = self.engine.events.get_nowait()
            except queue.Empty:
                break
            self.handle(ev)
        self.root.after(100, self.poll)

    def handle(self, ev):
        kind = ev[0]
        if kind in ("status", "error"):
            self.set_message(ev[1])
        elif kind == "model":
            self.set_message(f"Модель '{ev[1]}' готова")
        elif kind == "state":
            self.draw_power()
        elif kind == "recording" and ev[1]:
            self.set_message("Запись...")
            if not self.anim_running:
                self.anim_running = True
                self.animate()
        elif kind == "result":
            self.show_result(ev[1])
        elif kind == "capture":
            self.refresh_settings()
            self.draw_power()
        elif kind == "update":
            self.show_update(ev[1], ev[2])

    def close(self):
        self.engine.stop()
        self.root.destroy()


def main():
    global FONT
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    load_fonts()
    root = tk.Tk()
    FONT = choose_font(root)
    try:
        App(root)
    except Exception:
        detail = traceback.format_exc()
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text(detail, encoding="utf-8")
        except Exception:
            pass
        messagebox.showerror("Ошибка запуска", detail[-1500:] + f"\n\nПодробности: {LOG_PATH}")
        root.destroy()
        sys.exit(1)
    root.mainloop()


if __name__ == "__main__":
    main()
