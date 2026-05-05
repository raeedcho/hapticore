"""Neural recording implementations.

Recording does not use the ``backend:`` factory pattern (see ADR-015)
because multiple recording systems can be active simultaneously.
``RippleProcess`` and ``XipppyClient`` are internal — import them
directly where needed.
"""

from __future__ import annotations

from hapticore.recording.mock import MockNeuralRecording
from hapticore.recording.ripple_recording import RippleRecording

__all__ = ["MockNeuralRecording", "RippleRecording"]
