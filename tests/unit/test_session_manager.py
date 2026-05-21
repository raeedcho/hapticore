"""Unit tests for SessionManager.

Tests cover:
- Session directory creation and ID generation
- Recording lifecycle (start/stop recording, is_recording state)
- Session receipt JSON writing
- RippleProcess integration (start/stop/shutdown)
- Trellis file_name_base construction
- ZMQ infrastructure creation and cleanup
- Hardware factory lifecycle
- Interface property guards
- Backend compatibility validation
"""

from __future__ import annotations

import contextlib
import datetime
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import msgpack
import pytest
import zmq

from hapticore.core.config import (
    DisplayConfig,
    ExperimentConfig,
    HapticConfig,
    RecordingConfig,
    RippleRecordingConfig,
    SubjectConfig,
    SyncConfig,
    TaskConfig,
)
from hapticore.core.interfaces import DisplayInterface, HapticInterface, SyncInterface
from hapticore.core.messages import TOPIC_SESSION
from hapticore.core.messaging import EventPublisher
from hapticore.session import SessionManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_config(tmp_path: Path) -> ExperimentConfig:
    """An ExperimentConfig with all mock backends and save_dir in tmp_path."""
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


@pytest.fixture
def ripple_config(tmp_path: Path) -> ExperimentConfig:
    """An ExperimentConfig with ripple recording configured."""
    return ExperimentConfig(
        experiment_name="test_experiment",
        subject=SubjectConfig(subject_id="test-monkey"),
        haptic=HapticConfig(backend="mock"),
        display=DisplayConfig(backend="mock"),
        recording=RecordingConfig(
            save_dir=tmp_path,
            granularity="session",
            data_logging_enabled=False,
            ripple=RippleRecordingConfig(),
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
def minimal_config_with_logging(tmp_path: Path) -> ExperimentConfig:
    """ExperimentConfig with mock backends and data_logging_enabled=True."""
    return ExperimentConfig(
        experiment_name="test_experiment",
        subject=SubjectConfig(subject_id="test-monkey"),
        haptic=HapticConfig(backend="mock"),
        display=DisplayConfig(backend="mock"),
        recording=RecordingConfig(
            save_dir=tmp_path, granularity="session", data_logging_enabled=True,
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
# TestSessionDirectory
# ---------------------------------------------------------------------------


class TestSessionDirectory:
    def test_start_creates_session_directory_tree(
        self, minimal_config: ExperimentConfig, tmp_path: Path,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        try:
            assert mgr.session_dir is not None
            assert mgr.session_dir.is_dir()
            # Subdirectories are created per-segment, not at session level
            assert not (mgr.session_dir / "behavior").exists()
            assert not (mgr.session_dir / "sync").exists()
            assert not (mgr.session_dir / "neural" / "ripple").exists()
        finally:
            mgr.stop()

    def test_start_creates_neural_ripple_when_ripple_configured(
        self, ripple_config: ExperimentConfig,
    ) -> None:
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True

        def make_and_set_ready(*args: Any, **kwargs: Any) -> MagicMock:
            ready = kwargs.get("ready_event")
            if ready is not None:
                ready.set()
            return fake_proc

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_and_set_ready,
        ):
            mgr = SessionManager(ripple_config)
            mgr.start()
            try:
                # start() no longer creates neural/ripple — verify absent
                assert not (mgr.session_dir / "neural" / "ripple").exists()
                # start_recording() creates it in the segment directory
                mgr.start_recording()
                seg_dir = mgr.current_segment_dir
                assert seg_dir is not None
                assert (seg_dir / "neural" / "ripple").is_dir()
            finally:
                mgr.stop()

    def test_session_id_increments(
        self, minimal_config: ExperimentConfig, tmp_path: Path,
    ) -> None:
        today = datetime.date.today().strftime("%Y%m%d")
        subject_dir = tmp_path / "sub-test-monkey"
        subject_dir.mkdir(parents=True)
        (subject_dir / f"ses-{today}_001").mkdir()

        mgr = SessionManager(minimal_config)
        mgr.start()
        try:
            assert mgr.session_id == f"ses-{today}_002"
        finally:
            mgr.stop()

    def test_session_dir_without_ripple_skips_neural_ripple(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        try:
            assert mgr.session_dir is not None
            assert not (mgr.session_dir / "neural" / "ripple").exists()
        finally:
            mgr.stop()

    def test_session_id_property_before_start_is_none(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        assert mgr.session_id is None

    def test_session_dir_property_before_start_is_none(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        assert mgr.session_dir is None


# ---------------------------------------------------------------------------
# TestRecordingLifecycle
# ---------------------------------------------------------------------------


class TestRecordingLifecycle:
    def _make_sub(self, address: str) -> tuple[zmq.Context[Any], zmq.Socket[Any]]:
        """Create a SUB socket subscribed to TOPIC_SESSION."""
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(address)
        sub.subscribe(TOPIC_SESSION)
        return ctx, sub

    def _recv_session_messages(
        self,
        sub: zmq.Socket[Any],
        count: int,
        timeout_ms: int = 2000,
    ) -> list[dict[str, Any]]:
        """Receive `count` TOPIC_SESSION messages."""
        messages = []
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while len(messages) < count:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            socks = dict(poller.poll(remaining_ms))
            if sub in socks:
                _, payload = sub.recv_multipart()
                messages.append(msgpack.unpackb(payload, raw=False))
        return messages

    def test_start_recording_publishes_three_messages(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        pub_address = mgr.publisher._socket.last_endpoint.decode()
        sub_ctx, sub = self._make_sub(pub_address)
        time.sleep(0.05)  # slow-joiner
        try:
            mgr.start_recording()
            messages = self._recv_session_messages(sub, 3)
            assert len(messages) == 3
            assert messages[0]["action"] == "start_sync"
            assert messages[1]["action"] == "start_camera_trigger"
            assert messages[2]["action"] == "start_recording"
            assert "file_name_base" in messages[2]["params"]
            mgr.stop()
        finally:
            sub.close()
            sub_ctx.term()

    def test_stop_recording_publishes_three_messages(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        pub_address = mgr.publisher._socket.last_endpoint.decode()
        sub_ctx, sub = self._make_sub(pub_address)
        time.sleep(0.05)
        try:
            mgr.start_recording()
            # drain start messages
            self._recv_session_messages(sub, 3)
            mgr.stop_recording()
            messages = self._recv_session_messages(sub, 3)
            assert len(messages) == 3
            assert messages[0]["action"] == "stop_recording"
            assert messages[1]["action"] == "stop_camera_trigger"
            assert messages[2]["action"] == "stop_sync"
            mgr.stop()
        finally:
            sub.close()
            sub_ctx.term()

    def test_start_recording_raises_if_not_started(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        with pytest.raises(RuntimeError, match="start\\(\\)"):
            mgr.start_recording()

    def test_start_recording_raises_for_block_granularity(
        self, tmp_path: Path,
    ) -> None:
        config = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(
                save_dir=tmp_path, granularity="block", data_logging_enabled=False,
            ),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )
        mgr = SessionManager(config)
        mgr.start()
        try:
            with pytest.raises(NotImplementedError):
                mgr.start_recording()
        finally:
            mgr.stop()

    def test_start_recording_raises_for_trial_granularity(
        self, tmp_path: Path,
    ) -> None:
        config = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(
                save_dir=tmp_path, granularity="trial", data_logging_enabled=False,
            ),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )
        mgr = SessionManager(config)
        mgr.start()
        try:
            with pytest.raises(NotImplementedError):
                mgr.start_recording()
        finally:
            mgr.stop()

    def test_is_recording_tracks_state(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        try:
            assert not mgr.is_recording
            mgr.start_recording()
            assert mgr.is_recording
            mgr.stop_recording()
            assert not mgr.is_recording
        finally:
            mgr.stop()

    def test_stop_calls_stop_recording_if_active(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        pub_address = mgr.publisher._socket.last_endpoint.decode()
        sub_ctx, sub = self._make_sub(pub_address)
        time.sleep(0.05)
        try:
            mgr.start_recording()
            # drain start messages
            self._recv_session_messages(sub, 3)
            # stop without calling stop_recording() explicitly
            mgr.stop()
            stop_messages = self._recv_session_messages(sub, 3)
            assert any(m["action"] == "stop_recording" for m in stop_messages)
        finally:
            sub.close()
            sub_ctx.term()


# ---------------------------------------------------------------------------
# TestSessionReceipt
# ---------------------------------------------------------------------------


class TestSessionReceipt:
    def test_stop_writes_receipt_json(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        mgr.start_recording()
        mgr.stop_recording()
        mgr.stop()

        assert mgr.session_dir is not None
        receipt_path = mgr.session_dir / "session_receipt.json"
        assert receipt_path.exists()
        with receipt_path.open() as f:
            receipt = json.load(f)
        for key in ("session_id", "subject_id", "experiment_name", "timing",
                    "config_snapshot", "recording", "hardware"):
            assert key in receipt, f"Missing key: {key}"
        assert receipt["session_id"] == mgr.session_id
        assert receipt["subject_id"] == "test-monkey"
        assert receipt["experiment_name"] == "test_experiment"
        assert "start_utc" in receipt["timing"]
        assert "end_utc" in receipt["timing"]
        assert "duration_s" in receipt["timing"]

    def test_receipt_includes_trial_summary(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        from hapticore.tasks.trial_manager import TrialManager

        mgr = SessionManager(minimal_config)
        trial_manager = TrialManager(
            conditions=[{"target_angle": 0}],
            block_size=1,
            num_blocks=1,
        )
        mgr.set_trial_manager(trial_manager)
        mgr.start()
        mgr.stop()

        assert mgr.session_dir is not None
        receipt_path = mgr.session_dir / "session_receipt.json"
        with receipt_path.open() as f:
            receipt = json.load(f)
        assert receipt["trial_summary"] is not None
        assert "total_trials" in receipt["trial_summary"]

    def test_receipt_without_trial_manager_has_null_summary(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        mgr.stop()

        assert mgr.session_dir is not None
        receipt_path = mgr.session_dir / "session_receipt.json"
        with receipt_path.open() as f:
            receipt = json.load(f)
        assert receipt["trial_summary"] is None

    def test_receipt_hardware_section(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        mgr.stop()

        assert mgr.session_dir is not None
        receipt_path = mgr.session_dir / "session_receipt.json"
        with receipt_path.open() as f:
            receipt = json.load(f)
        hw = receipt["hardware"]
        assert hw["haptic_backend"] == "mock"
        assert hw["display_backend"] == "mock"
        assert hw["sync_backend"] == "mock"
        assert isinstance(hw["recording_systems"], list)


# ---------------------------------------------------------------------------
# TestRippleProcessIntegration
# ---------------------------------------------------------------------------


class TestRippleProcessIntegration:
    def _make_ready_proc_mock(self) -> tuple[MagicMock, Any]:
        """Create a mock RippleProcess that sets ready_event on start()."""
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

    def test_ripple_process_started_when_configured(
        self, ripple_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_proc_mock()

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_and_capture,
        ) as mock_cls:
            mgr = SessionManager(ripple_config)
            mgr.start()
            try:
                mock_cls.assert_called_once()
                fake_proc.start.assert_called_once()
            finally:
                mgr.stop()

    def test_ripple_process_not_started_when_unconfigured(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        with patch(
            "hapticore.session.manager.RippleProcess",
        ) as mock_cls:
            mgr = SessionManager(minimal_config)
            mgr.start()
            try:
                mock_cls.assert_not_called()
            finally:
                mgr.stop()

    def test_ripple_process_shutdown_on_stop(
        self, ripple_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_proc_mock()
        fake_proc.is_alive.return_value = False

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_and_capture,
        ):
            mgr = SessionManager(ripple_config)
            mgr.start()
            mgr.stop()

        fake_proc.request_shutdown.assert_called_once()
        fake_proc.join.assert_called()

    def test_ripple_process_terminates_if_join_times_out(
        self, ripple_config: ExperimentConfig,
    ) -> None:
        fake_proc, make_and_capture = self._make_ready_proc_mock()
        # Alive after first join, dead after terminate+join.
        fake_proc.is_alive.side_effect = [True, True, False]

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_and_capture,
        ):
            mgr = SessionManager(ripple_config)
            mgr.start()
            mgr.stop()

        fake_proc.terminate.assert_called_once()

    def test_ripple_process_dies_during_startup_raises(
        self, ripple_config: ExperimentConfig,
    ) -> None:
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = False  # Process dies immediately
        fake_proc.exitcode = -9

        def make_dead(*args: Any, **kwargs: Any) -> MagicMock:
            # Don't set the ready event — process "dies" immediately
            return fake_proc

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_dead,
        ):
            mgr = SessionManager(ripple_config)
            with pytest.raises(RuntimeError, match="died during startup"):
                mgr.start()


# ---------------------------------------------------------------------------
# TestTrellisFileNameBase
# ---------------------------------------------------------------------------


class TestTrellisFileNameBase:
    def test_colocated_trellis_path(
        self, tmp_path: Path,
    ) -> None:
        config = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(
                save_dir=tmp_path,
                granularity="session",
                data_logging_enabled=False,
                ripple=RippleRecordingConfig(trellis_data_dir=str(tmp_path)),
            ),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True

        def make_and_set_ready(*args: Any, **kwargs: Any) -> MagicMock:
            ready = kwargs.get("ready_event")
            if ready is not None:
                ready.set()
            return fake_proc

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_and_set_ready,
        ):
            mgr = SessionManager(config)
            mgr.start()
            try:
                mgr.start_recording()
                assert mgr._trellis_file_name_base is not None
                assert str(tmp_path) in mgr._trellis_file_name_base
                assert mgr.session_id is not None
                assert mgr.session_id in mgr._trellis_file_name_base
            finally:
                mgr.stop()

    def test_remote_trellis_path(
        self, tmp_path: Path,
    ) -> None:
        remote_dir = r"C:\Users\Trellis\dataFiles"
        config = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(
                save_dir=tmp_path,
                granularity="session",
                data_logging_enabled=False,
                ripple=RippleRecordingConfig(trellis_data_dir=remote_dir),
            ),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True

        def make_and_set_ready(*args: Any, **kwargs: Any) -> MagicMock:
            ready = kwargs.get("ready_event")
            if ready is not None:
                ready.set()
            return fake_proc

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_and_set_ready,
        ):
            mgr = SessionManager(config)
            mgr.start()
            try:
                mgr.start_recording()
                assert mgr._trellis_file_name_base is not None
                assert mgr._trellis_file_name_base.startswith(remote_dir)
                # Session-relative portion uses forward slashes
                session_relative_part = mgr._trellis_file_name_base[len(remote_dir):]
                assert "\\" not in session_relative_part
            finally:
                mgr.stop()


# ---------------------------------------------------------------------------
# TestInfrastructureLifecycle
# ---------------------------------------------------------------------------


class TestInfrastructureLifecycle:
    def test_start_creates_zmq_context_and_publisher(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        try:
            assert mgr.publisher is not None
            assert isinstance(mgr.publisher, EventPublisher)
        finally:
            mgr.stop()

    def test_start_calls_all_three_factories(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        with patch(
            "hapticore.session.manager.make_haptic_interface",
        ) as mock_haptic_factory, patch(
            "hapticore.session.manager.make_display_interface",
        ) as mock_display_factory, patch(
            "hapticore.session.manager.make_sync_interface",
        ) as mock_sync_factory:
            mock_haptic = MagicMock(spec=HapticInterface)
            mock_display = MagicMock(spec=DisplayInterface)
            mock_sync = MagicMock(spec=SyncInterface)

            mock_haptic_factory.return_value = contextlib.nullcontext(mock_haptic)
            mock_display_factory.return_value = contextlib.nullcontext(mock_display)
            mock_sync_factory.return_value = contextlib.nullcontext(mock_sync)

            mgr = SessionManager(minimal_config)
            mgr.start()
            try:
                mock_haptic_factory.assert_called_once()
                mock_display_factory.assert_called_once()
                mock_sync_factory.assert_called_once()
                # Verify factories were called with the correct config
                call_haptic_args = mock_haptic_factory.call_args
                assert call_haptic_args.args[0] is minimal_config.haptic
                call_display_args = mock_display_factory.call_args
                assert call_display_args.args[0] is minimal_config.display
                call_sync_args = mock_sync_factory.call_args
                assert call_sync_args.args[0] is minimal_config.sync
            finally:
                mgr.stop()

    def test_stop_cleans_up_zmq(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        # Publisher should exist after start
        assert mgr._publisher is not None
        assert mgr._ctx is not None
        assert mgr._haptic is not None
        assert mgr._display is not None
        assert mgr._sync is not None
        mgr.stop()
        # Publisher, context, and interfaces should be cleaned up after stop
        assert mgr._publisher is None
        assert mgr._ctx is None
        assert mgr._haptic is None
        assert mgr._display is None
        assert mgr._sync is None

    def test_haptic_property_before_start_raises(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        with pytest.raises(RuntimeError, match="start\\(\\)"):
            _ = mgr.haptic

    def test_display_property_before_start_raises(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        with pytest.raises(RuntimeError, match="start\\(\\)"):
            _ = mgr.display

    def test_sync_property_before_start_raises(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        with pytest.raises(RuntimeError, match="start\\(\\)"):
            _ = mgr.sync

    def test_publisher_property_before_start_raises(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        with pytest.raises(RuntimeError, match="start\\(\\)"):
            _ = mgr.publisher

    def test_backend_validation_in_start(
        self, tmp_path: Path,
    ) -> None:
        """Backend compatibility check is enforced in start(), not the CLI.

        dhd.mouse_input=True requires display.backend='psychopy' because
        mouse position is read from the PsychoPy window. Using display.backend
        ='mock' should raise ValueError before any infrastructure is created.
        """
        from hapticore.core.config import DhdConfig
        config = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monkey"),
            haptic=HapticConfig(backend="dhd", dhd=DhdConfig(mouse_input=True)),
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
        mgr = SessionManager(config)
        with pytest.raises(ValueError, match="mouse_input"):
            mgr.start()

    def test_context_manager_cleans_up_on_exit(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        with SessionManager(minimal_config) as session:
            assert session.publisher is not None
            assert session._exit_stack is not None
            assert session._haptic is not None
            assert session._display is not None
            assert session._sync is not None
        # After __exit__, everything should be cleaned up
        assert session._publisher is None
        assert session._ctx is None
        assert session._exit_stack is None
        assert session._haptic is None
        assert session._display is None
        assert session._sync is None


# ---------------------------------------------------------------------------
# TestMultiSegmentRecording
# ---------------------------------------------------------------------------


class TestMultiSegmentRecording:
    def test_start_recording_creates_standalone_segment_directory(
        self, minimal_config_with_logging: ExperimentConfig,
    ) -> None:
        """start_recording() creates a segment dir with behavior/ and sync/."""
        mgr = SessionManager(minimal_config_with_logging)
        mgr.start()
        try:
            mgr.start_recording()
            assert mgr.current_segment_label == "seg-001"
            seg_dir = mgr.current_segment_dir
            assert seg_dir is not None
            assert (seg_dir / "behavior").is_dir()
            assert (seg_dir / "sync").is_dir()
            assert not (seg_dir / "neural" / "ripple").exists()
        finally:
            mgr.stop()

    def test_two_segments_create_separate_directories(
        self, minimal_config_with_logging: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config_with_logging)
        mgr.start()
        try:
            mgr.start_recording()
            seg1_dir = mgr.current_segment_dir
            mgr.stop_recording()

            mgr.start_recording()
            seg2_dir = mgr.current_segment_dir
            mgr.stop_recording()

            assert seg1_dir != seg2_dir
            assert seg1_dir is not None and seg1_dir.is_dir()
            assert seg2_dir is not None and seg2_dir.is_dir()
            assert len(mgr.segments) == 2
            assert mgr.segments[0]["label"] == "seg-001"
            assert mgr.segments[1]["label"] == "seg-002"
        finally:
            mgr.stop()

    def test_custom_segment_label(
        self, minimal_config_with_logging: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config_with_logging)
        mgr.start()
        try:
            mgr.start_recording(segment_label="baseline")
            assert mgr.current_segment_label == "baseline"
            assert mgr.current_segment_dir is not None
            assert mgr.current_segment_dir.name == "baseline"
        finally:
            mgr.stop()

    def test_overwrite_protection(
        self, minimal_config_with_logging: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config_with_logging)
        mgr.start()
        try:
            mgr.start_recording(segment_label="seg-001")
            mgr.stop_recording()
            with pytest.raises(ValueError, match="already exists"):
                mgr.start_recording(segment_label="seg-001")
        finally:
            mgr.stop()

    def test_start_recording_while_recording_raises(
        self, minimal_config_with_logging: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config_with_logging)
        mgr.start()
        try:
            mgr.start_recording()
            with pytest.raises(RuntimeError, match="already active"):
                mgr.start_recording()
        finally:
            mgr.stop()

    def test_segment_metadata_recorded(
        self, minimal_config_with_logging: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config_with_logging)
        mgr.start()
        try:
            mgr.start_recording()
            time.sleep(0.05)
            mgr.stop_recording()
            assert len(mgr.segments) == 1
            seg = mgr.segments[0]
            assert seg["label"] == "seg-001"
            assert seg["timing"]["start_utc"] is not None
            assert seg["timing"]["end_utc"] is not None
            assert seg["timing"]["duration_s"] >= 0
            assert seg["trial_range"] == [-1, -1]
        finally:
            mgr.stop()

    def test_stop_recording_without_start_is_noop(
        self, minimal_config_with_logging: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config_with_logging)
        mgr.start()
        try:
            mgr.stop_recording()  # should not raise
            assert len(mgr.segments) == 0
        finally:
            mgr.stop()

    def test_trellis_path_routes_through_segment(
        self, ripple_config: ExperimentConfig,
    ) -> None:
        """Trellis file_name_base includes the segment directory."""
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True

        def make_and_set_ready(*args: Any, **kwargs: Any) -> MagicMock:
            ready = kwargs.get("ready_event")
            if ready is not None:
                ready.set()
            return fake_proc

        with patch(
            "hapticore.session.manager.RippleProcess",
            side_effect=make_and_set_ready,
        ):
            mgr = SessionManager(ripple_config)
            mgr.start()
            try:
                mgr.start_recording(segment_label="seg-001")
                trellis_base = mgr._trellis_file_name_base
                assert trellis_base is not None
                assert "seg-001/neural/ripple/" in trellis_base
                assert mgr.session_id is not None
                assert trellis_base.endswith(f"{mgr.session_id}_seg-001")
            finally:
                mgr.stop()
