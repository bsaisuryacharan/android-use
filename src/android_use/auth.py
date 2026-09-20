"""Bearer tokens with an identity, an expiry, and a revoke switch.

The URL secret that got this working is a capability: whoever holds the link is
the customer, forever, and it travels in a path where it can end up in logs,
referrers and screenshots. That is acceptable for one person running this for
their own family. It is not acceptable once other people's phones are involved.

This adds tokens that are hashed at rest, carry a subject, expire, and can be
revoked - deliberately as a separate layer rather than through the SDK's OAuth
support, because declaring OAuth changes the discovery handshake and would
break connectors that work today.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ACCOUNTS_PATH = Path(
    os.environ.get(
        "ANDROID_USE_ACCOUNTS",
        Path(
            os.environ.get(
                "ANDROID_USE_CONFIG", Path.home() / ".android-use" / "config.json"
            )
        ).parent
        / "accounts.json",
    )
)


@dataclass
class Grant:
    token_id: str
    subject: str
    expires_at: str
    revoked: bool
    label: str = ""

    @property
    def expired(self) -> bool:
        try:
            return datetime.now(timezone.utc) >= datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True

    @property
    def usable(self) -> bool:
        return not self.revoked and not self.expired


def _load() -> dict:
    if ACCOUNTS_PATH.is_file():
        try:
            return json.loads(ACCOUNTS_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {"grants": {}}


def _save(data: dict) -> None:
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_PATH.write_text(json.dumps(data, indent=2))
    try:
        ACCOUNTS_PATH.chmod(0o600)
    except OSError:
        pass


def _hash(token: str, salt: str) -> str:
    # Tokens are high-entropy random strings, so a single SHA-256 over a
    # per-token salt is enough; this is not a human-chosen password.
    return hashlib.sha256((salt + token).encode()).hexdigest()


def mint(subject: str, days: int = 90, label: str = "") -> tuple[str, Grant]:
    """Create a token. The plaintext is returned once and never stored."""
    data = _load()
    token_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    token = f"au_{token_id}_{secret}"
    salt = secrets.token_hex(16)
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    data.setdefault("grants", {})[token_id] = {
        "subject": subject,
        "salt": salt,
        "hash": _hash(token, salt),
        "expires_at": expires,
        "revoked": False,
        "label": label,
    }
    _save(data)
    return token, Grant(token_id, subject, expires, False, label)


def verify(token: str | None) -> str | None:
    """Return the subject this token belongs to, or None.

    Compared in constant time, and only after the token's own record is found -
    so a wrong token never reveals whether that token id exists.
    """
    if not token or not token.startswith("au_"):
        return None
    parts = token.split("_", 2)
    if len(parts) != 3:
        return None
    record = _load().get("grants", {}).get(parts[1])
    if not record:
        return None
    grant = Grant(
        parts[1], record.get("subject", ""), record.get("expires_at", ""),
        record.get("revoked", False), record.get("label", ""),
    )
    if not grant.usable:
        return None
    expected = record.get("hash", "")
    if not hmac.compare_digest(expected, _hash(token, record.get("salt", ""))):
        return None
    return grant.subject


def revoke(token_id: str) -> bool:
    data = _load()
    record = data.get("grants", {}).get(token_id)
    if not record:
        return False
    record["revoked"] = True
    _save(data)
    return True


def listing() -> list[Grant]:
    return [
        Grant(tid, r.get("subject", ""), r.get("expires_at", ""),
              r.get("revoked", False), r.get("label", ""))
        for tid, r in sorted(_load().get("grants", {}).items())
    ]


def any_configured() -> bool:
    """Has anyone set up token auth? If not, the URL secret still governs."""
    return bool(_load().get("grants"))
