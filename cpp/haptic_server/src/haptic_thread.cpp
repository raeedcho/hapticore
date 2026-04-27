#include "haptic_thread.hpp"

#include <algorithm>
#include <cmath>
#include <ctime>
#include <iostream>

#include "force_fields/null_field.hpp"
#include "force_fields/spring_damper_field.hpp"

#ifdef __linux__
#include <pthread.h>
#include <sched.h>
#include <sys/mman.h>
#endif

HapticThread::HapticThread(std::unique_ptr<DhdInterface> dhd,
                           TripleBuffer<HapticStateData>& state_buffer,
                           double force_limit_n,
                           [[maybe_unused]] int cpu_core)
    : dhd_(std::move(dhd))
    , state_buffer_(state_buffer)
    , force_limit_n_(force_limit_n)
#ifdef __linux__
    , cpu_core_(cpu_core)
#endif
{
    // Start with NullField
    active_field_ = std::make_shared<NullField>();

    // Pre-construct safety fallback field for heartbeat timeout.
    // Damping-only (stiffness=0, damping=10) — no heap allocation in the hot path.
    auto safe = std::make_shared<SpringDamperField>();
    msgpack::sbuffer sbuf;
    msgpack::packer<msgpack::sbuffer> pk(sbuf);
    pk.pack_map(2);
    pk.pack("stiffness"); pk.pack(0.0);
    pk.pack("damping");   pk.pack(10.0);
    auto oh = msgpack::unpack(sbuf.data(), sbuf.size());
    safe->update_params(oh.get());
    safety_field_ = std::move(safe);
}

void HapticThread::run(std::atomic<bool>& stop_requested) {
#ifdef __linux__
    mlockall(MCL_CURRENT | MCL_FUTURE);
    struct sched_param param{};
    param.sched_priority = 80;
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &param) != 0) {
        // Real-hardware builds without --allow-no-rt fail-fast before this point,
        // so reaching here means we're in a mock build or the user explicitly
        // passed --allow-no-rt. Log at a low level and continue.
        std::cerr << "Note: SCHED_FIFO unavailable; haptic thread using default priority.\n";
    }
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(cpu_core_, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpuset), &cpuset);
#endif

    constexpr long TICK_NS = 250'000;  // 4 kHz = 250 µs
    constexpr double DT = 0.00025;     // 250 µs in seconds

    struct timespec next_wakeup{};
    clock_gettime(CLOCK_MONOTONIC, &next_wakeup);

    while (!stop_requested.load(std::memory_order_relaxed)) {
        // 1. Get current time
        double now = get_monotonic_time();

        // 2. Read device state
        Vec3 pos{}, vel{};
        dhd_->get_position(pos);
        dhd_->get_linear_velocity(vel);

        // 3. Load current field (mutex-protected, ~25ns uncontended)
        std::shared_ptr<ForceField> field;
        {
            std::lock_guard<std::mutex> lock(field_mtx_);
            field = active_field_;
        }

        // 4. Check heartbeat — only swap to safety field once on transition
        double last_hb = last_heartbeat_time_.load(std::memory_order_acquire);
        if (last_hb > 0.0 && (now - last_hb) > HEARTBEAT_TIMEOUT_S) {
            if (!in_safety_mode_) {
                // Transition to safety field (swap once, not every tick)
                {
                    std::lock_guard<std::mutex> lock(field_mtx_);
                    active_field_ = safety_field_;
                }
                in_safety_mode_ = true;
            }
            field = safety_field_;
        } else if (last_hb > 0.0) {
            in_safety_mode_ = false;
        }

        // 5. Compute force
        Vec3 force = field->compute(pos, vel, DT);

        // 6. Clamp force
        force = clamp_force(force);

        // 7. Apply force to device
        if (!dhd_->set_force(force)) {
            // Record error; avoid logging from the RT loop to prevent
            // unpredictable latency.  The flag is checked on shutdown.
            force_error_logged_ = true;
        }

        // 8. Populate state in triple buffer
        auto& state = state_buffer_.write_buffer();
        state.timestamp = now;
        state.sequence = sequence_;
        state.position = pos;
        state.velocity = vel;
        state.force = force;
        state.active_field = field->name();
        state.field_state_buf.clear();
        msgpack::packer<msgpack::sbuffer> field_pk(state.field_state_buf);
        field->pack_state(field_pk);

        // 9. Update state snapshot for get_state command (before publish,
        //    while we still own the write buffer exclusively)
        {
            std::lock_guard<std::mutex> lock(state_mtx_);
            last_state_.timestamp = now;
            last_state_.sequence = sequence_;
            last_state_.position = pos;
            last_state_.velocity = vel;
            last_state_.force = force;
            last_state_.active_field = field->name();
            last_state_.field_state_buf.clear();
            if (state.field_state_buf.size() > 0) {
                last_state_.field_state_buf.write(
                    state.field_state_buf.data(),
                    state.field_state_buf.size());
            }
        }

        // 10. Publish to triple buffer (transfers write buffer to shared)
        state_buffer_.publish();

        // 11. Increment sequence
        ++sequence_;

        // 12. Sleep until next tick
        next_wakeup.tv_nsec += TICK_NS;
        if (next_wakeup.tv_nsec >= 1'000'000'000L) {
            next_wakeup.tv_sec += 1;
            next_wakeup.tv_nsec -= 1'000'000'000L;
        }
#ifdef __linux__
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next_wakeup, nullptr);
#else
        // Fallback for non-Linux (macOS dev builds)
        struct timespec now_ts{};
        clock_gettime(CLOCK_MONOTONIC, &now_ts);
        long diff_ns = (next_wakeup.tv_sec - now_ts.tv_sec) * 1'000'000'000L
                     + (next_wakeup.tv_nsec - now_ts.tv_nsec);
        if (diff_ns > 0) {
            struct timespec sleep_ts{};
            sleep_ts.tv_sec = diff_ns / 1'000'000'000L;
            sleep_ts.tv_nsec = diff_ns % 1'000'000'000L;
            nanosleep(&sleep_ts, nullptr);
        }
#endif
    }

    // Report set_force errors that occurred during the RT loop
    if (force_error_logged_) {
        std::cerr << "Warning: dhdSetForce failed at least once during the session\n";
    }

    // Disable force rendering and close the device on shutdown
    dhd_->enable_force(false);
    dhd_->close();
}

void HapticThread::set_field(std::shared_ptr<ForceField> field) {
    std::lock_guard<std::mutex> lock(field_mtx_);
    active_field_ = std::move(field);
}

std::shared_ptr<ForceField> HapticThread::get_field() const {
    std::lock_guard<std::mutex> lock(field_mtx_);
    return active_field_;
}

void HapticThread::update_heartbeat() {
    last_heartbeat_time_.store(get_monotonic_time(), std::memory_order_release);
}

bool HapticThread::heartbeat_expired() const {
    double last_hb = last_heartbeat_time_.load(std::memory_order_acquire);
    if (last_hb <= 0.0) return false;
    return (get_monotonic_time() - last_hb) > HEARTBEAT_TIMEOUT_S;
}

HapticStateData HapticThread::get_latest_state() const {
    std::lock_guard<std::mutex> lock(state_mtx_);
    HapticStateData copy;
    copy.timestamp = last_state_.timestamp;
    copy.sequence = last_state_.sequence;
    copy.position = last_state_.position;
    copy.velocity = last_state_.velocity;
    copy.force = last_state_.force;
    copy.active_field = last_state_.active_field;
    if (last_state_.field_state_buf.size() > 0) {
        copy.field_state_buf.write(
            last_state_.field_state_buf.data(),
            last_state_.field_state_buf.size());
    }
    return copy;
}

Vec3 HapticThread::clamp_force(const Vec3& f) const {
    Vec3 clamped;
    for (int i = 0; i < 3; ++i) {
        clamped[static_cast<size_t>(i)] =
            std::clamp(f[static_cast<size_t>(i)], -force_limit_n_, force_limit_n_);
    }
    double mag = std::sqrt(clamped[0] * clamped[0]
                         + clamped[1] * clamped[1]
                         + clamped[2] * clamped[2]);
    if (mag > force_limit_n_ && mag > 0.0) {
        double scale = force_limit_n_ / mag;
        for (int i = 0; i < 3; ++i) {
            clamped[static_cast<size_t>(i)] *= scale;
        }
    }
    return clamped;
}

double HapticThread::get_monotonic_time() {
    struct timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<double>(ts.tv_sec) + static_cast<double>(ts.tv_nsec) * 1e-9;
}
