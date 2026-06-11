"""Unit tests for SessionPanel and the new SessionManager properties.

Tests cover:
- SessionPanel initial state
- SessionPanel start_session / _finish_session lifecycle
- SessionPanel signals (session_started, session_stopped)
- SessionPanel stop-button slots
- SessionManager.zmq_context and event_pub_address properties
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

import zmq  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from hapticore.core.config import (  # noqa: E402
    DisplayConfig,
    ExperimentConfig,
    HapticConfig,
    RecordingConfig,
    SubjectConfig,
    SyncConfig,
    TaskConfig,
)
from hapticore.control.session_panel import SessionPanel  # noqa: E402
from hapticore.session import SessionManager  # noqa: E402


# ---------------------------------------------------------------------------
# Session-scoped QApplication fixture (same pattern as test_control_center.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provide a single QApplication for all tests (required by Qt widgets)."""
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_config(tmp_path: Path) -> ExperimentConfig:
    """ExperimentConfig with all-mock backends and minimal trial structure."""
    return ExperimentConfig(
        experiment_name="test_experiment",
        subject=SubjectConfig(subject_id="test-monkey"),
        haptic=HapticConfig(backend="mock"),
        display=DisplayConfig(backend="mock"),
        recording=RecordingConfig(
            save_dir=tmp_path, granularity="session", data_logging_enabled=False,
        ),
        task=TaskConfig(
            task_class="hapticore.tasks.center_out.CenterOutTask",
            conditions=[{"target_angle": 0}],
            block_size=1,
            num_blocks=1,
        ),
        sync=SyncConfig(backend="mock"),
    )


# ---------------------------------------------------------------------------
# TestSessionPanel
# ---------------------------------------------------------------------------


class TestSessionPanel:
    def test_initial_state(self, qapp: QApplication) -> None:
        """All stop buttons disabled and status label is 'No session' initially."""
        panel = SessionPanel()
        assert not panel._stop_block_btn.isEnabled()
        assert not panel._stop_trial_btn.isEnabled()
        assert not panel._stop_now_btn.isEnabled()
        assert panel._status_label.text() == "No session"
        assert panel.session is None
        assert panel.task is None

    def test_start_session_status_and_buttons(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """After start_session(), status is 'Running' and stop buttons are enabled."""
        panel = SessionPanel()
        try:
            panel.start_session(minimal_config)
            assert panel._status_label.text() == "Running"
            assert panel._stop_block_btn.isEnabled()
            assert panel._stop_trial_btn.isEnabled()
            assert panel._stop_now_btn.isEnabled()
            assert panel.session is not None
            assert panel.task is not None
        finally:
            panel._finish_session("Stopped")

    def test_start_session_emits_signal(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """start_session() emits session_started after a successful start."""
        panel = SessionPanel()
        captured: list[object] = []
        panel.session_started.connect(lambda: captured.append(True))
        try:
            panel.start_session(minimal_config)
            assert len(captured) == 1
        finally:
            panel._finish_session("Stopped")

    def test_finish_session_emits_signal(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """_finish_session() emits session_stopped."""
        panel = SessionPanel()
        captured: list[object] = []
        panel.session_stopped.connect(lambda: captured.append(True))
        panel.start_session(minimal_config)
        panel._finish_session("Done")
        assert len(captured) == 1

    def test_finish_session_disables_buttons(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """After _finish_session(), all stop buttons are disabled."""
        panel = SessionPanel()
        panel.start_session(minimal_config)
        panel._finish_session("Complete")
        assert not panel._stop_block_btn.isEnabled()
        assert not panel._stop_trial_btn.isEnabled()
        assert not panel._stop_now_btn.isEnabled()

    def test_finish_session_sets_status(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """_finish_session(reason) sets the status label to reason."""
        panel = SessionPanel()
        panel.start_session(minimal_config)
        panel._finish_session("Complete")
        assert panel._status_label.text() == "Complete"

    def test_stop_after_block_disables_buttons(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """_on_stop_after_block() disables buttons and sets status to Stopping."""
        panel = SessionPanel()
        try:
            panel.start_session(minimal_config)
            panel._on_stop_after_block()
            assert not panel._stop_block_btn.isEnabled()
            assert not panel._stop_trial_btn.isEnabled()
            assert not panel._stop_now_btn.isEnabled()
            assert "Stopping" in panel._status_label.text()
        finally:
            panel._finish_session("Stopped")

    def test_stop_after_trial_disables_buttons(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """_on_stop_after_trial() disables buttons and sets status to Stopping."""
        panel = SessionPanel()
        try:
            panel.start_session(minimal_config)
            panel._on_stop_after_trial()
            assert not panel._stop_block_btn.isEnabled()
            assert not panel._stop_trial_btn.isEnabled()
            assert not panel._stop_now_btn.isEnabled()
            assert "Stopping" in panel._status_label.text()
        finally:
            panel._finish_session("Stopped")

    def test_start_session_bad_task_class_shows_error(
        self, qapp: QApplication, tmp_path: Path,
    ) -> None:
        """If the task class cannot be imported, status shows an error."""
        config = ExperimentConfig(
            experiment_name="bad_task",
            subject=SubjectConfig(subject_id="test-monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(
                save_dir=tmp_path, granularity="session", data_logging_enabled=False,
            ),
            task=TaskConfig(
                task_class="hapticore.tasks.nonexistent.NoSuchTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )
        panel = SessionPanel()
        panel.start_session(config)
        assert panel._status_label.text().startswith("Error:")
        assert panel.session is None

    def test_start_session_undotted_task_class_shows_error(
        self, qapp: QApplication, tmp_path: Path,
    ) -> None:
        """If the task_class has no dot, status shows an error without crashing."""
        config = ExperimentConfig(
            experiment_name="bad_task",
            subject=SubjectConfig(subject_id="test-monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(
                save_dir=tmp_path, granularity="session", data_logging_enabled=False,
            ),
            task=TaskConfig(
                task_class="NoDot",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )
        panel = SessionPanel()
        panel.start_session(config)
        assert panel._status_label.text().startswith("Error:")
        assert panel.session is None

    def test_session_id_label_updated(
        self, qapp: QApplication, minimal_config: ExperimentConfig,
    ) -> None:
        """Session ID label is set after start_session()."""
        panel = SessionPanel()
        try:
            panel.start_session(minimal_config)
            assert panel._session_id_label.text() != ""
            assert panel._session_id_label.text().startswith("ses-")
        finally:
            panel._finish_session("Stopped")


# ---------------------------------------------------------------------------
# TestSessionManagerProperties
# ---------------------------------------------------------------------------


class TestSessionManagerProperties:
    def test_zmq_context_before_start_raises(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        """Accessing zmq_context before start() raises RuntimeError."""
        mgr = SessionManager(minimal_config)
        with pytest.raises(RuntimeError, match="start\\(\\)"):
            _ = mgr.zmq_context

    def test_event_pub_address_before_start_raises(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        """Accessing event_pub_address before start() raises RuntimeError."""
        mgr = SessionManager(minimal_config)
        with pytest.raises(RuntimeError, match="start\\(\\)"):
            _ = mgr.event_pub_address

    def test_zmq_context_after_start(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        """zmq_context returns a zmq.Context after start()."""
        mgr = SessionManager(minimal_config)
        mgr.start()
        try:
            ctx = mgr.zmq_context
            assert ctx is not None
            assert isinstance(ctx, zmq.Context)
        finally:
            mgr.stop()

    def test_event_pub_address_after_start(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        """event_pub_address returns a non-empty string after start()."""
        mgr = SessionManager(minimal_config)
        mgr.start()
        try:
            addr = mgr.event_pub_address
            assert isinstance(addr, str)
            assert len(addr) > 0
        finally:
            mgr.stop()
