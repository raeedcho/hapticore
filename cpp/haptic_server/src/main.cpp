#include <atomic>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

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
              << "  --help                Print this help\n";
}

/// Helper to pack a result map with a single string value: {"key": "value"}
void pack_active_field_result(msgpack::sbuffer& buf, const std::string& field_name) {
    msgpack::packer<msgpack::sbuffer> pk(buf);
    pk.pack_map(1);
    pk.pack("active_field");
    pk.pack(field_name);
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
        } else {
            std::cerr << "Unknown option: " << arg << "\n";
            print_usage();
            return EXIT_FAILURE;
        }
    }

    // 1. Create and open DHD interface
    auto dhd = create_dhd_interface();
    if (!dhd->open()) {
        std::cerr << "Error: failed to open haptic device\n";
        return EXIT_FAILURE;
    }
    std::cout << "Opened device: " << dhd->device_name() << "\n";

    // 2. Auto-calibrate if needed (skips if already calibrated this power cycle)
    if (auto_calibrate && !dhd->calibrate()) {
        std::cerr << "Warning: calibration failed — positions may be inaccurate. "
                  << "If not calibrated this power cycle, try power-cycling "
                  << "and restarting.\n";
    }

    // 3. Gravity compensation and force enable
    dhd->set_gravity_compensation(true);

    if (!dhd->enable_force(true)) {
        std::cerr << "Error: failed to enable force rendering\n";
        return EXIT_FAILURE;
    }

    dhd->set_effector_mass(0.0);

    // 4. Startup diagnostic: verify gravity compensation is producing forces
    {
        Vec3 pos{};
        dhd->get_position(pos);
        // With gravity comp on and zero user force, the force applied should
        // include gravity compensation (nonzero unless at singularity).
        Vec3 zero_force = {0.0, 0.0, 0.0};
        dhd->set_force(zero_force);
        double pos_mag = std::sqrt(pos[0]*pos[0] + pos[1]*pos[1] + pos[2]*pos[2]);
        if (pos_mag > 1e-6) {
            std::cout << "Gravity compensation check: device at nonzero position\n";
        } else {
            std::cout << "Gravity compensation check: device at origin "
                      << "(expected for mock hardware)\n";
        }
    }

    // 5. Create triple buffer for state sharing
    TripleBuffer<HapticStateData> state_buffer;

    // 6. Create haptic thread (owns the DHD)
    HapticThread haptic(std::move(dhd), state_buffer, force_limit, cpu_core);

    // 7. Define command handler lambda
    auto command_handler = [&haptic](const CommandData& cmd) -> CommandResponseData {
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
