"""Heartbeat keeper context manager for interactive hardware tests.

Sends periodic heartbeat commands to the haptic server in a background
thread so that force fields remain active while a human operator
evaluates the feel of the device.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from typing import Generator

import msgpack
import zmq


@contextmanager
def heartbeat_keeper(
    cmd_address: str,
    interval: float = 0.2,
    ctx: zmq.Context | None = None,
) -> Generator[None, None, None]:
    """Keep the haptic server's heartbeat alive in a background thread.

    Sends a heartbeat command every *interval* seconds (default 0.2 s,
    well within the server's 0.5 s timeout).  Use as a context manager
    around any block where force fields should remain active.

    Parameters
    ----------
    cmd_address : str
        ZMQ ROUTER address of the haptic server command socket.
    interval : float
        Seconds between heartbeat sends.  Must be < 0.5.
    ctx : zmq.Context, optional
        Shared ZMQ context.  A new one is created if not provided.
    """
    if interval >= 0.5:
        raise ValueError("Heartbeat interval must be less than server timeout (0.5 s)")

    own_ctx = ctx is None
    if own_ctx:
        ctx = zmq.Context()

    stop_event = threading.Event()

    def _heartbeat_loop() -> None:
        dealer = ctx.socket(zmq.DEALER)  # type: ignore[union-attr]
        dealer.connect(cmd_address)
        try:
            while not stop_event.is_set():
                cmd = msgpack.packb(
                    {
                        "command_id": uuid.uuid4().hex[:12],
                        "method": "heartbeat",
                        "params": {},
                    },
                    use_bin_type=True,
                )
                dealer.send_multipart([b"", cmd])
                # Drain the response (don't block indefinitely)
                if dealer.poll(timeout=100):
                    dealer.recv_multipart()
                stop_event.wait(interval)
        finally:
            dealer.close()

    thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=2.0)
        if own_ctx:
            ctx.term()
