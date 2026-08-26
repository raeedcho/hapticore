**Status:** Accepted
**Date:** 2026-08-26

# ADR-017: Optional device capabilities declared in rig config

## Context

A Novint Falcon was added as a desktop development device so that real force feedback is available on a laptop, rather than only the mouse-driven mock backend (`configs/rig/dev-mouse.yaml`). The Falcon uses the same Force Dimension SDK as the delta.3, so a single `dhd_real.cpp` drives both.

Not every SDK-supported device implements every SDK call. Verified against SDK 3.17.7 on macOS/arm64 during Falcon bringup:

- `drdOpen()` succeeds and `dhdGetSystemName()` returns `Falcon`.
- `drdAutoInit()` fails with `operation not available` (`DHD_ERROR_NOT_AVAILABLE`). The SDK's own `bin/autoinit` demo reports the device, then fails at the initialization step.
- Plain DHD operation — position reads and force output — works correctly (verified with the SDK's `bin/torus` demo, then with the full server running `center_out` at reduced scale).

`main.cpp` originally treated both DRD auto-calibration failure and gravity-compensation failure as fatal at startup. That is correct for the delta.3, where running an uncalibrated device yields inaccurate `dhdGetPosition()` values and incorrect gravity-compensation forces, and where silently continuing would corrupt behavioural data. It is wrong as a universal rule.

The first attempt at a fix inferred capability at runtime: `DhdReal::calibrate()` would return success when `drdIsSupported()` reported no DRD layer, on the reasoning that a device with nothing to calibrate has not failed to calibrate.  This was rejected for two independent reasons:

1. **It was unreachable on the device it was written for.** `calibrate()` has exactly one call site, `main.cpp:210`, guarded by `if (auto_calibrate && !dhd->calibrate())`. Short-circuit evaluation means a config that disables calibration already prevents the call, so the inference never ran on the Falcon path.
2. **It downgraded a rig safety check.** If a delta.3 ever reports no DRD support — transient USB enumeration, firmware state, the wrong device on the bus — returning success converts a startup abort into silently running an uncalibrated device. That trades a loud failure for silent data corruption on the recording rig, which is the wrong direction.

More generally, runtime inference is unreliable here because the SDK's capability query and the SDK's behaviour can disagree: a device may report a capability through `drdIsSupported()` and still reject the corresponding call.  Only the operator, at bringup, reliably knows what a given device implements.

## Decision

Optional SDK capabilities are declared per device in the rig config. The server never infers them from device type or from SDK capability queries.

Two fields on `DhdConfig`, both defaulting to `True` so existing rig configs are unchanged:

- `auto_calibrate: bool = True` — passed to the server as `--no-calibrate` when `False`.
- `gravity_compensation: bool = True` — passed as `--no-gravity-comp` when `False`.

Semantics:

- **When a capability is declared absent, the server skips the SDK call entirely** rather than calling it with a "disabled" argument. A device that does not implement `dhdSetGravityCompensation()` returns an error for either argument, so calling it with `DHD_OFF` would reintroduce the same failure.
- **When a capability is declared present and the call fails, startup aborts.** This is unchanged from the previous behaviour and preserves the rig's loud-failure property.
- **`dhd_real.cpp` contains no device-type branching.** `dhdGetSystemType()` and device-name comparisons are not used for control flow. `DhdReal::calibrate()` still returns `false` when `drdIsSupported()` reports no DRD layer, because on a rig configured with `auto_calibrate: true` that indicates a real fault.
- **Dependent capabilities are validated at config load.** `effector_mass_kg` combined with `gravity_compensation: false` is rejected by a `DhdConfig` validator, since effector mass exists only to feed gravity compensation.

`configs/rig/desktop-falcon.yaml` sets both flags `False` and omits `effector_mass_kg`. `configs/rig/rig2.yaml` is untouched.

## Consequences

- A single binary and a single `dhd_real.cpp` serve both devices. Adding a third Force Dimension device is a config file, not a code change.
- The rig config is authoritative on device capability. This is the only approach that works when the SDK's capability query disagrees with the SDK's behaviour.
- Misconfiguration fails loudly at startup rather than silently degrading.  Declaring `auto_calibrate: true` on a device that cannot calibrate aborts with the SDK's own error string.
- The cost is that adding a device requires knowing which capabilities it implements, discovered empirically at bringup rather than queried programmatically. The macOS section of `cpp/haptic_server/BUILDING.md` records the procedure: run the SDK's prebuilt demos in `$FD_SDK_DIR/bin` to separate "device not supported" from "capability not supported" before touching hapticore.
- **Whether the Falcon implements `dhdSetGravityCompensation()` was not determined.** It was declared absent as a precaution, since the handle is light and gravity compensation is a comfort feature on a desktop device rather than a safety requirement. If bringup later shows it is supported, set `gravity_compensation: true` and supply `effector_mass_kg`; no code change is needed.
- Force ceilings and workspace extent are also device properties, but they are configuration values rather than capability toggles and already live in the rig config (`force_limit_n`). They follow the same config-over-branches principle through a different mechanism.
- This ADR covers optional *SDK capabilities* only. Device-dependent task geometry — the Falcon's ~10 cm workspace against the delta.3's ~±18 cm — is a separate concern, currently handled by an explicit override layer (`configs/overrides/falcon-scale.yaml`) applied through `--extra-config`, and expected to be superseded by a rig-level workspace scale factor.
