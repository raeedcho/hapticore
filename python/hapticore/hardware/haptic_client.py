"""HapticInterface implementation that connects to a running haptic server.

See docs/haptic_server_protocol.md for the wire format. This client is the
production Python consumer of the haptic server protocol alongside
MockHapticInterface.

Notes
-----
ZMQ PUB-SUB has a slow-joiner problem: the subscriber may miss messages sent
before the subscription is fully established. After ``connect()``, the state
drain thread may not see a message for 100–300 ms even when the server is
running. ``get_latest_state()`` returns ``None`` until the first message
arrives; callers should handle this gracefully.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Self

import msgpack
import zmq

from hapticore.core.messages import (
    TOPIC_STATE,
    Command,
    CommandResponse,
    HapticState,
)

logger = logging.getLogger(__name__)


class HapticClient:
    """`HapticInterface` implementation that connects to a running haptic server.

    Owns a ZMQ SUB socket drained by a background thread (so that
    ``get_latest_state()`` is a cheap lock-protected read, never a blocking
    recv), two DEALER sockets (one for user commands, one for the heartbeat
    thread), and a second background thread that sends heartbeats at
    ``heartbeat_interval_s`` so the server does not revert to NullField +
    damping.

    Keeping heartbeat and user command sockets separate ensures heartbeats
    are never delayed by slow user commands, which would otherwise trip the
    server's 500 ms watchdog and silently revert the active force field.

    Construct, then call ``connect()`` (or use as a context manager) before
    any state/command access. ``close()`` stops the threads and releases
    sockets.
    """

    def __init__(
        self,
        state_address: str,
        command_address: str,
        *,
        heartbeat_interval_s: float = 0.2,
        command_timeout_ms: int = 1000,
        context: zmq.Context[Any] | None = None,
    ) -> None:
        if heartbeat_interval_s <= 0 or heartbeat_interval_s >= 0.5:
            raise ValueError(
                "heartbeat_interval_s must be in (0, 0.5); server watchdog is 500 ms"
            )
        self._state_address = state_address
        self._command_address = command_address
        self._heartbeat_interval_s = heartbeat_interval_s
        self._command_timeout_ms = command_timeout_ms
        self._own_context = context is None
        self._context: zmq.Context[Any] = context if context is not None else zmq.Context()
        self._state_sock: zmq.Socket[Any] | None = None
        # Two separate DEALER sockets: one for user commands, one for heartbeats.
        # Keeping them separate avoids lock contention that could starve the heartbeat
        # thread and trip the server's 500 ms watchdog during slow user commands.
        self._user_cmd_sock: zmq.Socket[Any] | None = None
        self._heartbeat_sock: zmq.Socket[Any] | None = None
        self._state_lock = threading.Lock()
        self._latest_state: HapticState | None = None
        self._callback: Callable[[HapticState], None] | None = None
        self._shutdown = threading.Event()
        self._state_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._connected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open sockets and start background threads.

        Raises
        ------
        RuntimeError
            If ``connect()`` has already been called on this instance.
        """
        if self._connected:
            raise RuntimeError("Already connected")

        # SUB socket: drain by background thread only
        state_sock: zmq.Socket[Any] = self._context.socket(zmq.SUB)
        state_sock.setsockopt(zmq.LINGER, 0)
        state_sock.setsockopt(zmq.RCVTIMEO, 50)
        state_sock.setsockopt(zmq.SUBSCRIBE, TOPIC_STATE)
        state_sock.connect(self._state_address)
        self._state_sock = state_sock

        # User command socket: used exclusively by send_command() (caller thread).
        user_cmd_sock: zmq.Socket[Any] = self._context.socket(zmq.DEALER)
        user_cmd_sock.setsockopt(zmq.LINGER, 0)
        user_cmd_sock.connect(self._command_address)
        self._user_cmd_sock = user_cmd_sock

        # Heartbeat socket: used exclusively by the heartbeat background thread.
        # Separate from the user socket so slow/timed-out user commands never
        # delay heartbeats and trip the server's 500 ms watchdog.
        heartbeat_sock: zmq.Socket[Any] = self._context.socket(zmq.DEALER)
        heartbeat_sock.setsockopt(zmq.LINGER, 0)
        heartbeat_sock.connect(self._command_address)
        self._heartbeat_sock = heartbeat_sock

        self._shutdown.clear()
        self._connected = True

        self._state_thread = threading.Thread(
            target=self._state_drain_loop, daemon=True, name="HapticClient-state"
        )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="HapticClient-heartbeat"
        )
        self._state_thread.start()
        self._heartbeat_thread.start()

    def close(self) -> None:
        """Stop background threads and release sockets.

        Idempotent — safe to call on a never-connected or already-closed
        instance (makes ``finally`` blocks simple).
        """
        if not self._connected:
            return

        self._shutdown.set()
        self._connected = False

        if self._state_thread is not None:
            self._state_thread.join(timeout=2.0)
            self._state_thread = None

        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)
            self._heartbeat_thread = None

        if self._state_sock is not None:
            self._state_sock.close(linger=0)
            self._state_sock = None

        if self._user_cmd_sock is not None:
            self._user_cmd_sock.close(linger=0)
            self._user_cmd_sock = None

        if self._heartbeat_sock is not None:
            self._heartbeat_sock.close(linger=0)
            self._heartbeat_sock = None

        if self._own_context:
            self._context.term()

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # HapticInterface Protocol methods
    # ------------------------------------------------------------------

    def get_latest_state(self) -> HapticState | None:
        """Return the most recently received state, or ``None`` if not yet received.

        Thread-safe. Returns ``None`` before the first PUB message arrives
        (slow-joiner window of ~100–300 ms after ``connect()``).

        Raises
        ------
        RuntimeError
            If called before ``connect()``.
        """
        self._require_connected()
        with self._state_lock:
            return self._latest_state

    def send_command(self, cmd: Command) -> CommandResponse:
        """Send a command and return the server's response.

        Never raises on protocol-level errors (timeout, malformed response,
        socket errors). On timeout, returns ``CommandResponse(success=False,
        error="... timed out ...")``.

        Before sending, any stale responses queued on the socket from prior
        timeouts are drained so they cannot be mistaken for this command's
        reply. The recv loop then validates ``command_id`` on each frame,
        discarding unmatched ones, until the correct reply arrives or the
        cumulative timeout expires.

        Raises
        ------
        RuntimeError
            If called before ``connect()``.
        """
        self._require_connected()

        command_id = cmd.command_id if cmd.command_id else uuid.uuid4().hex
        payload = msgpack.packb(
            {"command_id": command_id, "method": cmd.method, "params": cmd.params},
            use_bin_type=True,
        )

        assert self._user_cmd_sock is not None
        sock = self._user_cmd_sock

        # Drain any stale responses queued from prior timed-out commands.
        while True:
            try:
                sock.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            except zmq.ZMQError as exc:
                return CommandResponse(
                    command_id=command_id, success=False, result={},
                    error=f"Socket error draining before '{cmd.method}': {exc}",
                )

        try:
            sock.send_multipart([b"", payload])
        except zmq.ZMQError as exc:
            return CommandResponse(
                command_id=command_id, success=False, result={},
                error=f"Socket error sending '{cmd.method}': {exc}",
            )

        deadline = time.monotonic() + self._command_timeout_ms / 1000.0
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                return CommandResponse(
                    command_id=command_id, success=False, result={},
                    error=f"Command '{cmd.method}' timed out after {self._command_timeout_ms}ms",
                )

            try:
                if not sock.poll(remaining_ms, zmq.POLLIN):
                    return CommandResponse(
                        command_id=command_id,
                        success=False,
                        result={},
                        error=(
                            f"Command '{cmd.method}' timed out after"
                            f" {self._command_timeout_ms}ms"
                        ),
                    )
                frames: list[bytes] = sock.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                continue
            except zmq.ZMQError as exc:
                return CommandResponse(
                    command_id=command_id, success=False, result={},
                    error=f"Socket error receiving '{cmd.method}': {exc}",
                )

            if len(frames) < 2:
                logger.warning(
                    "Malformed response to '%s': expected ≥2 frames, got %d",
                    cmd.method, len(frames),
                )
                continue

            try:
                unpacked: dict[str, Any] = msgpack.unpackb(frames[1], raw=False)
                unpacked.pop("__msg_type__", None)
            except Exception as exc:
                logger.warning("Malformed response to '%s': %s", cmd.method, exc)
                continue

            if unpacked.get("command_id") != command_id:
                logger.debug(
                    "Dropping stale response (got command_id=%r, expected %r)",
                    unpacked.get("command_id"), command_id,
                )
                continue

            try:
                return CommandResponse(**unpacked)
            except Exception as exc:
                logger.warning("Malformed response to '%s': %s", cmd.method, exc)
                return CommandResponse(
                    command_id=command_id, success=False, result={},
                    error=f"Malformed server response: {exc}",
                )

    def subscribe_state(self, callback: Callable[[HapticState], None]) -> None:
        """Register a callback invoked on each new state message.

        The callback is called from the state drain thread. It must be
        non-blocking; long-running callbacks will delay state processing.
        """
        with self._state_lock:
            self._callback = callback

    def unsubscribe_state(self) -> None:
        """Remove the previously registered state callback."""
        with self._state_lock:
            self._callback = None

    # ------------------------------------------------------------------
    # Background thread loops
    # ------------------------------------------------------------------

    def _state_drain_loop(self) -> None:
        """Drain the SUB socket, updating ``_latest_state`` on each message."""
        assert self._state_sock is not None
        while not self._shutdown.is_set():
            try:
                parts: list[bytes] = self._state_sock.recv_multipart()
            except zmq.Again:
                # RCVTIMEO expired — loop and check shutdown
                continue
            except zmq.ZMQError:
                # Socket closed during shutdown
                break

            if len(parts) < 2:
                continue

            try:
                unpacked: dict[str, Any] = msgpack.unpackb(parts[1], raw=False)
                unpacked.pop("__msg_type__", None)
                state = HapticState(**unpacked)
            except Exception as exc:
                logger.warning("Failed to deserialize state message: %s", exc)
                continue

            with self._state_lock:
                self._latest_state = state
                cb = self._callback

            if cb is not None:
                try:
                    cb(state)
                except Exception as exc:
                    logger.warning("State callback raised: %s", exc)

    def _heartbeat_loop(self) -> None:
        """Send heartbeat commands at ``_heartbeat_interval_s`` until shutdown.

        Uses the dedicated ``_heartbeat_sock`` so it never contends with
        user calls to ``send_command()``.
        """
        assert self._heartbeat_sock is not None
        sock = self._heartbeat_sock
        _last_warn_time: float = 0.0
        while not self._shutdown.wait(self._heartbeat_interval_s):
            cmd_id = uuid.uuid4().hex[:12]
            payload = msgpack.packb(
                {"command_id": cmd_id, "method": "heartbeat", "params": {}},
                use_bin_type=True,
            )
            try:
                sock.send_multipart([b"", payload])
                if sock.poll(self._command_timeout_ms, zmq.POLLIN):
                    sock.recv_multipart(flags=zmq.NOBLOCK)
                else:
                    now = time.monotonic()
                    if now - _last_warn_time >= 1.0:
                        logger.warning(
                            "Heartbeat timed out — server may have reverted to NullField"
                        )
                        _last_warn_time = now
            except zmq.ZMQError:
                # Socket closed during shutdown — exit quietly
                break

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("HapticClient is not connected")
