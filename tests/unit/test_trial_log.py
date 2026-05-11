"""Tests for TrialManager.write_trial_log() and SessionManager trial log integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
from hapticore.session import SessionManager
from hapticore.tasks.trial_manager import TrialManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tm(
    conditions: list[dict] | None = None,
    block_size: int = 2,
    num_blocks: int | None = 1,
) -> TrialManager:
    if conditions is None:
        conditions = [{"target_id": 0}, {"target_id": 1}]
    return TrialManager(
        conditions=conditions,
        block_size=block_size,
        num_blocks=num_blocks,
        randomization="sequential",
    )


@pytest.fixture
def minimal_config(tmp_path: Path) -> ExperimentConfig:
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
# TestWriteTrialLog
# ---------------------------------------------------------------------------


class TestWriteTrialLog:
    def test_writes_header_with_no_trials(self, tmp_path: Path) -> None:
        tm = _make_tm()
        out = tmp_path / "trials.tsv"
        tm.write_trial_log(out)

        assert out.exists()
        lines = out.read_text().splitlines()
        assert lines == ["trial_number\tblock_number\toutcome\tcondition"]

    def test_basic_trial_output(self, tmp_path: Path) -> None:
        tm = _make_tm(num_blocks=2)
        tm.advance()
        tm.log_trial("success")
        tm.advance()
        tm.log_trial("timeout")
        tm.advance()
        tm.log_trial("success")

        out = tmp_path / "trials.tsv"
        tm.write_trial_log(out)

        lines = out.read_text().splitlines()
        assert len(lines) == 4  # header + 3 rows

        header = lines[0]
        assert header == "trial_number\tblock_number\toutcome\tcondition"

        for i, (line, expected_outcome) in enumerate(
            zip(lines[1:], ["success", "timeout", "success"])
        ):
            cols = line.split("\t")
            assert cols[0] == str(i), f"trial_number mismatch row {i}"
            assert cols[2] == expected_outcome, f"outcome mismatch row {i}"
            # condition column must be valid JSON
            json.loads(cols[3])

    def test_condition_is_json_encoded(self, tmp_path: Path) -> None:
        condition = {"target_angle": 90, "position": [0.08, 0.0]}
        tm = TrialManager(
            conditions=[condition],
            block_size=1,
            num_blocks=1,
            randomization="sequential",
        )
        tm.advance()
        tm.log_trial("success")

        out = tmp_path / "trials.tsv"
        tm.write_trial_log(out)

        lines = out.read_text().splitlines()
        assert len(lines) == 2
        cols = lines[1].split("\t")
        parsed = json.loads(cols[3])
        assert parsed == condition

    def test_extra_data_columns(self, tmp_path: Path) -> None:
        tm = _make_tm()
        tm.advance()
        tm.log_trial("success", reaction_time=0.345, peak_velocity=0.12)

        out = tmp_path / "trials.tsv"
        tm.write_trial_log(out)

        lines = out.read_text().splitlines()
        header_cols = lines[0].split("\t")
        assert "reaction_time" in header_cols
        assert "peak_velocity" in header_cols

        row_cols = lines[1].split("\t")
        rt_idx = header_cols.index("reaction_time")
        pv_idx = header_cols.index("peak_velocity")
        assert row_cols[rt_idx] == "0.345"
        assert row_cols[pv_idx] == "0.12"

    def test_missing_extra_data_uses_empty_string(self, tmp_path: Path) -> None:
        tm = _make_tm()
        tm.advance()
        tm.log_trial("success", reaction_time=0.5)
        tm.advance()
        tm.log_trial("timeout")  # no reaction_time

        out = tmp_path / "trials.tsv"
        tm.write_trial_log(out)

        lines = out.read_text().splitlines()
        header_cols = lines[0].split("\t")
        rt_idx = header_cols.index("reaction_time")

        row0 = lines[1].split("\t")
        row1 = lines[2].split("\t")
        assert row0[rt_idx] == "0.5"
        assert row1[rt_idx] == ""

    def test_multiple_blocks(self, tmp_path: Path) -> None:
        tm = TrialManager(
            conditions=[{"target_id": 0}, {"target_id": 1}],
            block_size=2,
            num_blocks=2,
            randomization="sequential",
        )
        for _ in range(4):
            tm.advance()
            tm.log_trial("success")

        out = tmp_path / "trials.tsv"
        tm.write_trial_log(out)

        lines = out.read_text().splitlines()
        assert len(lines) == 5  # header + 4 rows

        header_cols = lines[0].split("\t")
        bn_idx = header_cols.index("block_number")

        block_numbers = [lines[i + 1].split("\t")[bn_idx] for i in range(4)]
        assert block_numbers == ["0", "0", "1", "1"]


# ---------------------------------------------------------------------------
# TestSessionManagerTrialLogIntegration
# ---------------------------------------------------------------------------


class TestSessionManagerTrialLogIntegration:
    def test_stop_writes_trial_log(self, minimal_config: ExperimentConfig) -> None:
        mgr = SessionManager(minimal_config)
        trial_manager = TrialManager(
            conditions=[{"target_angle": 0}, {"target_angle": 90}],
            block_size=2,
            num_blocks=1,
            randomization="sequential",
        )
        mgr.set_trial_manager(trial_manager)
        mgr.start()

        trial_manager.advance()
        trial_manager.log_trial("success")
        trial_manager.advance()
        trial_manager.log_trial("timeout")

        mgr.stop()

        assert mgr.session_dir is not None
        assert mgr.session_id is not None
        tsv_path = mgr.session_dir / "behavior" / f"{mgr.session_id}_trials.tsv"
        assert tsv_path.exists()

        lines = tsv_path.read_text().splitlines()
        # header + 2 trial rows
        assert len(lines) == 3

    def test_stop_without_trial_manager_skips_trial_log(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        mgr.start()
        mgr.stop()

        assert mgr.session_dir is not None
        behavior_dir = mgr.session_dir / "behavior"
        tsv_files = list(behavior_dir.glob("*_trials.tsv"))
        assert tsv_files == []

    def test_trial_log_failure_does_not_prevent_receipt(
        self, minimal_config: ExperimentConfig,
    ) -> None:
        mgr = SessionManager(minimal_config)
        trial_manager = TrialManager(
            conditions=[{"target_angle": 0}],
            block_size=1,
            num_blocks=1,
        )
        mgr.set_trial_manager(trial_manager)
        mgr.start()

        with patch.object(trial_manager, "write_trial_log", side_effect=IOError("disk full")):
            mgr.stop()

        assert mgr.session_dir is not None
        receipt_path = mgr.session_dir / "session_receipt.json"
        assert receipt_path.exists()
        with receipt_path.open() as f:
            receipt = json.load(f)
        assert "session_id" in receipt
