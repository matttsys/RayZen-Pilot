"""Usage counters and AI accounting for the owner panel.

Counters are buffered in memory and written every minute (and on shutdown), so a busy group doesn't cost one
SQLite write per command. A crash can lose at most the last minute of counts."""
from __future__ import annotations

import time
from collections import Counter

from pilot import hooks
from pilot.rt import RT

_buf: Counter = Counter()


def day_key(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts or time.time()))


def count(name: str, n: int = 1) -> None:
    _buf[(day_key(), name[:48])] += n


@hooks.periodic("usage_flush", 60)
async def flush() -> None:
    if not _buf or RT.db is None:
        return
    items = list(_buf.items())
    _buf.clear()

    def f(c):
        c.executemany("INSERT INTO rz_usage(day,name,n) VALUES(?,?,?) ON CONFLICT(day,name) DO UPDATE SET n=n+excluded.n",
                      [(d, nm, n) for (d, nm), n in items])
    await RT.db.tx(f)


hooks.ON_SHUTDOWN.append(flush)


async def top_usage(days: int = 7, prefix: str = "cmd:", limit: int = 15) -> list[tuple[str, int]]:
    await flush()
    since = day_key(time.time() - days * 86400)
    rows = await RT.db.all("SELECT name, SUM(n) AS n FROM rz_usage WHERE day>=? AND name LIKE ? GROUP BY name ORDER BY n DESC LIMIT ?",
                           (since, prefix + "%", limit))
    return [(r["name"][len(prefix):], int(r["n"])) for r in rows]


def percentile(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


async def ai_summary(hours: int = 24) -> dict:
    """Calls, failures by kind, tokens split by source, latency percentiles."""
    t = time.time() - hours * 3600
    rows = await RT.db.all("SELECT ok, kind, model, prompt_tokens, completion_tokens, usage_src, latency_ms, attempts, feature "
                           "FROM rz_ai_usage WHERE ts>?", (t,))
    out = {"calls": len(rows), "ok": 0, "failed": Counter(), "prompt_tokens": 0, "completion_tokens": 0,
           "with_usage": 0, "without_usage": 0, "attempts": 0, "models": Counter(), "features": Counter()}
    lat = []
    for r in rows:
        if r["ok"]:
            out["ok"] += 1
        else:
            out["failed"][r["kind"] or "?"] += 1
        if r["usage_src"] == "provider":
            out["with_usage"] += 1
            out["prompt_tokens"] += r["prompt_tokens"] or 0
            out["completion_tokens"] += r["completion_tokens"] or 0
        elif r["ok"]:
            out["without_usage"] += 1
        out["attempts"] += r["attempts"] or 1
        if r["model"]:
            out["models"][r["model"]] += 1
        out["features"][r["feature"] or "assistant"] += 1
        if r["latency_ms"]:
            lat.append(r["latency_ms"])
    out["p50"], out["p95"] = percentile(lat, 50), percentile(lat, 95)
    return out


async def record_ai(chat_id: int | None, user_id: int | None, ok: bool, kind: str, model: str | None, usage: dict | None,
                    latency_ms: int, attempts: int, feature: str) -> None:
    """One row per user request. Provider-reported tokens are stored as-is; nothing is estimated."""
    u = usage or {}
    pt, ct = u.get("prompt_tokens"), u.get("completion_tokens")
    src = "provider" if isinstance(pt, int) or isinstance(ct, int) else "none"
    await RT.db.run("INSERT INTO rz_ai_usage(chat_id,user_id,ts,model,ok,kind,prompt_tokens,completion_tokens,usage_src,latency_ms,"
                    "attempts,feature) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chat_id, user_id, time.time(), model, int(ok), kind, pt if isinstance(pt, int) else None,
                     ct if isinstance(ct, int) else None, src, latency_ms, attempts, feature))
