"""Join requests, tickets, appeals and forum topics."""
from __future__ import annotations

import logging

from telegram import ChatJoinRequest

from pilot import audit, authz, confirm, logs, settings as S, tg
from pilot.ctx import Cmd, lang_tz
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.targets import note_user
from pilot.util import esc, fmt_dt, now, short

log = logging.getLogger("pilot.desk")
DRAFT_TTL = 900


async def set_draft(user_id: int, data: dict) -> None:
    await RT.db.run("INSERT OR REPLACE INTO rz_meta VALUES(?,?)", (f"draft:{user_id}", jd({**data, "exp": now() + DRAFT_TTL})))


async def pop_draft(user_id: int) -> dict | None:
    v = await RT.db.val("SELECT v FROM rz_meta WHERE k=?", (f"draft:{user_id}",))
    if not v:
        return None
    await RT.db.run("DELETE FROM rz_meta WHERE k=?", (f"draft:{user_id}",))
    d = jl(v, {})
    return d if d.get("exp", 0) >= now() else None


async def admin_card(chat_id: int, text_full: str, text_public: str, rows: list) -> tg.Out:
    """Send a card to the log chat, or to the group without the private text."""
    r = await logs.route(chat_id)
    if r and r["status"] == "active":
        out = await tg.send(r["dest_id"], text_full + "\n" + await logs.header(chat_id), rows=rows, thread_id=r["topic_id"])
        if out.ok:
            return out
    return await tg.send(chat_id, text_public, rows=rows, silent=True)


def ulabel(uid: int, name: str, username: str | None = None) -> str:
    un = f" @{esc(username)}" if username else ""
    return f'<a href="tg://user?id={uid}">{esc(name or uid)}</a>{un} [<code>{uid}</code>]'


# join requests
async def on_join_request(req: ChatJoinRequest) -> None:
    u = req.from_user
    await note_user(u, None, source="join_request")
    chat_id = req.chat.id
    eff = await S.values(chat_id)
    if not eff["jr_desk"]:
        return                                  # leave it in Telegram's own queue
    await RT.db.run("INSERT INTO rz_join_requests VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET "
                    "user_chat_id=excluded.user_chat_id, ts=excluded.ts, status='pending', bio=excluded.bio, screen=excluded.screen",
                    (chat_id, u.id, req.user_chat_id, u.full_name[:64], u.username, (req.bio or "")[:200], now(), "pending", "none"))
    lang = eff["lang"]
    fa = lang == "fa"
    screen = "none"
    if eff["jr_question"]:
        q = (f"👋 درخواست عضویت «{esc(req.chat.title)}» ثبت شد.\n\n{esc(eff['jr_question'])}" if fa else
             f"👋 Your request to join “{esc(req.chat.title)}” was received.\n\n{esc(eff['jr_question'])}")
        out = await tg.send(req.user_chat_id, q, rows=[[("✅ می‌پذیرم" if fa else "✅ I agree", f"jr:a:{chat_id}")]])
        screen = "sent" if out.ok else "dm_failed"
        await RT.db.run("UPDATE rz_join_requests SET screen=? WHERE chat_id=? AND user_id=?", (screen, chat_id, u.id))
    txt = (("📥 <b>درخواست عضویت</b>\n" if fa else "📥 <b>Join request</b>\n") + ulabel(u.id, u.full_name, u.username) +
           (f"\n📝 {esc(short(req.bio, 150))}" if req.bio else "") +
           (f"\n🔎 {'غربالگری' if fa else 'screening'}: {screen}" if eff["jr_question"] else ""))
    await admin_card(chat_id, txt, txt, desk_rows(chat_id, u.id, lang))


def desk_rows(chat_id: int, user_id: int, lang: str) -> list:
    fa = lang == "fa"
    return [[("✅ تأیید" if fa else "✅ Approve", f"jr:y:{chat_id}:{user_id}"), ("❌ رد" if fa else "❌ Decline", f"jr:n:{chat_id}:{user_id}")]]


async def decide_join(chat_id: int, user_id: int, approve: bool, actor: int | None, lang: str) -> tuple[bool, str]:
    fa = lang == "fa"
    row = await RT.db.one("SELECT * FROM rz_join_requests WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    if row and row["status"] != "pending":
        return False, ("این درخواست قبلاً رسیدگی شده: " if fa else "Already handled: ") + row["status"]
    fn = RT.bot.approve_chat_join_request if approve else RT.bot.decline_chat_join_request
    out = await tg.call(fn, chat_id=chat_id, user_id=user_id)
    if not out.ok:
        if out.kind == "not_found":
            await RT.db.run("UPDATE rz_join_requests SET status='gone' WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            return False, ("درخواست دیگه وجود نداره." if fa else
                           "Request no longer exists.")
        if out.kind == "rights":
            return False, "دسترسی «دعوت کاربر» لازمه." if fa else "Needs 'Invite users' right."
        return False, esc(out.desc)
    st = "approved" if approve else "declined"
    await RT.db.run("UPDATE rz_join_requests SET status=?, decided_by=?, decided_at=? WHERE chat_id=? AND user_id=?",
                    (st, actor, now(), chat_id, user_id))
    await audit.record(chat_id, actor, "admin" if actor else "auto", "join_approve" if approve else "join_decline", target_id=user_id,
                       target_name=(row or {}).get("name") or "", log_line=f"{'✅' if approve else '❌'} <b>#JOIN_{st.upper()}</b> <code>{user_id}</code>")
    return True, (("✅ تأیید شد." if approve else "❌ رد شد.") if fa else ("✅ Approved." if approve else "❌ Declined."))


async def screen_agree(chat_id: int, user_id: int) -> tuple[bool, str]:
    row = await RT.db.one("SELECT * FROM rz_join_requests WHERE chat_id=? AND user_id=? AND status='pending'", (chat_id, user_id))
    lang, _ = await lang_tz(chat_id)
    fa = lang == "fa"
    if not row:
        return False, "درخواست فعالی پیدا نشد." if fa else "No pending request found."
    await RT.db.run("UPDATE rz_join_requests SET screen='agreed' WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    if await S.get(chat_id, "jr_autoapprove"):
        ok, msg = await decide_join(chat_id, user_id, True, None, lang)
        return ok, msg
    return True, "✅ ثبت شد." if fa else "✅ Noted."


# tickets
async def open_ticket(chat_id: int, user, text: str, lang: str) -> tuple[bool, str, int | None]:
    fa = lang == "fa"
    if not await S.get(chat_id, "tickets_on"):
        return False, "تیکت خاموشه." if fa else "Tickets are off.", None
    text = (text or "").strip()
    if len(text) < 3:
        return False, "متن رو بنویس." if fa else "Write the text.", None
    open_n = await RT.db.val("SELECT COUNT(*) FROM rz_tickets WHERE chat_id=? AND user_id=? AND status IN ('open','claimed')", (chat_id, user.id), 0)
    last = await RT.db.val("SELECT MAX(created_at) FROM rz_tickets WHERE chat_id=? AND user_id=?", (chat_id, user.id), 0)
    if open_n >= 3 or now() - (last or 0) < 300:
        return False, ("تیکت باز داری. بعداً امتحان کن." if fa else
                       "You have an open ticket. Try later."), None
    tid, _ = await RT.db.run("INSERT INTO rz_tickets(chat_id,user_id,text,status,created_at) VALUES(?,?,?,?,?)",
                             (chat_id, user.id, text[:1500], "open", now()))
    full = (f"🎫 <b>{'تیکت' if fa else 'Ticket'} #{tid}</b>\n👤 {ulabel(user.id, user.full_name, user.username)}\n💬 {esc(text[:1500])}")
    public = (f"🎫 <b>{'تیکت جدید' if fa else 'New ticket'} #{tid}</b> — {ulabel(user.id, user.full_name)}\n"
              f"<i>{'فقط برای ادمین‌ها' if fa else 'Admins only'}</i>")
    rows = [[("👁 مشاهده" if fa else "👁 View", f"tk:v:{tid}"), ("🙋 برعهده" if fa else "🙋 Claim", f"tk:c:{tid}"),
             ("✅ بستن" if fa else "✅ Close", f"tk:x:{tid}")]]
    out = await admin_card(chat_id, full, public, rows)
    if not out.ok:
        await RT.db.run("UPDATE rz_tickets SET last_error=? WHERE id=?", (f"card: {out.kind}", tid))
        return False, ("تیکت ثبت شد ولی به ادمین‌ها نرسید." if fa else "Ticket saved, admins not notified."), tid
    dm = await RT.db.val("SELECT dm_ok FROM rz_users WHERE user_id=?", (user.id,), 0)
    note = "" if dm else (("\nبرای جواب، ربات رو استارت کن." if fa else "\nStart the bot to get the reply."))
    return True, (f"✅ تیکت #{tid} فرستاده شد." if fa else f"✅ Ticket #{tid} sent.") + note, tid


async def ticket_reply(chat_id: int, tid: int, text: str, actor: int, lang: str) -> tuple[bool, str]:
    fa = lang == "fa"
    t = await RT.db.one("SELECT * FROM rz_tickets WHERE id=? AND chat_id=?", (tid, chat_id))
    if not t:
        return False, "تیکت پیدا نشد." if fa else "Ticket not found."
    if t["status"] == "closed":
        return False, "تیکت بسته‌ست." if fa else "Ticket is closed."
    title = await RT.db.val("SELECT title FROM rz_chats WHERE chat_id=?", (chat_id,), "")
    out = await tg.send(t["user_id"], (f"📩 <b>{esc(title)}</b> · تیکت #{tid}\n" if fa else
                                       f"📩 <b>{esc(title)}</b> · ticket #{tid}\n") + esc(text[:3000]))
    if not out.ok:
        await RT.db.run("UPDATE rz_tickets SET last_error=? WHERE id=?", (f"reply: {out.kind}", tid))
        return False, ("❌ نرسید. کاربر ربات رو استارت نکرده." if fa else
                       "❌ Not delivered. User hasn't started the bot.")
    await RT.db.run("UPDATE rz_tickets SET replies=replies+1, status=CASE WHEN status='open' THEN 'claimed' ELSE status END, "
                    "claimed_by=COALESCE(claimed_by, ?) WHERE id=?", (actor, tid))
    return True, "✅ رسید." if fa else "✅ Delivered."


# appeals
async def open_appeal(chat_id: int, case_no: int, user, text: str, lang: str) -> tuple[bool, str]:
    fa = lang == "fa"
    if not await S.get(chat_id, "appeals_on"):
        return False, "تجدیدنظر خاموشه." if fa else "Appeals are off."
    case = await RT.db.one("SELECT * FROM rz_cases WHERE chat_id=? AND case_no=?", (chat_id, case_no))
    if not case or case["target_id"] != user.id:
        return False, "این پرونده به شما مربوط نیست." if fa else "That case is not yours."
    if case["status"] != "active":
        return False, "پرونده فعال نیست." if fa else "Case not active."
    if await RT.db.one("SELECT 1 FROM rz_appeals WHERE chat_id=? AND case_no=? AND status='pending'", (chat_id, case_no)):
        return False, "قبلاً فرستادی." if fa else "Already sent."
    aid, _ = await RT.db.run("INSERT INTO rz_appeals(chat_id,case_no,user_id,text,status,created_at) VALUES(?,?,?,?,?,?)",
                             (chat_id, case_no, user.id, text[:1500], "pending", now()))
    await RT.db.run("UPDATE rz_cases SET status='active' WHERE chat_id=? AND case_no=?", (chat_id, case_no))
    full = (f"⚖️ <b>{'تجدیدنظر' if fa else 'Appeal'} #{aid}</b> — {'پرونده' if fa else 'case'} #{case_no} ({esc(audit.action_name(case['action'], lang))})\n"
            f"👤 {ulabel(user.id, user.full_name, user.username)}\n📝 {esc(case['reason'] or '—')}\n💬 {esc(text[:1500])}")
    public = (f"⚖️ <b>{'تجدیدنظر جدید' if fa else 'New appeal'} #{aid}</b> — {'پرونده' if fa else 'case'} #{case_no}\n"
              f"<i>{'فقط برای ادمین‌ها' if fa else 'Admins only'}</i>")
    rows = [[("👁 مشاهده" if fa else "👁 View", f"ap:v:{aid}"), ("✅ پذیرش" if fa else "✅ Accept", f"ap:y:{aid}"),
             ("❌ رد" if fa else "❌ Reject", f"ap:n:{aid}")]]
    await admin_card(chat_id, full, public, rows)
    return True, (f"✅ تجدیدنظر #{aid} فرستاده شد." if fa else
                  f"✅ Appeal #{aid} sent.")


async def decide_appeal(aid: int, accept: bool, actor: int, chat_id: int, note: str = "") -> tuple[bool, str]:
    lang, _ = await lang_tz(chat_id)
    fa = lang == "fa"
    ap = await RT.db.one("SELECT * FROM rz_appeals WHERE id=? AND chat_id=?", (aid, chat_id))
    if not ap:
        return False, "تجدیدنظر پیدا نشد." if fa else "Appeal not found."
    if ap["status"] != "pending":
        return False, ("قبلاً بررسی شده: " if fa else "Already decided: ") + ap["status"]
    _, n = await RT.db.run("UPDATE rz_appeals SET status='deciding' WHERE id=? AND status='pending'", (aid,))
    if n != 1:
        return False, "قبلاً بررسی شده." if fa else "Already decided."
    result_txt = ""
    if accept:
        case = await RT.db.one("SELECT * FROM rz_cases WHERE chat_id=? AND case_no=?", (chat_id, ap["case_no"]))
        ok, msg = await audit.undo(chat_id, case["action_id"], actor, lang) if case and case["action_id"] else (False, "no action")
        if not ok:
            await RT.db.run("UPDATE rz_appeals SET status='pending' WHERE id=?", (aid,))
            return False, ("❌ ناموفق: " if fa else "❌ Failed: ") + msg
        result_txt = msg
        await audit.record(chat_id, actor, "admin", "appeal_accept", target_id=ap["user_id"], params={"appeal": aid, "case": ap["case_no"]},
                           log_line=f"⚖️ <b>#APPEAL_ACCEPTED</b> #{aid} case #{ap['case_no']} by <code>{actor}</code>")
    await RT.db.run("UPDATE rz_appeals SET status=?, reviewer_id=?, decided_at=?, note=? WHERE id=?",
                    ("accepted" if accept else "rejected", actor, now(), note[:300], aid))
    title = await RT.db.val("SELECT title FROM rz_chats WHERE chat_id=?", (chat_id,), "")
    dm = await tg.send(ap["user_id"], ((f"⚖️ تجدیدنظر شما در «{esc(title)}» " + ("پذیرفته شد ✅" if accept else "رد شد ❌")) if fa else
                                       (f"⚖️ Your appeal in “{esc(title)}” was " + ("accepted ✅" if accept else "rejected ❌"))))
    dm_note = "" if dm.ok else (" (به کاربر نرسید)" if fa else " (user not notified)")
    return True, (("✅ پذیرفته شد: " + result_txt) if accept else "❌ رد شد.") + dm_note if fa else \
        (("✅ Accepted: " + result_txt) if accept else "❌ Rejected.") + dm_note


# forum topics
async def topic_cmd(c: Cmd) -> str:
    sub = (c.args[0].lower() if c.args else "info")
    rest = " ".join(c.args[1:]).strip()
    if not c.is_forum:
        return c.t("تاپیک‌های گروه خاموشه.", "Topics are not enabled.")
    if sub == "info":
        return c.t(f"🧵 تاپیک: <code>{c.thread_id or 'General'}</code>\n"
                   "<code>/topic new نام</code> · <code>/topic rename نام</code> · <code>/topic close</code> · <code>/topic reopen</code> · "
                   "<code>/topic delete</code> · <code>/topic logs</code> · <code>/topic announce</code>",
                   f"🧵 Topic: <code>{c.thread_id or 'General'}</code>\n<code>/topic new name</code> · "
                   "<code>/topic rename name</code> · <code>/topic close</code> · <code>/topic reopen</code> · <code>/topic delete</code> · "
                   "<code>/topic logs</code> · <code>/topic announce</code>")
    if not await authz.bot_has(c.chat_id, "topics", fresh=True) and sub in ("new", "rename", "close", "reopen", "delete"):
        return c.t("❌ دسترسی «مدیریت تاپیک» لازمه.", "❌ Needs 'Manage topics' right.")
    th = c.thread_id
    if sub == "new":
        if not rest:
            return c.t("اسم تاپیک؟", "Topic name?")
        out = await tg.call(RT.bot.create_forum_topic, idempotent=False, chat_id=c.chat_id, name=rest[:128])
        if not out.ok:
            return c.t("❌ ناموفق: ", "❌ Failed: ") + esc(out.desc)
        await audit.record(c.chat_id, c.actor_id, "admin", "topic", params={"op": "new", "id": out.value.message_thread_id})
        return c.t(f"✅ تاپیک «{esc(rest)}» ساخته شد.", f"✅ Topic “{esc(rest)}” created.")
    if sub in ("rename", "close", "reopen", "delete") and not th:
        if sub in ("close", "reopen"):
            fn = RT.bot.close_general_forum_topic if sub == "close" else RT.bot.reopen_general_forum_topic
            out = await tg.call(fn, chat_id=c.chat_id)
            return (c.t("✅ تاپیک General انجام شد.", "✅ Done for General.") if out.ok else c.t("❌ خطا: ", "❌ Error: ") + esc(out.desc))
        return c.t("داخل خود تاپیک بفرست.", "Send it inside the topic.")
    if sub == "rename":
        if not rest:
            return c.t("اسم جدید؟", "New name?")
        out = await tg.call(RT.bot.edit_forum_topic, chat_id=c.chat_id, message_thread_id=th, name=rest[:128])
    elif sub == "close":
        out = await tg.call(RT.bot.close_forum_topic, chat_id=c.chat_id, message_thread_id=th)
    elif sub == "reopen":
        out = await tg.call(RT.bot.reopen_forum_topic, chat_id=c.chat_id, message_thread_id=th)
    elif sub == "delete":
        tok = await confirm.create(c.chat_id, c.actor_id, "topic_delete", {"thread": th}, right="topics")
        await c.say(c.t("⚠️ تاپیک و همهٔ پیام‌هاش حذف بشه؟", "⚠️ Delete this topic and all its messages?"),
                    rows=confirm.buttons(tok, c.lang))
        return ""
    elif sub == "logs":
        ok, msg = await logs.verify_destination(c.chat_id, c.chat_id, th, c.actor_id, c.lang)
        return msg
    elif sub == "announce":
        await S.set_value(c.chat_id, "announce_topic", th, c.actor_id)
        return c.t("✅ اطلاعیه‌ها اینجا میاد.", "✅ Announcements will post here.")
    else:
        return c.t("نامعتبر. /topic", "Invalid. /topic")
    if not out.ok:
        if out.kind == "not_found":
            return c.t("❌ تاپیک پیدا نشد.", "❌ Topic not found.")
        return c.t("❌ خطای تلگرام: ", "❌ Telegram error: ") + esc(out.desc)
    await audit.record(c.chat_id, c.actor_id, "admin", "topic", params={"op": sub, "id": th})
    return c.t("✅ انجام شد.", "✅ Done.")


@confirm.handler("topic_delete")
async def _topic_delete(p: dict, actor: int, chat_id: int, message) -> str:
    out = await tg.call(RT.bot.delete_forum_topic, chat_id=chat_id, message_thread_id=p["thread"])
    lang, _ = await lang_tz(chat_id)
    if out.ok:
        await audit.record(chat_id, actor, "admin", "topic", params={"op": "delete", "id": p["thread"]})
        return "✅ تاپیک حذف شد." if lang == "fa" else "✅ Topic deleted."
    return ("❌ خطا: " if lang == "fa" else "❌ Error: ") + esc(out.desc)
