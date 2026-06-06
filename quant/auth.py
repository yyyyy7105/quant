"""User authentication: register (CLI-only) and verify (app login).

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib, no extra deps).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime

from .db import connect

_ITERATIONS = 600_000  # OWASP recommendation for PBKDF2-SHA256


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Return (hex_hash, hex_salt)."""
    if salt is None:
        salt = os.urandom(32)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return h.hex(), salt.hex()


def register(username: str, password: str) -> bool:
    """Create a new user. Returns True on success, False if username taken."""
    pw_hash, pw_salt = _hash_password(password)
    ts = datetime.now().isoformat(timespec="seconds")
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO users (username, pw_hash, pw_salt, created_at) VALUES (?, ?, ?, ?)",
                (username.lower(), pw_hash, pw_salt, ts),
            )
            conn.commit()
        return True
    except Exception:
        return False


def verify(username: str, password: str) -> bool:
    """Check username + password. Returns True if valid."""
    with connect() as conn:
        row = conn.execute(
            "SELECT pw_hash, pw_salt FROM users WHERE username = ?",
            (username.lower(),),
        ).fetchone()
    if not row:
        return False
    stored_hash = row["pw_hash"]
    salt = bytes.fromhex(row["pw_salt"])
    candidate_hash, _ = _hash_password(password, salt)
    return candidate_hash == stored_hash


def change_password(username: str, new_password: str) -> bool:
    """Reset a user's password. Returns True if the user existed."""
    pw_hash, pw_salt = _hash_password(new_password)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE users SET pw_hash = ?, pw_salt = ? WHERE username = ?",
            (pw_hash, pw_salt, username.lower()),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_user(username: str) -> bool:
    """Remove a user. Returns True if the user existed."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM users WHERE username = ?", (username.lower(),)
        )
        conn.commit()
        return cur.rowcount > 0


def list_users() -> list[dict]:
    """Return all users (username + created_at, no secrets)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT username, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [{"username": r["username"], "created_at": r["created_at"]} for r in rows]


def has_users() -> bool:
    """Return True if at least one user is registered."""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return row["n"] > 0
