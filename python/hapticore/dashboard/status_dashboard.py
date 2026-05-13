"""StatusDashboardProcess — Qt window showing real-time session status.

PyQt6 is imported ONLY inside run() — never at module level.
Importing PyQt6 creates a QApplication event loop and must own the child
process's main thread.
"""

from __future__ import annotations

import logging
import multiprocessing
import signal
from typing import Any

import zmq

from hapticore.core.config import DashboardConfig, ZMQConfig
from hapticore.core.messages import TOPIC_EVENT
from hapticore.core.messaging import drain_sub_messages

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure helper functions (no Qt dependency — importable and testable anywhere)
# ---------------------------------------------------------------------------

# Outcome → hex color mapping
_OUTCOME_COLORS: dict[str, str] = {
    "success": "#4CAF50",
    "spill": "#F44336",
    "failure": "#F44336",
    "timeout": "#FF9800",
    "abort": "#FF9800",
}
_OUTCOME_COLOR_FALLBACK = "#FFEB3B"  # yellow for unknown outcomes


def outcome_color(outcome: str) -> str:
    """Map an outcome string to a hex color string.

    Known outcomes:
    - ``"success"`` → green (#4CAF50)
    - ``"spill"`` / ``"failure"`` → red (#F44336)
    - ``"timeout"`` / ``"abort"`` → orange (#FF9800)
    - anything else → yellow (#FFEB3B)
    """
    return _OUTCOME_COLORS.get(outcome, _OUTCOME_COLOR_FALLBACK)


def block_success_rate_color(success_rate: float) -> tuple[int, int, int]:
    """Map a success rate (0.0–1.0) to an RGB tuple via green→red gradient.

    Uses HSV(hue, 1, 1) → RGB conversion:
    - 0.0 (0% success) → hue 0° → red   (255, 0, 0)
    - 0.5 (50% success) → hue 60° → yellow (255, 255, 0)
    - 1.0 (100% success) → hue 120° → green (0, 255, 0)

    Returns:
        (r, g, b) as integers in [0, 255].
    """
    # Clamp to [0, 1]
    rate = max(0.0, min(1.0, success_rate))
    hue_deg = rate * 120.0  # map [0, 1] → [0°, 120°]

    h = hue_deg / 60.0
    i = int(h) % 6
    f = h - int(h)
    # HSV with S=1, V=1
    v = 1.0
    p = 0.0       # v * (1 - s) = 0
    q = 1.0 - f   # v * (1 - s*f)
    t = f         # v * (1 - s*(1-f))

    rgb_map = [
        (v, t, p),  # i=0
        (q, v, p),  # i=1
        (p, v, t),  # i=2
        (p, q, v),  # i=3
        (t, p, v),  # i=4
        (v, p, q),  # i=5
    ]
    r, g, b = rgb_map[i]
    return (round(r * 255), round(g * 255), round(b * 255))


def compute_block_index(trial_number: int, block_size: int) -> int:
    """Return the 0-based block index for a given 0-based trial number."""
    return trial_number // block_size


def compute_trial_within_block(trial_number: int, block_size: int) -> int:
    """Return the 0-based trial index within its block."""
    return trial_number % block_size


def format_condition(condition: dict[str, Any]) -> str:
    """Format a condition dict as a human-readable string ``k=v, k=v``."""
    if not condition:
        return "(none)"
    return ", ".join(f"{k}={v}" for k, v in condition.items())


# ---------------------------------------------------------------------------
# StatusDashboardProcess
# ---------------------------------------------------------------------------

_DOT_DIAMETER = 16
_DOT_SPACING = 4
_BLOCK_DOT_DIAMETER = 20

_COLOR_CURRENT_STATE = "#00BCD4"   # cyan
_COLOR_DEFAULT_STATE = "#424242"   # dark gray
_COLOR_STATE_TEXT = "#FFFFFF"      # white text on state boxes
_COLOR_UPCOMING_DOT = "#9E9E9E"    # gray border for upcoming dots
_COLOR_INPROGRESS = "#FFFFFF"      # white fill for in-progress trial dot
_COLOR_CURRENT_BLOCK_BORDER = "#00BCD4"  # cyan ring for current block dot


class StatusDashboardProcess(multiprocessing.Process):
    """Qt window showing real-time session status.

    Displays:
    - State machine pipeline with current state highlighted
    - Trial dot row (current block) with per-trial outcome colors and tooltips
    - Block dot row (session) with per-block success-rate gradient colors

    Subscribes to ``TOPIC_EVENT`` for ``StateTransition`` and ``TrialEvent``
    messages.  Read-only subscriber — does not publish any messages.

    PyQt6 is imported inside :meth:`run` only.
    """

    def __init__(
        self,
        dashboard_config: DashboardConfig,
        zmq_config: ZMQConfig,
        task_states: list[str],
        task_transitions: list[dict[str, Any]],
        task_initial_state: str,
        block_size: int,
        num_blocks: int | None,
        num_conditions: int,
        *,
        ready_event: multiprocessing.Event | None = None,  # type: ignore[type-arg]
    ) -> None:
        super().__init__(name="StatusDashboardProcess", daemon=True)
        self._dashboard_config = dashboard_config
        self._zmq_config = zmq_config
        self._task_states = list(task_states)
        self._task_transitions = list(task_transitions)
        self._task_initial_state = task_initial_state
        self._block_size = block_size
        self._num_blocks = num_blocks
        self._num_conditions = num_conditions
        self._ready_event = ready_event
        self._shutdown: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]

    def request_shutdown(self) -> None:
        """Signal the process to exit its event loop and shut down."""
        self._shutdown.set()

    # ------------------------------------------------------------------
    # Child-process entry point
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: PLR0912, PLR0915 — long but intentional (Qt loop)
        """Entry point executed in the child process.

        All PyQt6 imports live here.  Creates the QApplication and UI, then
        runs a custom event loop interleaving Qt event processing with ZMQ
        message polling via a QTimer.
        """
        from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: PLC0415

        signal.signal(signal.SIGINT, signal.SIG_IGN)

        app = QtWidgets.QApplication([])

        # ---- ZMQ subscriber ------------------------------------------------
        ctx = zmq.Context()
        event_sub = ctx.socket(zmq.SUB)
        event_sub.setsockopt(zmq.LINGER, 0)
        event_sub.connect(self._zmq_config.event_pub_address)
        event_sub.subscribe(TOPIC_EVENT)
        event_sub.setsockopt(zmq.RCVHWM, 100)

        # ---- Build UI ------------------------------------------------------
        window = QtWidgets.QWidget()
        window.setWindowTitle("Hapticore — Session Status")
        window.setStyleSheet("background-color: #212121; color: #FFFFFF;")
        root_layout = QtWidgets.QVBoxLayout(window)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # -- State machine pipeline (top section) --
        state_scene = QtWidgets.QGraphicsScene()
        state_view = QtWidgets.QGraphicsView(state_scene)
        state_view.setFixedHeight(80)
        state_view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        state_view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        state_view.setStyleSheet("background-color: #212121; border: none;")
        root_layout.addWidget(state_view)

        # Build state boxes
        box_w, box_h = 90, 36
        gap = 22  # gap between boxes (for arrow)
        state_items: dict[str, Any] = {}
        text_items: dict[str, Any] = {}
        x = 4
        for idx, state_name in enumerate(self._task_states):
            # Rounded-rect path
            path = QtGui.QPainterPath()
            path.addRoundedRect(x, 4, box_w, box_h, 6, 6)
            item = QtWidgets.QGraphicsPathItem(path)
            if state_name == self._task_initial_state:
                item.setBrush(QtGui.QBrush(QtGui.QColor(_COLOR_CURRENT_STATE)))
            else:
                item.setBrush(QtGui.QBrush(QtGui.QColor(_COLOR_DEFAULT_STATE)))
            item.setPen(QtGui.QPen(QtGui.QColor("#616161"), 1))
            state_scene.addItem(item)
            state_items[state_name] = item

            # Label
            label = QtWidgets.QGraphicsSimpleTextItem(state_name)
            label.setBrush(QtGui.QBrush(QtGui.QColor(_COLOR_STATE_TEXT)))
            font = QtGui.QFont("monospace", 7)
            label.setFont(font)
            br = label.boundingRect()
            label.setPos(
                x + (box_w - br.width()) / 2,
                4 + (box_h - br.height()) / 2,
            )
            state_scene.addItem(label)
            text_items[state_name] = label

            # Arrow to next state
            if idx < len(self._task_states) - 1:
                arrow_x = x + box_w + gap // 2 - 4
                arr = QtWidgets.QGraphicsSimpleTextItem("→")
                arr.setBrush(QtGui.QBrush(QtGui.QColor("#9E9E9E")))
                arr.setPos(arrow_x, 4 + (box_h - arr.boundingRect().height()) / 2)
                state_scene.addItem(arr)

            x += box_w + gap

        state_scene.setSceneRect(0, 0, x - gap + 4, box_h + 8)

        # -- Block dots row (middle section) --
        block_row = QtWidgets.QHBoxLayout()
        num_blocks_str = str(self._num_blocks) if self._num_blocks is not None else "∞"
        block_label = QtWidgets.QLabel(f"Block 1 / {num_blocks_str}")
        block_label.setFixedWidth(110)
        block_label.setStyleSheet("color: #FFFFFF; font-size: 11px;")
        block_row.addWidget(block_label)

        block_scene = QtWidgets.QGraphicsScene()
        block_view = QtWidgets.QGraphicsView(block_scene)
        block_view.setFixedHeight(40)
        block_view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        block_view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        block_view.setStyleSheet("background-color: #212121; border: none;")
        block_row.addWidget(block_view)
        root_layout.addLayout(block_row)

        # -- Trial dots row (bottom section) --
        trial_row = QtWidgets.QHBoxLayout()
        trial_label = QtWidgets.QLabel(f"Trial 1 / {self._block_size}")
        trial_label.setFixedWidth(110)
        trial_label.setStyleSheet("color: #FFFFFF; font-size: 11px;")
        trial_row.addWidget(trial_label)

        trial_scene = QtWidgets.QGraphicsScene()
        trial_view = QtWidgets.QGraphicsView(trial_scene)
        trial_view.setFixedHeight(40)
        trial_view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        trial_view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        trial_view.setStyleSheet("background-color: #212121; border: none;")
        trial_row.addWidget(trial_view)
        root_layout.addLayout(trial_row)

        # Outcome label
        outcome_label = QtWidgets.QLabel("Last outcome: —")
        outcome_label.setStyleSheet("color: #9E9E9E; font-size: 11px; padding-left: 4px;")
        root_layout.addWidget(outcome_label)

        # ---- Helper to create a fresh set of trial dots --------------------
        d = _DOT_DIAMETER
        sp = _DOT_SPACING

        def make_trial_dots(block_idx: int) -> list[Any]:
            trial_scene.clear()
            dots: list[Any] = []
            trial_in_block_str = f"{self._block_size}"
            block_num_str = (
                str(self._num_blocks) if self._num_blocks is not None else "∞"
            )
            for i in range(self._block_size):
                item = QtWidgets.QGraphicsEllipseItem(
                    i * (d + sp), 2, d, d,
                )
                item.setBrush(QtGui.QBrush(QtCore.Qt.GlobalColor.transparent))
                pen = QtGui.QPen(QtGui.QColor(_COLOR_UPCOMING_DOT), 1.5)
                item.setPen(pen)
                block_label_str = (
                    f"Block {block_idx + 1} / {block_num_str}"
                )
                item.setToolTip(
                    f"Trial {i + 1} / {trial_in_block_str} ({block_label_str})\n"
                    "Pending"
                )
                item.setAcceptHoverEvents(True)
                trial_scene.addItem(item)
                dots.append(item)
            trial_scene.setSceneRect(
                0, 0, self._block_size * (d + sp) - sp + 4, d + 8,
            )
            return dots

        # ---- Helper to create block dots (initial set) ---------------------
        bd = _BLOCK_DOT_DIAMETER
        block_items: list[Any] = []

        def make_block_dots(num: int) -> None:
            block_scene.clear()
            del block_items[:]
            total_str = str(self._num_blocks) if self._num_blocks is not None else "∞"
            for i in range(num):
                item = QtWidgets.QGraphicsEllipseItem(
                    i * (bd + sp), 2, bd, bd,
                )
                item.setBrush(QtGui.QBrush(QtCore.Qt.GlobalColor.transparent))
                pen = QtGui.QPen(QtGui.QColor(_COLOR_UPCOMING_DOT), 1.5)
                item.setPen(pen)
                item.setToolTip(
                    f"Block {i + 1} / {total_str}\nPending"
                )
                item.setAcceptHoverEvents(True)
                block_scene.addItem(item)
                block_items.append(item)
            block_scene.setSceneRect(
                0, 0, max(1, num) * (bd + sp) - sp + 4, bd + 8,
            )

        def add_block_dot(block_idx: int) -> None:
            """Add one new block dot for open-ended sessions."""
            i = block_idx
            item = QtWidgets.QGraphicsEllipseItem(
                i * (bd + sp), 2, bd, bd,
            )
            item.setBrush(QtGui.QBrush(QtCore.Qt.GlobalColor.transparent))
            pen = QtGui.QPen(QtGui.QColor(_COLOR_UPCOMING_DOT), 1.5)
            item.setPen(pen)
            item.setToolTip(f"Block {i + 1} / ∞\nPending")
            item.setAcceptHoverEvents(True)
            block_scene.addItem(item)
            block_items.append(item)
            block_scene.setSceneRect(
                0, 0, len(block_items) * (bd + sp) - sp + 4, bd + 8,
            )

        # Initialize block and trial dots
        if self._num_blocks is not None:
            make_block_dots(self._num_blocks)
        else:
            # Open-ended: start with one dot for block 0 and grow
            add_block_dot(0)

        trial_items: list[Any] = make_trial_dots(0)

        window.resize(900, 220)
        window.show()

        if self._ready_event is not None:
            self._ready_event.set()

        # ---- Session state -------------------------------------------------
        current_state = [self._task_initial_state]
        current_block_index = [0]
        # Per-block outcome counts: list of dicts {"success": N, ...}
        block_outcome_counts: list[dict[str, int]] = [{}]
        # Track completed trials per block to detect block completion
        block_completed_trials: list[int] = [0]

        # Mark block 0 as in-progress (cyan ring)
        if block_items:
            block_items[0].setPen(
                QtGui.QPen(QtGui.QColor(_COLOR_CURRENT_BLOCK_BORDER), 2.5)
            )
            block_label.setText(
                f"Block 1 / {str(self._num_blocks) if self._num_blocks is not None else '∞'}"
            )

        # ---- ZMQ poll callback ---------------------------------------------
        def poll_zmq() -> None:  # noqa: PLR0912 — event-dispatch fan-out
            nonlocal trial_items

            for msg in drain_sub_messages(event_sub):
                msg_type = msg.get("__msg_type__")

                if msg_type == "StateTransition":
                    new_state: str = msg.get("new_state", "")
                    trial_num: int = msg.get("trial_number", -1)

                    # Update state-machine pipeline highlighting
                    for sname, sitem in state_items.items():
                        if sname == new_state:
                            sitem.setBrush(
                                QtGui.QBrush(QtGui.QColor(_COLOR_CURRENT_STATE))
                            )
                        else:
                            sitem.setBrush(
                                QtGui.QBrush(QtGui.QColor(_COLOR_DEFAULT_STATE))
                            )
                    current_state[0] = new_state

                    if trial_num < 0:
                        continue

                    # Detect block transition
                    new_block = compute_block_index(trial_num, self._block_size)
                    if new_block != current_block_index[0]:
                        # New block started — ensure we have enough dots/counts
                        while len(block_outcome_counts) <= new_block:
                            block_outcome_counts.append({})
                            block_completed_trials.append(0)
                            if self._num_blocks is None and len(block_items) <= new_block:
                                add_block_dot(len(block_items))

                        # Mark new block with cyan ring
                        if new_block < len(block_items):
                            old_idx = current_block_index[0]
                            if old_idx < len(block_items):
                                # Restore old block dot border
                                old_pen = QtGui.QPen(QtGui.QColor(_COLOR_UPCOMING_DOT), 1.5)
                                block_items[old_idx].setPen(old_pen)
                            block_items[new_block].setPen(
                                QtGui.QPen(QtGui.QColor(_COLOR_CURRENT_BLOCK_BORDER), 2.5)
                            )

                        current_block_index[0] = new_block
                        trial_items = make_trial_dots(new_block)
                        num_blocks_str2 = (
                            str(self._num_blocks)
                            if self._num_blocks is not None else "∞"
                        )
                        block_label.setText(
                            f"Block {new_block + 1} / {num_blocks_str2}"
                        )

                    # Update in-progress trial dot
                    tib = compute_trial_within_block(trial_num, self._block_size)
                    if tib < len(trial_items):
                        item = trial_items[tib]
                        # Only mark as in-progress if not yet completed (no solid fill)
                        if item.brush().style() == QtCore.Qt.BrushStyle.NoBrush or \
                                item.brush().color() == QtGui.QColor(
                                    QtCore.Qt.GlobalColor.transparent
                                ):
                            item.setBrush(
                                QtGui.QBrush(QtGui.QColor(_COLOR_INPROGRESS))
                            )
                            item.setPen(
                                QtGui.QPen(QtGui.QColor(_COLOR_CURRENT_STATE), 2)
                            )
                            num_blocks_str3 = (
                                str(self._num_blocks)
                                if self._num_blocks is not None else "∞"
                            )
                            item.setToolTip(
                                f"Trial {tib + 1} / {self._block_size} "
                                f"(Block {compute_block_index(trial_num, self._block_size) + 1}"
                                f" / {num_blocks_str3})\n"
                                "In progress..."
                            )
                    trial_label.setText(
                        f"Trial {tib + 1} / {self._block_size}"
                    )

                elif msg_type == "TrialEvent":
                    event_name: str = msg.get("event_name", "")
                    if event_name != "trial_complete":
                        continue

                    trial_num = msg.get("trial_number", -1)
                    if trial_num < 0:
                        continue

                    data: dict[str, Any] = msg.get("data", {})
                    out: str = data.get("outcome", "unknown")
                    condition: dict[str, Any] = data.get("condition", {})

                    tib = compute_trial_within_block(trial_num, self._block_size)
                    blk = compute_block_index(trial_num, self._block_size)

                    # Extend counts lists if needed
                    while len(block_outcome_counts) <= blk:
                        block_outcome_counts.append({})
                        block_completed_trials.append(0)
                        if self._num_blocks is None and len(block_items) <= blk:
                            add_block_dot(len(block_items))

                    # Fill trial dot
                    if tib < len(trial_items) and blk == current_block_index[0]:
                        hex_color = outcome_color(out)
                        item = trial_items[tib]
                        item.setBrush(QtGui.QBrush(QtGui.QColor(hex_color)))
                        item.setPen(
                            QtGui.QPen(QtGui.QColor(hex_color).darker(130), 1)
                        )
                        num_blocks_str4 = (
                            str(self._num_blocks)
                            if self._num_blocks is not None else "∞"
                        )
                        item.setToolTip(
                            f"Trial {tib + 1} / {self._block_size}"
                            f" (Block {blk + 1} / {num_blocks_str4})\n"
                            f"Condition: {format_condition(condition)}\n"
                            f"Outcome: {out}"
                        )

                    # Update block outcome counts
                    counts = block_outcome_counts[blk]
                    counts[out] = counts.get(out, 0) + 1
                    block_completed_trials[blk] += 1

                    # Outcome label
                    color_map = {
                        "success": "color: #4CAF50;",
                        "spill": "color: #F44336;",
                        "failure": "color: #F44336;",
                        "timeout": "color: #FF9800;",
                        "abort": "color: #FF9800;",
                    }
                    style = color_map.get(out, "color: #FFEB3B;")
                    outcome_label.setText(f"Last outcome: {out}")
                    outcome_label.setStyleSheet(f"{style} font-size: 11px; padding-left: 4px;")

                    # Update block dot
                    if blk < len(block_items):
                        completed = block_completed_trials[blk]
                        total_in_block = self._block_size
                        success_count = counts.get("success", 0)
                        rate = success_count / completed if completed > 0 else 0.0
                        r, g, b = block_success_rate_color(rate)
                        block_items[blk].setBrush(
                            QtGui.QBrush(QtGui.QColor(r, g, b))
                        )

                        # Tooltip for block dot
                        outcome_summary = ", ".join(
                            f"{k}: {v}" for k, v in sorted(counts.items())
                        )
                        num_blocks_str5 = (
                            str(self._num_blocks)
                            if self._num_blocks is not None else "∞"
                        )
                        if completed >= total_in_block:
                            pct = round(success_count / total_in_block * 100)
                            block_items[blk].setToolTip(
                                f"Block {blk + 1} / {num_blocks_str5}\n"
                                f"{success_count} / {total_in_block} success ({pct}%)\n"
                                f"Outcomes: {outcome_summary}"
                            )
                        else:
                            block_items[blk].setToolTip(
                                f"Block {blk + 1} / {num_blocks_str5} (in progress)\n"
                                f"{completed} / {total_in_block} completed so far"
                            )

        # ---- Main event loop -----------------------------------------------
        timer = QtCore.QTimer()
        timer.setInterval(50)  # 20 Hz poll
        timer.timeout.connect(poll_zmq)
        timer.start()

        while not self._shutdown.is_set():
            app.processEvents()
            QtCore.QThread.msleep(10)

        timer.stop()
        event_sub.close()
        ctx.term()
        window.close()
        app.quit()
