"""Tests for Pydantic configuration models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hapticore.core.config import (
    EventCodeMap,
    ExperimentConfig,
    HapticConfig,
    RecordingConfig,
    RippleRecordingConfig,
    RippleSyncConfig,
    SubjectConfig,
    SyncConfig,
    TaskConfig,
    TeensyConfig,
    load_config,
    load_session_config,
)

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "configs"


class TestLoadConfig:
    """Tests for loading configuration from YAML."""

    def test_example_config_loads(self) -> None:
        config = load_config(CONFIGS_DIR / "example_flat_config.yaml")
        assert config.experiment_name == "center_out_reaching"
        assert config.subject.subject_id == "monkey_M"
        assert config.task.task_class == "hapticore.tasks.center_out.CenterOutTask"

    def test_round_trip(self) -> None:
        config = load_config(CONFIGS_DIR / "example_flat_config.yaml")
        dumped = config.model_dump()
        restored = ExperimentConfig.model_validate(dumped)
        assert restored.model_dump() == config.model_dump()


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
        assert config.sync.transport == "mock"
        assert config.sync.teensy is None

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
            CONFIGS_DIR / "example_flat_config.yaml",  # sets force_limit_n=20.0
        )
        assert config.haptic.force_limit_n == 20.0  # example_flat_config overrides rig's 15.0

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
            CONFIGS_DIR / "example_flat_config.yaml",
            overrides={"experiment_name": "overridden"},
        )
        assert config.experiment_name == "overridden"


class TestEnvVarOverride:
    """Tests for environment variable overrides."""

    def test_env_overrides_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HAPTICORE_ env vars override YAML values."""
        monkeypatch.setenv("HAPTICORE_HAPTIC__FORCE_LIMIT_N", "15.0")
        config = load_config(CONFIGS_DIR / "example_flat_config.yaml")
        assert config.haptic.force_limit_n == 15.0

    def test_env_nested_delimiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Double-underscore delimiter works for nested fields."""
        monkeypatch.setenv("HAPTICORE_DISPLAY__REFRESH_RATE_HZ", "120")
        config = load_config(CONFIGS_DIR / "example_flat_config.yaml")
        assert config.display.refresh_rate_hz == 120

    def test_env_does_not_wipe_other_nested_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setting one nested env var preserves other nested defaults."""
        monkeypatch.setenv("HAPTICORE_HAPTIC__FORCE_LIMIT_N", "10.0")
        config = load_config(CONFIGS_DIR / "example_flat_config.yaml")
        assert config.haptic.force_limit_n == 10.0
        assert config.haptic.publish_rate_hz == 200.0  # preserved from YAML


class TestSerializationCompatibility:
    """Tests for downstream serialization compatibility (SessionManager)."""

    def test_model_dump_json_round_trip(self) -> None:
        """model_dump_json() produces valid JSON that reconstructs the config."""
        config = load_config(CONFIGS_DIR / "example_flat_config.yaml")
        json_str = config.model_dump_json()
        restored = ExperimentConfig.model_validate_json(json_str)
        assert restored.model_dump() == config.model_dump()

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
        assert restored.model_dump() == config.model_dump()

    def test_model_dump_contains_all_sections(self) -> None:
        """model_dump() output contains all expected top-level keys."""
        config = load_config(CONFIGS_DIR / "example_flat_config.yaml")
        dumped = config.model_dump()
        expected_keys = {
            "experiment_name", "subject", "haptic", "display",
            "recording", "task", "sync", "zmq",
        }
        assert set(dumped.keys()) == expected_keys


class TestCliOverride:
    """Tests for CLI argument overrides."""

    def test_cli_overrides_yaml(self) -> None:
        """CLI arguments override YAML values."""
        config = load_config(
            CONFIGS_DIR / "example_flat_config.yaml",
            cli_parse_args=["--haptic.force_limit_n=30.0"],
        )
        assert config.haptic.force_limit_n == 30.0

    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI arguments take precedence over environment variables."""
        monkeypatch.setenv("HAPTICORE_HAPTIC__FORCE_LIMIT_N", "10.0")
        config = load_config(
            CONFIGS_DIR / "example_flat_config.yaml",
            cli_parse_args=["--haptic.force_limit_n=30.0"],
        )
        assert config.haptic.force_limit_n == 30.0


class TestLoadSessionConfig:
    """Tests for load_session_config() with required layers."""

    def test_session_config_loads(self) -> None:
        """All three required layers produce a valid config."""
        config = load_session_config(
            rig=CONFIGS_DIR / "rig" / "default.yaml",
            subject=CONFIGS_DIR / "subject" / "example_subject.yaml",
            task=CONFIGS_DIR / "task" / "center_out.yaml",
            extra=[CONFIGS_DIR / "example_experiment.yaml"],
        )
        assert config.experiment_name == "center_out_reaching"
        assert config.subject.subject_id == "monkey_M"
        assert config.task.task_class == "hapticore.tasks.center_out.CenterOutTask"

    def test_session_config_missing_rig_raises(self) -> None:
        """Omitting the rig argument raises TypeError at call time."""
        with pytest.raises(TypeError):
            load_session_config(  # type: ignore[call-arg]
                subject=CONFIGS_DIR / "subject" / "example_subject.yaml",
                task=CONFIGS_DIR / "task" / "center_out.yaml",
            )

    def test_session_config_missing_subject_raises(self) -> None:
        """Omitting the subject argument raises TypeError at call time."""
        with pytest.raises(TypeError):
            load_session_config(  # type: ignore[call-arg]
                rig=CONFIGS_DIR / "rig" / "default.yaml",
                task=CONFIGS_DIR / "task" / "center_out.yaml",
            )

    def test_session_config_missing_task_raises(self) -> None:
        """Omitting the task argument raises TypeError at call time."""
        with pytest.raises(TypeError):
            load_session_config(  # type: ignore[call-arg]
                rig=CONFIGS_DIR / "rig" / "default.yaml",
                subject=CONFIGS_DIR / "subject" / "example_subject.yaml",
            )


class TestEventCodeMap:
    def test_defaults_to_empty_maps(self) -> None:
        m = EventCodeMap()
        assert m.state_codes == {}
        assert m.event_codes == {}

    def test_populated_maps_load_from_dict(self) -> None:
        m = EventCodeMap.model_validate({
            "state_codes": {"reach": 10, "hold": 20},
            "event_codes": {"reward": 100},
        })
        assert m.state_codes == {"reach": 10, "hold": 20}
        assert m.event_codes == {"reward": 100}

    def test_rejects_non_int_state_code_value(self) -> None:
        with pytest.raises(ValidationError):
            EventCodeMap(state_codes={"reach": "ten"})  # type: ignore[dict-item]

    def test_rejects_non_int_event_code_value(self) -> None:
        with pytest.raises(ValidationError):
            EventCodeMap(event_codes={"reward": "one hundred"})  # type: ignore[dict-item]


class TestSyncConfigTransports:
    def test_default_transport_is_mock(self) -> None:
        cfg = SyncConfig()
        assert cfg.transport == "mock"

    def test_default_nested_blocks_are_none(self) -> None:
        cfg = SyncConfig()
        assert cfg.ripple is None
        assert cfg.teensy is None

    def test_ripple_scout_transport_with_populated_ripple_block(self) -> None:
        cfg = SyncConfig(
            transport="ripple_scout",
            ripple=RippleSyncConfig(sync_pulse_sma_index=2, event_code_digout_index=4),
        )
        assert cfg.transport == "ripple_scout"
        assert cfg.ripple is not None
        assert cfg.ripple.sync_pulse_sma_index == 2
        assert cfg.ripple.event_code_digout_index == 4

    def test_teensy_transport_with_populated_teensy_block(self) -> None:
        cfg = SyncConfig(
            transport="teensy",
            teensy=TeensyConfig(port="/dev/ttyUSB0", baud=9600),
        )
        assert cfg.transport == "teensy"
        assert cfg.teensy is not None
        assert cfg.teensy.port == "/dev/ttyUSB0"
        assert cfg.teensy.baud == 9600

    def test_invalid_transport_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SyncConfig(transport="bluetooth")  # type: ignore[arg-type]

    def test_code_map_round_trips_through_model(self) -> None:
        cfg = SyncConfig(
            code_map=EventCodeMap(
                state_codes={"reach": 10}, event_codes={"reward": 100},
            ),
        )
        dumped = cfg.model_dump()
        restored = SyncConfig.model_validate(dumped)
        assert restored.code_map.state_codes == {"reach": 10}
        assert restored.code_map.event_codes == {"reward": 100}

    def test_ripple_scout_auto_populates_ripple_block(self) -> None:
        cfg = SyncConfig(transport="ripple_scout")
        assert cfg.ripple is not None
        assert cfg.ripple.sync_pulse_sma_index == 0  # default
        assert cfg.teensy is None

    def test_teensy_auto_populates_teensy_block(self) -> None:
        cfg = SyncConfig(transport="teensy")
        assert cfg.teensy is not None
        assert cfg.teensy.port == "/dev/ttyACM0"  # default
        assert cfg.ripple is None

    def test_mock_transport_leaves_both_blocks_none(self) -> None:
        cfg = SyncConfig(transport="mock")
        assert cfg.ripple is None
        assert cfg.teensy is None

    def test_explicit_ripple_block_preserved(self) -> None:
        cfg = SyncConfig(
            transport="ripple_scout",
            ripple=RippleSyncConfig(sync_pulse_sma_index=2),
        )
        assert cfg.ripple is not None
        assert cfg.ripple.sync_pulse_sma_index == 2  # not overwritten to default


class TestRippleSyncConfig:
    def test_defaults(self) -> None:
        cfg = RippleSyncConfig()
        assert cfg.sync_pulse_sma_index == 0
        assert cfg.event_code_digout_index == 4

    def test_sma_index_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            RippleSyncConfig(sync_pulse_sma_index=4)

    def test_sma_index_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            RippleSyncConfig(sync_pulse_sma_index=-1)

    def test_digout_index_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            RippleSyncConfig(event_code_digout_index=5)

    def test_digout_index_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            RippleSyncConfig(event_code_digout_index=-1)


class TestRecordingConfigRipple:
    def test_ripple_none_by_default(self) -> None:
        cfg = RecordingConfig()
        assert cfg.ripple is None

    def test_populated_ripple_block(self) -> None:
        cfg = RecordingConfig(
            ripple=RippleRecordingConfig(use_tcp=False, operator_id=200),
        )
        assert cfg.ripple is not None
        assert cfg.ripple.use_tcp is False
        assert cfg.ripple.operator_id == 200
        assert cfg.ripple.auto_increment is True

    def test_operator_id_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            RippleRecordingConfig(operator_id=256)

    def test_operator_id_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            RippleRecordingConfig(operator_id=-1)
