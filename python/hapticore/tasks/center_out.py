"""Center-out reaching task with hold periods.

This is the reference implementation of a Hapticore behavioral task.
The monkey must move to a center target, hold, then reach to a peripheral
target and hold. Successful trials are rewarded; timeouts are logged.

States:
    iti → move_to_center → hold_center → reach → hold_target → success/timeout
"""

from __future__ import annotations

from typing import Any

from hapticore.core.messages import Command, HapticState
from hapticore.tasks.base import BaseTask, ParamSpec


class CenterOutTask(BaseTask):
    """Center-out reaching task."""

    PARAMS = {
        "num_targets": ParamSpec(
            type=int, default=8, min=1, max=32,
            description="Number of peripheral targets",
        ),
        "target_distance": ParamSpec(
            type=float, default=0.08, unit="m",
            description="Distance from center to target",
        ),
        "target_radius": ParamSpec(
            type=float, default=0.015, unit="m",
            description="Radius of acceptance zone for targets",
        ),
        "hold_time": ParamSpec(
            type=float, default=0.5, unit="s",
            description="Required hold duration at center and target",
        ),
        "reach_timeout": ParamSpec(
            type=float, default=2.0, unit="s",
            description="Maximum time to reach the peripheral target",
        ),
        "iti_duration": ParamSpec(
            type=float, default=1.0, unit="s",
            description="Inter-trial interval duration",
        ),
    }

    STATES = [
        "iti",
        "move_to_center",
        "hold_center",
        "reach",
        "hold_target",
        "success",
        "timeout",
    ]

    TRANSITIONS = [
        {"trigger": "trial_begin", "source": "iti", "dest": "move_to_center"},
        {"trigger": "at_center", "source": "move_to_center", "dest": "hold_center"},
        {"trigger": "hold_complete", "source": "hold_center", "dest": "reach"},
        {"trigger": "at_target", "source": "reach", "dest": "hold_target"},
        {"trigger": "hold_complete", "source": "hold_target", "dest": "success"},
        {"trigger": "time_expired", "source": "reach", "dest": "timeout"},
        {"trigger": "broke_hold", "source": "hold_center", "dest": "move_to_center"},
        {"trigger": "broke_target_hold", "source": "hold_target", "dest": "reach"},
        {"trigger": "trial_end", "source": ["success", "timeout"], "dest": "iti"},
    ]

    INITIAL_STATE = "iti"

    # --- Lifecycle ---

    def on_trial_start(self, condition: dict[str, Any]) -> None:
        """Normalize condition dict before callbacks run.

        Ensures ``target_position`` is always present (defaulting to the
        ``target_distance`` parameter on the X-axis) and is always 3D so
        distance checks in callbacks never need to extend it.
        """
        super().on_trial_start(condition)
        if "target_position" not in self.current_condition:
            self.current_condition["target_position"] = [
                self.params["target_distance"], 0.0, 0.0,
            ]
        tp = self.current_condition["target_position"]
        if len(tp) == 2:
            self.current_condition["target_position"] = [tp[0], tp[1], 0.0]

    # --- State callbacks ---

    def on_enter_move_to_center(self, event: Any = None) -> None:
        """Guide monkey to the center position."""
        self.haptic.send_command(Command(
            command_id=self.new_command_id(),
            method="set_force_field",
            params={
                "type": "spring_damper",
                "center": [0.0, 0.0, 0.0],
                "stiffness": 200.0,
                "damping": 5.0,
            },
        ))
        self.display.show_stimulus("center_target", {
            "type": "circle",
            "position": [0.0, 0.0],
            "radius": self.params["target_radius"],
            "color": [1.0, 1.0, 0.0],
        })
        self.sync.send_event_code(10)

    def on_enter_hold_center(self, event: Any = None) -> None:
        """Monkey is at center — start hold timer."""
        self.timer.set("hold_complete", self.params["hold_time"])

    def on_enter_reach(self, event: Any = None) -> None:
        """Go signal — show peripheral target, start timeout."""
        self.haptic.send_command(Command(
            command_id=self.new_command_id(),
            method="set_force_field",
            params={"type": "null"},
        ))
        target_pos = self.current_condition["target_position"]
        self.display.show_stimulus("peripheral_target", {
            "type": "circle",
            "position": target_pos[:2],
            "radius": self.params["target_radius"],
            "color": [0.0, 1.0, 0.0],
        })
        self.timer.set("time_expired", self.params["reach_timeout"])
        self.sync.send_event_code(20)

    def on_enter_hold_target(self, event: Any = None) -> None:
        """Monkey reached the target — start hold timer, cancel reach timeout."""
        self.timer.cancel("time_expired")
        self.timer.set("hold_complete", self.params["hold_time"])

    def on_enter_success(self, event: Any = None) -> None:
        """Monkey reached and held the target — reward."""
        self.reward()
        self.log_trial(outcome="success")
        self.display.clear()
        self.timer.set("trial_end", self.params["iti_duration"])
        self.sync.send_event_code(30)

    def on_enter_timeout(self, event: Any = None) -> None:
        """Monkey failed to reach in time."""
        self.display.clear()
        self.log_trial(outcome="timeout")
        self.timer.set("trial_end", self.params["iti_duration"])
        self.sync.send_event_code(40)

    # --- Continuous trigger checking ---

    def check_triggers(self, haptic_state: HapticState) -> None:
        """Fire position-based triggers based on current haptic state."""
        pos = haptic_state.position

        if self.state == "move_to_center":
            if self.distance(pos, [0.0, 0.0, 0.0]) < self.params["target_radius"]:
                self.trigger("at_center")

        elif self.state == "reach":
            target = self.current_condition["target_position"]
            if self.distance(pos, target) < self.params["target_radius"]:
                self.trigger("at_target")

        elif self.state == "hold_center":
            if self.distance(pos, [0.0, 0.0, 0.0]) > self.params["target_radius"]:
                self.timer.cancel("hold_complete")
                self.trigger("broke_hold")

        elif self.state == "hold_target":
            target = self.current_condition["target_position"]
            if self.distance(pos, target) > self.params["target_radius"]:
                self.timer.cancel("hold_complete")
                self.trigger("broke_target_hold")
