# ADR-011: Display unit convention — meters everywhere, DisplayProcess converts

**Status:** Accepted  
**Date:** 2026-04-13  
**Context:** The haptic server, force fields, task parameters, and condition dicts all work in meters (SI). The PsychoPy display window uses `units="cm"`. For haptic-state-derived visuals (cursor position, cart_pendulum/physics_world field-state renderers), the `DisplayProcess` converts meters → cm. However, the unit convention for discrete display commands (`show_stimulus`, `update_scene`) was undocumented, leading to a bug where the `CenterOutTask` passed meter-valued radii and positions directly to `show_stimulus()`, producing stimuli ~100× too small.

Additionally, the `display_scale` config parameter (default 100.0) was conflating two independent concerns: a workspace rescaling factor (calibration/tuning) and the physical meters→cm conversion (a fixed property of PsychoPy's unit system). This made the parameter's meaning ambiguous — `display_scale=1.0` ("no scaling") produced invisible stimuli, and `display_scale=200.0` couldn't be distinguished between "200 cm/m" and "2× workspace magnification at 100 cm/m."

## Decision

### Meters everywhere

All spatial values throughout the system use **meters (SI)** — including parameters in discrete display commands (`"show"`, `"update_scene"`), `DisplayConfig` fields (`cursor_radius`, `display_offset`), and condition dicts. The `DisplayProcess` is the **sole conversion boundary**: it converts all spatial parameters from meters to PsychoPy's cm before rendering. No other component performs unit conversion. Task authors work entirely in meters.

This mirrors ADR-008 (coordinate convention), which established the principle of converting once at the lowest hardware-facing layer so everything above works in a single frame.

### Separate workspace scaling from unit conversion

`display_scale` becomes a **dimensionless meters→meters workspace multiplier** (default 1.0). It controls how large the haptic workspace appears on the display: 1.0 = 1:1 mapping, 2.0 = everything appears twice as large. The fixed ×100 meters→cm conversion is a private constant (`_METERS_TO_CM = 100.0`) inside the display process, not exposed in config.

`display_offset` becomes **meters** (previously cm). It specifies the display origin shift for co-location calibration, in the same units as every other spatial value.

The effective conversion for any spatial value is: `value_cm = value_m × display_scale × _METERS_TO_CM`.

### What the DisplayProcess converts

**Position-like keys** — scale × _METERS_TO_CM + offset (also converted):
- `position`: `[x_m * eff_scale + offset_cm_x, y_m * eff_scale + offset_cm_y]`
- `start`, `end`: same (for `Line` stimuli)
- `vertices`: each vertex gets the same transformation (for `Polygon`/`ShapeStim`)

**Dimension-like keys** — scale × _METERS_TO_CM only (no offset):
- `radius`, `width`, `height`, `size`, `field_size`, `dot_size`

**Non-spatial keys** — pass through unchanged:
- `color`, `opacity`, `orientation`, `text`, `font`, `image_path`, `line_width`, `n_dots`, `coherence`, `direction`, `speed`, `phase`, `contrast`, `mask`, `fill`, `type`, `sf`

This conversion applies to:
- Haptic state cursor position (frame loop)
- Field-state renderers: cart_pendulum positions, physics body positions
- `DisplayConfig.cursor_radius` (scaled when creating the cursor stimulus)
- `"show"` command params via `_convert_spatial_params()`
- `"update_scene"` command params via `_convert_spatial_params()`

### What uses meters (SI) — everything above the DisplayProcess

- `ParamSpec` values with `unit="m"` (e.g., `target_radius`, `target_distance`)
- Condition dict positions (e.g., `target_position: [0.08, 0.0]`)
- Haptic command parameters (spring centers, workspace bounds)
- `HapticState.position`, `HapticState.velocity`, `HapticState.force`
- `field_state` positions (cup_x, ball_x, body positions)
- All arguments to `display.show_stimulus()` and `display.update_scene()`
- `DisplayConfig.cursor_radius` (default 0.005 m)
- `DisplayConfig.display_offset` (default [0.0, 0.0] m)
- `DisplayConfig.display_scale` (dimensionless, default 1.0)

### What uses cm — only display-internal constants

- Cart-pendulum visual constants (`_CUP_HALF_WIDTH_CM`, `_BALL_RADIUS_CM`) — display-internal dimensions not exposed to task code, defined directly in cm because they have no haptic-space analog
- `_METERS_TO_CM = 100.0` — private constant in the display process module

## Alternatives considered

**Pass-through with task-side conversion.** Document that display commands must use cm and require task authors to multiply by `display_scale` before every `show_stimulus()` call. This was attempted and failed within one PR iteration: the reference `CenterOutTask` and the task authoring guide examples both passed meter values despite the documentation saying otherwise. The approach requires every task author to remember the conversion at every call site, with no compiler or runtime error if they forget — the stimuli simply render invisibly. Rejected.

**Keep `display_scale` as a combined meters→cm factor (default 100.0).** This works if you never need to think about what the parameter means — but `display_scale=1.0` ("I don't want any scaling") producing invisible stimuli is a footgun, and the parameter can't cleanly express "2× workspace magnification" (you'd set 200.0, which reads as "200 cm per meter"). Separating the workspace multiplier from the physical unit conversion makes both meanings explicit and independently configurable. Rejected.

**No convention (status quo before this ADR).** The lack of a documented convention caused the original bug. Rejected.

## Consequences

- Task authors pass meters to `show_stimulus()` and `update_scene()`, the same units used everywhere else. No manual conversion needed.
- `display_scale` default changes from 100.0 to 1.0. Existing rig configs that set `display_scale` must divide by 100. At this stage of the project, no deployed rig configs exist, so migration cost is zero.
- `display_offset` units change from cm to meters. The current default `[0.0, 0.0]` is unaffected. Any non-default values must be divided by 100.
- New stimulus types that introduce spatial parameters must add the key name to `_convert_spatial_params()`. The function has a clear separation: positions (scale + offset) vs dimensions (scale only) vs non-spatial (pass-through).
- `update_stimulus()` in `stimulus_factory.py` operates below the conversion boundary — its docstring should note that values are in cm (post-conversion).
- The coding agent instructions (`.github/copilot-instructions.md`) must include this convention.
- If PsychoPy is ever replaced by a different rendering backend with different units, only `_METERS_TO_CM` changes — no user-facing config or task code is affected.
