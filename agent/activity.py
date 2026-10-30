"""Activity signals: active window, browser URL, running processes."""
import contextlib
import shutil
import subprocess
import sys
import urllib.parse

import psutil

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import uiautomation as auto
    import win32gui
    import win32process

BROWSERS = {"chrome.exe", "msedge.exe"}
# Verified on this machine (English UI) for both Chrome and Edge: an Edit
# control at depth 8 under the top-level window. AutomationId differs between
# the two browsers, so match on Name only. Localised UIs will use other names.
ADDRESS_BAR_NAME = "Address and search bar"
ADDRESS_BAR_SEARCH_DEPTH = 10


def thread_init():
    """UI Automation needs COM initialised in the thread that uses it."""
    if WINDOWS:
        return auto.UIAutomationInitializerInThread()
    return contextlib.nullcontext()


def _process_name(pid):
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, ValueError):
        return None


def _browser_url(hwnd):
    """Address bar text of a Chrome/Edge window, or None if not found."""
    try:
        bar = auto.EditControl(
            searchFromControl=auto.ControlFromHandle(hwnd),
            searchDepth=ADDRESS_BAR_SEARCH_DEPTH,
            Name=ADDRESS_BAR_NAME,
        )
        if not bar.Exists(maxSearchSeconds=0):  # single attempt, no retry
            return None
        return bar.GetValuePattern().Value or None
    except Exception:
        return None


def _active_window_windows():
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None, None, None
    title = win32gui.GetWindowText(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    process = _process_name(pid)
    url = _browser_url(hwnd) if process and process.lower() in BROWSERS else None
    return title, process, url


def _xdotool(*args):
    try:
        out = subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=2)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _active_window_linux():
    if not shutil.which("xdotool"):
        return None, None, None
    title = _xdotool("getactivewindow", "getwindowname")
    pid = _xdotool("getactivewindow", "getwindowpid")
    process = _process_name(int(pid)) if pid and pid.isdigit() else None
    return title, process, None  # no URL reading on Linux


def domain_of(url):
    """Host part of an address bar value; None if it isn't a URL (e.g. typed search text)."""
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url  # Chrome hides the scheme
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None
    if not host or " " in host or ("." not in host and host != "localhost"):
        return None
    return host


def foreground_process():
    """Process name of the foreground window only (cheap: no UI Automation)."""
    if WINDOWS:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return _process_name(pid)
    pid = _xdotool("getactivewindow", "getwindowpid") if shutil.which("xdotool") else None
    return _process_name(int(pid)) if pid and pid.isdigit() else None


def active_window():
    title, process, url = _active_window_windows() if WINDOWS else _active_window_linux()
    return {"kind": "active", "title": title, "process": process,
            "url": url, "domain": domain_of(url)}


def process_list():
    names = set()
    for p in psutil.process_iter(["name"]):
        if p.info["name"]:
            names.add(p.info["name"])
    return {"kind": "processes", "names": sorted(names)}
