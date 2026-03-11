# ADR-003: Custom Python framework over MonkeyLogic, Ex, or PsychoPy alone

**Status:** Accepted  
**Date:** 2026-03-11  
**Context:** Several established systems exist for primate behavioral task control: NIMH MonkeyLogic (MATLAB), the Ex system (MATLAB, multi-PC), and PsychoPy (Python). We need to choose or build the task control layer.

## Decision

Build a custom Python task controller using the `transitions` library for state machines, with PsychoPy as the visual stimulus backend running in a separate process.

## Alternatives considered

**NIMH MonkeyLogic:** The most widely used system in primate neurophysiology. Mature Scene Framework with 50+ adapters, simulation mode, active maintenance. But: MATLAB-only, Windows-only, NI-DAQ-centric I/O layer (not Ripple-native), closed-source core components (MGL, DAQ toolbox are MEX binaries), and the tight frame-locked rendering loop leaves little room for custom UDP/ZeroMQ communication with the haptic robot.

**Ex system (SmithLabNeuro):** Native Ripple/xippmex integration, distributed multi-PC architecture. But: requires 2+ dedicated Linux PCs, very small community (~9 GitHub stars), no formal state machine framework, no simulation mode, minimal documentation.

**PsychoPy alone (no state machine library):** PsychoPy's routine/trial-handler model works for linear trial structures but becomes unwieldy for the rich conditional state graphs typical of primate motor tasks (fixation breaks, correction trials, variable error handling per state). Task logic ends up as nested if/elif chains.

## Rationale

A custom Python framework with `transitions` provides: (1) declarative state machines where the task structure is a data structure, not control flow, enabling auto-generated diagrams and unit testing of transition logic; (2) PsychoPy's validated sub-millisecond visual timing as a rendering backend without being constrained by its experiment-management model; (3) full access to the Python scientific ecosystem (numpy, scipy, xipppy, SpikeGLX SDK) without MATLAB licensing; (4) Protocol-based hardware abstraction enabling mock testing of complete task logic without hardware; (5) strong AI-assisted development support (Python is the best-supported language for LLM coding tools).

The `transitions` library was chosen over `python-statemachine` because it has a larger community (6,400 vs 1,200 GitHub stars), supports hierarchical states, and has a Graphviz diagram extension. Both are single-maintainer projects; the interface abstraction in BaseTask allows swapping between them.

## Consequences

- No off-the-shelf task library for common primate paradigms (fixation monitoring, saccade detection, reward scheduling). These must be built as BaseTask utilities.
- New lab members must learn the transitions library's callback model. Mitigated by the template task and task authoring guide.
- The system is not directly comparable to MonkeyLogic for collaborators who use it. Behavioral data formats may need translation layers.
