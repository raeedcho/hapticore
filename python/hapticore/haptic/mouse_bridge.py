"""Mouse-to-haptic-server bridge.

Daemon thread that drains a mouse position queue (fed by DisplayProcess)
and forwards position + finite-differenced velocity to the C++ haptic
server via set_mock_position / set_mock_velocity ZMQ commands.

Mouse input flows through the real C++ haptic server and its force-field
simulation rather than being faked in Python.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing.queues
import threading
import time
from queue import Empty
from typing import Any

import msgpack
import zmq

logger = logging.getLogger(__name__)


class MouseBridge(threading.Thread):
    """Forward mouse positions from a queue to the haptic server.

    Owns a dedicated ZMQ DEALER socket (separate from HapticClient's
    socket — ZMQ sockets are not thread-safe). Sends two commands per
    mouse sample: set_mock_position and set_mock_velocity.

    Args:
        mouse_queue: Queue of ``(x_m, y_m)`` tuples in lab-frame meters,
            populated by DisplayProcess each frame (~60 Hz).
        command_address: ZMQ address of the haptic server's ROUTER socket
            (same address HapticClient connects to).
        context: Optional shared ZMQ context. If None, creates its own.
    """

    def __init__(
        self,
        mouse_queue: multiprocessing.queues.Queue[tuple[float, float]],
        command_address: str,
        context: zmq.Context[Any] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="MouseBridge")
        self._queue = mouse_queue
        self._command_address = command_address
        self._context = context
        self._shutdown = threading.Event()

    def run(self) -> None:
        own_ctx = self._context is None
        ctx: zmq.Context[Any] = self._context if self._context is not None else zmq.Context()
        dealer = ctx.socket(zmq.DEALER)
        dealer.setsockopt(zmq.LINGER, 0)
        # Sends are performed in non-blocking mode; SNDTIMEO is bypassed by
        # NOBLOCK, so pure best-effort drops are the correct behaviour here.
        dealer.connect(self._command_address)

        position = [0.0, 0.0, 0.0]
        prev_time = time.monotonic()
        cmd_seq = 0

        try:
            while not self._shutdown.is_set():
                # Drain queue, keep only the latest reading
                latest: tuple[float, float] | None = None
                try:
                    while True:
                        latest = self._queue.get_nowait()
                except Empty:
                    pass

                if latest is not None:
                    x, y = latest
                    now = time.monotonic()
                    dt = max(now - prev_time, 1e-6)
                    new_pos = [x, y, 0.0]
                    velocity = [
                        (new_pos[i] - position[i]) / dt for i in range(3)
                    ]
                    position = new_pos
                    prev_time = now

                    cmd_seq += 1
                    self._send_command(
                        dealer, f"mouse_pos_{cmd_seq}",
                        "set_mock_position", {"position": new_pos},
                    )
                    self._send_command(
                        dealer, f"mouse_vel_{cmd_seq}",
                        "set_mock_velocity", {"velocity": velocity},
                    )
                    # Drain pending responses to prevent DEALER inbound HWM
                    # from backing up and stalling the server's ROUTER send().
                    while True:
                        try:
                            dealer.recv_multipart(zmq.NOBLOCK)
                        except zmq.Again:
                            break
                else:
                    # No data this iteration; sleep briefly to avoid spinning.
                    # Display pushes at ~60 Hz; 8 ms sleep means we check
                    # ~125 times/s, well above the input rate.
                    self._shutdown.wait(timeout=0.008)
        except Exception:
            logger.exception("MouseBridge crashed")
        finally:
            dealer.close(linger=0)
            if own_ctx:
                ctx.term()

    def request_stop(self) -> None:
        """Signal the bridge to exit its run loop."""
        self._shutdown.set()

    @staticmethod
    def _send_command(
        dealer: zmq.Socket[Any],
        command_id: str,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Fire-and-forget a command to the haptic server.

        Does not wait for a response — the bridge is a high-frequency
        sender (~60 Hz × 2 commands) and blocking on each response
        would halve the throughput. Errors surface through the server's
        heartbeat timeout or through HapticClient command failures, not
        here.
        """
        payload = msgpack.packb({
            "command_id": command_id,
            "method": method,
            "params": params,
        }, use_bin_type=True)
        with contextlib.suppress(zmq.Again):
            dealer.send_multipart([b"", payload], zmq.NOBLOCK)
