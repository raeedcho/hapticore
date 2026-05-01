"""Cup-and-ball transport task with cart-pendulum dynamics.

The subject moves a cup-and-ball system from a left target to a right
target without spilling. A preview period shows the initial ball angle
before the go cue.

Every force field command is wrapped in a composite with a horizontal
channel (constrain Y and Z, free in X) so the task is always a 1D
transport along the X-axis.

States:
    iti → move_to_left → hold_left → preview → reach → hold_right →
    success / spill / timeout
"""

from __future__ import annotations

import math
from typing import Any

from hapticore.core.messages import Command, HapticState
from hapticore.display._field_visuals import (
    create_cart_pendulum_stimuli,
    hide_cart_pendulum_stimuli,
)
from hapticore.tasks.base import BaseTask, ParamSpec


class CupTask(BaseTask):
    """Cup-and-ball transport task."""

    PARAMS = {
        "left_x": ParamSpec(
            type=float, default=-0.06, unit="m",
            description="X-position of the left (start) target",
        ),
        "right_x": ParamSpec(
            type=float, default=0.06, unit="m",
            description="X-position of the right (end) target",
        ),
        "target_half_width": ParamSpec(
            type=float, default=0.015, unit="m",
            description="Half-width of rectangular targets",
        ),
        "target_height": ParamSpec(
            type=float, default=0.06, unit="m",
            description="Height of rectangular targets",
        ),
        "hold_time": ParamSpec(
            type=float, default=0.5, unit="s",
            description="Required hold duration at left and right targets",
        ),
        "preview_duration": ParamSpec(
            type=float, default=1.0, unit="s",
            description="Duration cup-ball preview is shown before go cue",
        ),
        "reach_timeout": ParamSpec(
            type=float, default=5.0, unit="s",
            description="Maximum time to reach the right target",
        ),
        "spill_timeout": ParamSpec(
            type=float, default=1.0, unit="s",
            description="Time to remain in spill state before starting next trial",
        ),
        "iti_duration": ParamSpec(
            type=float, default=1.0, unit="s",
            description="Inter-trial interval duration",
        ),
        "pendulum_length": ParamSpec(
            type=float, default=0.1, unit="m",
            description="Pendulum string length (must match force field)",
        ),
        "spill_threshold": ParamSpec(
            type=float, default=math.pi / 2, unit="rad",
            description="Ball angle at which spill is triggered",
        ),
        "ball_mass": ParamSpec(
            type=float, default=0.6, unit="kg",
            description="Ball mass for cart-pendulum dynamics",
        ),
        "ball_radius": ParamSpec(
            type=float, default=0.004, unit="m",
            description="Ball radius for display only (does not affect physics)",
        ),
        "cup_mass": ParamSpec(
            type=float, default=2.4, unit="kg",
            description="Cup mass for cart-pendulum dynamics",
        ),
        "cup_thickness": ParamSpec(
            type=float, default=0.003, unit="m",
            description="Cup thickness for display only (does not affect physics)",
        ),
        "angular_damping": ParamSpec(
            type=float, default=0.05, unit="N·m·s/rad",
            description="Angular damping coefficient",
        ),
        "coupling_stiffness": ParamSpec(
            type=float, default=1200.0, unit="N/m", min=0.0, max=3000.0,
            description="Stiffness of the coupling spring between cup and hand",
        ),
        "coupling_damping": ParamSpec(
            type=float, default=20.0, unit="N·s/m", min=0.0, max=100.0,
            description="Damping of the coupling spring between cup and hand",
        ),
        "channel_stiffness": ParamSpec(
            type=float, default=800.0, unit="N/m", min=0.0, max=3000.0,
            description="Stiffness of the Y/Z channel constraint",
        ),
        "channel_damping": ParamSpec(
            type=float, default=15.0, unit="N·s/m", min=0.0, max=100.0,
            description="Damping of the Y/Z channel constraint",
        ),
        "preview_stiffness": ParamSpec(
            type=float, default=200.0, unit="N/m", min=0.0, max=3000.0,
            description="Spring stiffness holding cursor at start during preview",
        ),
        "preview_damping": ParamSpec(
            type=float, default=5.0, unit="N·s/m", min=0.0,
            description="Spring damping during preview",
        ),
        "reward_ms": ParamSpec(
            type=int, default=100, min=1, max=1000, unit="ms",
            description="Reward solenoid pulse duration",
        ),
    }

    STATES = [
        "iti",
        "move_to_left",
        "hold_left",
        "preview",
        "reach",
        "hold_right",
        "success",
        "spill",
        "spill_end",
        "timeout",
    ]

    TRANSITIONS = [
        {"trigger": "trial_begin", "source": "iti", "dest": "move_to_left"},
        {"trigger": "at_left", "source": "move_to_left", "dest": "hold_left"},
        {"trigger": "hold_complete", "source": "hold_left", "dest": "preview"},
        {"trigger": "broke_hold", "source": "hold_left", "dest": "move_to_left"},
        {"trigger": "go_cue", "source": "preview", "dest": "reach"},
        {"trigger": "at_right", "source": "reach", "dest": "hold_right"},
        {"trigger": "hold_complete", "source": "hold_right", "dest": "success"},
        {"trigger": "broke_hold", "source": "hold_right", "dest": "reach"},
        {"trigger": "spilled", "source": ["reach", "hold_right"], "dest": "spill"},
        {"trigger": "spill_timeout", "source": "spill", "dest": "spill_end"},
        {"trigger": "time_expired", "source": "reach", "dest": "timeout"},
        {"trigger": "trial_end", "source": ["success", "spill_end", "timeout"], "dest": "iti"},
    ]

    INITIAL_STATE = "iti"

    # --- Lifecycle ---

    def on_trial_start(self, condition: dict[str, Any]) -> None:
        super().on_trial_start(condition)
        self.current_condition.setdefault("initial_phi", 0.0)

    # --- State callbacks ---

    def on_enter_move_to_left(self, event: Any = None) -> None:
        """Show left target, channel-only field (free in X)."""
        self._set_channeled_field("null", {})
        lx = self.params["left_x"]
        hw = self.params["target_half_width"]
        h = self.params["target_height"]
        self.display.show_stimulus("left_target", {
            "type": "rectangle",
            "position": [lx, h/2 - self.params["cup_thickness"] - self.params["ball_radius"]],
            "width": hw * 2,
            "height": h,
            "color": [0.7, 0.7, 0.0],
            "fill": False,
        })

    def on_enter_hold_left(self, event: Any = None) -> None:
        self.timer.set("hold_complete", self.params["hold_time"])

    def on_enter_preview(self, event: Any = None) -> None:
        """Hold cursor at start, show cup-ball preview and right target."""
        self.display.update_scene({"__cursor": {"visible": False}})
        phi = self.current_condition["initial_phi"]
        lx = self.params["left_x"]
        rx = self.params["right_x"]
        hw = self.params["target_half_width"]
        h = self.params["target_height"]

        # Spring-damper holds cursor at the left target during preview.
        # The subject sees the ball angle but can't drift away.
        self._set_channeled_field("spring_damper", {
            "center": [lx, 0.0, 0.0],
            "stiffness": self.params["preview_stiffness"],
            "damping": self.params["preview_damping"],
        })

        # Right target
        self.display.show_stimulus("right_target", {
            "type": "rectangle",
            "position": [rx, h/2 - self.params["cup_thickness"] - self.params["ball_radius"]],
            "width": hw * 2,
            "height": h,
            "color": [0.0, 0.7, 0.0],
            "fill": False,
        })

        # Connecting line between targets
        self.display.show_stimulus("track_line", {
            "type": "line",
            "start": [lx-hw, -self.params["cup_thickness"]-self.params["ball_radius"]],
            "end": [rx+hw, -self.params["cup_thickness"]-self.params["ball_radius"]],
            "color": [0.7, 0.7, 0.7],
            "line_width": 1.0,
        })

        # Cup-ball at left target with initial angle.
        # Visuals are frozen because active_field != "cart_pendulum",
        # so _update_cart_pendulum in DisplayProcess doesn't run.
        create_cart_pendulum_stimuli(
            self.display.show_stimulus,
            cup_position=[lx, 0.0],
            initial_phi=phi,
            pendulum_length=self.params["pendulum_length"],
            spill_threshold=self.params["spill_threshold"],
            ball_radius=self.params["ball_radius"],
            cup_thickness=self.params["cup_thickness"],
        )

        self.timer.set("go_cue", self.params["preview_duration"])

    def on_enter_reach(self, event: Any = None) -> None:
        """Go cue: hide left target, engage cart-pendulum field."""
        self.display.hide_stimulus("left_target")

        phi = self.current_condition["initial_phi"]
        self._set_channeled_field("cart_pendulum", {
            "pendulum_length": self.params["pendulum_length"],
            "ball_mass": self.params["ball_mass"],
            "cup_mass": self.params["cup_mass"],
            "coupling_stiffness": self.params["coupling_stiffness"],
            "coupling_damping": self.params["coupling_damping"],
            "angular_damping": self.params["angular_damping"],
            "spill_threshold": self.params["spill_threshold"],
            "initial_phi": phi,
        })

        self.timer.set("time_expired", self.params["reach_timeout"])

    def on_enter_hold_right(self, event: Any = None) -> None:
        self.timer.cancel("time_expired")
        self.timer.set("hold_complete", self.params["hold_time"])

    def on_enter_success(self, event: Any = None) -> None:
        self.sync.deliver_reward(self.params["reward_ms"])
        self.log_trial(outcome="success")
        self._end_trial()

    def on_enter_spill(self, event: Any = None) -> None:
        self.timer.cancel_all()  # Cancel any pending timers
        self.timer.set("spill_timeout", self.params['spill_timeout'])

    def on_enter_spill_end(self, event: Any = None) -> None:
        self.log_trial(outcome="spill")
        self._end_trial()

    def on_enter_timeout(self, event: Any = None) -> None:
        self.log_trial(outcome="timeout")
        self._end_trial()

    # --- Continuous trigger checking ---

    def check_triggers(self, haptic_state: HapticState) -> None:
        cart_pendulum_state = self._get_cart_pendulum_state(haptic_state.field_state)
        pos = haptic_state.position

        if self.state == "move_to_left":
            if self._in_target(pos[0], self.params["left_x"]):
                self.trigger("at_left")

        elif self.state == "hold_left":
            if not self._in_target(pos[0], self.params["left_x"]):
                self.timer.cancel("hold_complete")
                self.trigger("broke_hold")

        elif self.state == "reach":
            # Spill check BEFORE position check — if both are true on the
            # same tick, the trial should fail, not start a hold.
            if cart_pendulum_state.get("spilled", False):
                self._freeze_spill(cart_pendulum_state)
                self.trigger("spilled")
            elif self._in_target(pos[0], self.params["right_x"]):
                self.trigger("at_right")

        elif self.state == "hold_right":
            if cart_pendulum_state.get("spilled", False):
                self._freeze_spill(cart_pendulum_state)
                self.trigger("spilled")
            elif not self._in_target(pos[0], self.params["right_x"]):
                self.timer.cancel("hold_complete")
                self.trigger("broke_hold")

    # --- Helpers ---

    def _set_channeled_field(
        self, primary_type: str, primary_params: dict[str, Any],
    ) -> None:
        """Set a composite field: channel (Y/Z constraint) + primary field.

        Every force field in this task is wrapped in a composite so the
        subject is always constrained to horizontal (X-axis) movement.
        The channel axes [1, 2] and center [0, 0, 0] are task-design
        constants, not tunable parameters.
        """
        self.haptic.send_command(Command(
            command_id=self.new_command_id(),
            method="set_force_field",
            params={
                "type": "composite",
                "params": {
                    "fields": [
                        {
                            "type": "channel",
                            "params": {
                                "axes": [1, 2],
                                "center": [0.0, 0.0, 0.0],
                                "stiffness": self.params["channel_stiffness"],
                                "damping": self.params["channel_damping"],
                            },
                        },
                        {
                            "type": primary_type,
                            "params": primary_params,
                        },
                    ],
                },
            },
        ))

    def _in_target(self, x: float, target_x: float) -> bool:
        """Check if x-position is within the target's horizontal bounds."""
        return abs(x - target_x) < self.params["target_half_width"]

    def _end_trial(self) -> None:
        """Clean up visuals, reset field, start ITI timer."""
        self._set_channeled_field("null", {})
        self._clear_task_visuals()
        self.timer.cancel_all()  # Cancel any pending timers from the trial
        self.display.update_scene({"__cursor": {"visible": True}})
        self.timer.set("trial_end", self.params["iti_duration"])

    def _clear_task_visuals(self) -> None:
        """Remove all task-created stimuli."""
        hide_cart_pendulum_stimuli(self.display.hide_stimulus)
        for sid in ("left_target", "right_target", "track_line"):
            self.display.hide_stimulus(sid)

    def _get_cart_pendulum_state(self, field_state: dict[str, Any]) -> dict[str, Any]:
        """Extract cart-pendulum state from field_state, handling composite wrapping."""
        if "spilled" in field_state:
            return field_state  # bare cart_pendulum field
        for child in field_state.get("children", []):
            if "spilled" in child:
                return child
        return {}

    def _freeze_spill(self, cart_pendulum_state: dict[str, Any]) -> None:
        """When spill is triggered, freeze system for spill_timeout duration."""
        cup_x = cart_pendulum_state.get("cup_x", 0.0)
        # Spring-damper holds hand still during spill, similar to preview.
        # The subject sees the cup and ball frozen until end of spill.
        self._set_channeled_field("spring_damper", {
            "center": [cup_x, 0.0, 0.0],
            "stiffness": self.params["preview_stiffness"],
            "damping": self.params["preview_damping"],
        })
