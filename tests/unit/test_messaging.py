"""Tests for ZeroMQ messaging wrappers."""

from __future__ import annotations

import time
import uuid

import pytest

from hapticore.core.messages import (
    TOPIC_EVENT,
    TOPIC_STATE,
    Command,
    HapticState,
    serialize,
)
from hapticore.core.messaging import (
    CommandClient,
    CommandServer,
    EventBus,
)


def _unique_ipc() -> str:
    return f"ipc:///tmp/hapticore_test_{uuid.uuid4().hex[:8]}"


class TestEventPubSub:
    """Tests for EventPublisher and EventSubscriber."""

    def test_publish_receive(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber()

        # Allow slow-joiner time
        time.sleep(0.1)

        msg = HapticState(
            timestamp=1.0,
            sequence=1,
            position=[0.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            force=[0.0, 0.0, 0.0],
            active_field="null",
            field_state={},
        )
        payload = serialize(msg)
        pub.publish(TOPIC_STATE, payload)

        result = sub.recv(timeout_ms=500)
        assert result is not None
        topic, data = result
        assert topic == TOPIC_STATE
        assert data == payload

        pub.close()
        sub.close()

    def test_topic_filtering(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_STATE])

        time.sleep(0.1)

        state_msg = serialize(
            HapticState(
                timestamp=1.0, sequence=1, position=[0.0, 0.0, 0.0],
                velocity=[0.0, 0.0, 0.0], force=[0.0, 0.0, 0.0],
                active_field="null", field_state={},
            )
        )
        event_msg = b"some event data"

        pub.publish(TOPIC_EVENT, event_msg)
        pub.publish(TOPIC_STATE, state_msg)

        # Give time for messages to arrive
        time.sleep(0.05)

        received = []
        for _ in range(10):
            result = sub.recv(timeout_ms=100)
            if result is None:
                break
            received.append(result)

        # Should only get the state message
        assert len(received) >= 1
        assert all(topic == TOPIC_STATE for topic, _ in received)

        pub.close()
        sub.close()

    def test_multiple_subscribers(self) -> None:
        addr = _unique_ipc()
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub1 = bus.create_subscriber()
        sub2 = bus.create_subscriber()

        time.sleep(0.1)

        payload = b"test_payload"
        pub.publish(TOPIC_STATE, payload)

        time.sleep(0.05)

        r1 = sub1.recv(timeout_ms=500)
        r2 = sub2.recv(timeout_ms=500)

        assert r1 is not None
        assert r2 is not None
        assert r1[1] == payload
        assert r2[1] == payload

        pub.close()
        sub1.close()
        sub2.close()


class TestCommandClientServer:
    """Tests for CommandClient and CommandServer."""

    def test_round_trip(self) -> None:
        addr = _unique_ipc()
        server = CommandServer(addr)
        client = CommandClient(addr)

        time.sleep(0.05)

        def echo_handler(params: dict) -> dict:  # type: ignore[type-arg]
            return {"echoed": params}

        server.register_handler("echo", echo_handler)

        cmd = Command(command_id="test123", method="echo", params={"value": 42})

        # Send and poll in interleaved fashion
        import threading

        response_holder: list[object] = []

        def send_cmd() -> None:
            resp = client.send_command(cmd, timeout_ms=2000)
            response_holder.append(resp)

        t = threading.Thread(target=send_cmd)
        t.start()

        # Poll server until command is handled
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if server.poll_and_dispatch(timeout_ms=100):
                break

        t.join(timeout=2.0)

        assert len(response_holder) == 1
        resp = response_holder[0]
        assert isinstance(resp, object)
        assert hasattr(resp, "success")
        assert resp.success is True  # type: ignore[union-attr]
        assert resp.result["echoed"]["value"] == 42  # type: ignore[union-attr]

        client.close()
        server.close()

    def test_timeout(self) -> None:
        addr = _unique_ipc()
        server = CommandServer(addr)
        client = CommandClient(addr)

        time.sleep(0.05)

        # No handler registered - don't poll the server
        cmd = Command(command_id="timeout_test", method="nonexistent", params={})

        with pytest.raises(TimeoutError):
            client.send_command(cmd, timeout_ms=200)

        client.close()
        server.close()

    def test_unknown_method(self) -> None:
        addr = _unique_ipc()
        server = CommandServer(addr)
        client = CommandClient(addr)

        time.sleep(0.05)

        cmd = Command(command_id="unknown_test", method="does_not_exist", params={})

        import threading

        response_holder: list[object] = []

        def send_cmd() -> None:
            resp = client.send_command(cmd, timeout_ms=2000)
            response_holder.append(resp)

        t = threading.Thread(target=send_cmd)
        t.start()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if server.poll_and_dispatch(timeout_ms=100):
                break

        t.join(timeout=2.0)

        assert len(response_holder) == 1
        resp = response_holder[0]
        assert resp.success is False  # type: ignore[union-attr]
        assert "Unknown method" in resp.error  # type: ignore[union-attr]

        client.close()
        server.close()
