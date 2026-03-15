# Hapticore — Agent Instructions

## What this project is

Hapticore is a multi-process experimental control system for primate neurophysiology experiments coordinating a Force Dimensions delta.3 haptic robot, Ripple Grapevine neural recording/stimulation, Neuropixels/SpikeGLX recording, and visual stimulus display. The system runs behavioral tasks where monkeys interact with virtual haptic environments while neural activity is recorded.

## Architecture (three tiers — read `docs/architecture.md` for full detail)

- **Tier 1 (C++, hard real-time):** Haptic server runs at 4 kHz using Force Dimension DHD SDK. Evaluates parameterized force fields, publishes state via ZeroMQ PUB, accepts commands via ZeroMQ ROUTER. Python never sends raw forces — it sets field parameters. Box2D v3.0 provides 2D collision detection and rigid-body dynamics for tasks with physical interactions.
- **Tier 2 (Python, soft real-time):** Task controller uses `transitions` state machine library. PsychoPy renders visual stimuli in a separate process. Each hardware interface runs in its own process. ZeroMQ PUB-SUB distributes events between all processes, with msgpack serialization.
- **Tier 3 (Python, recording/analysis):** Wrappers around SpikeGLX Python SDK, Ripple xipppy, and LSL/pylsl. Teensy generates hardware sync pulses and event codes.

## Tech stack

- Python 3.11+ with type hints everywhere. Use `from __future__ import annotations`.
- C++17 for the haptic server. CMake 3.16+ build system with CPM.cmake for dependencies.
- ZeroMQ (`pyzmq` in Python, `cppzmq` in C++) for all inter-process communication.
- msgpack (`msgpack-python`, `msgpack-cxx`) for serialization — not JSON, not protobuf.
- Box2D v3.0 (C library, via CPM.cmake) for 2D physics/collision in the haptic server.
- Pydantic v2 for configuration validation. Configs loaded from YAML files.
- `transitions` library for behavioral state machines.
- PsychoPy for visual stimulus rendering (always in its own process, OpenGL in main thread).
- `pytest` for testing. Hardware tests marked with `@pytest.mark.hardware`.

## Project structure

```
hapticore/
├── python/hapticore/
│   ├── core/           # messaging (EventBus, CommandClient/Server), config (Pydantic models), interfaces (Protocol ABCs)
│   ├── tasks/          # behavioral task implementations (subclass BaseTask)
│   ├── hardware/       # real hardware interface implementations + mock implementations
│   ├── display/        # PsychoPy display process
│   ├── recording/      # Ripple, SpikeGLX, LSL wrappers
│   ├── sync/           # Teensy serial interface
│   └── cli/            # command-line entry points
├── cpp/haptic_server/  # C++ haptic server (CMake project)
├── firmware/teensy_sync/  # Arduino/Teensy firmware
├── configs/            # YAML experiment configuration templates
├── tests/              # unit/, integration/, hardware/ subdirectories
├── docs/               # architecture.md, task_authoring_guide.md, ADRs in docs/adr/
└── pyproject.toml
```

## Key conventions

- Every hardware interaction goes through a Protocol (ABC) interface defined in `core/interfaces.py`. Real implementations and mock implementations both satisfy the same Protocol. This is how we test without hardware.
- Message topics are string prefixes on ZeroMQ multipart messages: `b"state"`, `b"event"`, `b"display"`, `b"command"`.
- All timestamps use `time.monotonic()` within a session. Cross-system sync uses hardware TTL pulses, not software timestamps.
- Pydantic models use `Field()` with constraints (gt, lt, ge, le) for all numeric parameters. Invalid configs must fail at load time, not during an experiment.
- Tasks declare their state machine as class-level `STATES` and `TRANSITIONS` lists in `transitions` library format.
- Force fields are parameterized C++ classes. Python sets parameters via commands; C++ evaluates forces at 4 kHz. Never compute forces in Python.
- For tasks with collisions or rigid body dynamics, use the `PhysicsField` (Box2D wrapper) configured declaratively from Python. Do not write custom collision code per task. See `docs/task_authoring_guide.md` § "Approach B: Physics world".

## Build and test commands

```bash
# Python
pip install -e ".[dev]"          # install package with dev dependencies
pytest tests/unit/               # run unit tests (~30 seconds)
pytest tests/integration/        # run integration tests with mocks (~2 minutes)
pytest tests/hardware/ -m hardware  # run hardware tests (requires physical devices)
ruff check python/               # lint
mypy python/hapticore/core/      # type check core module (strict mode)

# C++ haptic server
cd cpp/haptic_server && mkdir -p build && cd build
cmake .. -DMOCK_HARDWARE=ON      # use mock DHD for testing without robot
cmake --build . --parallel
ctest                            # run C++ tests
```

## Common pitfalls an agent should avoid

- Do not import PsychoPy in any process except the display process. PsychoPy creates an OpenGL context on import and must own the main thread.
- Do not use `time.sleep()` for timing in the task controller. Use `time.monotonic()` polling or the TimerManager.
- Do not use `json` for message serialization — use `msgpack`. JSON is too slow for 1 kHz messaging.
- Do not use `threading` for parallelism in Python. Use `multiprocessing` or separate processes communicating via ZeroMQ. The GIL prevents true parallelism in threads.
- Do not use `zmq.REQ`/`zmq.REP` sockets — use `zmq.DEALER`/`zmq.ROUTER` for non-blocking command/response. REQ/REP deadlocks if a message is lost.
- Do not put raw numpy arrays into msgpack. Convert to lists first, or use msgpack's `default`/`object_hook` with a registered ext type for ndarray.
- ZeroMQ PUB-SUB has a slow-joiner problem: the subscriber may miss early messages. Always handle this gracefully (e.g., wait for first state message before starting a trial).
- Pydantic v2 uses `model_dump()` not `.dict()`, and `model_validate()` not `.parse_obj()`.
- Do not write per-task collision detection code in C++. Use the `PhysicsField` with Box2D, configured from Python. Only write a new C++ ForceField subclass if you need a fundamentally new analytical force computation.

## When working on tasks (python/hapticore/tasks/)

Read `docs/task_authoring_guide.md` first. Every task subclasses `BaseTask`, declares PARAMS, STATES, TRANSITIONS, and implements `on_enter_<state>` / `on_exit_<state>` callbacks. The `transitions` library wires these automatically. See `tasks/template_task.py` for the pattern.

## When working on the C++ haptic server (cpp/haptic_server/)

Read `docs/architecture.md` § "Tier 1: C++ haptic server" for the threading model. The haptic thread must never block on I/O. Use a triple buffer for lock-free state sharing between the haptic thread and publisher thread. Force fields inherit from `ForceField` base class and implement `compute(pos, vel, dt) -> force_vector`. The `PhysicsField` wraps Box2D and handles collision-based tasks. The server links against the Force Dimension SDK (pointed to by `FD_SDK_DIR` env var) and Box2D (pulled via CPM.cmake).

## When working on configuration (python/hapticore/core/config.py)

All config models use Pydantic v2 BaseModel. The top-level `ExperimentConfig` composes SubjectConfig, HapticConfig, DisplayConfig, RecordingConfig, TaskConfig, SyncConfig, and ZMQConfig. Load from YAML with `yaml.safe_load()` then validate with `ExperimentConfig.model_validate()`.
