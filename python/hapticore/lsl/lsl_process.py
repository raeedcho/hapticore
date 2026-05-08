"""LSLMarkerProcess — subprocess pushing behavioral events to an LSL outlet.

Subscribes to TOPIC_EVENT for StateTransition and TrialEvent messages,
formats each as a string marker, and pushes to a pylsl StreamOutlet.

pylsl is an optional dependency imported lazily in run(). CI tests inject
a fake module via the pylsl_module parameter.
"""

from __future__ import annotations

import logging
import multiprocessing
import signal
import time
from types import ModuleType
from typing import Any

import msgpack
import zmq

from hapticore.core.config import ZMQConfig
from hapticore.core.messages import TOPIC_EVENT

logger = logging.getLogger(__name__)


class LSLMarkerProcess(multiprocessing.Process):
    """Subprocess pushing behavioral events to an LSL marker outlet.

    Subscribes to TOPIC_EVENT for StateTransition and TrialEvent
    messages, formats each as a string marker, and pushes to a pylsl
    StreamOutlet. The outlet is discoverable by LabRecorder and other
    LSL consumers using the configured stream name.

    LSL markers flow for the entire session lifetime (start to stop),
    independent of the recording lifecycle. This means markers are
    available during test trials before formal recording begins.

    pylsl is imported lazily in run() — CI tests inject a fake module
    via the pylsl_module parameter.
    """

    _POLL_TIMEOUT_MS: int = 50
    _ERROR_LOG_INTERVAL_S: float = 5.0

    def __init__(
        self,
        stream_name: str,
        source_id: str,
        zmq_config: ZMQConfig,
        *,
        pylsl_module: ModuleType | None = None,
        ready_event: multiprocessing.Event | None = None,  # type: ignore[type-arg]
    ) -> None:
        super().__init__(name="LSLMarkerProcess", daemon=True)
        self._stream_name = stream_name
        self._source_id = source_id
        self._zmq_config = zmq_config
        self._pylsl_module = pylsl_module
        self._ready_event = ready_event
        self._shutdown = multiprocessing.Event()

    def request_shutdown(self) -> None:
        """Signal the process to exit cleanly."""
        self._shutdown.set()

    def run(self) -> None:
        """Entry point in child process."""
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        # Import pylsl (or use injected fake for testing)
        if self._pylsl_module is not None:
            pylsl = self._pylsl_module
        else:
            try:
                import pylsl  # noqa: PLC0415
            except ImportError as exc:
                raise ImportError(
                    "pylsl is required for LSL marker streaming but is not "
                    "installed. Install with: pip install pylsl "
                    "(see docs/rig-setup.md § LSL)."
                ) from exc

        # Create LSL outlet
        info = pylsl.StreamInfo(
            name=self._stream_name,
            type="Markers",
            channel_count=1,
            nominal_srate=0,  # irregular rate
            channel_format=pylsl.cf_string,
            source_id=self._source_id,
        )
        outlet = pylsl.StreamOutlet(info)

        # ZMQ subscriber
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(self._zmq_config.event_pub_address)
        sub.subscribe(TOPIC_EVENT)
        sub.setsockopt(zmq.RCVHWM, 1000)

        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        if self._ready_event is not None:
            self._ready_event.set()

        last_error_log_time = 0.0
        try:
            while not self._shutdown.is_set():
                socks = dict(poller.poll(self._POLL_TIMEOUT_MS))
                if sub not in socks:
                    continue

                topic, payload = sub.recv_multipart(zmq.NOBLOCK)
                msg = msgpack.unpackb(payload, raw=False)

                try:
                    marker = self._format_marker(msg)
                    if marker is not None:
                        outlet.push_sample([marker])
                except Exception:
                    now = time.monotonic()
                    if now - last_error_log_time > self._ERROR_LOG_INTERVAL_S:
                        logger.exception(
                            "Error pushing LSL marker for message=%r", msg,
                        )
                        last_error_log_time = now
        finally:
            sub.close()
            ctx.term()

    @staticmethod
    def _format_marker(msg: dict[str, Any]) -> str | None:
        """Format a message dict as an LSL marker string.

        Returns None for unrecognized message types (silently skipped).

        Format:
        - StateTransition: "state:<new_state>:<event_code>:<trial_number>"
        - TrialEvent:      "event:<event_name>:<event_code>:<trial_number>"
        """
        msg_type = msg.get("__msg_type__")
        if msg_type == "StateTransition":
            return (
                f"state:{msg['new_state']}"
                f":{msg['event_code']}"
                f":{msg['trial_number']}"
            )
        if msg_type == "TrialEvent":
            return (
                f"event:{msg['event_name']}"
                f":{msg['event_code']}"
                f":{msg['trial_number']}"
            )
        return None
