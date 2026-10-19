"""Owner console (/panel): runtime health, observed groups and users, usage, AI accounting, logs, errors, updates.

Only the configured OWNER_ID may use it, and only in the bot's private chat. That is checked on every entry
point: the command, the Persian phrase, every button press and every confirmation. Hiding buttons is not
security. Views show counts and ids, never message bodies or credentials. Telegram cannot list every chat a
bot belongs to or every user it has met, so everything here is "observed by this installation" and labeled so.
"""
from __future__ import annotations

import os
import platform
import time
from collections import Counter

from pilot import __version__, audit, authz, confirm, edition, hooks, logs, metrics, settings as S, tg
from pilot.commands import command
from pilot.config import redact
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.util import esc, fmt_dt, fmt_duration

GROUPS_PAGE = 8


def allowed(user_id: int | None, chat_id: int) -> bool:
    return authz.is_owner(user_id) and chat_id == user_id


def _n(v, fa: bool) -> str:
    s = f"{v:,}" if isinstance(v, int) else str(v)
    return s.translate(str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬")) if fa else s


def _ago(ts: float | None, fa: bool) -> str:
    if not ts:
        return "—"
    return fmt_duration(max(0, int(time.time() - ts)), "fa" if fa else "en") + (" پیش" if fa else " ago")


def _uptime(fa: bool) -> str:
    s = int(time.time() - RT.started_at)
    if s < 60:                                   # fmt_duration(0) means "permanent"
        return "کمتر از یک دقیقه" if fa else "under a minute"
    return fmt_duration(s, "fa" if fa else "en")


def _ai_state(fa: bool) -> str:
    st = RT.ai.status if RT.ai else "off"
    if not fa:
        return st
    kind = st[st.find("(") + 1:st.rfind(")")] if "(" in st else ""
    base = {"ok": "فعال", "off": "خاموش", "unknown": "هنوز استفاده نشده", "not configured": "تنظیم نشده"}.get(st)
    return base or ("ناپایدار" + (f" ({kind})" if kind else ""))


NAV = [("home", "خانه", "Home"), ("groups", "گروه‌ها", "Groups"), ("users", "کاربران", "Users"), ("usage", "مصرف", "Usage"),
       ("ai", "هوش مصنوعی", "AI"), ("logs", "لاگ‌ها", "Logs"), ("errors", "خطاها", "Errors"), ("update", "آپدیت", "Updates")]


def nav_rows(cur: str, fa: bool) -> list:
    btns = [(("• " if k == cur else "") + (f if fa else e), f"op:{k}:0") for k, f, e in NAV]
    return [btns[i:i + 4] for i in range(0, len(btns), 4)]


async def v_home(fa: bool) -> tuple[str, list]:
    t = time.time()
    import telegram
    db_size = os.path.getsize(RT.db.path) if RT.db and os.path.exists(RT.db.path) else 0
    schema = await RT.db.val("SELECT v FROM rz_meta WHERE k='schema_version'", (), "?")
    active = await RT.db.val("SELECT COUNT(*) FROM rz_chats WHERE type IN ('group','supergroup') AND bot_status IN ('member','administrator') "
                             "AND left_at IS NULL AND migrated_to IS NULL", (), 0)
    errs = await RT.db.val("SELECT COUNT(*) FROM rz_errors WHERE ts>?", (t - 86400,), 0)
    pend = await RT.db.val("SELECT COUNT(*) FROM rz_log_queue WHERE status='pending'", (), 0)
    held = await RT.db.val("SELECT COUNT(*) FROM rz_log_queue WHERE status='held'", (), 0)
    broken = await RT.db.val("SELECT COUNT(*) FROM rz_log_routes WHERE status='broken'", (), 0)
    last_tick = RT.tick_stats.get("last") or 0
    lat = metrics.percentile(RT.tick_stats.get("cmd_lat", []), 95)
    ai = RT.ai.status if RT.ai else "off"
    from pilot import updater
    up = await updater.status_line(fa)
    if fa:
        L = [f"<b>پنل مالک · RayZen Pilot {esc(__version__)}</b>",
             f"نسخه: {esc(edition.label('fa'))} · بیلد <code>{esc(edition.BUILD_ID)}</code>" + (f" · <code>{esc(edition.COMMIT[:10])}</code>" if edition.COMMIT else ""),
             f"مدت روشن بودن: {_uptime(True)}",
             f"پایتون {esc(platform.python_version())} · python-telegram-bot {esc(telegram.__version__)}",
             f"پایگاه داده: نسخهٔ {_n(schema, True)} · {_n(round(db_size / 1048576, 1), True)} مگابایت",
             f"زمان‌بند: آخرین اجرا {_ago(last_tick, True)} · خطا {_n(RT.tick_stats.get('errors', 0), True)}",
             f"گروه‌های فعال: {_n(active, True)} · خطای ۲۴ ساعت: {_n(errs, True)}",
             f"صف لاگ: {_n(pend, True)} در انتظار · {_n(held, True)} نگه‌داشته · مسیر قطع: {_n(broken, True)}",
             f"هوش مصنوعی: {esc(_ai_state(True))} · Model hidden",
             f"پاسخ دستورها (p95): " + (f"{_n(round(lat * 1000), True)} میلی‌ثانیه" if lat is not None else "—"),
             f"آپدیت: {up}"]
    else:
        L = [f"<b>Owner panel · RayZen Pilot {esc(__version__)}</b>",
             f"Edition: {esc(edition.label('en'))} · build <code>{esc(edition.BUILD_ID)}</code>" + (f" · <code>{esc(edition.COMMIT[:10])}</code>" if edition.COMMIT else ""),
             f"Uptime: {_uptime(False)}",
             f"Python {esc(platform.python_version())} · python-telegram-bot {esc(telegram.__version__)}",
             f"Database: schema {schema} · {round(db_size / 1048576, 1)} MB",
             f"Scheduler: last run {_ago(last_tick, False)} · errors {RT.tick_stats.get('errors', 0)}",
             f"Active groups: {active} · errors 24h: {errs}",
             f"Log queue: {pend} pending · {held} held · broken routes: {broken}",
             f"AI: {esc(ai)} · Model hidden",
             "Command handling p95: " + (f"{round(lat * 1000)} ms" if lat is not None else "—"),
             f"Updates: {up}"]
    return "\n".join(L), nav_rows("home", fa) + [[("↻ " + ("تازه کردن" if fa else "Refresh"), "op:home:0")]]


def _chat_state(r: dict) -> str:
    if r.get("migrated_to"):
        return "migrated"
    if r.get("bot_status") in ("left", "kicked") or r.get("left_at"):
        return "removed"
    if r.get("bot_status") in ("member", "administrator"):
        return "active"
    return "unknown"


STATE_FA = {"active": "فعال", "removed": "حذف شده", "unknown": "نامشخص", "migrated": "منتقل شده"}


async def v_groups(fa: bool, page: int) -> tuple[str, list]:
    rows = await RT.db.all("SELECT c.chat_id, c.title, c.bot_status, c.left_at, c.migrated_to, "
                           "(SELECT MAX(last_seen) FROM rz_user_chats u WHERE u.chat_id=c.chat_id) AS last_act "
                           "FROM rz_chats c WHERE c.type IN ('group','supergroup') ORDER BY last_act IS NULL, last_act DESC, c.chat_id")
    states = Counter(_chat_state(r) for r in rows)
    pages = max(1, (len(rows) + GROUPS_PAGE - 1) // GROUPS_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = rows[page * GROUPS_PAGE:(page + 1) * GROUPS_PAGE]
    if fa:
        head = (f"<b>گروه‌های دیده‌شده</b> · {_n(len(rows), True)}\n"
                f"فعال {_n(states['active'], True)} · حذف شده {_n(states['removed'], True)} · نامشخص {_n(states['unknown'], True)}"
                + (f" · منتقل شده {_n(states['migrated'], True)}" if states["migrated"] else "") +
                "\n<i>فقط گروه‌هایی که این نصب دیده است؛ تلگرام فهرست کامل نمی‌دهد.</i>")
    else:
        head = (f"<b>Observed groups</b> · {len(rows)}\nactive {states['active']} · removed {states['removed']} · unknown {states['unknown']}"
                + (f" · migrated {states['migrated']}" if states["migrated"] else "") +
                "\n<i>Only groups this installation has seen; Telegram gives no complete list.</i>")
    lines, btns = [], []
    for i, r in enumerate(chunk, start=page * GROUPS_PAGE + 1):
        st = _chat_state(r)
        lines.append(f"{i}. {esc((r['title'] or '—')[:40])} · <code>{r['chat_id']}</code> · {STATE_FA[st] if fa else st} · "
                     f"{('آخرین فعالیت ' if fa else 'last activity ') + _ago(r['last_act'], fa)}")
        btns.append((str(i), f"op:g:{r['chat_id']}"))
    nav = []
    if page > 0:
        nav.append(("‹", f"op:groups:{page - 1}"))
    if page < pages - 1:
        nav.append(("›", f"op:groups:{page + 1}"))
    kb = [btns[i:i + 4] for i in range(0, len(btns), 4)] + ([nav] if nav else []) + nav_rows("groups", fa)
    return head + "\n\n" + ("\n".join(lines) or "—"), kb


async def v_group(fa: bool, chat_id: int) -> tuple[str, list]:
    r = await RT.db.one("SELECT * FROM rz_chats WHERE chat_id=?", (chat_id,))
    if not r:
        return ("پیدا نشد." if fa else "Not found."), nav_rows("groups", fa)
    st = _chat_state(r)
    on = [S.label(k, "fa" if fa else "en") for k, _ in await S.enabled_filters(chat_id)]
    route = await logs.route(chat_id)
    seen = await RT.db.val("SELECT COUNT(*) FROM rz_user_chats WHERE chat_id=?", (chat_id,), 0)
    last = await RT.db.val("SELECT MAX(last_seen) FROM rz_user_chats WHERE chat_id=?", (chat_id,))
    locks_n = await RT.db.val("SELECT COUNT(*) FROM rz_lock_layers WHERE chat_id=?", (chat_id,), 0)
    night = await RT.db.one("SELECT start,end FROM rz_night WHERE chat_id=? AND enabled=1", (chat_id,))
    acts = await RT.db.val("SELECT COUNT(*) FROM rz_actions WHERE chat_id=? AND ts>?", (chat_id, time.time() - 7 * 86400), 0)
    lang = await S.get(chat_id, "lang")
    if fa:
        L = [f"<b>{esc(r['title'] or '—')}</b> · <code>{chat_id}</code>",
             f"وضعیت: {STATE_FA[st]} · نقش ربات: {esc(r['bot_status'] or '—')}" + (" · انجمن" if r["is_forum"] else ""),
             f"اضافه شدن: {fmt_dt(r['added_at'], RT.cfg.tz, 'fa') if r['added_at'] else '—'}",
             f"اعضای دیده‌شده: {_n(seen, True)} · آخرین فعالیت: {_ago(last, True)}",
             f"فیلترهای روشن: {esc('، '.join(on)) if on else 'هیچ'}",
             f"قفل فعال: {_n(locks_n, True)} · قفل شبانه: {esc(night['start'] + '–' + night['end']) if night else 'خاموش'}",
             f"لاگ: {logs.status_label(route['status'], 'fa') if route else 'تنظیم نشده'}"
             + (f" · {esc((route.get('last_error') or '')[:80])}" if route and route["status"] == "broken" else ""),
             f"اقدام‌های ۷ روز: {_n(acts, True)} · زبان: {esc(lang)}"]
        if r["migrated_to"]:
            L.append(f"منتقل شده به <code>{r['migrated_to']}</code>")
    else:
        L = [f"<b>{esc(r['title'] or '—')}</b> · <code>{chat_id}</code>",
             f"State: {st} · bot role: {esc(r['bot_status'] or '—')}" + (" · forum" if r["is_forum"] else ""),
             f"Added: {fmt_dt(r['added_at'], RT.cfg.tz, 'en') if r['added_at'] else '—'}",
             f"Observed members: {seen} · last activity: {_ago(last, False)}",
             f"Filters on: {esc(', '.join(on)) if on else 'none'}",
             f"Active locks: {locks_n} · night lock: {esc(night['start'] + '–' + night['end']) if night else 'off'}",
             f"Logs: {logs.status_label(route['status'], 'en') if route else 'not set'}"
             + (f" · {esc((route.get('last_error') or '')[:80])}" if route and route["status"] == "broken" else ""),
             f"Actions 7d: {acts} · language: {esc(lang)}"]
        if r["migrated_to"]:
            L.append(f"Migrated to <code>{r['migrated_to']}</code>")
    kb = []
    if route and route["status"] in ("broken", "active"):
        kb.append([("🔧 " + ("آزمایش دوبارهٔ لاگ" if fa else "Retest log route"), f"op:lt:{chat_id}")])
    if st == "active":
        kb.append([("خروج ربات از گروه" if fa else "Leave this group", f"op:lv:{chat_id}")])
    kb.append([("◀️ " + ("گروه‌ها" if fa else "Groups"), "op:groups:0")])
    return "\n".join(L), kb


async def v_users(fa: bool) -> tuple[str, list]:
    t = time.time()
    total = await RT.db.val("SELECT COUNT(*) FROM rz_users WHERE COALESCE(is_bot,0)=0", (), 0)
    bots = await RT.db.val("SELECT COUNT(*) FROM rz_users WHERE is_bot=1", (), 0)
    act = {}
    for d in (1, 7, 30):
        act[d] = await RT.db.val("SELECT COUNT(*) FROM rz_users WHERE COALESCE(is_bot,0)=0 AND last_seen>?", (t - d * 86400,), 0)
    dm = await RT.db.val("SELECT COUNT(*) FROM rz_users WHERE dm_ok=1", (), 0)
    memberships = await RT.db.val("SELECT COUNT(*) FROM rz_user_chats", (), 0)
    first = await RT.db.val("SELECT MIN(first_seen) FROM rz_user_chats", ())
    cmd_users = await RT.db.val("SELECT COUNT(DISTINCT actor_id) FROM rz_actions WHERE ts>? AND actor_id IS NOT NULL", (t - 30 * 86400,), 0)
    if fa:
        L = ["<b>کاربران دیده‌شده</b>",
             f"کل: {_n(total, True)} (و {_n(bots, True)} ربات)",
             f"فعال ۲۴ ساعت: {_n(act[1], True)} · ۷ روز: {_n(act[7], True)} · ۳۰ روز: {_n(act[30], True)}",
             f"پیوی ربات را باز کرده‌اند: {_n(dm, True)}",
             f"عضویت‌های دیده‌شده (کاربر×گروه): {_n(memberships, True)}",
             f"مدیرانی که در ۳۰ روز اقدامی ثبت کرده‌اند: {_n(cmd_users, True)}",
             f"شروع ثبت: {fmt_dt(first, RT.cfg.tz, 'fa') if first else '—'}",
             "<i>«دیده‌شده» یعنی ربات دست‌کم یک پیام، دکمه یا رویداد عضویت از او دریافت کرده. این فهرست کامل اعضا نیست.</i>"]
    else:
        L = ["<b>Observed users</b>", f"Total: {total} (plus {bots} bots)",
             f"Active 24h: {act[1]} · 7d: {act[7]} · 30d: {act[30]}", f"Opened a private chat: {dm}",
             f"Observed memberships (user×group): {memberships}", f"Admins with recorded actions in 30d: {cmd_users}",
             f"Tracking since: {fmt_dt(first, RT.cfg.tz, 'en') if first else '—'}",
             "<i>Observed = the bot received at least one message, button press or membership event from them. Not a full roster.</i>"]
    return "\n".join(L), nav_rows("users", fa)


async def v_usage(fa: bool) -> tuple[str, list]:
    top = await metrics.top_usage(7, "cmd:", 15)
    src = dict(await metrics.top_usage(7, "src:", 10))
    total = sum(n for _, n in top)
    if fa:
        L = ["<b>مصرف دستورها · ۷ روز</b>",
             f"راه اجرا: فارسی {_n(src.get('fa', 0), True)} · اسلش {_n(src.get('cmd', 0), True)} · دکمه {_n(src.get('button', 0), True)} · دستیار {_n(src.get('ai', 0), True)}"]
    else:
        L = ["<b>Command usage · 7 days</b>",
             f"Entry: Persian text {src.get('fa', 0)} · slash {src.get('cmd', 0)} · button {src.get('button', 0)} · assistant {src.get('ai', 0)}"]
    for name, n in top:
        L.append(f"<code>/{esc(name)}</code> · {_n(n, fa)}")
    if not top:
        L.append("هنوز چیزی ثبت نشده." if fa else "Nothing recorded yet.")
    L.append(("<i>شمارش در حافظه جمع و هر دقیقه ذخیره می‌شود.</i>" if fa else "<i>Counted in memory and saved every minute.</i>")
             if total else "")
    return "\n".join(x for x in L if x), nav_rows("usage", fa)


def _price() -> tuple[float, float] | None:
    try:
        pin = float(os.environ.get("AI_PRICE_IN_PER_M", "") or "nan")
        pout = float(os.environ.get("AI_PRICE_OUT_PER_M", "") or "nan")
    except ValueError:
        return None
    if pin != pin or pout != pout:          # NaN: not configured
        return None
    return pin, pout


async def v_ai(fa: bool) -> tuple[str, list]:
    cfg = RT.cfg
    if not cfg.ai_configured:
        return ("هوش مصنوعی تنظیم نشده (AI_BASE_URL، AI_API_KEY، AI_MODEL)." if fa else
                "AI is not configured (AI_BASE_URL, AI_API_KEY, AI_MODEL)."), nav_rows("ai", fa)
    d1, d7 = await metrics.ai_summary(24), await metrics.ai_summary(24 * 7)
    order = {m: i for i, m in enumerate(cfg.models)}

    def models_line(s: dict) -> str:
        parts = []
        for m, n in s["models"].most_common():
            i = order.get(m)
            name = ("اصلی" if fa else "primary") if i == 0 else ((f"جایگزین {i}" if fa else f"fallback {i}") if i else ("دیگر" if fa else "other"))
            parts.append(f"{name}: {_n(n, fa)}")
        return " · ".join(parts) or "—"

    def block(s: dict, title: str) -> list[str]:
        fails = " · ".join(f"{esc(k)} {_n(v, fa)}" for k, v in s["failed"].most_common(6)) or "—"
        retries = max(0, s["attempts"] - s["calls"])
        p50 = f"{_n(int(s['p50']), fa)}" if s["p50"] else "—"
        p95 = f"{_n(int(s['p95']), fa)}" if s["p95"] else "—"
        tok = (f"{_n(s['prompt_tokens'], fa)} / {_n(s['completion_tokens'], fa)}")
        if fa:
            out = [f"<b>{title}</b>", f"درخواست: {_n(s['calls'], True)} · موفق {_n(s['ok'], True)} · تلاش اضافه {_n(retries, True)}",
                   f"خطاها: {fails}", f"تأخیر (میلی‌ثانیه): p50 {p50} · p95 {p95}",
                   f"توکن گزارش‌شده توسط سرویس (ورودی/خروجی): {tok} در {_n(s['with_usage'], True)} درخواست",
                   f"بدون گزارش مصرف: {_n(s['without_usage'], True)} درخواست", f"مدل‌ها: {models_line(s)}"]
        else:
            out = [f"<b>{title}</b>", f"Requests: {s['calls']} · ok {s['ok']} · extra attempts {retries}", f"Errors: {fails}",
                   f"Latency (ms): p50 {p50} · p95 {p95}",
                   f"Provider-reported tokens (in/out): {tok} over {s['with_usage']} requests",
                   f"No usage reported: {s['without_usage']} requests", f"Models: {models_line(s)}"]
        pr = _price()
        if pr and s["with_usage"]:
            cost = s["prompt_tokens"] / 1e6 * pr[0] + s["completion_tokens"] / 1e6 * pr[1]
            out.append((f"هزینهٔ تخمینی با قیمت تنظیم‌شده: {cost:.4f} (فقط درخواست‌های دارای گزارش)" if fa else
                        f"Estimated cost at configured price: {cost:.4f} (reported requests only)"))
        return out

    today = d1["calls"]
    L = block(d1, "۲۴ ساعت" if fa else "24 hours") + [""] + block(d7, "۷ روز" if fa else "7 days")
    if not _price():
        L.append("\n" + ("قیمت تنظیم نشده (AI_PRICE_IN_PER_M و AI_PRICE_OUT_PER_M)؛ هزینه نمایش داده نمی‌شود." if fa else
                         "No price configured (AI_PRICE_IN_PER_M, AI_PRICE_OUT_PER_M); cost is not shown."))
    L.append(("سقف‌ها: " if fa else "Limits: ") +
             (f"کاربر {_n(cfg.ai_user_per_10min, True)}/۱۰ دقیقه · گروه {_n(cfg.ai_group_per_hour, True)}/ساعت · کل {_n(today, True)}/{_n(cfg.ai_global_per_day, True)} در روز"
              if fa else f"user {cfg.ai_user_per_10min}/10min · group {cfg.ai_group_per_hour}/hour · global {today}/{cfg.ai_global_per_day} per day"))
    le = RT.ai.last_error if RT.ai else None
    if le:
        L.append(("آخرین خطا: " if fa else "Last error: ") + f"{esc(le[1])} · {_ago(le[0], fa)}")
    L.append("Model hidden")
    return "\n".join(L), nav_rows("ai", fa)


async def v_logs(fa: bool) -> tuple[str, list]:
    rows = await RT.db.all("SELECT r.chat_id, r.dest_id, r.status, r.last_error, r.fail_count, r.last_ok, c.title FROM rz_log_routes r "
                           "LEFT JOIN rz_chats c ON c.chat_id=r.chat_id WHERE r.status IN ('broken','active','unverified') "
                           "ORDER BY CASE r.status WHEN 'broken' THEN 0 ELSE 1 END, r.chat_id LIMIT 30")
    broken = [r for r in rows if r["status"] == "broken"]
    q = {r["status"]: r["n"] for r in await RT.db.all("SELECT status, COUNT(*) AS n FROM rz_log_queue GROUP BY status")}
    if fa:
        L = [f"<b>مسیرهای لاگ</b> · {_n(len(rows), True)} · قطع: {_n(len(broken), True)}",
             f"صف: ارسال‌شده {_n(q.get('sent', 0), True)} · در انتظار {_n(q.get('pending', 0), True)} · نگه‌داشته {_n(q.get('held', 0), True)} · رهاشده {_n(q.get('dropped', 0) + q.get('failed', 0), True)}"]
    else:
        L = [f"<b>Log routes</b> · {len(rows)} · broken: {len(broken)}",
             f"Queue: sent {q.get('sent', 0)} · pending {q.get('pending', 0)} · held {q.get('held', 0)} · dropped {q.get('dropped', 0) + q.get('failed', 0)}"]
    kb = []
    for r in broken[:8]:
        L.append(f"⚠️ {esc((r['title'] or '—')[:30])} <code>{r['chat_id']}</code> → <code>{r['dest_id']}</code> · "
                 f"{esc(redact((r['last_error'] or '')[:80]))} · ×{r['fail_count']}")
        kb.append([("🔧 " + (r["title"] or str(r["chat_id"]))[:28], f"op:lt:{r['chat_id']}")])
    if not broken:
        L.append("مسیر قطع‌شده‌ای نیست." if fa else "No broken routes.")
    return "\n".join(L), kb + nav_rows("logs", fa)


async def v_errors(fa: bool) -> tuple[str, list]:
    rows = await RT.db.all("SELECT chat_id, ts, area, detail FROM rz_errors ORDER BY id DESC LIMIT 12")
    by = await RT.db.all("SELECT area, COUNT(*) AS n FROM rz_errors WHERE ts>? GROUP BY area ORDER BY n DESC LIMIT 6", (time.time() - 86400,))
    L = ["<b>خطاهای اخیر</b>" if fa else "<b>Recent errors</b>"]
    if by:
        L.append(("۲۴ ساعت: " if fa else "24h: ") + " · ".join(f"{esc(r['area'])} {_n(r['n'], fa)}" for r in by))
    secrets = RT.cfg.secrets() if RT.cfg else []
    for r in rows:
        L.append(f"• {_ago(r['ts'], fa)} · {esc(r['area'])}" + (f" · <code>{r['chat_id']}</code>" if r["chat_id"] else "") +
                 f"\n  {esc(redact((r['detail'] or '')[:160], secrets))}")
    if not rows:
        L.append("خطایی ثبت نشده." if fa else "No errors recorded.")
    return "\n".join(L), nav_rows("errors", fa)


async def render(view: str, arg: str, fa: bool) -> tuple[str, list]:
    if view == "groups":
        return await v_groups(fa, int(arg) if arg.lstrip("-").isdigit() else 0)
    if view == "g" and arg.lstrip("-").isdigit():
        return await v_group(fa, int(arg))
    if view == "users":
        return await v_users(fa)
    if view == "usage":
        return await v_usage(fa)
    if view == "ai":
        return await v_ai(fa)
    if view == "logs":
        return await v_logs(fa)
    if view == "errors":
        return await v_errors(fa)
    if view == "update":
        from pilot import updater
        t, kb = await updater.panel_view(fa)
        return t, kb + nav_rows("update", fa)
    return await v_home(fa)


@command("panel", "owner", scope="private", admin=False, section="owner")
async def k_panel(c: Cmd):
    if not allowed(c.actor_id, c.chat_id):
        return await c.say(c.t("این بخش فقط برای مالک ربات است.", "Bot owner only."))
    txt, kb = await render("home", "", c.lang == "fa")
    await c.say(txt, rows=kb)


async def cb_panel(q, c: Cmd, p: list[str]) -> None:
    fa = c.lang == "fa"
    if not allowed(q.from_user.id, c.chat_id):
        return await tg.answer(q, "فقط مالک ربات." if fa else "Bot owner only.", alert=True)
    view = p[1] if len(p) > 1 else "home"
    arg = p[2] if len(p) > 2 else ""
    if view == "lt" and arg.lstrip("-").isdigit():
        ok, msg = await logs.retest(int(arg), "fa" if fa else "en")
        await tg.answer(q, msg[:190], alert=not ok)
        txt, kb = await v_group(fa, int(arg))
        await tg.edit(q.message, txt, kb)
        return
    if view == "lv" and arg.lstrip("-").isdigit():
        cid = int(arg)
        title = await RT.db.val("SELECT title FROM rz_chats WHERE chat_id=?", (cid,), str(cid))
        tok = await confirm.create(c.chat_id, q.from_user.id, "owner_leave", {"chat": cid}, ttl=120)
        await tg.answer(q)
        await c.say((f"ربات از «{esc(title)}» خارج شود؟ تنظیمات و سوابق می‌ماند." if fa else
                     f"Leave “{esc(title)}”? Settings and records are kept."), rows=confirm.buttons(tok, c.lang), reply=False)
        return
    if view.startswith("u_"):
        from pilot import updater
        await updater.on_panel_action(q, c, view, arg)
        return
    await tg.answer(q)
    txt, kb = await render(view, arg, fa)
    await tg.edit(q.message, txt, kb)


hooks.CALLBACKS["op"] = cb_panel


@confirm.handler("owner_leave")
async def _leave_ok(p: dict, presser: int, chat_id: int, message) -> str:
    if not allowed(presser, chat_id):
        return "⛔️"
    cid = int(p["chat"])
    out = await tg.call(RT.bot.leave_chat, idempotent=False, chat_id=cid)
    if not out.ok:
        return "❌ " + esc(out.desc)
    await RT.db.run("UPDATE rz_chats SET bot_status='left', left_at=? WHERE chat_id=?", (time.time(), cid))
    await audit.record(cid, presser, "owner", "owner_leave", params={"chat": cid})
    return f"✅ خارج شد: <code>{cid}</code>"
