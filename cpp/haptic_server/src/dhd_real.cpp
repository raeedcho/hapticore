#include "dhd_real.hpp"
#include <iostream>
#include <drdc.h>

bool DhdReal::open() {
    if (drdOpen() < 0) {
        return false;
    }
    is_open_ = true;
    return true;
}

void DhdReal::close() {
    if (is_open_) {
        drdClose();
        is_open_ = false;
    }
}

bool DhdReal::is_open() const {
    return is_open_;
}

bool DhdReal::get_position(Vec3& pos) {
    Vec3 dhd;
    if (dhdGetPosition(&dhd[0], &dhd[1], &dhd[2]) < 0) return false;
    // Remap: Lab X = DHD Y (horizontal), Lab Y = DHD Z (vertical), Lab Z = DHD X (depth)
    pos[0] = dhd[1];
    pos[1] = dhd[2];
    pos[2] = dhd[0];
    return true;
}

bool DhdReal::get_linear_velocity(Vec3& vel) {
    Vec3 dhd;
    if (dhdGetLinearVelocity(&dhd[0], &dhd[1], &dhd[2]) < 0) return false;
    // Remap: Lab X = DHD Y, Lab Y = DHD Z, Lab Z = DHD X
    vel[0] = dhd[1];
    vel[1] = dhd[2];
    vel[2] = dhd[0];
    return true;
}

bool DhdReal::set_force(const Vec3& force) {
    // Inverse remap: DHD X = Lab Z, DHD Y = Lab X, DHD Z = Lab Y
    return dhdSetForce(force[2], force[0], force[1]) >= 0;
}

bool DhdReal::set_effector_mass(double mass_kg) {
    return dhdSetEffectorMass(mass_kg) >= 0;
}

bool DhdReal::enable_force(bool enable) {
    return dhdEnableForce(enable ? DHD_ON : DHD_OFF) >= 0;
}

bool DhdReal::set_gravity_compensation(bool enable) {
    return dhdSetGravityCompensation(enable ? DHD_ON : DHD_OFF) >= 0;
}

bool DhdReal::calibrate() {
    if (!drdIsSupported()) {
        std::cerr << "Warning: device does not report DRD support "
                  << "(unexpected for delta.3)\n";
        return false;
    }
    if (drdIsInitialized()) {
        std::cout << "Device already calibrated\n";
        return true;
    }
    std::cout << "Auto-calibrating — device will move, keep hands clear...\n";
    if (drdAutoInit() < 0) {
        std::cerr << "Error: auto-calibration failed ("
                  << dhdErrorGetLastStr() << ")\n";
        return false;
    }
    if (drdStop(true) < 0) {
        std::cerr << "Error: failed to stop DRD regulation ("
                  << dhdErrorGetLastStr() << ")\n";
        return false;
    }
    std::cout << "Calibration complete\n";
    return true;
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
