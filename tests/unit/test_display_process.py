"""Tests for DisplayProcess import safety, subclass checks, and drain logic."""

from __future__ import annotations

import multiprocessing
import time
import unittest.mock
from unittest.mock import MagicMock

import msgpack
import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY
from hapticore.core.messaging import make_ipc_address

# Type alias used in TestUpdatePhysicsBodies
from hapticore.display.process import DisplayProcess


class TestImportSafety:
    """Verify that importing the display package does not require PsychoPy."""

    def test_import_display_package(self) -> None:
        """import hapticore.display succeeds without PsychoPy installed."""
        import hapticore.display  # noqa: F401

    def test_import_display_client(self) -> None:
        import hapticore.display.display_client  # noqa: F401

    def test_import_display_process(self) -> None:
        import hapticore.display.process  # noqa: F401


class TestDisplayProcessSubclass:
    """Verify DisplayProcess is a multiprocessing.Process subclass."""

    def test_is_process_subclass(self) -> None:
        from hapticore.display.process import DisplayProcess

        assert issubclass(DisplayProcess, multiprocessing.Process)

    def test_instantiation(self) -> None:
        from hapticore.display.process import DisplayProcess

        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=ZMQConfig(),
            headless=True,
        )
        assert proc.name == "DisplayProcess"
        assert proc.daemon is True


class TestRequestShutdown:
    """Verify request_shutdown sets the internal event."""

    def test_sets_shutdown_event(self) -> None:
        from hapticore.display.process import DisplayProcess

        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=ZMQConfig(),
        )
        assert not proc._shutdown.is_set()
        proc.request_shutdown()
        assert proc._shutdown.is_set()


class TestDrainMessages:
    """Verify _drain_messages returns all pending messages without blocking."""

    def test_empty_socket_returns_empty_list(self) -> None:
        from hapticore.display.process import DisplayProcess

        addr = make_ipc_address("drain")
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(addr)

        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(addr)
        sub.subscribe(TOPIC_DISPLAY)
        time.sleep(0.1)

        result = DisplayProcess._drain_messages(sub)
        assert result == []

        sub.close()
        pub.close()
        ctx.term()

    def test_drains_all_pending_messages(self) -> None:
        from hapticore.display.process import DisplayProcess

        addr = make_ipc_address("drain")
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(addr)

        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(addr)
        sub.subscribe(TOPIC_DISPLAY)
        time.sleep(0.1)

        # Publish 3 messages
        for i in range(3):
            payload = msgpack.packb({"action": "clear", "index": i}, use_bin_type=True)
            pub.send_multipart([TOPIC_DISPLAY, payload])

        time.sleep(0.05)

        result = DisplayProcess._drain_messages(sub)
        assert len(result) == 3
        assert result[0]["index"] == 0
        assert result[1]["index"] == 1
        assert result[2]["index"] == 2

        # Socket should be empty now
        result2 = DisplayProcess._drain_messages(sub)
        assert result2 == []

        sub.close()
        pub.close()
        ctx.term()


class TestPhotodiodeInFrameLoop:
    """Verify _handle_display_command returns stim_id on show, None otherwise.

    The frame loop uses the return value to decide whether to trigger
    the photodiode: ``if shown_stim_ids and photodiode is not None``.
    """

    def test_show_returns_stim_id(self) -> None:
        """'show' command returns stim_id, which causes photodiode.trigger()."""
        from hapticore.display.process import DisplayProcess

        scene = MagicMock()
        cmd = {"action": "show", "stim_id": "target", "params": {"type": "circle"}}
        result = DisplayProcess._handle_display_command(scene, cmd)
        assert result == "target"

    def test_hide_returns_none(self) -> None:
        """'hide' command returns None — photodiode should not trigger."""
        from hapticore.display.process import DisplayProcess

        scene = MagicMock()
        cmd = {"action": "hide", "stim_id": "target"}
        result = DisplayProcess._handle_display_command(scene, cmd)
        assert result is None

    def test_clear_returns_none(self) -> None:
        """'clear' command returns None — photodiode should not trigger."""
        from hapticore.display.process import DisplayProcess

        scene = MagicMock()
        cmd = {"action": "clear"}
        result = DisplayProcess._handle_display_command(scene, cmd)
        assert result is None


class TestUpdateFromFieldState:
    """Verify _update_from_field_state dispatches based on active_field."""

    def _make_proc(self) -> DisplayProcess:
        from hapticore.display.process import DisplayProcess

        return DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def test_cart_pendulum_calls_update_cart_pendulum(self) -> None:
        proc = self._make_proc()
        scene = MagicMock()
        state = {
            "active_field": "cart_pendulum",
            "field_state": {"cup_x": 0.0, "ball_x": 0.02, "ball_y": -0.1},
        }
        with unittest.mock.patch.object(proc, "_update_cart_pendulum") as mock_cp:
            proc._update_from_field_state(scene, state)
            mock_cp.assert_called_once_with(scene, state, state["field_state"])

    def test_physics_world_calls_update_physics_bodies(self) -> None:
        proc = self._make_proc()
        scene = MagicMock()
        state = {
            "active_field": "physics_world",
            "field_state": {"bodies": {"puck": {"position": [0, 0], "angle": 0.0}}},
        }
        with unittest.mock.patch.object(proc, "_update_physics_bodies") as mock_pb:
            proc._update_from_field_state(scene, state)
            mock_pb.assert_called_once_with(scene, state["field_state"])

    def test_null_field_calls_neither(self) -> None:
        proc = self._make_proc()
        scene = MagicMock()
        state = {"active_field": "null", "field_state": {}}
        with (
            unittest.mock.patch.object(proc, "_update_cart_pendulum") as mock_cp,
            unittest.mock.patch.object(proc, "_update_physics_bodies") as mock_pb,
        ):
            proc._update_from_field_state(scene, state)
            mock_cp.assert_not_called()
            mock_pb.assert_not_called()

    def test_spring_damper_field_calls_neither(self) -> None:
        proc = self._make_proc()
        scene = MagicMock()
        state = {"active_field": "spring_damper", "field_state": {}}
        with (
            unittest.mock.patch.object(proc, "_update_cart_pendulum") as mock_cp,
            unittest.mock.patch.object(proc, "_update_physics_bodies") as mock_pb,
        ):
            proc._update_from_field_state(scene, state)
            mock_cp.assert_not_called()
            mock_pb.assert_not_called()


class TestUpdatePhysicsBodies:
    """Verify _update_physics_bodies scales positions and updates orientation."""

    def _make_proc(self, scale: float = 100.0, offset: list[float] | None = None) -> DisplayProcess:
        from hapticore.display.process import DisplayProcess

        return DisplayProcess(
            display_config=DisplayConfig(
                display_scale=scale,
                display_offset=offset or [0.0, 0.0],
            ),
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def test_updates_position_and_orientation(self) -> None:
        import math

        proc = self._make_proc(scale=100.0, offset=[1.0, 2.0])
        scene = MagicMock()
        scene.has_stimulus.return_value = True

        field_state = {
            "bodies": {
                "puck": {"position": [0.05, 0.1], "angle": 1.5708},
            },
        }
        proc._update_physics_bodies(scene, field_state)

        scene.update.assert_called_once_with(
            "__body_puck",
            {
                "position": [0.05 * 100.0 + 1.0, 0.1 * 100.0 + 2.0],
                "orientation": 1.5708 * (180.0 / math.pi),
            },
        )

    def test_skips_bodies_not_in_scene(self) -> None:
        proc = self._make_proc()
        scene = MagicMock()
        scene.has_stimulus.return_value = False

        field_state = {
            "bodies": {
                "puck": {"position": [0.05, 0.1], "angle": 0.0},
            },
        }
        proc._update_physics_bodies(scene, field_state)
        scene.update.assert_not_called()

    def test_handles_multiple_bodies(self) -> None:
        proc = self._make_proc(scale=100.0, offset=[0.0, 0.0])
        scene = MagicMock()
        scene.has_stimulus.return_value = True

        field_state = {
            "bodies": {
                "puck": {"position": [0.01, 0.02], "angle": 0.0},
                "striker": {"position": [0.03, 0.04], "angle": 0.5},
            },
        }
        proc._update_physics_bodies(scene, field_state)
        assert scene.update.call_count == 2
