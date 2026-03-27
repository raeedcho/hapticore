#include "field_factory.hpp"
#include "null_field.hpp"
#include "constant_field.hpp"
#include "spring_damper_field.hpp"
#include "workspace_limit_field.hpp"
#include "cart_pendulum_field.hpp"
#include "channel_field.hpp"
#include "composite_field.hpp"

std::unique_ptr<ForceField> create_field(const std::string& type_name) {
    if (type_name == "null") return std::make_unique<NullField>();
    if (type_name == "constant") return std::make_unique<ConstantField>();
    if (type_name == "spring_damper") return std::make_unique<SpringDamperField>();
    if (type_name == "workspace_limit") return std::make_unique<WorkspaceLimitField>();
    if (type_name == "cart_pendulum") return std::make_unique<CartPendulumField>();
    if (type_name == "channel") return std::make_unique<ChannelField>();
    if (type_name == "composite") return std::make_unique<CompositeField>();
    return nullptr;
}
