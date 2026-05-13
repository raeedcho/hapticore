"""Unit tests for WorkspaceMirrorProcess data logic.

Tests cover:
- DashboardConfig validation
- SessionManager integration (backend gating)

Does NOT test PsychoPy rendering — that lives in tests/display/.
"""

from __future__ import annotations

import collections
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pydantic import ValidationError

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


# ---------------------------------------------------------------------------
# DashboardConfig validation
# ---------------------------------------------------------------------------


class TestDashboardConfig:
    def test_default_construction(self) -> None:
        cfg = DashboardConfig()
        assert cfg.screen == 0
        assert cfg.resolution == (1920, 1080)
        assert cfg.background_color == [0.0, 0.0, 0.0]
        assert cfg.mirror_horizontal is False
        assert cfg.trail_length == 40
        assert cfg.trail_color == [0.3, 0.8, 1.0]
        assert cfg.force_arrow_scale == 0.01
        assert cfg.force_arrow_color == [1.0, 0.3, 0.3]

    def test_trail_length_zero(self) -> None:
        cfg = DashboardConfig(trail_length=0)
        assert cfg.trail_length == 0

    def test_trail_length_max(self) -> None:
        cfg = DashboardConfig(trail_length=200)
        assert cfg.trail_length == 200

    def test_trail_length_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            DashboardConfig(trail_length=-1)

    def test_trail_length_rejects_above_200(self) -> None:
        with pytest.raises(ValidationError):
            DashboardConfig(trail_length=201)

    def test_force_arrow_scale_rejects_nonpositive(self) -> None:
        with pytest.raises(ValidationError):
            DashboardConfig(force_arrow_scale=0.0)
        with pytest.raises(ValidationError):
            DashboardConfig(force_arrow_scale=-0.1)

    def test_round_trip_through_experiment_config(self, tmp_path: Path) -> None:
        """DashboardConfig round-trips through ExperimentConfig model_dump/model_validate."""
        cfg = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monk"),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
            dashboard=DashboardConfig(trail_length=20, force_arrow_scale=0.05),
        )
        dumped = cfg.model_dump()
        assert dumped["dashboard"]["trail_length"] == 20
        assert dumped["dashboard"]["force_arrow_scale"] == pytest.approx(0.05)
        # Validate that it round-trips through model_validate
        cfg2 = ExperimentConfig.model_validate(dumped)
        assert cfg2.dashboard is not None
        assert cfg2.dashboard.trail_length == 20
        assert cfg2.dashboard.force_arrow_scale == pytest.approx(0.05)

    def test_experiment_config_no_dashboard_by_default(self) -> None:
        cfg = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="monk"),
            task=TaskConfig(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                conditions=[{"target_angle": 0}],
                block_size=1,
                num_blocks=1,
            ),
        )
        assert cfg.dashboard is None


# ---------------------------------------------------------------------------
# SessionManager integration — workspace mirror gating
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
            block_size=1,
            num_blocks=1,
        ),
        sync=SyncConfig(backend="mock"),
        dashboard=dashboard,
    )


class TestSessionManagerWorkspaceMirrorGating:
    """Verify SessionManager does not launch WorkspaceMirrorProcess for mock backends."""

    def test_no_dashboard_config_no_mirror(self, tmp_path: Path) -> None:
        """No dashboard block → no WorkspaceMirrorProcess."""
        from hapticore.session import SessionManager

        cfg = _minimal_config(tmp_path, dashboard=None)
        sm = SessionManager(cfg)
        sm.start()
        try:
            assert sm._workspace_mirror_proc is None
        finally:
            sm.stop()

    def test_dashboard_with_mock_backend_no_mirror(self, tmp_path: Path) -> None:
        """Dashboard config present but display.backend='mock' → no mirror."""
        from hapticore.session import SessionManager

        cfg = _minimal_config(tmp_path, dashboard=DashboardConfig())
        sm = SessionManager(cfg)
        sm.start()
        try:
            assert sm._workspace_mirror_proc is None
        finally:
            sm.stop()
