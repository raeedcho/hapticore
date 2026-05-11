"""Unit tests for DataLoggerProcess.

Tests cover:
- _format_event: exact TSV output for StateTransition, TrialEvent, unknown types.
- _write_haptic_sample: binary round-trip, column order, multi-sample.
- _write_haptic_sidecar: JSON content verification.
- _open_files / _close_files: TSV header, file readability.
- DataLoggerProcess construction and shutdown.
- SessionManager integration with DataLoggerProcess.
- RecordingConfig field changes (data_logging_enabled, no lsl_enabled).
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
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
from hapticore.datalog.data_logger_process import DataLoggerProcess


# ---------------------------------------------------------------------------
# TestFormatEvent
# ---------------------------------------------------------------------------


class TestFormatEvent:
    def test_state_transition_format(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "StateTransition",
            "timestamp": 1.234,
            "trial_number": 5,
            "new_state": "hold",
            "previous_state": "reach",
            "trigger": "target_reached",
            "event_code": 3,
        }
        result = DataLoggerProcess._format_event(msg)
        assert result == "1.234\t5\tstate\thold\t3\n"

    def test_trial_event_format(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "TrialEvent",
            "timestamp": 2.0,
            "trial_number": 5,
            "event_name": "stimulus_on",
            "event_code": 10,
            "data": {},
        }
        result = DataLoggerProcess._format_event(msg)
        assert result == "2.0\t5\tevent\tstimulus_on\t10\n"

    def test_unknown_message_returns_none(self) -> None:
        msg: dict[str, Any] = {
            "__msg_type__": "SessionControl",
            "timestamp": 1.0,
            "action": "start_recording",
            "params": {},
        }
        result = DataLoggerProcess._format_event(msg)
        assert result is None

    def test_missing_msg_type_returns_none(self) -> None:
        msg: dict[str, Any] = {
            "timestamp": 1.0,
            "some_field": "some_value",
        }
        result = DataLoggerProcess._format_event(msg)
        assert result is None

    def test_state_transition_columns_order(self) -> None:
        """Verify exact tab-separated column order."""
        msg: dict[str, Any] = {
            "__msg_type__": "StateTransition",
            "timestamp": 0.5,
            "trial_number": 1,
            "new_state": "intertrial",
            "previous_state": "hold",
            "trigger": "hold_complete",
            "event_code": 7,
        }
        result = DataLoggerProcess._format_event(msg)
        assert result is not None
        parts = result.rstrip("\n").split("\t")
        assert parts == ["0.5", "1", "state", "intertrial", "7"]

    def test_trial_event_columns_order(self) -> None:
        """Verify exact tab-separated column order."""
        msg: dict[str, Any] = {
            "__msg_type__": "TrialEvent",
            "timestamp": 3.14,
            "trial_number": 12,
            "event_name": "reward",
            "event_code": 20,
            "data": {"amount_ms": 200},
        }
        result = DataLoggerProcess._format_event(msg)
        assert result is not None
        parts = result.rstrip("\n").split("\t")
        assert parts == ["3.14", "12", "event", "reward", "20"]


# ---------------------------------------------------------------------------
# TestWriteHapticSample
# ---------------------------------------------------------------------------


class TestWriteHapticSample:
    def _make_msg(
        self,
        timestamp: float = 1.0,
        position: list[float] | None = None,
        velocity: list[float] | None = None,
        force: list[float] | None = None,
    ) -> dict[str, Any]:
        return {
            "__msg_type__": "HapticState",
            "timestamp": timestamp,
            "sequence": 0,
            "position": position or [0.1, 0.2, 0.3],
            "velocity": velocity or [0.4, 0.5, 0.6],
            "force": force or [0.7, 0.8, 0.9],
            "active_field": "null",
            "field_state": {},
        }

    def test_writes_80_bytes(self) -> None:
        buf = BytesIO()
        DataLoggerProcess._write_haptic_sample(buf, self._make_msg())
        assert buf.tell() == 80  # 10 float64 × 8 bytes

    def test_column_order(self) -> None:
        buf = BytesIO()
        msg = self._make_msg(
            timestamp=1.5,
            position=[0.01, 0.02, 0.03],
            velocity=[0.04, 0.05, 0.06],
            force=[1.0, 2.0, 3.0],
        )
        DataLoggerProcess._write_haptic_sample(buf, msg)
        buf.seek(0)
        row = np.frombuffer(buf.read(), dtype="<f8")  # explicit little-endian
        assert row.shape == (10,)
        assert row[0] == pytest.approx(1.5)     # timestamp
        assert row[1] == pytest.approx(0.01)    # position_x
        assert row[2] == pytest.approx(0.02)    # position_y
        assert row[3] == pytest.approx(0.03)    # position_z
        assert row[4] == pytest.approx(0.04)    # velocity_x
        assert row[5] == pytest.approx(0.05)    # velocity_y
        assert row[6] == pytest.approx(0.06)    # velocity_z
        assert row[7] == pytest.approx(1.0)     # force_x
        assert row[8] == pytest.approx(2.0)     # force_y
        assert row[9] == pytest.approx(3.0)     # force_z

    def test_multiple_samples_concatenate(self) -> None:
        buf = BytesIO()
        for i in range(3):
            DataLoggerProcess._write_haptic_sample(
                buf,
                self._make_msg(
                    timestamp=float(i),
                    position=[float(i), 0.0, 0.0],
                    velocity=[0.0, 0.0, 0.0],
                    force=[0.0, 0.0, 0.0],
                ),
            )
        assert buf.tell() == 240  # 3 samples × 80 bytes
        buf.seek(0)
        data = np.frombuffer(buf.read(), dtype="<f8").reshape(-1, 10)
        assert data.shape == (3, 10)
        for i in range(3):
            assert data[i, 0] == pytest.approx(float(i))  # timestamp
            assert data[i, 1] == pytest.approx(float(i))  # position_x


# ---------------------------------------------------------------------------
# TestWriteHapticSidecar
# ---------------------------------------------------------------------------


class TestWriteHapticSidecar:
    def test_sidecar_content(self, tmp_path: Path) -> None:
        sidecar_path = tmp_path / "test_haptic.json"
        DataLoggerProcess._write_haptic_sidecar(sidecar_path, sample_count=42)
        data = json.loads(sidecar_path.read_text())

        assert data["dtype"] == "float64"
        assert data["byte_order"] == "little"
        assert data["n_columns"] == 10
        assert data["n_samples"] == 42
        assert data["bytes_per_sample"] == 80
        assert isinstance(data["columns"], list)
        assert len(data["columns"]) == 10
        assert data["columns"][0] == "timestamp_s"

    def test_sidecar_column_names(self, tmp_path: Path) -> None:
        sidecar_path = tmp_path / "test_haptic.json"
        DataLoggerProcess._write_haptic_sidecar(sidecar_path, sample_count=0)
        data = json.loads(sidecar_path.read_text())
        expected_columns = [
            "timestamp_s",
            "position_x", "position_y", "position_z",
            "velocity_x", "velocity_y", "velocity_z",
            "force_x", "force_y", "force_z",
        ]
        assert data["columns"] == expected_columns

    def test_sidecar_ends_with_newline(self, tmp_path: Path) -> None:
        sidecar_path = tmp_path / "test_haptic.json"
        DataLoggerProcess._write_haptic_sidecar(sidecar_path, sample_count=5)
        content = sidecar_path.read_text()
        assert content.endswith("\n")


# ---------------------------------------------------------------------------
# TestOpenCloseFiles
# ---------------------------------------------------------------------------


class TestOpenCloseFiles:
    def test_open_writes_tsv_header(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.tsv"
        haptic_bin_path = tmp_path / "haptic.bin"
        events_file, haptic_file = DataLoggerProcess._open_files(
            events_path, haptic_bin_path,
        )
        try:
            events_file.flush()
            first_line = events_path.read_text().splitlines()[0]
            assert first_line == "timestamp_s\ttrial_number\tmsg_type\tname\tevent_code"
        finally:
            DataLoggerProcess._close_files(events_file, haptic_file)

    def test_open_creates_binary_file(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.tsv"
        haptic_bin_path = tmp_path / "haptic.bin"
        events_file, haptic_file = DataLoggerProcess._open_files(
            events_path, haptic_bin_path,
        )
        DataLoggerProcess._close_files(events_file, haptic_file)
        assert haptic_bin_path.exists()
        assert events_path.exists()

    def test_close_flushes_and_files_readable(self, tmp_path: Path) -> None:
        events_path = tmp_path / "events.tsv"
        haptic_bin_path = tmp_path / "haptic.bin"
        events_file, haptic_file = DataLoggerProcess._open_files(
            events_path, haptic_bin_path,
        )
        events_file.write("1.0\t1\tstate\thold\t3\n")
        haptic_file.write(b"\x00" * 80)
        DataLoggerProcess._close_files(events_file, haptic_file)

        # Files should be readable after close
        text = events_path.read_text()
        assert "hold" in text
        binary = haptic_bin_path.read_bytes()
        assert len(binary) == 80

    def test_close_handles_none_files(self) -> None:
        # Should not raise
        DataLoggerProcess._close_files(None, None)


# ---------------------------------------------------------------------------
# TestDataLoggerProcessConstruction
# ---------------------------------------------------------------------------


class TestDataLoggerProcessConstruction:
    def _make_zmq_config(self) -> Any:
        from hapticore.core.config import ZMQConfig
        return ZMQConfig()

    def test_ready_event_is_optional(self, tmp_path: Path) -> None:
        proc = DataLoggerProcess(
            session_dir=tmp_path,
            session_id="ses-20260101_001",
            zmq_config=self._make_zmq_config(),
        )
        assert proc.name == "DataLoggerProcess"

    def test_request_shutdown_sets_event(self, tmp_path: Path) -> None:
        proc = DataLoggerProcess(
            session_dir=tmp_path,
            session_id="ses-20260101_001",
            zmq_config=self._make_zmq_config(),
        )
        assert not proc._shutdown.is_set()
        proc.request_shutdown()
        assert proc._shutdown.is_set()

    def test_is_daemon(self, tmp_path: Path) -> None:
        proc = DataLoggerProcess(
            session_dir=tmp_path,
            session_id="ses-20260101_001",
            zmq_config=self._make_zmq_config(),
        )
        assert proc.daemon is True


# ---------------------------------------------------------------------------
# TestSessionManagerDataLoggerIntegration
# ---------------------------------------------------------------------------


class TestSessionManagerDataLoggerIntegration:
    """Tests for DataLoggerProcess integration in SessionManager."""

    def _minimal_config(self, tmp_path: Path, *, data_logging_enabled: bool) -> ExperimentConfig:
        return ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monkey"),
            haptic=HapticConfig(backend="mock"),
            display=DisplayConfig(backend="mock"),
            recording=RecordingConfig(
                save_dir=tmp_path,
                granularity="session",
                data_logging_enabled=data_logging_enabled,
            ),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            sync=SyncConfig(backend="mock"),
        )

    def _make_ready_proc_mock(self) -> tuple[MagicMock, Any]:
        """Create a mock DataLoggerProcess that sets ready_event on start()."""
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

    def test_data_logger_started_when_enabled(self, tmp_path: Path) -> None:
        from hapticore.session import SessionManager

        config = self._minimal_config(tmp_path, data_logging_enabled=True)
        fake_proc, make_and_capture = self._make_ready_proc_mock()

        with patch(
            "hapticore.session.manager.DataLoggerProcess",
            side_effect=make_and_capture,
        ) as mock_cls:
            mgr = SessionManager(config)
            mgr.start()
            try:
                mock_cls.assert_called_once()
                fake_proc.start.assert_called_once()
            finally:
                mgr.stop()

    def test_data_logger_not_started_when_disabled(self, tmp_path: Path) -> None:
        from hapticore.session import SessionManager

        config = self._minimal_config(tmp_path, data_logging_enabled=False)

        with patch(
            "hapticore.session.manager.DataLoggerProcess",
        ) as mock_cls:
            mgr = SessionManager(config)
            mgr.start()
            try:
                mock_cls.assert_not_called()
            finally:
                mgr.stop()

    def test_data_logger_shutdown_on_stop(self, tmp_path: Path) -> None:
        from hapticore.session import SessionManager

        config = self._minimal_config(tmp_path, data_logging_enabled=True)
        fake_proc, make_and_capture = self._make_ready_proc_mock()
        fake_proc.is_alive.return_value = False

        with patch(
            "hapticore.session.manager.DataLoggerProcess",
            side_effect=make_and_capture,
        ):
            mgr = SessionManager(config)
            mgr.start()
            mgr.stop()

        fake_proc.request_shutdown.assert_called_once()
        fake_proc.join.assert_called()

    def test_data_logger_receives_session_dir(self, tmp_path: Path) -> None:
        from hapticore.session import SessionManager

        config = self._minimal_config(tmp_path, data_logging_enabled=True)
        captured_kwargs: dict[str, Any] = {}
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True

        def capture(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            ready = kwargs.get("ready_event")

            def start_and_set() -> None:
                if ready is not None:
                    ready.set()

            fake_proc.start.side_effect = start_and_set
            return fake_proc

        with patch(
            "hapticore.session.manager.DataLoggerProcess",
            side_effect=capture,
        ):
            mgr = SessionManager(config)
            mgr.start()
            try:
                assert mgr.session_dir is not None
                assert captured_kwargs.get("session_dir") == mgr.session_dir
            finally:
                mgr.stop()

    def test_data_logger_receives_session_id(self, tmp_path: Path) -> None:
        from hapticore.session import SessionManager

        config = self._minimal_config(tmp_path, data_logging_enabled=True)
        captured_kwargs: dict[str, Any] = {}
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = True

        def capture(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            ready = kwargs.get("ready_event")

            def start_and_set() -> None:
                if ready is not None:
                    ready.set()

            fake_proc.start.side_effect = start_and_set
            return fake_proc

        with patch(
            "hapticore.session.manager.DataLoggerProcess",
            side_effect=capture,
        ):
            mgr = SessionManager(config)
            mgr.start()
            try:
                assert mgr.session_id is not None
                assert captured_kwargs.get("session_id") == mgr.session_id
            finally:
                mgr.stop()

    def test_receipt_recording_section_has_data_logging_enabled(
        self, tmp_path: Path,
    ) -> None:
        from hapticore.session import SessionManager

        config = self._minimal_config(tmp_path, data_logging_enabled=False)
        mgr = SessionManager(config)
        mgr.start()
        mgr.stop()

        assert mgr.session_dir is not None
        receipt_path = mgr.session_dir / "session_receipt.json"
        with receipt_path.open() as f:
            receipt = json.load(f)
        assert "data_logging_enabled" in receipt["recording"]
        assert receipt["recording"]["data_logging_enabled"] is False

    def test_receipt_recording_systems_includes_data_logger(
        self, tmp_path: Path,
    ) -> None:
        from hapticore.session import SessionManager

        config = self._minimal_config(tmp_path, data_logging_enabled=True)
        fake_proc, make_and_capture = self._make_ready_proc_mock()
        fake_proc.is_alive.return_value = False

        with patch(
            "hapticore.session.manager.DataLoggerProcess",
            side_effect=make_and_capture,
        ):
            mgr = SessionManager(config)
            mgr.start()
            mgr.stop()

        assert mgr.session_dir is not None
        receipt_path = mgr.session_dir / "session_receipt.json"
        with receipt_path.open() as f:
            receipt = json.load(f)
        assert "data_logger" in receipt["hardware"]["recording_systems"]


# ---------------------------------------------------------------------------
# TestConfigChanges
# ---------------------------------------------------------------------------


class TestConfigChanges:
    def test_recording_config_has_data_logging_enabled(self) -> None:
        cfg = RecordingConfig()
        assert hasattr(cfg, "data_logging_enabled")
        assert cfg.data_logging_enabled is True

    def test_recording_config_no_lsl_fields(self) -> None:
        cfg = RecordingConfig()
        assert not hasattr(cfg, "lsl_enabled")
        assert not hasattr(cfg, "lsl_stream_name")

    def test_data_logging_enabled_can_be_set_false(self) -> None:
        cfg = RecordingConfig(data_logging_enabled=False)
        assert cfg.data_logging_enabled is False

    def test_recording_config_default_data_logging_enabled_true(self) -> None:
        cfg = RecordingConfig()
        assert cfg.data_logging_enabled is True
