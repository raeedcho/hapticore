/// Writes a packed HapticStateData to a file for cross-language testing.
/// Usage: write_test_state <output_file>

#include <cstdlib>
#include <fstream>
#include <iostream>

#include "state_data.hpp"

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "Usage: write_test_state <output_file>\n";
        return EXIT_FAILURE;
    }

    HapticStateData state;
    state.timestamp = 1234.567;
    state.sequence = 42;
    state.position = {0.1, 0.2, 0.3};
    state.velocity = {-0.01, 0.0, 0.05};
    state.force = {1.0, -2.0, 0.5};
    state.active_field = "spring_damper";

    // Pre-pack a field state with known values
    {
        msgpack::packer<msgpack::sbuffer> pk(state.field_state_buf);
        pk.pack_map(2);
        pk.pack("stiffness"); pk.pack(200.0);
        pk.pack("damping");   pk.pack(5.0);
    }

    msgpack::sbuffer buf;
    state.pack(buf);

    std::ofstream out(argv[1], std::ios::binary);
    if (!out) {
        std::cerr << "Error: cannot open output file: " << argv[1] << "\n";
        return EXIT_FAILURE;
    }
    out.write(buf.data(), static_cast<std::streamsize>(buf.size()));
    out.close();

    std::cout << "Wrote " << buf.size() << " bytes to " << argv[1] << "\n";
    return EXIT_SUCCESS;
}
