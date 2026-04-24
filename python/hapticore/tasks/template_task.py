"""Template task for creating new behavioral tasks.

Copy this file to create a new task:

    cp python/hapticore/tasks/template_task.py python/hapticore/tasks/my_task.py

Then edit the class attributes and callbacks below. See
``docs/task_authoring_guide.md`` for full documentation.
"""

from __future__ import annotations

from typing import Any

from hapticore.core.messages import HapticState
from hapticore.tasks.base import BaseTask, ParamSpec


class TemplateTask(BaseTask):
    """Template task — copy and customize for your experiment.

    This task has three states as a minimal example:
        iti → active → success

    Replace these with your task's actual state machine.
    """

    # ---- Declare task parameters ----
    # Each parameter has a type, default value, optional bounds and units.
    PARAMS = {
        "hold_time": ParamSpec(
            type=float, default=0.5, unit="s",
            description="Duration the subject must hold in the target",
        ),
        "timeout": ParamSpec(
            type=float, default=2.0, unit="s",
            description="Maximum time allowed before timeout",
        ),
        "target_radius": ParamSpec(
            type=float, default=0.015, unit="m",
            description="Acceptance radius for target zone",
        ),
        "reward_ms": ParamSpec(
            type=int, default=100, min=1, max=1000, unit="ms",
            description="Reward solenoid pulse duration",
        ),
    }

    # ---- Declare states ----
    # List all states in your state machine.
    STATES = ["iti", "active", "success", "timeout"]

    # ---- Declare transitions ----
    # Each transition has a trigger name, source state(s), and destination state.
    # Trigger names become methods on the task: self.trigger("trigger_name")
    TRANSITIONS = [
        {"trigger": "trial_begin", "source": "iti", "dest": "active"},
        {"trigger": "completed", "source": "active", "dest": "success"},
        {"trigger": "time_expired", "source": "active", "dest": "timeout"},
        {"trigger": "trial_end", "source": ["success", "timeout"], "dest": "iti"},
    ]

    # ---- Initial state ----
    INITIAL_STATE = "iti"

    # ---- State callbacks ----
    # Implement on_enter_<state> and on_exit_<state> for each state.
    # These are called automatically by the transitions library.
    #
    # IMPORTANT: All on_enter_*/on_exit_* callbacks must accept an
    # `event` parameter (can default to None). This is required by the
    # transitions library's send_event=True setting used by TaskController.
    # If you forget this parameter, you will get a confusing TypeError
    # at runtime.

    def on_enter_active(self, event: Any = None) -> None:
        """Called when entering the 'active' state.

        Set up force fields, show stimuli, start timers.
        """
        # Example: set a spring force field
        # self.haptic.send_command(Command(
        #     command_id=self.new_command_id(),
        #     method="set_force_field",
        #     params={"type": "spring_damper", "params": {"center": [0, 0, 0], "stiffness": 200}},
        # ))

        # Example: show a target
        # self.display.show_stimulus("target", {
        #     "type": "circle", "position": [0, 0], "radius": 0.015
        # })

        # Example: start a timeout timer
        self.timer.set("time_expired", self.params["timeout"])

        # Example: send event code for recording alignment
        # self.sync.send_event_code(10)

    def on_enter_success(self, event: Any = None) -> None:
        """Called when entering the 'success' state."""
        self.timer.cancel("time_expired")
        self.sync.deliver_reward(self.params["reward_ms"])
        self.log_trial(outcome="success")
        self.timer.set("trial_end", 0.5)

    def on_enter_timeout(self, event: Any = None) -> None:
        """Called when entering the 'timeout' state."""
        self.log_trial(outcome="timeout")
        self.timer.set("trial_end", 0.5)

    # ---- Continuous trigger checking ----

    def check_triggers(self, haptic_state: HapticState) -> None:
        """Called every main-loop iteration with the latest haptic state.

        Override to fire triggers based on position, velocity, etc.
        """
        # Example: check if hand is in the target zone
        # pos = haptic_state.position
        # if self.state == "active":
        #     target = self.current_condition.get("target_position", [0, 0, 0])
        #     if self.distance(pos, target) < self.params["target_radius"]:
        #         self.trigger("completed")
