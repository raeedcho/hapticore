#include "physics_field.hpp"
#include "msgpack_helpers.hpp"

#include <cmath>
#include <string>

PhysicsField::PhysicsField() = default;

PhysicsField::~PhysicsField() {
    destroy_world();
}

void PhysicsField::destroy_world() {
    if (world_valid_) {
        b2DestroyWorld(world_id_);
        world_valid_ = false;
    }
    bodies_.clear();
    joints_.clear();
    hand_body_idx_ = -1;
}

// ---- name / reset ----

std::string PhysicsField::name() const {
    return "physics_world";
}

void PhysicsField::reset() {
    // Reset all dynamic bodies to origin with zero velocity.
    // Kinematic body will be re-synced on next compute().
    if (!world_valid_) return;
    for (auto& bi : bodies_) {
        if (bi.type == b2_dynamicBody) {
            b2Body_SetTransform(bi.body_id, {0.0f, 0.0f}, b2MakeRot(0.0f));
            b2Body_SetLinearVelocity(bi.body_id, {0.0f, 0.0f});
            b2Body_SetAngularVelocity(bi.body_id, 0.0f);
        }
    }
}

// ---- compute (called at 4 kHz) ----

Vec3 PhysicsField::compute(const Vec3& pos, const Vec3& vel, double dt) {
    if (!world_valid_ || hand_body_idx_ < 0 || dt <= 0.0) {
        return {0.0, 0.0, 0.0};
    }

    // Phase 1: Drive the kinematic hand body to match the device position.
    // PhysicsField operates in the lab XY plane (X=right, Y=up).
    auto& hand = bodies_[static_cast<size_t>(hand_body_idx_)];
    auto hand_pos = b2Vec2{static_cast<float>(pos[0]),
                           static_cast<float>(pos[1])};
    auto hand_vel = b2Vec2{static_cast<float>(vel[0]),
                           static_cast<float>(vel[1])};
    b2Body_SetTransform(hand.body_id, hand_pos,
                        b2Body_GetRotation(hand.body_id));
    b2Body_SetLinearVelocity(hand.body_id, hand_vel);

    // Phase 2: Step the Box2D world.
    b2World_Step(world_id_, static_cast<float>(dt), sub_steps_);

    // Phase 3: Extract contact forces on the hand body.
    // Sum normalImpulse from all contact manifolds touching the hand body.
    // Box2D reports impulses (N·s); divide by dt to get force (N).
    double fx = 0.0;
    double fy = 0.0;

    int capacity = b2Body_GetContactCapacity(hand.body_id);
    if (capacity > MAX_CONTACTS) capacity = MAX_CONTACTS;
    int count = b2Body_GetContactData(hand.body_id, contact_buf_.data(),
                                      capacity);
    for (int i = 0; i < count; ++i) {
        const auto& cd = contact_buf_[static_cast<size_t>(i)];
        const auto& m = cd.manifold;
        // Manifold normal points from shape A to shape B.
        // If hand owns shapeA, the reaction on the hand is opposite the normal.
        // If hand owns shapeB, the reaction on the hand is along the normal.
        b2BodyId bodyA = b2Shape_GetBody(cd.shapeIdA);
        double sign = (bodyA.index1 == hand.body_id.index1 &&
                       bodyA.world0 == hand.body_id.world0 &&
                       bodyA.revision == hand.body_id.revision) ? -1.0 : 1.0;
        for (int j = 0; j < m.pointCount; ++j) {
            double impulse = static_cast<double>(
                m.points[static_cast<size_t>(j)].normalImpulse);
            fx += sign * static_cast<double>(m.normal.x) * impulse;
            fy += sign * static_cast<double>(m.normal.y) * impulse;
        }
    }

    // Also sum joint constraint forces acting on the hand body.
    for (const auto& ji : joints_) {
        b2Vec2 cf = b2Joint_GetConstraintForce(ji.joint_id);
        // Joint constraint force is the force applied to bodyB.
        // If the hand is bodyA of the joint, the reaction on hand = -cf.
        // We always set hand as bodyA when building joints, so negate.
        fx -= static_cast<double>(cf.x);
        fy -= static_cast<double>(cf.y);
    }

    // Convert impulse to force (Box2D normalImpulse is already impulse = F*dt).
    if (dt > 0.0) {
        fx /= dt;
        fy /= dt;
    }

    // Apply force scale and return (Z=0 since Box2D is 2D).
    return {fx * force_scale_, fy * force_scale_, 0.0};
}

// ---- pack_state ----

void PhysicsField::pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
    // Pack a "bodies" map: { "body_id": { "position": [x,y], "angle": a }, ... }
    pk.pack_map(1);
    pk.pack("bodies");

    // Count non-static bodies (dynamic + kinematic) for output
    uint32_t n = 0;
    for (const auto& bi : bodies_) {
        if (bi.type != b2_staticBody) ++n;
    }
    pk.pack_map(n);

    for (const auto& bi : bodies_) {
        if (bi.type == b2_staticBody) continue;
        pk.pack(bi.id);
        pk.pack_map(2);
        pk.pack("position");
        if (world_valid_) {
            b2Vec2 p = b2Body_GetPosition(bi.body_id);
            pk.pack_array(2);
            pk.pack(static_cast<double>(p.x));
            pk.pack(static_cast<double>(p.y));
        } else {
            pk.pack_array(2);
            pk.pack(0.0);
            pk.pack(0.0);
        }
        pk.pack("angle");
        if (world_valid_) {
            float angle = b2Rot_GetAngle(b2Body_GetRotation(bi.body_id));
            pk.pack(static_cast<double>(angle));
        } else {
            pk.pack(0.0);
        }
    }
}

// ---- update_params (creates or rebuilds the entire world) ----

bool PhysicsField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;
    return build_world(params);
}

// ---- build_world ----

bool PhysicsField::build_world(const msgpack::object& params) {
    // Tear down any existing world first.
    destroy_world();

    auto map = params.via.map;

    // Extract top-level keys: gravity, bodies, hand_body, force_scale, sub_steps
    const msgpack::object* bodies_arr = nullptr;
    std::string hand_body;
    double gx = 0.0, gy = 0.0;
    double fscale = 1.0;
    double substeps_d = 4.0;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;
        if (key.type != msgpack::type::STR) continue;
        std::string ks(key.via.str.ptr, key.via.str.size);

        if (ks == "gravity") {
            if (val.type != msgpack::type::ARRAY || val.via.array.size != 2)
                return false;
            if (!haptic::try_get_double(val.via.array.ptr[0], gx)) return false;
            if (!haptic::try_get_double(val.via.array.ptr[1], gy)) return false;
        } else if (ks == "bodies") {
            if (val.type != msgpack::type::ARRAY) return false;
            bodies_arr = &val;
        } else if (ks == "hand_body") {
            if (val.type != msgpack::type::STR) return false;
            hand_body = std::string(val.via.str.ptr, val.via.str.size);
        } else if (ks == "force_scale") {
            haptic::try_get_double(val, fscale);
        } else if (ks == "sub_steps") {
            haptic::try_get_double(val, substeps_d);
        }
    }

    if (!bodies_arr || hand_body.empty()) return false;

    // Create Box2D world.
    b2WorldDef world_def = b2DefaultWorldDef();
    world_def.gravity = {static_cast<float>(gx), static_cast<float>(gy)};
    world_id_ = b2CreateWorld(&world_def);
    world_valid_ = true;
    gravity_x_ = static_cast<float>(gx);
    gravity_y_ = static_cast<float>(gy);
    force_scale_ = fscale;
    sub_steps_ = static_cast<int>(substeps_d);
    if (sub_steps_ < 1) sub_steps_ = 1;
    hand_body_id_ = hand_body;

    // Create bodies.
    auto arr = bodies_arr->via.array;
    for (uint32_t i = 0; i < arr.size; ++i) {
        if (!parse_body(arr.ptr[i])) {
            destroy_world();
            return false;
        }
    }

    // Resolve hand_body_idx_.
    hand_body_idx_ = -1;
    for (size_t i = 0; i < bodies_.size(); ++i) {
        if (bodies_[i].id == hand_body_id_) {
            hand_body_idx_ = static_cast<int>(i);
            break;
        }
    }
    if (hand_body_idx_ < 0) {
        destroy_world();
        return false;
    }

    return true;
}

// ---- parse_body ----

bool PhysicsField::parse_body(const msgpack::object& body_obj) {
    if (body_obj.type != msgpack::type::MAP) return false;
    auto map = body_obj.via.map;

    std::string id;
    std::string type_str;
    const msgpack::object* shape_obj = nullptr;
    const msgpack::object* joint_obj = nullptr;
    double px = 0.0, py = 0.0;
    double mass_val = 1.0;
    double restitution_val = 0.0;
    double friction_val = 0.6;
    double linear_damping_val = 0.0;
    double angular_damping_val = 0.0;
    bool has_mass = false;
    bool has_restitution = false;
    bool has_friction = false;
    bool has_linear_damping = false;
    bool has_angular_damping = false;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;
        if (key.type != msgpack::type::STR) continue;
        std::string ks(key.via.str.ptr, key.via.str.size);

        if (ks == "id") {
            if (val.type != msgpack::type::STR) return false;
            id = std::string(val.via.str.ptr, val.via.str.size);
        } else if (ks == "type") {
            if (val.type != msgpack::type::STR) return false;
            type_str = std::string(val.via.str.ptr, val.via.str.size);
        } else if (ks == "shape") {
            shape_obj = &val;
        } else if (ks == "joint") {
            joint_obj = &val;
        } else if (ks == "position") {
            if (val.type != msgpack::type::ARRAY || val.via.array.size != 2)
                return false;
            if (!haptic::try_get_double(val.via.array.ptr[0], px)) return false;
            if (!haptic::try_get_double(val.via.array.ptr[1], py)) return false;
        } else if (ks == "mass") {
            if (!haptic::try_get_double(val, mass_val)) return false;
            has_mass = true;
        } else if (ks == "restitution") {
            if (!haptic::try_get_double(val, restitution_val)) return false;
            has_restitution = true;
        } else if (ks == "friction") {
            if (!haptic::try_get_double(val, friction_val)) return false;
            has_friction = true;
        } else if (ks == "linear_damping") {
            if (!haptic::try_get_double(val, linear_damping_val)) return false;
            has_linear_damping = true;
        } else if (ks == "angular_damping") {
            if (!haptic::try_get_double(val, angular_damping_val)) return false;
            has_angular_damping = true;
        }
    }

    if (id.empty() || type_str.empty() || !shape_obj) return false;

    // Map type string to b2BodyType.
    b2BodyType b2type;
    if (type_str == "static") b2type = b2_staticBody;
    else if (type_str == "kinematic") b2type = b2_kinematicBody;
    else if (type_str == "dynamic") b2type = b2_dynamicBody;
    else return false;

    // Create the Box2D body.
    b2BodyDef body_def = b2DefaultBodyDef();
    body_def.type = b2type;
    body_def.position = {static_cast<float>(px), static_cast<float>(py)};
    if (has_linear_damping) body_def.linearDamping = static_cast<float>(linear_damping_val);
    if (has_angular_damping) body_def.angularDamping = static_cast<float>(angular_damping_val);

    b2BodyId b2body = b2CreateBody(world_id_, &body_def);

    // Parse shape.
    if (shape_obj->type != msgpack::type::MAP) return false;
    auto smap = shape_obj->via.map;

    std::string shape_type;
    double shape_radius = 0.0;
    double shape_width = 0.0;
    double shape_height = 0.0;

    for (uint32_t i = 0; i < smap.size; ++i) {
        auto& sk = smap.ptr[i].key;
        auto& sv = smap.ptr[i].val;
        if (sk.type != msgpack::type::STR) continue;
        std::string sks(sk.via.str.ptr, sk.via.str.size);

        if (sks == "type") {
            if (sv.type != msgpack::type::STR) return false;
            shape_type = std::string(sv.via.str.ptr, sv.via.str.size);
        } else if (sks == "radius") {
            haptic::try_get_double(sv, shape_radius);
        } else if (sks == "width") {
            haptic::try_get_double(sv, shape_width);
        } else if (sks == "height") {
            haptic::try_get_double(sv, shape_height);
        }
    }

    // Create shape def with physics properties.
    b2ShapeDef shape_def = b2DefaultShapeDef();
    if (has_restitution) shape_def.restitution = static_cast<float>(restitution_val);
    if (has_friction) shape_def.friction = static_cast<float>(friction_val);
    // Enable contact events so we can read contact data on the hand body.
    shape_def.enableContactEvents = true;

    // For dynamic bodies with explicit mass: set density = mass / area.
    // For kinematic/static bodies density doesn't matter.
    if (shape_type == "circle") {
        if (shape_radius <= 0.0) return false;
        b2Circle circle;
        circle.center = {0.0f, 0.0f};
        circle.radius = static_cast<float>(shape_radius);
        if (has_mass && b2type == b2_dynamicBody) {
            double area = 3.14159265358979 * shape_radius * shape_radius;
            shape_def.density = static_cast<float>(mass_val / area);
        }
        b2CreateCircleShape(b2body, &shape_def, &circle);
    } else if (shape_type == "box") {
        if (shape_width <= 0.0 || shape_height <= 0.0) return false;
        float hx = static_cast<float>(shape_width) * 0.5f;
        float hy = static_cast<float>(shape_height) * 0.5f;
        b2Polygon box = b2MakeBox(hx, hy);
        if (has_mass && b2type == b2_dynamicBody) {
            double area = shape_width * shape_height;
            shape_def.density = static_cast<float>(mass_val / area);
        }
        b2CreatePolygonShape(b2body, &shape_def, &box);
    } else {
        return false;
    }

    // Store body info.
    BodyInfo bi;
    bi.id = id;
    bi.body_id = b2body;
    bi.type = b2type;
    bi.shape_type = shape_type;
    bi.shape_radius = static_cast<float>(shape_radius);
    bi.shape_width = static_cast<float>(shape_width);
    bi.shape_height = static_cast<float>(shape_height);
    bodies_.push_back(bi);

    // Handle inline joint definition.
    if (joint_obj) {
        if (!parse_joint(*joint_obj, id, b2body)) return false;
    }

    return true;
}

// ---- parse_joint ----

bool PhysicsField::parse_joint(const msgpack::object& joint_obj,
                               const std::string& owner_body_id,
                               b2BodyId owner_b2_id) {
    if (joint_obj.type != msgpack::type::MAP) return false;
    auto map = joint_obj.via.map;

    std::string jtype;
    std::string anchor_str;  // "hand" or a body id
    double offset_x = 0.0, offset_y = 0.0;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;
        if (key.type != msgpack::type::STR) continue;
        std::string ks(key.via.str.ptr, key.via.str.size);

        if (ks == "type") {
            if (val.type != msgpack::type::STR) return false;
            jtype = std::string(val.via.str.ptr, val.via.str.size);
        } else if (ks == "anchor") {
            if (val.type != msgpack::type::STR) return false;
            anchor_str = std::string(val.via.str.ptr, val.via.str.size);
        } else if (ks == "offset") {
            if (val.type != msgpack::type::ARRAY || val.via.array.size != 2)
                return false;
            haptic::try_get_double(val.via.array.ptr[0], offset_x);
            haptic::try_get_double(val.via.array.ptr[1], offset_y);
        }
    }

    if (jtype.empty() || anchor_str.empty()) return false;

    // Resolve anchor body.
    b2BodyId anchor_body{};
    bool found = false;

    if (anchor_str == "hand") {
        // Anchor to the hand body. It must already exist.
        for (const auto& bi : bodies_) {
            if (bi.id == hand_body_id_) {
                anchor_body = bi.body_id;
                found = true;
                break;
            }
        }
    } else {
        for (const auto& bi : bodies_) {
            if (bi.id == anchor_str) {
                anchor_body = bi.body_id;
                found = true;
                break;
            }
        }
    }
    if (!found) return false;

    b2Vec2 local_anchor_owner = {static_cast<float>(offset_x),
                                  static_cast<float>(offset_y)};

    if (jtype == "revolute") {
        b2RevoluteJointDef jd = b2DefaultRevoluteJointDef();
        // bodyA = anchor (hand), bodyB = owner (the body with the joint def)
        jd.bodyIdA = anchor_body;
        jd.bodyIdB = owner_b2_id;
        jd.localAnchorA = {0.0f, 0.0f};
        jd.localAnchorB = local_anchor_owner;
        b2JointId jid = b2CreateRevoluteJoint(world_id_, &jd);
        joints_.push_back({jid, owner_body_id, jtype});
    } else if (jtype == "prismatic") {
        b2PrismaticJointDef jd = b2DefaultPrismaticJointDef();
        jd.bodyIdA = anchor_body;
        jd.bodyIdB = owner_b2_id;
        jd.localAnchorA = {0.0f, 0.0f};
        jd.localAnchorB = local_anchor_owner;
        jd.localAxisA = {1.0f, 0.0f};  // default: slide along X
        b2JointId jid = b2CreatePrismaticJoint(world_id_, &jd);
        joints_.push_back({jid, owner_body_id, jtype});
    } else {
        return false;
    }

    return true;
}
