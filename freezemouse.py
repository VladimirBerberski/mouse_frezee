#!/usr/bin/env python3
"""
FreezeMouse - lock and hide your mouse cursor.

A tiny, free Windows utility:
  * Disable mouse MOVEMENT while keeping clicks working.
  * Optionally HIDE the mouse cursor.
  * Global hotkeys (all rebindable in the app):
        F3 - toggle "disable movement"
        F5 - toggle "hide cursor"
        F2 - restore everything to normal

All effects apply across every monitor. Windows only, no dependencies beyond
the Python standard library (tkinter + ctypes).

Run with:  python freezemouse.py

Safety: hiding the cursor changes the system cursor for the whole desktop.
FreezeMouse always restores the cursor when it exits (normal close, Ctrl+C, or
crash) and resets the cursor on startup, so if it is ever killed while hidden,
just launch it again.

Free software, MIT licensed. Use it, share it, tweak it.
"""

import os
import sys
import atexit
import threading
import ctypes
from ctypes import wintypes

if not sys.platform.startswith("win"):
    raise SystemExit("FreezeMouse only runs on Windows.")

import tkinter as tk
from tkinter import font as tkfont

APP_NAME = "FreezeMouse"
APP_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Win32 setup
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HHOOK = wintypes.HANDLE
HCURSOR = wintypes.HANDLE

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_MOUSEMOVE = 0x0200
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012

SPI_SETCURSORS = 0x0057
SPIF_SENDCHANGE = 0x0002

OCR_IDS = [32512, 32513, 32514, 32515, 32516, 32631, 32640, 32641,
           32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650, 32651]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = HHOOK
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = (HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = (HHOOK,)
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.CreateCursor.restype = HCURSOR
user32.CreateCursor.argtypes = (wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
user32.SetSystemCursor.restype = wintypes.BOOL
user32.SetSystemCursor.argtypes = (HCURSOR, wintypes.DWORD)
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
class State:
    def __init__(self):
        self.freeze = False
        self.hide = False
        self.freeze_vk = 0x72   # F3
        self.hide_vk = 0x74     # F5
        self.restore_vk = 0x71  # F2
        self.capture_target = None
        self.capture_which = None
        self.captured_vk = 0
        self.capture_signal = False
        self.restore_requested = False
        self.freeze_toggle = False
        self.hide_toggle = False
        self.hidden_now = False
        self.hook_thread_id = 0


state = State()
mouse_hook = None
kbd_hook = None


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------
@HOOKPROC
def _mouse_proc(nCode, wParam, lParam):
    if nCode == HC_ACTION and state.freeze and wParam == WM_MOUSEMOVE:
        return 1
    return user32.CallNextHookEx(mouse_hook, nCode, wParam, lParam)


@HOOKPROC
def _kbd_proc(nCode, wParam, lParam):
    if nCode == HC_ACTION and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        if state.capture_target is not None:
            state.captured_vk = vk
            state.capture_which = state.capture_target
            state.capture_target = None
            state.capture_signal = True
            return 1
        if vk == state.restore_vk:
            state.restore_requested = True
            return 1
        if vk == state.freeze_vk:
            state.freeze_toggle = True
            return 1
        if vk == state.hide_vk:
            state.hide_toggle = True
            return 1
    return user32.CallNextHookEx(kbd_hook, nCode, wParam, lParam)


def _hook_thread():
    global mouse_hook, kbd_hook
    state.hook_thread_id = kernel32.GetCurrentThreadId()
    hmod = kernel32.GetModuleHandleW(None)
    mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, _mouse_proc, hmod, 0)
    kbd_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _kbd_proc, hmod, 0)
    try:
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if mouse_hook:
            user32.UnhookWindowsHookEx(mouse_hook)
        if kbd_hook:
            user32.UnhookWindowsHookEx(kbd_hook)


# ---------------------------------------------------------------------------
# Cursor hide / show
# ---------------------------------------------------------------------------
def _make_blank_cursor():
    n = 32 * 32 // 8
    and_mask = (ctypes.c_ubyte * n)(*([0xFF] * n))
    xor_mask = (ctypes.c_ubyte * n)(*([0x00] * n))
    return user32.CreateCursor(None, 0, 0, 32, 32,
                               ctypes.cast(and_mask, ctypes.c_void_p),
                               ctypes.cast(xor_mask, ctypes.c_void_p))


def _hide_cursor():
    for ocr in OCR_IDS:
        cur = _make_blank_cursor()
        if cur:
            user32.SetSystemCursor(cur, ocr)


def _show_cursor():
    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)


# ---------------------------------------------------------------------------
# Virtual-key names
# ---------------------------------------------------------------------------
VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause", 0x14: "Caps Lock",
    0x1B: "Esc", 0x20: "Space", 0x21: "Page Up", 0x22: "Page Down", 0x23: "End",
    0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2D: "Insert", 0x2E: "Delete", 0x5B: "Left Win", 0x5C: "Right Win",
    0x90: "Num Lock", 0x91: "Scroll Lock",
    0xA0: "L Shift", 0xA1: "R Shift", 0xA2: "L Ctrl", 0xA3: "R Ctrl",
    0xA4: "L Alt", 0xA5: "R Alt",
}
for _i in range(0x30, 0x3A):
    VK_NAMES[_i] = chr(_i)
for _i in range(0x41, 0x5B):
    VK_NAMES[_i] = chr(_i)
for _i in range(1, 25):
    VK_NAMES[0x6F + _i] = "F" + str(_i)
for _i in range(10):
    VK_NAMES[0x60 + _i] = "Num " + str(_i)


def vk_name(vk):
    return VK_NAMES.get(vk, "0x%02X" % vk)


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
BG      = "#0e1420"   # window background
PANEL   = "#161e2e"   # card background
PANEL2  = "#1b2536"   # subtle raised
BORDER  = "#28344a"
TEXT    = "#e8eef7"
MUTED   = "#8a99b0"
ACCENT  = "#56c8ff"   # frost cyan
ACCENT_HV = "#7ad6ff"
ACCENT_DK = "#0c2b3d"
GREEN   = "#3ddc97"
TRACK_OFF = "#33415a"
KNOB    = "#f2f8ff"


class Toggle(tk.Canvas):
    """A simple modern on/off switch."""
    def __init__(self, master, value=False, command=None):
        super().__init__(master, width=48, height=26, highlightthickness=0, bd=0, bg=PANEL)
        self.command = command
        self.value = value
        self.bind("<Button-1>", self._click)
        self._draw()

    def _draw(self):
        self.delete("all")
        track = ACCENT if self.value else TRACK_OFF
        self.create_oval(2, 3, 23, 24, fill=track, outline=track)
        self.create_oval(25, 3, 46, 24, fill=track, outline=track)
        self.create_rectangle(12, 3, 36, 24, fill=track, outline=track)
        kx = 27 if self.value else 5
        self.create_oval(kx, 5, kx + 16, 21, fill=KNOB, outline=KNOB)

    def _click(self, _e):
        self.set(not self.value)
        if self.command:
            self.command(self.value)

    def set(self, v):
        self.value = bool(v)
        self._draw()


def flat_button(parent, text, command, primary=False):
    bg = ACCENT if primary else PANEL2
    fg = "#062233" if primary else TEXT
    hv = ACCENT_HV if primary else BORDER
    b = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2",
                 font=("Segoe UI", 10, "bold" if primary else "normal"),
                 padx=14, pady=8)
    b.bind("<Button-1>", lambda e: command())
    b.bind("<Enter>", lambda e: b.config(bg=hv))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def main():
    _show_cursor()
    threading.Thread(target=_hook_thread, daemon=True).start()

    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        root.iconbitmap(resource_path("freezemouse.ico"))
    except Exception:
        pass
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    outer = tk.Frame(root, bg=BG, padx=18, pady=16)
    outer.pack(fill="both", expand=True)

    # ---- header ----
    header = tk.Frame(outer, bg=BG)
    header.pack(fill="x")
    tk.Label(header, text="\u2744", bg=BG, fg=ACCENT,
             font=("Segoe UI Symbol", 22)).pack(side="left", padx=(0, 10))
    htext = tk.Frame(header, bg=BG)
    htext.pack(side="left", anchor="w")
    tk.Label(htext, text=APP_NAME, bg=BG, fg=TEXT,
             font=("Segoe UI Semibold", 16)).pack(anchor="w")
    tk.Label(htext, text="Lock movement \u00b7 hide the cursor", bg=BG, fg=MUTED,
             font=("Segoe UI", 9)).pack(anchor="w")

    status = tk.Label(outer, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9, "bold"))
    status.pack(anchor="w", pady=(12, 8))

    # ---- toggle card ----
    card = tk.Frame(outer, bg=PANEL, highlightthickness=1,
                    highlightbackground=BORDER)
    card.pack(fill="x")

    freeze_toggle = None
    hide_toggle = None

    def update_status():
        f, h = state.freeze, state.hide
        if f and h:
            status.config(text="\u25cf  Movement locked & cursor hidden", fg=ACCENT)
        elif f:
            status.config(text="\u25cf  Movement locked", fg=ACCENT)
        elif h:
            status.config(text="\u25cf  Cursor hidden", fg=ACCENT)
        else:
            status.config(text="\u25cb  Mouse is normal", fg=MUTED)

    def apply_freeze():
        update_status()

    def apply_hide():
        if state.hide != state.hidden_now:
            _hide_cursor() if state.hide else _show_cursor()
            state.hidden_now = state.hide
        update_status()

    def toggle_row(title, subtitle, getter, setter, first=False):
        row = tk.Frame(card, bg=PANEL, padx=14, pady=12)
        row.pack(fill="x")
        if not first:
            sep = tk.Frame(card, bg=BORDER, height=1)
            sep.pack(fill="x")
            sep.lower()
        left = tk.Frame(row, bg=PANEL)
        left.pack(side="left", anchor="w")
        tk.Label(left, text=title, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11)).pack(anchor="w")
        tk.Label(left, text=subtitle, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w")
        sw = Toggle(row, value=getter(), command=lambda v: setter(v))
        sw.pack(side="right")
        return sw

    # order: build freeze row first, then a divider+hide row
    def set_freeze(v):
        state.freeze = bool(v)
        apply_freeze()

    def set_hide(v):
        state.hide = bool(v)
        apply_hide()

    freeze_toggle = toggle_row(
        "Disable movement", "Lock the cursor in place \u2014 clicks still work",
        lambda: state.freeze, set_freeze, first=True)
    tk.Frame(card, bg=BORDER, height=1).pack(fill="x")
    hide_toggle = toggle_row(
        "Hide cursor", "Make the mouse pointer invisible",
        lambda: state.hide, set_hide, first=True)

    # ---- hotkeys ----
    tk.Label(outer, text="HOTKEYS", bg=BG, fg=MUTED,
             font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(16, 6))

    hk_card = tk.Frame(outer, bg=PANEL, highlightthickness=1,
                       highlightbackground=BORDER)
    hk_card.pack(fill="x")

    hotkey_widgets = {}

    def hotkey_row(name, action, first=False):
        if not first:
            tk.Frame(hk_card, bg=BORDER, height=1).pack(fill="x")
        row = tk.Frame(hk_card, bg=PANEL, padx=14, pady=9)
        row.pack(fill="x")
        tk.Label(row, text=name, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        cap = tk.Label(row, text="", bg=PANEL2, fg=ACCENT,
                       font=("Consolas", 10, "bold"), padx=12, pady=3,
                       highlightthickness=1, highlightbackground=BORDER)
        cap.pack(side="right")
        chg = tk.Label(row, text="change", bg=PANEL, fg=MUTED, cursor="hand2",
                       font=("Segoe UI", 9, "underline"))
        chg.pack(side="right", padx=10)

        def start_capture(_e=None, a=action, c=cap):
            state.capture_target = a
            c.config(text="press a key", fg=MUTED)
        chg.bind("<Button-1>", start_capture)
        chg.bind("<Enter>", lambda e: chg.config(fg=ACCENT))
        chg.bind("<Leave>", lambda e: chg.config(fg=MUTED))
        hotkey_widgets[action] = cap

    hotkey_row("Disable movement", "freeze", first=True)
    hotkey_row("Hide cursor", "hide")
    hotkey_row("Restore everything", "restore")

    def refresh_hotkeys():
        vk_map = {"freeze": state.freeze_vk, "hide": state.hide_vk, "restore": state.restore_vk}
        for action, cap in hotkey_widgets.items():
            cap.config(text=vk_name(vk_map[action]), fg=ACCENT)

    refresh_hotkeys()

    # ---- footer ----
    footer = tk.Frame(outer, bg=BG)
    footer.pack(fill="x", pady=(16, 0))

    def restore_now():
        freeze_toggle.set(False)
        hide_toggle.set(False)
        state.freeze = False
        state.hide = False
        apply_hide()
        apply_freeze()

    flat_button(footer, "Restore everything now", restore_now,
                primary=True).pack(fill="x")
    tk.Label(footer,
             text="Free software \u00b7 v%s \u00b7 press your restore key anytime" % APP_VERSION,
             bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(pady=(10, 0))

    update_status()

    # ---- background poll for hotkey events ----
    def poll():
        if state.restore_requested:
            state.restore_requested = False
            restore_now()
        if state.freeze_toggle:
            state.freeze_toggle = False
            set_freeze(not state.freeze)
            freeze_toggle.set(state.freeze)
        if state.hide_toggle:
            state.hide_toggle = False
            set_hide(not state.hide)
            hide_toggle.set(state.hide)
        if state.capture_signal:
            state.capture_signal = False
            which = state.capture_which
            if which == "freeze":
                state.freeze_vk = state.captured_vk
            elif which == "hide":
                state.hide_vk = state.captured_vk
            elif which == "restore":
                state.restore_vk = state.captured_vk
            refresh_hotkeys()
        root.after(50, poll)

    poll()

    def cleanup():
        _show_cursor()
        if state.hook_thread_id:
            user32.PostThreadMessageW(state.hook_thread_id, WM_QUIT, 0, 0)

    atexit.register(cleanup)

    def on_close():
        cleanup()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.eval('tk::PlaceWindow . center')
    root.mainloop()


if __name__ == "__main__":
    main()
