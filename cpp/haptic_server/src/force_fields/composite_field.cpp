#include "composite_field.hpp"
#include "field_factory.hpp"

Vec3 CompositeField::compute(const Vec3& pos, const Vec3& vel, double dt) {
    Vec3 total = {0.0, 0.0, 0.0};
    for (auto& child : children_) {
        Vec3 f = child->compute(pos, vel, dt);
        for (int i = 0; i < 3; ++i) {
            total[static_cast<size_t>(i)] += f[static_cast<size_t>(i)];
        }
    }
    return total;
}

std::string CompositeField::name() const {
    return "composite";
}

bool CompositeField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;

    auto map = params.via.map;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;

        if (key.type != msgpack::type::STR) continue;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "fields") {
            if (val.type != msgpack::type::ARRAY) return false;

            std::vector<std::unique_ptr<ForceField>> new_children;
            auto arr = val.via.array;

            for (uint32_t j = 0; j < arr.size; ++j) {
                auto& child_obj = arr.ptr[j];
                if (child_obj.type != msgpack::type::MAP) return false;

                auto child_map = child_obj.via.map;
                std::string child_type;
                const msgpack::object* child_params = nullptr;

                for (uint32_t k = 0; k < child_map.size; ++k) {
                    auto& ckey = child_map.ptr[k].key;
                    auto& cval = child_map.ptr[k].val;
                    if (ckey.type != msgpack::type::STR) continue;
                    std::string ck(ckey.via.str.ptr, ckey.via.str.size);
                    if (ck == "type") {
                        if (cval.type != msgpack::type::STR) return false;
                        child_type = std::string(cval.via.str.ptr, cval.via.str.size);
                    } else if (ck == "params") {
                        child_params = &cval;
                    }
                }

                if (child_type.empty()) return false;
                auto child = create_field(child_type);
                if (!child) return false;

                if (child_params) {
                    if (!child->update_params(*child_params)) return false;
                }

                new_children.push_back(std::move(child));
            }

            children_ = std::move(new_children);
            return true;
        }
    }

    return false;
}

void CompositeField::pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
    pk.pack_map(1);
    pk.pack("children");
    pk.pack_array(static_cast<uint32_t>(children_.size()));
    for (auto& child : children_) {
        child->pack_state(pk);
    }
}

void CompositeField::reset() {
    for (auto& child : children_) {
        child->reset();
    }
}
