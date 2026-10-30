import asyncio
import hmac
import os
import secrets
import time

import socketio
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import ai
import auth
import db
from risk import RiskScores
from risk import level as risk_level
from rules import Evaluator

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PUT"],
                   allow_headers=["Authorization", "Content-Type"])

# device_id -> {device_id, hostname, os, last_seen}
devices = {}
# socket sid -> device_id (agents only)
sid_to_device = {}
# device_id -> socket sid (agents only)
device_to_sid = {}
# device_id -> agent instance id of the current connection
device_instance = {}
# socket sid -> {"username", "role"} (dashboards only)
sid_to_user = {}
# device_id -> set of dashboard sids currently focused on it
focus_watchers = {}

EVENT_TYPES = {"screen", "window", "alert", "signal", "control", "heartbeat"}
SCREENSHOT_WAIT = 1.5  # seconds to wait for a fresh frame to attach to an alert
HEARTBEAT_CHECK_INTERVAL = 2
CAN_RUN_SESSIONS = ("admin", "teacher")
CAN_EDIT_PROFILES = ("admin", "teacher")
# lock / unlock / message: every role. Killing processes is more drastic: teachers and admins only.
CONTROL_ROLES = {"lock": auth.ROLES, "unlock": auth.ROLES, "message": auth.ROLES, "kill": ("admin", "teacher")}
MAX_MESSAGE_LEN = 500

conn = db.connect()
auth.seed_users(conn)
# The active exam session (a row of the sessions table) or None. Resumed after a server restart.
session = db.active_session(conn)
print(f"[server] database {db.DB_PATH}; active session: "
      f"{'#%d %r' % (session['id'], session['profile']) if session else 'none'}")

# Per-session state: rules dedup/state and risk scores start fresh with each session.
evaluator = Evaluator()
risk = RiskScores()
# device_id -> latest base64 JPEG, and an asyncio.Event set when the next frame arrives
latest_frame = {}
frame_arrived = {}
alert_tasks = set()
# device_id -> time.time() of the last heartbeat during the session; kept after disconnect
last_heartbeat = {}
# events that arrived with no session active (agents should send none): device_id -> count
dropped_events = {}
# device_id -> lock message, for devices an invigilator has locked (re-sent if the agent reconnects)
locked = {}
# every device that was connected at some point during the current session (for its report)
session_devices = set()
if auth.DEV_NO_AUTH:
    print("[auth] WARNING: DEV_NO_AUTH=true - login is OFF, every request and dashboard is treated as admin")
print(f"[ai] alert review {'ON, model ' + ai.MODEL if ai.ENABLED else 'OFF (no ANTHROPIC_API_KEY in server/.env)'}")


def spawn(coro):
    task = asyncio.create_task(coro)
    alert_tasks.add(task)  # keep a reference until done (asyncio docs)
    task.add_done_callback(alert_tasks.discard)


def spawn_alert(device_id, violation, ts):
    if session:
        spawn(raise_alert(device_id, violation, session, ts))


async def heartbeat_watchdog():
    while True:
        await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL)
        if not session:
            continue
        now = time.time()
        for device_id, last in list(last_heartbeat.items()):
            for violation in evaluator.check_heartbeat(device_id, last, now):
                spawn_alert(device_id, violation, int(now))


async def on_startup():
    spawn(heartbeat_watchdog())


async def raise_alert(device_id, violation, sess, ts):
    """Attach the screen at the time (next frame within SCREENSHOT_WAIT, else latest), store, push."""
    waiter = frame_arrived.setdefault(device_id, asyncio.Event())
    try:
        await asyncio.wait_for(waiter.wait(), SCREENSHOT_WAIT)
    except asyncio.TimeoutError:
        pass
    alert = {
        "device_id": device_id, **violation, "profile": sess["profile"],
        "screenshot_at_time": latest_frame.get(device_id), "ts": ts, "session_id": sess["id"],
    }
    alert["id"] = db.insert_alert(conn, alert)
    review = ai.ENABLED and bool(alert["screenshot_at_time"])
    db.set_alert_ai(conn, alert["id"], "pending" if review else "skipped")
    before, after = risk.add(device_id, alert["rule"], time.time())
    print(f"[alert] #{alert['id']} {device_id} {alert['severity']} {alert['rule']}: {alert['detail']}"
          f" (screenshot={'yes' if alert['screenshot_at_time'] else 'no'}) risk {before:.1f} -> {after:.1f}")
    data = {k: v for k, v in alert.items() if k != "device_id"}
    data["risk"] = round(after, 1)
    data["ai_status"] = "pending" if review else "skipped"
    await sio.emit("event", {"device_id": device_id, "type": "alert", "data": data, "ts": ts},
                   room="dashboard")
    if review:
        spawn(review_alert(alert))


async def review_alert(alert):
    """Ask the vision model whether the alert's screenshot shows the violation; store and push the verdict."""
    started = time.monotonic()
    try:
        v = await ai.review_screenshot(alert["screenshot_at_time"], alert["rule"], alert["detail"], alert["profile"])
        db.set_alert_ai(conn, alert["id"], "done", v.violation, v.type, v.confidence, v.reason)
        print(f"[ai] alert #{alert['id']} violation={v.violation} type={v.type} confidence={v.confidence:.2f}"
              f" ({time.monotonic() - started:.1f}s): {v.reason}")
    except ai.AIError as e:
        db.set_alert_ai(conn, alert["id"], "error", reason=str(e))
        print(f"[ai] alert #{alert['id']} review failed: {e}")
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert["id"],)).fetchone()
    await sio.emit("alert_ai", {"id": alert["id"], "device_id": alert["device_id"], **db.ai_fields(dict(row))},
                   room="dashboard")


def control_event(device_id, data):
    return {"device_id": device_id, "type": "control", "data": data, "ts": int(time.time())}


def session_control(device_id):
    if session:
        return control_event(device_id, {"session": "start", "session_id": session["id"],
                                         "profile": session["profile"]})
    return control_event(device_id, {"session": "end"})


async def send_focus(device_id, on):
    sid = device_to_sid.get(device_id)
    if sid:
        await sio.emit("control", control_event(device_id, {"focus": on}), to=sid)
        print(f"[focus] device_id={device_id} on={on}")


def session_public():
    return {"id": session["id"], "profile": session["profile"], "started_by": session["started_by"],
            "started_at": session["started_at"]} if session else None


@sio.event
async def connect(sid, environ, auth_data):
    auth_data = auth_data or {}
    role = auth_data.get("role")
    if role == "dashboard":
        user = auth.decode_token(auth_data.get("token"))
        if user is None:
            print(f"[connect] rejected dashboard sid={sid}: no valid login")
            return False
        sid_to_user[sid] = user
        await sio.enter_room(sid, "dashboard")
        print(f"[connect] dashboard sid={sid} user={user['username']} ({user['role']})")
        return True
    if role == "agent" and isinstance(auth_data.get("device_id"), str):
        device_id = auth_data["device_id"]
        stored = db.device_secret_hash(conn, device_id)
        secret = auth_data.get("device_secret")
        if stored is None or not isinstance(secret, str) or not hmac.compare_digest(auth.sha256(secret), stored):
            print(f"[connect] rejected agent device_id={device_id}: "
                  f"{'not enrolled' if stored is None else 'wrong device secret'}")
            return False
        devices[device_id] = {
            "device_id": device_id,
            "hostname": auth_data.get("hostname", ""),
            "os": auth_data.get("os", ""),
            "last_seen": int(time.time()),
        }
        sid_to_device[sid] = device_id
        device_to_sid[device_id] = sid
        instance = auth_data.get("instance")
        device_instance[device_id] = instance
        print(f"[connect] agent device_id={device_id} sid={sid} instance={instance}")
        if session:
            session_devices.add(device_id)
            last_heartbeat[device_id] = time.time()
            for violation in evaluator.on_connect(device_id, instance, time.time()):
                spawn_alert(device_id, violation, int(time.time()))
        # tell the agent whether to monitor; sent after connect completes
        spawn(sio.emit("control", session_control(device_id), to=sid))
        if session and focus_watchers.get(device_id):
            spawn(send_focus(device_id, True))
        if session and device_id in locked:  # still locked: an agent restart must not unlock it
            spawn(sio.emit("control", control_event(device_id, {"command": "lock", "message": locked[device_id]}), to=sid))
        return True
    print(f"[connect] rejected sid={sid}: unknown role")
    return False


@sio.event
async def disconnect(sid, reason):
    device_id = sid_to_device.pop(sid, None)
    if device_id:
        devices.pop(device_id, None)
        device_to_sid.pop(device_id, None)
        print(f"[disconnect] agent device_id={device_id} reason={reason}")
        return
    sid_to_user.pop(sid, None)
    # a dashboard left: release any focus it held
    for device_id, watchers in focus_watchers.items():
        if sid in watchers:
            watchers.discard(sid)
            if not watchers:
                await send_focus(device_id, False)
    print(f"[disconnect] sid={sid} reason={reason}")


@sio.event
async def event(sid, data):
    device_id = sid_to_device.get(sid)
    if device_id is None:
        return  # only agents may send events
    if session is None:
        dropped_events[device_id] = dropped_events.get(device_id, 0) + 1
        print(f"[event] dropped from {device_id}: no active session")
        return
    if (
        not isinstance(data, dict)
        or data.get("device_id") != device_id
        or data.get("type") not in EVENT_TYPES
        or not isinstance(data.get("data"), dict)
        or not isinstance(data.get("ts"), int)
    ):
        print(f"[event] invalid from {device_id}: {data}")
        return
    devices[device_id]["last_seen"] = int(time.time())
    await sio.emit("event", data, room="dashboard")

    if data["type"] == "screen" and isinstance(data["data"].get("image"), str):
        latest_frame[device_id] = data["data"]["image"]
        frame_arrived.pop(device_id, asyncio.Event()).set()
    elif data["type"] == "heartbeat":
        last_heartbeat[device_id] = time.time()
    elif data["type"] == "control" and data["data"].get("kind") == "control_result":
        result = data["data"]
        db.audit(conn, f"device:{device_id}", "agent", "control_result", device_id,
                 {k: v for k, v in result.items() if k != "kind"})
    elif data["type"] in ("window", "signal"):
        profile = db.get_profile(conn, session["profile"])
        for violation in evaluator.evaluate(device_id, data["type"], data["data"], profile, int(time.time())):
            spawn_alert(device_id, violation, data["ts"])


@sio.event
async def focus(sid, data):
    """Dashboard asks to start/stop focus on one device: {device_id, on}."""
    user = sid_to_user.get(sid)
    if user is None or not isinstance(data, dict):
        return  # only logged-in dashboards may request focus
    device_id = data.get("device_id")
    if not isinstance(device_id, str):
        return
    if data.get("on"):
        db.audit(conn, user["username"], user["role"], "view_screen", device_id)
    watchers = focus_watchers.setdefault(device_id, set())
    was_on = bool(watchers)
    if data.get("on"):
        watchers.add(sid)
    else:
        watchers.discard(sid)
    if bool(watchers) != was_on:
        await send_focus(device_id, bool(watchers))


# --- auth ------------------------------------------------------------------

class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
async def login(body: LoginBody):
    user = db.get_user(conn, body.username)
    if user is None or not auth.verify_password(body.password, user["password_hash"]):
        db.audit(conn, body.username, "-", "login_failed")
        raise HTTPException(401, "wrong username or password")
    db.audit(conn, user["username"], user["role"], "login")
    return {"token": auth.issue_token(user["username"], user["role"]),
            "username": user["username"], "role": user["role"]}


@app.get("/auth/mode")
async def auth_mode():
    """Tells the dashboard whether to skip its login screen (DEV_NO_AUTH)."""
    return {"dev_no_auth": auth.DEV_NO_AUTH}


@app.get("/me")
async def me(user=Depends(auth.current_user)):
    return user


# --- read-only views (any role) ---------------------------------------------

@app.get("/devices")
async def get_devices(_=Depends(auth.current_user)):
    return [{**d, "locked": d["device_id"] in locked} for d in devices.values()]


@app.get("/profiles")
async def get_profiles(_=Depends(auth.current_user)):
    active = session["profile"] if session else None
    return [{**p, "active": p["name"] == active} for p in db.list_profiles(conn)]


@app.get("/risk")
async def get_risk(_=Depends(auth.current_user)):
    return risk.snapshot(time.time())


@app.get("/alerts")
async def get_alerts(limit: int = 100, _=Depends(auth.current_user)):
    if not 1 <= limit <= 1000:
        raise HTTPException(400, "limit must be 1..1000")
    return db.list_alerts(conn, limit)


@app.get("/session")
async def get_session(_=Depends(auth.current_user)):
    return {"session": session_public(), "dropped_events": dropped_events}


# --- profiles (admin, teacher) -------------------------------------------------

class ProfileUpdate(BaseModel):
    allowed_apps: list[str] | None = None
    blocked_apps: list[str] | None = None
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    max_switches_per_min: int | None = Field(default=None, ge=0)


@app.put("/profiles/{name}")
async def update_profile(name: str, body: ProfileUpdate, user=Depends(auth.require(*CAN_EDIT_PROFILES))):
    if db.get_profile(conn, name) is None:
        raise HTTPException(404, "no such profile")
    fields = body.model_dump(exclude_none=True)
    db.update_profile(conn, name, fields)
    db.audit(conn, user["username"], user["role"], "profile_update", name, fields)
    return db.get_profile(conn, name)


# --- exam sessions (admin, teacher) -------------------------------------------

class SessionStart(BaseModel):
    profile: str


@app.post("/session/start")
async def start_session(body: SessionStart, user=Depends(auth.require(*CAN_RUN_SESSIONS))):
    global session, evaluator, risk
    if session:
        raise HTTPException(409, f"session #{session['id']} is already running")
    if db.get_profile(conn, body.profile) is None:
        raise HTTPException(404, "no such profile")
    now = int(time.time())
    session_id = db.start_session(conn, body.profile, user["username"], now)
    session = db.active_session(conn)
    evaluator, risk = Evaluator(), RiskScores()
    last_heartbeat.clear()
    session_devices.clear()
    session_devices.update(device_to_sid)
    for device_id in device_to_sid:
        last_heartbeat[device_id] = time.time()
        evaluator.on_connect(device_id, device_instance.get(device_id), time.time())
    db.audit(conn, user["username"], user["role"], "session_start", f"session:{session_id}",
             {"profile": body.profile, "devices": sorted(device_to_sid)})
    for device_id, sid in device_to_sid.items():
        await sio.emit("control", session_control(device_id), to=sid)
    await sio.emit("session", session_public(), room="dashboard")
    print(f"[session] #{session_id} started by {user['username']} with profile {body.profile!r}; "
          f"told {len(device_to_sid)} agent(s)")
    return session_public()


@app.post("/session/end")
async def end_session(user=Depends(auth.require(*CAN_RUN_SESSIONS))):
    global session
    if not session:
        raise HTTPException(409, "no session is running")
    ended = session
    now = time.time()
    final = {d: risk.current(d, now) for d in session_devices | {r["device_id"] for r in risk.snapshot(now)}}
    db.save_session_results(conn, ended["id"], final)
    db.end_session(conn, ended["id"], user["username"], int(now))
    session = None
    last_heartbeat.clear()
    unlocked = sorted(locked)
    locked.clear()  # agents close their lock windows themselves on session end
    await sio.emit("locked", [], room="dashboard")
    db.audit(conn, user["username"], user["role"], "session_end", f"session:{ended['id']}",
             {"unlocked": unlocked} if unlocked else None)
    for device_id, sid in device_to_sid.items():
        await sio.emit("control", session_control(device_id), to=sid)
    await sio.emit("session", None, room="dashboard")
    print(f"[session] #{ended['id']} ended by {user['username']}; told {len(device_to_sid)} agent(s)")
    return {"ended": ended["id"]}


# --- controls (dashboard -> agent) ---------------------------------------------

class ControlBody(BaseModel):
    target: str = Field(min_length=1)  # a device_id, or "all" for every connected agent
    command: str
    message: str = Field(default="", max_length=MAX_MESSAGE_LEN)  # lock screen text, or the message to show
    name: str = Field(default="", max_length=260)  # process name for kill


@app.post("/control")
async def control(body: ControlBody, user=Depends(auth.current_user)):
    if body.command not in CONTROL_ROLES:
        raise HTTPException(400, f"unknown command {body.command!r}")
    if user["role"] not in CONTROL_ROLES[body.command]:
        raise HTTPException(403, f"role {user['role']!r} may not {body.command}")
    if not session:
        raise HTTPException(409, "no exam session is running")
    if body.command == "message" and not body.message.strip():
        raise HTTPException(400, "message is empty")
    if body.command == "kill" and not body.name.strip():
        raise HTTPException(400, "process name is empty")
    targets = sorted(device_to_sid) if body.target == "all" else [body.target]
    missing = [d for d in targets if d not in device_to_sid]
    if missing:
        raise HTTPException(404, f"not connected: {', '.join(missing)}")

    data = {"command": body.command}
    detail = {}
    if body.command == "lock":
        data["message"] = detail["message"] = body.message.strip()
    elif body.command == "message":
        data["text"] = detail["message"] = body.message.strip()
        data["from"] = user["username"]
    elif body.command == "kill":
        data["name"] = detail["name"] = body.name.strip()
    db.audit(conn, user["username"], user["role"], f"control_{body.command}", body.target,
             {**detail, "devices": targets})

    for device_id in targets:
        if body.command == "lock":
            locked[device_id] = data["message"]
        elif body.command == "unlock":
            locked.pop(device_id, None)
        await sio.emit("control", control_event(device_id, data), to=device_to_sid[device_id])
    if body.command in ("lock", "unlock"):
        await sio.emit("locked", sorted(locked), room="dashboard")
    print(f"[control] {user['username']} ({user['role']}) {body.command} -> {', '.join(targets)} {detail or ''}")
    return {"sent_to": targets}


# --- reports (after a session ends; any role) -----------------------------------

@app.get("/sessions")
async def get_sessions(_=Depends(auth.current_user)):
    return db.list_sessions(conn)


def ended_session_or_error(session_id):
    sess = db.get_session(conn, session_id)
    if sess is None:
        raise HTTPException(404, "no such session")
    if sess["ended_at"] is None:
        raise HTTPException(409, "session is still running; reports are available after it ends")
    return sess


@app.get("/sessions/{session_id}/students")
async def get_session_students(session_id: int, _=Depends(auth.current_user)):
    ended_session_or_error(session_id)
    return [{**s, "level": risk_level(s["final_risk"])} for s in db.session_students(conn, session_id)]


def ai_label(a):
    if a.get("ai_status") == "done":
        verdict = "confirmed" if a["ai_violation"] else "likely false alarm"
        return f"{verdict} ({a['ai_confidence']:.0%}): {a['ai_reason']}"
    return {"pending": "not finished", "error": "review failed", "skipped": "not reviewed"}.get(a.get("ai_status"), "not reviewed")


@app.get("/sessions/{session_id}/students/{device_id}/report")
async def get_report(session_id: int, device_id: str, user=Depends(auth.current_user)):
    sess = ended_session_or_error(session_id)
    student = next((s for s in db.session_students(conn, session_id) if s["device_id"] == device_id), None)
    if student is None:
        raise HTTPException(404, "this device was not in the session")
    alerts = db.student_alerts(conn, session_id, device_id)
    level = risk_level(student["final_risk"])

    summary = db.get_summary(conn, session_id, device_id)
    summary_error = None
    if summary is None and ai.ENABLED:
        try:
            summary = await ai.summarize(device_id, sess["profile"], student["final_risk"], level, [
                {"time": time.strftime("%H:%M:%S", time.localtime(a["ts"])), "severity": a["severity"],
                 "rule": a["rule"], "detail": a["detail"], "ai": ai_label(a)} for a in alerts])
            db.save_summary(conn, session_id, device_id, summary)
            print(f"[ai] summary for {device_id} in session #{session_id}:\n{summary}")
        except ai.AIError as e:
            summary_error = str(e)
            print(f"[ai] summary for {device_id} in session #{session_id} failed: {e}")
    elif summary is None:
        summary_error = "AI is off (no ANTHROPIC_API_KEY in server/.env)"

    db.audit(conn, user["username"], user["role"], "view_report", device_id, {"session_id": session_id})
    return {
        "session": sess, "device_id": device_id, "final_risk": student["final_risk"], "level": level,
        "summary": summary, "summary_error": summary_error,
        "alerts": [{**a, **db.ai_fields(a)} for a in alerts],
    }


# --- device enrollment ---------------------------------------------------------

@app.post("/enrollment-tokens")
async def create_enrollment_token(user=Depends(auth.require("admin"))):
    token = secrets.token_urlsafe(24)
    token_id = db.insert_enrollment_token(conn, auth.sha256(token), user["username"], int(time.time()))
    db.audit(conn, user["username"], user["role"], "enrollment_token_create", f"token:{token_id}")
    return {"id": token_id, "token": token}  # shown once; only its hash is stored


class EnrollBody(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    token: str


@app.post("/enroll")
async def enroll(body: EnrollBody):
    secret = secrets.token_urlsafe(32)
    if not db.consume_enrollment_token(conn, auth.sha256(body.token), body.device_id,
                                       auth.sha256(secret), int(time.time())):
        db.audit(conn, f"device:{body.device_id}", "agent", "enroll_rejected", body.device_id)
        raise HTTPException(403, "invalid or already used enrollment token")
    db.audit(conn, f"device:{body.device_id}", "agent", "enroll", body.device_id)
    print(f"[enroll] device {body.device_id} enrolled")
    return {"device_id": body.device_id, "device_secret": secret}


# --- audit (admin) ---------------------------------------------------------

@app.get("/audit")
async def get_audit(limit: int = 200, _=Depends(auth.require("admin"))):
    if not 1 <= limit <= 1000:
        raise HTTPException(400, "limit must be 1..1000")
    return db.list_audit(conn, limit)


asgi_app = socketio.ASGIApp(sio, other_asgi_app=app, on_startup=on_startup)

if __name__ == "__main__":
    # TLS by default: mkcert-generated certs in server/certs, or SSL_CERTFILE / SSL_KEYFILE.
    # Same as: uvicorn main:asgi_app --host 0.0.0.0 --port 8000 --ssl-certfile ... --ssl-keyfile ...
    here = os.path.dirname(os.path.abspath(__file__))
    certfile = os.environ.get("SSL_CERTFILE", os.path.join(here, "certs", "server.pem"))
    keyfile = os.environ.get("SSL_KEYFILE", os.path.join(here, "certs", "server-key.pem"))
    if os.path.exists(certfile) and os.path.exists(keyfile):
        uvicorn.run(asgi_app, host="0.0.0.0", port=8000, ssl_certfile=certfile, ssl_keyfile=keyfile)
    elif os.environ.get("ALLOW_HTTP") == "1":
        print("[server] WARNING: no TLS certificate found; serving plain HTTP because ALLOW_HTTP=1")
        uvicorn.run(asgi_app, host="0.0.0.0", port=8000)
    else:
        raise SystemExit(f"no TLS certificate at {certfile} / {keyfile}. Generate one with mkcert "
                         "(or set SSL_CERTFILE/SSL_KEYFILE); set ALLOW_HTTP=1 to run without TLS.")
