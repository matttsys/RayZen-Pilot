"""/purgeword: delete recent messages that contain a word.

Telegram doesn't let bots search a chat, so the bot keeps a 48-hour index of the
words in group messages it has seen. Only normalized words are stored, never the
message text, and rows are dropped once they're older than 48 hours (Telegram's
own limit for deleting messages). /purgeword off stops indexing and clears it.
"""
from __future__ import annotations

import re

from pilot import audit, confirm, moderation as M, settings as S, tg
from pilot.commands import command, say, set_setting
from pilot.ctx import Cmd
from pilot.rt import RT
from pilot.util import esc, norm_digits, now

WINDOW = 48 * 3600          # Telegram can't delete older messages
SAFE = WINDOW - 120         # stay a little under the limit
MAX_WORDS = 300             # unique words kept per message
MAX_DELETE = 3000           # per run (30 deleteMessages calls)
MIN_LEN = 2
MAX_ALTS = 5                # comma-separated alternatives per run

_MAP = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا", "ٱ": "ا", "آ": "ا",
                      "\u200c": " ", "\u200d": "", "\u200e": "", "\u200f": "", "\ufeff": "", "\u0640": ""})
_MARKS = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
_TOKEN = re.compile(r"[^\W_]+")
_ALT = re.compile(r"[,،|؛;]+")

# endings written without a space: متینو، متینه، متینی، متینا، متینم، متینشون، متینجان ...
FA_SUFFIXES = frozenset("و ه ی ا م ت ش مو تو شو مون تون شون مان تان شان ها های هایی هام هات هاش "
                        "ای یه یم ید ام ات اش ان جان جون رو را تر ترین ست".split())
EN_SUFFIXES = frozenset(("s", "es"))


# matching
def normalize(s: str) -> str:
    s = norm_digits(s or "").translate(_MAP)
    return _MARKS.sub("", s).casefold()


def tokens(s: str) -> list[str]:
    return _TOKEN.findall(normalize(s))


def query_terms(raw: str) -> list[str]:
    """Words to look for. More than one word means all of them must be in the message."""
    return list(dict.fromkeys(tokens(raw)))[:5]


def query_groups(raw: str) -> list[list[str]]:
    """Comma-separated alternatives: "متین، matin" matches either one."""
    out: list[list[str]] = []
    for part in _ALT.split(raw or ""):
        t = query_terms(part)
        if t and t not in out:
            out.append(t)
    return out[:MAX_ALTS]


def valid_query(raw: str) -> bool:
    g = query_groups(raw)
    return bool(g) and all(len(t) >= MIN_LEN for terms in g for t in terms)


def token_match(tok: str, word: str) -> bool:
    if tok == word:
        return True
    if len(word) < 3 or not tok.startswith(word):
        return False
    return tok[len(word):] in (EN_SUFFIXES if word.isascii() else FA_SUFFIXES)


def message_match(words: set[str] | list[str], terms: list[str]) -> bool:
    return all(any(token_match(t, w) for t in words) for w in terms)


# index
async def enabled(chat_id: int) -> bool:
    return bool(await S.get(chat_id, "word_index"))


async def note(m, edited: bool = False) -> None:
    """Add a group message to the index (or refresh it after an edit)."""
    chat = m.chat
    if chat is None or chat.type not in ("group", "supergroup"):
        return
    text = m.text or m.caption or ""
    u = m.from_user
    if u is not None and u.id == RT.bot_id:
        return
    ts = m.date.timestamp() if m.date else now()
    if now() - ts > WINDOW or not await enabled(chat.id):
        return
    if text.lstrip().startswith("/"):
        if edited:
            await RT.db.run("DELETE FROM rz_msg_index WHERE chat_id=? AND message_id=?", (chat.id, m.message_id))
        return
    # messages without text (media, stickers) are kept with sender and time only, for /purgeuser
    toks = sorted({t[:64] for t in tokens(text[:4000])})[:MAX_WORDS] if text else []
    sender = m.sender_chat.id if m.sender_chat is not None else (u.id if u is not None else None)
    await RT.db.run("INSERT OR REPLACE INTO rz_msg_index(chat_id,message_id,sender_id,ts,words) VALUES(?,?,?,?,?)",
                    (chat.id, m.message_id, sender, ts, (" " + " ".join(toks) + " ") if toks else " "))


async def forget_ids(chat_id: int, ids: list[int]) -> None:
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        await RT.db.run(f"DELETE FROM rz_msg_index WHERE chat_id=? AND message_id IN ({','.join('?' * len(part))})",
                        (chat_id, *part))


async def clear(chat_id: int) -> int:
    _, n = await RT.db.run("DELETE FROM rz_msg_index WHERE chat_id=?", (chat_id,))
    return n


async def sweep() -> None:
    """Drop rows older than 48h, and everything for groups that turned the index off."""
    await RT.db.run("DELETE FROM rz_msg_index WHERE ts<?", (now() - WINDOW,))
    for r in await RT.db.all("SELECT DISTINCT chat_id FROM rz_msg_index"):
        if not await enabled(r["chat_id"]):
            await clear(r["chat_id"])


async def find(chat_id: int, groups: list[list[str]] | list[str]) -> list[dict]:
    """Indexed messages (newer than 48h minus a margin) that match any of the alternatives."""
    if groups and isinstance(groups[0], str):
        groups = [groups]
    groups = [g for g in groups if g]
    if not groups:
        return []
    keys = [max(g, key=len) for g in groups]
    like = " OR ".join("words LIKE ?" for _ in keys)
    rows = await RT.db.all(f"SELECT message_id, sender_id, words FROM rz_msg_index WHERE chat_id=? AND ts>=? AND ({like}) "
                           "ORDER BY message_id", (chat_id, now() - SAFE, *[f"% {k}%" for k in keys]))
    out = []
    for r in rows:
        words = r["words"].split()
        if any(message_match(words, g) for g in groups):
            out.append(r)
    return out


async def indexed_count(chat_id: int) -> int:
    return await RT.db.val("SELECT COUNT(*) FROM rz_msg_index WHERE chat_id=? AND ts>=?", (chat_id, now() - WINDOW), 0)


# command
def _people(c: Cmd, n: int) -> str:
    return c.t(f"{n} نفر", "1 person" if n == 1 else f"{n} people")


def _label(raw: str) -> str:
    return esc(" ".join(raw.split())[:40])


async def preview(c: Cmd, raw: str) -> str:
    """Count matches and ask for confirmation. Returns text when there's nothing to confirm."""
    if not await enabled(c.chat_id):
        return c.t("نمایهٔ پیام‌ها خاموش است. با <code>/purgeword on</code> روشنش کن؛ پیام‌های بعد از آن قابل پاکسازی‌اند.",
                   "The message index is off. Turn it on with <code>/purgeword on</code>; messages after that can be purged.")
    if not valid_query(raw):
        return c.t("حداقل ۲ حرف.", "At least 2 letters.")
    b = await M.bot_can(c, "delete")
    if b:
        return b
    rows = await find(c.chat_id, query_groups(raw))
    label = _label(raw)
    if not rows:
        return c.t(f"در ۴۸ ساعت اخیر پیامی با «{label}» نیست.", f"No messages with “{label}” in the last 48h.")
    people = len({r["sender_id"] for r in rows})
    tok = await confirm.create(c.chat_id, c.actor_id, "purgeword", {"raw": raw[:100], "cmd": c.to_payload()}, right="delete")
    await c.say(c.t(f"🧹 {len(rows)} پیام با «{label}» از {_people(c, people)} در ۴۸ ساعت اخیر. پاک شوند؟",
                    f"🧹 {len(rows)} messages with “{label}” from {_people(c, people)} in the last 48h. Delete them?"),
                rows=confirm.buttons(tok, c.lang, c.t("🧹 بله، پاک کن", "🧹 Yes, delete")))
    return ""


async def execute(c: Cmd, raw: str) -> str:
    """Delete every indexed message that matches. Used after confirmation (button or AI plan)."""
    if not await enabled(c.chat_id):
        return c.t("نمایهٔ پیام‌ها خاموش است: <code>/purgeword on</code>", "The message index is off: <code>/purgeword on</code>")
    if not valid_query(raw):
        return c.t("حداقل ۲ حرف.", "At least 2 letters.")
    b = await M.bot_can(c, "delete")
    if b:
        return b
    label = _label(raw)
    ids = [r["message_id"] for r in await find(c.chat_id, query_groups(raw))]
    if not ids:
        return c.t(f"پیامی با «{label}» نماند.", f"No messages with “{label}” left.")
    batch = ids[:MAX_DELETE]
    done: list[int] = []
    failed = 0
    last: tg.Out | None = None
    for i in range(0, len(batch), 100):
        chunk = batch[i:i + 100]
        out = await tg.call(RT.bot.delete_messages, chat_id=c.chat_id, message_ids=chunk)
        if out.ok:
            done += chunk
        else:
            failed += len(chunk)
            last = out
    await forget_ids(c.chat_id, done)
    outcome = "ok" if not failed else ("partial" if done else "failed")
    await audit.record(c.chat_id, c.actor_id, M.actor_kind(c), "purge_word",
                       params={"word": " ".join(raw.split())[:60], "n": len(done), "failed": failed}, outcome=outcome,
                       error=f"{last.kind}: {last.desc}" if last else None,
                       log_line=f"#PURGEWORD «{label}» · {len(done)}" + (f" · failed {failed}" if failed else "") + f"\n{M.who(c)}")
    if not done:
        return M.fail_text(c, last, "delete") if last else c.t("❌ پاک نشد.", "❌ Not deleted.")
    txt = c.t(f"🧹 {len(done)} پیام با «{label}» پاک شد.", f"🧹 Deleted {len(done)} messages with “{label}”.")
    if failed:
        txt += c.t(f"\n{failed} پیام پاک نشد.", f"\n{failed} couldn't be deleted.")
    if len(ids) > MAX_DELETE:
        txt += c.t(f"\n{len(ids) - MAX_DELETE} پیام دیگر مانده؛ دوباره بزن.", f"\n{len(ids) - MAX_DELETE} more left; run it again.")
    return txt


async def status_text(c: Cmd) -> str:
    on = await enabled(c.chat_id)
    n = await indexed_count(c.chat_id)
    return c.t("🧹 <code>/purgeword کلمه</code>\nپیام‌های ۴۸ ساعت اخیر که این کلمه را دارند، بعد از تأیید پاک می‌شوند.\n"
               "چند کلمه با ویرگول: <code>/purgeword متین، matin</code>\n"
               f"نمایه: {'روشن' if on else 'خاموش'} · {n} پیام · <code>/purgeword on|off</code>",
               "🧹 <code>/purgeword word</code>\nDeletes messages from the last 48h that contain the word, after you confirm.\n"
               "Several words, comma-separated: <code>/purgeword matin, متین</code>\n"
               f"Index: {'on' if on else 'off'} · {n} messages · <code>/purgeword on|off</code>")


@command("purgeword", "purgewords", right="delete", section="mod")
async def k_purgeword(c: Cmd) -> None:
    raw = c.rest.strip()
    low = raw.lower()
    if low in ("", "status", "help"):
        return await say(c, await status_text(c))
    if low in ("on", "off"):
        txt = await set_setting(c, "word_index", low == "on")
        if low == "off":
            n = await clear(c.chat_id)
            txt += c.t(f"\nنمایه پاک شد ({n} پیام).", f"\nIndex cleared ({n} messages).")
        return await say(c, txt)
    await say(c, await preview(c, raw))


@confirm.handler("purgeword")
async def _purgeword_ok(p: dict, presser: int, chat_id: int, message) -> str:
    c = Cmd.from_payload(p["cmd"])
    c.actor_id, c.anon = presser, False
    return await execute(c, p["raw"])
