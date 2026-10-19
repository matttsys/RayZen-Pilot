"""Observed activity: message counts per member and day, busy hours, joins, leaves and adds.

Only what the bot itself saw is counted, from the first day it was in the group ("since"). Message text is never
stored. Counters are buffered in memory and written every 30 s; a crash loses at most that window."""
from __future__ import annotations

import time
from collections import Counter, OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pilot import hooks, settings as S, tg
from pilot.commands import command, onoff, say, set_setting
from pilot.ctx import Cmd, lang_tz
from pilot.rt import RT
from pilot.targets import resolve
from pilot.util import esc, fmt_dt, gregorian_to_jalali, norm_digits, num, parse_hhmm, short

_msgs: Counter = Counter()          # (chat, user, day) -> messages
_media: Counter = Counter()
_hours: Counter = Counter()         # (chat, day, hour) -> messages
_seen: OrderedDict = OrderedDict()  # (chat, message_id) for duplicate updates
_ev_seen: dict = {}
_since: set[int] = set()
MEDIA_ATTRS = ("photo", "video", "document", "sticker", "animation", "voice", "audio", "video_note")


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or RT.cfg.tz)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def local_day(ts: float, tz: str) -> tuple[str, int]:
    d = datetime.fromtimestamp(ts, _tz(tz))
    return d.strftime("%Y-%m-%d"), d.hour


async def _enabled(chat_id: int) -> bool:
    return bool(await S.get(chat_id, "stats_on"))


async def observe(m) -> None:
    u = m.from_user
    if u is None or u.is_bot or m.sender_chat is not None:
        return
    key = (m.chat_id, m.message_id)
    if key in _seen:
        return
    _seen[key] = 1
    if len(_seen) > 20000:
        for _ in range(5000):
            _seen.popitem(last=False)
    if not await _enabled(m.chat_id):
        return
    _, tz = await lang_tz(m.chat_id)
    ts = m.date.timestamp() if m.date else time.time()
    day, hour = local_day(ts, tz)
    _msgs[(m.chat_id, u.id, day)] += 1
    if any(getattr(m, a, None) for a in MEDIA_ATTRS):
        _media[(m.chat_id, u.id, day)] += 1
    _hours[(m.chat_id, day, hour)] += 1
    if m.chat_id not in _since:
        _since.add(m.chat_id)
        await RT.db.run("INSERT OR IGNORE INTO rz_stats_meta(chat_id, since) VALUES(?,?)", (m.chat_id, ts))


hooks.ON_GROUP_MESSAGE.append(observe)


async def on_member(chat, user, joined: bool, by_user: int | None, link, via: str) -> None:
    if user.is_bot and not joined:
        return
    kind = "join" if joined else ("kick" if via == "kicked" else "leave")
    k = (chat.id, user.id, kind)
    t = time.time()
    if t - _ev_seen.get(k, 0) < 90:
        return                       # service message and chat_member update for the same event
    _ev_seen[k] = t
    if len(_ev_seen) > 20000:
        _ev_seen.clear()
    if not await _enabled(chat.id):
        return
    url = getattr(link, "invite_link", None) if link is not None else None
    await RT.db.run("INSERT INTO rz_member_events(chat_id,user_id,ts,kind,by_user,link) VALUES(?,?,?,?,?,?)",
                    (chat.id, user.id, t, kind, by_user if kind == "join" else by_user, url))
    await RT.db.run("INSERT OR IGNORE INTO rz_stats_meta(chat_id, since) VALUES(?,?)", (chat.id, t))
    if joined and url:
        await RT.db.run("UPDATE rz_invites SET joins=joins+1 WHERE chat_id=? AND link=?", (chat.id, url))


hooks.ON_MEMBER.append(on_member)


@hooks.periodic("activity_flush", 30)
async def flush() -> None:
    if RT.db is None or not (_msgs or _hours):
        return
    msgs, media, hours = dict(_msgs), dict(_media), dict(_hours)
    _msgs.clear(), _media.clear(), _hours.clear()

    def f(c):
        c.executemany("INSERT INTO rz_activity(chat_id,user_id,day,msgs,media) VALUES(?,?,?,?,?) ON CONFLICT(chat_id,user_id,day) "
                      "DO UPDATE SET msgs=msgs+excluded.msgs, media=media+excluded.media",
                      [(ch, us, d, n, media.get((ch, us, d), 0)) for (ch, us, d), n in msgs.items()])
        c.executemany("INSERT INTO rz_activity_hour(chat_id,day,hour,msgs) VALUES(?,?,?,?) ON CONFLICT(chat_id,day,hour) "
                      "DO UPDATE SET msgs=msgs+excluded.msgs", [(ch, d, h, n) for (ch, d, h), n in hours.items()])
    await RT.db.tx(f)


hooks.ON_SHUTDOWN.append(flush)


@hooks.periodic("activity_retention", 6 * 3600)
async def prune() -> None:
    rows = await RT.db.all("SELECT DISTINCT chat_id FROM rz_activity")
    for r in rows:
        days = await S.get(r["chat_id"], "retention_days") or 90
        cut = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
        await RT.db.run("DELETE FROM rz_activity WHERE chat_id=? AND day<?", (r["chat_id"], cut))
        await RT.db.run("DELETE FROM rz_activity_hour WHERE chat_id=? AND day<?", (r["chat_id"], cut))
        await RT.db.run("DELETE FROM rz_member_events WHERE chat_id=? AND ts<?", (r["chat_id"], time.time() - days * 86400))


async def clear(chat_id: int) -> None:
    for t in ("rz_activity", "rz_activity_hour", "rz_member_events", "rz_stats_meta"):
        await RT.db.run(f"DELETE FROM {t} WHERE chat_id=?", (chat_id,))
    _since.discard(chat_id)


# periods
PERIODS = {"today": 1, "امروز": 1, "day": 1, "روز": 1, "week": 7, "هفته": 7, "month": 30, "ماه": 30}


def parse_period(args: list[str], default: int = 7) -> int:
    for a in args:
        a = norm_digits(a).lower()
        if a in PERIODS:
            return PERIODS[a]
        if a.rstrip("dروز").isdigit():
            return max(1, min(90, int(a.rstrip("dروز"))))
    return default


def period_label(c: Cmd, days: int) -> str:
    if days == 1:
        return c.t("امروز", "today")
    return c.t(f"{num(days, 'fa')} روز اخیر", f"last {days} days")


def first_day(days: int, tz: str) -> str:
    d = datetime.now(_tz(tz)) - timedelta(days=days - 1)
    return d.strftime("%Y-%m-%d")


async def coverage(c: Cmd) -> str:
    since = await RT.db.val("SELECT since FROM rz_stats_meta WHERE chat_id=?", (c.chat_id,))
    if not since:
        return c.t("هنوز چیزی ثبت نشده.", "Nothing recorded yet.")
    return c.t(f"از {fmt_dt(since, c.tz, 'fa', with_tz=False)} · فقط پیام‌هایی که ربات دیده",
               f"since {fmt_dt(since, c.tz, 'en', with_tz=False)} · only messages the bot saw")


async def names(ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await RT.db.all(f"SELECT user_id, first_name, last_name, username FROM rz_users WHERE user_id IN ({','.join('?' * len(ids))})",
                           tuple(ids))
    out = {}
    for r in rows:
        nm = " ".join(x for x in (r["first_name"], r["last_name"]) if x) or ("@" + r["username"] if r["username"] else str(r["user_id"]))
        out[r["user_id"]] = short(nm, 32)
    return out


def link(uid: int, nm: str) -> str:
    return f'<a href="tg://user?id={uid}">{esc(nm)}</a>'


async def summary(chat_id: int, days: int, tz: str) -> dict:
    await flush()
    d0 = first_day(days, tz)
    tot = await RT.db.one("SELECT COALESCE(SUM(msgs),0) AS n, COUNT(DISTINCT user_id) AS u, COALESCE(SUM(media),0) AS md "
                          "FROM rz_activity WHERE chat_id=? AND day>=?", (chat_id, d0))
    top = await RT.db.all("SELECT user_id, SUM(msgs) AS n FROM rz_activity WHERE chat_id=? AND day>=? GROUP BY user_id "
                          "ORDER BY n DESC LIMIT 20", (chat_id, d0))
    t0 = datetime.strptime(d0, "%Y-%m-%d").replace(tzinfo=_tz(tz)).timestamp()
    ev = {r["kind"]: r["n"] for r in await RT.db.all(
        "SELECT kind, COUNT(*) AS n FROM rz_member_events WHERE chat_id=? AND ts>=? GROUP BY kind", (chat_id, t0))}
    return {"messages": tot["n"], "active": tot["u"], "media": tot["md"], "top": [(r["user_id"], r["n"]) for r in top],
            "joins": ev.get("join", 0), "leaves": ev.get("leave", 0) + ev.get("kick", 0), "since_ts": t0}


async def stats_text(c: Cmd, days: int) -> str:
    s = await summary(c.chat_id, days, c.tz)
    nm = await names([u for u, _ in s["top"][:3]])
    top = "، ".join(f"{link(u, nm.get(u, str(u)))} ({num(n, c.lang)})" for u, n in s["top"][:3]) or "-"
    net = s["joins"] - s["leaves"]
    return c.t(
        f"📊 <b>آمار {period_label(c, days)}</b>\nپیام: {num(s['messages'], 'fa')} · اعضای فعال: {num(s['active'], 'fa')} · رسانه: {num(s['media'], 'fa')}\n"
        f"ورود: {num(s['joins'], 'fa')} · خروج: {num(s['leaves'], 'fa')} · خالص: {num(net, 'fa')}\nفعال‌ترین: {top}\n<i>{await coverage(c)}</i>",
        f"📊 <b>Stats, {period_label(c, days)}</b>\nMessages: {s['messages']} · Active members: {s['active']} · Media: {s['media']}\n"
        f"Joined: {s['joins']} · Left: {s['leaves']} · Net: {net}\nMost active: {top}\n<i>{await coverage(c)}</i>")


async def _can_view(c: Cmd) -> bool:
    if await S.get(c.chat_id, "stats_public"):
        return True
    from pilot import authz
    return bool(c.anon) or (c.actor_id in await authz.admin_ids(c.chat_id))


async def _off_note(c: Cmd) -> str | None:
    if not await _enabled(c.chat_id):
        return c.t("آمار خاموشه. <code>/statsmode on</code>", "Stats are off. <code>/statsmode on</code>")
    return None


@command("stats", "activity", admin=False, section="stats")
async def k_stats(c: Cmd):
    if not await _can_view(c):
        return
    off = await _off_note(c)
    if off:
        return await say(c, off)
    await say(c, await stats_text(c, parse_period(c.args, 1 if not c.args else 7)))


@command("top", "topactive", admin=False, section="stats")
async def k_top(c: Cmd):
    if not await _can_view(c):
        return
    off = await _off_note(c)
    if off:
        return await say(c, off)
    days = parse_period(c.args, 7)
    s = await summary(c.chat_id, days, c.tz)
    if not s["top"]:
        return await say(c, c.t("هنوز پیامی شمرده نشده.", "No messages counted yet."))
    nm = await names([u for u, _ in s["top"][:10]])
    lines = [f"{num(i + 1, c.lang)}. {link(u, nm.get(u, str(u)))} — {num(n, c.lang)}" for i, (u, n) in enumerate(s["top"][:10])]
    await say(c, c.t(f"🏆 <b>فعال‌ترین‌ها، {period_label(c, days)}</b>\n", f"🏆 <b>Most active, {period_label(c, days)}</b>\n")
              + "\n".join(lines) + f"\n<i>{await coverage(c)}</i>")


async def user_activity(chat_id: int, user_id: int, tz: str) -> dict:
    await flush()
    out = {}
    for days in (1, 7, 30):
        out[days] = await RT.db.val("SELECT COALESCE(SUM(msgs),0) FROM rz_activity WHERE chat_id=? AND user_id=? AND day>=?",
                                    (chat_id, user_id, first_day(days, tz)), 0)
    rank_rows = await RT.db.all("SELECT user_id, SUM(msgs) AS n FROM rz_activity WHERE chat_id=? AND day>=? GROUP BY user_id "
                                "ORDER BY n DESC", (chat_id, first_day(30, tz)))
    out["rank"] = next((i + 1 for i, r in enumerate(rank_rows) if r["user_id"] == user_id), None)
    out["ranked"] = len(rank_rows)
    uc = await RT.db.one("SELECT first_seen, last_seen FROM rz_user_chats WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    out["first_seen"], out["last_seen"] = (uc or {}).get("first_seen"), (uc or {}).get("last_seen")
    return out


async def _user_card(c: Cmd, uid: int, title: str) -> str:
    a = await user_activity(c.chat_id, uid, c.tz)
    rank = c.t(f"رتبهٔ ۳۰ روز: {num(a['rank'], 'fa')} از {num(a['ranked'], 'fa')}", f"30-day rank: {a['rank']} of {a['ranked']}") if a["rank"] else ""
    first = fmt_dt(a["first_seen"], c.tz, c.lang, with_tz=False) if a["first_seen"] else "-"
    return c.t(f"📈 <b>{title}</b>\nامروز: {num(a[1], 'fa')} · ۷ روز: {num(a[7], 'fa')} · ۳۰ روز: {num(a[30], 'fa')}\n{rank}\n"
               f"اولین بار دیده شده: {first}\n<i>{await coverage(c)}</i>",
               f"📈 <b>{title}</b>\nToday: {a[1]} · 7 days: {a[7]} · 30 days: {a[30]}\n{rank}\nFirst seen: {first}\n<i>{await coverage(c)}</i>")


@command("mystats", admin=False, section="stats")
async def k_mystats(c: Cmd):
    if not c.actor_id:
        return
    off = await _off_note(c)
    if off:
        return await say(c, off)
    await say(c, await _user_card(c, c.actor_id, c.t("آمار شما", "Your stats")))


@command("userstats", right=None, section="stats")
async def k_userstats(c: Cmd):
    t = await resolve(c)
    if not t.ok or not t.user_id:
        return await say(c, t.err or c.t("کاربر؟", "Which user?"))
    await say(c, await _user_card(c, t.user_id, t.label()))


@command("hours", "busyhours", admin=False, section="stats")
async def k_hours(c: Cmd):
    if not await _can_view(c):
        return
    await flush()
    days = parse_period(c.args, 7)
    rows = await RT.db.all("SELECT hour, SUM(msgs) AS n FROM rz_activity_hour WHERE chat_id=? AND day>=? GROUP BY hour",
                           (c.chat_id, first_day(days, c.tz)))
    by = {r["hour"]: r["n"] for r in rows}
    if not by:
        return await say(c, c.t("هنوز چیزی ثبت نشده.", "Nothing recorded yet."))
    mx = max(by.values())
    lines = []
    for h in range(24):
        n = by.get(h, 0)
        if n:
            lines.append(f"<code>{h:02d}</code> {'█' * max(1, round(n / mx * 12))} {num(n, c.lang)}")
    best = max(by, key=by.get)
    await say(c, c.t(f"🕒 <b>ساعت‌های شلوغ، {period_label(c, days)}</b>\n", f"🕒 <b>Busy hours, {period_label(c, days)}</b>\n")
              + "\n".join(lines) + c.t(f"\nشلوغ‌ترین: ساعت {num(best, 'fa')} ({c.tz})", f"\nBusiest: {best}:00 ({c.tz})"))


@command("joins", "growth", right=None, section="stats")
async def k_joins(c: Cmd):
    days = parse_period(c.args, 7)
    t0 = datetime.strptime(first_day(days, c.tz), "%Y-%m-%d").replace(tzinfo=_tz(c.tz)).timestamp()
    rows = await RT.db.all("SELECT ts, kind FROM rz_member_events WHERE chat_id=? AND ts>=?", (c.chat_id, t0))
    per: dict[str, list[int]] = {}
    for r in rows:
        d, _ = local_day(r["ts"], c.tz)
        per.setdefault(d, [0, 0])[0 if r["kind"] == "join" else 1] += 1
    if not per:
        return await say(c, c.t("در این بازه ورود یا خروجی دیده نشد.", "No joins or leaves seen in this period."))
    lines = [f"<code>{d}</code> +{num(j, c.lang)} / −{num(lv, c.lang)}" for d, (j, lv) in sorted(per.items())]
    tj, tl = sum(v[0] for v in per.values()), sum(v[1] for v in per.values())
    await say(c, c.t(f"📅 <b>ورود و خروج، {period_label(c, days)}</b>\n", f"📅 <b>Joins and leaves, {period_label(c, days)}</b>\n")
              + "\n".join(lines[-31:]) + c.t(f"\nجمع: +{num(tj, 'fa')} / −{num(tl, 'fa')}", f"\nTotal: +{tj} / −{tl}")
              + f"\n<i>{await coverage(c)}</i>")


async def _event_list(c: Cmd, kind_sql: str, title_fa: str, title_en: str) -> None:
    days = parse_period(c.args, 7)
    rows = await RT.db.all(f"SELECT user_id, MAX(ts) AS ts FROM rz_member_events WHERE chat_id=? AND ts>=? AND {kind_sql} "
                           "GROUP BY user_id ORDER BY ts DESC LIMIT 30", (c.chat_id, time.time() - days * 86400))
    if not rows:
        return await say(c, c.t("موردی دیده نشد.", "None seen."))
    nm = await names([r["user_id"] for r in rows])
    lines = [f"• {link(r['user_id'], nm.get(r['user_id'], str(r['user_id'])))} · {fmt_dt(r['ts'], c.tz, c.lang, with_tz=False)}" for r in rows]
    await say(c, f"<b>{c.t(title_fa, title_en)}</b> ({period_label(c, days)})\n" + "\n".join(lines))


@command("newmembers", right=None, section="stats")
async def k_newmembers(c: Cmd):
    await _event_list(c, "kind='join'", "🆕 اعضای جدید", "🆕 New members")


@command("leavers", right=None, section="stats")
async def k_leavers(c: Cmd):
    await _event_list(c, "kind IN ('leave','kick')", "🚪 رفته‌ها", "🚪 Left")


@command("inactive", right=None, section="stats")
async def k_inactive(c: Cmd):
    days = 14
    for a in c.args:
        if norm_digits(a).isdigit():
            days = max(1, min(365, int(norm_digits(a))))
    rows = await RT.db.all("SELECT user_id, last_seen FROM rz_user_chats WHERE chat_id=? AND last_seen<? "
                           "AND COALESCE(status,'member') IN ('member','restricted','administrator') ORDER BY last_seen LIMIT 40",
                           (c.chat_id, time.time() - days * 86400))
    total = await RT.db.val("SELECT COUNT(*) FROM rz_user_chats WHERE chat_id=? AND last_seen<? "
                            "AND COALESCE(status,'member') IN ('member','restricted','administrator')", (c.chat_id, time.time() - days * 86400), 0)
    if not rows:
        return await say(c, c.t(f"عضو شناخته‌شده‌ای با بیش از {num(days, 'fa')} روز سکوت پیدا نشد.", f"No known member silent for over {days} days."))
    nm = await names([r["user_id"] for r in rows])
    lines = [f"• {link(r['user_id'], nm.get(r['user_id'], str(r['user_id'])))} · {fmt_dt(r['last_seen'], c.tz, c.lang, with_tz=False)}" for r in rows]
    await say(c, c.t(f"💤 <b>بیش از {num(days, 'fa')} روز بی‌صدا</b> ({num(total, 'fa')} نفر)\n", f"💤 <b>Silent for over {days} days</b> ({total})\n")
              + "\n".join(lines) + c.t("\n<i>فقط اعضایی که ربات قبلاً دیده؛ لیست کامل اعضا از تلگرام قابل دریافت نیست.</i>",
                                       "\n<i>Only members the bot has seen; Telegram doesn't give bots the full member list.</i>"))


@command("topadders", right=None, section="stats")
async def k_topadders(c: Cmd):
    days = parse_period(c.args, 30)
    rows = await RT.db.all("SELECT by_user, COUNT(DISTINCT user_id) AS n FROM rz_member_events WHERE chat_id=? AND kind='join' "
                           "AND by_user IS NOT NULL AND ts>=? GROUP BY by_user ORDER BY n DESC LIMIT 15",
                           (c.chat_id, time.time() - days * 86400))
    if not rows:
        return await say(c, c.t("هیچ افزودن مستقیمی دیده نشد.", "No direct adds seen."))
    nm = await names([r["by_user"] for r in rows])
    lines = [f"{num(i + 1, c.lang)}. {link(r['by_user'], nm.get(r['by_user'], str(r['by_user'])))} — {num(r['n'], c.lang)}" for i, r in enumerate(rows)]
    await say(c, c.t(f"➕ <b>افزودن مستقیم عضو، {period_label(c, days)}</b>\n", f"➕ <b>Direct adds, {period_label(c, days)}</b>\n")
              + "\n".join(lines) + c.t("\n<i>فقط وقتی که تلگرام اضافه‌کننده را اعلام کرده. ورود با لینک: /topinviters</i>",
                                       "\n<i>Only when Telegram reported who added them. Link joins: /topinviters</i>"))


@command("cohort", "retentionstats", right=None, section="stats")
async def k_cohort(c: Cmd):
    days = parse_period(c.args, 30)
    joined = await RT.db.all("SELECT DISTINCT user_id FROM rz_member_events WHERE chat_id=? AND kind='join' AND ts>=?",
                             (c.chat_id, time.time() - days * 86400))
    if not joined:
        return await say(c, c.t("در این بازه ورودی دیده نشد.", "No joins seen in this period."))
    ids = [r["user_id"] for r in joined]
    gone = 0
    for uid in ids:
        last = await RT.db.val("SELECT kind FROM rz_member_events WHERE chat_id=? AND user_id=? ORDER BY ts DESC, id DESC LIMIT 1",
                               (c.chat_id, uid))
        gone += last in ("leave", "kick")
    stay = len(ids) - gone
    pct = round(stay * 100 / len(ids))
    await say(c, c.t(f"🧲 <b>ماندگاری، {period_label(c, days)}</b>\nوارد شدند: {num(len(ids), 'fa')} · ماندند: {num(stay, 'fa')} · رفتند: {num(gone, 'fa')} · {num(pct, 'fa')}٪",
                     f"🧲 <b>Retention, {period_label(c, days)}</b>\nJoined: {len(ids)} · Stayed: {stay} · Left: {gone} · {pct}%")
              + f"\n<i>{await coverage(c)}</i>")


@command("statsmode", right="info", section="stats")
async def k_statsmode(c: Cmd):
    v = onoff(c)
    if v is None:
        on = await _enabled(c.chat_id)
        return await say(c, c.t(f"آمار فعالیت: {'روشن' if on else 'خاموش'} (فقط شمارش، متن پیام ذخیره نمی‌شود) · <code>/statsmode on|off</code>",
                                f"Activity stats: {'on' if on else 'off'} (counts only, no message text) · <code>/statsmode on|off</code>"))
    txt = await set_setting(c, "stats_on", v)
    if v is False:
        await clear(c.chat_id)
        txt += c.t("\nآمار ذخیره‌شده پاک شد.", "\nStored stats deleted.")
    await say(c, txt)


# scheduled report
@command("statsreport", right="info", section="stats")
async def k_statsreport(c: Cmd):
    a = [norm_digits(x).lower() for x in c.args]
    if not a:
        r = await RT.db.one("SELECT report, report_at, report_dest FROM rz_stats_meta WHERE chat_id=?", (c.chat_id,))
        cur = f"{r['report']} {r['report_at']} → {r['report_dest']}" if r and r["report"] else c.t("خاموش", "off")
        return await say(c, c.t(f"گزارش خودکار آمار: {cur}\n<code>/statsreport daily 22:00</code> · <code>/statsreport weekly 22:00</code> · "
                                f"<code>/statsreport daily 22:00 log</code> · <code>/statsreport off</code>",
                                f"Automatic stats report: {cur}\n<code>/statsreport daily 22:00</code> · <code>/statsreport weekly 22:00</code> · "
                                f"<code>/statsreport daily 22:00 log</code> · <code>/statsreport off</code>"))
    if a[0] in ("off", "خاموش"):
        await RT.db.run("UPDATE rz_stats_meta SET report=NULL WHERE chat_id=?", (c.chat_id,))
        return await say(c, c.t("✅ گزارش خودکار خاموش شد.", "✅ Automatic report off."))
    kind = {"daily": "daily", "روزانه": "daily", "weekly": "weekly", "هفتگی": "weekly"}.get(a[0])
    hm = parse_hhmm(a[1]) if len(a) > 1 else (22, 0)
    if not kind or not hm:
        return await say(c, c.t("<code>/statsreport daily 22:00</code>", "<code>/statsreport daily 22:00</code>"))
    dest = "log" if "log" in a or "لاگ" in a else "group"
    await RT.db.run("INSERT INTO rz_stats_meta(chat_id, since, report, report_at, report_dest) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET report=excluded.report, report_at=excluded.report_at, report_dest=excluded.report_dest",
                    (c.chat_id, time.time(), kind, f"{hm[0]:02d}:{hm[1]:02d}", dest))
    await say(c, c.t(f"✅ گزارش {'روزانه' if kind == 'daily' else 'هفتگی'} ساعت {hm[0]:02d}:{hm[1]:02d} → {'لاگ' if dest == 'log' else 'گروه'}",
                     f"✅ {kind} report at {hm[0]:02d}:{hm[1]:02d} → {dest}"))


@hooks.periodic("stats_reports", 60)
async def reports_due() -> None:
    from pilot import logs
    rows = await RT.db.all("SELECT * FROM rz_stats_meta WHERE report IS NOT NULL")
    for r in rows:
        lang, tz = await lang_tz(r["chat_id"])
        now_l = datetime.now(_tz(tz))
        h, m = map(int, r["report_at"].split(":"))
        if (now_l.hour, now_l.minute) < (h, m):
            continue
        if r["report"] == "weekly" and now_l.weekday() != 4:          # Friday
            continue
        key = now_l.strftime("%Y-%m-%d")
        if r["last_key"] == key:
            continue
        await RT.db.run("UPDATE rz_stats_meta SET last_key=? WHERE chat_id=?", (key, r["chat_id"]))
        c = Cmd(chat_id=r["chat_id"], chat_type="supergroup", lang=lang, tz=tz, source="auto")
        txt = await stats_text(c, 1 if r["report"] == "daily" else 7)
        if r["report_dest"] == "log":
            await logs.emit(r["chat_id"], txt)
        else:
            await tg.send(r["chat_id"], txt, silent=True)


def jalali_label(ts: float, tz: str) -> str:
    d = datetime.fromtimestamp(ts, _tz(tz))
    y, m, dd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{y}/{m:02d}/{dd:02d}"
