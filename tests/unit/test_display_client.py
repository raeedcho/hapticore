"""Tests for DisplayClient protocol compliance and message publishing."""

from __future__ import annotations

import time

import msgpack
import pytest

from hapticore.core.interfaces import DisplayInterface
from hapticore.core.messages import TOPIC_DISPLAY
from hapticore.core.messaging import EventBus, make_ipc_address
from hapticore.display.display_client import DisplayClient


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
