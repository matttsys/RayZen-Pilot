"""Settings registry.

Settings with enforce=True can delete, restrict or kick. They are off by default
and ignore global overrides."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pilot.db import jd, jl
from pilot.rt import RT
from pilot.util import now


@dataclass
class Spec:
    key: str
    kind: str            # bool | int | choice | text
    default: Any
    section: str
    fa: str
    en: str
    choices: tuple = ()
    lo: int = 0
    hi: int = 10**6
    enforce: bool = False
    global_ok: bool = False


SPECS: dict[str, Spec] = {}


def _s(*a, **kw) -> None:
    sp = Spec(*a, **kw)
    SPECS[sp.key] = sp


# general
_s("lang", "choice", "fa", "general", "زبان", "Language", choices=("fa", "en"), global_ok=True)
_s("tz", "text", None, "general", "منطقه زمانی", "Timezone", global_ok=True)
# ai
_s("ai_on", "bool", True, "ai", "هوش مصنوعی", "AI", global_ok=True)
_s("ai_members", "bool", True, "ai", "جواب به اعضا", "Answer members", global_ok=True)
# welcome
_s("welcome_on", "bool", False, "welcome", "خوشامد", "Welcome")
_s("welcome_text", "text", None, "welcome", "متن خوشامد", "Welcome text")
_s("welcome_media", "text", None, "welcome", "رسانه خوشامد", "Welcome media")
_s("welcome_clean", "bool", False, "welcome", "حذف خوشامد قبلی", "Delete old welcome")
_s("goodbye_on", "bool", False, "welcome", "خداحافظی", "Goodbye")
_s("goodbye_text", "text", None, "welcome", "متن خداحافظی", "Goodbye text")
_s("clean_service", "bool", False, "welcome", "حذف پیام ورود و خروج", "Delete join/leave", enforce=True)
# protection (off by default)
_s("f_channels", "bool", False, "protect", "پیام با کانال", "Channel posts", enforce=True)
_s("f_channels_action", "choice", "delete", "protect", "با پیام کانال", "On channel post", choices=("delete", "ban"))
_s("f_links", "bool", False, "protect", "لینک", "Links", enforce=True)
_s("f_forwards", "bool", False, "protect", "فوروارد", "Forwards", enforce=True)
_s("f_photos", "bool", False, "protect", "عکس", "Photos", enforce=True)
_s("f_videos", "bool", False, "protect", "ویدیو", "Videos", enforce=True)
_s("f_documents", "bool", False, "protect", "فایل", "Files", enforce=True)
_s("f_stickers", "bool", False, "protect", "استیکر", "Stickers", enforce=True)
_s("f_gifs", "bool", False, "protect", "گیف", "GIFs", enforce=True)
_s("f_voice", "bool", False, "protect", "ویس", "Voice", enforce=True)
_s("f_audio", "bool", False, "protect", "موسیقی", "Audio", enforce=True)
_s("f_words", "bool", False, "protect", "کلمات ممنوع", "Blocked words", enforce=True)
_s("flood_on", "bool", False, "protect", "ضد فلود", "Anti-flood", enforce=True)
_s("flood_n", "int", 6, "protect", "تعداد پیام فلود", "Flood messages", lo=3, hi=50)
_s("flood_s", "int", 10, "protect", "ثانیهٔ فلود", "Flood seconds", lo=3, hi=120)
_s("flood_action", "choice", "mute", "protect", "با فلود", "On flood", choices=("mute", "kick", "ban"))
_s("captcha_on", "bool", False, "protect", "کپچا", "Captcha", enforce=True)
_s("captcha_min", "int", 5, "protect", "دقیقهٔ کپچا", "Captcha minutes", lo=1, hi=60)
_s("captcha_fail", "choice", "kick", "protect", "بعد از مهلت", "On timeout", choices=("kick", "stay_muted"))
_s("raid_on", "bool", False, "protect", "ضد رید", "Anti-raid", enforce=True)
_s("raid_joins", "int", 10, "protect", "ورود در دقیقه", "Joins per minute", lo=3, hi=200)
_s("raid_lock_min", "int", 15, "protect", "دقیقهٔ قفل رید", "Raid lock minutes", lo=1, hi=720)
_s("cooldown_on", "bool", False, "protect", "فاصلهٔ ارسال", "Cooldown", enforce=True)
_s("cooldown_s", "int", 30, "protect", "ثانیهٔ فاصله", "Cooldown seconds", lo=5, hi=3600)
_s("paused", "bool", False, "protect", "توقف فیلترها", "Pause filters")
# warns
_s("warn_limit", "int", 3, "warn", "سقف اخطار", "Warn limit", lo=1, hi=20)
_s("warn_action", "choice", "mute", "warn", "در سقف اخطار", "At limit", choices=("mute", "kick", "ban"))
_s("warn_mute_h", "int", 24, "warn", "ساعت سایلنت", "Mute hours", lo=1, hi=720)
# help center
_s("answers_auto", "bool", False, "answers", "پاسخ خودکار", "Auto-answer")
# desk
_s("jr_desk", "bool", False, "desk", "درخواست عضویت", "Join requests")
_s("jr_question", "text", None, "desk", "سؤال عضویت", "Join question")
_s("jr_autoapprove", "bool", False, "desk", "تأیید خودکار", "Auto-approve")
_s("tickets_on", "bool", True, "desk", "تیکت پشتیبانی", "Support tickets")
_s("appeals_on", "bool", True, "desk", "اعتراض", "Appeals")
_s("report_on", "bool", True, "desk", "گزارش", "Reports")
_s("poll_members", "bool", False, "desk", "نظرسنجی اعضا", "Member polls")
# watchdog / privacy
_s("watch_on", "bool", True, "watch", "هشدار تغییر دسترسی", "Permission alerts")
_s("watch_notify", "choice", "log", "watch", "محل هشدار", "Alert to", choices=("log", "group", "off"))
_s("retention_days", "int", 90, "privacy", "روز نگهداری", "Retention days", lo=7, hi=730)
_s("keep_excerpt", "bool", False, "privacy", "ذخیرهٔ متن پیام در پرونده", "Keep message text in cases")
_s("word_index", "bool", True, "privacy", "نمایهٔ ۴۸ ساعته برای /purgeword", "48h index for /purgeword")
_s("announce_topic", "int", None, "topics", "تاپیک اطلاعیه‌ها", "Announcement topic")

FILTER_KEYS = [k for k, s in SPECS.items() if s.enforce]
SECTIONS = {
    "protect": ("🛡 فیلترها", "🛡 Filters"),
    "welcome": ("👋 خوشامد", "👋 Welcome"),
    "warn": ("⚠️ اخطارها", "⚠️ Warnings"),
    "answers": ("📚 پاسخ‌ها", "📚 Answers"),
    "desk": ("🛎 اعضا", "🛎 Members"),
    "ai": ("🤖 هوش مصنوعی", "🤖 AI"),
    "watch": ("👁 دسترسی‌ها", "👁 Permissions"),
    "privacy": ("🔐 حریم خصوصی", "🔐 Privacy"),
    "general": ("🌐 زبان و ساعت", "🌐 Language & time"),
}


def coerce(spec: Spec, value: Any) -> Any:
    if value is None:
        return None
    if spec.kind == "bool":
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("1", "on", "true", "yes", "روشن", "فعال", "enable"):
                return True
            if v in ("0", "off", "false", "no", "خاموش", "غیرفعال", "disable"):
                return False
            raise ValueError("bool expected")
        return bool(value)
    if spec.kind == "int":
        from pilot.util import norm_digits
        v = int(norm_digits(str(value)))
        if not spec.lo <= v <= spec.hi:
            raise ValueError(f"range {spec.lo}..{spec.hi}")
        return v
    if spec.kind == "choice":
        v = str(value).strip().lower()
        if v not in spec.choices:
            raise ValueError("one of " + "/".join(spec.choices))
        return v
    v = str(value)
    if len(v) > 3800:
        raise ValueError("too long")
    return v


async def load(chat_id: int) -> dict[str, tuple[Any, str]]:
    if chat_id in RT.settings_cache:
        return RT.settings_cache[chat_id]
    rows = await RT.db.all("SELECT chat_id,key,value,source FROM rz_settings WHERE chat_id IN (0, ?)", (chat_id,))
    glob = {r["key"]: jl(r["value"]) for r in rows if r["chat_id"] == 0}
    grp = {r["key"]: (jl(r["value"]), r["source"]) for r in rows if r["chat_id"] == chat_id and chat_id != 0}
    eff: dict[str, tuple[Any, str]] = {}
    for k, sp in SPECS.items():
        if k in grp:
            eff[k] = (grp[k][0], "group" if grp[k][1] != "migrated" else "migrated")
        elif sp.global_ok and k in glob:
            eff[k] = (glob[k], "global")
        else:
            d = sp.default
            if k == "tz":
                d = RT.cfg.tz if RT.cfg else "Asia/Tehran"
            eff[k] = (d, "default")
    RT.settings_cache[chat_id] = eff
    return eff


async def get(chat_id: int, key: str) -> Any:
    return (await load(chat_id))[key][0]


async def values(chat_id: int) -> dict[str, Any]:
    return {k: v[0] for k, v in (await load(chat_id)).items()}


async def set_value(chat_id: int, key: str, value: Any, actor: int, source: str = "admin") -> tuple[Any, Any]:
    """Save one setting. Returns (old, new). Raises ValueError if invalid."""
    sp = SPECS[key]
    if chat_id == 0 and not sp.global_ok:
        raise ValueError("not a global setting")
    new = coerce(sp, value)
    if key == "tz" and new:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(new)
        except Exception:  # noqa: BLE001
            raise ValueError("unknown timezone")
    old = (await load(chat_id))[key][0] if chat_id else None
    await RT.db.run("INSERT INTO rz_settings(chat_id,key,value,set_by,set_at,source) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value,set_by=excluded.set_by,"
                    "set_at=excluded.set_at,source=excluded.source", (chat_id, key, jd(new), actor, now(), source))
    invalidate(chat_id)
    return old, new


def invalidate(chat_id: int | None = None) -> None:
    if chat_id is None or chat_id == 0:
        RT.settings_cache.clear()
    else:
        RT.settings_cache.pop(chat_id, None)


async def enabled_filters(chat_id: int) -> list[tuple[str, str]]:
    eff = await load(chat_id)
    return [(k, eff[k][1]) for k in FILTER_KEYS if eff[k][0]]


def label(key: str, lang: str) -> str:
    sp = SPECS[key]
    return sp.fa if lang == "fa" else sp.en


def src_label(src: str, lang: str) -> str:
    return {"group": ("تنظیم گروه", "group"), "global": ("پیش‌فرض سراسری", "global default"),
            "default": ("پیش‌فرض", "default"), "migrated": ("نسخهٔ قبل", "migrated")}[src][0 if lang == "fa" else 1]


def fmt_value(key: str, v: Any, lang: str) -> str:
    sp = SPECS[key]
    if sp.kind == "bool":
        return ("روشن" if v else "خاموش") if lang == "fa" else ("on" if v else "off")
    if v is None:
        return "—"
    if sp.kind == "choice" and lang == "fa":
        return {"delete": "حذف", "ban": "بن", "mute": "سایلنت", "kick": "اخراج", "stay_muted": "سایلنت بماند",
                "log": "لاگ", "group": "گروه", "off": "خاموش", "fa": "فارسی", "en": "English"}.get(v, str(v))
    if sp.kind == "text":
        from pilot.util import short
        return short(str(v), 40)
    return str(v)
