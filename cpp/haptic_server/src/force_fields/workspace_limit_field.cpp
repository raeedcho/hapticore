#include "workspace_limit_field.hpp"
#include "msgpack_helpers.hpp"

namespace {

bool try_get_bounds_axis(const msgpack::object& obj, double& min_val, double& max_val) {
    if (obj.type != msgpack::type::ARRAY || obj.via.array.size != 2) return false;
    if (!haptic::try_get_double(obj.via.array.ptr[0], min_val)) return false;
    if (!haptic::try_get_double(obj.via.array.ptr[1], max_val)) return false;
    return min_val <= max_val;
}

} // namespace

Vec3 WorkspaceLimitField::compute(const Vec3& pos, const Vec3& vel, double /*dt*/) {
    Vec3 force = {0.0, 0.0, 0.0};
    in_bounds_ = true;

    for (int i = 0; i < 3; ++i) {
        auto idx = static_cast<size_t>(i);
        if (pos[idx] < bounds_min_[idx]) {
            force[idx] = stiffness_ * (bounds_min_[idx] - pos[idx]) - damping_ * vel[idx];
            in_bounds_ = false;
        } else if (pos[idx] > bounds_max_[idx]) {
            force[idx] = stiffness_ * (bounds_max_[idx] - pos[idx]) - damping_ * vel[idx];
            in_bounds_ = false;
        }
    }

    return force;
}

std::string WorkspaceLimitField::name() const {
    return "workspace_limit";
}

bool WorkspaceLimitField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;

    auto map = params.via.map;
    Vec3 new_min = bounds_min_;
    Vec3 new_max = bounds_max_;
    double new_stiffness = stiffness_;
    double new_damping = damping_;
    bool has_any = false;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;

        if (key.type != msgpack::type::STR) continue;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "bounds") {
            if (val.type != msgpack::type::MAP) return false;
            auto bounds_map = val.via.map;
            for (uint32_t j = 0; j < bounds_map.size; ++j) {
                auto& bkey = bounds_map.ptr[j].key;
                auto& bval = bounds_map.ptr[j].val;
                if (bkey.type != msgpack::type::STR) continue;
                std::string axis(bkey.via.str.ptr, bkey.via.str.size);
                size_t axis_idx;
                if (axis == "x") axis_idx = 0;
                else if (axis == "y") axis_idx = 1;
                else if (axis == "z") axis_idx = 2;
                else return false;
                if (!try_get_bounds_axis(bval, new_min[axis_idx], new_max[axis_idx])) return false;
            }
            has_any = true;
        } else if (key_str == "stiffness") {
            if (!haptic::try_get_double(val, new_stiffness)) return false;
            has_any = true;
        } else if (key_str == "damping") {
            if (!haptic::try_get_double(val, new_damping)) return false;
            has_any = true;
        }
    }

    if (!has_any) return false;
    if (new_stiffness < 0.0) return false;
    if (new_damping < 0.0) return false;

    bounds_min_ = new_min;
    bounds_max_ = new_max;
    stiffness_ = new_stiffness;
    damping_ = new_damping;
    return true;
}

void WorkspaceLimitField::pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
    pk.pack_map(1);
    pk.pack("in_bounds");
    pk.pack(in_bounds_);
}
