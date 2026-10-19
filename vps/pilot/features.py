"""Imports the feature modules so their commands, hooks and AI tools are registered once, in a fixed order."""
from pilot import activity, assistant, guards, invites, members, metrics, owner_panel, updater, utilities  # noqa: F401
from pilot import edition

OVERLAY = edition.load_overlay()   # owner-hosted gate; "none" in the public build
