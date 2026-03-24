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
- `pytest` for testing Python. Google Test for testing C++. Hardware tests marked with `@pytest.mark.hardware`.

## Project structure

```
hapticore/
├── python/hapticore/
│   ├── core/           # messaging (EventBus, CommandClient/Server), config (Pydantic models), interfaces (Protocol ABCs), messages (dataclass schemas + msgpack serialization)
│   ├── tasks/          # behavioral task implementations (subclass BaseTask)
│   ├── hardware/       # real hardware interface implementations + mock implementations
│   ├── display/        # PsychoPy display process
│   ├── recording/      # Ripple, SpikeGLX, LSL wrappers
│   ├── sync/           # Teensy serial interface
│   └── cli/            # command-line entry points
├── cpp/haptic_server/  # C++ haptic server (CMake project)
│   ├── src/            # source files (main, threads, force fields, DHD interface)
│   ├── tests/          # Google Test unit and integration tests
│   └── CMakeLists.txt
├── firmware/teensy_sync/  # Arduino/Teensy firmware
├── configs/            # YAML experiment configuration templates
├── tests/              # Python tests: unit/, integration/, hardware/ subdirectories
├── docs/               # architecture.md, task_authoring_guide.md, haptic_server_protocol.md, ADRs in docs/adr/
└── pyproject.toml
```

## Key conventions

- Every hardware interaction goes through a Protocol (ABC) interface defined in `core/interfaces.py`. Real implementations and mock implementations both satisfy the same Protocol. This is how we test without hardware.
- Message topics are byte-string prefixes on ZeroMQ multipart messages: `b"state"`, `b"event"`, `b"display"`, `b"trial"`.
- All Python timestamps use `time.monotonic()` within a session. C++ uses `clock_gettime(CLOCK_MONOTONIC)`. Cross-system sync uses hardware TTL pulses, not software timestamps.
- Pydantic models use `Field()` with constraints (gt, lt, ge, le) for all numeric parameters. Invalid configs must fail at load time, not during an experiment.
- Tasks declare their state machine as class-level `STATES` and `TRANSITIONS` lists in `transitions` library format.
- Force fields are parameterized C++ classes. Python sets parameters via commands; C++ evaluates forces at 4 kHz. Never compute forces in Python.
- For tasks with collisions or rigid body dynamics, use the `PhysicsField` (Box2D wrapper) configured declaratively from Python. Do not write custom collision code per task. See `docs/task_authoring_guide.md` § "Approach B: Physics world".
- The interface contract between C++ and Python is defined in `docs/haptic_server_protocol.md`. Both sides must conform to this spec.

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
cmake -S cpp/haptic_server -B build -DMOCK_HARDWARE=ON   # configure with mock DHD
cmake --build build --parallel                            # build
cd build && ctest --output-on-failure                     # run C++ tests
# Or use the convenience script:
./cpp/haptic_server/build.sh
```

## Common pitfalls an agent should avoid

### Python pitfalls
- Do not import PsychoPy in any process except the display process. PsychoPy creates an OpenGL context on import and must own the main thread.
- Do not use `time.sleep()` for timing in the task controller. Use `time.monotonic()` polling or the TimerManager.
- Do not use `json` for message serialization — use `msgpack`. JSON is too slow for 1 kHz messaging.
- Do not use `threading` for parallelism in Python. Use `multiprocessing` or separate processes communicating via ZeroMQ. The GIL prevents true parallelism in threads.
- Do not use `zmq.REQ`/`zmq.REP` sockets — use `zmq.DEALER`/`zmq.ROUTER` for non-blocking command/response. REQ/REP deadlocks if a message is lost.
- Do not put raw numpy arrays into msgpack. Convert to lists first, or use msgpack's `default`/`object_hook` with a registered ext type for ndarray.
- ZeroMQ PUB-SUB has a slow-joiner problem: the subscriber may miss early messages. Always handle this gracefully (e.g., wait for first state message before starting a trial).
- Pydantic v2 uses `model_dump()` not `.dict()`, and `model_validate()` not `.parse_obj()`.
- Do not write per-task collision detection code in C++. Use the `PhysicsField` with Box2D, configured from Python. Only write a new C++ ForceField subclass if you need a fundamentally new analytical force computation.

### C++ pitfalls
- Do NOT allocate heap memory in the haptic thread loop. Pre-allocate all buffers at startup. `new`, `malloc`, `std::vector::push_back`, `std::string` construction — none of these belong in the 4 kHz loop.
- Do NOT use `std::mutex` or any blocking synchronization in the haptic thread. Use the lock-free triple buffer for state sharing and `std::atomic` for field pointer swaps.
- Do NOT use `std::cout` or `printf` in the haptic loop. Logging I/O causes unpredictable latency. Log to a ring buffer and flush from a non-RT thread.
- Use `MSGPACK_DEFINE_MAP` (named keys), not `MSGPACK_DEFINE_ARRAY` (positional). Python deserializes as a dict and constructs dataclasses by keyword. Positional encoding breaks if either side adds a field.
- Compile with `-Wall -Wextra -Wpedantic -Werror` in all builds. Fix all warnings. Make sure to build in both a Linux and macOS environment to catch platform-specific warnings.
- Keep `ForceField::compute()` under 50 µs. Profile with a simple `clock_gettime` diff if in doubt. The full tick budget is 250 µs and includes DHD USB round-trip.
- Spring stiffness above 3000 N/m causes instability at 4 kHz. Reject such values in `update_params()`.
- **Wall-clock timing assertions**: Guard with `#ifdef __linux__` or split into separate tests. macOS GitHub Actions runners (virtualized Apple Silicon) have unreliable sleep granularity — `sleep_for(5ms)` can sleep 50ms+. This mirrors the Python-side lesson learned from timer coalescing. Timing-sensitive tests retain full value on Linux (the deployment target) while macOS CI still validates correctness without contributing flaky failures.

### Force Dimension SDK pitfalls
- The Force Dimension SDK is proprietary and not in version control. It is located via the `FD_SDK_DIR` environment variable. When `MOCK_HARDWARE=ON`, the mock DHD replaces all SDK calls.
- Headers are named `dhdc.h` and `drdc.h` — NOT `dhd.h`/`drd.h`. The function names (e.g., `dhdGetPosition`) do NOT have the `c` suffix — only the header filenames do.
- Libraries are named `libdhd` and `libdrd` — NOT `libdhdc`/`libdrdc`. The header naming convention and library naming convention do not match.
- Libraries are NOT in `$FD_SDK_DIR/lib/`. They are in `$FD_SDK_DIR/lib/release/lin-<arch>-gcc/` where `<arch>` is the machine architecture (e.g., `x86_64`). CMakeLists.txt uses `CMAKE_SYSTEM_PROCESSOR` to resolve this automatically, matching the SDK's own Makefile convention (see `Makefile.common` in the SDK root).
- `libdhd.a` statically depends on `libusb-1.0`. Since static libraries don't carry transitive dependencies, `usb-1.0` must be linked explicitly in `target_link_libraries` alongside `dhd` and `drd`.
- **`DhdReal` uses the DRD library for open/close/calibrate.** The device is opened with `drdOpen()` (not `dhdOpen()`) and closed with `drdClose()`. The include is `<drdc.h>` (which transitively includes `<dhdc.h>`). All DHD functions (`dhdGetPosition`, `dhdSetForce`, etc.) work normally after a DRD open — the DRD library is built on top of DHD and shares the device handle. DRD is used only for startup calibration (`drdAutoInit`, `drdIsInitialized`, `drdStop`); the haptic loop itself uses only DHD calls.
- **Gravity compensation is computed host-side, not on the controller.** The SDK computes gravity compensation torques inside `dhdSetForce()` based on the current joint configuration, and adds them to the requested force before sending the combined command to the controller over USB. The controller receives raw motor commands and has no knowledge of gravity. This means: (1) gravity comp only works when force commands are being actively sent, (2) `dhdEnableForce(DHD_ON)` must be called to initialize the force rendering pipeline including gravity comp, and (3) the force the haptic thread computes is *not* the force the device applies — the SDK adds gravity comp on top.
- **`dhdEnableForce(DHD_ON)` must be called before any `dhdSetForce()` calls.** Without it, the SDK's force rendering pipeline (including gravity compensation) is not initialized. The physical "FORCE" button on the DHC and `dhdEnableForce()` serve different roles: the button enables the hardware amplifiers, while the API call initializes the SDK's host-side processing. Both are needed.
- **Always check `dhdSetForce()` return values.** If the SDK considers force rendering uninitialized, `dhdSetForce()` may silently fail or return an error. At minimum, log the first error to aid hardware bringup debugging.
- **The delta.3 requires calibration once per power-on.** The server auto-calibrates via `drdAutoInit()` at startup (skipped if `drdIsInitialized()` returns true). The `--no-calibrate` flag suppresses this. If the device is uncalibrated, `dhdGetPosition()` returns inaccurate values and gravity compensation forces are wrong.

## When working on tasks (python/hapticore/tasks/)

Read `docs/task_authoring_guide.md` first. Every task subclasses `BaseTask`, declares PARAMS, STATES, TRANSITIONS, and implements `on_enter_<state>` / `on_exit_<state>` callbacks. The `transitions` library wires these automatically. See `tasks/template_task.py` for the pattern.

## When working on the C++ haptic server (cpp/haptic_server/)

Read `docs/architecture.md` § "Tier 1: C++ haptic server" and `docs/haptic_server_protocol.md` before writing code. The protocol doc defines every published state field, every command, and every parameter schema. Both the C++ server and the Python mock must implement it faithfully.

The haptic thread must never block on I/O. Use a triple buffer for lock-free state sharing between the haptic thread and publisher thread. Force fields inherit from `ForceField` base class and implement `compute(pos, vel, dt) -> force_vector`. The `PhysicsField` wraps Box2D and handles collision-based tasks. The server links against the Force Dimension SDK (pointed to by `FD_SDK_DIR` env var) and Box2D (pulled via CPM.cmake).

The server has three threads: haptic (SCHED_FIFO priority 80, 4 kHz), publisher (normal priority, configurable rate), and command (normal priority, polls ROUTER socket). See `docs/architecture.md` for the threading diagram.

## When working on configuration (python/hapticore/core/config.py)

All config models use Pydantic v2 BaseModel. The top-level `ExperimentConfig` composes SubjectConfig, HapticConfig, DisplayConfig, RecordingConfig, TaskConfig, SyncConfig, and ZMQConfig. Load from YAML with `yaml.safe_load()` then validate with `ExperimentConfig.model_validate()`.

## ADRs (architecture decision records)

Before proposing alternatives to a settled decision, check `docs/adr/` for context:
- `001`: ZeroMQ + msgpack over rpclib/gRPC/raw UDP
- `002`: Parameterized force fields (Python sends params, C++ evaluates at 4 kHz)
- `003`: Python over MonkeyLogic
- `004`: DHD SDK directly, not CHAI3D
- `005`: No Bonsai
- `006`: Monorepo
- `007`: Box2D for 2D physics
