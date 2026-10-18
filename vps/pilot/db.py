"""SQLite wrapper, migrations and import from the old schema."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("pilot.db")

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS rz_meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS rz_chats (
  chat_id INTEGER PRIMARY KEY, title TEXT, type TEXT, is_forum INTEGER DEFAULT 0,
  added_at REAL, onboarded INTEGER DEFAULT 0, bot_status TEXT, linked_chat_id INTEGER,
  last_seen REAL, migrated_to INTEGER, legacy INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS rz_settings (
  chat_id INTEGER, key TEXT, value TEXT, set_by INTEGER, set_at REAL, source TEXT,
  PRIMARY KEY (chat_id, key));
CREATE TABLE IF NOT EXISTS rz_users (
  user_id INTEGER PRIMARY KEY, username TEXT, username_lc TEXT, first_name TEXT, last_name TEXT,
  is_bot INTEGER DEFAULT 0, last_seen REAL, source TEXT, dm_ok INTEGER DEFAULT 0, lang TEXT);
CREATE INDEX IF NOT EXISTS ix_users_un ON rz_users(username_lc);
CREATE TABLE IF NOT EXISTS rz_username_history (user_id INTEGER, username TEXT, seen_until REAL);
CREATE TABLE IF NOT EXISTS rz_user_chats (
  chat_id INTEGER, user_id INTEGER, first_seen REAL, last_seen REAL, status TEXT,
  PRIMARY KEY (chat_id, user_id));
CREATE TABLE IF NOT EXISTS rz_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, ts REAL, actor_id INTEGER, actor_kind TEXT,
  action TEXT, target_id INTEGER, target_name TEXT, params TEXT, outcome TEXT, error TEXT,
  undo TEXT, undone_by INTEGER, undone_at REAL, case_no INTEGER);
CREATE INDEX IF NOT EXISTS ix_actions_chat ON rz_actions(chat_id, ts);
CREATE TABLE IF NOT EXISTS rz_cases (
  chat_id INTEGER, case_no INTEGER, target_id INTEGER, target_name TEXT, actor_id INTEGER, action TEXT,
  reason TEXT, duration_s INTEGER, expires_at REAL, evidence TEXT, status TEXT, created_at REAL,
  action_id INTEGER, PRIMARY KEY (chat_id, case_no));
CREATE TABLE IF NOT EXISTS rz_appeals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, case_no INTEGER, user_id INTEGER, text TEXT,
  status TEXT, reviewer_id INTEGER, decided_at REAL, note TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS rz_warns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_id INTEGER, reason TEXT, actor_id INTEGER,
  ts REAL, active INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_warns ON rz_warns(chat_id, user_id, active);
CREATE TABLE IF NOT EXISTS rz_answers (
  chat_id INTEGER, name TEXT, body TEXT, media_type TEXT, file_id TEXT, keywords TEXT DEFAULT '[]',
  created_by INTEGER, updated_at REAL, PRIMARY KEY (chat_id, name));
CREATE TABLE IF NOT EXISTS rz_rules (chat_id INTEGER PRIMARY KEY, body TEXT, updated_by INTEGER, updated_at REAL);
CREATE TABLE IF NOT EXISTS rz_blockwords (chat_id INTEGER, word TEXT, PRIMARY KEY (chat_id, word));
CREATE TABLE IF NOT EXISTS rz_lock_layers (
  chat_id INTEGER, layer TEXT, deny TEXT, reason TEXT, actor_id INTEGER, created_at REAL, expires_at REAL,
  PRIMARY KEY (chat_id, layer));
CREATE TABLE IF NOT EXISTS rz_perm_state (
  chat_id INTEGER PRIMARY KEY, baseline TEXT, applied TEXT, overrides TEXT DEFAULT '[]',
  captured_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS rz_night (
  chat_id INTEGER PRIMARY KEY, start TEXT, end TEXT, deny TEXT, enabled INTEGER, actor_id INTEGER,
  created_at REAL, last_state INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS rz_log_routes (
  chat_id INTEGER PRIMARY KEY, dest_id INTEGER, topic_id INTEGER, status TEXT, verified_by INTEGER,
  verified_at REAL, last_ok REAL, last_error TEXT, fail_count INTEGER DEFAULT 0, notified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS rz_log_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, text TEXT, attempts INTEGER DEFAULT 0,
  next_try REAL, status TEXT, created_at REAL, last_error TEXT);
CREATE INDEX IF NOT EXISTS ix_logq ON rz_log_queue(status, next_try);
CREATE TABLE IF NOT EXISTS rz_link_codes (code TEXT PRIMARY KEY, chat_id INTEGER, user_id INTEGER, expires_at REAL);
CREATE TABLE IF NOT EXISTS rz_confirm (
  token TEXT PRIMARY KEY, chat_id INTEGER, user_id INTEGER, kind TEXT, payload TEXT, right TEXT,
  created_at REAL, expires_at REAL, status TEXT);
CREATE TABLE IF NOT EXISTS rz_announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, thread_id INTEGER, body TEXT, src_chat_id INTEGER,
  src_msg_id INTEGER, next_run REAL, rule TEXT, status TEXT, created_by INTEGER, created_at REAL,
  last_sent REAL, last_key TEXT, sent_count INTEGER DEFAULT 0, pin INTEGER DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS rz_reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, thread_id INTEGER, user_id INTEGER, scope TEXT,
  text TEXT, due REAL, status TEXT, created_at REAL, attempts INTEGER DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS rz_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, thread_id INTEGER, title TEXT, place TEXT,
  starts_at REAL, remind_before INTEGER, status TEXT, created_by INTEGER, card_msg_id INTEGER,
  reminded INTEGER DEFAULT 0, started INTEGER DEFAULT 0, created_at REAL);
CREATE TABLE IF NOT EXISTS rz_rsvps (
  event_id INTEGER, user_id INTEGER, status TEXT, checked_in INTEGER DEFAULT 0, name TEXT, ts REAL,
  PRIMARY KEY (event_id, user_id));
CREATE TABLE IF NOT EXISTS rz_polls (
  poll_id TEXT PRIMARY KEY, chat_id INTEGER, msg_id INTEGER, question TEXT, options TEXT, anonymous INTEGER,
  created_by INTEGER, closed INTEGER DEFAULT 0, counts TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS rz_digest (chat_id INTEGER PRIMARY KEY, at TEXT, enabled INTEGER, last_key TEXT, requester INTEGER);
CREATE TABLE IF NOT EXISTS rz_join_requests (
  chat_id INTEGER, user_id INTEGER, user_chat_id INTEGER, name TEXT, username TEXT, bio TEXT, ts REAL,
  status TEXT, decided_by INTEGER, decided_at REAL, screen TEXT, PRIMARY KEY (chat_id, user_id));
CREATE TABLE IF NOT EXISTS rz_tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_id INTEGER, text TEXT, status TEXT,
  claimed_by INTEGER, created_at REAL, closed_at REAL, replies INTEGER DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS rz_watch (
  chat_id INTEGER PRIMARY KEY, last_perms TEXT, good_perms TEXT, bot_rights TEXT, last_check REAL, drift TEXT);
CREATE TABLE IF NOT EXISTS rz_errors (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, ts REAL, area TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_errors ON rz_errors(chat_id, ts);
CREATE TABLE IF NOT EXISTS rz_ai_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_id INTEGER, ts REAL, model TEXT, ok INTEGER, kind TEXT);
CREATE INDEX IF NOT EXISTS ix_ai_usage ON rz_ai_usage(ts);
CREATE TABLE IF NOT EXISTS rz_seen (k TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS rz_legacy_review (chat_id INTEGER, key TEXT, value TEXT, PRIMARY KEY (chat_id, key));
"""

# v2: 48-hour word index for /purgeword. Holds normalized words only, never the message text.
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS rz_msg_index (
  chat_id INTEGER, message_id INTEGER, sender_id INTEGER, ts REAL, words TEXT,
  PRIMARY KEY (chat_id, message_id));
CREATE INDEX IF NOT EXISTS ix_msg_index_ts ON rz_msg_index(ts);
CREATE INDEX IF NOT EXISTS ix_msg_index_sender ON rz_msg_index(sender_id);
"""

MIGRATIONS: list[tuple[int, str]] = [(1, SCHEMA_V1), (2, SCHEMA_V2)]
CHAT_TABLES = ["rz_chats", "rz_settings", "rz_user_chats", "rz_actions", "rz_cases", "rz_appeals", "rz_warns",
               "rz_answers", "rz_rules", "rz_blockwords", "rz_lock_layers", "rz_perm_state", "rz_night",
               "rz_log_routes", "rz_log_queue", "rz_confirm", "rz_announcements", "rz_reminders", "rz_events",
               "rz_polls", "rz_digest", "rz_join_requests", "rz_tickets", "rz_watch", "rz_errors",
               "rz_legacy_review", "rz_ai_usage", "rz_msg_index"]


def jd(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def jl(s: str | None, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


class DB:
    def __init__(self, path: str) -> None:
        self.path = path
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        self.conn: sqlite3.Connection | None = None

    async def _call(self, fn: Callable, *a: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._ex, partial(fn, *a))

    async def open(self) -> dict:
        return await self._call(self._open_sync)

    def _open_sync(self) -> dict:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        existed = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        c = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=8000")
        c.execute("PRAGMA synchronous=NORMAL")
        self.conn = c
        return self._migrate_sync(existed)

    def _version(self) -> int:
        c = self.conn
        has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rz_meta'").fetchone()
        if not has:
            return 0
        r = c.execute("SELECT v FROM rz_meta WHERE k='schema_version'").fetchone()
        return int(r[0]) if r else 0

    def _backup(self, tag: str) -> str:
        bdir = Path(self.path).parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        dst = bdir / f"{Path(self.path).stem}-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.db"
        b = sqlite3.connect(str(dst))
        with b:
            self.conn.backup(b)
        b.close()
        return str(dst)

    def _migrate_sync(self, existed: bool) -> dict:
        c = self.conn
        info: dict[str, Any] = {"from": self._version(), "backup": None, "legacy": None}
        target = MIGRATIONS[-1][0]
        if existed and info["from"] < target:
            info["backup"] = self._backup(f"pre-v{target}")
        for ver, sql in MIGRATIONS:
            if ver <= info["from"]:
                continue
            c.execute("BEGIN IMMEDIATE")
            try:
                if ver == 1:
                    # the old schema had its own rz_events table; keep it as legacy_rz_events
                    if self._has("rz_events") and "title" not in self._cols("rz_events"):
                        c.execute("ALTER TABLE rz_events RENAME TO legacy_rz_events")
                        c.execute("DROP INDEX IF EXISTS idx_rz_events")
                        info["renamed"] = ["rz_events->legacy_rz_events"]
                for stmt in [s for s in sql.split(";\n") if s.strip()]:
                    c.execute(stmt)
                c.execute("INSERT OR REPLACE INTO rz_meta(k,v) VALUES('schema_version',?)", (str(ver),))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        info["to"] = self._version()
        done = c.execute("SELECT v FROM rz_meta WHERE k='legacy_import'").fetchone()
        legacy = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chats'").fetchone()
        if legacy and not done:
            if not info["backup"]:
                info["backup"] = self._backup("pre-legacy-import")
            info["legacy"] = self._import_legacy()
        return info

    # legacy import
    def _cols(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _has(self, table: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def _import_legacy(self) -> dict:
        """Import rules, notes, keyword replies, welcome text, warns, users and the log chat
        from the old schema. Filters are not turned back on; they go to rz_legacy_review."""
        c = self.conn
        t = time.time()
        stats = {"chats": 0, "rules": 0, "notes": 0, "filters": 0, "welcome": 0, "warns": 0, "users": 0,
                 "log_routes": 0, "review": 0, "blockwords": 0}
        c.execute("BEGIN IMMEDIATE")
        try:
            cols = self._cols("chats")
            for r in c.execute("SELECT * FROM chats").fetchall():
                cid = r["chat_id"]
                c.execute("INSERT OR IGNORE INTO rz_chats(chat_id,title,type,added_at,onboarded,legacy) VALUES(?,?,?,?,1,1)",
                          (cid, r["title"] if "title" in cols else "", "supergroup" if cid < -10**12 else "group", t))
                stats["chats"] += 1
                if "lang" in cols and r["lang"] in ("fa", "en"):
                    c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (cid, "lang", jd(r["lang"]), 0, t, "migrated"))
                st = jl(r["settings"], {}) if "settings" in cols else {}
                if not isinstance(st, dict):
                    st = {}
                rules = st.get("rules")
                if isinstance(rules, str) and rules.strip():
                    c.execute("INSERT OR IGNORE INTO rz_rules VALUES(?,?,?,?)", (cid, rules[:3500], 0, t))
                    stats["rules"] += 1
                lc = st.get("log_channel")
                if isinstance(lc, (int, str)) and str(lc).lstrip("-").isdigit():
                    c.execute("INSERT OR IGNORE INTO rz_log_routes(chat_id,dest_id,status,last_error) VALUES(?,?,?,?)",
                              (cid, int(lc), "unverified", "migrated from v3 — an admin must verify"))
                    stats["log_routes"] += 1
                for k, v in st.items():
                    if k in ("rules", "log_channel", "lang"):
                        continue
                    if isinstance(v, (bool, int, str, float)) and v not in (False, 0, "", None):
                        c.execute("INSERT OR IGNORE INTO rz_legacy_review VALUES(?,?,?)", (cid, str(k)[:60], jd(v)[:200]))
                        stats["review"] += 1
            if self._has("notes"):
                for r in c.execute("SELECT chat_id,name,text,file_id,file_type FROM notes").fetchall():
                    c.execute("INSERT OR IGNORE INTO rz_answers(chat_id,name,body,media_type,file_id,keywords,created_by,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                              (r[0], str(r[1]).lower()[:40], r[2] or "", r[4], r[3], "[]", 0, t))
                    stats["notes"] += 1
            if self._has("filters"):
                for r in c.execute("SELECT chat_id,keyword,response,file_id,file_type,is_regex FROM filters").fetchall():
                    if r[5]:
                        c.execute("INSERT OR IGNORE INTO rz_legacy_review VALUES(?,?,?)", (r[0], f"regex_filter:{str(r[1])[:40]}", jd(r[2])[:200]))
                        continue
                    name = ("kw-" + str(r[1]).lower())[:40]
                    c.execute("INSERT OR IGNORE INTO rz_answers(chat_id,name,body,media_type,file_id,keywords,created_by,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                              (r[0], name, r[2] or "", r[4], r[3], jd([str(r[1]).lower()]), 0, t))
                    c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (r[0], "answers_auto", "true", 0, t, "migrated"))
                    stats["filters"] += 1
            if self._has("greetings"):
                gc = self._cols("greetings")
                for r in c.execute("SELECT * FROM greetings").fetchall():
                    cid = r["chat_id"]
                    if r["welcome_text"]:
                        c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (cid, "welcome_text", jd(r["welcome_text"][:3500]), 0, t, "migrated"))
                        c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (cid, "welcome_on", jd(bool(r["welcome_enabled"])), 0, t, "migrated"))
                        stats["welcome"] += 1
                    if "goodbye_text" in gc and r["goodbye_text"]:
                        c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (cid, "goodbye_text", jd(r["goodbye_text"][:3500]), 0, t, "migrated"))
                        c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (cid, "goodbye_on", jd(bool(r["goodbye_enabled"])), 0, t, "migrated"))
                    for k in ("clean_service", "clean_welcome"):
                        if k in gc and r[k]:
                            # filters stay off; listed for an admin to review
                            c.execute("INSERT OR IGNORE INTO rz_legacy_review VALUES(?,?,?)", (cid, f"greetings:{k}", "true"))
                            stats["review"] += 1
            if self._has("warns"):
                for r in c.execute("SELECT chat_id,user_id,count,reasons FROM warns WHERE count>0").fetchall():
                    reasons = jl(r[3], []) or []
                    for i in range(min(int(r[2]), 20)):
                        rs = reasons[i] if i < len(reasons) and isinstance(reasons[i], str) else "migrated"
                        c.execute("INSERT INTO rz_warns(chat_id,user_id,reason,actor_id,ts,active) VALUES(?,?,?,?,?,1)", (r[0], r[1], rs[:200], 0, t))
                    stats["warns"] += 1
            if self._has("users"):
                for r in c.execute("SELECT user_id,name,username FROM users").fetchall():
                    un = (r[2] or "").lstrip("@") or None
                    c.execute("INSERT OR IGNORE INTO rz_users(user_id,username,username_lc,first_name,last_seen,source) VALUES(?,?,?,?,?,?)",
                              (r[0], un, un.lower() if un else None, (r[1] or "")[:64], 0, "legacy"))
                    stats["users"] += 1
            if self._has("blocklist"):
                for r in c.execute("SELECT chat_id,trigger,is_regex FROM blocklist").fetchall():
                    if not r[2]:
                        c.execute("INSERT OR IGNORE INTO rz_blockwords VALUES(?,?)", (r[0], str(r[1]).lower()[:60]))
                        stats["blockwords"] += 1
            if self._has("rz_cfg"):
                # old per-chat JSON config: copy content, park filters
                for r in c.execute("SELECT chat_id, data FROM rz_cfg").fetchall():
                    cid, d = r[0], jl(r[1], {}) or {}
                    if not isinstance(d, dict):
                        continue
                    c.execute("INSERT OR IGNORE INTO rz_chats(chat_id,title,type,added_at,onboarded,legacy) VALUES(?,?,?,?,1,1)",
                              (cid, "", "supergroup" if cid < -10**12 else "group", t))
                    rules = (d.get("rules") or {}).get("text") if isinstance(d.get("rules"), dict) else None
                    if isinstance(rules, str) and rules.strip():
                        c.execute("INSERT OR IGNORE INTO rz_rules VALUES(?,?,?,?)", (cid, rules[:3500], 0, t))
                        stats["rules"] += 1
                    for kind in ("welcome", "goodbye"):
                        g = d.get(kind) if isinstance(d.get(kind), dict) else {}
                        if isinstance(g.get("text"), str) and g["text"].strip():
                            c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (cid, f"{kind}_text", jd(g["text"][:3500]), 0, t, "migrated"))
                            c.execute("INSERT OR IGNORE INTO rz_settings VALUES(?,?,?,?,?,?)", (cid, f"{kind}_on", jd(bool(g.get("on"))), 0, t, "migrated"))
                            stats["welcome"] += 1
                    park = {}
                    night = d.get("night") if isinstance(d.get("night"), dict) else {}
                    if night.get("on"):
                        park["night"] = {"start_min": night.get("start"), "end_min": night.get("end"), "mode": night.get("mode")}
                    for k in ("antichannel", "service_clean", "slow", "cmd_clean"):
                        if d.get(k):
                            park[k] = d[k]
                    for k in ("raid", "captcha", "links"):
                        if isinstance(d.get(k), dict) and (d[k].get("on") or d[k].get("mode") not in (None, "off")):
                            park[k] = d[k]
                    for k, v in park.items():
                        c.execute("INSERT OR IGNORE INTO rz_legacy_review VALUES(?,?,?)", (cid, f"rz:{k}", jd(v)[:200]))
                        stats["review"] += 1
            if self._has("ft_settings"):
                for r in c.execute("SELECT chat_id, key, value FROM ft_settings").fetchall():
                    v = jl(r[2], r[2])
                    if r[1] == "modlog_chat" and str(v).lstrip("-").isdigit():
                        c.execute("INSERT OR IGNORE INTO rz_log_routes(chat_id,dest_id,status,last_error) VALUES(?,?,?,?)",
                                  (r[0], int(v), "unverified", "migrated from v3 — an admin must verify"))
                        stats["log_routes"] += 1
                    elif v not in (None, False, 0, "", [], {}):
                        c.execute("INSERT OR IGNORE INTO rz_legacy_review VALUES(?,?,?)", (r[0], f"ft:{str(r[1])[:50]}", jd(v)[:200]))
                        stats["review"] += 1
            for tbl in ("locks",):
                if self._has(tbl):
                    for r in c.execute(f"SELECT chat_id, locks FROM {tbl}").fetchall():
                        lk = jl(r[1], {}) or {}
                        on = [k for k, v in lk.items() if v] if isinstance(lk, dict) else []
                        if on:
                            c.execute("INSERT OR IGNORE INTO rz_legacy_review VALUES(?,?,?)", (r[0], "legacy_locks", jd(on)[:200]))
                            stats["review"] += 1
            c.execute("INSERT OR REPLACE INTO rz_meta(k,v) VALUES('legacy_import',?)", (jd({"ts": t, **stats}),))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            log.exception("legacy import failed; legacy tables untouched")
            raise
        return stats

    # API
    async def all(self, sql: str, p: tuple | list = ()) -> list[dict]:
        return await self._call(lambda: [dict(r) for r in self.conn.execute(sql, p).fetchall()])

    async def one(self, sql: str, p: tuple | list = ()) -> dict | None:
        def f():
            r = self.conn.execute(sql, p).fetchone()
            return dict(r) if r else None
        return await self._call(f)

    async def val(self, sql: str, p: tuple | list = (), default: Any = None) -> Any:
        def f():
            r = self.conn.execute(sql, p).fetchone()
            return r[0] if r and r[0] is not None else default
        return await self._call(f)

    async def run(self, sql: str, p: tuple | list = ()) -> tuple[int, int]:
        def f():
            cur = self.conn.execute(sql, p)
            return cur.lastrowid, cur.rowcount
        return await self._call(f)

    async def tx(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        def f():
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                r = fn(self.conn)
                self.conn.execute("COMMIT")
                return r
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        return await self._call(f)

    async def migrate_chat(self, old: int, new: int) -> None:
        def f(c):
            for t in CHAT_TABLES:
                cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()}
                if "chat_id" in cols:
                    c.execute(f"UPDATE OR REPLACE {t} SET chat_id=? WHERE chat_id=?", (new, old))
            c.execute("UPDATE rz_log_routes SET dest_id=? WHERE dest_id=?", (new, old))
            c.execute("UPDATE rz_chats SET migrated_to=? WHERE chat_id=?", (new, old))
        await self.tx(f)

    async def close(self) -> None:
        if self.conn:
            await self._call(self.conn.close)
            self.conn = None
        self._ex.shutdown(wait=True)
