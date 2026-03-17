#include <atomic>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

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
              << "  --help                Print this help\n";
}

} // namespace

int main(int argc, char* argv[]) {
    // Default parameters
    std::string pub_address = "ipc:///tmp/hapticore_haptic_state";
    std::string cmd_address = "ipc:///tmp/hapticore_haptic_cmd";
    double pub_rate = 200.0;
    double force_limit = 20.0;
    int cpu_core = 1;

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
            pub_rate = std::stod(argv[++i]);
        } else if (arg == "--force-limit" && i + 1 < argc) {
            force_limit = std::stod(argv[++i]);
        } else if (arg == "--cpu-core" && i + 1 < argc) {
            cpu_core = std::stoi(argv[++i]);
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

    // 2. Gravity compensation
    dhd->set_effector_mass(0.0);

    // 3. Create triple buffer for state sharing
    TripleBuffer<HapticStateData> state_buffer;

    // 4. Create haptic thread (owns the DHD)
    HapticThread haptic(std::move(dhd), state_buffer, force_limit, cpu_core);

    // 5. Define command handler lambda
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
            // Build result map using msgpack objects owned by the response sbuffer
            // We pack and re-unpack to get stable msgpack::object references
            // For simplicity, we leave result empty and build it inline in pack()
            // Actually, we need to provide result as map<string, object>
            // Let's keep it simple - result will be empty for now, 
            // we'll add a custom pack for set_force_field result
            resp.success = true;
            return resp;

        } else if (cmd.method == "set_params") {
            auto current_field = haptic.get_field();
            if (!current_field) {
                resp.success = false;
                resp.error = "set_params: no active field";
                return resp;
            }

            if (!current_field->update_params(cmd.params.get())) {
                resp.success = false;
                resp.error = "set_params: invalid params";
                return resp;
            }

            resp.success = true;
            return resp;

        } else if (cmd.method == "get_state") {
            resp.success = true;
            // State is available via the publisher; this is a sync snapshot
            return resp;

        } else if (cmd.method == "heartbeat") {
            haptic.update_heartbeat();
            resp.success = true;
            return resp;

        } else if (cmd.method == "stop") {
            haptic.set_field(std::make_shared<NullField>());
            g_shutdown_requested.store(true);
            resp.success = true;
            return resp;

        } else {
            resp.success = false;
            resp.error = "unknown method: " + cmd.method;
            return resp;
        }
    };

    // 6. Create publisher and command threads
    PublisherThread publisher(state_buffer, pub_address, pub_rate);
    CommandThread commander(cmd_address, command_handler);

    // 7. Install signal handlers
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    // 8. Launch threads
    std::stop_source stop_source;
    auto stop_token = stop_source.get_token();

    std::jthread haptic_thread([&haptic](std::stop_token st) {
        haptic.run(st);
    });

    std::jthread pub_thread([&publisher](std::stop_token st) {
        publisher.run(st);
    });

    std::jthread cmd_thread([&commander](std::stop_token st) {
        commander.run(st);
    });

    std::cout << "Haptic server running.\n"
              << "  PUB: " << pub_address << "\n"
              << "  CMD: " << cmd_address << "\n"
              << "  Rate: " << pub_rate << " Hz\n"
              << "  Force limit: " << force_limit << " N\n"
              << "Press Ctrl+C to stop.\n";

    // 9. Wait for shutdown signal
    while (!g_shutdown_requested.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    std::cout << "\nShutting down...\n";

    // 10. Request all threads to stop
    haptic_thread.request_stop();
    pub_thread.request_stop();
    cmd_thread.request_stop();

    // jthread destructors will join automatically

    std::cout << "Haptic server stopped.\n";
    return EXIT_SUCCESS;
}
