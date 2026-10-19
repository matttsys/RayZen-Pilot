"""Extension points so feature modules don't have to patch the router, planner or filters.

Each list holds plain async callables. A failing hook is logged and skipped; it never stops the update."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

log = logging.getLogger("pilot.hooks")

PERIODIC: list[tuple[str, float, Callable[[], Awaitable[None]]]] = []   # (name, every_seconds, fn)
ON_GROUP_MESSAGE: list[Callable] = []     # fn(message) for every non-service group message, before filters
AFTER_FILTERS: list[Callable] = []        # fn(message, text) when the message survived the filters
ON_MEMBER: list[Callable] = []            # fn(chat, user, joined: bool, by_user: int | None, link, via: str)
GUARDS: list[Callable] = []               # fn(message, eff) -> rule key if it removed the message
GUARD_KEYS: list[str] = []                # settings that enable a guard
ON_SHUTDOWN: list[Callable] = []
ON_STARTED: list[Callable] = []
PRIVATE_START: list[Callable] = []        # fn(cmd) -> True if it fully handled /start (owner edition gate)
PRIVATE_TEXT: list[Callable] = []         # fn(cmd) -> True if it blocked a private assistant message (owner edition gate)
PRIVATE_DOCUMENT: list[Callable] = []     # fn(message) -> True if it handled a document sent in private chat
CALLBACKS: dict[str, Callable] = {}       # callback family -> fn(query, cmd, parts), for feature modules


async def run(hooks: list[Callable], *a) -> None:
    for fn in hooks:
        try:
            await fn(*a)
        except Exception:  # noqa: BLE001
            log.exception("hook %s failed", getattr(fn, "__name__", fn))


async def first(hooks: list[Callable], *a) -> bool:
    """Run hooks in order until one returns True (it handled the update)."""
    for fn in hooks:
        try:
            if await fn(*a):
                return True
        except Exception:  # noqa: BLE001
            log.exception("hook %s failed", getattr(fn, "__name__", fn))
    return False


def periodic(name: str, every: float):
    def deco(fn):
        PERIODIC.append((name, every, fn))
        return fn
    return deco
