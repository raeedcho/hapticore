"""Recording segment panel for the Hapticore Control Center."""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hapticore.session import SessionManager
from hapticore.tasks.base import BaseTask

logger = logging.getLogger(__name__)


class RecordingPanel(QWidget):
    """Widget for managing recording segment lifecycle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._session: SessionManager | None = None
        self._task: BaseTask | None = None
        self._trials_running: bool = False

        layout = QVBoxLayout(self)

        # Warning banner
        self._warning_label = QLabel("⚠ Trials running without recording")
        self._warning_label.setStyleSheet(
            "background-color: #FFA500; color: black; padding: 4px; font-weight: bold;"
        )
        self._warning_label.setVisible(False)
        layout.addWidget(self._warning_label)

        # Segment label row
        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Segment label:"))
        self._label_input = QLineEdit()
        self._label_input.setPlaceholderText("(auto: seg-001, seg-002, …)")
        self._label_input.setEnabled(False)
        label_row.addWidget(self._label_input)
        layout.addLayout(label_row)

        # Status row
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self._status_label = QLabel("Not recording")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        # Segment history
        layout.addWidget(QLabel("Segments:"))
        self._segment_list = QListWidget()
        layout.addWidget(self._segment_list)

        # Button row
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Recording")
        self._start_btn.setEnabled(False)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop Recording")
        self._stop_btn.setEnabled(False)
        btn_row.addWidget(self._stop_btn)

        layout.addLayout(btn_row)

        # Connect buttons
        self._start_btn.clicked.connect(self._on_start_recording)
        self._stop_btn.clicked.connect(self._on_stop_recording)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_session(
        self, session: SessionManager | None, task: BaseTask | None,
    ) -> None:
        """Bind or unbind the active session and task."""
        self._session = session
        self._task = task
        self._trials_running = False
        self._segment_list.clear()
        self._label_input.clear()
        self._status_label.setText("Not recording")
        if session is not None:
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._label_input.setEnabled(True)
        else:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._label_input.setEnabled(False)
        self._warning_label.setVisible(False)

    def set_trials_running(self, running: bool) -> None:
        """Update the trials-running state for the warning banner."""
        self._trials_running = running
        self._update_warning()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_warning(self) -> None:
        """Show warning when trials are running without recording."""
        show = (
            self._trials_running
            and self._session is not None
            and not self._session.is_recording
        )
        self._warning_label.setVisible(show)

    def _on_start_recording(self) -> None:
        """Start a new recording segment."""
        if self._session is None:
            return
        label = self._label_input.text().strip() or None
        active_params: dict[str, Any] | None = (
            dict(self._task.params) if self._task is not None else None
        )
        try:
            self._session.start_recording(
                segment_label=label, active_params=active_params,
            )
        except (RuntimeError, ValueError, NotImplementedError) as exc:
            self._status_label.setText(f"Error: {exc}")
            return
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._label_input.setEnabled(False)
        segment_label = self._session.current_segment_label or "unknown"
        self._status_label.setText(f"Recording: {segment_label}")
        self._update_warning()

    def _on_stop_recording(self) -> None:
        """Stop the current recording segment."""
        if self._session is None:
            return
        try:
            self._session.stop_recording()
        except Exception:
            logger.exception("Error stopping recording")
            self._status_label.setText("Error: failed to stop recording (see log)")
            return
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._label_input.setEnabled(True)
        self._label_input.clear()
        self._status_label.setText("Not recording")
        self._update_segment_list()
        self._update_warning()

    def _update_segment_list(self) -> None:
        """Rebuild the segment history list from session metadata."""
        self._segment_list.clear()
        if self._session is None:
            return
        for seg in self._session.segments:
            trial_range = seg.get("trial_range", [])
            label = seg.get("label", "?")
            if len(trial_range) == 2:
                text = f"{label} (trials {trial_range[0]}–{trial_range[1]})"
            else:
                text = label
            self._segment_list.addItem(text)
