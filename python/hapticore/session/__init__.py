"""Session lifecycle management.

``SessionManager`` owns the recording subprocess lifecycle, publishes
session-level commands, creates the data directory, and writes the
session receipt.
"""

from __future__ import annotations

from hapticore.session.manager import SessionManager

__all__ = ["SessionManager"]
