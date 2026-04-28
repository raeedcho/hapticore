#include <atomic>
#include <chrono>
#include <cmath>
#include <memory>
#include <string>
#include <thread>

#include <gtest/gtest.h>
#include <msgpack.hpp>
#include <zmq.hpp>

#include "command_data.hpp"
#include "command_thread.hpp"
#include "dhd_mock.hpp"
#include "force_fields/field_factory.hpp"
#include "force_fields/null_field.hpp"
#include "force_fields/spring_damper_field.hpp"
#include "haptic_thread.hpp"
#include "publisher_thread.hpp"
#include "state_data.hpp"
#include "triple_buffer.hpp"

// ==================== Publisher Thread Tests ====================

class PublisherThreadTest : public ::testing::Test {
protected:
    // Use a unique IPC address per test to avoid collisions
    std::string pub_address() {
        static int counter = 0;
        return "ipc:///tmp/hapticore_test_pub_" + std::to_string(counter++) + ".ipc";
    }
};

TEST_F(PublisherThreadTest, PublishesStateMessages) {
    auto addr = pub_address();
    TripleBuffer<HapticStateData> state_buffer;

    // Write known data
    auto& state = state_buffer.write_buffer();
    state.timestamp = 99.5;
    state.sequence = 7;
    state.position = {0.1, 0.2, 0.3};
    state.velocity = {0.4, 0.5, 0.6};
    state.force = {1.0, 2.0, 3.0};
    state.active_field = "test_field";
    state.field_state_buf.clear();
    msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
    pk.pack_map(0);
    state_buffer.publish();

    // Start publisher
    zmq::context_t pub_ctx(1);
    PublisherThread publisher(state_buffer, addr, 200.0, pub_ctx);
    std::atomic<bool> pub_stop{false};
    std::thread pub_thread([&publisher, &pub_stop]() {
        publisher.run(pub_stop);
    });

    // Connect subscriber
    zmq::context_t ctx(1);
    zmq::socket_t sub(ctx, zmq::socket_type::sub);
    sub.set(zmq::sockopt::subscribe, "state");
    sub.set(zmq::sockopt::rcvtimeo, 2000);  // 2 second timeout
    sub.connect(addr);

    // Wait for ZMQ slow-joiner (200ms is empirically sufficient for macOS CI;
    // the subsequent re-publish ensures the subscriber gets fresh data)
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    // Re-publish to ensure subscriber gets it
    {
        auto& s = state_buffer.write_buffer();
        s.timestamp = state.timestamp;
        s.sequence = state.sequence;
        s.position = state.position;
        s.velocity = state.velocity;
        s.force = state.force;
        s.active_field = state.active_field;
        s.field_state_buf.clear();
        msgpack::packer<msgpack::sbuffer> pk2(s.field_state_buf);
        pk2.pack_map(0);
    }
    state_buffer.publish();

    // Receive
    zmq::message_t topic_msg, data_msg;
    auto res = sub.recv(topic_msg, zmq::recv_flags::none);
    ASSERT_TRUE(res.has_value());
    EXPECT_EQ(std::string(static_cast<char*>(topic_msg.data()), topic_msg.size()), "state");

    res = sub.recv(data_msg, zmq::recv_flags::none);
    ASSERT_TRUE(res.has_value());

    auto oh = msgpack::unpack(static_cast<const char*>(data_msg.data()), data_msg.size());
    auto& obj = oh.get();
    ASSERT_EQ(obj.type, msgpack::type::MAP);

    auto map = obj.via.map;
    bool found_sequence = false;
    bool found_active_field = false;
    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key_str == "sequence") {
            EXPECT_EQ(map.ptr[i].val.via.u64, 7u);
            found_sequence = true;
        } else if (key_str == "active_field") {
            std::string s(map.ptr[i].val.via.str.ptr, map.ptr[i].val.via.str.size);
            EXPECT_EQ(s, "test_field");
            found_active_field = true;
        }
    }
    EXPECT_TRUE(found_sequence);
    EXPECT_TRUE(found_active_field);

    pub_stop.store(true);
    pub_thread.join();
    sub.close();
    ctx.close();
}

TEST_F(PublisherThreadTest, PublishRate) {
    auto addr = pub_address();
    TripleBuffer<HapticStateData> state_buffer;

    // Pre-fill data
    auto& state = state_buffer.write_buffer();
    state.active_field = "null";
    state.field_state_buf.clear();
    msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
    pk.pack_map(0);
    state_buffer.publish();

    zmq::context_t pub_ctx2(1);
    PublisherThread publisher(state_buffer, addr, 200.0, pub_ctx2);
    std::atomic<bool> pub_stop{false};
    std::thread pub_thread([&publisher, &pub_stop]() {
        publisher.run(pub_stop);
    });

    zmq::context_t ctx(1);
    zmq::socket_t sub(ctx, zmq::socket_type::sub);
    sub.set(zmq::sockopt::subscribe, "state");
    sub.set(zmq::sockopt::rcvtimeo, 2000);
    sub.connect(addr);

    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Continuously publish fresh data
    std::atomic<bool> writer_stop{false};
    std::thread writer([&state_buffer, &writer_stop]() {
        uint64_t seq = 0;
        while (!writer_stop.load(std::memory_order_relaxed)) {
            auto& s = state_buffer.write_buffer();
            s.sequence = seq++;
            s.active_field = "null";
            s.field_state_buf.clear();
            msgpack::packer<msgpack::sbuffer> pk2(s.field_state_buf);
            pk2.pack_map(0);
            state_buffer.publish();
            std::this_thread::sleep_for(std::chrono::microseconds(500));
        }
    });

#ifdef __linux__
    auto start = std::chrono::steady_clock::now();
#endif
    int count = 0;
    while (count < 20) {
        zmq::message_t topic, data;
        auto r = sub.recv(topic, zmq::recv_flags::none);
        if (!r.has_value()) break;
        auto r2 = sub.recv(data, zmq::recv_flags::none);
        ASSERT_TRUE(r2.has_value());
        ++count;
    }
#ifdef __linux__
    auto end = std::chrono::steady_clock::now();
    auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
#endif

    // At 200 Hz, 20 messages should take ~100 ms.
    // On macOS CI (virtualized Apple Silicon), sleep granularity is so poor
    // that wall-clock timing is meaningless — guard timing assertions to Linux.
    EXPECT_GE(count, 20);
#ifdef __linux__
    // Only assert timing on Linux — macOS CI runners have unreliable sleep
    // granularity due to virtualization (see copilot-instructions.md).
    EXPECT_GT(elapsed_ms, 60);   // At least 60 ms
    EXPECT_LT(elapsed_ms, 200);  // No more than 200 ms at 200 Hz
#endif

    writer_stop.store(true);
    writer.join();
    pub_stop.store(true);
    pub_thread.join();
    sub.close();
    ctx.close();
}

// ==================== Command Thread Tests ====================

class CommandThreadTest : public ::testing::Test {
protected:
    std::string cmd_address() {
        static int counter = 0;
        return "ipc:///tmp/hapticore_test_cmd_" + std::to_string(counter++) + ".ipc";
    }
};

TEST_F(CommandThreadTest, EchoHandler) {
    auto addr = cmd_address();

    auto echo_handler = [](const CommandData& cmd) -> CommandResponseData {
        CommandResponseData resp;
        resp.command_id = cmd.command_id;
        resp.success = true;
        return resp;
    };

    zmq::context_t cmd_ctx(1);
    CommandThread commander(addr, echo_handler, cmd_ctx);
    std::atomic<bool> cmd_stop{false};
    std::thread cmd_thread([&commander, &cmd_stop]() {
        commander.run(cmd_stop);
    });

    // Connect DEALER
    zmq::context_t ctx(1);
    zmq::socket_t dealer(ctx, zmq::socket_type::dealer);
    dealer.set(zmq::sockopt::rcvtimeo, 2000);
    dealer.connect(addr);

    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Send a command
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(3);
    pk.pack("command_id"); pk.pack("test_cmd_001");
    pk.pack("method");     pk.pack("heartbeat");
    pk.pack("params");     pk.pack_map(0);

    zmq::message_t empty(0);
    zmq::message_t payload(sbuf.data(), sbuf.size());
    dealer.send(empty, zmq::send_flags::sndmore);
    dealer.send(payload, zmq::send_flags::none);

    // Receive response: [empty_frame, payload]
    zmq::message_t resp_empty, resp_payload;
    auto res = dealer.recv(resp_empty, zmq::recv_flags::none);
    ASSERT_TRUE(res.has_value());
    res = dealer.recv(resp_payload, zmq::recv_flags::none);
    ASSERT_TRUE(res.has_value());

    auto oh = msgpack::unpack(
        static_cast<const char*>(resp_payload.data()), resp_payload.size());
    auto map = oh.get().via.map;

    bool found_id = false;
    bool found_success = false;
    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key_str == "command_id") {
            std::string s(map.ptr[i].val.via.str.ptr, map.ptr[i].val.via.str.size);
            EXPECT_EQ(s, "test_cmd_001");
            found_id = true;
        } else if (key_str == "success") {
            EXPECT_TRUE(map.ptr[i].val.via.boolean);
            found_success = true;
        }
    }
    EXPECT_TRUE(found_id);
    EXPECT_TRUE(found_success);

    cmd_stop.store(true);
    cmd_thread.join();
    dealer.close();
    ctx.close();
}

TEST_F(CommandThreadTest, UnknownMethodReturnsError) {
    auto addr = cmd_address();

    auto handler = [](const CommandData& cmd) -> CommandResponseData {
        CommandResponseData resp;
        resp.command_id = cmd.command_id;
        resp.success = false;
        resp.error = "unknown method: " + cmd.method;
        return resp;
    };

    zmq::context_t cmd_ctx2(1);
    CommandThread commander(addr, handler, cmd_ctx2);
    std::atomic<bool> cmd_stop{false};
    std::thread cmd_thread([&commander, &cmd_stop]() {
        commander.run(cmd_stop);
    });

    zmq::context_t ctx(1);
    zmq::socket_t dealer(ctx, zmq::socket_type::dealer);
    dealer.set(zmq::sockopt::rcvtimeo, 2000);
    dealer.connect(addr);

    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(3);
    pk.pack("command_id"); pk.pack("bad_cmd_001");
    pk.pack("method");     pk.pack("nonexistent_method");
    pk.pack("params");     pk.pack_map(0);

    zmq::message_t empty(0);
    zmq::message_t payload(sbuf.data(), sbuf.size());
    dealer.send(empty, zmq::send_flags::sndmore);
    dealer.send(payload, zmq::send_flags::none);

    zmq::message_t resp_empty, resp_payload;
    auto res = dealer.recv(resp_empty, zmq::recv_flags::none);
    ASSERT_TRUE(res.has_value());
    res = dealer.recv(resp_payload, zmq::recv_flags::none);
    ASSERT_TRUE(res.has_value());

    auto oh = msgpack::unpack(
        static_cast<const char*>(resp_payload.data()), resp_payload.size());
    auto map = oh.get().via.map;

    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key_str == "success") {
            EXPECT_FALSE(map.ptr[i].val.via.boolean);
        } else if (key_str == "error") {
            ASSERT_EQ(map.ptr[i].val.type, msgpack::type::STR);
            std::string s(map.ptr[i].val.via.str.ptr, map.ptr[i].val.via.str.size);
            EXPECT_NE(s.find("nonexistent_method"), std::string::npos);
        }
    }

    cmd_stop.store(true);
    cmd_thread.join();
    dealer.close();
    ctx.close();
}

TEST_F(CommandThreadTest, GarbageDoesNotCrash) {
    auto addr = cmd_address();

    auto handler = [](const CommandData& cmd) -> CommandResponseData {
        CommandResponseData resp;
        resp.command_id = cmd.command_id;
        resp.success = true;
        return resp;
    };

    zmq::context_t cmd_ctx3(1);
    CommandThread commander(addr, handler, cmd_ctx3);
    std::atomic<bool> cmd_stop{false};
    std::thread cmd_thread([&commander, &cmd_stop]() {
        commander.run(cmd_stop);
    });

    zmq::context_t ctx(1);
    zmq::socket_t dealer(ctx, zmq::socket_type::dealer);
    dealer.set(zmq::sockopt::rcvtimeo, 500);
    dealer.connect(addr);

    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Send garbage bytes
    zmq::message_t empty(0);
    zmq::message_t garbage("not valid msgpack!!!", 20);
    dealer.send(empty, zmq::send_flags::sndmore);
    dealer.send(garbage, zmq::send_flags::none);

    // No response should come for garbage (we timeout)
    zmq::message_t resp_empty;
    auto res = dealer.recv(resp_empty, zmq::recv_flags::none);
    // Expected to timeout (no response for malformed)
    EXPECT_FALSE(res.has_value());

    // Send a valid command after garbage to verify thread is still alive
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(3);
    pk.pack("command_id"); pk.pack("after_garbage");
    pk.pack("method");     pk.pack("heartbeat");
    pk.pack("params");     pk.pack_map(0);

    dealer.set(zmq::sockopt::rcvtimeo, 2000);
    zmq::message_t empty2(0);
    zmq::message_t payload(sbuf.data(), sbuf.size());
    dealer.send(empty2, zmq::send_flags::sndmore);
    dealer.send(payload, zmq::send_flags::none);

    zmq::message_t re, rp;
    res = dealer.recv(re, zmq::recv_flags::none);
    ASSERT_TRUE(res.has_value());
    auto res2 = dealer.recv(rp, zmq::recv_flags::none);
    ASSERT_TRUE(res2.has_value());

    auto oh = msgpack::unpack(static_cast<const char*>(rp.data()), rp.size());
    auto map = oh.get().via.map;
    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key_str == "command_id") {
            std::string s(map.ptr[i].val.via.str.ptr, map.ptr[i].val.via.str.size);
            EXPECT_EQ(s, "after_garbage");
        }
    }

    cmd_stop.store(true);
    cmd_thread.join();
    dealer.close();
    ctx.close();
}

// ==================== Haptic Thread Tests ====================

TEST(HapticThreadTest, SpringDamperForceDirection) {
    auto mock = std::make_unique<DhdMock>();
    auto* mock_ptr = mock.get();
    mock_ptr->open();

    // Offset position from center
    mock_ptr->set_mock_position({0.05, 0.0, 0.0});  // 5cm from center
    mock_ptr->set_mock_velocity({0.0, 0.0, 0.0});

    TripleBuffer<HapticStateData> state_buffer;
    HapticThread haptic(std::move(mock), state_buffer, 20.0, 0);

    // Set a spring field
    auto spring = std::make_shared<SpringDamperField>();
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(2);
    pk.pack("stiffness"); pk.pack(200.0);
    pk.pack("damping");   pk.pack(5.0);
    auto oh = msgpack::unpack(sbuf.data(), sbuf.size());
    spring->update_params(oh.get());
    haptic.set_field(spring);

    // Start heartbeat (so it doesn't timeout)
    haptic.update_heartbeat();

    // Run for a short time
    std::atomic<bool> haptic_stop{false};
    std::thread haptic_thread([&haptic, &haptic_stop]() {
        haptic.run(haptic_stop);
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    haptic_stop.store(true);
    haptic_thread.join();

    // Check applied forces direction: spring at center (0,0,0), position at (0.05,0,0)
    // Force should be negative x (restoring)
    auto& forces = mock_ptr->applied_forces();
    ASSERT_GT(forces.size(), 0u);

    // Check the first force is in the right direction
    for (const auto& f : forces) {
        EXPECT_LT(f[0], 0.0) << "Force x should be negative (restoring toward center)";
    }
}

TEST(HapticThreadTest, ForceClamping) {
    auto mock = std::make_unique<DhdMock>();
    auto* mock_ptr = mock.get();
    mock_ptr->open();

    // With stiffness=1000 N/m and offset=0.1m, spring force = 100N,
    // which is well above the 20N force_limit. Verify clamping works.
    mock_ptr->set_mock_position({0.1, 0.0, 0.0});
    mock_ptr->set_mock_velocity({0.0, 0.0, 0.0});

    TripleBuffer<HapticStateData> state_buffer;
    double force_limit = 20.0;
    HapticThread haptic(std::move(mock), state_buffer, force_limit, 0);

    auto spring = std::make_shared<SpringDamperField>();
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(2);
    pk.pack("stiffness"); pk.pack(1000.0);
    pk.pack("damping");   pk.pack(0.0);
    auto oh = msgpack::unpack(sbuf.data(), sbuf.size());
    spring->update_params(oh.get());
    haptic.set_field(spring);

    haptic.update_heartbeat();

    std::atomic<bool> haptic_stop{false};
    std::thread haptic_thread([&haptic, &haptic_stop]() {
        haptic.run(haptic_stop);
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    haptic_stop.store(true);
    haptic_thread.join();

    auto& forces = mock_ptr->applied_forces();
    ASSERT_GT(forces.size(), 0u);

    for (const auto& f : forces) {
        double mag = std::sqrt(f[0]*f[0] + f[1]*f[1] + f[2]*f[2]);
        EXPECT_LE(mag, force_limit + 1e-10)
            << "Force magnitude " << mag << " exceeds limit " << force_limit;
    }
}

TEST(HapticThreadTest, HeartbeatTimeout) {
    auto mock = std::make_unique<DhdMock>();
    auto* mock_ptr = mock.get();
    mock_ptr->open();

    // Position offset to generate force
    mock_ptr->set_mock_position({0.05, 0.0, 0.0});

    TripleBuffer<HapticStateData> state_buffer;
    HapticThread haptic(std::move(mock), state_buffer, 20.0, 0);

    // Set a spring field
    auto spring = std::make_shared<SpringDamperField>();
    haptic.set_field(spring);

    // Send one heartbeat to arm the timeout (otherwise it never triggers since
    // last_heartbeat_time_ starts at 0.0)
    haptic.update_heartbeat();

    std::atomic<bool> haptic_stop{false};
    std::thread haptic_thread([&haptic, &haptic_stop]() {
        haptic.run(haptic_stop);
    });

    // Wait for heartbeat to expire (500ms + margin)
    std::this_thread::sleep_for(std::chrono::milliseconds(700));

    haptic_stop.store(true);
    haptic_thread.join();

    // After timeout, forces should approach zero (NullField with damping only,
    // stiffness=0). With zero velocity, damping force is also zero.
    auto& forces = mock_ptr->applied_forces();
    ASSERT_GT(forces.size(), 0u);

    // Check the last few forces are near zero (velocity is 0, so damping-only
    // field with stiffness=0 produces zero force)
    size_t n = forces.size();
    size_t check_start = n > 10 ? n - 10 : 0;
    for (size_t i = check_start; i < n; ++i) {
        double mag = std::sqrt(forces[i][0]*forces[i][0] +
                               forces[i][1]*forces[i][1] +
                               forces[i][2]*forces[i][2]);
        EXPECT_NEAR(mag, 0.0, 0.01)
            << "Force magnitude at tick " << i << " should be near zero after heartbeat timeout";
    }
}

TEST(HapticThreadTest, SequenceMonotonicallyIncreasing) {
    auto mock = std::make_unique<DhdMock>();
    mock->open();

    TripleBuffer<HapticStateData> state_buffer;
    HapticThread haptic(std::move(mock), state_buffer, 20.0, 0);

    haptic.update_heartbeat();

    std::atomic<bool> haptic_stop{false};
    std::thread haptic_thread([&haptic, &haptic_stop]() {
        haptic.run(haptic_stop);
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    haptic_stop.store(true);
    haptic_thread.join();

    // Read from triple buffer — should have the latest sequence
    state_buffer.swap_read_buffer();
    const auto& state = state_buffer.read_buffer();
    EXPECT_GT(state.sequence, 0u);

    // The sequence should be consistent (last written value)
    // We can't easily verify monotonicity without reading multiple times,
    // but we can verify it's a reasonable number for ~30ms at 4kHz
    EXPECT_GT(state.sequence, 50u);   // Loop actually ran
#ifdef __linux__
    // Upper bound only meaningful with reliable sleep granularity.
    // macOS CI runners oversleep by 4-11x (see copilot-instructions.md).
    EXPECT_LT(state.sequence, 500u);
#endif
}

// ==================== Mock Position Injection Tests ====================
// Only compiled in mock-hardware builds

#ifdef HAPTIC_MOCK_HARDWARE

namespace {

/// Send a command via ZMQ DEALER socket using the protocol framing.
void send_zmq_command(zmq::socket_t& dealer, const std::string& id,
                      const std::string& method, const msgpack::sbuffer& params) {
    msgpack::sbuffer cmd_buf;
    msgpack::packer<msgpack::sbuffer> pk(cmd_buf);
    pk.pack_map(3);
    pk.pack("command_id"); pk.pack(id);
    pk.pack("method");     pk.pack(method);
    pk.pack("params");
    cmd_buf.write(params.data(), params.size());

    zmq::message_t empty(0);
    zmq::message_t payload(cmd_buf.data(), cmd_buf.size());
    dealer.send(empty, zmq::send_flags::sndmore);
    dealer.send(payload, zmq::send_flags::none);
}

/// Receive a command response from DEALER socket.
/// Returns true if a response with success=true was received within the socket timeout.
bool recv_response(zmq::socket_t& dealer) {
    zmq::message_t empty, payload;
    auto r1 = dealer.recv(empty, zmq::recv_flags::none);
    if (!r1.has_value()) return false;
    auto r2 = dealer.recv(payload, zmq::recv_flags::none);
    if (!r2.has_value()) return false;

    auto oh = msgpack::unpack(static_cast<const char*>(payload.data()), payload.size());
    auto map = oh.get().via.map;
    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key == "success") return map.ptr[i].val.via.boolean;
    }
    return false;
}

/// Extract a Vec3 from a msgpack map value keyed by key_name.
std::optional<Vec3> test_parse_vec3_param(const msgpack::object& params, const char* key_name) {
    if (params.type != msgpack::type::MAP) return std::nullopt;
    auto map = params.via.map;
    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;
        if (key.type != msgpack::type::STR) continue;
        std::string k(key.via.str.ptr, key.via.str.size);
        if (k == key_name) {
            if (val.type != msgpack::type::ARRAY || val.via.array.size != 3) return std::nullopt;
            Vec3 v{};
            for (int j = 0; j < 3; ++j) {
                auto& elem = val.via.array.ptr[j];
                if (elem.type == msgpack::type::FLOAT64)
                    v[j] = elem.via.f64;
                else if (elem.type == msgpack::type::FLOAT32)
                    // msgpack-cxx promotes FLOAT32 to double in via.f64 — there is no via.f32 member.
                    v[j] = static_cast<double>(elem.via.f64);
                else if (elem.type == msgpack::type::POSITIVE_INTEGER)
                    v[j] = static_cast<double>(elem.via.u64);
                else if (elem.type == msgpack::type::NEGATIVE_INTEGER)
                    v[j] = static_cast<double>(elem.via.i64);
                else
                    return std::nullopt;
            }
            return v;
        }
    }
    return std::nullopt;
}

/// Drain all currently buffered messages from a SUB socket without blocking.
/// Call this after the slow-joiner wait and before checking for specific messages,
/// to discard stale state snapshots published during the connection window.
void drain_sub_socket(zmq::socket_t& sub) {
    sub.set(zmq::sockopt::rcvtimeo, 0);
    zmq::message_t msg;
    while (sub.recv(msg, zmq::recv_flags::none).has_value()) {
        // drain all frames in this multipart message
        while (sub.get(zmq::sockopt::rcvmore)) {
            zmq::message_t extra;
            sub.recv(extra, zmq::recv_flags::none);
        }
    }
    sub.set(zmq::sockopt::rcvtimeo, 2000);
}

}  // namespace

class MockPositionInjectionTest : public ::testing::Test {
protected:
    std::string pub_address() {
        static int counter = 0;
        return "ipc:///tmp/hapticore_test_mock_pos_pub_" + std::to_string(counter++) + ".ipc";
    }
    std::string cmd_address() {
        static int counter = 0;
        return "ipc:///tmp/hapticore_test_mock_pos_cmd_" + std::to_string(counter++) + ".ipc";
    }
};

TEST_F(MockPositionInjectionTest, SetMockPositionAppearsInPublishedState) {
    auto pub_addr = pub_address();
    auto cmd_addr = cmd_address();

    // 1. Create DhdMock + HapticThread
    auto mock = std::make_unique<DhdMock>();
    mock->open();
    auto* mock_dhd = static_cast<DhdMock*>(mock.get());

    TripleBuffer<HapticStateData> state_buffer;
    HapticThread haptic(std::move(mock), state_buffer, 20.0, 0);

    // 2. Build command handler
    auto command_handler = [&haptic, mock_dhd](const CommandData& cmd) -> CommandResponseData {
        CommandResponseData resp;
        resp.command_id = cmd.command_id;
        if (cmd.method == "heartbeat") {
            haptic.update_heartbeat();
            resp.success = true;
            msgpack::packer<msgpack::sbuffer> pk(resp.result_buf);
            pk.pack_map(1); pk.pack("timeout_ms"); pk.pack(500);
        } else if (cmd.method == "set_mock_position") {
            auto vec = test_parse_vec3_param(cmd.params.get(), "position");
            if (!vec) {
                resp.success = false;
                resp.error = "set_mock_position: invalid params";
            } else {
                mock_dhd->set_mock_position(*vec);
                resp.success = true;
            }
        } else {
            resp.success = false;
            resp.error = "unknown method: " + cmd.method;
        }
        return resp;
    };

    // 3. Create ZMQ context and threads
    zmq::context_t ctx(1);
    PublisherThread publisher(state_buffer, pub_addr, 200.0, ctx);
    CommandThread commander(cmd_addr, command_handler, ctx);

    std::atomic<bool> stop_flag{false};
    std::thread haptic_thread([&haptic, &stop_flag]() { haptic.run(stop_flag); });
    std::thread pub_thread([&publisher, &stop_flag]() { publisher.run(stop_flag); });
    std::thread cmd_thread([&commander, &stop_flag]() { commander.run(stop_flag); });

    // 4. Connect sockets
    zmq::socket_t dealer(ctx, zmq::socket_type::dealer);
    zmq::socket_t sub(ctx, zmq::socket_type::sub);
    dealer.set(zmq::sockopt::rcvtimeo, 2000);
    sub.set(zmq::sockopt::subscribe, "state");
    sub.set(zmq::sockopt::rcvtimeo, 2000);
    dealer.connect(cmd_addr);
    sub.connect(pub_addr);

    // 5. Wait for slow-joiner
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    // Drain stale state messages that buffered during the slow-joiner wait
    // (publisher runs at 200 Hz during the wait → ~40 stale messages).
    // Without this drain the recv loop below would consume those old messages
    // (position = 0,0,0) and never see the injected position.
    drain_sub_socket(sub);

    // 6. Send heartbeat
    {
        msgpack::sbuffer params;
        msgpack::packer<msgpack::sbuffer> pk(params);
        pk.pack_map(0);
        send_zmq_command(dealer, "hb-1", "heartbeat", params);
        ASSERT_TRUE(recv_response(dealer));
    }

    // 7. Send set_mock_position with position [0.1, -0.05, 0.0]
    const Vec3 target_pos = {0.1, -0.05, 0.0};
    {
        msgpack::sbuffer params;
        msgpack::packer<msgpack::sbuffer> pk(params);
        pk.pack_map(1);
        pk.pack("position");
        pk.pack_array(3);
        pk.pack(target_pos[0]);
        pk.pack(target_pos[1]);
        pk.pack(target_pos[2]);
        send_zmq_command(dealer, "pos-1", "set_mock_position", params);
        ASSERT_TRUE(recv_response(dealer));
    }

    // 8. Wait for at least one publish cycle after position injection,
    //    then read messages until we see the updated position.
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    Vec3 read_pos = {-999.0, -999.0, -999.0};
    bool found_pos = false;
    for (int attempt = 0; attempt < 20 && !found_pos; ++attempt) {
        zmq::message_t topic_msg, data_msg;
        auto r = sub.recv(topic_msg, zmq::recv_flags::none);
        if (!r.has_value()) break;
        auto r2 = sub.recv(data_msg, zmq::recv_flags::none);
        if (!r2.has_value()) break;

        auto oh = msgpack::unpack(static_cast<const char*>(data_msg.data()), data_msg.size());
        auto map = oh.get().via.map;
        for (uint32_t i = 0; i < map.size; ++i) {
            std::string key(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
            if (key == "position" && map.ptr[i].val.type == msgpack::type::ARRAY) {
                auto arr = map.ptr[i].val.via.array;
                if (arr.size == 3) {
                    read_pos[0] = arr.ptr[0].via.f64;
                    read_pos[1] = arr.ptr[1].via.f64;
                    read_pos[2] = arr.ptr[2].via.f64;
                    if (std::abs(read_pos[0] - target_pos[0]) < 1e-9) {
                        found_pos = true;
                    }
                }
            }
        }
    }

    // 9. Stop all threads
    stop_flag.store(true);
    haptic_thread.join();
    pub_thread.join();
    cmd_thread.join();
    dealer.close();
    sub.close();
    ctx.close();

    // 10. Assertions
    ASSERT_TRUE(found_pos) << "Did not receive state with expected position";
    EXPECT_NEAR(read_pos[0], target_pos[0], 1e-9);
    EXPECT_NEAR(read_pos[1], target_pos[1], 1e-9);
    EXPECT_NEAR(read_pos[2], target_pos[2], 1e-9);
}

TEST_F(MockPositionInjectionTest, SetMockPositionWithCartPendulumProducesFieldState) {
    auto pub_addr = pub_address();
    auto cmd_addr = cmd_address();

    // 1. Create DhdMock + HapticThread
    auto mock = std::make_unique<DhdMock>();
    mock->open();
    auto* mock_dhd = static_cast<DhdMock*>(mock.get());

    TripleBuffer<HapticStateData> state_buffer;
    HapticThread haptic(std::move(mock), state_buffer, 20.0, 0);

    // 2. Build command handler (handles heartbeat, set_force_field, set_mock_position)
    auto command_handler = [&haptic, mock_dhd](const CommandData& cmd) -> CommandResponseData {
        CommandResponseData resp;
        resp.command_id = cmd.command_id;
        if (cmd.method == "heartbeat") {
            haptic.update_heartbeat();
            resp.success = true;
            msgpack::packer<msgpack::sbuffer> pk(resp.result_buf);
            pk.pack_map(1); pk.pack("timeout_ms"); pk.pack(500);
        } else if (cmd.method == "set_force_field") {
            const auto& params_obj = cmd.params.get();
            std::string field_type;
            const msgpack::object* field_params_ptr = nullptr;
            if (params_obj.type == msgpack::type::MAP) {
                auto m = params_obj.via.map;
                for (uint32_t i = 0; i < m.size; ++i) {
                    std::string k(m.ptr[i].key.via.str.ptr, m.ptr[i].key.via.str.size);
                    if (k == "type" && m.ptr[i].val.type == msgpack::type::STR)
                        field_type = std::string(m.ptr[i].val.via.str.ptr, m.ptr[i].val.via.str.size);
                    else if (k == "params")
                        field_params_ptr = &m.ptr[i].val;
                }
            }
            auto new_field = create_field(field_type);
            if (!new_field) {
                resp.success = false;
                resp.error = "set_force_field: unknown field type '" + field_type + "'";
            } else {
                if (field_params_ptr) new_field->update_params(*field_params_ptr);
                haptic.set_field(std::shared_ptr<ForceField>(std::move(new_field)));
                resp.success = true;
                msgpack::packer<msgpack::sbuffer> pk(resp.result_buf);
                pk.pack_map(1); pk.pack("active_field"); pk.pack(field_type);
            }
        } else if (cmd.method == "set_mock_position") {
            auto vec = test_parse_vec3_param(cmd.params.get(), "position");
            if (!vec) {
                resp.success = false;
                resp.error = "set_mock_position: invalid params";
            } else {
                mock_dhd->set_mock_position(*vec);
                resp.success = true;
            }
        } else {
            resp.success = false;
            resp.error = "unknown method: " + cmd.method;
        }
        return resp;
    };

    // 3. Create ZMQ context and threads
    zmq::context_t ctx(1);
    PublisherThread publisher(state_buffer, pub_addr, 200.0, ctx);
    CommandThread commander(cmd_addr, command_handler, ctx);

    std::atomic<bool> stop_flag{false};
    std::thread haptic_thread([&haptic, &stop_flag]() { haptic.run(stop_flag); });
    std::thread pub_thread([&publisher, &stop_flag]() { publisher.run(stop_flag); });
    std::thread cmd_thread([&commander, &stop_flag]() { commander.run(stop_flag); });

    // 4. Connect sockets
    zmq::socket_t dealer(ctx, zmq::socket_type::dealer);
    zmq::socket_t sub(ctx, zmq::socket_type::sub);
    dealer.set(zmq::sockopt::rcvtimeo, 2000);
    sub.set(zmq::sockopt::subscribe, "state");
    sub.set(zmq::sockopt::rcvtimeo, 2000);
    dealer.connect(cmd_addr);
    sub.connect(pub_addr);

    // 5. Wait for slow-joiner
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    // Drain stale state messages that buffered during the slow-joiner wait.
    drain_sub_socket(sub);

    // 6. Send heartbeat
    {
        msgpack::sbuffer params;
        msgpack::packer<msgpack::sbuffer> pk(params);
        pk.pack_map(0);
        send_zmq_command(dealer, "hb-2", "heartbeat", params);
        ASSERT_TRUE(recv_response(dealer));
    }

    // 7. Send set_force_field(type=cart_pendulum, params={pendulum_length: 0.3})
    {
        msgpack::sbuffer params;
        msgpack::packer<msgpack::sbuffer> pk(params);
        pk.pack_map(2);
        pk.pack("type"); pk.pack("cart_pendulum");
        pk.pack("params");
        pk.pack_map(1);
        pk.pack("pendulum_length"); pk.pack(0.3);
        send_zmq_command(dealer, "ff-1", "set_force_field", params);
        ASSERT_TRUE(recv_response(dealer));
    }

    // 8. Send set_mock_position({0.05, 0.0, 0.0})
    {
        msgpack::sbuffer params;
        msgpack::packer<msgpack::sbuffer> pk(params);
        pk.pack_map(1);
        pk.pack("position");
        pk.pack_array(3);
        pk.pack(0.05); pk.pack(0.0); pk.pack(0.0);
        send_zmq_command(dealer, "pos-2", "set_mock_position", params);
        ASSERT_TRUE(recv_response(dealer));
    }

    // 9. Wait for at least one publish cycle after position and field injection.
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    // 10. Read published state and verify field_state contains cart_pendulum keys
    bool found_cup_x = false, found_ball_x = false, found_ball_y = false, found_spilled = false;
    for (int attempt = 0; attempt < 20; ++attempt) {
        zmq::message_t topic_msg, data_msg;
        auto r = sub.recv(topic_msg, zmq::recv_flags::none);
        if (!r.has_value()) break;
        auto r2 = sub.recv(data_msg, zmq::recv_flags::none);
        if (!r2.has_value()) break;

        auto oh = msgpack::unpack(static_cast<const char*>(data_msg.data()), data_msg.size());
        auto map = oh.get().via.map;
        for (uint32_t i = 0; i < map.size; ++i) {
            std::string key(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
            if (key == "field_state" && map.ptr[i].val.type == msgpack::type::MAP) {
                auto fs_map = map.ptr[i].val.via.map;
                for (uint32_t j = 0; j < fs_map.size; ++j) {
                    std::string fs_key(fs_map.ptr[j].key.via.str.ptr, fs_map.ptr[j].key.via.str.size);
                    if (fs_key == "cup_x")   found_cup_x   = true;
                    if (fs_key == "ball_x")  found_ball_x  = true;
                    if (fs_key == "ball_y")  found_ball_y  = true;
                    if (fs_key == "spilled") found_spilled = true;
                }
                if (found_cup_x && found_ball_x && found_ball_y && found_spilled) break;
            }
        }
        if (found_cup_x && found_ball_x && found_ball_y && found_spilled) break;
    }

    // 11. Stop all threads
    stop_flag.store(true);
    haptic_thread.join();
    pub_thread.join();
    cmd_thread.join();
    dealer.close();
    sub.close();
    ctx.close();

    // 12. Assertions
    EXPECT_TRUE(found_cup_x)   << "field_state missing 'cup_x'";
    EXPECT_TRUE(found_ball_x)  << "field_state missing 'ball_x'";
    EXPECT_TRUE(found_ball_y)  << "field_state missing 'ball_y'";
    EXPECT_TRUE(found_spilled) << "field_state missing 'spilled'";
}

#endif  // HAPTIC_MOCK_HARDWARE

