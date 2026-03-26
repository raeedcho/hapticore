# ADR-008: Lab coordinate convention and DHD SDK remap

**Status:** Accepted  
**Date:** 2026-03-25  
**Context:** The Force Dimension delta.3 SDK uses a coordinate frame that does not match the lab's preferred convention. The DHD SDK frame (from the delta manual, figure 7) is X=forward/backward (depth toward operator), Y=left/right (horizontal), Z=up/down (vertical). The lab convention — chosen to match the monitor orientation, planned eye tracking, and how the team thinks about the workspace — is X=left/right (horizontal), Y=up/down (vertical), Z=forward/backward (depth). This mismatch was discovered during interactive testing when an offset spring center at `[+30mm, 0, 0]` described as "rightward" actually pulled forward (toward the operator), because DHD X is the depth axis.

## Decision

Remap coordinates in `DhdReal` — the lowest hardware abstraction layer — so that every component above it (force fields, protocol messages, Python clients, PsychoPy display) works in the lab frame transparently.

**Mapping (applied in `DhdReal` only):**

| Lab axis | Physical direction | DHD SDK axis |
|----------|--------------------|-------------|
| X | Left/right (horizontal) | Y |
| Y | Up/down (vertical) | Z |
| Z | Forward/backward (depth) | X |

Reading positions/velocities from the SDK:

```
Lab X = DHD Y
Lab Y = DHD Z
Lab Z = DHD X
```

Writing forces to the SDK (inverse):

```
DHD X = Lab Z
DHD Y = Lab X
DHD Z = Lab Y
```

**What does NOT remap:**

- `DhdMock` — the mock has no physical frame; it operates in lab frame by definition. Adding a remap would be both unnecessary and confusing for tests.
- Force field code — fields receive positions in lab frame and return forces in lab frame. No changes needed.
- Protocol messages — `position`, `velocity`, and `force` arrays are `[x, y, z]` in lab frame after the remap.

## Alternatives considered

**Remap in force fields:** Each force field would need its own remap logic, which is error-prone and violates the single-responsibility principle. Bugs would silently produce wrong-axis forces.

**Remap in Python:** Would leave the C++ state messages in DHD frame, requiring every Python consumer to remap independently. Worse, the forces computed by C++ force fields would still use DHD frame positions, producing physically incorrect forces relative to the monitor layout.

**No remap (document the DHD frame and work with it):** Every developer, every task, and every display component would need to remember the mapping. This is the status quo and already caused confusion.

## Rationale

The remap belongs at the lowest layer (`DhdReal`) because:

1. It is applied exactly once — no risk of double-remapping or missed remaps.
2. All code above `DhdReal` (force fields, state publisher, command handler, Python clients) sees a consistent lab frame without any changes.
3. Gravity compensation is unaffected — the SDK applies gravity compensation torques internally in `dhdSetForce()` based on the current joint configuration. The remap only affects the application-level force vector before it reaches the SDK, and the SDK adds its own gravity compensation on top.

## Consequences

- Lab X (+) is physically rightward, Lab Y (+) is physically upward, Lab Z (+) is physically toward the operator.
- All position, velocity, and force values in state messages and command parameters use the lab frame.
- Task authors and display code can treat X as horizontal and Y as vertical, matching the monitor.
- The `cart_pendulum` field's use of `pos[0]` as the horizontal swing axis is now correct without further changes.
- The `workspace_limit` bounds `x`, `y`, `z` correspond to horizontal, vertical, and depth limits respectively.
- Anyone reading DHD SDK documentation must remember that the SDK's native frame differs from the lab frame. This ADR and the protocol documentation serve as the reference.
