"""Log routing, queue and retries, plus owner notices."""
from __future__ import annotations

import logging
import secrets
import time

from pilot import tg
from pilot.rt import RT
from pilot.util import esc, fmt_dt, now

log = logging.getLogger("pilot.logs")
BACKOFF = [10, 30, 120, 600, 1800]
MAX_ATTEMPTS = 6
MAX_HELD = 50
PERMANENT = {"forbidden", "not_found", "rights", "unsupported", "bad_request"}


def status_label(status: str, lang: str) -> str:
    fa = lang == "fa"
    return {"active": "فعال" if fa else "active", "broken": "قطع" if fa else "broken",
            "unverified": "تأیید نشده" if fa else "unverified", "disabled": "خاموش" if fa else "off"}.get(status, status)


async def route(chat_id: int) -> dict | None:
    return await RT.db.one("SELECT * FROM rz_log_routes WHERE chat_id=?", (chat_id,))


async def header(chat_id: int) -> str:
    row = await RT.db.one("SELECT title FROM rz_chats WHERE chat_id=?", (chat_id,))
    from pilot.ctx import lang_tz
    lang, tz = await lang_tz(chat_id)
    return f"{esc((row or {}).get('title') or chat_id)} · <code>{chat_id}</code> · {fmt_dt(now(), tz, lang)}"


async def emit(chat_id: int, body: str) -> bool:
    """Queue a log entry. Never raises."""
    try:
        r = await route(chat_id)
        if not r or r["status"] not in ("active", "broken"):
            return False
        text = body + "\n" + await header(chat_id)
        status = "pending" if r["status"] == "active" else "held"
        await RT.db.run("INSERT INTO rz_log_queue(chat_id,text,attempts,next_try,status,created_at) VALUES(?,?,0,?,?,?)",
                        (chat_id, text[:4000], now(), status, now()))
        if status == "held":
            await RT.db.run("UPDATE rz_log_queue SET status='dropped' WHERE chat_id=? AND status='held' AND id NOT IN "
                            "(SELECT id FROM rz_log_queue WHERE chat_id=? AND status='held' ORDER BY id DESC LIMIT ?)",
                            (chat_id, chat_id, MAX_HELD))
        return True
    except Exception:  # noqa: BLE001
        log.exception("emit failed")
        return False


async def _deliver(r: dict, text: str) -> tg.Out:
    return await tg.call(RT.bot.send_message, idempotent=False, chat_id=r["dest_id"], text=text,
                         message_thread_id=r["topic_id"] or None, link_preview_options=tg.NO_PREVIEW,
                         disable_notification=True)


async def flush(limit: int = 25) -> int:
    rows = await RT.db.all("SELECT q.*, r.dest_id, r.topic_id, r.status AS rstatus FROM rz_log_queue q JOIN rz_log_routes r "
                           "ON r.chat_id=q.chat_id WHERE q.status='pending' AND q.next_try<=? ORDER BY q.id LIMIT ?", (now(), limit))
    sent = 0
    broken: set[int] = set()
    for q in rows:
        if q["chat_id"] in broken or q["rstatus"] != "active":
            continue
        out = await _deliver(q, q["text"])
        if out.ok:
            sent += 1
            await RT.db.run("UPDATE rz_log_queue SET status='sent', attempts=attempts+1 WHERE id=?", (q["id"],))
            await RT.db.run("UPDATE rz_log_routes SET last_ok=?, fail_count=0 WHERE chat_id=?", (now(), q["chat_id"]))
            continue
        att = q["attempts"] + 1
        if out.kind == "flood":
            await RT.db.run("UPDATE rz_log_queue SET next_try=?, last_error=? WHERE id=?",
                            (now() + tg.retry_after_s(out.desc) + 1, out.desc, q["id"]))
            break
        if out.kind == "migrated":
            await RT.db.run("UPDATE rz_log_routes SET dest_id=? WHERE chat_id=?", (int(out.desc), q["chat_id"]))
            continue
        if out.kind in PERMANENT:
            broken.add(q["chat_id"])
            await mark_broken(q["chat_id"], f"{out.kind}: {out.desc}")
            continue
        if att >= MAX_ATTEMPTS:
            await RT.db.run("UPDATE rz_log_queue SET status='dropped', attempts=?, last_error=? WHERE id=?", (att, out.desc, q["id"]))
        else:
            await RT.db.run("UPDATE rz_log_queue SET attempts=?, next_try=?, last_error=? WHERE id=?",
                            (att, now() + BACKOFF[min(att - 1, len(BACKOFF) - 1)], out.desc, q["id"]))
    return sent


async def mark_broken(chat_id: int, err: str) -> None:
    r = await route(chat_id)
    if not r:
        return
    await RT.db.run("UPDATE rz_log_routes SET status='broken', last_error=?, fail_count=fail_count+1 WHERE chat_id=?", (err[:300], chat_id))
    await RT.db.run("UPDATE rz_log_queue SET status='held' WHERE chat_id=? AND status='pending'", (chat_id,))
    from pilot.audit import error
    await error(chat_id, "logs", err)
    if not r["notified"]:
        await RT.db.run("UPDATE rz_log_routes SET notified=1 WHERE chat_id=?", (chat_id,))
        from pilot.ctx import lang_tz
        lang, _ = await lang_tz(chat_id)
        fa = lang == "fa"
        txt = (("⚠️ ارسال لاگ به <code>{d}</code> قطع شد.\n{e}") if fa else
               ("⚠️ Log delivery to <code>{d}</code> failed.\n{e}")).format(d=r["dest_id"], e=esc(err[:200]))
        await tg.send(chat_id, txt, rows=[[("🔧 تعمیر" if fa else "🔧 Repair", "lg:repair"),
                                         ("⛔️ غیرفعال" if fa else "⛔️ Disable", "lg:off")]], silent=True)


async def verify_destination(src_chat: int, dest_id: int, topic_id: int | None, requester: int, lang: str) -> tuple[bool, str]:
    """Check access in the destination and send a test entry."""
    fa = lang == "fa"
    from pilot import authz
    gc = await tg.call(RT.bot.get_chat, chat_id=dest_id)
    if not gc.ok:
        return False, ("❌ ربات به این چت دسترسی ندارد. اول ربات را آنجا ادمین کنید." if fa else
                       "❌ The bot can't access that chat. Make it admin there first.")
    dest = gc.value
    if dest.type == "private":
        return False, "❌ مقصد باید گروه یا کانال باشد." if fa else "❌ Must be a group or channel."
    if dest_id != src_chat:
        mem = await authz.member(dest_id, requester, fresh=True)
        if not authz.is_adminish(mem):
            return False, ("❌ شما در آن چت ادمین نیستید." if fa else "❌ You are not an admin there.")
    bm = await authz.bot_member(dest_id, fresh=True)
    if dest.type == "channel" and not authz.has_right(bm, "post"):
        return False, "❌ ربات در کانال دسترسی ارسال پست ندارد." if fa else "❌ The bot can't post in that channel."
    if bm is None or bm.status in ("left", "kicked"):
        return False, "❌ ربات عضو آن چت نیست." if fa else "❌ The bot is not in that chat."
    if topic_id and not dest.is_forum:
        return False, "❌ آن گروه تاپیک ندارد." if fa else "❌ That group has no topics."
    test = ("✅ لاگ وصل شد\n" if fa else "✅ Logs connected\n") + await header(src_chat)
    out = await tg.call(RT.bot.send_message, idempotent=False, chat_id=dest_id, text=test, message_thread_id=topic_id or None,
                        link_preview_options=tg.NO_PREVIEW)
    if not out.ok:
        return False, ("❌ ارسال پیام تست نشد: " if fa else "❌ Test message failed: ") + esc(out.desc)
    await RT.db.run("INSERT INTO rz_log_routes(chat_id,dest_id,topic_id,status,verified_by,verified_at,last_ok,last_error,fail_count,notified) "
                    "VALUES(?,?,?,?,?,?,?,NULL,0,0) ON CONFLICT(chat_id) DO UPDATE SET dest_id=excluded.dest_id,topic_id=excluded.topic_id,"
                    "status='active',verified_by=excluded.verified_by,verified_at=excluded.verified_at,last_ok=excluded.last_ok,"
                    "last_error=NULL,fail_count=0,notified=0", (src_chat, dest_id, topic_id, "active", requester, now(), now()))
    await RT.db.run("UPDATE rz_log_queue SET status='pending', next_try=? WHERE chat_id=? AND status='held'", (now(), src_chat))
    title = esc(getattr(dest, "title", "") or dest_id)
    return True, ((f"✅ لاگ وصل شد: {title}" + (f" (تاپیک {topic_id})" if topic_id else "")) if fa else
                  (f"✅ Logs connected: {title}" + (f" (topic {topic_id})" if topic_id else "")))


async def new_code(chat_id: int, user_id: int) -> str:
    code = "RZ-" + secrets.token_hex(4).upper()
    await RT.db.run("DELETE FROM rz_link_codes WHERE expires_at<?", (now(),))
    await RT.db.run("INSERT INTO rz_link_codes VALUES(?,?,?,?)", (code, chat_id, user_id, now() + 600))
    return code


async def take_code(code: str) -> dict | None:
    def f(c):
        r = c.execute("SELECT * FROM rz_link_codes WHERE code=? AND expires_at>=?", (code.upper(), time.time())).fetchone()
        if r:
            c.execute("DELETE FROM rz_link_codes WHERE code=?", (code.upper(),))
            return dict(r)
        return None
    return await RT.db.tx(f)


async def disable(chat_id: int) -> None:
    await RT.db.run("UPDATE rz_log_routes SET status='disabled' WHERE chat_id=?", (chat_id,))
    await RT.db.run("UPDATE rz_log_queue SET status='dropped' WHERE chat_id=? AND status IN ('pending','held')", (chat_id,))


async def repair(chat_id: int, requester: int, lang: str) -> tuple[bool, str]:
    r = await route(chat_id)
    if not r or not r["dest_id"]:
        return False, "❌ لاگ تنظیم نشده." if lang == "fa" else "❌ Logs not set."
    return await verify_destination(chat_id, r["dest_id"], r["topic_id"], requester, lang)


async def retest(chat_id: int, lang: str) -> tuple[bool, str]:
    """Owner repair: resend a test entry to the already verified destination. The destination is not changed,
    so no new authority check is needed; success reactivates the route and releases held entries."""
    fa = lang == "fa"
    r = await route(chat_id)
    if not r or not r["dest_id"] or r["status"] == "disabled":
        return False, "لاگ تنظیم نشده." if fa else "Logs not set."
    out = await _deliver(r, ("🔧 آزمایش لاگ\n" if fa else "🔧 Log test\n") + await header(chat_id))
    if not out.ok:
        await RT.db.run("UPDATE rz_log_routes SET last_error=? WHERE chat_id=?", (out.desc[:300], chat_id))
        return False, ("❌ نرسید: " if fa else "❌ Not delivered: ") + out.desc[:150]
    await RT.db.run("UPDATE rz_log_routes SET status='active', last_ok=?, last_error=NULL, fail_count=0, notified=0 WHERE chat_id=?",
                    (now(), chat_id))
    await RT.db.run("UPDATE rz_log_queue SET status='pending', next_try=? WHERE chat_id=? AND status='held'", (now(), chat_id))
    return True, "✅ رسید؛ مسیر فعال شد." if fa else "✅ Delivered; route active."


_owner_last: list[float] = []


async def owner(text: str) -> None:
    """Owner notice: LOG_CHANNEL if set, otherwise OWNER_ID's private chat."""
    if not RT.cfg or RT.bot is None:
        return
    dest = RT.cfg.log_channel or RT.cfg.owner_id
    if not dest:
        return
    t = time.time()
    _owner_last[:] = [x for x in _owner_last if t - x < 60]
    if len(_owner_last) >= 15:
        return
    _owner_last.append(t)
    await tg.call(RT.bot.send_message, idempotent=False, chat_id=dest, text=text[:3500],
                  link_preview_options=tg.NO_PREVIEW, disable_notification=True)
