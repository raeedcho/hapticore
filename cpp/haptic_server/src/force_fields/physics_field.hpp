#pragma once
#include "force_field.hpp"

#include <array>
#include <string>
#include <vector>

#include <box2d/box2d.h>
#include <box2d/collision.h>
#include <box2d/math_functions.h>

/// Per-body bookkeeping stored alongside Box2D body IDs.
struct BodyInfo {
    std::string id;
    b2BodyId body_id{};
    b2BodyType type = b2_staticBody;
    std::string shape_type;  // "circle" or "box"
    float shape_radius = 0.0f;
    float shape_width = 0.0f;
    float shape_height = 0.0f;
};

/// Per-joint bookkeeping for force extraction.
struct JointInfo {
    b2JointId joint_id{};
    std::string owner_body_id;  // the body that defined this joint
    std::string type;           // "revolute" or "prismatic"
};

/// PhysicsField wraps a Box2D v3.0 world for 2D rigid-body dynamics.
///
/// The hand controls a kinematic body. Dynamic bodies move according to
/// physics. Contact and joint reaction forces on the hand body are
/// extracted each tick and returned as the haptic force.
///
/// The world is created declaratively from a msgpack parameter map
/// (see docs/haptic_server_protocol.md § physics_world).
class PhysicsField : public ForceField {
public:
    PhysicsField();
    ~PhysicsField() override;

    PhysicsField(const PhysicsField&) = delete;
    PhysicsField& operator=(const PhysicsField&) = delete;

    Vec3 compute(const Vec3& pos, const Vec3& vel, double dt) override;
    std::string name() const override;
    bool update_params(const msgpack::object& params) override;
    void pack_state(msgpack::packer<msgpack::sbuffer>& pk) const override;
    void reset() override;

    // Test accessors
    bool has_world() const { return world_valid_; }
    size_t body_count() const { return bodies_.size(); }
    size_t joint_count() const { return joints_.size(); }

private:
    void destroy_world();
    bool build_world(const msgpack::object& params);
    bool parse_body(const msgpack::object& body_obj);
    bool parse_joint(const msgpack::object& joint_obj,
                     const std::string& owner_body_id,
                     b2BodyId owner_b2_id);

    b2WorldId world_id_{};
    bool world_valid_ = false;

    std::vector<BodyInfo> bodies_;
    std::vector<JointInfo> joints_;
    std::string hand_body_id_;
    int hand_body_idx_ = -1;       // index into bodies_
    double force_scale_ = 1.0;

    // Pre-allocated contact data buffer — avoids heap allocation in compute()
    static constexpr int MAX_CONTACTS = 16;
    std::array<b2ContactData, MAX_CONTACTS> contact_buf_{};

    // Cached gravity and sub-step count for tuning
    float gravity_x_ = 0.0f;
    float gravity_y_ = 0.0f;
    int sub_steps_ = 4;
};
