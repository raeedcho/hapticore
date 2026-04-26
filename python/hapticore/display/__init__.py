"""Display interface implementations and factory.

The production path uses ``DisplayProcess`` (a multiprocessing.Process
subclass) plus ``DisplayClient`` (a ZMQ proxy). The mock path uses
``MockDisplay`` in-process. Selection is driven by ``DisplayConfig.backend``
via ``make_display_interface``. See ADR-015.
"""

from __future__ import annotations

from hapticore.display.client import DisplayClient
from hapticore.display.factory import make_display_interface
from hapticore.display.mock import MockDisplay

__all__ = ["DisplayClient", "MockDisplay", "make_display_interface"]
