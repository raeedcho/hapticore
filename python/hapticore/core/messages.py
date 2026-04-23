"""Message dataclasses and serialization for inter-process communication.

All message types are dataclasses optimized for high-frequency messaging.
Serialization uses msgpack for speed. Numpy arrays are automatically
converted to lists during serialization.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

import msgpack
import numpy as np

# Topic constants for ZeroMQ PUB-SUB
TOPIC_STATE = b"state"
TOPIC_EVENT = b"event"
TOPIC_DISPLAY = b"display"
TOPIC_TRIAL = b"trial"
TOPIC_SESSION = b"session"
TOPIC_SYNC = b"sync"


def _msgpack_default(obj: object) -> Any:
    """Handle numpy types for msgpack serialization."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not msgpack serializable")


@dataclasses.dataclass(slots=True)
class HapticState:
    """State broadcast from the haptic server at 100-500 Hz."""

    timestamp: float
    sequence: int
    position: list[float]
    velocity: list[float]
    force: list[float]
    active_field: str
    field_state: dict[str, Any]


@dataclasses.dataclass(slots=True)
class StateTransition:
    """Published when the task state machine changes state."""

    timestamp: float
    previous_state: str
    new_state: str
    trigger: str
    trial_number: int
    event_code: int


@dataclasses.dataclass(slots=True)
class TrialEvent:
    """Arbitrary event within a trial (stimulus onset, response detected, etc.)."""

    timestamp: float
    event_name: str
    event_code: int
    trial_number: int
    data: dict[str, Any]


@dataclasses.dataclass(slots=True)
class Command:
    """Command sent from task controller to a hardware server."""

    command_id: str
    method: str
    params: dict[str, Any]


@dataclasses.dataclass(slots=True)
class CommandResponse:
    """Response from hardware server to a command."""

    command_id: str
    success: bool
    result: dict[str, Any]
    error: str | None = None


@dataclasses.dataclass(slots=True)
class SessionControl:
    """Request to start/stop recording, sync pulses, or camera trigger.

    Published by ``SessionManager`` (future) and consumed by ``SyncProcess``
    and recording processes. ``action`` is one of ``"start_recording"``,
    ``"stop_recording"``, ``"start_sync"``, ``"stop_sync"``,
    ``"start_camera_trigger"``, or ``"stop_camera_trigger"``. ``params``
    carries action-specific data such as ``file_name_base`` on
    ``start_recording``.
    """

    timestamp: float
    action: str
    params: dict[str, Any]


# Type alias for all message types
MessageType = (
    HapticState | StateTransition | TrialEvent | Command | CommandResponse | SessionControl
)

# Map class names to classes for deserialization
_MSG_TYPE_KEY = "__msg_type__"


def serialize(msg: MessageType) -> bytes:
    """Serialize a message dataclass to msgpack bytes."""
    data = dataclasses.asdict(msg)
    data[_MSG_TYPE_KEY] = type(msg).__name__
    result: bytes = msgpack.packb(data, default=_msgpack_default, use_bin_type=True)
    return result


def deserialize(
    data: bytes,
    msg_type: type[HapticState]
    | type[StateTransition]
    | type[TrialEvent]
    | type[Command]
    | type[CommandResponse]
    | type[SessionControl],
) -> MessageType:
    """Deserialize msgpack bytes to a message dataclass."""
    unpacked = msgpack.unpackb(data, raw=False)
    unpacked.pop(_MSG_TYPE_KEY, None)
    return msg_type(**unpacked)


def make_haptic_state(
    position: list[float] | None = None,
    velocity: list[float] | None = None,
    force: list[float] | None = None,
    active_field: str = "null",
    field_state: dict[str, Any] | None = None,
    sequence: int = 0,
) -> HapticState:
    """Factory for creating HapticState with sensible defaults."""
    return HapticState(
        timestamp=time.monotonic(),
        sequence=sequence,
        position=position or [0.0, 0.0, 0.0],
        velocity=velocity or [0.0, 0.0, 0.0],
        force=force or [0.0, 0.0, 0.0],
        active_field=active_field,
        field_state=field_state or {},
    )
