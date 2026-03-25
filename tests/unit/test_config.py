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
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "configs"


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
        assert restored == config


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

    def test_num_blocks_none_allowed(self) -> None:
        """num_blocks=None represents an open-ended session."""
        config = TaskConfig(task_class="hapticore.tasks.example.Task", num_blocks=None)
        assert config.num_blocks is None

    def test_num_blocks_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(task_class="hapticore.tasks.example.Task", num_blocks=0)


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


class TestLayeredMerge:
    """Tests for layered YAML configuration merging."""

    def test_rig_subject_task_layers(self) -> None:
        """Load rig + subject + task + experiment YAMLs, all sections present."""
        config = load_config(
            FIXTURES_DIR / "rig.yaml",
            FIXTURES_DIR / "subject.yaml",
            FIXTURES_DIR / "task.yaml",
            FIXTURES_DIR / "experiment.yaml",
        )
        assert config.experiment_name == "test_experiment"
        assert config.subject.subject_id == "test_monkey"
        assert config.task.task_class == "hapticore.tasks.center_out.CenterOutTask"
        assert config.haptic.force_limit_n == 15.0
        assert config.sync.teensy_port == "/dev/ttyACM0"

    def test_rig_task_no_subject(self) -> None:
        """Load rig + task (no subject YAML), subject from overrides."""
        config = load_config(
            FIXTURES_DIR / "rig.yaml",
            FIXTURES_DIR / "task.yaml",
            FIXTURES_DIR / "experiment.yaml",
            overrides={"subject": {"subject_id": "fallback_monkey"}},
        )
        assert config.subject.subject_id == "fallback_monkey"
        assert config.subject.species == "macaque"  # default
        assert config.haptic.force_limit_n == 15.0

    def test_later_file_wins(self) -> None:
        """When two files both set haptic.force_limit_n, the later file wins."""
        config = load_config(
            FIXTURES_DIR / "rig.yaml",
            FIXTURES_DIR / "subject.yaml",
            FIXTURES_DIR / "task.yaml",
            FIXTURES_DIR / "experiment.yaml",
            CONFIGS_DIR / "example_config.yaml",  # sets force_limit_n=20.0
        )
        assert config.haptic.force_limit_n == 20.0  # example_config overrides rig's 15.0

    def test_deep_merge_preserves_other_fields(self) -> None:
        """Rig sets workspace_bounds and force_limit_n; task file overrides publish_rate_hz.

        Deep merge ensures workspace_bounds and force_limit_n from the rig layer
        are preserved even though the later task file also contains a haptic section.
        """
        config = load_config(
            FIXTURES_DIR / "rig.yaml",
            FIXTURES_DIR / "subject.yaml",
            FIXTURES_DIR / "task_with_haptic_override.yaml",
            FIXTURES_DIR / "experiment.yaml",
        )
        # From rig.yaml
        assert config.haptic.force_limit_n == 15.0
        assert config.haptic.workspace_bounds["x"] == [-0.10, 0.10]
        # From task_with_haptic_override.yaml
        assert config.haptic.publish_rate_hz == 500.0

    def test_layered_loading_from_configs_dir(self) -> None:
        """Load from the real configs/ layered directory."""
        config = load_config(
            CONFIGS_DIR / "rig" / "default.yaml",
            CONFIGS_DIR / "subject" / "example_subject.yaml",
            CONFIGS_DIR / "task" / "center_out.yaml",
            CONFIGS_DIR / "example_experiment.yaml",
        )
        assert config.experiment_name == "center_out_reaching"
        assert config.subject.subject_id == "monkey_M"
        assert config.task.task_class == "hapticore.tasks.center_out.CenterOutTask"
        assert config.task.block_size == 8

    def test_overrides_take_priority(self) -> None:
        """Constructor overrides take priority over YAML values."""
        config = load_config(
            CONFIGS_DIR / "example_config.yaml",
            overrides={"experiment_name": "overridden"},
        )
        assert config.experiment_name == "overridden"


class TestEnvVarOverride:
    """Tests for environment variable overrides."""

    def test_env_overrides_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HAPTICORE_ env vars override YAML values."""
        monkeypatch.setenv("HAPTICORE_HAPTIC__FORCE_LIMIT_N", "15.0")
        config = load_config(CONFIGS_DIR / "example_config.yaml")
        assert config.haptic.force_limit_n == 15.0

    def test_env_nested_delimiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Double-underscore delimiter works for nested fields."""
        monkeypatch.setenv("HAPTICORE_DISPLAY__REFRESH_RATE_HZ", "120")
        config = load_config(CONFIGS_DIR / "example_config.yaml")
        assert config.display.refresh_rate_hz == 120

    def test_env_does_not_wipe_other_nested_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setting one nested env var preserves other nested defaults."""
        monkeypatch.setenv("HAPTICORE_HAPTIC__FORCE_LIMIT_N", "10.0")
        config = load_config(CONFIGS_DIR / "example_config.yaml")
        assert config.haptic.force_limit_n == 10.0
        assert config.haptic.publish_rate_hz == 200.0  # preserved from YAML


class TestSerializationCompatibility:
    """Tests for downstream serialization compatibility (SessionManager)."""

    def test_model_dump_json_round_trip(self) -> None:
        """model_dump_json() produces valid JSON that reconstructs the config."""
        config = load_config(CONFIGS_DIR / "example_config.yaml")
        json_str = config.model_dump_json()
        restored = ExperimentConfig.model_validate_json(json_str)
        assert restored == config

    def test_layered_model_dump_json_round_trip(self) -> None:
        """Layered config round-trips through JSON correctly."""
        config = load_config(
            FIXTURES_DIR / "rig.yaml",
            FIXTURES_DIR / "subject.yaml",
            FIXTURES_DIR / "task.yaml",
            FIXTURES_DIR / "experiment.yaml",
        )
        json_str = config.model_dump_json()
        restored = ExperimentConfig.model_validate_json(json_str)
        assert restored == config

    def test_model_dump_contains_all_sections(self) -> None:
        """model_dump() output contains all expected top-level keys."""
        config = load_config(CONFIGS_DIR / "example_config.yaml")
        dumped = config.model_dump()
        expected_keys = {
            "experiment_name", "subject", "haptic", "display",
            "recording", "task", "sync", "zmq",
        }
        assert set(dumped.keys()) == expected_keys
