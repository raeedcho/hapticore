# Phase 2: C++ Haptic Server with ZeroMQ Interface

## Goal

Build a standalone C++ executable that runs the Force Dimension delta.3 haptic loop at 4 kHz and communicates with the Python layer via ZeroMQ + msgpack. This is Phase 2 of 7. The server must compile and pass all tests without the physical robot using a mock DHD backend (`-DMOCK_HARDWARE=ON`).

## Context

Phase 1 (complete, tested — see `python/hapticore/`) built the Python messaging backbone: `EventBus`, `CommandClient`/`CommandServer`, msgpack serialization, Pydantic config, Protocol interfaces, and mock implementations. The Python side already subscribes to `HapticState` messages on topic `b"state"` and sends `Command`/`CommandResponse` via DEALER-ROUTER. Look at these files to understand the message formats the C++ server must produce and consume:

- `python/hapticore/core/messages.py` — `HapticState`, `Command`, `CommandResponse` dataclass fields and `serialize()`/`deserialize()` functions
- `python/hapticore/core/messaging.py` — `EventPublisher` (PUB, multipart `[topic, payload]`), `CommandClient` (DEALER, sends `[b"", payload]`), `CommandServer` (ROUTER, receives `[identity, b"", payload]`)
- `python/hapticore/core/config.py` — `HapticConfig` (force_limit_n, publish_rate_hz, workspace_bounds), `ZMQConfig` (haptic_state_address, haptic_command_address)

Read `.github/copilot-instructions.md` for the full architecture overview and `docs/haptic_server_protocol.md` for the exact interface contract before writing any code.

Key architecture decisions (see `docs/adr/`):
- **ADR-002:** Python sends field parameters, C++ evaluates forces at 4 kHz. Python never sends raw forces.
- **ADR-004:** Use Force Dimension DHD SDK directly — not CHAI3D.
- **ADR-007:** Box2D for 2D physics (not needed yet in Phase 2 — `PhysicsField` is a later addition).

## Plan — implement in this order

Work through these steps sequentially. After each step, verify the build compiles and tests pass before moving on:

```bash
cmake -S cpp/haptic_server -B build -DMOCK_HARDWARE=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
cd build && ctest --output-on-failure
```

---

### Step 1: CMake scaffolding and directory structure

Create the C++ project structure inside the existing monorepo:

```
cpp/haptic_server/
├── CMakeLists.txt
├── cmake/
│   └── CPM.cmake            # download from https://github.com/cpm-cmake/CPM.cmake
├── src/
│   ├── main.cpp
│   ├── haptic_thread.hpp
│   ├── haptic_thread.cpp
│   ├── publisher_thread.hpp
│   ├── publisher_thread.cpp
│   ├── command_thread.hpp
│   ├── command_thread.cpp
│   ├── triple_buffer.hpp
│   ├── state_data.hpp        # HapticStateData struct + serialization
│   ├── command_data.hpp      # CommandData / CommandResponseData structs
│   ├── force_fields/
│   │   ├── force_field.hpp        # abstract base
│   │   ├── null_field.hpp
│   │   ├── null_field.cpp
│   │   ├── spring_damper_field.hpp
│   │   ├── spring_damper_field.cpp
│   │   ├── constant_field.hpp
│   │   ├── constant_field.cpp
│   │   ├── workspace_limit_field.hpp
│   │   ├── workspace_limit_field.cpp
│   │   ├── cart_pendulum_field.hpp
│   │   ├── cart_pendulum_field.cpp
│   │   ├── composite_field.hpp
│   │   ├── composite_field.cpp
│   │   └── field_factory.hpp  # create fields by type name string
│   ├── dhd_interface.hpp      # abstract device interface
│   ├── dhd_real.hpp           # real DHD SDK wrapper
│   ├── dhd_real.cpp
│   ├── dhd_mock.hpp           # mock DHD for testing
│   └── dhd_mock.cpp
├── tests/
│   ├── CMakeLists.txt
│   ├── test_triple_buffer.cpp
│   ├── test_force_fields.cpp
│   ├── test_serialization.cpp
│   └── test_integration.cpp
└── build.sh
```

Create `CMakeLists.txt` with:
- `cmake_minimum_required(VERSION 3.16)`
- `project(haptic_server LANGUAGES CXX)` with `CMAKE_CXX_STANDARD 17` required
- Include `cmake/CPM.cmake`
- Use CPM to fetch: `cppzmq` (which pulls in `libzmq`), `msgpack-cxx` (header-only, `MSGPACK_USE_BOOST OFF`)
- `option(MOCK_HARDWARE "Use mock DHD instead of real Force Dimension SDK" ON)`
- When `MOCK_HARDWARE=OFF`: find the Force Dimension SDK headers and libraries via `FD_SDK_DIR` env var. Link `dhd` and `drd`.
- When `MOCK_HARDWARE=ON`: compile `dhd_mock.cpp` instead of `dhd_real.cpp`. Define preprocessor macro `HAPTIC_MOCK_HARDWARE`.
- Link `pthread` and (on Linux) `rt`
- Compiler flags: `-Wall -Wextra -Wpedantic` always; add `-Werror` for Debug builds
- `enable_testing()` and add `tests/` subdirectory using Google Test via CPM
- Build two targets: `haptic_server` executable and a `haptic_server_lib` static library (all source except `main.cpp`) that tests link against

Create `build.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
BUILD_DIR="${1:-build}"
MOCK="${MOCK_HARDWARE:-ON}"
cmake -S cpp/haptic_server -B "$BUILD_DIR" \
      -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-RelWithDebInfo}" \
      -DMOCK_HARDWARE="$MOCK"
cmake --build "$BUILD_DIR" --parallel "$(nproc 2>/dev/null || sysctl -n hw.ncpu)"
echo "Build complete. Run tests: cd $BUILD_DIR && ctest --output-on-failure"
```

For Step 1, `main.cpp` can be a stub that parses `--help` and returns 0. The goal is a project that configures, compiles, and runs `ctest` with zero tests.

**Test for Step 1:** `cmake --build build` succeeds. `ctest` runs and reports 0 tests.

---

### Step 2: Triple buffer (`triple_buffer.hpp`)

Implement a lock-free triple buffer for sharing `HapticStateData` between the haptic writer thread and the publisher reader thread. This is a header-only template.

Design requirements:
- **Lock-free.** The haptic thread (writer) must never block. Use `std::atomic` operations only.
- **Writer never waits.** `publish()` always succeeds instantly. If the reader hasn't consumed the previous value, the oldest unread value is silently overwritten. This is correct — the reader always wants the latest state.
- **Reader gets the latest published state.** `swap_read_buffer()` atomically exchanges indices so the reader sees the most recently published data.
- Template parameter `T` will be instantiated with `HapticStateData`.

Suggested API:
```cpp
template <typename T>
class TripleBuffer {
public:
    T& write_buffer();            // get writable slot (always instant)
    void publish();               // make current write slot available to reader

    bool swap_read_buffer();      // swap to latest; returns true if new data
    const T& read_buffer() const; // get the current read slot
};
```

Implementation approach: three `T` slots and a single `std::atomic<uint8_t>` encoding `[write_idx | dirty_idx | read_idx | new_data_flag]` using bitpacking and `compare_exchange_weak`. Alternatively, use three separate `std::atomic<int>` indices — correctness matters more than cleverness.

**Tests for Step 2** (`tests/test_triple_buffer.cpp`):
- Single-threaded: write value, publish, swap-read → value matches
- Single-threaded: write three values (publish after each), swap-read once → reader sees only the most recent value
- Multi-threaded: writer publishes 100,000 incrementing integers; reader reads continuously → reader always sees monotonically non-decreasing values (no torn reads)
- `swap_read_buffer()` returns `false` when no new data has been published since the last swap

---

### Step 3: DHD interface abstraction

Define an abstract interface for Force Dimension device calls, with a real and mock implementation swapped at compile time.

```cpp
// dhd_interface.hpp
#pragma once
#include <array>
#include <memory>
#include <string>

using Vec3 = std::array<double, 3>;

class DhdInterface {
public:
    virtual ~DhdInterface() = default;
    virtual bool open() = 0;
    virtual void close() = 0;
    virtual bool is_open() const = 0;
    virtual bool get_position(Vec3& pos) = 0;
    virtual bool get_linear_velocity(Vec3& vel) = 0;
    virtual bool set_force(const Vec3& force) = 0;
    virtual bool set_effector_mass(double mass_kg) = 0;
    virtual std::string device_name() const = 0;
    virtual Vec3 max_force() const = 0;
};

std::unique_ptr<DhdInterface> create_dhd_interface();
```

**`DhdReal`** (compiled when `MOCK_HARDWARE` is off): thin wrapper calling `dhdOpen()`, `dhdGetPosition()`, `dhdGetLinearVelocity()`, `dhdSetForce()`, `dhdSetEffectorMass()`, `dhdClose()`. Include `<dhd.h>` from the SDK.

**`DhdMock`** (compiled when `MOCK_HARDWARE` is on): returns configurable synthetic positions/velocities. Records every `set_force()` call in an internal `std::vector<Vec3>` for test assertions. Provide setters:
```cpp
void set_mock_position(const Vec3& pos);
void set_mock_velocity(const Vec3& vel);
const std::vector<Vec3>& applied_forces() const;
void clear_force_log();
```

`create_dhd_interface()` returns `DhdMock` or `DhdReal` based on the compile flag.

**Tests for Step 3:** Verify `DhdMock` returns configured values. Verify `set_force()` calls are recorded. Verify `create_dhd_interface()` returns the mock in test builds.

---

### Step 4: Force field base class and simple fields

Implement the `ForceField` abstract base and four concrete subclasses. These are pure math with no hardware dependency, so they're easy to test thoroughly.

```cpp
// force_field.hpp
#pragma once
#include <array>
#include <string>
#include <msgpack.hpp>

using Vec3 = std::array<double, 3>;

class ForceField {
public:
    virtual ~ForceField() = default;

    // Called at 4 kHz by the haptic thread.
    // Contract: must complete in < 50 µs. No allocation, no I/O, no locks.
    virtual Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) = 0;

    virtual std::string name() const = 0;

    // Update parameters from deserialized msgpack map. Return false on invalid params.
    virtual bool update_params(const msgpack::object& params) = 0;

    // Pack field-specific state into the provided packer. Default: empty map.
    virtual void pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
        pk.pack_map(0);
    }

    // Reset internal state (e.g., between trials).
    virtual void reset() {}
};
```

Implement these subclasses per the schemas in `docs/haptic_server_protocol.md`:

**`NullField`:** `compute()` returns `{0, 0, 0}`. `name()` returns `"null"`. Accepts no params.

**`ConstantField`:** Returns a fixed force vector. `update_params` expects `{"force": [fx, fy, fz]}`.

**`SpringDamperField`:** `F = -K * (pos - center) - B * vel`. `update_params` expects `{"stiffness": K, "damping": B, "center": [cx, cy, cz]}`. **Must reject stiffness > 3000 N/m** (return false from `update_params`). This is a hard safety limit — at 4 kHz, stiffness above this causes haptic instability.

**`WorkspaceLimitField`:** For each axis, if position exceeds bounds, apply a spring-damper restoring force toward the boundary. `update_params` expects `{"bounds": {"x": [min, max], ...}, "stiffness": K, "damping": B}`. `pack_state()` publishes `{"in_bounds": bool}`.

Also implement `FieldFactory`:
```cpp
// field_factory.hpp
#include <memory>
#include <string>
std::unique_ptr<ForceField> create_field(const std::string& type_name);
```

Returns the appropriate `ForceField` subclass for `"null"`, `"constant"`, `"spring_damper"`, `"workspace_limit"`, `"cart_pendulum"`, `"composite"`. Returns `nullptr` for unknown types.

**Tests for Step 4** (`tests/test_force_fields.cpp`):
- `NullField::compute()` returns `{0, 0, 0}` for any input
- `ConstantField` returns its configured vector regardless of position/velocity
- `SpringDamperField` math verification: pos=`{0.1, 0, 0}`, center=`{0, 0, 0}`, K=100, B=0 → force=`{-10, 0, 0}` (within 1e-10 tolerance)
- `SpringDamperField` damping: pos at center, vel=`{1, 0, 0}`, B=10 → force=`{-10, 0, 0}`
- `SpringDamperField` rejects stiffness=4000 (returns `false` from `update_params`)
- `SpringDamperField` accepts stiffness=3000 (returns `true`)
- `WorkspaceLimitField`: position inside bounds → zero force; position=`{0.2, 0, 0}` with x-max=0.15 → negative x-force restoring toward boundary
- `FieldFactory` returns correct types for all known names, returns `nullptr` for `"unknown"`
- All fields: `update_params` with missing keys → returns `false`, does not crash
- All fields: `update_params` with wrong value types → returns `false`, does not crash

---

### Step 5: CartPendulumField

Implement the cart-pendulum ODE integration. This is the most compute-intensive force field and the key physics validation for the cup-and-ball task.

**Physics model** (2D cart-pendulum, Bazzi et al. 2018):
- Cup = cart. Its position `x` comes from the robot handle: `cup_x = pos[0]`.
- Ball = pendulum bob. State: angle `φ` (0 = hanging down), angular velocity `φ̇`.
- ODE: `φ̈ = (-g·sin(φ) - ẍ·cos(φ) - b·φ̇) / L`
  where `g` = gravity, `ẍ` = cup acceleration (finite difference of velocity), `L` = pendulum length, `b` = angular damping coefficient
- Cup acceleration estimate: `ẍ = (vel_x_current - vel_x_previous) / dt`. Simple first-order difference. Store `vel_x_previous` as member state.
- Reaction force on cup: `F_reaction = m_b * L * (φ̈·cos(φ) - φ̇²·sin(φ))`
- If `cup_inertia_enabled`: total force x-component = `F_reaction + m_cup * ẍ`. Otherwise just `F_reaction`.
- Force is 1D (x-axis only). Y and Z components are zero.
- Spill detection: set `spilled_ = true` when `|φ| > spill_threshold`.

**Integration:** RK4 on the `[φ, φ̇]` state per haptic tick (dt = 0.00025 s).

**Parameters** (via `update_params`, see `docs/haptic_server_protocol.md` § `cart_pendulum`):
`ball_mass`, `cup_mass`, `pendulum_length`, `gravity`, `angular_damping`, `spill_threshold`, `cup_inertia_enabled`.

**`pack_state()`** publishes: `phi`, `phi_dot`, `spilled`, `cup_x`, `ball_x`, `ball_y` (see protocol doc).

**`reset()`**: Set `φ = 0`, `φ̇ = 0`, `spilled_ = false`, `vel_x_previous_ = 0`.

**Tests for Step 5** (`tests/test_force_fields.cpp`, continued):
- **Small-angle period:** Stationary cup, `φ₀ = 0.01 rad`, `φ̇₀ = 0`, L=1.0, g=9.81. Run for `T_expected = 2π√(L/g) ≈ 2.006 s` worth of ticks (8024 ticks at 4 kHz). Measure the time for φ to return to near 0.01 rad. Verify period matches analytical value within 2%.
- **Energy conservation:** Zero damping, stationary cup, `φ₀ = 0.5 rad`. Run 10,000 ticks. Verify total energy `E = 0.5 * m * L² * φ̇² + m * g * L * (1 - cos(φ))` is conserved within 0.1%.
- **Spill detection:** `φ₀ = 1.5 rad`, positive `φ̇`. After a few ticks, verify `spilled` becomes `true`.
- **Reaction force sign:** Cup accelerating right (positive ẍ) with ball hanging straight down → force x-component should be negative (ball resists being dragged right). Verify sign.
- **Parameter update mid-run:** Change `pendulum_length` via `update_params`, verify next `compute()` uses the new length.
- **Reset:** Call `reset()`, verify `φ = 0`, `φ̇ = 0`, `spilled = false`.

---

### Step 6: CompositeField

`CompositeField` owns a `std::vector<std::unique_ptr<ForceField>>` of child fields. Its `compute()` calls each child and returns the element-wise sum.

**`update_params`** expects:
```json
{"fields": [{"type": "workspace_limit", "params": {...}}, {"type": "spring_damper", "params": {...}}]}
```
Constructs children via `FieldFactory`. Returns `false` if any child type is unknown or any child's `update_params` fails.

**`pack_state()`** publishes: `{"children": [child_0_state, child_1_state, ...]}`.

**Tests:**
- CompositeField with SpringDamper + WorkspaceLimit: force equals sum of individual forces
- CompositeField with unknown child type → `update_params` returns false
- Empty children list → `compute()` returns `{0, 0, 0}`

---

### Step 7: State and command serialization (`state_data.hpp`, `command_data.hpp`)

Define the structs that cross the ZeroMQ boundary.

```cpp
// state_data.hpp
struct HapticStateData {
    double timestamp;
    uint64_t sequence;
    Vec3 position;
    Vec3 velocity;
    Vec3 force;
    std::string active_field;
    msgpack::sbuffer field_state_buf;  // pre-packed field state (see below)

    // Pack the full state message into the provided buffer
    void pack(msgpack::sbuffer& buf) const;
};
```

**The field_state serialization problem and solution:**

The publisher thread (Step 8) needs to serialize `HapticStateData` into a ZeroMQ message. But the publisher thread does not — and must not — have access to the `ForceField` object, because the `ForceField` is owned by the haptic thread and may be mutated on every tick. So we cannot pass the `ForceField` into `pack()` at publish time.

Instead, the **haptic thread pre-packs the field state at write time**. In the haptic loop (Step 10), immediately after calling `field->compute()`, the haptic thread calls `field->pack_state()` into the `field_state_buf` member of the `HapticStateData` that it is writing to the triple buffer. This captures the field's internal state (e.g., pendulum angle) at the exact moment the force was computed. The publisher thread later reads this pre-packed buffer without touching the `ForceField`.

**`HapticStateData::pack()`** then produces a msgpack map with 7 keys matching the Python `HapticState` dataclass exactly: `"timestamp"`, `"sequence"`, `"position"`, `"velocity"`, `"force"`, `"active_field"`, `"field_state"`. For the first 6 fields, use the normal `packer.pack(value)` calls. For `field_state`, pack the key string normally, then copy the pre-packed bytes directly into the output buffer:

```cpp
void HapticStateData::pack(msgpack::sbuffer& buf) const {
    msgpack::packer<msgpack::sbuffer> pk(buf);
    pk.pack_map(7);
    pk.pack("timestamp");    pk.pack(timestamp);
    pk.pack("sequence");     pk.pack(sequence);
    pk.pack("position");     pk.pack(position);
    pk.pack("velocity");     pk.pack(velocity);
    pk.pack("force");        pk.pack(force);
    pk.pack("active_field"); pk.pack(active_field);
    pk.pack("field_state");
    // field_state_buf already contains a valid msgpack value (a map),
    // so writing the raw bytes directly produces a correct key-value pair.
    buf.write(field_state_buf.data(), field_state_buf.size());
}
```

This works because msgpack is self-describing — the pre-packed bytes from `ForceField::pack_state()` are already a complete msgpack map value, and appending them after the key produces valid msgpack without any double-wrapping.

In the haptic thread (Step 10), the write sequence is:
```cpp
auto& state = state_buffer_.write_buffer();
state.timestamp = now;
state.sequence = sequence_++;
state.position = pos;
state.velocity = vel;
state.force = clamped_force;
state.active_field = field->name();
state.field_state_buf.clear();
field->pack_state(msgpack::packer<msgpack::sbuffer>(state.field_state_buf));
state_buffer_.publish();
```

```cpp
// command_data.hpp
struct CommandData {
    std::string command_id;
    std::string method;
    msgpack::object_handle params;  // holds the params map

    static CommandData unpack(const char* data, size_t len);
};

struct CommandResponseData {
    std::string command_id;
    bool success;
    std::map<std::string, msgpack::object> result;
    std::string error;

    void pack(msgpack::sbuffer& buf) const;
};
```

**Critical interop rules** (must match Python `messaging.py` behavior):
- Python `CommandClient.send_command()` packs `{"command_id": str, "method": str, "params": dict}` without a `__msg_type__` key. The C++ ROUTER side should unpack these three keys.
- The C++ response should pack `{"command_id": str, "success": bool, "result": map, "error": str_or_nil}`. Python's `CommandClient` will `unpackb` this and construct `CommandResponse(**unpacked)`. The `error` field should be msgpack nil (not absent) when there's no error, because the Python dataclass expects the key to exist (it has a default of `None`).

**Tests for Step 7** (`tests/test_serialization.cpp`):
- Pack a `HapticStateData` with known values, unpack the bytes, verify all keys are present in the map and values match
- Verify the packed output is a msgpack map (not array) — check the first byte is in the map type range
- Verify Vec3 values serialize as 3-element arrays of floats
- Pack a `CommandResponseData` with `success=true` and with `success=false` + error string, verify round-trip

Add a cross-language test (this can be a Python test in `tests/integration/`):
- Have a C++ test write packed `HapticStateData` bytes to a temp file
- Have a Python test read the file, unpack with `msgpack.unpackb(data, raw=False)`, and construct `HapticState(**unpacked)` — verify all fields match

---

### Step 8: Publisher thread

Reads from the triple buffer and broadcasts state via ZeroMQ PUB.

```cpp
class PublisherThread {
public:
    PublisherThread(TripleBuffer<HapticStateData>& state_buffer,
                    const std::string& pub_address,   // e.g. "tcp://*:5555"
                    double publish_rate_hz);           // from HapticConfig

    void run(std::stop_token stop);  // thread entry point
};
```

Behavior:
1. Create a `zmq::context_t` and bind a `zmq::socket_t` (PUB) to the address.
2. Loop at `publish_rate_hz` (default 200 Hz): call `state_buffer.swap_read_buffer()`. If new data, serialize with `HapticStateData::pack()`, send as multipart `[b"state", packed_bytes]`.
3. Timing: `std::this_thread::sleep_for()` is fine here — this thread does NOT need real-time scheduling.
4. On `stop_token` cancellation, close the socket.

The multipart topic frame must be the literal bytes `state` (5 bytes, no null terminator), matching `hapticore.core.messages.TOPIC_STATE = b"state"`.

**Tests for Step 8** (`tests/test_integration.cpp`):
- Start publisher thread, connect a `zmq::socket_t` (SUB, subscribe to `"state"`) in the test thread. Write known data to the triple buffer. Verify a message arrives with the correct topic prefix and the payload deserializes to matching values.
- Receive ~20 messages, verify elapsed time is approximately 100 ms (at 200 Hz ±20%).

---

### Step 9: Command thread

Listens on a ZeroMQ ROUTER socket and dispatches commands to a handler.

```cpp
class CommandThread {
public:
    using Handler = std::function<CommandResponseData(const CommandData&)>;

    CommandThread(const std::string& router_address,
                  Handler handler);

    void run(std::stop_token stop);
};
```

Behavior:
1. Bind a `zmq::socket_t` (ROUTER) to the address.
2. Poll with 100 ms timeout (so we can check `stop_token`).
3. On message: ROUTER delivers `[identity, empty_frame, payload]`. Deserialize payload as `CommandData`. Call `handler_`. Pack and send back `[identity, empty_frame, response_bytes]`.
4. Malformed messages: log a warning and skip (do not crash).

The handler function (provided by `main.cpp` in Step 11) dispatches based on `cmd.method`:
- `"set_force_field"` → construct new field via `FieldFactory`, atomically swap
- `"set_params"` → call `active_field->update_params()`
- `"get_state"` → return current state snapshot
- `"heartbeat"` → update heartbeat timestamp
- `"stop"` → signal shutdown

**Tests for Step 9** (`tests/test_integration.cpp`, continued):
- Start command thread with a test handler that echoes back. Connect a DEALER socket, send a valid command, verify response arrives with matching `command_id` and `success=true`.
- Send unknown method → response has `success=false` and `error` contains the method name.
- Send garbage bytes → thread doesn't crash, no response sent (or error response).

---

### Step 10: Haptic thread

The real-time loop. This is the most performance-critical and safety-critical code.

```cpp
class HapticThread {
public:
    HapticThread(std::unique_ptr<DhdInterface> dhd,
                 TripleBuffer<HapticStateData>& state_buffer,
                 double force_limit_n,       // from HapticConfig
                 int cpu_core = 1);          // core to pin to

    void run(std::stop_token stop);

    // Thread-safe field swap (called from command thread)
    void set_field(std::shared_ptr<ForceField> field);
    std::shared_ptr<ForceField> get_field() const;

    // Heartbeat tracking (called from command thread)
    void update_heartbeat();
    bool heartbeat_expired() const;

private:
    std::unique_ptr<DhdInterface> dhd_;
    TripleBuffer<HapticStateData>& state_buffer_;
    double force_limit_n_;
    int cpu_core_;
    uint64_t sequence_ = 0;

    std::atomic<std::shared_ptr<ForceField>> active_field_;
    std::atomic<double> last_heartbeat_time_{0.0};  // CLOCK_MONOTONIC seconds
    static constexpr double HEARTBEAT_TIMEOUT_S = 0.5;

    Vec3 clamp_force(const Vec3& force) const;
    double get_monotonic_time() const;
};
```

**Loop body (every 250 µs = 0.00025 s):**
1. `clock_gettime(CLOCK_MONOTONIC)` for timestamp
2. `dhd_->get_position(pos)` and `dhd_->get_linear_velocity(vel)`
3. Check heartbeat: if expired, atomically swap to a NullField with light damping (B=10) and log once
4. Load current field from `active_field_` (atomic load of shared_ptr)
5. `force = field->compute(pos, vel, dt)`
6. `force = clamp_force(force)` — per-axis clamp AND magnitude clamp to `force_limit_n_`
7. `dhd_->set_force(force)`
8. Populate `HapticStateData` in `state_buffer_.write_buffer()`: set the scalar fields, then pre-pack the field state into `field_state_buf` as described in Step 7 (call `field->pack_state()` into the buffer). Call `state_buffer_.publish()`.
9. Increment `sequence_`
10. `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next_wakeup)` — advance by 250,000 ns

**Force clamping implementation:**
```cpp
Vec3 HapticThread::clamp_force(const Vec3& f) const {
    Vec3 clamped;
    for (int i = 0; i < 3; ++i)
        clamped[i] = std::clamp(f[i], -force_limit_n_, force_limit_n_);
    double mag = std::sqrt(clamped[0]*clamped[0] + clamped[1]*clamped[1] + clamped[2]*clamped[2]);
    if (mag > force_limit_n_ && mag > 0.0) {
        double scale = force_limit_n_ / mag;
        for (int i = 0; i < 3; ++i) clamped[i] *= scale;
    }
    return clamped;
}
```

**Real-time setup (Linux only, in `run()` before the loop):**
```cpp
#ifdef __linux__
    mlockall(MCL_CURRENT | MCL_FUTURE);
    struct sched_param param{};
    param.sched_priority = 80;
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &param) != 0)
        std::cerr << "Warning: could not set SCHED_FIFO (need root or CAP_SYS_NICE)\n";
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(cpu_core_, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);
#endif
```

**What must NOT happen in the loop body:**
- No heap allocation (`new`, `malloc`, `std::vector::push_back`, `std::string` construction from literals is okay only if SSO covers it)
- No mutex locks
- No I/O (`std::cout`, `printf`, file writes)
- No ZeroMQ calls (the publisher and command threads handle networking)
- No virtual dispatch on anything other than the `ForceField` — and that's a single `compute()` call per tick, which is acceptable

**Tests for Step 10** (using `DhdMock`):
- Run haptic thread for 100 ticks with SpringDamperField. Mock position is offset from center. Verify mock's `applied_forces()` are in the correct direction.
- Force clamping: set mock position to generate 50 N of spring force. Verify applied force magnitude is clamped to `force_limit_n` (20 N).
- Heartbeat timeout: start thread, do NOT call `update_heartbeat()`. After ~500 ms, verify applied forces become zero (NullField fallback).
- Verify `sequence` in the triple buffer is monotonically increasing.

---

### Step 11: Main entry point (`main.cpp`)

Wire all threads together with signal handling and command dispatch.

```
Usage: haptic_server [options]
  --pub-address ADDR    ZMQ PUB address (default: ipc:///tmp/hapticore_haptic_state)
  --cmd-address ADDR    ZMQ ROUTER address (default: ipc:///tmp/hapticore_haptic_cmd)
  --pub-rate HZ         State publish rate (default: 200)
  --force-limit N       Force clamp in Newtons (default: 20)
  --cpu-core N          CPU core for haptic thread (default: 1)
  --help                Print this help
```

Startup sequence:
1. Parse arguments
2. Create `DhdInterface` via `create_dhd_interface()`. Call `open()`. If it fails, exit with error.
3. Call `dhd->set_effector_mass(0.0)` for gravity compensation (mass configurable later).
4. Create `TripleBuffer<HapticStateData>`
5. Create initial `NullField` as the active field
6. Define command handler lambda that dispatches based on method string (see Step 9)
7. Launch three threads: haptic, publisher, command
8. Install `SIGINT`/`SIGTERM` handler that requests stop on all threads
9. Join all threads on shutdown
10. Call `dhd->close()`

The command handler lambda captures references to the haptic thread (for `set_field`, `update_heartbeat`) and uses `FieldFactory` + the protocol's command dispatch table.

**Test for Step 11:** Not a unit test — this is a manual smoke test. Run `./haptic_server --pub-address tcp://*:5555 --cmd-address tcp://*:5556`. In a separate terminal, run a Python snippet:

```python
import zmq, msgpack, uuid, time
ctx = zmq.Context()

# Subscribe to state
sub = ctx.socket(zmq.SUB)
sub.connect("tcp://localhost:5555")
sub.subscribe(b"state")
time.sleep(0.1)

# Receive a few state messages
for _ in range(5):
    topic, data = sub.recv_multipart()
    state = msgpack.unpackb(data, raw=False)
    print(f"seq={state['sequence']} pos={state['position']} field={state['active_field']}")

# Send a command to set a spring field
dealer = ctx.socket(zmq.DEALER)
dealer.connect("tcp://localhost:5556")
cmd = msgpack.packb({
    "command_id": uuid.uuid4().hex[:12],
    "method": "set_force_field",
    "params": {"type": "spring_damper", "params": {"stiffness": 200, "damping": 10, "center": [0, 0, 0]}}
}, use_bin_type=True)
dealer.send_multipart([b"", cmd])
_, resp_bytes = dealer.recv_multipart()
resp = msgpack.unpackb(resp_bytes, raw=False)
print(f"Command response: {resp}")

# Verify state now shows spring_damper
time.sleep(0.1)
topic, data = sub.recv_multipart()
state = msgpack.unpackb(data, raw=False)
print(f"After command: field={state['active_field']}")
```

---

## Verification checklist

Before considering Phase 2 complete:

- [ ] `cmake -S cpp/haptic_server -B build -DMOCK_HARDWARE=ON && cmake --build build --parallel` succeeds with zero warnings
- [ ] `cd build && ctest --output-on-failure` — all tests pass
- [ ] Force field math tests pass with tight tolerances (1e-10 for exact formulas, 2% for period/energy)
- [ ] CartPendulumField energy conservation test: < 0.1% drift over 10,000 ticks
- [ ] Triple buffer multi-threaded test passes under ThreadSanitizer (`-fsanitize=thread`)
- [ ] Publisher thread test: messages arrive on SUB socket with correct topic and deserializable payload
- [ ] Command thread test: DEALER→ROUTER round-trip succeeds with correct `command_id`
- [ ] Heartbeat timeout test: forces revert to zero within ~500 ms of no heartbeats
- [ ] Force clamping test: applied force never exceeds `force_limit_n`
- [ ] Cross-language serialization: C++ packed bytes deserialize correctly in Python
- [ ] `./haptic_server --help` prints usage and exits 0
- [ ] No compiler warnings with `-Wall -Wextra -Wpedantic -Werror`

## What NOT to build in this phase

- No `PhysicsField` / Box2D integration — that comes later when specific tasks need it. Keep Box2D in the CMake deps list (it's already specified in ADR-007) but do not implement `PhysicsField` yet.
- No Python `HapticClient` process connecting to the real C++ server — Phase 3 builds the task controller that does this. For now, cross-language testing uses standalone scripts or the integration test described in Step 7.
- No configuration file loading in the C++ server — use command-line arguments for now. Config file parsing can be added later.
- No logging framework — use `std::cerr` for warnings and errors for now. A proper logging system (spdlog or similar) can be added in a later pass.
- No Windows support — the real-time scheduling calls (`SCHED_FIFO`, `mlockall`, `clock_nanosleep`) are Linux-only. The mock build should compile on macOS for development, but real-time features are `#ifdef __linux__` guarded.