import ctypes
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from ctypes import wintypes
import winreg

import pystray
from PIL import Image, ImageDraw, ImageTk


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


APP_NAME = "RestReminder"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTEX_NAME = "Global\\WillisRestReminderSingleInstance"


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "settings.json"
LEGACY_CONFIG_FILE = APP_DIR / "settings.json"


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", APP_DIR))
    return base_path / relative_path


@dataclass
class Settings:
    work_minutes: int = 15
    rest_minutes: int = 15
    force_mode: bool = False
    lock_windows: bool = False
    auto_start: bool = False
    float_x: int | None = None
    float_y: int | None = None
    password: str = "1234"


def load_settings() -> Settings:
    if not CONFIG_FILE.exists() and LEGACY_CONFIG_FILE.exists():
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(LEGACY_CONFIG_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    if not CONFIG_FILE.exists():
        return Settings()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return Settings(
            work_minutes=max(1, int(data.get("work_minutes", 15))),
            rest_minutes=max(1, int(data.get("rest_minutes", 15))),
            force_mode=bool(data.get("force_mode", False)),
            lock_windows=bool(data.get("lock_windows", False)),
            auto_start=bool(data.get("auto_start", False)),
            float_x=data.get("float_x") if isinstance(data.get("float_x"), int) else None,
            float_y=data.get("float_y") if isinstance(data.get("float_y"), int) else None,
            password=str(data.get("password", "1234")) or "1234",
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return Settings()


def save_settings(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_seconds(seconds: int) -> str:
    minutes, sec = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def format_minutes_only(seconds: int) -> str:
    minutes = max(0, (seconds + 59) // 60)
    return f"{minutes} min"


def speak_async(text: str) -> None:
    escaped = text.replace("'", "''")
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice = $speaker.GetInstalledVoices() | "
        "Where-Object { $_.VoiceInfo.Culture.Name -like 'en-*' } | "
        "Select-Object -First 1; "
        "if ($voice) { $speaker.SelectVoice($voice.VoiceInfo.Name) }; "
        "$speaker.Rate = 0; "
        f"$speaker.Speak('{escaped}')"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass


def acquire_single_instance_mutex():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not mutex:
        return None, False
    already_exists = ctypes.get_last_error() == 183
    return mutex, not already_exists


def get_app_executable() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return str(Path(__file__).resolve())


def set_auto_start(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            executable = get_app_executable()
            if getattr(sys, "frozen", False):
                command = f'"{executable}"'
            else:
                command = f'"{sys.executable}" "{executable}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def make_tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(37, 99, 235, 255))
    draw.ellipse((18, 18, 46, 46), outline=(255, 255, 255, 255), width=5)
    draw.line((32, 32, 32, 20), fill=(255, 255, 255, 255), width=4)
    draw.line((32, 32, 42, 38), fill=(255, 255, 255, 255), width=4)
    return image


def get_virtual_screen_bounds(root: tk.Tk) -> tuple[int, int, int, int]:
    try:
        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(76)
        y = user32.GetSystemMetrics(77)
        width = user32.GetSystemMetrics(78)
        height = user32.GetSystemMetrics(79)
        if width > 0 and height > 0:
            return x, y, width, height
    except Exception:
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class KeyboardBlocker:
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    WM_QUIT = 0x0012
    LLKHF_ALTDOWN = 0x20
    VK_TAB = 0x09
    VK_ESCAPE = 0x1B
    VK_SPACE = 0x20
    VK_F4 = 0x73
    VK_LWIN = 0x5B
    VK_RWIN = 0x5C
    VK_CONTROL = 0x11
    VK_SHIFT = 0x10

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", wintypes.WPARAM),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_void_p),
            ("lParam", ctypes.c_void_p),
            ("time", ctypes.c_ulong),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
        ]

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.hook = None
        self.thread_id = 0
        self.thread: threading.Thread | None = None
        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        self.user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self.user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.CallNextHookEx.restype = wintypes.LPARAM
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self.user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._callback_type = ctypes.WINFUNCTYPE(
            wintypes.LPARAM,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._callback = self._callback_type(self._hook_proc)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.thread_id:
            self.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)
        self.thread_id = 0
        self.hook = None
        self.thread = None

    def _run(self) -> None:
        self.thread_id = self.kernel32.GetCurrentThreadId()
        self.hook = self.user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._callback, None, 0)
        msg = self.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        if self.hook:
            self.user32.UnhookWindowsHookEx(self.hook)
            self.hook = None

    def _is_down(self, vk_code: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(vk_code) & 0x8000)

    def _hook_proc(self, n_code, w_param, l_param):
        if n_code >= 0 and w_param in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
            info = ctypes.cast(l_param, ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents
            vk_code = info.vkCode
            alt_down = bool(info.flags & self.LLKHF_ALTDOWN)
            ctrl_down = self._is_down(self.VK_CONTROL)
            shift_down = self._is_down(self.VK_SHIFT)
            blocked = (
                vk_code in (self.VK_LWIN, self.VK_RWIN)
                or (alt_down and vk_code in (self.VK_TAB, self.VK_ESCAPE, self.VK_SPACE, self.VK_F4))
                or (ctrl_down and vk_code == self.VK_ESCAPE)
                or (ctrl_down and shift_down and vk_code == self.VK_ESCAPE)
            )
            if blocked:
                return 1
        return self.user32.CallNextHookEx(self.hook, n_code, w_param, l_param)


class ToastWindow:
    def __init__(self, root: tk.Tk, title: str, message: str, seconds: int | None = 6):
        self.window = tk.Toplevel(root)
        self.window.title(title)
        self.window.attributes("-topmost", True)
        self.window.resizable(False, False)
        self.window.configure(bg="#111827")
        self.window.overrideredirect(True)

        body = tk.Frame(self.window, bg="#111827", padx=18, pady=14)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text=title,
            fg="#ffffff",
            bg="#111827",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        self.message_label = tk.Label(
            body,
            text=message,
            fg="#d1d5db",
            bg="#111827",
            font=("Microsoft YaHei UI", 10),
        )
        self.message_label.pack(anchor="w", pady=(6, 0))

        self.place()
        if seconds is not None:
            self.window.after(seconds * 1000, self.close)

    def place(self) -> None:
        self.window.update_idletasks()
        width = 280
        height = self.window.winfo_reqheight()
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        self.window.geometry(f"{width}x{height}+{screen_w - width - 22}+{screen_h - height - 58}")

    def set_message(self, message: str) -> None:
        if self.window.winfo_exists():
            self.message_label.configure(text=message)
            self.place()

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()


class WorkFloatWindow:
    BORDER_WIDTH = 6
    MIN_CONTENT_WIDTH = 120

    def __init__(self, app: "RestReminderApp"):
        self.app = app
        self.root = app.root
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.current_x: int | None = None
        self.current_y: int | None = None

        self.window = tk.Toplevel(self.root)
        self.window.title("Work Timer Border")
        self.window.attributes("-topmost", True)
        self.window.overrideredirect(True)
        self.window.resizable(False, False)
        self.window.configure(bg="#ffffff")
        self.window.attributes("-alpha", 0.5)

        self.content_window = tk.Toplevel(self.root)
        self.content_window.title("Work Timer")
        self.content_window.attributes("-topmost", True)
        self.content_window.overrideredirect(True)
        self.content_window.resizable(False, False)
        self.content_window.configure(bg="#111827")
        self.content_window.attributes("-alpha", 0.1)

        self.body = tk.Frame(self.content_window, bg="#111827", padx=13, pady=8)
        self.body.pack(fill="both", expand=True)
        self.time_label = tk.Label(
            self.body,
            text="",
            fg="#ffffff",
            bg="#111827",
            font=("Microsoft YaHei UI", 15, "bold"),
        )
        self.time_label.pack(anchor="center")
        for widget in (self.window, self.content_window, self.body, self.time_label):
            widget.bind("<ButtonPress-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.drag)
            widget.bind("<ButtonRelease-1>", self.finish_drag)
            widget.bind("<Button-3>", self.show_menu)
        self.place()

    def default_position(self, width: int, height: int) -> tuple[int, int]:
        screen_w = self.root.winfo_screenwidth()
        return screen_w - width - 22, height + 22

    def is_position_visible(self, x: int, y: int, width: int, height: int) -> bool:
        screen_x = 0
        screen_y = 0
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        return (
            x >= screen_x
            and y >= screen_y
            and x + width <= screen_x + screen_w
            and y + height <= screen_y + screen_h
        )

    def place(self) -> None:
        self.content_window.update_idletasks()
        content_width = max(self.MIN_CONTENT_WIDTH, self.content_window.winfo_reqwidth())
        content_height = self.content_window.winfo_reqheight()
        width = content_width + self.BORDER_WIDTH * 2
        height = content_height + self.BORDER_WIDTH * 2
        x = self.app.settings.float_x
        y = self.app.settings.float_y
        if x is None or y is None or not self.is_position_visible(x, y, width, height):
            x, y = self.default_position(width, height)
        self.current_x = x
        self.current_y = y
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.content_window.geometry(
            f"{content_width}x{content_height}+{x + self.BORDER_WIDTH}+{y + self.BORDER_WIDTH}"
        )

    def apply_current_geometry(self) -> None:
        self.content_window.update_idletasks()
        content_width = max(self.MIN_CONTENT_WIDTH, self.content_window.winfo_reqwidth())
        content_height = self.content_window.winfo_reqheight()
        width = content_width + self.BORDER_WIDTH * 2
        height = content_height + self.BORDER_WIDTH * 2
        x = self.current_x
        y = self.current_y
        if x is None or y is None:
            x, y = self.default_position(width, height)
        if not self.is_position_visible(x, y, width, height):
            x, y = self.default_position(width, height)
        self.current_x = x
        self.current_y = y
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.content_window.geometry(
            f"{content_width}x{content_height}+{x + self.BORDER_WIDTH}+{y + self.BORDER_WIDTH}"
        )

    def start_drag(self, event) -> None:
        if self.current_x is None or self.current_y is None:
            self.current_x = self.window.winfo_x()
            self.current_y = self.window.winfo_y()
        self.drag_offset_x = event.x_root - self.current_x
        self.drag_offset_y = event.y_root - self.current_y

    def drag(self, event) -> None:
        x = event.x_root - self.drag_offset_x
        y = event.y_root - self.drag_offset_y
        self.current_x = x
        self.current_y = y
        self.window.geometry(f"+{x}+{y}")
        self.content_window.geometry(f"+{x + self.BORDER_WIDTH}+{y + self.BORDER_WIDTH}")

    def finish_drag(self, _event) -> None:
        if self.current_x is not None and self.current_y is not None:
            self.app.save_float_position(self.current_x, self.current_y)

    def show_menu(self, event) -> str:
        self.app.show_float_menu(event.x_root, event.y_root)
        return "break"

    def update(self, seconds: int) -> None:
        if not self.window.winfo_exists() or not self.content_window.winfo_exists():
            return

        if seconds <= 30:
            bg = "#991b1b"
            fg = "#ffffff"
            alpha = 1.0
            text = f"{max(0, seconds)} sec"
        elif seconds <= 60:
            bg = "#dc2626"
            fg = "#ffffff"
            alpha = 0.5
            text = format_minutes_only(seconds)
        elif seconds <= 120:
            bg = "#facc15"
            fg = "#111827"
            alpha = 0.1
            text = format_minutes_only(seconds)
        else:
            bg = "#111827"
            fg = "#ffffff"
            alpha = 0.1
            text = format_minutes_only(seconds)

        self.window.configure(bg="#ffffff")
        self.window.attributes("-alpha", 0.5)
        self.content_window.configure(bg=bg)
        self.body.configure(bg=bg)
        self.time_label.configure(text=text, bg=bg, fg=fg)
        self.content_window.attributes("-alpha", alpha)
        self.window.attributes("-topmost", True)
        self.content_window.attributes("-topmost", True)
        self.window.lift()
        self.content_window.lift()
        self.apply_current_geometry()

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()
        if self.content_window.winfo_exists():
            self.content_window.destroy()


class RestOverlay:
    def __init__(self, app: "RestReminderApp", seconds: int, forced: bool, password: str):
        self.app = app
        self.forced = forced
        self.password = password
        self.remaining = seconds
        self.finished = False
        self.mode = "vision"
        self.keyboard_blocker: KeyboardBlocker | None = None
        self.black_idle_after = None

        self.window = tk.Toplevel(app.root)
        self.window.title("Break")
        self.window.attributes("-topmost", True)
        self.window.overrideredirect(True)
        self.window.protocol("WM_DELETE_WINDOW", self._ignore_close)
        x, y, width, height = get_virtual_screen_bounds(app.root)
        self.window.geometry(f"{width}x{height}{x:+d}{y:+d}")
        self.window.grab_set()

        self.show_vision()
        self.bind_activity()
        self.window.focus_force()
        self.keep_on_top()
        self.tick()

    def bind_activity(self) -> None:
        self.bind_activity_to_widget(self.window)

    def bind_activity_to_widget(self, widget) -> None:
        for event_name in ("<Motion>", "<Button>", "<Key>"):
            widget.bind(event_name, self.handle_activity, add="+")
        for child in widget.winfo_children():
            self.bind_activity_to_widget(child)

    def clear_window(self) -> None:
        for child in self.window.winfo_children():
            child.destroy()

    def show_vision(self) -> None:
        self.mode = "vision"
        self.stop_keyboard_blocker()
        self.cancel_black_idle_timer()
        self.clear_window()
        self.window.configure(bg="#020617")
        x, y, width, height = get_virtual_screen_bounds(self.app.root)
        chart = tk.Canvas(
            self.window,
            width=width,
            height=height,
            bg="#020617",
            highlightthickness=0,
        )
        chart.pack(expand=True, fill="both")
        self.draw_distance_chart_image(chart, width, height)
        self.bind_activity()

    def draw_distance_chart_image(self, chart: tk.Canvas, width: int, height: int) -> None:
        image_path = resource_path("assets/E.png")
        try:
            image = Image.open(image_path).convert("RGB")
            image_ratio = image.width / image.height
            screen_ratio = width / height
            if image_ratio > screen_ratio:
                new_width = width
                new_height = int(width / image_ratio)
            else:
                new_height = height
                new_width = int(height * image_ratio)
            resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self.vision_photo = ImageTk.PhotoImage(resized)
            chart.create_image(width // 2, height // 2, image=self.vision_photo, anchor="center")
        except OSError:
            self.draw_distance_chart(chart, width, height)

    def draw_distance_chart(self, chart: tk.Canvas, width: int, height: int) -> None:
        center_x = width // 2
        center_y = height // 2
        green = "#2dd4bf"
        max_half = int(min(width, height) * 0.42)
        min_half = 10
        steps = 34
        for index in range(steps):
            ratio = index / (steps - 1)
            if index == 1:
                half = max_half - (max_half - min_half) * 0.05
            else:
                half = max_half - (max_half - min_half) * (ratio ** 0.48)
            line_width = max(2, int(8 - index * 0.16))
            blue = min(190, 120 + index * 2)
            color = f"#14{blue:02x}a6"
            chart.create_rectangle(
                center_x - half,
                center_y - half,
                center_x + half,
                center_y + half,
                outline=color,
                width=line_width,
            )
        chart.create_rectangle(center_x - 5, center_y - 5, center_x + 5, center_y + 5, outline=green, width=2)

    def show_black(self) -> None:
        self.mode = "black"
        self.clear_window()
        self.window.configure(bg="#000000")
        if self.forced and not self.keyboard_blocker:
            self.keyboard_blocker = KeyboardBlocker()
            self.keyboard_blocker.start()

        body = tk.Frame(self.window, bg="#000000", padx=36, pady=32)
        body.pack(expand=True, fill="both")

        tk.Label(
            body,
            text="On Break",
            fg="#ffffff",
            bg="#000000",
            font=("Microsoft YaHei UI", 34, "bold"),
        ).pack(pady=(20, 16))

        self.time_label = tk.Label(
            body,
            text=format_seconds(self.remaining),
            fg="#ffffff",
            bg="#000000",
            font=("Consolas", 56, "bold"),
        )
        self.time_label.pack(pady=(0, 22))

        tk.Label(
            body,
            text="Enter password to end the break early. No activity for 3 seconds returns to the focus chart.",
            fg="#cfcfcf",
            bg="#000000",
            font=("Microsoft YaHei UI", 13),
        ).pack(pady=(0, 16))

        form = tk.Frame(body, bg="#000000")
        form.pack()
        self.password_entry = ttk.Entry(form, show="*", width=24)
        self.password_entry.grid(row=0, column=0, padx=(0, 10))
        self.password_entry.bind("<Return>", lambda _event: self.try_unlock())
        ttk.Button(form, text="End Break", command=self.try_unlock).grid(row=0, column=1)
        self.error_label = tk.Label(
            body,
            text="",
            fg="#ff7676",
            bg="#000000",
            font=("Microsoft YaHei UI", 11),
        )
        self.error_label.pack(pady=(12, 0))
        self.password_entry.focus_set()
        self.window.after(100, self.password_entry.focus_force)
        self.schedule_black_idle_timer()
        self.bind_activity()

    def _ignore_close(self) -> None:
        self.window.bell()

    def _break_event(self, _event) -> str:
        self.window.bell()
        return "break"

    def keep_on_top(self) -> None:
        if self.finished:
            return
        if self.window.winfo_exists():
            x, y, width, height = get_virtual_screen_bounds(self.app.root)
            self.window.geometry(f"{width}x{height}{x:+d}{y:+d}")
            self.window.attributes("-topmost", True)
            self.window.lift()
            self.window.after(500, self.keep_on_top)

    def tick(self) -> None:
        if self.finished:
            return
        if hasattr(self, "time_label") and self.time_label.winfo_exists():
            self.time_label.configure(text=format_seconds(self.remaining))
        if self.remaining > 0:
            self.remaining -= 1
            self.window.after(1000, self.tick)
            return
        self.window.after(1000, self.tick)

    def handle_activity(self, _event=None):
        if self.finished:
            return
        if self.remaining <= 0:
            self.finish()
            return
        if self.mode == "vision":
            self.show_black()
        elif self.mode == "black":
            self.schedule_black_idle_timer()

    def cancel_black_idle_timer(self) -> None:
        if self.black_idle_after is not None:
            try:
                self.window.after_cancel(self.black_idle_after)
            except tk.TclError:
                pass
            self.black_idle_after = None

    def schedule_black_idle_timer(self) -> None:
        self.cancel_black_idle_timer()
        self.black_idle_after = self.window.after(3000, self.show_vision)

    def today_password(self) -> str:
        return datetime.now().strftime("%m%d")

    def try_unlock(self) -> None:
        entered = self.password_entry.get()
        if entered in (self.password, self.today_password()):
            self.finish()
            return
        self.password_entry.delete(0, tk.END)
        self.error_label.configure(text="Incorrect password")
        self.window.bell()
        self.schedule_black_idle_timer()

    def stop_keyboard_blocker(self) -> None:
        if self.keyboard_blocker:
            self.keyboard_blocker.stop()
            self.keyboard_blocker = None

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.cancel_black_idle_timer()
        self.stop_keyboard_blocker()
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        if self.window.winfo_exists():
            self.window.destroy()
        self.app.finish_rest()


class RestReminderApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.title("Break Reminder")
        self.root.minsize(520, 620)
        self.root.resizable(True, True)

        self.running = False
        self.phase = "work"
        self.remaining = self.settings.work_minutes * 60
        self.work_float: WorkFloatWindow | None = None
        self.warned_30_seconds = False
        self.warned_rest_start = False
        self.overlay: RestOverlay | None = None
        self.tray_icon: pystray.Icon | None = None

        self.work_var = tk.StringVar(value=str(self.settings.work_minutes))
        self.rest_var = tk.StringVar(value=str(self.settings.rest_minutes))
        self.force_var = tk.BooleanVar(value=self.settings.force_mode)
        self.lock_var = tk.BooleanVar(value=self.settings.lock_windows)
        self.auto_start_var = tk.BooleanVar(value=self.settings.auto_start)
        self.password_var = tk.StringVar(value=self.settings.password)
        self.status_var = tk.StringVar(value="Ready")
        self.time_var = tk.StringVar(value=format_seconds(self.remaining))
        self.setting_vars = [
            self.work_var,
            self.rest_var,
            self.force_var,
            self.lock_var,
            self.auto_start_var,
            self.password_var,
        ]

        self._build_ui()
        for var in self.setting_vars:
            var.trace_add("write", self.on_settings_changed)
        self.root.after(0, self.fit_window_to_content)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.setup_tray()
        self.root.after(500, self.start)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root)
        outer.pack(expand=True, fill="both")

        self.canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", expand=True, fill="both")

        frame = ttk.Frame(self.canvas, padding=22)
        self.content_frame = frame
        content_window = self.canvas.create_window((0, 0), window=frame, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        def fit_content_width(event) -> None:
            self.canvas.itemconfigure(content_window, width=event.width)

        def on_mousewheel(event) -> None:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        frame.bind("<Configure>", update_scroll_region)
        self.canvas.bind("<Configure>", fit_content_width)
        self.canvas.bind_all("<MouseWheel>", on_mousewheel)

        ttk.Label(frame, text="Break Reminder", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status_var, font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(4, 18))

        ttk.Label(frame, textvariable=self.time_var, font=("Consolas", 48, "bold")).pack(pady=(0, 12))

        controls_box = ttk.LabelFrame(frame, text="Timer Controls", padding=12)
        controls_box.pack(fill="x", pady=(0, 10))
        self.start_button = tk.Button(
            controls_box,
            text="Start",
            command=self.start,
            bg="#16a34a",
            fg="#ffffff",
            activebackground="#15803d",
            activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 11, "bold"),
            relief="flat",
            height=2,
        )
        self.start_button.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.pause_button = tk.Button(
            controls_box,
            text="Pause",
            command=self.pause,
            bg="#f59e0b",
            fg="#111827",
            activebackground="#d97706",
            activeforeground="#111827",
            font=("Microsoft YaHei UI", 11, "bold"),
            relief="flat",
            height=2,
            state="disabled",
        )
        self.pause_button.pack(side="left", expand=True, fill="x", padx=8)
        self.end_button = tk.Button(
            controls_box,
            text="End",
            command=self.end_timer,
            bg="#dc2626",
            fg="#ffffff",
            activebackground="#b91c1c",
            activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 11, "bold"),
            relief="flat",
            height=2,
        )
        self.end_button.pack(side="left", expand=True, fill="x", padx=(8, 0))

        settings_box = ttk.LabelFrame(frame, text="Settings", padding=14)
        settings_box.pack(fill="x", pady=(0, 16))

        ttk.Label(settings_box, text="Work time (minutes)").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Spinbox(settings_box, from_=1, to=600, textvariable=self.work_var, width=12).grid(row=0, column=1, sticky="e", pady=6)

        ttk.Label(settings_box, text="Break time (minutes)").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Spinbox(settings_box, from_=1, to=240, textvariable=self.rest_var, width=12).grid(row=1, column=1, sticky="e", pady=6)

        ttk.Checkbutton(settings_box, text="Force mode (black screen during break)", variable=self.force_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=6
        )
        ttk.Checkbutton(settings_box, text="Lock Windows when break starts", variable=self.lock_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=6
        )
        ttk.Checkbutton(settings_box, text="Run at Windows startup", variable=self.auto_start_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=6
        )

        ttk.Label(settings_box, text="Unlock password").grid(row=5, column=0, sticky="w", pady=6)
        ttk.Entry(settings_box, textvariable=self.password_var, show="*", width=15).grid(row=5, column=1, sticky="e", pady=6)
        settings_box.columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text="Closing the window hides it to the system tray; the timer keeps running.",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(16, 0))

    def fit_window_to_content(self) -> None:
        self.root.update_idletasks()
        screen_height = self.root.winfo_screenheight()
        screen_width = self.root.winfo_screenwidth()
        requested_width = max(560, min(self.content_frame.winfo_reqwidth() + 44, screen_width - 80))
        requested_height = max(620, min(self.content_frame.winfo_reqheight() + 12, screen_height - 100))
        x = max(20, (screen_width - requested_width) // 2)
        y = max(20, (screen_height - requested_height) // 2)
        self.root.geometry(f"{requested_width}x{requested_height}+{x}+{y}")

    def setup_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Open Settings", lambda: self.call_ui(self.show_window), default=True),
            pystray.MenuItem("Start", lambda: self.call_ui(self.start)),
            pystray.MenuItem("Pause", lambda: self.call_ui(self.pause)),
            pystray.MenuItem("End", lambda: self.call_ui(self.end_timer)),
            pystray.MenuItem("Exit", lambda: self.call_ui(self.quit_app)),
        )
        self.tray_icon = pystray.Icon("rest_reminder", make_tray_image(), "Break Reminder", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_float_menu(self, x: int, y: int) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Open Settings", command=self.show_window)
        menu.add_command(label="Start", command=self.start)
        menu.add_command(label="Pause", command=self.pause)
        menu.add_command(label="End", command=self.end_timer)
        menu.add_separator()
        menu.add_command(label="Exit", command=self.quit_app)
        menu.tk_popup(x, y)

    def save_float_position(self, x: int, y: int) -> None:
        self.settings.float_x = x
        self.settings.float_y = y
        save_settings(self.settings)

    def call_ui(self, func) -> None:
        self.root.after(0, func)

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self) -> None:
        self.root.withdraw()

    def show_toast(self, title: str, message: str, seconds: int = 6) -> None:
        return

    def ensure_work_float(self) -> None:
        if self.work_float and self.work_float.window.winfo_exists():
            return
        self.work_float = WorkFloatWindow(self)

    def close_work_float(self) -> None:
        if self.work_float:
            self.work_float.close()
            self.work_float = None

    def update_work_float(self) -> None:
        if not self.running or self.phase != "work":
            return
        self.ensure_work_float()
        if self.work_float:
            self.work_float.update(self.remaining)
        if 0 < self.remaining <= 30 and not self.warned_30_seconds:
            self.warned_30_seconds = True
            speak_async("Thirty seconds until break")

    def read_settings_from_ui(self) -> Settings | None:
        try:
            settings = Settings(
                work_minutes=max(1, int(self.work_var.get())),
                rest_minutes=max(1, int(self.rest_var.get())),
                force_mode=self.force_var.get(),
                lock_windows=self.lock_var.get(),
                auto_start=self.auto_start_var.get(),
                float_x=self.settings.float_x,
                float_y=self.settings.float_y,
                password=self.password_var.get().strip() or "1234",
            )
        except ValueError:
            return None
        return settings

    def get_ui_settings_for_compare(self) -> Settings | None:
        try:
            return Settings(
                work_minutes=max(1, int(self.work_var.get())),
                rest_minutes=max(1, int(self.rest_var.get())),
                force_mode=self.force_var.get(),
                lock_windows=self.lock_var.get(),
                auto_start=self.auto_start_var.get(),
                float_x=self.settings.float_x,
                float_y=self.settings.float_y,
                password=self.password_var.get().strip() or "1234",
            )
        except ValueError:
            return None

    def has_unsaved_changes(self) -> bool:
        ui_settings = self.get_ui_settings_for_compare()
        if ui_settings is None:
            return False
        return (
            ui_settings.work_minutes != self.settings.work_minutes
            or ui_settings.rest_minutes != self.settings.rest_minutes
            or ui_settings.force_mode != self.settings.force_mode
            or ui_settings.lock_windows != self.settings.lock_windows
            or ui_settings.auto_start != self.settings.auto_start
            or ui_settings.password != self.settings.password
        )

    def on_settings_changed(self, *_args) -> None:
        self.root.after_idle(self.apply_settings)

    def update_save_button_state(self) -> None:
        return

    def load_settings_into_ui(self, settings: Settings) -> None:
        self.work_var.set(str(settings.work_minutes))
        self.rest_var.set(str(settings.rest_minutes))
        self.force_var.set(settings.force_mode)
        self.lock_var.set(settings.lock_windows)
        self.auto_start_var.set(settings.auto_start)
        self.password_var.set(settings.password)

    def cancel_settings(self) -> None:
        return

    def apply_settings(self, show_message: bool = True) -> Settings | None:
        settings = self.read_settings_from_ui()
        if settings is None:
            return None
        old_settings = self.settings
        time_changed = (
            settings.work_minutes != old_settings.work_minutes
            or settings.rest_minutes != old_settings.rest_minutes
        )
        self.settings = settings
        save_settings(settings)
        try:
            set_auto_start(settings.auto_start)
        except OSError as exc:
            messagebox.showerror("Settings Error", f"Failed to set Windows startup: {exc}")
            return None
        if self.phase == "work" and time_changed:
            self.remaining = settings.work_minutes * 60
            self.time_var.set(format_seconds(self.remaining))
            self.warned_30_seconds = False
            self.warned_rest_start = False
            if self.running:
                self.update_work_float()
            else:
                self.close_work_float()
        return settings

    def start(self) -> None:
        if self.running and self.phase == "work":
            return
        settings = self.settings
        if self.phase == "work" and self.remaining <= 0:
            self.remaining = settings.work_minutes * 60
            self.warned_30_seconds = False
            self.warned_rest_start = False
        self.running = True
        self.ensure_work_float()
        self.update_work_float()
        self.start_button.configure(text="Resume", state="disabled")
        self.pause_button.configure(state="normal")
        self.end_button.configure(state="normal")
        self.status_var.set("Working")
        self.tick()

    def today_password(self) -> str:
        return datetime.now().strftime("%m%d")

    def ask_control_password(self, action_name: str) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{action_name} Verification")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Enter password to {action_name.lower()} the timer").pack(anchor="w", pady=(0, 10))
        password_var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=password_var, show="*", width=26)
        entry.pack(fill="x")
        error_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=error_var, foreground="#dc2626").pack(anchor="w", pady=(8, 0))

        result = {"ok": False}

        def verify() -> None:
            value = password_var.get()
            if value in (self.settings.password, self.today_password()):
                result["ok"] = True
                dialog.destroy()
                return
            password_var.set("")
            error_var.set("Incorrect password")
            entry.focus_force()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="OK", command=verify).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", expand=True, fill="x", padx=(6, 0))
        entry.bind("<Return>", lambda _event: verify())
        dialog.update_idletasks()
        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2)
        y = self.root.winfo_y() + max(0, (self.root.winfo_height() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f"+{x}+{y}")
        entry.focus_force()
        self.root.wait_window(dialog)
        return result["ok"]

    def pause(self) -> None:
        if not self.ask_control_password("Pause"):
            return
        self.pause_without_password()

    def pause_without_password(self) -> None:
        if self.phase != "work":
            return
        self.running = False
        self.close_work_float()
        self.start_button.configure(state="normal")
        self.pause_button.configure(state="disabled")
        self.end_button.configure(state="normal")
        self.status_var.set("Paused")

    def end_timer(self) -> None:
        if not self.ask_control_password("End"):
            return
        self.end_timer_without_password()

    def end_timer_without_password(self) -> None:
        if self.overlay and self.overlay.forced:
            messagebox.showinfo("Force Break Active", "Enter the password to end the break during force mode.")
            return
        settings = self.settings
        if self.overlay:
            self.overlay.finished = True
            try:
                self.overlay.window.grab_release()
            except tk.TclError:
                pass
            if self.overlay.window.winfo_exists():
                self.overlay.window.destroy()
            self.overlay = None
        self.running = False
        self.phase = "work"
        self.remaining = settings.work_minutes * 60
        self.warned_30_seconds = False
        self.warned_rest_start = False
        self.close_work_float()
        self.time_var.set(format_seconds(self.remaining))
        self.status_var.set("Ended")
        self.start_button.configure(text="Start", state="normal")
        self.pause_button.configure(state="disabled")
        self.end_button.configure(state="normal")

    def tick(self) -> None:
        if not self.running or self.phase != "work":
            return
        self.time_var.set(format_seconds(self.remaining))
        self.update_work_float()
        if self.remaining <= 0:
            if not self.warned_rest_start:
                self.warned_rest_start = True
                speak_async("Break time")
            self.start_rest()
            return
        self.remaining -= 1
        self.root.after(1000, self.tick)

    def start_rest(self) -> None:
        settings = self.settings
        self.phase = "rest"
        self.running = False
        self.close_work_float()
        self.status_var.set("On Break")
        self.pause_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.end_button.configure(state="normal")
        self.show_window()

        if settings.lock_windows:
            ctypes.windll.user32.LockWorkStation()
        self.overlay = RestOverlay(
            self,
            seconds=settings.rest_minutes * 60,
            forced=settings.force_mode,
            password=settings.password,
        )

    def finish_rest(self) -> None:
        self.overlay = None
        self.phase = "work"
        self.remaining = self.settings.work_minutes * 60
        self.warned_30_seconds = False
        self.warned_rest_start = False
        self.time_var.set(format_seconds(self.remaining))
        self.running = True
        self.ensure_work_float()
        self.update_work_float()
        self.status_var.set("Working")
        self.pause_button.configure(state="normal")
        self.start_button.configure(text="Resume", state="disabled")
        self.root.after(1000, self.tick)

    def quit_app(self) -> None:
        if self.overlay and self.overlay.forced:
            messagebox.showinfo("Force Break Active", "Cannot exit during force break.")
            return
        if self.tray_icon:
            self.tray_icon.stop()
        self.close_work_float()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    instance_mutex, is_first_instance = acquire_single_instance_mutex()
    if is_first_instance:
        RestReminderApp().run()
