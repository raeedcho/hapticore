# ADR-015: Config-driven backend factory pattern and per-role package layout

**Status:** Accepted
**Date:** 2026-04-25

## Context

Early in the project, hardware interfaces were instantiated ad-hoc throughout the CLI and task code. As the number of backends grew (real device, mock, mouse-driven), duplicated selection logic appeared in multiple call sites. Separately, the package layout had an asymmetry: a generic `backends/` umbrella package sat next to per-role directories (`display/`, `sync/`), creating two organizational schemes pulling in different directions.

Two questions needed answers:

1. **Factory pattern:** How should callers select between real, mock, and alternative backends without repeating `if cfg.backend == "mock": ...` everywhere?
2. **Package layout:** Where should implementations live — in the umbrella `backends/` or in per-role directories?

## Decision

### Factory pattern

Each role that supports backend selection exposes a single factory function named `make_<role>_interface()`. The factory reads `<Role>Config.backend`, performs a lazy import of the chosen implementation class, constructs and returns an instance. Callers never import mock or real classes directly — they call the factory.

- `hapticore.haptic.make_haptic_interface(cfg, zmq_cfg, *, context, mouse_queue)` → `HapticInterface`
- `hapticore.display.make_display_interface(cfg, zmq_cfg, *, publisher, mouse_queue)` → context manager yielding `DisplayInterface`

Display's factory is a context manager (`@contextmanager`) because the production backend (`DisplayProcess`) is a subprocess with a lifecycle; the mock has no lifecycle. The context manager shape gives callers uniform teardown regardless of backend.

Recording does **not** get a factory. Multiple recording systems can be active simultaneously (Ripple + SpikeGLX + LSL), so the single-selection factory pattern does not apply. Recording backends are instantiated directly by the caller.

Sync does **not** yet have a factory (as of this ADR). `make_sync_interface()` will be added in Phase 5C when the wiring is ready; it will live in `hapticore.sync`.

### Package layout

Collapse to a single scheme: each role owns one self-contained directory under `hapticore/`, holding all of that role's implementations (real, mock, alternative), supporting modules, and the role factory. The `backends/` umbrella is eliminated.

End-state layout:

```
hapticore/
├── haptic/         # HapticClient, MockHapticInterface, MouseHapticInterface, make_haptic_interface
├── display/        # DisplayProcess, DisplayClient, MockDisplay, make_display_interface, factory.py, mock.py
├── sync/           # SyncProcess, TeensySync, MockSync  (make_sync_interface added in Phase 5C)
└── recording/      # MockNeuralRecording  (real implementations added in Phase 5B)
```

Each role's `__init__.py` re-exports the public names for that role. Internal submodules (`client.py`, `mock.py`, `factory.py`, `mouse.py`) are importable directly but are not part of the stable public API.

## Consequences

- **Uniform call sites.** All callers instantiate a backend with one import and one call. Backend selection logic lives in exactly one place per role.
- **Mocks co-locate with real implementations.** `hapticore/display/mock.py` is next to `hapticore/display/client.py`. When the real implementation changes its interface, the mock is trivially findable.
- **Phase 5C lands cleanly.** `make_sync_interface()` goes in `hapticore/sync/__init__.py` next to `TeensySync` and `SyncProcess`. No new directory, no second restructure.
- **No `backends/` backwards-compat shim.** The rename is hard. Import sites were updated atomically with the restructure. Downstream code that depended on `hapticore.backends` must update its imports.
- **Display factory is a context manager; haptic factory is not.** This asymmetry reflects the underlying lifecycle difference (subprocess vs. object). Both are intentional and stable.
