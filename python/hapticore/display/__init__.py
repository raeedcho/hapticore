"""Display process and client for PsychoPy visual stimulus rendering.

This package provides the ZMQ transport layer for display commands.
PsychoPy is imported ONLY inside DisplayProcess.run() — never at module level.
"""

from __future__ import annotations

from hapticore.display.client import DisplayClient
from hapticore.display.factory import make_display_interface
from hapticore.display.mock import MockDisplay
from hapticore.display.process import DisplayProcess

__all__ = ["DisplayClient", "DisplayProcess", "MockDisplay", "make_display_interface"]
