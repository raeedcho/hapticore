# ADR-002: Parameterized force fields bridge the Python–C++ rate mismatch

**Status:** Accepted  
**Date:** 2026-03-11  
**Context:** The haptic force loop must run at 4 kHz (250 µs per cycle) for stable rendering, but the Python task controller runs at ~100 Hz (~10 ms per cycle). How should forces be communicated?

## Decision

Python sends *force field parameters* (e.g., spring stiffness, target position, pendulum length) via ZeroMQ commands. The C++ haptic thread evaluates `F = f(position, velocity, params)` at 4 kHz using the latest parameters. Python never sends raw force values.

## Alternatives considered

**Direct force commands from Python:** Python computes forces at its control rate and sends them to the C++ server. The server applies the latest received force. Problems: 100 Hz force updates cause perceptible force discontinuities and potential instability for stiff virtual surfaces (rule of thumb: stable haptic rendering requires ≥1 kHz updates). A single dropped or delayed Python message produces a stale force for 10+ ms.

**Python sends target forces at high rate via shared memory:** Possible on the same machine, but requires Python to achieve near-kHz rates, which is unreliable due to GIL and garbage collection. Tight coupling between Python and C++ timing makes the system fragile.

## Rationale

Parameterized fields cleanly separate *what* the haptic environment should feel like (decided by Python at task logic rate) from *how* to render it (executed by C++ at haptic rate). If Python stalls for 50 ms, forces remain physically correct because the C++ thread keeps evaluating the same field. The task controller operates on a higher level of abstraction — "set a spring pulling toward the start position" rather than "apply 2.3 N at 37° right now" — which is both safer and easier to reason about. Novel dynamics (cup-and-ball, viscous curls, elastic obstacles) are implemented as C++ ForceField subclasses that run at the full haptic rate.

## Consequences

- Adding a fundamentally new haptic interaction requires writing a C++ ForceField subclass, rebuilding the server, and registering the field type. This is higher friction than modifying a Python script, but it ensures the physics are always evaluated at the correct rate.
- The command interface between Python and C++ must support arbitrary parameter dicts. We use msgpack maps for this, not a fixed struct.
- The haptic server's safety layer (force clamping, communication timeout) operates on the C++ side, independent of Python liveness.
