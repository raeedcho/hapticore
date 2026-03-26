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

    cup_x_ = pos[0];
    double vel_x = vel[0];

    // Estimate cup acceleration via finite difference + EMA low-pass filter.
    // Use kNominalDt here to match the sample period used to precompute accel_filter_alpha_.
    double raw_accel = (vel_x - vel_x_prev_) / kNominalDt;
    vel_x_prev_ = vel_x;
    filtered_accel_ = accel_filter_alpha_ * raw_accel + (1.0 - accel_filter_alpha_) * filtered_accel_;

    // RK4 integration of [phi, phi_dot]
    State s0{phi_, phi_dot_};

    State k1 = derivatives(s0, filtered_accel_);
    State s1{s0.phi + 0.5 * dt * k1.phi, s0.phi_dot + 0.5 * dt * k1.phi_dot};

    State k2 = derivatives(s1, filtered_accel_);
    State s2{s0.phi + 0.5 * dt * k2.phi, s0.phi_dot + 0.5 * dt * k2.phi_dot};

    State k3 = derivatives(s2, filtered_accel_);
    State s3{s0.phi + dt * k3.phi, s0.phi_dot + dt * k3.phi_dot};

    State k4 = derivatives(s3, filtered_accel_);

    phi_ = s0.phi + (dt / 6.0) * (k1.phi + 2.0 * k2.phi + 2.0 * k3.phi + k4.phi);
    phi_dot_ = s0.phi_dot + (dt / 6.0) * (k1.phi_dot + 2.0 * k2.phi_dot
                                           + 2.0 * k3.phi_dot + k4.phi_dot);

    // Spill detection
    if (std::abs(phi_) > spill_threshold_) {
        spilled_ = true;
    }

    // Compute φ̈ at the updated state for reaction force
    double phi_ddot = (-gravity_ * std::sin(phi_) - filtered_accel_ * std::cos(phi_)
                       - angular_damping_ * phi_dot_) / pendulum_length_;

    // Reaction force on cup: F_reaction = m_b * L * (φ̈·cos(φ) - φ̇²·sin(φ))
    double f_reaction = ball_mass_ * pendulum_length_
                        * (phi_ddot * std::cos(phi_) - phi_dot_ * phi_dot_ * std::sin(phi_));

    double force_x = f_reaction;
    if (cup_inertia_enabled_) {
        force_x += cup_mass_ * filtered_accel_;
    }

    return {force_x, 0.0, 0.0};
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
    bool new_inertia = cup_inertia_enabled_;
    double new_filter_hz = accel_filter_hz_;
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
        } else if (key_str == "cup_inertia_enabled") {
            if (!haptic::try_get_bool(val, new_inertia)) return false;
            has_any = true;
        } else if (key_str == "accel_filter_hz") {
            if (!haptic::try_get_double(val, new_filter_hz)) return false;
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
    if (new_filter_hz < 5.0 || new_filter_hz > 200.0) return false;

    ball_mass_ = new_ball_mass;
    cup_mass_ = new_cup_mass;
    pendulum_length_ = new_length;
    gravity_ = new_gravity;
    angular_damping_ = new_damping;
    spill_threshold_ = new_threshold;
    cup_inertia_enabled_ = new_inertia;
    accel_filter_hz_ = new_filter_hz;
    accel_filter_alpha_ = compute_alpha(accel_filter_hz_, kNominalDt);
    return true;
}

void CartPendulumField::pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
    pk.pack_map(6);
    pk.pack("phi");       pk.pack(phi_);
    pk.pack("phi_dot");   pk.pack(phi_dot_);
    pk.pack("spilled");   pk.pack(spilled_);
    pk.pack("cup_x");     pk.pack(cup_x_);
    pk.pack("ball_x");    pk.pack(cup_x_ + pendulum_length_ * std::sin(phi_));
    pk.pack("ball_y");    pk.pack(-pendulum_length_ * std::cos(phi_));
}

void CartPendulumField::reset() {
    phi_ = 0.0;
    phi_dot_ = 0.0;
    spilled_ = false;
    vel_x_prev_ = 0.0;
    filtered_accel_ = 0.0;
}
