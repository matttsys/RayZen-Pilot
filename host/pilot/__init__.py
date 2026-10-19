"""RayZen Pilot."""
import os as _os

# Pre-release: v5.0.0 is tagged only after the operator's live Telegram acceptance (see CHANGELOG).
__version__ = "5.0.0-alpha.2"
CODENAME = "Omni"
# AGPL-3.0 section 13: a modified bot must offer its users the corresponding source. Forks set SOURCE_URL.
_src = _os.environ.get("SOURCE_URL", "").strip()
SOURCE_URL = _src if _src.startswith("https://") and len(_src) < 200 else "https://github.com/matttsys/RayZen-Pilot"
