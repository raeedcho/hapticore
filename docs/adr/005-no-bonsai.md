# ADR-005: Python + ZeroMQ over Bonsai for system coordination

**Status:** Accepted  
**Date:** 2026-03-11  
**Context:** Bonsai (bonsai-rx.org) is a reactive visual programming framework widely used in rodent neuroscience for coordinating hardware I/O. It has packages for SpikeGLX, ONIX/Neuropixels, NI-DAQ, and visual stimuli (BonVision). Should we use it as our coordination layer?

## Decision

Do not use Bonsai. Coordinate all components through Python processes communicating via ZeroMQ.

## Rationale

Bonsai's strengths (reactive dataflow, hardware driver ecosystem, BonVision stimuli) are offset by critical gaps for this specific rig:

1. **No Ripple/Grapevine package exists.** Ripple provides APIs only for MATLAB (xippmex) and Python (xipppy). Integrating with Bonsai would require a custom C# package wrapping the Ripple SDK or a ZeroMQ bridge to a separate Python process — adding a third language (C#) and the bridge complexity we're trying to avoid.

2. **No Force Dimensions package exists.** The haptic robot would need either custom C# driver work or the same bridge approach. The robot's native interface is C/C++ and Python.

3. **Adds a third language.** The lab has Python and MATLAB expertise. Bonsai is C#/.NET. Custom operators, debugging, and extending the system all require C# fluency that the team doesn't have and doesn't want to invest in.

4. **Reactive programming learning curve.** Users consistently report difficulty mapping imperative trial logic onto reactive observable patterns. Task state machines (if/then branching, conditional timeouts, error recovery) are more naturally expressed as `transitions` state graphs than as `SelectMany`/`TakeUntil` chains.

5. **Windows-only for production.** Linux support via Mono is experimental with known UI bugs.

6. **No workflow unit testing.** Visual workflow graphs cannot be programmatically tested. C# extensions can be tested with .NET testing, but the workflow itself is tested only by running it.

Bonsai remains viable as a future addition for Neuropixels streaming (via ONIX hardware) if sub-millisecond closed-loop from Neuropixels data is needed. The ZeroMQ architecture allows plugging in a Bonsai process alongside the existing Python processes without rebuilding anything.

## Consequences

- We forgo BonVision's shader-based stimulus rendering (handles gratings, sparse noise, VR environments). PsychoPy covers our stimulus needs (targets, cursors, shapes) but lacks BonVision's advanced visual neuroscience primitives.
- We forgo Bonsai's Harp device integration (hardware-timestamped behavioral peripherals). We use a Teensy for sync and event codes instead.
- If the lab later adopts ONIX hardware for Neuropixels, a Bonsai process can be added to the ZeroMQ topology as a Tier 3 subscriber/publisher.
