# Hapticore Task Authoring Guide

This guide explains how to create a new behavioral task in Hapticore. A "task" defines a behavioral paradigm: the states the monkey moves through on each trial, the visual stimuli displayed, the haptic forces felt, and the conditions that determine success or failure.

## What you need to create

A new task requires two things: a **task module** (Python file) and a **task config** (YAML file in `configs/task/`). Optionally, you may also define a **stimulus config** (YAML) and add a new **force field** (C++, only for novel haptic interactions not covered by existing fields).

## Quick start: copy the template

```bash
cp python/hapticore/tasks/template_task.py python/hapticore/tasks/my_task.py
# Create a task config in the layered configs directory
cp configs/task/center_out.yaml configs/task/my_task.yaml
```

Edit both files following the instructions below. At run time, compose the task config with rig and subject layers and supply a per-session experiment name:

```python
config = load_config(
    "configs/rig/rig2.yaml",
    "configs/subject/monkey_a.yaml",
    "configs/task/my_task.yaml",
    overrides={"experiment_name": "my_first_experiment"},
)
```

## Step 1: Define the task class

Every task is a Python class that subclasses `BaseTask`. The class declares its structure using class-level attributes that Hapticore reads at runtime. Here is a minimal center-out reaching task:

```python
"""Center-out reaching task with hold periods."""
from __future__ import annotations
from hapticore.tasks.base import BaseTask, ParamSpec

class CenterOutTask(BaseTask):

    PARAMS = {
        "num_targets": ParamSpec(type=int, default=8, min=1, max=32,
                                 description="Number of peripheral targets"),
        "target_distance": ParamSpec(type=float, default=0.08, unit="m",
                                     description="Distance from center to target"),
        "target_radius": ParamSpec(type=float, default=0.015, unit="m"),
        "hold_time": ParamSpec(type=float, default=0.5, unit="s"),
        "reach_timeout": ParamSpec(type=float, default=2.0, unit="s"),
        "iti_duration": ParamSpec(type=float, default=1.0, unit="s"),
        "reward_ms": ParamSpec(type=int, default=100, min=1, max=1000, unit="ms",
                              description="Reward solenoid pulse duration"),
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
        {"trigger": "trial_begin",    "source": "iti",            "dest": "move_to_center"},
        {"trigger": "at_center",      "source": "move_to_center", "dest": "hold_center"},
        {"trigger": "hold_complete",  "source": "hold_center",    "dest": "reach"},
        {"trigger": "at_target",      "source": "reach",          "dest": "hold_target"},
        {"trigger": "hold_complete",  "source": "hold_target",    "dest": "success"},
        {"trigger": "time_expired",   "source": ["reach", "hold_target"], "dest": "timeout"},
        {"trigger": "broke_hold",     "source": "hold_center",    "dest": "move_to_center"},
        {"trigger": "trial_end",      "source": ["success", "timeout"], "dest": "iti"},
    ]

    INITIAL_STATE = "iti"
```

### How states and transitions work

The `transitions` library creates a state machine from your STATES and TRANSITIONS lists. A **trigger** is a named event that causes a transition from a **source** state to a **dest** state. Triggers can be fired manually (`self.trigger("at_center")`) or by the framework (e.g., `time_expired` from a timer).

The source field can be a single state name, a list of state names, or `"*"` for any state. The `transitions` library also supports `conditions` (guard functions that must return True for the transition to fire) and `unless` (guard functions that must return False). See the [transitions documentation](https://github.com/pytransitions/transitions) for advanced features.

## Step 2: Implement state callbacks

For each state, you can implement `on_enter_<state>()` and `on_exit_<state>()` methods. These are called automatically by the state machine when entering or exiting a state.

**Display units:** All spatial parameters in `show_stimulus()` and `update_scene()` use **meters**, matching the rest of the system. The display process converts meters to cm automatically. `display_scale` (default 1.0) controls how large the haptic workspace appears on screen — 1.0 means 1:1 mapping, 2.0 means everything appears twice as large. Do not pre-convert values to cm.

```python
    def on_enter_move_to_center(self):
        """Guide monkey to the center position."""
        # Set a spring force field pulling toward center
        self.set_field("spring_damper",{
            "center": [0.0, 0.0, 0.0],
            "stiffness": 200.0,  # N/m
            "damping": 5.0,      # N·s/m
        })
        # Show the center target on display
        self.display.show_stimulus("center_target", {
            "type": "circle",
            "position": [0.0, 0.0],
            "radius": self.params["target_radius"],
            "color": [1.0, 1.0, 0.0],  # yellow
        })
        # Send event code for recording alignment
        self.sync.send_event_code(10)

    def on_enter_hold_center(self):
        """Monkey is at center — start hold timer."""
        self.timer.set("hold_complete", self.params["hold_time"])

    def on_enter_reach(self):
        """Go signal — show peripheral target, start timeout."""
        # Remove the guiding spring, just keep workspace limits
        self.set_field("null",{})
        # Get target position from current trial condition
        target_pos = self.current_condition["target_position"]
        self.display.show_stimulus("peripheral_target", {
            "type": "circle",
            "position": target_pos,
            "radius": self.params["target_radius"],
            "color": [0.0, 1.0, 0.0],  # green
        })
        self.timer.set("time_expired", self.params["reach_timeout"])
        self.sync.send_event_code(20)

    def on_enter_success(self):
        """Monkey reached and held the target — reward."""
        self.sync.deliver_reward(self.params["reward_ms"])
        self.log_trial(outcome="success")
        self.timer.set("trial_end", self.params["iti_duration"])

    def on_enter_timeout(self):
        """Monkey failed to reach in time."""
        self.display.clear()
        self.log_trial(outcome="timeout")
        self.timer.set("trial_end", self.params["iti_duration"])
```

### Checking conditions in the main loop

The task controller's main loop polls haptic state at ~100 Hz. Override `check_triggers()` to fire transitions based on continuous data:

```python
    def check_triggers(self, haptic_state: HapticState):
        """Called every main-loop iteration with latest haptic state."""
        pos = haptic_state.position

        if self.state == "move_to_center":
            if self.distance(pos, [0, 0, 0]) < self.params["target_radius"]:
                self.trigger("at_center")

        elif self.state == "reach":
            target = self.current_condition["target_position"]
            if self.distance(pos, target) < self.params["target_radius"]:
                self.trigger("at_target")

        elif self.state == "hold_center":
            if self.distance(pos, [0, 0, 0]) > self.params["target_radius"]:
                self.trigger("broke_hold")
```

## Step 3: Define trial conditions

Create a task config YAML in `configs/task/`. This file contains only the task-specific settings:

```yaml
# configs/task/center_out.yaml
task:
  task_class: "hapticore.tasks.center_out.CenterOutTask"
  params:
    num_targets: 8
    target_distance: 0.08
    hold_time: 0.5
  conditions:
    - {target_id: 0, target_position: [0.08, 0.0]}
    - {target_id: 1, target_position: [0.0566, 0.0566]}
    - {target_id: 2, target_position: [0.0, 0.08]}
    - {target_id: 3, target_position: [-0.0566, 0.0566]}
    - {target_id: 4, target_position: [-0.08, 0.0]}
    - {target_id: 5, target_position: [-0.0566, -0.0566]}
    - {target_id: 6, target_position: [0.0, -0.08]}
    - {target_id: 7, target_position: [0.0566, -0.0566]}
  block_size: 8          # one of each target per block
  num_blocks: 20
  randomization: "pseudorandom"
```

Subject-specific overrides (e.g., different hold time for a new animal) go in a separate subject YAML:

```yaml
# configs/subject/monkey_a.yaml
subject:
  subject_id: "monkey_A"
  species: "macaque"
  implant_info:
    array_type: "utah"
    hemisphere: "left"
    area: "M1"
```

Compose them at load time with `load_config()`:

```python
from hapticore.core.config import load_config

config = load_config(
    "configs/rig/rig2.yaml",          # rig hardware settings
    "configs/subject/monkey_a.yaml",  # subject identity
    "configs/task/center_out.yaml",   # task params + conditions
)
```

The `TrialManager` automatically shuffles conditions within each block, presents them sequentially, and exposes `self.current_condition` to the task at each trial start.

## Step 4: Choosing the right haptic interaction

Hapticore provides two approaches to haptic feedback, depending on the complexity of the virtual environment.

### Approach A: Analytical force fields (simple tasks)

For tasks where forces can be expressed as mathematical functions of position and velocity — springs, dampers, constant forces, viscous curls, or ODE-based dynamics like the cup-and-ball — use the built-in force field types directly. No C++ modification needed.

Built-in field types: `null`, `spring_damper`, `constant`, `workspace_limit`, `cart_pendulum`, `channel`, `composite`.

Example — cup-and-ball task with preview:

```python
from hapticore.display._field_visuals import CartPendulumVisuals

# In on_trial_start — construct the visual helper once per trial:
self._cup_visuals = CartPendulumVisuals(
    self.display,
    pendulum_length=self.params["pendulum_length"],
)

# During the preview/delay state: show cup-and-ball at the starting
# position with a random initial ball angle. The haptic field stays
# null (or spring_damper), so the cup-and-ball visuals are frozen at
# the previewed pose (the device can still move freely, but the display
# won't update the cup/ball positions until the cart_pendulum field is
# engaged).
phi = self.current_condition["initial_phi"]      # e.g. 0.3 radians
cup_pos = self.current_condition["start_position"]  # e.g. [-0.08, 0.0]
self._cup_visuals.show(cup_position=cup_pos, initial_phi=phi)

# At the go cue: engage the cart-pendulum force field with the same
# initial_phi so the simulation starts from the previewed pose.
# _update_cart_pendulum takes over position rendering from here.
self.set_field("cart_pendulum", {
    "pendulum_length": 0.3,
    "ball_mass": 0.6,
    "cup_mass": 2.4,
    "angular_damping": 0.05,
    "initial_phi": phi,
})
```

Semantic visual changes (e.g., spill indication) are handled through the visual helper, not the display renderer. This avoids a race between the display process and task controller:

```python
# On spill detection:
self._cup_visuals.set_ball_color([1.0, 0.3, 0.3])  # red

# To restore default colors:
self._cup_visuals.reset_ball_color()
```

To hide the visuals (e.g., at trial end):

```python
self._cup_visuals.hide()
```

The key contract: both `initial_phi` and `pendulum_length` passed to `CartPendulumVisuals.show()` must match the same parameters passed to `set_field("cart_pendulum", ...)` so the visual preview and the simulation start at the same pose. Any mismatch will cause a visible "jump" when the field engages.

Note: the cart-pendulum model is 1D — the C++ simulation only tracks horizontal (X) motion. When the field engages, `_update_cart_pendulum` sets `cup_y` to the display offset (effectively Y=0 in workspace coordinates). If `cup_position[1]` in the preview is non-zero, the cup will jump vertically when the field takes over. For a smooth transition, always use `cup_position=[x, 0.0]`.

### Background fields

Tasks can declare background force fields that are automatically applied
to every `set_field()` call. This is useful for channel constraints and
workspace limits that should always be active:

```python
# In on_trial_start — declare background fields once:
self.background_fields = [
    {
        "type": "channel",
        "params": {
            "axes": [1, 2],  # constrain Y and Z
            "center": [0.0, 0.0, 0.0],
            "stiffness": 800.0,
            "damping": 15.0,
        },
    },
]

# Every subsequent set_field call wraps the primary field in a
# composite with the background fields as siblings:
self.set_field("spring_damper", {...})
# → sends composite(channel + spring_damper)

self.set_field("cart_pendulum", {...})
# → sends composite(channel + cart_pendulum)

self.set_field("null", {})
# → sends composite(channel + null) — channel stays active during ITI
```

If `background_fields` is empty (the default), `set_field()` sends the
primary field directly with no composite wrapper.

Example — constrain to a horizontal plane (free in X and Y, held at Z=0):

```python
self.set_field("channel", {
    "axes": [2],            # constrain Z only
    "stiffness": 800,
    "damping": 15,
    "center": [0, 0, 0],   # hold Z at zero
})
```

Example — constrain cup-and-ball to a horizontal line using `background_fields`:

```python
# In on_trial_start:
self.background_fields = [
    {"type": "channel", "params": {
        "axes": [1, 2],       # constrain Y and Z (free in X)
        "stiffness": 800,
        "damping": 15,
        "center": [0, 0, 0],
    }},
]

# In on_enter_reach — set_field automatically wraps in composite:
self.set_field("cart_pendulum", {
    "pendulum_length": 0.6,
    "ball_mass": 0.6,
    "cup_mass": 2.4,
})
```

To add a *new* analytical force field (e.g., a velocity-dependent curl field), create a C++ `ForceField` subclass in `cpp/haptic_server/src/force_fields/`, implement `compute(pos, vel, dt)`, and register the type name in the command dispatcher. See `CartPendulumField` as a reference.

### Approach B: Physics world (tasks with collisions and rigid bodies)

For tasks involving collisions, multiple interacting objects, or joints — Tetris-like block placement, air hockey, or pivoted rod navigation — use the `PhysicsField`, which wraps a Box2D 2D physics engine running inside the haptic thread at 4 kHz.

You describe the physics world declaratively from Python: define bodies (with shapes, masses, and types), joints between bodies, and static obstacles. The monkey controls a *kinematic body* — Box2D moves all other bodies according to physics and returns the reaction forces the monkey feels through the robot.

Example — air hockey task:

```python
self.set_field("physics_world", {
    "gravity": [0.0, 0.0],           # top-down, no gravity
    "bodies": [
        {
            "id": "striker",
            "type": "kinematic",       # controlled by the robot
            "shape": {"type": "circle", "radius": 0.02},
        },
        {
            "id": "puck",
            "type": "dynamic",
            "shape": {"type": "circle", "radius": 0.015},
            "position": [0.0, 0.05],
            "mass": 0.1,
            "restitution": 0.9,        # elastic bouncing
            "friction": 0.1,
            "linear_damping": 0.3,     # simulates table friction
        },
        {
            "id": "wall_top",
            "type": "static",
            "shape": {"type": "box", "width": 0.3, "height": 0.005},
            "position": [0.0, 0.12],
        },
        # ... more walls, goal gaps, etc.
    ],
    "hand_body": "striker",
})
```

Example — pivoted rod with barriers:

```python
self.set_field("physics_world",{
    "gravity": [0.0, -9.81],
    "bodies": [
        {
            "id": "rod",
            "type": "dynamic",
            "shape": {"type": "box", "width": 0.2, "height": 0.01},
            "joint": {"type": "revolute", "anchor": "hand", "offset": [0.0, 0.0]},
            "mass": 0.3,
        },
        {
            "id": "barrier_left",
            "type": "static",
            "shape": {"type": "box", "width": 0.01, "height": 0.1},
            "position": [-0.08, 0.0],
        },
        {
            "id": "barrier_right",
            "type": "static",
            "shape": {"type": "box", "width": 0.01, "height": 0.1},
            "position": [0.08, 0.0],
        },
    ],
    "hand_body": "rod",
})
```

#### How the display renders physics worlds

The display process does not need to know about Box2D. The `HapticState.field_state` dict published by the haptic server includes the positions and angles of all dynamic bodies:

```python
# In check_triggers or display update:
bodies = haptic_state.field_state.get("bodies", {})
# bodies = {"puck": {"position": [0.03, 0.07], "angle": 0.0},
#            "striker": {"position": [0.01, -0.02], "angle": 0.0}}
```

The display process uses these positions to render shapes at the correct locations. The mapping between body IDs and visual representations is defined in the task's display logic.

#### When to use Approach A vs. B

Use **analytical fields** when forces depend smoothly on position/velocity and there are no rigid contacts between objects. These are simpler to tune and debug.

Use **PhysicsField** when the task involves:
- Object-object or object-wall collisions (anything that should feel like rigid contact)
- Multiple bodies interacting through physics (puck and striker, blocks stacking)
- Joints and constraints (pivots, sliders, ropes)
- Any scenario where you want the monkey to feel the reaction forces from a simulated physical environment

## Step 5: Test without hardware

Run your task using the CI rig config (mock interfaces, no hardware required):

```bash
hapticore run \
    --rig configs/rig/ci.yaml \
    --subject configs/subject/example_subject.yaml \
    --task configs/task/my_task.yaml \
    --experiment-name "my_task_test"
```

This launches all processes with mock hardware:
- **Mock haptic**: returns synthetic position data (stationary at origin by default, or a scripted trajectory with `--replay trajectory.csv`)
- **Mock display**: renders to a window without vsync enforcement
- **Mock recording**: logs to local files
- **Mock sync**: logs event codes to console

The full state machine runs. Check the event log to verify correct state sequences. Run a full session of trials to catch edge cases in transition logic.

### Automated testing

Write a pytest test for your task:

```python
def test_center_out_correct_trial(mock_hardware):
    """Verify state sequence for a correct trial."""
    task = CenterOutTask()
    task.setup(hardware=mock_hardware, params={"hold_time": 0.1}, ...)

    # Simulate: start trial
    task.trigger("trial_begin")
    assert task.state == "move_to_center"

    # Simulate: hand arrives at center
    mock_hardware["haptic"].set_position([0.0, 0.0, 0.0])
    task.check_triggers(mock_hardware["haptic"].get_latest_state())
    assert task.state == "hold_center"

    # Simulate: hold period elapses
    task.trigger("hold_complete")
    assert task.state == "reach"

    # ... continue through success
```

### Visualize the state machine

Generate a diagram of your task's state machine:

```bash
hapticore graph-task hapticore.tasks.center_out.CenterOutTask --output my_task.svg
```

This uses the `transitions` library's `GraphMachine` to produce an SVG showing all states and transitions. Useful for lab meetings and documentation.

## Summary of files to create for a new task

| File | Required? | Purpose |
|------|-----------|---------|
| `python/hapticore/tasks/my_task.py` | Yes | Task class with STATES, TRANSITIONS, callbacks |
| `configs/task/my_task.yaml` | Yes | Task config with params and conditions |
| `configs/subject/monkey_x.yaml` | Yes (per subject) | Subject identity and implant info |
| `tests/unit/test_my_task.py` | Recommended | Automated state-machine tests with mocks |
| `cpp/.../my_custom_field.h/.cpp` | Only for novel analytical force computations | Custom ForceField subclass |

Most tasks — including those with collisions and rigid body dynamics — do not require any C++ changes. The `PhysicsField` handles collision detection, contact forces, and joint dynamics via Box2D, configured entirely from Python.

## Common pitfalls

### Hold-break detection: check every tick, not just on entry

A common mistake is checking whether the hand left a hold zone only once (e.g., in `on_enter_hold_center`). The hand can leave at any point during the hold period. Check distance continuously in `check_triggers()`:

```python
def check_triggers(self, haptic_state: HapticState):
    if self.state == "hold_center":
        if self.distance(haptic_state.position, [0, 0, 0]) > self.params["target_radius"]:
            self.trigger("broke_hold")
```

Without this, the monkey can leave the hold zone after entry and still get rewarded when the hold timer expires.

### Timer cleanup: cancel timers on state exit

If a state sets a timer (e.g., `self.timer.set("time_expired", 2.0)`), cancel it when leaving that state early. Otherwise the timer fires in the wrong state, potentially triggering an invalid transition:

```python
def on_exit_reach(self):
    self.timer.cancel("time_expired")
```

This is especially important for states that can be exited by multiple triggers (e.g., `reach` can end via `at_target` or `time_expired`).

### Unreachable transitions: verify every trigger has a path

The `transitions` library silently ignores triggers that don't match any transition from the current state. If you misspell a trigger name or forget to add a transition, the state machine silently stays put. Use `hapticore graph-task` to visualize and manually verify that every state has a way out:

```bash
hapticore graph-task hapticore.tasks.my_task.MyTask
```

A state with no outgoing transitions is a deadlock — the task will hang there forever.


### PsychoPy text stimuli and window `viewScale`

PsychoPy text stimuli (`visual.TextStim`) do not compose cleanly with window-level `viewScale` — text can render un-flipped even in a mirrored frame. This is not a concern for current Hapticore stimuli (circles, rectangles, physics bodies), but if you ever add text to a task, flip it per-stimulus with `flipHoriz=True` on the `TextStim` object rather than relying on the window-level mirror.
