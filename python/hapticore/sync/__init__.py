"""Teensy 4.1 sync hub interface.

Contains ``SyncProcess`` (the subprocess that owns the USB serial
connection to the Teensy), ``TeensySync`` (the ``SyncInterface`` shim
that publishes commands over ZMQ), ``MockSync`` (the in-process mock),
and supporting modules for the wire protocol and serial adapter.

No ``make_sync_interface`` factory yet — Phase 5C adds it (see ADR-015).
Until then, ``cli/__init__.py`` constructs ``MockSync()`` directly.
"""

from __future__ import annotations

from hapticore.sync.mock import MockSync
from hapticore.sync.sync_process import SyncProcess
from hapticore.sync.teensy_sync import TeensySync

__all__ = ["MockSync", "SyncProcess", "TeensySync"]