"""Unit tests for RecordingPanel."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from hapticore.control.recording_panel import RecordingPanel  # noqa: E402


# ---------------------------------------------------------------------------
# Session-scoped QApplication fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provide a single QApplication for all tests (required by Qt widgets)."""
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_session() -> MagicMock:
    session = MagicMock()
    session.is_recording = False
    session.segments = []
    session.current_segment_label = None
    return session


@pytest.fixture()
def mock_task() -> MagicMock:
    task = MagicMock()
    task.params = {"hold_time": 0.5, "reach_timeout": 5.0}
    return task


# ---------------------------------------------------------------------------
# TestRecordingPanel
# ---------------------------------------------------------------------------


class TestRecordingPanel:
    def test_initial_state(self, qapp: QApplication) -> None:
        panel = RecordingPanel()
        assert not panel._start_btn.isEnabled()
        assert not panel._stop_btn.isEnabled()
        assert not panel._label_input.isEnabled()
        assert panel._status_label.text() == "Not recording"
        assert panel._segment_list.count() == 0
        assert not panel._warning_label.isVisible()

    def test_set_session_enables_start(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        assert panel._start_btn.isEnabled()
        assert not panel._stop_btn.isEnabled()
        assert panel._label_input.isEnabled()
        assert not panel._warning_label.isVisible()

    def test_set_session_none_disables_all(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel.set_session(None, None)
        assert not panel._start_btn.isEnabled()
        assert not panel._stop_btn.isEnabled()
        assert not panel._label_input.isEnabled()
        assert not panel._warning_label.isVisible()

    def test_start_recording_calls_session(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel._label_input.setText("baseline")
        panel._on_start_recording()
        mock_session.start_recording.assert_called_once_with(
            segment_label="baseline",
            active_params={"hold_time": 0.5, "reach_timeout": 5.0},
        )

    def test_start_recording_empty_label_passes_none(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel._label_input.clear()
        panel._on_start_recording()
        mock_session.start_recording.assert_called_once_with(
            segment_label=None,
            active_params={"hold_time": 0.5, "reach_timeout": 5.0},
        )

    def test_start_recording_toggles_buttons(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        mock_session.current_segment_label = "seg-001"
        panel.set_session(mock_session, mock_task)
        panel._on_start_recording()
        assert not panel._start_btn.isEnabled()
        assert panel._stop_btn.isEnabled()
        assert not panel._label_input.isEnabled()
        assert "seg-001" in panel._status_label.text()

    def test_start_recording_error_shows_status(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        mock_session.start_recording.side_effect = ValueError("duplicate label")
        panel.set_session(mock_session, mock_task)
        panel._on_start_recording()
        assert "Error" in panel._status_label.text()
        assert panel._start_btn.isEnabled()

    def test_stop_recording_calls_session(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel._on_start_recording()
        panel._on_stop_recording()
        mock_session.stop_recording.assert_called_once()

    def test_stop_recording_toggles_buttons(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel._on_start_recording()
        panel._on_stop_recording()
        assert panel._start_btn.isEnabled()
        assert not panel._stop_btn.isEnabled()
        assert panel._label_input.isEnabled()
        assert panel._label_input.text() == ""

    def test_update_segment_list(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        mock_session.segments = [
            {"label": "seg-001", "trial_range": [0, 50]},
            {"label": "seg-002", "trial_range": [50, 100]},
        ]
        panel.set_session(mock_session, mock_task)
        panel._update_segment_list()
        assert panel._segment_list.count() == 2
        first_text = panel._segment_list.item(0).text()
        assert "seg-001" in first_text
        assert "0" in first_text
        assert "50" in first_text


# ---------------------------------------------------------------------------
# TestWarningBanner
# ---------------------------------------------------------------------------


class TestWarningBanner:
    def test_warning_hidden_by_default(self, qapp: QApplication) -> None:
        panel = RecordingPanel()
        assert not panel._warning_label.isVisible()

    def test_warning_shown_when_trials_running_without_recording(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        mock_session.is_recording = False
        panel.set_trials_running(True)
        assert panel._warning_label.isVisible()

    def test_warning_hidden_when_recording_active(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        mock_session.is_recording = True
        panel.set_trials_running(True)
        assert not panel._warning_label.isVisible()

    def test_warning_hidden_when_recording_starts(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel.set_trials_running(True)
        assert panel._warning_label.isVisible()
        mock_session.is_recording = True
        mock_session.current_segment_label = "seg-001"
        panel._on_start_recording()
        assert not panel._warning_label.isVisible()

    def test_warning_reappears_when_recording_stops_with_trials_running(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel.set_trials_running(True)
        mock_session.is_recording = False
        panel._on_stop_recording()
        assert panel._warning_label.isVisible()

    def test_warning_hidden_when_trials_stop(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel.set_trials_running(True)
        assert panel._warning_label.isVisible()
        panel.set_trials_running(False)
        assert not panel._warning_label.isVisible()

    def test_warning_hidden_when_session_cleared(
        self, qapp: QApplication, mock_session: MagicMock, mock_task: MagicMock
    ) -> None:
        panel = RecordingPanel()
        panel.set_session(mock_session, mock_task)
        panel.set_trials_running(True)
        assert panel._warning_label.isVisible()
        panel.set_session(None, None)
        assert not panel._warning_label.isVisible()
