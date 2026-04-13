"""Tests for DisplayProcess import safety, subclass checks, and drain logic."""

from __future__ import annotations

import multiprocessing
import time
import unittest.mock
from typing import Any
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

    def _make_proc(self) -> DisplayProcess:
        return DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def test_show_returns_stim_id(self) -> None:
        """'show' command returns stim_id, which causes photodiode.trigger()."""
        proc = self._make_proc()
        scene = MagicMock()
        cmd = {"action": "show", "stim_id": "target", "params": {"type": "circle"}}
        result = proc._handle_display_command(scene, cmd)
        assert result == "target"

    def test_hide_returns_none(self) -> None:
        """'hide' command returns None — photodiode should not trigger."""
        proc = self._make_proc()
        scene = MagicMock()
        cmd = {"action": "hide", "stim_id": "target"}
        result = proc._handle_display_command(scene, cmd)
        assert result is None

    def test_clear_returns_none(self) -> None:
        """'clear' command returns None — photodiode should not trigger."""
        proc = self._make_proc()
        scene = MagicMock()
        cmd = {"action": "clear"}
        result = proc._handle_display_command(scene, cmd)
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

    def _make_proc(
        self, display_scale: float = 1.0, offset: list[float] | None = None,
    ) -> DisplayProcess:
        from hapticore.display.process import DisplayProcess

        return DisplayProcess(
            display_config=DisplayConfig(
                display_scale=display_scale,
                display_offset=offset or [0.0, 0.0],
            ),
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def test_updates_position_and_orientation(self) -> None:
        import math

        from hapticore.display.process import _METERS_TO_CM

        # display_scale=1.0 → eff_scale = 1.0 * 100 = 100
        # offset=[0.01, 0.02] m → eff_offset = [1.0, 2.0] cm
        proc = self._make_proc(display_scale=1.0, offset=[0.01, 0.02])
        scene = MagicMock()
        scene.has_stimulus.return_value = True

        field_state = {
            "bodies": {
                "puck": {"position": [0.05, 0.1], "angle": 1.5708},
            },
        }
        proc._update_physics_bodies(scene, field_state)

        eff = 1.0 * _METERS_TO_CM
        scene.update.assert_called_once_with(
            "__body_puck",
            {
                "position": [0.05 * eff + 0.01 * eff, 0.1 * eff + 0.02 * eff],
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
        proc = self._make_proc(display_scale=1.0, offset=[0.0, 0.0])
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


class TestUpdateCartPendulum:
    """Verify _update_cart_pendulum creates and updates cup/ball/string stimuli."""

    def _make_proc(
        self, display_scale: float = 1.0, offset: list[float] | None = None,
    ) -> DisplayProcess:
        return DisplayProcess(
            display_config=DisplayConfig(
                display_scale=display_scale,
                display_offset=offset or [0.0, 0.0],
            ),
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def _make_field_state(
        self,
        *,
        cup_x: float = 0.0,
        ball_x: float = 0.02,
        ball_y: float = -0.1,
        spilled: bool = False,
    ) -> dict[str, Any]:
        return {
            "cup_x": cup_x,
            "ball_x": ball_x,
            "ball_y": ball_y,
            "phi": 0.2,
            "phi_dot": 0.0,
            "spilled": spilled,
        }

    def test_creates_cup_ball_string_on_first_call(self) -> None:
        """First call should create __cup, __ball, __string via scene.show()."""
        proc = self._make_proc()
        scene = MagicMock()
        scene.has_stimulus.return_value = False

        state: dict[str, Any] = {"active_field": "cart_pendulum"}
        fs = self._make_field_state()
        proc._update_cart_pendulum(scene, state, fs)

        # All three stimuli should be created via show()
        show_calls = {call.args[0]: call.args[1] for call in scene.show.call_args_list}
        assert "__cup" in show_calls
        assert "__ball" in show_calls
        assert "__string" in show_calls

        # Cup should be a polygon
        assert show_calls["__cup"]["type"] == "polygon"
        # Ball should be a circle
        assert show_calls["__ball"]["type"] == "circle"
        # String should be a line
        assert show_calls["__string"]["type"] == "line"

    def test_updates_existing_stimuli_on_subsequent_calls(self) -> None:
        """Subsequent calls should update __cup and __ball via scene.update()."""
        proc = self._make_proc()
        scene = MagicMock()
        # Simulate stimuli already existing
        scene.has_stimulus.return_value = True
        string_stim = MagicMock()
        scene.get_stimulus.return_value = string_stim

        state: dict[str, Any] = {"active_field": "cart_pendulum"}
        fs = self._make_field_state(cup_x=0.03, ball_x=0.05, ball_y=-0.08)
        proc._update_cart_pendulum(scene, state, fs)

        # Cup and ball should be updated (not created)
        scene.show.assert_not_called()
        update_calls = {call.args[0]: call.args[1] for call in scene.update.call_args_list}
        assert "__cup" in update_calls
        assert "__ball" in update_calls

        # String endpoints should be set directly on the stimulus object
        assert string_stim.start is not None
        assert string_stim.end is not None

    def test_positions_scaled_by_display_scale_and_offset(self) -> None:
        """Positions from field_state (meters) are converted via eff_scale + offset."""
        from hapticore.display.process import _METERS_TO_CM

        # display_scale=1.0, offset=[0.05, 0.1] m
        # eff_scale = 1.0 * 100 = 100, eff_offset = [5.0, 10.0] cm
        proc = self._make_proc(display_scale=1.0, offset=[0.05, 0.1])
        scene = MagicMock()
        scene.has_stimulus.return_value = False

        state: dict[str, Any] = {"active_field": "cart_pendulum"}
        fs = self._make_field_state(cup_x=0.03, ball_x=0.05, ball_y=-0.08)
        proc._update_cart_pendulum(scene, state, fs)

        show_calls = {call.args[0]: call.args[1] for call in scene.show.call_args_list}

        eff = 1.0 * _METERS_TO_CM
        # Cup: cup_x * eff + offset_x * eff = 0.03*100 + 0.05*100 = 8.0
        assert show_calls["__cup"]["position"] == [0.03 * eff + 0.05 * eff, 0.1 * eff]
        # Ball: ball_x * eff + off_x, ball_y * eff + off_y
        assert show_calls["__ball"]["position"] == [
            0.05 * eff + 0.05 * eff,
            -0.08 * eff + 0.1 * eff,
        ]

    def test_ball_color_blue_when_not_spilled(self) -> None:
        """Ball should be blue when spilled=False."""
        from hapticore.display.process import _BALL_COLOR

        proc = self._make_proc()
        scene = MagicMock()
        scene.has_stimulus.return_value = False

        state: dict[str, Any] = {"active_field": "cart_pendulum"}
        fs = self._make_field_state(spilled=False)
        proc._update_cart_pendulum(scene, state, fs)

        show_calls = {call.args[0]: call.args[1] for call in scene.show.call_args_list}
        assert show_calls["__ball"]["color"] == _BALL_COLOR

    def test_ball_color_red_when_spilled(self) -> None:
        """Ball should turn red when spilled=True."""
        from hapticore.display.process import _SPILL_COLOR

        proc = self._make_proc()
        scene = MagicMock()
        scene.has_stimulus.return_value = False

        state: dict[str, Any] = {"active_field": "cart_pendulum"}
        fs = self._make_field_state(spilled=True)
        proc._update_cart_pendulum(scene, state, fs)

        show_calls = {call.args[0]: call.args[1] for call in scene.show.call_args_list}
        assert show_calls["__ball"]["color"] == _SPILL_COLOR

    def test_spill_color_change_on_update(self) -> None:
        """When updating existing ball, spill color should be passed to update()."""
        from hapticore.display.process import _SPILL_COLOR

        proc = self._make_proc()
        scene = MagicMock()
        scene.has_stimulus.return_value = True
        scene.get_stimulus.return_value = MagicMock()

        state: dict[str, Any] = {"active_field": "cart_pendulum"}
        fs = self._make_field_state(spilled=True)
        proc._update_cart_pendulum(scene, state, fs)

        update_calls = {call.args[0]: call.args[1] for call in scene.update.call_args_list}
        assert update_calls["__ball"]["color"] == _SPILL_COLOR

    def test_string_endpoints_updated_directly(self) -> None:
        """On subsequent frames, string start/end are set directly on the stim."""
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc(display_scale=1.0, offset=[0.0, 0.0])
        scene = MagicMock()
        scene.has_stimulus.return_value = True
        string_stim = MagicMock()
        scene.get_stimulus.return_value = string_stim

        state: dict[str, Any] = {"active_field": "cart_pendulum"}
        fs = self._make_field_state(cup_x=0.01, ball_x=0.03, ball_y=-0.05)
        proc._update_cart_pendulum(scene, state, fs)

        eff = 1.0 * _METERS_TO_CM
        # String endpoints should match cup center → ball center (in cm)
        assert string_stim.start == [0.01 * eff, 0.0]
        assert string_stim.end == [0.03 * eff, -0.05 * eff]


class TestEffectiveScale:
    """Verify _effective_scale combines display_scale with _METERS_TO_CM."""

    def test_default_scale(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = DisplayProcess(
            display_config=DisplayConfig(),  # display_scale=1.0
            zmq_config=ZMQConfig(),
            headless=True,
        )
        assert proc._effective_scale() == 1.0 * _METERS_TO_CM

    def test_custom_scale(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = DisplayProcess(
            display_config=DisplayConfig(display_scale=2.0),
            zmq_config=ZMQConfig(),
            headless=True,
        )
        assert proc._effective_scale() == 2.0 * _METERS_TO_CM


class TestEffectiveOffsetCm:
    """Verify _effective_offset_cm converts meters offset to cm."""

    def test_zero_offset(self) -> None:
        proc = DisplayProcess(
            display_config=DisplayConfig(display_offset=[0.0, 0.0]),
            zmq_config=ZMQConfig(),
            headless=True,
        )
        assert proc._effective_offset_cm() == [0.0, 0.0]

    def test_nonzero_offset(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = DisplayProcess(
            display_config=DisplayConfig(
                display_scale=1.0,
                display_offset=[0.05, -0.03],
            ),
            zmq_config=ZMQConfig(),
            headless=True,
        )
        eff = 1.0 * _METERS_TO_CM
        assert proc._effective_offset_cm() == [0.05 * eff, -0.03 * eff]


class TestConvertSpatialParams:
    """Verify _convert_spatial_params converts meters → cm for spatial keys."""

    def _make_proc(
        self, display_scale: float = 1.0, offset: list[float] | None = None,
    ) -> DisplayProcess:
        return DisplayProcess(
            display_config=DisplayConfig(
                display_scale=display_scale,
                display_offset=offset or [0.0, 0.0],
            ),
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def test_position_converted(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc(display_scale=1.0, offset=[0.01, 0.02])
        eff = 1.0 * _METERS_TO_CM
        result = proc._convert_spatial_params({"position": [0.05, 0.1]})
        assert result["position"] == [0.05 * eff + 0.01 * eff, 0.1 * eff + 0.02 * eff]

    def test_radius_converted(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc(display_scale=1.0)
        eff = 1.0 * _METERS_TO_CM
        result = proc._convert_spatial_params({"radius": 0.015})
        assert result["radius"] == 0.015 * eff

    def test_vertices_converted(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc(display_scale=1.0, offset=[0.01, 0.0])
        eff = 1.0 * _METERS_TO_CM
        verts = [[-0.01, 0.0], [0.01, 0.0], [0.0, 0.02]]
        result = proc._convert_spatial_params({"vertices": verts})
        expected = [
            [-0.01 * eff, 0.0],
            [0.01 * eff, 0.0],
            [0.0 * eff, 0.02 * eff],
        ]
        assert result["vertices"] == expected

    def test_nonspatial_passes_through(self) -> None:
        proc = self._make_proc()
        result = proc._convert_spatial_params({
            "color": [1.0, 0.0, 0.0],
            "opacity": 0.5,
            "type": "circle",
        })
        assert result == {"color": [1.0, 0.0, 0.0], "opacity": 0.5, "type": "circle"}

    def test_mixed_params(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc(display_scale=1.0)
        eff = 1.0 * _METERS_TO_CM
        result = proc._convert_spatial_params({
            "type": "circle",
            "position": [0.05, 0.0],
            "radius": 0.01,
            "color": [1.0, 1.0, 0.0],
        })
        assert result["type"] == "circle"
        assert result["position"] == [0.05 * eff, 0.0]
        assert result["radius"] == 0.01 * eff
        assert result["color"] == [1.0, 1.0, 0.0]

    def test_start_end_converted(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc(display_scale=1.0)
        eff = 1.0 * _METERS_TO_CM
        result = proc._convert_spatial_params({
            "start": [0.0, 0.0],
            "end": [0.1, 0.05],
        })
        assert result["start"] == [0.0, 0.0]
        assert result["end"] == [0.1 * eff, 0.05 * eff]

    def test_width_height_converted(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc(display_scale=2.0)
        eff = 2.0 * _METERS_TO_CM
        result = proc._convert_spatial_params({"width": 0.02, "height": 0.01})
        assert result["width"] == 0.02 * eff
        assert result["height"] == 0.01 * eff


class TestHandleDisplayCommandConversion:
    """Verify _handle_display_command converts spatial params for show/update_scene."""

    def _make_proc(self) -> DisplayProcess:
        return DisplayProcess(
            display_config=DisplayConfig(),  # display_scale=1.0
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def test_show_converts_radius(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc()
        scene = MagicMock()
        cmd = {
            "action": "show",
            "stim_id": "target",
            "params": {"type": "circle", "radius": 0.015, "position": [0.08, 0.0]},
        }
        proc._handle_display_command(scene, cmd)

        eff = 1.0 * _METERS_TO_CM
        args = scene.show.call_args
        assert args[0][1]["radius"] == 0.015 * eff
        assert args[0][1]["position"] == [0.08 * eff, 0.0]
        # Non-spatial keys pass through
        assert args[0][1]["type"] == "circle"

    def test_update_scene_converts_positions(self) -> None:
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc()
        scene = MagicMock()
        cmd = {
            "action": "update_scene",
            "params": {
                "target": {"position": [0.05, 0.1]},
            },
        }
        proc._handle_display_command(scene, cmd)

        eff = 1.0 * _METERS_TO_CM
        args = scene.update.call_args
        assert args[0][1]["position"] == [0.05 * eff, 0.1 * eff]
