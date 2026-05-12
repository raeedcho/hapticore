"""WorkspaceMirrorProcess — control-room workspace mirror with haptic overlays.

PsychoPy is imported ONLY inside run() — never at module level.
Importing PsychoPy creates an OpenGL context and must own the main thread.
"""

from __future__ import annotations

import collections
import logging
import math
import multiprocessing
import multiprocessing.queues
import os
import signal
import sys
import time
from typing import TYPE_CHECKING, Any

import zmq

from hapticore.core.config import DashboardConfig, DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_STATE
from hapticore.core.messaging import drain_sub_messages

if TYPE_CHECKING:
    from psychopy.visual import Window

    from hapticore.display.scene_manager import SceneManager

logger = logging.getLogger(__name__)


class WorkspaceMirrorProcess(multiprocessing.Process):
    """PsychoPy window on the control-room screen mirroring the rig display.

    Subscribes to TOPIC_DISPLAY and TOPIC_STATE from the same ZMQ addresses
    the rig DisplayProcess uses. Renders identical stimuli via SceneManager
    (shared with DisplayProcess), plus diagnostic overlays (position trail,
    force arrow).

    Read-only subscriber — does not publish any messages.
    """

    # Sleep duration for headless mode throttle (~60 Hz).
    _HEADLESS_FRAME_SLEEP: float = 1.0 / 60.0

    def __init__(
        self,
        dashboard_config: DashboardConfig,
        display_config: DisplayConfig,
        zmq_config: ZMQConfig,
        *,
        ready_event: multiprocessing.Event | None = None,  # type: ignore[type-arg]
        headless: bool = False,
    ) -> None:
        super().__init__(name="WorkspaceMirrorProcess", daemon=True)
        self._dashboard_config = dashboard_config
        self._display_config = display_config
        self._zmq_config = zmq_config
        self._ready_event = ready_event
        self._headless = headless
        self._shutdown = multiprocessing.Event()

    def request_shutdown(self) -> None:
        """Signal the process to exit its frame loop and shut down."""
        self._shutdown.set()

    def run(self) -> None:
        """Entry point executed in the child process."""
        # Disable pyglet's shadow window on Linux (same reason as DisplayProcess).
        if sys.platform == "linux":
            os.environ["PYGLET_SHADOW_WINDOW"] = "0"

        from psychopy import visual  # noqa: F811 — import ONLY here

        from hapticore.display._x11 import restore_pointer_focus
        from hapticore.display.scene_manager import SceneManager

        signal.signal(signal.SIGINT, signal.SIG_IGN)

        win = self._create_window(visual)
        restore_pointer_focus()

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

        scene = SceneManager(win, self._display_config)

        # Signal readiness after sockets are created and subscribed.
        if self._ready_event is not None:
            self._ready_event.set()

        try:
            self._frame_loop(win, display_sub, state_sub, scene)
        finally:
            dropped = getattr(win, "nDroppedFrames", 0)
            if dropped:
                logger.warning("WorkspaceMirror: dropped %d frames", dropped)
            display_sub.close()
            state_sub.close()
            ctx.term()
            win.close()

    def _frame_loop(
        self,
        win: Window,
        display_sub: zmq.Socket[Any],
        state_sub: zmq.Socket[Any],
        scene: SceneManager,
    ) -> None:
        """Main rendering loop — one iteration per vsync frame."""
        latest_state: dict[str, Any] | None = None

        # Position trail: ring buffer of [x_m, y_m] pairs
        trail_maxlen = self._dashboard_config.trail_length
        trail: collections.deque[list[float]] = collections.deque(maxlen=trail_maxlen)

        # Pre-allocate overlay stimuli (lazy — created on first frame with state)
        trail_stims: list[Any] | None = None
        arrow_line: Any | None = None
        arrow_head: Any | None = None
        overlays_initialized = False

        while not self._shutdown.is_set():
            # 1. Drain display commands and dispatch to scene
            for cmd in drain_sub_messages(display_sub):
                self._handle_display_command(scene, cmd)

            # 2. Drain haptic state — keep only the latest
            state_msgs = drain_sub_messages(state_sub)
            if state_msgs:
                latest_state = state_msgs[-1]

            if latest_state is not None:
                pos = latest_state.get("position", [0.0, 0.0, 0.0])
                # 3a. Update cursor via SceneManager
                scene.set_cursor_position(pos[:2])
                # 3b. Update field-state visuals
                scene.update_from_field_state(latest_state)

                # 3c. Initialize overlay stimuli on first frame with data
                if not overlays_initialized:
                    trail_stims, arrow_line, arrow_head = self._init_overlays(win, scene)
                    overlays_initialized = True

                # 3d. Update position trail
                trail.append([pos[0], pos[1]])
                self._update_trail(trail, trail_stims, scene)

                # 3e. Update force arrow
                force = latest_state.get("force", [0.0, 0.0, 0.0])
                self._update_force_arrow(pos, force, arrow_line, arrow_head, scene)

            # 4. Draw scene stimuli
            scene.draw_all()

            # 5. Draw overlays on top
            if overlays_initialized:
                self._draw_overlays(trail_stims, arrow_line, arrow_head, latest_state)

            # 6. Flip
            win.flip()

            # Throttle headless mode (no vsync → flip returns immediately)
            if self._headless:
                time.sleep(self._HEADLESS_FRAME_SLEEP)

    def _init_overlays(
        self,
        win: Window,
        scene: SceneManager,
    ) -> tuple[list[Any] | None, Any | None, Any | None]:
        """Pre-allocate trail and force-arrow PsychoPy stimuli.

        Returns (trail_stims, arrow_line, arrow_head).
        All stimuli start hidden (opacity=0).
        """
        from psychopy import visual as psychopy_visual

        trail_length = self._dashboard_config.trail_length
        trail_color = self._dashboard_config.trail_color
        eff = scene.effective_scale
        trail_radius_cm = self._display_config.cursor_radius * 0.5 * eff

        # Trail circles
        trail_stims: list[Any] | None = None
        if trail_length > 0:
            trail_stims = []
            for _ in range(trail_length):
                c = psychopy_visual.Circle(
                    win,
                    radius=trail_radius_cm,
                    fillColor=trail_color,
                    lineColor=trail_color,
                    units="cm",
                    opacity=0.0,
                )
                trail_stims.append(c)

        # Force arrow: Line shaft + ShapeStim (triangle) arrowhead
        arrow_color = self._dashboard_config.force_arrow_color
        arrow_line = psychopy_visual.Line(
            win,
            start=(0, 0),
            end=(0, 0),
            lineColor=arrow_color,
            lineWidth=2,
            units="cm",
            opacity=0.0,
        )
        # Triangle arrowhead: small isoceles triangle pointing right (+X).
        # It will be rotated to the force direction each frame.
        head_size_cm = max(trail_radius_cm * 2.0, 0.2)
        arrow_head = psychopy_visual.ShapeStim(
            win,
            vertices=[
                [head_size_cm, 0],
                [-head_size_cm * 0.5, head_size_cm * 0.5],
                [-head_size_cm * 0.5, -head_size_cm * 0.5],
            ],
            fillColor=arrow_color,
            lineColor=arrow_color,
            units="cm",
            opacity=0.0,
        )

        return trail_stims, arrow_line, arrow_head

    def _update_trail(
        self,
        trail: collections.deque[list[float]],
        trail_stims: list[Any] | None,
        scene: SceneManager,
    ) -> None:
        """Update position trail circle positions and opacities."""
        if trail_stims is None:
            return

        eff = scene.effective_scale
        offset = scene.effective_offset_cm
        n = len(trail)
        trail_list = list(trail)  # oldest first

        for i, stim in enumerate(trail_stims):
            if i < n:
                pos_m = trail_list[i]
                pos_cm = [pos_m[0] * eff + offset[0], pos_m[1] * eff + offset[1]]
                stim.pos = pos_cm
                # Linear opacity: oldest = 1/n, newest = 1.0
                stim.opacity = (i + 1) / n
            else:
                stim.opacity = 0.0

    def _update_force_arrow(
        self,
        pos_m: list[float],
        force: list[float],
        arrow_line: Any | None,
        arrow_head: Any | None,
        scene: SceneManager,
    ) -> None:
        """Update force arrow geometry from current position and force."""
        if arrow_line is None or arrow_head is None:
            return

        fx = force[0]
        fy = force[1]
        eff = scene.effective_scale
        offset = scene.effective_offset_cm
        cursor_cm = [pos_m[0] * eff + offset[0], pos_m[1] * eff + offset[1]]

        arrow_length_cm = (
            (fx ** 2 + fy ** 2) ** 0.5
            * self._dashboard_config.force_arrow_scale
            * eff
        )

        if arrow_length_cm < 0.05:
            # Force too small — hide arrow
            arrow_line.opacity = 0.0
            arrow_head.opacity = 0.0
        else:
            angle = math.atan2(fy, fx)
            tip_cm = [
                cursor_cm[0] + math.cos(angle) * arrow_length_cm,
                cursor_cm[1] + math.sin(angle) * arrow_length_cm,
            ]
            arrow_line.start = tuple(cursor_cm)
            arrow_line.end = tuple(tip_cm)
            arrow_line.opacity = 1.0

            arrow_head.pos = tuple(tip_cm)
            # PsychoPy orientation is in degrees, measured counter-clockwise from +X.
            arrow_head.ori = -math.degrees(angle)  # negative: PsychoPy uses CW convention
            arrow_head.opacity = 1.0

    def _draw_overlays(
        self,
        trail_stims: list[Any] | None,
        arrow_line: Any | None,
        arrow_head: Any | None,
        latest_state: dict[str, Any] | None,
    ) -> None:
        """Draw overlay stimuli on top of the scene."""
        if trail_stims is not None:
            for stim in trail_stims:
                if stim.opacity > 0:
                    stim.draw()

        if latest_state is not None and arrow_line is not None and arrow_head is not None:
            if arrow_line.opacity > 0:
                arrow_line.draw()
            if arrow_head.opacity > 0:
                arrow_head.draw()

    def _handle_display_command(self, scene: SceneManager, cmd: dict[str, Any]) -> None:
        """Dispatch a single display command to the SceneManager.

        Simpler than DisplayProcess version — no timing event return value needed.
        """
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
                cursor_cmd = params.pop("__cursor", None)
                if cursor_cmd is not None and "visible" in cursor_cmd:
                    scene.set_cursor_visible(cursor_cmd["visible"])
                for stim_id, stim_params in params.items():
                    scene.update(stim_id, stim_params)
        except Exception:
            logger.exception("WorkspaceMirror: error handling display command: %r", cmd)

    def _create_window(self, visual_module: Any) -> Window:
        """Create a PsychoPy Window from the dashboard configuration."""
        from psychopy import monitors  # noqa: F811 — import ONLY here

        dash = self._dashboard_config
        disp = self._display_config

        mon = monitors.Monitor("hapticore_mirror")
        mon.setWidth(disp.monitor_width_cm)
        mon.setSizePix(list(dash.resolution))
        mon.setDistance(disp.monitor_distance_cm)

        view_scale: list[float] | None = None
        if dash.mirror_horizontal:
            view_scale = [-1.0, 1.0]

        return visual_module.Window(
            size=list(dash.resolution),
            fullscr=False,
            color=dash.background_color,
            monitor=mon,
            units="cm",
            allowGUI=False,
            winType="pyglet",
            checkTiming=False,
            screen=dash.screen,
            viewScale=view_scale,
        )
