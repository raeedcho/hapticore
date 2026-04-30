"""Tests for DisplayClient protocol compliance and message publishing."""

from __future__ import annotations

import time

import msgpack
import pytest

from hapticore.core.interfaces import DisplayInterface
from hapticore.core.messages import TOPIC_DISPLAY
from hapticore.core.messaging import EventBus, make_ipc_address
from hapticore.display.client import DisplayClient


def _unique_ipc() -> str:
    return make_ipc_address("test")


class TestProtocolCompliance:
    """Verify DisplayClient satisfies DisplayInterface Protocol."""

    def test_isinstance_check(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            client = DisplayClient(pub)
            assert isinstance(client, DisplayInterface)
        finally:
            pub.close()


class TestShowStimulus:
    def test_publishes_correct_message(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.show_stimulus("target", {"type": "circle", "radius": 0.01})

        result = sub.recv(timeout_ms=500)
        assert result is not None
        topic, payload = result
        assert topic == TOPIC_DISPLAY
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "show"
        assert msg["stim_id"] == "target"
        assert msg["params"] == {"type": "circle", "radius": 0.01}
        assert "timestamp" in msg
        assert isinstance(msg["timestamp"], float)

        pub.close()
        sub.close()


class TestHideStimulus:
    def test_publishes_correct_message(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.hide_stimulus("target")

        result = sub.recv(timeout_ms=500)
        assert result is not None
        topic, payload = result
        assert topic == TOPIC_DISPLAY
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "hide"
        assert msg["stim_id"] == "target"
        assert "timestamp" in msg

        pub.close()
        sub.close()


class TestClear:
    def test_publishes_correct_message(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.clear()

        result = sub.recv(timeout_ms=500)
        assert result is not None
        topic, payload = result
        assert topic == TOPIC_DISPLAY
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "clear"
        assert "timestamp" in msg

        pub.close()
        sub.close()


class TestUpdateScene:
    def test_publishes_correct_message(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.update_scene({"target": {"position": [0.1, 0]}})

        result = sub.recv(timeout_ms=500)
        assert result is not None
        topic, payload = result
        assert topic == TOPIC_DISPLAY
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "update_scene"
        assert msg["params"] == {"target": {"position": [0.1, 0]}}
        assert "timestamp" in msg

        pub.close()
        sub.close()


class TestGetFlipTimestamp:
    def test_raises_not_implemented(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            client = DisplayClient(pub)
            with pytest.raises(NotImplementedError):
                client.get_flip_timestamp()
        finally:
            pub.close()


class TestShowCartPendulum:
    def test_creates_three_stimuli(self) -> None:
        """show_cart_pendulum() should publish three show commands."""
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.show_cart_pendulum()

        msgs: list[dict] = []
        for _ in range(3):
            result = sub.recv(timeout_ms=500)
            assert result is not None
            _topic, payload = result
            msgs.append(msgpack.unpackb(payload, raw=False))

        stim_ids = {m["stim_id"] for m in msgs}
        assert stim_ids == {"__cup", "__ball", "__string"}

        # Verify types
        by_id = {m["stim_id"]: m for m in msgs}
        assert by_id["__cup"]["params"]["type"] == "polygon"
        assert by_id["__ball"]["params"]["type"] == "circle"
        assert by_id["__string"]["params"]["type"] == "line"

        # Verify default parameters
        assert by_id["__ball"]["params"]["radius"] == 0.008
        assert by_id["__cup"]["params"]["fill"] is False

        pub.close()
        sub.close()

    def test_custom_colors(self) -> None:
        """Custom cup_color should override the default."""
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        custom_color = [1.0, 0.0, 0.0]
        client = DisplayClient(pub)
        client.show_cart_pendulum(cup_color=custom_color)

        msgs: list[dict] = []
        for _ in range(3):
            result = sub.recv(timeout_ms=500)
            assert result is not None
            _topic, payload = result
            msgs.append(msgpack.unpackb(payload, raw=False))

        by_id = {m["stim_id"]: m for m in msgs}
        assert by_id["__cup"]["params"]["color"] == custom_color

        pub.close()
        sub.close()

    def test_initial_pose_params(self) -> None:
        """show_cart_pendulum with initial_phi places ball at correct offset."""
        import math

        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        phi = 0.3
        length = 0.4
        cup_pos = [0.1, 0.0]
        client.show_cart_pendulum(
            cup_position=cup_pos, initial_phi=phi, pendulum_length=length,
        )

        msgs: list[dict] = []
        for _ in range(3):
            result = sub.recv(timeout_ms=500)
            assert result is not None
            _topic, payload = result
            msgs.append(msgpack.unpackb(payload, raw=False))

        by_id = {m["stim_id"]: m for m in msgs}

        # Cup at specified position
        cup_pos_actual = by_id["__cup"]["params"]["position"]
        assert cup_pos_actual[0] == pytest.approx(0.1)
        assert cup_pos_actual[1] == pytest.approx(0.0)

        # Ball at cup + L*sin(phi), cup - L*cos(phi)
        expected_bx = 0.1 + 0.4 * math.sin(0.3)
        expected_by = 0.0 - 0.4 * math.cos(0.3)
        ball_pos = by_id["__ball"]["params"]["position"]
        assert ball_pos[0] == pytest.approx(expected_bx, abs=1e-9)
        assert ball_pos[1] == pytest.approx(expected_by, abs=1e-9)

        # String connects cup to ball
        string_params = by_id["__string"]["params"]
        assert string_params["start"][0] == pytest.approx(0.1)
        assert string_params["end"][0] == pytest.approx(expected_bx, abs=1e-9)
        assert string_params["end"][1] == pytest.approx(expected_by, abs=1e-9)

        pub.close()
        sub.close()


class TestHideCartPendulum:
    def test_hides_three_stimuli(self) -> None:
        """hide_cart_pendulum() should publish three hide commands."""
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.hide_cart_pendulum()

        stim_ids: set[str] = set()
        for _ in range(3):
            result = sub.recv(timeout_ms=500)
            assert result is not None
            _topic, payload = result
            msg = msgpack.unpackb(payload, raw=False)
            assert msg["action"] == "hide"
            stim_ids.add(msg["stim_id"])

        assert stim_ids == {"__cup", "__ball", "__string"}

        pub.close()
        sub.close()


class TestShowPhysicsBodies:
    def test_creates_prefixed_stimuli(self) -> None:
        """show_physics_bodies() should create __body_<id> stimuli."""
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.show_physics_bodies({
            "puck": {"type": "circle", "radius": 0.02},
            "striker": {"type": "circle", "radius": 0.03},
        })

        stim_ids: set[str] = set()
        for _ in range(2):
            result = sub.recv(timeout_ms=500)
            assert result is not None
            _topic, payload = result
            msg = msgpack.unpackb(payload, raw=False)
            assert msg["action"] == "show"
            stim_ids.add(msg["stim_id"])

        assert stim_ids == {"__body_puck", "__body_striker"}

        pub.close()
        sub.close()


class TestHidePhysicsBodies:
    def test_hides_prefixed_stimuli(self) -> None:
        """hide_physics_bodies() should hide __body_<id> stimuli."""
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_DISPLAY])
        time.sleep(0.1)

        client = DisplayClient(pub)
        client.hide_physics_bodies(["puck", "striker"])

        stim_ids: set[str] = set()
        for _ in range(2):
            result = sub.recv(timeout_ms=500)
            assert result is not None
            _topic, payload = result
            msg = msgpack.unpackb(payload, raw=False)
            assert msg["action"] == "hide"
            stim_ids.add(msg["stim_id"])

        assert stim_ids == {"__body_puck", "__body_striker"}

        pub.close()
        sub.close()
