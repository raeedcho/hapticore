# ADR-012: Separate repository for camera acquisition

## Status

Proposed

## Context

Hapticore integrates five synchronized Blackfly S cameras for markerless motion capture of primate kinematics. The camera system requires the Spinnaker SDK (PySpin), GPU-accelerated video encoding (NVENC), and dedicated USB3 controller hardware. It runs on a dedicated camera PC, separate from the haptic control rig.

ADR-006 (monorepo) established that tightly coupled subsystems belong in a single repository. The question is whether the camera acquisition system is tightly coupled to Hapticore or loosely coupled.

## Decision

Camera acquisition lives in a **separate repository** (`raeedcho/markerless-capture` or similar), outside the Hapticore monorepo.

## Rationale

The camera system differs from other Hapticore subsystems along every axis that ADR-006 used to justify the monorepo:

**Different deployment target.** Camera acquisition runs on a dedicated camera PC with its own GPU, USB3 controller cards, and Spinnaker SDK installation. It never runs on the haptic control rig. The Hapticore monorepo targets the haptic rig (Ubuntu) and developer laptops (macOS).

**No shared wire format.** The camera system does not participate in Hapticore's ZeroMQ + msgpack message bus. It receives a hardware TTL trigger on a GPIO pin and writes video files + timestamp CSVs to disk. The interface contract between the camera system and Hapticore is "files on disk with timestamps" — aligned post-hoc via the shared sync signal, identical to how SpikeGLX data is aligned.

**Different dependency stack.** PySpin (Spinnaker SDK), FFMPEG with NVENC, and USB3 controller drivers have no overlap with Hapticore's pixi-managed Python environment. Pulling these into Hapticore's `pixi.toml` would create installation complexity on machines that don't have cameras attached and would likely cause CI failures on GitHub Actions runners that lack Spinnaker.

**Independent release cadence.** Camera acquisition can be updated, restarted, or replaced without touching the experiment control system. Swapping the acquisition pipeline (e.g., from PySpin to Bonsai) should not require changes to the Hapticore repo.

**Different pose estimation tooling.** Downstream markerless tracking (DeepLabCut + Anipose) belongs with the acquisition pipeline, not with haptic control. These tools have heavy ML dependencies (PyTorch, CUDA) that should not pollute the Hapticore environment.

### What stays in the Hapticore monorepo

The **Teensy firmware** remains in Hapticore (`firmware/teensy/`) because it is tightly coupled: it receives serial commands from `SyncProcess`, generates event codes defined by Hapticore's task configs, and controls reward delivery triggered by Hapticore's state machine. The firmware, the event code map, and the `SyncProcess` Python code must evolve atomically.

## Consequences

- Camera acquisition and Hapticore have no code-level dependencies. Integration is purely through hardware signals (TTL sync) and file conventions (timestamp formats, directory structure).
- The camera repo needs its own documentation for sync wiring, Spinnaker configuration, and video file format conventions.
- Session management conventions (directory naming, timestamp formats) must be documented in both repos to ensure post-hoc alignment works. A shared convention document or a minimal shared Python package for session directory layout may be warranted if this becomes fragile.
- The camera repo can be reused in other lab setups that don't use Hapticore.
