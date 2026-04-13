"""DisplayProcess — multiprocessing.Process subclass for PsychoPy rendering.

PsychoPy is imported ONLY inside run() — never at module level.
Importing PsychoPy creates an OpenGL context and must own the main thread.
"""

from __future__ import annotations

import contextlib
import logging
import math
import multiprocessing
import signal
import time
from typing import TYPE_CHECKING, Any

import msgpack
import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_EVENT, TOPIC_STATE, TrialEvent, serialize

if TYPE_CHECKING:
    from psychopy.visual import Window

    from hapticore.display.photodiode import PhotodiodePatch
    from hapticore.display.scene_manager import SceneManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cup-and-ball visual constants (all in cm — the display uses units="cm")
# ---------------------------------------------------------------------------
_CUP_HALF_WIDTH_CM: float = 1.5
_CUP_DEPTH_CM: float = 3.0
_BALL_RADIUS_CM: float = 0.8
_BALL_COLOR: list[float] = [0.2, 0.6, 1.0]
_SPILL_COLOR: list[float] = [1.0, 0.3, 0.3]
_CUP_COLOR: list[float] = [0.8, 0.8, 0.8]
_STRING_COLOR: list[float] = [0.5, 0.5, 0.5]
_STRING_WIDTH: float = 2.0  # pixels


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
    ) -> None:
        super().__init__(name="DisplayProcess", daemon=True)
        self._display_config = display_config
        self._zmq_config = zmq_config
        self._headless = headless
        self._shutdown = multiprocessing.Event()

    def request_shutdown(self) -> None:
        """Signal the process to exit its frame loop and shut down."""
        self._shutdown.set()

    def run(self) -> None:
        """Entry point executed in the child process."""
        from psychopy import visual  # noqa: F811 — import ONLY here

        from hapticore.display.photodiode import PhotodiodePatch
        from hapticore.display.scene_manager import SceneManager

        signal.signal(signal.SIGINT, signal.SIG_IGN)

        win = self._create_window(visual)

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

        scene = SceneManager(win, self._display_config)
        photodiode = PhotodiodePatch(
            win,
            corner=self._display_config.photodiode_corner,
            enabled=self._display_config.photodiode_enabled,
        )

        try:
            self._frame_loop(win, display_sub, state_sub, timing_pub, scene, photodiode)
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

            if latest_state is not None:
                scale = self._display_config.display_scale
                offset = self._display_config.display_offset
                if interpolation_enabled:
                    dt = min(
                        time.monotonic() - state_receive_time,
                        self._MAX_INTERP_DT,
                    )
                    raw = self._interpolate_position(latest_state, dt)
                else:
                    raw = latest_state.get("position", [0.0, 0.0, 0.0])

                cursor_pos = [raw[0] * scale + offset[0], raw[1] * scale + offset[1]]
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

    @staticmethod
    def _handle_display_command(scene: SceneManager, cmd: dict[str, Any]) -> str | None:
        """Dispatch a single display command to the SceneManager.

        Returns the stim_id for successful ``"show"`` commands, ``None`` otherwise.
        """
        action = cmd.get("action")
        try:
            if action == "show":
                stim_id = cmd["stim_id"]
                scene.show(stim_id, cmd.get("params", {}))
                return stim_id
            elif action == "hide":
                scene.hide(cmd.get("stim_id", ""))
            elif action == "clear":
                scene.clear()
            elif action == "update_scene":
                params = cmd.get("params", {})
                for stim_id, stim_params in params.items():
                    scene.update(stim_id, stim_params)
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

    def _ensure_stimulus(
        self, scene: SceneManager, stim_id: str,
        create_params: dict[str, Any], update_params: dict[str, Any],
    ) -> None:
        """Create stimulus on first call, update on subsequent calls."""
        if not scene.has_stimulus(stim_id):
            scene.show(stim_id, create_params)
        else:
            scene.update(stim_id, update_params)

    def _update_cart_pendulum(
        self,
        scene: SceneManager,
        state: dict[str, Any],
        field_state: dict[str, Any],
    ) -> None:
        """Render cup, ball, and string for the CartPendulumField."""
        scale = self._display_config.display_scale
        offset = self._display_config.display_offset

        cup_x = field_state.get("cup_x", 0.0)
        ball_x = field_state.get("ball_x", 0.0)
        ball_y = field_state.get("ball_y", 0.0)
        spilled = field_state.get("spilled", False)

        # Convert meters → cm and apply offset
        cup_cx = cup_x * scale + offset[0]
        cup_cy = offset[1]
        ball_cx = ball_x * scale + offset[0]
        ball_cy = ball_y * scale + offset[1]

        ball_color = _SPILL_COLOR if spilled else _BALL_COLOR

        # --- Cup (U-shaped polygon) ---
        hw = _CUP_HALF_WIDTH_CM
        d = _CUP_DEPTH_CM
        cup_vertices = [
            [-hw, 0.0],
            [-hw, -d],
            [hw, -d],
            [hw, 0.0],
        ]
        self._ensure_stimulus(
            scene,
            "__cup",
            create_params={
                "type": "polygon",
                "vertices": cup_vertices,
                "color": _CUP_COLOR,
                "fill": False,
                "position": [cup_cx, cup_cy],
            },
            update_params={"position": [cup_cx, cup_cy]},
        )

        # --- Ball (filled circle) ---
        self._ensure_stimulus(
            scene,
            "__ball",
            create_params={
                "type": "circle",
                "radius": _BALL_RADIUS_CM,
                "color": ball_color,
                "position": [ball_cx, ball_cy],
            },
            update_params={
                "position": [ball_cx, ball_cy],
                "color": ball_color,
            },
        )

        # --- String (line from cup center to ball center) ---
        # PsychoPy Line has start/end attributes not covered by
        # update_stimulus(), so we access the raw stimulus directly.
        if not scene.has_stimulus("__string"):
            scene.show(
                "__string",
                {
                    "type": "line",
                    "start": [cup_cx, cup_cy],
                    "end": [ball_cx, ball_cy],
                    "color": _STRING_COLOR,
                    "line_width": _STRING_WIDTH,
                },
            )
        else:
            string_stim = scene.get_stimulus("__string")
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
        scale = self._display_config.display_scale
        offset = self._display_config.display_offset
        bodies = field_state.get("bodies", {})
        for body_id, body_state in bodies.items():
            stim_id = f"__body_{body_id}"
            if scene.has_stimulus(stim_id):
                pos = body_state.get("position", [0, 0])
                angle_rad = body_state.get("angle", 0.0)
                scene.update(stim_id, {
                    "position": [
                        pos[0] * scale + offset[0],
                        pos[1] * scale + offset[1],
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

        effective_fullscr = cfg.fullscreen and not self._headless
        return visual_module.Window(
            size=list(cfg.resolution),
            fullscr=effective_fullscr,
            color=cfg.background_color,
            monitor=mon,
            units="cm",
            allowGUI=not effective_fullscr,
            winType="pyglet",
            checkTiming=False,
        )

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
