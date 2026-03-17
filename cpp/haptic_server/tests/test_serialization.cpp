#include <gtest/gtest.h>
#include <msgpack.hpp>

#include "state_data.hpp"
#include "command_data.hpp"

// ==================== HapticStateData Tests ====================

TEST(HapticStateDataTest, PackProducesMapWith7Keys) {
    HapticStateData state;
    state.timestamp = 1234.567;
    state.sequence = 42;
    state.position = {0.1, 0.2, 0.3};
    state.velocity = {-0.01, 0.0, 0.05};
    state.force = {1.0, -2.0, 0.5};
    state.active_field = "spring_damper";

    // Pre-pack an empty field state
    msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
    pk.pack_map(0);

    msgpack::sbuffer buf;
    state.pack(buf);

    auto oh = msgpack::unpack(buf.data(), buf.size());
    const auto& obj = oh.get();

    // Must be a map
    ASSERT_EQ(obj.type, msgpack::type::MAP);
    ASSERT_EQ(obj.via.map.size, 7u);
}

TEST(HapticStateDataTest, PackedValuesMatchInput) {
    HapticStateData state;
    state.timestamp = 1234.567;
    state.sequence = 42;
    state.position = {0.1, 0.2, 0.3};
    state.velocity = {-0.01, 0.0, 0.05};
    state.force = {1.0, -2.0, 0.5};
    state.active_field = "spring_damper";

    msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
    pk.pack_map(0);

    msgpack::sbuffer buf;
    state.pack(buf);

    auto oh = msgpack::unpack(buf.data(), buf.size());
    const auto& obj = oh.get();
    auto map = obj.via.map;

    // Extract values by key
    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "timestamp") {
            EXPECT_DOUBLE_EQ(val.via.f64, 1234.567);
        } else if (key_str == "sequence") {
            EXPECT_EQ(val.via.u64, 42u);
        } else if (key_str == "position") {
            ASSERT_EQ(val.type, msgpack::type::ARRAY);
            ASSERT_EQ(val.via.array.size, 3u);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[0].via.f64, 0.1);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[1].via.f64, 0.2);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[2].via.f64, 0.3);
        } else if (key_str == "velocity") {
            ASSERT_EQ(val.type, msgpack::type::ARRAY);
            ASSERT_EQ(val.via.array.size, 3u);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[0].via.f64, -0.01);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[1].via.f64, 0.0);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[2].via.f64, 0.05);
        } else if (key_str == "force") {
            ASSERT_EQ(val.type, msgpack::type::ARRAY);
            ASSERT_EQ(val.via.array.size, 3u);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[0].via.f64, 1.0);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[1].via.f64, -2.0);
            EXPECT_DOUBLE_EQ(val.via.array.ptr[2].via.f64, 0.5);
        } else if (key_str == "active_field") {
            ASSERT_EQ(val.type, msgpack::type::STR);
            std::string s(val.via.str.ptr, val.via.str.size);
            EXPECT_EQ(s, "spring_damper");
        } else if (key_str == "field_state") {
            ASSERT_EQ(val.type, msgpack::type::MAP);
            EXPECT_EQ(val.via.map.size, 0u);
        }
    }
}

TEST(HapticStateDataTest, PackedOutputIsMapType) {
    HapticStateData state;
    msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
    pk.pack_map(0);

    msgpack::sbuffer buf;
    state.pack(buf);

    // The first byte of msgpack map with <= 15 elements is 0x80-0x8f (fixmap)
    // For exactly 7 keys: 0x87
    auto first_byte = static_cast<uint8_t>(buf.data()[0]);
    EXPECT_GE(first_byte, 0x80u);
    EXPECT_LE(first_byte, 0x8fu);
    EXPECT_EQ(first_byte, 0x87u);  // fixmap with 7 entries
}

TEST(HapticStateDataTest, Vec3SerializeAs3ElementArrays) {
    HapticStateData state;
    state.position = {1.5, 2.5, 3.5};
    msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
    pk.pack_map(0);

    msgpack::sbuffer buf;
    state.pack(buf);

    auto oh = msgpack::unpack(buf.data(), buf.size());
    auto map = oh.get().via.map;

    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key_str == "position") {
            auto& val = map.ptr[i].val;
            ASSERT_EQ(val.type, msgpack::type::ARRAY);
            ASSERT_EQ(val.via.array.size, 3u);
            for (uint32_t j = 0; j < 3; ++j) {
                auto elem_type = val.via.array.ptr[j].type;
                EXPECT_TRUE(elem_type == msgpack::type::FLOAT64 ||
                            elem_type == msgpack::type::FLOAT32)
                    << "Element " << j << " should be float type";
            }
        }
    }
}

TEST(HapticStateDataTest, FieldStatePrePackedCorrectly) {
    HapticStateData state;
    state.active_field = "cart_pendulum";

    // Pre-pack a field state with some data
    {
        msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
        pk.pack_map(2);
        pk.pack("phi");    pk.pack(0.5);
        pk.pack("spilled"); pk.pack(false);
    }

    msgpack::sbuffer buf;
    state.pack(buf);

    auto oh = msgpack::unpack(buf.data(), buf.size());
    auto map = oh.get().via.map;

    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key_str == "field_state") {
            auto& val = map.ptr[i].val;
            ASSERT_EQ(val.type, msgpack::type::MAP);
            ASSERT_EQ(val.via.map.size, 2u);

            // Check phi key
            auto& fs_map = val.via.map;
            bool found_phi = false;
            bool found_spilled = false;
            for (uint32_t j = 0; j < fs_map.size; ++j) {
                std::string fk(fs_map.ptr[j].key.via.str.ptr, fs_map.ptr[j].key.via.str.size);
                if (fk == "phi") {
                    EXPECT_DOUBLE_EQ(fs_map.ptr[j].val.via.f64, 0.5);
                    found_phi = true;
                } else if (fk == "spilled") {
                    EXPECT_EQ(fs_map.ptr[j].val.via.boolean, false);
                    found_spilled = true;
                }
            }
            EXPECT_TRUE(found_phi);
            EXPECT_TRUE(found_spilled);
        }
    }
}

TEST(HapticStateDataTest, EmptyFieldStateBufProducesEmptyMap) {
    HapticStateData state;
    // field_state_buf is empty (no pre-pack)

    msgpack::sbuffer buf;
    state.pack(buf);

    auto oh = msgpack::unpack(buf.data(), buf.size());
    auto map = oh.get().via.map;

    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        if (key_str == "field_state") {
            auto& val = map.ptr[i].val;
            ASSERT_EQ(val.type, msgpack::type::MAP);
            EXPECT_EQ(val.via.map.size, 0u);
        }
    }
}

// ==================== CommandData Tests ====================

TEST(CommandDataTest, UnpackValidCommand) {
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(3);
    pk.pack("command_id"); pk.pack("abc123");
    pk.pack("method");     pk.pack("set_force_field");
    pk.pack("params");
    pk.pack_map(1);
    pk.pack("type"); pk.pack("spring_damper");

    auto cmd = CommandData::unpack(sbuf.data(), sbuf.size());
    EXPECT_EQ(cmd.command_id, "abc123");
    EXPECT_EQ(cmd.method, "set_force_field");
    EXPECT_EQ(cmd.params.get().type, msgpack::type::MAP);
}

TEST(CommandDataTest, UnpackMissingCommandIdThrows) {
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(2);
    pk.pack("method"); pk.pack("heartbeat");
    pk.pack("params"); pk.pack_map(0);

    EXPECT_THROW(CommandData::unpack(sbuf.data(), sbuf.size()), std::runtime_error);
}

TEST(CommandDataTest, UnpackMissingMethodThrows) {
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(2);
    pk.pack("command_id"); pk.pack("abc123");
    pk.pack("params"); pk.pack_map(0);

    EXPECT_THROW(CommandData::unpack(sbuf.data(), sbuf.size()), std::runtime_error);
}

TEST(CommandDataTest, UnpackMissingParamsGetsEmptyMap) {
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(2);
    pk.pack("command_id"); pk.pack("abc123");
    pk.pack("method");     pk.pack("heartbeat");

    auto cmd = CommandData::unpack(sbuf.data(), sbuf.size());
    EXPECT_EQ(cmd.command_id, "abc123");
    EXPECT_EQ(cmd.method, "heartbeat");
    EXPECT_EQ(cmd.params.get().type, msgpack::type::MAP);
    EXPECT_EQ(cmd.params.get().via.map.size, 0u);
}

TEST(CommandDataTest, UnpackNonMapThrows) {
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_array(3);
    pk.pack("abc123");
    pk.pack("heartbeat");
    pk.pack_map(0);

    EXPECT_THROW(CommandData::unpack(sbuf.data(), sbuf.size()), std::runtime_error);
}

// ==================== CommandResponseData Tests ====================

TEST(CommandResponseDataTest, PackSuccessResponse) {
    CommandResponseData resp;
    resp.command_id = "resp123";
    resp.success = true;
    // empty result map and empty error

    msgpack::sbuffer buf;
    resp.pack(buf);

    auto oh = msgpack::unpack(buf.data(), buf.size());
    auto& obj = oh.get();
    ASSERT_EQ(obj.type, msgpack::type::MAP);
    ASSERT_EQ(obj.via.map.size, 4u);

    auto map = obj.via.map;
    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        auto& val = map.ptr[i].val;

        if (key_str == "command_id") {
            ASSERT_EQ(val.type, msgpack::type::STR);
            std::string s(val.via.str.ptr, val.via.str.size);
            EXPECT_EQ(s, "resp123");
        } else if (key_str == "success") {
            ASSERT_EQ(val.type, msgpack::type::BOOLEAN);
            EXPECT_TRUE(val.via.boolean);
        } else if (key_str == "result") {
            ASSERT_EQ(val.type, msgpack::type::MAP);
            EXPECT_EQ(val.via.map.size, 0u);
        } else if (key_str == "error") {
            // Empty error should be nil
            EXPECT_EQ(val.type, msgpack::type::NIL);
        }
    }
}

TEST(CommandResponseDataTest, PackErrorResponse) {
    CommandResponseData resp;
    resp.command_id = "err456";
    resp.success = false;
    resp.error = "something went wrong";

    msgpack::sbuffer buf;
    resp.pack(buf);

    auto oh = msgpack::unpack(buf.data(), buf.size());
    auto map = oh.get().via.map;

    for (uint32_t i = 0; i < map.size; ++i) {
        std::string key_str(map.ptr[i].key.via.str.ptr, map.ptr[i].key.via.str.size);
        auto& val = map.ptr[i].val;

        if (key_str == "command_id") {
            std::string s(val.via.str.ptr, val.via.str.size);
            EXPECT_EQ(s, "err456");
        } else if (key_str == "success") {
            EXPECT_FALSE(val.via.boolean);
        } else if (key_str == "error") {
            ASSERT_EQ(val.type, msgpack::type::STR);
            std::string s(val.via.str.ptr, val.via.str.size);
            EXPECT_EQ(s, "something went wrong");
        }
    }
}

TEST(CommandResponseDataTest, PackedOutputIsMap) {
    CommandResponseData resp;
    resp.command_id = "test";
    resp.success = true;

    msgpack::sbuffer buf;
    resp.pack(buf);

    auto first_byte = static_cast<uint8_t>(buf.data()[0]);
    // fixmap range: 0x80-0x8f
    EXPECT_GE(first_byte, 0x80u);
    EXPECT_LE(first_byte, 0x8fu);
    EXPECT_EQ(first_byte, 0x84u);  // fixmap with 4 entries
}
