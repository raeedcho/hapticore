"""Unit tests for SessionPanel, SessionManager new properties, and TrialManager.clear_stop_request."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from hapticore.core.config import ExperimentConfig, load_config  # noqa: E402
from hapticore.control.app import ControlCenterWindow  # noqa: E402
from hapticore.control.session_panel import SessionPanel  # noqa: E402
from hapticore.session import SessionManager  # noqa: E402
from hapticore.tasks.trial_manager import TrialManager  # noqa: E402


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
    return app  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CONFIGS_ROOT = Path("configs")


@pytest.fixture()
def minimal_config(tmp_path: Path) -> ExperimentConfig:
    """A valid ExperimentConfig using mock backends and tmp_path for data."""
    config = load_config(
        _CONFIGS_ROOT / "rig" / "ci.yaml",
        _CONFIGS_ROOT / "subject" / "example_subject.yaml",
        _CONFIGS_ROOT / "experiments" / "center_out.yaml",
    )
    config = config.model_copy(
        update={"recording": config.recording.model_copy(update={"save_dir": tmp_path})}
    )
    return config


# ---------------------------------------------------------------------------
# TestSessionPanel
# ---------------------------------------------------------------------------


class TestSessionPanel:
    def test_initial_state(self, qapp: QApplication) -> None:
        panel = SessionPanel()
        assert not panel._start_trials_btn.isEnabled()
        assert not panel._stop_block_btn.isEnabled()
        assert not panel._stop_trial_btn.isEnabled()
        assert not panel._stop_session_btn.isEnabled()
        assert panel._status_label.text() == "No session"
        assert panel.session is None
        assert panel.task is None

    def test_start_session_enables_controls(
        self, qapp: QApplication, minimal_config: ExperimentConfig
    ) -> None:
        panel = SessionPanel()
        try:
            panel.start_session(minimal_config)
            assert panel._status_label.text() == "Session ready"
            assert panel.session is not None
            assert panel._start_trials_btn.isEnabled()
            assert panel._stop_session_btn.isEnabled()
            assert not panel._stop_block_btn.isEnabled()
            assert not panel._stop_trial_btn.isEnabled()
        finally:
            panel._on_stop_session()

    def test_start_session_emits_signal(
        self, qapp: QApplication, minimal_config: ExperimentConfig
    ) -> None:
        panel = SessionPanel()
        captured: list[object] = []
        panel.session_started.connect(lambda: captured.append(True))
        try:
            panel.start_session(minimal_config)
            assert len(captured) == 1
        finally:
            panel._on_stop_session()

    def test_start_session_failure_shows_error(
        self, qapp: QApplication, minimal_config: ExperimentConfig
    ) -> None:
        bad_config = minimal_config.model_copy(
            update={
                "task": minimal_config.task.model_copy(
                    update={"task_class": "nonexistent.NoSuchTask"}
                )
            }
        )
        panel = SessionPanel()
        panel.start_session(bad_config)
        assert panel._status_label.text().startswith("Error")
        assert panel.session is None

    def test_start_trials_enables_stop_buttons(
        self, qapp: QApplication, minimal_config: ExperimentConfig
    ) -> None:
        panel = SessionPanel()
        try:
            panel.start_session(minimal_config)
            panel._on_start_trials()
            assert panel._stop_block_btn.isEnabled()
            assert panel._stop_trial_btn.isEnabled()
            assert not panel._start_trials_btn.isEnabled()
        finally:
            panel._on_stop_session()

    def test_stop_trials_enables_restart(
        self, qapp: QApplication, minimal_config: ExperimentConfig
    ) -> None:
        panel = SessionPanel()
        try:
            panel.start_session(minimal_config)
            panel._on_start_trials()
            panel._stop_trials("Paused")
            assert panel._start_trials_btn.isEnabled()
            assert not panel._stop_block_btn.isEnabled()
            assert not panel._stop_trial_btn.isEnabled()
            assert panel._status_label.text() == "Paused"
            assert panel.session is not None
        finally:
            panel._on_stop_session()

    def test_stop_session_clears_all(
        self, qapp: QApplication, minimal_config: ExperimentConfig
    ) -> None:
        panel = SessionPanel()
        panel.start_session(minimal_config)
        panel._on_stop_session()
        assert not panel._start_trials_btn.isEnabled()
        assert not panel._stop_block_btn.isEnabled()
        assert not panel._stop_trial_btn.isEnabled()
        assert not panel._stop_session_btn.isEnabled()
        assert panel.session is None
        assert panel.task is None
        assert panel._status_label.text() == "Stopped"

    def test_stop_session_emits_signal(
        self, qapp: QApplication, minimal_config: ExperimentConfig
    ) -> None:
        panel = SessionPanel()
        captured: list[object] = []
        panel.session_stopped.connect(lambda: captured.append(True))
        panel.start_session(minimal_config)
        panel._on_stop_session()
        assert len(captured) == 1

    def test_start_button_reenabled_on_failure(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        window = ControlCenterWindow(configs_root=_CONFIGS_ROOT)
        # Build a config with a bad task class
        from hapticore.core.config import (
            DisplayConfig,
            ExperimentConfig,
            HapticConfig,
            RecordingConfig,
            SubjectConfig,
            SyncConfig,
            TaskConfig,
        )
        bad_config = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="test-monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(save_dir=tmp_path, data_logging_enabled=False),
            task=TaskConfig(
                task_class="nonexistent.NoSuchTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )
        window.config_panel._validated_config = bad_config
        window.config_panel.start_session_btn.setEnabled(True)
        window._on_start_session()
        assert window.config_panel.start_session_btn.isEnabled()


# ---------------------------------------------------------------------------
# TestSessionManagerProperties
# ---------------------------------------------------------------------------


class TestSessionManagerProperties:
    def test_zmq_context_before_start_raises(
        self, minimal_config: ExperimentConfig
    ) -> None:
        session = SessionManager(minimal_config)
        with pytest.raises(RuntimeError):
            _ = session.zmq_context

    def test_event_pub_address_before_start_raises(
        self, minimal_config: ExperimentConfig
    ) -> None:
        session = SessionManager(minimal_config)
        with pytest.raises(RuntimeError):
            _ = session.event_pub_address

    def test_zmq_context_after_start(self, minimal_config: ExperimentConfig) -> None:
        import zmq

        session = SessionManager(minimal_config)
        try:
            session.start()
            assert isinstance(session.zmq_context, zmq.Context)
        finally:
            session.stop()

    def test_event_pub_address_after_start(
        self, minimal_config: ExperimentConfig
    ) -> None:
        session = SessionManager(minimal_config)
        try:
            session.start()
            addr = session.event_pub_address
            assert isinstance(addr, str)
            assert len(addr) > 0
        finally:
            session.stop()

    def test_event_pub_address_after_stop_raises(
        self, minimal_config: ExperimentConfig
    ) -> None:
        session = SessionManager(minimal_config)
        session.start()
        session.stop()
        with pytest.raises(RuntimeError):
            _ = session.event_pub_address


# ---------------------------------------------------------------------------
# TestTrialManagerClearStop
# ---------------------------------------------------------------------------


class TestTrialManagerClearStop:
    def _make_tm(self, num_blocks: int | None = None) -> TrialManager:
        return TrialManager(
            conditions=[{"target_id": i} for i in range(4)],
            block_size=4,
            num_blocks=num_blocks,
            randomization="sequential",
        )

    def test_clear_stop_request_allows_advance(self) -> None:
        tm = self._make_tm(num_blocks=None)
        # Advance through the first block
        for _ in range(4):
            result = tm.advance()
            assert result is not None
            tm.log_trial(outcome="success")
        # Request stop at block boundary — at_block_boundary is now True
        tm.request_stop(after="block")
        # advance() should return None at block boundary
        assert tm.advance() is None
        # Clear the stop — advance should work again
        tm.clear_stop_request()
        result = tm.advance()
        assert result is not None

    def test_clear_stop_request_after_trial_stop(self) -> None:
        tm = self._make_tm(num_blocks=None)
        # Advance one trial
        result = tm.advance()
        assert result is not None
        tm.log_trial(outcome="success")
        # Request stop after trial
        tm.request_stop(after="trial")
        # advance() should return None
        assert tm.advance() is None
        # Clear the stop — advance should work again
        tm.clear_stop_request()
        result = tm.advance()
        assert result is not None
