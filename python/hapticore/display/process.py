"""DisplayProcess — multiprocessing.Process subclass for PsychoPy rendering.

PsychoPy is imported ONLY inside run() — never at module level.
Importing PsychoPy creates an OpenGL context and must own the main thread.
"""

from __future__ import annotations

import contextlib
import logging
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
