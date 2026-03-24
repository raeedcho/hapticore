#pragma once
#include "dhd_interface.hpp"

class DhdReal : public DhdInterface {
public:
    bool open() override;
    void close() override;
    bool is_open() const override;
    bool get_position(Vec3& pos) override;
    bool get_linear_velocity(Vec3& vel) override;
    bool set_force(const Vec3& force) override;
    bool set_effector_mass(double mass_kg) override;
    bool enable_force(bool enable) override;
    bool set_gravity_compensation(bool enable) override;
    bool calibrate() override;
    std::string device_name() const override;
    Vec3 max_force() const override;

private:
    bool is_open_ = false;
};
