"""Fixtures for hardware tests that connect to a running haptic server.

These tests assume the C++ haptic_server is already running with a real
delta.3 connected. Start it before running:

    ./haptic_server --pub-address tcp://*:5555 --cmd-address tcp://*:5556

Or with default IPC addresses:

    ./haptic_server

Override addresses via environment variables:
    HAPTICORE_PUB_ADDRESS=tcp://rigmachine:5555
    HAPTICORE_CMD_ADDRESS=tcp://rigmachine:5556
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Generator

import msgpack
import pytest
import zmq

from hapticore.core.messages import HapticState


# ---------------------------------------------------------------------------
# Address configuration
# ---------------------------------------------------------------------------

DEFAULT_PUB = "ipc:///tmp/hapticore_haptic_state"
DEFAULT_CMD = "ipc:///tmp/hapticore_haptic_cmd"


def _pub_address() -> str:
    return os.environ.get("HAPTICORE_PUB_ADDRESS", DEFAULT_PUB)


def _cmd_address() -> str:
    return os.environ.get("HAPTICORE_CMD_ADDRESS", DEFAULT_CMD)


# ---------------------------------------------------------------------------
# Session-scoped fixtures (shared across all hardware & interactive tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pub_address() -> str:
    """PUB address of the haptic server (session-scoped)."""
    return _pub_address()


@pytest.fixture(scope="session")
def cmd_address() -> str:
    """CMD address of the haptic server (session-scoped)."""
    return _cmd_address()


@pytest.fixture(scope="session")
def zmq_context() -> Generator[zmq.Context[zmq.Socket[bytes]], None, None]:
    """Session-scoped ZMQ context for interactive tests."""
    ctx: zmq.Context[zmq.Socket[bytes]] = zmq.Context()
    yield ctx
    ctx.term()


# ---------------------------------------------------------------------------
# Module-scoped fixtures (for automated hardware tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def zmq_ctx() -> Generator[zmq.Context[zmq.Socket[bytes]], None, None]:
    """Shared ZMQ context for the test module."""
    ctx: zmq.Context[zmq.Socket[bytes]] = zmq.Context()
    yield ctx
    ctx.term()


@pytest.fixture(scope="module")
def state_sub(zmq_ctx: zmq.Context[zmq.Socket[bytes]]) -> Generator[zmq.Socket[bytes], None, None]:
    """SUB socket connected to the haptic server's state PUB.

    Module-scoped so the slow-joiner delay is paid once.
    """
    sub = zmq_ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"state")
    sub.setsockopt(zmq.RCVTIMEO, 3000)  # 3 second timeout
    sub.connect(_pub_address())
    # Allow time for ZMQ slow-joiner
    time.sleep(0.3)
    yield sub
    sub.close()


@pytest.fixture(scope="module")
def cmd_dealer(zmq_ctx: zmq.Context[zmq.Socket[bytes]]) -> Generator[zmq.Socket[bytes], None, None]:
    """DEALER socket connected to the haptic server's command ROUTER."""
    dealer = zmq_ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 3000)
    dealer.connect(_cmd_address())
    time.sleep(0.1)
    yield dealer
    dealer.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_command(
    dealer: zmq.Socket[bytes],
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a command to the haptic server and return the response dict.

    Raises TimeoutError if no response within the socket's RCVTIMEO.
    """
    cmd_id = uuid.uuid4().hex[:12]
    payload = msgpack.packb(
        {"command_id": cmd_id, "method": method, "params": params or {}},
        use_bin_type=True,
    )
    dealer.send_multipart([b"", payload])

    try:
        frames = dealer.recv_multipart()
    except zmq.Again as exc:
        raise TimeoutError(f"No response to '{method}' (id={cmd_id})") from exc

    # DEALER receives [empty_frame, payload]
    resp: dict[str, Any] = msgpack.unpackb(frames[-1], raw=False)
    assert resp["command_id"] == cmd_id, "Response command_id mismatch"
    return resp


def receive_state(sub: zmq.Socket[bytes]) -> HapticState:
    """Receive one state message and return as HapticState."""
    try:
        topic, data = sub.recv_multipart()
    except zmq.Again as exc:
        raise TimeoutError(
            "No state message received — is the haptic server running?"
        ) from exc

    assert topic == b"state"
    unpacked: dict[str, Any] = msgpack.unpackb(data, raw=False)
    return HapticState(**unpacked)


def receive_n_states(
    sub: zmq.Socket[bytes], n: int
) -> list[HapticState]:
    """Receive n state messages."""
    return [receive_state(sub) for _ in range(n)]


def drain_and_receive_state(
    sub: zmq.Socket[bytes],
    settle_time: float = 0.05,
) -> HapticState:
    """Drain stale messages from the ZMQ SUB queue, then read fresh state.

    The module-scoped SUB socket accumulates messages at ~200 Hz.  After a
    command changes the active field, older messages published before the
    change may still be queued.  This helper discards them, waits briefly
    for the new state to propagate, and returns the first fresh message.
    """
    time.sleep(settle_time)
    # Drain all buffered messages
    while True:
        try:
            sub.recv_multipart(flags=zmq.NOBLOCK)
        except zmq.Again:
            break
    # Now read the next fresh message
    return receive_state(sub)
