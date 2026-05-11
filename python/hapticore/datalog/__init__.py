"""Behavioral data logging to disk.

``DataLoggerProcess`` subscribes to the ZMQ event bus and haptic state
stream and writes events (TSV) and haptic state (flat binary) to the
session's behavior/ directory.
"""

from __future__ import annotations

from hapticore.datalog.data_logger_process import DataLoggerProcess

__all__ = ["DataLoggerProcess"]
