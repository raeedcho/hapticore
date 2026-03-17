#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <stop_token>

#include "dhd_interface.hpp"
#include "force_fields/force_field.hpp"
#include "state_data.hpp"
#include "triple_buffer.hpp"

class HapticThread {
public:
    HapticThread(std::unique_ptr<DhdInterface> dhd,
                 TripleBuffer<HapticStateData>& state_buffer,
                 double force_limit_n,
                 int cpu_core = 1);

    void run(std::stop_token stop);

    // Thread-safe field swap (called from command thread)
    void set_field(std::shared_ptr<ForceField> field);
    std::shared_ptr<ForceField> get_field() const;

    // Heartbeat tracking (called from command thread)
    void update_heartbeat();
    bool heartbeat_expired() const;

    // Access the underlying DHD interface (for shutdown)
    DhdInterface* dhd() const { return dhd_.get(); }

private:
    std::unique_ptr<DhdInterface> dhd_;
    TripleBuffer<HapticStateData>& state_buffer_;
    double force_limit_n_;
    int cpu_core_;
    uint64_t sequence_ = 0;

    std::atomic<std::shared_ptr<ForceField>> active_field_;
    std::atomic<double> last_heartbeat_time_{0.0};
    static constexpr double HEARTBEAT_TIMEOUT_S = 0.5;

    // Pre-constructed safety field for heartbeat timeout (no heap allocs in hot path)
    std::shared_ptr<ForceField> safety_field_;

    Vec3 clamp_force(const Vec3& force) const;
    static double get_monotonic_time();
};
