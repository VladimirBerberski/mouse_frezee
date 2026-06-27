# FreezeMouse ❄

**Lock your mouse in place and/or hide the cursor — clicks still work.**
A tiny, free Windows utility for gamers, multi-monitor setups, screen
cleaning, presentations, or anywhere a stray cursor gets in the way.

No installer, no telemetry, no dependencies. Just run it.

---

## Features

- **Disable movement** — freezes the cursor where it is; left/right/middle
  clicks and the scroll wheel keep working normally.
- **Hide cursor** — makes the pointer invisible system-wide.
- **Global hotkeys** that work even while a game has focus, all rebindable
  from inside the app.
- **Always recoverable** — a restore hotkey and button bring the mouse back
  instantly, and the cursor is automatically restored on exit (or if the app
  is ever closed unexpectedly).

## Default hotkeys

| Action               | Key |
|----------------------|-----|
| Disable movement     | F3  |
| Hide cursor          | F5  |
| Restore everything   | F2  |

Click **change** next to any hotkey and press a new key to rebind it.

## Usage

1. Download and run `FreezeMouse.exe` (or `python freezemouse.py`).
2. Flip a switch, or use the hotkeys.
3. Press your **Restore** key (F2 by default) at any time to bring the mouse
   fully back — this works even while the cursor is hidden.

> If you want movement/hide to apply while a game or app running **as
> administrator** has focus, run FreezeMouse as administrator too.

## Build from source

You only need Python 3 (the standard install includes everything else).

```
build.bat
```

Or manually:

```
pip install pyinstaller
pyinstaller --onefile --noconsole --name FreezeMouse --icon freezemouse.ico --add-data "freezemouse.ico;." freezemouse.py
```

The finished executable lands in `dist\FreezeMouse.exe`.

### Heads-up on antivirus / SmartScreen

Because FreezeMouse uses global mouse/keyboard hooks and ships as an unsigned
single-file build, some antivirus engines or Windows SmartScreen may show a
warning — a common false positive for this kind of tool, not a sign anything
is wrong. If it gets blocked, add an exclusion, or build with `--onedir`
instead of `--onefile` (a folder build that AV tends to trust more).

## How it works

FreezeMouse installs low-level Windows hooks. Movement is disabled by
swallowing mouse-move events (clicks pass through untouched). The cursor is
hidden by temporarily swapping the system cursors for a transparent one, which
is reverted whenever you restore or close the app.

## License

MIT — free to use, modify, and share. See `LICENSE`.
