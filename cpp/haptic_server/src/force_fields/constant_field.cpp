#include "constant_field.hpp"

Vec3 ConstantField::compute(const Vec3& /*pos*/, const Vec3& /*vel*/, double /*dt*/) {
    return force_;
}

std::string ConstantField::name() const {
    return "constant";
}

bool ConstantField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;

    auto map = params.via.map;
    bool found_force = false;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;

        if (key.type != msgpack::type::STR) continue;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "force") {
            if (val.type != msgpack::type::ARRAY || val.via.array.size != 3) return false;
            for (int j = 0; j < 3; ++j) {
                auto& elem = val.via.array.ptr[j];
                if (elem.type == msgpack::type::FLOAT64) {
                    force_[static_cast<size_t>(j)] = elem.via.f64;
                } else if (elem.type == msgpack::type::FLOAT32) {
                    force_[static_cast<size_t>(j)] = elem.via.f64;
                } else if (elem.type == msgpack::type::POSITIVE_INTEGER) {
                    force_[static_cast<size_t>(j)] = static_cast<double>(elem.via.u64);
                } else if (elem.type == msgpack::type::NEGATIVE_INTEGER) {
                    force_[static_cast<size_t>(j)] = static_cast<double>(elem.via.i64);
                } else {
                    return false;
                }
            }
            found_force = true;
        }
    }

    return found_force;
}
