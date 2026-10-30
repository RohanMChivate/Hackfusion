"""Small always-on-top "Exam monitoring ON" banner. Run as a child process of the agent:
    python banner.py <agent_pid>
Exits by itself if the agent process is gone. Never takes keyboard focus."""
import sys
import tkinter as tk

import psutil

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import win32con
    import win32gui

CHECK_MS = 2000

parent_pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
# whatever the student is using now must keep focus
previous_foreground = win32gui.GetForegroundWindow() if WINDOWS else None

root = tk.Tk()
root.title("InvigilAI banner")
root.withdraw()                      # build hidden; shown below without activation
root.overrideredirect(True)          # no title bar / close button
root.attributes("-alpha", 0.92)
root.configure(bg="#0a0a0a")
tk.Label(
    root, text="●  Exam monitoring ON", bg="#0a0a0a", fg="#34d399",
    font=("Segoe UI", 10, "bold"), padx=16, pady=6,
).pack()
root.update_idletasks()
root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_reqwidth()) // 2}+8")

if WINDOWS:
    root.update_idletasks()
    hwnd = win32gui.GetParent(root.winfo_id())  # Tk's toplevel wrapper window
    exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                           exstyle | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_TOPMOST)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

    def keep_on_top():
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    keep_on_top()
    # belt and braces: if we still ended up in front, hand focus straight back
    if win32gui.GetForegroundWindow() == hwnd and previous_foreground:
        try:
            win32gui.SetForegroundWindow(previous_foreground)
        except win32gui.error:
            pass
else:
    root.deiconify()
    root.attributes("-topmost", True)

    def keep_on_top():
        root.attributes("-topmost", True)


def tick():
    if parent_pid and not psutil.pid_exists(parent_pid):
        root.destroy()
        return
    keep_on_top()  # re-assert in case another topmost window covered it
    root.after(CHECK_MS, tick)


root.after(CHECK_MS, tick)
root.mainloop()
