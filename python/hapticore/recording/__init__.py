"""Neural recording implementations.

This package will grow to hold ``RippleRecording``, ``XipppyClient``, and
``RippleProcess`` in Phase 5B. Recording does not use the ``backend:``
factory pattern (see ADR-015) because multiple recording systems can be
active simultaneously.
"""

from __future__ import annotations

from hapticore.recording.mock import MockNeuralRecording

__all__ = ["MockNeuralRecording"]
