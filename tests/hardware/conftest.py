"""Fixtures for hardware tests that connect to a running haptic server.

These tests assume the C++ haptic_server is already running with a real
delta.3 connected. Start it before running:

    ./haptic_server --pub-address tcp://*:5555 --cmd-address tcp://*:5556

Or with default IPC addresses:

    ./haptic_server

Override addresses via environment variables:
    HAPTICORE_PUB_ADDRESS=tcp://rigmachine:5555
    HAPTICORE_CMD_ADDRESS=tcp://rigmachine:5556

The Teensy bench fixtures (``teensy``, ``ripple``, ``bench``) do not use the
haptic server. They need the Teensy wired to the Ripple Scout, Trellis running,
and xipppy installed; see tests/hardware/test_teensy_firmware.py.
"""

from __future__ import annotations

import os
import time
import uuid
from itertools import pairwise
from typing import Any, Generator

import msgpack
import pytest
import zmq

from hapticore.core.config import TeensyConfig
from hapticore.core.messages import HapticState
from hapticore.recording.xipppy_client import XipppyClient
from hapticore.sync import protocol
from hapticore.sync.teensy_serial import TeensySerialClient

from .ripple_digin import DiginRecorder, sma_edges


# ---------------------------------------------------------------------------
# pytest command-line options
# ---------------------------------------------------------------------------

def pytest_addoption(parser: pytest.Parser) -> None:
    """Add command-line options for interactive feel-test timing and the Teensy bench."""
    parser.addoption(
        "--countdown",
        default=5,
        type=int,
        help="Seconds to count down before activating a field (default: 5)",
    )
    parser.addoption(
        "--duration",
        default=10,
        type=int,
        help="Seconds to keep the field active for evaluation (default: 10)",
    )
    parser.addoption(
        "--teensy-port",
        default=TeensyConfig().port,
        help="Serial port of the Teensy sync hub (default: %(default)s)",
    )


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
    """Session-scoped ZMQ context shared by all hardware and interactive tests."""
    ctx: zmq.Context[zmq.Socket[bytes]] = zmq.Context()
    yield ctx
    ctx.term()


# ---------------------------------------------------------------------------
# Module-scoped fixtures (for automated hardware tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def state_sub(
    pub_address: str,
    zmq_context: zmq.Context[zmq.Socket[bytes]],
) -> Generator[zmq.Socket[bytes], None, None]:
    """SUB socket connected to the haptic server's state PUB.

    Module-scoped so the slow-joiner delay is paid once.
    """
    sub = zmq_context.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"state")
    sub.setsockopt(zmq.RCVTIMEO, 3000)  # 3 second timeout
    sub.connect(pub_address)
    # Allow time for ZMQ slow-joiner
    time.sleep(0.3)
    yield sub
    sub.close()


@pytest.fixture(scope="module")
def cmd_dealer(
    cmd_address: str,
    zmq_context: zmq.Context[zmq.Socket[bytes]],
) -> Generator[zmq.Socket[bytes], None, None]:
    """DEALER socket connected to the haptic server's command ROUTER."""
    dealer = zmq_context.socket(zmq.DEALER)
    dealer.setsockopt(zmq.RCVTIMEO, 3000)
    dealer.connect(cmd_address)
    time.sleep(0.1)
    yield dealer
    dealer.close()


# ---------------------------------------------------------------------------
# Teensy bench fixtures (the Ripple Scout measures the Teensy's outputs)
# ---------------------------------------------------------------------------

# docs/teensy-bringup.md (Step 1): the Teensy resets when its serial port is opened.
_TEENSY_BOOT_S = 2.0

# Bench check: the sync output toggles every 0.5 s. The tolerance covers the Scout's 0.1 ms
# sampling and timer jitter, and still catches wrong timestamp units.
_BENCH_SYNC_RUN_S = 2.5
_SYNC_HALF_PERIOD_S = 0.5
_SYNC_HALF_PERIOD_TOL_S = 1e-3


def _stop_teensy_outputs(client: TeensySerialClient) -> None:
    """Stop the outputs that keep running on their own: sync and camera trigger."""
    client.write(protocol.format_stop_sync())
    client.write(protocol.format_stop_camera_trigger())


@pytest.fixture(scope="module")
def teensy(request: pytest.FixtureRequest) -> Generator[TeensySerialClient, None, None]:
    """Open serial link to the Teensy, with its outputs off at the start and end of a module.

    Module-scoped so the port is free between modules. Build commands with
    ``hapticore.sync.protocol.format_*``, never as hand-typed strings. Does not
    skip when the Teensy is missing: a bench run should fail loudly.
    """
    client = TeensySerialClient(
        port=request.config.getoption("--teensy-port"),
        baud=TeensyConfig().baud,
    )
    client.open()
    try:
        time.sleep(_TEENSY_BOOT_S)
        _stop_teensy_outputs(client)
        yield client
        _stop_teensy_outputs(client)
    finally:
        client.close()


@pytest.fixture(scope="session")
def ripple() -> Generator[DiginRecorder, None, None]:
    """Recorder of the Ripple Scout's digital-input events (Trellis must be running).

    Does not skip when xipppy is missing: ``XipppyClient.connect()`` raises an
    ImportError that says how to install it, and a bench run should fail loudly.
    """
    client = XipppyClient(use_tcp=True)
    client.connect()
    try:
        import xipppy  # noqa: PLC0415 — lazy by design

        yield DiginRecorder(digin=xipppy.digin)
    finally:
        client.disconnect()


@pytest.fixture(scope="module")
def bench(teensy: TeensySerialClient, ripple: DiginRecorder) -> None:
    """Fail-fast bench check: the Scout must see the Teensy's sync output.

    Runs the sync output for 2.5 s and checks the SMA1 edges the Scout reports.
    If the bench is not wired or configured as the suite assumes, every test in
    the module errors at once with the likely cause, instead of each failing in
    its own confusing way.

    The run is 2.5 s because ``start_sync()`` keeps the previous toggle level, so
    after some stops the first toggle makes no edge. The edges are read before
    ``S0`` is sent, because stopping drives a high pin low, which can add an
    edge that is not a half period after the last toggle.
    """
    ripple.clear()
    teensy.write(protocol.format_start_sync())
    try:
        ripple.wait(_BENCH_SYNC_RUN_S)
        edges = sma_edges(ripple.events(), 1)
    finally:
        teensy.write(protocol.format_stop_sync())

    if not edges:
        pytest.fail(
            f"Bench check failed: no SMA1 events arrived in {_BENCH_SYNC_RUN_S} s of sync "
            "output. Check the wiring: Teensy pin 2 → Scout DIO BNC Input 1, with a shared "
            "ground (docs/teensy-bringup.md, Step 2). Also check that Trellis is running and "
            "that --teensy-port is the sync hub.",
            pytrace=False,
        )
    if {edge.level for edge in edges} != {0, 1}:
        polarity = "rising" if edges[0].level else "falling"
        pytest.fail(
            f"Bench check failed: only {polarity} SMA1 edges arrived ({len(edges)} in "
            f"{_BENCH_SYNC_RUN_S} s). The suite needs both edges to measure pulses, so Ripple "
            "must report the rise and the fall of an SMA input.",
            pytrace=False,
        )
    intervals = [later.t_s - earlier.t_s for earlier, later in pairwise(edges)]
    if any(abs(dt - _SYNC_HALF_PERIOD_S) > _SYNC_HALF_PERIOD_TOL_S for dt in intervals):
        listed = ", ".join(f"{dt:.4f}" for dt in intervals)
        pytest.fail(
            "Bench check failed: consecutive SMA1 edges are not 0.5 s ± 1 ms apart "
            f"(intervals in s: {listed}). The sync output toggles every 0.5 s, so this usually "
            "means the digin() timestamp units are not the assumed 30 kHz ticks. A single odd "
            "interval is more likely a stray edge, such as one left over from an earlier run.",
            pytrace=False,
        )


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
