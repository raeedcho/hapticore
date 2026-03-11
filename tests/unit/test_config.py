"""Tests for Pydantic configuration models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hapticore.core.config import (
    ExperimentConfig,
    HapticConfig,
    SubjectConfig,
    TaskConfig,
    load_config,
)


CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"


class TestLoadConfig:
    """Tests for loading configuration from YAML."""

    def test_example_config_loads(self) -> None:
        config = load_config(CONFIGS_DIR / "example_config.yaml")
        assert config.experiment_name == "center_out_reaching"
        assert config.subject.subject_id == "monkey_M"
        assert config.task.task_class == "hapticore.tasks.center_out.CenterOutTask"

    def test_round_trip(self) -> None:
        config = load_config(CONFIGS_DIR / "example_config.yaml")
        dumped = config.model_dump()
        restored = ExperimentConfig.model_validate(dumped)
        assert restored.experiment_name == config.experiment_name
        assert restored.subject.subject_id == config.subject.subject_id
        assert restored.haptic.force_limit_n == config.haptic.force_limit_n


class TestRequiredFields:
    """Tests for required field validation."""

    def test_missing_experiment_name(self) -> None:
        with pytest.raises(ValidationError):
            ExperimentConfig(
                subject=SubjectConfig(subject_id="test"),
                task=TaskConfig(task_class="hapticore.tasks.example.Task"),
            )  # type: ignore[call-arg]

    def test_missing_subject_id(self) -> None:
        with pytest.raises(ValidationError):
            SubjectConfig()  # type: ignore[call-arg]

    def test_missing_task_class(self) -> None:
        with pytest.raises(ValidationError):
            TaskConfig()  # type: ignore[call-arg]


class TestValueConstraints:
    """Tests for field value constraints."""

    def test_force_limit_too_high(self) -> None:
        with pytest.raises(ValidationError):
            HapticConfig(force_limit_n=50.0)

    def test_force_limit_zero(self) -> None:
        with pytest.raises(ValidationError):
            HapticConfig(force_limit_n=0.0)

    def test_negative_refresh_rate(self) -> None:
        from hapticore.core.config import DisplayConfig

        with pytest.raises(ValidationError):
            DisplayConfig(refresh_rate_hz=-1)

    def test_invalid_randomization(self) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(task_class="hapticore.tasks.example.Task", randomization="invalid")


class TestDefaults:
    """Tests for default value application."""

    def test_haptic_defaults(self) -> None:
        config = HapticConfig()
        assert config.force_limit_n == 20.0
        assert config.publish_rate_hz == 200.0
        assert config.server_address == "localhost"

    def test_experiment_config_defaults(self) -> None:
        config = ExperimentConfig(
            experiment_name="test",
            subject=SubjectConfig(subject_id="test_subject"),
            task=TaskConfig(task_class="hapticore.tasks.example.Task"),
        )
        assert config.haptic.force_limit_n == 20.0
        assert config.display.fullscreen is True
        assert config.recording.lsl_enabled is True
        assert config.sync.sync_pulse_rate_hz == 1.0
        assert config.zmq.transport == "ipc"

    def test_subject_species_default(self) -> None:
        config = SubjectConfig(subject_id="test")
        assert config.species == "macaque"
