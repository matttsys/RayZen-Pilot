"""Permission locks.

Layers: manual:<type>, night, raid. Default permissions are the baseline minus what
the active layers deny, except keys an admin reopened by hand. Locks never delete messages."""
from __future__ import annotations

import asyncio
import logging

from telegram import ChatPermissions

from pilot import tg
from pilot.db import jd, jl
from pilot.rt import RT
from pilot.util import now

log = logging.getLogger("pilot.locks")
KEYS = [k for k in ChatPermissions.__slots__ if k.startswith("can_")]
SEND = ["can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos", "can_send_videos",
        "can_send_video_notes", "can_send_voice_notes", "can_send_polls", "can_send_other_messages", "can_add_web_page_previews"]
MEDIA = ["can_send_audios", "can_send_documents", "can_send_photos", "can_send_videos", "can_send_video_notes",
         "can_send_voice_notes", "can_send_other_messages"]
LOCK_TYPES: dict[str, tuple[list[str], str, str]] = {
    "all": (SEND, "گروه", "Group"),
    "media": (MEDIA, "رسانه", "media"),
    "photos": (["can_send_photos"], "عکس", "photos"),
    "videos": (["can_send_videos"], "ویدیو", "videos"),
    "documents": (["can_send_documents"], "فایل", "files"),
    "audio": (["can_send_audios"], "موسیقی", "audio"),
    "voice": (["can_send_voice_notes", "can_send_video_notes"], "ویس", "voice"),
    "stickers": (["can_send_other_messages"], "استیکر و گیف", "stickers & GIFs"),
    "polls": (["can_send_polls"], "نظرسنجی", "polls"),
    "previews": (["can_add_web_page_previews"], "پیش‌نمایش لینک", "link previews"),
    "invite": (["can_invite_users"], "دعوت", "invites"),
    "pin": (["can_pin_messages"], "سنجاق", "pins"),
    "info": (["can_change_info"], "اطلاعات گروه", "group info"),
    "topics": (["can_manage_topics"], "تاپیک", "topics"),
}
LOCK_ALIASES = {"همه": "all", "گروه": "all", "رسانه": "media", "عکس": "photos", "ویدیو": "videos", "فایل": "documents",
                "استیکر": "stickers", "گیف": "stickers", "نظرسنجی": "polls", "ویس": "voice", "موسیقی": "audio",
                "لینک": "previews", "دعوت": "invite", "سنجاق": "pin", "gif": "stickers", "gifs": "stickers",
                "sticker": "stickers", "poll": "polls", "photo": "photos", "video": "videos", "links": "previews",
                "document": "documents", "files": "documents", "everything": "all", "chat": "all", "text": "all"}
_locks: dict[int, asyncio.Lock] = {}


def _lk(chat_id: int) -> asyncio.Lock:
    if chat_id not in _locks:
        _locks[chat_id] = asyncio.Lock()
    return _locks[chat_id]


def lock_type(word: str | None) -> str | None:
    w = (word or "all").strip().lower()
    w = LOCK_ALIASES.get(w, w)
    return w if w in LOCK_TYPES else None


def as_dict(p: ChatPermissions | None) -> dict[str, bool]:
    return {k: bool(getattr(p, k, False)) for k in KEYS} if p is not None else {}


async def current(chat_id: int) -> tg.Out:
    out = await tg.call(RT.bot.get_chat, chat_id=chat_id)
    if not out.ok:
        return out
    return tg.Out(True, as_dict(out.value.permissions))


async def layers(chat_id: int) -> list[dict]:
    rows = await RT.db.all("SELECT * FROM rz_lock_layers WHERE chat_id=? ORDER BY created_at", (chat_id,))
    for r in rows:
        r["deny"] = jl(r["deny"], [])
    return rows


async def add_layer(chat_id: int, layer: str, deny: list[str], actor: int | None, reason: str = "",
                    expires_at: float | None = None) -> tg.Out:
    await RT.db.run("INSERT INTO rz_lock_layers VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,layer) DO UPDATE SET "
                    "deny=excluded.deny, reason=excluded.reason, actor_id=excluded.actor_id, created_at=excluded.created_at,"
                    "expires_at=excluded.expires_at", (chat_id, layer, jd(deny), reason[:200], actor, now(), expires_at))
    # a new lock clears earlier manual reopenings
    await RT.db.run("UPDATE rz_perm_state SET overrides='[]' WHERE chat_id=?", (chat_id,))
    res = await reconcile(chat_id)
    if not res.ok:
        await RT.db.run("DELETE FROM rz_lock_layers WHERE chat_id=? AND layer=?", (chat_id, layer))
        await reconcile(chat_id)
    return res


async def remove_layer(chat_id: int, layer: str) -> tg.Out:
    _, n = await RT.db.run("DELETE FROM rz_lock_layers WHERE chat_id=? AND layer=?", (chat_id, layer))
    if not n:
        return tg.Out(True, None, "noop", "")
    return await reconcile(chat_id)


async def reconcile(chat_id: int) -> tg.Out:
    """Sync Telegram default permissions with the active layers.
    Out.value = {"changed": bool, "drift": [keys changed by admins], "target": {...}}"""
    async with _lk(chat_id):
        await RT.db.run("DELETE FROM rz_lock_layers WHERE chat_id=? AND expires_at IS NOT NULL AND expires_at<=?", (chat_id, now()))
        act = await layers(chat_id)
        st = await RT.db.one("SELECT * FROM rz_perm_state WHERE chat_id=?", (chat_id,))
        if not act and not st:
            return tg.Out(True, {"changed": False, "drift": [], "target": None}, "noop")
        cur = await current(chat_id)
        if not cur.ok:
            return cur
        cur_p: dict = cur.value
        if st:
            baseline, applied = jl(st["baseline"], {}), jl(st["applied"], {})
            overrides = set(jl(st["overrides"], []))
        else:
            baseline, applied, overrides = dict(cur_p), dict(cur_p), set()
            # save the baseline before changing anything
            await RT.db.run("INSERT OR REPLACE INTO rz_perm_state VALUES(?,?,?,?,?,?)",
                            (chat_id, jd(baseline), jd(applied), "[]", now(), now()))
        denied = set()
        for la in act:
            denied.update(la["deny"])
        drift = [k for k in KEYS if k in cur_p and applied.get(k) != cur_p.get(k)]
        for k in drift:
            if k in denied:
                if cur_p[k]:
                    overrides.add(k)   # an admin reopened this in Telegram; keep it
            else:
                baseline[k] = cur_p[k]  # admin change on an unlocked key becomes the baseline
        if not act:
            target = {k: (cur_p[k] if k in drift else baseline.get(k, cur_p.get(k, False))) for k in KEYS}
        else:
            target = {k: bool(baseline.get(k, cur_p.get(k, False))) and (k not in denied or k in overrides) for k in KEYS}
        changed = False
        if any(target[k] != cur_p.get(k) for k in KEYS):
            out = await tg.call(RT.bot.set_chat_permissions, chat_id=chat_id, permissions=ChatPermissions(**target),
                                use_independent_chat_permissions=True)
            if not out.ok:
                from pilot.audit import error
                await error(chat_id, "locks", f"setChatPermissions: {out.kind} {out.desc}")
                return out
            changed = True
            # store what Telegram applied (it can adjust related flags) so the next
            # reconcile doesn't read it as a manual change
            after = await current(chat_id)
            if after.ok:
                target = {k: bool(after.value.get(k, target[k])) for k in KEYS}
        if act:
            await RT.db.run("UPDATE rz_perm_state SET baseline=?, applied=?, overrides=?, updated_at=? WHERE chat_id=?",
                            (jd(baseline), jd(target), jd(sorted(overrides)), now(), chat_id))
        else:
            await RT.db.run("DELETE FROM rz_perm_state WHERE chat_id=?", (chat_id,))
        if drift:
            log.info("chat %s: admin changed permissions directly during lock: %s", chat_id, drift)
        return tg.Out(True, {"changed": changed, "drift": drift, "target": target})


def describe_layer(la: dict, lang: str, tz: str) -> str:
    from pilot.util import fmt_dt
    name = la["layer"]
    if name.startswith("manual:"):
        lt = name.split(":", 1)[1]
        d = LOCK_TYPES.get(lt, ([], lt, lt))
        nm = d[1] if lang == "fa" else d[2]
    elif name == "night":
        nm = "شبانه" if lang == "fa" else "night"
    elif name == "raid":
        nm = "ضد رید" if lang == "fa" else "raid"
    else:
        nm = name
    if la.get("expires_at"):
        nm += (" تا " if lang == "fa" else " until ") + fmt_dt(la["expires_at"], tz, lang, with_tz=False)
    return nm
