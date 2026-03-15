#include <cstdlib>
#include <iostream>
#include <string>

int main(int argc, char* argv[]) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: haptic_server [options]\n"
                      << "  --pub-address ADDR    ZMQ PUB address (default: ipc:///tmp/hapticore_haptic_state)\n"
                      << "  --cmd-address ADDR    ZMQ ROUTER address (default: ipc:///tmp/hapticore_haptic_cmd)\n"
                      << "  --pub-rate HZ         State publish rate (default: 200)\n"
                      << "  --force-limit N       Force clamp in Newtons (default: 20)\n"
                      << "  --cpu-core N          CPU core for haptic thread (default: 1)\n"
                      << "  --help                Print this help\n";
            return EXIT_SUCCESS;
        }
    }
    std::cout << "haptic_server: use --help for options\n";
    return EXIT_SUCCESS;
}
