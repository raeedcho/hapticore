#pragma once
#include "dhd_interface.hpp"
#include <vector>

class DhdMock : public DhdInterface {
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

    // Mock-specific methods
    void set_mock_position(const Vec3& pos);
    void set_mock_velocity(const Vec3& vel);
    const std::vector<Vec3>& applied_forces() const;
    void clear_force_log();

private:
    bool is_open_ = false;
    Vec3 position_ = {0.0, 0.0, 0.0};
    Vec3 velocity_ = {0.0, 0.0, 0.0};
    double effector_mass_ = 0.0;
    std::vector<Vec3> force_log_;
};
