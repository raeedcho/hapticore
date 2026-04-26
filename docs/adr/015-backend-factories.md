# ADR-015: Config-driven backend selection via factory functions

**Status:** Accepted
**Date:** 2026-04-25
**Context:** As Hapticore grew to support multiple implementations of each task-facing interface — `MockHapticInterface`, `MouseHapticInterface`, and `HapticClient` for haptic; `MockDisplay` and `DisplayClient`+`DisplayProcess` for display — selection between them was happening through a mix of CLI flags (`--input mouse`, `--display`) and ad-hoc construction inside the CLI's `_simulate` function. The real `HapticClient` had no CLI path at all. There was no consistent place in the resolved session config that recorded "which backend ran this session," and the CLI was the only construction site that knew how to instantiate each backend. Phase 5C will introduce a third interface in the same shape (`SyncInterface` with `MockSync` and `TeensySync`), so the right time to settle the pattern was after the second case (display) and before the third.

## Decision

Each single-selection task-facing interface (haptic, display, sync) is constructed via a `make_<role>_interface()` factory function living in that role's package. Selection is config-driven: the corresponding `*Config` model has a `backend: Literal[...]` field whose value names the implementation. The CLI calls the factory with the resolved config; it does not branch on backend types itself.

Concretely, today:

- `HapticConfig.backend: Literal["dhd", "mock", "mouse"]` → `make_haptic_interface(cfg, zmq_cfg, *, context=None, mouse_queue=None) -> HapticInterface`, in `hapticore.haptic`.
- `DisplayConfig.backend: Literal["psychopy", "mock"]` → `make_display_interface(cfg, zmq_cfg, *, publisher, mouse_queue=None) -> Iterator[DisplayInterface]` (a `@contextmanager` because the `psychopy` path owns subprocess lifecycle), in `hapticore.display`.

Phase 5C will add:

- `SyncConfig.backend: Literal["mock", "teensy"]` (already exists in the schema; the field was renamed from `transport` in the backend-rename PR) → `make_sync_interface(cfg, zmq_cfg, *, ...)` in `hapticore.sync`, mirroring the display factory's context-manager shape because the `teensy` path owns a `SyncProcess` subprocess.

### Recording is the explicit exception

`RecordingConfig` does **not** use a `backend:` discriminator and **no `make_recording_interface()` factory exists**. Multiple recording systems (Ripple via xipppy, SpikeGLX, LSL) can be active simultaneously in a single session, which is fundamentally different from haptic/display/sync where exactly one implementation runs at a time. Recording uses presence-by-nesting instead: `RecordingConfig.ripple: RippleRecordingConfig | None = None`, `RecordingConfig.lsl_enabled: bool`. A non-`None` block (or `True` flag) means that system is active for the session.

### Lifecycle handling

The factory's job is construction, not lifecycle. Lifecycle stays at the call site, expressed via context-manager idiom in three forms depending on what the backend owns:

- The constructed object is itself a context manager — `HapticClient.__enter__`/`__exit__` start and stop the heartbeat and SUB drain threads.
- The factory is a context manager — `make_display_interface` yields a `DisplayInterface` and shuts down the underlying `DisplayProcess` on exit.
- The backend owns nothing — `MockHapticInterface`, `MockDisplay`, `MockSync` are bare returns from their factories. The CLI wraps these in `contextlib.nullcontext()` so the same `with ... as iface:` block works uniformly.

The `HapticInterface` Protocol does not include `connect`/`close` methods; mock implementations should not need to implement no-op lifecycle methods. The asymmetry between resource-owning and resource-free backends is real and informative — erasing it for cosmetic uniformity would obscure which backends own what.

## Rationale

**Config-driven selection makes the session config receipt complete.** The resolved `ExperimentConfig` is JSON-serialized into the session directory at session start (per ADR-009). When backend selection lives in CLI flags, the receipt is incomplete — reconstructing what ran requires both the config file and the shell history. With selection in config, the receipt is the single source of truth for which implementation produced the data.

**Single construction site.** The CLI's `_run` function constructs each interface with one line per role: `make_haptic_interface(...)`, `with make_display_interface(...) as display:`. Adding a backend (a fourth haptic implementation, say) means adding a `Literal` value, a config block if the backend has parameters, and a branch in the factory — no CLI changes. Future contributors do not need to read `_run` to understand where backends are wired.

**The pattern matches `SyncConfig.transport` (now `backend`) which already existed.** When `SyncConfig.transport: Literal["mock", "teensy"]` was added with its `_populate_selected_transport` validator, a precedent was set: discriminator field + `Literal` + auto-populating nested config block + `model_validator`. Haptic and display follow the same pattern. We considered Pydantic's tagged-union `Field(discriminator=...)` machinery but rejected it because the manual `Literal + nested block + model_validator` pattern is consistent with what already existed, easier for Copilot to extend by analogy, and produces clearer Pydantic error messages on invalid values.

**Factories return constructed objects, not configured-but-uninitialized objects.** `make_haptic_interface(cfg)` returns a fully-formed `HapticClient` — but does not call `connect()`. The decision to defer `connect()` to the caller means the factory is free of side effects, trivially unit-testable (no real server required), and lifecycle ownership stays with whoever entered the `with` block. A "factory that also connects" would have to handle connection failures at a layer that doesn't know what to do with them.

**Recording's exception is principled, not an oversight.** Forcing recording into the `backend:` mold would require either picking one system as "the" recording (dropping multi-recording capability) or inventing a `backends: list[...]` form that's unique among the discriminators (broken symmetry hidden under cosmetic similarity). Modeling activation as "presence of a config block" matches the actual domain — Ripple, SpikeGLX, and LSL are independent decisions, not mutually exclusive choices.

**Why a context manager for the display factory rather than a Protocol method.** Display lifecycle lives on `DisplayProcess` (the subprocess), not on `DisplayClient` (the ZMQ proxy). Putting `__enter__`/`__exit__` on `DisplayClient` would conflate "this is the network proxy" with "this owns a subprocess." Making the factory itself the context manager places lifecycle responsibility in the right place — in code that knows about both halves of the real-display pair.

**Per-role package layout, no umbrella `backends/` directory.** Each role has its own self-contained directory under `hapticore/` that holds its real and mock implementations, supporting modules (subprocesses, helpers, ZMQ proxies), and the role's factory function. The earlier alternative — grouping all backend implementations under a single `hapticore/backends/` umbrella — was tried briefly and discarded because it created two organizational schemes (umbrella for "things satisfying a Protocol" plus per-role directories for "everything else") that pulled against each other. The role's directory is the single home for all the role's code; the role's `__init__.py` is the public API.

## Consequences

- Adding a new backend for an existing role: define a new `Literal` value, optionally add a nested config block, add a branch in the factory function inside the role's `__init__.py` (or `factory.py` for display). No CLI changes, no Protocol changes, no test infrastructure changes.
- Adding a new role (a hypothetical `RewardInterface`, say): add the Protocol to `core/interfaces.py`, the config block to `core/config.py`, create a new `hapticore/<role>/` package containing implementations and a factory, and add one line in the CLI to call it.
- Backends live in per-role packages: `hapticore/haptic/`, `hapticore/display/`, `hapticore/sync/`, and `hapticore/recording/`. Each role's directory holds its real and mock implementations plus the role's factory function (where one exists — sync's lands in Phase 5C; recording does not have one). The `hapticore/backends/` umbrella package no longer exists; an earlier version briefly grouped backends under that umbrella before the per-role split.
- The `_run` function in `cli/__init__.py` is the only place that knows about `make_haptic_interface`, `make_display_interface`, `contextlib.nullcontext()` for stateless mocks, and the `mouse_queue` and `EventPublisher` plumbing the factories need. If a second orchestrator ever appears (for example, a long-running daemon mode), it will replicate this construction logic — accepted as cost.
- Recording stays out of the factory pattern indefinitely. Phase 5B constructs `RippleRecording` directly in whatever orchestration code runs sessions; LSL is similarly direct. If a future requirement for "all-or-nothing recording" appears (i.e. a true single-selection model for recording), revisit this decision; until then, do not migrate for symmetry alone.
- Test method names and config-block names settle on `backend` as the project-wide vocabulary. The values inside (`dhd`, `psychopy`, `teensy`, `mock`, `mouse`) are role-specific; the field name is uniform.
- Future contributors who want to revisit "why doesn't recording use a backend?" should land here. The answer is in the Decision and Rationale sections; do not migrate without revisiting them.
