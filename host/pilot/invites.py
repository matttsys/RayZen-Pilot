"""Invite links created by the bot, with join counts and evidence-based inviter credit.

Attribution only uses links this bot created: Telegram reports the full link in chat_member updates for
those, and each personal link has exactly one owner. Joins through other links or without a link get no
credit. Direct adds are counted separately (/topadders) from the service message's sender."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from pilot import audit, authz, moderation as M, settings as S, tg
from pilot.commands import command, onoff, say, set_setting
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.util import esc, fmt_dt, fmt_duration, looks_like_duration, norm_digits, num, numeric_word, parse_duration

MAX_LINKS = 200


def _parse_opts(args: list[str]) -> tuple[str, int | None, int | None]:
    """name words, optional duration (expiry) and optional 'limit N' / عدد نفر."""
    name, expire, limit = [], None, None
    i = 0
    while i < len(args):
        a = norm_digits(args[i]).lower()
        if a in ("limit", "سقف", "ظرفیت") and i + 1 < len(args) and norm_digits(args[i + 1]).isdigit():
            limit = max(1, min(99999, int(norm_digits(args[i + 1]))))
            i += 2
            continue
        if looks_like_duration(a):
            expire = parse_duration(a)
            i += 1
            continue
        if i + 1 < len(args) and numeric_word(a) and looks_like_duration(a + " " + args[i + 1]):
            expire = parse_duration(a + " " + args[i + 1])
            i += 2
            continue
        name.append(args[i])
        i += 1
    return " ".join(name)[:32], expire, limit


async def create_link(c: Cmd, name: str, expire: int | None, limit: int | None, request: bool, owner_id: int | None) -> tuple[bool, str]:
    b = await M.bot_can(c, "invite")
    if b:
        return False, b
    n = await RT.db.val("SELECT COUNT(*) FROM rz_invites WHERE chat_id=? AND revoked=0", (c.chat_id,), 0)
    if n >= MAX_LINKS:
        return False, c.t(f"سقف {MAX_LINKS} لینک فعال پر است. یکی را باطل کنید.", f"{MAX_LINKS} active links max. Revoke one first.")
    kw = {"chat_id": c.chat_id, "name": name or None}
    if expire:
        kw["expire_date"] = datetime.fromtimestamp(time.time() + expire, tz=timezone.utc)
    if request:
        kw["creates_join_request"] = True
    elif limit:
        kw["member_limit"] = limit                  # Telegram doesn't allow a limit on join-request links
    out = await tg.call(RT.bot.create_chat_invite_link, idempotent=False, **kw)
    if not out.ok:
        return False, M.fail_text(c, out, "invite")
    link = out.value.invite_link
    await RT.db.run("INSERT OR REPLACE INTO rz_invites(chat_id,link,name,owner_id,created_by,created_at,expires_at,member_limit,join_request,revoked,joins) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0,0)", (c.chat_id, link, name, owner_id, c.actor_id, time.time(),
                                                      (time.time() + expire) if expire else None, None if request else limit, int(request)))
    await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "setting", params={"invite_link": name or "-", "request": request},
                       log_line=f"🔗 #INVITE_LINK {esc(name or '-')}" + (" (request)" if request else "") + f"\n{M.who(c)}")
    extra = []
    if expire:
        extra.append(c.t(f"اعتبار: {fmt_duration(expire, 'fa')}", f"valid {fmt_duration(expire, 'en')}"))
    if limit and not request:
        extra.append(c.t(f"ظرفیت: {num(limit, 'fa')} نفر", f"limit {limit}"))
    if request:
        extra.append(c.t("با تأیید ادمین", "needs admin approval"))
    return True, f"🔗 {esc(link)}" + (f"\n{' · '.join(extra)}" if extra else "")


@command("newlink", "createlink", right="invite", section="invites")
async def k_newlink(c: Cmd):
    name, expire, limit = _parse_opts(c.args)
    ok, txt = await create_link(c, name, expire, limit, False, None)
    await say(c, txt if ok or txt else c.t("❌ ساخته نشد.", "❌ Not created."))


@command("requestlink", right="invite", section="invites")
async def k_requestlink(c: Cmd):
    name, expire, _ = _parse_opts(c.args)
    ok, txt = await create_link(c, name, expire, None, True, None)
    if ok and not await S.get(c.chat_id, "jr_desk"):
        txt += c.t("\nدرخواست‌ها را در تلگرام تأیید کنید یا میز عضویت را روشن کنید: <code>/joindesk on</code>",
                   "\nApprove requests in Telegram or turn on the join desk: <code>/joindesk on</code>")
    await say(c, txt)


@command("links", "invitelinks", right="invite", section="invites")
async def k_links(c: Cmd):
    rows = await RT.db.all("SELECT rowid AS n, * FROM rz_invites WHERE chat_id=? AND revoked=0 ORDER BY created_at DESC LIMIT 30", (c.chat_id,))
    if not rows:
        return await say(c, c.t("لینکی با ربات ساخته نشده. <code>/newlink نام</code>", "No links made by the bot yet. <code>/newlink name</code>"))
    now = time.time()
    lines = []
    for r in rows:
        state = ""
        if r["expires_at"] and r["expires_at"] < now:
            state = c.t(" · منقضی", " · expired")
        own = f" · 👤 <code>{r['owner_id']}</code>" if r["owner_id"] else ""
        lines.append(f"<code>#{r['n']}</code> {esc(r['name'] or '-')} · {c.t('ورود', 'joins')}: {num(r['joins'], c.lang)}{own}{state}\n{esc(r['link'])}")
    await say(c, c.t("🔗 <b>لینک‌های ربات</b>\n", "🔗 <b>Bot links</b>\n") + "\n".join(lines) +
              c.t("\nباطل کردن: <code>/revokelink #شماره</code>", "\nRevoke: <code>/revokelink #number</code>"))


@command("revokelink", right="invite", section="invites")
async def k_revokelink(c: Cmd):
    a = norm_digits(c.args[0]).lstrip("#") if c.args else ""
    if not a:
        return await say(c, c.t("<code>/revokelink #شماره</code> یا خود لینک", "<code>/revokelink #number</code> or the link"))
    if a.isdigit():
        row = await RT.db.one("SELECT rowid AS n, * FROM rz_invites WHERE chat_id=? AND rowid=?", (c.chat_id, int(a)))
    else:
        row = await RT.db.one("SELECT rowid AS n, * FROM rz_invites WHERE chat_id=? AND link=?", (c.chat_id, c.args[0]))
    if not row:
        return await say(c, c.t("این لینک با ربات ساخته نشده یا پیدا نشد.", "Not found, or not created by the bot."))
    out = await tg.call(RT.bot.revoke_chat_invite_link, chat_id=c.chat_id, invite_link=row["link"])
    if not out.ok and out.kind != "not_found":
        return await say(c, M.fail_text(c, out, "invite"))
    await RT.db.run("UPDATE rz_invites SET revoked=1 WHERE chat_id=? AND link=?", (c.chat_id, row["link"]))
    await say(c, c.t(f"✅ لینک «{esc(row['name'] or '-')}» باطل شد.", f"✅ Link “{esc(row['name'] or '-')}” revoked."))


@command("mylink", "invitelink", admin=False, section="invites")
async def k_mylink(c: Cmd):
    if not c.actor_id:
        return await say(c, c.t("با حساب ناشناس نمیشه.", "Not for anonymous admins."))
    if not await S.get(c.chat_id, "member_links"):
        return await say(c, c.t("لینک دعوت شخصی در این گروه خاموش است. ادمین: <code>/memberlinks on</code>",
                                "Personal invite links are off here. Admins: <code>/memberlinks on</code>"))
    row = await RT.db.one("SELECT * FROM rz_invites WHERE chat_id=? AND owner_id=? AND revoked=0 ORDER BY created_at DESC LIMIT 1",
                          (c.chat_id, c.actor_id))
    if row:
        link, joins = row["link"], row["joins"]
    else:
        ok, txt = await create_link(c, f"u{c.actor_id}", None, None, False, c.actor_id)
        if not ok:
            return await say(c, txt)
        row = await RT.db.one("SELECT * FROM rz_invites WHERE chat_id=? AND owner_id=? AND revoked=0 ORDER BY created_at DESC LIMIT 1",
                              (c.chat_id, c.actor_id))
        link, joins = row["link"], 0
    await say(c, c.t(f"🔗 لینک دعوت شما: {esc(link)}\nورود با این لینک: {num(joins, 'fa')}",
                     f"🔗 Your invite link: {esc(link)}\nJoins through it: {joins}"))


@command("memberlinks", right="invite", section="invites")
async def k_memberlinks(c: Cmd):
    v = onoff(c)
    if v is None:
        on = await S.get(c.chat_id, "member_links")
        return await say(c, c.t(f"لینک دعوت شخصی اعضا: {'روشن' if on else 'خاموش'}\nهر عضو با <code>/mylink</code> یک لینک می‌گیرد و ورودها به نامش ثبت می‌شود.\n"
                                "<code>/memberlinks on|off</code>",
                                f"Personal member invite links: {'on' if on else 'off'}\nEach member gets one with <code>/mylink</code>; joins are credited to them.\n"
                                "<code>/memberlinks on|off</code>"))
    txt = await set_setting(c, "member_links", v)
    if v and not await authz.bot_has(c.chat_id, "invite", fresh=True):
        txt += c.t("\n⚠️ ربات دسترسی «دعوت کاربر» ندارد.", "\n⚠️ The bot lacks the Invite users right.")
    await say(c, txt)


@command("topinviters", "inviters", admin=False, section="invites")
async def k_topinviters(c: Cmd):
    from pilot import activity
    days = activity.parse_period(c.args, 30)
    rows = await RT.db.all("SELECT i.owner_id AS uid, COUNT(DISTINCT e.user_id) AS n FROM rz_member_events e JOIN rz_invites i "
                           "ON i.chat_id=e.chat_id AND i.link=e.link WHERE e.chat_id=? AND e.kind='join' AND e.ts>=? AND i.owner_id IS NOT NULL "
                           "GROUP BY i.owner_id ORDER BY n DESC LIMIT 15", (c.chat_id, time.time() - days * 86400))
    if not rows:
        return await say(c, c.t("هنوز کسی با لینک شخصی دعوت نکرده.", "No joins through personal links yet."))
    nm = await activity.names([r["uid"] for r in rows])
    lines = [f"{num(i + 1, c.lang)}. {activity.link(r['uid'], nm.get(r['uid'], str(r['uid'])))} — {num(r['n'], c.lang)}" for i, r in enumerate(rows)]
    await say(c, c.t(f"📨 <b>دعوت‌کننده‌ها، {activity.period_label(c, days)}</b>\n", f"📨 <b>Top inviters, {activity.period_label(c, days)}</b>\n")
              + "\n".join(lines) + c.t("\n<i>فقط ورود با لینک‌های شخصی ربات شمرده می‌شود.</i>", "\n<i>Only joins through the bot's personal links count.</i>"))


@command("linkstats", right="invite", section="invites")
async def k_linkstats(c: Cmd):
    from pilot import activity
    days = activity.parse_period(c.args, 30)
    t0 = time.time() - days * 86400
    total = await RT.db.val("SELECT COUNT(*) FROM rz_member_events WHERE chat_id=? AND kind='join' AND ts>=?", (c.chat_id, t0), 0)
    by_link = await RT.db.val("SELECT COUNT(*) FROM rz_member_events WHERE chat_id=? AND kind='join' AND ts>=? AND link IS NOT NULL", (c.chat_id, t0), 0)
    added = await RT.db.val("SELECT COUNT(*) FROM rz_member_events WHERE chat_id=? AND kind='join' AND ts>=? AND by_user IS NOT NULL", (c.chat_id, t0), 0)
    rest = max(0, total - by_link - added)
    await say(c, c.t(f"📥 <b>راه ورود، {activity.period_label(c, days)}</b>\nکل ورودها: {num(total, 'fa')}\nبا لینک: {num(by_link, 'fa')} · اضافه‌شده توسط عضو: {num(added, 'fa')} · نامشخص/دیگر: {num(rest, 'fa')}",
                     f"📥 <b>How people joined, {activity.period_label(c, days)}</b>\nTotal joins: {total}\nVia link: {by_link} · added by a member: {added} · unknown/other: {rest}")
              + f"\n<i>{await activity.coverage(c)}</i>")
