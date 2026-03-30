# ADR-010: Virtual coupling for mass rendering on an impedance-type haptic device

**Status:** Accepted
**Date:** 2026-03-27
**Context:** The cart-pendulum task requires the monkey to feel a virtual cup with a mass of approximately 2.4 kg. The original task was implemented on a FCS HapticMaster, which is an admittance-type device. Our system uses a Force Dimension delta.3, which is an impedance-type device. These two device architectures render virtual mass through fundamentally different mechanisms, and the delta.3 cannot stably render 2.4 kg of virtual mass using the same approach that worked on the HapticMaster.

## Decision

Render virtual mass through a virtual coupling architecture: simulate the cart-pendulum dynamics internally as a coupled 4D ODE system and connect the simulation to the physical device through a spring-damper coupler. The device never computes or commands `F = M·a` directly.

## Background: impedance vs. admittance haptic devices

Haptic devices fall into two architectural categories based on what they measure and what they command:

**Impedance-type devices** (e.g., Force Dimension delta.3, Phantom, Falcon) measure motion (position/velocity via encoders) and command force (via back-drivable actuators). The control loop is: read position → compute force from virtual environment → apply force. These devices have low mechanical impedance — the handle moves freely when unpowered, with minimal friction and inertia. This makes them excellent for rendering springs, dampers, textures, and free-space motion, where crispness and transparency matter.

**Admittance-type devices** (e.g., FCS HapticMaster, large industrial robot arms) measure force (via a force/torque sensor at the handle) and command motion (via high-gear-ratio, non-back-drivable actuators with stiff position servos). The control loop is: read force from user → compute motion from virtual dynamics → command position. These devices have high mechanical impedance — the handle is effectively locked in place when unpowered. They excel at rendering large virtual masses, stiff constraints, and high forces.

## Why the delta.3 cannot directly render 2.4 kg of virtual mass

On the HapticMaster, rendering a 2.4 kg cup is straightforward. The device measures the user's applied force, feeds it into the simulation (`a = F/M`), integrates to get position, and servos the handle to that position. The mass lives inside the forward dynamics — no differentiation is needed.

On the delta.3, the situation is inverted. To make the handle *feel* massive, the device must command `F = M·a`, which requires estimating acceleration by differentiating sampled velocity data. At 4 kHz (dt = 0.25 ms), dividing by dt amplifies sensor noise by ~4000×. Even with EMA low-pass filtering (30 Hz cutoff), the resulting acceleration estimate is noisy enough that multiplying by a large mass produces force commands that inject energy into the system faster than the device's physical damping can absorb it.

The Colgate-Schenkel passivity condition for sampled-data haptic systems gives `b > K·T/2 + B`, where `b` is device physical damping, `K` is virtual stiffness, `B` is virtual damping, and `T` is the sample period. For virtual mass rendering, Colonnese and Okamura (2015) showed that the passivity-guaranteed renderable mass is on the order of micrograms (`M < b·T/2`). The less conservative coupled-stability boundary — which accounts for the human operator's hand impedance — permits masses up to roughly 2× the device's own effective mass (Gil et al. 2020). For the delta.3 (effective end-effector mass ~0.3–0.5 kg), this caps out around 0.6–1.0 kg, well below the 2.4 kg needed for the task.

The cart-pendulum coupling makes the problem worse: the pendulum's gravitational restoring force adds effective virtual stiffness, and combined spring-mass rendering has a smaller stability region than pure mass rendering alone.

## Alternatives considered

**Direct `F = M·a` with aggressive filtering:** A low-pass Butterworth filter at 20–40 Hz on the acceleration estimate expands the stable mass range from micrograms to hundreds of grams, but this is still far below 2.4 kg. It also degrades the bandwidth and accuracy of the rendered mass at frequencies above the filter cutoff. Hardware testing confirmed that even with EMA filtering, the cart-pendulum oscillated wildly with `cup_mass = 2.4` and `cup_inertia_enabled = true`. Increasing angular damping to 10× the default and reducing ball mass to near zero did not help — the instability is in the cup inertia feedback loop, not the pendulum dynamics.

**Time-domain passivity control (TDPC):** A Passivity Observer tracks cumulative energy exchange. When net energy generation is detected, an adaptive damping term is injected to restore passivity. This is a valuable safety net but introduces perceptible viscous drag during active interaction. It mitigates instability after the fact rather than preventing the architectural cause.

**Symplectic Euler (cart) + RK4 (pendulum) with one-tick lag:** An earlier design split the integration into two separate steps — symplectic Euler for the cart position/velocity, RK4 for the pendulum angle/angular velocity — with the cart-pendulum coupling broken by using the previous tick's cart acceleration for the pendulum reaction force. This was rejected because: (a) symplectic Euler is not truly symplectic for non-separable Hamiltonians like the cart-pendulum (the cross-term `m·L·ẋ·φ̇·cos(φ)` in the kinetic energy couples the two subsystems), so energy preservation is not guaranteed; and (b) the one-tick lag introduces a small phase error in the coupling at every tick. The Lagrangian formulation (see below) resolves the circularity algebraically, making the split unnecessary.

**Exploit device inertia subtraction:** The user already feels the device's own mass (~0.4 kg) when accelerating the handle, so only M_additional = 2.4 − 0.4 = 2.0 kg needs active rendering. This helps but does not close the gap.

## Rationale

The virtual coupling architecture sidesteps the fundamental problem by never requiring the device to compute `F = M·a`:

1. The full cart-pendulum dynamics are derived from the system Lagrangian, which gives a coupled 4D ODE in `[x_cart, v_cart, φ, φ̇]`. The Euler-Lagrange equation for the cart coordinate resolves the circularity between cart acceleration and pendulum angular acceleration algebraically: `ẍ·(M + m·sin²φ) = F_couple + m·(g·sinφ·cosφ + b·φ̇·cosφ + L·φ̇²·sinφ)`, where the effective mass `M + m·sin²φ` absorbs the pendulum coupling terms. This avoids needing to estimate cart acceleration from sensor data or break the coupling with a time lag.
2. The 4D system is integrated with a single RK4 pass per haptic tick. RK4's O(dt⁴) local error is negligible at dt = 0.25 ms, and energy conservation tests confirm < 0.5% drift over 10,000 ticks with zero damping. The unified integration is both more accurate and simpler than split-integrator approaches.
3. The haptic device connects to the simulated cart through a spring-damper virtual coupler: `F_device = K_vc · (x_sim − x_dev) + B_vc · (v_sim − v_dev)`.
4. The coupler parameters (default K_vc = 800 N/m, B_vc = 2 N·s/m) are moderate by design. The coupling bandwidth `ω_couple = √(K_vc/M_cup) ≈ 18 rad/s` is well above the pendulum's natural frequency `ω_pend = √(g/L) ≈ 5.7 rad/s` (with default parameters), so the pendulum dynamics transmit clearly through the coupler. Strict Colgate-Schenkel passivity of the coupler (`b > K_vc·T/2 + B_vc`) is not formally guaranteed — the strict passive stiffness limit for the delta.3 at 4 kHz is only ~800 N/m with B = 0, and virtual damping makes it harder, not easier. In practice, the human operator's hand impedance (1–10 N·s/m of damping) and the device's own end-effector mass provide substantial stability margin beyond the strict passivity boundary. The heuristic caps (K_vc ≤ 5000 N/m, B_vc ≤ 50 N·s/m) keep the parameters in a conservative region of the coupled-stability space.

The mass is rendered *implicitly* through the simulation dynamics, not *explicitly* through force feedback from differentiated position. The device only ever renders spring and damper forces — which impedance-type devices handle well within their stability envelope. The user feels the cup's inertia because the simulation's position response lags behind rapid hand movements proportionally to the virtual mass, and the coupler spring transmits this lag as a restoring force.

## Consequences

- The `CartPendulumField` simulates the coupled cart-pendulum dynamics internally (Lagrangian-derived 4D RK4) and returns a coupling spring-damper force to the device, rather than computing reaction forces that include a mass term directly.
- The coupler introduces a small compliance between the device handle and the simulated cart position. At the default coupling stiffness this is nearly imperceptible for slow movements, but during fast accelerations the simulation visibly lags the handle — this lag *is* the inertial feel, and is physically correct (analogous to the compliance in a real grip on a massive object).
- The display process should render the cup at the *simulated* cart position (`cup_x` in `field_state`), not the device handle position. This ensures visual-haptic consistency — the visual cup moves with the simulated dynamics, and any position gap between handle and cup corresponds to the coupling stretch the user is actively feeling.
- The 4D RK4 integration and the coupler force computation both run within the 250 µs haptic tick budget. For a 4D system, the four evaluations of the derivative function per RK4 step are negligible compared to the DHD USB round-trip.
- Future force fields that need to render virtual mass or inertia should follow the same virtual coupling pattern: simulate dynamics internally, couple to the device through a spring-damper. The Lagrangian formulation generalizes to any coupled mechanical system.
- The `PhysicsField` (Box2D, ADR-007) already uses a conceptually similar pattern — the monkey controls a kinematic body, and Box2D computes reaction forces from constraints. Virtual coupling generalizes this to continuous inertial dynamics where the controlled body has significant mass.

## Key references

- Adams, R.J. and Hannaford, B. (1999). Stable haptic interaction with virtual environments. *IEEE Trans. Robotics and Automation*, 15(3).
- Colgate, J.E. and Schenkel, G. (1997). Passivity of a class of sampled-data systems. *J. Robotic Systems*, 14(1).
- Colonnese, N. and Okamura, A. (2015). M-Width: Stability, noise characterization, and accuracy of rendering virtual mass. *IJRR*, 34(6).
- Gil, J.J., Ugartemendia, A., and Díaz, I. (2020). Stability analysis and user perception of haptic rendering combining virtual elastic, viscous, and inertial effects. *Applied Sciences*, 10(24).
