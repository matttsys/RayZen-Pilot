"""Environment config."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


class ConfigError(SystemExit):
    pass


def redact(text: str, secrets: list[str] | None = None) -> str:
    s = str(text)
    for sec in secrets or []:
        if sec and len(sec) > 6:
            s = s.replace(sec, sec[:4] + "…redacted")
    s = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b", "<bot-token-redacted>", s)
    s = re.sub(r"\b(sk-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{10,}", r"\1…redacted", s)
    s = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]{8,}", r"\1<redacted>", s)
    return s


def normalize_base_url(url: str) -> str:
    """Strip quotes, whitespace, trailing slashes and a trailing /chat/completions."""
    u = (url or "").strip().strip("'\"").strip()
    u = u.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if u.lower().endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    return u


_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")


def parse_model_list(raw: str) -> tuple[list[str], list[str]]:
    """Split AI_FALLBACK_MODELS (comma, semicolon, space, newline or |). Returns (models, rejected)."""
    if not raw:
        return [], []
    s = raw.strip().strip("[]")
    parts = re.split(r"[,;|\s]+", s)
    out: list[str] = []
    bad: list[str] = []
    seen: set[str] = set()
    for p in parts:
        p = p.strip().strip("'\"`").strip()
        if not p:
            continue
        if not _MODEL_RE.match(p) or p.endswith("/") or "//" in p:
            bad.append(p[:40])
            continue
        if p.lower() in seen:
            continue
        seen.add(p.lower())
        out.append(p)
    return out, bad


@dataclass
class Config:
    token: str
    owner_id: int
    ai_base: str = ""
    ai_key: str = ""
    ai_model: str = ""
    ai_fallbacks: list[str] = field(default_factory=list)
    ai_rejected: list[str] = field(default_factory=list)
    ai_timeout: float = 60.0
    ai_extra: dict = field(default_factory=dict)
    log_channel: int | None = None
    tz: str = "Asia/Tehran"
    port: int | None = None
    db_path: str = "data/pilot.db"
    rate_group_per_min: int = 20
    ai_group_per_hour: int = 30
    ai_user_per_10min: int = 8
    ai_global_per_day: int = 3000
    ai_concurrency: int = 4
    warnings: list[str] = field(default_factory=list)

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_base and self.ai_key and self.ai_model)

    @property
    def models(self) -> list[str]:
        seen, out = set(), []
        for m in [self.ai_model, *self.ai_fallbacks]:
            if m and m.lower() not in seen:
                seen.add(m.lower())
                out.append(m)
        return out

    def secrets(self) -> list[str]:
        return [self.token, self.ai_key]


def _int(name: str, default: int, lo: int = 0, hi: int = 10**9) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError(f"CRITICAL: {name} must be an integer (got a non-numeric value).")
    return max(lo, min(hi, v))


def load_config() -> Config:
    token = (os.environ.get("BOT_TOKEN") or os.environ.get("BOT_TOKEN_PILOT") or "").strip().strip("'\"")
    if not re.match(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$", token):
        raise ConfigError("CRITICAL: BOT_TOKEN is missing or malformed (expected '<digits>:<secret>' from @BotFather).")
    owner = os.environ.get("OWNER_ID", "").strip()
    if not owner.isdigit() or int(owner) <= 0:
        raise ConfigError("CRITICAL: OWNER_ID must be your positive numeric Telegram user id (see @userinfobot).")
    cfg = Config(token=token, owner_id=int(owner))
    cfg.ai_base = normalize_base_url(os.environ.get("AI_BASE_URL", ""))
    cfg.ai_key = os.environ.get("AI_API_KEY", "").strip().strip("'\"")
    cfg.ai_model = os.environ.get("AI_MODEL", "").strip().strip("'\"")
    if cfg.ai_base and not re.match(r"^https?://", cfg.ai_base):
        cfg.warnings.append("AI_BASE_URL must start with http(s):// — AI disabled.")
        cfg.ai_base = ""
    if not cfg.ai_configured:
        missing = [n for n, v in (("AI_BASE_URL", cfg.ai_base), ("AI_API_KEY", cfg.ai_key), ("AI_MODEL", cfg.ai_model)) if not v]
        cfg.warnings.append("AI off: " + ", ".join(missing) + " not set.")
    cfg.ai_fallbacks, cfg.ai_rejected = parse_model_list(os.environ.get("AI_FALLBACK_MODELS", ""))
    if cfg.ai_rejected:
        cfg.warnings.append(f"AI_FALLBACK_MODELS: ignored {len(cfg.ai_rejected)} malformed entr(y/ies): {', '.join(cfg.ai_rejected)}")
    cfg.ai_timeout = float(_int("AI_TIMEOUT_S", 60, 5, 300))
    raw_extra = os.environ.get("AI_EXTRA_BODY", "").strip()
    if raw_extra:
        import json as _json
        try:
            ex = _json.loads(raw_extra)
            if not isinstance(ex, dict):
                raise ValueError
            cfg.ai_extra = {k: v for k, v in ex.items() if k not in ("model", "messages", "api_key")}
        except ValueError:
            cfg.warnings.append("AI_EXTRA_BODY must be a JSON object — ignored.")
    lc = os.environ.get("LOG_CHANNEL", "").strip()
    if lc:
        if re.match(r"^-?\d+$", lc):
            cfg.log_channel = int(lc)
        else:
            cfg.warnings.append("LOG_CHANNEL must be a numeric chat id — owner logs disabled.")
    tz = os.environ.get("TZ", "").strip() or "Asia/Tehran"
    try:
        ZoneInfo(tz)
        cfg.tz = tz
    except Exception:  # noqa: BLE001
        cfg.warnings.append(f"TZ '{tz[:40]}' unknown — using Asia/Tehran.")
        cfg.tz = "Asia/Tehran"
    port = os.environ.get("SERVER_PORT", "").strip()
    cfg.port = int(port) if port.isdigit() else None
    cfg.db_path = os.environ.get("DB_PATH_PILOT", "").strip() or "data/pilot.db"
    cfg.rate_group_per_min = _int("RATE_GROUP_PER_MIN", 20, 1, 60)
    cfg.ai_group_per_hour = _int("AI_GROUP_PER_HOUR", 30, 1, 10000)
    cfg.ai_user_per_10min = _int("AI_USER_PER_10MIN", 8, 1, 1000)
    cfg.ai_global_per_day = _int("AI_GLOBAL_PER_DAY", 3000, 1, 10**7)
    cfg.ai_concurrency = _int("AI_CONCURRENCY", 2, 1, 32)
    if os.environ.get("ALLOW_EVAL", "0").strip() not in ("", "0"):
        cfg.warnings.append("ALLOW_EVAL is ignored: /eval was removed for safety.")
    return cfg
