"""Base task class and parameter specification for behavioral tasks.

All behavioral tasks inherit from BaseTask and declare their structure
via class-level attributes (PARAMS, STATES, TRANSITIONS, INITIAL_STATE).
The TaskController reads these declarations to wire up the transitions
state machine at runtime.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from abc import ABC
from collections.abc import Callable
from typing import Any

from hapticore.core.messages import Command


@dataclasses.dataclass(frozen=True)
class ParamSpec:
    """Specification for a single task parameter.

    Attributes:
        type: Python type (int, float, str, bool).
        default: Default value for the parameter.
        description: Human-readable description.
        unit: Physical unit string (e.g. "s", "m", "N/m").
        min: Inclusive lower bound (for numeric types).
        max: Inclusive upper bound (for numeric types).
    """

    type: type
    default: Any
    description: str = ""
    unit: str = ""
    min: float | int | None = None
    max: float | int | None = None


class BaseTask(ABC):
    """Base class for all behavioral tasks.

    Subclasses MUST define these class attributes:
        PARAMS: dict[str, ParamSpec]     — parameter specifications
        STATES: list[str]                — state machine states
        TRANSITIONS: list[dict]          — transitions library format
        INITIAL_STATE: str               — starting state name

    Subclasses MAY define:
        HARDWARE: dict[str, type]        — logical hardware role → Protocol type
    """
    # Injected by transitions.Machine at runtime via TaskController.setup()
    trigger: Callable[..., bool]
    state: str

    # These must be overridden by subclasses
    PARAMS: dict[str, ParamSpec]
    STATES: list[str]
    TRANSITIONS: list[dict[str, Any]]
    INITIAL_STATE: str
    HARDWARE: dict[str, type] = {}

    def setup(
        self,
        hardware: dict[str, Any],
        params: dict[str, Any],
        event_bus: Any,
        trial_manager: Any,
        timer_manager: Any,
    ) -> None:
        """Called by TaskController to wire the task into the runtime.

        Stores references as instance attributes for use in callbacks.
        """
        self.haptic = hardware.get("haptic")
        self.display = hardware.get("display")
        self.sync = hardware.get("sync")
        self.params = dict(params)
        self.event_bus = event_bus
        self.trial_manager = trial_manager
        self.timer = timer_manager
        # state is managed by transitions.Machine once attached
        self.current_condition: dict[str, Any] = {}
        self.trial_number: int = -1

    def cleanup(self) -> None:  # noqa: B027
        """Called when the session ends. Override to release task-specific resources."""

    def check_triggers(self, haptic_state: Any) -> None:  # noqa: B027
        """Called every main-loop iteration with the latest haptic state.

        Override this to fire triggers based on continuous data (e.g.,
        position in target zone). Default implementation does nothing.
        """

    def on_trial_start(self, condition: dict[str, Any]) -> None:
        """Called at the start of each trial with the condition dict.

        Override to set up trial-specific state. Default implementation
        stores ``self.current_condition = condition``.
        """
        self.current_condition = condition

    def on_trial_end(self, outcome: str) -> None:  # noqa: B027
        """Called at the end of each trial with the outcome string.

        Override for trial-end cleanup. Default does nothing.
        """

    def log_trial(self, outcome: str, **extra_data: Any) -> None:
        """Log a completed trial via the TrialManager and publish a TrialEvent."""
        from hapticore.core.messages import TOPIC_TRIAL, TrialEvent, serialize

        self.trial_manager.log_trial(outcome, **extra_data)
        event = TrialEvent(
            timestamp=time.monotonic(),
            event_name="trial_complete",
            event_code=0,
            trial_number=self.trial_number,
            data={"outcome": outcome, **extra_data},
        )
        self.event_bus.publish(TOPIC_TRIAL, serialize(event))

    def new_command_id(self) -> str:
        """Generate a unique command ID."""
        return uuid.uuid4().hex[:12]

    @staticmethod
    def distance(a: list[float], b: list[float]) -> float:
        """Euclidean distance between two 3D points."""
        return sum((ai - bi) ** 2 for ai, bi in zip(a, b, strict=True)) ** 0.5

    @property
    def background_fields(self) -> list[dict[str, Any]]:
        """Background force fields applied to every set_field() call.

        Set this in on_trial_start() to declare fields that should always
        be active (e.g., channel constraints, workspace limits). When
        non-empty, set_field() wraps the primary field in a composite
        with these as siblings. When empty, set_field() sends the primary
        field directly (no composite wrapper).
        """
        if not hasattr(self, "_background_fields"):
            self._background_fields: list[dict[str, Any]] = []
        return self._background_fields

    @background_fields.setter
    def background_fields(self, fields: list[dict[str, Any]]) -> None:
        self._background_fields = list(fields)

    def set_field(
        self, field_type: str, field_params: dict[str, Any],
    ) -> None:
        """Send a set_force_field command, wrapping in composite if needed.

        If background_fields is non-empty, wraps the primary field in a
        composite alongside the background fields. When background_fields
        is empty, sends the primary field directly (no composite wrapper).
        """
        if self.background_fields:
            params: dict[str, Any] = {
                "type": "composite",
                "params": {
                    "fields": [
                        *self.background_fields,
                        {"type": field_type, "params": field_params},
                    ],
                },
            }
        else:
            params = {"type": field_type, "params": field_params}

        self.haptic.send_command(Command(
            command_id=self.new_command_id(),
            method="set_force_field",
            params=params,
        ))
