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

    field.set_initial_state(0.01, 0.0);

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

    field.set_initial_state(0.3, 0.0);

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

    field.set_initial_state(1.5, 5.0);

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
    field.set_initial_state(phi_0, 0.0);

    constexpr double dt = 0.00025;
    constexpr int num_ticks = 10000;
    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    // Full Lagrangian energy: T + V
    // T = 0.5*(M+m)*v^2 + m*L*v*pd*cos(p) + 0.5*m*L^2*pd^2
    // V = m*g*L*(1 - cos(p)) + 0.5*K*x^2
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

    // coupling_stiffness above 5000
    auto oh2 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("coupling_stiffness"); pk.pack(6000.0);
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

    field.set_initial_state(0.5, 1.0);

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
}
