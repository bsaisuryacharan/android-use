"""A record of everything this server did to each phone.

Two people need it. The helper, afterwards: "what did you change on Amma's
phone this morning?". And anyone deciding whether to trust the tool at all -
a log of every tap is the difference between trusting it and hoping.

One JSON object per line, appended; the file rolls over at a couple of
megabytes so it can never fill a disk. Typed text is kept (it is the most
useful part of the record) except where it went into a password field.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(
    os.environ.get(
        "ANDROID_USE_AUDIT",
        Path(
            os.environ.get(
                "ANDROID_USE_CONFIG", Path.home() / ".android-use" / "config.json"
            )
        ).parent
        / "audit.jsonl",
    )
)
MAX_BYTES = 2_000_000
_lock = threading.Lock()


def record(action: str, device: str, detail: str = "", ok: bool = True) -> None:
    """Append one entry. Never raises: losing a log line must not fail a tap."""
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "device": device or "",
        "action": action,
        "detail": detail[:300],
        "ok": bool(ok),
    }
    try:
        with _lock:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOG_PATH.is_file() and LOG_PATH.stat().st_size > MAX_BYTES:
                LOG_PATH.replace(LOG_PATH.with_suffix(".jsonl.1"))
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            try:
                LOG_PATH.chmod(0o600)
            except OSError:
                pass
    except OSError:
        pass


def recent(limit: int = 20, device: str = "") -> list[dict]:
    """The newest entries first, optionally for one phone only."""
    if not LOG_PATH.is_file():
        return []
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    wanted = device.strip().lower()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if wanted and entry.get("device", "").lower() != wanted:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def render(entries: list[dict]) -> str:
    if not entries:
        return "Nothing has been done to any phone yet (the activity log is empty)."
    lines = []
    for e in entries:
        mark = "" if e.get("ok", True) else "  (failed)"
        where = f"[{e['device']}] " if e.get("device") else ""
        detail = f" - {e['detail']}" if e.get("detail") else ""
        lines.append(f"{e.get('at', '?')}  {where}{e.get('action', '?')}{detail}{mark}")
    return "\n".join(lines)
