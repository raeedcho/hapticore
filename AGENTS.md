# Hapticore agent instructions

Hapticore controls primate neurophysiology experiments. A C++ server drives a Force Dimension delta.3 at 4 kHz; Python processes run tasks, display, sync, recording control, and logging over ZeroMQ + msgpack; a Teensy 4.1 sends hardware sync pulses and event codes to the neural recording systems (Ripple Grapevine now, Neuropixels via SpikeGLX planned). Read the code for structure. This file covers what the code can't tell you. When code and docs disagree, trust the code and point out the mismatch in your PR.

## Safety

Unless the human explicitly asks in the current session, never run `pixi run test-hardware` or `pixi run test-interactive`, run `hapticore run` with a non-mock rig config (e.g. `configs/rig/rig2.yaml`), build or start a non-mock haptic server, flash firmware, or trigger reward or stimulation. These can move the robot or drive outputs on a live rig.

## Commands

The environment is already set up: locally by the human, and in Copilot's cloud by `.github/workflows/copilot-setup-steps.yml`. Don't install or reinstall anything unless your task is a dependency change. If a tool or package is missing, say so rather than installing it. Run everything through `pixi run` (Python 3.11).

| Check | Command |
|---|---|
| Lint | `pixi run lint` (ruff on `python/`) |
| Types | `pixi run typecheck` (mypy `--strict`, `python/hapticore/core/` only) |
| Python tests | `pixi run test` (unit + integration); while iterating, `pixi run pytest tests/unit/test_x.py -q` |
| C++ (mock) | `pixi run mock-cpp` (build and test the `dev-mock` preset) |
| Display (Linux) | `QT_QPA_PLATFORM=offscreen xvfb-run -a -s "-screen 0 1920x1080x24" pixi run test-display` |
| Firmware | from `firmware/teensy/`: `pixi run -- pio run -e teensy41`, then `pixi run -- pio test -e native` |
| End-to-end on mocks | `pixi run hapticore run --rig configs/rig/ci.yaml --subject configs/subject/example_subject.yaml --experiment configs/experiments/center_out.yaml --fast` |

- After changing C++ code, rebuild with `pixi run mock-cpp` before running the integration tests. `test_cross_language.py` and `test_haptic_server_lifecycle.py` skip, rather than fail, when no binary exists under `cpp/haptic_server/build/dev-mock/` or `build/ci/`.
- To change dependencies, edit `pixi.toml` or `pyproject.toml`, run `pixi install`, and commit the updated `pixi.lock`. CI fails on a stale lockfile.
- The C++ server links the system libzmq (apt or Homebrew). Don't add zeromq to `pixi.toml`.
- CI runs on Ubuntu and macOS. If you can only build on one, say which platform-sensitive changes you couldn't check.

## Working rules

- When your task comes from a GitHub issue, deliver it as a pull request against main, with Closes #<issue> as the first line of the description. If you can't finish, open a draft PR that says what's left instead of stopping without one.
- Do what the issue asks and nothing more. No unrequested refactors, renames, or reformatting; list suggested follow-ups in the PR description.
- Fix root causes. Don't skip or xfail tests, broaden `except` clauses, or add sleeps to get to green. If you can't fix something, say so.
- Rename in place across all call sites, with no deprecation aliases or compatibility shims.
- Prefer explicit call sites over wrappers that hide coupling.
- Tests must exercise hapticore code, not the stdlib, Pydantic, or arithmetic identities.
- The PR title becomes the squash-merge commit message. Make it imperative, prefixed with the issue's ID if it has one (e.g. `CC-C.3: Recording panel …`). In the description, list the verification commands you ran and their results.
- Settled decisions live in `docs/adr/`. Check there before proposing an alternative, and never deviate silently.

## Architecture invariants

- Python never computes forces; it sets parameters on C++ force fields (ADR-002). Collisions and rigid bodies go through the Box2D `PhysicsField`, configured from Python (ADR-007). Write a new C++ field only for a new analytical force law.
- Never render mass as `F = M·a_estimated`. Simulate the mass and couple it to the device through a spring-damper (virtual coupling; see `CartPendulumField` and ADR-010).
- Use the lab frame everywhere: X right, Y up, Z toward the operator. The DHD-frame remap happens only inside `DhdReal` (ADR-008).
- Spatial values are meters in code, configs, and messages. Only the display process converts to cm (ADR-011).
- `docs/haptic_server_protocol.md` is the C++↔Python contract. A change to commands, state fields, or field parameters must update that doc, both sides, and the cross-language tests in the same PR.
- `core/messages.py` is the authority on message types and topics.
- Hardware is accessed through the Protocols in `core/interfaces.py`, built by `make_<role>_interface()` factories. Single-choice subsystems use a `backend:` discriminator; optional ones are `X | None = None`. Recording is the deliberate exception, because several recording systems can be active at once (ADR-015).
- Invalid configs fail at load time. Constrain numeric fields (`Field(gt=…, le=…)`) or add validators.
- Tabular outputs are TSV, never CSV.

## Python

- Concurrency means separate processes talking over ZeroMQ. Threads are only for small I/O loops, such as `HapticClient`'s heartbeat. Use DEALER/ROUTER sockets, never REQ/REP.
- Serialize messages with msgpack, not JSON. Convert numpy arrays to lists first.
- PUB/SUB drops messages sent before a subscriber connects. Wait for the first message before relying on a subscription.
- PsychoPy opens an OpenGL context on import and must own its process's main thread. Import it lazily, only inside a window-owning process's run loop (`DisplayProcess`, `WorkspaceMirrorProcess`). Everywhere else, use `TYPE_CHECKING` imports.
- Qt dashboard processes use the `spawn` context defined in `dashboard/__init__.py`. Don't change the global multiprocessing start method.
- Use `time.monotonic()` for timestamps. In task and controller code, use `TimerManager` for timing; `time.sleep()` is only for the main loop's rate limiting. Cross-system alignment uses the Teensy's hardware pulses, never software clocks.
- `xipppy` is not redistributable. Never add it as a dependency. It is imported lazily in `recording/xipppy_client.py`, tests use mocks, and the API is summarized in `docs/xipppy_api_reference.md`.
- New tasks subclass `BaseTask`. Start from `tasks/template_task.py` and see `docs/task_authoring_guide.md`. Each task's `PARAMS` is standalone, not inherited.

## Python tests

- Get unique ZMQ addresses from the `ipc_address`, `event_address`, or `command_address` fixtures (or `make_ipc_address()`). Set `LINGER` to 0 and close sockets in `finally`.
- Wait on events or conditions, not fixed sleeps. macOS CI runners coalesce timers, so `time.sleep()` can overshoot by an order of magnitude.
- For headless Qt, assert `not widget.isHidden()`, not `widget.isVisible()`.
- Display tests are marked `display` and are skipped on macOS CI. Locally on macOS, the autouse fixture in `tests/display/conftest.py` pumps the event loop.

## C++ haptic server (`cpp/haptic_server/`)

- C++20, compiled with `-Wall -Wextra -Wpedantic -Werror`. CI builds and tests on Linux and macOS and runs a ThreadSanitizer job.
- The 4 kHz haptic thread (SCHED_FIFO) never allocates, locks, blocks, or prints:
  - no `new`, `malloc`, `push_back`, or `std::string` construction;
  - no `std::mutex`;
  - no I/O.
  Share state through `TripleBuffer` and swap fields with `std::atomic`.
- Keep `ForceField::compute()` under 50 µs. The whole 250 µs tick also includes the USB round trip.
- `update_params()` must reject stiffness above 3000 N/m, which is unstable at 4 kHz.
- msgpack structs use `MSGPACK_DEFINE_MAP` (named keys), never `MSGPACK_DEFINE_ARRAY`.
- Guard wall-clock timing assertions with `#ifdef __linux__`, because sleeps on macOS runners are unreliable.
- A new force field needs:
  - the C++ class, with parameter validation;
  - tests in `cpp/haptic_server/tests/test_force_fields.cpp`;
  - its parameter schema in the protocol doc;
  - a feel-test class in `tests/hardware/test_interactive_fields.py`.

### Force Dimension SDK (proprietary, not in the repo)

- The build finds the SDK via `FD_SDK_DIR`. `MOCK_HARDWARE=ON` swaps in `DhdMock`, and CI only builds the mock. That means `DhdReal` changes can't be compiled there; say so in the PR.
- Headers are `dhdc.h` and `drdc.h`, but function names have no `c` (e.g. `dhdGetPosition`).
- Libraries are `libdhd` and `libdrd`, under `lib/release/lin-<arch>-gcc/` (Linux) or `lib/release/mac-<arch>-clang/` (macOS). Linux must link `usb-1.0` explicitly; macOS links the IOKit and CoreFoundation frameworks instead.
- `DhdReal` opens, closes, and calibrates through DRD: `drdOpen`, `drdClose`, and `drdAutoInit` (skipped if `drdIsInitialized()`). The haptic loop uses only DHD calls.
- Gravity compensation is computed on the host, inside `dhdSetForce()`. Three consequences:
  - it only works while forces are being sent;
  - `dhdEnableForce(DHD_ON)` must be called first;
  - the force you command isn't the force the device applies.
  Always check `dhdSetForce()` return values.
- Device capabilities such as auto-calibration and gravity compensation are declared in the rig config and passed as `--no-calibrate` / `--no-gravity-comp`. Never branch on device type in C++ (ADR-017).

## Teensy firmware (`firmware/teensy/`)

- Outputs are 3.3 V logic; don't assume 5 V TTL.
- The camera trigger and 1 Hz sync run on `IntervalTimer`s, never `delay()` or `millis()` loops. To change a running timer's period, use `update()`, not `end()` then `begin()`.
- Serial parsing in `loop()` is non-blocking; never use `readString()`.
- The event-code strobe runs in the main loop, not an ISR.
- Pin numbers live only in `src/pins.h`. Strobe and pulse timing constants live in `src/timing.h`.
- Put warning flags in `build_src_flags`. Never add `-Wpedantic` or similar strictness flags to `build_flags`, which also applies to the Teensy core and its GCC extensions.
- Teensy 4 IntervalTimers share one PIT interrupt and priority: no delays, busy-waits, loops, or Serial inside any ISR.

## Hardware tests (agents may write them; the human runs them)

- Markers: `hardware`, plus `interactive` for feel tests.
- Feel tests all live in `tests/hardware/test_interactive_fields.py`, one class per field. They use `run_timed_evaluation()` and the function-scoped `client` fixture. That fixture is a `HapticClient` whose background heartbeat satisfies the server's 500 ms watchdog, and it reverts to NullField on teardown.
- Never send a single heartbeat and then block on `input()`. The server reverts to NullField after 500 ms without a heartbeat.
- Shared fixtures and helpers are in `tests/hardware/conftest.py`.

## Further reading

`docs/architecture.md` (overview), `docs/haptic_server_protocol.md` (C++↔Python contract), `docs/task_authoring_guide.md`, `docs/adr/`, `docs/rig-setup.md`, `docs/teensy-bringup.md`.
