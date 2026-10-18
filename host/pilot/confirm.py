"""One-time confirmation buttons. Also used to verify anonymous admins."""
from __future__ import annotations

import secrets
from typing import Any, Awaitable, Callable

from pilot import authz
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.util import now

HANDLERS: dict[str, Callable[..., Awaitable[str]]] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


async def create(chat_id: int, user_id: int | None, kind: str, payload: dict, right: str | None = None, ttl: int = 300) -> str:
    token = secrets.token_urlsafe(9)
    await RT.db.run("DELETE FROM rz_confirm WHERE expires_at<?", (now() - 86400,))
    await RT.db.run("INSERT INTO rz_confirm VALUES(?,?,?,?,?,?,?,?,?)",
                    (token, chat_id, user_id, kind, jd(payload), right, now(), now() + ttl, "pending"))
    return token


def buttons(token: str, lang: str, yes: str | None = None) -> list[list[tuple[str, str]]]:
    return [[(yes or ("✅ بله" if lang == "fa" else "✅ Yes"), f"cf:y:{token}"),
             ("✖️ لغو" if lang == "fa" else "✖️ Cancel", f"cf:n:{token}")]]


async def take(token: str, presser: int, chat_id: int, lang: str) -> tuple[dict | None, str]:
    fa = lang == "fa"
    r = await RT.db.one("SELECT * FROM rz_confirm WHERE token=?", (token,))
    if not r or r["chat_id"] != chat_id:
        return None, "منقضی شده." if fa else "Expired."
    if r["status"] != "pending":
        return None, "قبلاً انجام شده." if fa else "Already done."
    if r["expires_at"] < now():
        await RT.db.run("UPDATE rz_confirm SET status='expired' WHERE token=?", (token,))
        return None, "منقضی شده." if fa else "Expired."
    if r["user_id"] and presser != r["user_id"]:
        return None, "فقط درخواست‌دهنده." if fa else "Only the requester."
    if r["right"] is not None or not r["user_id"]:
        if chat_id < 0:
            ok, why = await authz.check(chat_id, presser, r["right"] or None, fresh=True)
            if not ok:
                return None, ("دسترسی ندارید." if fa else "You don't have the right.")
    _, n = await RT.db.run("UPDATE rz_confirm SET status='running' WHERE token=? AND status='pending'", (token,))
    if n != 1:
        return None, "قبلاً انجام شده." if fa else "Already done."
    r["payload"] = jl(r["payload"], {})
    return r, ""


async def finish(token: str, status: str = "done") -> None:
    await RT.db.run("UPDATE rz_confirm SET status=? WHERE token=?", (status, token))


async def cancel(token: str, presser: int, chat_id: int, lang: str) -> str:
    r = await RT.db.one("SELECT * FROM rz_confirm WHERE token=?", (token,))
    fa = lang == "fa"
    if not r or r["chat_id"] != chat_id or r["status"] != "pending":
        return "منقضی شده." if fa else "Expired."
    if r["user_id"] and presser != r["user_id"]:
        ok, _ = await authz.check(chat_id, presser, None) if chat_id < 0 else (False, "")
        if not ok:
            return "فقط درخواست‌دهنده یا ادمین‌ها." if fa else "Requester or admins only."
    await RT.db.run("UPDATE rz_confirm SET status='cancelled' WHERE token=? AND status='pending'", (token,))
    return "لغو شد." if fa else "Cancelled."


async def run(r: dict, presser: int, message: Any) -> str:
    fn = HANDLERS.get(r["kind"])
    if fn is None:
        await finish(r["token"], "failed")
        return "unknown confirmation kind"
    try:
        text = await fn(r["payload"], presser, r["chat_id"], message)
        await finish(r["token"], "done")
        return text
    except Exception as exc:  # noqa: BLE001
        await finish(r["token"], "failed")
        from pilot.audit import error
        await error(r["chat_id"], "confirm:" + r["kind"], repr(exc)[:300])
        raise
