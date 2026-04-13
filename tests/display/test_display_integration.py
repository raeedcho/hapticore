"""Integration tests for DisplayProcess with PsychoPy.

Guarded by ``pytest.importorskip("psychopy")`` and ``@pytest.mark.display``.
Tests the full frame loop including command dispatch and timing events.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("psychopy")

import msgpack  # noqa: E402
import zmq  # noqa: E402

from hapticore.core.config import DisplayConfig, ZMQConfig  # noqa: E402
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_EVENT, TOPIC_STATE  # noqa: E402
from hapticore.core.messaging import make_ipc_address  # noqa: E402


def _make_zmq_config() -> ZMQConfig:
    """Generate a ZMQConfig with unique addresses for test isolation."""
    return ZMQConfig(
        event_pub_address=make_ipc_address("dp_evt"),
        haptic_state_address=make_ipc_address("dp_state"),
        display_event_address=make_ipc_address("dp_tevt"),
    )


@pytest.mark.display
class TestDisplayIntegration:
    """Full integration tests for DisplayProcess frame loop."""

    def test_show_command_no_crash(self) -> None:
        """Start DisplayProcess, send a show command, run for 10 frames, no crash."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(zmq_config.event_pub_address)

        try:
            time.sleep(0.5)

            payload = msgpack.packb(
                {
                    "action": "show",
                    "stim_id": "target",
                    "params": {"type": "circle", "radius": 0.01},
                    "timestamp": time.monotonic(),
                },
                use_bin_type=True,
            )
            pub.send_multipart([TOPIC_DISPLAY, payload])

            # Let it run for a few frames
            time.sleep(0.5)
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()
            pub.close()
            ctx.term()

    def test_stimulus_onset_event_published(self) -> None:
        """Send 'show' command, subscribe to timing events, verify onset event."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()

        # Publisher for display commands
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(zmq_config.event_pub_address)

        # Subscriber for timing events
        event_sub = ctx.socket(zmq.SUB)
        event_sub.setsockopt(zmq.LINGER, 0)
        event_sub.connect(zmq_config.display_event_address)
        event_sub.subscribe(TOPIC_EVENT)

        try:
            time.sleep(1.5)

            cmd_ts = time.monotonic()
            payload = msgpack.packb(
                {
                    "action": "show",
                    "stim_id": "target",
                    "params": {"type": "circle", "radius": 0.01},
                    "timestamp": cmd_ts,
                },
                use_bin_type=True,
            )
            pub.send_multipart([TOPIC_DISPLAY, payload])

            # Wait for timing event
            poller = zmq.Poller()
            poller.register(event_sub, zmq.POLLIN)
            received = False
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                socks = dict(poller.poll(100))
                if event_sub in socks:
                    topic, data = event_sub.recv_multipart()
                    assert topic == TOPIC_EVENT
                    event = msgpack.unpackb(data, raw=False)
                    assert event["event_name"] == "stimulus_onset"
                    assert "target" in event["data"]["stim_ids"]
                    assert "onset_timestamp" in event["data"]
                    assert event["data"]["onset_timestamp"] > 0
                    received = True
                    break

            assert received, "Did not receive stimulus_onset timing event"
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()
            pub.close()
            event_sub.close()
            ctx.term()

    def test_show_hide_show_no_crash(self) -> None:
        """Send show, hide, then show again for same stim_id — no crash."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(zmq_config.event_pub_address)

        try:
            time.sleep(0.5)

            for action in ["show", "hide", "show"]:
                msg: dict = {
                    "action": action,
                    "stim_id": "target",
                    "timestamp": time.monotonic(),
                }
                if action == "show":
                    msg["params"] = {"type": "circle", "radius": 0.01}
                payload = msgpack.packb(msg, use_bin_type=True)
                pub.send_multipart([TOPIC_DISPLAY, payload])
                time.sleep(0.1)

            time.sleep(0.3)
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()
            pub.close()
            ctx.term()

    def test_headless_empty_loop_no_crash(self) -> None:
        """Run for 100+ frames headless with no stimuli — verify no crash."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        try:
            # headless flip is very fast, 100 frames should complete quickly
            time.sleep(2.0)
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()


@pytest.mark.display
class TestCartPendulumRendering:
    """Integration tests for cup-and-ball rendering from field_state."""

    def test_cart_pendulum_state_no_crash(self) -> None:
        """Publish cart_pendulum haptic state — process stays alive."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        state_pub = ctx.socket(zmq.PUB)
        state_pub.setsockopt(zmq.LINGER, 0)
        state_pub.bind(zmq_config.haptic_state_address)

        try:
            time.sleep(0.5)

            # Publish haptic state with cart_pendulum field_state
            state = {
                "position": [0.0, 0.0, 0.0],
                "velocity": [0.0, 0.0, 0.0],
                "active_field": "cart_pendulum",
                "field_state": {
                    "cup_x": 0.0,
                    "ball_x": 0.02,
                    "ball_y": -0.1,
                    "phi": 0.2,
                    "phi_dot": 0.0,
                    "spilled": False,
                },
            }
            for _ in range(5):
                payload = msgpack.packb(state, use_bin_type=True)
                state_pub.send_multipart([TOPIC_STATE, payload])
                time.sleep(0.05)

            time.sleep(0.5)
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()
            state_pub.close()
            ctx.term()

    def test_spill_state_no_crash(self) -> None:
        """Publish spilled=False then spilled=True — process stays alive."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        state_pub = ctx.socket(zmq.PUB)
        state_pub.setsockopt(zmq.LINGER, 0)
        state_pub.bind(zmq_config.haptic_state_address)

        try:
            time.sleep(0.5)

            for spilled in [False, True, False]:
                state = {
                    "position": [0.0, 0.0, 0.0],
                    "velocity": [0.0, 0.0, 0.0],
                    "active_field": "cart_pendulum",
                    "field_state": {
                        "cup_x": 0.0,
                        "ball_x": 0.0,
                        "ball_y": -0.1,
                        "phi": 0.0,
                        "phi_dot": 0.0,
                        "spilled": spilled,
                    },
                }
                payload = msgpack.packb(state, use_bin_type=True)
                state_pub.send_multipart([TOPIC_STATE, payload])
                time.sleep(0.1)

            time.sleep(0.3)
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()
            state_pub.close()
            ctx.term()

    def test_field_type_switch_no_crash(self) -> None:
        """Switch from cart_pendulum to null — process stays alive."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        state_pub = ctx.socket(zmq.PUB)
        state_pub.setsockopt(zmq.LINGER, 0)
        state_pub.bind(zmq_config.haptic_state_address)

        try:
            time.sleep(0.5)

            # Start with cart_pendulum
            cp_state = {
                "position": [0.0, 0.0, 0.0],
                "velocity": [0.0, 0.0, 0.0],
                "active_field": "cart_pendulum",
                "field_state": {
                    "cup_x": 0.0,
                    "ball_x": 0.0,
                    "ball_y": -0.1,
                    "phi": 0.0,
                    "phi_dot": 0.0,
                    "spilled": False,
                },
            }
            for _ in range(3):
                payload = msgpack.packb(cp_state, use_bin_type=True)
                state_pub.send_multipart([TOPIC_STATE, payload])
                time.sleep(0.05)

            # Switch to null field
            null_state = {
                "position": [0.0, 0.0, 0.0],
                "velocity": [0.0, 0.0, 0.0],
                "active_field": "null",
                "field_state": {},
            }
            for _ in range(3):
                payload = msgpack.packb(null_state, use_bin_type=True)
                state_pub.send_multipart([TOPIC_STATE, payload])
                time.sleep(0.05)

            time.sleep(0.3)
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()
            state_pub.close()
            ctx.term()

    def test_end_to_end_with_display_commands(self) -> None:
        """Full sequence: show target, cart_pendulum state, clear — no crash."""
        from hapticore.display.process import DisplayProcess

        zmq_config = _make_zmq_config()
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        cmd_pub = ctx.socket(zmq.PUB)
        cmd_pub.setsockopt(zmq.LINGER, 0)
        cmd_pub.bind(zmq_config.event_pub_address)

        state_pub = ctx.socket(zmq.PUB)
        state_pub.setsockopt(zmq.LINGER, 0)
        state_pub.bind(zmq_config.haptic_state_address)

        event_sub = ctx.socket(zmq.SUB)
        event_sub.setsockopt(zmq.LINGER, 0)
        event_sub.connect(zmq_config.display_event_address)
        event_sub.subscribe(TOPIC_EVENT)

        try:
            time.sleep(1.5)

            # Show a target circle
            show_cmd = msgpack.packb(
                {
                    "action": "show",
                    "stim_id": "target",
                    "params": {"type": "circle", "radius": 1.0},
                    "timestamp": time.monotonic(),
                },
                use_bin_type=True,
            )
            # Send repeatedly to cover ZMQ slow-joiner window
            for _ in range(3):
                cmd_pub.send_multipart([TOPIC_DISPLAY, show_cmd])
                time.sleep(0.05)

            # Publish 50 cart_pendulum states
            for i in range(50):
                state = {
                    "position": [0.0, 0.0, 0.0],
                    "velocity": [0.0, 0.0, 0.0],
                    "active_field": "cart_pendulum",
                    "field_state": {
                        "cup_x": 0.001 * i,
                        "ball_x": 0.001 * i + 0.02,
                        "ball_y": -0.1,
                        "phi": 0.1,
                        "phi_dot": 0.0,
                        "spilled": False,
                    },
                }
                payload = msgpack.packb(state, use_bin_type=True)
                state_pub.send_multipart([TOPIC_STATE, payload])
                time.sleep(0.02)

            # Clear
            clear_cmd = msgpack.packb(
                {"action": "clear", "timestamp": time.monotonic()},
                use_bin_type=True,
            )
            cmd_pub.send_multipart([TOPIC_DISPLAY, clear_cmd])
            time.sleep(0.3)

            assert proc.is_alive()

            # Check for at least one stimulus_onset event
            poller = zmq.Poller()
            poller.register(event_sub, zmq.POLLIN)
            received = False
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                socks = dict(poller.poll(100))
                if event_sub in socks:
                    _topic, data = event_sub.recv_multipart()
                    event = msgpack.unpackb(data, raw=False)
                    if event.get("event_name") == "stimulus_onset":
                        received = True
                        break

            assert received, "Expected at least one stimulus_onset event"
        finally:
            proc.request_shutdown()
            proc.join(timeout=3.0)
            assert not proc.is_alive()
            cmd_pub.close()
            state_pub.close()
            event_sub.close()
            ctx.term()
