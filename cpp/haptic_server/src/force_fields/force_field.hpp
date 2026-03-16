#pragma once
#include <string>
#include <msgpack.hpp>
#include "types.hpp"

class ForceField {
public:
    virtual ~ForceField() = default;

    // Called at 4 kHz by the haptic thread.
    // Contract: must complete in < 50 µs. No allocation, no I/O, no locks.
    virtual Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) = 0;

    virtual std::string name() const = 0;

    // Update parameters from deserialized msgpack map. Return false on invalid params.
    virtual bool update_params(const msgpack::object& params) = 0;

    // Pack field-specific state into the provided packer. Default: empty map.
    virtual void pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
        pk.pack_map(0);
    }

    // Reset internal state (e.g., between trials).
    virtual void reset() {}
};
