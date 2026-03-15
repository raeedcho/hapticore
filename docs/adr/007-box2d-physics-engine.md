# ADR-007: Box2D as the embedded 2D physics engine for haptic interactions

**Status:** Accepted  
**Date:** 2026-03-13  
**Context:** Several planned tasks require collision detection and rigid-body dynamics beyond what analytical force fields provide: a Tetris-like block placement task (polygon-polygon collision), an air hockey task (circle dynamics and elastic collisions), and a pivoted rod navigation task (revolute joints, swept-line collision against barriers). The haptic server needs a physics engine that can run a full simulation step within the 250 µs haptic tick budget.

## Decision

Embed Box2D v3.0 (MIT license, C library) in the C++ haptic server as the physics backend for a `PhysicsField` force field subclass. Box2D handles rigid body dynamics, collision detection, contact resolution, and joint constraints. The monkey controls a kinematic body whose position is set from the robot each tick; Box2D computes the dynamics of all other bodies and returns the reaction forces felt by the monkey.

## Alternatives considered

**Bespoke collision code per task:** Simple for axis-aligned rectangles (Tetris) or circles (air hockey), but each new task geometry requires new C++ code. The pivoted rod task — which requires revolute joints, continuous collision detection for a swept line segment, and torque propagation — would be a substantial custom implementation. Fragile and not reusable.

**FCL (Flexible Collision Library):** Focused on collision *detection* (distance queries, boolean overlap), not collision *response* (forces, impulses, friction). Excellent for motion planning; wrong tool for haptic rendering where you need forces at every tick, not just a boolean "colliding or not." Would still need a custom dynamics solver layered on top.

**Bullet Physics:** Full 3D physics engine, much heavier than Box2D. Overkill for 2D planar tasks. A typical Bullet step with even a simple scene takes 200–500 µs — too close to the 250 µs tick budget for comfort. Better suited to complex 3D environments, which are not in the current task roadmap.

**Re-introduce CHAI3D:** CHAI3D includes collision detection and haptic rendering algorithms (god-object, proxy point). But it brings a full 3D scene graph, OpenGL rendering thread, and significant build complexity (see ADR-004). The collision and haptic rendering capabilities are tightly coupled to CHAI3D's scene management rather than being usable as a standalone library.

## Rationale

Box2D v3.0 (rewritten by Erin Catto as a clean C library) handles all three planned tasks within a single `PhysicsField` implementation: convex polygon collision for Tetris, circle-circle and circle-edge for air hockey, and revolute joints with contact constraints for the pivoted rod. A Box2D step with 20–30 bodies and a dozen contacts takes ~50–100 µs, well within the 250 µs budget. The library is MIT-licensed, has ~8,800 GitHub stars, builds trivially with CMake, and has no transitive dependencies. It is one of the most battle-tested physics engines in existence (used in Angry Birds, Limbo, and thousands of other applications).

The `PhysicsField` pattern means task authors configure the physics world declaratively from Python (body shapes, masses, joints, static obstacles) via the standard command interface. They do not write C++ for new tasks unless they need a fundamentally new physics capability. The Tetris task, air hockey task, and rod task are all just different configurations of the same `PhysicsField`.

## Consequences

- Box2D is 2D only. If a future task requires 3D rigid body dynamics (e.g., manipulating a 3D object in the full delta.3 workspace), a different engine (Bullet, MuJoCo, or a custom 3D solver) would be needed. This is unlikely given the effectively planar workspace for reaching tasks.
- The `field_state` dict in `HapticState` messages must be flexible enough to carry positions and angles for an arbitrary number of bodies. The current design uses a generic dict, which accommodates this.
- Contact forces from Box2D are constraint-based (not penalty-based), which produces more realistic rigid contact feel than spring-based penalty forces. However, the force magnitudes may need scaling and damping tuning to feel natural through the delta.3's force range (~20 N max).
- Box2D's contact listener callbacks run synchronously during the world step, so the `PhysicsField` can extract per-contact-point forces for detailed haptic rendering (e.g., feeling different contact normals as a piece slides along a surface).
- Adding Box2D to the CMake build via CPM.cmake is a one-line addition. No build complexity concern.
