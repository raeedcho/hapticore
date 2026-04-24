# ADR-009: pydantic-settings with layered YAML composition

**Status:** Accepted  
**Date:** 2026-03-25  
**Context:** The configuration system used a single flat YAML file per experiment, loaded via `yaml.safe_load()` and validated with `ExperimentConfig(BaseModel).model_validate()`. Every monkey running the same task on the same rig got a separate config file duplicating all hardware settings. A change to a rig's monitor distance or Teensy port required editing every config file referencing that rig. As the lab scales to multiple animals, tasks, and rigs, this duplication becomes a maintenance hazard.

## Decision

Switch `ExperimentConfig` from `BaseModel` to `BaseSettings` (from `pydantic-settings`) and restructure `configs/` into composable layers merged at load time with deep merge.

Source priority (highest wins):

1. CLI arguments (via `cli_parse_args` parameter to `load_config()`)
2. Constructor kwargs (`overrides` dict)
3. Environment variables (`HAPTICORE_` prefix, `__` double-underscore delimiter)
4. YAML files (layered, later files override earlier ones)
5. Field defaults in the Pydantic models

The `configs/` directory is organized into layers:

```
configs/
├── rig/           # hardware: display, haptic workspace, ZMQ, sync
├── subject/       # subject_id, species, implant_info
├── task/          # task_class, params, conditions, block structure
└── *.yaml         # top-level experiment name + any overrides
```

## Rationale

- **Eliminates config duplication across animals and tasks.** A rig's hardware settings are defined once in `configs/rig/default.yaml`. Subject identity is defined once per animal. Task parameters are defined once per task. Composing them is a single `load_config()` call.
- **Enables per-rig environment variable overrides without editing YAML.** Setting `HAPTICORE_SYNC__TEENSY_PORT=/dev/ttyACM1` on a specific workstation overrides the default port without touching version-controlled files.
- **Provides CLI overrides for one-off sessions.** A quick parameter tweak (e.g., shorter hold time for training) does not require creating a new YAML file.
- **pydantic-settings is already a declared dependency** (`pydantic-settings>=2.0` in `pyproject.toml`) and was not previously used.
- **Backward compatible.** `BaseSettings` is a subclass of `BaseModel`, so `model_dump()`, `model_dump_json()`, and `model_validate()` all work identically. The `SessionManager`'s JSON serialization of resolved configs is unaffected. All nested config models remain plain `BaseModel`.

## Consequences

- `load_config()` now accepts `*yaml_paths` (variadic) instead of a single `yaml_path`. Call sites passing a single path still work.
- `load_session_config()` requires `rig`, `subject`, and `task` as named arguments. Omitting any one raises a `TypeError` at call time — catching missing-layer bugs before config loading. This is the primary entry point for real experiment sessions.
- The CLI `run` command requires layered mode (`--rig`, `--subject`, `--task`, `--experiment-name`). Single-file configs remain supported via `load_config(path)` from Python for scripting and tests; the CLI's previous `--config` flag was removed once the layered path was stable.
- Environment variables with the `HAPTICORE_` prefix are now automatically read. Stale env vars in a shell session could silently override YAML values. Mitigated by using `__` (double underscore) as the nested delimiter to avoid ambiguity with field names containing single underscores.
- The `pydantic-settings` YAML source requires the `pyyaml` package, which is already a dependency. The version constraint is `pydantic-settings[yaml]>=2.3` to ensure YAML and CLI source support.
