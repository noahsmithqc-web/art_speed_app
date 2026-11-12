"""The things that have to happen on this computer rather than in the browser.

A web page cannot hand back a real Windows path - a file input gives you a name and a copy of
the bytes, never `C:\\Utili_4k\\ART projects`. So the folder picker is a native dialog opened by
the engine, and its answer is a path the rest of the app can actually write to. "Open project
folder" is the same story in reverse: only this side can start File Explorer.

Both are best-effort. A machine with no display, no Tk, or a locked-down Explorer gets a clear
refusal rather than a traceback, because neither is worth stopping the app over: a folder can be
typed instead, and Summary works whether or not Explorer opens.

The dialog must not wedge the app (Q43). Tk is single-threaded and its own loop blocks, so the
route runs `pick_folder` on a worker thread and the server keeps answering everything else. One
dialog at a time: a second request while one is open is refused rather than queued, so a user
clicking twice does not end up with two pickers fighting over the same wizard step.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from art_speed import paths
from art_speed.log import get_logger

log = get_logger(__name__)


class DialogUnavailable(Exception):
    """No native dialog on this machine - say so and let them type a path instead."""


class DialogBusy(Exception):
    """A picker is already open. Only one at a time."""

    def __init__(self):
        super().__init__("A picker is already open. Finish with it first.")


_in_use = threading.Lock()


def _tk():
    """tkinter, or None. A soft import like C4's keyring: a Python built without Tk still runs
    the app, it just cannot open the picker."""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception as exc:                     # pragma: no cover - depends on the build
        log.warning("tkinter is not available, so the folder picker can't open (%s)", exc)
        return None
    return tkinter, filedialog


def can_pick() -> bool:
    """Whether the folder picker can open at all. The Settings screen uses this to decide
    between a Modify… button and a plain text box."""
    return _tk() is not None


def _ask(what: str, fallback: str, ask) -> str | None:
    """Run one native picker. `ask(filedialog, root)` opens it; the answer comes back as a
    path, or None when they cancelled. `what` names the picker in messages ("folder picker")
    and `fallback` says what to do instead when there is none."""
    tk = _tk()
    if tk is None:
        raise DialogUnavailable(f"This copy of Python has no {what} (tkinter is missing). "
                                f"{fallback}")
    tkinter, filedialog = tk

    if not _in_use.acquire(blocking=False):
        raise DialogBusy()
    try:
        root = tkinter.Tk()
        try:
            root.withdraw()
            # The browser is what they were looking at, so the dialog has to come up in front
            # of it - otherwise the app looks hung while the picker sits behind the window.
            root.attributes("-topmost", True)
            root.update()
            chosen = ask(filedialog, root)
        finally:
            root.destroy()
    except DialogUnavailable:
        raise
    except Exception as exc:                     # a headless session, no display, no window
        log.warning("the %s could not open (%s)", what, exc)
        raise DialogUnavailable(f"The {what} could not open on this computer. {fallback}") \
            from exc
    finally:
        _in_use.release()

    if not chosen:
        log.info("%s cancelled", what)
        return None
    path = str(Path(chosen))                     # forward slashes back to \ on Windows
    log.info("%s returned %s", what, path)
    return path


def pick_folder(title: str = "Select folder",
                start: str | os.PathLike | None = None) -> str | None:
    """Open the native folder picker. Returns the chosen folder, or None if they cancelled.

    Call this off the request thread - it blocks until the user answers.
    """
    return _ask("folder picker", "Type the folder instead.",
                lambda filedialog, root: filedialog.askdirectory(
                    parent=root, title=title,
                    initialdir=str(start) if start else None, mustexist=True))


def pick_file(title: str = "Select file", start: str | os.PathLike | None = None,
              types: tuple = (("Excel workbook", "*.xlsx"), ("All files", "*.*"))) -> str | None:
    """Open the native file picker (N5: "Add near miss data…"). Returns the chosen file, or
    None if they cancelled. The browser cannot hand the engine a path, so the picker is the
    engine's; a file dropped on the page arrives as bytes instead (NM29).

    Call this off the request thread - it blocks until the user answers.
    """
    return _ask("file picker", "Drop the file on the page instead.",
                lambda filedialog, root: filedialog.askopenfilename(
                    parent=root, title=title,
                    initialdir=str(start) if start else None, filetypes=types))


def _explorer_windows() -> set:
    """The handles of the File Explorer windows open now (Windows only)."""
    import ctypes
    from ctypes import wintypes
    user32, found = ctypes.windll.user32, set()
    name = ctypes.create_unicode_buffer(64)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        user32.GetClassNameW(hwnd, name, 64)
        if name.value in ("CabinetWClass", "ExploreWClass") and user32.IsWindowVisible(hwnd):
            found.add(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found


def _to_front(hwnd) -> None:
    """Make a window the active one. Windows refuses this to a background program (the taskbar
    button only flashes), so the call is made while attached to the thread that has the keyboard."""
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    fore = user32.GetForegroundWindow()
    theirs = user32.GetWindowThreadProcessId(fore, None) if fore else 0
    mine = kernel32.GetCurrentThreadId()
    attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)                       # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)


def allow_foreground() -> None:
    """Let the program about to be started take the keyboard: a key event from this process is what
    Windows asks for. 0xE8 is a key code no keyboard has, so nothing sees a keystroke."""
    if not paths.IS_WINDOWS:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.keybd_event(0xE8, 0, 0, 0)
        user32.keybd_event(0xE8, 0, 2, 0)                    # KEYEVENTF_KEYUP
        user32.AllowSetForegroundWindow(-1)                  # ASFW_ANY
    except Exception as exc:                                 # never the reason a folder does not open
        log.debug("allow_foreground: %s", exc)


def _front_when_open(before: set, tries: int = 30) -> None:
    """After Explorer was asked for a folder: wait for its window and make it the active one."""
    import time
    try:
        for _ in range(tries):
            time.sleep(0.1)
            new = _explorer_windows() - before
            if new:
                _to_front(next(iter(new)))
                return
    except Exception as exc:
        log.debug("front_when_open: %s", exc)


def reveal(target: str | os.PathLike) -> None:
    """Show a folder in File Explorer (Summary's "Open project folder").

    Never raises for a non-zero exit: Explorer returns 1 even when it opened the window.
    """
    path = Path(target)
    if not path.exists():
        raise DialogUnavailable(f"{path} is not there any more.")
    try:
        if paths.IS_WINDOWS:
            try:
                before = _explorer_windows()
            except Exception:                    # the folder still opens; it may only flash
                before = None
            allow_foreground()
            # explorer.exe with the folder always opens a window of its own at that folder. os.startfile
            # hands the choice to the shell, which may only raise an Explorer window that is already open.
            # One quoted string: Explorer splits an unquoted argument at commas.
            subprocess.Popen(f'explorer.exe "{path}"', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if before is not None:
                threading.Thread(target=_front_when_open, args=(before,), name="explorer to front", daemon=True).start()
        else:
            subprocess.Popen(["xdg-open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, AttributeError) as exc:
        log.warning("could not open %s in the file manager (%s)", path, exc)
        raise DialogUnavailable(f"Could not open {path} in File Explorer.") from exc
    log.info("opened %s in the file manager", path)
