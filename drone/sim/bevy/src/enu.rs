//! ENU <-> engine-space conversions.
//!
//! Every IPC op speaks the ENU ground frame: x=east m, y=north m, z=up m, yaw
//! radians CCW from +x (east). The render engine uses y-up: x=east, y=up,
//! z=-north. This is the identical mapping `fixedwing_manager.gd` uses
//! (`Vector3(st.position.x, st.position.z, -st.position.y)`), kept in one place
//! for the same reason that file centralised it: building "3D" vectors in the
//! wrong frame silently corrupted bearing, elevation and occlusion for a long
//! time before anyone noticed.

use bevy::math::{Vec2, Vec3};

/// ENU position/point -> engine space.
#[inline]
pub fn to_engine(enu: Vec3) -> Vec3 {
    Vec3::new(enu.x, enu.z, -enu.y)
}

/// Engine-space position/point -> ENU.
#[inline]
pub fn to_enu(p: Vec3) -> Vec3 {
    Vec3::new(p.x, -p.z, p.y)
}

/// An (east, north, up) direction as an engine-space direction. Same algebra as
/// `to_engine`; named separately to match the GDScript helper it replaces
/// (`_enu_dir_to_godot`) so the two files read alike.
#[inline]
pub fn dir_to_engine(east: f32, north: f32, up: f32) -> Vec3 {
    Vec3::new(east, up, -north)
}

/// An engine-space delta (target - camera) as its (east, north, up) components.
/// Mirrors `_godot_rel_to_enu`.
#[inline]
pub fn rel_to_enu(rel: Vec3) -> Vec3 {
    Vec3::new(rel.x, -rel.z, rel.y)
}

/// Horizontal (east, north) part of an ENU vector.
#[inline]
pub fn horiz(enu: Vec3) -> Vec2 {
    Vec2::new(enu.x, enu.y)
}

/// Signed angle from `from` to `to`, in (-PI, PI]. Godot's `Vector2.angle_to`.
#[inline]
pub fn angle_to(from: Vec2, to: Vec2) -> f32 {
    (from.x * to.y - from.y * to.x).atan2(from.x * to.x + from.y * to.y)
}

/// Godot's `wrapf(v, -PI, PI)`.
#[inline]
pub fn wrap_pi(v: f32) -> f32 {
    let tau = std::f32::consts::TAU;
    let mut x = (v + std::f32::consts::PI) % tau;
    if x < 0.0 {
        x += tau;
    }
    x - std::f32::consts::PI
}
