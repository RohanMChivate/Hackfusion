"""Users, passwords, JWTs and role checks."""
import hashlib
import hmac
import os
import secrets
import time

import jwt
from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException

import db

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
# DEV ONLY: skip JWT checks and treat every request and dashboard socket as admin.
DEV_NO_AUTH = os.environ.get("DEV_NO_AUTH", "").strip().lower() == "true"
DEV_USER = {"username": "dev-no-auth", "role": "admin"}
SECRET_PATH = os.path.join(HERE, ".jwt_secret")
INITIAL_PASSWORDS_PATH = os.path.join(HERE, "initial_passwords.txt")
TOKEN_TTL = 8 * 3600
ALGORITHM = "HS256"
PBKDF2_ITERATIONS = 200_000

ROLES = ("admin", "teacher", "invigilator")
SEED_USERS = [("admin", "admin"), ("teacher", "teacher"), ("invigilator", "invigilator")]


def _load_secret():
    if os.environ.get("JWT_SECRET"):
        return os.environ["JWT_SECRET"]
    if not os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "w") as f:
            f.write(secrets.token_hex(64))
    with open(SECRET_PATH) as f:
        return f.read().strip()


SECRET = _load_secret()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, iterations, salt, digest = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def seed_users(conn):
    """On an empty users table, create one user per role with a random password,
    written once to initial_passwords.txt."""
    if db.count_users(conn):
        return
    lines = []
    for username, role in SEED_USERS:
        password = secrets.token_urlsafe(9)
        db.insert_user(conn, username, hash_password(password), role)
        lines.append(f"{username}\t{role}\t{password}")
    with open(INITIAL_PASSWORDS_PATH, "w") as f:
        f.write("username\trole\tpassword\n" + "\n".join(lines) + "\n")
    print(f"[auth] seeded {len(lines)} users; passwords written to {INITIAL_PASSWORDS_PATH}")


def issue_token(username, role):
    now = int(time.time())
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + TOKEN_TTL},
                      SECRET, algorithm=ALGORITHM)


def decode_token(token):
    """Return {'username', 'role'} or None if the token is missing, invalid or expired."""
    if DEV_NO_AUTH:
        return dict(DEV_USER)
    if not token:
        return None
    try:
        claims = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    if claims.get("role") not in ROLES:
        return None
    return {"username": claims["sub"], "role": claims["role"]}


def current_user(authorization: str | None = Header(None)):
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    user = decode_token(token)
    if user is None:
        raise HTTPException(401, "login required")
    return user


def require(*roles):
    def check(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, f"role {user['role']!r} may not do this")
        return user
    return check
