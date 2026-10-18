"""Commands, part 1: moderation, locks, filters, welcome, rules, answers, logs, history."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from pilot import audit, authz, confirm, content, locks, logs, moderation as M, settings as S, tg
from pilot.ctx import Cmd
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.targets import Target, resolve
from pilot.util import esc, fmt_dt, fmt_duration, looks_like_duration, norm_digits, now, parse_duration, parse_hhmm


@dataclass
class Spec:
    fn: Callable[[Cmd], Awaitable[None]]
    right: str | None = None
    scope: str = "group"       # group | private | any
    admin: bool = True
    section: str = "misc"
    names: list[str] = field(default_factory=list)


REG: dict[str, Spec] = {}


def command(*names: str, right: str | None = None, scope: str = "group", admin: bool = True, section: str = "misc"):
    def deco(fn):
        sp = Spec(fn, right, scope, admin, section, list(names))
        for n in names:
            REG[n] = sp
        return fn
    return deco


async def say(c: Cmd, text: str, rows=None) -> None:
    if text:
        await c.say(text, rows=rows)


def rest_after(c: Cmd, n: int) -> str:
    parts = c.rest.split(None, n)
    return parts[n] if len(parts) > n else ""


async def target_and_rest(c: Cmd) -> tuple[Target, list[str]]:
    t = await resolve(c)
    return t, c.args[t.consumed:] if t.ok else []


def split_dur(words: list[str]) -> tuple[int | None, str]:
    if words and looks_like_duration(words[0]):
        return parse_duration(words[0]), " ".join(words[1:])
    if len(words) >= 2 and looks_like_duration(words[0] + " " + words[1]) and not looks_like_duration(words[0]):
        return parse_duration(words[0] + " " + words[1]), " ".join(words[2:])
    return None, " ".join(words)


# moderation
async def _mod(c: Cmd, kind: str, need_duration: bool = False) -> str:
    t, rest = await target_and_rest(c)
    if not t.ok:
        return t.err
    dur, reason = split_dur(rest) if kind in ("ban", "mute", "tban", "tmute") else (None, " ".join(rest))
    if need_duration and not dur:
        return c.t("مدت؟ مثلاً <code>2h</code> یا <code>30m</code>", "Duration? e.g. <code>2h</code> or <code>30m</code>")
    if kind in ("ban", "tban", "dban") and t.via == "username" and not t.verified and c.source != "confirmed":
        tok = await confirm.create(c.chat_id, c.actor_id, "mod_confirm", {"cmd": c.to_payload(), "kind": kind}, right="restrict")
        await c.say(c.t(f"⚠️ {t.label()} عضو گروه نیست. بن شود؟", f"⚠️ {t.label()} is not in the group. Ban?"),
                    rows=confirm.buttons(tok, c.lang))
        return ""
    if kind in ("ban", "tban"):
        r = await M.ban(c, t, dur, reason)
    elif kind == "dban":
        r = await M.ban(c, t, None, reason, delete_reply=True)
    elif kind in ("mute", "tmute"):
        r = await M.mute(c, t, dur, reason)
    elif kind == "unban":
        r = await M.unban(c, t)
    elif kind == "unmute":
        r = await M.unmute(c, t)
    elif kind == "kick":
        r = await M.kick(c, t, reason)
    else:
        r = await M.warn(c, t, reason)
    return r.text


@confirm.handler("mod_confirm")
async def _mod_confirmed(p: dict, presser: int, chat_id: int, message) -> str:
    c = Cmd.from_payload(p["cmd"])
    c.actor_id, c.anon, c.source = presser, False, "confirmed"
    return await _mod(c, p["kind"])


@command("ban", right="restrict", section="mod")
async def k_ban(c): await say(c, await _mod(c, "ban"))
@command("tban", "tempban", right="restrict", section="mod")
async def k_tban(c): await say(c, await _mod(c, "tban", True))
@command("dban", right="restrict", section="mod")
async def k_dban(c): await say(c, await _mod(c, "dban"))
@command("unban", right="restrict", section="mod")
async def k_unban(c): await say(c, await _mod(c, "unban"))
@command("mute", right="restrict", section="mod")
async def k_mute(c): await say(c, await _mod(c, "mute"))
@command("tmute", "tempmute", right="restrict", section="mod")
async def k_tmute(c): await say(c, await _mod(c, "tmute", True))
@command("unmute", right="restrict", section="mod")
async def k_unmute(c): await say(c, await _mod(c, "unmute"))
@command("kick", right="restrict", section="mod")
async def k_kick(c): await say(c, await _mod(c, "kick"))
@command("warn", right="restrict", section="mod")
async def k_warn(c): await say(c, await _mod(c, "warn"))


@command("warns", admin=False, section="mod")
async def k_warns(c):
    t = await resolve(c) if (c.args or c.reply) else Target(user_id=c.actor_id, name=c.actor_name, via="self")
    await say(c, t.err if not t.ok else await M.warns_text(c, t))


@command("rmwarn", "unwarn", right="restrict", section="mod")
async def k_rmwarn(c):
    t = await resolve(c)
    await say(c, t.err if not t.ok else (await M.rm_warn(c, t)).text)


@command("resetwarns", right="restrict", section="mod")
async def k_resetwarns(c):
    t = await resolve(c)
    await say(c, t.err if not t.ok else (await M.reset_warns(c, t)).text)


@command("kickme", admin=False, section="mod")
async def k_kickme(c):
    if c.anon or not c.actor_id:
        return await say(c, c.t("ادمین ناشناس نمی‌تواند.", "Not for anonymous admins."))
    m = await authz.member(c.chat_id, c.actor_id, fresh=True)
    if authz.is_adminish(m):
        return await say(c, c.t("ادمین‌ها نمی‌توانند.", "Not for admins."))
    t = Target(user_id=c.actor_id, name=c.actor_name, via="self")
    sub = Cmd(**{**c.to_payload(), "reply": None})
    sub.actor_id = RT.bot_id
    r = await M.kick(sub, t, c.t("درخواست خود کاربر", "self-requested"))
    await say(c, r.text)


@command("del", right="delete", section="mod")
async def k_del(c):
    if not c.reply:
        return await say(c, c.t("روی پیام ریپلای کنید.", "Reply to a message."))
    out = await tg.delete(c.chat_id, c.reply.message_id)
    if out.ok:
        await tg.delete(c.chat_id, c.message_id)
        from pilot.purgeword import forget_ids
        await forget_ids(c.chat_id, [c.reply.message_id])
        await audit.record(c.chat_id, c.actor_id, "admin", "purge", params={"n": 1})
    else:
        await say(c, M.fail_text(c, out, "delete"))


@command("purge", right="delete", section="mod")
async def k_purge(c):
    if not c.reply:
        return await say(c, c.t("روی اولین پیام ریپلای کنید.", "Reply to the first message."))
    n = c.message_id - c.reply.message_id + 1
    if n > 30 and c.source != "confirmed":
        tok = await confirm.create(c.chat_id, c.actor_id, "purge", {"from": c.reply.message_id, "to": c.message_id, "cmd": c.to_payload()}, right="delete")
        return await say(c, c.t(f"⚠️ {n} پیام حذف شود؟", f"⚠️ Delete {n} messages?"), confirm.buttons(tok, c.lang))
    await say(c, (await M.purge(c, c.reply.message_id, c.message_id)).text)


@confirm.handler("purge")
async def _purge_ok(p, presser, chat_id, message):
    c = Cmd.from_payload(p["cmd"])
    c.actor_id = presser
    return (await M.purge(c, p["from"], p["to"])).text


@command("pin", right="pin", section="mod")
async def k_pin(c):
    if not c.reply:
        return await say(c, c.t("روی پیام ریپلای کنید.", "Reply to a message."))
    loud = bool(c.args and c.args[0].lower() in ("loud", "notify", "اعلان"))
    out = await tg.call(RT.bot.pin_chat_message, chat_id=c.chat_id, message_id=c.reply.message_id, disable_notification=not loud)
    if out.ok:
        await audit.record(c.chat_id, c.actor_id, "admin", "pin", params={"message_id": c.reply.message_id})
    await say(c, c.t("📌 سنجاق شد.", "📌 Pinned.") if out.ok else M.fail_text(c, out, "pin"))


@command("unpin", right="pin", section="mod")
async def k_unpin(c):
    kw = {"message_id": c.reply.message_id} if c.reply else {}
    out = await tg.call(RT.bot.unpin_chat_message, chat_id=c.chat_id, **kw)
    await say(c, c.t("✅ برداشته شد.", "✅ Unpinned.") if out.ok else M.fail_text(c, out, "pin"))


@command("unpinall", right="pin", section="mod")
async def k_unpinall(c):
    tok = await confirm.create(c.chat_id, c.actor_id, "unpinall", {}, right="pin")
    await say(c, c.t("⚠️ همهٔ سنجاق‌ها برداشته شود؟", "⚠️ Unpin all?"), confirm.buttons(tok, c.lang))


@confirm.handler("unpinall")
async def _unpinall(p, presser, chat_id, message):
    out = await tg.call(RT.bot.unpin_all_chat_messages, chat_id=chat_id)
    return "✅" if out.ok else "❌ " + esc(out.desc)


@command("promote", right="promote", section="mod")
async def k_promote(c):
    t = await resolve(c)
    if not t.ok:
        return await say(c, t.err)
    out = await tg.call(RT.bot.promote_chat_member, chat_id=c.chat_id, user_id=t.user_id, can_delete_messages=True,
                        can_restrict_members=True, can_pin_messages=True, can_invite_users=True, can_manage_chat=True)
    if out.ok:
        authz.invalidate(c.chat_id, t.user_id)
        await audit.record(c.chat_id, c.actor_id, "admin", "promote", target_id=t.user_id, target_name=t.name,
                           log_line=f"#PROMOTE {t.label()} · {M.who(c)}")
    await say(c, c.t(f"✅ {t.label()} ادمین شد.", f"✅ {t.label()} is now admin.")
                 if out.ok else M.fail_text(c, out, "promote"))


@command("demote", right="promote", section="mod")
async def k_demote(c):
    t = await resolve(c)
    if not t.ok:
        return await say(c, t.err)
    out = await tg.call(RT.bot.promote_chat_member, chat_id=c.chat_id, user_id=t.user_id, can_manage_chat=False, can_delete_messages=False,
                        can_restrict_members=False, can_pin_messages=False, can_invite_users=False, can_change_info=False,
                        can_promote_members=False, can_manage_video_chats=False, can_manage_topics=False)
    if out.ok:
        authz.invalidate(c.chat_id, t.user_id)
        await audit.record(c.chat_id, c.actor_id, "admin", "demote", target_id=t.user_id, target_name=t.name,
                           log_line=f"#DEMOTE {t.label()} · {M.who(c)}")
    await say(c, c.t(f"✅ {t.label()} دیگر ادمین نیست.", f"✅ {t.label()} is no longer admin.") if out.ok else
              M.fail_text(c, out, "promote"))


@command("admins", admin=False, section="mod")
async def k_admins(c):
    ads = await authz.admins(c.chat_id)
    lines = [c.t("<b>ادمین‌ها</b>", "<b>Admins</b>")]
    for m in ads:
        if m.user.is_bot or getattr(m, "is_anonymous", False):
            continue
        crown = "👑 " if m.status == "creator" else "• "
        lines.append(crown + esc(m.user.full_name))
    await say(c, "\n".join(lines))


@command("report", admin=False, section="desk")
async def k_report(c):
    if not await S.get(c.chat_id, "report_on"):
        return
    if not c.reply:
        return await say(c, c.t("روی پیام ریپلای کنید.", "Reply to a message."))
    key = f"report:{c.chat_id}:{c.reply.message_id}"
    _, n = await RT.db.run("INSERT OR IGNORE INTO rz_seen VALUES(?,?)", (key, now()))
    if not n:
        return await say(c, c.t("قبلاً گزارش شده.", "Already reported."))
    link = f"https://t.me/c/{str(c.chat_id)[4:]}/{c.reply.message_id}" if str(c.chat_id).startswith("-100") else ""
    txt = (f"#REPORT {esc(c.actor_name)} [<code>{c.actor_id}</code>] → {esc(c.reply.user_name or c.reply.sender_chat_title)} "
           f"[<code>{c.reply.user_id or c.reply.sender_chat_id}</code>]" + (f"\n{link}" if link else "") +
           (f"\n{esc(' '.join(c.args)[:200])}" if c.args else ""))
    sent = await logs.emit(c.chat_id, txt)
    if not sent:
        ads = [m for m in await authz.admins(c.chat_id) if not m.user.is_bot and not getattr(m, "is_anonymous", False)][:5]
        tags = "".join(f'<a href="tg://user?id={m.user.id}">⁣</a>' for m in ads)
        return await say(c, c.t("گزارش شد.", "Reported.") + tags)
    await say(c, c.t("گزارش شد.", "Reported."))


# locks / night / slow mode
async def do_lock(c: Cmd, typ: str, duration: int | None) -> str:
    b = await M.bot_can(c, "restrict")
    if b:
        return b
    keys, fa, en = locks.LOCK_TYPES[typ]
    layer = f"manual:{typ}"
    res = await locks.add_layer(c.chat_id, layer, keys, c.actor_id, "manual", expires_at=(now() + duration) if duration else None)
    if not res.ok:
        return M.fail_text(c, res, "restrict")
    lay = await RT.db.one("SELECT created_at FROM rz_lock_layers WHERE chat_id=? AND layer=?", (c.chat_id, layer))
    await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "lock", params={"type": typ, "duration": duration},
                       undo={"op": "remove_layer", "layer": layer, "created_at": (lay or {}).get("created_at", 0)},
                       log_line=f"#LOCK {en}" + (f" {fmt_duration(duration, 'en')}" if duration else "") + f" · {M.who(c)}")
    d = M._dur_txt(c, duration)
    return c.t(f"🔒 {fa} قفل شد{d}.", f"🔒 {en} locked{d}.")


async def do_unlock(c: Cmd, typ: str) -> str:
    lays = await locks.layers(c.chat_id)
    if typ == "all":
        targets = [la["layer"] for la in lays if la["layer"].startswith("manual:")]
    elif typ in ("raid", "night"):
        targets = [typ] if any(la["layer"] == typ for la in lays) else []
    else:
        targets = [f"manual:{typ}"] if any(la["layer"] == f"manual:{typ}" for la in lays) else []
    if not targets:
        return c.t("قفل نیست.", "Not locked.")
    for la in targets:
        await RT.db.run("DELETE FROM rz_lock_layers WHERE chat_id=? AND layer=?", (c.chat_id, la))
    if typ == "night":
        await RT.db.run("UPDATE rz_night SET last_state=0 WHERE chat_id=?", (c.chat_id,))
    res = await locks.reconcile(c.chat_id)
    if not res.ok:
        return M.fail_text(c, res, "restrict")
    await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "unlock", params={"layers": targets},
                       log_line=f"#UNLOCK {', '.join(targets)} · {M.who(c)}")
    remaining = await locks.layers(c.chat_id)
    msg = c.t("🔓 باز شد.", "🔓 Unlocked.")
    if remaining:
        msg += "\n" + c.t("هنوز قفل: ", "Still locked: ") + "، ".join(locks.describe_layer(la, c.lang, c.tz) for la in remaining)
    return msg


@command("lock", right="restrict", section="locks")
async def k_lock(c):
    typ = locks.lock_type(c.args[0] if c.args else "all")
    if not typ:
        return await say(c, c.t("نوع نامعتبر: ", "Unknown type: ") + ", ".join(locks.LOCK_TYPES))
    dur = parse_duration(c.args[1]) if len(c.args) > 1 else None
    await say(c, await do_lock(c, typ, dur))


@command("lockdown", right="restrict", section="locks")
async def k_lockdown(c):
    if c.args and c.args[0].lower() in ("off", "خاموش"):
        return await say(c, await do_unlock(c, "all"))
    dur = parse_duration(c.args[0]) if c.args else None
    await say(c, await do_lock(c, "all", dur))


@command("unlock", right="restrict", section="locks")
async def k_unlock(c):
    w = (c.args[0].lower() if c.args else "all")
    typ = w if w in ("raid", "night") else locks.lock_type(w)
    if not typ:
        return await say(c, c.t("نوع نامعتبر.", "Unknown type."))
    await say(c, await do_unlock(c, typ))


@command("lockstatus", "locks", admin=False, section="locks")
async def k_lockstatus(c):
    lays = await locks.layers(c.chat_id)
    night = await RT.db.one("SELECT * FROM rz_night WHERE chat_id=? AND enabled=1", (c.chat_id,))
    lines = ["• " + locks.describe_layer(la, c.lang, c.tz) for la in lays] or [c.t("قفلی فعال نیست.", "No locks.")]
    if night:
        lines.append(c.t(f"🌙 شبانه: {night['start']} تا {night['end']}", f"🌙 Night: {night['start']}-{night['end']}"))
    st = await RT.db.one("SELECT overrides FROM rz_perm_state WHERE chat_id=?", (c.chat_id,))
    if st and jl(st["overrides"], []):
        lines.append(c.t("دستی باز شده: ", "Opened manually: ") + ", ".join(jl(st["overrides"], [])))
    await say(c, "\n".join(lines))


@command("night", right="restrict", section="locks")
async def k_night(c):
    from pilot import planner
    a = [norm_digits(x) for x in c.args]
    if not a or a[0].lower() in ("status", "وضعیت"):
        n = await RT.db.one("SELECT * FROM rz_night WHERE chat_id=? AND enabled=1", (c.chat_id,))
        return await say(c, (c.t(f"🌙 شبانه: {n['start']} تا {n['end']}", f"🌙 Night: {n['start']}-{n['end']}") if n else
                             c.t("🌙 خاموش · <code>/night 01:00 08:00</code>", "🌙 Off · <code>/night 01:00 08:00</code>")))
    if a[0].lower() in ("off", "خاموش"):
        ok = await planner.clear_night(c.chat_id)
        await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "night", params={"state": "removed"})
        return await say(c, c.t("✅ قفل شبانه خاموش شد.", "✅ Night lock off.") if ok else
                       c.t("فعال نبود.", "Not active."))
    if len(a) < 2 or not parse_hhmm(a[0]) or not parse_hhmm(a[1]):
        return await say(c, c.t("<code>/night 01:00 08:00</code> · <code>/night off</code>", "<code>/night 01:00 08:00</code> · <code>/night off</code>"))
    s, e = parse_hhmm(a[0]), parse_hhmm(a[1])
    if s == e:
        return await say(c, c.t("شروع و پایان یکی است.", "Start equals end."))
    tok = await confirm.create(c.chat_id, c.actor_id, "night", {"start": f"{s[0]:02d}:{s[1]:02d}", "end": f"{e[0]:02d}:{e[1]:02d}",
                                                                 "cmd": c.to_payload()}, right="restrict")
    await say(c, c.t(f"🌙 گروه هر شب از {s[0]:02d}:{s[1]:02d} تا {e[0]:02d}:{e[1]:02d} بسته شود؟",
                     f"🌙 Close the group every night {s[0]:02d}:{s[1]:02d}-{e[0]:02d}:{e[1]:02d}?"), confirm.buttons(tok, c.lang))


@confirm.handler("night")
async def _night_ok(p, presser, chat_id, message):
    from pilot import planner
    c = Cmd.from_payload(p["cmd"])
    if not await authz.bot_has(chat_id, "restrict", fresh=True):
        return c.t("❌ ربات دسترسی بن کاربران ندارد.", "❌ The bot lacks the Ban users right.")
    await planner.set_night(chat_id, p["start"], p["end"], presser)
    await audit.record(chat_id, presser, "admin", "night", params={"start": p["start"], "end": p["end"]},
                       log_line=f"#NIGHT {p['start']}-{p['end']} · <code>{presser}</code>")
    active = await RT.db.one("SELECT 1 FROM rz_lock_layers WHERE chat_id=? AND layer='night'", (chat_id,))
    return c.t(f"✅ قفل شبانه: {p['start']} تا {p['end']}" + (" · الان قفل است" if active else ""),
               f"✅ Night lock: {p['start']}-{p['end']}" + (" · locked now" if active else ""))


SLOW_INFO = ("🐢 اسلومود: {cur}\nاز تنظیمات خود تلگرام عوضش کن.",
             "🐢 Slow mode: {cur}\nChange it in Telegram group settings.")


async def slowmode_text(c: Cmd) -> str:
    out = await tg.call(RT.bot.get_chat, chat_id=c.chat_id)
    sm = getattr(out.value, "slow_mode_delay", None) if out.ok else None
    cur = fmt_duration(sm, c.lang) if sm else c.t("خاموش", "off")
    return c.t(SLOW_INFO[0], SLOW_INFO[1]).format(cur=cur)


@command("slowmode", admin=False, section="locks")
async def k_slowmode(c): await say(c, await slowmode_text(c))


# filters
async def set_setting(c: Cmd, key: str, value, quiet: bool = False) -> str:
    try:
        old, new = await S.set_value(c.chat_id, key, value, c.actor_id or 0)
    except ValueError as e:
        return c.t("❌ نامعتبر: ", "❌ Invalid: ") + esc(str(e))
    if old != new:
        await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "setting", params={"key": key, "old": old, "new": new},
                           undo={"op": "setting", "key": key, "old": old, "new": new},
                           log_line=f"#SETTING {key}: {esc(str(old)[:40])} → {esc(str(new)[:40])} · {M.who(c)}")
    lbl = S.label(key, c.lang)
    txt = c.t(f"✅ {lbl}: {S.fmt_value(key, new, 'fa')}", f"✅ {lbl}: {S.fmt_value(key, new, 'en')}")
    if S.SPECS[key].enforce and new is True:
        need = "delete" if key not in ("captcha_on", "raid_on") else "restrict"
        if not await authz.bot_has(c.chat_id, need, fresh=True):
            txt += c.t(f"\n⚠️ ربات دسترسی «{authz.right_label(need, 'fa')}» ندارد.",
                       f"\n⚠️ The bot lacks the '{authz.right_label(need, 'en')}' right.")
        if await S.get(c.chat_id, "paused"):
            txt += c.t("\n⏸ فیلترها متوقف‌اند (/resume)", "\n⏸ Filters paused (/resume)")
    return txt


def onoff(c: Cmd) -> bool | None:
    if not c.args:
        return None
    w = c.args[0].lower()
    if w in ("on", "yes", "1", "روشن", "فعال"):
        return True
    if w in ("off", "no", "0", "خاموش", "غیرفعال"):
        return False
    return None


async def toggle_cmd(c: Cmd, key: str, extra: Callable[[Cmd], Awaitable[str]] | None = None) -> None:
    v = onoff(c)
    if v is None:
        cur = await S.load(c.chat_id)
        val, src = cur[key]
        return await say(c, c.t(f"{S.label(key, 'fa')}: {S.fmt_value(key, val, 'fa')} · <code>/{c.name} on|off</code>",
                                f"{S.label(key, 'en')}: {S.fmt_value(key, val, 'en')} · <code>/{c.name} on|off</code>"))
    txt = await set_setting(c, key, v)
    if extra and v:
        txt += await extra(c)
    await say(c, txt)


@command("antichannel", right="info", section="filters")
async def k_antichannel(c):
    async def extra(c2):
        if len(c2.args) > 1 and c2.args[1].lower() in ("ban", "delete"):
            return "\n" + await set_setting(c2, "f_channels_action", c2.args[1].lower())
        return ""
    await toggle_cmd(c, "f_channels", extra)


@command("antilink", right="info", section="filters")
async def k_antilink(c): await toggle_cmd(c, "f_links")
@command("antiforward", right="info", section="filters")
async def k_antiforward(c): await toggle_cmd(c, "f_forwards")
@command("captcha", right="restrict", section="filters")
async def k_captcha(c): await toggle_cmd(c, "captcha_on")
@command("antiraid", right="restrict", section="filters")
async def k_antiraid(c): await toggle_cmd(c, "raid_on")
@command("cleanservice", right="delete", section="welcome")
async def k_cleanservice(c): await toggle_cmd(c, "clean_service")
@command("cleanwelcome", right="info", section="welcome")
async def k_cleanwelcome(c): await toggle_cmd(c, "welcome_clean")


@command("cooldown", right="info", section="locks")
async def k_cooldown(c):
    async def extra(c2):
        if len(c2.args) > 1:
            return "\n" + await set_setting(c2, "cooldown_s", c2.args[1])
        return ""
    await toggle_cmd(c, "cooldown_on", extra)


@command("flood", "antiflood", right="restrict", section="filters")
async def k_flood(c):
    async def extra(c2):
        out = ""
        if len(c2.args) > 1:
            out += "\n" + await set_setting(c2, "flood_n", c2.args[1])
        if len(c2.args) > 2:
            out += "\n" + await set_setting(c2, "flood_action", c2.args[2])
        return out
    await toggle_cmd(c, "flood_on", extra)


MEDIA_KEYS = {"photos": "f_photos", "photo": "f_photos", "عکس": "f_photos", "videos": "f_videos", "video": "f_videos", "ویدیو": "f_videos",
              "documents": "f_documents", "files": "f_documents", "فایل": "f_documents", "stickers": "f_stickers", "استیکر": "f_stickers",
              "gifs": "f_gifs", "gif": "f_gifs", "گیف": "f_gifs", "voice": "f_voice", "ویس": "f_voice", "audio": "f_audio", "music": "f_audio",
              "موسیقی": "f_audio", "links": "f_links", "لینک": "f_links", "forwards": "f_forwards", "فوروارد": "f_forwards",
              "channels": "f_channels", "کانال": "f_channels", "words": "f_words"}


@command("block", "filteron", right="info", section="filters")
async def k_block(c):
    key = MEDIA_KEYS.get(c.args[0].lower()) if c.args else None
    if not key:
        return await say(c, "<code>/block photos|videos|documents|stickers|gifs|voice|audio|links|forwards|channels|words</code>")
    await say(c, await set_setting(c, key, True))


@command("unblock", "filteroff", right="info", section="filters")
async def k_unblock(c):
    key = MEDIA_KEYS.get(c.args[0].lower()) if c.args else None
    if not key:
        return await say(c, "<code>/unblock photos</code>")
    await say(c, await set_setting(c, key, False))


@command("blocklist", admin=False, section="filters")
async def k_blocklist(c):
    rows = await RT.db.all("SELECT word FROM rz_blockwords WHERE chat_id=? ORDER BY word", (c.chat_id,))
    on = await S.get(c.chat_id, "f_words")
    head = c.t(f"کلمات ممنوع ({'روشن' if on else 'خاموش'}):", f"Blocked words ({'on' if on else 'off'}):")
    await say(c, head + "\n" + ("، ".join(esc(r["word"]) for r in rows) or "-"))


@command("addblock", "addblocklist", right="info", section="filters")
async def k_addblock(c):
    words = [w.strip().lower() for w in re.split(r"[,،\n]", c.rest) if w.strip()][:50]
    if not words:
        return await say(c, c.t("<code>/addblock کلمه۱, کلمه۲</code>", "<code>/addblock word1, word2</code>"))
    for w in words:
        await RT.db.run("INSERT OR IGNORE INTO rz_blockwords VALUES(?,?)", (c.chat_id, w[:60]))
    on = await S.get(c.chat_id, "f_words")
    await say(c, c.t(f"✅ {len(words)} کلمه اضافه شد.", f"✅ Added {len(words)}.") +
              ("" if on else c.t(" (فیلتر خاموش است: <code>/block words</code>)", " (filter off: <code>/block words</code>)")))


@command("rmblock", "rmblocklist", right="info", section="filters")
async def k_rmblock(c):
    words = [w.strip().lower() for w in re.split(r"[,،\n]", c.rest) if w.strip()]
    n = 0
    for w in words:
        _, k = await RT.db.run("DELETE FROM rz_blockwords WHERE chat_id=? AND word=?", (c.chat_id, w))
        n += k
    await say(c, c.t(f"✅ {n} کلمه حذف شد.", f"✅ Removed {n}."))


@command("pause", right="info", section="filters")
async def k_pause(c):
    await set_setting(c, "paused", True)
    await say(c, c.t("⏸ فیلترها متوقف شدند. /resume", "⏸ Filters paused. /resume"))


@command("resume", right="info", section="filters")
async def k_resume(c): await say(c, await set_setting(c, "paused", False))


@command("safereset", right="info", section="filters")
async def k_safereset(c):
    on = await S.enabled_filters(c.chat_id)
    if not on:
        return await say(c, c.t("هیچ فیلتری روشن نیست.", "No filters are on."))
    tok = await confirm.create(c.chat_id, c.actor_id, "safereset", {"keys": [k for k, _ in on], "cmd": c.to_payload()}, right="info")
    lines = [c.t("این فیلترها خاموش شوند؟", "Turn these filters off?")]
    lines += [f"• {esc(S.label(k, c.lang))}" for k, src in on]
    await say(c, "\n".join(lines), confirm.buttons(tok, c.lang))


@confirm.handler("safereset")
async def _safereset(p, presser, chat_id, message):
    c = Cmd.from_payload(p["cmd"])
    c.actor_id = presser
    for k in p["keys"]:
        await S.set_value(chat_id, k, False, presser)
    await audit.record(chat_id, presser, M.actor_kind(c), "reset_filters", params={"keys": p["keys"]},
                       log_line=f"#SAFE_RESET {', '.join(p['keys'])} · <code>{presser}</code>")
    return c.t(f"✅ {len(p['keys'])} فیلتر خاموش شد.", f"✅ {len(p['keys'])} filter(s) off.")


@command("why", right=None, section="filters")
async def k_why(c):
    from pilot.tools import why_text
    await say(c, await why_text(c.chat_id, c.lang, c.tz))


# welcome / goodbye
async def save_greeting(c: Cmd, kind: str, text_html: str, enable: bool | None) -> str:
    media = None
    if c.reply and c.reply.media_type in ("photo", "animation", "video", "document") and kind == "welcome":
        media = f"{c.reply.media_type}:{c.reply.file_id}"
        text_html = text_html or c.reply.text_html
    if not text_html and c.reply:
        text_html = c.reply.text_html
    if not text_html and not media:
        return c.t(f"<code>/set{kind} متن</code> یا ریپلای روی پیام", f"<code>/set{kind} text</code> or reply to a message")
    await set_setting(c, f"{kind}_text", text_html or "")
    if kind == "welcome":
        await S.set_value(c.chat_id, "welcome_media", media, c.actor_id or 0)
    msg = c.t("✅ ذخیره شد.", "✅ Saved.")
    if enable:
        await set_setting(c, f"{kind}_on", True)
    elif not await S.get(c.chat_id, f"{kind}_on"):
        msg += c.t(f" خاموش است: <code>/{kind} on</code>", f" It's off: <code>/{kind} on</code>")
    return msg


async def greeting_preview(c: Cmd, kind: str) -> None:
    from telegram import User
    u = User(id=c.actor_id or RT.bot_id, first_name=c.actor_name or "Member", is_bot=False)
    await content.send_greeting(c.chat_id, c.chat_title, [u], kind, thread_id=c.thread_id if c.is_forum else None)


@command("setwelcome", right="info", section="welcome")
async def k_setwelcome(c):
    await say(c, await save_greeting(c, "welcome", c.rest_html, enable=None))
    if c.rest_html or c.reply:
        await greeting_preview(c, "welcome")


@command("setgoodbye", right="info", section="welcome")
async def k_setgoodbye(c):
    await say(c, await save_greeting(c, "goodbye", c.rest_html, enable=None))


@command("resetwelcome", right="info", section="welcome")
async def k_resetwelcome(c):
    await S.set_value(c.chat_id, "welcome_text", None, c.actor_id or 0)
    await S.set_value(c.chat_id, "welcome_media", None, c.actor_id or 0)
    await say(c, c.t("✅ متن خوشامد پیش‌فرض شد.", "✅ Welcome text reset."))


async def _greet_cmd(c: Cmd, kind: str) -> None:
    if c.args and c.args[0].lower() in ("preview", "پیش‌نمایش", "test"):
        return await greeting_preview(c, kind)
    v = onoff(c)
    if v is None:
        eff = await S.load(c.chat_id)
        on, src = eff[f"{kind}_on"]
        txt = eff[f"{kind}_text"][0]
        return await say(c, c.t(f"{'خوشامد' if kind == 'welcome' else 'خداحافظی'}: {S.fmt_value('welcome_on', on, 'fa')} · <code>/{kind} on|off|preview</code>",
                                f"{kind.title()}: {S.fmt_value('welcome_on', on, 'en')} · <code>/{kind} on|off|preview</code>"))
    await say(c, await set_setting(c, f"{kind}_on", v))


@command("welcome", right="info", section="welcome")
async def k_welcome(c): await _greet_cmd(c, "welcome")
@command("goodbye", right="info", section="welcome")
async def k_goodbye(c): await _greet_cmd(c, "goodbye")


# rules + help center
@command("rules", admin=False, scope="any", section="answers")
async def k_rules(c):
    if c.is_private:
        return await say(c, c.t("فقط در گروه.", "Group only."))
    body = await RT.db.val("SELECT body FROM rz_rules WHERE chat_id=?", (c.chat_id,))
    await say(c, body if body else c.t("قانونی ثبت نشده.", "No rules set."))


@command("setrules", right="info", section="answers")
async def k_setrules(c):
    body = c.rest_html or (c.reply.text_html if c.reply else "")
    if not body:
        return await say(c, c.t("<code>/setrules متن</code> یا ریپلای روی پیام", "<code>/setrules text</code> or reply to a message"))
    await RT.db.run("INSERT OR REPLACE INTO rz_rules VALUES(?,?,?,?)", (c.chat_id, body[:3800], c.actor_id, now()))
    await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "setting", params={"key": "rules"})
    await say(c, c.t("✅ قوانین ذخیره شد.", "✅ Rules saved."))


@command("clearrules", right="info", section="answers")
async def k_clearrules(c):
    await RT.db.run("DELETE FROM rz_rules WHERE chat_id=?", (c.chat_id,))
    await say(c, c.t("✅ قوانین پاک شد.", "✅ Rules cleared."))


@command("save", "addanswer", right="info", section="answers")
async def k_save(c):
    if not c.args:
        return await say(c, c.t("<code>/save نام متن</code>", "<code>/save name text</code>"))
    name = c.args[0]
    hp = c.rest_html.split(None, 1)
    body = hp[1] if len(hp) > 1 else ""
    mt = fid = None
    if c.reply:
        body = body or c.reply.text_html
        mt, fid = c.reply.media_type, c.reply.file_id
    if not body and not fid:
        return await say(c, c.t("متنی نیست.", "Nothing to save."))
    try:
        st = await content.answer_save(c.chat_id, name, body, mt, fid, c.actor_id)
    except ValueError:
        return await say(c, c.t("❌ نام نامعتبر.", "❌ Invalid name."))
    await say(c, c.t(f"✅ <code>#{esc(name.lower())}</code> ذخیره شد.", f"✅ <code>#{esc(name.lower())}</code> saved."))


@command("get", admin=False, section="answers")
async def k_get(c):
    if not c.args:
        return await say(c, c.t("<code>/get نام</code>", "<code>/get name</code>"))
    row = await content.answer_get(c.chat_id, c.args[0])
    if not row:
        return await say(c, c.t("پیدا نشد. /faq", "Not found. /faq"))
    await content.answer_send(c.chat_id, row, thread_id=c.thread_id if c.is_forum else None, reply_to=c.message_id)


@command("clear", "rmanswer", right="info", section="answers")
async def k_clear(c):
    if not c.args:
        return await say(c, c.t("<code>/clear نام</code>", "<code>/clear name</code>"))
    _, n = await RT.db.run("DELETE FROM rz_answers WHERE chat_id=? AND name=?", (c.chat_id, c.args[0].lower().lstrip("#")))
    await say(c, c.t("✅ حذف شد.", "✅ Removed.") if n else c.t("پیدا نشد.", "Not found."))


@command("faq", "answers", "notes", admin=False, section="answers")
async def k_faq(c):
    txt, rows = await content.faq_menu(c.chat_id, c.lang)
    await say(c, txt, rows)


@command("keyword", "filter", right="info", section="answers")
async def k_keyword(c):
    """/keyword name word1, word2  and  /filter word reply"""
    if len(c.args) < 2:
        return await say(c, c.t("<code>/filter کلمه پاسخ</code> · <code>/keyword نام کلمه۱, کلمه۲</code>",
                                "<code>/filter word reply</code> · <code>/keyword name kw1, kw2</code>"))
    if c.name == "filter":
        kw = c.args[0].lower()
        hp = c.rest_html.split(None, 1)
        body = hp[1] if len(hp) > 1 else ""
        name = ("kw-" + kw)[:40]
        try:
            await content.answer_save(c.chat_id, name, body, None, None, c.actor_id)
        except ValueError:
            return await say(c, c.t("❌ نامعتبر.", "❌ Invalid."))
        kws = [kw]
    else:
        name = c.args[0].lower().lstrip("#")
        if not await content.answer_get(c.chat_id, name):
            return await say(c, c.t("پیدا نشد. اول /save", "Not found. Use /save first."))
        kws = [w.strip().lower() for w in re.split(r"[,،]", rest_after(c, 1)) if w.strip()][:10]
    await RT.db.run("UPDATE rz_answers SET keywords=? WHERE chat_id=? AND name=?", (jd(kws), c.chat_id, name))
    auto = await S.get(c.chat_id, "answers_auto")
    await say(c, c.t("✅ ذخیره شد.", "✅ Saved.") +
              ("" if auto else c.t(" پاسخ خودکار خاموش است: <code>/autoanswer on</code>", " Auto-answer is off: <code>/autoanswer on</code>")))


@command("stop", right="info", section="answers")
async def k_stop(c):
    if not c.args:
        return await say(c, c.t("<code>/stop کلمه</code>", "<code>/stop word</code>"))
    _, n = await RT.db.run("DELETE FROM rz_answers WHERE chat_id=? AND name=?", (c.chat_id, ("kw-" + c.args[0].lower())[:40]))
    await say(c, c.t("✅ حذف شد.", "✅ Removed.") if n else c.t("پیدا نشد.", "Not found."))


@command("autoanswer", right="info", section="answers")
async def k_autoanswer(c): await toggle_cmd(c, "answers_auto")


# logs
@command("setlog", right="info", section="logs")
async def k_setlog(c):
    a = [norm_digits(x) for x in c.args]
    if a and re.fullmatch(r"-?\d{5,16}", a[0]):
        dest = int(a[0]) if a[0].startswith("-") else -int(a[0])
        topic = int(a[1]) if len(a) > 1 and a[1].isdigit() else None
        ok, msg = await logs.verify_destination(c.chat_id, dest, topic, c.actor_id, c.lang)
        if ok:
            await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "log_setup", params={"dest": dest, "topic": topic})
        return await say(c, msg)
    if a and a[0].startswith("@"):
        out = await tg.call(RT.bot.get_chat, chat_id=a[0])
        if not out.ok:
            return await say(c, c.t("❌ پیدا نشد.", "❌ Not found."))
        ok, msg = await logs.verify_destination(c.chat_id, out.value.id, None, c.actor_id, c.lang)
        return await say(c, msg)
    code = await logs.new_code(c.chat_id, c.actor_id)
    await say(c, c.t(f"ربات رو در گروه لاگ ادمین کن و اینو اونجا بفرست (۱۰ دقیقه):\n<code>/linklog {code}</code>",
                     f"Make the bot admin in the log chat and send this there (10 min):\n<code>/linklog {code}</code>"))


@command("unsetlog", right="info", section="logs")
async def k_unsetlog(c):
    await logs.disable(c.chat_id)
    await say(c, c.t("✅ لاگ خاموش شد.", "✅ Logs off."))


@command("logtest", "logrepair", right="info", section="logs")
async def k_logtest(c):
    ok, msg = await logs.repair(c.chat_id, c.actor_id, c.lang)
    await say(c, msg)


@command("logstatus", right=None, section="logs")
async def k_logstatus(c):
    r = await logs.route(c.chat_id)
    if not r or r["status"] == "disabled":
        return await say(c, c.t("لاگ تنظیم نشده. /setlog", "Logs not set. /setlog"))
    q = await RT.db.one("SELECT SUM(status='pending') p, SUM(status='held') h FROM rz_log_queue WHERE chat_id=?", (c.chat_id,))
    pend = int(q["p"] or 0) + int(q["h"] or 0)
    rows = [[(c.t("🔧 تست", "🔧 Test"), "lg:repair"), (c.t("⛔️ خاموش", "⛔️ Disable"), "lg:off")]]
    await say(c, c.t(f"لاگ: {logs.status_label(r['status'], 'fa')} · <code>{r['dest_id']}</code>"
                     + (f"\nدر صف: {pend}" if pend else "") + (f"\n⚠️ {esc(r['last_error'])}" if r["last_error"] and r["status"] == "broken" else ""),
                     f"Logs: {logs.status_label(r['status'], 'en')} · <code>{r['dest_id']}</code>"
                     + (f"\nQueued: {pend}" if pend else "") + (f"\n⚠️ {esc(r['last_error'])}" if r["last_error"] and r["status"] == "broken" else "")), rows)


# history / undo / cases
@command("history", right="restrict", section="tools")
async def k_history(c):
    n = min(int(norm_digits(c.args[0])), 25) if c.args and norm_digits(c.args[0]).isdigit() else 10
    rows = await audit.recent(c.chat_id, n)
    if not rows:
        return await say(c, c.t("چیزی ثبت نشده.", "Nothing yet."))
    lines = [audit.fmt_action(r, c.lang, c.tz) for r in rows]
    btns, row = [], []
    for r in rows:
        if r["undo"] and not r["undone_at"] and r["outcome"] == "ok":
            row.append((f"↩️ {r['id']}", f"un:{r['id']}"))
            if len(row) == 4:
                btns.append(row)
                row = []
    if row:
        btns.append(row)
    await say(c, "\n".join(lines), btns)


@command("undo", right="restrict", section="tools")
async def k_undo(c):
    if not c.args or not norm_digits(c.args[0]).isdigit():
        return await say(c, c.t("<code>/undo شماره</code> (از /history)", "<code>/undo id</code> (from /history)"))
    ok, msg = await audit.undo(c.chat_id, int(norm_digits(c.args[0])), c.actor_id, c.lang)
    await say(c, ("✅ " if ok else "") + msg)


@command("case", right="restrict", section="tools")
async def k_case(c):
    if not c.args or not norm_digits(c.args[0]).lstrip("#").isdigit():
        return await say(c, "<code>/case 12</code>")
    no = int(norm_digits(c.args[0]).lstrip("#"))
    r = await RT.db.one("SELECT * FROM rz_cases WHERE chat_id=? AND case_no=?", (c.chat_id, no))
    if not r:
        return await say(c, c.t("پرونده پیدا نشد.", "Case not found."))
    ev = jl(r["evidence"], {}) or {}
    link = f"https://t.me/c/{str(c.chat_id)[4:]}/{ev['message_id']}" if ev.get("message_id") and str(c.chat_id).startswith("-100") else ""
    aps = await RT.db.all("SELECT id,status FROM rz_appeals WHERE chat_id=? AND case_no=?", (c.chat_id, no))
    await say(c, (f"<b>{c.t('پرونده', 'Case')} #{no}</b> · {esc(audit.action_name(r['action'], c.lang))} · {audit.case_status(r['status'], c.lang)}\n"
                  f"{esc(r['target_name'] or '')} [<code>{r['target_id']}</code>] · {c.t('توسط', 'by')} <code>{r['actor_id'] or 'auto'}</code>\n"
                  + (f"{c.t('دلیل', 'Reason')}: {esc(r['reason'])}\n" if r["reason"] else "") + f"{fmt_dt(r['created_at'], c.tz, c.lang)}" +
                  (f" · {fmt_duration(r['duration_s'], c.lang)}" if r["duration_s"] else "") + (f"\n{link}" if link else "") +
                  (f"\n{esc(ev['excerpt'])}" if ev.get("excerpt") else "") +
                  (f"\n{c.t('اعتراض', 'Appeals')}: " + ", ".join(f"#{a['id']} {a['status']}" for a in aps) if aps else "")),
              [[(c.t("↩️ برگرداندن", "↩️ Undo"), f"un:{r['action_id']}")]] if r["status"] == "active" and r["action_id"] else None)


@command("cases", right="restrict", section="tools")
async def k_cases(c):
    if c.args or c.reply:
        t = await resolve(c)
        if not t.ok:
            return await say(c, t.err)
        rows = await RT.db.all("SELECT * FROM rz_cases WHERE chat_id=? AND target_id=? ORDER BY case_no DESC LIMIT 15", (c.chat_id, t.user_id))
    else:
        rows = await RT.db.all("SELECT * FROM rz_cases WHERE chat_id=? ORDER BY case_no DESC LIMIT 15", (c.chat_id,))
    if not rows:
        return await say(c, c.t("پرونده‌ای نیست.", "No cases."))
    await say(c, "\n".join([f"#{r['case_no']} {esc(audit.action_name(r['action'], c.lang))} · {esc(r['target_name'] or r['target_id'])} · "
                            f"{audit.case_status(r['status'], c.lang)} · {fmt_dt(r['created_at'], c.tz, c.lang, with_tz=False)}" for r in rows]))


@command("appeals", right="restrict", section="desk")
async def k_appeals(c):
    rows = await RT.db.all("SELECT * FROM rz_appeals WHERE chat_id=? AND status='pending' ORDER BY id LIMIT 10", (c.chat_id,))
    if not rows:
        return await say(c, c.t("اعتراضی نیست.", "No appeals."))
    for a in rows:
        await say(c, f"{c.t('اعتراض', 'Appeal')} #{a['id']} · {c.t('پرونده', 'case')} #{a['case_no']} · <code>{a['user_id']}</code>\n{esc(a['text'][:500])}",
                  [[(c.t("✅ قبول", "✅ Accept"), f"ap:y:{a['id']}"), (c.t("❌ رد", "❌ Reject"), f"ap:n:{a['id']}")]])


# settings shortcuts
@command("set", right="info", section="tools")
async def k_set(c):
    if len(c.args) < 2 or c.args[0] not in S.SPECS:
        return await say(c, c.t("<code>/set کلید مقدار</code>\n", "<code>/set key value</code>\n") +
                       ", ".join(k for k in S.SPECS if S.SPECS[k].kind != "text"))
    await say(c, await set_setting(c, c.args[0], " ".join(c.args[1:])))


# AI tool bridge
async def run_tool(c: Cmd, tool: str, g: dict) -> str:
    """Run one AI step using the command handlers."""
    if tool in ("ban", "unban", "mute", "unmute", "kick", "warn"):
        sub = Cmd(**{**c.to_payload(), "reply": None, "mentions": []})
        sub.reply = c.reply
        sub.args = [] if g["target"] == "reply" else [g["target"]]
        t = await resolve(sub)
        if not t.ok:
            return t.err
        if tool == "ban" and t.via == "username" and not t.verified:
            return c.t("❌ این یوزرنیم عضو گروه نیست. با آیدی عددی یا ریپلای بگویید.",
                       "❌ That username isn't in the group. Use a numeric id or reply.")
        dur, reason = g.get("duration"), g.get("reason", "")
        fn = {"ban": lambda: M.ban(sub, t, dur, reason), "unban": lambda: M.unban(sub, t), "mute": lambda: M.mute(sub, t, dur, reason),
              "unmute": lambda: M.unmute(sub, t), "kick": lambda: M.kick(sub, t, reason), "warn": lambda: M.warn(sub, t, reason)}[tool]
        return (await fn()).text
    if tool == "lock":
        return await do_lock(c, g["type"], g.get("duration"))
    if tool == "unlock":
        return await do_unlock(c, g["type"])
    if tool == "night_lock":
        if not await authz.bot_has(c.chat_id, "restrict", fresh=True):
            return c.t("❌ ربات دسترسی بن کاربران ندارد.", "❌ The bot lacks the Ban users right.")
        return await _night_ok({"start": g["start"], "end": g["end"], "cmd": c.to_payload()}, c.actor_id, c.chat_id, None)
    if tool == "night_off":
        from pilot import planner
        ok = await planner.clear_night(c.chat_id)
        return c.t("✅ قفل شبانه خاموش شد.", "✅ Night lock off.") if ok else c.t("فعال نبود.", "Not active.")
    if tool in ("set_welcome", "set_goodbye"):
        kind = "welcome" if tool == "set_welcome" else "goodbye"
        sub = Cmd(**{**c.to_payload(), "reply": None})
        msg = await save_greeting(sub, kind, esc(g["text"]), g.get("enable"))
        if kind == "welcome":
            await greeting_preview(c, "welcome")
        return msg
    if tool == "welcome_off":
        return await set_setting(c, "welcome_on", False)
    if tool == "set_rules":
        await RT.db.run("INSERT OR REPLACE INTO rz_rules VALUES(?,?,?,?)", (c.chat_id, esc(g["text"]), c.actor_id, now()))
        return c.t("✅ قوانین ذخیره شد.", "✅ Rules saved.")
    if tool == "set_filter":
        return await set_setting(c, g["key"], g["value"])
    if tool == "filters_off":
        on = [k for k, _ in await S.enabled_filters(c.chat_id)]
        if not on:
            return c.t("هیچ فیلتری روشن نبود.", "No filters were on.")
        return await _safereset({"keys": on, "cmd": c.to_payload()}, c.actor_id, c.chat_id, None)
    if tool == "set_log":
        ok, msg = await logs.verify_destination(c.chat_id, g["chat_id"], g.get("topic_id"), c.actor_id, c.lang)
        return msg
    if tool == "explain_deletions":
        ok, _ = await authz.check(c.chat_id, c.actor_id, None) if not c.anon else (True, "")
        if not ok:
            return c.t("⛔️ فقط ادمین‌ها.", "⛔️ Admins only.")
        from pilot.tools import why_text
        return await why_text(c.chat_id, c.lang, c.tz)
    if tool == "health":
        from pilot.tools import health_text
        return await health_text(c.chat_id, c.lang, c.tz, c.is_forum)
    if tool == "slowmode_info":
        return await slowmode_text(c)
    if tool == "show_rules":
        body = await RT.db.val("SELECT body FROM rz_rules WHERE chat_id=?", (c.chat_id,))
        return body if body else c.t("قانونی ثبت نشده.", "No rules set.")
    if tool == "save_answer":
        await content.answer_save(c.chat_id, g["name"], esc(g["text"]), None, None, c.actor_id)
        return c.t(f"✅ #{esc(g['name'])} ذخیره شد.", f"✅ #{esc(g['name'])} saved.")
    if tool == "show_answer":
        row = await content.answer_get(c.chat_id, g["name"])
        if not row:
            return c.t("پیدا نشد. /faq", "Not found. /faq")
        await content.answer_send(c.chat_id, row, thread_id=c.thread_id if c.is_forum else None)
        return ""
    if tool == "purge_word":
        from pilot import purgeword
        return await purgeword.execute(c, g["word"])
    if tool == "pin":
        out = await tg.call(RT.bot.pin_chat_message, chat_id=c.chat_id, message_id=c.reply.message_id, disable_notification=True)
        return c.t("📌 سنجاق شد.", "📌 Pinned.") if out.ok else M.fail_text(c, out, "pin")
    from pilot import commands2 as K2
    return await K2.run_tool2(c, tool, g)
