"""Pydantic v2 configuration models for experiment setup.

All nested config models use Pydantic BaseModel with Field() constraints.
The top-level ExperimentConfig uses pydantic-settings BaseSettings for
layered configuration from YAML files, environment variables, and CLI args.
Invalid configs fail at load time, not during an experiment.
Load from YAML with load_config().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources.providers.cli import CliSettingsSource
from pydantic_settings.sources.providers.yaml import YamlConfigSettingsSource


class ZMQConfig(BaseModel):
    """ZeroMQ socket addresses."""

    event_pub_address: str = "ipc:///tmp/hapticore_events"
    haptic_state_address: str = "ipc:///tmp/hapticore_haptic_state"
    haptic_command_address: str = "ipc:///tmp/hapticore_haptic_cmd"
    transport: str = "ipc"


class SubjectConfig(BaseModel):
    """Subject/animal information."""

    subject_id: str = Field(..., min_length=1, description="Subject identifier")
    species: str = "macaque"
    implant_info: dict[str, Any] = Field(default_factory=dict)


class HapticConfig(BaseModel):
    """Haptic server configuration."""

    server_address: str = "localhost"
    workspace_bounds: dict[str, list[float]] = Field(
        default_factory=lambda: {"x": [-0.15, 0.15], "y": [-0.15, 0.15], "z": [-0.15, 0.15]},
        description="Workspace limits in meters",
    )
    force_limit_n: float = Field(
        default=20.0, gt=0, le=40.0, description="Maximum force in Newtons"
    )
    publish_rate_hz: float = Field(default=200.0, gt=0, le=1000.0)


class DisplayConfig(BaseModel):
    """Visual display configuration."""

    resolution: tuple[int, int] = (1920, 1080)
    refresh_rate_hz: int = Field(default=60, gt=0, le=240)
    fullscreen: bool = True
    monitor_distance_cm: float = Field(default=50.0, gt=0)
    background_color: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class RecordingConfig(BaseModel):
    """Neural recording configuration."""

    ripple_enabled: bool = False
    spikeglx_enabled: bool = False
    lsl_enabled: bool = True
    save_dir: Path = Field(default=Path("data"))


class TaskConfig(BaseModel):
    """Behavioral task configuration."""

    task_class: str = Field(
        ...,
        description="Dotted path to task class, e.g. 'hapticore.tasks.center_out.CenterOutTask'",
    )
    params: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    block_size: int = Field(default=20, gt=0)
    # None means open-ended (run until request_stop is called). When an integer
    # is provided Pydantic enforces gt=0; the constraint is not applied to None.
    num_blocks: int | None = Field(default=10, gt=0)
    randomization: str = Field(
        default="pseudorandom", pattern=r"^(pseudorandom|sequential|latin_square)$"
    )


class SyncConfig(BaseModel):
    """Teensy sync pulse configuration."""

    teensy_port: str = "/dev/ttyACM0"
    sync_pulse_rate_hz: float = Field(default=1.0, gt=0, le=10.0)
    event_code_bits: int = Field(default=8, ge=1, le=16)


class ExperimentConfig(BaseSettings):
    """Top-level experiment configuration.

    Composes all sub-configurations. Uses pydantic-settings for layered
    configuration from YAML files, environment variables (HAPTICORE_ prefix,
    __ delimiter), and CLI arguments.

    Load from YAML with load_config().
    """

    model_config = SettingsConfigDict(
        env_prefix="HAPTICORE_",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
        cli_parse_args=False,
    )

    experiment_name: str
    subject: SubjectConfig
    haptic: HapticConfig = Field(default_factory=HapticConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    task: TaskConfig
    sync: SyncConfig = Field(default_factory=SyncConfig)
    zmq: ZMQConfig = Field(default_factory=ZMQConfig)


def _build_sources(
    yaml_paths: list[str],
    cli_parse_args: bool | list[str] | tuple[str, ...] | None,
    init_kwargs: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Build the settings source chain for ExperimentConfig.

    Priority order (highest wins first):
    1. CLI arguments (if cli_parse_args is set)
    2. Constructor kwargs (init_kwargs / overrides)
    3. Environment variables (HAPTICORE_ prefix, __ delimiter)
    4. YAML files (layered with deep merge, later files win)
    5. Field defaults in the Pydantic models
    """
    from pydantic_settings.sources import (
        DefaultSettingsSource,
        EnvSettingsSource,
        InitSettingsSource,
    )

    settings_cls = ExperimentConfig

    init_source = InitSettingsSource(settings_cls, init_kwargs=init_kwargs)
    env_source = EnvSettingsSource(
        settings_cls,
        env_prefix=settings_cls.model_config.get("env_prefix", ""),
        env_nested_delimiter=settings_cls.model_config.get("env_nested_delimiter"),
        env_prefix_target=settings_cls.model_config.get("env_prefix_target"),
    )

    sources: list[Any] = [init_source, env_source]

    if yaml_paths:
        sources.append(
            YamlConfigSettingsSource(
                settings_cls, yaml_file=yaml_paths, deep_merge=True,
            ),
        )

    if cli_parse_args:
        sources.append(
            CliSettingsSource(settings_cls, cli_parse_args=cli_parse_args),
        )

    sources.append(DefaultSettingsSource(settings_cls))
    return tuple(sources), init_kwargs


def load_config(
    *yaml_paths: str | Path,
    overrides: dict[str, Any] | None = None,
    cli_parse_args: bool | list[str] | tuple[str, ...] | None = None,
) -> ExperimentConfig:
    """Load experiment config from layered YAML files.

    Files are merged left-to-right (later files override earlier ones).
    Environment variables (HAPTICORE_ prefix, __ delimiter) override YAML values.
    CLI arguments override everything when ``cli_parse_args`` is set.

    Typical usage::

        config = load_config(
            "configs/rig/default.yaml",
            "configs/subject/example_subject.yaml",
            "configs/task/center_out.yaml",
            "configs/example_experiment.yaml",
        )

    A single flat YAML file also works::

        config = load_config("configs/example_config.yaml")

    Args:
        *yaml_paths: YAML file paths, merged left-to-right.
        overrides: Dict of keyword overrides (highest priority after CLI).
        cli_parse_args: If truthy, parse CLI arguments via pydantic-settings.
    """
    yaml_file_list = [str(Path(p)) for p in yaml_paths]
    kwargs = overrides or {}
    sources = _build_sources(yaml_file_list, cli_parse_args, kwargs)
    return ExperimentConfig(_build_sources=sources)
