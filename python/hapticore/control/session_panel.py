"""Session controls panel for the Hapticore Control Center."""

from __future__ import annotations

import importlib
import logging

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
    """Widget for managing session and trial lifecycle."""

    session_started = pyqtSignal()
    session_stopped = pyqtSignal()
    trials_started = pyqtSignal()
    trials_stopped = pyqtSignal()

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
        self._start_trials_btn = QPushButton("Start Trials")
        self._start_trials_btn.setEnabled(False)
        btn_row.addWidget(self._start_trials_btn)

        self._stop_block_btn = QPushButton("Stop After Block")
        self._stop_block_btn.setEnabled(False)
        btn_row.addWidget(self._stop_block_btn)

        self._stop_trial_btn = QPushButton("Stop After Trial")
        self._stop_trial_btn.setEnabled(False)
        btn_row.addWidget(self._stop_trial_btn)

        self._stop_session_btn = QPushButton("Stop Session")
        self._stop_session_btn.setEnabled(False)
        btn_row.addWidget(self._stop_session_btn)

        layout.addLayout(btn_row)

        # Connect buttons
        self._start_trials_btn.clicked.connect(self._on_start_trials)
        self._stop_block_btn.clicked.connect(self._on_stop_after_block)
        self._stop_trial_btn.clicked.connect(self._on_stop_after_trial)
        self._stop_session_btn.clicked.connect(self._on_stop_session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, config: ExperimentConfig) -> None:
        """Create the session infrastructure but do NOT start trials.

        Replicates the setup portion of ``_run()`` in ``cli/__init__.py``.
        """
        if self._session is not None:
            logger.warning("start_session() called while session already active — ignoring")
            return

        # Import the task class
        task_class_path = config.task.task_class
        if "." not in task_class_path:
            self._status_label.setText(
                f"Error: task_class must be a dotted path, got {task_class_path!r}"
            )
            return

        try:
            module_path, class_name = task_class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            task_cls = getattr(module, class_name)
            task: BaseTask = task_cls()
        except Exception as exc:  # noqa: BLE001
            self._status_label.setText(f"Error: {exc}")
            return

        session: SessionManager | None = None
        controller: TaskController | None = None
        try:
            session = SessionManager(config)
            session.start()
            self._session_id_label.setText(session.session_id or "")

            controller = TaskController(
                task=task,
                haptic=session.haptic,
                display=session.display,
                sync=session.sync,
                audio=session.audio,
                event_publisher=session.publisher,
                trial_manager=session.trial_manager,
                params=dict(config.task.params) if config.task.params else None,
                poll_rate_hz=1000.0,
                zmq_context=session.zmq_context,
                event_address=session.event_pub_address,
            )
            controller.setup()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error during session start")
            if controller is not None:
                try:
                    controller.teardown()
                except Exception:  # noqa: BLE001
                    logger.exception("Error during controller teardown in cleanup")
            if session is not None:
                try:
                    session.stop()
                except Exception:  # noqa: BLE001
                    logger.exception("Error during session stop in cleanup")
            self._session = None
            self._controller = None
            self._task = None
            self._status_label.setText(f"Error: {exc}")
            return

        self._session = session
        self._controller = controller
        self._task = task

        self._start_trials_btn.setEnabled(True)
        self._stop_block_btn.setEnabled(False)
        self._stop_trial_btn.setEnabled(False)
        self._stop_session_btn.setEnabled(True)
        self._status_label.setText("Session ready")
        self.session_started.emit()

    @property
    def session(self) -> SessionManager | None:
        """The active SessionManager, or None."""
        return self._session

    @property
    def task(self) -> BaseTask | None:
        """The active task instance, or None."""
        return self._task

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_start_trials(self) -> None:
        """Start (or restart) trials within the current session."""
        if self._controller is None:
            return
        self._controller.trial_manager.clear_stop_request()
        if not self._controller.start_first_trial():
            self._status_label.setText("No more trials to run")
            return
        self._tick_timer = QTimer(self)
        self._tick_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start(max(1, round(1000 / self._controller.poll_rate_hz)))
        self._start_trials_btn.setEnabled(False)
        self._stop_block_btn.setEnabled(True)
        self._stop_trial_btn.setEnabled(True)
        self._stop_session_btn.setEnabled(True)
        self._status_label.setText("Trials running")
        self.trials_started.emit()

    def _on_tick(self) -> None:
        """Called by QTimer at poll_rate_hz."""
        if self._controller is None:
            return
        try:
            still_running = self._controller.tick()
        except Exception:  # noqa: BLE001
            logger.exception("Error during tick — stopping session")
            self._on_stop_session()
            self._status_label.setText("Error: tick failed (see log)")
            return
        if not still_running:
            self._stop_trials("Complete")

    def _on_stop_after_block(self) -> None:
        if self._controller is not None:
            self._controller.trial_manager.request_stop(after="block")
            self._stop_block_btn.setEnabled(False)
            self._stop_trial_btn.setEnabled(False)
            self._status_label.setText("Stopping (after block)…")

    def _on_stop_after_trial(self) -> None:
        if self._controller is not None:
            self._controller.trial_manager.request_stop(after="trial")
            self._stop_block_btn.setEnabled(False)
            self._stop_trial_btn.setEnabled(False)
            self._status_label.setText("Stopping (after trial)…")

    def _stop_trials(self, reason: str) -> None:
        """Stop the tick timer and update UI. Session stays alive."""
        if self._tick_timer is not None:
            self._tick_timer.stop()
            self._tick_timer = None
        self._start_trials_btn.setEnabled(True)
        self._stop_block_btn.setEnabled(False)
        self._stop_trial_btn.setEnabled(False)
        self._stop_session_btn.setEnabled(True)
        self._status_label.setText(reason)
        self.trials_stopped.emit()

    def _on_stop_session(self) -> None:
        """Tear down everything: stop trials if running, then end the session."""
        # Stop tick timer if trials are running
        trials_were_running = self._tick_timer is not None
        if self._tick_timer is not None:
            self._tick_timer.stop()
            self._tick_timer = None

        # Stop recording if active (CC-C.3 may have started it)
        if self._session is not None and self._session.is_recording:
            try:
                self._session.stop_recording()
            except Exception:  # noqa: BLE001
                logger.exception("Error stopping recording during session stop")

        # Teardown controller
        if self._controller is not None:
            try:
                self._controller.teardown()
            except Exception:  # noqa: BLE001
                logger.exception("Error during controller teardown")

        # Stop session
        if self._session is not None:
            try:
                self._session.stop()
            except Exception:  # noqa: BLE001
                logger.exception("Error during session stop")

        # Clear all refs
        self._controller = None
        self._session = None
        self._task = None

        # Update UI
        self._start_trials_btn.setEnabled(False)
        self._stop_block_btn.setEnabled(False)
        self._stop_trial_btn.setEnabled(False)
        self._stop_session_btn.setEnabled(False)
        self._session_id_label.setText("")
        self._status_label.setText("Stopped")
        if trials_were_running:
            self.trials_stopped.emit()
        self.session_stopped.emit()
