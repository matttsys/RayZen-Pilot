"""Commands, part 2: start/help, AI, scheduling, digest, backup, desk, topics, watchdog, privacy."""
from __future__ import annotations

import io
import json
import re

from telegram import ChatPermissions, InputFile

from pilot import ai, audit, authz, confirm, desk, locks, logs, planner, settings as S, tg, tools, ui
from pilot.commands import command, onoff, say, set_setting, toggle_cmd
from pilot.ctx import Cmd
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.targets import resolve
from pilot.util import esc, fmt_dt, fmt_duration, norm_digits, now, parse_when, rule_label, short


def bot_link(payload: str = "") -> str:
    return f"https://t.me/{RT.bot_username}" + (f"?start={payload}" if payload else "")


def add_link() -> str:
    return (f"https://t.me/{RT.bot_username}?startgroup=true&admin=delete_messages+restrict_members+pin_messages+"
            "invite_users+change_info+manage_topics")


async def dm(user_id: int, text: str, rows=None, doc: tuple[str, bytes] | None = None) -> tg.Out:
    if doc:
        return await tg.call(RT.bot.send_document, idempotent=False, chat_id=user_id,
                             document=InputFile(io.BytesIO(doc[1]), filename=doc[0]), caption=text[:1000])
    return await tg.send(user_id, text, rows=rows)


# start / help / settings
@command("start", scope="any", admin=False, section="general")
async def k_start(c: Cmd):
    if not c.is_private:
        return await say(c, c.t("✅ آنلاین. /help · /settings", "✅ Online. /help · /settings"))
    await RT.db.run("UPDATE rz_users SET dm_ok=1 WHERE user_id=?", (c.actor_id,))
    p = c.args[0] if c.args else ""
    m = re.fullmatch(r"ap_(\d+)_(\d+)", p)
    if m:
        chat_id, case_no = -int(m.group(1)), int(m.group(2))
        case = await RT.db.one("SELECT * FROM rz_cases WHERE chat_id=? AND case_no=?", (chat_id, case_no))
        if not case or case["target_id"] != c.actor_id:
            return await say(c, c.t("این پرونده به شما مربوط نیست.", "That case is not yours."))
        await desk.set_draft(c.actor_id, {"kind": "appeal", "chat": chat_id, "case": case_no})
        return await say(c, c.t(f"⚖️ تجدیدنظر برای پرونده #{case_no} ({esc(audit.action_name(case['action'], 'fa'))}).\n"
                                "دلیلت رو در یک پیام بفرست (۱۵ دقیقه).",
                                f"⚖️ Appeal for case #{case_no}. Send your reason in one message (15 min)."))
    m = re.fullmatch(r"tk_(\d+)", p)
    if m:
        chat_id = -int(m.group(1))
        mem = await authz.member(chat_id, c.actor_id, fresh=True)
        if mem is None or mem.status in ("left", "kicked"):
            return await say(c, c.t("فقط اعضای گروه.", "Only members of that group can open a ticket."))
        await desk.set_draft(c.actor_id, {"kind": "ticket", "chat": chat_id})
        return await say(c, c.t("🎫 پیامت رو برای ادمین‌ها بفرست (۱۵ دقیقه).",
                                "🎫 Send your message to the admins (15 min)."))
    if p == "rem":
        return await say(c, c.t("✅ یادآوری‌ها اینجا میاد.", "✅ Reminders will arrive here."))
    from pilot import SOURCE_URL, hooks
    if not p and await hooks.first(hooks.PRIVATE_START, c):
        return                                   # owner-hosted edition: channel request handled it
    await say(c, c.t("👋 <b>RayZen Pilot</b>\nربات مدیریت گروه.\n\n"
                     "/help · /mycases · /mydata\nEnglish: /lang en",
                     "👋 <b>RayZen Pilot</b>\nGroup management bot.\n\n"
                     "/help · /mycases · /mydata\nفارسی: /lang fa"),
              [[(c.t("➕ افزودن به گروه", "➕ Add to a group"), add_link())],
               [(c.t("کد منبع (AGPL)", "Source code (AGPL)"), SOURCE_URL)]])


@command("help", scope="any", admin=False, section="general")
async def k_help(c: Cmd):
    from pilot import catalog
    role = await catalog.role_of(c.chat_id, c.chat_type, c.actor_id, c.anon)
    q = c.rest.strip()
    txt, rows = catalog.search(q, c.lang, role, c.is_private) if q else catalog.main_page(c.lang, role)
    await say(c, txt, rows)


@command("settings", "config", right=None, section="general")
async def k_settings(c: Cmd):
    txt, rows = await ui.settings_main(c.chat_id, c.chat_title, c.lang)
    await say(c, txt, rows)


@command("ai", "ask", scope="any", admin=False, section="ai")
async def k_ai(c: Cmd):
    await ai.handle(c, c.rest)


# info / language / time
@command("id", scope="any", admin=False, section="general")
async def k_id(c: Cmd):
    lines = [c.t(f"🆔 این چت: <code>{c.chat_id}</code>", f"🆔 This chat: <code>{c.chat_id}</code>")]
    if c.thread_id:
        lines.append(c.t(f"🧵 تاپیک: <code>{c.thread_id}</code>", f"🧵 Topic: <code>{c.thread_id}</code>"))
    if c.actor_id:
        lines.append(c.t(f"👤 شما: <code>{c.actor_id}</code>", f"👤 You: <code>{c.actor_id}</code>"))
    if c.reply:
        if c.reply.user_id:
            lines.append(c.t(f"↩️ کاربر ریپلای‌شده: <code>{c.reply.user_id}</code>", f"↩️ Replied user: <code>{c.reply.user_id}</code>"))
        if c.reply.sender_chat_id:
            lines.append(c.t(f"📢 فرستنده: <code>{c.reply.sender_chat_id}</code>", f"📢 Sender chat: <code>{c.reply.sender_chat_id}</code>"))
    await say(c, "\n".join(lines))


@command("info", right=None, section="mod")
async def k_info(c: Cmd):
    t = await resolve(c)
    if not t.ok:
        return await say(c, t.err)
    if t.sender_chat_id:
        return await say(c, f"📢 {t.label()}")
    mem = await authz.member(c.chat_id, t.user_id, fresh=True)
    st = mem.status if mem else c.t("نامشخص", "unknown/never joined")
    w = await RT.db.val("SELECT COUNT(*) FROM rz_warns WHERE chat_id=? AND user_id=? AND active=1", (c.chat_id, t.user_id), 0)
    cs = await RT.db.val("SELECT COUNT(*) FROM rz_cases WHERE chat_id=? AND target_id=?", (c.chat_id, t.user_id), 0)
    row = await RT.db.one("SELECT username, last_seen, source FROM rz_users WHERE user_id=?", (t.user_id,))
    seen = fmt_dt((row or {}).get("last_seen"), c.tz, c.lang, with_tz=False) if row else "—"
    await say(c, c.t(f"👤 {t.label()}\nوضعیت در گروه: <b>{esc(st)}</b>\nاخطار: {w} · پرونده: {cs}\nآخرین بازدید: {seen}",
                     f"👤 {t.label()}\nStatus here: <b>{esc(st)}</b>\nWarns: {w} · Cases: {cs}\nLast seen: {seen}"))


@command("lang", "language", right="info", scope="any", section="tools")
async def k_lang(c: Cmd):
    v = (c.args[0].lower() if c.args else "")
    if v not in ("fa", "en"):
        return await say(c, c.t("استفاده: <code>/lang fa</code> یا <code>/lang en</code>", "Usage: <code>/lang fa</code> or <code>/lang en</code>"))
    if c.is_private:
        await RT.db.run("UPDATE rz_users SET lang=? WHERE user_id=?", (v, c.actor_id))
        return await say(c, "✅ زبان: فارسی" if v == "fa" else "✅ Language: English")
    await set_setting(c, "lang", v)
    await say(c, "✅ زبان گروه: فارسی" if v == "fa" else "✅ Group language: English")


@command("timezone", "tz", right="info", section="tools")
async def k_tz(c: Cmd):
    if not c.args:
        return await say(c, c.t(f"🕒 منطقهٔ زمانی: <code>{esc(c.tz)}</code>\nتغییر: <code>/timezone Asia/Tehran</code>",
                                f"🕒 Timezone: <code>{esc(c.tz)}</code>\nChange: <code>/timezone Europe/Berlin</code>"))
    txt = await set_setting(c, "tz", c.args[0])
    if txt.startswith("✅"):
        await planner.night_tick()      # recheck night lock with the new timezone
    await say(c, txt)


# health / digest
@command("health", "dashboard", right=None, section="tools")
async def k_health(c: Cmd):
    await say(c, await tools.health_text(c.chat_id, c.lang, c.tz, c.is_forum))


@command("digest", right=None, section="tools")
async def k_digest(c: Cmd):
    a = [norm_digits(x).lower() for x in c.args]
    if a and a[0] in ("off", "خاموش"):
        await RT.db.run("UPDATE rz_digest SET enabled=0 WHERE chat_id=?", (c.chat_id,))
        return await say(c, c.t("✅ گزارش روزانه خاموش شد.", "✅ Daily digest off."))
    if a and a[0] in ("daily", "روزانه"):
        from pilot.util import parse_hhmm
        hm = parse_hhmm(a[1]) if len(a) > 1 else None
        if not hm:
            return await say(c, c.t("استفاده: <code>/digest daily 22:00</code>", "Usage: <code>/digest daily 22:00</code>"))
        at = f"{hm[0]:02d}:{hm[1]:02d}"
        await RT.db.run("INSERT INTO rz_digest(chat_id,at,enabled,last_key,requester) VALUES(?,?,1,NULL,?) ON CONFLICT(chat_id) DO UPDATE SET "
                        "at=excluded.at, enabled=1, requester=excluded.requester", (c.chat_id, at, c.actor_id))
        r = await logs.route(c.chat_id)
        dest = c.t("لاگ", "log chat") if r and r["status"] == "active" else c.t("پیوی", "private chat")
        return await say(c, c.t(f"✅ گزارش روزانه ساعت {at} ← {dest}",
                                f"✅ Daily digest at {at} → {dest}"))
    hours = int(a[0]) if a and a[0].isdigit() and 1 <= int(a[0]) <= 168 else 24
    note = None
    if "ai" in a and RT.ai and RT.cfg.ai_configured:
        facts = await tools.digest_facts(c.chat_id, hours)
        try:
            raw, _ = await RT.ai.chat([{"role": "system", "content": "Summarize ONLY these numbers for group admins in 2-3 short sentences in "
                                        + ("Persian" if c.lang == "fa" else "English") + ". Do not add facts, names or guesses."},
                                       {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}], json_mode=False, max_tokens=500,
                                      user_tag=str(c.actor_id or c.chat_id))
            note = ai.hide_model(raw.strip())
        except ai.AIError as e:
            note = c.t(f"(خلاصه ناموفق: {ai.ERR_FA.get(e.kind, e.kind)})", f"(summary failed: {ai.ERR_EN.get(e.kind, e.kind)})")
    await say(c, await tools.digest_text(c.chat_id, c.lang, c.tz, hours, ai_note=note))


# announcements
def _remainder_html(c: Cmd, consumed: int) -> str:
    parts = c.rest_html.split(None, consumed)
    return parts[consumed] if len(parts) > consumed else ""


async def create_announcement(c: Cmd, ts: float, rule: str, body: str, src_msg: int | None, pin: bool, status: str) -> int:
    thread = await S.get(c.chat_id, "announce_topic") or (c.thread_id if c.is_forum else None)
    aid, _ = await RT.db.run("INSERT INTO rz_announcements(chat_id,thread_id,body,src_chat_id,src_msg_id,next_run,rule,status,created_by,created_at,pin) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (c.chat_id, thread, body[:3800], c.chat_id if src_msg else None, src_msg, ts, rule,
                                                                status, c.actor_id, now(), int(pin)))
    return aid


WHEN_HELP = ("زمان: <code>2h</code> · <code>21:00</code> · <code>فردا 09:00</code> · <code>2026-01-05 10:00</code> · "
             "<code>daily 09:00</code> · <code>weekly 18:00</code> · <code>every 6h</code>",
             "When: <code>2h</code> · <code>21:00</code> · <code>tomorrow 09:00</code> · <code>2026-01-05 10:00</code> · "
             "<code>daily 09:00</code> · <code>weekly 18:00</code> · <code>every 6h</code>")


@command("announce", "schedule", right="info", section="schedule")
async def k_announce(c: Cmd):
    a = list(c.args)
    if a and a[0].lower() in ("edit", "cancel", "pause", "resume"):
        sub = a[0].lower()
        if len(a) < 2 or not norm_digits(a[1]).lstrip("#").isdigit():
            return await say(c, c.t(f"استفاده: <code>/announce {sub} شماره</code>", f"Usage: <code>/announce {sub} id</code>"))
        aid = int(norm_digits(a[1]).lstrip("#"))
        if sub == "edit":
            body = _remainder_html(c, 2)
            if not body:
                return await say(c, c.t("متن جدید را بنویسید.", "Give the new text."))
            _, n = await RT.db.run("UPDATE rz_announcements SET body=?, src_chat_id=NULL, src_msg_id=NULL WHERE id=? AND chat_id=? AND status IN ('active','paused','preview')",
                                   (body[:3800], aid, c.chat_id))
            return await say(c, c.t("✅ متن به‌روز شد.", "✅ Text updated.") if n else c.t("پیدا نشد.", "Not found."))
        return await say(c, await ann_control(c.chat_id, aid, {"cancel": "x", "pause": "p", "resume": "r"}[sub], c.lang, c.tz))
    pin = False
    if a and a[0].lower() in ("pin", "سنجاق"):
        pin, a = True, a[1:]
    ts, rule, used = parse_when(a, c.tz)
    if not ts:
        return await say(c, c.t("<code>/announce زمان متن</code> یا ریپلای + <code>/announce زمان</code>\n" + WHEN_HELP[0],
                                "<code>/announce when text</code> or reply + <code>/announce when</code>\n" + WHEN_HELP[1]))
    body = _remainder_html(c, used + (1 if pin else 0))
    src = c.reply.message_id if (c.reply and not body) else None
    if not body and not src:
        return await say(c, c.t("متن یا ریپلای لازمه.", "Text or reply needed."))
    aid = await create_announcement(c, ts, rule, body, src, pin, "preview")
    tok = await confirm.create(c.chat_id, c.actor_id, "ann_confirm", {"id": aid}, right="info", ttl=900)
    shown = c.t("📎 پیام ریپلای‌شده", "📎 replied message") if src else body
    await say(c, c.t(f"📣 <b>پیش‌نمایش اطلاعیه #{aid}</b> — {rule_label(rule, 'fa')}\nاولین ارسال: {fmt_dt(ts, c.tz, 'fa')}" + (" · سنجاق" if pin else "") +
                     f"\n———\n{shown}\n———",
                     f"📣 <b>Announcement #{aid} preview</b> — {rule_label(rule, 'en')}\nFirst run: {fmt_dt(ts, c.tz, 'en')}" + (" · pinned" if pin else "") +
                     f"\n———\n{shown}\n———"), confirm.buttons(tok, c.lang, c.t("✅ زمان‌بندی", "✅ Schedule")))


@confirm.handler("ann_confirm")
async def _ann_ok(p, presser, chat_id, message):
    _, n = await RT.db.run("UPDATE rz_announcements SET status='active' WHERE id=? AND chat_id=? AND status='preview'", (p["id"], chat_id))
    from pilot.ctx import lang_tz
    lang, tz = await lang_tz(chat_id)
    a = await RT.db.one("SELECT * FROM rz_announcements WHERE id=?", (p["id"],))
    if not n or not a:
        return "منقضی شده." if lang == "fa" else "Expired."
    if a["next_run"] <= now():
        await RT.db.run("UPDATE rz_announcements SET next_run=? WHERE id=?", (now() + 5, a["id"]))
    await audit.record(chat_id, presser, "admin", "setting", params={"announcement": a["id"], "rule": a["rule"]},
                       log_line=f"📣 <b>#ANNOUNCEMENT</b> #{a['id']} {a['rule']} next {fmt_dt(a['next_run'], tz, 'en')}")
    return (f"✅ اطلاعیه #{a['id']} ثبت شد. ارسال: {fmt_dt(max(a['next_run'], now()), tz, lang)}" if lang == "fa" else
            f"✅ Announcement #{a['id']} scheduled. Next: {fmt_dt(max(a['next_run'], now()), tz, lang)}")


async def ann_control(chat_id: int, aid: int, op: str, lang: str, tz: str) -> str:
    fa = lang == "fa"
    a = await RT.db.one("SELECT * FROM rz_announcements WHERE id=? AND chat_id=?", (aid, chat_id))
    if not a:
        return "پیدا نشد." if fa else "Not found."
    if op == "x":
        await RT.db.run("UPDATE rz_announcements SET status='cancelled' WHERE id=? AND status<>'sending'", (aid,))
        return f"🗑 اطلاعیه #{aid} لغو شد." if fa else f"🗑 Announcement #{aid} cancelled."
    if op == "p":
        _, n = await RT.db.run("UPDATE rz_announcements SET status='paused' WHERE id=? AND status='active'", (aid,))
        return (f"⏸ اطلاعیه #{aid} متوقف شد." if fa else f"⏸ Paused #{aid}.") if n else ("فعال نبود." if fa else "It wasn't active.")
    if a["status"] != "paused":
        return "متوقف نبود." if fa else "It wasn't paused."
    nxt = a["next_run"]
    if nxt <= now():
        if a["rule"] == "once":
            return "زمانش گذشته." if fa else "Time has passed."
        from pilot.util import next_after
        nxt = next_after(a["rule"], a["next_run"], tz)
    await RT.db.run("UPDATE rz_announcements SET status='active', next_run=? WHERE id=?", (nxt, aid))
    return (f"▶️ ادامه. ارسال بعدی: {fmt_dt(nxt, tz, lang)}" if fa else f"▶️ Resumed; next {fmt_dt(nxt, tz, lang)}")


@command("announcements", "schedules", right="info", section="schedule")
async def k_announcements(c: Cmd):
    rows = await RT.db.all("SELECT * FROM rz_announcements WHERE chat_id=? AND status IN ('active','paused','failed','uncertain') ORDER BY next_run LIMIT 15",
                           (c.chat_id,))
    if not rows:
        return await say(c, c.t("📣 اطلاعیه‌ای نیست.", "📣 No announcements."))
    btns = []
    for a in rows:
        b = [("⏸ #%d" % a["id"], f"an:p:{a['id']}")] if a["status"] == "active" else [("▶️ #%d" % a["id"], f"an:r:{a['id']}")]
        b.append(("🗑 #%d" % a["id"], f"an:x:{a['id']}"))
        btns.append(b)
    await say(c, "\n".join([c.t("📣 <b>اطلاعیه‌ها</b>", "📣 <b>Announcements</b>")] + [planner.ann_line(a, c.lang, c.tz) for a in rows]), btns)


# reminders
async def create_reminder(c: Cmd, ts: float, text: str, scope: str) -> tuple[int, str]:
    chat = c.chat_id if (scope == "group" or c.is_group) else c.actor_id
    rid, _ = await RT.db.run("INSERT INTO rz_reminders(chat_id,thread_id,user_id,scope,text,due,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                             (chat, c.thread_id if c.is_forum else None, c.actor_id, scope, text[:1500], ts, "active", now()))
    where = {"group": ("گروه", "group"), "here": ("همین‌جا", "here")}.get(scope, ("پیوی", "private"))
    msg = c.t(f"⏰ یادآوری #{rid}: {fmt_dt(ts, c.tz, 'fa')} ({where[0]})", f"⏰ Reminder #{rid}: {fmt_dt(ts, c.tz, 'en')} ({where[1]})")
    if scope == "me" and not c.is_private:
        ok = await RT.db.val("SELECT dm_ok FROM rz_users WHERE user_id=?", (c.actor_id,), 0)
        if not ok:
            msg += c.t("\n⚠️ ربات رو استارت کن، وگرنه یادآوری همین‌جا با منشن میاد.",
                       "\n⚠️ Start the bot, otherwise it's posted here with a mention.")
    return rid, msg


@command("remind", "reminder", scope="any", admin=False, section="schedule")
async def k_remind(c: Cmd):
    a = list(c.args)
    scope = "me"
    if a and a[0].lower() in ("group", "گروه"):
        if not c.is_group:
            return await say(c, c.t("فقط داخل گروه.", "Groups only."))
        ok, _ = await authz.check(c.chat_id, c.actor_id, "info")
        if not ok:
            return await say(c, c.t("⛔️ فقط ادمین‌ها. شخصی: <code>/remind 2h متن</code>",
                                    "⛔️ Admins only. Personal: <code>/remind 2h text</code>"))
        scope, a = "group", a[1:]
    if not c.actor_id:
        return await say(c, c.t("با حساب ناشناس نمیشه.", "Not available for anonymous admins."))
    ts, rule, used = parse_when(a, c.tz)
    text = " ".join(a[used:]).strip()
    if not ts or not text:
        return await say(c, c.t("<code>/remind 2h متن</code> · <code>/remind فردا 09:00 متن</code>", "<code>/remind 2h text</code>"))
    if ts <= now() + 20:
        return await say(c, c.t("زمان باید در آینده باشد.", "Time must be in the future."))
    n_open = await RT.db.val("SELECT COUNT(*) FROM rz_reminders WHERE user_id=? AND status='active'", (c.actor_id,), 0)
    if n_open >= 25:
        return await say(c, c.t("سقف ۲۵ یادآوری پر شده.", "Limit of 25 reminders reached."))
    _, msg = await create_reminder(c, ts, text, scope)
    rows = None
    if scope == "me" and not c.is_private:
        rows = [[(c.t("▶️ استارت ربات", "▶️ Start the bot"), bot_link("rem"))]]
    await say(c, msg, rows)


@command("reminders", scope="any", admin=False, section="schedule")
async def k_reminders(c: Cmd):
    rows = await RT.db.all("SELECT * FROM rz_reminders WHERE status='active' AND (user_id=? OR (scope='group' AND chat_id=?)) ORDER BY due LIMIT 15",
                           (c.actor_id or 0, c.chat_id))
    if not rows:
        return await say(c, c.t("⏰ یادآوری نداری.", "⏰ No active reminders."))
    lines = [c.t("⏰ <b>یادآوری‌ها</b>", "⏰ <b>Reminders</b>")]
    btns = []
    for r in rows:
        lines.append(f"#{r['id']} {fmt_dt(r['due'], c.tz, c.lang, with_tz=False)} · {'👥' if r['scope'] == 'group' else '👤'} {esc(short(r['text'], 50))}")
        btns.append([(f"🗑 #{r['id']}", f"rm:x:{r['id']}")])
    await say(c, "\n".join(lines), btns[:10])


# polls & events
async def create_poll(c: Cmd, question: str, options: list[str], anonymous: bool = True) -> str:
    from telegram import InputPollOption
    # poll text is plain text, not HTML
    out = await tg.call(RT.bot.send_poll, idempotent=False, chat_id=c.chat_id, question=question[:300],
                        options=[InputPollOption(o[:100], text_parse_mode=None) for o in options], question_parse_mode=None,
                        is_anonymous=anonymous, message_thread_id=c.thread_id if c.is_forum else None)
    if not out.ok:
        return c.t("❌ نظرسنجی ساخته نشد: ", "❌ Poll not created: ") + esc(out.desc)
    pm = out.value
    await RT.db.run("INSERT OR REPLACE INTO rz_polls VALUES(?,?,?,?,?,?,?,0,?,?)",
                    (pm.poll.id, c.chat_id, pm.message_id, question[:300], jd(options), int(anonymous), c.actor_id, jd([0] * len(options)), now()))
    return c.t("📊 ساخته شد. بستن: ریپلای + <code>/pollclose</code>", "📊 Created. Close: reply + <code>/pollclose</code>")


@command("poll", admin=False, section="schedule")
async def k_poll(c: Cmd):
    ok, _ = await authz.check(c.chat_id, c.actor_id, None)
    if not ok and not await S.get(c.chat_id, "poll_members"):
        return await say(c, c.t("⛔️ فقط ادمین‌ها.", "⛔️ Admins only."))
    raw = c.rest
    anonymous = True
    if raw.lower().startswith(("public ", "علنی ")):
        anonymous, raw = False, raw.split(None, 1)[1] if len(raw.split(None, 1)) > 1 else ""
    parts = [p.strip() for p in re.split(r"\s*\|\s*|\n", raw) if p.strip()]
    if len(parts) < 3 or len(parts) > 11:
        return await say(c, c.t("<code>/poll سؤال | گزینه۱ | گزینه۲</code> (۲ تا ۱۰ گزینه)",
                                "<code>/poll question | a | b</code> (2-10 options)"))
    await say(c, await create_poll(c, parts[0], parts[1:], anonymous))


@command("pollclose", "closepoll", right=None, section="schedule")
async def k_pollclose(c: Cmd):
    if not c.reply:
        return await say(c, c.t("روی نظرسنجی ریپلای کنید.", "Reply to the poll."))
    row = await RT.db.one("SELECT * FROM rz_polls WHERE chat_id=? AND msg_id=?", (c.chat_id, c.reply.message_id))
    out = await tg.call(RT.bot.stop_poll, chat_id=c.chat_id, message_id=c.reply.message_id)
    if not out.ok:
        return await say(c, c.t("❌ ناموفق: ", "❌ Failed: ") + esc(out.desc))
    p = out.value
    total = p.total_voter_count
    lines = [c.t(f"📊 <b>نتیجه:</b> {esc(p.question)} · {total} رأی", f"📊 <b>Result:</b> {esc(p.question)} · {total} votes")]
    for o in p.options:
        pct = round(100 * o.voter_count / total) if total else 0
        lines.append(f"• {esc(o.text)}: {o.voter_count} ({pct}%)")
    if row:
        await RT.db.run("UPDATE rz_polls SET closed=1, counts=? WHERE poll_id=?", (jd([o.voter_count for o in p.options]), row["poll_id"]))
    await say(c, "\n".join(lines))


@command("event", right="info", section="schedule")
async def k_event(c: Cmd):
    a = list(c.args)
    ts, _rule, used = parse_when(a, c.tz)
    rest = " ".join(a[used:])
    title, _, place = rest.partition("|")
    if not ts or not title.strip():
        return await say(c, c.t("<code>/event فردا 20:00 عنوان | مکان</code>\n" + WHEN_HELP[0],
                                "<code>/event tomorrow 20:00 title | place</code>\n" + WHEN_HELP[1]))
    eid, _ = await RT.db.run("INSERT INTO rz_events(chat_id,thread_id,title,place,starts_at,remind_before,status,created_by,created_at) "
                             "VALUES(?,?,?,?,?,?,?,?,?)", (c.chat_id, c.thread_id if c.is_forum else None, title.strip()[:200],
                                                           place.strip()[:200] or None, ts, 1800, "active", c.actor_id, now()))
    ev = await RT.db.one("SELECT * FROM rz_events WHERE id=?", (eid,))
    txt, rows = await planner.event_card(ev, c.lang, c.tz)
    out = await c.say(txt, rows, reply=False)
    if out.ok:
        await RT.db.run("UPDATE rz_events SET card_msg_id=? WHERE id=?", (out.value.message_id, eid))
    await audit.record(c.chat_id, c.actor_id, "admin", "setting", params={"event": eid})


@command("events", admin=False, section="schedule")
async def k_events(c: Cmd):
    rows = await RT.db.all("SELECT * FROM rz_events WHERE chat_id=? AND status='active' ORDER BY starts_at LIMIT 10", (c.chat_id,))
    if not rows:
        return await say(c, c.t("📅 رویداد فعالی نیست.", "📅 No upcoming events."))
    lines = [c.t("📅 <b>رویدادها</b>", "📅 <b>Events</b>")]
    for e in rows:
        g = await RT.db.val("SELECT COUNT(*) FROM rz_rsvps WHERE event_id=? AND status='going'", (e["id"],), 0)
        lines.append(f"#{e['id']} {esc(e['title'])} · {fmt_dt(e['starts_at'], c.tz, c.lang, with_tz=False)} · ✅ {g}")
    await say(c, "\n".join(lines))


@command("eventcancel", right="info", section="schedule")
async def k_eventcancel(c: Cmd):
    if not c.args or not norm_digits(c.args[0]).isdigit():
        return await say(c, c.t("استفاده: <code>/eventcancel شماره</code>", "Usage: <code>/eventcancel id</code>"))
    eid = int(norm_digits(c.args[0]))
    _, n = await RT.db.run("UPDATE rz_events SET status='cancelled' WHERE id=? AND chat_id=? AND status='active'", (eid, c.chat_id))
    if not n:
        return await say(c, c.t("پیدا نشد.", "Not found."))
    ev = await RT.db.one("SELECT * FROM rz_events WHERE id=?", (eid,))
    await planner.refresh_card(ev)
    await say(c, c.t(f"❌ رویداد «{esc(ev['title'])}» لغو شد.", f"❌ Event “{esc(ev['title'])}” cancelled."))


@command("eventsummary", admin=False, section="schedule")
async def k_eventsummary(c: Cmd):
    if not c.args or not norm_digits(c.args[0]).isdigit():
        return await say(c, c.t("استفاده: <code>/eventsummary شماره</code>", "Usage: <code>/eventsummary id</code>"))
    ev = await RT.db.one("SELECT * FROM rz_events WHERE id=? AND chat_id=?", (int(norm_digits(c.args[0])), c.chat_id))
    if not ev:
        return await say(c, c.t("پیدا نشد.", "Not found."))
    rs = await RT.db.all("SELECT * FROM rz_rsvps WHERE event_id=? ORDER BY ts", (ev["id"],))
    by = {"going": [], "maybe": [], "no": []}
    for r in rs:
        by.setdefault(r["status"], []).append(esc(r["name"] or str(r["user_id"])) + (" 📍" if r["checked_in"] else ""))
    await say(c, c.t(f"📅 <b>{esc(ev['title'])}</b> — {fmt_dt(ev['starts_at'], c.tz, 'fa')}\n✅ می‌آیند ({len(by['going'])}): {'، '.join(by['going']) or '—'}\n"
                     f"🤔 شاید ({len(by['maybe'])}): {'، '.join(by['maybe']) or '—'}\n❌ نه ({len(by['no'])})",
                     f"📅 <b>{esc(ev['title'])}</b> — {fmt_dt(ev['starts_at'], c.tz, 'en')}\n✅ Going ({len(by['going'])}): {', '.join(by['going']) or '—'}\n"
                     f"🤔 Maybe ({len(by['maybe'])}): {', '.join(by['maybe']) or '—'}\n❌ No ({len(by['no'])})"))


# backup / restore / clone
@command("backup", "export", right="info", section="tools")
async def k_backup(c: Cmd):
    if not c.actor_id:
        return await say(c, c.t("با حساب ناشناس نمیشه.", "Not available for anonymous admins."))
    data = await tools.export_config(c.chat_id)
    raw = json.dumps(data, ensure_ascii=False, indent=1).encode()
    out = await dm(c.actor_id, c.t(f"📦 پشتیبان «{esc(c.chat_title)}»\nبازیابی: ریپلای روی فایل + /restore",
                                   f"📦 Backup of “{esc(c.chat_title)}”\nRestore: reply to the file + /restore"),
                   doc=(f"rayzen-backup-{abs(c.chat_id)}.json", raw))
    if out.ok:
        await audit.record(c.chat_id, c.actor_id, "admin", "setting", params={"backup": len(raw)})
        return await say(c, c.t("✅ به پیوی فرستاده شد.", "✅ Sent to private chat."))
    await say(c, c.t("❌ اول ربات رو استارت کن.",
                     "❌ Start the bot first."), [[(c.t("▶️ استارت ربات", "▶️ Start the bot"), bot_link())]])


SEC_FA = {"settings": "تنظیمات", "rules": "قوانین", "answers": "پاسخ‌ها", "blockwords": "کلمات ممنوع", "night": "برنامهٔ شبانه"}


async def _import_preview(c: Cmd, data, kind: str, sections: set[str], source: int | None = None) -> None:
    new, errs = tools.validate_config(data)
    if new is None:
        return await say(c, c.t("❌ فایل معتبر نیست: ", "❌ Invalid file: ") + esc("; ".join(errs)))
    diff_safe = await tools.diff_config(c.chat_id, new, c.lang, sections, protections=False)
    has_filters = any(S.SPECS[k].enforce and v for k, v in new["settings"].items()) and "settings" in sections
    if not diff_safe:
        return await say(c, c.t("✅ تغییری نیست.", "✅ No changes."))
    lines = [c.t(f"♻️ <b>پیش‌نمایش {'بازیابی' if kind == 'restore' else 'کپی تنظیمات'}</b> — بخش‌ها: ",
                 f"♻️ <b>{'Restore' if kind == 'restore' else 'Clone'} preview</b> — sections: ") +
             "، ".join(SEC_FA[s] if c.lang == "fa" else s for s in sorted(sections))]
    lines += diff_safe[:40]
    if errs:
        lines.append(c.t("⚠️ رد شده: ", "⚠️ Skipped: ") + esc("; ".join(errs[:5])))
    payload = {"new": new, "sections": sorted(sections), "source": source}
    tok = await confirm.create(c.chat_id, c.actor_id, "import_cfg", {**payload, "protections": False}, right="info", ttl=600)
    rows = confirm.buttons(tok, c.lang, c.t("✅ اعمال (بدون فیلترها)", "✅ Apply (no filters)"))
    if has_filters:
        tok2 = await confirm.create(c.chat_id, c.actor_id, "import_cfg", {**payload, "protections": True}, right="info", ttl=600)
        rows.insert(0, [(c.t("⚠️ اعمال با فیلترها", "⚠️ Apply with filters"), f"cf:y:{tok2}")])
    await say(c, "\n".join(lines), rows)


@confirm.handler("import_cfg")
async def _import_ok(p, presser, chat_id, message):
    if p.get("source"):
        ok, _ = await authz.check(p["source"], presser, "info", fresh=True)
        if not ok:
            return "⛔️ دیگه در گروه مبدأ مدیر نیستی. / Not an admin of the source anymore."
    done = await tools.apply_config(chat_id, p["new"], presser, set(p["sections"]), p["protections"])
    from pilot.ctx import lang_tz
    lang, _ = await lang_tz(chat_id)
    return ("✅ اعمال شد: " if lang == "fa" else "✅ Applied: ") + ", ".join(done)


@command("restore", "import", right="info", section="tools")
async def k_restore(c: Cmd):
    if not c.reply or c.reply.media_type != "document" or not c.reply.file_id:
        return await say(c, c.t("روی فایل پشتیبان ریپلای کن.", "Reply to the backup file."))
    f = await tg.call(RT.bot.get_file, file_id=c.reply.file_id)
    if not f.ok:
        return await say(c, c.t("❌ ناموفق: ", "❌ Failed: ") + esc(f.desc))
    if (f.value.file_size or 0) > 512_000:
        return await say(c, c.t("❌ فایل خیلی بزرگه.", "❌ File too large."))
    try:
        raw = await f.value.download_as_bytearray()
        data = json.loads(bytes(raw).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return await say(c, c.t("❌ فایل نامعتبر.", "❌ Invalid file."))
    secs = {s for s in c.args if s in tools.SECTIONS} or {"settings", "rules", "answers", "blockwords"}
    await _import_preview(c, data, "restore", secs)


@command("clone", "copyconfig", right="info", section="tools")
async def k_clone(c: Cmd):
    a = [norm_digits(x) for x in c.args]
    if not a or not re.fullmatch(r"-\d{5,16}", a[0]):
        return await say(c, c.t("<code>/clone -100آیدی_مبدأ [settings rules answers blockwords night]</code>",
                                "<code>/clone -100source_id [settings rules answers blockwords night]</code>"))
    src = int(a[0])
    if src == c.chat_id:
        return await say(c, c.t("مبدأ و مقصد یکیه.", "Same group."))
    ok, _ = await authz.check(src, c.actor_id, "info", fresh=True)
    if not ok:
        return await say(c, c.t("⛔️ در گروه مبدأ دسترسی نداری.",
                                "⛔️ No access in the source group."))
    secs = {s for s in a[1:] if s in tools.SECTIONS} or {"settings", "rules", "answers", "blockwords"}
    await _import_preview(c, await tools.export_config(src), "clone", secs, source=src)


# join-request desk
@command("joindesk", right="invite", section="desk")
async def k_joindesk(c: Cmd):
    async def extra(c2):
        miss = "" if await authz.bot_has(c2.chat_id, "invite", fresh=True) else c2.t("\n⚠️ دسترسی «دعوت کاربر» لازمه.", "\n⚠️ Needs 'Invite users' right.")
        return miss + c2.t("\n/requests", "\n/requests")
    await toggle_cmd(c, "jr_desk", extra)


@command("joinquestion", right="invite", section="desk")
async def k_joinquestion(c: Cmd):
    if not c.rest or c.rest.strip().lower() in ("off", "خاموش"):
        await S.set_value(c.chat_id, "jr_question", None, c.actor_id or 0)
        return await say(c, c.t("✅ سؤال حذف شد.", "✅ Question removed."))
    await S.set_value(c.chat_id, "jr_question", c.rest[:500], c.actor_id or 0)
    await say(c, c.t("✅ سؤال ذخیره شد.",
                     "✅ Question saved."))


@command("joinauto", right="invite", section="desk")
async def k_joinauto(c: Cmd): await toggle_cmd(c, "jr_autoapprove")


@command("requests", right="invite", section="desk")
async def k_requests(c: Cmd):
    rows = await RT.db.all("SELECT * FROM rz_join_requests WHERE chat_id=? AND status='pending' ORDER BY ts LIMIT 10", (c.chat_id,))
    if not rows:
        return await say(c, c.t("📥 درخواست منتظری نیست." + ("" if await S.get(c.chat_id, "jr_desk") else " (/joindesk on)"),
                                "📥 No pending requests." + ("" if await S.get(c.chat_id, "jr_desk") else " (/joindesk on)")))
    for r in rows:
        await say(c, f"📥 {desk.ulabel(r['user_id'], r['name'], r['username'])} · {fmt_dt(r['ts'], c.tz, c.lang, with_tz=False)}"
                     + (f"\n📝 {esc(short(r['bio'], 120))}" if r["bio"] else "") + f"\n🔎 {esc(r['screen'] or '—')}",
                  desk.desk_rows(c.chat_id, r["user_id"], c.lang))


# tickets / appeals
@command("contact", "ticket", "admin_contact", admin=False, section="desk")
async def k_contact(c: Cmd):
    if not c.actor_id:
        return
    text = c.rest.strip()
    if not text:
        return await say(c, c.t("<code>/contact متن</code> یا:", "<code>/contact text</code> or:"),
                         [[(c.t("✉️ ارسال خصوصی", "✉️ Send privately"), bot_link(f"tk_{abs(c.chat_id)}"))]])
    from telegram import User
    u = User(id=c.actor_id, first_name=c.actor_name or str(c.actor_id), is_bot=False)
    ok, msg, _ = await desk.open_ticket(c.chat_id, u, text, c.lang)
    if ok and c.message_id and await authz.bot_has(c.chat_id, "delete"):
        await tg.delete(c.chat_id, c.message_id)        # keep the text out of the group
    await c.say(msg, reply=not ok)


@command("tickets", right=None, section="desk")
async def k_tickets(c: Cmd):
    rows = await RT.db.all("SELECT * FROM rz_tickets WHERE chat_id=? AND status IN ('open','claimed') ORDER BY id LIMIT 10", (c.chat_id,))
    if not rows:
        return await say(c, c.t("🎫 تیکت بازی نیست.", "🎫 No open tickets."))
    for t in rows:
        await say(c, f"🎫 #{t['id']} · {t['status']} · <code>{t['user_id']}</code>" + (f" · 🙋 <code>{t['claimed_by']}</code>" if t["claimed_by"] else "") +
                  f"\n💬 {esc(short(t['text'], 300))}\n" + c.t(f"پاسخ: <code>/treply {t['id']} متن</code>", f"Reply: <code>/treply {t['id']} text</code>"),
                  [[(c.t("🙋 برعهده", "🙋 Claim"), f"tk:c:{t['id']}"), (c.t("✅ بستن", "✅ Close"), f"tk:x:{t['id']}")]])


@command("treply", scope="any", admin=False, section="desk")
async def k_treply(c: Cmd):
    if len(c.args) < 2 or not norm_digits(c.args[0]).lstrip("#").isdigit():
        return await say(c, c.t("استفاده: <code>/treply شماره متن</code>", "Usage: <code>/treply id text</code>"))
    tid = int(norm_digits(c.args[0]).lstrip("#"))
    t = await RT.db.one("SELECT chat_id FROM rz_tickets WHERE id=?", (tid,))
    if not t:
        return await say(c, c.t("تیکت پیدا نشد.", "Ticket not found."))
    ok, _ = await authz.check(t["chat_id"], c.actor_id, None, fresh=True)
    if not ok:
        return await say(c, c.t("⛔️ فقط ادمین‌ها.", "⛔️ Admins only."))
    ok, msg = await desk.ticket_reply(t["chat_id"], tid, c.rest.split(None, 1)[1], c.actor_id, c.lang)
    await say(c, msg)


@command("mycases", "appeal", scope="private", admin=False, section="history")
async def k_mycases(c: Cmd):
    rows = await RT.db.all("SELECT c.chat_id, c.case_no, c.action, c.reason, c.created_at, ch.title FROM rz_cases c LEFT JOIN rz_chats ch ON ch.chat_id=c.chat_id "
                           "WHERE c.target_id=? AND c.status='active' ORDER BY c.created_at DESC LIMIT 10", (c.actor_id,))
    if not rows:
        return await say(c, c.t("📁 پرونده‌ای نداری.", "📁 You have no active cases."))
    lines = [c.t("📁 <b>پرونده‌ها</b>", "📁 <b>Your cases</b>")]
    btns = []
    for r in rows:
        lines.append(f"• {esc(r['title'] or r['chat_id'])} — #{r['case_no']} {esc(audit.action_name(r['action'], c.lang))} · {esc(short(r['reason'] or '—', 60))}")
        btns.append([(c.t(f"⚖️ تجدیدنظر #{r['case_no']}", f"⚖️ Appeal #{r['case_no']}"), f"ma:{abs(r['chat_id'])}:{r['case_no']}")])
    await say(c, "\n".join(lines), btns)


# topics / watchdog
@command("topic", "topics", right="topics", section="desk")
async def k_topic(c: Cmd):
    await say(c, await desk.topic_cmd(c))


@command("watchdog", right="info", section="tools")
async def k_watchdog(c: Cmd):
    v = onoff(c)
    if v is not None:
        return await say(c, await set_setting(c, "watch_on", v))
    res = await tools.watchdog_check(c.chat_id, notify=False)
    if not res.get("ok"):
        return await say(c, c.t("❌ ناموفق: ", "❌ Failed: ") + esc(res.get("error", "")))
    lays = await locks.layers(c.chat_id)
    w = await RT.db.one("SELECT good_perms FROM rz_watch WHERE chat_id=?", (c.chat_id,))
    good = jl((w or {}).get("good_perms"))
    lines = [c.t("👁 <b>نگهبان دسترسی‌ها</b>", "👁 <b>Permission watchdog</b>")]
    cur = res["current"]
    lines.append(c.t("دسترسی اعضا:", "Member permissions:"))
    lines.append(" ".join(f"{'✅' if cur.get(k) else '⛔️'}{esc(tools._perm_name(k, c.lang))}" for k in locks.KEYS if k in cur))
    diff = [k for k in locks.KEYS if good and good.get(k) != cur.get(k)]
    rows = None
    if lays:
        lines.append(c.t("🔒 قفل فعاله (/lockstatus)", "🔒 Lock active (/lockstatus)"))
    elif diff:
        lines.append(c.t("تغییر کرده: ", "Changed: ") + ", ".join(esc(tools._perm_name(k, c.lang)) for k in diff))
        rows = [[(c.t("🔧 بازگردانی", "🔧 Restore"), "wd:fix"), (c.t("👍 درسته", "👍 Keep"), "wd:ok")]]
    else:
        lines.append(c.t("✅ بدون تغییر.", "✅ No changes."))
    await say(c, "\n".join(lines), rows)


@confirm.handler("wd_fix")
async def _wd_fix(p, presser, chat_id, message):
    if await locks.layers(chat_id):
        return "🔒 قفل فعاله. / Lock active."
    out = await tg.call(RT.bot.set_chat_permissions, chat_id=chat_id, permissions=ChatPermissions(**p["perms"]), use_independent_chat_permissions=True)
    if not out.ok:
        return "❌ " + esc(out.desc)
    cur = await locks.current(chat_id)
    await RT.db.run("UPDATE rz_watch SET last_perms=?, drift=NULL WHERE chat_id=?", (jd(cur.value if cur.ok else p["perms"]), chat_id))
    await audit.record(chat_id, presser, "admin", "restore", params={"watchdog": True}, log_line=f"👁 <b>#PERMISSIONS_RESTORED</b> by <code>{presser}</code>")
    return "✅ برگشت. / Restored."


# privacy
@command("privacy", scope="any", admin=False, section="privacy")
async def k_privacy(c: Cmd):
    if c.is_private:
        return await say(c, c.t("🔐 <b>حریم خصوصی</b>\nذخیره: آیدی، نام و یوزرنیم (با سابقهٔ تغییر)، اخطارها و پرونده‌ها، "
                                "شمار روزانهٔ پیام‌ها و ورود/خروج‌هایی که ربات دیده.\n"
                                "متن پیام‌های گروه ذخیره نمیشه؛ فقط کلماتش ۴۸ ساعت برای /purgeword. "
                                "گفتگو با دستیار حداکثر یک روز نگه داشته میشه.\n/mydata · /forgetme",
                                "🔐 <b>Privacy</b>\nStored: id, name and username (with change history), warnings and cases, "
                                "daily message counts and the joins/leaves the bot saw.\n"
                                "Group message text is not stored; only its words, for 48h, for /purgeword. "
                                "Assistant conversations are kept for at most a day.\n/mydata · /forgetme"))
    ok, _ = await authz.check(c.chat_id, c.actor_id, None)
    if not ok:
        return await say(c, c.t("🔐 متن پیام‌ها ذخیره نمیشه؛ فقط کلماتش ۴۸ ساعت برای /purgeword. /mydata در پیوی",
                                "🔐 Message text is not stored; only its words, for 48h, for /purgeword. /mydata in private"))
    await say(c, await tools.data_policy_text(c.chat_id, c.lang),
              [[(c.t("📤 خروجی سوابق", "📤 Export records"), "pv:e"), (c.t("🗑 حذف داده‌های گروه", "🗑 Reset group data"), "pv:r")]])


@command("retention", right="info", section="privacy")
async def k_retention(c: Cmd):
    if not c.args:
        return await say(c, c.t(f"⏳ نگهداری: {await S.get(c.chat_id, 'retention_days')} روز · <code>/retention 60</code>",
                                f"⏳ Retention: {await S.get(c.chat_id, 'retention_days')} days · <code>/retention 60</code>"))
    await say(c, await set_setting(c, "retention_days", c.args[0]))


async def export_data_to(c: Cmd) -> str:
    raw = await tools.export_audit(c.chat_id)
    out = await dm(c.actor_id, c.t("📤 سوابق مدیریتی", "📤 Moderation records"),
                   doc=(f"rayzen-audit-{abs(c.chat_id)}.json", raw))
    return c.t("✅ به پیوی فرستاده شد.", "✅ Sent to private chat.") if out.ok else \
        c.t("❌ اول ربات رو استارت کن.", "❌ Start the bot first.")


@command("exportdata", right="info", section="privacy")
async def k_exportdata(c: Cmd):
    await say(c, await export_data_to(c))


async def reset_preview(c: Cmd) -> None:
    tok = await confirm.create(c.chat_id, c.actor_id, "reset_data", {}, right="info", ttl=300)
    await say(c, c.t("⚠️ <b>حذف داده‌های گروه</b>\nتنظیمات، پاسخ‌ها، قوانین، تاریخچه، پرونده‌ها، اخطارها، اطلاعیه‌ها، یادآورها، تیکت‌ها، لاگ و نمایهٔ ۴۸ ساعته پاک میشن.",
                     "⚠️ <b>Delete group data</b>\nSettings, answers, rules, history, cases, warnings, announcements, reminders, tickets, log route and the 48h index will be removed."),
              confirm.buttons(tok, c.lang, c.t("🗑 بله، حذف کن", "🗑 Yes, delete")))


@command("resetdata", right="info", section="privacy")
async def k_resetdata(c: Cmd):
    await reset_preview(c)


@confirm.handler("reset_data")
async def _reset_ok(p, presser, chat_id, message):
    done = await tools.reset_group_data(chat_id)
    await audit.record(chat_id, presser, "admin", "setting", params={"reset_data": done})
    return "✅ حذف شد: " + (", ".join(done) or "—")


@command("mydata", scope="private", admin=False, section="privacy")
async def k_mydata(c: Cmd):
    data = await tools.user_data(c.actor_id)
    raw = json.dumps(data, ensure_ascii=False, indent=1, default=str).encode()
    out = await dm(c.actor_id, c.t("📤 داده‌های شما", "📤 Your data"), doc=(f"rayzen-mydata-{c.actor_id}.json", raw))
    if not out.ok:
        await say(c, "❌ " + esc(out.desc))


@command("forgetme", scope="private", admin=False, section="privacy")
async def k_forgetme(c: Cmd):
    tok = await confirm.create(c.actor_id, c.actor_id, "forget", {}, ttl=300)
    await say(c, c.t("🗑 پاک میشه: پروفایل و سابقهٔ نام، شمار فعالیت، ورود و خروج‌ها، حالت غیبت، حافظهٔ دستیار، "
                     "یادآورها، RSVPها، تیکت‌های بسته، نمایهٔ ۴۸ ساعته.\nمی‌مونه: اخطارها، پرونده‌ها و فهرست معتمدها.",
                     "🗑 Removed: profile and name history, activity counters, joins/leaves, AFK, assistant memory, "
                     "reminders, RSVPs, closed tickets, 48h index.\nKept: warnings, cases and the trusted list."),
              confirm.buttons(tok, c.lang, c.t("🗑 حذف کن", "🗑 Delete")))


@confirm.handler("forget")
async def _forget_ok(p, presser, chat_id, message):
    done = await tools.forget_user(presser)
    return "✅ " + (", ".join(done) or "—")


# legacy review / owner
@command("legacy", right="info", section="tools")
async def k_legacy(c: Cmd):
    txt, rows = await ui.legacy_page(c.chat_id, c.lang)
    await say(c, txt, rows)


# AI tool bridge (part 2)
async def run_tool2(c: Cmd, tool: str, g: dict) -> str:
    if tool == "announce":
        ok, _ = await authz.check(c.chat_id, c.actor_id, "info")
        if not ok:
            return c.t("⛔️ فقط ادمین‌ها با دسترسی «تغییر اطلاعات».", "⛔️ Admins with 'Change info' only.")
        aid = await create_announcement(c, g["ts"], g["rule"], esc(g["text"]), None, False, "active")
        await audit.record(c.chat_id, c.actor_id, "ai", "setting", params={"announcement": aid, "rule": g["rule"]},
                           log_line=f"📣 <b>#ANNOUNCEMENT</b> #{aid} {g['rule']} (via AI)")
        return c.t(f"✅ اطلاعیه #{aid} زمان‌بندی شد ({rule_label(g['rule'], 'fa')})؛ اولین ارسال {fmt_dt(g['ts'], c.tz, 'fa')}. مدیریت: /announcements",
                   f"✅ Announcement #{aid} scheduled ({rule_label(g['rule'], 'en')}); first run {fmt_dt(g['ts'], c.tz, 'en')}. Manage: /announcements")
    if tool == "remind":
        if not c.actor_id:
            return c.t("مدیر ناشناس نمی‌تواند یادآوری بسازد.", "Anonymous admins can't create reminders.")
        _, msg = await create_reminder(c, g["ts"], g["text"], g.get("scope", "me"))
        return "✅ " + msg
    if tool == "poll":
        return await create_poll(c, g["question"], g["options"])
    return c.t("❌ این ابزار پشتیبانی نمی‌شود.", "❌ Unsupported tool.")
