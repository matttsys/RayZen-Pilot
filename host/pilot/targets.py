"""Find the target user of a command.

Bots can't look up arbitrary @usernames, so only usernames seen in updates are used,
and they're re-checked with getChatMember when possible."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from telegram import User

from pilot import authz
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.util import esc, norm_digits

STALE_AFTER = 30 * 86400
USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


async def note_user(u: User | None, chat_id: int | None = None, status: str | None = None, source: str = "update") -> None:
    if u is None or u.id in (authz.ANON_ADMIN_BOT, authz.TG_SERVICE_ID):
        return
    sig = f"{u.username}|{u.first_name}|{u.last_name}|{chat_id}|{status}"
    hit = RT.user_seen.get(u.id)
    t = time.time()
    if hit and hit[1] == sig and t - hit[0] < 900:
        return
    RT.user_seen[u.id] = (t, sig)
    if len(RT.user_seen) > 50000:
        RT.user_seen.clear()
    un = u.username
    lc = un.lower() if un else None

    def f(c):
        if lc:
            for r in c.execute("SELECT user_id, username FROM rz_users WHERE username_lc=? AND user_id<>?", (lc, u.id)).fetchall():
                # username moved to another account; drop the old binding
                c.execute("INSERT INTO rz_username_history VALUES(?,?,?)", (r[0], r[1], t))
                c.execute("UPDATE rz_users SET username=NULL, username_lc=NULL WHERE user_id=?", (r[0],))
        prev = c.execute("SELECT username, first_name, last_name FROM rz_users WHERE user_id=?", (u.id,)).fetchone()
        if prev and prev[0] and prev[0] != un:
            c.execute("INSERT INTO rz_username_history VALUES(?,?,?)", (u.id, prev[0], t))
        if prev and (prev[1] or prev[2]) and (prev[1] or "", prev[2] or "") != ((u.first_name or "")[:64], (u.last_name or "")[:64]):
            old_name = " ".join(x for x in (prev[1], prev[2]) if x)[:128]
            c.execute("INSERT OR REPLACE INTO rz_name_history(user_id,name,seen_at) VALUES(?,?,?)", (u.id, old_name, t))
        c.execute("INSERT INTO rz_users(user_id,username,username_lc,first_name,last_name,is_bot,last_seen,source) "
                  "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,"
                  "username_lc=excluded.username_lc,first_name=excluded.first_name,last_name=excluded.last_name,"
                  "is_bot=excluded.is_bot,last_seen=excluded.last_seen,source=excluded.source",
                  (u.id, un, lc, (u.first_name or "")[:64], (u.last_name or "")[:64], int(u.is_bot), t, source))
        if chat_id and chat_id < 0:
            c.execute("INSERT INTO rz_user_chats(chat_id,user_id,first_seen,last_seen,status) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(chat_id,user_id) DO UPDATE SET last_seen=excluded.last_seen,"
                      "status=COALESCE(excluded.status, rz_user_chats.status)", (chat_id, u.id, t, t, status))
    await RT.db.tx(f)


@dataclass
class Target:
    user_id: int | None = None
    name: str = ""
    username: str | None = None
    via: str = ""                 # reply | mention | id | username
    sender_chat_id: int | None = None
    sender_chat_type: str | None = None
    consumed: int = 0
    verified: bool = False        # confirmed by Telegram
    unverified_note: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return not self.err

    def label(self) -> str:
        if self.sender_chat_id:
            return f"{esc(self.name or 'channel')} [<code>{self.sender_chat_id}</code>]"
        nm = esc(self.name) if self.name else ("@" + esc(self.username) if self.username else "")
        link = f'<a href="tg://user?id={self.user_id}">{nm or self.user_id}</a>'
        return f"{link} [<code>{self.user_id}</code>]"


async def lookup_username(un: str) -> dict | None:
    return await RT.db.one("SELECT * FROM rz_users WHERE username_lc=?", (un.lower(),))


async def resolve(c: Cmd, *, use_reply: bool = True) -> Target:
    """Target from the first argument (id, @username, text mention) or the replied message."""
    a0 = norm_digits(c.args[0]) if c.args else ""
    # text mention as first argument
    if c.mentions:
        mt = c.mentions[0]
        return Target(user_id=mt["id"], name=mt["name"], username=mt.get("username"), via="mention",
                      consumed=max(1, len(mt["name"].split())), verified=True)
    if a0:
        if re.fullmatch(r"-\d{5,16}", a0):
            return Target(err=c.t("❌ این آیدی گروه یا کانال است، نه کاربر.", "❌ That's a group or channel id, not a user."))
        if re.fullmatch(r"\d{5,16}", a0):
            uid = int(a0)
            if uid >= 2 ** 52:
                return Target(err=c.t("❌ آیدی نامعتبر.", "❌ Invalid id."))
            row = await RT.db.one("SELECT first_name,last_name,username FROM rz_users WHERE user_id=?", (uid,))
            nm = " ".join(x for x in ((row or {}).get("first_name"), (row or {}).get("last_name")) if x)
            return Target(user_id=uid, name=nm, username=(row or {}).get("username"), via="id", consumed=1, verified=True)
        m = USERNAME_RE.match(c.args[0])
        if m and (c.args[0].startswith("@") or not (use_reply and c.reply)):
            un = m.group(1)
            row = await lookup_username(un)
            if not row:
                if not c.args[0].startswith("@"):
                    pass  # unknown word: treat it as the reason
                else:
                    return Target(err=c.t(
                        f"❌ @{esc(un)} پیدا نشد. روی پیامش ریپلای کنید یا آیدی عددی بدهید.",
                        f"❌ @{esc(un)} not found. Reply to their message or use the numeric id."))
            else:
                uid = row["user_id"]
                t = Target(user_id=uid, name=" ".join(x for x in (row.get("first_name"), row.get("last_name")) if x),
                           username=row["username"], via="username", consumed=1)
                if c.is_group:
                    mem = await authz.member(c.chat_id, uid, fresh=True)
                    if mem is not None and mem.status not in ("left", "kicked"):
                        cur = (mem.user.username or "").lower()
                        if cur == un.lower():
                            t.verified = True
                            t.name = mem.user.full_name
                        else:
                            await note_user(mem.user, c.chat_id)
                            return Target(err=c.t(
                                f"❌ یوزرنیم @{esc(un)} عوض شده. ریپلای کنید یا آیدی عددی بدهید.",
                                f"❌ @{esc(un)} has changed hands. Reply or use the numeric id."))
                if not t.verified:
                    age = time.time() - (row.get("last_seen") or 0)
                    if age > STALE_AFTER:
                        return Target(err=c.t(
                            f"❌ @{esc(un)} قابل تأیید نیست. ریپلای کنید یا آیدی عددی بدهید.",
                            f"❌ Can't verify @{esc(un)}. Reply or use the numeric id."))
                return t
    if use_reply and c.reply is not None:
        r = c.reply
        if r.sender_chat_id:
            return Target(sender_chat_id=r.sender_chat_id, sender_chat_type=r.sender_chat_type, name=r.sender_chat_title,
                          via="reply", verified=True)
        if r.user_id:
            return Target(user_id=r.user_id, name=r.user_name, username=r.username, via="reply", verified=True)
    return Target(err=c.t("روی پیام کاربر ریپلای کنید یا آیدی/یوزرنیم بدهید.",
                          "Reply to the user or give an id/username."))
