"""Tests for BaseTask and ParamSpec."""

from __future__ import annotations

import re

from hapticore.tasks.base import BaseTask, ParamSpec


class SimpleTestTask(BaseTask):
    """Minimal concrete subclass for testing."""

    PARAMS = {
        "hold_time": ParamSpec(type=float, default=0.5, unit="s"),
        "timeout": ParamSpec(type=float, default=2.0, unit="s"),
    }
    STATES = ["iti", "active", "done"]
    TRANSITIONS = [
        {"trigger": "start", "source": "iti", "dest": "active"},
        {"trigger": "finish", "source": "active", "dest": "done"},
    ]
    INITIAL_STATE = "iti"


class TestParamSpec:
    def test_creation_all_fields(self) -> None:
        spec = ParamSpec(
            type=float,
            default=0.5,
            description="Hold duration",
            unit="s",
            min=0.0,
            max=5.0,
        )
        assert spec.type is float
        assert spec.default == 0.5
        assert spec.description == "Hold duration"
        assert spec.unit == "s"
        assert spec.min == 0.0
        assert spec.max == 5.0

    def test_creation_minimal(self) -> None:
        spec = ParamSpec(type=int, default=8)
        assert spec.type is int
        assert spec.default == 8
        assert spec.description == ""
        assert spec.unit == ""
        assert spec.min is None
        assert spec.max is None

    def test_frozen(self) -> None:
        spec = ParamSpec(type=float, default=0.5)
        try:
            spec.default = 1.0  # type: ignore[misc]
            raise AssertionError("Should raise FrozenInstanceError")
        except AttributeError:
            pass


class TestBaseTask:
    def test_concrete_subclass_instantiation(self) -> None:
        task = SimpleTestTask()
        assert task.INITIAL_STATE == "iti"
        assert len(task.STATES) == 3
        assert len(task.TRANSITIONS) == 2
        assert len(task.PARAMS) == 2

    def test_distance_3d(self) -> None:
        d = BaseTask.distance([0.0, 0.0, 0.0], [3.0, 4.0, 0.0])
        assert abs(d - 5.0) < 1e-9

    def test_distance_identical_points(self) -> None:
        d = BaseTask.distance([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        assert d == 0.0

    def test_distance_known_3d(self) -> None:
        d = BaseTask.distance([1.0, 2.0, 3.0], [4.0, 6.0, 3.0])
        assert abs(d - 5.0) < 1e-9

    def test_new_command_id_format(self) -> None:
        task = SimpleTestTask()
        cid = task.new_command_id()
        assert len(cid) == 12
        assert re.match(r"^[0-9a-f]{12}$", cid)

    def test_new_command_id_unique(self) -> None:
        task = SimpleTestTask()
        id1 = task.new_command_id()
        id2 = task.new_command_id()
        assert id1 != id2

    def test_missing_class_attributes(self) -> None:
        """Accessing required attributes on an incomplete subclass raises."""

        class IncompleteTask(BaseTask):
            pass

        task = IncompleteTask()
        try:
            _ = task.PARAMS
            raise AssertionError("Should raise AttributeError")
        except AttributeError:
            pass

    def test_default_check_triggers_noop(self) -> None:
        task = SimpleTestTask()
        # Should not raise
        task.check_triggers(None)

    def test_default_on_trial_start(self) -> None:
        task = SimpleTestTask()
        task.current_condition = {}
        task.on_trial_start({"target_id": 3})
        assert task.current_condition == {"target_id": 3}

    def test_default_on_trial_end_noop(self) -> None:
        task = SimpleTestTask()
        # Should not raise
        task.on_trial_end("success")

    def test_default_cleanup_noop(self) -> None:
        task = SimpleTestTask()
        # Should not raise
        task.cleanup()
