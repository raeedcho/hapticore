"""MockAudio: in-process AudioInterface implementation for testing."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MockAudio:
    """Mock audio interface for testing.

    Logs every ``play_cue()`` call to ``_play_log`` for test assertions.
    Warns on unknown cue names (same contract as the real backend).
    """

    def __init__(self, known_cues: set[str] | None = None) -> None:
        self._known_cues = known_cues
        self._play_log: list[str] = []

    def play_cue(self, name: str) -> None:
        """Record the cue name. Warn if not in known_cues (when provided)."""
        if self._known_cues is not None and name not in self._known_cues:
            logger.warning("Unknown audio cue %r (mock)", name)
        self._play_log.append(name)
