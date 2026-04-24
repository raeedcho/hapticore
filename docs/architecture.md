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
│  - Safety field (beam break+FTDI)  - To Ripple + SpikeGLX     │
│  - ZMQ PUB: state at 200 Hz        - Reward TTL               │
│  - ZMQ ROUTER: commands            - 30-120 Hz Camera trigger │
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
│                                                               │
│  Camera recording                                             │
│  - separate repository                                        │
│  - H.264 video                                                │
│  - Timestamp CSVs                                             │
│                                                               │
│  Ripple + SpikeGLX record the Teensy 1 Hz sync.               │
│  Camera 1 strobe feeds Ripple for frame-to-neural alignment.  │
│  8-bit event codes land in both neural streams (ADR-014).     │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

## Tier 1: Hardware

### C++ haptic server

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

**Safety**: force clamping every tick, communication timeout (revert to NullField + damping if no heartbeat in 500 ms), maximum stiffness enforcement, auto-calibration at startup (with `--no-calibrate` override). Beam-break-triggered safety field engagement on handle release is planned; see issue/spec TBD.

**Key design principle**: Python never sends raw force values. Python sends *field parameters* (spring constant, target position, pendulum length, or a full physics world specification). The C++ thread evaluates forces at 4 kHz using these parameters. This decouples the 4 kHz control rate from the ~100 Hz Python rate. See ADR-002 for rationale.

**Dependencies**: Force Dimension SDK (DHD for haptic control, DRD for startup calibration), Box2D v3.0 (via CPM.cmake), cppzmq, msgpack-cxx, pthreads/rt.

#### Device type and mass rendering constraint
 
The Force Dimension delta.3 is an **impedance-type** haptic device: it measures position/velocity (via encoders) and commands force (via back-drivable actuators). This contrasts with **admittance-type** devices (e.g., the FCS HapticMaster used for the original cart-pendulum task), which measure force and command position. Impedance devices excel at rendering springs, dampers, and free-space motion but cannot stably render large virtual masses through direct `F = M·a` feedback — double-differentiating sampled position data to estimate acceleration amplifies noise catastrophically at 4 kHz, and the passivity-guaranteed renderable mass is orders of magnitude below what tasks like the cart-pendulum require.
 
This constraint is why force fields that involve virtual inertia (e.g., `CartPendulumField`) use a **virtual coupling** architecture: the dynamics are simulated internally and connected to the physical device through a spring-damper coupler. The device only ever renders spring and damper forces, which are well within its stability envelope. See ADR-010 for the full rationale and literature references.

### Teensy 4.1 sync hub
 
A Teensy 4.1 microcontroller serves as the centralized timing hub for the entire rig, generating all TTL sync and trigger signals from a single crystal oscillator. It is connected to the haptic control rig via USB serial and controlled by Hapticore's `SyncProcess`.
 
#### Output signals
 
| Teensy pin | Signal | Frequency | Destination |
|---|---|---|---|
| Pin A | Camera frame trigger | 30–120 Hz | All 5 cameras, Line 3 (parallel wired) |
| Pin B | Cross-system sync | 1 Hz, 50% duty cycle | BNC T-split → Ripple Scout SMA input + SpikeGLX NI-DAQ digital input |
| Pin C | Behavioral event strobe | On-demand | BNC T-split → Ripple Scout SMA input + SpikeGLX NI-DAQ digital input |
| Pin D | Reward TTL | On-demand | Solenoid driver circuit |
 
The 1 Hz sync and event strobe each use a single Teensy output pin split to both recording systems via BNC T-connectors. This guarantees both systems see identical edge timing with zero inter-pin skew.
 
#### Serial protocol
 
`SyncProcess` sends ASCII commands over USB serial:
- `S1` / `S0` — start/stop 1 Hz sync pulse generation
- `C<rate>` — set camera trigger rate (e.g., `C60` for 60 Hz)
- `T1` / `T0` — start/stop camera trigger generation
- `E<code>` — emit a behavioral event code (8-bit parallel, per ADR-014)
- `R<duration_ms>` — pulse reward TTL for specified duration

#### Firmware location
 
Teensy firmware lives in `hapticore/firmware/teensy/` and builds via PlatformIO or Arduino IDE + Teensyduino. The firmware, event code definitions, and `SyncProcess` are maintained together in the monorepo because they share message schemas and must evolve atomically (see ADR-006, ADR-013).
 
#### Hardware notes
 
- Teensy 4.1 outputs 3.3V logic. Blackfly S Line 3 input threshold is 2.6V — direct connection works. Ripple Scout and NI-DAQ LVTTL inputs accept 3.3V (TTL high threshold is 2.0V). If any downstream device requires 5V, add a 74AHCT125 level-shifting buffer.
- Camera trigger uses a hardware IntervalTimer (PIT-based, ~6.67 ns resolution) for jitter-free pulse generation independent of serial command processing.
- The 1 Hz sync also uses a hardware timer, not a software delay loop.

### Camera subsystem (separate repository)
 
Five synchronized Blackfly S cameras capture markerless motion data for 3D pose estimation. The camera system runs on a **dedicated camera PC** and is maintained in a separate repository (see ADR-012). It has no runtime coupling to Hapticore — the integration is purely through hardware sync signals and post-hoc file alignment.
 
#### Hardware trigger architecture
 
All five Blackfly S cameras operate in external trigger mode. The Teensy sync hub emits a single frame-trigger signal (30–120 Hz, configurable per session) that is wired in parallel to Line 3 of every camera. Every rising edge captures one frame on all five cameras simultaneously. Inter-camera synchronization is therefore a hardware property — sub-microsecond by construction, because all cameras receive the same physical edge.

Because the five cameras are hardware-locked by the shared trigger, one return signal to the neural recording is sufficient to anchor camera timing in neural time. Camera 1's exposure-active strobe (Line 1 or Line 2, configured in Spinnaker) is wired back to a Ripple Scout SMA input and produces one neural-side pulse per captured frame. The Spinnaker hardware frame counter on the camera PC remains the authoritative source for per-camera drop detection; the neural-side strobe loopback provides alignment, not redundant drop detection.

Camera-PC clock to neural-clock alignment uses the strobe loopback itself: each captured frame has a Spinnaker timestamp on the camera PC and a strobe edge on Ripple, and a linear fit of paired timestamps maps between the two clocks. The 1 Hz cross-system sync is not separately wired to the camera PC — it does not need to be, because the per-frame strobe pairs already over-determine the clock mapping at 30–120 Hz.
 
#### Interface contract
 
The camera system writes:
- Per-camera compressed video files (H.264 NVENC)
- Per-camera timestamp CSVs with columns: `frame_number`, `hardware_timestamp`, `system_timestamp`, `exposure_time_us`
- A session metadata JSON with camera serial numbers, resolution, frame rate, trigger source, and Spinnaker SDK version
These files are placed in a session directory following the same naming convention as Hapticore's session data. Post-hoc alignment pairs each camera frame's Spinnaker timestamp with its strobe edge on Ripple, producing a linear map between the camera PC's clock and neural time.

## Tier 2: Python task control

### Inter-process communication

All processes communicate via ZeroMQ with msgpack serialization. See ADR-001 for rationale.

- **Event distribution** (PUB-SUB): One-to-many broadcast. The TaskController publishes state transitions and trial events. Multiple subscribers (DisplayProcess, SyncProcess, RecordingProcesses) each receive a copy. Topic-filtered multipart messages: `[topic_bytes, msgpack_payload]`.
- **Command/response** (DEALER-ROUTER): Point-to-point. TaskController sends commands to the haptic server (set force field, move to position). Asynchronous—DEALER doesn't block waiting for reply.
- **Transport**: `ipc://` by default for same-machine (15–30 µs latency). `tcp://` available for cross-machine.

### Task controller

The `TaskController` process is the experiment orchestrator. It creates a `transitions.Machine` from the task's declared STATES and TRANSITIONS, wires `on_enter_<state>`/`on_exit_<state>` callbacks, and runs a main loop that polls ZeroMQ for haptic state and dispatches state machine triggers. Every state transition publishes an event on the bus and sends an event code through the SyncProcess. The production path selects between `HapticClient` (real delta.3), `MockHapticInterface` (CI/simulation), and `MouseHapticInterface` (laptop development) via the `kind` field of `HapticConfig`, using the `make_haptic_interface()` factory in `hapticore.hardware`. Both `HapticClient` and all mock interfaces satisfy the `HapticInterface` Protocol.

### Display process

PsychoPy runs in a dedicated process (`DisplayProcess`). OpenGL calls must happen in the main thread. The frame loop: drain ZMQ subscriber queue (non-blocking) → update stimulus positions → update from field state → draw → `win.flip()`. Stimulus onset timestamps captured via `win.callOnFlip()`.

**Display command protocol:** The TaskController sends commands on the `b"display"` topic via the `EventPublisher`. Commands are msgpack-encoded dicts with an `"action"` key:
- `"show"` — create/replace a stimulus (`stim_id`, `params` with `"type"` key)
- `"hide"` — remove a stimulus (`stim_id`)
- `"clear"` — remove all stimuli
- `"update_scene"` — update properties of existing stimuli (`params` dict of `{stim_id: {property: value}}`)

**Timing events:** On each `win.flip()` that follows a `"show"` command, a `stimulus_onset` event is published on the `b"event"` topic via the dedicated `display_event_address` PUB socket. This provides sub-frame onset timestamps for trial event logging.

**Unit system:** All spatial values throughout the system — including display command parameters and `DisplayConfig` fields — use meters (SI). The PsychoPy window uses `units="cm"` internally. The `DisplayProcess` is the sole conversion boundary: it applies `display_scale` (a dimensionless workspace multiplier, default 1.0) and a fixed ×100 meters→cm factor to all spatial parameters before rendering. `display_offset` is in meters and specifies the display origin shift for co-location calibration. This conversion applies to haptic state cursor positions, field-state body positions, and all spatial parameters in `"show"` / `"update_scene"` display commands (`position`, `radius`, `width`, `height`, `start`, `end`, `vertices`). Non-spatial parameters (`color`, `opacity`, `orientation`, `line_width`) pass through unchanged. See ADR-011.

**Photodiode patch:** A corner patch toggles black/white on stimulus onset (`"show"` commands) for hardware timing verification. Drawn last, on top of all stimuli. Configured via `DisplayConfig.photodiode_enabled` and `photodiode_corner`.

**Field-state rendering:** The frame loop dispatches to field-specific renderers based on `active_field` in the haptic state, but only updates positions of stimuli that already exist. Tasks are responsible for creating and removing field-state visuals via `display.show_cart_pendulum()` / `display.hide_cart_pendulum()` or `display.show_physics_bodies()` / `display.hide_physics_bodies()`. This decouples visual lifecycle from the haptic field — a task can show the pendulum visuals during a preview state while the field is still `null`, or hide visuals during a "blind" condition without changing the haptic field.

- **`cart_pendulum`:** Updates cup (`__cup`), ball (`__ball`), and string (`__string`) positions. Ball color changes to red when `spilled=True`. All positions from `field_state` are converted via `_effective_scale()` (= `display_scale × _METERS_TO_CM`) and `_effective_offset_cm()`.
- **`physics_world`:** Updates positions and angles of `__body_<id>` stimuli. The task controller creates the visual appearance during state entry callbacks; the renderer only updates positions (scaled) and orientations (radians→degrees).
- Other field types (null, spring_damper, etc.): no continuous visual updates — the task controller manages discrete stimuli via show/hide commands.

Reserved stimulus IDs (prefixed `__`): `__cup`, `__ball`, `__string` (cart_pendulum); `__body_<id>` (physics_world).

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

Offline alignment extracts sync edges from each system (emitted by the Teensy sync hub per ADR-013) and builds pairwise linear time mappings, achieving < 0.1 ms cross-system accuracy. Behavioral event codes (ADR-014) provide additional time anchors.

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
│   ├── rig2.yaml          # real Rig 2 hardware (delta.3, two monitors)
│   ├── ci.yaml            # mock-everything for automated testing
│   └── dev-mouse.yaml     # mouse-driven haptic for laptop development
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
    rig="configs/rig/rig2.yaml",
    subject="configs/subject/example_subject.yaml",
    task="configs/task/center_out.yaml",
    overrides={"experiment_name": "center_out_2026_03_25"},
)
```

`load_session_config()` requires rig, subject, and task paths as **keyword-only** arguments — omitting one raises `TypeError` before any config loading happens. This prevents silently running with default rig values when a layer file is forgotten. Additional YAML files can be passed as `extra=[...]`. The required `experiment_name` field can be provided via an extra YAML or `overrides={"experiment_name": ...}`.

**Flexible loading** (for tests and scripting):

```python
config = load_config(
    "configs/rig/rig2.yaml",
    "configs/subject/example_subject.yaml",
    "configs/task/center_out.yaml",
    "configs/example_experiment.yaml",
)
```

A single flat YAML still works for simple setups: `load_config("configs/my_experiment.yaml")`.

**CLI usage**:

```bash
# CLI usage
hapticore run \
    --rig configs/rig/rig2.yaml \
    --subject configs/subject/example_subject.yaml \
    --task configs/task/center_out.yaml \
    --experiment-name "my_session_2026_03_25"
```

See ADR-009 for the rationale behind this design.
