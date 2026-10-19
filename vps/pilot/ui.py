"""Settings panel and help.

Buttons carry the target value (st:t:key:1), not a toggle, so an old button can't
flip a setting the wrong way."""
from __future__ import annotations

from pilot import authz, locks, logs, settings as S
from pilot.rt import RT
from pilot.util import esc, fa_digits, fmt_dt

# help
HELP: dict[str, tuple[str, str, str, str]] = {
    # key: (fa title, en title, fa body, en body)
    "mod": ("🛡 مدیریت کاربران", "🛡 Members", (
        "<code>/ban</code> · <code>/tban 2h</code> · <code>/unban</code> (ریپلای، آیدی عددی یا یوزرنیم)\n"
        "<code>/mute</code> · <code>/tmute 30m</code> · <code>/unmute</code> · <code>/kick</code> · <code>/kickme</code>\n"
        "<code>/warn</code> · <code>/warns</code> · <code>/rmwarn</code> · <code>/resetwarns</code>\n"
        "<code>/del</code> · <code>/purge</code> · <code>/pin</code> · <code>/unpin</code> · <code>/unpinall</code>\n"
        "<code>/purgeword کلمه</code>: پاک کردن پیام‌های ۴۸ ساعت اخیر که این کلمه را دارند (چند کلمه: <code>متین، matin</code>)\n"
        "<code>/promote</code> · <code>/demote</code> · <code>/admins</code> · <code>/info</code> · <code>/id</code> · <code>/report</code>\n"
        "با ریپلای: بن · سکوت ۲ ساعت · رفع سکوت · اخطار · اخراج"), (
        "<code>/ban</code> · <code>/tban 2h</code> · <code>/unban</code> (reply, numeric id or username)\n"
        "<code>/mute</code> · <code>/tmute 30m</code> · <code>/unmute</code> · <code>/kick</code> · <code>/kickme</code>\n"
        "<code>/warn</code> · <code>/warns</code> · <code>/rmwarn</code> · <code>/resetwarns</code>\n"
        "<code>/del</code> · <code>/purge</code> · <code>/pin</code> · <code>/unpin</code> · <code>/unpinall</code>\n"
        "<code>/purgeword word</code>: delete messages from the last 48h that contain the word (several: <code>matin, متین</code>)\n"
        "<code>/promote</code> · <code>/demote</code> · <code>/admins</code> · <code>/info</code> · <code>/id</code> · <code>/report</code>")),
    "locks": ("🔒 قفل و شب", "🔒 Locks & night", (
        "<code>/lock</code> [نوع] [مدت] · <code>/unlock</code> [نوع] · <code>/lockdown 1h</code> · <code>/lockstatus</code>\n"
        "انواع: all, media, photos, videos, documents, audio, voice, stickers, polls, previews, invite, pin, info, topics\n"
        "<code>/night 01:00 08:00</code> · <code>/night off</code>\n"
        "<code>/slowmode</code> · <code>/cooldown on 30</code>"), (
        "<code>/lock</code> [type] [duration] · <code>/unlock</code> [type] · <code>/lockdown 1h</code> · <code>/lockstatus</code>\n"
        "Types: all, media, photos, videos, documents, audio, voice, stickers, polls, previews, invite, pin, info, topics\n"
        "<code>/night 01:00 08:00</code> · <code>/night off</code>\n"
        "<code>/slowmode</code> · <code>/cooldown on 30</code>")),
    "filters": ("🚫 فیلترها", "🚫 Filters", (
        "<code>/antichannel on|off</code> · <code>/antilink</code> · <code>/antiforward</code>\n"
        "<code>/block photos</code> · <code>/unblock photos</code> (photos, videos, documents, stickers, gifs, voice, audio, links, forwards, channels, words)\n"
        "<code>/addblock کلمه</code> · <code>/rmblock</code> · <code>/blocklist</code>\n"
        "<code>/flood on 6 mute</code> · <code>/captcha on</code> · <code>/antiraid on</code> · <code>/cleanservice on</code>\n"
        "<code>/pause</code> · <code>/resume</code> · <code>/safereset</code> · <code>/why</code>"), (
        "<code>/antichannel on|off</code> · <code>/antilink</code> · <code>/antiforward</code>\n"
        "<code>/block photos</code> · <code>/unblock photos</code> · <code>/addblock word</code> · <code>/rmblock</code> · <code>/blocklist</code>\n"
        "<code>/flood on 6 mute</code> · <code>/captcha on</code> · <code>/antiraid on</code> · <code>/cleanservice on</code>\n"
        "<code>/pause</code> · <code>/resume</code> · <code>/safereset</code> · <code>/why</code>")),
    "welcome": ("👋 خوشامد", "👋 Welcome", (
        "<code>/setwelcome متن</code> (یا ریپلای) · <code>/welcome on|off|preview</code> · <code>/resetwelcome</code>\n"
        "<code>/setgoodbye متن</code> · <code>/goodbye on|off</code> · <code>/cleanwelcome on</code>\n"
        "متغیرها: {mention} {first} {fullname} {username} {chatname} {id} · دکمه: [متن](https://...)"), (
        "<code>/setwelcome text</code> (or reply) · <code>/welcome on|off|preview</code> · <code>/resetwelcome</code>\n"
        "<code>/setgoodbye text</code> · <code>/goodbye on|off</code> · <code>/cleanwelcome on</code>\n"
        "Variables: {mention} {first} {fullname} {username} {chatname} {id} · button: [label](https://...)")),
    "answers": ("📚 قوانین و پاسخ‌ها", "📚 Rules & answers", (
        "<code>/rules</code> · <code>/setrules</code> · <code>/clearrules</code>\n"
        "<code>/save نام متن</code> · <code>/get نام</code> یا <code>#نام</code> · <code>/clear نام</code> · <code>/faq</code>\n"
        "<code>/keyword نام کلید۱, کلید۲</code> · <code>/filter کلید پاسخ</code> · <code>/stop کلید</code> · <code>/autoanswer on</code>"), (
        "<code>/rules</code> · <code>/setrules</code> · <code>/clearrules</code>\n"
        "<code>/save name text</code> · <code>/get name</code> or <code>#name</code> · <code>/clear name</code> · <code>/faq</code>\n"
        "<code>/keyword name kw1, kw2</code> · <code>/filter kw reply</code> · <code>/stop kw</code> · <code>/autoanswer on</code>")),
    "logs": ("📝 لاگ", "📝 Logs", (
        "<code>/setlog</code> · <code>/setlog -100123…</code> [topic]\n"
        "<code>/linklog کد</code> (در گروه لاگ) · <code>/logstatus</code> · <code>/logtest</code> · <code>/unsetlog</code>"), (
        "<code>/setlog</code> · <code>/setlog -100123…</code> [topic]\n"
        "<code>/linklog code</code> (in the log chat) · <code>/logstatus</code> · <code>/logtest</code> · <code>/unsetlog</code>")),
    "history": ("🕘 تاریخچه و پرونده", "🕘 History & cases", (
        "<code>/history</code> · <code>/undo شماره</code> · <code>/case 12</code> · <code>/cases</code> · <code>/appeals</code>\n"
        "اعضا: <code>/mycases</code> در پیوی ربات"), (
        "<code>/history</code> · <code>/undo id</code> · <code>/case 12</code> · <code>/cases</code> · <code>/appeals</code>\n"
        "Members: <code>/mycases</code> in private chat")),
    "schedule": ("🗓 زمان‌بندی", "🗓 Scheduling", (
        "<code>/announce 21:00 متن</code> · <code>/announce daily 09:00 متن</code> · <code>/announcements</code>\n"
        "<code>/remind 2h متن</code> · <code>/remind group فردا 10:00 متن</code> · <code>/reminders</code>\n"
        "<code>/poll سؤال | گزینه۱ | گزینه۲</code> · <code>/pollclose</code>\n"
        "<code>/event فردا 20:00 عنوان | مکان</code> · <code>/events</code> · <code>/eventsummary</code> · <code>/eventcancel</code>"), (
        "<code>/announce 21:00 text</code> · <code>/announce daily 09:00 text</code> · <code>/announcements</code>\n"
        "<code>/remind 2h text</code> · <code>/remind group tomorrow 10:00 text</code> · <code>/reminders</code>\n"
        "<code>/poll question | a | b</code> · <code>/pollclose</code> · <code>/event tomorrow 20:00 title | place</code> · "
        "<code>/events</code> · <code>/eventsummary</code> · <code>/eventcancel</code>")),
    "tools": ("🧰 ابزار ادمین", "🧰 Admin tools", (
        "<code>/settings</code> · <code>/health</code> · <code>/digest</code> · <code>/digest daily 22:00</code>\n"
        "<code>/backup</code> · <code>/restore</code> (ریپلای روی فایل) · <code>/clone -100…</code> · <code>/watchdog</code>\n"
        "<code>/lang fa|en</code> · <code>/timezone Asia/Tehran</code> · <code>/set</code> · <code>/legacy</code>"), (
        "<code>/settings</code> · <code>/health</code> · <code>/digest</code> · <code>/digest daily 22:00</code>\n"
        "<code>/backup</code> · <code>/restore</code> (reply to file) · <code>/clone -100…</code> · <code>/watchdog</code>\n"
        "<code>/lang fa|en</code> · <code>/timezone Asia/Tehran</code> · <code>/set</code> · <code>/legacy</code>")),
    "desk": ("🛎 تیکت و عضویت", "🛎 Desk", (
        "<code>/contact متن</code> · <code>/tickets</code> · <code>/treply شماره متن</code>\n"
        "<code>/joindesk on</code> · <code>/joinquestion متن</code> · <code>/joinauto on</code> · <code>/requests</code>\n"
        "<code>/topic</code>"), (
        "<code>/contact text</code> · <code>/tickets</code> · <code>/treply id text</code>\n"
        "<code>/joindesk on</code> · <code>/joinquestion text</code> · <code>/joinauto on</code> · <code>/requests</code>\n"
        "<code>/topic</code>")),
    "privacy": ("🔐 حریم خصوصی", "🔐 Privacy", (
        "<code>/privacy</code> · <code>/retention 90</code> · <code>/exportdata</code> · <code>/resetdata</code>\n"
        "اعضا (پیوی): <code>/mydata</code> · <code>/forgetme</code>"), (
        "<code>/privacy</code> · <code>/retention 90</code> · <code>/exportdata</code> · <code>/resetdata</code>\n"
        "Members (private chat): <code>/mydata</code> · <code>/forgetme</code>")),
    "ai": ("🤖 هوش مصنوعی", "🤖 AI", (
        "<code>/ai متن</code> یا منشن ربات\n"
        "<code>/ai از ۱ شب تا ۸ صبح گروه رو ببند</code>\n"
        "<code>/ai این کاربر رو دو ساعت سایلنت کن</code> (ریپلای)\n"
        "<code>/ai کاربر 123456789 رو بن کن</code>"), (
        "<code>/ai text</code> or mention the bot\n"
        "<code>/ai lock the group from 01:00 to 08:00</code>\n"
        "<code>/ai mute this user for 2 hours</code> (reply)\n"
        "<code>/ai ban 123456789</code>")),
}
HELP_ORDER = ["mod", "locks", "filters", "welcome", "answers", "logs", "history", "schedule", "tools", "desk", "privacy", "ai"]


def help_main(lang: str) -> tuple[str, list]:
    fa = lang == "fa"
    txt = "<b>راهنما</b>" if fa else "<b>Help</b>"
    rows, row = [], []
    for k in HELP_ORDER:
        row.append((HELP[k][0] if fa else HELP[k][1], f"hp:{k}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return txt, rows


def help_section(key: str, lang: str) -> tuple[str, list]:
    h = HELP.get(key)
    if not h:
        return help_main(lang)
    fa = lang == "fa"
    return f"<b>{h[0] if fa else h[1]}</b>\n{h[2] if fa else h[3]}", [[("◀️ بازگشت" if fa else "◀️ Back", "hp:main")]]


# settings
SECTION_ORDER = ["protect", "welcome", "warn", "answers", "desk", "stats", "ai", "watch", "privacy", "general"]
RESTRICT_KEYS = {"captcha_on", "captcha_min", "captcha_fail", "raid_on", "raid_joins", "raid_lock_min", "flood_on",
                 "flood_n", "flood_s", "flood_action", "warn_limit", "warn_action", "warn_mute_h"}
INT_STEPS = {"flood_n": 1, "flood_s": 5, "captcha_min": 1, "raid_joins": 1, "raid_lock_min": 5, "cooldown_s": 5,
             "warn_limit": 1, "warn_mute_h": 6, "retention_days": 30}
TEXT_HINT = {"welcome_text": "/setwelcome", "goodbye_text": "/setgoodbye", "welcome_media": "/setwelcome",
             "jr_question": "/joinquestion", "tz": "/timezone", "announce_topic": "/topic announce"}


def right_for(key: str) -> str:
    return "restrict" if key in RESTRICT_KEYS else "info"


async def settings_main(chat_id: int, title: str, lang: str) -> tuple[str, list]:
    fa = lang == "fa"
    eff = await S.load(chat_id)
    on = await S.enabled_filters(chat_id)
    lays = await locks.layers(chat_id)
    r = await logs.route(chat_id)
    lines = [f"<b>{'تنظیمات' if fa else 'Settings'}: {esc(title)}</b>"]
    lines.append(("فیلترها: " if fa else "Filters: ") +
                 ("، ".join(esc(S.label(k, lang)) for k, _ in on) if on and fa else ", ".join(esc(S.label(k, lang)) for k, _ in on) if on
                  else ("خاموش" if fa else "off")))
    if eff["paused"][0]:
        lines.append("⏸ " + ("فیلترها متوقف‌اند" if fa else "Filters paused"))
    lines.append(("قفل‌ها: " if fa else "Locks: ") + (fa_digits(len(lays)) if fa else str(len(lays))))
    lines.append(("خوشامد: " if fa else "Welcome: ") + S.fmt_value("welcome_on", eff["welcome_on"][0], lang))
    lines.append(("لاگ: " if fa else "Logs: ") + (logs.status_label(r["status"], lang) if r and r["status"] != "disabled" else ("تنظیم نشده" if fa else "not set")))
    lines.append(("زبان/ساعت: " if fa else "Language/time: ") + f"{eff['lang'][0]} · {esc(eff['tz'][0])}")
    rows, row = [], []
    for sec in SECTION_ORDER:
        row.append((S.SECTIONS[sec][0 if fa else 1], f"st:s:{sec}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("📝 " + ("لاگ" if fa else "Logs"), "st:logs"), ("🔒 " + ("قفل‌ها" if fa else "Locks"), "st:locks"),
                 ("🩺 " + ("سلامت" if fa else "Health"), "st:health")])
    legacy = await RT.db.val("SELECT COUNT(*) FROM rz_legacy_review WHERE chat_id=?", (chat_id,), 0)
    if legacy:
        rows.append([("📦 " + (f"نسخهٔ قبل ({fa_digits(legacy)})" if fa else f"Previous version ({legacy})"), "st:legacy")])
    rows.append([("✖️ " + ("بستن" if fa else "Close"), "st:x")])
    return "\n".join(lines), rows


async def settings_section(chat_id: int, sec: str, lang: str) -> tuple[str, list]:
    fa = lang == "fa"
    eff = await S.load(chat_id)
    keys = [k for k, sp in S.SPECS.items() if sp.section == sec]
    lines = [f"<b>{S.SECTIONS[sec][0 if fa else 1]}</b>"]
    rows: list[list[tuple[str, str]]] = []
    for k in keys:
        sp = S.SPECS[k]
        v, src = eff[k]
        src_mark = {"default": "", "group": " ✎", "migrated": " ⇢", "global": " 🌐"}.get(src, "")
        lbl = S.label(k, lang)
        if sp.kind == "bool":
            lines.append(f"{'🟢' if v else '⚪️'} {esc(lbl)}")
            rows.append([(f"{'🟢' if v else '⚪️'} {lbl}{src_mark}"[:60], f"st:t:{k}:{0 if v else 1}")])
        elif sp.kind == "choice":
            lines.append(f"▫️ {esc(lbl)}: {esc(S.fmt_value(k, v, lang))}")
            nxt = sp.choices[(sp.choices.index(v) + 1) % len(sp.choices)] if v in sp.choices else sp.choices[0]
            rows.append([(f"🔁 {lbl}: {S.fmt_value(k, v, lang)}"[:60], f"st:c:{k}:{nxt}")])
        elif sp.kind == "int" and k in INT_STEPS:
            step = INT_STEPS[k]
            lines.append(f"▫️ {esc(lbl)}: {v}")
            lo, hi = max(sp.lo, (v or 0) - step), min(sp.hi, (v or 0) + step)
            rows.append([(f"➖ {lo}", f"st:i:{k}:{lo}"), (f"{lbl}: {v}"[:40], f"st:s:{sec}"), (f"➕ {hi}", f"st:i:{k}:{hi}")])
        else:
            hint = TEXT_HINT.get(k, "/set " + k)
            shown = S.fmt_value(k, v, lang) if v not in (None, "") else ("پیش‌فرض" if fa else "default")
            lines.append(f"▫️ {esc(lbl)}: {esc(shown)} · <code>{esc(hint)}</code>")
    if sec == "ai":
        lines.append(("\nسرویس: " if fa else "\nService: ") + ai_status_label(lang) + " · Model hidden")
    rows.append([("◀️ " + ("بازگشت" if fa else "Back"), "st:main")])
    return "\n".join(lines), rows


async def logs_page(chat_id: int, lang: str, tz: str) -> tuple[str, list]:
    fa = lang == "fa"
    r = await logs.route(chat_id)
    if not r or r["status"] == "disabled":
        txt = ("<b>لاگ</b>\nتنظیم نشده · <code>/setlog</code>" if fa else
               "<b>Logs</b>\nNot set · <code>/setlog</code>")
        return txt, [[("◀️ " + ("بازگشت" if fa else "Back"), "st:main")]]
    txt = (f"<b>{'لاگ' if fa else 'Logs'}</b>\n{logs.status_label(r['status'], lang)} · <code>{r['dest_id']}</code>"
           + (f" topic {r['topic_id']}" if r["topic_id"] else "") +
           f"\n{'آخرین ارسال' if fa else 'Last sent'}: {fmt_dt(r['last_ok'], tz, lang)}" +
           (f"\n⚠️ {esc(r['last_error'])}" if r["last_error"] else ""))
    return txt, [[("🔧 " + ("تست" if fa else "Test"), "lg:repair"), ("⛔️ " + ("خاموش" if fa else "Disable"), "lg:off")],
                 [("◀️ " + ("بازگشت" if fa else "Back"), "st:main")]]


async def locks_page(chat_id: int, lang: str, tz: str) -> tuple[str, list]:
    fa = lang == "fa"
    lays = await locks.layers(chat_id)
    night = await RT.db.one("SELECT * FROM rz_night WHERE chat_id=? AND enabled=1", (chat_id,))
    lines = ["<b>" + ("قفل‌ها" if fa else "Locks") + "</b>"]
    lines += ["• " + locks.describe_layer(la, lang, tz) for la in lays] or [("• هیچ" if fa else "• none")]
    lines.append(("شبانه: " if fa else "Night: ") + (f"{night['start']} تا {night['end']}" if night and fa else f"{night['start']}-{night['end']}" if night
                                                       else ("خاموش" if fa else "off")))
    rows = []
    if any(la["layer"].startswith("manual:") for la in lays):
        rows.append([("🔓 " + ("بازکردن همه" if fa else "Unlock all"), "lk:all")])
    rows.append([("◀️ " + ("بازگشت" if fa else "Back"), "st:main")])
    return "\n".join(lines), rows


async def legacy_page(chat_id: int, lang: str) -> tuple[str, list]:
    fa = lang == "fa"
    rows_ = await RT.db.all("SELECT key, value FROM rz_legacy_review WHERE chat_id=? ORDER BY key LIMIT 40", (chat_id,))
    lines = ["<b>" + ("تنظیمات نسخهٔ قبل (خاموش)" if fa else "Previous version settings (off)") + "</b>"]
    for r in rows_:
        lines.append(f"• <code>{esc(r['key'])}</code> = {esc(r['value'][:60])}")
    if not rows_:
        lines.append("-")
    return "\n".join(lines), [[("🧹 " + ("پاک کردن فهرست" if fa else "Clear list"), "lr:clear")],
                              [("◀️ " + ("بازگشت" if fa else "Back"), "st:main")]]


def ai_status_label(lang: str) -> str:
    st = RT.ai.status if RT.ai else "not configured"
    fa = lang == "fa"
    if st in ("ok", "unknown"):
        return "فعال" if fa else "on"
    if st == "not configured":
        return "خاموش" if fa else "off"
    return "مشکل دارد" if fa else "degraded"


async def can_manage(chat_id: int, user_id: int | None, right: str) -> bool:
    ok, _ = await authz.check(chat_id, user_id, right, fresh=True)
    return ok
