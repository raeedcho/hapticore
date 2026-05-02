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
        import hapticore.display.client  # noqa: F401

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
            "field_state": {"cup_x": 0.0, "ball_x": 0.02, "ball_y": 0.1},
        }
        with unittest.mock.patch.object(proc, "_update_cart_pendulum") as mock_cp:
            proc._update_from_field_state(scene, state)
            mock_cp.assert_called_once_with(scene, state["field_state"])

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

    def test_composite_with_cart_pendulum_child(self) -> None:
        """Composite field dispatches to _update_cart_pendulum for cup_x child."""
        proc = self._make_proc()
        scene = MagicMock()
        cp_child = {"cup_x": 0.01, "ball_x": 0.02, "ball_y": 0.05, "spilled": False}
        state = {
            "active_field": "composite",
            "field_state": {
                "children": [
                    cp_child,
                    {"in_bounds": True},  # channel child
                ],
            },
        }
        with unittest.mock.patch.object(proc, "_update_cart_pendulum") as mock_cp:
            proc._update_from_field_state(scene, state)
            mock_cp.assert_called_once_with(scene, cp_child)

    def test_composite_with_physics_world_child(self) -> None:
        """Composite field dispatches to _update_physics_bodies for bodies child."""
        proc = self._make_proc()
        scene = MagicMock()
        pw_child = {"bodies": {"puck": {"position": [0, 0], "angle": 0.0}}}
        state = {
            "active_field": "composite",
            "field_state": {
                "children": [
                    pw_child,
                    {"in_bounds": True},
                ],
            },
        }
        with unittest.mock.patch.object(proc, "_update_physics_bodies") as mock_pb:
            proc._update_from_field_state(scene, state)
            mock_pb.assert_called_once_with(scene, pw_child)

    def test_composite_with_no_visual_children(self) -> None:
        """Composite with only channel/spring_damper children calls neither renderer."""
        proc = self._make_proc()
        scene = MagicMock()
        state = {
            "active_field": "composite",
            "field_state": {
                "children": [
                    {"in_bounds": True},  # channel
                    {},                    # spring_damper (empty pack_state)
                ],
            },
        }
        with (
            unittest.mock.patch.object(proc, "_update_cart_pendulum") as mock_cp,
            unittest.mock.patch.object(proc, "_update_physics_bodies") as mock_pb,
        ):
            proc._update_from_field_state(scene, state)
            mock_cp.assert_not_called()
            mock_pb.assert_not_called()

    def test_composite_with_empty_children(self) -> None:
        """Composite with empty children list calls neither renderer."""
        proc = self._make_proc()
        scene = MagicMock()
        state = {
            "active_field": "composite",
            "field_state": {"children": []},
        }
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
    """Verify _update_cart_pendulum updates cup/ball positions only."""

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
        ball_y: float = 0.1,
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

    def test_no_creation_when_stimuli_missing(self) -> None:
        """When stimuli don't exist, renderer does nothing (no creation)."""
        proc = self._make_proc()
        scene = MagicMock()
        scene.has_stimulus.return_value = False
        scene.get_stimulus.return_value = None

        fs = self._make_field_state()
        proc._update_cart_pendulum(scene, fs)

        # No stimuli should be created via show()
        scene.show.assert_not_called()
        # No stimuli should be updated via update()
        scene.update.assert_not_called()

    def test_updates_existing_stimuli_on_subsequent_calls(self) -> None:
        """Subsequent calls should update __cup and __ball via scene.update()."""
        proc = self._make_proc()
        scene = MagicMock()
        # Simulate stimuli already existing
        scene.has_stimulus.return_value = True

        fs = self._make_field_state(cup_x=0.03, ball_x=0.05, ball_y=0.08)
        proc._update_cart_pendulum(scene, fs)

        # Cup and ball should be updated (not created)
        scene.show.assert_not_called()
        update_calls = {call.args[0]: call.args[1] for call in scene.update.call_args_list}
        assert "__cup" in update_calls
        assert "__ball" in update_calls

    def test_positions_scaled_by_display_scale_and_offset(self) -> None:
        """Positions from field_state (meters) are converted via eff_scale + offset."""
        from hapticore.display.process import _METERS_TO_CM

        # display_scale=1.0, offset=[0.05, 0.1] m
        # eff_scale = 1.0 * 100 = 100, eff_offset = [5.0, 10.0] cm
        proc = self._make_proc(display_scale=1.0, offset=[0.05, 0.1])
        scene = MagicMock()
        scene.has_stimulus.return_value = True

        # ball_y=0.08 m is non-negative (L*(1-cos(phi)) convention)
        fs = self._make_field_state(cup_x=0.03, ball_x=0.05, ball_y=0.08)
        proc._update_cart_pendulum(scene, fs)

        update_calls = {call.args[0]: call.args[1] for call in scene.update.call_args_list}

        eff = 1.0 * _METERS_TO_CM
        # Cup: cup_x * eff + offset_x * eff = 0.03*100 + 0.05*100 = 8.0
        assert update_calls["__cup"]["position"] == [0.03 * eff + 0.05 * eff, 0.1 * eff]
        # Ball: ball_x * eff + off_x, ball_y * eff + off_y
        assert update_calls["__ball"]["position"] == [
            0.05 * eff + 0.05 * eff,
            0.08 * eff + 0.1 * eff,
        ]

    def test_renderer_does_not_change_ball_color(self) -> None:
        """Renderer only updates position — ball color is managed by task controller.

        CartPendulumVisuals.set_ball_color()/reset_ball_color() are the public
        helpers for changing ball color, avoiding a race with the continuous
        renderer.
        """
        proc = self._make_proc()
        scene = MagicMock()
        scene.has_stimulus.return_value = True

        # Even with spilled=True, the renderer must not set ball color
        fs = self._make_field_state(spilled=True)
        proc._update_cart_pendulum(scene, fs)

        update_calls = {call.args[0]: call.args[1] for call in scene.update.call_args_list}
        assert "color" not in update_calls.get("__ball", {})


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

    def test_update_scene_cursor_visibility(self) -> None:
        """update_scene with __cursor key calls set_cursor_visible."""
        proc = self._make_proc()
        scene = MagicMock()
        cmd = {
            "action": "update_scene",
            "params": {"__cursor": {"visible": False}},
        }
        proc._handle_display_command(scene, cmd)
        scene.set_cursor_visible.assert_called_once_with(False)

    def test_update_scene_cursor_visibility_with_other_params(self) -> None:
        """__cursor is handled and other stimulus updates still process."""
        from hapticore.display.process import _METERS_TO_CM

        proc = self._make_proc()
        scene = MagicMock()
        cmd = {
            "action": "update_scene",
            "params": {
                "__cursor": {"visible": True},
                "target": {"position": [0.05, 0.0]},
            },
        }
        proc._handle_display_command(scene, cmd)
        scene.set_cursor_visible.assert_called_once_with(True)
        # Normal stimulus update should also fire
        eff = 1.0 * _METERS_TO_CM
        scene.update.assert_called_once()
        args = scene.update.call_args
        assert args[0][0] == "target"
        assert args[0][1]["position"] == [0.05 * eff, 0.0]


class TestCreateWindowKwargs:
    """Verify _create_window passes screen and viewScale from DisplayConfig."""

    def _make_visual_mock(self) -> MagicMock:
        """Return a MagicMock that records Window constructor calls."""
        visual = MagicMock()
        visual.Window.return_value = MagicMock()
        return visual

    def _make_proc(self, **display_kwargs: Any) -> DisplayProcess:
        return DisplayProcess(
            display_config=DisplayConfig(**display_kwargs),
            zmq_config=ZMQConfig(),
            headless=True,
        )

    def _get_window_kwargs(self, visual: MagicMock) -> dict[str, Any]:
        _, kwargs = visual.Window.call_args
        return kwargs

    def _call_create_window(
        self, proc: DisplayProcess, visual: MagicMock,
    ) -> dict[str, Any]:
        """Call _create_window with mocked psychopy.monitors; return Window kwargs."""
        mock_monitors = MagicMock()
        mock_monitors.Monitor.return_value = MagicMock()
        sys_modules_patch = {
            "psychopy": MagicMock(),
        }
        with unittest.mock.patch.dict("sys.modules", sys_modules_patch):
            proc._create_window(visual)
        return self._get_window_kwargs(visual)

    def test_default_config_screen_zero_viewscale_none(self) -> None:
        """Default config → screen=0, viewScale=None."""
        proc = self._make_proc()
        visual = self._make_visual_mock()
        kwargs = self._call_create_window(proc, visual)
        assert kwargs["screen"] == 0
        assert kwargs["viewScale"] is None

    def test_screen_1_passed_through(self) -> None:
        """screen=1 is passed verbatim to visual.Window."""
        proc = self._make_proc(screen=1)
        visual = self._make_visual_mock()
        kwargs = self._call_create_window(proc, visual)
        assert kwargs["screen"] == 1

    def test_mirror_horizontal_only(self) -> None:
        """mirror_horizontal=True → viewScale=[-1.0, 1.0]."""
        proc = self._make_proc(mirror_horizontal=True)
        visual = self._make_visual_mock()
        kwargs = self._call_create_window(proc, visual)
        assert kwargs["viewScale"] == [-1.0, 1.0]

    def test_mirror_vertical_only(self) -> None:
        """mirror_vertical=True → viewScale=[1.0, -1.0]."""
        proc = self._make_proc(mirror_vertical=True)
        visual = self._make_visual_mock()
        kwargs = self._call_create_window(proc, visual)
        assert kwargs["viewScale"] == [1.0, -1.0]

    def test_both_mirrors(self) -> None:
        """Both mirror flags → viewScale=[-1.0, -1.0]."""
        proc = self._make_proc(mirror_horizontal=True, mirror_vertical=True)
        visual = self._make_visual_mock()
        kwargs = self._call_create_window(proc, visual)
        assert kwargs["viewScale"] == [-1.0, -1.0]
