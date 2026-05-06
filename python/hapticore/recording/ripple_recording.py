"""RippleRecording — NeuralRecordingInterface shim publishing to RippleProcess over ZMQ.

Methods publish SessionControl messages on TOPIC_SESSION; RippleProcess
subscribes and translates to xipppy.trial() calls. Local state tracks
recording status for is_recording(), since the shim has no synchronous
read path back to xipppy.

Parallel to TeensySync for the sync interface.
"""

from __future__ import annotations

import time
from typing import Any

from hapticore.core.messages import TOPIC_SESSION, SessionControl, serialize
from hapticore.core.messaging import EventPublisher


class RippleRecording:
    """NeuralRecordingInterface shim publishing to RippleProcess over ZMQ.

    Methods publish SessionControl messages on TOPIC_SESSION; RippleProcess
    subscribes and translates to xipppy.trial() calls. Local state tracks
    recording status for is_recording(), since the shim has no synchronous
    read path back to xipppy.

    Parallel to TeensySync for the sync interface.
    """

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher
        self._recording = False
        self._start_time: float | None = None

    def start_recording(self, filename: str) -> None:
        self._publish_session("start_recording", {"file_name_base": filename})
        self._recording = True
        self._start_time = time.monotonic()

    def stop_recording(self) -> None:
        self._publish_session("stop_recording", {})
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording

    def get_timestamp(self) -> float:
        """Elapsed seconds since start_recording().

        Returns monotonic elapsed time, not Ripple processor time. Real
        Ripple 30 kHz timestamps come from the NEV file post-hoc via
        CatGT/TPrime alignment, not from runtime queries through the shim.
        """
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time

    def _publish_session(self, action: str, params: dict[str, Any]) -> None:
        msg = SessionControl(timestamp=time.monotonic(), action=action, params=params)
        self._publisher.publish(TOPIC_SESSION, serialize(msg))
