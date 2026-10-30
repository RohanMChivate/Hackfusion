"""Integrity signals: keystroke counts, clipboard length, paste bursts, monitor count.

Privacy: which keys were pressed is never seen (the key listener callback takes
no arguments, so pynput does not pass the key), and clipboard text is only
measured for length, never stored, logged or sent.
"""
import collections
import hashlib
import sys
import threading
import time

import mss
import pyperclip
from pynput import keyboard

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import win32clipboard

KEY_WINDOW = 5.0
PASTE_MIN_CHARS = 150
PASTE_MAX_KEYSTROKES = 10
EDITORS_AND_BROWSERS = {
    "code.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "notepad++.exe",
    "sublime_text.exe", "notepad.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    # Linux process names
    "code", "chrome", "chromium", "firefox", "gedit",
}


class KeyCounter:
    """Counts key presses. Never sees which key."""

    def __init__(self):
        self._lock = threading.Lock()
        self._times = collections.deque()
        self._window_count = 0
        self._listener = None

    def _on_press(self):
        now = time.monotonic()
        with self._lock:
            self._times.append(now)
            self._window_count += 1

    def start(self):
        # a stopped pynput listener (a thread) can't be restarted, so make a new one each time
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        with self._lock:
            self._times.clear()
            self._window_count = 0

    def take_window_count(self):
        """Presses since the previous call."""
        with self._lock:
            n, self._window_count = self._window_count, 0
        return n

    def count_last(self, seconds=KEY_WINDOW):
        cutoff = time.monotonic() - seconds
        with self._lock:
            while self._times and self._times[0] < cutoff:
                self._times.popleft()
            return len(self._times)


def _clipboard_length():
    """Length of the clipboard text ('' for non-text), or None if it can't be read right now."""
    try:
        text = pyperclip.paste()
    except Exception:
        return None
    return len(text) if isinstance(text, str) else 0


class ClipboardWatcher:
    def __init__(self):
        self._last = None  # Windows: sequence number; elsewhere: digest of the text

    def changed_length(self):
        """Clipboard text length if the clipboard changed since the last call, else None.
        The first call only records a baseline."""
        if WINDOWS:
            # Sequence number changes on every clipboard write; no need to read content to compare.
            seq = win32clipboard.GetClipboardSequenceNumber()
            if seq == self._last:
                return None
            baseline = self._last is None
            length = _clipboard_length()
            if length is None:
                return None  # busy; try again next poll
            self._last = seq
            return None if baseline else length
        try:
            text = pyperclip.paste()
        except Exception:
            return None
        digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()
        if digest == self._last:
            return None
        baseline = self._last is None
        self._last = digest
        return None if baseline else len(text)


def is_paste_burst(chars, keystrokes_last_5s, process):
    return (chars > PASTE_MIN_CHARS and keystrokes_last_5s < PASTE_MAX_KEYSTROKES
            and (process or "").lower() in EDITORS_AND_BROWSERS)


def monitor_count():
    # A fresh MSS each time: MSS caches .monitors per instance, which would hide hot-plugged screens.
    with mss.MSS() as sct:
        return len(sct.monitors) - 1  # monitors[0] is the union of all screens
