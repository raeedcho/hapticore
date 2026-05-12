"""DisplayProcess — multiprocessing.Process subclass for PsychoPy rendering.

PsychoPy is imported ONLY inside run() — never at module level.
Importing PsychoPy creates an OpenGL context and must own the main thread.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import multiprocessing.queues
import os
import signal
import sys
import time
from queue import Full
from typing import TYPE_CHECKING, Any

import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_EVENT, TOPIC_STATE, TrialEvent, serialize
from hapticore.core.messaging import drain_sub_messages
from hapticore.display._x11 import restore_pointer_focus

if TYPE_CHECKING:
    from psychopy.event import Mouse as _PsychoPyMouse
    from psychopy.visual import Window

    from hapticore.display.photodiode import PhotodiodePatch
    from hapticore.display.scene_manager import SceneManager

logger = logging.getLogger(__name__)


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
        restore_pointer_focus()

        # In mouse mode, create a PsychoPy Mouse bound to the window.
        mouse = None
        if self._mouse_queue is not None:
            from psychopy import event as psychopy_event

            mouse = psychopy_event.Mouse(win=win)
            win.mouseVisible = False  # Hide the default PsychoPy cursor; we'll render our own

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
            display_msgs = drain_sub_messages(display_sub)
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
            state_msgs = drain_sub_messages(state_sub)
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
                eff = scene.effective_scale
                offset = scene.effective_offset_cm
                x_m = (mx_cm - offset[0]) / eff
                y_m = (my_cm - offset[1]) / eff
                with contextlib.suppress(Full):
                    self._mouse_queue.put_nowait((x_m, y_m))
                scene.set_cursor_position([x_m, y_m])
            elif latest_state is not None:
                if interpolation_enabled:
                    dt = min(
                        time.monotonic() - state_receive_time,
                        self._MAX_INTERP_DT,
                    )
                    raw = self._interpolate_position(latest_state, dt)
                else:
                    raw = latest_state.get("position", [0.0, 0.0, 0.0])

                scene.set_cursor_position(raw[:2])

            # 2b. Update scene from field_state data
            if latest_state is not None:
                scene.update_from_field_state(latest_state)

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
        passed in meters-space directly to SceneManager, which converts
        internally to display cm.

        Returns the stim_id for successful ``"show"`` commands, ``None`` otherwise.
        """
        action = cmd.get("action")
        try:
            if action == "show":
                stim_id = cmd["stim_id"]
                params = cmd.get("params", {})  # pass meters-space directly
                scene.show(stim_id, params)
                return stim_id
            elif action == "hide":
                scene.hide(cmd.get("stim_id", ""))
            elif action == "clear":
                scene.clear()
            elif action == "update_scene":
                params = cmd.get("params", {})
                # Handle cursor visibility as a special key before
                # iterating over normal stimulus updates.
                cursor_cmd = params.pop("__cursor", None)
                if cursor_cmd is not None and "visible" in cursor_cmd:
                    scene.set_cursor_visible(cursor_cmd["visible"])
                for stim_id, stim_params in params.items():
                    scene.update(stim_id, stim_params)  # pass meters-space directly
            else:
                logger.warning("Unknown display action: %r", action)
        except Exception:
            logger.exception("Error handling display command: %r", cmd)
        return None

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
