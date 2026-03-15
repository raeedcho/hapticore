#pragma once
#include "force_field.hpp"

class WorkspaceLimitField : public ForceField {
public:
    Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) override;
    std::string name() const override;
    bool update_params(const msgpack::object& params) override;
    void pack_state(msgpack::packer<msgpack::sbuffer>& pk) const override;

private:
    Vec3 bounds_min_ = {-0.15, -0.15, -0.15};
    Vec3 bounds_max_ = {0.15, 0.15, 0.15};
    double stiffness_ = 2000.0;
    double damping_ = 10.0;
    bool in_bounds_ = true;
};
