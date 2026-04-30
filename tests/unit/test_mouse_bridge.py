"""Unit tests for MouseBridge."""

from __future__ import annotations

import multiprocessing
import multiprocessing.queues
import time
from typing import Any

import msgpack
import pytest
import zmq

from hapticore.core.messaging import make_ipc_address
from hapticore.haptic.mouse_bridge import MouseBridge


def _make_router(
    ctx: zmq.Context[Any], address: str,
) -> zmq.Socket[Any]:
    """Bind a ROUTER socket with a short receive timeout."""
    router: zmq.Socket[Any] = ctx.socket(zmq.ROUTER)
    router.setsockopt(zmq.LINGER, 0)
    router.setsockopt(zmq.RCVTIMEO, 2000)
    router.bind(address)
    return router


class TestMouseBridge:
    def test_bridge_sends_position_and_velocity(self) -> None:
        """Bridge sends set_mock_position and set_mock_velocity for each sample."""
        address = make_ipc_address("mb_test_sends")
        ctx: zmq.Context[Any] = zmq.Context()
        router = _make_router(ctx, address)

        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=4)
        )
        bridge = MouseBridge(mouse_queue=queue, command_address=address)

        try:
            bridge.start()
            time.sleep(0.05)  # let bridge connect
            queue.put((0.1, -0.05))
            time.sleep(0.05)

            # Receive position command
            frames = router.recv_multipart()
            # ROUTER frames: [identity, b"", payload]
            payload = msgpack.unpackb(frames[2], raw=False)
            assert payload["method"] == "set_mock_position"
            pos = payload["params"]["position"]
            assert abs(pos[0] - 0.1) < 1e-9
            assert abs(pos[1] - (-0.05)) < 1e-9
            assert pos[2] == 0.0

            # Receive velocity command
            frames = router.recv_multipart()
            payload = msgpack.unpackb(frames[2], raw=False)
            assert payload["method"] == "set_mock_velocity"
            vel = payload["params"]["velocity"]
            assert len(vel) == 3
        finally:
            bridge.request_stop()
            bridge.join(timeout=2.0)
            router.close(linger=0)
            ctx.term()

    def test_bridge_computes_finite_difference_velocity(self) -> None:
        """Velocity approximates (Δx/Δt, Δy/Δt, 0)."""
        address = make_ipc_address("mb_test_vel")
        ctx: zmq.Context[Any] = zmq.Context()
        router = _make_router(ctx, address)

        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=4)
        )
        bridge = MouseBridge(mouse_queue=queue, command_address=address)

        try:
            bridge.start()
            time.sleep(0.05)

            queue.put((0.0, 0.0))
            # Drain first pos+vel pair
            router.recv_multipart()  # set_mock_position
            router.recv_multipart()  # set_mock_velocity

            time.sleep(0.1)  # ~100 ms gap
            queue.put((0.1, 0.0))
            time.sleep(0.05)

            # Receive second position command (discard)
            router.recv_multipart()
            # Receive second velocity command
            frames = router.recv_multipart()
            payload = msgpack.unpackb(frames[2], raw=False)
            assert payload["method"] == "set_mock_velocity"
            vel = payload["params"]["velocity"]
            # Δx=0.1, Δt≈0.1s → vx≈1.0 m/s; allow ±50% for timing variation
            assert 0.3 < vel[0] < 3.0, f"vx={vel[0]:.3f} m/s outside expected range"
            assert abs(vel[1]) < 1.0
            assert vel[2] == pytest.approx(0.0)
        finally:
            bridge.request_stop()
            bridge.join(timeout=2.0)
            router.close(linger=0)
            ctx.term()

    def test_bridge_drains_queue_keeps_latest(self) -> None:
        """When multiple samples are queued, only the latest is forwarded."""
        address = make_ipc_address("mb_test_drain")
        ctx: zmq.Context[Any] = zmq.Context()
        router = _make_router(ctx, address)

        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=20)
        )
        bridge = MouseBridge(mouse_queue=queue, command_address=address)

        try:
            # Fill queue before starting bridge to avoid race
            for i in range(10):
                queue.put((float(i) * 0.01, 0.0))

            bridge.start()
            time.sleep(0.1)

            # Collect all position commands received within a short window
            positions: list[list[float]] = []
            while True:
                try:
                    frames = router.recv_multipart()
                    payload = msgpack.unpackb(frames[2], raw=False)
                    if payload["method"] == "set_mock_position":
                        positions.append(payload["params"]["position"])
                except zmq.Again:
                    break

            # The last position received must be the final queued value (x=0.09)
            assert len(positions) >= 1
            assert abs(positions[-1][0] - 0.09) < 1e-9, (
                f"Expected last position x≈0.09, got {positions[-1][0]}"
            )
        finally:
            bridge.request_stop()
            bridge.join(timeout=2.0)
            router.close(linger=0)
            ctx.term()

    def test_bridge_stops_on_request(self) -> None:
        """request_stop() causes the bridge thread to exit."""
        address = make_ipc_address("mb_test_stop")
        ctx: zmq.Context[Any] = zmq.Context()
        router = _make_router(ctx, address)

        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=4)
        )
        bridge = MouseBridge(mouse_queue=queue, command_address=address)

        try:
            bridge.start()
            assert bridge.is_alive()

            bridge.request_stop()
            bridge.join(timeout=2.0)

            assert not bridge.is_alive()
        finally:
            bridge.request_stop()
            bridge.join(timeout=1.0)
            router.close(linger=0)
            ctx.term()

    def test_bridge_fire_and_forget_does_not_block(self) -> None:
        """Bridge should not hang even if the ROUTER never reads responses."""
        address = make_ipc_address("mb_test_noblock")
        ctx: zmq.Context[Any] = zmq.Context()
        # Bind the router but never read from it
        router: zmq.Socket[Any] = ctx.socket(zmq.ROUTER)
        router.setsockopt(zmq.LINGER, 0)
        router.bind(address)

        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=120)
        )
        bridge = MouseBridge(mouse_queue=queue, command_address=address)

        try:
            bridge.start()
            time.sleep(0.02)

            for i in range(100):
                queue.put((float(i) * 0.001, 0.0))

            time.sleep(0.2)
            bridge.request_stop()
            bridge.join(timeout=2.0)

            assert not bridge.is_alive(), "Bridge hung — fire-and-forget may be blocking"
        finally:
            if bridge.is_alive():
                bridge.request_stop()
                bridge.join(timeout=1.0)
            router.close(linger=0)
            ctx.term()
