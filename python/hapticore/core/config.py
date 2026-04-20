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


class RippleRecordingConfig(BaseModel):
    """Ripple Grapevine recording settings.

    Used when ``RecordingConfig.ripple is not None``. Controls the xipppy
    TCP connection and Trellis ``trial()`` calls made by ``RippleProcess``.
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
    """Mapping from state/event names to integer digital event codes.

    Used transport-agnostically by both Ripple Scout DIO (Phase 5A) and the
    Teensy sync hub (Phase 5B). The recording process (e.g. ``RippleProcess``)
    consumes this to translate semantic events into hardware digout calls:

    - ``state_codes`` — emitted automatically by the recording process when a
      ``StateTransition`` message is published whose ``new_state`` is listed
      here. Default empty so existing tasks that call ``send_event_code(int)``
      explicitly are not double-fired.
    - ``event_codes`` — looked up when an explicit ``send_event_code(name)``
      is published over the bus. Forward-facing; existing tasks still pass
      raw ints and are unaffected.

    Codes ``0`` and ``65535`` should be avoided (they collide with Ripple's
    bus-idle and all-ones states respectively) but are not rejected here —
    bit-width validation is the responsibility of the transport.
    """

    state_codes: dict[str, int] = Field(default_factory=dict)
    event_codes: dict[str, int] = Field(default_factory=dict)


class TeensyConfig(BaseModel):
    """Teensy sync-hub settings (Phase 5B).

    Used when ``SyncConfig.transport == 'teensy'``. Ignored in Phase 5A.
    """

    port: str = "/dev/ttyACM0"
    baud: int = Field(default=115200, gt=0)


class RippleSyncConfig(BaseModel):
    """Ripple Scout DIO settings for 1 Hz sync pulse and event codes.

    Used when ``SyncConfig.transport == 'ripple_scout'``. Consumed by
    ``RippleProcess`` (Phase 5A.2).
    """

    sync_pulse_sma_index: int = Field(
        default=0, ge=0, le=3,
        description="SMA output index 0-3 used for the 1 Hz sync pulse.",
    )
    event_code_digout_index: int = Field(
        default=4, ge=0, le=4,
        description=(
            "xipppy digout index used for event codes. 4 selects the "
            "16-bit parallel port (recommended). 0-3 would restrict codes "
            "to binary on a single SMA line."
        ),
    )


class SyncConfig(BaseModel):
    """Sync transport + event code map.

    Transport-specific knobs are nested under ``ripple`` / ``teensy``. When a
    transport is selected, the corresponding nested block is auto-populated
    with defaults if not provided explicitly, so the config is always
    internally consistent after validation.
    """

    transport: Literal["mock", "ripple_scout", "teensy"] = "mock"
    sync_pulse_rate_hz: float = Field(default=1.0, gt=0, le=10.0)
    code_map: EventCodeMap = Field(default_factory=EventCodeMap)
    ripple: RippleSyncConfig | None = None
    teensy: TeensyConfig | None = None

    @model_validator(mode="after")
    def _populate_selected_transport(self) -> Self:
        if self.transport == "ripple_scout" and self.ripple is None:
            self.ripple = RippleSyncConfig()
        elif self.transport == "teensy" and self.teensy is None:
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
