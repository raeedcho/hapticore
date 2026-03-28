# ADR-009: Virtual coupling for mass rendering on an impedance-type haptic device

**Status:** Accepted  
**Date:** 2026-03-27  
**Context:** The cart-pendulum task requires the monkey to feel a virtual cup with a mass of approximately 2.4 kg. The original task was implemented on a FCS HapticMaster, which is an admittance-type device. Our system uses a Force Dimension delta.3, which is an impedance-type device. These two device architectures render virtual mass through fundamentally different mechanisms, and the delta.3 cannot stably render 2.4 kg of virtual mass using the same approach that worked on the HapticMaster.

## Decision

Render virtual mass through a virtual coupling architecture: simulate the cart-pendulum dynamics in an implicit integrator and connect the simulation to the physical device through a spring-damper coupler. The device never computes or commands `F = M·a` directly.

## Background: impedance vs. admittance haptic devices

Haptic devices fall into two architectural categories based on what they measure and what they command:

**Impedance-type devices** (e.g., Force Dimension delta.3, Phantom, Falcon) measure motion (position/velocity via encoders) and command force (via back-drivable actuators). The control loop is: read position → compute force from virtual environment → apply force. These devices have low mechanical impedance — the handle moves freely when unpowered, with minimal friction and inertia. This makes them excellent for rendering springs, dampers, textures, and free-space motion, where crispness and transparency matter.

**Admittance-type devices** (e.g., FCS HapticMaster, large industrial robot arms) measure force (via a force/torque sensor at the handle) and command motion (via high-gear-ratio, non-back-drivable actuators with stiff position servos). The control loop is: read force from user → compute motion from virtual dynamics → command position. These devices have high mechanical impedance — the handle is effectively locked in place when unpowered. They excel at rendering large virtual masses, stiff constraints, and high forces.

## Why the delta.3 cannot directly render 2.4 kg of virtual mass

On the HapticMaster, rendering a 2.4 kg cup is straightforward. The device measures the user's applied force, feeds it into the simulation (`a = F/M`), integrates to get position, and servos the handle to that position. The mass lives inside the forward dynamics — no differentiation is needed.

On the delta.3, the situation is inverted. To make the handle *feel* massive, the device must command `F = M·a`, which requires estimating acceleration by double-differentiating sampled position data. At 4 kHz (dt = 0.25 ms), dividing by dt² amplifies encoder noise by ~16,000,000×. Even with aggressive low-pass filtering, the passivity-guaranteed renderable mass is on the order of micrograms, and the less conservative coupled-stability boundary (Gil et al. 2020) caps out at roughly 2× the device's own effective mass (~0.4–0.5 kg for the delta.3). At M = 2.4 kg, the mass-to-device-mass ratio of ~5–12× substantially exceeds this limit.

The cart-pendulum coupling makes the problem worse: the pendulum's gravitational restoring force adds effective virtual stiffness, and combined spring-mass rendering has a smaller stability region than pure mass rendering alone.

## Alternatives considered

**Direct `F = M·a` with aggressive filtering:** A low-pass Butterworth filter at 20–40 Hz on the acceleration estimate expands the stable mass range from micrograms to hundreds of grams, but this is still far below 2.4 kg. It also degrades the bandwidth and accuracy of the rendered mass at frequencies above the filter cutoff.

**Time-domain passivity control (TDPC):** A Passivity Observer tracks cumulative energy exchange. When net energy generation is detected, an adaptive damping term is injected to restore passivity. This is a valuable safety net but introduces perceptible viscous drag during active interaction. It mitigates instability after the fact rather than preventing the architectural cause.

**Exploit device inertia subtraction:** The user already feels the device's own mass (~0.4 kg) when accelerating the handle, so only M_additional = 2.4 − 0.4 = 2.0 kg needs active rendering. This helps but does not close the gap.

## Rationale

The virtual coupling architecture sidesteps the fundamental problem by never requiring the device to compute `F = M·a`:

1. The full cart-pendulum dynamics are simulated in a separate integration step using an implicit (backward Euler) integrator, which guarantees discrete-time energy conservation of the simulation.
2. The haptic device connects to the simulated cart position through a spring-damper virtual coupler: `F_device = K_vc · (x_sim − x_device) + B_vc · (v_sim − v_device)`.
3. The coupler parameters are chosen per the Adams-Hannaford two-port passivity criterion: `B_vc ≥ T · K_vc / 2`.

The mass is rendered *implicitly* through the simulation dynamics, not *explicitly* through force feedback from differentiated position. The device only ever renders spring and damper forces — which impedance-type devices handle well within their stability envelope. The user feels the cup's inertia because the simulation's position response lags behind rapid hand movements proportionally to the virtual mass, and the coupler spring transmits this lag as a restoring force.

This is the standard solution in the haptics literature for rendering virtual environments with significant inertia on impedance-type devices (Adams and Hannaford 1999, Colonnese and Okamura 2015).

## Consequences

- The `CartPendulumField` is restructured to simulate dynamics internally and couple to the device through spring-damper forces, rather than computing reaction forces that include a mass term directly.
- The coupler introduces a small compliance between the device handle and the simulated cart position. At high coupler stiffness this is imperceptible, but it is a fundamental tradeoff of the architecture — perfect position tracking between device and simulation is not achievable while maintaining passivity.
- The simulation integration and the coupler force computation both run within the 250 µs haptic tick budget. Implicit integration is more expensive per step than RK4, but for a low-dimensional system (cart + pendulum) the cost is negligible.
- Future force fields that need to render virtual mass or inertia (e.g., a heavy object manipulation task) should follow the same pattern: simulate dynamics internally, couple to the device through a passive spring-damper.
- The `PhysicsField` (Box2D, ADR-007) already uses a similar pattern — the monkey controls a kinematic body, and Box2D computes reaction forces from constraints. Virtual coupling generalizes this to continuous inertial dynamics.

## Key references

- Adams, R.J. and Hannaford, B. (1999). Stable haptic interaction with virtual environments. *IEEE Trans. Robotics and Automation*, 15(3).
- Colgate, J.E. and Schenkel, G. (1997). Passivity of a class of sampled-data systems. *J. Robotic Systems*, 14(1).
- Colonnese, N. and Okamura, A. (2015). M-Width: Stability, noise characterization, and accuracy of rendering virtual mass. *IJRR*, 34(6).
- Gil, J.J., Ugartemendia, A., and Díaz, I. (2020). Stability boundary for haptic rendering of virtual mass. *Applied Sciences*, 10(8).
