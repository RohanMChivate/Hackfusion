import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("INVIGILAI_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "invigilai.db"))

LIST_FIELDS = ("allowed_apps", "blocked_apps", "allowed_domains", "blocked_domains")

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    allowed_apps TEXT NOT NULL,      -- JSON list of process names; empty = any app
    blocked_apps TEXT NOT NULL,      -- JSON list of process names
    allowed_domains TEXT NOT NULL,   -- JSON list; empty = any domain
    blocked_domains TEXT NOT NULL,   -- JSON list; matches the domain and its subdomains
    max_switches_per_min INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    rule TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    profile TEXT NOT NULL,
    screenshot_at_time TEXT,         -- base64 JPEG of the device screen, or NULL
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_ts ON alerts (ts);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'invigilator'))
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    profile TEXT NOT NULL,
    started_by TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_by TEXT,
    ended_at INTEGER              -- NULL while the session is active
);
CREATE TABLE IF NOT EXISTS enrollment_tokens (
    id INTEGER PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL, -- sha256 of the token; the token itself is shown once
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    used_by_device TEXT,
    used_at INTEGER
);
CREATE TABLE IF NOT EXISTS enrolled_devices (
    device_id TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,       -- sha256 of the device secret
    enrolled_at INTEGER NOT NULL,
    token_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    actor TEXT NOT NULL,             -- username, or device:<id> for agents
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT                      -- JSON
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_log (ts);
CREATE TABLE IF NOT EXISTS session_results (
    session_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    final_risk REAL NOT NULL,        -- risk score when the session ended
    PRIMARY KEY (session_id, device_id)
);
CREATE TABLE IF NOT EXISTS report_summaries (
    session_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    summary TEXT NOT NULL,           -- three lines written by the AI model
    created_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, device_id)
);
"""

# columns added to alerts after the table first shipped: name -> SQL type
ALERT_MIGRATIONS = {
    "session_id": "INTEGER",
    "ai_status": "TEXT",             # pending | done | error | skipped
    "ai_violation": "INTEGER",
    "ai_type": "TEXT",
    "ai_confidence": "REAL",
    "ai_reason": "TEXT",
}

AI_DOMAINS = [
    "chatgpt.com", "chat.openai.com", "gemini.google.com", "claude.ai",
    "perplexity.ai", "copilot.microsoft.com", "deepseek.com",
]
MESSAGING_DOMAINS = ["web.whatsapp.com", "discord.com", "web.telegram.org"]
# WhatsApp.Root.exe verified on this machine; the others are not installed here.
MESSAGING_APPS = ["WhatsApp.exe", "WhatsApp.Root.exe", "Discord.exe", "Telegram.exe"]
BROWSERS = ["chrome.exe", "msedge.exe"]

SEED_PROFILES = [
    {
        "name": "Coding Lab Exam",
        "allowed_apps": [
            # editors
            "Code.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "notepad++.exe", "sublime_text.exe",
            # terminals
            "WindowsTerminal.exe", "OpenConsole.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "python.exe",
            # browsers, for localhost only (see allowed_domains)
            *BROWSERS,
        ],
        "blocked_apps": MESSAGING_APPS,
        "allowed_domains": ["localhost", "127.0.0.1"],
        "blocked_domains": AI_DOMAINS + MESSAGING_DOMAINS,
        "max_switches_per_min": 10,
    },
    {
        "name": "Online MCQ",
        "allowed_apps": BROWSERS,
        "blocked_apps": MESSAGING_APPS,
        # Set to the one exam site's domain. Left empty because no exam site was given.
        "allowed_domains": [],
        "blocked_domains": AI_DOMAINS + MESSAGING_DOMAINS,
        "max_switches_per_min": 4,
    },
]


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(alerts)")}
    for column, sql_type in ALERT_MIGRATIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column} {sql_type}")
    for p in SEED_PROFILES:
        conn.execute(
            "INSERT OR IGNORE INTO profiles (name, allowed_apps, blocked_apps, allowed_domains,"
            " blocked_domains, max_switches_per_min) VALUES (?, ?, ?, ?, ?, ?)",
            (p["name"], *(json.dumps(p[f]) for f in LIST_FIELDS), p["max_switches_per_min"]),
        )
    conn.commit()
    return conn


def _profile(row):
    p = dict(row)
    for f in LIST_FIELDS:
        p[f] = json.loads(p[f])
    return p


def get_profile(conn, name):
    row = conn.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone()
    return _profile(row) if row else None


def list_profiles(conn):
    return [_profile(r) for r in conn.execute("SELECT * FROM profiles ORDER BY id")]


def update_profile(conn, name, fields):
    sets = []
    values = []
    for f in LIST_FIELDS:
        if f in fields:
            sets.append(f"{f} = ?")
            values.append(json.dumps(fields[f]))
    if "max_switches_per_min" in fields:
        sets.append("max_switches_per_min = ?")
        values.append(fields["max_switches_per_min"])
    if sets:
        conn.execute(f"UPDATE profiles SET {', '.join(sets)} WHERE name = ?", (*values, name))
        conn.commit()


# --- users -----------------------------------------------------------------

def get_user(conn, username):
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def count_users(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def insert_user(conn, username, password_hash, role):
    conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                 (username, password_hash, role))
    conn.commit()


# --- sessions --------------------------------------------------------------

def active_session(conn):
    row = conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def start_session(conn, profile, username, now):
    cur = conn.execute("INSERT INTO sessions (profile, started_by, started_at) VALUES (?, ?, ?)",
                       (profile, username, now))
    conn.commit()
    return cur.lastrowid


def end_session(conn, session_id, username, now):
    conn.execute("UPDATE sessions SET ended_by = ?, ended_at = ? WHERE id = ?", (username, now, session_id))
    conn.commit()


# --- enrollment ------------------------------------------------------------

def insert_enrollment_token(conn, token_hash, username, now):
    cur = conn.execute("INSERT INTO enrollment_tokens (token_hash, created_by, created_at) VALUES (?, ?, ?)",
                       (token_hash, username, now))
    conn.commit()
    return cur.lastrowid


def consume_enrollment_token(conn, token_hash, device_id, secret_hash, now):
    """Atomically mark an unused token used and (re-)enroll the device. Returns False if the token is unknown or used."""
    with conn:
        cur = conn.execute(
            "UPDATE enrollment_tokens SET used_by_device = ?, used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (device_id, now, token_hash),
        )
        if cur.rowcount != 1:
            return False
        token_id = conn.execute("SELECT id FROM enrollment_tokens WHERE token_hash = ?", (token_hash,)).fetchone()[0]
        conn.execute(
            "INSERT INTO enrolled_devices (device_id, secret_hash, enrolled_at, token_id) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(device_id) DO UPDATE SET secret_hash = excluded.secret_hash,"
            " enrolled_at = excluded.enrolled_at, token_id = excluded.token_id",
            (device_id, secret_hash, now, token_id),
        )
    return True


def device_secret_hash(conn, device_id):
    row = conn.execute("SELECT secret_hash FROM enrolled_devices WHERE device_id = ?", (device_id,)).fetchone()
    return row[0] if row else None


# --- audit -----------------------------------------------------------------

def audit(conn, actor, role, action, target=None, detail=None, now=None):
    conn.execute(
        "INSERT INTO audit_log (ts, actor, role, action, target, detail) VALUES (?, ?, ?, ?, ?, ?)",
        (int(now or time.time()), actor, role, action, target, json.dumps(detail) if detail is not None else None),
    )
    conn.commit()


def list_audit(conn, limit=200):
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        out.append(d)
    return out


# --- alerts ----------------------------------------------------------------

def insert_alert(conn, alert):
    cur = conn.execute(
        "INSERT INTO alerts (device_id, rule, severity, detail, profile, screenshot_at_time, ts, session_id)"
        " VALUES (:device_id, :rule, :severity, :detail, :profile, :screenshot_at_time, :ts, :session_id)",
        alert,
    )
    conn.commit()
    return cur.lastrowid


def set_alert_ai(conn, alert_id, status, violation=None, type_=None, confidence=None, reason=None):
    conn.execute(
        "UPDATE alerts SET ai_status = ?, ai_violation = ?, ai_type = ?, ai_confidence = ?, ai_reason = ?"
        " WHERE id = ?",
        (status, None if violation is None else int(violation), type_, confidence, reason, alert_id),
    )
    conn.commit()


def ai_fields(alert):
    """The AI review of an alert row as sent to dashboards."""
    return {
        "ai_status": alert.get("ai_status"),
        "ai_violation": None if alert.get("ai_violation") is None else bool(alert["ai_violation"]),
        "ai_type": alert.get("ai_type"),
        "ai_confidence": alert.get("ai_confidence"),
        "ai_reason": alert.get("ai_reason"),
    }


# --- session results & reports -----------------------------------------------

def list_sessions(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM sessions ORDER BY id DESC")]


def get_session(conn, session_id):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def save_session_results(conn, session_id, scores):
    conn.executemany(
        "INSERT OR REPLACE INTO session_results (session_id, device_id, final_risk) VALUES (?, ?, ?)",
        [(session_id, d, s) for d, s in scores.items()],
    )
    conn.commit()


def session_students(conn, session_id):
    """Every device with a final score or an alert in the session."""
    rows = conn.execute(
        "SELECT d.device_id, COALESCE(r.final_risk, 0) AS final_risk,"
        " (SELECT COUNT(*) FROM alerts a WHERE a.session_id = ? AND a.device_id = d.device_id) AS alert_count"
        " FROM (SELECT device_id FROM session_results WHERE session_id = ?"
        "       UNION SELECT device_id FROM alerts WHERE session_id = ?) d"
        " LEFT JOIN session_results r ON r.session_id = ? AND r.device_id = d.device_id"
        " ORDER BY final_risk DESC, d.device_id",
        (session_id, session_id, session_id, session_id),
    )
    return [dict(r) for r in rows]


def student_alerts(conn, session_id, device_id):
    rows = conn.execute("SELECT * FROM alerts WHERE session_id = ? AND device_id = ? ORDER BY ts, id",
                        (session_id, device_id))
    return [dict(r) for r in rows]


def get_summary(conn, session_id, device_id):
    row = conn.execute("SELECT summary FROM report_summaries WHERE session_id = ? AND device_id = ?",
                       (session_id, device_id)).fetchone()
    return row[0] if row else None


def save_summary(conn, session_id, device_id, summary):
    conn.execute("INSERT OR REPLACE INTO report_summaries (session_id, device_id, summary, created_at)"
                 " VALUES (?, ?, ?, ?)", (session_id, device_id, summary, int(time.time())))
    conn.commit()


def list_alerts(conn, limit=100):
    rows = conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
