"""Unit tests for StatusDashboardProcess logic.

Tests cover:
- DashboardConfig.status_enabled field
- BaseTask.log_trial() condition inclusion in TrialEvent data
- Progress computation helpers (compute_block_index, compute_trial_within_block)
- Outcome color mapping (outcome_color)
- Block success rate color (block_success_rate_color)
- SessionManager gating for the status dashboard

Does NOT test Qt rendering — that lives in tests/display/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from hapticore.core.config import (
    DashboardConfig,
    DisplayConfig,
    ExperimentConfig,
    HapticConfig,
    RecordingConfig,
    SubjectConfig,
    SyncConfig,
    TaskConfig,
)
from hapticore.dashboard.status_dashboard import (
    block_success_rate_color,
    compute_block_index,
    compute_trial_within_block,
    outcome_color,
)


# ---------------------------------------------------------------------------
# DashboardConfig.status_enabled
# ---------------------------------------------------------------------------


class TestDashboardConfigStatusEnabled:
    def test_default_is_true(self) -> None:
        cfg = DashboardConfig()
        assert cfg.status_enabled is True

    def test_can_set_false(self) -> None:
        cfg = DashboardConfig(status_enabled=False)
        assert cfg.status_enabled is False

    def test_round_trip_model_dump(self) -> None:
        cfg = DashboardConfig(status_enabled=False)
        dumped = cfg.model_dump()
        assert dumped["status_enabled"] is False
        cfg2 = DashboardConfig.model_validate(dumped)
        assert cfg2.status_enabled is False

    def test_round_trip_through_experiment_config(self, tmp_path: Path) -> None:
        exp = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monk"),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=8,
                num_blocks=4,
            ),
            dashboard=DashboardConfig(status_enabled=True),
        )
        dumped = exp.model_dump()
        assert dumped["dashboard"]["status_enabled"] is True
        exp2 = ExperimentConfig.model_validate(dumped)
        assert exp2.dashboard is not None
        assert exp2.dashboard.status_enabled is True


# ---------------------------------------------------------------------------
# BaseTask.log_trial() — condition dict inclusion
# ---------------------------------------------------------------------------


class TestLogTrialConditionInclusion:
    """Verify log_trial() includes the current condition dict in TrialEvent.data."""

    def _make_task(self) -> Any:
        from hapticore.tasks.base import BaseTask, ParamSpec

        class MinimalTask(BaseTask):
            PARAMS = {"hold_time": ParamSpec(type=float, default=0.5)}
            STATES = ["iti", "active", "done"]
            TRANSITIONS = [
                {"trigger": "start", "source": "iti", "dest": "active"},
                {"trigger": "finish", "source": "active", "dest": "done"},
            ]
            INITIAL_STATE = "iti"

        task = MinimalTask()
        task.current_condition = {"target_id": 3, "position": [0.08, 0]}
        task.trial_number = 5

        # Wire minimal event_bus and trial_manager mocks
        captured: list[Any] = []
        mock_bus = MagicMock()
        mock_bus.publish.side_effect = lambda topic, payload: captured.append(payload)
        task.event_bus = mock_bus

        mock_tm = MagicMock()
        task.trial_manager = mock_tm

        return task, captured

    def test_condition_key_present(self) -> None:
        import msgpack

        task, captured = self._make_task()
        task.log_trial("success")

        assert len(captured) == 1
        msg = msgpack.unpackb(captured[0], raw=False)
        assert "condition" in msg["data"]

    def test_condition_value_matches_current_condition(self) -> None:
        import msgpack

        task, captured = self._make_task()
        task.log_trial("success")

        msg = msgpack.unpackb(captured[0], raw=False)
        assert msg["data"]["condition"] == {"target_id": 3, "position": [0.08, 0]}

    def test_outcome_still_present(self) -> None:
        import msgpack

        task, captured = self._make_task()
        task.log_trial("timeout")

        msg = msgpack.unpackb(captured[0], raw=False)
        assert msg["data"]["outcome"] == "timeout"

    def test_extra_data_still_propagates(self) -> None:
        import msgpack

        task, captured = self._make_task()
        task.log_trial("success", reaction_time=0.42)

        msg = msgpack.unpackb(captured[0], raw=False)
        assert msg["data"]["reaction_time"] == pytest.approx(0.42)
        assert msg["data"]["condition"] == {"target_id": 3, "position": [0.08, 0]}

    def test_condition_is_a_copy(self) -> None:
        """Mutating current_condition after log_trial does not affect the event."""
        import msgpack

        task, captured = self._make_task()
        task.log_trial("success")
        task.current_condition["target_id"] = 99  # mutate after the fact

        msg = msgpack.unpackb(captured[0], raw=False)
        assert msg["data"]["condition"]["target_id"] == 3


# ---------------------------------------------------------------------------
# Progress computation
# ---------------------------------------------------------------------------


class TestProgressComputation:
    def test_block_index_trial_0(self) -> None:
        assert compute_block_index(0, 8) == 0

    def test_block_index_trial_7(self) -> None:
        assert compute_block_index(7, 8) == 0

    def test_block_index_trial_8(self) -> None:
        assert compute_block_index(8, 8) == 1

    def test_block_index_trial_14(self) -> None:
        """Example from the issue: trial 14, block_size 8 → block 1."""
        assert compute_block_index(14, 8) == 1

    def test_trial_within_block_trial_14(self) -> None:
        """Example from the issue: trial 14, block_size 8 → index 6."""
        assert compute_trial_within_block(14, 8) == 6

    def test_trial_within_block_first(self) -> None:
        assert compute_trial_within_block(0, 8) == 0

    def test_trial_within_block_last_of_block(self) -> None:
        assert compute_trial_within_block(7, 8) == 7

    def test_trial_within_block_first_of_second_block(self) -> None:
        assert compute_trial_within_block(8, 8) == 0

    def test_open_ended_detection(self) -> None:
        """num_blocks=None signals open-ended session."""
        from hapticore.core.config import TaskConfig

        cfg = TaskConfig(
            task_class="hapticore.tasks.center_out.CenterOutTask",
            conditions=[{"target_angle": 0}],
            block_size=8,
            num_blocks=None,
        )
        assert cfg.num_blocks is None


# ---------------------------------------------------------------------------
# Outcome color mapping
# ---------------------------------------------------------------------------


class TestOutcomeColorMapping:
    def test_success_is_green(self) -> None:
        assert outcome_color("success") == "#4CAF50"

    def test_spill_is_red(self) -> None:
        assert outcome_color("spill") == "#F44336"

    def test_failure_is_red(self) -> None:
        assert outcome_color("failure") == "#F44336"

    def test_timeout_is_orange(self) -> None:
        assert outcome_color("timeout") == "#FF9800"

    def test_abort_is_orange(self) -> None:
        assert outcome_color("abort") == "#FF9800"

    def test_unknown_outcome_is_yellow(self) -> None:
        assert outcome_color("something_else") == "#FFEB3B"
        assert outcome_color("") == "#FFEB3B"


# ---------------------------------------------------------------------------
# Block success rate color
# ---------------------------------------------------------------------------


class TestBlockSuccessRateColor:
    def test_full_success_is_green(self) -> None:
        r, g, b = block_success_rate_color(1.0)
        assert g == 255
        assert r == 0
        assert b == 0

    def test_zero_success_is_red(self) -> None:
        r, g, b = block_success_rate_color(0.0)
        assert r == 255
        assert g == 0
        assert b == 0

    def test_half_success_is_yellow(self) -> None:
        r, g, b = block_success_rate_color(0.5)
        # 50% → hue 60° → yellow (both R and G at max, B=0)
        assert r == 255
        assert g == 255
        assert b == 0

    def test_clamped_above_1(self) -> None:
        """Values above 1.0 are clamped to 1.0 (green)."""
        r, g, b = block_success_rate_color(1.5)
        assert g == 255
        assert r == 0

    def test_clamped_below_0(self) -> None:
        """Values below 0.0 are clamped to 0.0 (red)."""
        r, g, b = block_success_rate_color(-0.5)
        assert r == 255
        assert g == 0

    def test_returns_ints(self) -> None:
        r, g, b = block_success_rate_color(0.75)
        assert isinstance(r, int)
        assert isinstance(g, int)
        assert isinstance(b, int)

    def test_intermediate_values_vary_monotonically(self) -> None:
        """Green channel should increase monotonically with success_rate."""
        rates = [0.0, 0.25, 0.5, 0.75, 1.0]
        green_vals = [block_success_rate_color(r)[1] for r in rates]
        # Green goes from 0 (at 0% success, red) to 255 (at 100% success, green)
        assert green_vals == sorted(green_vals)


# ---------------------------------------------------------------------------
# SessionManager gating for status dashboard
# ---------------------------------------------------------------------------


def _minimal_config(tmp_path: Path, *, dashboard: Any = None) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_name="test",
        subject=SubjectConfig(subject_id="monk"),
        haptic=HapticConfig(backend="mock"),
        display=DisplayConfig(backend="mock"),
        recording=RecordingConfig(
            save_dir=tmp_path,
            data_logging_enabled=False,
        ),
        task=TaskConfig(
            task_class="hapticore.tasks.center_out.CenterOutTask",
            conditions=[{"target_angle": 0}],
            block_size=4,
            num_blocks=2,
        ),
        sync=SyncConfig(backend="mock"),
        dashboard=dashboard,
    )


class TestSessionManagerStatusDashboardGating:
    """Verify SessionManager launches/skips StatusDashboardProcess correctly."""

    def test_no_dashboard_config_no_status_dashboard(self, tmp_path: Path) -> None:
        """dashboard=None → no StatusDashboardProcess launched."""
        from hapticore.session import SessionManager

        cfg = _minimal_config(tmp_path, dashboard=None)
        sm = SessionManager(cfg)
        sm.start()
        try:
            assert sm._status_dashboard_proc is None
        finally:
            sm.stop()

    def test_dashboard_status_enabled_false_no_dashboard(self, tmp_path: Path) -> None:
        """status_enabled=False → no StatusDashboardProcess launched."""
        from hapticore.session import SessionManager

        cfg = _minimal_config(tmp_path, dashboard=DashboardConfig(status_enabled=False))
        sm = SessionManager(cfg)
        sm.start()
        try:
            assert sm._status_dashboard_proc is None
        finally:
            sm.stop()

    def test_dashboard_status_enabled_true_mock_backend_launches(
        self, tmp_path: Path
    ) -> None:
        """status_enabled=True with mock display backend → StatusDashboardProcess IS launched.

        Unlike workspace mirror, status dashboard works with mock backend.
        The process is started but we just check it was instantiated
        (without waiting for Qt window creation in a no-display environment).
        """
        from hapticore.session import SessionManager
        from hapticore.dashboard.status_dashboard import StatusDashboardProcess

        cfg = _minimal_config(tmp_path, dashboard=DashboardConfig(status_enabled=True))
        sm = SessionManager(cfg)

        # We cannot actually start the Qt process in a headless unit test
        # environment (no DISPLAY).  Patch _start_status_dashboard to verify
        # the gating logic reaches it, without spawning a real Qt process.
        reached: list[bool] = []

        original = sm._start_status_dashboard

        def patched_start() -> None:
            reached.append(True)
            # Create a minimal proc object without starting it
            sm._status_dashboard_proc = MagicMock(spec=StatusDashboardProcess)
            sm._status_dashboard_started = True

        sm._start_status_dashboard = patched_start  # type: ignore[method-assign]
        sm.start()
        try:
            assert reached, "Expected _start_status_dashboard to be called"
        finally:
            sm._stop_status_dashboard = MagicMock()  # type: ignore[method-assign]
            sm.stop()
