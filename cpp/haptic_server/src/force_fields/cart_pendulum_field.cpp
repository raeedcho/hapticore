#include "cart_pendulum_field.hpp"
#include "msgpack_helpers.hpp"
#include <cmath>

CartPendulumField::State CartPendulumField::derivatives(const State& s, double x_accel) const {
    // φ̈ = (-g·sin(φ) - ẍ·cos(φ) - b·φ̇) / L
    double phi_ddot = (-gravity_ * std::sin(s.phi) - x_accel * std::cos(s.phi)
                       - angular_damping_ * s.phi_dot) / pendulum_length_;
    return {s.phi_dot, phi_ddot};
}

Vec3 CartPendulumField::compute(const Vec3& pos, const Vec3& vel, double dt) {
    if (dt <= 0.0) return {0.0, 0.0, 0.0};

    double x_dev = pos[0];
    double v_dev = vel[0];
    cup_x_dev_ = x_dev;  // store for pack_state coupling_stretch

    // On first tick after reset, sync simulation to device position
    if (first_tick_) {
        x_sim_ = x_dev;
        v_sim_ = v_dev;
        first_tick_ = false;
    }

    // 1. Coupling force on the simulated cart from the device.
    //    Computed once from the state at the start of the tick; held constant
    //    during the RK4 sub-steps (the device state doesn't change within a tick).
    double f_couple = coupling_stiffness_ * (x_dev - x_sim_)
                    + coupling_damping_ * (v_dev - v_sim_);

    // 2. Integrate the full 4D state [x, v, phi, phi_dot] with RK4.
    //    Cart acceleration is computed algebraically to avoid circularity:
    //    a = (F_couple + m*(-g*sin*cos - b*pd*cos - L*pd^2*sin)) / (M + m*cos^2)
    struct FullState { double x; double v; double phi; double phi_dot; };

    auto full_derivs = [&](const FullState& s) -> FullState {
        double cp = std::cos(s.phi);
        double sp = std::sin(s.phi);
        // Coupling force uses start-of-tick device state (constant over sub-steps)
        double fc = coupling_stiffness_ * (x_dev - s.x)
                  + coupling_damping_ * (v_dev - s.v);
        double pend_terms = ball_mass_
            * (-gravity_ * sp * cp
               - angular_damping_ * s.phi_dot * cp
               - pendulum_length_ * s.phi_dot * s.phi_dot * sp);
        double eff_mass = cup_mass_ + ball_mass_ * cp * cp;
        double a = (fc + pend_terms) / eff_mass;
        double phi_ddot = (-gravity_ * sp - a * cp
                           - angular_damping_ * s.phi_dot) / pendulum_length_;
        return {s.v, a, s.phi_dot, phi_ddot};
    };

    FullState s0{x_sim_, v_sim_, phi_, phi_dot_};
    FullState k1 = full_derivs(s0);
    FullState s1{s0.x + 0.5*dt*k1.x, s0.v + 0.5*dt*k1.v,
                 s0.phi + 0.5*dt*k1.phi, s0.phi_dot + 0.5*dt*k1.phi_dot};
    FullState k2 = full_derivs(s1);
    FullState s2{s0.x + 0.5*dt*k2.x, s0.v + 0.5*dt*k2.v,
                 s0.phi + 0.5*dt*k2.phi, s0.phi_dot + 0.5*dt*k2.phi_dot};
    FullState k3 = full_derivs(s2);
    FullState s3{s0.x + dt*k3.x, s0.v + dt*k3.v,
                 s0.phi + dt*k3.phi, s0.phi_dot + dt*k3.phi_dot};
    FullState k4 = full_derivs(s3);

    x_sim_   = s0.x       + (dt/6.0)*(k1.x       + 2*k2.x       + 2*k3.x       + k4.x);
    v_sim_   = s0.v       + (dt/6.0)*(k1.v       + 2*k2.v       + 2*k3.v       + k4.v);
    phi_     = s0.phi     + (dt/6.0)*(k1.phi     + 2*k2.phi     + 2*k3.phi     + k4.phi);
    phi_dot_ = s0.phi_dot + (dt/6.0)*(k1.phi_dot + 2*k2.phi_dot + 2*k3.phi_dot + k4.phi_dot);

    // 3. Spill detection
    if (std::abs(phi_) > spill_threshold_) {
        spilled_ = true;
    }

    // 4. Store simulated cup position for state packing
    cup_x_ = x_sim_;

    // 5. Return coupling force on device (Newton's 3rd law)
    return {-f_couple, 0.0, 0.0};
}

std::string CartPendulumField::name() const {
    return "cart_pendulum";
}

bool CartPendulumField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;

    auto map = params.via.map;
    double new_ball_mass = ball_mass_;
    double new_cup_mass = cup_mass_;
    double new_length = pendulum_length_;
    double new_gravity = gravity_;
    double new_damping = angular_damping_;
    double new_threshold = spill_threshold_;
    double new_coupling_stiffness = coupling_stiffness_;
    double new_coupling_damping = coupling_damping_;
    bool has_any = false;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;

        if (key.type != msgpack::type::STR) continue;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "ball_mass") {
            if (!haptic::try_get_double(val, new_ball_mass)) return false;
            has_any = true;
        } else if (key_str == "cup_mass") {
            if (!haptic::try_get_double(val, new_cup_mass)) return false;
            has_any = true;
        } else if (key_str == "pendulum_length") {
            if (!haptic::try_get_double(val, new_length)) return false;
            has_any = true;
        } else if (key_str == "gravity") {
            if (!haptic::try_get_double(val, new_gravity)) return false;
            has_any = true;
        } else if (key_str == "angular_damping") {
            if (!haptic::try_get_double(val, new_damping)) return false;
            has_any = true;
        } else if (key_str == "spill_threshold") {
            if (!haptic::try_get_double(val, new_threshold)) return false;
            has_any = true;
        } else if (key_str == "coupling_stiffness") {
            if (!haptic::try_get_double(val, new_coupling_stiffness)) return false;
            has_any = true;
        } else if (key_str == "coupling_damping") {
            if (!haptic::try_get_double(val, new_coupling_damping)) return false;
            has_any = true;
        }
    }

    if (!has_any) return false;

    // Validate constraints
    if (new_ball_mass <= 0.0) return false;
    if (new_cup_mass <= 0.0) return false;
    if (new_length <= 0.0) return false;
    if (new_gravity <= 0.0) return false;
    if (new_damping < 0.0) return false;
    if (new_threshold <= 0.0) return false;
    if (new_coupling_stiffness <= 0.0 || new_coupling_stiffness > 5000.0) return false;
    if (new_coupling_damping < 0.0 || new_coupling_damping > 50.0) return false;

    ball_mass_ = new_ball_mass;
    cup_mass_ = new_cup_mass;
    pendulum_length_ = new_length;
    gravity_ = new_gravity;
    angular_damping_ = new_damping;
    spill_threshold_ = new_threshold;
    coupling_stiffness_ = new_coupling_stiffness;
    coupling_damping_ = new_coupling_damping;
    return true;
}

void CartPendulumField::pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
    pk.pack_map(7);
    pk.pack("phi");              pk.pack(phi_);
    pk.pack("phi_dot");          pk.pack(phi_dot_);
    pk.pack("spilled");          pk.pack(spilled_);
    pk.pack("cup_x");            pk.pack(cup_x_);
    pk.pack("ball_x");           pk.pack(x_sim_ + pendulum_length_ * std::sin(phi_));
    pk.pack("ball_y");           pk.pack(-pendulum_length_ * std::cos(phi_));
    pk.pack("coupling_stretch"); pk.pack(cup_x_dev_ - x_sim_);
}

void CartPendulumField::reset() {
    phi_ = 0.0;
    phi_dot_ = 0.0;
    spilled_ = false;
    x_sim_ = 0.0;
    v_sim_ = 0.0;
    cup_x_ = 0.0;
    cup_x_dev_ = 0.0;
    first_tick_ = true;  // will re-sync to device on next compute()
}
