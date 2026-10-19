"""Everyday utilities: Jalali/Gregorian dates, world time, a safe calculator, countdowns, unit conversion,
random picks and timers. All local, no external services."""
from __future__ import annotations

import ast
import math
import operator
import re
import secrets
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pilot.commands import command, say
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.util import esc, fa_digits, fmt_duration, gregorian_to_jalali, norm_digits, num, parse_duration

J_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
FA_WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd + ((jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    months = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    for gm in range(1, 13):
        if gd <= months[gm]:
            break
        gd -= months[gm]
    return gy, gm, gd


def parse_date(s: str) -> date | None:
    """YYYY-MM-DD or YYYY/MM/DD; years below 1700 are read as Jalali."""
    m = re.fullmatch(r"(\d{3,4})[-/.](\d{1,2})[-/.](\d{1,2})", norm_digits(s.strip()))
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        if y < 1700:
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                return None
            y, mo, d = jalali_to_gregorian(y, mo, d)
        return date(y, mo, d)
    except ValueError:
        return None


def jalali_str(d: date, lang: str = "fa") -> str:
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    if lang == "fa":
        return f"{FA_WEEKDAYS[d.weekday()]} {fa_digits(jd)} {J_MONTHS[jm - 1]} {fa_digits(jy)}"
    return f"{jy}/{jm:02d}/{jd:02d}"


@command("jalali", "date", "today", scope="any", admin=False, section="utils")
async def k_jalali(c: Cmd):
    if c.args:
        d = parse_date(c.args[0])
        if d is None:
            return await say(c, c.t("تاریخ میلادی بدهید: <code>/jalali 2025-03-21</code>", "Give a date: <code>/jalali 2025-03-21</code>"))
    else:
        d = datetime.now(ZoneInfo(c.tz)).date()
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    await say(c, c.t(f"📅 {jalali_str(d)}\n{fa_digits(d.isoformat())} میلادی", f"📅 {d.isoformat()} · Jalali {jy}/{jm:02d}/{jd:02d}"))


@command("gregorian", "miladi", scope="any", admin=False, section="utils")
async def k_gregorian(c: Cmd):
    d = parse_date(c.args[0]) if c.args else None
    if d is None:
        return await say(c, c.t("تاریخ شمسی بدهید: <code>/gregorian 1404/01/01</code>", "Give a Jalali date: <code>/gregorian 1404/01/01</code>"))
    await say(c, c.t(f"📅 {fa_digits(d.isoformat())} ({d.strftime('%A')})", f"📅 {d.isoformat()} ({d.strftime('%A')})"))


CITIES = {"تهران": "Asia/Tehran", "tehran": "Asia/Tehran", "ایران": "Asia/Tehran", "کابل": "Asia/Kabul", "kabul": "Asia/Kabul",
          "دبی": "Asia/Dubai", "dubai": "Asia/Dubai", "استانبول": "Europe/Istanbul", "istanbul": "Europe/Istanbul",
          "لندن": "Europe/London", "london": "Europe/London", "پاریس": "Europe/Paris", "paris": "Europe/Paris",
          "برلین": "Europe/Berlin", "berlin": "Europe/Berlin", "مسکو": "Europe/Moscow", "moscow": "Europe/Moscow",
          "نیویورک": "America/New_York", "new york": "America/New_York", "newyork": "America/New_York",
          "تورنتو": "America/Toronto", "toronto": "America/Toronto", "لس آنجلس": "America/Los_Angeles", "la": "America/Los_Angeles",
          "توکیو": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "پکن": "Asia/Shanghai", "beijing": "Asia/Shanghai",
          "دهلی": "Asia/Kolkata", "delhi": "Asia/Kolkata", "سیدنی": "Australia/Sydney", "sydney": "Australia/Sydney",
          "ایروان": "Asia/Yerevan", "باکو": "Asia/Baku", "تفلیس": "Asia/Tbilisi", "دوشنبه": "Asia/Dushanbe",
          "utc": "UTC", "گرینویچ": "UTC"}


@command("time", "now", scope="any", admin=False, section="utils")
async def k_time(c: Cmd):
    q = " ".join(c.args).strip().lower()
    tz = c.tz
    if q:
        tz = CITIES.get(q)
        if not tz and c.args and "/" in c.args[0]:
            try:
                ZoneInfo(c.args[0])
                tz = c.args[0]
            except Exception:  # noqa: BLE001
                tz = None
        if not tz:
            return await say(c, c.t("شهر را نشناختم. مثل <code>/time لندن</code> یا <code>/time Europe/Berlin</code>",
                                    "Unknown place. Try <code>/time london</code> or <code>/time Europe/Berlin</code>"))
    d = datetime.now(ZoneInfo(tz))
    await say(c, c.t(f"🕒 {fa_digits(d.strftime('%H:%M'))} · {jalali_str(d.date())} · <code>{esc(tz)}</code>",
                     f"🕒 {d.strftime('%H:%M')} · {d.strftime('%a %Y-%m-%d')} · <code>{esc(tz)}</code>"))


# calculator: a small AST walker, no eval
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10,
          "ln": math.log, "pi": math.pi, "e": math.e, "radical": math.sqrt, "جذر": math.sqrt}


class CalcError(ValueError):
    pass


def _ev(n, depth: int = 0):
    if depth > 40:
        raise CalcError("too deep")
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        a, b = _ev(n.left, depth + 1), _ev(n.right, depth + 1)
        if isinstance(n.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise CalcError("power too large")
        if isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)) and b == 0:
            raise CalcError("division by zero")
        return _OPS[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
        return _OPS[type(n.op)](_ev(n.operand, depth + 1))
    if isinstance(n, ast.Name) and n.id in _FUNCS and isinstance(_FUNCS[n.id], float):
        return _FUNCS[n.id]
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and len(n.args) == 1 and not n.keywords:
        return _FUNCS[n.func.id](_ev(n.args[0], depth + 1))
    raise CalcError("unsupported")


def calc(expr: str) -> float | int:
    s = norm_digits(expr).replace("×", "*").replace("÷", "/").replace("^", "**").replace("٪", "/100").replace("−", "-")
    s = s.replace("،", ",").replace("٫", ".").strip()
    if not s or len(s) > 200 or not re.fullmatch(r"[0-9a-zجذر+\-*/%().,\s]+", s.lower()):
        raise CalcError("bad characters")
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError:
        raise CalcError("syntax")
    v = _ev(tree.body)
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        raise CalcError("not a number")
    if isinstance(v, int) and abs(v) > 10 ** 30:
        raise CalcError("too large")
    return v


def fmt_num(v) -> str:
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return f"{int(v):,}"
        return f"{v:,.6f}".rstrip("0").rstrip(".")
    return f"{v:,}"


@command("calc", "calculate", scope="any", admin=False, section="utils")
async def k_calc(c: Cmd):
    if not c.rest.strip():
        return await say(c, c.t("<code>/calc (12+8)*3</code> · <code>/calc sqrt(16)</code>", "<code>/calc (12+8)*3</code>"))
    try:
        v = calc(c.rest)
    except CalcError:
        return await say(c, c.t("❌ عبارت را نفهمیدم. فقط عدد و + − × ÷ ^ ( ) و sqrt.", "❌ Can't evaluate. Numbers, + - * / ^ ( ) and sqrt only."))
    out = fmt_num(v)
    await say(c, f"🧮 <code>{esc(c.rest.strip()[:80])}</code> = <b>{fa_digits(out) if c.lang == 'fa' else out}</b>")


@command("countdown", "daysuntil", scope="any", admin=False, section="utils")
async def k_countdown(c: Cmd):
    d = parse_date(c.args[0]) if c.args else None
    if d is None:
        return await say(c, c.t("<code>/countdown 1404/01/01 نوروز</code>", "<code>/countdown 2026-01-01 New year</code>"))
    title = " ".join(c.args[1:])[:60]
    today = datetime.now(ZoneInfo(c.tz)).date()
    n = (d - today).days
    lbl = f"«{esc(title)}» " if title else ""
    if n > 0:
        txt = c.t(f"⏳ {lbl}{num(n, 'fa')} روز مانده ({jalali_str(d)})", f"⏳ {lbl}{n} days left ({d.isoformat()})")
    elif n == 0:
        txt = c.t(f"🎯 {lbl}امروز است.", f"🎯 {lbl}is today.")
    else:
        txt = c.t(f"⌛️ {lbl}{num(-n, 'fa')} روز گذشته.", f"⌛️ {lbl}{-n} days ago.")
    await say(c, txt)


@command("daysbetween", "datediff", scope="any", admin=False, section="utils")
async def k_daysbetween(c: Cmd):
    ds = [parse_date(a) for a in c.args[:2]]
    if len(ds) < 2 or None in ds:
        return await say(c, c.t("<code>/daysbetween 1403/01/01 1404/01/01</code>", "<code>/daysbetween 2025-01-01 2025-12-31</code>"))
    n = abs((ds[1] - ds[0]).days)
    await say(c, c.t(f"📏 {num(n, 'fa')} روز ({num(n // 7, 'fa')} هفته و {num(n % 7, 'fa')} روز)", f"📏 {n} days ({n // 7} weeks {n % 7} days)"))


@command("choose", "pick", scope="any", admin=False, section="utils")
async def k_choose(c: Cmd):
    opts = [o.strip() for o in re.split(r"[|،,\n]| یا | or ", c.rest) if o.strip()]
    if len(opts) < 2:
        return await say(c, c.t("<code>/choose چای | قهوه | آب</code>", "<code>/choose tea | coffee | water</code>"))
    await say(c, f"🎲 <b>{esc(secrets.choice(opts[:50])[:200])}</b>")


@command("random", "roll", scope="any", admin=False, section="utils")
async def k_random(c: Cmd):
    nums = [int(norm_digits(a)) for a in c.args[:2] if norm_digits(a).lstrip("-").isdigit()]
    lo, hi = (1, 100) if not nums else ((1, nums[0]) if len(nums) == 1 else (min(nums), max(nums)))
    if hi - lo > 10 ** 12 or hi < lo:
        return await say(c, c.t("بازه نامعتبر.", "Invalid range."))
    v = lo + secrets.randbelow(hi - lo + 1)
    await say(c, c.t(f"🎲 {num(v, 'fa')} (از {num(lo, 'fa')} تا {num(hi, 'fa')})", f"🎲 {v} ({lo}–{hi})"))


# units: (dimension, factor to base)
UNITS = {
    "m": ("len", 1), "متر": ("len", 1), "km": ("len", 1000), "کیلومتر": ("len", 1000), "cm": ("len", 0.01), "سانتیمتر": ("len", 0.01),
    "سانت": ("len", 0.01), "mm": ("len", 0.001), "میلیمتر": ("len", 0.001), "mi": ("len", 1609.344), "mile": ("len", 1609.344),
    "مایل": ("len", 1609.344), "ft": ("len", 0.3048), "فوت": ("len", 0.3048), "in": ("len", 0.0254), "اینچ": ("len", 0.0254),
    "kg": ("mass", 1), "کیلو": ("mass", 1), "کیلوگرم": ("mass", 1), "g": ("mass", 0.001), "گرم": ("mass", 0.001),
    "lb": ("mass", 0.45359237), "پوند": ("mass", 0.45359237), "oz": ("mass", 0.028349523125), "اونس": ("mass", 0.028349523125),
    "t": ("mass", 1000), "تن": ("mass", 1000), "مثقال": ("mass", 0.004608),
    "l": ("vol", 1), "لیتر": ("vol", 1), "ml": ("vol", 0.001), "میلی‌لیتر": ("vol", 0.001), "gal": ("vol", 3.785411784), "گالن": ("vol", 3.785411784),
    "b": ("data", 1), "بایت": ("data", 1), "kb": ("data", 1024), "کیلوبایت": ("data", 1024), "mb": ("data", 1024 ** 2),
    "مگ": ("data", 1024 ** 2), "مگابایت": ("data", 1024 ** 2), "gb": ("data", 1024 ** 3), "گیگ": ("data", 1024 ** 3),
    "گیگابایت": ("data", 1024 ** 3), "tb": ("data", 1024 ** 4), "ترابایت": ("data", 1024 ** 4),
    "s": ("time", 1), "ثانیه": ("time", 1), "min": ("time", 60), "دقیقه": ("time", 60), "h": ("time", 3600), "ساعت": ("time", 3600),
    "d": ("time", 86400), "روز": ("time", 86400), "week": ("time", 604800), "هفته": ("time", 604800),
    "kmh": ("speed", 1 / 3.6), "km/h": ("speed", 1 / 3.6), "کیلومتربرساعت": ("speed", 1 / 3.6), "m/s": ("speed", 1), "mph": ("speed", 0.44704),
    "c": ("temp", None), "سانتیگراد": ("temp", None), "سلسیوس": ("temp", None), "f": ("temp", None), "فارنهایت": ("temp", None),
    "k": ("temp", None), "کلوین": ("temp", None),
}
_TEMP = {"c": "c", "سانتیگراد": "c", "سلسیوس": "c", "f": "f", "فارنهایت": "f", "k": "k", "کلوین": "k"}


def convert(v: float, a: str, b: str) -> float:
    a, b = a.lower().replace("\u200c", ""), b.lower().replace("\u200c", "")
    ua, ub = UNITS.get(a), UNITS.get(b)
    if not ua or not ub or ua[0] != ub[0]:
        raise ValueError("units")
    if ua[0] == "temp":
        x, y = _TEMP[a], _TEMP[b]
        cel = v if x == "c" else ((v - 32) * 5 / 9 if x == "f" else v - 273.15)
        return cel if y == "c" else (cel * 9 / 5 + 32 if y == "f" else cel + 273.15)
    return v * ua[1] / ub[1]


@command("convert", "unit", scope="any", admin=False, section="utils")
async def k_convert(c: Cmd):
    a = [x for x in c.args if x.lower() not in ("to", "به", "=", "->", "در")]
    try:
        v = float(norm_digits(a[0]).replace("٫", ".").replace(",", ""))
        r = convert(v, a[1], a[2])
    except (IndexError, ValueError):
        return await say(c, c.t("<code>/convert 5 km mi</code> · <code>/convert 30 c f</code> · <code>/convert 2 گیگ مگ</code>",
                                "<code>/convert 5 km mi</code> · <code>/convert 30 c f</code>"))
    out = fmt_num(round(r, 6))
    await say(c, f"🔁 {esc(a[0])} {esc(a[1])} = <b>{fa_digits(out) if c.lang == 'fa' else out}</b> {esc(a[2])}")


@command("timer", scope="any", admin=False, section="utils")
async def k_timer(c: Cmd):
    from pilot import commands2 as K2
    if not c.actor_id:
        return await say(c, c.t("با حساب ناشناس نمیشه.", "Not for anonymous admins."))
    a = list(c.args)
    dur = None
    used = 0
    for n in (2, 1):
        if len(a) >= n:
            d = parse_duration(" ".join(a[:n]))
            if d:
                dur, used = d, n
                break
    if not dur or dur < 10 or dur > 7 * 86400:
        return await say(c, c.t("<code>/timer 10m چای</code> · تایمر ۲۵ دقیقه", "<code>/timer 10m tea</code>"))
    n_open = await RT.db.val("SELECT COUNT(*) FROM rz_reminders WHERE user_id=? AND status='active'", (c.actor_id,), 0)
    if n_open >= 25:
        return await say(c, c.t("سقف ۲۵ یادآوری پر شده.", "Limit of 25 reminders reached."))
    label = " ".join(a[used:])[:200] or c.t("تایمر تمام شد", "Timer done")
    rid, _ = await K2.create_reminder(c, time.time() + dur, label, "here" if c.is_group else "me")
    await say(c, c.t(f"⏱ تایمر {fmt_duration(dur, 'fa')} (#{num(rid, 'fa')})", f"⏱ Timer {fmt_duration(dur, 'en')} (#{rid})"))
