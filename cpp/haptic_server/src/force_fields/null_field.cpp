#include "null_field.hpp"

Vec3 NullField::compute(const Vec3& /*pos*/, const Vec3& /*vel*/, double /*dt*/) {
    return {0.0, 0.0, 0.0};
}

std::string NullField::name() const {
    return "null";
}

bool NullField::update_params(const msgpack::object& /*params*/) {
    return true;
}
