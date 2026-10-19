"""Command catalogue: the help pages are generated from the real command registry (commands.REG).

Every canonical command needs a short description here; validate() reports anything missing, stale or
unregistered so tests can fail on drift. Persian text phrases come from fa_commands, so help shows what people
actually type. Members only see member commands in groups; owner commands only show for the configured owner."""
from __future__ import annotations

from pilot import fa_commands
from pilot.util import esc

PAGE = 8

# key, fa title, en title, registry sections shown in this category
CATEGORIES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("mod", "🛡 مدیریت کاربران", "🛡 Moderation", ("mod",)),
    ("members", "👥 اعضا", "👥 Members", ("members",)),
    ("locks", "🔒 قفل‌ها", "🔒 Locks", ("locks",)),
    ("filters", "🚫 فیلترها", "🚫 Filters", ("filters",)),
    ("welcome", "👋 خوشامد", "👋 Welcome", ("welcome",)),
    ("answers", "📌 قوانین و پاسخ‌ها", "📌 Rules & answers", ("answers",)),
    ("stats", "📊 آمار", "📊 Statistics", ("stats",)),
    ("invites", "🔗 لینک دعوت", "🔗 Invite links", ("invites",)),
    ("schedule", "⏰ زمان‌بندی", "⏰ Scheduling", ("schedule",)),
    ("desk", "📨 تیکت و درخواست", "📨 Desk", ("desk", "history")),
    ("logs", "📝 لاگ", "📝 Logs", ("logs",)),
    ("ai", "💬 دستیار", "💬 Assistant", ("ai",)),
    ("utils", "🧮 ابزارها", "🧮 Utilities", ("utils",)),
    ("tools", "⚙️ تنظیمات", "⚙️ Settings", ("tools", "general")),
    ("privacy", "🔐 حریم خصوصی", "🔐 Privacy", ("privacy",)),
    ("owner", "🛠 مالک ربات", "🛠 Bot owner", ("owner",)),
]
CAT_KEYS = [k for k, *_ in CATEGORIES]
OLD_KEYS = {"history": "tools"}           # help keys used by v4 buttons

# canonical name: (fa, en)
DESC: dict[str, tuple[str, str]] = {
    # moderation
    "ban": ("بن کاربر؛ با ریپلای، آیدی عددی یا یوزرنیم. مدت اختیاری", "Ban by reply, numeric id or username; optional duration"),
    "tban": ("بن موقت با مدت مشخص", "Temporary ban"),
    "dban": ("حذف پیام و بن فرستنده", "Delete the message and ban its sender"),
    "unban": ("رفع بن؛ عضو فعلی را اخراج نمی‌کند", "Lift a ban without removing current members"),
    "mute": ("سکوت کاربر، با مدت اختیاری", "Mute, optional duration"),
    "tmute": ("سکوت موقت", "Temporary mute"),
    "unmute": ("رفع سکوت", "Unmute"),
    "kick": ("اخراج؛ کاربر می‌تواند برگردد", "Kick; the user can rejoin"),
    "kickme": ("خروج خودتان از گروه", "Leave the group yourself"),
    "warn": ("اخطار؛ با رسیدن به سقف، جریمهٔ تنظیم‌شده اجرا می‌شود", "Warn; the configured penalty runs at the limit"),
    "warns": ("اخطارهای یک کاربر یا خودتان", "Warnings of a user or yourself"),
    "rmwarn": ("حذف آخرین اخطار", "Remove the latest warning"),
    "resetwarns": ("پاک کردن همهٔ اخطارهای کاربر", "Clear all of a user's warnings"),
    "del": ("حذف پیام ریپلای‌شده", "Delete the replied message"),
    "purge": ("حذف پیام‌ها از پیام ریپلای‌شده تا الان", "Delete from the replied message to now"),
    "purgeword": ("حذف پیام‌های ۴۸ ساعت اخیر که یک کلمه دارند (با پیش‌نمایش)", "Delete last-48h messages containing a word (previewed)"),
    "pin": ("سنجاق پیام ریپلای‌شده", "Pin the replied message"),
    "unpin": ("برداشتن سنجاق", "Unpin"),
    "unpinall": ("برداشتن همهٔ سنجاق‌ها (با تأیید)", "Unpin everything (confirmed)"),
    "promote": ("ادمین کردن با دسترسی‌های محدود", "Promote with limited rights"),
    "demote": ("برکناری ادمینی که ربات ارتقا داده", "Demote an admin the bot promoted"),
    "admins": ("فهرست ادمین‌ها", "List admins"),
    "info": ("مشخصات کاربر و وضعیتش در گروه", "User details and status here"),
    "trust": ("معتمد کردن؛ فیلترها روی او اجرا نمی‌شوند", "Trust a member; filters skip them"),
    "untrust": ("حذف از معتمدها", "Remove from trusted"),
    "trusted": ("فهرست معتمدها", "Trusted members"),
    # members
    "seen": ("آخرین باری که ربات این کاربر را دیده", "When the bot last observed this user"),
    "checkmember": ("بررسی عضویت فعلی یک کاربر مشخص", "Check one user's current membership"),
    "namehistory": ("نام‌های قبلی که ربات دیده", "Earlier names the bot observed"),
    "banlist": ("بن‌های ثبت‌شده توسط ربات", "Bans recorded by the bot"),
    "mutelist": ("سکوت‌های فعال ثبت‌شده توسط ربات", "Active mutes recorded by the bot"),
    "membercount": ("تعداد اعضا از تلگرام و تعداد اعضای دیده‌شده", "Member count from Telegram plus observed members"),
    "adminrights": ("دسترسی‌های هر ادمین", "Each admin's rights"),
    "banchannel": ("بن کانالی که با نام کانال پیام داده (ریپلای)", "Ban the channel identity of a replied message"),
    "unbanchannel": ("رفع بن کانال با آیدی", "Unban a channel identity by id"),
    "purgeuser": ("حذف پیام‌های اخیر یک کاربر (با تأیید)", "Delete a user's recent messages (confirmed)"),
    # locks
    "lock": ("قفل یک نوع محتوا؛ مدت اختیاری", "Lock a content type; optional duration"),
    "unlock": ("باز کردن قفل؛ سکوت‌های فردی دست نمی‌خورد", "Unlock without lifting individual mutes"),
    "lockdown": ("قفل کامل گروه (با تأیید)", "Lock the whole group (confirmed)"),
    "lockstatus": ("قفل‌های فعال و زمان پایانشان", "Active locks and end times"),
    "night": ("قفل شبانه با دسترسی‌های واقعی گروه", "Night lock using real group permissions"),
    "slowmode": ("راهنمای حالت آهستهٔ خود تلگرام", "How to set Telegram's own slow mode"),
    "cooldown": ("فاصلهٔ پیام توسط ربات (اختیاری، پیش‌فرض خاموش)", "Bot-managed message cooldown (opt-in)"),
    # filters
    "antilink": ("حذف لینک‌ها", "Delete links"),
    "antiforward": ("حذف فورواردها", "Delete forwards"),
    "antichannel": ("حذف پیام کانال‌های بیرونی؛ کانال متصل و ادمین ناشناس آزادند", "Delete posts from outside channels; linked channel and anonymous admins pass"),
    "flood": ("ضد فلود با تعداد و بازهٔ قابل تنظیم", "Flood control with set count and window"),
    "captcha": ("کپچای ورود اعضای جدید", "Join captcha for newcomers"),
    "antiraid": ("قفل خودکار هنگام ورود ناگهانی تعداد زیاد", "Auto-lock on a sudden join wave"),
    "antitabchi": ("محدودیت لینک و فوروارد برای تازه‌واردها", "Limit links and forwards from newcomers"),
    "antimention": ("حذف پیام‌هایی که خیلی‌ها را منشن می‌کنند", "Delete mass-mention messages"),
    "antidupe": ("حذف پیام تکراری پشت‌سرهم", "Delete repeated messages"),
    "probation": ("دورهٔ آزمایشی تازه‌واردها: بدون لینک و رسانه", "Newcomer probation: no links or media"),
    "block": ("روشن کردن یک فیلتر محتوا", "Turn on a content filter"),
    "unblock": ("خاموش کردن یک فیلتر محتوا", "Turn off a content filter"),
    "addblock": ("افزودن کلمهٔ ممنوع (چندتایی با کاما)", "Add blocked words (comma separated)"),
    "rmblock": ("حذف کلمهٔ ممنوع", "Remove a blocked word"),
    "blocklist": ("فهرست کلمات ممنوع", "Blocked words"),
    "blockexport": ("خروجی کلمات ممنوع در پیوی", "Export blocked words to private chat"),
    "blockclear": ("پاک کردن همهٔ کلمات ممنوع (با تأیید)", "Clear all blocked words (confirmed)"),
    "pause": ("توقف موقت همهٔ فیلترها", "Pause all filters"),
    "resume": ("ادامهٔ فیلترها", "Resume filters"),
    "why": ("دلیل حذف پیام‌های اخیر", "Why recent messages were removed"),
    "safereset": ("خاموش کردن همهٔ فیلترها با پیش‌نمایش", "Turn every filter off, with preview"),
    # welcome
    "welcome": ("خوشامد: روشن، خاموش، پیش‌نمایش", "Welcome: on, off, preview"),
    "setwelcome": ("متن یا رسانهٔ خوشامد؛ ریپلای روی رسانه", "Welcome text or media (reply to media)"),
    "resetwelcome": ("برگرداندن خوشامد پیش‌فرض", "Reset the welcome"),
    "cleanwelcome": ("حذف خوشامد قبلی وقتی نفر بعدی می‌آید", "Delete the previous welcome on the next join"),
    "goodbye": ("پیام خداحافظی: روشن یا خاموش", "Goodbye message on/off"),
    "setgoodbye": ("متن خداحافظی", "Goodbye text"),
    "cleanservice": ("حذف پیام‌های ورود و خروج تلگرام", "Delete Telegram join/leave service messages"),
    # rules and answers
    "rules": ("نمایش قوانین", "Show the rules"),
    "setrules": ("تنظیم قوانین", "Set the rules"),
    "clearrules": ("حذف قوانین", "Remove the rules"),
    "save": ("ذخیرهٔ پاسخ آماده با نام؛ با #نام فراخوانی می‌شود", "Save a named answer; call it with #name"),
    "get": ("نمایش پاسخ ذخیره‌شده", "Show a saved answer"),
    "clear": ("حذف پاسخ ذخیره‌شده", "Delete a saved answer"),
    "faq": ("فهرست پاسخ‌های ذخیره‌شده", "List saved answers"),
    "keyword": ("پاسخ خودکار به یک کلمه", "Auto-reply to a keyword"),
    "stop": ("حذف پاسخ خودکار کلمه", "Remove a keyword auto-reply"),
    "autoanswer": ("روشن/خاموش کردن پاسخ‌های خودکار", "Keyword auto-replies on/off"),
    # stats
    "stats": ("آمار پیام‌ها و اعضا از زمان شروع ثبت", "Messages and members since tracking began"),
    "mystats": ("آمار خودتان در این گروه", "Your own stats here"),
    "userstats": ("آمار یک کاربر", "One member's stats"),
    "top": ("فعال‌ترین‌ها در یک بازه", "Most active members for a period"),
    "hours": ("ساعت‌های شلوغ گروه", "Busiest hours"),
    "joins": ("ورود و خروج روزانه", "Daily joins and leaves"),
    "newmembers": ("اعضای جدید اخیر", "Recent newcomers"),
    "leavers": ("کسانی که اخیراً رفته‌اند", "Recent leavers"),
    "inactive": ("اعضای دیده‌شده‌ای که مدتی پیام نداده‌اند", "Observed members silent for a while"),
    "topadders": ("کسانی که عضو اضافه کرده‌اند (فقط ثبت‌شده)", "Members who added others (observed only)"),
    "cohort": ("چند درصد تازه‌واردها مانده‌اند", "How many newcomers stayed"),
    "statsreport": ("گزارش خودکار آمار روزانه یا هفتگی", "Automatic daily or weekly stats report"),
    "statsmode": ("روشن/خاموش کردن ثبت آمار (فقط شمارش)", "Stats tracking on/off (counts only)"),
    # invites
    "newlink": ("ساخت لینک دعوت با نام، انقضا یا سقف", "Create a named invite link with expiry or limit"),
    "requestlink": ("لینک دعوت با درخواست عضویت", "Invite link that needs approval"),
    "links": ("لینک‌های ساخته‌شده توسط ربات", "Links created by the bot"),
    "linkstats": ("ورود از هر لینک", "Joins per link"),
    "revokelink": ("باطل کردن لینک", "Revoke a link"),
    "mylink": ("لینک دعوت شخصی شما", "Your personal invite link"),
    "memberlinks": ("اجازهٔ لینک شخصی برای اعضا", "Allow personal links for members"),
    "topinviters": ("بیشترین ورود از لینک‌های شخصی", "Most joins via personal links"),
    # schedule
    "remind": ("یادآوری برای خودتان یا گروه", "Reminder for you or the group"),
    "reminders": ("یادآوری‌های فعال شما", "Your active reminders"),
    "announce": ("اطلاعیهٔ زمان‌بندی‌شده یا تکراری", "Scheduled or repeating announcement"),
    "announcements": ("مدیریت اطلاعیه‌ها", "Manage announcements"),
    "poll": ("نظرسنجی سریع", "Quick poll"),
    "pollclose": ("بستن نظرسنجی", "Close a poll"),
    "event": ("رویداد با حضور و غیاب", "Event with RSVPs"),
    "events": ("رویدادهای پیش رو", "Upcoming events"),
    "eventcancel": ("لغو رویداد", "Cancel an event"),
    "eventsummary": ("خلاصهٔ حضور در رویداد", "Event attendance summary"),
    # desk
    "report": ("گزارش پیام به ادمین‌ها", "Report a message to admins"),
    "contact": ("پیام خصوصی به ادمین‌ها (تیکت)", "Private message to admins (ticket)"),
    "tickets": ("تیکت‌های باز", "Open tickets"),
    "treply": ("پاسخ به تیکت", "Reply to a ticket"),
    "appeals": ("اعتراض‌های باز", "Open appeals"),
    "mycases": ("پرونده‌های شما و اعتراض", "Your cases and appeals"),
    "joindesk": ("بررسی درخواست‌های عضویت توسط ادمین‌ها", "Admins review join requests"),
    "joinquestion": ("سؤال قبل از تأیید عضویت", "Question before join approval"),
    "joinauto": ("تأیید خودکار درخواست بعد از پاسخ", "Auto-approve after an answer"),
    "requests": ("درخواست‌های عضویت در انتظار", "Pending join requests"),
    "topic": ("تاپیک‌های انجمن: ساخت، بستن، باز کردن", "Forum topics: create, close, reopen"),
    # logs
    "setlog": ("تنظیم گروه یا کانال لاگ با کد تأیید", "Set a log group/channel with a code"),
    "linklog": ("ثبت کد لاگ در مقصد", "Register the log code at the destination"),
    "logstatus": ("وضعیت تحویل لاگ", "Log delivery status"),
    "logtest": ("ارسال آزمایشی و تعمیر لاگ", "Test and repair log delivery"),
    "unsetlog": ("خاموش کردن لاگ", "Turn logs off"),
    # assistant
    "ai": ("سؤال از دستیار (با نام دستیار یا ریپلای هم کار می‌کند)", "Ask the assistant (name or reply works too)"),
    "summarize": ("خلاصهٔ پیام ریپلای‌شده", "Summarize the replied message"),
    "translate": ("ترجمهٔ پیام ریپلای‌شده", "Translate the replied message"),
    "fixtext": ("غلط‌گیری متن", "Proofread a text"),
    "explain": ("توضیح ساده", "Explain simply"),
    "forgetchat": ("شروع گفتگوی تازه با دستیار", "Start a fresh conversation"),
    "ainame": ("اسمی که پیام‌های شروع‌شده با آن به دستیار می‌رسد", "Name that addresses the assistant"),
    # utilities
    "jalali": ("تاریخ شمسی امروز یا تبدیل میلادی به شمسی", "Today in Jalali, or convert to Jalali"),
    "gregorian": ("تبدیل شمسی به میلادی", "Convert Jalali to Gregorian"),
    "time": ("ساعت فعلی در منطقهٔ زمانی گروه یا شهر", "Current time in the group zone or a city"),
    "calc": ("ماشین‌حساب", "Calculator"),
    "countdown": ("روزهای مانده تا یک تاریخ", "Days left until a date"),
    "daysbetween": ("فاصلهٔ دو تاریخ", "Days between two dates"),
    "timer": ("تایمر کوتاه", "Short timer"),
    "choose": ("انتخاب تصادفی از بین گزینه‌ها", "Pick one of the options"),
    "random": ("عدد تصادفی", "Random number"),
    "convert": ("تبدیل واحد", "Unit conversion"),
    # settings / upkeep
    "settings": ("منوی تنظیمات گروه", "Group settings menu"),
    "set": ("تغییر مستقیم یک تنظیم", "Change one setting directly"),
    "health": ("وضعیت گروه و دسترسی‌های ربات", "Group status and bot rights"),
    "digest": ("گزارش روزانهٔ مدیریتی", "Daily moderation digest"),
    "history": ("تاریخچهٔ اقدام‌ها", "Action history"),
    "undo": ("برگرداندن یک اقدام", "Undo an action"),
    "case": ("جزئیات یک پرونده", "Case details"),
    "cases": ("پرونده‌های اخیر", "Recent cases"),
    "lang": ("زبان ربات", "Bot language"),
    "timezone": ("منطقهٔ زمانی گروه", "Group timezone"),
    "backup": ("پشتیبان تنظیمات در پیوی", "Settings backup to private chat"),
    "restore": ("بازیابی از فایل پشتیبان (با پیش‌نمایش)", "Restore from a backup (previewed)"),
    "clone": ("کپی تنظیمات از گروه دیگری که آنجا ادمین هستید", "Copy settings from another group you admin"),
    "watchdog": ("هشدار تغییر دسترسی‌های گروه", "Alert on permission changes"),
    "botclean": ("پاک شدن خودکار جواب‌های کوتاه ربات", "Auto-delete short bot replies"),
    "legacy": ("تنظیمات نسخهٔ قبل که نیاز به بازبینی دارند", "Earlier-version settings to review"),
    "start": ("شروع", "Start"),
    "help": ("همین راهنما؛ با یک کلمه جستجو می‌کند", "This help; add a word to search"),
    "id": ("آیدی عددی چت، شما و پیام ریپلای‌شده", "Numeric ids of chat, you and the replied message"),
    "afk": ("حالت غیبت؛ منشن‌کننده‌ها باخبر می‌شوند", "Away mode; people who mention you are told"),
    # privacy
    "privacy": ("چه چیزی ذخیره می‌شود و چه مدت", "What is stored and for how long"),
    "mydata": ("داده‌های شما", "Your data"),
    "forgetme": ("حذف داده‌های شما (با تأیید)", "Delete your data (confirmed)"),
    "exportdata": ("خروجی داده‌های گروه", "Export group data"),
    "resetdata": ("پاک کردن داده‌های گروه (با پیش‌نمایش)", "Reset group data (previewed)"),
    "retention": ("مدت نگهداری سوابق", "Record retention period"),
    # owner
    "panel": ("پنل مالک ربات: سلامت، گروه‌ها، مصرف، هوش مصنوعی، آپدیت", "Owner console: health, groups, usage, AI, updates"),
    "update": ("بررسی و نصب نسخهٔ امضاشده", "Check and install a signed release"),
}


def _specs():
    from pilot import commands as K
    seen, out = set(), []
    for sp in K.REG.values():
        if id(sp) not in seen:
            seen.add(id(sp))
            out.append(sp)
    return out


def _phrases() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p, cmd, _ in fa_commands.PHRASES:
        name = cmd.partition(" ")[0]
        if " " not in cmd:
            out.setdefault(name, []).append(p)
    return out


def category_of(spec) -> str:
    for key, _, _, secs in CATEGORIES:
        if spec.section in secs:
            return key
    return "tools"


def visible(spec, role: str) -> bool:
    if spec.section == "owner":
        return role == "owner"
    if role == "member":
        return not spec.admin
    return True


def entries(cat: str, role: str) -> list:
    return sorted((sp for sp in _specs() if category_of(sp) == cat and visible(sp, role)),
                  key=lambda sp: (sp.admin, sp.names[0]))


def line(sp, lang: str, mark_admin: bool = False) -> str:
    fa = lang == "fa"
    name = sp.names[0]
    d = DESC.get(name, ("", ""))[0 if fa else 1]
    ph = _phrases().get(name)
    shown = ph[0].replace(" های", "\u200cهای").replace(" ها", "\u200cها") if ph else ""
    head = f"<b>{esc(shown)}</b> · <code>/{name}</code>" if (ph and fa) else f"<code>/{name}</code>"
    tag = (" ·ادمین" if fa else " ·admin") if (mark_admin and sp.admin) else ""
    return f"• {head}{tag}\n  {esc(d)}" if d else f"• {head}{tag}"


def main_page(lang: str, role: str) -> tuple[str, list]:
    fa = lang == "fa"
    cats = [(k, fa_t if fa else en_t) for k, fa_t, en_t, _ in CATEGORIES if entries(k, role)]
    total = sum(len(entries(k, role)) for k, _ in cats)
    if fa:
        txt = ("<b>راهنما</b>\nدستورها را بدون «/» هم می‌شود فارسی نوشت؛ مثلاً با ریپلای: <b>اخطار</b>، <b>سکوت ۲ ساعت</b>.\n"
               f"جستجو: <code>راهنما کلمه</code> · {fa_digits(total)} دستور")
    else:
        txt = f"<b>Help</b>\nSearch: <code>/help word</code> · {total} commands"
    rows, row = [], []
    for k, title in cats:
        row.append((title, f"hp:{k}:0"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return txt, rows


def fa_digits(n: int) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def category_page(cat: str, page: int, lang: str, role: str, mark_admin: bool = False) -> tuple[str, list]:
    fa = lang == "fa"
    cat = OLD_KEYS.get(cat, cat)
    items = entries(cat, role)
    if not items:
        return main_page(lang, role)
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    title = next((ft if fa else et) for k, ft, et, _ in CATEGORIES if k == cat)
    body = "\n".join(line(sp, lang, mark_admin) for sp in items[page * PAGE:(page + 1) * PAGE])
    pg = (f" · {fa_digits(page + 1)}/{fa_digits(pages)}" if fa else f" · {page + 1}/{pages}") if pages > 1 else ""
    nav = []
    if page > 0:
        nav.append(("‹", f"hp:{cat}:{page - 1}"))
    nav.append(("بازگشت" if fa else "Back", "hp:main"))
    if page < pages - 1:
        nav.append(("›", f"hp:{cat}:{page + 1}"))
    return f"<b>{title}</b>{pg}\n\n{body}", [nav]


def search(q: str, lang: str, role: str, mark_admin: bool = False) -> tuple[str, list]:
    fa = lang == "fa"
    nq = fa_commands.norm(q).lstrip("/")
    ph = _phrases()
    for k, ft, et, _ in CATEGORIES:                 # a category name opens the category
        if nq in (k, fa_commands.norm(ft.split(" ", 1)[-1]), fa_commands.norm(et.split(" ", 1)[-1])):
            return category_page(k, 0, lang, role, mark_admin)
    scored = []
    for sp in _specs():
        if not visible(sp, role):
            continue
        names = [n.lower() for n in sp.names]
        phr = [fa_commands.norm(p) for p in ph.get(sp.names[0], [])]
        d = DESC.get(sp.names[0], ("", ""))
        if nq in names or nq in phr:
            s = 0
        elif any(n.startswith(nq) for n in names) or any(p.startswith(nq) for p in phr):
            s = 1
        elif nq in fa_commands.norm(d[0]) or nq in d[1].lower() or any(nq in p for p in phr):
            s = 2
        else:
            continue
        scored.append((s, sp.names[0], sp))
    scored.sort(key=lambda x: (x[0], x[1]))
    hits = [sp for _, _, sp in scored[:PAGE]]
    back = [[("بازگشت" if fa else "Back", "hp:main")]]
    if not hits:
        return (f"برای «{esc(q)}» چیزی پیدا نشد." if fa else f"Nothing found for “{esc(q)}”."), back
    head = f"<b>جستجو: {esc(q)}</b>" if fa else f"<b>Search: {esc(q)}</b>"
    return head + "\n\n" + "\n".join(line(sp, lang, mark_admin) for sp in hits), back


async def role_of(chat_id: int, chat_type: str, user_id: int | None, anon: bool = False) -> str:
    from pilot import authz
    if chat_type == "private":
        return "owner" if authz.is_owner(user_id) else "admin"
    if anon:
        return "admin"
    if user_id and user_id in await authz.admin_ids(chat_id):
        return "admin"
    return "member"


def validate() -> list[str]:
    """Drift between the registry, phrases and this catalogue. Empty list = consistent."""
    from pilot import commands as K
    problems = []
    canon = {sp.names[0]: sp for sp in _specs()}
    for name, sp in canon.items():
        if name not in DESC:
            problems.append(f"no description: /{name}")
        if not any(sp.section in secs for *_, secs in CATEGORIES):
            problems.append(f"no category for section {sp.section}: /{name}")
    for name in DESC:
        if name not in canon:
            problems.append(f"description for unregistered or non-canonical command: {name}")
    for p, cmd, _ in fa_commands.PHRASES:
        if cmd.partition(" ")[0] not in K.REG:
            problems.append(f"phrase «{p}» -> unregistered /{cmd}")
    problems += [f"phrase conflict: {x}" for x in fa_commands.conflicts()]
    return problems
