"""Integration test for ZeroMQ PUB-SUB across processes."""

from __future__ import annotations

import multiprocessing
import sys
import time

import pytest

from hapticore.core.messages import (
    TOPIC_STATE,
    HapticState,
    deserialize,
    serialize,
)
from hapticore.core.messaging import EventBus, make_ipc_address


def _publisher_process(
    address: str,
    num_messages: int,
    ready_event: multiprocessing.Event,  # type: ignore[type-arg]
    done_event: multiprocessing.Event,  # type: ignore[type-arg]
) -> None:
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
        time.sleep(0.001)  # best-effort ~1 kHz

    # Let in-flight messages drain before signalling completion.
    # 0.2s covers the ZMQ send buffer at any expected send rate.
    time.sleep(0.2)
    done_event.set()
    # Allow subscribers to observe done_event before the socket closes.
    time.sleep(0.1)
    pub.close()


def _subscriber_process(
    address: str,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
    ready_event: multiprocessing.Event,  # type: ignore[type-arg]
    pub_ready: multiprocessing.Event,  # type: ignore[type-arg]
    done_event: multiprocessing.Event,  # type: ignore[type-arg]
    max_wait_s: float = 60.0,
) -> None:
    """Subscribe and count received messages, measuring latency."""
    bus = EventBus(address)
    sub = bus.create_subscriber(topics=[TOPIC_STATE])

    ready_event.set()

    # Wait for publisher to be ready before receiving.
    # On macOS with spawn, publisher startup can take 2-3 seconds.
    if not pub_ready.wait(timeout=15):
        # Publisher did not signal in time; report empty result for diagnostics.
        print("WARNING: pub_ready timed out — publisher may have crashed", file=sys.stderr)
        sub.close()
        result_queue.put({"count": 0, "latencies": []})
        return

    count = 0
    latencies: list[float] = []
    deadline = time.monotonic() + max_wait_s  # safety valve only

    while time.monotonic() < deadline:
        result = sub.recv(timeout_ms=100)
        if result is not None:
            _, payload = result
            msg = deserialize(payload, HapticState)
            latency = time.monotonic() - msg.timestamp
            latencies.append(latency)
            count += 1
        elif done_event.is_set():
            # Publisher signalled done and recv() returned None (no buffered
            # messages remain); exit cleanly. The 0.2s drain sleep in the
            # publisher ensures the ZMQ send buffer is flushed before
            # done_event is set, so this is safe to treat as end-of-stream.
            break

    sub.close()
    result_queue.put({"count": count, "latencies": latencies})


class TestPubSubIntegration:
    """Integration tests for multi-process PUB-SUB messaging."""

    def test_multiprocess_pubsub(self) -> None:
        """Test that multiple subscriber processes receive published messages."""
        address = make_ipc_address("integ")
        num_messages = 500  # Reduced from 1000 for CI reliability

        pub_ready = multiprocessing.Event()
        pub_done = multiprocessing.Event()
        sub1_ready = multiprocessing.Event()
        sub2_ready = multiprocessing.Event()
        q1: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()
        q2: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()

        # Start subscribers first, then publisher
        sub1 = multiprocessing.Process(
            target=_subscriber_process, args=(address, q1, sub1_ready, pub_ready, pub_done)
        )
        sub2 = multiprocessing.Process(
            target=_subscriber_process, args=(address, q2, sub2_ready, pub_ready, pub_done)
        )
        pub = multiprocessing.Process(
            target=_publisher_process, args=(address, num_messages, pub_ready, pub_done)
        )

        sub1.start()
        sub2.start()

        # Wait for subscribers to be ready
        sub1_ready.wait(timeout=10)
        sub2_ready.wait(timeout=10)
        time.sleep(0.1)

        pub.start()
        pub_ready.wait(timeout=10)

        try:
            pub.join(timeout=30)  # generous for slow CI
            sub1.join(timeout=10)
            sub2.join(timeout=10)

            assert not pub.is_alive(), "Publisher process did not exit within timeout"
            assert not sub1.is_alive(), "Subscriber 1 process did not exit within timeout"
            assert not sub2.is_alive(), "Subscriber 2 process did not exit within timeout"

            assert pub.exitcode == 0, f"Publisher process exited with code {pub.exitcode}"
            assert sub1.exitcode == 0, f"Subscriber 1 process exited with code {sub1.exitcode}"
            assert sub2.exitcode == 0, f"Subscriber 2 process exited with code {sub2.exitcode}"

            r1 = q1.get(timeout=5)
            r2 = q2.get(timeout=5)

            # Both subscribers should receive most messages
            # Allow for slow-joiner: may miss first few messages
            min_expected = num_messages * 0.95  # At least 95%
            assert r1["count"] >= min_expected, f"Sub1 got {r1['count']}, expected >= {min_expected}"
            assert r2["count"] >= min_expected, f"Sub2 got {r2['count']}, expected >= {min_expected}"

            # Latency check: median should be < 1 ms (relaxed for CI)
            latencies1 = r1["latencies"]
            if latencies1:
                sorted_lat = sorted(latencies1)  # type: ignore[arg-type]
                median_lat = sorted_lat[len(sorted_lat) // 2]
                # Relaxed to 10ms for CI environments
                assert median_lat < 0.01, f"Median latency {median_lat*1000:.2f}ms exceeds 10ms"
        finally:
            for proc in (pub, sub1, sub2):
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=5)

    @pytest.mark.slow
    def test_multiprocess_pubsub_strict(self) -> None:
        """Full spec compliance: 1000 msgs, <1ms latency, <1% loss."""
        address = make_ipc_address("integ-s")
        num_messages = 1000

        pub_ready = multiprocessing.Event()
        pub_done = multiprocessing.Event()
        sub1_ready = multiprocessing.Event()
        sub2_ready = multiprocessing.Event()
        q1: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()
        q2: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()

        sub1 = multiprocessing.Process(
            target=_subscriber_process, args=(address, q1, sub1_ready, pub_ready, pub_done)
        )
        sub2 = multiprocessing.Process(
            target=_subscriber_process, args=(address, q2, sub2_ready, pub_ready, pub_done)
        )
        pub = multiprocessing.Process(
            target=_publisher_process, args=(address, num_messages, pub_ready, pub_done)
        )

        sub1.start()
        sub2.start()

        sub1_ready.wait(timeout=10)
        sub2_ready.wait(timeout=10)
        time.sleep(0.1)

        pub.start()
        pub_ready.wait(timeout=10)

        pub.join(timeout=60)  # generous for slow CI
        sub1.join(timeout=15)
        sub2.join(timeout=15)

        r1 = q1.get(timeout=5)
        r2 = q2.get(timeout=5)

        # Strict: allow at most 1% loss (first 1-5 messages from slow-joiner)
        min_expected = num_messages * 0.99
        assert r1["count"] >= min_expected, f"Sub1 got {r1['count']}, expected >= {min_expected}"
        assert r2["count"] >= min_expected, f"Sub2 got {r2['count']}, expected >= {min_expected}"

        # Strict latency: median < 1 ms
        for label, result in [("Sub1", r1), ("Sub2", r2)]:
            latencies = result["latencies"]
            if latencies:
                sorted_lat = sorted(latencies)  # type: ignore[arg-type]
                median_lat = sorted_lat[len(sorted_lat) // 2]
                assert median_lat < 0.001, (
                    f"{label} median latency {median_lat * 1000:.2f}ms exceeds 1ms"
                )
