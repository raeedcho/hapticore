#include <functional>
#include <vector>
#include <set>
#include <string>
#include <gtest/gtest.h>
#include <cmath>
#include <msgpack.hpp>
#include "force_fields/null_field.hpp"
#include "force_fields/constant_field.hpp"
#include "force_fields/spring_damper_field.hpp"
#include "force_fields/workspace_limit_field.hpp"
#include "force_fields/cart_pendulum_field.hpp"
#include "force_fields/channel_field.hpp"
#include "force_fields/composite_field.hpp"
#include "force_fields/physics_field.hpp"
#include "force_fields/field_factory.hpp"

// Helper to create a msgpack object from a map
static msgpack::object_handle pack_and_unpack(const std::function<void(msgpack::packer<msgpack::sbuffer>&)>& fn) {
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    fn(pk);
    return msgpack::unpack(sbuf.data(), sbuf.size());
}

// ==================== NullField Tests ====================

TEST(NullFieldTest, ComputeReturnsZero) {
    NullField field;
    Vec3 pos = {1.0, 2.0, 3.0};
    Vec3 vel = {0.5, -0.3, 0.1};
    Vec3 force = field.compute(pos, vel, 0.00025);
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
    EXPECT_DOUBLE_EQ(force[2], 0.0);
}

TEST(NullFieldTest, NameIsNull) {
    NullField field;
    EXPECT_EQ(field.name(), "null");
}

TEST(NullFieldTest, UpdateParamsAcceptsAnything) {
    NullField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(0);
    });
    EXPECT_TRUE(field.update_params(oh.get()));
}

// ==================== ConstantField Tests ====================

TEST(ConstantFieldTest, ReturnsConfiguredForce) {
    ConstantField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("force");
        pk.pack_array(3);
        pk.pack(1.0); pk.pack(-2.5); pk.pack(3.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {10.0, 20.0, 30.0};
    Vec3 vel = {5.0, -3.0, 1.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    EXPECT_DOUBLE_EQ(force[0], 1.0);
    EXPECT_DOUBLE_EQ(force[1], -2.5);
    EXPECT_DOUBLE_EQ(force[2], 3.0);
}

TEST(ConstantFieldTest, NameIsConstant) {
    ConstantField field;
    EXPECT_EQ(field.name(), "constant");
}

TEST(ConstantFieldTest, MissingForceKeyReturnsFalse) {
    ConstantField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ConstantFieldTest, WrongForceTypeReturnsFalse) {
    ConstantField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("force");
        pk.pack("not_an_array");
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

// ==================== SpringDamperField Tests ====================

TEST(SpringDamperFieldTest, SpringForceComputation) {
    SpringDamperField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("stiffness"); pk.pack(100.0);
        pk.pack("damping");   pk.pack(0.0);
        pk.pack("center");
        pk.pack_array(3);
        pk.pack(0.0); pk.pack(0.0); pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.1, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    // F = -K * (pos - center) = -100 * 0.1 = -10
    EXPECT_NEAR(force[0], -10.0, 1e-10);
    EXPECT_NEAR(force[1], 0.0, 1e-10);
    EXPECT_NEAR(force[2], 0.0, 1e-10);
}

TEST(SpringDamperFieldTest, DampingForce) {
    SpringDamperField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("stiffness"); pk.pack(0.0);
        pk.pack("damping");   pk.pack(10.0);
        pk.pack("center");
        pk.pack_array(3);
        pk.pack(0.0); pk.pack(0.0); pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {1.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    // F = -B * vel = -10 * 1.0 = -10
    EXPECT_NEAR(force[0], -10.0, 1e-10);
    EXPECT_NEAR(force[1], 0.0, 1e-10);
    EXPECT_NEAR(force[2], 0.0, 1e-10);
}

TEST(SpringDamperFieldTest, RejectsHighStiffness) {
    SpringDamperField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("stiffness"); pk.pack(4000.0);
        pk.pack("damping");   pk.pack(5.0);
        pk.pack("center");
        pk.pack_array(3);
        pk.pack(0.0); pk.pack(0.0); pk.pack(0.0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(SpringDamperFieldTest, AcceptsMaxStiffness) {
    SpringDamperField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("stiffness"); pk.pack(3000.0);
        pk.pack("damping");   pk.pack(5.0);
        pk.pack("center");
        pk.pack_array(3);
        pk.pack(0.0); pk.pack(0.0); pk.pack(0.0);
    });
    EXPECT_TRUE(field.update_params(oh.get()));
}

TEST(SpringDamperFieldTest, NameIsSpringDamper) {
    SpringDamperField field;
    EXPECT_EQ(field.name(), "spring_damper");
}

TEST(SpringDamperFieldTest, MissingKeysReturnsFalse) {
    SpringDamperField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(SpringDamperFieldTest, WrongValueTypeReturnsFalse) {
    SpringDamperField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("stiffness");
        pk.pack("not_a_number");
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

// ==================== WorkspaceLimitField Tests ====================

TEST(WorkspaceLimitFieldTest, InsideBoundsZeroForce) {
    WorkspaceLimitField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("bounds");
        pk.pack_map(3);
        pk.pack("x"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("y"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("z"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("stiffness"); pk.pack(2000.0);
        pk.pack("damping");   pk.pack(10.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
    EXPECT_DOUBLE_EQ(force[2], 0.0);
}

TEST(WorkspaceLimitFieldTest, OutsideBoundsRestoringForce) {
    WorkspaceLimitField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("bounds");
        pk.pack_map(3);
        pk.pack("x"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("y"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("z"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("stiffness"); pk.pack(2000.0);
        pk.pack("damping");   pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    // Position exceeds x-max of 0.15
    Vec3 pos = {0.2, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    // F = K * (max - pos) = 2000 * (0.15 - 0.2) = 2000 * (-0.05) = -100
    EXPECT_NEAR(force[0], -100.0, 1e-10);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
    EXPECT_DOUBLE_EQ(force[2], 0.0);
}

TEST(WorkspaceLimitFieldTest, PackStateReportsInBounds) {
    WorkspaceLimitField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("bounds");
        pk.pack_map(3);
        pk.pack("x"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("y"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
        pk.pack("z"); pk.pack_array(2); pk.pack(-0.15); pk.pack(0.15);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    // Inside bounds
    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};
    field.compute(pos, vel, 0.00025);

    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    field.pack_state(pk);

    auto result = msgpack::unpack(sbuf.data(), sbuf.size());
    auto map = result.get().via.map;
    ASSERT_EQ(map.size, 1u);
    std::string key(map.ptr[0].key.via.str.ptr, map.ptr[0].key.via.str.size);
    EXPECT_EQ(key, "in_bounds");
    EXPECT_TRUE(map.ptr[0].val.via.boolean);
}

TEST(WorkspaceLimitFieldTest, NameIsWorkspaceLimit) {
    WorkspaceLimitField field;
    EXPECT_EQ(field.name(), "workspace_limit");
}

TEST(WorkspaceLimitFieldTest, MissingKeysReturnsFalse) {
    WorkspaceLimitField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(WorkspaceLimitFieldTest, WrongBoundsTypeReturnsFalse) {
    WorkspaceLimitField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("bounds");
        pk.pack("not_a_map");
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

// ==================== CartPendulumField Tests ====================

TEST(CartPendulumFieldTest, CouplingTracksPositionAtSteadyState) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("ball_mass");          pk.pack(0.6);
        pk.pack("cup_mass");           pk.pack(2.4);
        pk.pack("pendulum_length");    pk.pack(0.3);
        pk.pack("angular_damping");    pk.pack(0.1);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    constexpr double dt = 0.00025;
    Vec3 pos = {0.05, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    Vec3 force{0.0, 0.0, 0.0};
    for (int tick = 0; tick < 20000; ++tick) {
        force = field.compute(pos, vel, dt);
    }

    // x_sim should have converged close to 0.05
    double gap = std::abs(field.x_sim() - 0.05);
    EXPECT_LT(gap, 1e-4)
        << "Coupling gap " << gap << " should be < 1e-4 at steady state";
    // Force should be near zero at steady state
    EXPECT_NEAR(force[0], 0.0, 0.01)
        << "Force at steady state should be near zero";
}

TEST(CartPendulumFieldTest, InertialResistanceDuringAcceleration) {
    constexpr double dt = 0.00025;
    constexpr double velocity = 0.1; // m/s constant velocity

    auto run_with_mass = [&](double cup_mass) -> double {
        CartPendulumField field;
        auto oh = pack_and_unpack([&](msgpack::packer<msgpack::sbuffer>& pk) {
            pk.pack_map(3);
            pk.pack("cup_mass");        pk.pack(cup_mass);
            pk.pack("angular_damping"); pk.pack(0.1);
            pk.pack("ball_mass");       pk.pack(0.6);
        });
        field.update_params(oh.get());

        // First tick at rest to sync simulation
        field.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, dt);

        double max_opposing_force = 0.0;
        for (int tick = 1; tick <= 400; ++tick) {
            double t = tick * dt;
            Vec3 pos = {velocity * t, 0.0, 0.0};
            Vec3 vel = {velocity, 0.0, 0.0};
            Vec3 force = field.compute(pos, vel, dt);
            // Force should oppose motion direction (negative x for positive velocity)
            if (force[0] < 0.0) {
                max_opposing_force = std::max(max_opposing_force, -force[0]);
            }
        }
        return max_opposing_force;
    };

    double force_light = run_with_mass(1.0);
    double force_heavy = run_with_mass(4.0);

    // Force should oppose motion
    EXPECT_GT(force_light, 0.0) << "Should have opposing force during acceleration";
    EXPECT_GT(force_heavy, 0.0) << "Should have opposing force during acceleration";
    // Heavier cup should produce larger opposing force
    EXPECT_GT(force_heavy, force_light)
        << "Heavier cup mass should produce larger inertial resistance";
}

TEST(CartPendulumFieldTest, PendulumPeriodWithStationaryDevice) {
    CartPendulumField field;
    double L = 1.0;
    auto oh = pack_and_unpack([&](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("ball_mass");        pk.pack(1.0);
        pk.pack("pendulum_length");  pk.pack(L);
        pk.pack("gravity");          pk.pack(9.81);
        pk.pack("angular_damping");  pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    auto oh_init = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("initial_phi"); pk.pack(0.01);
    });
    ASSERT_TRUE(field.update_params(oh_init.get()));

    constexpr double dt = 0.00025;
    double T_expected = 2.0 * M_PI * std::sqrt(L / 9.81);
    int total_ticks = static_cast<int>(T_expected / dt) * 2;

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    double prev_phi = field.phi();
    std::vector<int> positive_crossings;

    for (int tick = 0; tick < total_ticks; ++tick) {
        field.compute(pos, vel, dt);
        double cur_phi = field.phi();
        if (prev_phi < 0.0 && cur_phi >= 0.0) {
            positive_crossings.push_back(tick);
        }
        prev_phi = cur_phi;
    }

    ASSERT_GE(positive_crossings.size(), 2u)
        << "Not enough zero crossings detected";

    double measured_period = (positive_crossings[1] - positive_crossings[0]) * dt;
    double error_pct = std::abs(measured_period - T_expected) / T_expected * 100.0;
    EXPECT_LT(error_pct, 2.0)
        << "Period error: " << error_pct << "% (measured=" << measured_period
        << ", expected=" << T_expected << ")";
}

TEST(CartPendulumFieldTest, PendulumForceTransmitsThroughCoupling) {
    CartPendulumField field;
    double L = 0.3;
    auto oh = pack_and_unpack([&](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("ball_mass");        pk.pack(0.6);
        pk.pack("pendulum_length");  pk.pack(L);
        pk.pack("gravity");          pk.pack(9.81);
        pk.pack("angular_damping");  pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    auto oh_init = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("initial_phi"); pk.pack(0.3);
    });
    ASSERT_TRUE(field.update_params(oh_init.get()));

    constexpr double dt = 0.00025;
    double T_pend = 2.0 * M_PI * std::sqrt(L / 9.81);
    int half_period_ticks = static_cast<int>(0.5 * T_pend / dt);

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    bool has_positive = false;
    bool has_negative = false;

    for (int tick = 0; tick < half_period_ticks; ++tick) {
        Vec3 force = field.compute(pos, vel, dt);
        if (force[0] > 1e-6) has_positive = true;
        if (force[0] < -1e-6) has_negative = true;
    }

    EXPECT_TRUE(has_positive && has_negative)
        << "Pendulum force should oscillate through coupling (pos="
        << has_positive << ", neg=" << has_negative << ")";
}

TEST(CartPendulumFieldTest, SpillDetection) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(5);
        pk.pack("ball_mass");          pk.pack(1.0);
        pk.pack("pendulum_length");    pk.pack(1.0);
        pk.pack("gravity");            pk.pack(9.81);
        pk.pack("angular_damping");    pk.pack(0.0);
        pk.pack("spill_threshold");    pk.pack(1.5708);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    auto oh_init = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("initial_phi"); pk.pack(1.5);
        pk.pack("initial_phi_dot"); pk.pack(5.0);
    });
    ASSERT_TRUE(field.update_params(oh_init.get()));

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    for (int i = 0; i < 100; ++i) {
        field.compute(pos, vel, 0.00025);
        if (field.spilled()) break;
    }

    EXPECT_TRUE(field.spilled());
}

TEST(CartPendulumFieldTest, EnergyConservationCoupledSystem) {
    // Stationary device at origin with zero damping. Total Lagrangian energy
    // of the coupled cart-pendulum system should be well-conserved by RK4.
    CartPendulumField field;
    double m_b = 0.6, M = 2.4, L = 0.3, g = 9.81, K = 800.0;
    auto oh = pack_and_unpack([&](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(7);
        pk.pack("ball_mass");           pk.pack(m_b);
        pk.pack("cup_mass");            pk.pack(M);
        pk.pack("pendulum_length");     pk.pack(L);
        pk.pack("gravity");             pk.pack(g);
        pk.pack("angular_damping");     pk.pack(0.0);
        pk.pack("coupling_stiffness");  pk.pack(K);
        pk.pack("coupling_damping");    pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    double phi_0 = 0.5;
    auto oh_init = pack_and_unpack([&](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("initial_phi"); pk.pack(phi_0);
    });
    ASSERT_TRUE(field.update_params(oh_init.get()));

    constexpr double dt = 0.00025;
    constexpr int num_ticks = 10000;
    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    // Full Lagrangian energy of the coupled cart-pendulum system (T + V).
    // Ball position = (x + L·sinφ, -L·cosφ), so ball velocity has a
    // cross-term between cart translation and pendulum rotation:
    // T = 0.5*(M+m)*v^2 + m*L*v*pd*cos(p) + 0.5*m*L^2*pd^2
    // V = m*g*L*(1 - cos(p)) + 0.5*K*x^2   (coupling spring PE)
    auto total_energy = [&](double p, double pd, double x, double v) {
        return 0.5 * (M + m_b) * v * v
             + m_b * L * v * pd * std::cos(p)
             + 0.5 * m_b * L * L * pd * pd
             + m_b * g * L * (1.0 - std::cos(p))
             + 0.5 * K * x * x;
    };

    double E_initial = total_energy(phi_0, 0.0, 0.0, 0.0);

    for (int tick = 0; tick < num_ticks; ++tick) {
        field.compute(pos, vel, dt);
    }

    double E_final = total_energy(field.phi(), field.phi_dot(),
                                  field.x_sim(), field.v_sim());

    double energy_drift_pct = std::abs(E_final - E_initial) / E_initial * 100.0;
    EXPECT_LT(energy_drift_pct, 0.5)
        << "Energy drift: " << energy_drift_pct << "% (initial=" << E_initial
        << ", final=" << E_final << ")";
}

TEST(CartPendulumFieldTest, ResetReSyncsSimulation) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("ball_mass");        pk.pack(0.6);
        pk.pack("cup_mass");         pk.pack(2.4);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    // Run some ticks to move simulation state away from zero
    for (int i = 0; i < 100; ++i) {
        field.compute({0.05, 0.0, 0.0}, {0.1, 0.0, 0.0}, 0.00025);
    }

    field.reset();

    // After reset, first tick with pos={0.03,0,0} should produce near-zero force
    // because x_sim is initialized to x_dev=0.03
    Vec3 force = field.compute({0.03, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    EXPECT_NEAR(force[0], 0.0, 0.01)
        << "First tick after reset should have near-zero coupling force";
}

TEST(CartPendulumFieldTest, ParameterUpdateChangesForce) {
    CartPendulumField field;
    auto oh1 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("coupling_stiffness"); pk.pack(800.0);
        pk.pack("coupling_damping");   pk.pack(2.0);
    });
    ASSERT_TRUE(field.update_params(oh1.get()));

    // First tick syncs; second tick creates a gap
    field.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    Vec3 force1 = field.compute({0.01, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    double mag1 = std::abs(force1[0]);

    // Increase coupling stiffness
    auto oh2 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("coupling_stiffness"); pk.pack(2000.0);
    });
    ASSERT_TRUE(field.update_params(oh2.get()));

    Vec3 force2 = field.compute({0.01, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    double mag2 = std::abs(force2[0]);

    EXPECT_GT(mag2, mag1)
        << "Higher coupling stiffness should produce larger force";
}

TEST(CartPendulumFieldTest, ParameterValidation) {
    CartPendulumField field;

    // coupling_stiffness negative
    auto oh1 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("coupling_stiffness"); pk.pack(-1.0);
    });
    EXPECT_FALSE(field.update_params(oh1.get()));

    // coupling_stiffness above 3000
    auto oh2 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("coupling_stiffness"); pk.pack(4000.0);
    });
    EXPECT_FALSE(field.update_params(oh2.get()));

    // coupling_damping negative
    auto oh3 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("coupling_damping"); pk.pack(-1.0);
    });
    EXPECT_FALSE(field.update_params(oh3.get()));

    // coupling_damping above 50
    auto oh4 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("coupling_damping"); pk.pack(100.0);
    });
    EXPECT_FALSE(field.update_params(oh4.get()));

    // Missing keys
    auto oh5 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(0);
    });
    EXPECT_FALSE(field.update_params(oh5.get()));
}

TEST(CartPendulumFieldTest, InitialPhiSetsPendulumAngle) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("initial_phi");     pk.pack(0.3);
        pk.pack("initial_phi_dot"); pk.pack(1.5);
    });
    ASSERT_TRUE(field.update_params(oh.get()));
    EXPECT_DOUBLE_EQ(field.phi(), 0.3);
    EXPECT_DOUBLE_EQ(field.phi_dot(), 1.5);

    // First tick should produce near-zero coupling force because first_tick_
    // causes x_sim to snap to device x.
    Vec3 force = field.compute({0.05, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    EXPECT_NEAR(force[0], 0.0, 0.01);
}

TEST(CartPendulumFieldTest, InitialPhiValidatesRange) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("ball_mass");   pk.pack(1.0);
        pk.pack("initial_phi"); pk.pack(4.0);  // > pi, invalid
    });
    EXPECT_FALSE(field.update_params(oh.get()));

    // Atomicity: initial_phi_dot must not have been committed despite being valid.
    EXPECT_DOUBLE_EQ(field.phi_dot(), 0.0);
    EXPECT_DOUBLE_EQ(field.phi(), 0.0);
}

TEST(CartPendulumFieldTest, InitialPhiOptionalKeysIndependent) {
    {
        CartPendulumField field;
        auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
            pk.pack_map(1);
            pk.pack("initial_phi"); pk.pack(0.5);
        });
        ASSERT_TRUE(field.update_params(oh.get()));
        EXPECT_DOUBLE_EQ(field.phi(), 0.5);
        EXPECT_DOUBLE_EQ(field.phi_dot(), 0.0);
    }
    {
        CartPendulumField field;
        auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
            pk.pack_map(1);
            pk.pack("initial_phi_dot"); pk.pack(2.0);
        });
        ASSERT_TRUE(field.update_params(oh.get()));
        EXPECT_DOUBLE_EQ(field.phi(), 0.0);
        EXPECT_DOUBLE_EQ(field.phi_dot(), 2.0);
    }
}

TEST(CartPendulumFieldTest, ResetClearsState) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("ball_mass");        pk.pack(1.0);
        pk.pack("pendulum_length");  pk.pack(1.0);
        pk.pack("gravity");          pk.pack(9.81);
        pk.pack("angular_damping");  pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    auto oh_init = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("initial_phi"); pk.pack(0.5);
        pk.pack("initial_phi_dot"); pk.pack(1.0);
    });
    ASSERT_TRUE(field.update_params(oh_init.get()));

    Vec3 pos = {0.1, 0.0, 0.0};
    Vec3 vel = {1.0, 0.0, 0.0};
    for (int i = 0; i < 10; ++i) {
        field.compute(pos, vel, 0.00025);
    }

    field.reset();
    EXPECT_DOUBLE_EQ(field.phi(), 0.0);
    EXPECT_DOUBLE_EQ(field.phi_dot(), 0.0);
    EXPECT_FALSE(field.spilled());
    EXPECT_DOUBLE_EQ(field.x_sim(), 0.0);
    EXPECT_DOUBLE_EQ(field.v_sim(), 0.0);
}

TEST(CartPendulumFieldTest, NameIsCartPendulum) {
    CartPendulumField field;
    EXPECT_EQ(field.name(), "cart_pendulum");
}

TEST(CartPendulumFieldTest, PackStateHasExpectedKeys) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("ball_mass");        pk.pack(1.0);
        pk.pack("pendulum_length");  pk.pack(1.0);
        pk.pack("gravity");          pk.pack(9.81);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    field.pack_state(pk);

    auto result = msgpack::unpack(sbuf.data(), sbuf.size());
    auto map = result.get().via.map;
    EXPECT_EQ(map.size, 7u);

    std::set<std::string> expected_keys = {"phi", "phi_dot", "spilled", "cup_x", "ball_x", "ball_y", "coupling_stretch"};
    std::set<std::string> actual_keys;
    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        actual_keys.insert(key);
    }
    EXPECT_EQ(actual_keys, expected_keys);
}

// ==================== ChannelField Tests ====================

TEST(ChannelFieldTest, DefaultConstraintsZAxis) {
    // Default axes = [2] (constrain Z only), so X and Y should be free
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("stiffness"); pk.pack(500.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.1, 0.2, 0.05};
    Vec3 vel = {1.0, -0.5, 0.3};
    Vec3 force = field.compute(pos, vel, 0.00025);
    // X and Y should be exactly zero (unconstrained)
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
    // Z: F = -500 * (0.05 - 0) - 10 * 0.3 = -25 - 3 = -28
    EXPECT_NEAR(force[2], -28.0, 1e-10);
}

TEST(ChannelFieldTest, UnconstrainedAxesZeroForce) {
    // With axes=[2], forces on X and Y must be exactly 0 regardless of position/velocity
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("axes");
        pk.pack_array(1); pk.pack(2);
        pk.pack("stiffness"); pk.pack(800.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {10.0, -20.0, 0.0};
    Vec3 vel = {5.0, -3.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
}

TEST(ChannelFieldTest, ConstrainedAxisRestoringForce) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("axes");
        pk.pack_array(1); pk.pack(2);
        pk.pack("stiffness"); pk.pack(800.0);
        pk.pack("damping");   pk.pack(0.0);
        pk.pack("center");
        pk.pack_array(3); pk.pack(0.0); pk.pack(0.0); pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.1, 0.2, 0.05};
    Vec3 vel = {0.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    // Only Z is constrained: F[2] = -800 * 0.05 = -40
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
    EXPECT_NEAR(force[2], -40.0, 1e-10);
}

TEST(ChannelFieldTest, ConstrainToLine) {
    // Constrain Y and Z (free in X only) — horizontal line constraint
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("axes");
        pk.pack_array(2); pk.pack(1); pk.pack(2);
        pk.pack("stiffness"); pk.pack(800.0);
        pk.pack("damping");   pk.pack(15.0);
        pk.pack("center");
        pk.pack_array(3); pk.pack(0.0); pk.pack(0.0); pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.1, 0.02, 0.03};
    Vec3 vel = {1.0, 0.5, -0.2};
    Vec3 force = field.compute(pos, vel, 0.00025);
    // X is free
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    // Y: F = -800 * 0.02 - 15 * 0.5 = -16 - 7.5 = -23.5
    EXPECT_NEAR(force[1], -23.5, 1e-10);
    // Z: F = -800 * 0.03 - 15 * (-0.2) = -24 + 3 = -21
    EXPECT_NEAR(force[2], -21.0, 1e-10);
}

TEST(ChannelFieldTest, AllAxesMatchesSpringDamper) {
    // With axes=[0,1,2], behavior should match SpringDamperField
    ChannelField channel;
    auto oh_ch = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("axes");
        pk.pack_array(3); pk.pack(0); pk.pack(1); pk.pack(2);
        pk.pack("stiffness"); pk.pack(200.0);
        pk.pack("damping");   pk.pack(5.0);
        pk.pack("center");
        pk.pack_array(3); pk.pack(0.1); pk.pack(-0.05); pk.pack(0.0);
    });
    ASSERT_TRUE(channel.update_params(oh_ch.get()));

    SpringDamperField spring;
    auto oh_sd = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("stiffness"); pk.pack(200.0);
        pk.pack("damping");   pk.pack(5.0);
        pk.pack("center");
        pk.pack_array(3); pk.pack(0.1); pk.pack(-0.05); pk.pack(0.0);
    });
    ASSERT_TRUE(spring.update_params(oh_sd.get()));

    Vec3 pos = {0.05, 0.1, -0.03};
    Vec3 vel = {0.2, -0.1, 0.5};
    Vec3 f_ch = channel.compute(pos, vel, 0.00025);
    Vec3 f_sd = spring.compute(pos, vel, 0.00025);
    for (int i = 0; i < 3; ++i) {
        EXPECT_NEAR(f_ch[static_cast<size_t>(i)], f_sd[static_cast<size_t>(i)], 1e-10);
    }
}

TEST(ChannelFieldTest, RejectsHighStiffness) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("stiffness"); pk.pack(3001.0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, AcceptsMaxStiffness) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("stiffness"); pk.pack(3000.0);
    });
    EXPECT_TRUE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, RejectsNegativeStiffness) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("stiffness"); pk.pack(-1.0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, RejectsNegativeDamping) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("damping"); pk.pack(-1.0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, RejectsHighDamping) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("damping"); pk.pack(101.0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, RejectsInvalidAxisValue) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("axes");
        pk.pack_array(1); pk.pack(3);  // axis 3 is invalid
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, RejectsNonIntegerAxis) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("axes");
        pk.pack_array(1); pk.pack("x");
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, RejectsNegativeAxisValue) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("axes");
        pk.pack_array(1); pk.pack(-1);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, RejectsNonArrayAxes) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("axes"); pk.pack(2);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, MissingKeysReturnsFalse) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(ChannelFieldTest, NameIsChannel) {
    ChannelField field;
    EXPECT_EQ(field.name(), "channel");
}

TEST(ChannelFieldTest, CenterShiftsEquilibrium) {
    ChannelField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("axes");
        pk.pack_array(1); pk.pack(2);
        pk.pack("stiffness"); pk.pack(500.0);
        pk.pack("damping");   pk.pack(0.0);
        pk.pack("center");
        pk.pack_array(3); pk.pack(0.0); pk.pack(0.0); pk.pack(0.1);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    // At center, force should be zero
    Vec3 pos_at_center = {0.5, 0.3, 0.1};
    Vec3 vel = {0.0, 0.0, 0.0};
    Vec3 force = field.compute(pos_at_center, vel, 0.00025);
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
    EXPECT_NEAR(force[2], 0.0, 1e-10);

    // Displaced from center
    Vec3 pos_displaced = {0.5, 0.3, 0.15};
    Vec3 force2 = field.compute(pos_displaced, vel, 0.00025);
    // F[2] = -500 * (0.15 - 0.1) = -25
    EXPECT_NEAR(force2[2], -25.0, 1e-10);
}

TEST(ChannelFieldTest, ComposesWithOtherFields) {
    CompositeField composite;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("fields");
        pk.pack_array(2);
        // Child 1: constant force in X
        pk.pack_map(2);
        pk.pack("type"); pk.pack("constant");
        pk.pack("params");
        pk.pack_map(1);
        pk.pack("force"); pk.pack_array(3); pk.pack(5.0); pk.pack(0.0); pk.pack(0.0);
        // Child 2: channel constraining Y and Z
        pk.pack_map(2);
        pk.pack("type"); pk.pack("channel");
        pk.pack("params");
        pk.pack_map(3);
        pk.pack("axes"); pk.pack_array(2); pk.pack(1); pk.pack(2);
        pk.pack("stiffness"); pk.pack(800.0);
        pk.pack("damping");   pk.pack(0.0);
    });
    ASSERT_TRUE(composite.update_params(oh.get()));

    Vec3 pos = {0.0, 0.02, 0.03};
    Vec3 vel = {0.0, 0.0, 0.0};
    Vec3 force = composite.compute(pos, vel, 0.00025);
    // X: constant 5.0 + channel 0.0 = 5.0
    EXPECT_NEAR(force[0], 5.0, 1e-10);
    // Y: constant 0.0 + channel(-800 * 0.02) = -16.0
    EXPECT_NEAR(force[1], -16.0, 1e-10);
    // Z: constant 0.0 + channel(-800 * 0.03) = -24.0
    EXPECT_NEAR(force[2], -24.0, 1e-10);
}

// ==================== CompositeField Tests ====================

TEST(CompositeFieldTest, SumOfChildForces) {
    CompositeField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("fields");
        pk.pack_array(2);
        // Child 1: spring_damper
        pk.pack_map(2);
        pk.pack("type"); pk.pack("spring_damper");
        pk.pack("params");
        pk.pack_map(3);
        pk.pack("stiffness"); pk.pack(100.0);
        pk.pack("damping");   pk.pack(0.0);
        pk.pack("center");    pk.pack_array(3); pk.pack(0.0); pk.pack(0.0); pk.pack(0.0);
        // Child 2: constant
        pk.pack_map(2);
        pk.pack("type"); pk.pack("constant");
        pk.pack("params");
        pk.pack_map(1);
        pk.pack("force"); pk.pack_array(3); pk.pack(5.0); pk.pack(0.0); pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.1, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    // Spring: -100 * 0.1 = -10, Constant: 5, Total: -5
    EXPECT_NEAR(force[0], -5.0, 1e-10);
    EXPECT_NEAR(force[1], 0.0, 1e-10);
    EXPECT_NEAR(force[2], 0.0, 1e-10);
}

TEST(CompositeFieldTest, UnknownChildTypeReturnsFalse) {
    CompositeField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("fields");
        pk.pack_array(1);
        pk.pack_map(2);
        pk.pack("type"); pk.pack("unknown_type");
        pk.pack("params"); pk.pack_map(0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(CompositeFieldTest, EmptyChildrenReturnsZero) {
    CompositeField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("fields");
        pk.pack_array(0);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    Vec3 pos = {0.1, 0.0, 0.0};
    Vec3 vel = {1.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel, 0.00025);
    EXPECT_DOUBLE_EQ(force[0], 0.0);
    EXPECT_DOUBLE_EQ(force[1], 0.0);
    EXPECT_DOUBLE_EQ(force[2], 0.0);
}

TEST(CompositeFieldTest, NameIsComposite) {
    CompositeField field;
    EXPECT_EQ(field.name(), "composite");
}

// ==================== FieldFactory Tests ====================

TEST(FieldFactoryTest, CreatesNullField) {
    auto field = create_field("null");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "null");
}

TEST(FieldFactoryTest, CreatesConstantField) {
    auto field = create_field("constant");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "constant");
}

TEST(FieldFactoryTest, CreatesSpringDamperField) {
    auto field = create_field("spring_damper");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "spring_damper");
}

TEST(FieldFactoryTest, CreatesWorkspaceLimitField) {
    auto field = create_field("workspace_limit");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "workspace_limit");
}

TEST(FieldFactoryTest, CreatesCartPendulumField) {
    auto field = create_field("cart_pendulum");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "cart_pendulum");
}

TEST(FieldFactoryTest, CreatesCompositeField) {
    auto field = create_field("composite");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "composite");
}

TEST(FieldFactoryTest, CreatesChannelField) {
    auto field = create_field("channel");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "channel");
}

TEST(FieldFactoryTest, ReturnsNullptrForUnknown) {
    auto field = create_field("unknown");
    EXPECT_EQ(field, nullptr);
}

TEST(FieldFactoryTest, ReturnsNullptrForEmptyString) {
    auto field = create_field("");
    EXPECT_EQ(field, nullptr);
}

TEST(FieldFactoryTest, CreatesPhysicsWorldField) {
    auto field = create_field("physics_world");
    ASSERT_NE(field, nullptr);
    EXPECT_EQ(field->name(), "physics_world");
}

// ==================== PhysicsField Tests ====================

TEST(PhysicsFieldTest, DefaultStateNoWorld) {
    PhysicsField pf;
    EXPECT_FALSE(pf.has_world());
    EXPECT_EQ(pf.body_count(), 0u);
    // compute should be safe without a world
    Vec3 f = pf.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    EXPECT_DOUBLE_EQ(f[0], 0.0);
    EXPECT_DOUBLE_EQ(f[1], 0.0);
    EXPECT_DOUBLE_EQ(f[2], 0.0);
}

TEST(PhysicsFieldTest, CreateSimpleWorld) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(1);
        // hand body: kinematic circle
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
    });
    EXPECT_TRUE(pf.update_params(oh.get()));
    EXPECT_TRUE(pf.has_world());
    EXPECT_EQ(pf.body_count(), 1u);
}

TEST(PhysicsFieldTest, AirHockeyTwoBodies) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("striker");
        pk.pack("bodies"); pk.pack_array(2);
        // striker: kinematic circle
        pk.pack_map(3);
        pk.pack("id"); pk.pack("striker");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
        // puck: dynamic circle
        pk.pack_map(6);
        pk.pack("id"); pk.pack("puck");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.015);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.05);
        pk.pack("mass"); pk.pack(0.1);
        pk.pack("restitution"); pk.pack(0.9);
    });
    EXPECT_TRUE(pf.update_params(oh.get()));
    EXPECT_TRUE(pf.has_world());
    EXPECT_EQ(pf.body_count(), 2u);
}

TEST(PhysicsFieldTest, BoxShapeWorks) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(-9.81);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        // hand: kinematic circle
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        // wall: static box
        pk.pack_map(4);
        pk.pack("id"); pk.pack("wall");
        pk.pack("type"); pk.pack("static");
        pk.pack("shape"); pk.pack_map(3);
            pk.pack("type"); pk.pack("box");
            pk.pack("width"); pk.pack(0.3);
            pk.pack("height"); pk.pack(0.005);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.0); pk.pack(-0.05);
    });
    EXPECT_TRUE(pf.update_params(oh.get()));
    EXPECT_EQ(pf.body_count(), 2u);
}

TEST(PhysicsFieldTest, ComputeReturnsZeroWhenNotTouching) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        // hand body
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        // distant puck
        pk.pack_map(5);
        pk.pack("id"); pk.pack("puck");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.5); pk.pack(0.5);
        pk.pack("mass"); pk.pack(0.1);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));

    // Step several times with hand at origin — puck is far away
    double dt = 0.00025;
    Vec3 f = {0.0, 0.0, 0.0};
    for (int i = 0; i < 10; ++i) {
        f = pf.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, dt);
    }
    // No contact → force should be zero (or very close)
    EXPECT_NEAR(f[0], 0.0, 1e-6);
    EXPECT_NEAR(f[1], 0.0, 1e-6);
    EXPECT_DOUBLE_EQ(f[2], 0.0);  // always zero (2D)
}

TEST(PhysicsFieldTest, RevoluteJointCreation) {
    // Create a kinematic hand and a dynamic rod jointed to it.
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(-9.81);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        // kinematic hand body
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        // dynamic rod with revolute joint to hand
        pk.pack_map(6);
        pk.pack("id"); pk.pack("rod");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("shape"); pk.pack_map(3);
            pk.pack("type"); pk.pack("box");
            pk.pack("width"); pk.pack(0.2);
            pk.pack("height"); pk.pack(0.01);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("mass"); pk.pack(0.3);
        pk.pack("joint"); pk.pack_map(3);
            pk.pack("type"); pk.pack("revolute");
            pk.pack("anchor"); pk.pack("hand");
            pk.pack("offset"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
    });
    EXPECT_TRUE(pf.update_params(oh.get()));
    EXPECT_TRUE(pf.has_world());
    EXPECT_EQ(pf.body_count(), 2u);
    EXPECT_EQ(pf.joint_count(), 1u);
}

TEST(PhysicsFieldTest, MissingHandBodyFails) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("nonexistent");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
    });
    EXPECT_FALSE(pf.update_params(oh.get()));
    EXPECT_FALSE(pf.has_world());
}

TEST(PhysicsFieldTest, MissingBodiesFails) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(2);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
    });
    EXPECT_FALSE(pf.update_params(oh.get()));
}

TEST(PhysicsFieldTest, InvalidShapeTypeFails) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("triangle");
            pk.pack("radius"); pk.pack(0.02);
    });
    EXPECT_FALSE(pf.update_params(oh.get()));
}

TEST(PhysicsFieldTest, PackStateContainsBodiesMap) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));

    // Pack state and verify structure
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pf.pack_state(pk);
    auto state_oh = msgpack::unpack(sbuf.data(), sbuf.size());
    auto& state = state_oh.get();
    ASSERT_EQ(state.type, msgpack::type::MAP);
    auto state_map = state.via.map;
    ASSERT_GE(state_map.size, 1u);
    // First key should be "bodies"
    std::string first_key(state_map.ptr[0].key.via.str.ptr,
                          state_map.ptr[0].key.via.str.size);
    EXPECT_EQ(first_key, "bodies");
}

TEST(PhysicsFieldTest, ResetDoesNotCrash) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));
    // Reset and then compute — should not crash
    pf.reset();
    Vec3 f = pf.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    EXPECT_DOUBLE_EQ(f[2], 0.0);
}

TEST(PhysicsFieldTest, RebuildWorldOnSecondUpdateParams) {
    PhysicsField pf;
    // First world
    auto oh1 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("a");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("a");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
    });
    ASSERT_TRUE(pf.update_params(oh1.get()));
    EXPECT_EQ(pf.body_count(), 1u);

    // Second world with 2 bodies — should tear down old world
    auto oh2 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("b");
        pk.pack("bodies"); pk.pack_array(2);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("b");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        pk.pack_map(4);
        pk.pack("id"); pk.pack("c");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        pk.pack("mass"); pk.pack(0.1);
    });
    ASSERT_TRUE(pf.update_params(oh2.get()));
    EXPECT_EQ(pf.body_count(), 2u);
}

// ==================== Additional PhysicsField Tests ====================

TEST(PhysicsFieldTest, DuplicateBodyIdRejected) {
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("a");
        pk.pack("bodies"); pk.pack_array(2);
        // first body with id "a"
        pk.pack_map(3);
        pk.pack("id"); pk.pack("a");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        // duplicate id "a"
        pk.pack_map(3);
        pk.pack("id"); pk.pack("a");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
    });
    EXPECT_FALSE(pf.update_params(oh.get()));
}

TEST(PhysicsFieldTest, DynamicHandBodyRejected) {
    // A dynamic hand body would create F=M·a instability (ADR-010).
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(4);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("mass"); pk.pack(1.0);
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
    });
    EXPECT_FALSE(pf.update_params(oh.get()));
    EXPECT_FALSE(pf.has_world());
}

TEST(PhysicsFieldTest, DeferredJointOrderIndependent) {
    // Body "rod" references "hand" in its joint, but "hand" is declared AFTER
    // "rod" in the bodies array.  Deferred joint resolution should handle this.
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        // "rod" declared first — references "hand" which doesn't exist yet
        pk.pack_map(5);
        pk.pack("id"); pk.pack("rod");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("mass"); pk.pack(0.1);
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        pk.pack("joint"); pk.pack_map(3);
            pk.pack("type"); pk.pack("revolute");
            pk.pack("anchor"); pk.pack("hand");
            pk.pack("offset"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        // "hand" declared second
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
    });
    EXPECT_TRUE(pf.update_params(oh.get()));
    EXPECT_EQ(pf.body_count(), 2u);
    EXPECT_EQ(pf.joint_count(), 1u);
}

TEST(PhysicsFieldTest, ResetRestoresInitialPositions) {
    // Dynamic body initially at (0.05, 0.10) should return there after reset.
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(-9.81);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        pk.pack_map(5);
        pk.pack("id"); pk.pack("ball");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("mass"); pk.pack(0.5);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.05); pk.pack(0.10);
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));

    // Step the simulation so the ball falls under gravity.
    for (int i = 0; i < 100; ++i) {
        pf.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    }

    // Read state — ball should have moved from initial position.
    {
        msgpack::sbuffer sbuf;
        msgpack::packer<msgpack::sbuffer> pk(sbuf);
        pf.pack_state(pk);
        auto state_oh = msgpack::unpack(sbuf.data(), sbuf.size());
        auto& state_map = state_oh.get().via.map;
        // "bodies" key
        auto& bodies_map = state_map.ptr[0].val.via.map;
        for (uint32_t i = 0; i < bodies_map.size; ++i) {
            std::string bid(bodies_map.ptr[i].key.via.str.ptr,
                            bodies_map.ptr[i].key.via.str.size);
            if (bid == "ball") {
                auto& bm = bodies_map.ptr[i].val.via.map;
                // Find "position"
                for (uint32_t j = 0; j < bm.size; ++j) {
                    std::string k(bm.ptr[j].key.via.str.ptr,
                                  bm.ptr[j].key.via.str.size);
                    if (k == "position") {
                        double y_after_fall =
                            bm.ptr[j].val.via.array.ptr[1].as<double>();
                        EXPECT_LT(y_after_fall, 0.10);  // fallen below initial
                    }
                }
            }
        }
    }

    // Reset and verify ball is back at initial position.
    pf.reset();
    {
        msgpack::sbuffer sbuf;
        msgpack::packer<msgpack::sbuffer> pk(sbuf);
        pf.pack_state(pk);
        auto state_oh = msgpack::unpack(sbuf.data(), sbuf.size());
        auto& state_map = state_oh.get().via.map;
        auto& bodies_map = state_map.ptr[0].val.via.map;
        for (uint32_t i = 0; i < bodies_map.size; ++i) {
            std::string bid(bodies_map.ptr[i].key.via.str.ptr,
                            bodies_map.ptr[i].key.via.str.size);
            if (bid == "ball") {
                auto& bm = bodies_map.ptr[i].val.via.map;
                for (uint32_t j = 0; j < bm.size; ++j) {
                    std::string k(bm.ptr[j].key.via.str.ptr,
                                  bm.ptr[j].key.via.str.size);
                    if (k == "position") {
                        double x_reset =
                            bm.ptr[j].val.via.array.ptr[0].as<double>();
                        double y_reset =
                            bm.ptr[j].val.via.array.ptr[1].as<double>();
                        EXPECT_NEAR(x_reset, 0.05, 1e-4);
                        EXPECT_NEAR(y_reset, 0.10, 1e-4);
                    }
                }
            }
        }
    }
}

TEST(PhysicsFieldTest, GravityFall) {
    // A dynamic body with downward gravity should fall (y decreases).
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(-9.81);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        pk.pack_map(5);
        pk.pack("id"); pk.pack("ball");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("mass"); pk.pack(0.1);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.0); pk.pack(1.0);
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));

    // Step simulation — hand at origin, ball at y=1.0 falling
    for (int i = 0; i < 200; ++i) {
        pf.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    }

    // Check ball y-position decreased (gravity pulled it down).
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pf.pack_state(pk);
    auto state_oh = msgpack::unpack(sbuf.data(), sbuf.size());
    auto& bodies_map = state_oh.get().via.map.ptr[0].val.via.map;
    for (uint32_t i = 0; i < bodies_map.size; ++i) {
        std::string bid(bodies_map.ptr[i].key.via.str.ptr,
                        bodies_map.ptr[i].key.via.str.size);
        if (bid == "ball") {
            auto& bm = bodies_map.ptr[i].val.via.map;
            for (uint32_t j = 0; j < bm.size; ++j) {
                std::string k(bm.ptr[j].key.via.str.ptr,
                              bm.ptr[j].key.via.str.size);
                if (k == "position") {
                    double y_val =
                        bm.ptr[j].val.via.array.ptr[1].as<double>();
                    EXPECT_LT(y_val, 1.0);  // ball has fallen
                }
            }
        }
    }
}

TEST(PhysicsFieldTest, ForceScaleMultiplier) {
    // With force_scale=2.0, forces should double compared to force_scale=1.0.
    // We test by pushing the hand into a dynamic puck against a static wall.
    // (Box2D needs at least one dynamic body to generate contact impulses.)
    auto make_world = [](double scale) {
        return pack_and_unpack([scale](msgpack::packer<msgpack::sbuffer>& pk) {
            pk.pack_map(4);
            pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
            pk.pack("hand_body"); pk.pack("hand");
            pk.pack("force_scale"); pk.pack(scale);
            pk.pack("bodies"); pk.pack_array(3);
            // kinematic hand
            pk.pack_map(3);
            pk.pack("id"); pk.pack("hand");
            pk.pack("type"); pk.pack("kinematic");
            pk.pack("shape"); pk.pack_map(2);
                pk.pack("type"); pk.pack("circle");
                pk.pack("radius"); pk.pack(0.02);
            // dynamic puck between hand and wall
            pk.pack_map(5);
            pk.pack("id"); pk.pack("puck");
            pk.pack("type"); pk.pack("dynamic");
            pk.pack("mass"); pk.pack(0.5);
            pk.pack("position"); pk.pack_array(2); pk.pack(0.06); pk.pack(0.0);
            pk.pack("shape"); pk.pack_map(2);
                pk.pack("type"); pk.pack("circle");
                pk.pack("radius"); pk.pack(0.02);
            // static wall
            pk.pack_map(4);
            pk.pack("id"); pk.pack("wall");
            pk.pack("type"); pk.pack("static");
            pk.pack("position"); pk.pack_array(2); pk.pack(0.10); pk.pack(0.0);
            pk.pack("shape"); pk.pack_map(3);
                pk.pack("type"); pk.pack("box");
                pk.pack("width"); pk.pack(0.02);
                pk.pack("height"); pk.pack(0.2);
        });
    };

    // Force with scale=1.0
    PhysicsField pf1;
    auto oh1 = make_world(1.0);
    ASSERT_TRUE(pf1.update_params(oh1.get()));
    // Drive hand into the puck
    Vec3 f1{};
    for (int i = 0; i < 100; ++i) {
        f1 = pf1.compute({0.05, 0.0, 0.0}, {1.0, 0.0, 0.0}, 0.00025);
    }

    // Force with scale=2.0
    PhysicsField pf2;
    auto oh2 = make_world(2.0);
    ASSERT_TRUE(pf2.update_params(oh2.get()));
    Vec3 f2{};
    for (int i = 0; i < 100; ++i) {
        f2 = pf2.compute({0.05, 0.0, 0.0}, {1.0, 0.0, 0.0}, 0.00025);
    }

    // Both should have nonzero force, and scale=2 should be ~2x scale=1.
    ASSERT_LT(f1[0], -1e-6) << "Baseline force should be nonzero negative";
    double ratio = f2[0] / f1[0];
    EXPECT_NEAR(ratio, 2.0, 0.1);
}

TEST(PhysicsFieldTest, StaticWallCollisionForceDirection) {
    // SAFETY-CRITICAL TEST: Hand pushed into a dynamic puck pinned against a
    // static wall should experience force in the negative-X direction (pushback).
    //
    // Box2D only generates contact impulses for contacts involving at least one
    // dynamic body.  Kinematic-static contacts produce zero impulse.  So we use
    // a dynamic puck sandwiched between the kinematic hand and the static wall.
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(3);
        // kinematic hand — circle at the origin
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
        // dynamic puck — between hand and wall
        pk.pack_map(5);
        pk.pack("id"); pk.pack("puck");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("mass"); pk.pack(0.5);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.06); pk.pack(0.0);
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
        // static wall far right
        pk.pack_map(4);
        pk.pack("id"); pk.pack("wall");
        pk.pack("type"); pk.pack("static");
        pk.pack("position"); pk.pack_array(2); pk.pack(0.10); pk.pack(0.0);
        pk.pack("shape"); pk.pack_map(3);
            pk.pack("type"); pk.pack("box");
            pk.pack("width"); pk.pack(0.02);
            pk.pack("height"); pk.pack(0.2);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));

    // Drive the hand to the right so it pushes the puck against the wall.
    // Hand edge at 0.05+0.02=0.07, puck center at 0.06, puck edge at 0.08,
    // wall edge at 0.10-0.01=0.09.  Position the hand to compress the puck
    // into the wall.
    Vec3 f{};
    for (int i = 0; i < 100; ++i) {
        f = pf.compute({0.05, 0.0, 0.0}, {1.0, 0.0, 0.0}, 0.00025);
    }
    // The puck is to the right of the hand — the reaction force on the hand
    // must push it back to the left (negative X).
    EXPECT_LT(f[0], 0.0) << "Wall collision force should push hand left (negative X)";
    // Y and Z should be near zero.
    EXPECT_NEAR(f[1], 0.0, std::abs(f[0]) * 0.1 + 1e-6);
    EXPECT_DOUBLE_EQ(f[2], 0.0);
}

TEST(PhysicsFieldTest, DynamicBodyReaction) {
    // Move the kinematic hand into a free dynamic puck (no wall).
    // Verify: (a) force on hand opposes hand's motion direction,
    // (b) the puck moves away from the hand.
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        // kinematic hand at origin
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
        // free dynamic puck just ahead of hand (touching distance)
        pk.pack_map(5);
        pk.pack("id"); pk.pack("puck");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("mass"); pk.pack(0.5);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.041); pk.pack(0.0);
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));

    // Drive hand rightward, continuously pushing through the puck's space.
    // The hand moves faster than the puck can escape, maintaining contact.
    bool saw_negative_force = false;
    Vec3 f{};
    for (int i = 0; i < 200; ++i) {
        // Hand accelerates rightward — moves 0.0005 m per tick = 2 m/s
        double x = 0.0005 * i;
        f = pf.compute({x, 0.0, 0.0}, {2.0, 0.0, 0.0}, 0.00025);
        if (f[0] < -1e-6) saw_negative_force = true;
    }
    // (a) At some point during the push, hand should have experienced pushback.
    EXPECT_TRUE(saw_negative_force)
        << "Contact force should oppose hand motion at some point during push";

    // (b) Verify puck has moved rightward by reading pack_state.
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk2(sbuf);
    pf.pack_state(pk2);
    auto oh_state = msgpack::unpack(sbuf.data(), sbuf.size());
    auto state_map = oh_state->via.map;
    for (uint32_t i = 0; i < state_map.size; ++i) {
        std::string k(state_map.ptr[i].key.via.str.ptr,
                      state_map.ptr[i].key.via.str.size);
        if (k == "bodies") {
            auto bmap = state_map.ptr[i].val.via.map;
            for (uint32_t j = 0; j < bmap.size; ++j) {
                std::string bid(bmap.ptr[j].key.via.str.ptr,
                                bmap.ptr[j].key.via.str.size);
                if (bid == "puck") {
                    auto pmap = bmap.ptr[j].val.via.map;
                    for (uint32_t m = 0; m < pmap.size; ++m) {
                        std::string pk3(pmap.ptr[m].key.via.str.ptr,
                                        pmap.ptr[m].key.via.str.size);
                        if (pk3 == "position") {
                            double puck_x = 0.0;
                            pmap.ptr[m].val.via.array.ptr[0].convert(puck_x);
                            EXPECT_GT(puck_x, 0.041)
                                << "Puck should have moved rightward from initial 0.041";
                        }
                    }
                }
            }
        }
    }
}

TEST(PhysicsFieldTest, JointForcePropagation) {
    // Create a kinematic hand with a dynamic rod attached via revolute joint.
    // Accelerate the hand sideways and verify nonzero joint reaction force.
    PhysicsField pf;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(-9.81);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(2);
        // kinematic hand
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
        // dynamic rod attached to hand
        pk.pack_map(6);
        pk.pack("id"); pk.pack("rod");
        pk.pack("type"); pk.pack("dynamic");
        pk.pack("mass"); pk.pack(0.3);
        pk.pack("position"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("shape"); pk.pack_map(3);
            pk.pack("type"); pk.pack("box");
            pk.pack("width"); pk.pack(0.2);
            pk.pack("height"); pk.pack(0.01);
        pk.pack("joint"); pk.pack_map(3);
            pk.pack("type"); pk.pack("revolute");
            pk.pack("anchor"); pk.pack("hand");
            pk.pack("offset"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
    });
    ASSERT_TRUE(pf.update_params(oh.get()));

    // Let gravity act on the rod for a few ticks (rod hangs from hand).
    for (int i = 0; i < 20; ++i) {
        pf.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    }

    // Now accelerate hand sideways over multiple ticks.
    Vec3 f{};
    for (int i = 0; i < 100; ++i) {
        double x = 0.001 * i;  // increasing x position
        f = pf.compute({x, 0.0, 0.0}, {1.0, 0.0, 0.0}, 0.00025);
    }
    // The joint should transmit force — at minimum gravity pulling the rod down
    // should produce a nonzero Y component (rod weight on hand).
    double force_mag = std::sqrt(f[0] * f[0] + f[1] * f[1]);
    EXPECT_GT(force_mag, 1e-6) << "Joint should transmit nonzero force to hand";
}

TEST(PhysicsFieldTest, PreserveWorldOnFailedUpdate) {
    PhysicsField pf;
    // Build a valid world first.
    auto oh1 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("hand");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("hand");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.02);
    });
    ASSERT_TRUE(pf.update_params(oh1.get()));
    EXPECT_TRUE(pf.has_world());
    EXPECT_EQ(pf.body_count(), 1u);

    // Attempt an invalid update (missing hand_body reference).
    auto oh2 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(3);
        pk.pack("gravity"); pk.pack_array(2); pk.pack(0.0); pk.pack(0.0);
        pk.pack("hand_body"); pk.pack("nonexistent");
        pk.pack("bodies"); pk.pack_array(1);
        pk.pack_map(3);
        pk.pack("id"); pk.pack("x");
        pk.pack("type"); pk.pack("kinematic");
        pk.pack("shape"); pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(0.01);
    });
    EXPECT_FALSE(pf.update_params(oh2.get()));

    // Previous world should still be intact.
    EXPECT_TRUE(pf.has_world());
    EXPECT_EQ(pf.body_count(), 1u);

    // Compute should still work on the preserved world.
    Vec3 f = pf.compute({0.0, 0.0, 0.0}, {0.0, 0.0, 0.0}, 0.00025);
    EXPECT_DOUBLE_EQ(f[2], 0.0);
}

// ==================== Non-map input tests ====================

TEST(ForceFieldTest, NonMapParamsReturnsFalse) {
    // Test all fields with non-map params
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack("not_a_map");
    });

    ConstantField cf;
    EXPECT_FALSE(cf.update_params(oh.get()));

    SpringDamperField sdf;
    EXPECT_FALSE(sdf.update_params(oh.get()));

    WorkspaceLimitField wlf;
    EXPECT_FALSE(wlf.update_params(oh.get()));

    CartPendulumField cpf;
    EXPECT_FALSE(cpf.update_params(oh.get()));

    CompositeField comp;
    EXPECT_FALSE(comp.update_params(oh.get()));

    ChannelField ch;
    EXPECT_FALSE(ch.update_params(oh.get()));

    PhysicsField pf;
    EXPECT_FALSE(pf.update_params(oh.get()));
}
