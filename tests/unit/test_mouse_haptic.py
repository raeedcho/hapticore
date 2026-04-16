"""Tests for MouseHapticInterface — mouse-driven haptic mock."""

from __future__ import annotations

import multiprocessing
import multiprocessing.queues
import time
from typing import TYPE_CHECKING

from hapticore.core.interfaces import HapticInterface
from hapticore.core.messages import Command
from hapticore.hardware.mouse_haptic import MouseHapticInterface

if TYPE_CHECKING:
    _MouseQueue = multiprocessing.queues.Queue[tuple[float, float]]

# multiprocessing.Queue uses a feeder thread; put() returns before data is
# available to get_nowait().  A short sleep lets the feeder flush.
_QUEUE_SETTLE: float = 0.05


def test_satisfies_protocol() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)
    assert isinstance(iface, HapticInterface)


def test_position_updates_from_queue() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)

    q.put((0.05, -0.03))
    time.sleep(_QUEUE_SETTLE)
    state = iface.get_latest_state()
    assert state is not None

    assert abs(state.position[0] - 0.05) < 1e-9
    assert abs(state.position[1] - (-0.03)) < 1e-9
    assert state.position[2] == 0.0


def test_stale_queue_holds_last_position() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)

    q.put((0.02, 0.01))
    time.sleep(_QUEUE_SETTLE)
    iface.get_latest_state()

    # No new item — position should be unchanged, velocity should be zero
    state = iface.get_latest_state()
    assert state is not None
    assert abs(state.position[0] - 0.02) < 1e-9
    assert state.velocity == [0.0, 0.0, 0.0]


def test_sequence_increments() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)

    s1 = iface.get_latest_state()
    s2 = iface.get_latest_state()
    assert s1 is not None and s2 is not None
    assert s2.sequence == s1.sequence + 1


def test_send_command_returns_success() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)
    cmd = Command(command_id="test-1", method="set_field", params={"field": "null"})
    resp = iface.send_command(cmd)
    assert resp.success is True
    assert resp.command_id == "test-1"


def test_active_field_is_null() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)
    state = iface.get_latest_state()
    assert state is not None
    assert state.active_field == "null"
    assert state.field_state == {}


def test_subscribe_state_fires_callback() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)
    received: list[object] = []
    iface.subscribe_state(lambda s: received.append(s))

    q.put((0.01, 0.02))
    time.sleep(_QUEUE_SETTLE)
    state = iface.get_latest_state()

    assert len(received) == 1
    assert received[0] is state


def test_unsubscribe_stops_callback() -> None:
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)
    received: list[object] = []
    iface.subscribe_state(lambda s: received.append(s))
    iface.unsubscribe_state()

    iface.get_latest_state()
    assert len(received) == 0


def test_velocity_uses_position_update_interval() -> None:
    """Velocity denominator must be the time between position updates,
    not between get_latest_state() polls (1 kHz vs ~60 Hz mouse rate).
    """
    q: _MouseQueue = multiprocessing.Queue()
    iface = MouseHapticInterface(mouse_queue=q)

    # Establish baseline at origin
    q.put((0.0, 0.0))
    time.sleep(_QUEUE_SETTLE)
    iface.get_latest_state()

    # Poll empty for ~300 ms (300 iterations × 1 ms sleep).
    # Before fix: _prev_time advances to T0 + 300ms.
    # After fix:  _position_time stays at T0.
    for _ in range(300):
        iface.get_latest_state()
        time.sleep(0.001)

    # New position arrives after ~300 ms of empty polls
    q.put((0.01, 0.0))  # 1 cm displacement
    time.sleep(_QUEUE_SETTLE)
    state = iface.get_latest_state()
    assert state is not None

    # With fix:   dt ≈ 350 ms → velocity ≈ 0.029 m/s  → passes
    # Before fix: dt ≈  50 ms → velocity ≈ 0.20  m/s  → fails
    assert abs(state.velocity[0]) < 0.1, (
        f"Velocity {state.velocity[0]:.2f} m/s is implausibly high — "
        "dt is probably using poll interval instead of position-update interval"
    )
