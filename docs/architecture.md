# Hapticore Architecture

## Overview

Hapticore is a three-tier system for primate neurophysiology experiments involving haptic interaction, visual stimuli, and neural recording. The tiers are separated by timing requirements: sub-millisecond hardware real-time, 1–17 ms soft real-time task control, and 10–100 ms recording and analysis.

```
┌────────────── TIER 1: HARD REAL-TIME (sub-ms) ──────────────┐
│                                                               │
│  C++ Haptic Server (4 kHz)         Teensy Sync (µs-precise)  │
│  - Force Dimension DHD SDK         - TTL event codes          │
│  - Parameterized force fields      - 1 Hz sync square wave    │
│  - Box2D physics for collisions    - Serial from Python       │
│  - ZMQ PUB: state at 200 Hz       - To Ripple + SpikeGLX     │
│  - ZMQ ROUTER: commands                                       │
│                                                               │
│  Ripple Code-on-the-Box (optional)                            │
│  - Closed-loop stim on NIP RT Linux                           │
│  - Configured via xipppy from Tier 2                          │
└──────────────────┬────────────────────┬───────────────────────┘
                   │ ZMQ (ipc://)       │ USB Serial
┌──────────────────▼────────────────────▼───────────────────────┐
│           TIER 2: SOFT REAL-TIME TASK CONTROL (1-17 ms)       │
│                                                               │
│  TaskController process          DisplayProcess               │
│  - transitions state machine     - PsychoPy (OpenGL main thr) │
│  - TrialManager (conditions,     - ZMQ SUB: state + display   │
│    blocks, randomization)        - Frame-locked rendering     │
│  - TimerManager (monotonic)      - Photodiode trigger         │
│  - ZMQ PUB: events                                            │
│  - ZMQ DEALER: haptic commands                                │
│                                                               │
│  SyncProcess                     HapticClientProcess          │
│  - Teensy serial wrapper         - ZMQ SUB: haptic state      │
│  - Event code translation        - ZMQ DEALER: commands       │
│  - LSL marker outlet             - Bridges C++ ↔ Python       │
└──────────────────┬────────────────────────────────────────────┘
                   │ ZMQ PUB-SUB + LSL
┌──────────────────▼────────────────────────────────────────────┐
│            TIER 3: RECORDING & ANALYSIS (10-100 ms)           │
│                                                               │
│  RippleProcess           SpikeGLXProcess      LabRecorder     │
│  - xipppy API            - SpikeGLX SDK       - LSL streams   │
│  - Start/stop recording  - Start/stop run     - XDF files     │
│  - NEV/NS* files         - Binary files       - Sync markers  │
└───────────────────────────────────────────────────────────────┘
```

## Tier 1: C++ haptic server

The haptic server is a standalone C++ executable with three threads.

**Haptic thread** (SCHED_FIFO priority 80, pinned CPU core): Runs at 4 kHz. Each tick: read device position/velocity via `dhdGetPosition()`/`dhdGetLinearVelocity()` → evaluate the active `ForceField::compute(pos, vel, dt)` → clamp forces to device limits → apply via `dhdSetForce()` → write state to a triple buffer. Uses `clock_nanosleep()` for precise timing and `mlockall()` to prevent page faults. Note: the SDK transparently adds gravity compensation forces on top of the application's force vector inside `dhdSetForce()`. The force computed by `ForceField::compute()` represents the task-level force only; the actual force applied to the device includes gravity compensation computed by the SDK based on the current joint configuration.

**Publisher thread**: Reads latest state from the triple buffer at a configurable rate (default 200 Hz). Serializes with msgpack (named keys via `MSGPACK_DEFINE_MAP`). Sends on ZMQ PUB socket.

**Command thread**: Listens on ZMQ ROUTER socket. Deserializes incoming commands, atomically swaps force field parameters into the haptic thread's parameter buffer. Never blocks the haptic thread.

**Force fields** inherit from a `ForceField` base class:
- `NullField`: F = 0 (safe default)
- `SpringDamperField`: F = −K(pos − center) − B·vel
- `ConstantField`: F = constant vector
- `WorkspaceLimitField`: spring forces at boundaries
- `CartPendulumField`: virtual-coupling simulation of cup-and-ball dynamics. The device connects to a simulated cart through a spring-damper coupler; the cart-pendulum ODE is integrated internally with RK4. Virtual mass lives entirely in the simulation, avoiding the instability of direct F=M·a rendering. Returns coupling force to the device.
- `PhysicsField`: wraps a Box2D world for rigid-body dynamics and collision (see ADR-007). Supports polygons, circles, revolute/prismatic joints, and static obstacles. Used for tasks involving collisions (e.g., Tetris-like block placement, air hockey) and underactuated dynamics (e.g., pivoted rod navigation). The monkey controls a kinematic body; Box2D computes reaction forces from contacts and constraints.
- `CompositeField`: sum of multiple fields

**Safety**: force clamping every tick, communication timeout (revert to NullField + damping if no heartbeat in 500 ms), maximum stiffness enforcement, auto-calibration at startup (with `--no-calibrate` override).

**Key design principle**: Python never sends raw force values. Python sends *field parameters* (spring constant, target position, pendulum length, or a full physics world specification). The C++ thread evaluates forces at 4 kHz using these parameters. This decouples the 4 kHz control rate from the ~100 Hz Python rate. See ADR-002 for rationale.

**Dependencies**: Force Dimension SDK (DHD for haptic control, DRD for startup calibration), Box2D v3.0 (via CPM.cmake), cppzmq, msgpack-cxx, pthreads/rt.

### Device type and mass rendering constraint
 
The Force Dimension delta.3 is an **impedance-type** haptic device: it measures position/velocity (via encoders) and commands force (via back-drivable actuators). This contrasts with **admittance-type** devices (e.g., the FCS HapticMaster used for the original cart-pendulum task), which measure force and command position. Impedance devices excel at rendering springs, dampers, and free-space motion but cannot stably render large virtual masses through direct `F = M·a` feedback — double-differentiating sampled position data to estimate acceleration amplifies noise catastrophically at 4 kHz, and the passivity-guaranteed renderable mass is orders of magnitude below what tasks like the cart-pendulum require.
 
This constraint is why force fields that involve virtual inertia (e.g., `CartPendulumField`) use a **virtual coupling** architecture: the dynamics are simulated internally and connected to the physical device through a spring-damper coupler. The device only ever renders spring and damper forces, which are well within its stability envelope. See ADR-010 for the full rationale and literature references.

## Tier 2: Python task control

### Inter-process communication

All processes communicate via ZeroMQ with msgpack serialization. See ADR-001 for rationale.

- **Event distribution** (PUB-SUB): One-to-many broadcast. The TaskController publishes state transitions and trial events. Multiple subscribers (DisplayProcess, SyncProcess, RecordingProcesses) each receive a copy. Topic-filtered multipart messages: `[topic_bytes, msgpack_payload]`.
- **Command/response** (DEALER-ROUTER): Point-to-point. TaskController sends commands to the haptic server (set force field, move to position). Asynchronous—DEALER doesn't block waiting for reply.
- **Transport**: `ipc://` by default for same-machine (15–30 µs latency). `tcp://` available for cross-machine.

### Task controller

The `TaskController` process is the experiment orchestrator. It creates a `transitions.Machine` from the task's declared STATES and TRANSITIONS, wires `on_enter_<state>`/`on_exit_<state>` callbacks, and runs a main loop that polls ZeroMQ for haptic state and dispatches state machine triggers. Every state transition publishes an event on the bus and sends an event code through the SyncProcess.

### Display process

PsychoPy runs in a dedicated process. OpenGL calls must happen in the main thread. The frame loop: drain ZMQ subscriber queue (non-blocking) → update stimulus positions → draw → `win.flip()`. Stimulus onset timestamps captured via `win.callOnFlip()`.

For tasks using PhysicsField (Tetris, air hockey, rod navigation), the display process reads the full set of body positions and angles from the `field_state` dict in the published `HapticState`. It renders all bodies without knowing anything about the physics — just positions and shapes.

### BaseTask structure

Every task is a Python class that declares:
- `PARAMS`: dict of parameter specifications (type, default, range, unit)
- `STATES`: list of state names
- `TRANSITIONS`: list of transition dicts in `transitions` library format
- `HARDWARE`: dict mapping logical names to Protocol types
- State callbacks: `on_enter_<state>()`, `on_exit_<state>()` methods

See `docs/task_authoring_guide.md` for the full task creation workflow.

## Tier 3: Recording and synchronization

### Timestamp alignment

A Teensy 4.1 generates a continuous 1 Hz sync square wave routed to both Ripple's Digital I/O and SpikeGLX's NI-DAQ digital input. The same Teensy outputs event codes (8–16 bit parallel GPIO + strobe) to both systems. Offline alignment extracts sync edges from each system and builds pairwise linear time mappings, achieving < 0.1 ms cross-system accuracy.

### Data directory structure

```
data/sub-{subject}/ses-{date}_{num}/
├── behavior/
│   ├── *_events.csv        # all state transitions and events
│   ├── *_trials.csv        # per-trial summary
│   ├── *_config.json       # resolved experiment config
│   └── *_haptic.bin        # high-rate haptic state log
├── neural/
│   ├── ripple/             # NEV/NS* files from Trellis
│   └── spikeglx/           # binary files from SpikeGLX
├── sync/
│   └── *_sync_edges.csv    # extracted sync edges for alignment
└── lsl/
    └── *_recording.xdf     # LSL LabRecorder output
```

## Message schemas

Defined as Python dataclasses in `hapticore/core/messages.py`:

| Message | Topic | Direction | Rate |
|---------|-------|-----------|------|
| `HapticState` | `b"state"` | haptic server → subscribers | 200 Hz |
| `StateTransition` | `b"event"` | task controller → subscribers | on transition |
| `TrialEvent` | `b"trial"` | task controller → subscribers | on event |
| `Command` | (DEALER-ROUTER) | task controller → haptic server | on demand |
| `CommandResponse` | (DEALER-ROUTER) | haptic server → task controller | on demand |

The `field_state` dict within `HapticState` carries force-field-specific state. For `PhysicsField`, this includes positions and angles of all dynamic bodies — enough for the display process to render the full scene.

## Configuration

The configuration system uses `pydantic-settings` for layered composition from multiple sources. The top-level `ExperimentConfig` (a `BaseSettings` subclass) composes: SubjectConfig, HapticConfig, DisplayConfig, RecordingConfig, TaskConfig, SyncConfig, ZMQConfig. All nested models remain plain `BaseModel`. Validated at load time — invalid parameters fail before any hardware initializes. Resolved config saved as JSON alongside every recording session.

**Source priority** (highest wins):

1. CLI arguments (via `cli_parse_args` parameter to `load_config()`)
2. Constructor kwargs (`overrides` dict passed to `load_config()`)
3. Environment variables (`HAPTICORE_` prefix, `__` double-underscore delimiter)
4. YAML files (layered with deep merge — later files override earlier ones)
5. Field defaults in the Pydantic models

**Layered YAML structure**:

```
configs/
├── rig/
│   └── default.yaml          # haptic workspace, display, ZMQ, sync
├── subject/
│   └── example_subject.yaml  # subject_id, species, implant_info
├── task/
│   └── center_out.yaml       # task_class, params, conditions, block structure
└── example_experiment.yaml   # experiment_name + any overrides
```

Each layer file contains only the keys it owns. Deep merge combines them.

**Session loading** (preferred for real experiments):

```python
config = load_session_config(
    rig="configs/rig/default.yaml",
    subject="configs/subject/example_subject.yaml",
    task="configs/task/center_out.yaml",
    overrides={"experiment_name": "center_out_2026_03_25"},
)
```

`load_session_config()` requires rig, subject, and task paths as **keyword-only** arguments — omitting one raises `TypeError` before any config loading happens. This prevents silently running with default rig values when a layer file is forgotten. Additional YAML files can be passed as `extra=[...]`. The required `experiment_name` field can be provided via an extra YAML or `overrides={"experiment_name": ...}`.

**Flexible loading** (for tests and scripting):

```python
config = load_config(
    "configs/rig/default.yaml",
    "configs/subject/example_subject.yaml",
    "configs/task/center_out.yaml",
    "configs/example_experiment.yaml",
)
```

A single flat YAML still works for simple setups: `load_config("configs/my_experiment.yaml")`.

**CLI usage**:

```bash
# Layered mode (preferred)
hapticore simulate \
    --rig configs/rig/default.yaml \
    --subject configs/subject/example_subject.yaml \
    --task configs/task/center_out.yaml \
    --experiment-name "my_session_2026_03_25"

# Flat file mode (backward compatible)
hapticore simulate --config configs/example_config.yaml
```

See ADR-009 for the rationale behind this design.
