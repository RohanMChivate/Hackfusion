"""Fullscreen, topmost lock window. Run as a child of the agent:
    python lock.py <agent_pid> <message>
The agent terminates it on unlock / session end; it exits by itself if the agent dies.
This is a soft lock: it covers every screen and keeps focus, but Ctrl+Alt+Del still works."""
import sys
import tkinter as tk

import psutil

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import ctypes

    import win32api
    import win32con

    # DPI-aware, so sizes are physical pixels and the window covers the screen exactly at any scaling
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()

CHECK_MS = 250  # re-take focus quickly so keystrokes never reach the windows underneath

parent_pid = int(sys.argv[1])
message = sys.argv[2] if len(sys.argv) > 2 else ""

root = tk.Tk()
root.title("InvigilAI lock")
root.overrideredirect(True)
if WINDOWS:
    # the whole virtual screen: every monitor
    x, y, width, height = (win32api.GetSystemMetrics(m) for m in (
        win32con.SM_XVIRTUALSCREEN, win32con.SM_YVIRTUALSCREEN,
        win32con.SM_CXVIRTUALSCREEN, win32con.SM_CYVIRTUALSCREEN))
    root.geometry(f"{width}x{height}+{x}+{y}")
else:
    root.attributes("-fullscreen", True)
root.configure(bg="#0a0a0a", cursor="arrow")
root.attributes("-topmost", True)
root.protocol("WM_DELETE_WINDOW", lambda: None)  # ignore Alt+F4

box = tk.Frame(root, bg="#171717", padx=48, pady=40)
box.place(relx=0.5, rely=0.5, anchor="center")
tk.Label(box, text="\U0001F512  Screen locked", bg="#171717", fg="#ffffff",
         font=("Segoe UI", 22, "bold")).pack()
if message:
    tk.Label(box, text=message, bg="#171717", fg="#d4d4d4", font=("Segoe UI", 13),
             wraplength=560, justify="center").pack(pady=(18, 0))
tk.Label(box, text="Your invigilator will unlock this computer.", bg="#171717", fg="#737373",
         font=("Segoe UI", 10)).pack(pady=(22, 0))

# swallow keys so nothing reaches the windows underneath
root.bind_all("<Key>", lambda e: "break")


def tick():
    if not psutil.pid_exists(parent_pid):
        root.destroy()
        return
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.after(CHECK_MS, tick)


root.update_idletasks()
root.focus_force()
root.after(CHECK_MS, tick)
root.mainloop()
