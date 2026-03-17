#include <gtest/gtest.h>
#include "triple_buffer.hpp"
#include <atomic>
#include <array>
#include <cstdint>
#include <thread>

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

TEST(TripleBufferTest, NoNewDataSwapDoesNotReturnReadSlotToWriter) {
    TripleBuffer<int> buf;

    buf.write_buffer() = 1;
    buf.publish();
    ASSERT_TRUE(buf.swap_read_buffer());

    const int* read_slot_before = &buf.read_buffer();
    EXPECT_FALSE(buf.swap_read_buffer());

    buf.write_buffer() = 2;
    buf.publish();
    int* write_slot_after_publish = &buf.write_buffer();

    EXPECT_NE(write_slot_after_publish, read_slot_before);
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
    for (;;) {
        if (buf.swap_read_buffer()) {
            int val = buf.read_buffer();
            EXPECT_GE(val, last_seen)
                << "Non-monotonic read: got " << val << " after " << last_seen;
            last_seen = val;
        } else if (done.load(std::memory_order_acquire)) {
            // Writer finished and no more new data
            break;
        }
    }
    EXPECT_GT(last_seen, 0) << "Reader should have seen at least one value";

    writer.join();
}

TEST(TripleBufferTest, MultiThreadStructNoTornReads) {
    struct Snapshot {
        uint64_t seq{0};
        uint64_t inv_seq{~uint64_t{0}};
        std::array<uint64_t, 8> payload{};
        uint64_t checksum{0};
    };

    auto compute_checksum = [](const Snapshot& s) {
        uint64_t sum = s.seq ^ s.inv_seq;
        for (uint64_t v : s.payload) sum ^= v;
        return sum;
    };

    TripleBuffer<Snapshot> buf;
    constexpr uint64_t NUM_WRITES = 200000;
    std::atomic<bool> done{false};

    std::thread writer([&]() {
        for (uint64_t i = 1; i <= NUM_WRITES; ++i) {
            Snapshot& s = buf.write_buffer();
            s.seq = i;
            s.inv_seq = ~i;
            for (size_t j = 0; j < s.payload.size(); ++j) {
                s.payload[j] = (i * 1315423911ULL) ^ static_cast<uint64_t>(j);
            }
            s.checksum = compute_checksum(s);
            buf.publish();
        }
        done.store(true, std::memory_order_release);
    });

    uint64_t last_seen = 0;
    for (;;) {
        if (buf.swap_read_buffer()) {
            const Snapshot& s = buf.read_buffer();
            EXPECT_EQ(s.inv_seq, ~s.seq);
            EXPECT_EQ(s.checksum, compute_checksum(s));
            EXPECT_GE(s.seq, last_seen);
            last_seen = s.seq;
        } else if (done.load(std::memory_order_acquire)) {
            break;
        }
    }

    EXPECT_GT(last_seen, 0);
    writer.join();
}
