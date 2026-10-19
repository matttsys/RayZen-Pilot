"""Scheduler: announcements, reminders, events, night lock, digests, retention.

Items are claimed with a guarded UPDATE before sending, so a restart can't double-send."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import ReplyParameters

from pilot import audit, authz, locks, logs, settings as S, tg
from pilot.ctx import lang_tz
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.util import esc, fmt_dt, in_window, next_after, now, parse_hhmm, rule_label

log = logging.getLogger("pilot.sched")
_last: dict[str, float] = {}


def every(name: str, sec: float) -> bool:
    t = time.time()
    if t - _last.get(name, 0) >= sec:
        _last[name] = t
        return True
    return False


async def tick(context=None) -> None:
    RT.tick_stats["last"] = time.time()
    for name, fn in (("layers", expire_layers), ("night", night_tick), ("ann", announcements_due),
                     ("rem", reminders_due), ("ev", events_due), ("logs", logs.flush), ("captcha", _captcha)):
        try:
            await fn()
        except Exception:  # noqa: BLE001
            RT.tick_stats["errors"] += 1
            log.exception("tick step %s failed", name)
            await audit.error(None, "tick:" + name, "exception (see process log)")
    try:
        if every("digest", 60):
            await digests_due()
        if every("watch", 300):
            from pilot.tools import watchdog_sweep
            await watchdog_sweep()
        if every("retention", 6 * 3600):
            await retention()
        if every("msgindex", 600):
            from pilot.purgeword import sweep
            await sweep()
    except Exception:  # noqa: BLE001
        RT.tick_stats["errors"] += 1
        log.exception("periodic step failed")
    from pilot import hooks
    for name, sec, fn in hooks.PERIODIC:
        if every("hook:" + name, sec):
            try:
                await fn()
            except Exception:  # noqa: BLE001
                RT.tick_stats["errors"] += 1
                log.exception("periodic hook %s failed", name)
                await audit.error(None, "tick:" + name, "exception (see process log)")


async def _captcha() -> None:
    from pilot.content import expire_captchas
    await expire_captchas()


async def recover_on_start() -> dict:
    """Don't resend items that were interrupted mid-send."""
    t = now() - 120
    _, a = await RT.db.run("UPDATE rz_announcements SET status=CASE WHEN rule='once' THEN 'uncertain' ELSE 'active' END, "
                           "last_error='interrupted during send; not resent to avoid duplicates' WHERE status='sending'")
    rows = await RT.db.all("SELECT id, rule, next_run, chat_id FROM rz_announcements WHERE status='active' AND rule<>'once' AND next_run<?", (t,))
    for r in rows:
        _, tz = await lang_tz(r["chat_id"])
        nxt = next_after(r["rule"], r["next_run"], tz)
        await RT.db.run("UPDATE rz_announcements SET next_run=?, last_error=COALESCE(last_error,'') || ' missed run(s) skipped' WHERE id=?",
                        (nxt, r["id"]))
    _, b = await RT.db.run("UPDATE rz_reminders SET status='active' WHERE status='sending'")
    return {"interrupted_announcements": a, "advanced_recurring": len(rows), "reminders_requeued": b}


# lock layers / night
async def expire_layers() -> None:
    rows = await RT.db.all("SELECT DISTINCT chat_id, layer FROM rz_lock_layers WHERE expires_at IS NOT NULL AND expires_at<=?", (now(),))
    for r in rows:
        res = await locks.reconcile(r["chat_id"])
        lang, _ = await lang_tz(r["chat_id"])
        if res.ok:
            await audit.record(r["chat_id"], None, "system", "unlock", params={"layer": r["layer"], "reason": "expired"},
                               log_line=f"#UNLOCK {esc(r['layer'])} (expired)")
            if r["layer"] == "raid" or r["layer"].startswith("manual:"):
                await tg.send(r["chat_id"], "🔓 قفل تمام شد." if lang == "fa" else "🔓 Lock ended.", silent=True)


async def night_tick() -> None:
    rows = await RT.db.all("SELECT * FROM rz_night WHERE enabled=1")
    for n in rows:
        cid = n["chat_id"]
        lang, tz = await lang_tz(cid)
        s, e = parse_hhmm(n["start"]), parse_hhmm(n["end"])
        if not s or not e:
            continue
        should = in_window(s, e, tz)
        has = await RT.db.one("SELECT 1 FROM rz_lock_layers WHERE chat_id=? AND layer='night'", (cid,))
        if should and not has:
            res = await locks.add_layer(cid, "night", jl(n["deny"], locks.SEND), n["actor_id"], "night schedule")
            if res.ok:
                await audit.record(cid, n["actor_id"], "system", "night", params={"state": "locked"},
                                   log_line=f"#NIGHT_LOCK {n['start']}-{n['end']}")
                if n["last_state"] != 1:
                    await tg.send(cid, f"🌙 گروه تا {n['end']} بسته است." if lang == "fa" else
                                  f"🌙 Group closed until {n['end']}.", silent=True)
                await RT.db.run("UPDATE rz_night SET last_state=1 WHERE chat_id=?", (cid,))
            else:
                await audit.error(cid, "night", f"lock failed: {res.kind} {res.desc}")
        elif not should and has:
            res = await locks.remove_layer(cid, "night")
            if res.ok:
                await audit.record(cid, None, "system", "night", params={"state": "open"}, log_line="#NIGHT_UNLOCK")
                if n["last_state"] != 0:
                    others = await RT.db.val("SELECT COUNT(*) FROM rz_lock_layers WHERE chat_id=?", (cid,), 0)
                    extra = ((" (قفل‌های دیگر هنوز فعال‌اند)" if lang == "fa" else " (other locks still active)") if others else "")
                    await tg.send(cid, ("☀️ گروه باز شد." if lang == "fa" else "☀️ Group open.") + extra, silent=True)
                await RT.db.run("UPDATE rz_night SET last_state=0 WHERE chat_id=?", (cid,))
            else:
                await audit.error(cid, "night", f"unlock failed: {res.kind} {res.desc}")


async def set_night(chat_id: int, start: str, end: str, actor: int, deny: list[str] | None = None) -> None:
    await RT.db.run("INSERT INTO rz_night(chat_id,start,end,deny,enabled,actor_id,created_at,last_state) VALUES(?,?,?,?,1,?,?,0) "
                    "ON CONFLICT(chat_id) DO UPDATE SET start=excluded.start,end=excluded.end,deny=excluded.deny,enabled=1,"
                    "actor_id=excluded.actor_id,created_at=excluded.created_at", (chat_id, start, end, jd(deny or locks.SEND), actor, now()))
    await night_tick()


async def clear_night(chat_id: int) -> bool:
    _, n = await RT.db.run("UPDATE rz_night SET enabled=0 WHERE chat_id=? AND enabled=1", (chat_id,))
    await locks.remove_layer(chat_id, "night")
    return n > 0


# announcements
async def ann_send(a: dict) -> tg.Out:
    thread = a["thread_id"] or None
    if a["src_chat_id"] and a["src_msg_id"]:
        out = await tg.call(RT.bot.copy_message, idempotent=False, chat_id=a["chat_id"], from_chat_id=a["src_chat_id"],
                            message_id=a["src_msg_id"], message_thread_id=thread)
    else:
        out = await tg.send(a["chat_id"], a["body"], thread_id=thread)
    if out.ok and a["pin"]:
        mid = getattr(out.value, "message_id", None)
        if mid:
            await tg.call(RT.bot.pin_chat_message, chat_id=a["chat_id"], message_id=mid, disable_notification=True)
    return out


async def announcements_due() -> None:
    rows = await RT.db.all("SELECT * FROM rz_announcements WHERE status='active' AND next_run<=? ORDER BY next_run LIMIT 20", (now(),))
    for a in rows:
        key = f"{a['id']}:{int(a['next_run'])}"
        _, n = await RT.db.run("UPDATE rz_announcements SET status='sending', last_key=? WHERE id=? AND status='active' AND next_run=? "
                               "AND (last_key IS NULL OR last_key<>?)", (key, a["id"], a["next_run"], key))
        if n != 1:
            continue
        out = await ann_send(a)
        _, tz = await lang_tz(a["chat_id"])
        if out.ok:
            nxt = next_after(a["rule"], a["next_run"], tz)
            await RT.db.run("UPDATE rz_announcements SET status=?, next_run=?, last_sent=?, sent_count=sent_count+1, last_error=? WHERE id=?",
                            ("active" if nxt else "done", nxt or a["next_run"], now(),
                             "topic missing — sent to General" if out.desc == "topic_missing_fallback" else None, a["id"]))
        elif out.kind in ("forbidden", "not_found", "rights", "bad_request"):
            await RT.db.run("UPDATE rz_announcements SET status='failed', last_error=? WHERE id=?", (f"{out.kind}: {out.desc}"[:300], a["id"]))
            await audit.error(a["chat_id"], "announce", f"#{a['id']} {out.kind}: {out.desc}")
        else:
            await RT.db.run("UPDATE rz_announcements SET status='active', next_run=?, last_key=NULL, last_error=? WHERE id=?",
                            (now() + 60, f"{out.kind}: {out.desc}"[:300], a["id"]))


def ann_line(a: dict, lang: str, tz: str) -> str:
    st = {"active": "▶️", "paused": "⏸", "done": "✅", "failed": "❌", "preview": "📝", "uncertain": "❔", "cancelled": "🗑"}.get(a["status"], "•")
    body = ("کپی پیام" if lang == "fa" else "copied message") if a["src_msg_id"] else esc((a["body"] or "")[:50])
    nxt = fmt_dt(a["next_run"], tz, lang, with_tz=False) if a["status"] in ("active", "paused", "preview") else "—"
    err = f"\n   ⚠️ {esc(a['last_error'][:80])}" if a.get("last_error") else ""
    topic = f" · topic {a['thread_id']}" if a["thread_id"] else ""
    return (f"{st} <code>#{a['id']}</code> {rule_label(a['rule'], lang)} · "
            f"{'بعدی' if lang == 'fa' else 'next'}: {nxt}{topic}\n   {body}{err}")


# reminders
async def reminders_due() -> None:
    rows = await RT.db.all("SELECT * FROM rz_reminders WHERE status='active' AND due<=? ORDER BY due LIMIT 30", (now(),))
    for r in rows:
        _, n = await RT.db.run("UPDATE rz_reminders SET status='sending' WHERE id=? AND status='active'", (r["id"],))
        if n != 1:
            continue
        lang = "fa"
        if r["chat_id"] and r["chat_id"] < 0:
            lang, _ = await lang_tz(r["chat_id"])
        snooze = [[("⏰ ۱۰ دقیقه" if lang == "fa" else "⏰ 10 min", f"rm:s:{r['id']}:600"),
                   ("⏰ ۱ ساعت" if lang == "fa" else "⏰ 1 hour", f"rm:s:{r['id']}:3600")]]
        head = "⏰ "
        if r["scope"] == "group":
            out = await tg.send(r["chat_id"], head + esc(r["text"]), thread_id=r["thread_id"])
            status = "sent" if out.ok else "failed"
        elif r["scope"] == "here":
            mention = f'<a href="tg://user?id={r["user_id"]}">⏱</a> '
            out = await tg.send(r["chat_id"], mention + esc(r["text"]), thread_id=r["thread_id"])
            status = "sent" if out.ok else "failed"
        else:
            out = await tg.send(r["user_id"], head + esc(r["text"]), rows=snooze)
            status = "sent" if out.ok else "failed"
            if not out.ok and out.kind == "forbidden" and r["chat_id"] and r["chat_id"] < 0:
                mention = f'<a href="tg://user?id={r["user_id"]}">⏰</a>'
                out2 = await tg.send(r["chat_id"], f"{mention} " + esc(r["text"]) +
                                     "", thread_id=r["thread_id"])
                status = "sent_group" if out2.ok else "failed"
                if out2.ok:
                    await RT.db.run("UPDATE rz_users SET dm_ok=0 WHERE user_id=?", (r["user_id"],))
        await RT.db.run("UPDATE rz_reminders SET status=?, attempts=attempts+1, last_error=? WHERE id=?",
                        (status, None if out.ok else f"{out.kind}: {out.desc}"[:200], r["id"]))


# events
async def event_card(ev: dict, lang: str, tz: str) -> tuple[str, list]:
    rs = await RT.db.all("SELECT status, checked_in FROM rz_rsvps WHERE event_id=?", (ev["id"],))
    g = sum(1 for r in rs if r["status"] == "going")
    m = sum(1 for r in rs if r["status"] == "maybe")
    no = sum(1 for r in rs if r["status"] == "no")
    ci = sum(1 for r in rs if r["checked_in"])
    fa = lang == "fa"
    st = {"active": "", "cancelled": " · " + ("لغو شد" if fa else "cancelled"), "done": " · ✅"}.get(ev["status"], "")
    txt = (f"📅 <b>{esc(ev['title'])}</b>{st}\n🕒 {fmt_dt(ev['starts_at'], tz, lang)}" +
           (f"\n📍 {esc(ev['place'])}" if ev.get("place") else "") +
           (f"\n✅ می‌آیم: {g} · 🤔 شاید: {m} · ❌ نه: {no}" if fa else f"\n✅ Going: {g} · 🤔 Maybe: {m} · ❌ No: {no}") +
           ((f"\nحاضر: {ci}" if fa else f"\nChecked in: {ci}") if ci else ""))
    if ev["status"] != "active":
        return txt, []
    rows = [[("✅ می‌آیم" if fa else "✅ Going", f"ev:g:{ev['id']}"), ("🤔 شاید" if fa else "🤔 Maybe", f"ev:m:{ev['id']}"),
             ("❌ نه" if fa else "❌ No", f"ev:n:{ev['id']}")]]
    if now() >= ev["starts_at"] - 1800:
        rows.append([("📍 حاضرم" if fa else "📍 Check in", f"ev:c:{ev['id']}")])
    return txt, rows


async def refresh_card(ev: dict) -> None:
    if not ev.get("card_msg_id"):
        return
    lang, tz = await lang_tz(ev["chat_id"])
    txt, rows = await event_card(ev, lang, tz)
    await tg.call(RT.bot.edit_message_text, chat_id=ev["chat_id"], message_id=ev["card_msg_id"], text=txt,
                  reply_markup=tg.kb(rows), link_preview_options=tg.NO_PREVIEW)


async def events_due() -> None:
    t = now()
    rows = await RT.db.all("SELECT * FROM rz_events WHERE status='active' AND ((reminded=0 AND starts_at-remind_before<=?) "
                           "OR (started=0 AND starts_at<=?) OR starts_at<=?)", (t, t, t - 6 * 3600))
    for ev in rows:
        lang, tz = await lang_tz(ev["chat_id"])
        fa = lang == "fa"
        if ev["starts_at"] <= t - 6 * 3600:
            await RT.db.run("UPDATE rz_events SET status='done' WHERE id=? AND status='active'", (ev["id"],))
            ev["status"] = "done"
            await refresh_card(ev)
            continue
        if not ev["reminded"] and ev["starts_at"] - ev["remind_before"] <= t < ev["starts_at"]:
            _, n = await RT.db.run("UPDATE rz_events SET reminded=1 WHERE id=? AND reminded=0", (ev["id"],))
            if n == 1:
                going = await RT.db.all("SELECT user_id, name FROM rz_rsvps WHERE event_id=? AND status='going' LIMIT 25", (ev["id"],))
                tags = " ".join(f'<a href="tg://user?id={g["user_id"]}">{esc(g["name"] or "•")}</a>' for g in going)
                await tg.send(ev["chat_id"], (f"⏰ {esc(ev['title'])} · {fmt_dt(ev['starts_at'], tz, lang, with_tz=False)}" if fa else
                                              f"⏰ {esc(ev['title'])} · {fmt_dt(ev['starts_at'], tz, lang, with_tz=False)}") +
                              (f"\n{tags}" if tags else ""), thread_id=ev["thread_id"], reply_to=ev["card_msg_id"])
        if not ev["started"] and ev["starts_at"] <= t:
            _, n = await RT.db.run("UPDATE rz_events SET started=1, reminded=1 WHERE id=? AND started=0", (ev["id"],))
            if n == 1:
                await tg.send(ev["chat_id"], (f"🟢 {esc(ev['title'])} شروع شد." if fa else f"🟢 {esc(ev['title'])} started."),
                              thread_id=ev["thread_id"], reply_to=ev["card_msg_id"])
                ev = await RT.db.one("SELECT * FROM rz_events WHERE id=?", (ev["id"],))
                await refresh_card(ev)


# digests
async def digests_due() -> None:
    rows = await RT.db.all("SELECT * FROM rz_digest WHERE enabled=1")
    for d in rows:
        lang, tz = await lang_tz(d["chat_id"])
        hm = parse_hhmm(d["at"])
        if not hm:
            continue
        loc = datetime.now(ZoneInfo(tz))
        today = loc.strftime("%Y-%m-%d")
        if (loc.hour, loc.minute) < hm or d["last_key"] == today:
            continue
        _, n = await RT.db.run("UPDATE rz_digest SET last_key=? WHERE chat_id=? AND (last_key IS NULL OR last_key<>?)",
                               (today, d["chat_id"], today))
        if n != 1:
            continue
        from pilot.tools import digest_text
        text = await digest_text(d["chat_id"], lang, tz, hours=24)
        r = await logs.route(d["chat_id"])
        if r and r["status"] == "active":
            out = await tg.send(r["dest_id"], text, thread_id=r["topic_id"], silent=True)
        else:
            out = await tg.send(d["requester"], text, silent=True)
        if not out.ok:
            await audit.error(d["chat_id"], "digest", f"{out.kind}: {out.desc}")


# retention
async def retention() -> None:
    chats = await RT.db.all("SELECT chat_id FROM rz_chats")
    t = now()
    for ch in chats:
        days = await S.get(ch["chat_id"], "retention_days")
        cut = t - days * 86400
        await RT.db.run("DELETE FROM rz_actions WHERE chat_id=? AND ts<?", (ch["chat_id"], cut))
        await RT.db.run("DELETE FROM rz_cases WHERE chat_id=? AND created_at<? AND status<>'active'", (ch["chat_id"], cut))
        await RT.db.run("DELETE FROM rz_errors WHERE chat_id=? AND ts<?", (ch["chat_id"], cut))
    await RT.db.run("UPDATE rz_cases SET evidence=json_remove(evidence,'$.excerpt') WHERE created_at<? AND evidence LIKE '%excerpt%'", (t - 14 * 86400,))
    await RT.db.run("DELETE FROM rz_log_queue WHERE status IN ('sent','dropped') AND created_at<?", (t - 3 * 86400,))
    await RT.db.run("DELETE FROM rz_ai_usage WHERE ts<?", (t - 30 * 86400,))
    await RT.db.run("DELETE FROM rz_seen WHERE ts<?", (t - 7 * 86400,))
    await RT.db.run("DELETE FROM rz_confirm WHERE expires_at<?", (t - 86400,))
    await RT.db.run("DELETE FROM rz_errors WHERE chat_id IS NULL AND ts<?", (t - 30 * 86400,))
