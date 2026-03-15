#pragma once
#include "force_field.hpp"
#include <vector>
#include <memory>

class CompositeField : public ForceField {
public:
    Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) override;
    std::string name() const override;
    bool update_params(const msgpack::object& params) override;
    void pack_state(msgpack::packer<msgpack::sbuffer>& pk) const override;
    void reset() override;

private:
    std::vector<std::unique_ptr<ForceField>> children_;
};
