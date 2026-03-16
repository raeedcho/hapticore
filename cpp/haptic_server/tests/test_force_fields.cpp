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

TEST(CartPendulumFieldTest, SmallAnglePeriod) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(5);
        pk.pack("ball_mass");          pk.pack(1.0);
        pk.pack("pendulum_length");    pk.pack(1.0);
        pk.pack("gravity");            pk.pack(9.81);
        pk.pack("angular_damping");    pk.pack(0.0);
        pk.pack("cup_inertia_enabled"); pk.pack(false);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    // Set initial angle: phi0 = 0.01 rad, phi_dot0 = 0
    field.set_initial_state(0.01, 0.0);

    constexpr double dt = 0.00025; // 4 kHz
    double T_expected = 2.0 * M_PI * std::sqrt(1.0 / 9.81); // ≈ 2.006 s
    int total_ticks = static_cast<int>(T_expected / dt) * 2; // run for 2 full periods

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    // Track zero crossings of phi (going positive)
    double prev_phi = field.phi();
    std::vector<int> positive_crossings;

    for (int tick = 0; tick < total_ticks; ++tick) {
        field.compute(pos, vel, dt);
        double cur_phi = field.phi();
        // Detect positive-going zero crossing
        if (prev_phi < 0.0 && cur_phi >= 0.0) {
            positive_crossings.push_back(tick);
        }
        prev_phi = cur_phi;
    }

    // We need at least 2 positive crossings to measure a period
    ASSERT_GE(positive_crossings.size(), 2u)
        << "Not enough zero crossings detected";

    double measured_period = (positive_crossings[1] - positive_crossings[0]) * dt;
    double error_pct = std::abs(measured_period - T_expected) / T_expected * 100.0;
    EXPECT_LT(error_pct, 2.0)
        << "Period error: " << error_pct << "% (measured=" << measured_period
        << ", expected=" << T_expected << ")";
}

TEST(CartPendulumFieldTest, EnergyConservation) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(5);
        pk.pack("ball_mass");          pk.pack(1.0);
        pk.pack("pendulum_length");    pk.pack(1.0);
        pk.pack("gravity");            pk.pack(9.81);
        pk.pack("angular_damping");    pk.pack(0.0);
        pk.pack("cup_inertia_enabled"); pk.pack(false);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    double m = 1.0, L = 1.0, g = 9.81;
    field.set_initial_state(0.5, 0.0); // phi0 = 0.5 rad

    double E_initial = m * g * L * (1.0 - std::cos(0.5)); // KE=0 at start

    constexpr double dt = 0.00025;
    constexpr int num_ticks = 10000;
    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    for (int tick = 0; tick < num_ticks; ++tick) {
        field.compute(pos, vel, dt);
    }

    double phi = field.phi();
    double phi_dot = field.phi_dot();
    double E_final = 0.5 * m * L * L * phi_dot * phi_dot + m * g * L * (1.0 - std::cos(phi));

    double energy_drift_pct = std::abs(E_final - E_initial) / E_initial * 100.0;
    EXPECT_LT(energy_drift_pct, 0.1)
        << "Energy drift: " << energy_drift_pct << "% (initial=" << E_initial
        << ", final=" << E_final << ")";
}

TEST(CartPendulumFieldTest, SpillDetection) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(5);
        pk.pack("ball_mass");          pk.pack(1.0);
        pk.pack("pendulum_length");    pk.pack(1.0);
        pk.pack("gravity");            pk.pack(9.81);
        pk.pack("angular_damping");    pk.pack(0.0);
        pk.pack("spill_threshold");    pk.pack(1.5708); // π/2
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    // Start with phi near the spill threshold with large positive angular velocity
    field.set_initial_state(1.5, 5.0);

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};

    // Run a few ticks - should spill quickly since phi starts at 1.5 with positive phi_dot
    for (int i = 0; i < 100; ++i) {
        field.compute(pos, vel, 0.00025);
        if (field.spilled()) break;
    }

    EXPECT_TRUE(field.spilled());
}

TEST(CartPendulumFieldTest, ReactionForceSign) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(5);
        pk.pack("ball_mass");          pk.pack(1.0);
        pk.pack("cup_mass");           pk.pack(1.0);
        pk.pack("pendulum_length");    pk.pack(0.5);
        pk.pack("gravity");            pk.pack(9.81);
        pk.pack("cup_inertia_enabled"); pk.pack(false);
    });
    ASSERT_TRUE(field.update_params(oh.get()));

    // First tick: establish vel_x_prev = 0
    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel_zero = {0.0, 0.0, 0.0};
    field.compute(pos, vel_zero, 0.00025);

    // Second tick: cup accelerates right (positive velocity from zero)
    Vec3 vel_right = {1.0, 0.0, 0.0};
    Vec3 force = field.compute(pos, vel_right, 0.00025);

    // Ball hanging straight down (phi ≈ 0), cup accelerating right
    // Ball resists being dragged right → force x-component should be negative
    EXPECT_LT(force[0], 0.0) << "Reaction force should oppose cup acceleration";
}

TEST(CartPendulumFieldTest, ParameterUpdateMidRun) {
    CartPendulumField field;
    auto oh1 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(4);
        pk.pack("ball_mass");        pk.pack(1.0);
        pk.pack("pendulum_length");  pk.pack(1.0);
        pk.pack("gravity");          pk.pack(9.81);
        pk.pack("angular_damping");  pk.pack(0.0);
    });
    ASSERT_TRUE(field.update_params(oh1.get()));

    field.set_initial_state(0.1, 0.0);

    Vec3 pos = {0.0, 0.0, 0.0};
    Vec3 vel = {0.0, 0.0, 0.0};
    for (int i = 0; i < 10; ++i) {
        field.compute(pos, vel, 0.00025);
    }

    // Change pendulum length mid-run
    auto oh2 = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("pendulum_length"); pk.pack(0.5);
    });
    ASSERT_TRUE(field.update_params(oh2.get()));

    // Verify we can still compute without errors
    Vec3 force = field.compute(pos, vel, 0.00025);
    (void)force;
    SUCCEED();
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
}

TEST(CartPendulumFieldTest, NameIsCartPendulum) {
    CartPendulumField field;
    EXPECT_EQ(field.name(), "cart_pendulum");
}

TEST(CartPendulumFieldTest, MissingKeysReturnsFalse) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
}

TEST(CartPendulumFieldTest, InvalidMassReturnsFalse) {
    CartPendulumField field;
    auto oh = pack_and_unpack([](msgpack::packer<msgpack::sbuffer>& pk) {
        pk.pack_map(1);
        pk.pack("ball_mass"); pk.pack(-1.0);
    });
    EXPECT_FALSE(field.update_params(oh.get()));
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
    EXPECT_EQ(map.size, 6u);

    std::set<std::string> expected_keys = {"phi", "phi_dot", "spilled", "cup_x", "ball_x", "ball_y"};
    std::set<std::string> actual_keys;
    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        actual_keys.insert(key);
    }
    EXPECT_EQ(actual_keys, expected_keys);
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
}
