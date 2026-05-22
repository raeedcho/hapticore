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

        # Session Controls placeholder
        self.session_group = QGroupBox("Session Controls")
        self.session_group.setEnabled(False)
        session_layout = QVBoxLayout(self.session_group)
        session_layout.addWidget(QLabel("Start a session to enable controls."))
        layout.addWidget(self.session_group)

        # Recording placeholder
        self.recording_group = QGroupBox("Recording")
        self.recording_group.setEnabled(False)
        recording_layout = QVBoxLayout(self.recording_group)
        recording_layout.addWidget(QLabel("Start a session to enable recording."))
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


def run_control_center(configs_root: Path | None = None) -> int:
    """Launch the Hapticore Control Center and block until closed."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Hapticore Control Center")
    window = ControlCenterWindow(configs_root=configs_root)
    window.resize(500, 800)
    window.show()
    return app.exec()
