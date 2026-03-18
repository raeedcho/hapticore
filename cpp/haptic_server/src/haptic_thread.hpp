#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>

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

    void run(std::atomic<bool>& stop_requested);

    // Thread-safe field swap (called from command thread)
    void set_field(std::shared_ptr<ForceField> field);
    std::shared_ptr<ForceField> get_field() const;

    // Heartbeat tracking (called from command thread)
    void update_heartbeat();
    bool heartbeat_expired() const;

    // Thread-safe state snapshot (called from command thread for get_state)
    HapticStateData get_latest_state() const;

    // Access the underlying DHD interface (for shutdown)
    DhdInterface* dhd() const { return dhd_.get(); }

private:
    std::unique_ptr<DhdInterface> dhd_;
    TripleBuffer<HapticStateData>& state_buffer_;
    double force_limit_n_;
    uint64_t sequence_ = 0;

#ifdef __linux__
    int cpu_core_;
#endif

    // Mutex-protected shared_ptr for cross-thread field swap.
    // Uncontended lock/unlock is ~25ns, well within the 250µs tick budget.
    mutable std::mutex field_mtx_;
    std::shared_ptr<ForceField> active_field_;

    std::atomic<double> last_heartbeat_time_{0.0};
    static constexpr double HEARTBEAT_TIMEOUT_S = 0.5;

    // Pre-constructed safety field for heartbeat timeout (no heap allocs in hot path)
    std::shared_ptr<ForceField> safety_field_;

    // Track whether we've already transitioned to the safety field
    bool in_safety_mode_ = false;

    // Mutex-protected state snapshot for get_state command
    mutable std::mutex state_mtx_;
    HapticStateData last_state_;

    Vec3 clamp_force(const Vec3& force) const;
    static double get_monotonic_time();
};
