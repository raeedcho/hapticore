"""Main window and entry point for the Hapticore Control Center."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QLabel,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hapticore.control.config_panel import ConfigPanel
from hapticore.control.recording_panel import RecordingPanel
from hapticore.control.session_panel import SessionPanel
from hapticore.core.config import ExperimentConfig


class ControlCenterWindow(QMainWindow):
    """Main window for the Hapticore Control Center."""

    def __init__(self, configs_root: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Hapticore Control Center")

        # Scroll area as central widget
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)

        container = QWidget()
        layout = QVBoxLayout(container)

        # Configuration panel
        config_box = QGroupBox("Configuration")
        config_box_layout = QVBoxLayout(config_box)
        self.config_panel = ConfigPanel(configs_root=configs_root)
        config_box_layout.addWidget(self.config_panel)
        layout.addWidget(config_box)

        # Session Controls panel
        self.session_group = QGroupBox("Session Controls")
        session_layout = QVBoxLayout(self.session_group)
        self.session_panel = SessionPanel()
        session_layout.addWidget(self.session_panel)
        layout.addWidget(self.session_group)

        # Recording panel
        self.recording_group = QGroupBox("Recording")
        self.recording_group.setEnabled(False)
        recording_layout = QVBoxLayout(self.recording_group)
        self.recording_panel = RecordingPanel()
        recording_layout.addWidget(self.recording_panel)
        layout.addWidget(self.recording_group)

        # Parameters placeholder
        self.params_group = QGroupBox("Parameters")
        self.params_group.setEnabled(False)
        params_layout = QVBoxLayout(self.params_group)
        params_layout.addWidget(QLabel("Start a session to enable parameters."))
        layout.addWidget(self.params_group)

        # Epochs placeholder
        self.epochs_group = QGroupBox("Epochs (reserved)")
        self.epochs_group.setEnabled(False)
        epochs_layout = QVBoxLayout(self.epochs_group)
        epochs_layout.addWidget(QLabel("Epoch controls will go here."))
        layout.addWidget(self.epochs_group)

        # Notes placeholder
        self.notes_group = QGroupBox("Notes")
        self.notes_group.setEnabled(False)
        notes_layout = QVBoxLayout(self.notes_group)
        notes_layout.addWidget(QLabel("Start a session to enable notes."))
        layout.addWidget(self.notes_group)

        layout.addStretch()
        scroll.setWidget(container)

        # Wire config panel signals
        self.config_panel.start_session_btn.clicked.connect(self._on_start_session)
        self.config_panel.config_validated.connect(self._on_config_validated)

        # Wire session panel signals
        self.session_panel.session_started.connect(self._on_session_started)
        self.session_panel.session_stopped.connect(self._on_session_stopped)
        self.session_panel.trials_started.connect(
            lambda: self.recording_panel.set_trials_running(True)
        )
        self.session_panel.trials_stopped.connect(
            lambda: self.recording_panel.set_trials_running(False)
        )

    def _on_config_validated(self, config: ExperimentConfig) -> None:
        """Enable the Start Session button when a config is validated."""
        self.config_panel.start_session_btn.setEnabled(True)

    def _on_start_session(self) -> None:
        """Kick off a new session using the currently validated config."""
        config = self.config_panel.validated_config
        if config is None:
            return
        self.config_panel.start_session_btn.setEnabled(False)
        self.session_panel.start_session(config)
        # Re-enable if start failed (session_started was not emitted)
        if self.session_panel.session is None:
            self.config_panel.start_session_btn.setEnabled(True)

    def _on_session_started(self) -> None:
        """Lock the config panel and enable the other control groups."""
        self.config_panel.set_editable(False)
        self.recording_group.setEnabled(True)
        self.recording_panel.set_session(
            self.session_panel.session, self.session_panel.task,
        )
        self.params_group.setEnabled(True)
        self.notes_group.setEnabled(True)

    def _on_session_stopped(self) -> None:
        """Unlock the config panel and disable the other control groups."""
        self.config_panel.set_editable(True)
        self.config_panel.start_session_btn.setEnabled(True)
        self.recording_group.setEnabled(False)
        self.recording_panel.set_session(None, None)
        self.params_group.setEnabled(False)
        self.notes_group.setEnabled(False)


def run_control_center(configs_root: Path | None = None) -> int:
    """Launch the Hapticore Control Center and block until closed."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Hapticore Control Center")
    window = ControlCenterWindow(configs_root=configs_root)
    window.resize(500, 800)
    window.show()
    return app.exec()
