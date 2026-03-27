#pragma once
#include "force_field.hpp"
#include <array>

class ChannelField : public ForceField {
public:
    Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) override;
    std::string name() const override;
    bool update_params(const msgpack::object& params) override;

private:
    std::array<bool, 3> active_ = {false, false, true};  // default: constrain Z
    double stiffness_ = 500.0;
    double damping_ = 10.0;
    Vec3 center_ = {0.0, 0.0, 0.0};

    static constexpr double MAX_STIFFNESS = 3000.0;
    static constexpr double MAX_DAMPING = 100.0;
};
