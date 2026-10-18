"""Cmd: who sent what, where. Shared by commands, buttons and the AI."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from telegram import Message, Update

from pilot import settings as S
from pilot import tg
from pilot.authz import ANON_ADMIN_BOT
from pilot.rt import RT


@dataclass
class Snap:
    message_id: int
    user_id: int | None = None
    user_name: str = ""
    username: str | None = None
    is_bot: bool = False
    sender_chat_id: int | None = None
    sender_chat_type: str | None = None
    sender_chat_title: str = ""
    auto_fwd: bool = False
    text: str = ""
    text_html: str = ""
    media_type: str | None = None
    file_id: str | None = None
    from_self: bool = False


def media_of(m: Message) -> tuple[str | None, str | None]:
    if m.photo:
        return "photo", m.photo[-1].file_id
    for kind in ("animation", "video", "document", "sticker", "audio", "voice", "video_note"):
        obj = getattr(m, kind, None)
        if obj is not None:
            return kind, obj.file_id
    return None, None


def snap_of(m: Message | None, parent: Message | None = None) -> Snap | None:
    if m is None:
        return None
    # in forums every message replies to the topic's first message; ignore that
    if m.forum_topic_created is not None:
        return None
    if parent is not None and parent.is_topic_message and m.message_id == parent.message_thread_id:
        return None
    u = m.from_user
    mt, fid = media_of(m)
    sc = m.sender_chat
    return Snap(
        message_id=m.message_id,
        user_id=u.id if u else None,
        user_name=(u.full_name if u else "")[:64],
        username=u.username if u else None,
        is_bot=bool(u and u.is_bot),
        sender_chat_id=sc.id if sc else None,
        sender_chat_type=sc.type if sc else None,
        sender_chat_title=(sc.title or "")[:64] if sc else "",
        auto_fwd=bool(m.is_automatic_forward),
        text=(m.text or m.caption or "")[:3800],
        text_html=(m.text_html if m.text else (m.caption_html if m.caption else "")) or "",
        media_type=mt,
        file_id=fid,
        from_self=bool(u and u.id == RT.bot_id),
    )


@dataclass
class Cmd:
    chat_id: int
    chat_type: str
    chat_title: str = ""
    is_forum: bool = False
    thread_id: int | None = None
    message_id: int | None = None
    actor_id: int | None = None
    actor_name: str = ""
    anon: bool = False
    name: str = ""
    args: list[str] = field(default_factory=list)
    rest: str = ""
    rest_html: str = ""
    reply: Snap | None = None
    mentions: list[dict] = field(default_factory=list)
    lang: str = "fa"
    tz: str = "Asia/Tehran"
    source: str = "cmd"
    update_id: int | None = None
    message: Any = None
    linked_chat_id: int | None = None

    # helpers
    def t(self, fa: str, en: str) -> str:
        return fa if self.lang == "fa" else en

    @property
    def is_group(self) -> bool:
        return self.chat_type in ("group", "supergroup")

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"

    async def say(self, text: str, rows=None, reply: bool = True) -> tg.Out:
        return await tg.send(self.chat_id, text, rows=rows, thread_id=self.thread_id if self.is_forum else None,
                             reply_to=self.message_id if reply and self.message_id else None)

    def to_payload(self) -> dict:
        d = asdict(self)
        d.pop("message", None)
        return d

    @classmethod
    def from_payload(cls, d: dict) -> "Cmd":
        d = dict(d)
        r = d.pop("reply", None)
        d.pop("message", None)
        c = cls(**d)
        c.reply = Snap(**r) if r else None
        return c


async def lang_tz(chat_id: int) -> tuple[str, str]:
    eff = await S.load(chat_id)
    return eff["lang"][0] or "fa", eff["tz"][0] or RT.cfg.tz


async def build(update: Update, name: str = "", text: str | None = None, source: str = "cmd") -> Cmd | None:
    m = update.effective_message
    chat = update.effective_chat
    if chat is None:
        return None
    raw = text if text is not None else ((m.text or m.caption or "") if m else "")
    parts = raw.split(None, 1)
    rest = parts[1] if (len(parts) > 1 and source == "cmd") else (raw if source != "cmd" else "")
    rest_html = ""
    if m is not None and source == "cmd":
        th = (m.text_html if m.text else (m.caption_html if m.caption else "")) or ""
        hp = th.split(None, 1)
        rest_html = hp[1] if len(hp) > 1 else ""
    user = update.effective_user
    anon = False
    actor_id = user.id if user else None
    if m is not None and m.sender_chat is not None:
        if m.sender_chat.id == chat.id or (user and user.id == ANON_ADMIN_BOT):
            anon = True      # anonymous admin, verified later with a button
        actor_id = None
    if user and user.id == ANON_ADMIN_BOT:
        anon, actor_id = True, None
    mentions = []
    if m is not None:
        for ent in (m.entities or m.caption_entities or ()):
            if ent.type == "text_mention" and ent.user:
                mentions.append({"id": ent.user.id, "name": ent.user.full_name[:64], "username": ent.user.username,
                                 "offset": ent.offset})
    lang, tz = await lang_tz(chat.id) if chat.type != "private" else (await _user_lang(user), RT.cfg.tz)
    return Cmd(
        chat_id=chat.id, chat_type=chat.type, chat_title=(chat.title or "")[:128], is_forum=bool(chat.is_forum),
        thread_id=m.message_thread_id if (m is not None and m.is_topic_message) else None,
        message_id=m.message_id if m is not None else None,
        actor_id=actor_id, actor_name=(user.full_name if user and not anon else "")[:64], anon=anon,
        name=name, args=rest.split() if rest else [], rest=rest, rest_html=rest_html,
        reply=snap_of(m.reply_to_message, m) if m is not None else None, mentions=mentions,
        lang=lang, tz=tz, source=source, update_id=update.update_id, message=m,
    )


async def _user_lang(user) -> str:
    if user is None:
        return "fa"
    v = await RT.db.val("SELECT lang FROM rz_users WHERE user_id=?", (user.id,))
    if v in ("fa", "en"):
        return v
    # Persian unless the user picked English with /lang en
    return "fa"
