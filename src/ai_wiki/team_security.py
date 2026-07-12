"""Optional authenticated team-mode security services.

This module is imported only when AI_WIKI_TEAM_MODE=1 so local-first installs do
not acquire a server or authentication runtime dependency.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TeamSecurity:
    def __init__(self, root: Path):
        self.root = root.resolve()
        data = self.root / "data"
        data.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(data / "team-auth.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS team_users (
                user_id TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                roles TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_tokens (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                label TEXT NOT NULL, expires_at TEXT, created_at TEXT NOT NULL,
                revoked_at TEXT, FOREIGN KEY(user_id) REFERENCES team_users(user_id)
            );
            CREATE TABLE IF NOT EXISTS team_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                action TEXT NOT NULL, target TEXT, allowed INTEGER NOT NULL,
                detail TEXT, at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rate_events (
                key TEXT NOT NULL, at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rate_events ON rate_events(key, at);
        """)
        self.conn.commit()

    @staticmethod
    def _hasher():
        try:
            from argon2 import PasswordHasher
        except ImportError as exc:
            raise RuntimeError("team mode requires ai-wiki[team]") from exc
        return PasswordHasher()

    def create_user(self, user_id: str, password: str, roles: list[str]) -> None:
        if len(password) < 12:
            raise ValueError("team passwords must contain at least 12 characters")
        allowed = {"owner", "reviewer", "agent", "reader"}
        if not roles or set(roles) - allowed:
            raise ValueError("invalid team roles")
        self.conn.execute(
            "INSERT INTO team_users VALUES (?, ?, ?, 1, ?)",
            (user_id, self._hasher().hash(password), ",".join(sorted(set(roles))), _now().isoformat()),
        )
        self.conn.commit()
        self.audit(user_id, "create_user", user_id, True)

    def verify_password(self, user_id: str, password: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM team_users WHERE user_id=? AND enabled=1", (user_id,),
        ).fetchone()
        if not row:
            self.audit(user_id or "anonymous", "login", user_id, False, "unknown user")
            return None
        try:
            self._hasher().verify(row["password_hash"], password)
        except Exception:
            self.audit(user_id, "login", user_id, False, "invalid credential")
            return None
        self.audit(user_id, "login", user_id, True)
        return {"id": user_id, "roles": row["roles"].split(",")}

    def issue_token(self, user_id: str, label: str, days: int = 30) -> str:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = (_now() + timedelta(days=days)).isoformat()
        self.conn.execute(
            "INSERT INTO api_tokens VALUES (?, ?, ?, ?, ?, NULL)",
            (digest, user_id, label, expires, _now().isoformat()),
        )
        self.conn.commit()
        self.audit(user_id, "issue_token", label, True)
        return token

    def verify_token(self, token: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.conn.execute(
            """SELECT u.user_id, u.roles, t.expires_at FROM api_tokens t
               JOIN team_users u ON u.user_id=t.user_id
               WHERE t.token_hash=? AND t.revoked_at IS NULL AND u.enabled=1""", (digest,),
        ).fetchone()
        if not row or (row["expires_at"] and row["expires_at"] <= _now().isoformat()):
            return None
        return {"id": row["user_id"], "roles": row["roles"].split(",")}

    def rate_limit(self, key: str, *, limit: int = 120, window_seconds: int = 60) -> bool:
        cutoff = (_now() - timedelta(seconds=window_seconds)).isoformat()
        self.conn.execute("DELETE FROM rate_events WHERE at<?", (cutoff,))
        count = self.conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE key=?", (key,),
        ).fetchone()[0]
        if count >= limit:
            self.conn.commit()
            return False
        self.conn.execute("INSERT INTO rate_events VALUES (?, ?)", (key, _now().isoformat()))
        self.conn.commit()
        return True

    def audit(self, actor: str, action: str, target: str = "", allowed: bool = True,
              detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO team_audit(actor,action,target,allowed,detail,at) VALUES (?,?,?,?,?,?)",
            (actor, action, target, int(allowed), detail, _now().isoformat()),
        )
        self.conn.commit()

    @staticmethod
    def encrypt_sensitive(value: bytes, key_reference: str) -> dict[str, str]:
        if not key_reference.startswith("env:"):
            raise ValueError("encryption key must be an env: reference")
        key = os.environ.get(key_reference[4:])
        if not key:
            raise ValueError("encryption key is unavailable")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise RuntimeError("field encryption requires ai-wiki[team]") from exc
        material = hashlib.sha256(key.encode()).digest()
        nonce = os.urandom(12)
        ciphertext = AESGCM(material).encrypt(nonce, value, None)
        return {"algorithm": "AES-256-GCM", "nonce": nonce.hex(), "ciphertext": ciphertext.hex()}

    def close(self) -> None:
        self.conn.close()
