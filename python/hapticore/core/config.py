"""Pydantic v2 configuration models for experiment setup.

All config models use Pydantic BaseModel with Field() constraints.
Invalid configs fail at load time, not during an experiment.
Load from YAML with load_config().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


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


class ExperimentConfig(BaseModel):
    """Top-level experiment configuration.

    Composes all sub-configurations. Load from YAML with load_config().
    """

    experiment_name: str
    subject: SubjectConfig
    haptic: HapticConfig = Field(default_factory=HapticConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    task: TaskConfig
    sync: SyncConfig = Field(default_factory=SyncConfig)
    zmq: ZMQConfig = Field(default_factory=ZMQConfig)


def load_config(yaml_path: str | Path) -> ExperimentConfig:
    """Load and validate experiment configuration from a YAML file."""
    path = Path(yaml_path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    return ExperimentConfig.model_validate(raw)
