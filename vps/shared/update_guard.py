"""Crash guard for updates, used by app.py. Standard library only, so it works even if the new bot code
doesn't import.

The updater writes data/updates/state.json with status "pending" before it restarts the bot. The new
version marks it "ok" once it has started. If the bot exits before that twice in a row, app.py calls
rollback(), which moves the previous code back from the backup directory."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

MAX_FAILS = 2


def state_path(root: Path) -> Path:
    return root / "data" / "updates" / "state.json"


def read(root: Path) -> dict:
    try:
        return json.loads(state_path(root).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write(root: Path, st: dict) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


def note_exit(root: Path, healthy_run: bool) -> str | None:
    """Called by app.py after the bot process exits. Returns a log line when it rolled back."""
    st = read(root)
    if st.get("status") == "restarting":
        st.update({"status": "pending", "fails": 0})   # the old version exited on purpose; next start is the new one
        write(root, st)
        return None
    if st.get("status") != "pending" or healthy_run:
        return None
    st["fails"] = int(st.get("fails", 0)) + 1
    write(root, st)
    if st["fails"] < MAX_FAILS:
        return None
    ok, msg = rollback(root, st, reason="new version failed to start")
    return msg


def rollback(root: Path, st: dict | None = None, reason: str = "") -> tuple[bool, str]:
    st = st or read(root)
    backup = root / st.get("backup", "") if st.get("backup") else None
    entries = st.get("entries") or []
    if not backup or not backup.is_dir() or not entries:
        st["status"] = "failed"
        st["error"] = "no backup to roll back to"
        write(root, st)
        return False, "rollback impossible: no backup"
    trash = root / "data" / "updates" / f"failed-{st.get('to', 'x')}-{int(time.time())}"
    trash.mkdir(parents=True, exist_ok=True)
    for name in entries:
        if "/" in name or name.startswith(".") or name in ("data", ".env"):
            continue
        cur, old = root / name, backup / name
        if not old.exists():
            continue
        if cur.exists() or cur.is_symlink():
            shutil.move(str(cur), str(trash / name))
        shutil.move(str(old), str(cur))
    st.update({"status": "rolled_back", "reason": reason, "rolled_back_at": time.time()})
    write(root, st)
    return True, f"rolled back {st.get('to')} -> {st.get('from')}: {reason}"
