"""Session controls panel for the Hapticore Control Center.

Manages the session lifecycle (start, tick via QTimer, stop) and
exposes signals so the main window can enable/disable other panels.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hapticore.core.config import ExperimentConfig
from hapticore.session import SessionManager
from hapticore.tasks.base import BaseTask
from hapticore.tasks.controller import TaskController

logger = logging.getLogger(__name__)


class SessionPanel(QWidget):
    """Widget that manages the session lifecycle for the Control Center.

    Signals:
        session_started: Emitted after a session starts successfully.
        session_stopped: Emitted after session teardown completes.
    """

    session_started = pyqtSignal()
    session_stopped = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._session: SessionManager | None = None
        self._controller: TaskController | None = None
        self._task: BaseTask | None = None
        self._tick_timer: QTimer | None = None

        layout = QVBoxLayout(self)

        # Status row
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self._status_label = QLabel("No session")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        # Session info row
        session_row = QHBoxLayout()
        session_row.addWidget(QLabel("Session:"))
        self._session_id_label = QLabel("")
        session_row.addWidget(self._session_id_label)
        session_row.addStretch()
        layout.addLayout(session_row)

        # Button row
        btn_row = QHBoxLayout()
        self._stop_block_btn = QPushButton("Stop After Block")
        self._stop_block_btn.setEnabled(False)
        self._stop_block_btn.clicked.connect(self._on_stop_after_block)
        btn_row.addWidget(self._stop_block_btn)

        self._stop_trial_btn = QPushButton("Stop After Trial")
        self._stop_trial_btn.setEnabled(False)
        self._stop_trial_btn.clicked.connect(self._on_stop_after_trial)
        btn_row.addWidget(self._stop_trial_btn)

        self._stop_now_btn = QPushButton("Stop Now")
        self._stop_now_btn.setEnabled(False)
        self._stop_now_btn.clicked.connect(self._on_stop_now)
        btn_row.addWidget(self._stop_now_btn)

        layout.addLayout(btn_row)

    # -- Properties ---------------------------------------------------------

    @property
    def session(self) -> SessionManager | None:
        """The active SessionManager, or None if no session is running."""
        return self._session

    @property
    def task(self) -> BaseTask | None:
        """The active task instance, or None if no session is running."""
        return self._task

    # -- Public methods -----------------------------------------------------

    def start_session(self, config: ExperimentConfig) -> None:
        """Launch a session for the given config.

        Imports the task class, creates SessionManager and TaskController,
        calls setup() and start_first_trial(), then starts a QTimer that
        drives tick() at poll_rate_hz.

        On any error, the status label is updated and no signals are emitted.
        """
        # Import the task class
        task_class_path = config.task.task_class
        if "." not in task_class_path:
            self._status_label.setText(
                f"Error: task_class must be a dotted path, got '{task_class_path}'"
            )
            return
        module_path, class_name = task_class_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            task_cls = getattr(module, class_name)
            task = task_cls()
        except Exception as exc:
            self._status_label.setText(f"Error: {exc}")
            return

        session: SessionManager | None = None
        controller: TaskController | None = None
        try:
            session = SessionManager(config)
            session.start()

            self._session_id_label.setText(session.session_id)

            params: dict[str, Any] | None = config.task.params or None
            controller = TaskController(
                task=task,
                haptic=session.haptic,
                display=session.display,
                sync=session.sync,
                event_publisher=session.publisher,
                trial_manager=session.trial_manager,
                params=params,
                poll_rate_hz=100.0,
                zmq_context=session.zmq_context,
                event_address=session.event_pub_address,
            )
            controller.setup()
            if not controller.start_first_trial():
                raise RuntimeError("No trials to run - session aborted")
        except Exception as exc:
            logger.exception("Error starting session")
            if controller is not None:
                try:
                    controller.teardown()
                except Exception:
                    logger.exception("Error during controller teardown after start failure")
            if session is not None:
                try:
                    session.stop()
                except Exception:
                    logger.exception("Error during session stop after start failure")
            self._session = None
            self._controller = None
            self._task = None
            self._session_id_label.setText("")
            self._status_label.setText(f"Error: {exc}")
            return

        self._session = session
        self._controller = controller
        self._task = task

        # Start the tick timer
        self._tick_timer = QTimer(self)
        self._tick_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start(int(1000 / controller.poll_rate_hz))

        self._set_stops_enabled(True)
        self._status_label.setText("Running")
        self.session_started.emit()

    # -- Private slots ------------------------------------------------------

    def _on_tick(self) -> None:
        if self._controller is None:
            return
        still_running = self._controller.tick()
        if not still_running:
            self._finish_session("Complete")

    def _on_stop_after_block(self) -> None:
        if self._controller is not None:
            self._controller.trial_manager.request_stop(after="block")
            self._set_stops_enabled(False)
            self._status_label.setText("Stopping (after block)...")

    def _on_stop_after_trial(self) -> None:
        if self._controller is not None:
            self._controller.trial_manager.request_stop(after="trial")
            self._set_stops_enabled(False)
            self._status_label.setText("Stopping (after trial)...")

    def _on_stop_now(self) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._set_stops_enabled(False)
            self._status_label.setText("Stopping...")

    # -- Internal helpers ---------------------------------------------------

    def _finish_session(self, reason: str) -> None:
        """Teardown the session and emit session_stopped.

        Args:
            reason: Status text to display (e.g. "Complete" or "Stopped").
        """
        if self._tick_timer is not None:
            self._tick_timer.stop()
            self._tick_timer = None

        if self._session is not None and self._session.is_recording:
            try:
                self._session.stop_recording()
            except Exception:
                logger.exception("Error stopping recording during session finish")

        if self._controller is not None:
            try:
                self._controller.teardown()
            except Exception:
                logger.exception("Error during controller teardown")
        self._controller = None

        if self._session is not None:
            try:
                self._session.stop()
            except Exception:
                logger.exception("Error during session stop")

        self._set_stops_enabled(False)
        self._status_label.setText(reason)
        self.session_stopped.emit()

    def _set_stops_enabled(self, enabled: bool) -> None:
        """Enable or disable all three stop buttons."""
        self._stop_block_btn.setEnabled(enabled)
        self._stop_trial_btn.setEnabled(enabled)
        self._stop_now_btn.setEnabled(enabled)
