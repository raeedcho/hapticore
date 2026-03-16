#pragma once
#include <memory>
#include <string>
#include "types.hpp"

class DhdInterface {
public:
    virtual ~DhdInterface() = default;
    virtual bool open() = 0;
    virtual void close() = 0;
    virtual bool is_open() const = 0;
    virtual bool get_position(Vec3& pos) = 0;
    virtual bool get_linear_velocity(Vec3& vel) = 0;
    virtual bool set_force(const Vec3& force) = 0;
    virtual bool set_effector_mass(double mass_kg) = 0;
    virtual std::string device_name() const = 0;
    virtual Vec3 max_force() const = 0;
};

std::unique_ptr<DhdInterface> create_dhd_interface();
