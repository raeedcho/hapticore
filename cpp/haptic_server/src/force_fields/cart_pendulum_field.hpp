#pragma once
#include "force_field.hpp"
#include <cmath>

class CartPendulumField : public ForceField {
public:
    Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) override;
    std::string name() const override;
    bool update_params(const msgpack::object& params) override;
    void pack_state(msgpack::packer<msgpack::sbuffer>& pk) const override;
    void reset() override;

    // Accessors for testing
    double phi() const { return phi_; }
    double phi_dot() const { return phi_dot_; }
    bool spilled() const { return spilled_; }
    double filtered_accel() const { return filtered_accel_; }

    // Test-only setters for initial conditions
    void set_initial_state(double phi, double phi_dot) {
        phi_ = phi;
        phi_dot_ = phi_dot;
    }

private:
    // Pendulum state
    double phi_ = 0.0;        // angle (0 = hanging straight down)
    double phi_dot_ = 0.0;    // angular velocity
    bool spilled_ = false;
    double vel_x_prev_ = 0.0; // previous x velocity for acceleration estimate
    double cup_x_ = 0.0;      // last cup position

    // Acceleration filter state
    double filtered_accel_ = 0.0;
    double accel_filter_alpha_ = 1.0 - std::exp(-2.0 * M_PI * 30.0 * 0.00025); // ~30 Hz at 4 kHz

    // Parameters
    double ball_mass_ = 0.6;
    double cup_mass_ = 2.4;
    double pendulum_length_ = 0.3;
    double gravity_ = 9.81;
    double angular_damping_ = 0.1;
    double spill_threshold_ = 1.5708; // π/2
    bool cup_inertia_enabled_ = true;
    double accel_filter_hz_ = 30.0;

    // RK4 helper: returns [phi_dot, phi_ddot]
    struct State { double phi; double phi_dot; };
    State derivatives(const State& s, double x_accel) const;

    static double compute_alpha(double cutoff_hz, double dt) {
        return 1.0 - std::exp(-2.0 * M_PI * cutoff_hz * dt);
    }
};
