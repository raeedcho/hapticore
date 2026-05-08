"""Unit tests for LSLMarkerProcess and SessionManager LSL integration.

All tests run in CI without pylsl installed. The fake pylsl module is
permanent CI infrastructure — it records push_sample calls so tests can
verify marker content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hapticore.core.config import (
    DisplayConfig,
    ExperimentConfig,
    HapticConfig,
    RecordingConfig,
    SubjectConfig,
    SyncConfig,
    TaskConfig,
)
from hapticore.core.config import ZMQConfig
from hapticore.lsl.lsl_process import LSLMarkerProcess
from hapticore.session import SessionManager


# ---------------------------------------------------------------------------
# Fake pylsl module
# ---------------------------------------------------------------------------


class _FakeStreamInfo:
    def __init__(self, name: str, type: str, source_id: str) -> None:
        self.name = name
        self.type = type
        self.source_id = source_id


class _FakeOutlet:
    def __init__(self, info: _FakeStreamInfo) -> None:
        self.info = info
        self.samples: list[list[str]] = []

    def push_sample(self, sample: list[str]) -> None:
        self.samples.append(sample)


class _FakePylsl:
    """Fake pylsl module for LSLMarkerProcess tests."""

    cf_string = "cf_string"

    def __init__(self) -> None:
        self.outlets: list[_FakeOutlet] = []

    def StreamInfo(  # noqa: N802
        self,
        name: str,
        type: str,
        channel_count: int,
        nominal_srate: int,
        channel_format: str,
        source_id: str,
    ) -> _FakeStreamInfo:
        return _FakeStreamInfo(name, type, source_id)

    def StreamOutlet(self, info: _FakeStreamInfo) -> _FakeOutlet:  # noqa: N802
        outlet = _FakeOutlet(info)
        self.outlets.append(outlet)
        return outlet


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lsl_config(tmp_path: Path) -> ExperimentConfig:
    """An ExperimentConfig with LSL enabled."""
    return ExperimentConfig(
        experiment_name="test_experiment",
        subject=SubjectConfig(subject_id="test-monkey"),
        haptic=HapticConfig(backend="mock"),
        display=DisplayConfig(backend="mock"),
        recording=RecordingConfig(
            save_dir=tmp_path,
            granularity="session",
            lsl_enabled=True,
            lsl_stream_name="TestStream",
        ),
        task=TaskConfig(
            task_class="hapticore.tasks.center_out.CenterOutTask",
            conditions=[{"target_angle": 0}],
            block_size=1,
            num_blocks=1,
        ),
        sync=SyncConfig(backend="mock"),
    )


@pytest.fixture
def lsl_disabled_config(tmp_path: Path) -> ExperimentConfig:
    """An ExperimentConfig with LSL disabled."""
    return ExperimentConfig(
        experiment_name="test_experiment",
        subject=SubjectConfig(subject_id="test-monkey"),
        haptic=HapticConfig(backend="mock"),
        display=DisplayConfig(backend="mock"),
        recording=RecordingConfig(
            save_dir=tmp_path,
            granularity="session",
            lsl_enabled=False,
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
# TestFormatMarker
# ---------------------------------------------------------------------------


class TestFormatMarker:
    def test_state_transition_format(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "StateTransition",
            "new_state": "hold",
            "event_code": 5,
            "trial_number": 42,
        }
        assert LSLMarkerProcess._format_marker(msg) == "state:hold:5:42"

    def test_trial_event_format(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "TrialEvent",
            "event_name": "stimulus_on",
            "event_code": 10,
            "trial_number": 3,
        }
        assert LSLMarkerProcess._format_marker(msg) == "event:stimulus_on:10:3"

    def test_unknown_message_type_returns_none(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "SessionControl",
            "action": "start_recording",
            "params": {},
        }
        assert LSLMarkerProcess._format_marker(msg) is None

    def test_missing_msg_type_returns_none(self) -> None:
        msg: dict[str, Any] = {"action": "something"}
        assert LSLMarkerProcess._format_marker(msg) is None

    def test_state_transition_zero_trial(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "StateTransition",
            "new_state": "intertrial",
            "event_code": 0,
            "trial_number": 0,
        }
        assert LSLMarkerProcess._format_marker(msg) == "state:intertrial:0:0"

    def test_trial_event_with_large_code(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "TrialEvent",
            "event_name": "target_acquired",
            "event_code": 255,
            "trial_number": 1000,
        }
        assert LSLMarkerProcess._format_marker(msg) == "event:target_acquired:255:1000"


# ---------------------------------------------------------------------------
# TestLSLMarkerProcessConstruction
# ---------------------------------------------------------------------------


class TestLSLMarkerProcessConstruction:
    def test_ready_event_is_optional(self) -> None:
        proc = LSLMarkerProcess(
            stream_name="Test",
            source_id="ses-test_001",
            zmq_config=ZMQConfig(),
        )
        assert proc._ready_event is None

    def test_request_shutdown_sets_event(self) -> None:
        proc = LSLMarkerProcess(
            stream_name="Test",
            source_id="ses-test_001",
            zmq_config=ZMQConfig(),
        )
        assert not proc._shutdown.is_set()
        proc.request_shutdown()
        assert proc._shutdown.is_set()

    def test_stream_name_stored(self) -> None:
        proc = LSLMarkerProcess(
            stream_name="MyStream",
            source_id="ses-test_001",
            zmq_config=ZMQConfig(),
        )
        assert proc._stream_name == "MyStream"

    def test_source_id_stored(self) -> None:
        proc = LSLMarkerProcess(
            stream_name="Test",
            source_id="ses-20250101_001",
            zmq_config=ZMQConfig(),
        )
        assert proc._source_id == "ses-20250101_001"

    def test_pylsl_module_stored(self) -> None:
        fake = _FakePylsl()
        proc = LSLMarkerProcess(
            stream_name="Test",
            source_id="ses-test_001",
            zmq_config=ZMQConfig(),
            pylsl_module=fake,
        )
        assert proc._pylsl_module is fake


# ---------------------------------------------------------------------------
# TestSessionManagerLSLIntegration
# ---------------------------------------------------------------------------


class TestSessionManagerLSLIntegration:
    def _make_ready_lsl_proc_mock(self) -> tuple[MagicMock, Any]:
        """Create a mock LSLMarkerProcess that sets ready_event on start()."""
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True
        captured: dict[str, Any] = {}

        def make_and_capture(*args: Any, **kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            ready = kwargs.get("ready_event")

            def start_and_set_ready() -> None:
                if ready is not None:
                    ready.set()

            fake_proc.start.side_effect = start_and_set_ready
            return fake_proc

        return fake_proc, make_and_capture

    def test_lsl_process_started_when_enabled(
        self, lsl_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_lsl_proc_mock()

        with patch(
            "hapticore.session.manager.LSLMarkerProcess",
            side_effect=make_and_capture,
        ) as mock_cls:
            mgr = SessionManager(lsl_config)
            mgr.start()
            try:
                mock_cls.assert_called_once()
                fake_proc.start.assert_called_once()
            finally:
                mgr.stop()

    def test_lsl_process_not_started_when_disabled(
        self, lsl_disabled_config: ExperimentConfig,
    ) -> None:
        with patch(
            "hapticore.session.manager.LSLMarkerProcess",
        ) as mock_cls:
            mgr = SessionManager(lsl_disabled_config)
            mgr.start()
            try:
                mock_cls.assert_not_called()
            finally:
                mgr.stop()

    def test_lsl_process_shutdown_on_stop(
        self, lsl_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_lsl_proc_mock()
        fake_proc.is_alive.return_value = False

        with patch(
            "hapticore.session.manager.LSLMarkerProcess",
            side_effect=make_and_capture,
        ):
            mgr = SessionManager(lsl_config)
            mgr.start()
            mgr.stop()

        fake_proc.request_shutdown.assert_called_once()
        fake_proc.join.assert_called()

    def test_lsl_process_receives_source_id(
        self, lsl_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_lsl_proc_mock()
        captured: dict[str, Any] = {}

        def capturing_factory(*args: Any, **kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            # also call the original factory to set ready_event
            return make_and_capture(*args, **kwargs)

        with patch(
            "hapticore.session.manager.LSLMarkerProcess",
            side_effect=capturing_factory,
        ):
            mgr = SessionManager(lsl_config)
            mgr.start()
            try:
                assert mgr.session_id is not None
                assert captured.get("source_id") == mgr.session_id
            finally:
                mgr.stop()

    def test_lsl_process_receives_stream_name(
        self, lsl_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_lsl_proc_mock()
        captured: dict[str, Any] = {}

        def capturing_factory(*args: Any, **kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return make_and_capture(*args, **kwargs)

        with patch(
            "hapticore.session.manager.LSLMarkerProcess",
            side_effect=capturing_factory,
        ):
            mgr = SessionManager(lsl_config)
            mgr.start()
            try:
                assert captured.get("stream_name") == "TestStream"
            finally:
                mgr.stop()

    def test_lsl_process_terminates_if_join_times_out(
        self, lsl_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_lsl_proc_mock()
        # Alive after first join, dead after terminate+join
        fake_proc.is_alive.side_effect = [True, True, False]

        with patch(
            "hapticore.session.manager.LSLMarkerProcess",
            side_effect=make_and_capture,
        ):
            mgr = SessionManager(lsl_config)
            mgr.start()
            mgr.stop()

        fake_proc.terminate.assert_called_once()

    def test_lsl_process_started_with_ready_event(
        self, lsl_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_lsl_proc_mock()
        captured: dict[str, Any] = {}

        def capturing_factory(*args: Any, **kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return make_and_capture(*args, **kwargs)

        with patch(
            "hapticore.session.manager.LSLMarkerProcess",
            side_effect=capturing_factory,
        ):
            mgr = SessionManager(lsl_config)
            mgr.start()
            try:
                assert "ready_event" in captured
                assert captured["ready_event"] is not None
            finally:
                mgr.stop()
