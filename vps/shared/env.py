""".env loader. Existing environment variables win."""
from __future__ import annotations

import os
from pathlib import Path


def parse_env_line(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line:
        return None
    key, _, val = line.partition("=")
    key, val = key.strip(), val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    elif val.startswith("#"):
        val = ""
    elif " #" in val or "\t#" in val:
        val = val.replace("\t#", " #").split(" #", 1)[0].rstrip()
    return (key, val) if key else None


def load_env(path: Path | str) -> int:
    p = Path(path)
    if not p.is_file():
        return 0
    n = 0
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        kv = parse_env_line(raw)
        if kv and kv[0] not in os.environ:
            os.environ[kv[0]] = kv[1]
            n += 1
    return n
