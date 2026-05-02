"""DisplayProcess — multiprocessing.Process subclass for PsychoPy rendering.

PsychoPy is imported ONLY inside run() — never at module level.
Importing PsychoPy creates an OpenGL context and must own the main thread.
"""

from __future__ import annotations

import contextlib
import logging
import math
import multiprocessing
import multiprocessing.queues
import os
import signal
import sys
import time
from queue import Full
from typing import TYPE_CHECKING, Any

import msgpack
import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_EVENT, TOPIC_STATE, TrialEvent, serialize
from hapticore.display._field_visuals import CART_PENDULUM_STIM_IDS, physics_body_stim_id

if TYPE_CHECKING:
    from psychopy.event import Mouse as _PsychoPyMouse
    from psychopy.visual import Window

    from hapticore.display.photodiode import PhotodiodePatch
    from hapticore.display.scene_manager import SceneManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixed meters → cm conversion — property of the PsychoPy backend (units="cm").
# ---------------------------------------------------------------------------
_METERS_TO_CM: float = 100.0

# Spatial parameter key categories for _convert_spatial_params().
_SPATIAL_POSITION_KEYS = frozenset({"position", "start", "end"})
_SPATIAL_DIMENSION_KEYS = frozenset({
    "radius", "width", "height", "size", "field_size", "dot_size",
})
_SPATIAL_VERTEX_KEYS = frozenset({"vertices"})

# ---------------------------------------------------------------------------
# Cup-and-ball visual constants used by the renderer's per-frame updates.
# Creation-time defaults live in display_client.py (task-controlled lifecycle).
# ---------------------------------------------------------------------------
_BALL_COLOR: list[float] = [0.2, 0.6, 1.0]
_SPILL_COLOR: list[float] = [1.0, 0.3, 0.3]


class DisplayProcess(multiprocessing.Process):
    """Separate process for visual stimulus rendering via PsychoPy.

    PsychoPy is imported only inside ``run()`` so that no OpenGL context
    is created in the parent process.
    """

    def __init__(
        self,
        display_config: DisplayConfig,
        zmq_config: ZMQConfig,
        *,
        headless: bool = False,
        mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None,
    ) -> None:
        super().__init__(name="DisplayProcess", daemon=True)
        self._display_config = display_config
        self._zmq_config = zmq_config
        self._headless = headless
        self._shutdown = multiprocessing.Event()
        self._mouse_queue = mouse_queue

    # ------------------------------------------------------------------
    # Spatial conversion helpers
    # ------------------------------------------------------------------

    def _effective_scale(self) -> float:
        """Combined workspace scale × meters→cm conversion factor."""
        return self._display_config.display_scale * _METERS_TO_CM

    def _effective_offset_cm(self) -> list[float]:
        """Display offset (meters) converted to cm."""
        s = self._effective_scale()
        return [o * s for o in self._display_config.display_offset]

    def _convert_spatial_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Convert spatial parameters from meters to display cm.

        Position-like keys get scale + offset; dimension-like keys get
        scale only; vertex lists get per-vertex position conversion.
        Non-spatial keys pass through unchanged.
        """
        eff = self._effective_scale()
        offset = self._effective_offset_cm()
        out: dict[str, Any] = {}
        for k, v in params.items():
            if k in _SPATIAL_POSITION_KEYS:
                out[k] = [v[0] * eff + offset[0], v[1] * eff + offset[1]]
            elif k in _SPATIAL_DIMENSION_KEYS:
                out[k] = v * eff if isinstance(v, (int, float)) else [c * eff for c in v]
            elif k in _SPATIAL_VERTEX_KEYS:
                out[k] = [
                    [vx * eff, vy * eff]
                    for vx, vy in v
                ]
            else:
                out[k] = v
        return out

    def request_shutdown(self) -> None:
        """Signal the process to exit its frame loop and shut down."""
        self._shutdown.set()

    def run(self) -> None:
        """Entry point executed in the child process."""
        # Disable pyglet's shadow window on Linux. The shadow window is a
        # hidden 1x1 GL context probe created at import time. In Zaphod
        # multi-screen setups, it anchors the real window to the wrong X
        # screen. Disabling it defers GL context creation to the real
        # window, which respects the ``screen`` parameter correctly.
        # Harmless on single-screen setups.
        if sys.platform == "linux":
            os.environ["PYGLET_SHADOW_WINDOW"] = "0"

        from psychopy import visual  # noqa: F811 — import ONLY here

        from hapticore.display.photodiode import PhotodiodePatch, remap_corner_for_mirror
        from hapticore.display.scene_manager import SceneManager

        signal.signal(signal.SIGINT, signal.SIG_IGN)

        win = self._create_window(visual)
        self._restore_pointer_focus()

        # In mouse mode, create a PsychoPy Mouse bound to the window.
        mouse = None
        if self._mouse_queue is not None:
            from psychopy import event as psychopy_event

            mouse = psychopy_event.Mouse(win=win)

        ctx = zmq.Context()
        display_sub = ctx.socket(zmq.SUB)
        display_sub.setsockopt(zmq.LINGER, 0)
        display_sub.connect(self._zmq_config.event_pub_address)
        display_sub.subscribe(TOPIC_DISPLAY)
        display_sub.setsockopt(zmq.RCVHWM, 100)

        state_sub = ctx.socket(zmq.SUB)
        state_sub.setsockopt(zmq.LINGER, 0)
        state_sub.connect(self._zmq_config.haptic_state_address)
        state_sub.subscribe(TOPIC_STATE)
        state_sub.setsockopt(zmq.RCVHWM, 10)

        timing_pub = ctx.socket(zmq.PUB)
        timing_pub.setsockopt(zmq.LINGER, 0)
        timing_pub.bind(self._zmq_config.display_event_address)

        scene = SceneManager(
            win, self._display_config, spatial_scale=self._effective_scale(),
        )
        photodiode_render_corner = remap_corner_for_mirror(
            self._display_config.photodiode_corner,
            mirror_horizontal=self._display_config.mirror_horizontal,
            mirror_vertical=self._display_config.mirror_vertical,
        )
        photodiode = PhotodiodePatch(
            win,
            corner=photodiode_render_corner,
            enabled=self._display_config.photodiode_enabled,
        )

        try:
            self._frame_loop(
                win, display_sub, state_sub, timing_pub, scene, photodiode,
                mouse=mouse,
            )
        finally:
            dropped = getattr(win, "nDroppedFrames", 0)
            if dropped:
                logger.warning("Dropped %d frames during display loop", dropped)
            display_sub.close()
            state_sub.close()
            timing_pub.close()
            ctx.term()
            win.close()

    # Cap dead-reckoning extrapolation to one haptic publish period (200 Hz).
    # Prevents runaway cursor if the haptic server disconnects or stalls.
    _MAX_INTERP_DT: float = 1.0 / 200.0

    # Sleep duration for headless mode throttle (~60 Hz).
    _HEADLESS_FRAME_SLEEP: float = 1.0 / 60.0

    def _frame_loop(
        self,
        win: Window,
        display_sub: zmq.Socket[Any],
        state_sub: zmq.Socket[Any],
        timing_pub: zmq.Socket[Any],
        scene: SceneManager,
        photodiode: PhotodiodePatch | None = None,
        mouse: _PsychoPyMouse | None = None,
    ) -> None:
        """Main rendering loop — one iteration per vsync frame."""
        latest_state: dict[str, Any] | None = None
        state_receive_time: float = time.monotonic()
        interpolation_enabled = self._display_config.cursor_interpolation

        while not self._shutdown.is_set():
            # 1. Drain display commands and dispatch
            display_msgs = self._drain_messages(display_sub)
            shown_stim_ids: list[str] = []
            command_timestamp: float | None = None
            for cmd in display_msgs:
                result = self._handle_display_command(scene, cmd)
                if result is not None:
                    shown_stim_ids.append(result)
                    if command_timestamp is None:
                        command_timestamp = cmd.get("timestamp", time.monotonic())

            # Trigger photodiode on stimulus onset
            if shown_stim_ids and photodiode is not None:
                photodiode.trigger()

            # 2. Drain haptic state — keep only the latest
            state_msgs = self._drain_messages(state_sub)
            if state_msgs:
                latest_state = state_msgs[-1]
                state_receive_time = time.monotonic()

            # 2a. Mouse mode: read mouse, push to queue, drive cursor directly
            if mouse is not None and self._mouse_queue is not None:
                mx_cm, my_cm = mouse.getPos()
                # Mouse returns raw screen coords; PsychoPy's viewScale does not
                # transform them. If we mirrored the rendered frame, flip the
                # mouse reading to match so the cursor tracks the subject's hand.
                # mouse mode is a visual-feedback loop; offset composition is not
                # load-bearing here because the user adapts to whatever mapping they see
                if self._display_config.mirror_horizontal:
                    mx_cm = -mx_cm
                if self._display_config.mirror_vertical:
                    my_cm = -my_cm
                eff = self._effective_scale()
                offset = self._effective_offset_cm()
                x_m = (mx_cm - offset[0]) / eff
                y_m = (my_cm - offset[1]) / eff
                with contextlib.suppress(Full):
                    self._mouse_queue.put_nowait((x_m, y_m))
                scene.set_cursor_position([mx_cm, my_cm])
            elif latest_state is not None:
                eff_scale = self._effective_scale()
                eff_offset = self._effective_offset_cm()
                if interpolation_enabled:
                    dt = min(
                        time.monotonic() - state_receive_time,
                        self._MAX_INTERP_DT,
                    )
                    raw = self._interpolate_position(latest_state, dt)
                else:
                    raw = latest_state.get("position", [0.0, 0.0, 0.0])

                cursor_pos = [
                    raw[0] * eff_scale + eff_offset[0],
                    raw[1] * eff_scale + eff_offset[1],
                ]
                scene.set_cursor_position(cursor_pos)

            # 2b. Update scene from field_state data
            if latest_state is not None:
                self._update_from_field_state(scene, latest_state)

            # 3. Draw all stimuli
            scene.draw_all()

            # 3b. Draw photodiode on top of everything
            if photodiode is not None:
                photodiode.draw()

            # 4. Capture flip timestamp via callOnFlip
            flip_time: list[float] = []
            win.callOnFlip(lambda ft=flip_time: ft.append(time.monotonic()))

            # 5. Flip — blocks until vsync
            win.flip()

            # Throttle headless mode (no vsync → flip returns immediately)
            if self._headless:
                time.sleep(self._HEADLESS_FRAME_SLEEP)

            # 6. Publish stimulus_onset timing event if any "show" commands
            if shown_stim_ids and flip_time:
                onset_ts = flip_time[0]
                cmd_ts = command_timestamp if command_timestamp is not None else onset_ts
                event = TrialEvent(
                    timestamp=onset_ts,
                    event_name="stimulus_onset",
                    event_code=0,
                    trial_number=-1,
                    data={
                        "stim_ids": shown_stim_ids,
                        "command_timestamp": cmd_ts,
                        "onset_timestamp": onset_ts,
                        "onset_delay": onset_ts - cmd_ts,
                    },
                )
                with contextlib.suppress(zmq.Again):
                    timing_pub.send_multipart(
                        [TOPIC_EVENT, serialize(event)], zmq.NOBLOCK,
                    )

    @staticmethod
    def _interpolate_position(state: dict[str, Any], dt: float) -> list[float]:
        """Extrapolate position using velocity for dead-reckoning interpolation.

        Only used when ``cursor_interpolation=True``.

        Parameters
        ----------
        state : dict
            Haptic state dict with ``"position"`` and ``"velocity"`` keys.
        dt : float
            Time elapsed since the state was received (seconds).
        """
        pos = state.get("position", [0.0, 0.0, 0.0])
        vel = state.get("velocity", [0.0, 0.0, 0.0])
        return [pos[0] + vel[0] * dt, pos[1] + vel[1] * dt]

    def _handle_display_command(self, scene: SceneManager, cmd: dict[str, Any]) -> str | None:
        """Dispatch a single display command to the SceneManager.

        Spatial parameters in ``"show"`` and ``"update_scene"`` commands are
        converted from meters to display cm via :meth:`_convert_spatial_params`.

        Returns the stim_id for successful ``"show"`` commands, ``None`` otherwise.
        """
        action = cmd.get("action")
        try:
            if action == "show":
                stim_id = cmd["stim_id"]
                params = self._convert_spatial_params(cmd.get("params", {}))
                scene.show(stim_id, params)
                return stim_id
            elif action == "hide":
                scene.hide(cmd.get("stim_id", ""))
            elif action == "clear":
                scene.clear()
            elif action == "update_scene":
                params = cmd.get("params", {})
                for stim_id, stim_params in params.items():
                    scene.update(stim_id, self._convert_spatial_params(stim_params))
            else:
                logger.warning("Unknown display action: %r", action)
        except Exception:
            logger.exception("Error handling display command: %r", cmd)
        return None

    # ------------------------------------------------------------------
    # Field-state rendering
    # ------------------------------------------------------------------

    def _update_from_field_state(self, scene: SceneManager, state: dict[str, Any]) -> None:
        """Update scene from haptic field_state data."""
        active_field = state.get("active_field", "")
        field_state = state.get("field_state", {})

        if active_field == "cart_pendulum":
            self._update_cart_pendulum(scene, state, field_state)
        elif active_field == "physics_world":
            self._update_physics_bodies(scene, field_state)
        # Other field types (null, spring_damper, constant, workspace_limit, channel):
        # no continuous visual updates needed — task controller manages
        # discrete stimuli via show/hide commands.

    def _update_cart_pendulum(
        self,
        scene: SceneManager,
        state: dict[str, Any],
        field_state: dict[str, Any],
    ) -> None:
        """Update cup, ball, and string positions from CartPendulumField state.

        Positions are converted from meters to cm via _effective_scale/offset.
        Only updates stimuli that already exist — the task is responsible for
        creating them via create_cart_pendulum_stimuli().
        """
        _CUP_ID, _BALL_ID, _STRING_ID = CART_PENDULUM_STIM_IDS
        eff_scale = self._effective_scale()
        eff_offset = self._effective_offset_cm()

        cup_x = field_state.get("cup_x", 0.0)
        ball_x = field_state.get("ball_x", 0.0)
        ball_y = field_state.get("ball_y", 0.0)
        spilled = field_state.get("spilled", False)

        cup_cx = cup_x * eff_scale + eff_offset[0]
        cup_cy = eff_offset[1]
        ball_cx = ball_x * eff_scale + eff_offset[0]
        ball_cy = ball_y * eff_scale + eff_offset[1]

        # Update cup position
        if scene.has_stimulus(_CUP_ID):
            scene.update(_CUP_ID, {"position": [cup_cx, cup_cy]})

        # Update ball position and spill color
        if scene.has_stimulus(_BALL_ID):
            ball_color = _SPILL_COLOR if spilled else _BALL_COLOR
            scene.update(_BALL_ID, {
                "position": [ball_cx, ball_cy],
                "color": ball_color,
            })

        # Update string endpoints
        string_stim = scene.get_stimulus(_STRING_ID)
        if string_stim is not None:
            string_stim.start = [cup_cx, cup_cy]
            string_stim.end = [ball_cx, ball_cy]

    def _update_physics_bodies(
        self, scene: SceneManager, field_state: dict[str, Any],
    ) -> None:
        """Update positions and angles of physics body stimuli.

        The task controller creates visual stimuli for each body via
        ``show_stimulus("__body_<id>", ...)``. This method only updates
        positions and angles from the physics simulation.
        """
        eff_scale = self._effective_scale()
        eff_offset = self._effective_offset_cm()
        bodies = field_state.get("bodies", {})
        for body_id, body_state in bodies.items():
            stim_id = physics_body_stim_id(body_id)
            if scene.has_stimulus(stim_id):
                pos = body_state.get("position", [0, 0])
                angle_rad = body_state.get("angle", 0.0)
                scene.update(stim_id, {
                    "position": [
                        pos[0] * eff_scale + eff_offset[0],
                        pos[1] * eff_scale + eff_offset[1],
                    ],
                    "orientation": angle_rad * (180.0 / math.pi),
                })

    def _create_window(self, visual_module: Any) -> Window:
        """Create a PsychoPy Window from the display configuration."""
        from psychopy import monitors  # noqa: F811 — import ONLY here

        cfg = self._display_config
        mon = monitors.Monitor("hapticore")
        mon.setWidth(cfg.monitor_width_cm)        # physical screen width
        mon.setSizePix(list(cfg.resolution))       # pixel resolution
        mon.setDistance(cfg.monitor_distance_cm)    # viewing distance

        view_scale: list[float] | None = None
        if cfg.mirror_horizontal or cfg.mirror_vertical:
            view_scale = [
                -1.0 if cfg.mirror_horizontal else 1.0,
                -1.0 if cfg.mirror_vertical else 1.0,
            ]

        return visual_module.Window(
            size=list(cfg.resolution),
            fullscr=False,
            color=cfg.background_color,
            monitor=mon,
            units="cm",
            allowGUI=False,
            winType="pyglet",
            checkTiming=False,
            screen=cfg.screen,
            viewScale=view_scale,
        )

    def _restore_pointer_focus(self) -> None:
        """Restore X11 keyboard focus to follow the mouse pointer.

        After creating a PsychoPy window on a WM-less Zaphod screen, pyglet
        calls XSetInputFocus on the new window, which moves keyboard focus
        away from the control-room screen. Without a window manager on the
        rig screen, nothing returns focus when the operator interacts with
        the control room. Setting focus to PointerRoot causes keyboard
        input to follow the mouse pointer across X screens.
        """
        if sys.platform != "linux":
            return
        import ctypes
        import ctypes.util

        try:
            x11_path = ctypes.util.find_library("X11")
            if not x11_path:
                return
            x11 = ctypes.cdll.LoadLibrary(x11_path)
            
            # Declare C signatures — without these, ctypes assumes c_int for
            # all args/returns, truncating 64-bit pointers on x86_64.
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XSetInputFocus.argtypes = [
                ctypes.c_void_p,  # display
                ctypes.c_long,    # focus (Window / PointerRoot)
                ctypes.c_int,     # revert_to
                ctypes.c_ulong,   # time
            ]
            x11.XSetInputFocus.restype = ctypes.c_int
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XFlush.restype = ctypes.c_int
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.restype = ctypes.c_int

            display = x11.XOpenDisplay(None)
            if not display:
                return
            POINTER_ROOT = 1
            REVERT_TO_POINTER_ROOT = 1
            CURRENT_TIME = 0
            try:
                x11.XSetInputFocus(
                    display, POINTER_ROOT, REVERT_TO_POINTER_ROOT, CURRENT_TIME
                )
                x11.XFlush(display)
            finally:
                x11.XCloseDisplay(display)
        except (OSError, AttributeError):
            logger.debug("Could not restore X11 keyboard focus to PointerRoot")


    @staticmethod
    def _drain_messages(socket: zmq.Socket[Any]) -> list[dict[str, Any]]:
        """Read all pending messages from a SUB socket without blocking.

        Returns a list of deserialized msgpack dicts. Returns an empty
        list immediately if no messages are pending.
        """
        messages: list[dict[str, Any]] = []
        while True:
            try:
                _topic, payload = socket.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break
            try:
                messages.append(msgpack.unpackb(payload, raw=False))
            except (msgpack.UnpackException, ValueError):
                logger.warning("Skipping malformed display message")
        return messages
