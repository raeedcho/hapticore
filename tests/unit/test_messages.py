"""Tests for message dataclasses and serialization."""

from __future__ import annotations

import numpy as np
import pytest

from hapticore.core.messages import (
    Command,
    CommandResponse,
    HapticState,
    StateTransition,
    TrialEvent,
    deserialize,
    serialize,
)


class TestHapticState:
    """Tests for HapticState serialization."""

    def test_round_trip(self) -> None:
        msg = HapticState(
            timestamp=1234.5678,
            sequence=42,
            position=[0.01, 0.02, 0.03],
            velocity=[0.1, 0.2, 0.3],
            force=[1.0, 2.0, 3.0],
            active_field="spring",
            field_state={"stiffness": 100.0},
        )
        data = serialize(msg)
        restored = deserialize(data, HapticState)
        assert isinstance(restored, HapticState)
        assert restored.timestamp == msg.timestamp
        assert restored.sequence == msg.sequence
        assert restored.position == msg.position
        assert restored.velocity == msg.velocity
        assert restored.force == msg.force
        assert restored.active_field == msg.active_field
        assert restored.field_state == msg.field_state

    def test_float_precision(self) -> None:
        msg = HapticState(
            timestamp=1234567890.123456,
            sequence=0,
            position=[0.123456789, -0.987654321, 0.000001],
            velocity=[0.0, 0.0, 0.0],
            force=[0.0, 0.0, 0.0],
            active_field="null",
            field_state={},
        )
        data = serialize(msg)
        restored = deserialize(data, HapticState)
        assert restored.position[0] == pytest.approx(0.123456789, rel=1e-9)
        assert restored.position[1] == pytest.approx(-0.987654321, rel=1e-9)
        assert restored.position[2] == pytest.approx(0.000001, rel=1e-6)

    def test_nested_field_state(self) -> None:
        msg = HapticState(
            timestamp=1.0,
            sequence=0,
            position=[0.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            force=[0.0, 0.0, 0.0],
            active_field="cart_pendulum",
            field_state={
                "angle": 0.5,
                "angular_velocity": 1.2,
                "targets": [1, 2, 3],
                "label": "test",
                "nested": {"a": 1, "b": [2.0, 3.0]},
            },
        )
        data = serialize(msg)
        restored = deserialize(data, HapticState)
        assert restored.field_state["angle"] == 0.5
        assert restored.field_state["targets"] == [1, 2, 3]
        assert restored.field_state["label"] == "test"
        assert restored.field_state["nested"]["a"] == 1
        assert restored.field_state["nested"]["b"] == [2.0, 3.0]

    def test_numpy_arrays_handled(self) -> None:
        msg = HapticState(
            timestamp=1.0,
            sequence=0,
            position=np.array([0.1, 0.2, 0.3]),  # type: ignore[arg-type]
            velocity=np.array([0.0, 0.0, 0.0]),  # type: ignore[arg-type]
            force=np.array([1.0, 2.0, 3.0]),  # type: ignore[arg-type]
            active_field="null",
            field_state={},
        )
        data = serialize(msg)
        assert isinstance(data, bytes)
        restored = deserialize(data, HapticState)
        assert restored.position == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.3)]
        assert restored.velocity == [0.0, 0.0, 0.0]
        assert restored.force == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(3.0)]

    def test_numpy_in_field_state(self) -> None:
        """Numpy arrays in field_state dict should be handled by msgpack default."""
        msg = HapticState(
            timestamp=1.0,
            sequence=0,
            position=[0.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            force=[0.0, 0.0, 0.0],
            active_field="test",
            field_state={"array": np.array([1.0, 2.0, 3.0])},
        )
        data = serialize(msg)
        assert isinstance(data, bytes)
        restored = deserialize(data, HapticState)
        assert restored.field_state["array"] == [1.0, 2.0, 3.0]


class TestStateTransition:
    def test_round_trip(self) -> None:
        msg = StateTransition(
            timestamp=100.0,
            previous_state="center_hold",
            new_state="reach",
            trigger="go_cue",
            trial_number=5,
            event_code=42,
        )
        data = serialize(msg)
        restored = deserialize(data, StateTransition)
        assert isinstance(restored, StateTransition)
        assert restored.previous_state == "center_hold"
        assert restored.new_state == "reach"
        assert restored.trigger == "go_cue"
        assert restored.trial_number == 5
        assert restored.event_code == 42


class TestTrialEvent:
    def test_round_trip(self) -> None:
        msg = TrialEvent(
            timestamp=200.0,
            event_name="stimulus_onset",
            event_code=10,
            trial_number=3,
            data={"stim_id": "target_1", "position": [0.05, 0.0]},
        )
        data = serialize(msg)
        restored = deserialize(data, TrialEvent)
        assert isinstance(restored, TrialEvent)
        assert restored.event_name == "stimulus_onset"
        assert restored.data["stim_id"] == "target_1"
        assert restored.data["position"] == [0.05, 0.0]


class TestCommand:
    def test_round_trip(self) -> None:
        msg = Command(
            command_id="abc123",
            method="set_force_field",
            params={"field_type": "spring", "stiffness": 100.0},
        )
        data = serialize(msg)
        restored = deserialize(data, Command)
        assert isinstance(restored, Command)
        assert restored.command_id == "abc123"
        assert restored.method == "set_force_field"
        assert restored.params["stiffness"] == 100.0


class TestCommandResponse:
    def test_round_trip(self) -> None:
        msg = CommandResponse(
            command_id="abc123",
            success=True,
            result={"field_active": True},
        )
        data = serialize(msg)
        restored = deserialize(data, CommandResponse)
        assert isinstance(restored, CommandResponse)
        assert restored.command_id == "abc123"
        assert restored.success is True
        assert restored.error is None

    def test_round_trip_with_error(self) -> None:
        msg = CommandResponse(
            command_id="xyz",
            success=False,
            result={},
            error="Method not found",
        )
        data = serialize(msg)
        restored = deserialize(data, CommandResponse)
        assert restored.success is False
        assert restored.error == "Method not found"


class TestSerialization:
    def test_produces_bytes(self) -> None:
        msg = HapticState(
            timestamp=1.0,
            sequence=0,
            position=[0.0, 0.0, 0.0],
            velocity=[0.0, 0.0, 0.0],
            force=[0.0, 0.0, 0.0],
            active_field="null",
            field_state={},
        )
        data = serialize(msg)
        assert isinstance(data, bytes)

    def test_benchmark_haptic_state(self, benchmark: object) -> None:
        """Benchmark: serialize + deserialize should take < 50 µs."""
        msg = HapticState(
            timestamp=1234.5678,
            sequence=42,
            position=[0.01, 0.02, 0.03],
            velocity=[0.1, 0.2, 0.3],
            force=[1.0, 2.0, 3.0],
            active_field="spring",
            field_state={"stiffness": 100.0, "damping": 5.0},
        )

        def round_trip() -> HapticState:
            data = serialize(msg)
            return deserialize(data, HapticState)  # type: ignore[return-value]

        benchmark(round_trip)  # type: ignore[operator]

    def test_round_trip_under_50us(self) -> None:
        """Assert round-trip stays under 50 µs."""
        import time

        msg = HapticState(
            timestamp=1234.5678,
            sequence=42,
            position=[0.01, 0.02, 0.03],
            velocity=[0.1, 0.2, 0.3],
            force=[1.0, 2.0, 3.0],
            active_field="spring",
            field_state={"stiffness": 100.0, "damping": 5.0},
        )
        num_iterations = 10_000
        start = time.perf_counter()
        for _ in range(num_iterations):
            deserialize(serialize(msg), HapticState)
        elapsed = (time.perf_counter() - start) / num_iterations
        assert elapsed < 50e-6, f"Round-trip {elapsed * 1e6:.1f} µs exceeds 50 µs"
