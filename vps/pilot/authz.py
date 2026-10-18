"""Admin and rights checks based on Telegram member data."""
from __future__ import annotations

import time

from telegram import ChatMember, ChatMemberAdministrator, ChatMemberOwner

from pilot import tg
from pilot.rt import RT

ANON_ADMIN_BOT = 1087968824
TG_SERVICE_ID = 777000

RIGHT_ATTR = {
    "restrict": "can_restrict_members",
    "delete": "can_delete_messages",
    "pin": "can_pin_messages",
    "info": "can_change_info",
    "invite": "can_invite_users",
    "promote": "can_promote_members",
    "topics": "can_manage_topics",
    "post": "can_post_messages",
}
RIGHT_FA = {"restrict": "بن کاربران", "delete": "حذف پیام", "pin": "سنجاق پیام", "info": "تغییر اطلاعات گروه",
            "invite": "دعوت کاربر", "promote": "افزودن ادمین", "topics": "مدیریت تاپیک", "post": "ارسال پست"}
RIGHT_EN = {"restrict": "Ban users", "delete": "Delete messages", "pin": "Pin messages", "info": "Change group info",
            "invite": "Invite users", "promote": "Add admins", "topics": "Manage topics", "post": "Post messages"}

MEMBER_TTL = 45.0
ADMINS_TTL = 300.0


def right_label(r: str, lang: str) -> str:
    return (RIGHT_FA if lang == "fa" else RIGHT_EN).get(r, r)


def is_owner(user_id: int | None) -> bool:
    return bool(user_id) and RT.cfg is not None and user_id == RT.cfg.owner_id


async def member(chat_id: int, user_id: int, fresh: bool = False) -> ChatMember | None:
    key = (chat_id, user_id)
    hit = RT.member_cache.get(key)
    if hit and not fresh and time.time() - hit[0] < MEMBER_TTL:
        return hit[1]
    out = await tg.call(RT.bot.get_chat_member, chat_id=chat_id, user_id=user_id)
    if not out.ok:
        RT.member_cache.pop(key, None)
        return None
    RT.member_cache[key] = (time.time(), out.value)
    if len(RT.member_cache) > 20000:
        RT.member_cache.clear()
    return out.value


def invalidate(chat_id: int, user_id: int | None = None) -> None:
    if user_id is None:
        for k in [k for k in RT.member_cache if k[0] == chat_id]:
            RT.member_cache.pop(k, None)
    else:
        RT.member_cache.pop((chat_id, user_id), None)
    RT.admins_cache.pop(chat_id, None)


def has_right(m: ChatMember | None, right: str | None) -> bool:
    if m is None:
        return False
    if isinstance(m, ChatMemberOwner) or m.status == "creator":
        return True
    if not (isinstance(m, ChatMemberAdministrator) or m.status == "administrator"):
        return False
    if right is None:
        return True
    return bool(getattr(m, RIGHT_ATTR[right], False))


def is_adminish(m: ChatMember | None) -> bool:
    return m is not None and m.status in ("creator", "administrator")


async def check(chat_id: int, user_id: int | None, right: str | None, fresh: bool = True) -> tuple[bool, str]:
    """(ok, reason) where reason in ok|not_admin|missing_right|unknown_user."""
    if not user_id:
        return False, "unknown_user"
    m = await member(chat_id, user_id, fresh=fresh)
    if not is_adminish(m):
        return False, "not_admin"
    if not has_right(m, right):
        return False, "missing_right"
    return True, "ok"


async def bot_member(chat_id: int, fresh: bool = False) -> ChatMember | None:
    return await member(chat_id, RT.bot_id, fresh=fresh)


async def bot_has(chat_id: int, right: str, fresh: bool = False) -> bool:
    return has_right(await bot_member(chat_id, fresh=fresh), right)


async def admin_ids(chat_id: int) -> set[int]:
    hit = RT.admins_cache.get(chat_id)
    if hit and time.time() - hit[0] < ADMINS_TTL:
        return hit[1]
    out = await tg.call(RT.bot.get_chat_administrators, chat_id=chat_id)
    ids = {m.user.id for m in out.value} if out.ok else (hit[1] if hit else set())
    RT.admins_cache[chat_id] = (time.time(), ids)
    return ids


async def admins(chat_id: int) -> list[ChatMember]:
    out = await tg.call(RT.bot.get_chat_administrators, chat_id=chat_id)
    return list(out.value) if out.ok else []
