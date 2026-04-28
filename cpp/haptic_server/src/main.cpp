#include <atomic>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>

#ifdef __linux__
#include <pthread.h>
#include <sched.h>
#include <sys/prctl.h>
#endif

#include <zmq.hpp>

#include "command_data.hpp"
#include "command_thread.hpp"
#include "dhd_interface.hpp"
#include "force_fields/field_factory.hpp"
#include "force_fields/null_field.hpp"
#include "haptic_thread.hpp"
#include "publisher_thread.hpp"
#include "state_data.hpp"
#include "triple_buffer.hpp"

#ifdef HAPTIC_MOCK_HARDWARE
#include "dhd_mock.hpp"
#endif

namespace {

std::atomic<bool> g_shutdown_requested{false};

void signal_handler(int /*sig*/) {
    g_shutdown_requested.store(true);
}

void print_usage() {
    std::cout << "Usage: haptic_server [options]\n"
              << "  --pub-address ADDR    ZMQ PUB address (default: ipc:///tmp/hapticore_haptic_state)\n"
              << "  --cmd-address ADDR    ZMQ ROUTER address (default: ipc:///tmp/hapticore_haptic_cmd)\n"
              << "  --pub-rate HZ         State publish rate (default: 200)\n"
              << "  --force-limit N       Force clamp in Newtons (default: 20)\n"
              << "  --cpu-core N          CPU core for haptic thread (default: 1)\n"
              << "  --no-calibrate        Skip auto-calibration on startup\n"
              << "  --die-with-parent     Exit when parent process dies (Linux only; for auto-spawn use)\n"
#if defined(__linux__) && !defined(HAPTIC_MOCK_HARDWARE)
              << "  --allow-no-rt         Continue even if SCHED_FIFO is unavailable (degraded timing)\n"
#endif
              << "  --help                Print this help\n";
}

/// Helper to pack a result map with a single string value: {"key": "value"}
void pack_active_field_result(msgpack::sbuffer& buf, const std::string& field_name) {
    msgpack::packer<msgpack::sbuffer> pk(buf);
    pk.pack_map(1);
    pk.pack("active_field");
    pk.pack(field_name);
}

/// Extract a Vec3 from a msgpack map value keyed by ``key_name``.
/// Returns std::nullopt if the key is missing, the value is not an array
/// of exactly 3 elements, or any element is not numeric.
std::optional<Vec3> parse_vec3_param(const msgpack::object& params, const char* key_name) {
    if (params.type != msgpack::type::MAP) return std::nullopt;
    auto map = params.via.map;
    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;
        if (key.type != msgpack::type::STR) continue;
        std::string k(key.via.str.ptr, key.via.str.size);
        if (k == key_name) {
            if (val.type != msgpack::type::ARRAY || val.via.array.size != 3) {
                return std::nullopt;
            }
            Vec3 v{};
            for (int j = 0; j < 3; ++j) {
                auto& elem = val.via.array.ptr[j];
                if (elem.type == msgpack::type::FLOAT64) {
                    v[j] = elem.via.f64;
                } else if (elem.type == msgpack::type::FLOAT32) {
                    v[j] = static_cast<double>(elem.via.f64);
                } else if (elem.type == msgpack::type::POSITIVE_INTEGER) {
                    v[j] = static_cast<double>(elem.via.u64);
                } else if (elem.type == msgpack::type::NEGATIVE_INTEGER) {
                    v[j] = static_cast<double>(elem.via.i64);
                } else {
                    return std::nullopt;
                }
            }
            return v;
        }
    }
    return std::nullopt;
}

} // namespace

int main(int argc, char* argv[]) {
    // Default parameters
    std::string pub_address = "ipc:///tmp/hapticore_haptic_state";
    std::string cmd_address = "ipc:///tmp/hapticore_haptic_cmd";
    double pub_rate = 200.0;
    double force_limit = 20.0;
    int cpu_core = 1;
    bool auto_calibrate = true;
    bool die_with_parent = false;
#if defined(__linux__) && !defined(HAPTIC_MOCK_HARDWARE)
    bool allow_no_rt = false;
#endif

    // Parse command-line arguments
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            print_usage();
            return EXIT_SUCCESS;
        } else if (arg == "--pub-address" && i + 1 < argc) {
            pub_address = argv[++i];
        } else if (arg == "--cmd-address" && i + 1 < argc) {
            cmd_address = argv[++i];
        } else if (arg == "--pub-rate" && i + 1 < argc) {
            try {
                pub_rate = std::stod(argv[++i]);
            } catch (const std::exception&) {
                std::cerr << "Error: --pub-rate requires a numeric value\n";
                return EXIT_FAILURE;
            }
            if (pub_rate <= 0.0) {
                std::cerr << "Error: --pub-rate must be positive\n";
                return EXIT_FAILURE;
            }
        } else if (arg == "--force-limit" && i + 1 < argc) {
            try {
                force_limit = std::stod(argv[++i]);
            } catch (const std::exception&) {
                std::cerr << "Error: --force-limit requires a numeric value\n";
                return EXIT_FAILURE;
            }
            if (force_limit <= 0.0) {
                std::cerr << "Error: --force-limit must be positive\n";
                return EXIT_FAILURE;
            }
        } else if (arg == "--cpu-core" && i + 1 < argc) {
            try {
                cpu_core = std::stoi(argv[++i]);
            } catch (const std::exception&) {
                std::cerr << "Error: --cpu-core requires an integer value\n";
                return EXIT_FAILURE;
            }
            if (cpu_core < 0) {
                std::cerr << "Error: --cpu-core must be non-negative\n";
                return EXIT_FAILURE;
            }
        } else if (arg == "--no-calibrate") {
            auto_calibrate = false;
        } else if (arg == "--die-with-parent") {
            die_with_parent = true;
#if defined(__linux__) && !defined(HAPTIC_MOCK_HARDWARE)
        } else if (arg == "--allow-no-rt") {
            allow_no_rt = true;
#endif
        } else {
            std::cerr << "Unknown option: " << arg << "\n";
            print_usage();
            return EXIT_FAILURE;
        }
    }

#ifdef __linux__
    // Link server lifetime to parent — only when explicitly requested (e.g., by
    // the Python factory). Manual launches (nohup, tmux, systemd-run) must NOT
    // pass this flag so the server outlives its launching shell as expected.
    if (die_with_parent) {
        if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0) {
            std::cerr << "Warning: prctl(PR_SET_PDEATHSIG) failed; "
                      << "spawned server may not be cleaned up if the parent crashes.\n";
        }
    }
#else
    (void)die_with_parent;  // suppress unused-variable warning on non-Linux
#endif

#if defined(__linux__) && !defined(HAPTIC_MOCK_HARDWARE)
    // Pre-flight: verify SCHED_FIFO capability before launching any threads.
    // A real-hardware build with degraded timing is silently wrong — the haptic
    // loop won't hit 4 kHz reliably. Fail fast unless the user explicitly opts
    // out, but always print a warning when the capability is missing so the
    // user has runtime evidence of degraded scheduling.
    {
        struct sched_param rt_probe{};
        rt_probe.sched_priority = 1;  // minimum SCHED_FIFO priority is sufficient to validate CAP_SYS_NICE
        const bool can_use_rt =
            pthread_setschedparam(pthread_self(), SCHED_FIFO, &rt_probe) == 0;
        if (can_use_rt) {
            // Probe succeeded — revert main thread to normal scheduling.
            // The haptic thread will re-apply SCHED_FIFO on itself when it starts.
            struct sched_param normal{};
            if (pthread_setschedparam(pthread_self(), SCHED_OTHER, &normal) != 0) {
                std::cerr << "Warning: failed to revert main thread to SCHED_OTHER after RT probe.\n";
            }
        } else if (!allow_no_rt) {
            std::cerr << "Error: cannot set SCHED_FIFO (CAP_SYS_NICE not granted).\n"
                      << "  Fix: sudo setcap cap_sys_nice=eip " << argv[0] << "\n"
                      << "  Or pass --allow-no-rt to run without real-time priority "
                      << "(degraded timing; not suitable for data collection).\n";
            return EXIT_FAILURE;
        } else {
            std::cerr << "WARNING: --allow-no-rt set; SCHED_FIFO unavailable. "
                         "Do not use this run for data collection.\n";
        }
    }
#endif

    // 1. Create and open DHD interface
    auto dhd = create_dhd_interface();
    if (!dhd->open()) {
        std::cerr << "Error: failed to open haptic device\n";
        return EXIT_FAILURE;
    }
    std::cout << "Opened device: " << dhd->device_name() << "\n";

    // 2. Auto-calibrate if needed (skips if already calibrated this power cycle)
    if (auto_calibrate && !dhd->calibrate()) {
        std::cerr << "Error: calibration failed — cannot safely enable forces. "
                  << "If the device has not been calibrated this power cycle, "
                  << "power-cycle the hardware and restart the server.\n";
        return EXIT_FAILURE;
    }

    // 3. Gravity compensation and force enable
    if (!dhd->set_gravity_compensation(true)) {
        std::cerr << "Error: failed to enable gravity compensation\n";
        return EXIT_FAILURE;
    }

    if (!dhd->enable_force(true)) {
        std::cerr << "Error: failed to enable force rendering\n";
        return EXIT_FAILURE;
    }

    // 4. Startup diagnostic: position sanity check
    {
        Vec3 pos{};
        if (!dhd->get_position(pos)) {
            std::cerr << "Warning: failed to read position during startup check\n";
        } else {
            double pos_mag = std::sqrt(pos[0]*pos[0] + pos[1]*pos[1] + pos[2]*pos[2]);
            if (pos_mag > 1e-6) {
                std::cout << "Position sanity check: device at nonzero position\n";
            } else {
                std::cout << "Position sanity check: device at origin "
                          << "(expected for mock hardware)\n";
            }
        }
    }

    // 5. Create triple buffer for state sharing
    TripleBuffer<HapticStateData> state_buffer;

    // 6. Create haptic thread (owns the DHD)
    HapticThread haptic(std::move(dhd), state_buffer, force_limit, cpu_core);

#ifdef HAPTIC_MOCK_HARDWARE
    // Safe: HAPTIC_MOCK_HARDWARE selects dhd_mock.cpp at link time,
    // so haptic.dhd() returns a DhdMock*. This never compiles in
    // real-hardware builds.
    auto* mock_dhd = static_cast<DhdMock*>(haptic.dhd());
#endif

    // 7. Define command handler lambda
#ifdef HAPTIC_MOCK_HARDWARE
    auto command_handler = [&haptic, mock_dhd](const CommandData& cmd) -> CommandResponseData {
#else
    auto command_handler = [&haptic](const CommandData& cmd) -> CommandResponseData {
#endif
        CommandResponseData resp;
        resp.command_id = cmd.command_id;

        if (cmd.method == "set_force_field") {
            // Extract "type" and "params" from cmd.params
            const auto& params_obj = cmd.params.get();
            if (params_obj.type != msgpack::type::MAP) {
                resp.success = false;
                resp.error = "set_force_field: params must be a map";
                return resp;
            }

            std::string field_type;
            const msgpack::object* field_params = nullptr;

            auto map = params_obj.via.map;
            for (uint32_t i = 0; i < map.size; ++i) {
                auto& key = map.ptr[i].key;
                auto& val = map.ptr[i].val;
                if (key.type != msgpack::type::STR) continue;
                std::string key_str(key.via.str.ptr, key.via.str.size);
                if (key_str == "type") {
                    if (val.type != msgpack::type::STR) {
                        resp.success = false;
                        resp.error = "set_force_field: type must be string";
                        return resp;
                    }
                    field_type = std::string(val.via.str.ptr, val.via.str.size);
                } else if (key_str == "params") {
                    field_params = &val;
                }
            }

            if (field_type.empty()) {
                resp.success = false;
                resp.error = "set_force_field: missing type";
                return resp;
            }

            auto new_field = create_field(field_type);
            if (!new_field) {
                resp.success = false;
                resp.error = "set_force_field: unknown field type '" + field_type + "'";
                return resp;
            }

            if (field_params) {
                if (!new_field->update_params(*field_params)) {
                    resp.success = false;
                    resp.error = "set_force_field: invalid params for '" + field_type + "'";
                    return resp;
                }
            }

            haptic.set_field(std::shared_ptr<ForceField>(std::move(new_field)));
            resp.success = true;
            pack_active_field_result(resp.result_buf, field_type);
            return resp;

        } else if (cmd.method == "set_params") {
            // To avoid a data race with the haptic thread's compute(),
            // we reconstruct the field with the current type and new params,
            // then atomically swap it in. Note: this discards any accumulated
            // internal state (e.g., CartPendulumField's pendulum angle). For
            // fields with internal dynamics, use set_force_field to fully
            // specify the field, or call reset() on the new field as needed.
            auto current_field = haptic.get_field();
            if (!current_field) {
                resp.success = false;
                resp.error = "set_params: no active field";
                return resp;
            }

            std::string field_name = current_field->name();
            auto new_field = create_field(field_name);
            if (!new_field) {
                resp.success = false;
                resp.error = "set_params: failed to reconstruct field";
                return resp;
            }

            if (!new_field->update_params(cmd.params.get())) {
                resp.success = false;
                resp.error = "set_params: invalid params";
                return resp;
            }

            haptic.set_field(std::shared_ptr<ForceField>(std::move(new_field)));
            resp.success = true;
            pack_active_field_result(resp.result_buf, field_name);
            return resp;

        } else if (cmd.method == "get_state") {
            // Return a snapshot of the current HapticState
            auto state = haptic.get_latest_state();
            state.pack(resp.result_buf);
            resp.success = true;
            return resp;

        } else if (cmd.method == "heartbeat") {
            haptic.update_heartbeat();
            resp.success = true;
            // Per protocol: {"timeout_ms": 500}
            {
                msgpack::packer<msgpack::sbuffer> pk(resp.result_buf);
                pk.pack_map(1);
                pk.pack("timeout_ms");
                pk.pack(500);
            }
            return resp;

        } else if (cmd.method == "stop") {
            haptic.set_field(std::make_shared<NullField>());
            g_shutdown_requested.store(true);
            resp.success = true;
            // Per protocol: {"shutting_down": true}
            {
                msgpack::packer<msgpack::sbuffer> pk(resp.result_buf);
                pk.pack_map(1);
                pk.pack("shutting_down");
                pk.pack(true);
            }
            return resp;

#ifdef HAPTIC_MOCK_HARDWARE
        } else if (cmd.method == "set_mock_position") {
            auto vec = parse_vec3_param(cmd.params.get(), "position");
            if (!vec) {
                resp.success = false;
                resp.error = "set_mock_position: params must contain \"position\" as array[3] of numbers";
                return resp;
            }
            mock_dhd->set_mock_position(*vec);
            resp.success = true;
            return resp;

        } else if (cmd.method == "set_mock_velocity") {
            auto vec = parse_vec3_param(cmd.params.get(), "velocity");
            if (!vec) {
                resp.success = false;
                resp.error = "set_mock_velocity: params must contain \"velocity\" as array[3] of numbers";
                return resp;
            }
            mock_dhd->set_mock_velocity(*vec);
            resp.success = true;
            return resp;
#else
        } else if (cmd.method == "set_mock_position" || cmd.method == "set_mock_velocity") {
            resp.success = false;
            resp.error = cmd.method + " rejected: not a mock-hardware build";
            return resp;
#endif

        } else {
            resp.success = false;
            resp.error = "unknown method: " + cmd.method;
            return resp;
        }
    };

    // 8. Create shared ZMQ context
    zmq::context_t zmq_ctx(1);

    // 9. Create publisher and command threads
    PublisherThread publisher(state_buffer, pub_address, pub_rate, zmq_ctx);
    CommandThread commander(cmd_address, command_handler, zmq_ctx);

    // 10. Install signal handlers
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    // 11. Shared stop flag for all threads
    std::atomic<bool> stop_flag{false};

    // 12. Launch threads
    std::thread haptic_thread([&haptic, &stop_flag]() {
        haptic.run(stop_flag);
    });

    std::thread pub_thread([&publisher, &stop_flag]() {
        publisher.run(stop_flag);
    });

    std::thread cmd_thread([&commander, &stop_flag]() {
        commander.run(stop_flag);
    });

    std::cout << "Haptic server running.\n"
              << "  PUB: " << pub_address << "\n"
              << "  CMD: " << cmd_address << "\n"
              << "  Rate: " << pub_rate << " Hz\n"
              << "  Force limit: " << force_limit << " N\n"
              << "Press Ctrl+C to stop.\n";

    // 13. Wait for shutdown signal
    while (!g_shutdown_requested.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    std::cout << "\nShutting down...\n";

    // 14. Signal all threads to stop and join
    stop_flag.store(true);

    haptic_thread.join();
    pub_thread.join();
    cmd_thread.join();

    std::cout << "Haptic server stopped.\n";
    return EXIT_SUCCESS;
}
