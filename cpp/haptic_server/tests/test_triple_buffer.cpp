#include <gtest/gtest.h>
#include "triple_buffer.hpp"
#include <thread>
#include <atomic>

TEST(TripleBufferTest, SingleThreadWriteReadBasic) {
    TripleBuffer<int> buf;
    buf.write_buffer() = 42;
    buf.publish();
    ASSERT_TRUE(buf.swap_read_buffer());
    EXPECT_EQ(buf.read_buffer(), 42);
}

TEST(TripleBufferTest, ReaderSeesLatestValue) {
    TripleBuffer<int> buf;
    buf.write_buffer() = 1;
    buf.publish();
    buf.write_buffer() = 2;
    buf.publish();
    buf.write_buffer() = 3;
    buf.publish();
    ASSERT_TRUE(buf.swap_read_buffer());
    EXPECT_EQ(buf.read_buffer(), 3);
}

TEST(TripleBufferTest, NoNewDataReturnsFalse) {
    TripleBuffer<int> buf;
    buf.write_buffer() = 42;
    buf.publish();
    ASSERT_TRUE(buf.swap_read_buffer());
    EXPECT_EQ(buf.read_buffer(), 42);
    // No new publish, so swap_read_buffer should return false
    EXPECT_FALSE(buf.swap_read_buffer());
}

TEST(TripleBufferTest, MultiThreadMonotonicity) {
    TripleBuffer<int> buf;
    constexpr int NUM_WRITES = 100000;
    std::atomic<bool> done{false};

    // Writer thread publishes incrementing integers
    std::thread writer([&]() {
        for (int i = 1; i <= NUM_WRITES; ++i) {
            buf.write_buffer() = i;
            buf.publish();
        }
        done.store(true, std::memory_order_release);
    });

    // Reader thread reads and checks monotonicity
    int last_seen = 0;
    while (!done.load(std::memory_order_acquire) || buf.swap_read_buffer()) {
        if (buf.swap_read_buffer()) {
            int val = buf.read_buffer();
            EXPECT_GE(val, last_seen) << "Non-monotonic read: got " << val << " after " << last_seen;
            last_seen = val;
        }
    }
    EXPECT_GT(last_seen, 0) << "Reader should have seen at least one value";

    writer.join();
}
