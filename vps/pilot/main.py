"""Bot process: config, database, application, ticker, polling."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from telegram import (BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
                      LinkPreviewOptions, Update)
from telegram.constants import ParseMode
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder, Defaults, TypeHandler

from pilot import __version__
from pilot.config import ConfigError, load_config, redact
from pilot.rt import RT

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("pilot")
TICK_S = 15
ALLOWED = ["message", "edited_message", "channel_post", "callback_query", "my_chat_member", "chat_member", "chat_join_request"]


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        red = redact(msg, RT.cfg.secrets() if RT.cfg else [])
        if red != msg:
            record.msg, record.args = red, ()
        return True


def setup_logging() -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    h.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper() if os.environ.get("LOG_LEVEL") else logging.INFO)
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


GROUP_CMDS = [("help", "راهنما", "Help"), ("rules", "قوانین گروه", "Group rules"), ("faq", "پاسخ‌ها", "Answers"),
              ("report", "گزارش به ادمین‌ها", "Report to admins"), ("contact", "پیام به ادمین‌ها", "Message admins"),
              ("remind", "یادآوری", "Reminder"), ("ai", "هوش مصنوعی", "AI")]
ADMIN_CMDS = [("settings", "تنظیمات", "Settings"), ("health", "وضعیت گروه", "Group health"), ("ban", "بن", "Ban"),
              ("mute", "سایلنت", "Mute"), ("warn", "اخطار", "Warn"), ("unban", "رفع بن", "Unban"), ("unmute", "رفع سایلنت", "Unmute"),
              ("lock", "قفل", "Lock"), ("unlock", "باز کردن", "Unlock"), ("night", "قفل شبانه", "Night lock"),
              ("setwelcome", "متن خوشامد", "Welcome text"), ("setlog", "تنظیم لاگ", "Set log"),
              ("history", "تاریخچه", "History"), ("why", "چرا پیام پاک شد", "Why deleted"),
              ("announce", "اطلاعیه", "Announcement"), ("digest", "گزارش", "Report"),
              ("ai", "هوش مصنوعی", "AI"), ("help", "راهنما", "Help")]
PRIVATE_CMDS = [("start", "شروع", "Start"), ("help", "راهنما", "Help"), ("mycases", "پرونده‌های من", "My cases"),
                ("reminders", "یادآوری‌های من", "My reminders"), ("mydata", "داده‌های من", "My data"),
                ("privacy", "حریم خصوصی", "Privacy"), ("forgetme", "حذف داده‌های من", "Delete my data")]


async def set_commands(app: Application) -> None:
    for scope, cmds in ((BotCommandScopeAllGroupChats(), GROUP_CMDS), (BotCommandScopeAllChatAdministrators(), ADMIN_CMDS),
                        (BotCommandScopeAllPrivateChats(), PRIVATE_CMDS)):
        for lang in (None, "en"):
            try:
                await app.bot.set_my_commands([BotCommand(n, en if lang == "en" else fa) for n, fa, en in cmds],
                                              scope=scope, language_code=lang)
            except Exception as exc:  # noqa: BLE001
                log.warning("set_my_commands failed: %s", exc)


async def ticker() -> None:
    from pilot import planner
    while True:
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(planner.tick(), timeout=120)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            RT.tick_stats["errors"] = RT.tick_stats.get("errors", 0) + 1
            log.exception("ticker iteration failed")
        await asyncio.sleep(max(1.0, TICK_S - (time.monotonic() - t0)))


async def post_init(app: Application) -> None:
    from pilot import ai, locks, planner
    from pilot.db import DB
    RT.app, RT.bot = app, app.bot
    me = await app.bot.get_me()
    RT.bot_id, RT.bot_username = me.id, me.username or ""
    RT.db = DB(str((ROOT / RT.cfg.db_path) if not os.path.isabs(RT.cfg.db_path) else RT.cfg.db_path))
    info = await RT.db.open()
    log.info("database %s schema v%s→v%s backup=%s legacy_import=%s", RT.cfg.db_path, info.get("from"), info.get("to"),
             info.get("backup"), info.get("legacy"))
    RT.ai = ai.AIClient(RT.cfg)
    rec = await planner.recover_on_start()
    log.info("restart recovery: %s", rec)
    rows = await RT.db.all("SELECT DISTINCT chat_id FROM rz_lock_layers UNION SELECT chat_id FROM rz_perm_state")
    for r in rows:
        res = await locks.reconcile(r["chat_id"])
        log.info("lock reconcile chat=%s ok=%s %s", r["chat_id"], res.ok, res.kind if not res.ok else "")
    await set_commands(app)
    if not os.environ.get("PILOT_NO_TICKER"):
        RT.tick_stats["task"] = asyncio.get_running_loop().create_task(ticker())
    for w in RT.cfg.warnings:
        log.warning("config: %s", w)
    log.info("RayZen Pilot %s up as @%s (id %s); AI=%s models=%s tz=%s", __version__, RT.bot_username, RT.bot_id,
             "on" if RT.cfg.ai_configured else "off", f"{len(RT.cfg.models)} (hidden)", RT.cfg.tz)
    from pilot.logs import owner
    await owner(f"✅ ربات روشن شد · {__version__}")


async def post_shutdown(app: Application) -> None:
    task = RT.tick_stats.get("task")
    if task:
        task.cancel()
    if RT.ai:
        await RT.ai.close()
    if RT.db:
        await RT.db.close()
    log.info("shutdown complete")


async def on_error(update, context) -> None:
    log.error("unhandled error: %s", redact(repr(context.error), RT.cfg.secrets() if RT.cfg else []))


def build_app(request=None, rate_limit: bool = True) -> Application:
    from pilot import router
    # all bot text is HTML with escaped values
    defaults = Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))
    b = (ApplicationBuilder().token(RT.cfg.token).defaults(defaults).concurrent_updates(64)
         .post_init(post_init).post_shutdown(post_shutdown))
    if request is not None:
        b = b.request(request).get_updates_request(request)
    else:
        b = (b.connect_timeout(15).read_timeout(30).write_timeout(30).pool_timeout(15).connection_pool_size(64)
             .get_updates_read_timeout(40))
    if rate_limit:
        try:
            b = b.rate_limiter(AIORateLimiter(max_retries=0))
        except RuntimeError:
            log.warning("aiolimiter not installed; running without the PTB rate limiter")
    app = b.build()
    app.add_handler(TypeHandler(Update, router.dispatch))
    app.add_error_handler(on_error)
    return app


def main() -> int:
    from shared.env import load_env
    load_env(ROOT / ".env")
    setup_logging()
    try:
        RT.cfg = load_config()
    except ConfigError as e:
        print(str(e), flush=True)
        return 2
    os.chdir(ROOT)
    app = build_app()
    app.run_polling(allowed_updates=ALLOWED, drop_pending_updates=False, close_loop=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
