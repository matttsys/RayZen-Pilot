"""Telegram call helpers."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, ReplyParameters
from telegram.constants import ParseMode
from telegram.error import (BadRequest, ChatMigrated, Forbidden, NetworkError, RetryAfter, TelegramError,
                            TimedOut)

from pilot.rt import RT

log = logging.getLogger("pilot.tg")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


@dataclass
class Out:
    ok: bool
    value: Any = None
    kind: str = "ok"
    desc: str = ""

    def __bool__(self) -> bool:
        return self.ok


_RIGHTS = ("not enough rights", "chat_admin_required", "need administrator rights", "have no rights",
           "not an administrator", "rights to", "administrator rights", "can't restrict", "no rights")
_TADMIN = ("user is an administrator", "can't remove chat owner", "user_admin_invalid", "is an administrator of the chat",
           "can't restrict self", "can't ban", "administrators can't", "chat owner")
_NOTFOUND = ("user not found", "participant_id_invalid", "user_id_invalid", "chat not found", "message to delete not found",
             "message thread not found", "topic_id_invalid", "message to edit not found", "message not found",
             "member not found", "peer_id_invalid", "hide_requester_missing", "user_already_participant", "message to copy not found",
             "message can't be deleted", "message_id_invalid", "wrong file identifier")
_UNSUP = ("only for supergroup", "not a forum", "chat is not a forum", "method is available only", "supergroup",
          "topic_not_modified", "not supported")


def classify(exc: BaseException) -> tuple[str, str]:
    d = str(getattr(exc, "message", "") or exc)
    low = d.lower()
    if isinstance(exc, RetryAfter):
        ra = exc.retry_after
        secs = ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)
        return "flood", f"retry_after={int(secs)}"
    if isinstance(exc, ChatMigrated):
        return "migrated", str(exc.new_chat_id)
    if isinstance(exc, TimedOut):
        return "uncertain", "timed out"
    if isinstance(exc, Forbidden):
        return "forbidden", d
    if isinstance(exc, BadRequest):
        if any(k in low for k in _TADMIN):
            return "target_admin", d
        if any(k in low for k in _RIGHTS):
            return "rights", d
        if any(k in low for k in _NOTFOUND):
            return "not_found", d
        if any(k in low for k in _UNSUP):
            return "unsupported", d
        if "not modified" in low:
            return "not_modified", d
        return "bad_request", d
    if isinstance(exc, NetworkError):
        return "network", d
    if isinstance(exc, TelegramError):
        return "bad_request", d
    return "internal", f"{type(exc).__name__}: {d}"[:300]


def retry_after_s(desc: str) -> float:
    try:
        return float(desc.split("=", 1)[1])
    except (IndexError, ValueError):
        return 5.0


async def call(fn: Callable[..., Awaitable[Any]], *a: Any, idempotent: bool = True, **kw: Any) -> Out:
    """Call the Bot API. Short flood waits are retried once; network errors are retried
    once for idempotent calls. TimedOut is reported as 'uncertain'."""
    attempts = 2
    for i in range(attempts):
        try:
            return Out(True, await fn(*a, **kw))
        except Exception as exc:  # noqa: BLE001
            kind, desc = classify(exc)
            if kind == "flood" and i == 0 and retry_after_s(desc) <= 15:
                await asyncio.sleep(retry_after_s(desc) + 0.5)
                continue
            if kind == "network" and idempotent and i == 0:
                await asyncio.sleep(1.5)
                continue
            if kind == "internal":
                log.exception("internal error in telegram call")
            return Out(False, None, kind, desc[:300])
    return Out(False, None, "network", "retries exhausted")


def kb(rows: list[list[tuple[str, str]]] | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    out = []
    for row in rows:
        btns = []
        for label, data in row:
            if data.startswith(("http://", "https://", "tg://")):
                btns.append(InlineKeyboardButton(label, url=data))
            else:
                btns.append(InlineKeyboardButton(label, callback_data=data[:64]))
        if btns:
            out.append(btns)
    return InlineKeyboardMarkup(out) if out else None


async def send(chat_id: int, text: str, rows=None, thread_id: int | None = None, reply_to: int | None = None,
               silent: bool = False, markup=None) -> Out:
    kw: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096], "link_preview_options": NO_PREVIEW,
                          "disable_notification": silent, "parse_mode": ParseMode.HTML}
    if thread_id:
        kw["message_thread_id"] = thread_id
    if reply_to:
        kw["reply_parameters"] = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
    m = markup or kb(rows)
    if m:
        kw["reply_markup"] = m
    out = await call(RT.bot.send_message, idempotent=False, **kw)
    if not out.ok and out.kind == "bad_request" and "parse" in out.desc.lower():
        # bad HTML: resend as plain text
        import html as _h
        import re as _re
        kw["text"] = _h.unescape(_re.sub(r"<[^>]+>", "", text))[:4096]
        kw["parse_mode"] = None
        out = await call(RT.bot.send_message, idempotent=False, **kw)
    if not out.ok and out.kind == "not_found" and thread_id:
        kw.pop("message_thread_id", None)
        out2 = await call(RT.bot.send_message, idempotent=False, **kw)
        if out2.ok:
            out2.desc = "topic_missing_fallback"
            return out2
    if not out.ok:
        log.info("send to %s failed: %s %s", chat_id, out.kind, out.desc[:120])
    return out


async def edit(msg, text: str, rows=None, markup=None) -> Out:
    if msg is None:
        return Out(False, kind="not_found")
    kw: dict[str, Any] = {"chat_id": msg.chat_id, "message_id": msg.message_id, "text": text[:4096],
                          "reply_markup": markup or kb(rows), "link_preview_options": NO_PREVIEW, "parse_mode": ParseMode.HTML}
    out = await call(RT.bot.edit_message_text, **kw)
    if not out.ok and out.kind == "bad_request" and "parse" in out.desc.lower():
        import html as _h
        import re as _re
        kw["text"] = _h.unescape(_re.sub(r"<[^>]+>", "", text))[:4096]
        kw["parse_mode"] = None
        out = await call(RT.bot.edit_message_text, **kw)
    if not out.ok and out.kind == "not_modified":
        return Out(True, msg, "not_modified")
    return out


async def answer(query, text: str | None = None, alert: bool = False) -> None:
    try:
        await query.answer(text=(text or None) and text[:190], show_alert=alert)
    except Exception:  # noqa: BLE001  (old callback query)
        pass


async def delete(chat_id: int, message_id: int) -> Out:
    return await call(RT.bot.delete_message, chat_id=chat_id, message_id=message_id)
