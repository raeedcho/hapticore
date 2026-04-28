#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <thread>

#include "dhd_interface.hpp"
#include "dhd_mock.hpp"

TEST(DhdMockTest, ReturnsConfiguredPositionAndVelocity) {
    DhdMock mock;
    ASSERT_TRUE(mock.open());

    mock.set_mock_position({1.0, 2.0, 3.0});
    mock.set_mock_velocity({-1.0, 0.5, 0.25});

    Vec3 pos{};
    Vec3 vel{};
    ASSERT_TRUE(mock.get_position(pos));
    ASSERT_TRUE(mock.get_linear_velocity(vel));

    EXPECT_DOUBLE_EQ(pos[0], 1.0);
    EXPECT_DOUBLE_EQ(pos[1], 2.0);
    EXPECT_DOUBLE_EQ(pos[2], 3.0);

    EXPECT_DOUBLE_EQ(vel[0], -1.0);
    EXPECT_DOUBLE_EQ(vel[1], 0.5);
    EXPECT_DOUBLE_EQ(vel[2], 0.25);
}

TEST(DhdMockTest, RecordsAppliedForces) {
    DhdMock mock;
    ASSERT_TRUE(mock.open());

    ASSERT_TRUE(mock.set_force({1.0, -2.0, 3.0}));
    ASSERT_TRUE(mock.set_force({4.0, 5.0, 6.0}));

    ASSERT_EQ(mock.applied_forces().size(), 2u);
    EXPECT_DOUBLE_EQ(mock.applied_forces()[0][0], 1.0);
    EXPECT_DOUBLE_EQ(mock.applied_forces()[0][1], -2.0);
    EXPECT_DOUBLE_EQ(mock.applied_forces()[1][1], 5.0);
}

TEST(DhdMockTest, ClearsForceLog) {
    DhdMock mock;
    ASSERT_TRUE(mock.open());

    ASSERT_TRUE(mock.set_force({1.0, 0.0, 0.0}));
    ASSERT_FALSE(mock.applied_forces().empty());

    mock.clear_force_log();
    EXPECT_TRUE(mock.applied_forces().empty());
}

TEST(DhdMockTest, FactoryReturnsMockInMockBuild) {
    auto dhd = create_dhd_interface();
    ASSERT_NE(dhd, nullptr);
#ifdef HAPTIC_MOCK_HARDWARE
    EXPECT_EQ(dhd->device_name(), "MockDHD");
#endif
}

TEST(DhdMockTest, ConcurrentReadWriteIsSafe) {
    DhdMock mock;
    mock.open();

    std::atomic<bool> stop{false};

    // Writer: rapidly update position and velocity
    std::thread writer([&mock, &stop]() {
        double t = 0.0;
        while (!stop.load(std::memory_order_relaxed)) {
            mock.set_mock_position({std::sin(t), std::cos(t), 0.0});
            mock.set_mock_velocity({std::cos(t), -std::sin(t), 0.0});
            t += 0.001;
        }
    });

    // Reader: rapidly read position (simulates 4 kHz haptic loop)
    std::thread reader([&mock, &stop]() {
        Vec3 pos{}, vel{};
        int reads = 0;
        while (!stop.load(std::memory_order_relaxed) && reads < 100000) {
            mock.get_position(pos);
            mock.get_linear_velocity(vel);
            // Verify values are consistent (not torn): x² + y² ≈ 1.0
            double r2 = pos[0]*pos[0] + pos[1]*pos[1];
            EXPECT_NEAR(r2, 1.0, 0.01)
                << "Position appears torn at read " << reads;
            ++reads;
        }
    });

    // Let them race for a bit
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    stop.store(true);
    writer.join();
    reader.join();
}
