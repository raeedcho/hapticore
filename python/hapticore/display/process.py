"""DisplayProcess — multiprocessing.Process subclass for PsychoPy rendering.

PsychoPy is imported ONLY inside run() — never at module level.
Importing PsychoPy creates an OpenGL context and must own the main thread.
"""

from __future__ import annotations

import logging
import multiprocessing
import signal
from typing import Any

import msgpack
import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_STATE

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

        try:
            # Placeholder frame loop — drain messages and flip.
            # Full rendering added in Phase 4B.
            while not self._shutdown.is_set():
                # TODO(4B): capture and dispatch drained display commands
                self._drain_messages(display_sub)
                # TODO(4B): capture and dispatch drained haptic state
                self._drain_messages(state_sub)
                win.flip()
        finally:
            display_sub.close()
            state_sub.close()
            ctx.term()
            win.close()

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
