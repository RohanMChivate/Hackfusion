import base64
import io
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid

import imagehash
import mss
import requests
import socketio
from PIL import Image

import activity
import controls
import integrity

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_URL = os.environ.get("SERVER_URL", "https://127.0.0.1:8000")
# CA that signed the server's certificate (mkcert's public rootCA.pem). Without it, the system's trusted CAs are used.
SERVER_CA = os.environ.get("SERVER_CA", os.path.join(HERE, "certs", "rootCA.pem"))
DEVICE_ID = os.environ.get("DEVICE_ID", socket.gethostname())
# One-time token from the admin; only needed until the device is enrolled.
ENROLL_TOKEN = os.environ.get("ENROLL_TOKEN")
CREDENTIALS_PATH = os.environ.get("AGENT_CREDENTIALS", os.path.join(HERE, f"credentials-{DEVICE_ID}.json"))
IDLE_POLL = 0.5  # how often idle loops check whether a session started

CAPTURE_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 3.0
FORCE_SEND_AFTER = 10.0
HASH_THRESHOLD = 4
FRAME_WIDTH = 320
JPEG_QUALITY = 50
LOG_INTERVAL = 60.0

# focus mode: requested by the server when a dashboard opens this device
FOCUS_INTERVAL = 0.2  # up to 5 fps
FOCUS_FRAME_WIDTH = 960

ACTIVE_WINDOW_INTERVAL = 2.0
PROCESS_LIST_INTERVAL = 10.0

CLIPBOARD_POLL_INTERVAL = 0.5
KEYSTROKE_WINDOW = 5.0
MONITOR_CHECK_INTERVAL = 10.0

# New on every start; the server compares it to spot agent restarts.
INSTANCE_ID = uuid.uuid4().hex


def https_session():
    """HTTP session that verifies the server's certificate against SERVER_CA. Socket.IO uses its
    `verify` for both HTTPS polling and the wss:// WebSocket."""
    s = requests.Session()
    if os.path.exists(SERVER_CA):
        s.verify = SERVER_CA
    return s


http = https_session()
sio = socketio.Client(http_session=http)
focus = threading.Event()
# Set only between the server's session start and end. Nothing is captured or sent otherwise.
session = threading.Event()
banner = None  # subprocess.Popen of banner.py while a session runs
lock_window = None  # subprocess.Popen of lock.py while locked
message_windows = []  # subprocess.Popen of message.py, one per message
# bytes sent by all threads since the last per-minute log
bytes_lock = threading.Lock()
bytes_sent = 0


def show_banner(on):
    global banner
    if on and (banner is None or banner.poll() is not None):
        banner = subprocess.Popen([sys.executable, os.path.join(HERE, "banner.py"), str(os.getpid())])
    elif not on and banner is not None:
        banner.terminate()
        banner = None


def window_script(name, *args):
    return subprocess.Popen([sys.executable, os.path.join(HERE, name), str(os.getpid()), *map(str, args)])


def set_locked(on, message=""):
    global lock_window
    if on and (lock_window is None or lock_window.poll() is not None):
        lock_window = window_script("lock.py", message)
    elif not on and lock_window is not None:
        lock_window.terminate()
        lock_window = None


def close_control_windows():
    set_locked(False)
    for w in message_windows:
        if w.poll() is None:
            w.terminate()
    message_windows.clear()


def control_result(command, ok, detail, **extra):
    send(make_event("control", {"kind": "control_result", "command": command, "ok": ok,
                                "detail": detail, **extra}))
    print(f"[control] {command}: {'ok' if ok else 'failed'} - {detail}")


def run_command(data):
    """Dashboard controls, relayed by the server. Only obeyed during a session."""
    command = data.get("command")
    if not session.is_set():
        return
    if command == "lock":
        set_locked(True, data.get("message", ""))
        control_result("lock", True, "screen locked")
    elif command == "unlock":
        set_locked(False)
        control_result("unlock", True, "screen unlocked")
    elif command == "message":
        message_windows[:] = [w for w in message_windows if w.poll() is None]
        message_windows.append(window_script("message.py", data.get("from", "invigilator"), data.get("text", "")))
        control_result("message", True, "message shown")
    elif command == "kill":
        # may wait a few seconds for the process to exit: don't block the socket thread
        def kill():
            result = controls.kill_by_name(str(data.get("name", "")))
            control_result("kill", result["ok"], result["detail"], name=data.get("name"),
                           killed=result["killed"], refused=result["refused"])
        threading.Thread(target=kill, daemon=True).start()


@sio.on("control")
def on_control(event):
    data = event.get("data", {}) if isinstance(event, dict) else {}
    if data.get("session") == "start" and not session.is_set():
        session.set()
        show_banner(True)
        print(f"[session] start #{data.get('session_id')} profile={data.get('profile')!r}")
    elif data.get("session") == "end" and session.is_set():
        session.clear()
        focus.clear()
        show_banner(False)
        close_control_windows()
        print("[session] end")
    elif data.get("session") == "end":
        print("[session] none active; waiting")
    if "focus" in data:
        if data["focus"]:
            focus.set()
        else:
            focus.clear()
        print(f"focus {'on' if data['focus'] else 'off'}")
    if "command" in data:
        run_command(data)


@sio.event
def disconnect(reason):
    focus.clear()  # server re-sends focus on reconnect if still wanted


def make_event(type_, data):
    return {"device_id": DEVICE_ID, "type": type_, "data": data, "ts": int(time.time())}


def primary_monitor(sct):
    for m in sct.monitors[1:]:
        if m.get("is_primary"):
            return m
    return sct.monitors[1]


def capture(sct, monitor, width):
    shot = sct.grab(monitor)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    height = round(img.height * width / img.width)
    return img.resize((width, height))


def encode_jpeg(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def send(event):
    """Emit an event; return bytes sent (JSON size), or 0 if not connected or no session."""
    global bytes_sent
    if not session.is_set() or not sio.connected:
        return 0
    sio.emit("event", event)
    n = len(json.dumps(event))
    with bytes_lock:
        bytes_sent += n
    return n


def take_bytes_sent():
    global bytes_sent
    with bytes_lock:
        n, bytes_sent = bytes_sent, 0
    return n


def activity_loop():
    """Active window every 2s, process list every 10s. Runs in its own thread."""
    with activity.thread_init():
        last_window = None
        last_processes = 0.0
        while True:
            if not session.is_set():
                last_window, last_processes = None, 0.0  # report fresh state when a session starts
                time.sleep(IDLE_POLL)
                continue
            tick = time.monotonic()
            try:
                window = activity.active_window()
                send(make_event("window", window))
                key = (window["process"], window["title"], window["url"])
                if key != last_window:
                    print(f"[window] {window['process']} | {window['title']!r} | "
                          f"url={window['url']!r} domain={window['domain']!r}")
                    last_window = key
                if tick - last_processes >= PROCESS_LIST_INTERVAL:
                    procs = activity.process_list()
                    send(make_event("window", procs))
                    print(f"[processes] {len(procs['names'])} names")
                    last_processes = tick
            except Exception as e:  # keep the thread alive; next pass may succeed
                print(f"[activity] error: {e!r}")
            time.sleep(max(0.0, ACTIVE_WINDOW_INTERVAL - (time.monotonic() - tick)))


def signal(data):
    return send(make_event("signal", data))


def integrity_loop():
    """Keystroke counts every 5s, clipboard length on change, paste bursts, monitor count every 10s."""
    keys = integrity.KeyCounter()
    clipboard = None
    while True:
        if not session.is_set():
            if clipboard is not None:  # session just ended: stop listening to the keyboard
                keys.stop()
                clipboard = None
            time.sleep(IDLE_POLL)
            continue
        if clipboard is None:  # session just started
            keys.start()
            clipboard = integrity.ClipboardWatcher()  # baseline: earlier clipboard changes don't count
            last_keys = time.monotonic()
            last_monitor_check = 0.0
            last_monitors = None
        tick = time.monotonic()
        try:
            length = clipboard.changed_length()
            if length is not None:
                recent = keys.count_last(KEYSTROKE_WINDOW)
                process = activity.foreground_process()
                signal({"kind": "clipboard", "length": length})
                print(f"[clipboard] length={length} keystrokes_last_5s={recent} app={process}")
                if integrity.is_paste_burst(length, recent, process):
                    signal({"kind": "paste_burst", "chars": length,
                            "keystrokes_last_5s": recent, "app": process})
                    print(f"[paste_burst] chars={length} keystrokes_last_5s={recent} app={process}")

            if tick - last_keys >= KEYSTROKE_WINDOW:
                n = keys.take_window_count()
                signal({"kind": "keystrokes", "count": n, "window_s": KEYSTROKE_WINDOW})
                if n:
                    print(f"[keystrokes] {n} in last {KEYSTROKE_WINDOW:.0f}s")
                last_keys = tick

            if tick - last_monitor_check >= MONITOR_CHECK_INTERVAL:
                count = integrity.monitor_count()
                signal({"kind": "monitors", "count": count})
                if count != last_monitors:
                    print(f"[monitors] {count}")
                    last_monitors = count
                last_monitor_check = tick
        except Exception as e:  # keep the thread alive; next pass may succeed
            print(f"[integrity] error: {e!r}")
        time.sleep(max(0.0, CLIPBOARD_POLL_INTERVAL - (time.monotonic() - tick)))


def screen_event(img, focused):
    return make_event("screen", {
        "image": encode_jpeg(img), "width": img.width, "height": img.height,
        "focus": focused,
    })


def device_secret():
    """Stored device secret, enrolling first with ENROLL_TOKEN if this device has none."""
    if os.path.exists(CREDENTIALS_PATH):
        with open(CREDENTIALS_PATH) as f:
            creds = json.load(f)
        if creds.get("device_id") == DEVICE_ID:
            return creds["device_secret"]
    if not ENROLL_TOKEN:
        sys.exit(f"device {DEVICE_ID} is not enrolled: set ENROLL_TOKEN to a token from the admin")
    r = http.post(f"{SERVER_URL}/enroll", json={"device_id": DEVICE_ID, "token": ENROLL_TOKEN}, timeout=10)
    if r.status_code != 200:
        sys.exit(f"enrollment failed: HTTP {r.status_code} {r.text}")
    creds = r.json()
    with open(CREDENTIALS_PATH, "w") as f:
        json.dump(creds, f)
    print(f"enrolled as {DEVICE_ID}; credentials saved to {CREDENTIALS_PATH}")
    return creds["device_secret"]


def main():
    # window titles can contain any Unicode; a cp1252 log file must not crash us
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    secret = device_secret()
    try:
        sio.connect(SERVER_URL, auth={
            "role": "agent", "device_id": DEVICE_ID, "device_secret": secret,
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "instance": INSTANCE_ID,
        })
    except socketio.exceptions.ConnectionError as e:
        sys.exit(f"server refused connection: {e}")
    print(f"connected to {SERVER_URL} as {DEVICE_ID} (instance {INSTANCE_ID}) via {sio.transport()}"
          f", CA {http.verify if isinstance(http.verify, str) else 'system store'}; idle until a session starts")
    threading.Thread(target=activity_loop, daemon=True).start()
    threading.Thread(target=integrity_loop, daemon=True).start()

    last_hash = None
    last_sent = 0.0
    last_heartbeat = 0.0
    window_start = time.monotonic()
    sent = skipped = focus_sent = 0

    with mss.MSS() as sct:
        monitor = primary_monitor(sct)
        while True:
            if not session.is_set():
                last_hash, last_heartbeat = None, 0.0  # first frame + heartbeat go out at session start
                time.sleep(IDLE_POLL)
                continue
            tick = time.monotonic()
            focused = focus.is_set()

            if focused:
                # no change-skip in focus mode
                n = send(screen_event(capture(sct, monitor, FOCUS_FRAME_WIDTH), True))
                if n:
                    focus_sent += 1
                last_hash = None  # send a fresh thumbnail as soon as focus ends
            else:
                img = capture(sct, monitor, FRAME_WIDTH)
                h = imagehash.dhash(img)
                distance = None if last_hash is None else h - last_hash
                if distance is None or distance > HASH_THRESHOLD or tick - last_sent >= FORCE_SEND_AFTER:
                    n = send(screen_event(img, False))
                    if n:
                        last_hash, last_sent = h, tick
                        sent += 1
                else:
                    skipped += 1

            if tick - last_heartbeat >= HEARTBEAT_INTERVAL:
                if send(make_event("heartbeat", {})):
                    last_heartbeat = tick

            if tick - window_start >= LOG_INTERVAL:
                print(f"[last {tick - window_start:.0f}s] frames sent={sent} skipped={skipped} "
                      f"focus frames={focus_sent} bytes sent={take_bytes_sent()}")
                window_start = tick
                sent = skipped = focus_sent = 0

            interval = FOCUS_INTERVAL if focused else CAPTURE_INTERVAL
            time.sleep(max(0.0, interval - (time.monotonic() - tick)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        show_banner(False)
        close_control_windows()
        sio.disconnect()
