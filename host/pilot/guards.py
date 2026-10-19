"""Opt-in guards on top of the basic filters: newcomer probation, anti-tabchi, mass mentions, repeated
messages, plus the trusted list, AFK replies and automatic cleanup of the bot's own short replies.

Every guard is off by default. Join times come only from joins the bot actually observed, so people who
joined before the bot was added are never treated as newcomers."""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from pilot import audit, authz, hooks, settings as S, tg
from pilot.commands import command, onoff, say, set_setting, toggle_cmd
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.targets import Target, resolve
from pilot.util import esc, fmt_dt, fmt_duration, norm_digits, num, short

_dupes: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=20))
_trust_cache: dict[tuple[int, int], tuple[float, bool]] = {}
_afk_note: dict[tuple[int, int], float] = {}
MEDIA = ("photo", "video", "document", "animation", "sticker", "voice", "video_note", "audio")


# trusted members
async def is_trusted(chat_id: int, user_id: int) -> bool:
    k = (chat_id, user_id)
    hit = _trust_cache.get(k)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    v = bool(await RT.db.val("SELECT 1 FROM rz_trusted WHERE chat_id=? AND user_id=?", (chat_id, user_id)))
    _trust_cache[k] = (time.time(), v)
    if len(_trust_cache) > 20000:
        _trust_cache.clear()
    return v


async def joined_at(chat_id: int, user_id: int) -> float | None:
    """Last join the bot saw for this user, or None if it never saw one."""
    return await RT.db.val("SELECT MAX(ts) FROM rz_member_events WHERE chat_id=? AND user_id=? AND kind='join'", (chat_id, user_id))


def _links_or_invites(m) -> bool:
    from pilot.content import has_link
    if has_link(m):
        return True
    for e in (m.entities or ()) + (m.caption_entities or ()):
        if e.type == "mention":
            return True                      # @channel / @bot promotion
    return False


def _mentions(m) -> int:
    return sum(1 for e in (m.entities or ()) + (m.caption_entities or ()) if e.type in ("mention", "text_mention"))


async def _sanction(m, eff: dict, action: str, rule: str, reason_fa: str, reason_en: str) -> None:
    from pilot import moderation
    lang = eff["lang"]
    c = Cmd(chat_id=m.chat_id, chat_type=m.chat.type, actor_id=None, actor_name="auto", source="auto", lang=lang, tz=eff["tz"],
            thread_id=m.message_thread_id if m.is_topic_message else None, is_forum=bool(m.chat.is_forum))
    t = Target(user_id=m.from_user.id, name=m.from_user.full_name, username=m.from_user.username, via="auto", verified=True)
    reason = reason_fa if lang == "fa" else reason_en
    r = await (moderation.ban(c, t, reason=reason) if action == "ban" else moderation.mute(c, t, duration=86400, reason=reason))
    if r.ok:
        await c.say(r.text, reply=False)


async def check(m, eff: dict) -> str | None:
    """Run the enabled guards on a message from an ordinary member. Returns the rule that removed it."""
    from pilot.content import _remove
    u = m.from_user
    t = time.time()
    if eff.get("tabchi_on") or eff.get("probation_on"):
        j = await joined_at(m.chat_id, u.id)
        if j:
            age = t - j
            if eff.get("tabchi_on") and age <= eff["tabchi_min"] * 60 and (_links_or_invites(m) or m.forward_origin is not None):
                rule = await _remove(m, "tabchi_on")
                if rule and eff["tabchi_action"] in ("mute", "ban"):
                    await _sanction(m, eff, eff["tabchi_action"], rule, "تبلیغ در دقایق اول عضویت", "advertising right after joining")
                return rule or None
            if eff.get("probation_on") and age <= eff["probation_h"] * 3600:
                if _links_or_invites(m) or m.forward_origin is not None or any(getattr(m, a, None) for a in MEDIA):
                    return await _remove(m, "probation_on")
    if eff.get("mentions_on") and _mentions(m) > eff["mentions_max"]:
        return await _remove(m, "mentions_on")
    if eff.get("dupes_on"):
        body = (m.text or m.caption or "").strip()
        if len(body) >= 3:
            h = hashlib.blake2s(" ".join(body.lower().split()).encode(), digest_size=8).hexdigest()
            dq = _dupes[(m.chat_id, u.id)]
            dq.append((h, t))
            same = sum(1 for x, ts in dq if x == h and t - ts <= 600)
            if same > eff["dupes_n"]:
                return await _remove(m, "dupes_on")
    return None


hooks.GUARDS.append(check)
hooks.GUARD_KEYS.extend(["probation_on", "tabchi_on", "mentions_on", "dupes_on"])


# commands
@command("probation", right="info", section="filters")
async def k_probation(c: Cmd):
    v = onoff(c)
    if v is None:
        on, h = await S.get(c.chat_id, "probation_on"), await S.get(c.chat_id, "probation_h")
        return await say(c, c.t(f"دورهٔ آزمایشی: {'روشن' if on else 'خاموش'} · {num(h, 'fa')} ساعت\n"
                                "عضو تازه در این مدت لینک، فوروارد و رسانه نمی‌تواند بفرستد (فقط ورودهایی که ربات دیده).\n"
                                "<code>/probation on 24</code> · <code>/probation off</code>",
                                f"Probation: {'on' if on else 'off'} · {h} h\nNew members can't send links, forwards or media "
                                "during this time (only joins the bot saw).\n<code>/probation on 24</code> · <code>/probation off</code>"))
    txt = await set_setting(c, "probation_on", v)
    if v and len(c.args) > 1 and norm_digits(c.args[1]).isdigit():
        txt += "\n" + await set_setting(c, "probation_h", norm_digits(c.args[1]))
    await say(c, txt)


@command("antitabchi", "antispambot", right="info", section="filters")
async def k_antitabchi(c: Cmd):
    v = onoff(c)
    if v is None:
        on = await S.get(c.chat_id, "tabchi_on")
        mins, act = await S.get(c.chat_id, "tabchi_min"), await S.get(c.chat_id, "tabchi_action")
        return await say(c, c.t(f"ضد تبچی: {'روشن' if on else 'خاموش'} · {num(mins, 'fa')} دقیقهٔ اول · {S.fmt_value('tabchi_action', act, 'fa')}\n"
                                "لینک، فوروارد یا @آیدی از عضوی که تازه وارد شده حذف می‌شود.\n"
                                "<code>/antitabchi on 30 mute</code> · <code>/antitabchi off</code>",
                                f"Anti-tabchi: {'on' if on else 'off'} · first {mins} min · {act}\n"
                                "Links, forwards or @handles from a member who just joined are removed.\n"
                                "<code>/antitabchi on 30 mute</code> · <code>/antitabchi off</code>"))
    txt = await set_setting(c, "tabchi_on", v)
    for a in c.args[1:3]:
        a = norm_digits(a).lower()
        if a.isdigit():
            txt += "\n" + await set_setting(c, "tabchi_min", a)
        elif a in ("delete", "mute", "ban", "حذف", "سکوت", "بن"):
            txt += "\n" + await set_setting(c, "tabchi_action", {"حذف": "delete", "سکوت": "mute", "بن": "ban"}.get(a, a))
    await say(c, txt)


@command("antimention", right="info", section="filters")
async def k_antimention(c: Cmd):
    v = onoff(c)
    if v is None:
        return await toggle_cmd(c, "mentions_on")
    txt = await set_setting(c, "mentions_on", v)
    if v and len(c.args) > 1 and norm_digits(c.args[1]).isdigit():
        txt += "\n" + await set_setting(c, "mentions_max", norm_digits(c.args[1]))
    await say(c, txt)


@command("antidupe", "antirepeat", right="info", section="filters")
async def k_antidupe(c: Cmd):
    v = onoff(c)
    if v is None:
        return await toggle_cmd(c, "dupes_on")
    txt = await set_setting(c, "dupes_on", v)
    if v and len(c.args) > 1 and norm_digits(c.args[1]).isdigit():
        txt += "\n" + await set_setting(c, "dupes_n", norm_digits(c.args[1]))
    await say(c, txt)


@command("trust", "approve", right="restrict", section="mod")
async def k_trust(c: Cmd):
    t = await resolve(c)
    if not t.ok or not t.user_id:
        return await say(c, t.err or c.t("کاربر؟", "Which user?"))
    reason = " ".join(c.args[t.consumed:])[:200]
    await RT.db.run("INSERT OR REPLACE INTO rz_trusted(chat_id,user_id,by_user,ts,reason) VALUES(?,?,?,?,?)",
                    (c.chat_id, t.user_id, c.actor_id, time.time(), reason))
    _trust_cache.pop((c.chat_id, t.user_id), None)
    await audit.record(c.chat_id, c.actor_id, "admin", "setting", target_id=t.user_id, target_name=t.name,
                       params={"trusted": True}, log_line=f"🤝 #TRUST {t.label()}")
    await say(c, c.t(f"🤝 {t.label()} معتمد شد؛ فیلترها روی پیام‌هایش اجرا نمی‌شوند.",
                     f"🤝 {t.label()} is trusted; filters skip their messages."))


@command("untrust", "unapprove", right="restrict", section="mod")
async def k_untrust(c: Cmd):
    t = await resolve(c)
    if not t.ok or not t.user_id:
        return await say(c, t.err or c.t("کاربر؟", "Which user?"))
    _, n = await RT.db.run("DELETE FROM rz_trusted WHERE chat_id=? AND user_id=?", (c.chat_id, t.user_id))
    _trust_cache.pop((c.chat_id, t.user_id), None)
    await say(c, c.t(f"✅ {t.label()} از لیست معتمدها حذف شد.", f"✅ {t.label()} removed from trusted.") if n else
              c.t("در لیست نبود.", "Wasn't trusted."))


@command("trusted", "approved", right=None, section="mod")
async def k_trusted(c: Cmd):
    rows = await RT.db.all("SELECT t.user_id, u.first_name, u.username FROM rz_trusted t LEFT JOIN rz_users u ON u.user_id=t.user_id "
                           "WHERE t.chat_id=? ORDER BY t.ts DESC LIMIT 50", (c.chat_id,))
    if not rows:
        return await say(c, c.t("لیست معتمدها خالیه. <code>/trust</code> با ریپلای.", "No trusted members. <code>/trust</code> by reply."))
    lines = [f"• <a href=\"tg://user?id={r['user_id']}\">{esc(r['first_name'] or r['user_id'])}</a> <code>{r['user_id']}</code>" for r in rows]
    await say(c, c.t(f"🤝 <b>معتمدها</b> ({num(len(rows), 'fa')})\n", f"🤝 <b>Trusted</b> ({len(rows)})\n") + "\n".join(lines))


# bot reply cleanup
async def schedule_cleanup(chat_id: int, message) -> None:
    ttl = await S.get(chat_id, "bot_ttl_min")
    if not ttl or message is None:
        return
    await RT.db.run("INSERT OR IGNORE INTO rz_bot_msgs(chat_id,message_id,delete_at) VALUES(?,?,?)",
                    (chat_id, message.message_id, time.time() + ttl * 60))


@hooks.periodic("bot_msg_cleanup", 30)
async def cleanup_due() -> None:
    rows = await RT.db.all("SELECT chat_id, message_id FROM rz_bot_msgs WHERE delete_at<=? LIMIT 200", (time.time(),))
    for r in rows:
        await tg.delete(r["chat_id"], r["message_id"])
        await RT.db.run("DELETE FROM rz_bot_msgs WHERE chat_id=? AND message_id=?", (r["chat_id"], r["message_id"]))


@command("botclean", "autodelete", right="delete", section="tools")
async def k_botclean(c: Cmd):
    a = norm_digits(c.args[0]).lower() if c.args else ""
    if not a:
        cur = await S.get(c.chat_id, "bot_ttl_min")
        return await say(c, c.t(f"پاک شدن جواب‌های کوتاه ربات: {'بعد از ' + fmt_duration(cur * 60, 'fa') if cur else 'خاموش'}\n"
                                "<code>/botclean 5</code> (دقیقه) · <code>/botclean off</code>\nپیام‌های دکمه‌دار و اطلاعیه‌ها پاک نمی‌شوند.",
                                f"Auto-delete short bot replies: {'after ' + fmt_duration(cur * 60, 'en') if cur else 'off'}\n"
                                "<code>/botclean 5</code> (minutes) · <code>/botclean off</code>\nButtons and announcements stay."))
    val = 0 if a in ("off", "0", "خاموش") else a
    txt = await set_setting(c, "bot_ttl_min", val)
    if val and not await authz.bot_has(c.chat_id, "delete", fresh=True):
        txt += c.t("\n⚠️ ربات دسترسی «حذف پیام» ندارد.", "\n⚠️ The bot lacks the Delete messages right.")
    await say(c, txt)


# AFK
@command("afk", "away", admin=False, section="general")
async def k_afk(c: Cmd):
    if not c.actor_id:
        return
    if not await S.get(c.chat_id, "afk_on"):
        return await say(c, c.t("حالت غیبت در این گروه خاموشه.", "AFK is off in this group."))
    reason = c.rest.strip()[:200]
    await RT.db.run("INSERT OR REPLACE INTO rz_afk(chat_id,user_id,reason,since) VALUES(?,?,?,?)",
                    (c.chat_id, c.actor_id, reason, time.time()))
    await say(c, c.t(f"💤 {esc(c.actor_name)} غایب شد" + (f": {esc(reason)}" if reason else "."),
                     f"💤 {esc(c.actor_name)} is away" + (f": {esc(reason)}" if reason else ".")))


async def _afk_after(m, text: str) -> None:
    u = m.from_user
    if u is None or u.is_bot or m.sender_chat is not None:
        return
    chat_id = m.chat_id
    if text.startswith("/afk") or text.startswith("/away") or text.strip().startswith(("حالت غیبت",)):
        return
    if not await RT.db.val("SELECT 1 FROM rz_afk WHERE chat_id=? LIMIT 1", (chat_id,)):
        return
    lang = await S.get(chat_id, "lang")
    tz = await S.get(chat_id, "tz") or RT.cfg.tz
    row = await RT.db.one("SELECT since FROM rz_afk WHERE chat_id=? AND user_id=?", (chat_id, u.id))
    if row:
        await RT.db.run("DELETE FROM rz_afk WHERE chat_id=? AND user_id=?", (chat_id, u.id))
        gone = fmt_duration(int(time.time() - row["since"]), lang)
        await tg.send(chat_id, (f"👋 {esc(u.first_name)} برگشت ({gone})." if lang == "fa" else f"👋 {esc(u.first_name)} is back ({gone})."),
                      thread_id=m.message_thread_id if m.is_topic_message else None, silent=True)
    targets: set[int] = set()
    r = m.reply_to_message
    if r is not None and r.from_user and not r.from_user.is_bot and r.from_user.id != u.id:
        targets.add(r.from_user.id)
    for e in (m.entities or ()):
        if e.type == "text_mention" and e.user:
            targets.add(e.user.id)
        elif e.type == "mention":
            un = (text[e.offset + 1:e.offset + e.length] or "").lower()
            uid = await RT.db.val("SELECT user_id FROM rz_users WHERE username_lc=?", (un,))
            if uid:
                targets.add(uid)
    for uid in list(targets)[:3]:
        a = await RT.db.one("SELECT a.reason, a.since, u.first_name FROM rz_afk a LEFT JOIN rz_users u ON u.user_id=a.user_id "
                            "WHERE a.chat_id=? AND a.user_id=?", (chat_id, uid))
        if not a:
            continue
        k = (chat_id, uid)
        if time.time() - _afk_note.get(k, 0) < 120:
            continue                         # don't repeat in a busy thread
        _afk_note[k] = time.time()
        since = fmt_dt(a["since"], tz, lang, with_tz=False)
        nm = esc(a["first_name"] or uid)
        why = f": {esc(short(a['reason'], 120))}" if a["reason"] else ""
        await tg.send(chat_id, (f"💤 {nm} از {since} غایبه{why}" if lang == "fa" else f"💤 {nm} is away since {since}{why}"),
                      thread_id=m.message_thread_id if m.is_topic_message else None, reply_to=m.message_id, silent=True)


hooks.AFTER_FILTERS.append(_afk_after)
