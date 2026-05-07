"""Teensy 4.1 sync hub interface.

Contains ``SyncProcess``, ``TeensySync``, ``MockSync``, and the
``make_sync_interface`` factory (ADR-015).
"""

from __future__ import annotations

from hapticore.sync.factory import make_sync_interface
from hapticore.sync.mock import MockSync
from hapticore.sync.sync_process import SyncProcess
from hapticore.sync.teensy_sync import TeensySync

__all__ = ["MockSync", "SyncProcess", "TeensySync", "make_sync_interface"]
