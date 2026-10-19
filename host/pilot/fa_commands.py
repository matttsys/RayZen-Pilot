"""Persian text commands ("بن", "سکوت ۲ ساعت", "قفل گروه", ...).

Matching works on a normalized copy of the first line. The original text is kept for arguments, so names,
reasons and templates are never rewritten. A phrase only counts when it starts the message; quotes, forwards,
code blocks and edited messages never trigger anything. Authorization is checked by the normal command path.
"""
from __future__ import annotations

import re

_AR = str.maketrans({"ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "آ", "ۀ": "ه",
                     "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
                     "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9"})
_MARKS = re.compile(r"[\u064B-\u065F\u0670\u0640]")          # harakat, superscript alef, tatweel
_SPACE = re.compile(r"[\s\u200c\u200d\u200e\u200f\u00a0]+")
_TRAIL = re.compile(r"[\s.!؟?،,:;]+$")
QUOTES = ("«", "\"", "“", "”", "'", "‘", ">", "`")

# kind: target = needs a reply, @username, numeric id or text mention
#       args   = free arguments allowed
#       exact  = the whole first line must be the phrase
#       multi  = arguments may span several lines (templates, rules)
PHRASES: list[tuple[str, str, str]] = [
    # members and sanctions
    ("بن", "ban", "target"), ("مسدود", "ban", "target"), ("بن موقت", "tban", "target"),
    ("رفع بن", "unban", "target"), ("آنبن", "unban", "target"), ("حذف بن", "unban", "target"),
    ("سکوت", "mute", "target"), ("سایلنت", "mute", "target"), ("میوت", "mute", "target"),
    ("رفع سکوت", "unmute", "target"), ("رفع سایلنت", "unmute", "target"), ("آنمیوت", "unmute", "target"),
    ("اخطار", "warn", "target"), ("وارن", "warn", "target"), ("حذف اخطار", "rmwarn", "target"),
    ("پاک کردن اخطارها", "resetwarns", "target"), ("ریست اخطار", "resetwarns", "target"),
    ("اخطارها", "warns", "args"), ("اخطارهای من", "warns", "exact"),
    ("اخراج", "kick", "target"), ("کیک", "kick", "target"),
    ("ارتقا", "promote", "target"), ("ادمین کن", "promote", "target"), ("عزل", "demote", "target"),
    ("برکناری", "demote", "target"),
    ("اعتماد", "trust", "target"), ("رفع اعتماد", "untrust", "target"), ("لیست اعتماد", "trusted", "exact"),
    ("لیست معتمدها", "trusted", "exact"), ("معتمد", "trust", "target"), ("حذف معتمد", "untrust", "target"),
    ("پاکسازی کاربر", "purgeuser", "target"), ("حذف پیام های کاربر", "purgeuser", "target"),
    ("این کیه", "info", "exact"), ("مشخصات", "info", "args"), ("اطلاعات کاربر", "info", "args"), ("آخرین بازدید", "seen", "target"),
    ("بررسی عضویت", "checkmember", "target"), ("تاریخچه نام", "namehistory", "target"),
    ("لیست بن", "banlist", "exact"), ("لیست سکوت", "mutelist", "exact"), ("تعداد اعضا", "membercount", "exact"),
    ("ادمین ها", "admins", "exact"), ("دسترسی ادمین ها", "adminrights", "exact"),
    ("بن کانال", "banchannel", "exact"), ("رفع بن کانال", "unbanchannel", "args"),
    # messages
    ("حذف", "del", "exact"), ("پاکسازی", "purge", "exact"), ("سنجاق", "pin", "exact"), ("پین", "pin", "exact"),
    ("برداشتن سنجاق", "unpin", "exact"), ("حذف سنجاق", "unpin", "exact"),
    ("حذف کلمه", "purgeword", "args"), ("پاکسازی کلمه", "purgeword", "args"), ("گزارش", "report", "args"),
    ("ریپورت", "report", "args"),
    # locks
    ("قفل گروه", "lockdown", "args"), ("قفل", "lock", "args"), ("بازکردن قفل", "unlock", "args"),
    ("باز کردن قفل", "unlock", "args"), ("بازکردن گروه", "unlock", "exact"), ("باز کردن گروه", "unlock", "exact"),
    ("وضعیت قفل", "lockstatus", "exact"), ("قفل شبانه", "night", "args"), ("حالت شب", "night", "args"),
    # filters
    ("ضد لینک", "antilink", "args"), ("ضد فوروارد", "antiforward", "args"), ("ضد کانال", "antichannel", "args"),
    ("ضد فلود", "flood", "args"), ("کپچا", "captcha", "args"), ("ضد رید", "antiraid", "args"),
    ("فیلتر کلمه", "addblock", "args"), ("افزودن کلمه ممنوع", "addblock", "args"),
    ("حذف کلمه ممنوع", "rmblock", "args"), ("کلمات ممنوع", "blocklist", "exact"), ("لیست کلمات ممنوع", "blocklist", "exact"),
    ("خروجی کلمات ممنوع", "blockexport", "exact"), ("پاک کردن کلمات ممنوع", "blockclear", "exact"), ("توقف فیلترها", "pause", "exact"), ("ادامه فیلترها", "resume", "exact"),
    ("چرا پاک شد", "why", "exact"), ("دوره آزمایشی", "probation", "args"), ("ضد تبچی", "antitabchi", "args"),
    ("ضد منشن", "antimention", "args"), ("ضد تکرار", "antidupe", "args"),
    # welcome, rules, answers
    ("خوشامد", "welcome", "args"), ("تنظیم خوشامد", "setwelcome", "multi"), ("پیش نمایش خوشامد", "welcome preview", "exact"), ("روشن کردن خوشامد", "welcome on", "exact"), ("خاموش کردن خوشامد", "welcome off", "exact"),
    ("خداحافظی", "goodbye", "args"), ("قوانین", "rules", "exact"), ("تنظیم قوانین", "setrules", "multi"),
    ("حذف قوانین", "clearrules", "exact"), ("ذخیره", "save", "multi"), ("پاسخ ها", "faq", "exact"),
    # schedule
    ("یادآوری", "remind", "multi"), ("یادم بنداز", "remind", "multi"), ("تایمر", "timer", "args"),
    ("اطلاعیه", "announce", "multi"), ("نظرسنجی", "poll", "multi"), ("رویداد", "event", "multi"),
    # activity
    ("آمار", "stats", "args"), ("آمار گروه", "stats", "args"), ("آمار من", "mystats", "exact"),
    ("آمار کاربر", "userstats", "target"), ("فعال ترین ها", "top", "args"), ("برترین ها", "top", "args"),
    ("ساعت های شلوغ", "hours", "args"), ("ورود و خروج", "joins", "args"), ("اعضای جدید", "newmembers", "args"),
    ("غیرفعال ها", "inactive", "args"), ("اضافه کننده ها", "topadders", "args"), ("گزارش آمار", "statsreport", "args"),
    ("ماندگاری اعضا", "cohort", "args"),
    # invites
    ("لینک جدید", "newlink", "args"), ("آمار لینک ها", "linkstats", "args"), ("لینک دعوت من", "mylink", "exact"),
    ("لینک شخصی اعضا", "memberlinks", "args"), ("لینک درخواستی", "requestlink", "args"), ("لینک ها", "links", "exact"),
    ("باطل کردن لینک", "revokelink", "args"), ("لینک من", "mylink", "exact"), ("دعوت کننده ها", "topinviters", "args"),
    # utilities
    ("تاریخ", "jalali", "args"), ("امروز", "jalali", "exact"), ("تاریخ میلادی", "gregorian", "args"),
    ("ساعت", "time", "args"), ("حساب کن", "calc", "args"), ("محاسبه", "calc", "args"), ("روز شمار", "countdown", "args"),
    ("فاصله تاریخ", "daysbetween", "args"), ("انتخاب کن", "choose", "args"), ("عدد تصادفی", "random", "args"),
    ("تبدیل واحد", "convert", "args"), ("حالت غیبت", "afk", "args"),
    # assistant content tools (reply to a message)
    ("فراموش کن", "forgetchat", "exact"), ("گفتگوی جدید", "newchat", "exact"), ("اسم دستیار", "ainame", "args"),
    ("خلاصه کن", "summarize", "args"), ("ترجمه کن", "translate", "args"), ("ترجمه", "translate", "args"),
    ("ویرایش متن", "fixtext", "exact"), ("غلط گیری", "fixtext", "exact"), ("توضیح بده", "explain", "args"),
    # tools
    ("راهنما", "help", "args"), ("دستورات", "help", "args"), ("تنظیمات", "settings", "exact"),
    ("سلامت گروه", "health", "exact"), ("گزارش روزانه", "digest", "args"), ("آیدی", "id", "exact"),
    ("ایدی", "id", "exact"), ("زبان", "lang", "args"), ("منطقه زمانی", "timezone", "args"),
    ("تماس با ادمین", "contact", "multi"), ("حریم خصوصی", "privacy", "exact"), ("تاریخچه", "history", "args"),
    ("برگرداندن", "undo", "args"), ("پاک شدن پیام ربات", "botclean", "args"),
    # owner (private chat)
    ("پنل", "panel", "exact"), ("پنل مدیریت", "panel", "exact"), ("آپدیت", "update", "args"), ("بروزرسانی", "update", "args"),
]


def norm(s: str) -> str:
    """Normalized form used ONLY for matching."""
    s = (s or "").translate(_AR)
    s = _MARKS.sub("", s)
    s = _SPACE.sub(" ", s).strip().lower()
    return _TRAIL.sub("", s)


_TABLE: list[tuple[str, str, str, int]] = sorted(
    ((norm(p), cmd, kind, len(norm(p).split())) for p, cmd, kind in PHRASES), key=lambda x: (-len(x[0]), x[0]))


def conflicts() -> list[str]:
    """Phrases that normalize to the same text but point at different commands."""
    seen: dict[str, str] = {}
    out = []
    for p, cmd, _, _ in _TABLE:
        if p in seen and seen[p] != cmd:
            out.append(f"{p}: {seen[p]} / {cmd}")
        seen.setdefault(p, cmd)
    return out


def _rest(original: str, words: int) -> tuple[str, int]:
    """Drop the first `words` normalized words from the original text and keep the rest untouched.
    Also returns how many whitespace tokens of the original were consumed (a ZWNJ word like «پیش‌نمایش»
    is one original token but two normalized words)."""
    got = 0
    toks = list(re.finditer(r"\S+", original))
    for i, m in enumerate(toks):
        got += len(norm(m.group(0)).split())
        if got >= words:
            return original[m.end():].strip(), i + 1
    return "", len(toks)


def match(text: str) -> tuple[str, str, str, int] | None:
    """(command, rest, kind, original_tokens_used) for an intentional Persian command, else None."""
    if not text:
        return None
    raw = text.strip()
    if not raw or raw.startswith(QUOTES):
        return None
    first, _, more = raw.partition("\n")
    n = norm(first)
    if not n or len(n) > 200:
        return None
    for p, cmd, kind, words in _TABLE:
        if n == p:
            hit = True
        elif n.startswith(p + " ") and kind != "exact":
            hit = True
        else:
            continue
        if hit:
            if more and kind != "multi":
                return None                      # long multi-line chat is not a command
            rest, used = _rest(first, words)
            if more:
                rest = (rest + "\n" + more).strip()
            return cmd, rest, kind, used
    return None


def intentional(m) -> bool:
    """False for forwards, automatic forwards and messages that start with quoted or code text."""
    if m is None or m.forward_origin is not None or getattr(m, "is_automatic_forward", False):
        return False
    for e in (m.entities or ()):
        if e.offset == 0 and e.type in ("code", "pre", "blockquote", "expandable_blockquote"):
            return False
    if getattr(m, "quote", None) is not None and not m.reply_to_message:
        return False
    return True


def has_target(m, rest: str) -> bool:
    if m.reply_to_message is not None and not getattr(m.reply_to_message, "forum_topic_created", None):
        return True
    if re.match(r"^[@\d]", norm(rest)):
        return True
    return any(e.type == "text_mention" for e in (m.entities or ()))
