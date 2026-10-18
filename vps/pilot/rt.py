"""Runtime globals."""
from __future__ import annotations

import time
from typing import Any


class Runtime:
    def __init__(self) -> None:
        self.cfg = None
        self.db = None
        self.app = None
        self.bot = None
        self.bot_id = 0
        self.bot_username = ""
        self.started_at = time.time()
        self.ai = None
        self.settings_cache: dict[int, dict[str, tuple[Any, str]]] = {}
        self.member_cache: dict[tuple[int, int], tuple[float, Any]] = {}
        self.admins_cache: dict[int, tuple[float, set[int]]] = {}
        self.user_seen: dict[int, tuple[float, str]] = {}
        self.degraded: dict[str, str] = {}
        self.tick_stats: dict[str, Any] = {"last": 0.0, "errors": 0}

    @property
    def bot_link(self) -> str:
        return f"https://t.me/{self.bot_username}" if self.bot_username else ""


RT = Runtime()
