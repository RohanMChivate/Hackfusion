"""Message from the invigilator: small topmost card with an OK button. Run as a child of the agent:
    python message.py <agent_pid> <from> <text>
Shown without taking keyboard focus, so typing in the exam isn't interrupted."""
import sys
import time
import tkinter as tk

import psutil

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import win32con
    import win32gui

CHECK_MS = 2000

parent_pid = int(sys.argv[1])
sender = sys.argv[2]
text = sys.argv[3]
# whatever the student is using now must keep focus
previous_foreground = win32gui.GetForegroundWindow() if WINDOWS else None

root = tk.Tk()
root.title("Message from invigilator")
root.withdraw()
root.overrideredirect(True)
root.configure(bg="#171717", padx=28, pady=22)
tk.Label(root, text=f"Message from {sender}  ·  {time.strftime('%H:%M')}", bg="#171717", fg="#34d399",
         font=("Segoe UI", 10, "bold")).pack(anchor="w")
tk.Label(root, text=text, bg="#171717", fg="#ffffff", font=("Segoe UI", 13),
         wraplength=420, justify="left").pack(anchor="w", pady=(10, 16))
tk.Button(root, text="OK", command=root.destroy, bg="#34d399", fg="#0a0a0a", activebackground="#6ee7b7",
          relief="flat", font=("Segoe UI", 10, "bold"), padx=22, pady=4, cursor="hand2").pack(anchor="e")
root.update_idletasks()
root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_reqwidth()) // 2}+60")

if WINDOWS:
    root.update_idletasks()
    hwnd = win32gui.GetParent(root.winfo_id())
    exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                           exstyle | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_TOPMOST)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def give_focus_back():
        # Tk can still activate the window once its event loop runs; hand focus straight back
        if win32gui.GetForegroundWindow() == hwnd and previous_foreground:
            try:
                win32gui.SetForegroundWindow(previous_foreground)
            except win32gui.error:
                pass

    give_focus_back()
    root.after(150, give_focus_back)
    root.after(500, give_focus_back)
else:
    root.deiconify()
    root.attributes("-topmost", True)


def tick():
    if not psutil.pid_exists(parent_pid):
        root.destroy()
        return
    root.after(CHECK_MS, tick)


root.after(CHECK_MS, tick)
root.mainloop()
