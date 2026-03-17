#pragma once

#include <map>
#include <string>

#include <msgpack.hpp>

/// Deserialized command from a DEALER client.
struct CommandData {
    std::string command_id;
    std::string method;
    msgpack::object_handle params;  // holds the params map

    /// Deserialize a command from raw msgpack bytes.
    /// Expects a map with keys: "command_id", "method", "params".
    static CommandData unpack(const char* data, size_t len) {
        CommandData cmd;
        auto oh = msgpack::unpack(data, len);
        const auto& obj = oh.get();

        if (obj.type != msgpack::type::MAP) {
            throw std::runtime_error("CommandData: expected map");
        }

        auto map = obj.via.map;
        bool has_id = false;
        bool has_method = false;
        const msgpack::object* params_obj = nullptr;

        for (uint32_t i = 0; i < map.size; ++i) {
            auto& key = map.ptr[i].key;
            auto& val = map.ptr[i].val;

            if (key.type != msgpack::type::STR) continue;
            std::string key_str(key.via.str.ptr, key.via.str.size);

            if (key_str == "command_id") {
                if (val.type != msgpack::type::STR) {
                    throw std::runtime_error("CommandData: command_id must be string");
                }
                cmd.command_id = std::string(val.via.str.ptr, val.via.str.size);
                has_id = true;
            } else if (key_str == "method") {
                if (val.type != msgpack::type::STR) {
                    throw std::runtime_error("CommandData: method must be string");
                }
                cmd.method = std::string(val.via.str.ptr, val.via.str.size);
                has_method = true;
            } else if (key_str == "params") {
                params_obj = &val;
            }
        }

        if (!has_id || !has_method) {
            throw std::runtime_error("CommandData: missing command_id or method");
        }

        // Deep-copy the params object into its own object_handle so it outlives
        // the original unpack zone.
        if (params_obj != nullptr) {
            msgpack::sbuffer sbuf;
            msgpack::pack(sbuf, *params_obj);
            cmd.params = msgpack::unpack(sbuf.data(), sbuf.size());
        } else {
            // Default to an empty map
            msgpack::sbuffer sbuf;
            msgpack::packer<msgpack::sbuffer> pk(sbuf);
            pk.pack_map(0);
            cmd.params = msgpack::unpack(sbuf.data(), sbuf.size());
        }

        return cmd;
    }
};

/// Response to be sent back to the DEALER client.
struct CommandResponseData {
    std::string command_id;
    bool success = false;
    std::map<std::string, msgpack::object> result;
    std::string error;

    /// Pack the response into the provided buffer.
    /// Produces a msgpack map with keys: "command_id", "success", "result", "error".
    /// The "error" field is msgpack nil when empty (matching Python's None).
    void pack(msgpack::sbuffer& buf) const {
        msgpack::packer<msgpack::sbuffer> pk(buf);
        pk.pack_map(4);
        pk.pack("command_id"); pk.pack(command_id);
        pk.pack("success");    pk.pack(success);
        pk.pack("result");     pk.pack(result);
        pk.pack("error");
        if (error.empty()) {
            pk.pack_nil();
        } else {
            pk.pack(error);
        }
    }
};
