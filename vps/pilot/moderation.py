"""Ban, mute, kick and warn. Everything that moderates goes through here."""
from __future__ import annotations

import time
from dataclasses import dataclass

from telegram import ChatPermissions

from pilot import audit, authz, settings as S, tg
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.targets import Target
from pilot.util import esc, fmt_dt, fmt_duration, now

MUTED = ChatPermissions(**{k: False for k in ChatPermissions.__slots__ if k.startswith("can_send")},
                        can_add_web_page_previews=False)
UNMUTED = ChatPermissions.all_permissions()


@dataclass
class Act:
    ok: bool
    text: str
    status: str = "ok"      # ok | noop | failed | uncertain | refused
    action_id: int | None = None
    case_no: int | None = None


def actor_kind(c: Cmd) -> str:
    return {"ai": "ai", "auto": "auto", "appeal": "admin", "button": "admin"}.get(c.source, "admin")


def who(c: Cmd) -> str:
    if c.source == "auto":
        return "auto"
    nm = esc(c.actor_name) or str(c.actor_id)
    tag = " (AI)" if c.source == "ai" else ""
    return f'<a href="tg://user?id={c.actor_id}">{nm}</a> [<code>{c.actor_id}</code>]{tag}'


def fail_text(c: Cmd, out: tg.Out, right: str) -> str:
    k = out.kind
    if k == "rights":
        return c.t(f"❌ ربات دسترسی «{authz.right_label(right, 'fa')}» ندارد.", f"❌ The bot lacks the '{authz.right_label(right, 'en')}' right.")
    if k == "target_admin":
        return c.t("❌ کاربر ادمین است.", "❌ The user is an admin.")
    if k == "not_found":
        return c.t("❌ کاربر پیدا نشد.", "❌ User not found.")
    if k == "uncertain":
        return c.t("❔ تلگرام جواب نداد. معلوم نیست انجام شد یا نه.", "❔ Telegram timed out. Result unknown.")
    if k == "flood":
        return c.t("⏳ کمی بعد دوباره امتحان کنید.", "⏳ Try again shortly.")
    return c.t(f"❌ {esc(out.desc)}", f"❌ {esc(out.desc)}")


async def protected(c: Cmd, t: Target, action: str) -> str | None:
    uid = t.user_id
    if t.sender_chat_id:
        if t.sender_chat_id == c.chat_id:
            return c.t("ادمین ناشناس است.", "That's an anonymous admin.")
        if c.linked_chat_id and t.sender_chat_id == c.linked_chat_id:
            return c.t("کانال متصل گروه است.", "That's the linked channel.")
        return None
    if uid == RT.bot_id:
        return c.t("روی خود ربات نمی‌شود.", "Not on the bot itself.")
    if uid in (authz.ANON_ADMIN_BOT, authz.TG_SERVICE_ID):
        return c.t("حساب سیستمی تلگرام است.", "That's a Telegram system account.")
    if authz.is_owner(uid) and action not in ("unban", "unmute"):
        return c.t("مالک ربات است.", "That's the bot owner.")
    if uid == c.actor_id and action not in ("kickme",):
        return c.t("روی خودتان نمی‌شود.", "Not on yourself.")
    if action in ("ban", "mute", "kick", "warn"):
        m = await authz.member(c.chat_id, uid, fresh=True)
        if m is not None and m.status == "creator":
            return c.t("سازندهٔ گروه است.", "That's the group owner.")
        if m is not None and m.status == "administrator":
            return c.t("ادمین است.", "That's an admin.")
    return None


async def bot_can(c: Cmd, right: str) -> str | None:
    if not await authz.bot_has(c.chat_id, right, fresh=True):
        return c.t(f"❌ ربات دسترسی «{authz.right_label(right, 'fa')}» ندارد.",
                   f"❌ The bot lacks the '{authz.right_label(right, 'en')}' right.")
    return None


def _until(duration: int | None) -> int | None:
    if not duration:
        return None
    return int(now() + max(duration, 35))


def _dur_txt(c: Cmd, duration: int | None) -> str:
    if not duration:
        return ""
    return c.t(f" ({fmt_duration(duration, 'fa')})", f" ({fmt_duration(duration, 'en')})")


def _case_txt(c: Cmd, case_no: int | None) -> str:
    return ""


async def _evidence(c: Cmd) -> dict:
    ev: dict = {}
    if c.reply:
        ev["message_id"] = c.reply.message_id
        if await S.get(c.chat_id, "keep_excerpt"):
            ev["excerpt"] = (c.reply.text or "")[:200]
    return ev


async def ban(c: Cmd, t: Target, duration: int | None = None, reason: str = "", delete_reply: bool = False) -> Act:
    if t.sender_chat_id:
        return await ban_channel(c, t)
    p = await protected(c, t, "ban")
    if p:
        return Act(False, "⛔️ " + p, "refused")
    b = await bot_can(c, "restrict")
    if b:
        return Act(False, b, "failed")
    prior = await authz.member(c.chat_id, t.user_id, fresh=True)
    if prior is not None and prior.status == "kicked" and not duration:
        return Act(True, c.t(f"{t.label()} از قبل بن است.", f"{t.label()} is already banned."), "noop")
    out = await tg.call(RT.bot.ban_chat_member, chat_id=c.chat_id, user_id=t.user_id, until_date=_until(duration))
    if not out.ok and out.kind == "uncertain":
        chk = await authz.member(c.chat_id, t.user_id, fresh=True)
        if chk is not None and chk.status == "kicked":
            out = tg.Out(True)
    action = "tban" if duration else "ban"
    if not out.ok:
        await audit.record(c.chat_id, c.actor_id, actor_kind(c), action, target_id=t.user_id, target_name=t.name,
                           outcome="failed", error=f"{out.kind}: {out.desc}")
        return Act(False, fail_text(c, out, "restrict"), "uncertain" if out.kind == "uncertain" else "failed")
    if delete_reply and c.reply:
        await tg.delete(c.chat_id, c.reply.message_id)
    authz.invalidate(c.chat_id, t.user_id)
    member_note = ""
    aid, case_no = await audit.record(
        c.chat_id, c.actor_id, actor_kind(c), action, target_id=t.user_id, target_name=t.name,
        params={"duration": duration, "via": t.via, "reason": reason[:200]}, undo={"op": "unban", "user": t.user_id},
        reason=reason, duration=duration, evidence=await _evidence(c),
        log_line=f"#{action.upper()} {t.label()}" + (f" · {fmt_duration(duration, 'en')}" if duration else "") +
                 f"\n{who(c)}" + (f"\n{esc(reason)}" if reason else ""))
    txt = c.t(f"🚫 {t.label()} بن شد{_dur_txt(c, duration)}.", f"🚫 {t.label()} banned{_dur_txt(c, duration)}.")
    if reason:
        txt += c.t(f"\nدلیل: {esc(reason)}", f"\nReason: {esc(reason)}")
    return Act(True, txt + _case_txt(c, case_no), "ok", aid, case_no)


async def ban_channel(c: Cmd, t: Target) -> Act:
    p = await protected(c, t, "ban")
    if p:
        return Act(False, "⛔️ " + p, "refused")
    out = await tg.call(RT.bot.ban_chat_sender_chat, chat_id=c.chat_id, sender_chat_id=t.sender_chat_id)
    if not out.ok:
        return Act(False, fail_text(c, out, "restrict"), "failed")
    if c.reply:
        await tg.delete(c.chat_id, c.reply.message_id)
    aid, case_no = await audit.record(c.chat_id, c.actor_id, actor_kind(c), "ban_channel", target_id=t.sender_chat_id,
                                      target_name=t.name, undo={"op": "unban_channel", "chat": t.sender_chat_id},
                                      log_line=f"#BAN_CHANNEL {t.label()}\n{who(c)}")
    return Act(True, c.t(f"🚫 کانال {t.label()} بن شد.", f"🚫 Channel {t.label()} banned."), "ok", aid, case_no)


async def unban(c: Cmd, t: Target) -> Act:
    if t.sender_chat_id:
        out = await tg.call(RT.bot.unban_chat_sender_chat, chat_id=c.chat_id, sender_chat_id=t.sender_chat_id)
        return Act(out.ok, c.t("✅ کانال آزاد شد.", "✅ Channel unbanned.") if out.ok else fail_text(c, out, "restrict"))
    b = await bot_can(c, "restrict")
    if b:
        return Act(False, b, "failed")
    prior = await authz.member(c.chat_id, t.user_id, fresh=True)
    if prior is not None and prior.status != "kicked":
        return Act(True, c.t(f"{t.label()} بن نیست.", f"{t.label()} is not banned."), "noop")
    out = await tg.call(RT.bot.unban_chat_member, chat_id=c.chat_id, user_id=t.user_id, only_if_banned=True)
    if not out.ok:
        return Act(False, fail_text(c, out, "restrict"), "failed")
    authz.invalidate(c.chat_id, t.user_id)
    await RT.db.run("UPDATE rz_cases SET status='reversed' WHERE chat_id=? AND target_id=? AND action IN ('ban','tban','auto_ban') "
                    "AND status='active'", (c.chat_id, t.user_id))
    aid, _ = await audit.record(c.chat_id, c.actor_id, actor_kind(c), "unban", target_id=t.user_id, target_name=t.name,
                                log_line=f"#UNBAN {t.label()}\n{who(c)}")
    return Act(True, c.t(f"✅ {t.label()} آنبن شد.", f"✅ {t.label()} unbanned."), "ok", aid)


async def mute(c: Cmd, t: Target, duration: int | None = None, reason: str = "") -> Act:
    if t.sender_chat_id:
        return Act(False, c.t("برای کانال /ban بزنید.", "Use /ban for channels."), "refused")
    p = await protected(c, t, "mute")
    if p:
        return Act(False, "⛔️ " + p, "refused")
    b = await bot_can(c, "restrict")
    if b:
        return Act(False, b, "failed")
    prior = await authz.member(c.chat_id, t.user_id, fresh=True)
    prev, prev_until = None, None
    if prior is not None and prior.status == "restricted":
        prev = audit.perms_of(prior)
        ud = getattr(prior, "until_date", None)
        prev_until = ud.timestamp() if ud else None
    note = ""
    out = await tg.call(RT.bot.restrict_chat_member, chat_id=c.chat_id, user_id=t.user_id, permissions=MUTED,
                        until_date=_until(duration), use_independent_chat_permissions=True)
    action = "tmute" if duration else "mute"
    if not out.ok:
        await audit.record(c.chat_id, c.actor_id, actor_kind(c), action, target_id=t.user_id, target_name=t.name,
                           outcome="failed", error=f"{out.kind}: {out.desc}")
        return Act(False, fail_text(c, out, "restrict"), "failed")
    authz.invalidate(c.chat_id, t.user_id)
    set_p = {k: bool(getattr(MUTED, k) or False) for k in ChatPermissions.__slots__ if k.startswith("can_")}
    aid, case_no = await audit.record(
        c.chat_id, c.actor_id, actor_kind(c), action, target_id=t.user_id, target_name=t.name,
        params={"duration": duration, "via": t.via, "reason": reason[:200]},
        undo={"op": "restore_member", "user": t.user_id, "set": set_p, "prev": prev, "prev_until": prev_until},
        reason=reason, duration=duration, evidence=await _evidence(c),
        log_line=f"#{action.upper()} {t.label()}" + (f" · {fmt_duration(duration, 'en')}" if duration else "") +
                 f"\n{who(c)}" + (f"\n{esc(reason)}" if reason else ""))
    txt = c.t(f"🔇 {t.label()} سایلنت شد{_dur_txt(c, duration)}.", f"🔇 {t.label()} muted{_dur_txt(c, duration)}.")
    if reason:
        txt += c.t(f"\nدلیل: {esc(reason)}", f"\nReason: {esc(reason)}")
    return Act(True, txt + _case_txt(c, case_no), "ok", aid, case_no)


async def unmute(c: Cmd, t: Target) -> Act:
    b = await bot_can(c, "restrict")
    if b:
        return Act(False, b, "failed")
    prior = await authz.member(c.chat_id, t.user_id, fresh=True)
    if prior is not None and prior.status != "restricted":
        return Act(True, c.t(f"{t.label()} سایلنت نیست.", f"{t.label()} is not muted."), "noop")
    out = await tg.call(RT.bot.restrict_chat_member, chat_id=c.chat_id, user_id=t.user_id, permissions=UNMUTED,
                        use_independent_chat_permissions=True)
    if not out.ok:
        return Act(False, fail_text(c, out, "restrict"), "failed")
    authz.invalidate(c.chat_id, t.user_id)
    await RT.db.run("UPDATE rz_cases SET status='reversed' WHERE chat_id=? AND target_id=? AND action IN ('mute','tmute','auto_mute') "
                    "AND status='active'", (c.chat_id, t.user_id))
    aid, _ = await audit.record(c.chat_id, c.actor_id, actor_kind(c), "unmute", target_id=t.user_id, target_name=t.name,
                                log_line=f"#UNMUTE {t.label()}\n{who(c)}")
    return Act(True, c.t(f"🔊 {t.label()} از سایلنت درآمد.", f"🔊 {t.label()} unmuted."), "ok", aid)


async def kick(c: Cmd, t: Target, reason: str = "") -> Act:
    if t.sender_chat_id:
        return await ban_channel(c, t)
    p = await protected(c, t, "kick")
    if p:
        return Act(False, "⛔️ " + p, "refused")
    b = await bot_can(c, "restrict")
    if b:
        return Act(False, b, "failed")
    prior = await authz.member(c.chat_id, t.user_id, fresh=True)
    if prior is None or prior.status in ("left", "kicked"):
        return Act(True, c.t(f"{t.label()} عضو گروه نیست.", f"{t.label()} is not in the group."), "noop")
    out = await tg.call(RT.bot.ban_chat_member, chat_id=c.chat_id, user_id=t.user_id, until_date=int(now() + 60))
    if not out.ok:
        return Act(False, fail_text(c, out, "restrict"), "failed")
    await tg.call(RT.bot.unban_chat_member, chat_id=c.chat_id, user_id=t.user_id, only_if_banned=True)
    authz.invalidate(c.chat_id, t.user_id)
    aid, case_no = await audit.record(c.chat_id, c.actor_id, actor_kind(c), "kick", target_id=t.user_id, target_name=t.name,
                                      params={"reason": reason[:200]}, reason=reason, evidence=await _evidence(c),
                                      log_line=f"#KICK {t.label()}\n{who(c)}" + (f"\n{esc(reason)}" if reason else ""))
    return Act(True, c.t(f"👢 {t.label()} اخراج شد.", f"👢 {t.label()} kicked."), "ok", aid, case_no)


async def warn(c: Cmd, t: Target, reason: str = "") -> Act:
    if t.sender_chat_id:
        return Act(False, c.t("به کانال اخطار نمی‌شود داد.", "Channels can't be warned."), "refused")
    p = await protected(c, t, "warn")
    if p:
        return Act(False, "⛔️ " + p, "refused")
    eff = await S.values(c.chat_id)
    wid, _ = await RT.db.run("INSERT INTO rz_warns(chat_id,user_id,reason,actor_id,ts,active) VALUES(?,?,?,?,?,1)",
                             (c.chat_id, t.user_id, reason[:200], c.actor_id, now()))
    n = await RT.db.val("SELECT COUNT(*) FROM rz_warns WHERE chat_id=? AND user_id=? AND active=1", (c.chat_id, t.user_id), 0)
    lim = eff["warn_limit"]
    aid, case_no = await audit.record(c.chat_id, c.actor_id, actor_kind(c), "warn", target_id=t.user_id, target_name=t.name,
                                      params={"reason": reason[:200], "count": n}, undo={"op": "unwarn", "warn_id": wid},
                                      reason=reason, evidence=await _evidence(c),
                                      log_line=f"#WARN {n}/{lim} {t.label()}\n{who(c)}" + (f"\n{esc(reason)}" if reason else ""))
    txt = c.t(f"⚠️ {t.label()} اخطار گرفت ({n}/{lim}).", f"⚠️ {t.label()} warned ({n}/{lim}).")
    if reason:
        txt += c.t(f"\nدلیل: {esc(reason)}", f"\nReason: {esc(reason)}")
    txt += _case_txt(c, case_no)
    if n >= lim:
        await RT.db.run("UPDATE rz_warns SET active=0 WHERE chat_id=? AND user_id=? AND active=1", (c.chat_id, t.user_id))
        act = eff["warn_action"]
        sub = Cmd(**{**c.to_payload(), "reply": None})
        sub.reply = c.reply
        if act == "ban":
            r = await ban(sub, t, reason=c.t("سقف اخطار", "warn limit"))
        elif act == "kick":
            r = await kick(sub, t, reason=c.t("سقف اخطار", "warn limit"))
        else:
            r = await mute(sub, t, duration=eff["warn_mute_h"] * 3600, reason=c.t("سقف اخطار", "warn limit"))
        txt += "\n" + r.text
    return Act(True, txt, "ok", aid, case_no)


async def warns_text(c: Cmd, t: Target) -> str:
    rows = await RT.db.all("SELECT id,reason,ts FROM rz_warns WHERE chat_id=? AND user_id=? AND active=1 ORDER BY id", (c.chat_id, t.user_id))
    lim = await S.get(c.chat_id, "warn_limit")
    if not rows:
        return c.t(f"{t.label()} اخطاری ندارد.", f"{t.label()} has no warnings.")
    lines = [c.t(f"اخطارهای {t.label()}: {len(rows)}/{lim}", f"{t.label()} warnings: {len(rows)}/{lim}")]
    for r in rows:
        lines.append(f"• {esc(r['reason']) + ' · ' if r['reason'] else ''}{fmt_dt(r['ts'], c.tz, c.lang, with_tz=False)}")
    return "\n".join(lines)


async def reset_warns(c: Cmd, t: Target) -> Act:
    _, n = await RT.db.run("UPDATE rz_warns SET active=0 WHERE chat_id=? AND user_id=? AND active=1", (c.chat_id, t.user_id))
    await audit.record(c.chat_id, c.actor_id, actor_kind(c), "unwarn", target_id=t.user_id, target_name=t.name, params={"all": True, "n": n})
    return Act(True, c.t(f"✅ اخطارهای {t.label()} پاک شد.", f"✅ {t.label()} warnings cleared."))


async def rm_warn(c: Cmd, t: Target) -> Act:
    r = await RT.db.one("SELECT id FROM rz_warns WHERE chat_id=? AND user_id=? AND active=1 ORDER BY id DESC LIMIT 1", (c.chat_id, t.user_id))
    if not r:
        return Act(True, c.t("اخطاری نیست.", "No warnings."), "noop")
    await RT.db.run("UPDATE rz_warns SET active=0 WHERE id=?", (r["id"],))
    await audit.record(c.chat_id, c.actor_id, actor_kind(c), "unwarn", target_id=t.user_id, target_name=t.name, params={"warn_id": r["id"]})
    return Act(True, c.t(f"✅ یک اخطار {t.label()} برداشته شد.", f"✅ One warning removed from {t.label()}."))


async def purge(c: Cmd, from_id: int, to_id: int) -> Act:
    b = await bot_can(c, "delete")
    if b:
        return Act(False, b, "failed")
    ids = list(range(from_id, to_id + 1))[-1000:]
    deleted_calls = 0
    failed = 0
    for i in range(0, len(ids), 100):
        out = await tg.call(RT.bot.delete_messages, chat_id=c.chat_id, message_ids=ids[i:i + 100])
        if out.ok:
            deleted_calls += 1
        else:
            failed += 1
    await audit.record(c.chat_id, c.actor_id, actor_kind(c), "purge", params={"from": from_id, "to": to_id, "n": len(ids)},
                       outcome="ok" if not failed else ("partial" if deleted_calls else "failed"),
                       log_line=f"#PURGE {len(ids)}\n{who(c)}")
    if failed and not deleted_calls:
        return Act(False, c.t("❌ حذف نشد. پیام‌های بالای ۴۸ ساعت حذف نمی‌شوند.",
                              "❌ Not deleted. Messages older than 48h can't be deleted."), "failed")
    return Act(True, "")
