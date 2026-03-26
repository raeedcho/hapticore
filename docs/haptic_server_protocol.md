# Haptic Server Protocol

This document defines the ZeroMQ + msgpack interface contract between the C++ haptic server and Python clients. Both sides must conform to this spec. The Python `HapticInterface` mock (in `python/hapticore/hardware/mock.py`) must faithfully implement this contract for offline testing.

## Transport

The server binds two ZeroMQ sockets. Addresses are configured via the YAML config (`zmq` section) or command-line flags.

| Socket | Pattern | Default address | Purpose |
|--------|---------|-----------------|---------|
| State | PUB | `ipc:///tmp/hapticore_haptic_state` | Broadcasts device state at `publish_rate_hz` |
| Command | ROUTER | `ipc:///tmp/hapticore_haptic_cmd` | Receives commands, sends responses |

For cross-machine operation, use `tcp://*:5555` (state) and `tcp://*:5556` (command).

## Coordinate convention

All positions, velocities, and forces in state messages and command parameters use the **lab frame**:

| Lab axis | Direction | Sign convention |
|----------|-----------|-----------------|
| X | Left / right (horizontal) | + = rightward |
| Y | Up / down (vertical) | + = upward |
| Z | Forward / backward (depth) | + = toward operator |

This differs from the Force Dimension DHD SDK's native frame (X=depth, Y=horizontal, Z=vertical). The remap is applied inside `DhdReal` (see ADR-008) so all other code — force fields, protocol messages, and Python clients — works in the lab frame transparently. `DhdMock` does not remap (it already operates in lab frame by definition).

## State messages (PUB socket → SUB clients)

Published as ZeroMQ multipart: `[topic, payload]`.

- **Topic frame:** `b"state"` (the literal bytes, matching `hapticore.core.messages.TOPIC_STATE`)
- **Payload frame:** msgpack-encoded map with these keys:

| Key | Type | Description |
|-----|------|-------------|
| `timestamp` | float64 | `clock_gettime(CLOCK_MONOTONIC)` in seconds |
| `sequence` | uint64 | Monotonically increasing counter, starts at 0 |
| `position` | array[3] of float64 | Device position `[x, y, z]` in meters |
| `velocity` | array[3] of float64 | Device velocity `[vx, vy, vz]` in m/s |
| `force` | array[3] of float64 | Applied force `[fx, fy, fz]` in Newtons |
| `active_field` | string | Name of the active ForceField (e.g. `"null"`, `"spring_damper"`) |
| `field_state` | map | Force-field-specific state (see per-field tables below) |

**Serialization rule:** Use msgpack **named keys** (map), not positional arrays. In C++ this means `MSGPACK_DEFINE_MAP`. The Python side deserializes with `msgpack.unpackb(data, raw=False)` and constructs `HapticState(**unpacked)`.

**Rate:** Configurable, default 200 Hz (from `HapticConfig.publish_rate_hz`). Jitter of ±2 ms is acceptable.

## Command messages (DEALER client → ROUTER server)

### Request (client sends)

ZeroMQ DEALER sends: `[empty_frame, payload]`
ZeroMQ ROUTER receives: `[client_identity, empty_frame, payload]`

Payload is a msgpack map:

| Key | Type | Description |
|-----|------|-------------|
| `command_id` | string | Unique ID (e.g. 12-char hex from `uuid4`) for matching response |
| `method` | string | Command name (see table below) |
| `params` | map | Method-specific parameters |

### Response (server sends)

ZeroMQ ROUTER sends: `[client_identity, empty_frame, payload]`
ZeroMQ DEALER receives: `[empty_frame, payload]`

Payload is a msgpack map:

| Key | Type | Description |
|-----|------|-------------|
| `command_id` | string | Echoed from request |
| `success` | bool | `true` if command succeeded |
| `result` | map | Method-specific return values (empty `{}` on failure) |
| `error` | string or nil | Error message if `success` is `false`, otherwise `nil`/absent |

### Supported commands

#### `set_force_field`

Switch the active force field. Atomically swaps the field pointer seen by the haptic thread.

**params:**
```
{
    "type": "<field_type_name>",  // e.g. "null", "spring_damper", "constant",
                                  //       "workspace_limit", "cart_pendulum",
                                  //       "channel", "composite"
    "params": { ... }             // field-specific parameters (see below)
}
```

**result:** `{"active_field": "<field_type_name>"}` on success.

#### `set_params`

Update the active force field's parameters without replacing the field instance. Use for mid-trial parameter changes (e.g., changing pendulum length between conditions).

**params:** The field-specific parameter map (same format as the `params` key inside `set_force_field`).

**result:** `{"active_field": "<current_field_name>"}` on success.

#### `get_state`

Return a snapshot of the current state. Useful for synchronization.

**params:** `{}` (empty)

**result:** Same fields as a published `HapticState`.

#### `heartbeat`

Reset the communication timeout watchdog. Python must send this at least every 500 ms. If the server receives no heartbeat within the timeout window, it reverts the active field to NullField with light damping (safety measure).

**params:** `{}` (empty)

**result:** `{"timeout_ms": 500}`

#### `stop`

Cleanly shut down the server. Reverts to NullField, then exits.

**params:** `{}` (empty)

**result:** `{"shutting_down": true}`

## Force field parameter schemas

### `null`

No parameters. Always returns `F = [0, 0, 0]`.

**field_state:** `{}` (empty map)

### `constant`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | array[3] float | `[0,0,0]` | Constant force vector in Newtons |

**field_state:** `{}` (empty map)

### `spring_damper`

| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| `stiffness` | float | 100.0 | 0 ≤ K ≤ 3000 | Spring constant in N/m |
| `damping` | float | 5.0 | 0 ≤ B ≤ 100 | Damping coefficient in N·s/m |
| `center` | array[3] float | `[0,0,0]` | — | Equilibrium position in meters |

Force: `F = -K * (pos - center) - B * vel`

Stiffness hard limit: 3000 N/m at 4 kHz (stability boundary). Reject params with stiffness above this.

**field_state:** `{}` (empty map)

### `workspace_limit`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `bounds` | map | from config | `{"x": [min, max], "y": [min, max], "z": [min, max]}` in meters |
| `stiffness` | float | 2000.0 | Wall spring stiffness in N/m |
| `damping` | float | 10.0 | Wall damping in N·s/m |

Force: for each axis, if `pos[i] < min[i]`, apply `K * (min[i] - pos[i]) - B * vel[i]`; if `pos[i] > max[i]`, apply `K * (max[i] - pos[i]) - B * vel[i]`; otherwise zero on that axis.

**field_state:** `{"in_bounds": true/false}`

### `cart_pendulum`

| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| `ball_mass` | float | 0.6 | > 0 | Ball (pendulum bob) mass in kg |
| `cup_mass` | float | 2.4 | > 0 | Cup (cart) mass in kg |
| `pendulum_length` | float | 0.3 | > 0 | Pendulum length in meters |
| `gravity` | float | 9.81 | > 0 | Gravitational acceleration in m/s² |
| `angular_damping` | float | 0.1 | ≥ 0 | Angular damping in N·m·s/rad |
| `spill_threshold` | float | 1.5708 | > 0 | Ball spill angle in radians (π/2) |
| `cup_inertia_enabled` | bool | true | — | Include cup inertial resistance |
| `accel_filter_hz` | float | 30.0 | 5 ≤ f ≤ 200 | Low-pass cutoff for cup acceleration estimate (Hz) |

Dynamics: 2D cart-pendulum. Cup position = `pos[0]` (x-axis). RK4 integration per tick.

**field_state:**

| Key | Type | Description |
|-----|------|-------------|
| `phi` | float | Current ball angle (radians, 0 = hanging straight down) |
| `phi_dot` | float | Ball angular velocity (rad/s) |
| `spilled` | bool | Whether `|phi| > spill_threshold` |
| `cup_x` | float | Cup position (meters) |
| `ball_x` | float | Ball world x position: `cup_x + L*sin(phi)` |
| `ball_y` | float | Ball y position relative to cup: `-L*cos(phi)` |
| `filtered_accel` | float | Low-pass-filtered cup acceleration estimate (m/s²) |

### `channel`

A per-axis spring-damper that constrains motion to a plane or line. Axes listed in `axes` are constrained (spring-damper restoring force); unlisted axes are completely free (zero force). Useful for confining the hand to a horizontal plane (free X/Y, constrain Z) or a line (free X, constrain Y/Z).

| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| `axes` | array of int | `[2]` | values in {0, 1, 2} | Axes to constrain (0=X, 1=Y, 2=Z) |
| `stiffness` | float | 500.0 | 0 ≤ K ≤ 3000 | Spring constant in N/m |
| `damping` | float | 10.0 | 0 ≤ B ≤ 100 | Damping coefficient in N·s/m |
| `center` | array[3] float | `[0,0,0]` | — | Equilibrium position (only constrained axes matter) |

Force: for each axis `i`: if `i` in `axes`, `F[i] = -K * (pos[i] - center[i]) - B * vel[i]`; otherwise `F[i] = 0`.

Stiffness hard limit: 3000 N/m at 4 kHz (stability boundary). Reject params with stiffness above this.

**field_state:** `{}` (empty map)

### `composite`

A sum-of-fields. Its `compute()` returns the element-wise sum of all child fields.

**params:**
```
{
    "fields": [
        {"type": "workspace_limit", "params": { ... }},
        {"type": "spring_damper", "params": { ... }}
    ]
}
```

**field_state:** `{"children": [<child_0_field_state>, <child_1_field_state>, ...]}`

## Safety invariants

These must hold on **every haptic tick** regardless of command state:

1. **Force clamping:** Per-axis clamp to `[-force_limit, +force_limit]` AND magnitude clamp to `force_limit`. Default `force_limit` = 20 N (from `HapticConfig.force_limit_n`).
2. **Heartbeat timeout:** If no `heartbeat` command received within 500 ms, atomically swap to NullField with damping (`B = 10 N·s/m`) and log a warning. Resume normal operation when the next heartbeat arrives.
3. **Stiffness limit:** ForceField `update_params` must reject stiffness values > 3000 N/m.
4. **Gravity compensation:** Call `dhdSetEffectorMass()` at startup with the configured effector mass so the DHD SDK provides gravity compensation.
