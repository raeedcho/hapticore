#pragma once
#include "force_field.hpp"

class SpringDamperField : public ForceField {
public:
    Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) override;
    std::string name() const override;
    bool update_params(const msgpack::object& params) override;

private:
    double stiffness_ = 100.0;
    double damping_ = 5.0;
    Vec3 center_ = {0.0, 0.0, 0.0};

    static constexpr double MAX_STIFFNESS = 3000.0;
};
