"""RippleProcess — subprocess owning the xipppy connection to the Ripple Grapevine Scout.

Subscribes to TOPIC_SESSION for SessionControl messages:
- start_recording: calls XipppyClient.start_recording() with
  params["file_name_base"] from the message.
- stop_recording: calls XipppyClient.stop_recording().

The Teensy handles all sync and event code emission (ADR-013).
This process only controls Trellis recording state.

# TODO(5A.6): digin() verification for rig-bringup diagnostics
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

from hapticore.core.config import RippleRecordingConfig, ZMQConfig
from hapticore.core.messages import TOPIC_SESSION
from hapticore.recording.xipppy_client import XipppyClient

logger = logging.getLogger(__name__)


class RippleProcess(multiprocessing.Process):
    """Subprocess owning the xipppy connection to Ripple Grapevine Scout.

    Subscribes to TOPIC_SESSION for SessionControl messages:
    - start_recording: calls XipppyClient.start_recording() with
      params["file_name_base"] from the message.
    - stop_recording: calls XipppyClient.stop_recording().

    The Teensy handles all sync and event code emission (ADR-013).
    This process only controls Trellis recording state.
    """

    # Main poll loop block duration; short enough for responsive shutdown.
    _POLL_TIMEOUT_MS: int = 50

    # Error-log throttle: don't spam if the connection has died mid-session.
    _ERROR_LOG_INTERVAL_S: float = 5.0

    def __init__(
        self,
        recording_config: RippleRecordingConfig,
        zmq_config: ZMQConfig,
        *,
        xipppy_module: ModuleType | None = None,
    ) -> None:
        super().__init__(name="RippleProcess", daemon=True)
        if recording_config is None:
            raise ValueError("recording_config must be a RippleRecordingConfig instance, got None")
        self._config = recording_config
        self._zmq_config = zmq_config
        self._xipppy_module = xipppy_module
        self._shutdown = multiprocessing.Event()

    def request_shutdown(self) -> None:
        """Signal the process to exit and close the xipppy connection."""
        self._shutdown.set()

    def run(self) -> None:
        """Entry point in child process."""
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        client = XipppyClient(
            use_tcp=self._config.use_tcp,
            operator_id=self._config.operator_id,
            xipppy_module=self._xipppy_module,
        )

        recording_active = False
        last_error_log_time = 0.0

        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)

        with client:
            sub.connect(self._zmq_config.event_pub_address)
            sub.subscribe(TOPIC_SESSION)
            sub.setsockopt(zmq.RCVHWM, 1000)

            poller = zmq.Poller()
            poller.register(sub, zmq.POLLIN)

            try:
                while not self._shutdown.is_set():
                    socks = dict(poller.poll(self._POLL_TIMEOUT_MS))
                    if sub not in socks:
                        continue

                    topic, payload = sub.recv_multipart(zmq.NOBLOCK)
                    msg = msgpack.unpackb(payload, raw=False)

                    try:
                        if topic == TOPIC_SESSION:
                            action = msg.get("action")
                            self._handle_session_control(client, msg)
                            if action == "start_recording":
                                recording_active = True
                            elif action == "stop_recording":
                                recording_active = False
                    except Exception:
                        now = time.monotonic()
                        if now - last_error_log_time > self._ERROR_LOG_INTERVAL_S:
                            logger.exception(
                                "Error handling topic=%r message=%r", topic, msg,
                            )
                            last_error_log_time = now
            finally:
                if recording_active:
                    try:
                        client.stop_recording()
                    except Exception:
                        logger.exception("Error stopping recording on shutdown")
                sub.close()
                ctx.term()

    def _handle_session_control(
        self, client: XipppyClient, msg: dict[str, Any],
    ) -> None:
        """Dispatch a SessionControl action."""
        if msg.get("__msg_type__") != "SessionControl":
            return
        action = msg.get("action")
        if action == "start_recording":
            params = msg.get("params", {})
            file_name_base = str(params.get("file_name_base", ""))
            client.start_recording(file_name_base, self._config.auto_stop_time_s)
        elif action == "stop_recording":
            client.stop_recording()
        # start_sync, stop_sync, start_camera_trigger, stop_camera_trigger
        # are for SyncProcess, not us.
