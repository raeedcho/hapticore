# ADR-004: Force Dimension DHD SDK directly, not CHAI3D scene graph

**Status:** Accepted  
**Date:** 2026-03-11  
**Context:** The predecessor hapticEnvironment codebase uses CHAI3D (v2.3/v3.0) as a middleware layer between the application and the Force Dimension device. CHAI3D provides a scene graph, collision detection, and a haptic rendering pipeline. Should we continue using it?

## Decision

Use the Force Dimension DHD SDK directly for device communication. Do not use CHAI3D's scene graph, collision detection, or rendering pipeline.

## Rationale

In the parameterized-force-field architecture (see ADR-002), the C++ haptic server evaluates mathematical force functions — not scene-graph collisions. CHAI3D's value proposition is haptic rendering of 3D virtual environments with mesh collision detection. Our force fields are analytical functions (springs, dampers, pendulum ODEs) that don't benefit from collision detection or scene graph traversal. CHAI3D adds ~50K lines of dependency code, an OpenGL rendering thread we don't use (visual display is handled by PsychoPy in a separate process), and CMake build complexity for features we bypass entirely.

The DHD SDK is a thin C library (~20 functions) that provides `dhdGetPosition()`, `dhdGetLinearVelocity()`, `dhdSetForce()`, and device management. It compiles trivially and has no transitive dependencies beyond libusb.

The predecessor djoshea/haptic-control system used CHAI3D because it also rendered 3D graphics from the C++ process. Our architecture renders visuals in a separate PsychoPy process, so the C++ server has no graphics responsibilities.

## Consequences

- Custom collision detection must be implemented if any task requires it (e.g., haptic walls, surfaces). For simple planar boundaries, the WorkspaceLimitField handles this analytically. Complex 3D geometry would require re-introducing a collision library.
- The C++ codebase is much smaller and builds faster without CHAI3D.
- Developers familiar with CHAI3D's API will need to learn the DHD SDK directly, though it is simpler.
