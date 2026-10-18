"""Helpers: escaping, digits, durations, time parsing, Jalali dates."""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_DIG = {ord(c): str(i) for i, c in enumerate(FA_DIGITS)} | {ord(c): str(i) for i, c in enumerate(AR_DIGITS)}


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=False)


def norm_digits(s: str) -> str:
    return (s or "").translate(_DIG)


def fa_digits(s) -> str:
    return str(s).translate({ord(str(i)): FA_DIGITS[i] for i in range(10)})


def num(v, lang: str) -> str:
    return fa_digits(v) if lang == "fa" else str(v)


def now() -> float:
    return time.time()


# durations
_UNITS = {
    "s": 1, "sec": 1, "second": 1, "seconds": 1, "ثانیه": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "دقیقه": 60, "دقیقه‌ای": 60,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600, "ساعت": 3600,
    "d": 86400, "day": 86400, "days": 86400, "روز": 86400,
    "w": 604800, "week": 604800, "weeks": 604800, "هفته": 604800,
}
_FA_NUMWORDS = {"یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "هفت": 7, "هشت": 8,
                "نه": 9, "ده": 10, "دوازده": 12, "پانزده": 15, "بیست": 20, "سی": 30, "چهل": 40, "نیم": 0.5}
_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z\u0600-\u06FF\u200c]+)$")


def parse_duration(text: str) -> int | None:
    """'2h', '30m', '1d12h', '۲ ساعت', 'دو ساعت', '90' (minutes). Returns seconds or None."""
    s = norm_digits((text or "").strip().lower())
    if not s or s.startswith("-"):
        return None
    if s.isdigit():
        return int(s) * 60 or None
    for w, n in _FA_NUMWORDS.items():
        s = re.sub(rf"(^|\s){w}(\s)", rf"\g<1>{n}\2", s)
    total = 0.0
    found = False
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([a-z\u0600-\u06FF\u200c]+)", s):
        unit = _UNITS.get(m.group(2).replace("\u200c", ""))
        if unit is None:
            unit = _UNITS.get(m.group(2))
        if unit is None:
            return None
        total += float(m.group(1)) * unit
        found = True
    if not found or total <= 0:
        return None
    return int(total)


def looks_like_duration(text: str) -> bool:
    return parse_duration(text) is not None and not norm_digits(text or "").strip().isdigit()


def fmt_duration(sec: int | float | None, lang: str = "fa") -> str:
    if not sec:
        return "دائمی" if lang == "fa" else "permanent"
    sec = int(sec)
    parts = []
    for n, fa, en in ((86400, "روز", "d"), (3600, "ساعت", "h"), (60, "دقیقه", "m"), (1, "ثانیه", "s")):
        if sec >= n:
            q, sec = divmod(sec, n)
            parts.append(f"{fa_digits(q)} {fa}" if lang == "fa" else f"{q}{en}")
        if len(parts) == 2:
            break
    return (" و " if lang == "fa" else " ").join(parts)


# jalali
def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def fmt_dt(ts: float | None, tz: str, lang: str = "fa", with_tz: bool = True) -> str:
    if not ts:
        return "—"
    d = datetime.fromtimestamp(ts, ZoneInfo(tz))
    if lang == "fa":
        jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
        s = fa_digits(f"{jy}/{jm:02d}/{jd:02d} {d.hour:02d}:{d.minute:02d}")
    else:
        s = d.strftime("%Y-%m-%d %H:%M")
    return f"{s} ({tz})" if with_tz else s


# when parsing
_HHMM = re.compile(r"^(\d{1,2})[:.٫](\d{2})$")


def parse_hhmm(s: str) -> tuple[int, int] | None:
    s = norm_digits((s or "").strip())
    m = _HHMM.match(s)
    if not m:
        if s.isdigit() and 0 <= int(s) <= 23:
            return int(s), 0
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi
    return None


def next_local(h: int, m: int, tz: str, after: float | None = None, weekday: int | None = None) -> float:
    """Next HH:MM in tz after `after`."""
    z = ZoneInfo(tz)
    base = datetime.fromtimestamp(after or now(), z)
    cand = base.replace(hour=h, minute=m, second=0, microsecond=0)
    for _ in range(9):
        if cand.timestamp() > (after or now()) and (weekday is None or cand.weekday() == weekday):
            return cand.timestamp()
        cand = (cand + timedelta(days=1)).replace(hour=h, minute=m)
    return cand.timestamp()


def parse_when(tokens: list[str], tz: str) -> tuple[float | None, str | None, int]:
    """Parse a leading time spec from tokens. Returns (unix_ts, rule, n_tokens_consumed).
    Supported: '2h' / 'in 2h' / '+2h', 'HH:MM', 'tomorrow HH:MM' / 'فردا HH:MM',
    'YYYY-MM-DD HH:MM', optional recurrence prefix 'daily'/'روزانه'/'weekly'/'هفتگی'/'every 6h'."""
    t = [norm_digits(x) for x in tokens]
    i = 0
    rule = "once"
    if i < len(t) and t[i].lower() in ("daily", "روزانه", "هرروز"):
        rule, i = "daily", i + 1
    elif i < len(t) and t[i].lower() in ("weekly", "هفتگی"):
        rule, i = "weekly", i + 1
    elif i + 1 < len(t) and t[i].lower() in ("every", "هر"):
        d = parse_duration(t[i + 1])
        if d and d >= 600:
            return now() + d, f"every:{d}", i + 2
        return None, None, 0
    if i < len(t) and t[i].lower() in ("in", "بعد"):
        i += 1
    if i >= len(t):
        return None, None, 0
    tok = t[i].lstrip("+")
    if rule == "once":
        d = parse_duration(tok) if not parse_hhmm(tok) else None
        if d and not tok.isdigit():
            return now() + d, "once", i + 1
    day_shift = 0
    if tok.lower() in ("tomorrow", "فردا"):
        day_shift, i = 1, i + 1
        if i >= len(t):
            return None, None, 0
        tok = t[i]
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", tok)
    if m and i + 1 < len(t) and parse_hhmm(t[i + 1]):
        h, mi = parse_hhmm(t[i + 1])
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), h, mi, tzinfo=ZoneInfo(tz))
        except ValueError:
            return None, None, 0
        return dt.timestamp(), rule, i + 2
    hm = parse_hhmm(tok)
    if hm and (":" in tok or "." in tok):
        ts = next_local(hm[0], hm[1], tz)
        if day_shift:
            z = ZoneInfo(tz)
            tomorrow = (datetime.now(z) + timedelta(days=1)).replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            ts = tomorrow.timestamp()
        return ts, rule, i + 1
    return None, None, 0


def next_after(rule: str, prev_due: float, tz: str) -> float | None:
    if rule == "once":
        return None
    if rule.startswith("every:"):
        step = int(rule.split(":", 1)[1])
        nxt = prev_due + step
        while nxt <= now():
            nxt += step
        return nxt
    z = ZoneInfo(tz)
    d = datetime.fromtimestamp(prev_due, z)
    days = 1 if rule == "daily" else 7
    nxt = (d + timedelta(days=days)).replace(hour=d.hour, minute=d.minute)
    while nxt.timestamp() <= now():
        nxt = (nxt + timedelta(days=days)).replace(hour=d.hour, minute=d.minute)
    return nxt.timestamp()


def rule_label(rule: str, lang: str) -> str:
    if rule == "daily":
        return "روزانه" if lang == "fa" else "daily"
    if rule == "weekly":
        return "هفتگی" if lang == "fa" else "weekly"
    if rule.startswith("every:"):
        return ("هر " if lang == "fa" else "every ") + fmt_duration(int(rule[6:]), lang)
    return "یک‌بار" if lang == "fa" else "once"


def in_window(start: tuple[int, int], end: tuple[int, int], tz: str, at: float | None = None) -> bool:
    """True if `at` is within [start, end), including windows that cross midnight."""
    d = datetime.fromtimestamp(at or now(), ZoneInfo(tz))
    cur = d.hour * 60 + d.minute
    s, e = start[0] * 60 + start[1], end[0] * 60 + end[1]
    if s == e:
        return False
    return s <= cur < e if s < e else (cur >= s or cur < e)


# buttons in text
_BTN = re.compile(r"\[([^\[\]\n]{1,40})\]\((https?://[^\s)]{3,300}|tg://[^\s)]{3,200})\)")


def split_buttons(text: str) -> tuple[str, list[list[tuple[str, str]]]]:
    """Extract `[Label](https://url)` markup into button rows (one row per source line)."""
    rows: list[list[tuple[str, str]]] = []
    out_lines = []
    for line in (text or "").split("\n"):
        found = _BTN.findall(line)
        if found:
            rows.append([(html.unescape(a), b) for a, b in found])
            rest = _BTN.sub("", line).strip()
            if rest:
                out_lines.append(rest)
        else:
            out_lines.append(line)
    return "\n".join(out_lines).strip(), rows


def short(s: str | None, n: int = 120) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"
