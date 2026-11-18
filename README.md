# Hapticore

Hapticore is an experimental control system for primate neurophysiology. It runs behavioral tasks on a Force Dimension delta.3 haptic robot and keeps the robot, the visual display, and the neural recordings aligned on one hardware timebase.

- **Haptics:** a C++ server drives the delta.3 at 4 kHz. Python sets force-field parameters (springs, channels, virtual dynamics, Box2D physics worlds); it never computes forces itself.
- **Task control:** Python state-machine tasks, a PsychoPy display, audio cues, and a Qt Control Center GUI.
- **Recording and sync:** Ripple Grapevine recording control, behavioral data logging, and a Teensy 4.1 sync hub that sends sync pulses and 8-bit event codes to the recording systems.

Neuropixels recording through SpikeGLX, stimulation control, and a separate camera-acquisition system are planned. See [docs/architecture.md](docs/architecture.md) for the full design.

Hapticore is under active development, and its interfaces may change without notice.

## Requirements

- **Operating system:** Linux (x86-64) on the rig. macOS on Apple silicon works for development, with mock hardware or a Novint Falcon.
- **[pixi](https://pixi.sh):** manages the Python 3.11 environment and the build tools.
- **System libzmq:** needed to build the C++ haptic server (`sudo apt install libzmq3-dev` or `brew install zeromq`). See [cpp/haptic_server/BUILDING.md](cpp/haptic_server/BUILDING.md).
- **Real hardware only:** the Force Dimension SDK (for the delta.3 or Falcon) and Ripple's xipppy (for Grapevine). Neither is distributed with Hapticore; [docs/rig-setup.md](docs/rig-setup.md) explains how to install them.

## Quickstart (no hardware)

```bash
git clone https://github.com/raeedcho/hapticore.git
cd hapticore
pixi install
pixi run install-psychopy   # once; PsychoPy is installed separately from the lockfile
pixi run test               # unit and integration tests
```

Run a short center-out session with every device mocked:

```bash
pixi run hapticore run \
    --rig configs/rig/ci.yaml \
    --subject configs/subject/example_subject.yaml \
    --experiment configs/experiments/center_out.yaml \
    --fast
```

To drive a task with your mouse in a real display window, build the mock haptic server first. This session writes its data under `data/`.

```bash
pixi run mock-cpp-build
pixi run hapticore run \
    --rig configs/rig/dev-mouse.yaml \
    --subject configs/subject/example_subject.yaml \
    --experiment configs/experiments/center_out.yaml
```

Two other commands are available:
- `pixi run hapticore gui` opens the Control Center.
- `pixi run hapticore graph hapticore.tasks.center_out.CenterOutTask` draws a task's state machine.

## Documentation

| Document | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design |
| [docs/rig-setup.md](docs/rig-setup.md) | Setting up a rig machine |
| [docs/teensy-bringup.md](docs/teensy-bringup.md) | Wiring and testing the sync hub |
| [docs/task_authoring_guide.md](docs/task_authoring_guide.md) | Writing a new task |
| [docs/haptic_server_protocol.md](docs/haptic_server_protocol.md) | The C++ ↔ Python protocol |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [cpp/haptic_server/BUILDING.md](cpp/haptic_server/BUILDING.md) | Building the haptic server |
| [AGENTS.md](AGENTS.md) | Working conventions, for contributors and coding agents alike |

## Repository layout

| Path | Contents |
|---|---|
| `python/hapticore/` | Python package: tasks, display, sync, recording, session management |
| `cpp/haptic_server/` | C++ haptic server |
| `firmware/teensy/` | Teensy 4.1 sync-hub firmware (PlatformIO) |
| `configs/` | Layered YAML configs: rig, subject, experiment |
| `tests/` | Python test suite |
| `docs/` | Documentation and ADRs |
