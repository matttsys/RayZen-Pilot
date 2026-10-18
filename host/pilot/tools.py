"""Admin tools: digest, health, backup/restore/clone, watchdog, privacy, /why, onboarding."""
from __future__ import annotations

import json
import logging
import time

from telegram import ChatPermissions

from pilot import audit, authz, locks, logs, settings as S, tg
from pilot.db import CHAT_TABLES, jd, jl
from pilot.rt import RT
from pilot.util import esc, fmt_dt, fmt_duration, now, rule_label, short

log = logging.getLogger("pilot.tools")
NEEDED = [("delete", "فیلترها، /del، /purge", "filters, /del, /purge"),
          ("restrict", "بن/سکوت، قفل گروه، کپچا", "ban/mute, group locks, captcha"),
          ("pin", "سنجاق و اطلاعیهٔ سنجاق‌شده", "pins, pinned announcements"),
          ("invite", "میز درخواست عضویت، لینک دعوت", "join-request desk, invite link"),
          ("topics", "ابزار تاپیک (انجمن)", "topic toolkit (forums)")]


def _fa(lang: str) -> bool:
    return lang == "fa"


async def rights_lines(chat_id: int, lang: str, is_forum: bool = True) -> tuple[list[str], list[str]]:
    bm = await authz.bot_member(chat_id, fresh=True)
    have, miss = [], []
    for r, fa, en in NEEDED:
        if r == "topics" and not is_forum:
            continue
        line = authz.right_label(r, lang)
        (have if authz.has_right(bm, r) else miss).append(line)
    return have, miss


async def onboarding_text(chat, lang: str) -> tuple[str, list]:
    have, miss = await rights_lines(chat.id, lang, bool(chat.is_forum))
    fa = _fa(lang)
    txt = "<b>RayZen Pilot</b>"
    if miss:
        txt += ("\nدسترسی لازم: " if fa else "\nMissing rights: ") + ("، " if fa else ", ").join(esc(x) for x in miss)
    return txt, [[("⚙️ تنظیمات" if fa else "⚙️ Settings", "st:main"), ("❓ راهنما" if fa else "❓ Help", "hp:main")]]


# deletion diagnostics
async def why_text(chat_id: int, lang: str, tz: str) -> str:
    fa = _fa(lang)
    eff = await S.load(chat_id)
    on = [(k, src) for k, src in await S.enabled_filters(chat_id)]
    lines = []
    if eff["paused"][0]:
        lines.append("⏸ فیلترها متوقف‌اند (/resume)" if fa else "⏸ Filters paused (/resume)")
    if on:
        lines.append(("فیلترهای روشن: " if fa else "Filters on: ") + ("، " if fa else ", ").join(esc(S.label(k, lang)) for k, _ in on))
    else:
        lines.append("هیچ فیلتری روشن نیست." if fa else "No filters are on.")
    rows = await RT.db.all("SELECT params, ts FROM rz_actions WHERE chat_id=? AND action='auto_delete' AND ts>? ORDER BY id DESC LIMIT 200",
                           (chat_id, now() - 7 * 86400))
    by: dict[str, list] = {}
    for r in rows:
        rule = (jl(r["params"], {}) or {}).get("rule", "?")
        by.setdefault(rule, []).append(r["ts"])
    if by:
        lines.append("\nحذف‌های ۷ روز اخیر:" if fa else "\nDeleted in the last 7 days:")
        for rule, ts in sorted(by.items(), key=lambda x: -len(x[1])):
            lbl = S.label(rule, lang) if rule in S.SPECS else rule
            lines.append(f"• {esc(lbl)}: {len(ts)} · {'آخرین' if fa else 'last'} {fmt_dt(max(ts), tz, lang, with_tz=False)}")
    else:
        lines.append("ربات در ۷ روز اخیر پیامی پاک نکرده." if fa else "The bot deleted nothing in the last 7 days.")
    if on:
        lines.append("/pause · /safereset")
    return "\n".join(lines)


# health
async def health_text(chat_id: int, lang: str, tz: str, is_forum: bool) -> str:
    fa = _fa(lang)
    t = now()
    gc = await tg.call(RT.bot.get_chat, chat_id=chat_id)
    full = gc.value if gc.ok else None
    title = esc(getattr(full, "title", "") or chat_id)
    L = [f"<b>{title}</b>"]
    have, miss = await rights_lines(chat_id, lang, is_forum)
    L.append(("دسترسی ربات: " if fa else "Bot rights: ") + " ".join([f"✅{esc(x)}" for x in have] + [f"❌{esc(x)}" for x in miss]))
    cnt = await tg.call(RT.bot.get_chat_member_count, chat_id=chat_id)
    if cnt.ok:
        L.append(("اعضا: " if fa else "Members: ") + str(cnt.value))
    if full is not None:
        sm = getattr(full, "slow_mode_delay", None)
        if sm:
            L.append(("اسلومود: " if fa else "Slow mode: ") + fmt_duration(sm, lang))
    lays = await locks.layers(chat_id)
    L.append(("قفل‌ها: " if fa else "Locks: ") + ("، ".join(locks.describe_layer(la, lang, tz) for la in lays) if lays else ("هیچ" if fa else "none")))
    night = await RT.db.one("SELECT * FROM rz_night WHERE chat_id=? AND enabled=1", (chat_id,))
    if night:
        L.append(f"{'شبانه' if fa else 'Night'}: {night['start']}-{night['end']}")
    on = await S.enabled_filters(chat_id)
    L.append(("فیلترها: " if fa else "Filters: ") + (("، " if fa else ", ").join(esc(S.label(k, lang)) for k, _ in on) if on else ("خاموش" if fa else "off")))
    if await S.get(chat_id, "paused"):
        L.append("⏸ " + ("فیلترها متوقف‌اند" if fa else "Filters paused"))
    eff = await S.values(chat_id)
    L.append(f"{'خوشامد' if fa else 'Welcome'}: {S.fmt_value('welcome_on', eff['welcome_on'], lang)} · "
             f"{'خداحافظی' if fa else 'Goodbye'}: {S.fmt_value('goodbye_on', eff['goodbye_on'], lang)}")
    r = await logs.route(chat_id)
    if r and r["status"] != "disabled":
        q = await RT.db.one("SELECT SUM(status='pending') p, SUM(status='held') h, SUM(status='dropped') d FROM rz_log_queue WHERE chat_id=?", (chat_id,))
        pend = int(q["p"] or 0) + int(q["h"] or 0)
        L.append(f"{'لاگ' if fa else 'Logs'}: {logs.status_label(r['status'], lang)} · <code>{r['dest_id']}</code>" +
                 (f" · {'در صف' if fa else 'queued'} {pend}" if pend else "") +
                 (f"\n⚠️ {esc(short(r['last_error'], 120))}" if r["last_error"] and r["status"] == "broken" else ""))
    else:
        L.append(f"{'لاگ: تنظیم نشده' if fa else 'Logs: not set'}")
    ann = await RT.db.one("SELECT COUNT(*) n, MIN(next_run) nx FROM rz_announcements WHERE chat_id=? AND status='active'", (chat_id,))
    rem = await RT.db.val("SELECT COUNT(*) FROM rz_reminders WHERE chat_id=? AND status IN ('active','waiting_dm')", (chat_id,), 0)
    evn = await RT.db.val("SELECT COUNT(*) FROM rz_events WHERE chat_id=? AND status='active'", (chat_id,), 0)
    if ann["n"] or rem or evn:
        L.append(f"{'اطلاعیه' if fa else 'Announcements'}: {ann['n']} · {'یادآوری' if fa else 'Reminders'}: {rem} · {'رویداد' if fa else 'Events'}: {evn}")
    ap = await RT.db.val("SELECT COUNT(*) FROM rz_appeals WHERE chat_id=? AND status='pending'", (chat_id,), 0)
    tk = await RT.db.val("SELECT COUNT(*) FROM rz_tickets WHERE chat_id=? AND status IN ('open','claimed')", (chat_id,), 0)
    jr = await RT.db.val("SELECT COUNT(*) FROM rz_join_requests WHERE chat_id=? AND status='pending'", (chat_id,), 0)
    if ap or tk or jr:
        L.append(f"{'اعتراض' if fa else 'Appeals'}: {ap} · {'تیکت' if fa else 'Tickets'}: {tk} · {'درخواست عضویت' if fa else 'Join requests'}: {jr}")
    from pilot.ui import ai_status_label
    used = await RT.db.val("SELECT COUNT(*) FROM rz_ai_usage WHERE chat_id=? AND ts>?", (chat_id, t - 3600), 0)
    L.append(f"AI: {ai_status_label(lang)}" + (f" · {used}/{RT.cfg.ai_group_per_hour}" if RT.cfg.ai_configured else ""))
    errs = await RT.db.all("SELECT area, detail, ts FROM rz_errors WHERE chat_id=? AND ts>? ORDER BY id DESC LIMIT 3", (chat_id, t - 86400))
    nerr = await RT.db.val("SELECT COUNT(*) FROM rz_errors WHERE chat_id=? AND ts>?", (chat_id, t - 86400), 0)
    if nerr:
        L.append(f"{'خطا (۲۴ ساعت)' if fa else 'Errors (24h)'}: {nerr}")
        L += [f"• {esc(e['area'])}: {esc(short(e['detail'], 90))}" for e in errs]
    return "\n".join(L)


# digest
async def digest_facts(chat_id: int, hours: int) -> dict:
    since = now() - hours * 3600
    acts = await RT.db.all("SELECT action, outcome, params FROM rz_actions WHERE chat_id=? AND ts>?", (chat_id, since))
    by: dict[str, int] = {}
    rules: dict[str, int] = {}
    failed = 0
    for a in acts:
        by[a["action"]] = by.get(a["action"], 0) + 1
        if a["outcome"] != "ok":
            failed += 1
        if a["action"] == "auto_delete":
            rk = (jl(a["params"], {}) or {}).get("rule", "?")
            rules[rk] = rules.get(rk, 0) + 1
    return {
        "hours": hours, "actions": by, "auto_delete_rules": rules, "failed_actions": failed,
        "settings_changes": await RT.db.val("SELECT COUNT(*) FROM rz_settings WHERE chat_id=? AND set_at>?", (chat_id, since), 0),
        "pending_appeals": await RT.db.val("SELECT COUNT(*) FROM rz_appeals WHERE chat_id=? AND status='pending'", (chat_id,), 0),
        "open_tickets": await RT.db.val("SELECT COUNT(*) FROM rz_tickets WHERE chat_id=? AND status IN ('open','claimed')", (chat_id,), 0),
        "pending_join_requests": await RT.db.val("SELECT COUNT(*) FROM rz_join_requests WHERE chat_id=? AND status='pending'", (chat_id,), 0),
        "errors": await RT.db.val("SELECT COUNT(*) FROM rz_errors WHERE chat_id=? AND ts>?", (chat_id, since), 0),
        "failed_jobs": await RT.db.val("SELECT COUNT(*) FROM rz_announcements WHERE chat_id=? AND status IN ('failed','uncertain')", (chat_id,), 0),
        "log_status": ((await logs.route(chat_id)) or {}).get("status", "none"),
    }


async def digest_text(chat_id: int, lang: str, tz: str, hours: int = 24, ai_note: str | None = None) -> str:
    f = await digest_facts(chat_id, hours)
    fa = _fa(lang)
    row = await RT.db.one("SELECT title FROM rz_chats WHERE chat_id=?", (chat_id,))
    L = [f"<b>{'گزارش' if fa else 'Report'} {hours} {'ساعت' if fa else 'h'}</b> · {esc((row or {}).get('title') or chat_id)}"]
    if f["actions"]:
        L.append("• " + "، ".join(f"{audit.action_name(k, lang)}: {v}" for k, v in sorted(f["actions"].items(), key=lambda x: -x[1])))
    else:
        L.append("• " + ("کاری انجام نشده." if fa else "No actions."))
    if f["auto_delete_rules"]:
        L.append("• " + ("حذف خودکار: " if fa else "Auto-deletes: ") +
                 "، ".join(f"{S.label(k, lang) if k in S.SPECS else k}: {v}" for k, v in f["auto_delete_rules"].items()))
    if f["failed_actions"]:
        L.append(f"• {'ناموفق' if fa else 'Failed'}: {f['failed_actions']}")
    if f["settings_changes"]:
        L.append(f"• {'تغییر تنظیمات' if fa else 'Settings changes'}: {f['settings_changes']}")
    if f["pending_appeals"] or f["open_tickets"] or f["pending_join_requests"]:
        L.append(f"• {'اعتراض' if fa else 'Appeals'}: {f['pending_appeals']} · {'تیکت' if fa else 'Tickets'}: {f['open_tickets']}"
                 f" · {'درخواست عضویت' if fa else 'Join requests'}: {f['pending_join_requests']}")
    if f["errors"] or f["failed_jobs"]:
        L.append(f"• {'خطا' if fa else 'Errors'}: {f['errors']} · {'اطلاعیهٔ ناموفق' if fa else 'Failed announcements'}: {f['failed_jobs']}")
    if ai_note:
        L.append("\n🤖 " + esc(ai_note[:600]))
    return "\n".join(L)


# backup / restore / clone
SECTIONS = ("settings", "rules", "answers", "blockwords", "night")
NEVER_IMPORT = {"paused", "announce_topic"}


async def export_config(chat_id: int) -> dict:
    rows = await RT.db.all("SELECT key, value FROM rz_settings WHERE chat_id=?", (chat_id,))
    sett = {r["key"]: jl(r["value"]) for r in rows if r["key"] in S.SPECS and r["key"] not in NEVER_IMPORT}
    rules = await RT.db.val("SELECT body FROM rz_rules WHERE chat_id=?", (chat_id,))
    ans = await RT.db.all("SELECT name, body, media_type, file_id, keywords FROM rz_answers WHERE chat_id=? ORDER BY name", (chat_id,))
    bw = [r["word"] for r in await RT.db.all("SELECT word FROM rz_blockwords WHERE chat_id=? ORDER BY word", (chat_id,))]
    night = await RT.db.one("SELECT start, end, deny FROM rz_night WHERE chat_id=? AND enabled=1", (chat_id,))
    return {"rayzen_backup": 1, "exported_at": int(now()), "settings": sett, "rules": rules,
            "answers": [{**a, "keywords": jl(a["keywords"], [])} for a in ans], "blockwords": bw,
            "night": {"start": night["start"], "end": night["end"], "deny": jl(night["deny"], locks.SEND)} if night else None}


def validate_config(d) -> tuple[dict | None, list[str]]:
    errs: list[str] = []
    if not isinstance(d, dict) or d.get("rayzen_backup") != 1:
        return None, ["not a RayZen backup (version 1)"]
    out = {"settings": {}, "rules": None, "answers": [], "blockwords": [], "night": None}
    for k, v in (d.get("settings") or {}).items():
        if k not in S.SPECS or k in NEVER_IMPORT:
            errs.append(f"ignored unknown setting {str(k)[:30]}")
            continue
        try:
            out["settings"][k] = S.coerce(S.SPECS[k], v)
        except (ValueError, TypeError) as e:
            errs.append(f"{k}: {e}")
    r = d.get("rules")
    if isinstance(r, str) and r.strip():
        out["rules"] = r[:3800]
    for a in (d.get("answers") or [])[:200]:
        if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("body", ""), str):
            from pilot.content import NAME_RE
            if NAME_RE.match(a["name"]):
                kws = [str(x)[:40] for x in (a.get("keywords") or []) if isinstance(x, str)][:10]
                out["answers"].append({"name": a["name"].lower(), "body": a.get("body", "")[:3800], "media_type": a.get("media_type"),
                                       "file_id": a.get("file_id"), "keywords": kws})
                continue
        errs.append("ignored an invalid answer")
    out["blockwords"] = [str(w).lower()[:60] for w in (d.get("blockwords") or []) if isinstance(w, str) and w.strip()][:500]
    n = d.get("night")
    if isinstance(n, dict):
        from pilot.util import parse_hhmm
        if parse_hhmm(str(n.get("start"))) and parse_hhmm(str(n.get("end"))):
            deny = [k for k in (n.get("deny") or locks.SEND) if k in locks.KEYS]
            out["night"] = {"start": str(n["start"]), "end": str(n["end"]), "deny": deny or locks.SEND}
    return out, errs


async def diff_config(chat_id: int, new: dict, lang: str, sections: set[str], protections: bool) -> list[str]:
    fa = _fa(lang)
    cur = await S.values(chat_id)
    L = []
    if "settings" in sections:
        for k, v in new["settings"].items():
            if S.SPECS[k].enforce and v and not protections:
                if cur[k] != v:
                    L.append(f"⏭ {esc(S.label(k, lang))}: {'رد شد (فیلتر)' if fa else 'skipped (filter)'}")
                continue
            if cur[k] != v:
                warn = " ⚠️" if S.SPECS[k].enforce and v else ""
                L.append(f"• {esc(S.label(k, lang))}: {esc(S.fmt_value(k, cur[k], lang))} → {esc(S.fmt_value(k, v, lang))}{warn}")
    if "rules" in sections and new["rules"] is not None:
        old = await RT.db.val("SELECT body FROM rz_rules WHERE chat_id=?", (chat_id,))
        if (old or "") != new["rules"]:
            L.append("• " + ("قوانین" if fa else "rules"))
    if "answers" in sections and new["answers"]:
        names = {r["name"] for r in await RT.db.all("SELECT name FROM rz_answers WHERE chat_id=?", (chat_id,))}
        add = [a["name"] for a in new["answers"] if a["name"] not in names]
        upd = [a["name"] for a in new["answers"] if a["name"] in names]
        L.append("• " + (f"پاسخ‌ها: {len(add)} جدید، {len(upd)} تغییر" if fa else f"answers: {len(add)} new, {len(upd)} changed"))
    if "blockwords" in sections and new["blockwords"]:
        L.append("• " + (f"کلمات ممنوع: {len(new['blockwords'])}" if fa else f"blocked words: {len(new['blockwords'])}"))
    if "night" in sections and new["night"]:
        L.append("• " + (f"شبانه {new['night']['start']}-{new['night']['end']}" if fa else
                         f"night {new['night']['start']}-{new['night']['end']}"))
    return L


async def apply_config(chat_id: int, new: dict, actor: int, sections: set[str], protections: bool) -> list[str]:
    done = []
    if "settings" in sections:
        n = 0
        for k, v in new["settings"].items():
            if S.SPECS[k].enforce and v and not protections:
                continue
            await S.set_value(chat_id, k, v, actor, source="import")
            n += 1
        done.append(f"settings:{n}")
    if "rules" in sections and new["rules"] is not None:
        await RT.db.run("INSERT OR REPLACE INTO rz_rules VALUES(?,?,?,?)", (chat_id, new["rules"], actor, now()))
        done.append("rules")
    if "answers" in sections:
        for a in new["answers"]:
            await RT.db.run("INSERT OR REPLACE INTO rz_answers VALUES(?,?,?,?,?,?,?,?)",
                            (chat_id, a["name"], a["body"], a["media_type"], a["file_id"], jd(a["keywords"]), actor, now()))
        done.append(f"answers:{len(new['answers'])}")
    if "blockwords" in sections:
        for w in new["blockwords"]:
            await RT.db.run("INSERT OR IGNORE INTO rz_blockwords VALUES(?,?)", (chat_id, w))
        done.append(f"blockwords:{len(new['blockwords'])}")
    if "night" in sections and new["night"]:
        from pilot.planner import set_night
        await set_night(chat_id, new["night"]["start"], new["night"]["end"], actor, new["night"]["deny"])
        done.append("night")
    S.invalidate(chat_id)
    await audit.record(chat_id, actor, "admin", "restore", params={"sections": sorted(sections), "protections": protections, "done": done},
                       log_line=f"#CONFIG_IMPORT {', '.join(done)} · <code>{actor}</code>")
    return done


# watchdog
async def watchdog_sweep() -> None:
    rows = await RT.db.all("SELECT c.chat_id FROM rz_chats c LEFT JOIN rz_watch w ON w.chat_id=c.chat_id "
                           "WHERE c.bot_status IN ('administrator','member') AND (w.last_check IS NULL OR w.last_check<?) LIMIT 15",
                           (now() - 1800,))
    for r in rows:
        if await S.get(r["chat_id"], "watch_on"):
            await watchdog_check(r["chat_id"], notify=True)
        else:
            await RT.db.run("INSERT INTO rz_watch(chat_id,last_check) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET last_check=excluded.last_check",
                            (r["chat_id"], now()))


async def watchdog_check(chat_id: int, notify: bool = True) -> dict:
    cur = await locks.current(chat_id)
    w = await RT.db.one("SELECT * FROM rz_watch WHERE chat_id=?", (chat_id,))
    if not cur.ok:
        await RT.db.run("INSERT INTO rz_watch(chat_id,last_check) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET last_check=excluded.last_check", (chat_id, now()))
        return {"ok": False, "error": cur.desc}
    st = await RT.db.one("SELECT applied FROM rz_perm_state WHERE chat_id=?", (chat_id,))
    expected = jl(st["applied"]) if st else (jl(w["last_perms"]) if w else None)
    drift = [k for k in locks.KEYS if expected and expected.get(k) != cur.value.get(k)]
    await RT.db.run("INSERT INTO rz_watch(chat_id,last_perms,last_check,drift) VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
                    "last_perms=excluded.last_perms,last_check=excluded.last_check,drift=excluded.drift",
                    (chat_id, jd(cur.value), now(), jd(drift) if drift else (w or {}).get("drift")))
    if drift and notify and not st:
        await watchdog_notify(chat_id, drift, expected, cur.value)
    return {"ok": True, "drift": drift, "current": cur.value, "expected": expected}


def _perm_name(k: str, lang: str) -> str:
    from pilot.locks import LOCK_TYPES
    for name, (keys, fa, en) in LOCK_TYPES.items():
        if keys == [k]:
            return fa if lang == "fa" else en
    return {"can_send_messages": ("ارسال پیام", "send messages"), "can_react_to_messages": ("واکنش", "reactions"),
            "can_edit_tag": ("برچسب", "edit tag")}.get(k, (k, k))[0 if lang == "fa" else 1]


async def watchdog_notify(chat_id: int, drift: list[str], before: dict, after: dict) -> None:
    from pilot.ctx import lang_tz
    lang, tz = await lang_tz(chat_id)
    fa = _fa(lang)
    dest = await S.get(chat_id, "watch_notify")
    lines = [f"• {esc(_perm_name(k, lang))}: {'✅' if before.get(k) else '⛔️'} → {'✅' if after.get(k) else '⛔️'}" for k in drift]
    txt = ("⚠️ دسترسی‌های گروه تغییر کرد\n" if fa else "⚠️ Group permissions changed\n") + "\n".join(lines)
    rows = [[("🔧 برگرداندن" if fa else "🔧 Restore", "wd:fix"), ("👍 درست است" if fa else "👍 It's fine", "wd:ok")]]
    await RT.db.run("UPDATE rz_watch SET good_perms=COALESCE(good_perms, ?) WHERE chat_id=?", (jd(before), chat_id))
    if dest == "off":
        return
    if dest == "log":
        r = await logs.route(chat_id)
        if r and r["status"] == "active":
            await logs.emit(chat_id, txt)
            return
    await tg.send(chat_id, txt, rows=rows, silent=True)


# privacy
DATA_TABLES = [("rz_settings", "تنظیمات", "settings"), ("rz_user_chats", "اعضا", "members"),
               ("rz_actions", "تاریخچه", "history"), ("rz_cases", "پرونده‌ها", "cases"),
               ("rz_warns", "اخطارها", "warnings"), ("rz_appeals", "اعتراض‌ها", "appeals"),
               ("rz_tickets", "تیکت‌ها", "tickets"), ("rz_answers", "پاسخ‌ها", "answers"),
               ("rz_announcements", "اطلاعیه‌ها", "announcements"), ("rz_reminders", "یادآورها", "reminders"),
               ("rz_events", "رویدادها", "events"), ("rz_join_requests", "درخواست‌های عضویت", "join requests"),
               ("rz_errors", "خطاها", "errors")]


async def data_policy_text(chat_id: int, lang: str) -> str:
    fa = _fa(lang)
    L = ["<b>داده‌های ذخیره‌شده</b>" if fa else "<b>Stored data</b>"]
    for t, a, b in DATA_TABLES:
        n = await RT.db.val(f"SELECT COUNT(*) FROM {t} WHERE chat_id=?", (chat_id,), 0)
        L.append(f"• {a if fa else b}: {n}")
    days = await S.get(chat_id, "retention_days")
    L.append((f"\nنگهداری: {days} روز · <code>/retention 60</code>" if fa else
              f"\nRetention: {days} days · <code>/retention 60</code>"))
    return "\n".join(L)


async def export_audit(chat_id: int) -> bytes:
    acts = await RT.db.all("SELECT id,ts,actor_id,actor_kind,action,target_id,outcome,error,case_no,undone_at FROM rz_actions WHERE chat_id=? ORDER BY id", (chat_id,))
    cases = await RT.db.all("SELECT case_no,target_id,actor_id,action,reason,duration_s,expires_at,status,created_at FROM rz_cases WHERE chat_id=? ORDER BY case_no", (chat_id,))
    appeals = await RT.db.all("SELECT id,case_no,user_id,status,reviewer_id,decided_at,created_at FROM rz_appeals WHERE chat_id=?", (chat_id,))
    return json.dumps({"chat_id": chat_id, "exported_at": int(now()), "actions": acts, "cases": cases, "appeals": appeals},
                      ensure_ascii=False, indent=1).encode()


async def reset_group_data(chat_id: int) -> list[str]:
    keep = {"rz_chats", "rz_lock_layers", "rz_perm_state"}
    done = []

    def f(c):
        for t in CHAT_TABLES:
            if t in keep:
                continue
            n = c.execute(f"DELETE FROM {t} WHERE chat_id=?", (chat_id,)).rowcount
            if n:
                done.append(f"{t}:{n}")
    await RT.db.tx(f)
    S.invalidate(chat_id)
    return done


async def user_data(user_id: int) -> dict:
    q = RT.db.all
    return {
        "user": await RT.db.one("SELECT user_id,username,first_name,last_name,last_seen,dm_ok,lang FROM rz_users WHERE user_id=?", (user_id,)),
        "groups_seen": await q("SELECT chat_id,first_seen,last_seen,status FROM rz_user_chats WHERE user_id=?", (user_id,)),
        "warnings": await q("SELECT chat_id,reason,ts,active FROM rz_warns WHERE user_id=?", (user_id,)),
        "cases": await q("SELECT chat_id,case_no,action,reason,status,created_at,expires_at FROM rz_cases WHERE target_id=?", (user_id,)),
        "appeals": await q("SELECT chat_id,case_no,text,status,created_at FROM rz_appeals WHERE user_id=?", (user_id,)),
        "tickets": await q("SELECT chat_id,id,text,status,created_at FROM rz_tickets WHERE user_id=?", (user_id,)),
        "reminders": await q("SELECT id,text,due,status FROM rz_reminders WHERE user_id=?", (user_id,)),
        "rsvps": await q("SELECT event_id,status,checked_in FROM rz_rsvps WHERE user_id=?", (user_id,)),
    }


async def forget_user(user_id: int) -> list[str]:
    done = []

    def f(c):
        for sql, label in (("DELETE FROM rz_user_chats WHERE user_id=?", "groups_seen"),
                           ("DELETE FROM rz_username_history WHERE user_id=?", "username_history"),
                           ("DELETE FROM rz_reminders WHERE user_id=?", "reminders"),
                           ("DELETE FROM rz_rsvps WHERE user_id=?", "rsvps"),
                           ("UPDATE rz_tickets SET text='[removed by user]' WHERE user_id=? AND status='closed'", "closed_ticket_text"),
                           ("DELETE FROM rz_join_requests WHERE user_id=? AND status<>'pending'", "join_requests"),
                           ("DELETE FROM rz_users WHERE user_id=?", "profile_index")):
            n = c.execute(sql, (user_id,)).rowcount
            if n:
                done.append(f"{label}:{n}")
    await RT.db.tx(f)
    RT.user_seen.pop(user_id, None)
    return done
