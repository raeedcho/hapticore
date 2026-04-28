#include "dhd_mock.hpp"

bool DhdMock::open() {
    is_open_ = true;
    return true;
}

void DhdMock::close() {
    is_open_ = false;
}

bool DhdMock::is_open() const {
    return is_open_;
}

bool DhdMock::get_position(Vec3& pos) {
    std::lock_guard<std::mutex> lock(io_mtx_);
    pos = position_;
    return true;
}

bool DhdMock::get_linear_velocity(Vec3& vel) {
    std::lock_guard<std::mutex> lock(io_mtx_);
    vel = velocity_;
    return true;
}

bool DhdMock::set_force(const Vec3& force) {
    force_log_.push_back(force);
    return true;
}

bool DhdMock::set_effector_mass(double mass_kg) {
    effector_mass_ = mass_kg;
    return true;
}

bool DhdMock::enable_force(bool /*enable*/) {
    return true;
}

bool DhdMock::set_gravity_compensation(bool /*enable*/) {
    return true;
}

bool DhdMock::calibrate() {
    return true;
}

std::string DhdMock::device_name() const {
    return "MockDHD";
}

Vec3 DhdMock::max_force() const {
    return {20.0, 20.0, 20.0};
}

void DhdMock::set_mock_position(const Vec3& pos) {
    std::lock_guard<std::mutex> lock(io_mtx_);
    position_ = pos;
}

void DhdMock::set_mock_velocity(const Vec3& vel) {
    std::lock_guard<std::mutex> lock(io_mtx_);
    velocity_ = vel;
}

const std::vector<Vec3>& DhdMock::applied_forces() const {
    return force_log_;
}

void DhdMock::clear_force_log() {
    force_log_.clear();
}

std::unique_ptr<DhdInterface> create_dhd_interface() {
    return std::make_unique<DhdMock>();
}
