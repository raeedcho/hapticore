"""MockNeuralRecording: in-process NeuralRecordingInterface implementation for testing."""

from __future__ import annotations

import time
from typing import Any


class MockNeuralRecording:
    """Mock neural recording interface."""

    def __init__(self) -> None:
        self._recording = False
        self._filename: str | None = None
        self._start_time: float | None = None
        self._call_log: list[tuple[str, Any]] = []

    def start_recording(self, filename: str) -> None:
        """Start a mock recording session."""
        self._recording = True
        self._filename = filename
        self._start_time = time.monotonic()
        self._call_log.append(("start_recording", filename))

    def stop_recording(self) -> None:
        """Stop the mock recording session."""
        self._recording = False
        self._call_log.append(("stop_recording", None))

    def is_recording(self) -> bool:
        """Return whether a recording is in progress."""
        return self._recording

    def get_timestamp(self) -> float:
        """Return the elapsed time since recording started."""
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time
