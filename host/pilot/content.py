"""Filters, welcome/goodbye, captcha, anti-raid, saved answers and rules."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque

from telegram import ChatPermissions, Message, User

from pilot import audit, authz, confirm, locks, settings as S, tg
from pilot.ctx import Cmd, lang_tz
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.util import esc, fmt_duration, now, split_buttons

log = logging.getLogger("pilot.content")
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|telegram\.dog/|tg://join)", re.I)
_flood: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=60))
_cool: dict[tuple[int, int], float] = {}
_joins: dict[int, deque] = defaultdict(lambda: deque(maxlen=500))
_join_seen: dict[tuple[int, int], float] = {}
_welcome_buf: dict[int, list[User]] = {}
_answer_cool: dict[tuple[int, str], float] = {}

MEDIA_RULES = [("f_photos", "photo"), ("f_videos", "video"), ("f_documents", "document"), ("f_stickers", "sticker"),
               ("f_gifs", "animation"), ("f_voice", "voice"), ("f_voice", "video_note"), ("f_audio", "audio")]


def has_link(m: Message) -> bool:
    for e in (m.entities or ()) + (m.caption_entities or ()):
        if e.type in ("url", "text_link"):
            return True
    return bool(LINK_RE.search((m.text or "") + " " + (m.caption or "")))


def norm_text(s: str) -> str:
    s = (s or "").lower().replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", s)


async def linked_chat(chat_id: int) -> int | None:
    row = await RT.db.one("SELECT linked_chat_id, last_seen FROM rz_chats WHERE chat_id=?", (chat_id,))
    if row and row["last_seen"] and time.time() - row["last_seen"] < 6 * 3600:
        return row["linked_chat_id"]
    out = await tg.call(RT.bot.get_chat, chat_id=chat_id)
    lid = getattr(out.value, "linked_chat_id", None) if out.ok else (row or {}).get("linked_chat_id")
    await RT.db.run("UPDATE rz_chats SET linked_chat_id=?, last_seen=? WHERE chat_id=?", (lid, time.time(), chat_id))
    return lid


async def enforce(m: Message) -> str | None:
    """Apply enabled filters. Returns the filter key that removed the message, if any."""
    chat_id = m.chat_id
    eff = await S.values(chat_id)
    if eff["paused"]:
        return None
    from pilot import hooks
    msg_rules = [k for k in ("f_channels", "f_links", "f_forwards", "f_photos", "f_videos", "f_documents", "f_stickers",
                             "f_gifs", "f_voice", "f_audio", "f_words", "flood_on", "cooldown_on", *hooks.GUARD_KEYS) if eff.get(k)]
    if not msg_rules:
        return None
    sc = m.sender_chat
    if sc is not None:
        if sc.id == chat_id or m.is_automatic_forward:
            return None                     # anonymous admin or linked channel
        if sc.id == await linked_chat(chat_id):
            return None                     # linked discussion channel
        if eff["f_channels"] and sc.type == "channel":
            return await _remove(m, "f_channels", ban_sender_chat=eff["f_channels_action"] == "ban")
        return None
    u = m.from_user
    if u is None or u.id in (RT.bot_id, authz.TG_SERVICE_ID, authz.ANON_ADMIN_BOT):
        return None
    if u.id in await authz.admin_ids(chat_id):
        return None
    from pilot import guards
    if await guards.is_trusted(chat_id, u.id):
        return None
    if eff["f_links"] and has_link(m):
        return await _remove(m, "f_links")
    if eff["f_forwards"] and m.forward_origin is not None:
        return await _remove(m, "f_forwards")
    for key, attr in MEDIA_RULES:
        if eff[key] and (getattr(m, attr, None) or (attr == "photo" and m.photo)):
            if attr == "document" and m.animation:
                continue
            return await _remove(m, key)
    if eff["f_words"]:
        words = await RT.db.all("SELECT word, action FROM rz_blockwords WHERE chat_id=?", (chat_id,))
        txt = " " + norm_text((m.text or "") + " " + (m.caption or "")) + " "
        for w in words:
            if norm_text(w["word"]) and norm_text(w["word"]) in txt:
                rule = await _remove(m, "f_words")
                if rule and w["action"] in ("warn", "mute", "ban"):
                    await _word_action(m, eff, w["action"], w["word"])
                return rule
    key = (chat_id, u.id)
    t = time.time()
    if eff["cooldown_on"]:
        last = _cool.get(key, 0)
        if t - last < eff["cooldown_s"]:
            return await _remove(m, "cooldown_on")
        _cool[key] = t
    if eff["flood_on"]:
        dq = _flood[key]
        dq.append(t)
        recent = [x for x in dq if t - x <= eff["flood_s"]]
        if len(recent) > eff["flood_n"]:
            dq.clear()
            await _remove(m, "flood_on")
            await _flood_action(m, eff)
            return "flood_on"
    for guard in hooks.GUARDS:
        try:
            rule = await guard(m, eff)
        except Exception:  # noqa: BLE001
            log.exception("guard failed")
            continue
        if rule:
            return rule
    return None


async def _word_action(m: Message, eff: dict, action: str, word: str) -> None:
    """Per-word sanction set with /addblock word warn|mute|ban (delete only by default)."""
    from pilot import moderation
    from pilot.targets import Target
    c = Cmd(chat_id=m.chat_id, chat_type=m.chat.type, actor_id=None, actor_name="auto", source="auto", lang=eff["lang"],
            tz=eff["tz"], thread_id=m.message_thread_id if m.is_topic_message else None, is_forum=bool(m.chat.is_forum))
    t = Target(user_id=m.from_user.id, name=m.from_user.full_name, username=m.from_user.username, via="auto", verified=True)
    reason = c.t("کلمهٔ ممنوع", "blocked word")
    if action == "warn":
        r = await moderation.warn(c, t, reason)
    elif action == "ban":
        r = await moderation.ban(c, t, reason=reason)
    else:
        r = await moderation.mute(c, t, duration=3600, reason=reason)
    if r.ok:
        await c.say(r.text, reply=False)


async def _remove(m: Message, rule: str, ban_sender_chat: bool = False) -> str | None:
    out = await tg.delete(m.chat_id, m.message_id)
    sender = m.sender_chat.id if m.sender_chat else (m.from_user.id if m.from_user else None)
    name = (m.sender_chat.title if m.sender_chat else (m.from_user.full_name if m.from_user else "")) or ""
    await audit.record(m.chat_id, None, "auto", "auto_delete", target_id=sender, target_name=name,
                       params={"rule": rule, "message_id": m.message_id}, outcome="ok" if out.ok else "failed",
                       error=None if out.ok else f"{out.kind}: {out.desc}")
    if ban_sender_chat and m.sender_chat:
        r = await tg.call(RT.bot.ban_chat_sender_chat, chat_id=m.chat_id, sender_chat_id=m.sender_chat.id)
        if r.ok:
            await audit.record(m.chat_id, None, "auto", "ban_channel", target_id=m.sender_chat.id, target_name=name,
                               undo={"op": "unban_channel", "chat": m.sender_chat.id}, params={"rule": rule},
                               log_line=f"🚫 <b>#AUTO_BAN_CHANNEL</b>\n📢 {esc(name)} [<code>{m.sender_chat.id}</code>]\n🤖 rule: {rule}")
    if not out.ok:
        await audit.error(m.chat_id, "filter", f"{rule}: delete failed {out.kind}")
        return None
    return rule


async def _flood_action(m: Message, eff: dict) -> None:
    from pilot import moderation
    from pilot.targets import Target
    lang, tz = eff["lang"], eff["tz"]
    c = Cmd(chat_id=m.chat_id, chat_type=m.chat.type, actor_id=None, actor_name="auto", source="auto", lang=lang, tz=tz,
            thread_id=m.message_thread_id if m.is_topic_message else None, is_forum=bool(m.chat.is_forum))
    t = Target(user_id=m.from_user.id, name=m.from_user.full_name, username=m.from_user.username, via="auto", verified=True)
    reason = c.t("فلود", "flooding")
    act = eff["flood_action"]
    if act == "ban":
        r = await moderation.ban(c, t, reason=reason)
    elif act == "kick":
        r = await moderation.kick(c, t, reason=reason)
    else:
        r = await moderation.mute(c, t, duration=600, reason=reason)
    if r.ok:
        await c.say(r.text, reply=False)


# welcome / goodbye
def render(tpl: str, u: User, chat_title: str, count: int | None = None) -> str:
    mention = f'<a href="tg://user?id={u.id}">{esc(u.first_name)}</a>'
    vals = {"first": esc(u.first_name), "last": esc(u.last_name or ""), "fullname": esc(u.full_name),
            "username": ("@" + esc(u.username)) if u.username else mention, "mention": mention, "id": str(u.id),
            "chatname": esc(chat_title), "group": esc(chat_title), "count": str(count or ""), "name": esc(u.first_name),
            "نام": esc(u.first_name), "گروه": esc(chat_title), "منشن": mention}
    return re.sub(r"\{(\w+)\}", lambda mm: vals.get(mm.group(1), mm.group(0)), tpl or "")


DEFAULT_WELCOME = {"fa": "{mention} خوش آمدی", "en": "Welcome {mention}"}


async def send_greeting(chat_id: int, chat_title: str, users: list[User], kind: str = "welcome",
                        preview_to: int | None = None, thread_id: int | None = None) -> tg.Out:
    eff = await S.values(chat_id)
    lang = eff["lang"]
    tpl = eff[f"{kind}_text"] or (DEFAULT_WELCOME[lang] if kind == "welcome" else
                                 ("{fullname} رفت." if lang == "fa" else "{fullname} left."))
    body, rows = split_buttons(tpl)
    if len(users) == 1:
        text = render(body, users[0], chat_title)
    else:
        names = "، ".join(f'<a href="tg://user?id={u.id}">{esc(u.first_name)}</a>' for u in users[:15])
        tmp = body.replace("{mention}", "{__names}").replace("{first}", "{__names}").replace("{fullname}", "{__names}")
        text = render(tmp, users[0], chat_title).replace("{__names}", names)
    media = eff["welcome_media"] if kind == "welcome" else None
    target = preview_to or chat_id
    markup = tg.kb(rows)
    if media:
        mtype, _, fid = media.partition(":")
        fn = {"photo": RT.bot.send_photo, "animation": RT.bot.send_animation, "video": RT.bot.send_video,
              "document": RT.bot.send_document}.get(mtype)
        if fn:
            kw = {"chat_id": target, mtype: fid, "caption": text[:1024], "reply_markup": markup}
            if thread_id:
                kw["message_thread_id"] = thread_id
            out = await tg.call(fn, idempotent=False, **kw)
            if out.ok:
                return out
    return await tg.send(target, text, markup=markup, thread_id=thread_id, silent=kind == "goodbye")


async def on_join(chat, u: User, thread_id: int | None = None) -> None:
    key = (chat.id, u.id)
    t = time.time()
    if t - _join_seen.get(key, 0) < 90:
        return                              # service message and chat_member can both fire
    _join_seen[key] = t
    if len(_join_seen) > 20000:
        _join_seen.clear()
    if u.id == RT.bot_id:
        return
    eff = await S.values(chat.id)
    if eff["paused"]:
        eff = {**eff, "captcha_on": False, "raid_on": False}
    if eff["raid_on"]:
        dq = _joins[chat.id]
        dq.append(t)
        n = len([x for x in dq if t - x <= 60])
        if n >= eff["raid_joins"]:
            has = await RT.db.one("SELECT 1 FROM rz_lock_layers WHERE chat_id=? AND layer='raid'", (chat.id,))
            if not has:
                res = await locks.add_layer(chat.id, "raid", locks.SEND, None, "raid", expires_at=t + eff["raid_lock_min"] * 60)
                lang = eff["lang"]
                if res.ok:
                    await audit.record(chat.id, None, "auto", "lock", params={"layer": "raid", "joins_per_min": n},
                                       log_line=f"#RAID_LOCK {n}/min · {eff['raid_lock_min']} min")
                    await tg.send(chat.id, (f"🚨 رید. گروه {fmt_duration(eff['raid_lock_min'] * 60, 'fa')} بسته شد. /unlock raid") if lang == "fa" else
                                  (f"🚨 Raid. Group locked for {fmt_duration(eff['raid_lock_min'] * 60, 'en')}. /unlock raid"))
    if eff["captcha_on"] and not u.is_bot:
        await start_captcha(chat, u, eff, thread_id)
        return
    if eff["welcome_on"]:
        buf = _welcome_buf.setdefault(chat.id, [])
        buf.append(u)
        if len(buf) == 1:
            asyncio.get_running_loop().call_later(2.5, lambda: asyncio.ensure_future(_flush_welcome(chat.id, chat.title or "", thread_id)))


async def _flush_welcome(chat_id: int, title: str, thread_id: int | None) -> None:
    users = _welcome_buf.pop(chat_id, [])
    if not users:
        return
    out = await send_greeting(chat_id, title, users, "welcome", thread_id=None)
    if not out.ok:
        await audit.error(chat_id, "welcome", f"{out.kind}: {out.desc}")
        return
    if await S.get(chat_id, "welcome_clean"):
        prev = await RT.db.val("SELECT v FROM rz_meta WHERE k=?", (f"lastwelcome:{chat_id}",))
        if prev:
            await tg.delete(chat_id, int(prev))
    await RT.db.run("INSERT OR REPLACE INTO rz_meta VALUES(?,?)", (f"lastwelcome:{chat_id}", str(out.value.message_id)))


async def on_leave(chat, u: User) -> None:
    if u.id == RT.bot_id or u.is_bot:
        return
    if await S.get(chat.id, "goodbye_on"):
        out = await send_greeting(chat.id, chat.title or "", [u], "goodbye")
        if not out.ok:
            await audit.error(chat.id, "goodbye", f"{out.kind}: {out.desc}")


# captcha
async def start_captcha(chat, u: User, eff: dict, thread_id: int | None) -> None:
    lang = eff["lang"]
    if not await authz.bot_has(chat.id, "restrict"):
        await audit.error(chat.id, "captcha", "bot lacks restrict right; captcha skipped")
        return
    out = await tg.call(RT.bot.restrict_chat_member, chat_id=chat.id, user_id=u.id,
                        permissions=ChatPermissions(can_send_messages=False), use_independent_chat_permissions=True)
    if not out.ok:
        await audit.error(chat.id, "captcha", f"restrict failed {out.kind}")
        return
    tok = await confirm.create(chat.id, u.id, "captcha", {"user": u.id, "name": u.first_name[:40]}, right=None,
                               ttl=eff["captcha_min"] * 60)
    mention = f'<a href="tg://user?id={u.id}">{esc(u.first_name)}</a>'
    txt = (f"{mention} دکمه را بزن ({fmt_duration(eff['captcha_min'] * 60, 'fa')})" if lang == "fa" else
           f"{mention} press the button ({fmt_duration(eff['captcha_min'] * 60, 'en')})")
    sent = await tg.send(chat.id, txt, rows=[[("✅ ربات نیستم" if lang == "fa" else "✅ I'm not a bot", f"cap:{tok}")]], thread_id=thread_id)
    if sent.ok:
        await RT.db.run("UPDATE rz_confirm SET payload=? WHERE token=?",
                        (jd({"user": u.id, "name": u.first_name[:40], "msg": sent.value.message_id}), tok))


async def solve_captcha(token: str, presser: int, chat_id: int) -> tuple[bool, str, int | None]:
    lang, _ = await lang_tz(chat_id)
    r = await RT.db.one("SELECT * FROM rz_confirm WHERE token=? AND kind='captcha'", (token,))
    if not r or r["chat_id"] != chat_id or r["status"] != "pending":
        return False, "منقضی شده." if lang == "fa" else "Expired.", None
    if r["user_id"] != presser:
        return False, "مال شما نیست." if lang == "fa" else "Not for you.", None
    _, n = await RT.db.run("UPDATE rz_confirm SET status='done' WHERE token=? AND status='pending'", (token,))
    if n != 1:
        return False, "قبلاً انجام شد." if lang == "fa" else "Already done.", None
    out = await tg.call(RT.bot.restrict_chat_member, chat_id=chat_id, user_id=presser, permissions=ChatPermissions.all_permissions(),
                        use_independent_chat_permissions=True)
    p = jl(r["payload"], {})
    if out.ok and await S.get(chat_id, "welcome_on"):
        row = await RT.db.one("SELECT title FROM rz_chats WHERE chat_id=?", (chat_id,))
        u = User(id=presser, first_name=p.get("name") or "", is_bot=False)
        await send_greeting(chat_id, (row or {}).get("title") or "", [u], "welcome")
    return out.ok, (("✅" if lang == "fa" else "✅") if out.ok else esc(out.desc)), p.get("msg")


async def expire_captchas() -> None:
    rows = await RT.db.all("SELECT * FROM rz_confirm WHERE kind='captcha' AND status='pending' AND expires_at<=?", (now(),))
    for r in rows:
        _, n = await RT.db.run("UPDATE rz_confirm SET status='expired' WHERE token=? AND status='pending'", (r["token"],))
        if n != 1:
            continue
        p = jl(r["payload"], {})
        if p.get("msg"):
            await tg.delete(r["chat_id"], p["msg"])
        if await S.get(r["chat_id"], "captcha_fail") == "kick":
            out = await tg.call(RT.bot.ban_chat_member, chat_id=r["chat_id"], user_id=r["user_id"], until_date=int(now() + 60))
            if out.ok:
                await tg.call(RT.bot.unban_chat_member, chat_id=r["chat_id"], user_id=r["user_id"], only_if_banned=True)
                await audit.record(r["chat_id"], None, "auto", "auto_kick", target_id=r["user_id"], target_name=p.get("name", ""),
                                   reason="captcha timeout", log_line=f"👢 <b>#CAPTCHA_KICK</b> <code>{r['user_id']}</code>")


# help center
NAME_RE = re.compile(r"^[\w\u0600-\u06FF\-]{1,40}$")


async def answer_send(chat_id: int, row: dict, thread_id: int | None = None, reply_to: int | None = None) -> tg.Out:
    body, rows = split_buttons(row["body"] or "")
    markup = tg.kb(rows)
    if row.get("file_id") and row.get("media_type"):
        fn = {"photo": RT.bot.send_photo, "animation": RT.bot.send_animation, "video": RT.bot.send_video,
              "document": RT.bot.send_document, "audio": RT.bot.send_audio, "voice": RT.bot.send_voice,
              "sticker": RT.bot.send_sticker}.get(row["media_type"])
        if fn:
            kw = {"chat_id": chat_id, row["media_type"]: row["file_id"], "reply_markup": markup}
            if row["media_type"] != "sticker":
                kw["caption"] = body[:1024] or None
            if thread_id:
                kw["message_thread_id"] = thread_id
            out = await tg.call(fn, idempotent=False, **kw)
            if out.ok:
                return out
    return await tg.send(chat_id, body or "—", markup=markup, thread_id=thread_id, reply_to=reply_to)


async def answer_get(chat_id: int, name: str) -> dict | None:
    return await RT.db.one("SELECT * FROM rz_answers WHERE chat_id=? AND name=?", (chat_id, name.lower().lstrip("#")))


async def answer_save(chat_id: int, name: str, body: str, media_type: str | None, file_id: str | None, actor: int | None) -> str:
    name = name.lower().lstrip("#")
    if not NAME_RE.match(name):
        raise ValueError("bad name")
    old = await answer_get(chat_id, name)
    await RT.db.run("INSERT INTO rz_answers(chat_id,name,body,media_type,file_id,keywords,created_by,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(chat_id,name) DO UPDATE SET body=excluded.body, media_type=excluded.media_type, "
                    "file_id=excluded.file_id, created_by=excluded.created_by, updated_at=excluded.updated_at",
                    (chat_id, name, body[:3800], media_type, file_id, (old or {}).get("keywords") or "[]", actor, now()))
    return "updated" if old else "created"


async def answer_keyword_hit(m: Message) -> bool:
    """Keyword auto-answers, rate limited per answer."""
    text = norm_text(m.text or m.caption or "")
    if not text or len(text) > 500:
        return False
    rows = await RT.db.all("SELECT * FROM rz_answers WHERE chat_id=? AND keywords<>'[]'", (m.chat_id,))
    padded = f" {text} "
    for r in rows:
        for kw in jl(r["keywords"], []) or []:
            k = norm_text(kw)
            if k and (f" {k} " in padded or (len(k) > 3 and k in text)):
                ck = (m.chat_id, r["name"])
                if time.time() - _answer_cool.get(ck, 0) < 60:
                    return False
                _answer_cool[ck] = time.time()
                await answer_send(m.chat_id, r, thread_id=m.message_thread_id if m.is_topic_message else None,
                                  reply_to=m.message_id)
                return True
    return False


async def faq_menu(chat_id: int, lang: str) -> tuple[str, list]:
    rows = await RT.db.all("SELECT name FROM rz_answers WHERE chat_id=? ORDER BY name LIMIT 40", (chat_id,))
    has_rules = await RT.db.one("SELECT 1 FROM rz_rules WHERE chat_id=?", (chat_id,))
    btns: list[list[tuple[str, str]]] = []
    if has_rules:
        btns.append([("📜 قوانین" if lang == "fa" else "📜 Rules", "fq:__rules")])
    row: list[tuple[str, str]] = []
    for r in rows:
        row.append((r["name"], f"fq:{r['name']}"[:64]))
        if len(row) == 3:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    if not btns:
        return (("چیزی ذخیره نشده. <code>/save نام متن</code>" if lang == "fa" else
                 "Nothing saved. <code>/save name text</code>"), [])
    return (("<b>پاسخ‌ها</b>" if lang == "fa" else "<b>Answers</b>"), btns)
