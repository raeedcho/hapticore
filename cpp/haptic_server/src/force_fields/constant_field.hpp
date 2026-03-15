#pragma once
#include "force_field.hpp"

class ConstantField : public ForceField {
public:
    Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) override;
    std::string name() const override;
    bool update_params(const msgpack::object& params) override;

private:
    Vec3 force_ = {0.0, 0.0, 0.0};
};
