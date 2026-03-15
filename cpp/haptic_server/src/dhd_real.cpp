#include "dhd_real.hpp"
#include <dhd.h>

bool DhdReal::open() {
    if (dhdOpen() < 0) {
        return false;
    }
    is_open_ = true;
    return true;
}

void DhdReal::close() {
    if (is_open_) {
        dhdClose();
        is_open_ = false;
    }
}

bool DhdReal::is_open() const {
    return is_open_;
}

bool DhdReal::get_position(Vec3& pos) {
    return dhdGetPosition(&pos[0], &pos[1], &pos[2]) >= 0;
}

bool DhdReal::get_linear_velocity(Vec3& vel) {
    return dhdGetLinearVelocity(&vel[0], &vel[1], &vel[2]) >= 0;
}

bool DhdReal::set_force(const Vec3& force) {
    return dhdSetForce(force[0], force[1], force[2]) >= 0;
}

bool DhdReal::set_effector_mass(double mass_kg) {
    return dhdSetEffectorMass(mass_kg) >= 0;
}

std::string DhdReal::device_name() const {
    return dhdGetSystemName();
}

Vec3 DhdReal::max_force() const {
    // delta.3 rated max continuous force
    return {20.0, 20.0, 20.0};
}

std::unique_ptr<DhdInterface> create_dhd_interface() {
    return std::make_unique<DhdReal>();
}
