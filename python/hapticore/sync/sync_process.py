"""SyncProcess — subprocess owning the Teensy serial connection.

Subscribes to three topics:
- TOPIC_SYNC: direct commands from the TeensySync shim (event codes,
  reward pulses, camera rate).
- TOPIC_EVENT: StateTransition messages, for auto-emission of event
  codes per the config's state_codes map.
- TOPIC_SESSION: SessionControl messages, for session-level start/stop
  of sync pulses and camera trigger.

The Teensy runs the 1 Hz sync pulse and camera frame trigger on its own
hardware timers (ADR-013). This process only sends on/off commands.
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

from hapticore.core.config import SyncConfig, ZMQConfig
from hapticore.core.messages import TOPIC_EVENT, TOPIC_SESSION, TOPIC_SYNC

logger = logging.getLogger(__name__)


class SyncProcess(multiprocessing.Process):
    """Subprocess that owns the Teensy USB serial connection.

    Auto-emits event codes on state transitions, forwards explicit
    commands from the ``TeensySync`` shim, and handles session-level
    start/stop of sync pulses and camera trigger via ``SessionControl``.
    """

    # Main poll loop block duration; short enough for responsive shutdown.
    _POLL_TIMEOUT_MS: int = 50

    # Error-log throttle: don't spam if the port has died mid-session.
    _ERROR_LOG_INTERVAL_S: float = 5.0

    def __init__(
        self,
        sync_config: SyncConfig,
        zmq_config: ZMQConfig,
        *,
        serial_module: ModuleType | None = None,
    ) -> None:
        super().__init__(name="SyncProcess", daemon=True)
        if sync_config.transport != "teensy":
            raise ValueError(
                f"SyncProcess requires sync_config.transport='teensy', "
                f"got {sync_config.transport!r}"
            )
        if sync_config.teensy is None:
            raise ValueError(
                "SyncProcess requires sync_config.teensy to be populated. "
                "Set sync.transport='teensy' to auto-populate."
            )
        self._sync_config = sync_config
        self._zmq_config = zmq_config
        self._serial_module = serial_module
        self._shutdown = multiprocessing.Event()

    def request_shutdown(self) -> None:
        """Signal the process to exit and close the serial connection."""
        self._shutdown.set()

    def run(self) -> None:
        """Entry point executed in the child process."""
        from hapticore.sync import protocol
        from hapticore.sync.teensy_serial import TeensySerialClient

        signal.signal(signal.SIGINT, signal.SIG_IGN)

        assert self._sync_config.teensy is not None

        client = TeensySerialClient(
            port=self._sync_config.teensy.port,
            baud=self._sync_config.teensy.baud,
            serial_module=self._serial_module,
        )
        client.open()

        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(self._zmq_config.event_pub_address)
        for topic in (TOPIC_SYNC, TOPIC_EVENT, TOPIC_SESSION):
            sub.subscribe(topic)
        sub.setsockopt(zmq.RCVHWM, 1000)

        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        last_error_log_time = 0.0

        try:
            while not self._shutdown.is_set():
                socks = dict(poller.poll(self._POLL_TIMEOUT_MS))
                if sub not in socks:
                    continue

                topic, payload = sub.recv_multipart(zmq.NOBLOCK)
                msg = msgpack.unpackb(payload, raw=False)

                try:
                    if topic == TOPIC_SYNC:
                        self._handle_sync_command(client, protocol, msg)
                    elif topic == TOPIC_EVENT:
                        self._handle_event(client, protocol, msg)
                    elif topic == TOPIC_SESSION:
                        self._handle_session_control(client, protocol, msg)
                except Exception:
                    now = time.monotonic()
                    if now - last_error_log_time > self._ERROR_LOG_INTERVAL_S:
                        logger.exception(
                            "Error handling topic=%r message=%r", topic, msg,
                        )
                        last_error_log_time = now
        finally:
            # Best-effort stop everything so we leave the Teensy quiet on shutdown.
            try:
                client.write(protocol.format_stop_sync())
                client.write(protocol.format_stop_camera_trigger())
            except Exception:
                logger.exception("Error sending stop commands on shutdown")
            sub.close()
            ctx.term()
            client.close()

    def _handle_sync_command(
        self, client: Any, protocol: Any, msg: dict[str, Any],
    ) -> None:
        """Dispatch an explicit command from the TeensySync shim."""
        action = msg.get("action")
        if action == "send_event_code":
            client.write(protocol.format_event_code(int(msg["code"])))
        elif action == "deliver_reward":
            client.write(protocol.format_reward_ms(int(msg["duration_ms"])))
        elif action == "set_camera_trigger_rate":
            client.write(protocol.format_set_camera_rate(float(msg["rate_hz"])))
        else:
            logger.warning("Unknown TOPIC_SYNC action: %r", action)

    def _handle_event(
        self, client: Any, protocol: Any, msg: dict[str, Any],
    ) -> None:
        """Auto-emit event codes on StateTransition messages per code_map."""
        if msg.get("__msg_type__") != "StateTransition":
            return
        new_state = msg.get("new_state")
        if new_state is None:
            return
        code = self._sync_config.code_map.state_codes.get(new_state)
        if code is None:
            return
        client.write(protocol.format_event_code(code))

    def _handle_session_control(
        self, client: Any, protocol: Any, msg: dict[str, Any],
    ) -> None:
        """Dispatch a SessionControl action."""
        if msg.get("__msg_type__") != "SessionControl":
            return
        action = msg.get("action")
        if action == "start_sync":
            client.write(protocol.format_start_sync())
        elif action == "stop_sync":
            client.write(protocol.format_stop_sync())
        elif action == "start_camera_trigger":
            client.write(protocol.format_start_camera_trigger())
        elif action == "stop_camera_trigger":
            client.write(protocol.format_stop_camera_trigger())
        # start_recording / stop_recording are for the recording process, not us.
