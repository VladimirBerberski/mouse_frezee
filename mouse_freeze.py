#!/usr/bin/env python3
"""
Mouse Freeze - a small Windows utility.

Features:
  * Disable mouse MOVEMENT while keeping clicks working (low-level mouse hook).
  * Optionally HIDE the mouse cursor.
  * Three configurable global hotkeys:
        F3 - toggle "disable movement"
        F5 - toggle "hide cursor"
        F2 - restore everything to normal
    Any of the three can be rebound from the GUI.

All effects apply across every monitor.

Windows only. Standard library only (tkinter + ctypes). Run with:
    python mouse_freeze.py

Safety note: hiding the cursor uses SetSystemCursor, which changes the cursor
for the whole system. This program always restores the cursor when it exits
(normal close, Ctrl+C, or crash via atexit) and resets the system cursors on
startup, so if it ever dies while the cursor is hidden, just relaunch it.
"""

import sys
import atexit
import threading
import ctypes
from ctypes import wintypes

if not sys.platform.startswith("win"):
    raise SystemExit("This tool only runs on Windows.")

import tkinter as tk
from tkinter import ttk

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

# OCR_* system cursor ids that SetSystemCursor can replace.
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

# Function prototypes
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
# Shared state between the GUI thread and the hook thread
# ---------------------------------------------------------------------------
class State:
    def __init__(self):
        self.freeze = False              # read by the mouse hook
        self.hide = False                # hide-cursor feature on/off

        # Hotkeys (virtual-key codes)
        self.freeze_vk = 0x72            # F3
        self.hide_vk = 0x74              # F5
        self.restore_vk = 0x71           # F2

        # Hotkey-capture handshake
        self.capture_target = None       # "freeze" | "hide" | "restore" | None
        self.capture_which = None
        self.captured_vk = 0
        self.capture_signal = False

        # Requests raised by the hook thread, consumed by the GUI poll loop
        self.restore_requested = False
        self.freeze_toggle = False
        self.hide_toggle = False

        self.hidden_now = False          # actual cursor-hidden state
        self.hook_thread_id = 0


state = State()
mouse_hook = None
kbd_hook = None


# ---------------------------------------------------------------------------
# Hook procedures (run on the dedicated hook thread)
# ---------------------------------------------------------------------------
@HOOKPROC
def _mouse_proc(nCode, wParam, lParam):
    if nCode == HC_ACTION and state.freeze and wParam == WM_MOUSEMOVE:
        return 1  # block movement (clicks and wheel still pass through)
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
            return 1  # don't leak the bind-setting key into other apps
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
            if r in (0, -1):  # WM_QUIT or error
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
    and_mask = (ctypes.c_ubyte * n)(*([0xFF] * n))  # AND=1, XOR=0 -> transparent
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
# Virtual-key -> readable name
# ---------------------------------------------------------------------------
VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause", 0x14: "Caps Lock",
    0x1B: "Esc", 0x20: "Space", 0x21: "Page Up", 0x22: "Page Down", 0x23: "End",
    0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2D: "Insert", 0x2E: "Delete", 0x5B: "Left Win", 0x5C: "Right Win",
    0x90: "Num Lock", 0x91: "Scroll Lock",
    0xA0: "Left Shift", 0xA1: "Right Shift", 0xA2: "Left Ctrl", 0xA3: "Right Ctrl",
    0xA4: "Left Alt", 0xA5: "Right Alt",
}
for _i in range(0x30, 0x3A):           # 0-9
    VK_NAMES[_i] = chr(_i)
for _i in range(0x41, 0x5B):           # A-Z
    VK_NAMES[_i] = chr(_i)
for _i in range(1, 25):                # F1-F24
    VK_NAMES[0x6F + _i] = "F" + str(_i)
for _i in range(10):                   # Numpad 0-9
    VK_NAMES[0x60 + _i] = "Num " + str(_i)


def vk_name(vk):
    return VK_NAMES.get(vk, "Key 0x%02X" % vk)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def main():
    _show_cursor()  # clear any leftover hidden cursor from a previous crash
    threading.Thread(target=_hook_thread, daemon=True).start()

    root = tk.Tk()
    root.title("Mouse Freeze")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    freeze_var = tk.BooleanVar(value=False)
    hide_var = tk.BooleanVar(value=False)

    frm = ttk.Frame(root, padding=14)
    frm.grid()
    row = 0

    def reconcile_hide():
        want = bool(state.hide)
        if want != state.hidden_now:
            if want:
                _hide_cursor()
            else:
                _show_cursor()
            state.hidden_now = want

    def apply_freeze():
        state.freeze = bool(freeze_var.get())

    def apply_hide():
        state.hide = bool(hide_var.get())
        reconcile_hide()

    ttk.Checkbutton(frm, text="Disable mouse movement (clicks still work)",
                    variable=freeze_var, command=apply_freeze).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
    row += 1
    ttk.Checkbutton(frm, text="Hide mouse cursor",
                    variable=hide_var, command=apply_hide).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
    row += 1

    ttk.Separator(frm).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8)
    row += 1

    ttk.Label(frm, text="Hotkeys").grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    hotkey_labels = {}

    def make_hotkey_row(name, action):
        nonlocal row
        lbl = ttk.Label(frm, text="")
        lbl.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=1)

        def start_capture(a=action):
            state.capture_target = a
            btn.config(text="Press any key...")

        btn = ttk.Button(frm, text="Change", width=14, command=start_capture)
        btn.grid(row=row, column=1, sticky="e", pady=1)
        hotkey_labels[action] = (lbl, name, btn)
        row += 1

    make_hotkey_row("Disable movement", "freeze")
    make_hotkey_row("Hide cursor", "hide")
    make_hotkey_row("Restore all", "restore")

    def refresh_hotkey_labels():
        vk_map = {"freeze": state.freeze_vk, "hide": state.hide_vk, "restore": state.restore_vk}
        for action, (lbl, name, btn) in hotkey_labels.items():
            lbl.config(text="%s:  %s" % (name, vk_name(vk_map[action])))

    refresh_hotkey_labels()

    ttk.Separator(frm).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8)
    row += 1

    def restore_now():
        freeze_var.set(False)
        hide_var.set(False)
        apply_freeze()
        apply_hide()

    ttk.Button(frm, text="Restore now", command=restore_now).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
    row += 1

    ttk.Label(frm, foreground="#666",
              text="Tip: press the restore hotkey at any time to unfreeze\n"
                   "and show the cursor - even when it is hidden.").grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def poll():
        if state.restore_requested:
            state.restore_requested = False
            restore_now()
        if state.freeze_toggle:
            state.freeze_toggle = False
            freeze_var.set(not freeze_var.get())
            apply_freeze()
        if state.hide_toggle:
            state.hide_toggle = False
            hide_var.set(not hide_var.get())
            apply_hide()
        if state.capture_signal:
            state.capture_signal = False
            which = state.capture_which
            if which == "freeze":
                state.freeze_vk = state.captured_vk
            elif which == "hide":
                state.hide_vk = state.captured_vk
            elif which == "restore":
                state.restore_vk = state.captured_vk
            refresh_hotkey_labels()
            for action, (lbl, name, btn) in hotkey_labels.items():
                btn.config(text="Change")
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
    root.mainloop()


if __name__ == "__main__":
    main()
