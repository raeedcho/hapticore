#include "physics_field.hpp"
#include "msgpack_helpers.hpp"

#include <cmath>
#include <iostream>
#include <set>
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
    pending_joints_.clear();
    joints_.clear();
    hand_body_idx_ = -1;
}

// ---- name / reset ----

std::string PhysicsField::name() const {
    return "physics_world";
}

void PhysicsField::reset() {
    // Reset all dynamic bodies to their configured initial positions with zero
    // velocity. Kinematic body will be re-synced on next compute().
    if (!world_valid_) return;
    for (auto& bi : bodies_) {
        if (bi.type == b2_dynamicBody) {
            b2Body_SetTransform(bi.body_id,
                                {bi.init_x, bi.init_y},
                                b2MakeRot(bi.init_angle));
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

    // Phase 3: Extract forces on the hand body from contacts and joints.
    //
    // Contact data: Box2D reports contact impulses in N·s (normalImpulse,
    // tangentImpulse). We accumulate them and divide by dt to get force (N).
    double contact_fx = 0.0;
    double contact_fy = 0.0;

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
        double nx = static_cast<double>(m.normal.x);
        double ny = static_cast<double>(m.normal.y);
        // Tangent is perpendicular to normal: t = (-ny, nx)
        double tx = -ny;
        double ty = nx;
        for (int j = 0; j < m.pointCount; ++j) {
            double ni = static_cast<double>(
                m.points[static_cast<size_t>(j)].normalImpulse);
            double ti = static_cast<double>(
                m.points[static_cast<size_t>(j)].tangentImpulse);
            contact_fx += sign * (nx * ni + tx * ti);
            contact_fy += sign * (ny * ni + ty * ti);
        }
    }

    // Convert contact impulses to forces.
    contact_fx /= dt;
    contact_fy /= dt;

    // Joint constraint forces: b2Joint_GetConstraintForce() already returns
    // force in Newtons (internally it multiplies the solver impulse by inv_h).
    // Only include joints where the hand body is one of the two connected
    // bodies; other joints (e.g., B-C in a chain hand→B→C) don't act on hand.
    double joint_fx = 0.0;
    double joint_fy = 0.0;

    for (const auto& ji : joints_) {
        bool hand_is_a = (ji.body_id_a.index1 == hand.body_id.index1 &&
                          ji.body_id_a.world0 == hand.body_id.world0 &&
                          ji.body_id_a.revision == hand.body_id.revision);
        bool hand_is_b = (ji.body_id_b.index1 == hand.body_id.index1 &&
                          ji.body_id_b.world0 == hand.body_id.world0 &&
                          ji.body_id_b.revision == hand.body_id.revision);
        if (!hand_is_a && !hand_is_b) continue;

        b2Vec2 cf = b2Joint_GetConstraintForce(ji.joint_id);
        // GetConstraintForce returns the force applied to bodyB.
        // Reaction on bodyA is the negative of that.
        if (hand_is_a) {
            joint_fx -= static_cast<double>(cf.x);
            joint_fy -= static_cast<double>(cf.y);
        } else {
            joint_fx += static_cast<double>(cf.x);
            joint_fy += static_cast<double>(cf.y);
        }
    }

    double fx = (contact_fx + joint_fx) * force_scale_;
    double fy = (contact_fy + joint_fy) * force_scale_;

    // Z=0 since Box2D is 2D.
    return {fx, fy, 0.0};
}

// ---- pack_state ----

void PhysicsField::pack_state(msgpack::packer<msgpack::sbuffer>& pk) const {
    // Pack a "bodies" map with position, angle, linear_velocity, and shape.
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
        pk.pack_map(4);

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

        pk.pack("linear_velocity");
        if (world_valid_) {
            b2Vec2 v = b2Body_GetLinearVelocity(bi.body_id);
            pk.pack_array(2);
            pk.pack(static_cast<double>(v.x));
            pk.pack(static_cast<double>(v.y));
        } else {
            pk.pack_array(2);
            pk.pack(0.0);
            pk.pack(0.0);
        }

        pk.pack("shape");
        if (bi.shape_type == "circle") {
            pk.pack_map(2);
            pk.pack("type"); pk.pack("circle");
            pk.pack("radius"); pk.pack(static_cast<double>(bi.shape_radius));
        } else {
            pk.pack_map(3);
            pk.pack("type"); pk.pack("box");
            pk.pack("width"); pk.pack(static_cast<double>(bi.shape_width));
            pk.pack("height"); pk.pack(static_cast<double>(bi.shape_height));
        }
    }
}

// ---- update_params (creates or rebuilds the entire world) ----

bool PhysicsField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;
    // Build into a temporary state; only swap if successful.
    // Save current state so we can restore on failure.
    auto old_world_id = world_id_;
    bool old_world_valid = world_valid_;
    auto old_bodies = std::move(bodies_);
    auto old_joints = std::move(joints_);
    auto old_pending = std::move(pending_joints_);
    auto old_hand_id = hand_body_id_;
    int old_hand_idx = hand_body_idx_;
    double old_force_scale = force_scale_;
    float old_gx = gravity_x_;
    float old_gy = gravity_y_;
    int old_sub = sub_steps_;

    // Clear so build_world starts fresh (without destroying the old Box2D world yet)
    world_valid_ = false;
    bodies_.clear();
    joints_.clear();
    pending_joints_.clear();
    hand_body_idx_ = -1;

    if (build_world(params)) {
        // Success — destroy old world
        if (old_world_valid) {
            b2DestroyWorld(old_world_id);
        }
        return true;
    }

    // Failure — restore previous state
    if (world_valid_) {
        b2DestroyWorld(world_id_);
    }
    world_id_ = old_world_id;
    world_valid_ = old_world_valid;
    bodies_ = std::move(old_bodies);
    joints_ = std::move(old_joints);
    pending_joints_ = std::move(old_pending);
    hand_body_id_ = old_hand_id;
    hand_body_idx_ = old_hand_idx;
    force_scale_ = old_force_scale;
    gravity_x_ = old_gx;
    gravity_y_ = old_gy;
    sub_steps_ = old_sub;
    return false;
}

// ---- build_world ----

bool PhysicsField::build_world(const msgpack::object& params) {
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
            if (!haptic::try_get_double(val, fscale)) return false;
        } else if (ks == "sub_steps") {
            if (!haptic::try_get_double(val, substeps_d)) return false;
            if (std::trunc(substeps_d) != substeps_d) return false;
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

    // Create bodies; track IDs for uniqueness.
    std::set<std::string> seen_ids;
    auto arr = bodies_arr->via.array;
    for (uint32_t i = 0; i < arr.size; ++i) {
        if (!parse_body(arr.ptr[i], seen_ids)) {
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
        return false;
    }

    // Reject dynamic hand body — requires virtual coupling (ADR-010).
    if (bodies_[static_cast<size_t>(hand_body_idx_)].type == b2_dynamicBody) {
        std::cerr << "PhysicsField: dynamic hand body requires virtual coupling "
                  << "(not yet implemented)\n";
        return false;
    }

    // Resolve deferred joints (second pass — all bodies exist now).
    if (!resolve_pending_joints()) {
        return false;
    }

    return true;
}

// ---- parse_body ----

bool PhysicsField::parse_body(const msgpack::object& body_obj,
                               std::set<std::string>& seen_ids) {
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
        } else if (ks == "restitution") {
            if (!haptic::try_get_double(val, restitution_val)) return false;
        } else if (ks == "friction") {
            if (!haptic::try_get_double(val, friction_val)) return false;
        } else if (ks == "linear_damping") {
            if (!haptic::try_get_double(val, linear_damping_val)) return false;
        } else if (ks == "angular_damping") {
            if (!haptic::try_get_double(val, angular_damping_val)) return false;
        }
    }

    if (id.empty() || type_str.empty() || !shape_obj) return false;

    // Reject duplicate body IDs.
    if (!seen_ids.insert(id).second) return false;

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
    body_def.linearDamping = static_cast<float>(linear_damping_val);
    body_def.angularDamping = static_cast<float>(angular_damping_val);

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

    // Create shape def with physics properties — always apply documented defaults.
    b2ShapeDef shape_def = b2DefaultShapeDef();
    shape_def.restitution = static_cast<float>(restitution_val);
    shape_def.friction = static_cast<float>(friction_val);
    // Enable contact events so we can read contact data on the hand body.
    shape_def.enableContactEvents = true;

    // For dynamic bodies: set density = mass / area (default mass = 1.0 kg).
    if (shape_type == "circle") {
        if (shape_radius <= 0.0) return false;
        b2Circle circle;
        circle.center = {0.0f, 0.0f};
        circle.radius = static_cast<float>(shape_radius);
        if (b2type == b2_dynamicBody) {
            double area = 3.14159265358979 * shape_radius * shape_radius;
            shape_def.density = static_cast<float>(mass_val / area);
        }
        b2CreateCircleShape(b2body, &shape_def, &circle);
    } else if (shape_type == "box") {
        if (shape_width <= 0.0 || shape_height <= 0.0) return false;
        float hx = static_cast<float>(shape_width) * 0.5f;
        float hy = static_cast<float>(shape_height) * 0.5f;
        b2Polygon box = b2MakeBox(hx, hy);
        if (b2type == b2_dynamicBody) {
            double area = shape_width * shape_height;
            shape_def.density = static_cast<float>(mass_val / area);
        }
        b2CreatePolygonShape(b2body, &shape_def, &box);
    } else {
        return false;
    }

    // Store body info (including initial pose for reset).
    BodyInfo bi;
    bi.id = id;
    bi.body_id = b2body;
    bi.type = b2type;
    bi.shape_type = shape_type;
    bi.shape_radius = static_cast<float>(shape_radius);
    bi.shape_width = static_cast<float>(shape_width);
    bi.shape_height = static_cast<float>(shape_height);
    bi.init_x = static_cast<float>(px);
    bi.init_y = static_cast<float>(py);
    bi.init_angle = 0.0f;
    bodies_.push_back(bi);

    // Defer joint creation to second pass (after all bodies exist).
    if (joint_obj) {
        if (joint_obj->type != msgpack::type::MAP) return false;
        auto jmap = joint_obj->via.map;

        PendingJoint pj;
        pj.owner_body_id = id;

        for (uint32_t i = 0; i < jmap.size; ++i) {
            auto& jk = jmap.ptr[i].key;
            auto& jv = jmap.ptr[i].val;
            if (jk.type != msgpack::type::STR) continue;
            std::string jks(jk.via.str.ptr, jk.via.str.size);

            if (jks == "type") {
                if (jv.type != msgpack::type::STR) return false;
                pj.type = std::string(jv.via.str.ptr, jv.via.str.size);
            } else if (jks == "anchor") {
                if (jv.type != msgpack::type::STR) return false;
                pj.anchor_str = std::string(jv.via.str.ptr, jv.via.str.size);
            } else if (jks == "offset") {
                if (jv.type != msgpack::type::ARRAY || jv.via.array.size != 2)
                    return false;
                double ox = 0.0, oy = 0.0;
                haptic::try_get_double(jv.via.array.ptr[0], ox);
                haptic::try_get_double(jv.via.array.ptr[1], oy);
                pj.offset_x = static_cast<float>(ox);
                pj.offset_y = static_cast<float>(oy);
            }
        }

        if (pj.type.empty() || pj.anchor_str.empty()) return false;
        pending_joints_.push_back(pj);
    }

    return true;
}

// ---- resolve_pending_joints (second pass after all bodies are created) ----

bool PhysicsField::resolve_pending_joints() {
    for (const auto& pj : pending_joints_) {
        // Resolve owner body.
        b2BodyId owner_b2{};
        bool owner_found = false;
        for (const auto& bi : bodies_) {
            if (bi.id == pj.owner_body_id) {
                owner_b2 = bi.body_id;
                owner_found = true;
                break;
            }
        }
        if (!owner_found) return false;

        // Resolve anchor body.
        b2BodyId anchor_b2{};
        bool anchor_found = false;
        std::string anchor_id = pj.anchor_str;
        if (anchor_id == "hand") anchor_id = hand_body_id_;
        for (const auto& bi : bodies_) {
            if (bi.id == anchor_id) {
                anchor_b2 = bi.body_id;
                anchor_found = true;
                break;
            }
        }
        if (!anchor_found) return false;

        b2Vec2 local_anchor_owner = {pj.offset_x, pj.offset_y};

        if (pj.type == "revolute") {
            b2RevoluteJointDef jd = b2DefaultRevoluteJointDef();
            jd.bodyIdA = anchor_b2;
            jd.bodyIdB = owner_b2;
            jd.localAnchorA = {0.0f, 0.0f};
            jd.localAnchorB = local_anchor_owner;
            b2JointId jid = b2CreateRevoluteJoint(world_id_, &jd);
            joints_.push_back({jid, anchor_b2, owner_b2,
                               pj.owner_body_id, pj.type});
        } else if (pj.type == "prismatic") {
            b2PrismaticJointDef jd = b2DefaultPrismaticJointDef();
            jd.bodyIdA = anchor_b2;
            jd.bodyIdB = owner_b2;
            jd.localAnchorA = {0.0f, 0.0f};
            jd.localAnchorB = local_anchor_owner;
            jd.localAxisA = {1.0f, 0.0f};  // default: slide along X
            b2JointId jid = b2CreatePrismaticJoint(world_id_, &jd);
            joints_.push_back({jid, anchor_b2, owner_b2,
                               pj.owner_body_id, pj.type});
        } else {
            return false;
        }
    }
    return true;
}
