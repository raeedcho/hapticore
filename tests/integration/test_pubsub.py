"""Integration test for ZeroMQ PUB-SUB across processes."""

from __future__ import annotations

import multiprocessing
import time
import uuid

from hapticore.core.messages import (
    TOPIC_STATE,
    HapticState,
    deserialize,
    serialize,
)
from hapticore.core.messaging import EventBus


def _publisher_process(address: str, num_messages: int, ready_event: multiprocessing.Event) -> None:  # type: ignore[type-arg]
    """Publish num_messages at approximately 1 kHz."""
    bus = EventBus(address)
    pub = bus.create_publisher()

    # Signal that we are ready
    ready_event.set()

    # Wait for subscribers to connect (slow-joiner mitigation)
    time.sleep(0.3)

    for i in range(num_messages):
        msg = HapticState(
            timestamp=time.monotonic(),
            sequence=i,
            position=[0.01 * i, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            force=[0.0, 0.0, 0.0],
            active_field="null",
            field_state={},
        )
        pub.publish(TOPIC_STATE, serialize(msg))
        time.sleep(0.001)  # ~1 kHz

    # Send a few extra to flush
    time.sleep(0.1)
    pub.close()


def _subscriber_process(
    address: str,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
    ready_event: multiprocessing.Event,  # type: ignore[type-arg]
    duration_s: float = 3.0,
) -> None:
    """Subscribe and count received messages, measuring latency."""
    bus = EventBus(address)
    sub = bus.create_subscriber(topics=[TOPIC_STATE])

    ready_event.set()

    count = 0
    latencies: list[float] = []
    deadline = time.monotonic() + duration_s

    while time.monotonic() < deadline:
        result = sub.recv(timeout_ms=100)
        if result is not None:
            _, payload = result
            msg = deserialize(payload, HapticState)
            latency = time.monotonic() - msg.timestamp
            latencies.append(latency)
            count += 1

    sub.close()
    result_queue.put({"count": count, "latencies": latencies})


class TestPubSubIntegration:
    """Integration tests for multi-process PUB-SUB messaging."""

    def test_multiprocess_pubsub(self) -> None:
        """Test that multiple subscriber processes receive published messages."""
        address = f"ipc:///tmp/hapticore_integ_{uuid.uuid4().hex[:8]}"
        num_messages = 500  # Reduced from 1000 for CI reliability

        pub_ready = multiprocessing.Event()
        sub1_ready = multiprocessing.Event()
        sub2_ready = multiprocessing.Event()
        q1: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()
        q2: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()

        # Start subscribers first, then publisher
        sub1 = multiprocessing.Process(
            target=_subscriber_process, args=(address, q1, sub1_ready)
        )
        sub2 = multiprocessing.Process(
            target=_subscriber_process, args=(address, q2, sub2_ready)
        )
        pub = multiprocessing.Process(
            target=_publisher_process, args=(address, num_messages, pub_ready)
        )

        sub1.start()
        sub2.start()

        # Wait for subscribers to be ready
        sub1_ready.wait(timeout=5)
        sub2_ready.wait(timeout=5)
        time.sleep(0.1)

        pub.start()
        pub_ready.wait(timeout=5)

        pub.join(timeout=10)
        sub1.join(timeout=10)
        sub2.join(timeout=10)

        r1 = q1.get(timeout=5)
        r2 = q2.get(timeout=5)

        # Both subscribers should receive most messages
        # Allow for slow-joiner: may miss first few messages
        min_expected = num_messages * 0.90  # At least 90%
        assert r1["count"] >= min_expected, f"Sub1 got {r1['count']}, expected >= {min_expected}"
        assert r2["count"] >= min_expected, f"Sub2 got {r2['count']}, expected >= {min_expected}"

        # Latency check: median should be < 1 ms (relaxed for CI)
        latencies1 = r1["latencies"]
        if latencies1:
            sorted_lat = sorted(latencies1)  # type: ignore[arg-type]
            median_lat = sorted_lat[len(sorted_lat) // 2]
            # Relaxed to 10ms for CI environments
            assert median_lat < 0.01, f"Median latency {median_lat*1000:.2f}ms exceeds 10ms"
