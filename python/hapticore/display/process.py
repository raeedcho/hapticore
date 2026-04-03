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
from typing import Any

import msgpack
import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_EVENT, TOPIC_STATE, TrialEvent, serialize

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

        try:
            self._frame_loop(win, display_sub, state_sub, timing_pub, scene)
        finally:
            dropped = getattr(win, "nDroppedFrames", 0)
            if dropped:
                logger.warning("Dropped %d frames during display loop", dropped)
            display_sub.close()
            state_sub.close()
            timing_pub.close()
            ctx.term()
            win.close()

    def _frame_loop(
        self,
        win: Any,
        display_sub: zmq.Socket[Any],
        state_sub: zmq.Socket[Any],
        timing_pub: zmq.Socket[Any],
        scene: Any,
    ) -> None:
        """Main rendering loop — one iteration per vsync frame."""
        while not self._shutdown.is_set():
            # 1. Drain display commands and dispatch
            display_msgs = self._drain_messages(display_sub)
            shown_stim_ids: list[str] = []
            command_timestamp: float | None = None
            for cmd in display_msgs:
                action = cmd.get("action")
                if action == "show":
                    stim_id = cmd.get("stim_id", "")
                    shown_stim_ids.append(stim_id)
                    if command_timestamp is None:
                        command_timestamp = cmd.get("timestamp", time.monotonic())
                self._handle_display_command(scene, cmd)

            # 2. Drain haptic state — keep only the latest
            state_msgs = self._drain_messages(state_sub)
            if state_msgs:
                latest_state = state_msgs[-1]
                position = latest_state.get("position", [0.0, 0.0, 0.0])
                # X, Y only (ignore Z)
                scene.set_cursor_position([position[0], position[1]])

            # 3. Draw all stimuli
            scene.draw_all()

            # 4. Capture flip timestamp via callOnFlip
            flip_time: list[float] = []
            win.callOnFlip(lambda ft=flip_time: ft.append(time.monotonic()))

            # 5. Flip — blocks until vsync
            win.flip()

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
    def _handle_display_command(scene: Any, cmd: dict[str, Any]) -> None:
        """Dispatch a single display command to the SceneManager."""
        action = cmd.get("action")
        try:
            if action == "show":
                scene.show(cmd["stim_id"], cmd.get("params", {}))
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

    def _create_window(self, visual_module: Any) -> Any:
        """Create a PsychoPy Window from the display configuration."""
        cfg = self._display_config
        effective_fullscr = cfg.fullscreen and not self._headless
        return visual_module.Window(
            size=list(cfg.resolution),
            fullscr=effective_fullscr,
            color=cfg.background_color,
            units="m",
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
