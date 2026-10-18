#!/usr/bin/env python3
"""Interactive setup for RayZen Pilot. Writes .env next to this file.

    python3 configure.py                  ask everything
    python3 configure.py --non-interactive  read BOT_TOKEN, OWNER_ID, AI_API_KEY ... from the environment
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOUS = "https://inference-api.nousresearch.com/v1"
DEFAULT_MODEL = "meituan/longcat-2.5-preview:free"
DEFAULT_FALLBACKS = "reflection/beam-1.0-501b-a23b:free,stepfun/step-5-preview:free,stepfun/step-3.7-flash:free"
TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$")

TEMPLATE = """# RayZen Pilot configuration

# Bot token from @BotFather
BOT_TOKEN=

# Your numeric Telegram user id
OWNER_ID=

# AI assistant (optional). Any OpenAI-compatible API works.
AI_BASE_URL={nous}
AI_API_KEY=
AI_MODEL={model}
AI_FALLBACK_MODELS={fallbacks}

# Default timezone for new groups
TZ=Asia/Tehran

# Chat id for owner notices. Empty = your private chat.
LOG_CHANNEL=
""".format(nous=NOUS, model=DEFAULT_MODEL, fallbacks=DEFAULT_FALLBACKS)

COLOR = sys.stdout.isatty() and os.name != "nt"


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, total: int, title: str) -> None:
    say()
    say(c("1;36", f"[{n}/{total}] ") + c("1", title))


def good(text: str) -> None:
    say(c("32", "  ✔ ") + text)


def warn(text: str) -> None:
    say(c("33", "  ! ") + text)


def bad(text: str) -> None:
    say(c("31", "  ✖ ") + text)


class Console:
    """Reads from the terminal even when stdin is a pipe (curl ... | bash)."""

    def __init__(self) -> None:
        self.tty = None
        if not sys.stdin.isatty() and os.name != "nt":
            try:
                self.tty = open("/dev/tty", "r+", encoding="utf-8")
            except OSError:
                self.tty = None

    def ask(self, prompt: str, default: str = "", secret: bool = False) -> str:
        shown = f" [{mask(default) if secret else default}]" if default else ""
        text = f"  {prompt}{shown}: "
        if secret:
            if self.tty:
                val = getpass.getpass(text, stream=self.tty)
            else:
                val = getpass.getpass(text)
        elif self.tty:
            self.tty.write(text)
            self.tty.flush()
            val = self.tty.readline()
        else:
            val = input(text)
        val = (val or "").strip()
        return val or default

    def yes(self, prompt: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            v = self.ask(f"{prompt} ({hint})").lower()
            if not v:
                return default
            if v in ("y", "yes", "بله", "آره"):
                return True
            if v in ("n", "no", "نه", "خیر"):
                return False


def mask(v: str) -> str:
    if not v:
        return ""
    return v[:4] + "…" + v[-4:] if len(v) > 12 else "…"


def api(url: str, headers: dict | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "rayzen-pilot-setup"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, {}


def check_token(token: str) -> tuple[bool | None, str]:
    code, data = api(f"https://api.telegram.org/bot{token}/getMe")
    if code == 200 and data.get("ok"):
        return True, "@" + data["result"].get("username", "?")
    if code in (401, 404):
        return False, "Telegram rejected this token"
    return None, "could not reach api.telegram.org"


def detect_owner(token: str, console: Console, bot: str) -> str:
    say(f"  Open {c('1', bot)} in Telegram and send it any message. Waiting up to 90 seconds…")
    deadline = time.time() + 90
    while time.time() < deadline:
        code, data = api(f"https://api.telegram.org/bot{token}/getUpdates?timeout=10&allowed_updates=%5B%22message%22%5D",
                         timeout=15)
        if code == 409:
            warn("The bot is already running somewhere else, so auto-detect can't read messages.")
            return ""
        for upd in reversed(data.get("result", []) if code == 200 else []):
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            user = msg.get("from") or {}
            if chat.get("type") == "private" and user.get("id"):
                name = user.get("first_name", "")
                good(f"Found you: {name} ({user['id']})")
                return str(user["id"])
        time.sleep(1)
    warn("No message received.")
    return ""


def check_ai(base: str, key: str) -> tuple[bool | None, str]:
    code, _ = api(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}", "User-Agent": "rayzen-pilot-setup"})
    if code == 200:
        return True, "key accepted"
    if code in (401, 403):
        return False, "the AI provider rejected this key"
    if code == 0:
        return None, "could not reach the AI provider"
    return None, f"provider answered HTTP {code}; the key may still work"


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if v.startswith("#"):
            v = ""
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def write_env(path: Path, values: dict[str, str]) -> None:
    example = HERE / ".env.example"
    template = example.read_text(encoding="utf-8") if example.is_file() else TEMPLATE
    lines, seen = [], set()
    for raw in template.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", raw)
        if m and m.group(1) in values:
            key = m.group(1)
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(raw)
    extra = [k for k in values if k not in seen and values[k] != ""]
    if extra:
        lines.append("")
        lines.append("# Other settings")
        lines += [f"{k}={values[k]}" for k in extra]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(path)


def valid_tz(tz: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
        return True
    except Exception:  # noqa: BLE001
        return False


def interactive(env_path: Path, skip_check: bool) -> dict[str, str]:
    console = Console()
    cur = read_env(env_path)
    vals = dict(cur)
    total = 4
    say()
    say(c("1;35", "  RayZen Pilot setup") + c("2", "  ·  راه‌اندازی"))
    say(c("2", f"  Writing to {env_path}"))

    step(1, total, "Bot token  ·  توکن ربات")
    say(c("2", "  Create a bot with @BotFather and paste the token here."))
    bot_name = ""
    while True:
        token = console.ask("BOT_TOKEN", cur.get("BOT_TOKEN", ""), secret=True)
        if not TOKEN_RE.match(token):
            bad("That doesn't look like a bot token (123456:ABC…).")
            continue
        if skip_check:
            break
        ok, info = check_token(token)
        if ok:
            bot_name = info
            good(f"Token works: {info}")
            break
        if ok is False:
            bad(info)
            continue
        warn(f"{info}; saving it anyway.")
        break
    vals["BOT_TOKEN"] = token

    step(2, total, "Owner  ·  مالک ربات")
    say(c("2", "  Your numeric Telegram id. Leave empty to detect it automatically."))
    while True:
        owner = console.ask("OWNER_ID", cur.get("OWNER_ID", ""))
        if not owner and bot_name:
            owner = detect_owner(token, console, bot_name)
        if owner.isdigit() and int(owner) > 0:
            break
        bad("Needs a number. You can get it from @userinfobot.")
    vals["OWNER_ID"] = owner

    step(3, total, "AI assistant  ·  هوش مصنوعی")
    say(c("2", "  Optional. Without a key the bot works normally, /ai is just off."))
    has_key = bool(cur.get("AI_API_KEY"))
    if console.yes("Use the AI assistant?", default=True if has_key or not cur else has_key):
        base = console.ask("AI_BASE_URL", cur.get("AI_BASE_URL") or NOUS)
        while True:
            key = console.ask("AI_API_KEY", cur.get("AI_API_KEY", ""), secret=True)
            if key:
                break
            bad("Key is empty.")
        if not skip_check:
            ok, info = check_ai(base, key)
            (good if ok else bad if ok is False else warn)(info)
        vals["AI_BASE_URL"] = base
        vals["AI_API_KEY"] = key
        vals["AI_MODEL"] = console.ask("AI_MODEL", cur.get("AI_MODEL") or DEFAULT_MODEL)
        vals.setdefault("AI_FALLBACK_MODELS", cur.get("AI_FALLBACK_MODELS") or (DEFAULT_FALLBACKS if base.rstrip("/") == NOUS else ""))
    else:
        vals["AI_API_KEY"] = ""

    step(4, total, "Timezone  ·  منطقهٔ زمانی")
    while True:
        tz = console.ask("TZ", cur.get("TZ") or "Asia/Tehran")
        if valid_tz(tz):
            break
        bad("Unknown timezone. Example: Asia/Tehran, Europe/Berlin, UTC")
    vals["TZ"] = tz
    return vals


def non_interactive(env_path: Path) -> dict[str, str]:
    vals = read_env(env_path)
    for k in ("BOT_TOKEN", "OWNER_ID", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_FALLBACK_MODELS", "TZ", "LOG_CHANNEL"):
        if os.environ.get(k):
            vals[k] = os.environ[k].strip()
    if not TOKEN_RE.match(vals.get("BOT_TOKEN", "")):
        raise SystemExit("BOT_TOKEN is missing or invalid.")
    if not vals.get("OWNER_ID", "").isdigit():
        raise SystemExit("OWNER_ID is missing.")
    vals.setdefault("AI_BASE_URL", NOUS)
    vals.setdefault("AI_MODEL", DEFAULT_MODEL)
    vals.setdefault("TZ", "Asia/Tehran")
    return vals


def main() -> int:
    ap = argparse.ArgumentParser(description="Create or update the RayZen Pilot .env file.")
    ap.add_argument("--env", default=str(HERE / ".env"), help="path of the .env file (default: next to this script)")
    ap.add_argument("--non-interactive", action="store_true", help="take values from environment variables")
    ap.add_argument("--skip-check", action="store_true", help="don't contact Telegram or the AI provider")
    args = ap.parse_args()
    env_path = Path(args.env).resolve()
    try:
        vals = non_interactive(env_path) if args.non_interactive else interactive(env_path, args.skip_check)
    except (KeyboardInterrupt, EOFError):
        say()
        warn("Cancelled. Nothing was written.")
        return 1
    env_path.parent.mkdir(parents=True, exist_ok=True)
    write_env(env_path, vals)
    say()
    good(f"Saved {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
