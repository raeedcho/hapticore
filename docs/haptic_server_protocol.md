# Haptic Server Protocol

This document defines the ZeroMQ + msgpack interface contract between the C++ haptic server and Python clients. Both sides must conform to this spec. The Python `HapticInterface` contract is satisfied by `MockHapticInterface` (in-process mock, `python/hapticore/backends/mock.py`) and `HapticClient` (ZMQ client for a running server, `python/hapticore/backends/haptic_client.py`). Both must conform to this spec.

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
                                  //       "channel", "composite", "physics_world"
    "params": { ... }             // field-specific parameters (see below)
}
```

**result:** `{"active_field": "<field_type_name>"}` on success.

#### `set_params`

Replace the active field with a fresh instance of the same type, constructed with the supplied parameters. This discards any accumulated internal state (e.g., `CartPendulumField`'s pendulum angle, `PhysicsField`'s body poses). Use this to change field parameters mid-session when starting a fresh integration from defaults is acceptable; otherwise use `set_force_field` with a full parameter map.

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
| `coupling_stiffness` | float | 800.0 | > 0, ≤ 3000 | Virtual coupler stiffness in N/m |
| `coupling_damping` | float | 2.0 | ≥ 0, ≤ 50 | Virtual coupler damping in N·s/m |
| `initial_phi` | float | 0.0 | abs(initial_phi) ≤ π | Optional. Initial ball angle in radians. Commits to `phi_` and triggers a cup position re-sync on the next `compute()` tick so coupling force is zero at t=0. |
| `initial_phi_dot` | float | 0.0 | — | Optional. Initial ball angular velocity in rad/s. Also triggers a cup position re-sync on the next `compute()` tick. |

> **Trial-start usage.** Because both `set_force_field` and `set_params` construct a fresh field instance internally, `initial_phi` / `initial_phi_dot` function as per-trial initial conditions: the task controller sends them in the params map at the start of each trial, and the new instance begins integration from that state. The simulated cup re-syncs to the device's x position on the first `compute()` tick, so the coupling force is zero at t=0 regardless of where the device was at the end of the previous trial.

Dynamics: 2D cart-pendulum with virtual coupling. The device connects to a simulated cart through a spring-damper coupler (K_vc, B_vc). The cart-pendulum ODE is integrated internally with RK4. Virtual mass lives entirely in the simulation; the device only feels the coupling spring-damper.

**field_state:**

| Key | Type | Description |
|-----|------|-------------|
| `phi` | float | Pendulum angle (rad, 0 = hanging down) |
| `phi_dot` | float | Pendulum angular velocity (rad/s) |
| `spilled` | bool | Whether `|phi|` > spill_threshold |
| `cup_x` | float | Simulated cart position in meters (`x_sim`, not device position) |
| `ball_x` | float | Ball world x position: `x_sim + L*sin(phi)` |
| `ball_y` | float | Ball y position relative to cup: `-L*cos(phi)` |
| `coupling_stretch` | float | `x_dev - x_sim` in meters (for debugging) |

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

### `physics_world`

A Box2D v3.0 physics world with rigid bodies, collisions, and joints. The hand controls a kinematic body; Box2D simulates all other bodies and returns the reaction forces felt through the robot.

**params:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gravity` | array[2] float | `[0,0]` | World gravity `[gx, gy]` in m/s² |
| `bodies` | array of body defs | (required) | List of body definitions (see below) |
| `hand_body` | string | (required) | ID of the kinematic body controlled by the device |
| `force_scale` | float | 1.0 | Multiplier applied to the output force |
| `sub_steps` | int | 1 | Number of Box2D sub-steps per tick |

**Body definition:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | (required) | Unique identifier for this body |
| `type` | string | (required) | `"static"`, `"kinematic"`, or `"dynamic"` |
| `shape` | map | (required) | Shape definition (see below) |
| `position` | array[2] float | `[0,0]` | Initial position `[x, y]` in meters |
| `mass` | float | 1.0 | Body mass in kg (dynamic bodies only) |
| `restitution` | float | 0.0 | Bounce coefficient [0, 1] |
| `friction` | float | 0.3 | Coulomb friction coefficient |
| `fixed_rotation` | bool | false | If true, body cannot rotate |
| `linear_damping` | float | 0.0 | Linear velocity damping |
| `angular_damping` | float | 0.0 | Angular velocity damping |
| `joint` | map | — | Optional inline joint definition |

**Shape definition:**

Circle: `{"type": "circle", "radius": <float>}`
Box: `{"type": "box", "width": <float>, "height": <float>}`

**Joint definition (inline on a body):**

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `"revolute"` or `"prismatic"` |
| `anchor` | string | Body ID to anchor to, or `"hand"` for the hand body |
| `offset` | array[2] float | Local anchor offset `[ox, oy]` on the owner body |

**Dynamics:** The hand body is driven kinematically to match the device XY position each tick. `b2World_Step` advances the simulation. Contact impulses (normal and tangent, in N·s) acting on the hand body are summed and divided by `dt` to produce contact forces. Joint constraint forces (already in Newtons, as Box2D internally multiplies solver impulses by `1/dt`) are added directly. The Z component is always 0 (Box2D is 2D). Configurations where `hand_body` references a `"dynamic"` body are rejected — dynamic hand bodies require virtual coupling (ADR-010), which is not yet implemented.

**field_state:**

```
{
    "bodies": {
        "<body_id>": {
            "position": [x, y],
            "angle": <float>,
            "linear_velocity": [vx, vy],
            "shape": {"type": "circle", "radius": <float>}
        },
        ...
    }
}
```

Only non-static bodies (dynamic and kinematic) are included in the output.

## Safety invariants

These must hold on **every haptic tick** regardless of command state:

1. **Force clamping:** Per-axis clamp to `[-force_limit, +force_limit]` AND magnitude clamp to `force_limit`. Default `force_limit` = 20 N (from `HapticConfig.force_limit_n`).
2. **Heartbeat timeout:** If no `heartbeat` command received within 500 ms, atomically swap to NullField with damping (`B = 10 N·s/m`) and log a warning. Resume normal operation when the next heartbeat arrives.
3. **Stiffness limit:** ForceField `update_params` must reject stiffness values > 3000 N/m.
4. **Gravity compensation:** Call `dhdSetEffectorMass()` at startup with the configured effector mass so the DHD SDK provides gravity compensation.
5. **Beam break safety field:** (spec pending)
