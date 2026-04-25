"""Teensy 4.1 sync hub interface.

Contains the ``SyncProcess`` subprocess that owns the USB serial
connection to the Teensy, the ``TeensySync`` shim that satisfies
``SyncInterface`` by publishing over ZMQ, and supporting modules for
the wire protocol and serial adapter.
"""

from __future__ import annotations

from hapticore.sync.mock import MockSync
from hapticore.sync.sync_process import SyncProcess
from hapticore.sync.teensy_sync import TeensySync

__all__ = ["MockSync", "SyncProcess", "TeensySync"]
