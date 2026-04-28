# Hapticore — Agent Instructions

## What this project is

Hapticore is a multi-process experimental control system for primate neurophysiology experiments coordinating a Force Dimension delta.3 haptic robot, Ripple Grapevine neural recording/stimulation, Neuropixels/SpikeGLX recording, and visual stimulus display. The system runs behavioral tasks where monkeys interact with virtual haptic environments while neural activity is recorded.

## Architecture (three tiers — read `docs/architecture.md` for full detail)

- **Tier 1 (C++, hard real-time):** Haptic server runs at 4 kHz using Force Dimension DHD SDK. Evaluates parameterized force fields, publishes state via ZeroMQ PUB, accepts commands via ZeroMQ ROUTER. Python never sends raw forces — it sets field parameters. Box2D v3.0 provides 2D collision detection and rigid-body dynamics for tasks with physical interactions. A beam-break sensor on an FTDI FT232H provides a separate safety-critical GPIO read path directly into the C++ server — see docs/rig-setup.md § FTDI FT232H.
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
│   ├── haptic/         # haptic interface implementations and factory (HapticClient, MockHapticInterface, MouseHapticInterface, make_haptic_interface)
│   ├── display/        # PsychoPy display process, client, mock, and factory (make_display_interface)
│   ├── recording/      # neural recording implementations (MockNeuralRecording; real implementations land in Phase 5B)
│   ├── sync/           # Teensy serial interface (SyncProcess, TeensySync, MockSync)
│   └── cli/            # command-line entry points
├── cpp/haptic_server/  # C++ haptic server (CMake project)
│   ├── src/            # source files (main, threads, force fields, DHD interface)
│   ├── tests/          # Google Test unit and integration tests
│   └── CMakeLists.txt
├── firmware/teensy/    # Arduino/Teensy firmware
├── configs/            # YAML experiment configuration templates
├── tests/              # Python tests: unit/, integration/, hardware/ subdirectories
├── docs/               # architecture.md, task_authoring_guide.md, haptic_server_protocol.md, ADRs in docs/adr/
└── pyproject.toml
```

## Coordinate convention (see ADR-008)

All positions, velocities, and forces use the **lab frame**: X = horizontal (+ right), Y = vertical (+ up), Z = depth (+ toward operator). This differs from the DHD SDK's native frame (X=depth, Y=horizontal, Z=vertical). The remap is applied inside `DhdReal` at the lowest layer, so all force fields, protocol messages, and Python clients see lab-frame values. `DhdMock` does not remap. Do not introduce DHD-frame assumptions anywhere above `DhdReal`.

## Key conventions

- Every hardware interaction goes through a Protocol (ABC) interface defined in `core/interfaces.py`. Real implementations and mock implementations both satisfy the same Protocol. This is how we test without hardware.
- Message topics are byte-string prefixes on ZeroMQ multipart messages: `b"state"`, `b"event"`, `b"display"`, `b"trial"`.
- All Python timestamps use `time.monotonic()` within a session. C++ uses `clock_gettime(CLOCK_MONOTONIC)`. Cross-system sync uses hardware TTL pulses, not software timestamps.
- Pydantic models use `Field()` with constraints (gt, lt, ge, le) for all numeric parameters. Invalid configs must fail at load time, not during an experiment.
- Tasks declare their state machine as class-level `STATES` and `TRANSITIONS` lists in `transitions` library format.
- Force fields are parameterized C++ classes. Python sets parameters via commands; C++ evaluates forces at 4 kHz. Never compute forces in Python.
- For tasks with collisions or rigid body dynamics, use the `PhysicsField` (Box2D wrapper) configured declaratively from Python. Do not write custom collision code per task. See `docs/task_authoring_guide.md` § "Approach B: Physics world".
- The interface contract between C++ and Python is defined in `docs/haptic_server_protocol.md`. Both sides must conform to this spec.
- All spatial values (positions, radii, widths, distances) use **meters (SI)** throughout task code, config files, and inter-process messages. `display_scale` is a dimensionless workspace multiplier (default 1.0); the fixed meters→cm conversion for PsychoPy is handled internally by the display process. `display_offset` is in meters. Never pass pre-converted cm values to `show_stimulus()` or `update_scene()`. See ADR-011.

## Build and test commands

```bash
# Python (primary workflow — uses pixi)
pixi install                         # install all dependencies + editable package
pixi run install-psychopy # install PsychoPy (run once after pixi install)
pixi run test-unit                   # run Python unit tests
pixi run test-integration            # run Python integration tests
pixi run lint                        # ruff check
pixi run typecheck                   # mypy strict
pixi run cpp                         # configure + build + test C++ (mock)
pixi run test-hardware               # hardware tests (requires running server)

# pip install -e ".[dev]" still works for Python-only development
# but won't provide cmake or ninja.

# C++ haptic server (via pixi task or manual CMake)
pixi run cpp                         # full mock build + test via pixi tasks
# Or run CMake manually inside a pixi shell:
pixi shell
cd cpp/haptic_server
cmake --preset dev-mock
cmake --build --preset dev-mock
ctest --preset dev-mock
```

After any change to `pixi.toml` or `pyproject.toml`, always run `pixi install` and commit the updated `pixi.lock` alongside it. CI will fail if the lockfile is out of sync.

### Validating display changes

CI uses `setup-pixi` and installs system deps (`xvfb`, `libsdl2-2.0-0`, `libglu1-mesa`) via apt before running display tests. On macOS, display tests run locally but require the event loop pump (`tests/display/conftest.py`) to avoid window stalls.

When modifying display code (`python/hapticore/display/` or `tests/display/`), validate with:

```bash
xvfb-run -a -s "-screen 0 1920x1080x24" pixi run test-display
pixi run test-unit
```

## Teensy firmware (`firmware/teensy/`)
 
The Teensy 4.1 sync hub firmware lives in `firmware/teensy/`. It generates hardware-timed TTL signals for camera frame triggering, cross-system sync, behavioral event codes, and reward delivery. The firmware accepts ASCII serial commands from the Python `SyncProcess`.
 
### Build
 
The firmware builds with PlatformIO or Arduino IDE + Teensyduino. The CI job does not flash hardware but does compile-check the firmware:
 
```bash
cd firmware/teensy
pio run  # compile only
```

Note: Do not add `-Wpedantic` to firmware/teensy/platformio.ini's build_flags. The Teensy core framework uses anonymous structs and other GCC extensions that don't pass ISO C++ pedantic checks, and PlatformIO applies build_flags globally, including to vendor framework code.
 
### Key constraints
 
- **3.3V output logic.** All GPIO outputs are 3.3V. Do not assume 5V TTL compatibility with downstream devices.
- **IntervalTimers for timing-critical signals.** The camera trigger and 1 Hz sync must use Teensy's PIT-based IntervalTimers (`IntervalTimer` class), not `delay()` or `millis()` loops. This ensures jitter-free pulse generation independent of serial command processing.
- **Serial command parsing must be non-blocking.** The main loop checks `Serial.available()` and processes commands without blocking timer ISRs. Never use `Serial.readString()` or other blocking reads.
- **Event code timing.** The event strobe sequence (set data lines → 500 µs settle → 1 ms strobe → 500 µs clear) totals ~2 ms. This is handled in the main loop, not in an ISR.
- **Pin assignments are defined in a single header** (`pins.h` or equivalent). Do not scatter magic pin numbers through the code.

## Common pitfalls an agent should avoid

### Python pitfalls
- Do not import PsychoPy in any process except the display process. PsychoPy creates an OpenGL context on import and must own the main thread.
- Do not use `time.sleep()` for timing in the task controller. Use `time.monotonic()` polling or the TimerManager. (Exception: the main loop's rate-limiting sleep at the end of each tick is fine — it yields CPU, not delays task logic.)
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
- **Virtual mass rendering:** Never compute `F = M * a_estimated` and apply it directly to the device. Discrete acceleration estimation via finite differences amplifies position sensor noise by roughly `1/dt^2` in the acceleration estimate (and thus by `M/dt^2` in force), causing instability for virtual masses above roughly 2× the device's physical mass. Use a virtual coupling approach instead: simulate the mass in software, connect to the device through a spring-damper coupler. The device only feels the coupler; the mass lives in the simulation. See the `CartPendulumField` implementation for the canonical pattern and ADR-010 for rationale.
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

### Hardware & interactive test conventions

- Hardware tests use `@pytest.mark.hardware`. Interactive feel-tests (where the operator physically evaluates force feedback) use both `@pytest.mark.hardware` and `@pytest.mark.interactive`. Both markers are registered in `pyproject.toml`.
- **All interactive feel-tests live in `tests/hardware/test_interactive_fields.py`**, organized as one test class per field type. When adding a new force field, add its feel-test class to this file — do not create a separate module.
- **Always use `run_timed_evaluation()`** for interactive feel-tests. This handles the heartbeat keeper, countdown/duration timing, TTY detection, NullField revert, and operator confirmation in a consistent flow. Do not write raw `input()` calls or one-shot heartbeats.
- **Never send a single heartbeat before a blocking `input()` call.** The server reverts to NullField after 500 ms without a heartbeat. Use the `HapticClient` (in `hapticore/haptic/client.py`) to keep forces alive.
- Interactive tests use the function-scoped `dealer` fixture (which reverts to NullField in its teardown), NOT the module-scoped `cmd_dealer` (which is for automated tests).  They also take `cmd_address`, `zmq_context`, `countdown`, and `duration` as fixtures.
- Do not add standalone `test_cleanup_revert_to_null` functions. The `dealer` fixture handles cleanup automatically.
- All shared ZMQ fixtures and helpers live in `tests/hardware/conftest.py`. The heartbeat keeper lives in `tests/hardware/heartbeat_keeper.py`. Check these before writing new infrastructure.

## When working on tasks (python/hapticore/tasks/)

Read `docs/task_authoring_guide.md` first. Every task subclasses `BaseTask`, declares PARAMS, STATES, TRANSITIONS, and implements `on_enter_<state>` / `on_exit_<state>` callbacks. The `transitions` library wires these automatically. See `tasks/template_task.py` for the pattern.

## When working on the C++ haptic server (cpp/haptic_server/)

Read `docs/architecture.md` § "Tier 1: C++ haptic server" and `docs/haptic_server_protocol.md` before writing code. The protocol doc defines every published state field, every command, and every parameter schema. Both the C++ server and the Python mock must implement it faithfully.

The haptic thread must never block on I/O. Use a triple buffer for lock-free state sharing between the haptic thread and publisher thread. Force fields inherit from `ForceField` base class and implement `compute(pos, vel, dt) -> force_vector`. The `PhysicsField` wraps Box2D and handles collision-based tasks. The server links against the Force Dimension SDK (pointed to by `FD_SDK_DIR` env var) and Box2D (pulled via CPM.cmake).

The server has three threads: haptic (SCHED_FIFO priority 80, 4 kHz), publisher (normal priority, configurable rate), and command (normal priority, polls ROUTER socket). See `docs/architecture.md` for the threading diagram.

## When working on configuration (python/hapticore/core/config.py)

All nested config models use Pydantic v2 `BaseModel`. The top-level `ExperimentConfig` is a `pydantic-settings` `BaseSettings` subclass supporting layered YAML files, environment variables, and CLI arguments.

Load configs with `load_config(*yaml_paths)` which deep-merges multiple YAML files (later files win):

```python
config = load_config(
    "configs/rig/default.yaml",
    "configs/subject/monkey_a.yaml",
    "configs/task/center_out.yaml",
)
```

Environment variables use `HAPTICORE_` prefix and `__` (double underscore) delimiter for nesting: `HAPTICORE_HAPTIC__FORCE_LIMIT_N=15.0`. Single underscores within field names (e.g., `force_limit_n`) are not delimiters.

## ADRs (architecture decision records)

Before proposing alternatives to a settled decision, check `docs/adr/` for context:
- `001`: ZeroMQ + msgpack over rpclib/gRPC/raw UDP
- `002`: Parameterized force fields (Python sends params, C++ evaluates at 4 kHz)
- `003`: Python over MonkeyLogic
- `004`: DHD SDK directly, not CHAI3D
- `005`: No Bonsai
- `006`: Monorepo
- `007`: Box2D for 2D physics
- `008`: Lab coordinate convention (DHD SDK remap in `DhdReal`)
- `009`: pydantic-settings with layered YAML composition
- `010`: Virtual coupling for stable mass rendering on impedance-type device
- `011`: SI units (meters) for all spatial values in code and configs--display process handles conversion to cm for PsychoPy
- `012`: Separate repository for video capture of behavior
- `013`: Teensy 4.1 as centralized sync hub (supersedes xipppy-DIO sync plan)
- `014`: 8-bit parallel event codes via the Scout D-sub port
- `015`: Config-driven backend selection via factory functions
