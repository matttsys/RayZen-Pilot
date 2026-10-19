"""Update router."""
from __future__ import annotations

import logging
import re
import time

from telegram import ChatMemberRestricted, Update, User

from pilot import ai, audit, authz, confirm, content, desk, fa_commands, hooks, logs, purgeword, settings as S, targets, tg, tools, ui
from pilot import commands as K
from pilot import commands2 as K2
from pilot import features  # noqa: F401  (registers the v5 feature modules: commands, hooks, AI tools)
from pilot.ctx import Cmd, build, lang_tz, _user_lang
from pilot.db import jl
from pilot.rt import RT
from pilot.util import esc, now

log = logging.getLogger("pilot.router")
STALE_S = 600
_deny_cool: dict[tuple[int, int], float] = {}
_leave_seen: dict[tuple[int, int], float] = {}
_chat_known: dict[int, str] = {}

def _is_member_status(cm) -> bool:
    if cm is None:
        return False
    if cm.status in ("member", "administrator", "creator"):
        return True
    return isinstance(cm, ChatMemberRestricted) and bool(cm.is_member)


async def ensure_chat(chat) -> None:
    title = (chat.title or "")[:128]
    if _chat_known.get(chat.id) == title:
        return
    row = await RT.db.one("SELECT bot_status FROM rz_chats WHERE chat_id=?", (chat.id,))
    if row is None:
        await RT.db.run("INSERT OR IGNORE INTO rz_chats(chat_id,title,type,is_forum,added_at,onboarded) VALUES(?,?,?,?,?,0)",
                        (chat.id, title, chat.type, int(bool(chat.is_forum)), now()))
    else:
        await RT.db.run("UPDATE rz_chats SET title=?, type=?, is_forum=? WHERE chat_id=?", (title, chat.type, int(bool(chat.is_forum)), chat.id))
    if row is None or not row["bot_status"]:
        bm = await authz.bot_member(chat.id, fresh=True)
        if bm is not None:
            await RT.db.run("UPDATE rz_chats SET bot_status=? WHERE chat_id=?", (bm.status, chat.id))
    _chat_known[chat.id] = title


# entry
async def dispatch(update: Update, context=None) -> None:
    try:
        if update.callback_query:
            await on_callback(update)
        elif update.message:
            await on_message(update)
        elif update.edited_message:
            await purgeword.note(update.edited_message, edited=True)
        elif update.chat_join_request:
            await desk.on_join_request(update.chat_join_request)
        elif update.my_chat_member:
            await on_my_member(update)
        elif update.chat_member:
            await on_chat_member(update)
        elif update.channel_post:
            await on_channel_post(update)
    except Exception as exc:  # noqa: BLE001
        log.exception("update %s failed", update.update_id)
        chat = update.effective_chat
        await audit.error(chat.id if chat and chat.id < 0 else None, "router", f"{type(exc).__name__}: {str(exc)[:200]}")


# messages
async def on_message(update: Update) -> None:
    m = update.message
    chat = m.chat
    if chat.type == "channel":
        return
    group = chat.type in ("group", "supergroup")
    if m.migrate_to_chat_id:
        await RT.db.migrate_chat(chat.id, m.migrate_to_chat_id)
        S.invalidate(chat.id)
        S.invalidate(m.migrate_to_chat_id)
        _chat_known.pop(chat.id, None)
        log.info("chat %s migrated to %s", chat.id, m.migrate_to_chat_id)
        return
    if m.migrate_from_chat_id:
        return
    u = m.from_user
    if u:
        await targets.note_user(u, chat.id if group else None)
    if m.reply_to_message and m.reply_to_message.from_user:
        await targets.note_user(m.reply_to_message.from_user, chat.id if group else None)
    if group:
        await ensure_chat(chat)
    thread = m.message_thread_id if m.is_topic_message else None
    # service messages
    if m.new_chat_members:
        for nu in m.new_chat_members:
            await targets.note_user(nu, chat.id, status="member")
            if nu.id != RT.bot_id:
                by = u.id if u and u.id != nu.id else None
                await hooks.run(hooks.ON_MEMBER, chat, nu, True, by, None, "service")
                await content.on_join(chat, nu, thread)
        await _clean_service(m)
        return
    if m.left_chat_member:
        lu = m.left_chat_member
        if lu.id != RT.bot_id:
            by = u.id if u and u.id != lu.id else None
            await hooks.run(hooks.ON_MEMBER, chat, lu, False, by, None, "service")
        if lu.id != RT.bot_id and _first_leave(chat.id, lu.id):
            await content.on_leave(chat, lu)
        await _clean_service(m)
        return
    if group and not m.new_chat_members and not m.left_chat_member:
        await hooks.run(hooks.ON_GROUP_MESSAGE, m)
    if m.date and time.time() - m.date.timestamp() > STALE_S:
        if group:
            await purgeword.note(m)             # still deletable for 48h
        return                                  # skip commands queued while offline
    text = m.text or m.caption or ""
    if chat.type == "private":
        if u:
            await RT.db.run("UPDATE rz_users SET dm_ok=1 WHERE user_id=?", (u.id,))
        if m.document and await hooks.first(hooks.PRIVATE_DOCUMENT, m):
            return
        if text.startswith("/"):
            if await run_command(update, text):
                return
        if u and text and not text.startswith("/"):
            d = await desk.pop_draft(u.id)
            if d:
                return await _draft(update, d, text)
            if await persian_command(update, text):
                return
            if re.fullmatch(r"\s*RZ-[0-9A-Fa-f]{8}\s*", text):
                return await tg.send(chat.id, "این کد را در گروه یا کانال لاگ بفرستید." if (await _user_lang(u)) == "fa" else
                                     "Send this code in the log group or channel.")
            c = await build(update, name="ai", text=text, source="mention")
            if c is None or await hooks.first(hooks.PRIVATE_TEXT, c):
                return
            await ai.handle(c, text)
        return
    if not group:
        return
    # filters (only the enabled ones)
    if await content.enforce(m):
        return
    if text.startswith("/"):
        if await run_command(update, text):
            return
    await hooks.run(hooks.AFTER_FILTERS, m, text)
    if not text:
        return
    if await persian_command(update, text):
        return
    ai_text = await _ai_trigger(m, text)
    if ai_text is None:
        await purgeword.note(m)                 # commands and requests to the bot are not indexed
    if text.startswith("#"):
        name = text[1:].split(None, 1)[0] if len(text) > 1 else ""
        row = await content.answer_get(chat.id, name) if name else None
        if row:
            await content.answer_send(chat.id, row, thread_id=thread, reply_to=m.message_id)
            return
    if ai_text is not None:
        c = await build(update, name="ai", text=ai_text, source="mention")
        await ai.handle(c, ai_text)
        return
    if await S.get(chat.id, "answers_auto"):
        await content.answer_keyword_hit(m)


def _first_leave(chat_id: int, user_id: int) -> bool:
    t = time.time()
    if t - _leave_seen.get((chat_id, user_id), 0) < 90:
        return False
    _leave_seen[(chat_id, user_id)] = t
    if len(_leave_seen) > 20000:
        _leave_seen.clear()
    return True


async def _clean_service(m) -> None:
    if await S.get(m.chat_id, "clean_service") and not await S.get(m.chat_id, "paused"):
        out = await tg.delete(m.chat_id, m.message_id)
        if out.ok:
            await audit.record(m.chat_id, None, "auto", "auto_delete", params={"rule": "clean_service", "message_id": m.message_id})


async def _ai_trigger(m, text: str) -> str | None:
    """Text for the assistant when the message is addressed to it: @mention, reply to one of its answers,
    or a message that starts with the group's assistant name. Anything else is ordinary chat."""
    from pilot import assistant
    un = RT.bot_username.lower()
    if un:
        for e in (m.entities or ()) + (m.caption_entities or ()):
            if e.type == "mention":
                frag = text[e.offset:e.offset + e.length] if len(text) >= e.offset + e.length else ""
                if frag.lower() == "@" + un:
                    stripped = (text[:e.offset] + text[e.offset + e.length:]).strip()
                    return stripped
    r = m.reply_to_message
    if r and r.from_user and r.from_user.id == RT.bot_id:
        rt = (r.text or "").lstrip()
        if assistant.is_answer(m.chat_id, r.message_id) or rt.startswith(("🤖", "❓")):
            return text
    name = await S.get(m.chat_id, "ai_name")
    if name and RT.cfg.ai_configured and fa_commands.intentional(m):
        n, t = fa_commands.norm(name), fa_commands.norm(text)
        if t == n:
            return ""
        for sep in (" ", "،", ",", ":", "؛"):
            if t.startswith(n + sep):
                cut = len(name)
                return text.strip()[cut:].lstrip(" ،,:؛\u200c").strip() or text
    return None


async def _draft(update: Update, d: dict, text: str) -> None:
    u = update.effective_user
    lang = await _user_lang(u)
    if d.get("kind") == "appeal":
        ok, msg = await desk.open_appeal(int(d["chat"]), int(d["case"]), u, text, lang)
    elif d.get("kind") == "ticket":
        ok, msg, _ = await desk.open_ticket(int(d["chat"]), u, text, lang)
    else:
        msg = "-"
    await tg.send(update.effective_chat.id, msg)


# commands
CMD_RE = re.compile(r"^/([A-Za-z0-9_]{1,32})(?:@([A-Za-z0-9_]{3,64}))?(?=\s|$)")


async def run_command(update: Update, text: str, name: str | None = None, source: str = "cmd", html_skip: int = 1) -> bool:
    if name is None:
        mm = CMD_RE.match(text)
        if not mm:
            return False
        name, at = mm.group(1).lower(), mm.group(2)
        if at and RT.bot_username and at.lower() != RT.bot_username.lower():
            return True                                         # addressed to another bot
    spec = K.REG.get(name)
    if spec is None:
        return False
    c = await build(update, name=name, text=text, html_skip=html_skip)
    if c is None:
        return True
    c.source = source
    from pilot import metrics
    metrics.count("cmd:" + (spec.names[0] if spec.names else name))
    metrics.count("src:" + source)
    if spec.scope == "group" and not c.is_group:
        await c.say(c.t("فقط در گروه.", "Group only."))
        return True
    if spec.scope == "private" and not c.is_private:
        await c.say(c.t("فقط در پیوی ربات.", "Private chat only."),
                    rows=[[(c.t("پیوی ربات", "Open bot"), K2.bot_link())]])
        return True
    if spec.admin and c.is_group:
        if c.anon:
            tok = await confirm.create(c.chat_id, None, "anon_cmd", {"cmd": c.to_payload()}, right=spec.right, ttl=300)
            await c.say(c.t("ادمین ناشناس: برای اجرا دکمه را بزنید.", "Anonymous admin: press to run."),
                        rows=[[(c.t("✅ اجرا", "✅ Run"), f"cf:y:{tok}")]])
            return True
        ok, why = await authz.check(c.chat_id, c.actor_id, spec.right, fresh=True)
        if not ok:
            key = (c.chat_id, c.actor_id or 0)
            if time.time() - _deny_cool.get(key, 0) > 60:
                _deny_cool[key] = time.time()
                if why == "missing_right" and spec.right:
                    await c.say(c.t(f"⛔️ دسترسی «{authz.right_label(spec.right, 'fa')}» ندارید.",
                                    f"⛔️ You need the '{authz.right_label(spec.right, 'en')}' right."))
                else:
                    await c.say(c.t("⛔️ فقط ادمین‌ها.", "⛔️ Admins only."))
            return True
    await _run_spec(spec, c)
    return True


async def _run_spec(spec, c: Cmd) -> None:
    t0 = time.monotonic()
    try:
        await spec.fn(c)
    except Exception as exc:  # noqa: BLE001
        log.exception("command /%s failed", c.name)
        await audit.error(c.chat_id if c.is_group else None, "cmd:" + c.name, f"{type(exc).__name__}: {str(exc)[:200]}")
        await c.say(c.t("❌ خطا. انجام نشد.", "❌ Error. Not done."))
    finally:
        dt = time.monotonic() - t0
        RT.tick_stats.setdefault("cmd_lat", []).append(dt)
        if len(RT.tick_stats["cmd_lat"]) > 500:
            del RT.tick_stats["cmd_lat"][:250]


@confirm.handler("anon_cmd")
async def _anon_cmd(p: dict, presser: int, chat_id: int, message) -> str:
    c = Cmd.from_payload(p["cmd"])
    c.actor_id, c.anon, c.actor_name = presser, False, p.get("_pname", "")[:64]
    spec = K.REG.get(c.name)
    if spec is None:
        return "-"
    await _run_spec(spec, c)
    return c.t("✅ اجرا شد.", "✅ Done.")


async def persian_command(update: Update, text: str) -> bool:
    """Persian text command. Unknown or casual text returns False and is treated as chat."""
    m = update.message
    if not fa_commands.intentional(m):
        return False
    hit = fa_commands.match(text)
    if hit is None:
        return False
    cmd, rest, kind, words = hit
    cmd, _, fixed = cmd.partition(" ")           # phrases may carry fixed arguments ("welcome preview")
    if fixed:
        rest = (fixed + " " + rest).strip()
    spec = K.REG.get(cmd)
    if spec is None:
        return False
    if spec.scope == "private" and m.chat.type != "private":
        return False
    if spec.scope == "group" and m.chat.type == "private":
        return False
    if spec.admin and m.chat.type != "private":
        u = m.from_user
        anon = m.sender_chat is not None and m.sender_chat.id == m.chat_id
        if not anon and (u is None or u.id not in await authz.admin_ids(m.chat_id)):
            return False                         # a member saying "بن" is just chatting
    if kind == "target" and not fa_commands.has_target(m, rest):
        return False
    if m.chat.type == "private" and rest and spec.section not in ("utils", "general"):
        return False                             # in private chat full sentences go to the assistant
    return await run_command(update, f"/{cmd} {rest}".strip(), name=cmd, source="fa", html_skip=words)


# callbacks
def _qcmd(q, lang: str, tz: str, name: str = "") -> Cmd:
    msg = q.message
    chat = msg.chat
    is_topic = bool(getattr(msg, "is_topic_message", False))
    return Cmd(chat_id=chat.id, chat_type=chat.type, chat_title=(chat.title or "")[:128], is_forum=bool(chat.is_forum),
               thread_id=getattr(msg, "message_thread_id", None) if is_topic else None, message_id=msg.message_id,
               actor_id=q.from_user.id, actor_name=q.from_user.full_name[:64], name=name, lang=lang, tz=tz,
               source="button", update_id=None)


async def on_callback(update: Update) -> None:
    q = update.callback_query
    data = q.data or ""
    msg = q.message
    if msg is None or getattr(msg, "chat", None) is None:
        return await tg.answer(q, "منقضی شده.")
    chat = msg.chat
    user = q.from_user
    group = chat.type in ("group", "supergroup")
    await targets.note_user(user, chat.id if group else None)
    if group:
        lang, tz = await lang_tz(chat.id)
    else:
        lang, tz = await _user_lang(user), RT.cfg.tz
    fa = lang == "fa"
    parts = data.split(":")
    fam = parts[0]
    c = _qcmd(q, lang, tz)
    try:
        fn = CALLBACKS.get(fam) or hooks.CALLBACKS.get(fam)
        if fn is None:
            return await tg.answer(q, "منقضی شده." if fa else "Expired.")
        await fn(q, c, parts)
    except Exception as exc:  # noqa: BLE001
        log.exception("callback %s failed", fam)
        await audit.error(chat.id if group else None, "cb:" + fam, f"{type(exc).__name__}: {str(exc)[:200]}")
        await tg.answer(q, "❌ خطا. انجام نشد." if fa else "❌ Error. Not done.", alert=True)


async def _need(q, c: Cmd, right: str | None, chat_id: int | None = None) -> bool:
    cid = chat_id if chat_id is not None else c.chat_id
    if cid > 0:
        return True
    ok, why = await authz.check(cid, q.from_user.id, right, fresh=True)
    if not ok:
        if why == "missing_right" and right:
            await tg.answer(q, c.t(f"⛔️ دسترسی «{authz.right_label(right, 'fa')}» ندارید.",
                                   f"⛔️ You need the '{authz.right_label(right, 'en')}' right."), alert=True)
        else:
            await tg.answer(q, c.t("⛔️ فقط ادمین‌ها.", "⛔️ Admins only."), alert=True)
    return ok


async def cb_confirm(q, c: Cmd, p: list[str]) -> None:
    if len(p) < 3:
        return await tg.answer(q)
    op, tok = p[1], ":".join(p[2:])
    if op == "n":
        txt = await confirm.cancel(tok, q.from_user.id, c.chat_id, c.lang)
        await tg.answer(q, txt)
        if "لغو شد" in txt or "Cancelled" in txt:
            await tg.edit(q.message, "✖️ " + esc(txt))
        return
    r, err = await confirm.take(tok, q.from_user.id, c.chat_id, c.lang)
    if r is None:
        return await tg.answer(q, err, alert=True)
    await tg.answer(q)
    r["payload"]["_pname"] = q.from_user.full_name
    try:
        text = await confirm.run(r, q.from_user.id, q.message)
    except Exception:  # noqa: BLE001
        log.exception("confirmation %s failed", r["kind"])
        text = c.t("❌ خطا. انجام نشد.", "❌ Error. Not done.")
    if text:
        await tg.edit(q.message, text)
    else:
        await tg.call(RT.bot.edit_message_reply_markup, chat_id=c.chat_id, message_id=q.message.message_id, reply_markup=None)


async def cb_captcha(q, c: Cmd, p: list[str]) -> None:
    ok, txt, mid = await content.solve_captcha(":".join(p[1:]), q.from_user.id, c.chat_id)
    await tg.answer(q, txt, alert=not ok)
    if ok and mid:
        await tg.delete(c.chat_id, mid)


async def cb_settings(q, c: Cmd, p: list[str]) -> None:
    if c.chat_id > 0:
        return await tg.answer(q, c.t("در گروه /settings بزنید.", "Use /settings in the group."), alert=True)
    op = p[1] if len(p) > 1 else "main"
    if op == "x":
        if not await _need(q, c, None):
            return
        await tg.answer(q)
        await tg.delete(c.chat_id, q.message.message_id)
        return
    if not await _need(q, c, None):
        return
    if op in ("t", "c", "i") and len(p) >= 4:
        key, val = p[2], ":".join(p[3:])
        if key not in S.SPECS:
            return await tg.answer(q, c.t("نامعتبر.", "Invalid."), alert=True)
        if not await _need(q, c, ui.right_for(key)):
            return
        txt = await K.set_setting(c, key, val)
        plain = re.sub(r"<[^>]+>", "", txt)
        await tg.answer(q, plain[:190], alert="⚠️" in plain or "❌" in plain)
        t2, rows = await ui.settings_section(c.chat_id, S.SPECS[key].section, c.lang)
        await tg.edit(q.message, t2, rows)
        return
    await tg.answer(q)
    if op == "s" and len(p) > 2 and p[2] in S.SECTIONS:
        t2, rows = await ui.settings_section(c.chat_id, p[2], c.lang)
    elif op == "logs":
        t2, rows = await ui.logs_page(c.chat_id, c.lang, c.tz)
    elif op == "locks":
        t2, rows = await ui.locks_page(c.chat_id, c.lang, c.tz)
    elif op == "health":
        t2 = await tools.health_text(c.chat_id, c.lang, c.tz, c.is_forum)
        rows = [[("◀️ " + c.t("بازگشت", "Back"), "st:main")]]
    elif op == "legacy":
        t2, rows = await ui.legacy_page(c.chat_id, c.lang)
    else:
        t2, rows = await ui.settings_main(c.chat_id, c.chat_title, c.lang)
    await tg.edit(q.message, t2, rows)


async def cb_help(q, c: Cmd, p: list[str]) -> None:
    from pilot import catalog
    await tg.answer(q)
    role = await catalog.role_of(c.chat_id, c.chat_type, q.from_user.id)
    key = p[1] if len(p) > 1 else "main"
    page = int(p[2]) if len(p) > 2 and p[2].isdigit() else 0
    if key in catalog.CAT_KEYS or key in catalog.OLD_KEYS:
        t2, rows = catalog.category_page(key, page, c.lang, role, c.chat_id > 0)
    else:
        t2, rows = catalog.main_page(c.lang, role)
    await tg.edit(q.message, t2, rows)


async def cb_logs(q, c: Cmd, p: list[str]) -> None:
    if not await _need(q, c, "info"):
        return
    op = p[1] if len(p) > 1 else ""
    if op == "off":
        await logs.disable(c.chat_id)
        await tg.answer(q)
        await tg.edit(q.message, c.t("لاگ خاموش شد.", "Logs off."))
        return
    await tg.answer(q)
    ok, msg = await logs.repair(c.chat_id, q.from_user.id, c.lang)
    await c.say(msg, reply=False)


async def cb_undo(q, c: Cmd, p: list[str]) -> None:
    if not await _need(q, c, "restrict"):
        return
    if len(p) < 2 or not p[1].isdigit():
        return await tg.answer(q)
    ok, msg = await audit.undo(c.chat_id, int(p[1]), q.from_user.id, c.lang)
    await tg.answer(q, re.sub(r"<[^>]+>", "", msg)[:190], alert=not ok)
    if ok:
        await c.say(f"↩️ {msg} ({esc(q.from_user.full_name)})", reply=False)


async def _dm_or_alert(q, c: Cmd, text_html: str) -> None:
    out = await tg.send(q.from_user.id, text_html)
    if out.ok:
        await tg.answer(q, c.t("در پیوی فرستاده شد.", "Sent in private."))
    else:
        await tg.answer(q, re.sub(r"<[^>]+>", "", text_html)[:190], alert=True)


async def cb_appeal(q, c: Cmd, p: list[str]) -> None:
    if len(p) < 3 or not p[2].isdigit():
        return await tg.answer(q)
    ap = await RT.db.one("SELECT * FROM rz_appeals WHERE id=?", (int(p[2]),))
    if not ap:
        return await tg.answer(q, c.t("پیدا نشد.", "Not found."), alert=True)
    if not await _need(q, c, "restrict", ap["chat_id"]):
        return
    if p[1] == "v":
        return await _dm_or_alert(q, c, f"⚖️ #{ap['id']} · case #{ap['case_no']} · <code>{ap['user_id']}</code>\n{esc(ap['text'])}")
    await tg.answer(q)
    ok, msg = await desk.decide_appeal(ap["id"], p[1] == "y", q.from_user.id, ap["chat_id"])
    base = re.sub(r"\n\n» .*$", "", q.message.text_html or "", flags=re.S) if getattr(q.message, "text_html", None) else ""
    await tg.edit(q.message, (base + "\n\n» " if base else "") + msg + f" ({esc(q.from_user.full_name)})", rows=None)


async def cb_ticket(q, c: Cmd, p: list[str]) -> None:
    if len(p) < 3 or not p[2].isdigit():
        return await tg.answer(q)
    t = await RT.db.one("SELECT * FROM rz_tickets WHERE id=?", (int(p[2]),))
    if not t:
        return await tg.answer(q, c.t("پیدا نشد.", "Not found."), alert=True)
    if not await _need(q, c, None, t["chat_id"]):
        return
    op = p[1]
    tl, _ = await lang_tz(t["chat_id"])
    fa = tl == "fa"
    if op == "v":
        return await _dm_or_alert(q, c, f"🎫 #{t['id']} · <code>{t['user_id']}</code>\n{esc(t['text'])}\n\n" +
                                  (f"<code>/treply {t['id']} متن</code>" if fa else f"<code>/treply {t['id']} text</code>"))
    if t["status"] == "closed":
        return await tg.answer(q, "بسته است." if fa else "Closed.", alert=True)
    if op == "c":
        await RT.db.run("UPDATE rz_tickets SET status='claimed', claimed_by=? WHERE id=?", (q.from_user.id, t["id"]))
        await tg.answer(q, "با شما." if fa else "Yours.")
        note = f"🙋 {esc(q.from_user.full_name)}"
    else:
        await RT.db.run("UPDATE rz_tickets SET status='closed', closed_at=? WHERE id=?", (now(), t["id"]))
        await tg.answer(q, "بسته شد." if fa else "Closed.")
        dm = await tg.send(t["user_id"], (f"تیکت #{t['id']} بسته شد." if fa else f"Ticket #{t['id']} closed."))
        note = f"✅ {'بسته شد' if fa else 'Closed'} ({esc(q.from_user.full_name)})" + ("" if dm.ok else (" · به کاربر نرسید" if fa else " · user not notified"))
    base = getattr(q.message, "text_html", "") or ""
    rows = None if op == "x" else [[("👁", f"tk:v:{t['id']}"), ("✅", f"tk:x:{t['id']}")]]
    await tg.edit(q.message, base + "\n" + note, rows)


async def cb_join(q, c: Cmd, p: list[str]) -> None:
    op = p[1] if len(p) > 1 else ""
    if op == "a" and len(p) >= 3:
        ok, msg = await desk.screen_agree(int(p[2]), q.from_user.id)
        await tg.answer(q, re.sub(r"<[^>]+>", "", msg)[:190], alert=not ok)
        if ok:
            await tg.edit(q.message, (getattr(q.message, "text_html", "") or "") + "\n\n" + msg)
        return
    if op not in ("y", "n") or len(p) < 4:
        return await tg.answer(q)
    chat_id, uid = int(p[2]), int(p[3])
    if not await _need(q, c, "invite", chat_id):
        return
    ok, msg = await desk.decide_join(chat_id, uid, op == "y", q.from_user.id, c.lang)
    await tg.answer(q, re.sub(r"<[^>]+>", "", msg)[:190], alert=not ok)
    await tg.edit(q.message, (getattr(q.message, "text_html", "") or "") + f"\n\n{msg} ({esc(q.from_user.full_name)})")


async def cb_reminder(q, c: Cmd, p: list[str]) -> None:
    if len(p) < 3 or not p[2].isdigit():
        return await tg.answer(q)
    r = await RT.db.one("SELECT * FROM rz_reminders WHERE id=?", (int(p[2]),))
    if not r:
        return await tg.answer(q, c.t("پیدا نشد.", "Not found."), alert=True)
    mine = r["user_id"] == q.from_user.id
    if p[1] == "s":
        if not mine:
            return await tg.answer(q, c.t("مال شما نیست.", "Not yours."), alert=True)
        secs = int(p[3]) if len(p) > 3 and p[3].isdigit() else 600
        secs = max(60, min(secs, 86400))
        await RT.db.run("INSERT INTO rz_reminders(chat_id,thread_id,user_id,scope,text,due,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (r["chat_id"], r["thread_id"], r["user_id"], r["scope"], r["text"], now() + secs, "active", now()))
        from pilot.util import fmt_dt, fmt_duration
        await tg.answer(q, c.t(f"⏰ {fmt_duration(secs, 'fa')} دیگر", f"⏰ In {fmt_duration(secs, 'en')}"))
        await tg.call(RT.bot.edit_message_reply_markup, chat_id=c.chat_id, message_id=q.message.message_id, reply_markup=None)
        return
    if p[1] == "x":
        if not mine:
            ok = r["scope"] == "group" and r["chat_id"] < 0 and (await authz.check(r["chat_id"], q.from_user.id, "info"))[0]
            if not ok:
                return await tg.answer(q, c.t("فقط سازنده یا ادمین‌ها.", "Creator or admins only."), alert=True)
        _, n = await RT.db.run("UPDATE rz_reminders SET status='cancelled' WHERE id=? AND status='active'", (r["id"],))
        await tg.answer(q, c.t("لغو شد.", "Cancelled.") if n else c.t("فعال نبود.", "Not active."))


async def cb_event(q, c: Cmd, p: list[str]) -> None:
    from pilot import planner
    if len(p) < 3 or not p[2].isdigit():
        return await tg.answer(q)
    ev = await RT.db.one("SELECT * FROM rz_events WHERE id=? AND chat_id=?", (int(p[2]), c.chat_id))
    if not ev or ev["status"] != "active":
        return await tg.answer(q, c.t("فعال نیست.", "Not active."), alert=True)
    uid, name = q.from_user.id, q.from_user.full_name[:64]
    op = p[1]
    if op == "c":
        if now() < ev["starts_at"] - 1800:
            return await tg.answer(q, c.t("از ۳۰ دقیقه قبل از شروع.", "Opens 30 min before start."), alert=True)
        await RT.db.run("INSERT INTO rz_rsvps(event_id,user_id,status,checked_in,name,ts) VALUES(?,?,?,?,?,?) ON CONFLICT(event_id,user_id) "
                        "DO UPDATE SET checked_in=1, name=excluded.name", (ev["id"], uid, "going", 1, name, now()))
        await tg.answer(q, c.t("ثبت شد.", "Checked in."))
    else:
        st = {"g": "going", "m": "maybe", "n": "no"}.get(op)
        if not st:
            return await tg.answer(q)
        await RT.db.run("INSERT INTO rz_rsvps(event_id,user_id,status,checked_in,name,ts) VALUES(?,?,?,0,?,?) ON CONFLICT(event_id,user_id) "
                        "DO UPDATE SET status=excluded.status, name=excluded.name, ts=excluded.ts", (ev["id"], uid, st, name, now()))
        await tg.answer(q, {"going": c.t("می‌آیم", "Going"), "maybe": c.t("شاید", "Maybe"), "no": c.t("نمی‌آیم", "Not going")}[st])
    if not ev.get("card_msg_id"):
        await RT.db.run("UPDATE rz_events SET card_msg_id=? WHERE id=?", (q.message.message_id, ev["id"]))
        ev["card_msg_id"] = q.message.message_id
    await planner.refresh_card(ev)


async def cb_announce(q, c: Cmd, p: list[str]) -> None:
    if not await _need(q, c, "info"):
        return
    if len(p) < 3 or not p[2].isdigit() or p[1] not in ("p", "r", "x"):
        return await tg.answer(q)
    msg = await K2.ann_control(c.chat_id, int(p[2]), p[1], c.lang, c.tz)
    await tg.answer(q, re.sub(r"<[^>]+>", "", msg)[:190])


async def cb_faq(q, c: Cmd, p: list[str]) -> None:
    name = ":".join(p[1:])
    await tg.answer(q)
    if name == "__rules":
        body = await RT.db.val("SELECT body FROM rz_rules WHERE chat_id=?", (c.chat_id,))
        await c.say(body if body else c.t("قانونی ثبت نشده.", "No rules set."), reply=False)
        return
    row = await content.answer_get(c.chat_id, name)
    if row:
        await content.answer_send(c.chat_id, row, thread_id=c.thread_id)
    else:
        await tg.answer(q, c.t("پیدا نشد.", "Not found."), alert=True)


async def cb_watch(q, c: Cmd, p: list[str]) -> None:
    from pilot import locks
    if not await _need(q, c, "info"):
        return
    if len(p) < 2 or p[1] not in ("ok", "fix"):
        return await tg.answer(q)
    w = await RT.db.one("SELECT * FROM rz_watch WHERE chat_id=?", (c.chat_id,))
    cur = await locks.current(c.chat_id)
    if not cur.ok:
        return await tg.answer(q, c.t("❌ خطا.", "❌ Error."), alert=True)
    if p[1] == "ok":
        from pilot.db import jd
        await RT.db.run("INSERT INTO rz_watch(chat_id,last_perms,good_perms,last_check,drift) VALUES(?,?,?,?,NULL) ON CONFLICT(chat_id) "
                        "DO UPDATE SET good_perms=excluded.good_perms, last_perms=excluded.last_perms, drift=NULL",
                        (c.chat_id, jd(cur.value), jd(cur.value), now()))
        await tg.answer(q)
        await tg.edit(q.message, (getattr(q.message, "text_html", "") or "") + "\n\n" + c.t("👍 ثبت شد.", "👍 Noted."))
        return
    good = jl((w or {}).get("good_perms"))
    if not good:
        return await tg.answer(q, c.t("حالت قبلی ثبت نشده.", "No saved state."), alert=True)
    if await locks.layers(c.chat_id):
        return await tg.answer(q, c.t("قفل فعال است. /lockstatus", "A lock is active. /lockstatus"), alert=True)
    diff = [k for k in locks.KEYS if good.get(k) != cur.value.get(k)]
    if not diff:
        return await tg.answer(q, c.t("تغییری نیست.", "No changes."), alert=True)
    tok = await confirm.create(c.chat_id, q.from_user.id, "wd_fix", {"perms": {k: bool(good.get(k, cur.value.get(k))) for k in locks.KEYS}}, right="restrict")
    lines = [c.t("<b>برگرداندن دسترسی‌ها</b>", "<b>Restore permissions</b>")]
    lines += [f"• {esc(tools._perm_name(k, c.lang))}: {'✅' if cur.value.get(k) else '⛔️'} → {'✅' if good.get(k) else '⛔️'}" for k in diff]
    await tg.answer(q)
    await c.say("\n".join(lines), rows=confirm.buttons(tok, c.lang), reply=False)


async def cb_privacy(q, c: Cmd, p: list[str]) -> None:
    if not await _need(q, c, "info"):
        return
    if len(p) < 2 or p[1] not in ("e", "r"):
        return await tg.answer(q)
    await tg.answer(q)
    if p[1] == "e":
        await c.say(await K2.export_data_to(c), reply=False)
    else:
        await K2.reset_preview(c)


async def cb_myappeal(q, c: Cmd, p: list[str]) -> None:
    if len(p) < 3 or not p[1].isdigit() or not p[2].isdigit():
        return await tg.answer(q)
    chat_id, case_no = -int(p[1]), int(p[2])
    case = await RT.db.one("SELECT * FROM rz_cases WHERE chat_id=? AND case_no=?", (chat_id, case_no))
    if not case or case["target_id"] != q.from_user.id:
        return await tg.answer(q, c.t("مال شما نیست.", "Not yours."), alert=True)
    await desk.set_draft(q.from_user.id, {"kind": "appeal", "chat": chat_id, "case": case_no})
    await tg.answer(q)
    await tg.send(q.from_user.id, c.t(f"اعتراض به پروندهٔ #{case_no}: دلیلتان را در یک پیام بفرستید.",
                                      f"Appeal case #{case_no}: send your reason in one message."))


async def cb_lock(q, c: Cmd, p: list[str]) -> None:
    if not await _need(q, c, "restrict"):
        return
    await tg.answer(q)
    msg = await K.do_unlock(c, "all")
    await c.say(msg, reply=False)
    t2, rows = await ui.locks_page(c.chat_id, c.lang, c.tz)
    await tg.edit(q.message, t2, rows)


async def cb_legacy(q, c: Cmd, p: list[str]) -> None:
    if not await _need(q, c, "info"):
        return
    await RT.db.run("DELETE FROM rz_legacy_review WHERE chat_id=?", (c.chat_id,))
    await tg.answer(q, c.t("پاک شد.", "Cleared."))
    t2, rows = await ui.settings_main(c.chat_id, c.chat_title, c.lang)
    await tg.edit(q.message, t2, rows)


CALLBACKS = {"cf": cb_confirm, "cap": cb_captcha, "st": cb_settings, "hp": cb_help, "lg": cb_logs, "un": cb_undo,
             "ap": cb_appeal, "tk": cb_ticket, "jr": cb_join, "rm": cb_reminder, "ev": cb_event, "an": cb_announce,
             "fq": cb_faq, "wd": cb_watch, "pv": cb_privacy, "ma": cb_myappeal, "lk": cb_lock, "lr": cb_legacy}


# membership
async def on_my_member(update: Update) -> None:
    cmu = update.my_chat_member
    chat = cmu.chat
    old, new = cmu.old_chat_member.status, cmu.new_chat_member.status
    if chat.type == "private":
        await RT.db.run("UPDATE rz_users SET dm_ok=? WHERE user_id=?", (0 if new == "kicked" else 1, chat.id))
        return
    await RT.db.run("INSERT INTO rz_chats(chat_id,title,type,is_forum,added_at,onboarded,bot_status) VALUES(?,?,?,?,?,0,?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, type=excluded.type, is_forum=excluded.is_forum, "
                    "bot_status=excluded.bot_status", (chat.id, (chat.title or "")[:128], chat.type, int(bool(chat.is_forum)), now(), new))
    authz.invalidate(chat.id)
    _chat_known.pop(chat.id, None)
    if chat.type == "channel":
        return
    actor = cmu.from_user
    if actor:
        await targets.note_user(actor, chat.id)
    joined = old in ("left", "kicked") and new in ("member", "administrator")
    promoted = old == "member" and new == "administrator"
    if joined or promoted:
        # Quiet onboarding: nothing is posted to the group and no setting or permission changes.
        # Missing rights are explained only when someone uses a feature that needs them.
        await RT.db.run("UPDATE rz_chats SET onboarded=1, left_at=NULL WHERE chat_id=?", (chat.id,))
        await logs.owner(f"➕ {'در این گروه ادمین شدم' if promoted else 'به گروه اضافه شدم'}: {esc(chat.title)} [<code>{chat.id}</code>] "
                         f"· توسط <code>{actor.id if actor else '?'}</code>")
    elif new in ("left", "kicked"):
        await RT.db.run("UPDATE rz_chats SET left_at=? WHERE chat_id=?", (now(), chat.id))
        await logs.owner(f"➖ از گروه حذف شدم: {esc(chat.title)} [<code>{chat.id}</code>]")


async def on_chat_member(update: Update) -> None:
    cm = update.chat_member
    chat = cm.chat
    if chat.type not in ("group", "supergroup"):
        return
    u = cm.new_chat_member.user
    await targets.note_user(u, chat.id, status=cm.new_chat_member.status)
    authz.invalidate(chat.id, u.id)
    if "administrator" in (cm.old_chat_member.status, cm.new_chat_member.status) or "creator" in (cm.old_chat_member.status, cm.new_chat_member.status):
        authz.invalidate(chat.id)
    was, is_ = _is_member_status(cm.old_chat_member), _is_member_status(cm.new_chat_member)
    if u.id == RT.bot_id:
        return
    actor = cm.from_user
    by = actor.id if actor and actor.id != u.id else None
    via = "request" if getattr(cm, "via_join_request", False) else ("link" if cm.invite_link else ("added" if by else "self"))
    if not was and is_:
        await hooks.run(hooks.ON_MEMBER, chat, u, True, by, cm.invite_link, via)
        await ensure_chat(chat)
        await content.on_join(chat, u)
    elif was and not is_:
        await hooks.run(hooks.ON_MEMBER, chat, u, False, by, None, "kicked" if cm.new_chat_member.status == "kicked" else "left")
        if _first_leave(chat.id, u.id):
            await content.on_leave(chat, u)


async def on_channel_post(update: Update) -> None:
    p = update.channel_post
    text = (p.text or p.caption or "").strip()
    mm = re.search(r"\bRZ-[0-9A-Fa-f]{8}\b", text)
    if not mm:
        return
    await link_code(mm.group(0).upper(), p.chat.id, None, None, p.message_id)


async def link_code(code: str, dest_id: int, topic_id: int | None, sender_id: int | None, msg_id: int | None,
                    reply: Cmd | None = None) -> None:
    row = await RT.db.one("SELECT * FROM rz_link_codes WHERE code=? AND expires_at>=?", (code, now()))
    if not row:
        if reply:
            await reply.say(reply.t("❌ کد نامعتبر یا منقضی است.", "❌ Invalid or expired code."))
        return
    if sender_id is not None and sender_id != row["user_id"]:
        if reply:
            await reply.say(reply.t("❌ این کد مال شما نیست.", "❌ This code isn't yours."))
        return
    taken = await logs.take_code(code)
    if not taken:
        return
    lang, _ = await lang_tz(taken["chat_id"])
    ok, msg = await logs.verify_destination(taken["chat_id"], dest_id, topic_id, taken["user_id"], lang)
    if ok:
        await audit.record(taken["chat_id"], taken["user_id"], "admin", "log_setup", params={"dest": dest_id, "topic": topic_id, "via": "code"})
        if msg_id:
            await tg.delete(dest_id, msg_id)
    await tg.send(taken["chat_id"], msg)
    if reply and not ok:
        await reply.say(msg)


@K.command("linklog", admin=False, section="logs")
async def k_linklog(c: Cmd):
    code = (c.args[0] if c.args else "").strip().upper()
    if not re.fullmatch(r"RZ-[0-9A-F]{8}", code):
        return await c.say(c.t("<code>/linklog RZ-XXXXXXXX</code>", "<code>/linklog RZ-XXXXXXXX</code>"))
    if c.anon or not c.actor_id:
        return await c.say(c.t("❌ با حساب خودتان بفرستید، نه ناشناس.", "❌ Send it from your own account, not anonymously."))
    await link_code(code, c.chat_id, c.thread_id, c.actor_id, c.message_id, reply=c)
