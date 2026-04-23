"""Protocol classes for hardware interface abstraction.

These define the contracts that both real and mock implementations must satisfy.
All protocols are runtime-checkable for isinstance() verification.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from hapticore.core.messages import Command, CommandResponse, HapticState


@runtime_checkable
class HapticInterface(Protocol):
    """Interface for haptic device communication."""

    def get_latest_state(self) -> HapticState | None: ...
    def send_command(self, cmd: Command) -> CommandResponse: ...
    def subscribe_state(self, callback: Callable[[HapticState], None]) -> None: ...
    def unsubscribe_state(self) -> None: ...


@runtime_checkable
class NeuralRecordingInterface(Protocol):
    """Interface for neural recording systems (Ripple, SpikeGLX)."""

    def start_recording(self, filename: str) -> None: ...
    def stop_recording(self) -> None: ...
    def is_recording(self) -> bool: ...
    def get_timestamp(self) -> float: ...


@runtime_checkable
class SyncInterface(Protocol):
    """Interface for hardware sync (Teensy 4.1 sync hub).

    Covers the four signal types the Teensy produces (ADR-013): 1 Hz
    cross-system sync pulse, 8-bit parallel event codes with strobe,
    camera frame trigger, and reward TTL.
    """

    # Event codes
    def send_event_code(self, code: int) -> None: ...

    # 1 Hz cross-system sync pulse
    def start_sync_pulses(self) -> None: ...
    def stop_sync_pulses(self) -> None: ...
    def is_sync_running(self) -> bool: ...

    # Camera frame trigger
    def set_camera_trigger_rate(self, rate_hz: float) -> None: ...
    def start_camera_trigger(self) -> None: ...
    def stop_camera_trigger(self) -> None: ...
    def is_camera_trigger_running(self) -> bool: ...

    # Reward
    def deliver_reward(self, duration_ms: int) -> None: ...


@runtime_checkable
class DisplayInterface(Protocol):
    """Interface for visual stimulus display (PsychoPy)."""

    def update_scene(self, scene_state: dict[str, Any]) -> None: ...
    def show_stimulus(self, stim_id: str, params: dict[str, Any]) -> None: ...
    def hide_stimulus(self, stim_id: str) -> None: ...
    def clear(self) -> None: ...
    def get_flip_timestamp(self) -> float | None: ...

    def show_cart_pendulum(
        self,
        *,
        cup_color: list[float] | None = None,
        ball_color: list[float] | None = None,
        string_color: list[float] | None = None,
        cup_half_width: float = 0.015,
        cup_depth: float = 0.03,
        ball_radius: float = 0.008,
    ) -> None: ...

    def hide_cart_pendulum(self) -> None: ...

    def show_physics_bodies(
        self, body_specs: dict[str, dict[str, Any]],
    ) -> None: ...

    def hide_physics_bodies(self, body_ids: list[str]) -> None: ...
