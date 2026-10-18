"""Action history, cases, error log and undo."""
from __future__ import annotations

import logging
from typing import Any

from telegram import ChatPermissions

from pilot import authz, logs, tg
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.util import esc, fmt_duration, now

log = logging.getLogger("pilot.audit")
CASE_ACTIONS = {"ban", "tban", "mute", "tmute", "kick", "warn", "auto_ban", "auto_mute", "auto_kick", "ban_channel"}

ACTION_FA = {"ban": "بن", "tban": "بن موقت", "unban": "رفع بن", "mute": "سکوت", "tmute": "سکوت موقت",
             "unmute": "رفع سکوت", "kick": "اخراج", "warn": "اخطار", "unwarn": "حذف اخطار", "lock": "قفل",
             "unlock": "باز کردن قفل", "setting": "تغییر تنظیم", "auto_delete": "حذف خودکار", "purge": "پاکسازی",
             "auto_mute": "سکوت خودکار", "auto_kick": "اخراج خودکار", "auto_ban": "بن خودکار", "ban_channel": "بن کانال",
             "pin": "سنجاق", "promote": "ارتقا", "demote": "عزل", "night": "قفل شبانه", "welcome": "خوشامد",
             "restore": "بازیابی تنظیمات", "join_approve": "تأیید عضویت", "join_decline": "رد عضویت", "undo": "برگرداندن",
             "appeal_accept": "قبول اعتراض", "topic": "تاپیک", "log_setup": "تنظیم لاگ", "reset_filters": "خاموشی فیلترها"}


def case_status(st: str, lang: str) -> str:
    if lang != "fa":
        return st
    return {"active": "فعال", "reversed": "برگشت خورده", "expired": "تمام شده"}.get(st, st)


def action_name(a: str, lang: str) -> str:
    return ACTION_FA.get(a, a) if lang == "fa" else a


async def record(chat_id: int, actor_id: int | None, actor_kind: str, action: str, *, target_id: int | None = None,
                 target_name: str = "", params: dict | None = None, outcome: str = "ok", error: str | None = None,
                 undo: dict | None = None, reason: str | None = None, duration: int | None = None,
                 evidence: dict | None = None, log_line: str | None = None) -> tuple[int, int | None]:
    t = now()
    make_case = outcome == "ok" and action in CASE_ACTIONS and target_id is not None

    def f(c):
        cur = c.execute("INSERT INTO rz_actions(chat_id,ts,actor_id,actor_kind,action,target_id,target_name,params,outcome,error,undo) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (chat_id, t, actor_id, actor_kind, action, target_id, target_name[:64],
                                                          jd(params or {}), outcome, (error or "")[:300] or None,
                                                          jd(undo) if undo else None))
        aid = cur.lastrowid
        case_no = None
        if make_case:
            case_no = (c.execute("SELECT COALESCE(MAX(case_no),0)+1 FROM rz_cases WHERE chat_id=?", (chat_id,)).fetchone()[0])
            c.execute("INSERT INTO rz_cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (chat_id, case_no, target_id, target_name[:64], actor_id, action, (reason or "")[:300], duration,
                       (t + duration) if duration else None, jd(evidence or {}), "active", t, aid))
            c.execute("UPDATE rz_actions SET case_no=? WHERE id=?", (case_no, aid))
        return aid, case_no

    aid, case_no = await RT.db.tx(f)
    if log_line:
        suffix = f" · #{case_no}" if case_no else ""
        await logs.emit(chat_id, log_line + suffix)
    return aid, case_no


async def error(chat_id: int | None, area: str, detail: str) -> None:
    try:
        await RT.db.run("INSERT INTO rz_errors(chat_id,ts,area,detail) VALUES(?,?,?,?)", (chat_id, now(), area[:40], detail[:400]))
    except Exception:  # noqa: BLE001
        log.exception("could not persist error")


async def recent(chat_id: int, limit: int = 10) -> list[dict]:
    return await RT.db.all("SELECT * FROM rz_actions WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))


UNMUTED = ChatPermissions.all_permissions()


def perms_of(member) -> dict:
    keys = [k for k in ChatPermissions.__slots__ if k.startswith("can_")]
    return {k: bool(getattr(member, k, True)) for k in keys}


async def undo(chat_id: int, action_id: int, actor_id: int, lang: str) -> tuple[bool, str]:
    """Undo an action if it is reversible and nothing changed since."""
    fa = lang == "fa"
    row = await RT.db.one("SELECT * FROM rz_actions WHERE id=? AND chat_id=?", (action_id, chat_id))
    if not row:
        return False, "پیدا نشد." if fa else "Not found."
    if row["undone_at"]:
        return False, "قبلاً برگشت خورده." if fa else "Already undone."
    if row["outcome"] != "ok":
        return False, "انجام نشده بود." if fa else "It never succeeded."
    u = jl(row["undo"])
    if not u:
        why = {"kick": ("اخراج برنمی‌گردد.", "Kicks can't be undone."),
               "purge": ("پیام حذف‌شده برنمی‌گردد.", "Deleted messages can't be restored."),
               "auto_delete": ("پیام حذف‌شده برنمی‌گردد.", "Deleted messages can't be restored.")}
        m = why.get(row["action"], ("برنمی‌گردد.", "Can't be undone."))
        return False, m[0] if fa else m[1]
    op = u.get("op")
    ok, msg = False, ""
    if op == "unban":
        mem = await authz.member(chat_id, u["user"], fresh=True)
        if mem is not None and mem.status != "kicked":
            ok, msg = False, ("کاربر دیگر بن نیست." if fa else "User is no longer banned.")
        else:
            out = await tg.call(RT.bot.unban_chat_member, chat_id=chat_id, user_id=u["user"], only_if_banned=True)
            ok = out.ok
            msg = ("آنبن شد." if fa else "Unbanned.") if ok else "❌ " + esc(out.desc)
    elif op == "restore_member":
        mem = await authz.member(chat_id, u["user"], fresh=True)
        if mem is None or mem.status != "restricted":
            ok, msg = False, ("کاربر دیگر محدود نیست." if fa else "User is no longer restricted.")
        elif {k: v for k, v in perms_of(mem).items() if k.startswith("can_send")} != \
                {k: v for k, v in (u.get("set") or {}).items() if k.startswith("can_send")}:
            ok, msg = False, ("محدودیت کاربر بعداً عوض شده. دست نخورد." if fa else "The restriction changed since. Left as is.")
        else:
            prev = u.get("prev")
            if prev and (u.get("prev_until") or 0) > now() + 30:
                out = await tg.call(RT.bot.restrict_chat_member, chat_id=chat_id, user_id=u["user"],
                                    permissions=ChatPermissions(**prev), until_date=int(u["prev_until"]),
                                    use_independent_chat_permissions=True)
            else:
                out = await tg.call(RT.bot.restrict_chat_member, chat_id=chat_id, user_id=u["user"], permissions=UNMUTED,
                                    use_independent_chat_permissions=True)
            ok = out.ok
            msg = ("برگشت." if fa else "Reverted.") if ok else "❌ " + esc(out.desc)
    elif op == "unwarn":
        _, n = await RT.db.run("UPDATE rz_warns SET active=0 WHERE id=? AND chat_id=? AND active=1", (u["warn_id"], chat_id))
        ok = n > 0
        msg = ("اخطار برداشته شد." if fa else "Warning removed.") if ok else ("اخطار فعال نیست." if fa else "Warning not active.")
    elif op == "setting":
        from pilot import settings as S
        cur = await S.get(chat_id, u["key"])
        if cur != u["new"]:
            ok, msg = False, ("این تنظیم بعداً عوض شده. دست نخورد." if fa else "Setting changed since. Left as is.")
        else:
            await S.set_value(chat_id, u["key"], u["old"], actor_id)
            ok, msg = True, ("برگشت." if fa else "Reverted.")
    elif op == "remove_layer":
        from pilot import locks
        lay = await RT.db.one("SELECT created_at FROM rz_lock_layers WHERE chat_id=? AND layer=?", (chat_id, u["layer"]))
        if not lay or abs((lay["created_at"] or 0) - u.get("created_at", 0)) > 1:
            ok, msg = False, ("این قفل دیگر فعال نیست." if fa else "That lock is no longer active.")
        else:
            res = await locks.remove_layer(chat_id, u["layer"])
            ok, msg = res.ok, res.desc
    elif op == "unban_channel":
        out = await tg.call(RT.bot.unban_chat_sender_chat, chat_id=chat_id, sender_chat_id=u["chat"])
        ok = out.ok
        msg = ("کانال آزاد شد." if fa else "Channel unbanned.") if ok else esc(out.desc)
    else:
        return False, "برنمی‌گردد." if fa else "Can't be undone."
    if ok:
        await RT.db.run("UPDATE rz_actions SET undone_by=?, undone_at=? WHERE id=?", (actor_id, now(), action_id))
        if row["case_no"]:
            await RT.db.run("UPDATE rz_cases SET status='reversed' WHERE chat_id=? AND case_no=?", (chat_id, row["case_no"]))
        await record(chat_id, actor_id, "admin", "undo", target_id=row["target_id"], target_name=row["target_name"] or "",
                     params={"of": action_id, "op": op},
                     log_line=f"#UNDO {esc(row['action'])} {action_id} · <code>{actor_id}</code>")
    return ok, msg


def fmt_action(r: dict, lang: str, tz: str) -> str:
    from pilot.util import fmt_dt
    who = r["target_name"] or (str(r["target_id"]) if r["target_id"] else "")
    st = {"ok": "✅", "failed": "❌", "partial": "⚠️", "uncertain": "❔"}.get(r["outcome"], "•")
    und = " ↩️" if r["undone_at"] else ""
    case = f" #{r['case_no']}" if r["case_no"] else ""
    p = jl(r["params"], {}) or {}
    dur = f" · {fmt_duration(p['duration'], lang)}" if p.get("duration") else ""
    return (f"{st} <code>{r['id']}</code> {esc(action_name(r['action'], lang))}{case} {esc(who)}{dur}{und}"
            f" · {fmt_dt(r['ts'], tz, lang, with_tz=False)}")
