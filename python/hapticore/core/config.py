"""Pydantic v2 configuration models for experiment setup.

All nested config models use Pydantic BaseModel with Field() constraints.
The top-level ExperimentConfig uses pydantic-settings BaseSettings for
layered configuration from YAML files, environment variables, and CLI args.
Invalid configs fail at load time, not during an experiment.
Load from YAML with load_config().
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    CliSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class ZMQConfig(BaseModel):
    """ZeroMQ socket addresses."""

    event_pub_address: str = "ipc:///tmp/hapticore_events"
    haptic_state_address: str = "ipc:///tmp/hapticore_haptic_state"
    haptic_command_address: str = "ipc:///tmp/hapticore_haptic_cmd"
    display_event_address: str = "ipc:///tmp/hapticore_display_events"
    transport: str = "ipc"


class SubjectConfig(BaseModel):
    """Subject/animal information."""

    subject_id: str = Field(..., min_length=1, description="Subject identifier")
    species: str = "macaque"
    implant_info: dict[str, Any] = Field(default_factory=dict)


class DhdConfig(BaseModel):
    """Delta.3 haptic client settings.

    Used when ``HapticConfig.backend == 'dhd'``. The client connects to a
    running C++ haptic server via ZMQ; addresses live in ``ZMQConfig``.
    These fields tune the client's command and heartbeat behavior.
    """

    force_limit_n: float = Field(
        default=20.0, gt=0, le=40.0, description="Maximum force in Newtons"
    )
    publish_rate_hz: float = Field(
        default=200.0, gt=0, le=1000.0, description="Rate at which to publish haptic state updates"
    )
    heartbeat_interval_s: float = Field(
        default=0.2, gt=0.0, lt=0.5,
        description="Heartbeat period in seconds. Must be strictly less "
                    "than the server's 500 ms watchdog timeout.",
    )
    command_timeout_ms: int = Field(
        default=1000, gt=0,
        description="Timeout in milliseconds for a single command round-trip.",
    )


class HapticConfig(BaseModel):
    """Haptic interface configuration."""

    backend: Literal["dhd", "mock", "mouse"] = "mock"

    dhd: DhdConfig | None = None

    @model_validator(mode="after")
    def _populate_selected_backend(self) -> Self:
        if self.backend == "dhd" and self.dhd is None:
            self.dhd = DhdConfig()
        return self


class DisplayConfig(BaseModel):
    """Visual display configuration."""

    backend: Literal["psychopy", "mock"] = "mock"

    resolution: tuple[int, int] = (1920, 1080)
    refresh_rate_hz: int = Field(default=60, gt=0, le=240)
    fullscreen: bool = True
    monitor_distance_cm: float = Field(default=50.0, gt=0)
    monitor_width_cm: float = Field(default=53.0, gt=0, description="Physical screen width in cm")
    background_color: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    photodiode_enabled: bool = Field(
        default=True, description="Enable photodiode timing patch"
    )
    photodiode_corner: str = Field(
        default="bottom_left",
        pattern=r"^(bottom_left|bottom_right|top_left|top_right)$",
        description="Screen corner for photodiode patch",
    )
    cursor_radius: float = Field(default=0.005, gt=0, description="Cursor radius in meters")
    cursor_color: list[float] = Field(
        default_factory=lambda: [1.0, 1.0, 1.0], description="Cursor RGB color"
    )
    cursor_visible: bool = Field(default=True, description="Whether cursor is drawn")
    cursor_interpolation: bool = Field(
        default=False, description="Interpolate cursor position between haptic state updates"
    )
    display_scale: float = Field(
        default=1.0,
        description="Workspace scale factor (dimensionless, meters→meters). "
        "1.0 = haptic workspace maps 1:1 onto display workspace. "
        "2.0 = everything appears twice as large on screen.",
    )
    display_offset: list[float] = Field(
        default_factory=lambda: [0.0, 0.0],
        description="Display offset in meters [x, y] for co-location calibration",
    )
    screen: int = Field(
        default=0, ge=0,
        description="Monitor index (0 = first monitor in enumeration order). "
                    "Run 'hapticore list-screens' to enumerate available monitors.",
    )
    mirror_horizontal: bool = Field(
        default=False,
        description="Mirror the rendered image across the vertical axis. Use "
                    "when the subject views the monitor via a canted mirror that "
                    "reflects the image left-right. See docs/rig-setup.md.",
    )
    mirror_vertical: bool = Field(
        default=False,
        description="Mirror the rendered image across the horizontal axis.",
    )


class RippleRecordingConfig(BaseModel):
    """Ripple Grapevine recording-control settings.

    Controls the xipppy TCP connection used to start and stop Trellis
    recording via ``trial()`` and to read digital inputs (where the
    Teensy's event strobe and sync pulses land). Sync-pulse and event-code
    *generation* are the Teensy's responsibility (ADR-013); this config
    covers only the recording side.
    """

    use_tcp: bool = True
    operator_id: int = Field(
        default=129, ge=0, le=255,
        description="Trellis operator address (last octet of Trellis IPv4).",
    )
    auto_increment: bool = True


class RecordingConfig(BaseModel):
    """Neural recording configuration.

    Presence of a nested block indicates the corresponding system is in use
    for this session; ``None`` (default) means not in use.
    """

    save_dir: Path = Field(default=Path("data"))
    lsl_enabled: bool = True
    ripple: RippleRecordingConfig | None = None


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


class EventCodeMap(BaseModel):
    """Mapping from state/event names to 8-bit digital event codes.

    Event codes are emitted by the Teensy sync hub on an 8-bit parallel
    bus + strobe line wired to both recording systems (see ADR-014). The
    ``SyncProcess`` (Phase 5A.4) consumes this map to translate semantic
    events into ``E<code>`` commands to the Teensy firmware:

    - ``state_codes`` — emitted automatically by ``SyncProcess`` when a
      ``StateTransition`` message is published whose ``new_state`` is
      listed here. Default empty so existing tasks that call
      ``send_event_code(int)`` explicitly are not double-fired.
    - ``event_codes`` — looked up when an explicit named event is
      published over the bus. Forward-facing; existing tasks pass raw
      ints via ``SyncInterface.send_event_code`` and are unaffected.

    Codes are 8 bits (0–255). Code ``0`` should generally be avoided
    (bus-idle state); the value space is otherwise open.
    """

    state_codes: dict[str, int] = Field(default_factory=dict)
    event_codes: dict[str, int] = Field(default_factory=dict)


class TeensyConfig(BaseModel):
    """Teensy 4.1 sync hub settings.

    Used when ``SyncConfig.backend == 'teensy'``. The Teensy 4.1 is
    the centralized hardware timing source for all rig TTL signals —
    sync pulse, camera trigger, event codes, and reward. See ADR-013.
    """

    port: str = "/dev/ttyACM0"
    baud: int = Field(default=115200, gt=0)


class SyncConfig(BaseModel):
    """Sync backend + event code map.

    Backend-specific knobs are nested under ``teensy``. When the
    ``teensy`` backend is selected, the nested block is auto-populated
    with defaults if not provided explicitly, so the config is always
    internally consistent after validation. See ADR-013 (Teensy sync hub)
    for why Teensy is the sole hardware sync source.
    """

    backend: Literal["mock", "teensy"] = "mock"
    sync_pulse_rate_hz: float = Field(default=1.0, gt=0, le=10.0)
    code_map: EventCodeMap = Field(default_factory=EventCodeMap)
    teensy: TeensyConfig | None = None

    @model_validator(mode="after")
    def _populate_selected_backend(self) -> Self:
        if self.backend == "teensy" and self.teensy is None:
            self.teensy = TeensyConfig()
        return self


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


def load_config(
    *yaml_paths: str | Path,
    overrides: dict[str, Any] | None = None,
    cli_parse_args: bool | list[str] | tuple[str, ...] | None = None,
) -> ExperimentConfig:
    """Load experiment config from layered YAML files.

    Files are merged left-to-right (later files override earlier ones).
    Environment variables (HAPTICORE_ prefix, __ delimiter) override YAML values.
    CLI arguments override everything when ``cli_parse_args`` is set.

    Priority order (highest wins first):

    1. CLI arguments (if cli_parse_args is set)
    2. Constructor kwargs (overrides dict)
    3. Environment variables (HAPTICORE_ prefix, __ delimiter)
    4. YAML files (layered with deep merge, later files win)
    5. Field defaults in the Pydantic models

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
    init_kwargs: dict[str, Any] = overrides or {}

    class _ConfigWithSources(ExperimentConfig):
        """Dynamic subclass with customised settings sources.

        Uses the public ``settings_customise_sources`` hook to inject
        YAML file paths and CLI args at runtime.
        """

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: Any,
            env_settings: Any,
            dotenv_settings: Any,
            file_secret_settings: Any,
            **kwargs: Any,
        ) -> tuple[Any, ...]:
            sources: list[Any] = []

            if cli_parse_args:
                sources.append(
                    CliSettingsSource(
                        settings_cls, cli_parse_args=cli_parse_args,
                    ),
                )

            sources.append(init_settings)
            sources.append(env_settings)

            if yaml_file_list:
                sources.append(
                    YamlConfigSettingsSource(
                        settings_cls,
                        yaml_file=yaml_file_list,
                        deep_merge=True,
                    ),
                )

            return tuple(sources)

    return _ConfigWithSources(**init_kwargs)


def load_session_config(
    *,
    rig: str | Path,
    subject: str | Path,
    task: str | Path,
    extra: Sequence[str | Path] = (),
    overrides: dict[str, Any] | None = None,
    cli_parse_args: bool | list[str] | tuple[str, ...] | None = None,
) -> ExperimentConfig:
    """Load a complete session config with all required layers.

    This is the primary entry point for real experiment sessions.
    All parameters are keyword-only to prevent accidentally omitting or
    mis-ordering a config file.

    For flexible or testing use, use ``load_config(*yaml_paths)`` directly.

    Args:
        rig: Path to rig config YAML (haptic, display, sync, ZMQ settings).
        subject: Path to subject config YAML (subject_id, species, implant_info).
        task: Path to task config YAML (task_class, params, conditions).
        extra: Additional YAML files merged on top (later files win). Pass as a
            list or tuple, e.g. ``extra=["configs/example_experiment.yaml"]``.
        overrides: Dict of keyword overrides (highest priority after CLI).
        cli_parse_args: If truthy, parse CLI arguments via pydantic-settings.
    """
    return load_config(
        rig, subject, task, *extra,
        overrides=overrides, cli_parse_args=cli_parse_args,
    )
