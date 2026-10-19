"""Member tools: last seen, membership checks, name history, sanction lists, member counts, admin rights,
channel bans and per-user cleanup.

Telegram doesn't give bots the member list or arbitrary username lookups. Everything here is either a
direct API answer (getChatMember, getChatMemberCount, getChatAdministrators) or the bot's own observations,
and the reply says which."""
from __future__ import annotations

import time

from pilot import audit, authz, confirm, moderation as M, purgeword, tg
from pilot.commands import command, say
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.targets import Target, resolve
from pilot.util import esc, fmt_dt, norm_digits, num

STATUS_FA = {"creator": "سازنده", "administrator": "ادمین", "member": "عضو", "restricted": "محدود", "left": "خارج شده",
             "kicked": "بن شده"}


def _st(status: str | None, lang: str) -> str:
    if not status:
        return "نامشخص" if lang == "fa" else "unknown"
    return STATUS_FA.get(status, status) if lang == "fa" else status


async def _target(c: Cmd) -> Target | None:
    t = await resolve(c)
    if not t.ok or not t.user_id:
        await say(c, t.err or c.t("کاربر؟ ریپلای کنید یا آیدی عددی بدهید.", "Which user? Reply or give a numeric id."))
        return None
    return t


@command("seen", "lastseen", admin=False, section="members")
async def k_seen(c: Cmd):
    t = await _target(c)
    if t is None:
        return
    row = await RT.db.one("SELECT first_seen, last_seen FROM rz_user_chats WHERE chat_id=? AND user_id=?", (c.chat_id, t.user_id))
    if not row:
        return await say(c, c.t(f"{t.label()} را در این گروه ندیده‌ام.", f"I haven't seen {t.label()} in this group."))
    await say(c, c.t(f"👁 {t.label()}\nآخرین فعالیتی که دیدم: {fmt_dt(row['last_seen'], c.tz, 'fa', with_tz=False)}\n"
                     f"اولین بار: {fmt_dt(row['first_seen'], c.tz, 'fa', with_tz=False)}\n<i>فقط پیام‌ها و رویدادهایی که ربات دریافت کرده.</i>",
                     f"👁 {t.label()}\nLast activity I saw: {fmt_dt(row['last_seen'], c.tz, 'en', with_tz=False)}\n"
                     f"First seen: {fmt_dt(row['first_seen'], c.tz, 'en', with_tz=False)}\n<i>Only updates the bot received.</i>"))


@command("checkmember", "ismember", right=None, section="members")
async def k_checkmember(c: Cmd):
    t = await _target(c)
    if t is None:
        return
    out = await tg.call(RT.bot.get_chat_member, chat_id=c.chat_id, user_id=t.user_id)
    if not out.ok:
        return await say(c, c.t(f"❓ تلگرام جواب نداد ({esc(out.kind)}). وضعیت نامشخص است.", f"❓ Telegram didn't answer ({esc(out.kind)}). Status unknown."))
    m = out.value
    st = m.status
    extra = ""
    if st == "restricted":
        extra = c.t(" · عضو: " + ("بله" if getattr(m, "is_member", False) else "خیر"), " · member: " + ("yes" if getattr(m, "is_member", False) else "no"))
    until = getattr(m, "until_date", None)
    if until:
        extra += c.t(f" · تا {fmt_dt(until.timestamp(), c.tz, 'fa', with_tz=False)}", f" · until {fmt_dt(until.timestamp(), c.tz, 'en', with_tz=False)}")
    await say(c, c.t(f"🔎 {t.label()}: <b>{_st(st, 'fa')}</b>{extra}\n<i>از تلگرام، همین الان.</i>",
                     f"🔎 {t.label()}: <b>{_st(st, 'en')}</b>{extra}\n<i>From Telegram, just now.</i>"))


@command("namehistory", "names", right=None, section="members")
async def k_namehistory(c: Cmd):
    t = await _target(c)
    if t is None:
        return
    uns = await RT.db.all("SELECT username, seen_until FROM rz_username_history WHERE user_id=? ORDER BY seen_until DESC LIMIT 10", (t.user_id,))
    nms = await RT.db.all("SELECT name, seen_at FROM rz_name_history WHERE user_id=? ORDER BY seen_at DESC LIMIT 10", (t.user_id,))
    if not uns and not nms:
        return await say(c, c.t(f"تغییر نام یا یوزرنیمی از {t.label()} ندیده‌ام.", f"No name or username changes seen for {t.label()}."))
    lines = [f"• @{esc(r['username'])} ({fmt_dt(r['seen_until'], c.tz, c.lang, with_tz=False)})" for r in uns if r["username"]]
    lines += [f"• {esc(r['name'])} ({fmt_dt(r['seen_at'], c.tz, c.lang, with_tz=False)})" for r in nms]
    await say(c, c.t(f"🪪 <b>نام‌های قبلی</b> {t.label()}\n", f"🪪 <b>Previous names</b> {t.label()}\n") + "\n".join(lines) +
              c.t("\n<i>فقط تغییراتی که ربات دیده.</i>", "\n<i>Only changes the bot saw.</i>"))


async def _sanctioned(c: Cmd, actions: tuple[str, ...], undo_actions: tuple[str, ...]) -> list[dict]:
    """Latest bot-recorded sanction per user that wasn't undone, reversed or expired."""
    from pilot.db import jl
    q = ",".join("?" * len(actions + undo_actions))
    rows = await RT.db.all(f"SELECT target_id, target_name, action, ts, params, undone_at FROM rz_actions WHERE chat_id=? AND target_id>0 "
                           f"AND action IN ({q}) AND outcome='ok' ORDER BY id", (c.chat_id, *actions, *undo_actions))
    last: dict[int, dict] = {}
    for r in rows:
        d = (jl(r["params"], {}) or {}).get("duration")
        last[r["target_id"]] = {**r, "expires_at": (r["ts"] + d) if isinstance(d, (int, float)) and d else None}
    now = time.time()
    return [r for r in last.values() if r["action"] in actions and not r["undone_at"] and not (r["expires_at"] and r["expires_at"] < now)]


async def _list(c: Cmd, rows: list[dict], title_fa: str, title_en: str) -> None:
    if not rows:
        return await say(c, c.t("موردی ثبت نشده.", "Nothing recorded."))
    lines = []
    for r in rows[-40:]:
        until = c.t(f" تا {fmt_dt(r['expires_at'], c.tz, 'fa', with_tz=False)}", f" until {fmt_dt(r['expires_at'], c.tz, 'en', with_tz=False)}") if r["expires_at"] else ""
        lines.append(f"• <a href=\"tg://user?id={r['target_id']}\">{esc(r['target_name'] or r['target_id'])}</a> <code>{r['target_id']}</code>{until}")
    await say(c, f"<b>{c.t(title_fa, title_en)}</b> ({num(len(rows), c.lang)})\n" + "\n".join(lines) +
              c.t("\n<i>فقط کارهایی که با این ربات انجام شده؛ تلگرام لیست کامل بن‌ها را به ربات نمی‌دهد.</i>",
                  "\n<i>Only actions done through this bot; Telegram doesn't give bots the full ban list.</i>"))


@command("banlist", "bans", right="restrict", section="members")
async def k_banlist(c: Cmd):
    await _list(c, await _sanctioned(c, ("ban", "tban", "dban"), ("unban",)), "🚫 بن‌شده‌ها", "🚫 Banned")


@command("mutelist", "mutes", right="restrict", section="members")
async def k_mutelist(c: Cmd):
    await _list(c, await _sanctioned(c, ("mute", "tmute"), ("unmute",)), "🔇 ساکت‌شده‌ها", "🔇 Muted")


@command("membercount", "members", admin=False, section="members")
async def k_membercount(c: Cmd):
    out = await tg.call(RT.bot.get_chat_member_count, chat_id=c.chat_id)
    known = await RT.db.val("SELECT COUNT(*) FROM rz_user_chats WHERE chat_id=? AND COALESCE(status,'member') NOT IN ('left','kicked')",
                            (c.chat_id,), 0)
    active = await RT.db.val("SELECT COUNT(*) FROM rz_user_chats WHERE chat_id=? AND last_seen>?", (c.chat_id, time.time() - 7 * 86400), 0)
    total = num(out.value, c.lang) if out.ok else c.t("نامشخص", "unknown")
    await say(c, c.t(f"👥 اعضا (تلگرام): <b>{total}</b>\nاعضایی که ربات دیده: {num(known, 'fa')} · فعال در ۷ روز: {num(active, 'fa')}",
                     f"👥 Members (Telegram): <b>{total}</b>\nMembers the bot has seen: {known} · active in 7 days: {active}"))


@command("adminrights", "adminlist", right=None, section="members")
async def k_adminrights(c: Cmd):
    admins = await authz.admins(c.chat_id)
    if not admins:
        return await say(c, c.t("❌ لیست ادمین‌ها دریافت نشد.", "❌ Couldn't get the admin list."))
    lines = []
    for a in admins[:30]:
        if a.user.is_bot and a.user.id != RT.bot_id:
            continue
        if a.status == "creator":
            rights = c.t("همهٔ دسترسی‌ها", "all rights")
        else:
            rights = "، ".join(authz.right_label(r, c.lang) for r in authz.RIGHT_ATTR if authz.has_right(a, r)) or "-"
        anon = c.t(" · ناشناس", " · anonymous") if getattr(a, "is_anonymous", False) else ""
        title = f" «{esc(a.custom_title)}»" if getattr(a, "custom_title", None) else ""
        lines.append(f"• {esc(a.user.full_name)}{title} <code>{a.user.id}</code>{anon}\n  {rights}")
    await say(c, c.t("👮 <b>ادمین‌ها و دسترسی‌ها</b>\n", "👮 <b>Admins and rights</b>\n") + "\n".join(lines))


@command("banchannel", right="restrict", section="members")
async def k_banchannel(c: Cmd):
    if c.reply and c.reply.sender_chat_id:
        if c.reply.sender_chat_id == c.chat_id or c.reply.auto_fwd:
            return await say(c, c.t("این پیام ادمین ناشناس یا کانال متصل است؛ بن نمی‌شود.", "That's an anonymous admin or the linked channel; not banned."))
        t = Target(sender_chat_id=c.reply.sender_chat_id, sender_chat_type=c.reply.sender_chat_type, name=c.reply.sender_chat_title,
                   via="reply", verified=True)
    elif c.args and norm_digits(c.args[0]).lstrip("-").isdigit() and norm_digits(c.args[0]).startswith("-100"):
        cid = int(norm_digits(c.args[0]))
        if cid == c.chat_id:
            return await say(c, c.t("این خود گروه است.", "That's this group."))
        t = Target(sender_chat_id=cid, name=str(cid), via="id", verified=False)
    else:
        return await say(c, c.t("روی پیام کانال ریپلای کنید یا آیدی <code>-100…</code> بدهید.", "Reply to the channel's message or give its <code>-100…</code> id."))
    await say(c, (await M.ban_channel(c, t)).text)


@command("unbanchannel", right="restrict", section="members")
async def k_unbanchannel(c: Cmd):
    cid = c.reply.sender_chat_id if (c.reply and c.reply.sender_chat_id) else None
    if cid is None and c.args and norm_digits(c.args[0]).startswith("-100"):
        cid = int(norm_digits(c.args[0]))
    if cid is None:
        return await say(c, c.t("آیدی کانال؟ <code>/unbanchannel -100…</code>", "Channel id? <code>/unbanchannel -100…</code>"))
    out = await tg.call(RT.bot.unban_chat_sender_chat, chat_id=c.chat_id, sender_chat_id=cid)
    if out.ok:
        await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "unban_channel", target_id=cid, log_line=f"#UNBAN_CHANNEL <code>{cid}</code>\n{M.who(c)}")
    await say(c, c.t(f"✅ کانال <code>{cid}</code> آزاد شد.", f"✅ Channel <code>{cid}</code> unbanned.") if out.ok else M.fail_text(c, out, "restrict"))


# delete a member's recent messages (from the 48h index; message text is never stored)
@command("purgeuser", "delall", right="delete", section="members")
async def k_purgeuser(c: Cmd):
    t = await _target(c)
    if t is None:
        return
    if not await purgeword.enabled(c.chat_id):
        return await say(c, c.t("نمایهٔ ۴۸ ساعته خاموش است: <code>/purgeword on</code>", "The 48h index is off: <code>/purgeword on</code>"))
    n = await RT.db.val("SELECT COUNT(*) FROM rz_msg_index WHERE chat_id=? AND sender_id=? AND ts>?", (c.chat_id, t.user_id, time.time() - purgeword.WINDOW), 0)
    if not n:
        return await say(c, c.t(f"از {t.label()} پیامی در ۴۸ ساعت اخیر ثبت نشده.", f"No messages from {t.label()} in the last 48h."))
    tok = await confirm.create(c.chat_id, c.actor_id, "purgeuser", {"cmd": c.to_payload(), "uid": t.user_id, "label": t.label()}, right="delete")
    await say(c, c.t(f"⚠️ {num(n, 'fa')} پیام ۴۸ ساعت اخیر {t.label()} پاک شود؟", f"⚠️ Delete {n} messages from {t.label()} (last 48h)?"),
              confirm.buttons(tok, c.lang))


@confirm.handler("purgeuser")
async def _purgeuser_ok(p, presser, chat_id, message):
    c = Cmd.from_payload(p["cmd"])
    c.actor_id = presser
    b = await M.bot_can(c, "delete")
    if b:
        return b
    ids = [r["message_id"] for r in await RT.db.all("SELECT message_id FROM rz_msg_index WHERE chat_id=? AND sender_id=? AND ts>? LIMIT 1000",
                                                     (chat_id, p["uid"], time.time() - purgeword.WINDOW))]
    done, failed = [], 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        out = await tg.call(RT.bot.delete_messages, chat_id=chat_id, message_ids=chunk)
        if out.ok:
            done += chunk
        else:
            failed += len(chunk)
    await purgeword.forget_ids(chat_id, done)
    await audit.record(chat_id, presser, "admin", "purge", target_id=p["uid"], params={"n": len(done), "failed": failed, "by_user": True},
                       outcome="ok" if not failed else ("partial" if done else "failed"), log_line=f"#PURGE_USER {p['label']} · {len(done)}")
    txt = c.t(f"🧹 {num(len(done), 'fa')} پیام پاک شد.", f"🧹 Deleted {len(done)} messages.")
    if failed:
        txt += c.t(f" {num(failed, 'fa')} پیام پاک نشد.", f" {failed} couldn't be deleted.")
    return txt
