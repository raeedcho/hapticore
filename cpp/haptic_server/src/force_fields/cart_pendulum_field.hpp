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
    double x_sim() const { return x_sim_; }
    double v_sim() const { return v_sim_; }

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
    double cup_x_ = 0.0;      // simulated cart position (for pack_state)

    // Virtual coupling simulation state
    double x_sim_ = 0.0;      // simulated cart position
    double v_sim_ = 0.0;      // simulated cart velocity
    double cup_x_dev_ = 0.0;  // last device x position (for coupling_stretch)
    bool first_tick_ = true;   // sync simulation to device on first tick

    // Parameters
    double ball_mass_ = 0.6;
    double cup_mass_ = 2.4;
    double pendulum_length_ = 0.3;
    double gravity_ = 9.81;
    double angular_damping_ = 0.1;
    double spill_threshold_ = 1.5708; // π/2
    double coupling_stiffness_ = 800.0;
    double coupling_damping_ = 2.0;
};
