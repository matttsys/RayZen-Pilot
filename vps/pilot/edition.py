"""Which distribution this is.

selfhost: the public build. No channel-membership requirement, no remote checks, no telemetry.
owner_hosted: the project owner's hosted bot. Adds the optional @rzroot start flow (pilot/gate.py), which
only ships in that build. If gate.py shows up in a selfhost build it is refused, not loaded."""
from __future__ import annotations

import importlib.util
import logging

from pilot import _build

log = logging.getLogger("pilot.edition")
EDITIONS = ("selfhost", "owner_hosted")
EDITION = _build.EDITION if _build.EDITION in EDITIONS else "selfhost"
BUILD_ID = _build.BUILD_ID
COMMIT = _build.COMMIT


def owner_hosted() -> bool:
    return EDITION == "owner_hosted"


def label(lang: str = "fa") -> str:
    if owner_hosted():
        return "نسخهٔ میزبانی مالک" if lang == "fa" else "owner-hosted"
    return "نسخهٔ خودمیزبان" if lang == "fa" else "self-hosted"


def load_overlay() -> str:
    """Import the owner-hosted gate when allowed. Returns a short status for logs and the panel."""
    present = importlib.util.find_spec("pilot.gate") is not None
    if not owner_hosted():
        if present:
            log.warning("pilot/gate.py found in a selfhost build; ignored")
            return "refused (selfhost build)"
        return "none"
    if not present:
        log.error("owner_hosted build without pilot/gate.py")
        return "missing"
    from pilot import gate  # noqa: F401
    return "loaded"
