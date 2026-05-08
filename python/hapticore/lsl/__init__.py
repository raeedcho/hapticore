"""LSL marker outlet for real-time event streaming.

``LSLMarkerProcess`` subscribes to ``TOPIC_EVENT`` and pushes
``StateTransition`` and ``TrialEvent`` messages as string markers
to an LSL outlet. pylsl is an optional dependency — see
``docs/rig-setup.md`` for installation.
"""

from __future__ import annotations

from hapticore.lsl.lsl_process import LSLMarkerProcess

__all__ = ["LSLMarkerProcess"]
