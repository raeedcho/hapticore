#include <gtest/gtest.h>

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
