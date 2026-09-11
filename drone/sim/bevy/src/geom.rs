//! Axis-aligned rectangle and slab-test helpers.
//!
//! `Rect2` here is Godot's: position is the MIN corner and size is a positive
//! extent, so `x..x+w` by `y..y+h`. The city is 45k of these plus a height, and
//! all of its ray queries are analytic against them (see `env::manhattan`), so
//! these functions are on the hot path for both detection and occupancy.

use bevy::math::{Vec2, Vec3};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect2 {
    pub pos: Vec2,
    pub size: Vec2,
}

impl Rect2 {
    #[inline]
    pub fn new(x: f32, y: f32, w: f32, h: f32) -> Self {
        Self {
            pos: Vec2::new(x, y),
            size: Vec2::new(w, h),
        }
    }

    #[inline]
    pub fn end(&self) -> Vec2 {
        self.pos + self.size
    }

    /// Godot's `Rect2.intersects` (strict: touching edges do not count).
    #[inline]
    pub fn intersects(&self, o: &Rect2) -> bool {
        let a_end = self.end();
        let b_end = o.end();
        self.pos.x < b_end.x && a_end.x > o.pos.x && self.pos.y < b_end.y && a_end.y > o.pos.y
    }

    /// Godot's `Rect2.has_point` (inclusive on the min edge, exclusive on max).
    #[inline]
    pub fn has_point(&self, p: Vec2) -> bool {
        let e = self.end();
        p.x >= self.pos.x && p.x < e.x && p.y >= self.pos.y && p.y < e.y
    }

    /// Godot's `Rect2.grow`.
    #[inline]
    pub fn grow(&self, by: f32) -> Rect2 {
        Rect2 {
            pos: self.pos - Vec2::splat(by),
            size: self.size + Vec2::splat(by * 2.0),
        }
    }

    /// Point of the footprint nearest to `p`, which is what a long block should
    /// report as its position rather than its centre.
    #[inline]
    pub fn nearest_point(&self, p: Vec2) -> Vec2 {
        let e = self.end();
        Vec2::new(p.x.clamp(self.pos.x, e.x), p.y.clamp(self.pos.y, e.y))
    }
}

/// Ray vs a footprint rect extruded from z=0 to z=`height`, in ENU.
///
/// Returns the entry distance along `dir` (which must be normalised) within
/// `max_dist`, or `None`. Direct port of `env_manhattan.gd::_ray_box`: an exact
/// slab test, which is why 45k buildings need no physics bodies at all.
pub fn ray_box(from: Vec3, dir: Vec3, r: &Rect2, height: f32, max_dist: f32) -> Option<f32> {
    // f64 internally, because the GDScript this ports does the same explicitly
    // (`PackedFloat64Array`). Occlusion is a discrete yes/no taken from this
    // number, so a grazing ray along a facade should not be decided by single
    // precision.
    let mut lo = 0.0f64;
    let mut hi = max_dist as f64;
    let e = r.end();
    let mins = [r.pos.x as f64, r.pos.y as f64, 0.0];
    let maxs = [e.x as f64, e.y as f64, height as f64];
    let o = [from.x as f64, from.y as f64, from.z as f64];
    let d = [dir.x as f64, dir.y as f64, dir.z as f64];
    for a in 0..3 {
        if d[a].abs() < 1e-9 {
            // Parallel to this slab: either inside it for the whole ray or it
            // can never be hit.
            if o[a] < mins[a] || o[a] > maxs[a] {
                return None;
            }
            continue;
        }
        let inv = 1.0 / d[a];
        let mut t0 = (mins[a] - o[a]) * inv;
        let mut t1 = (maxs[a] - o[a]) * inv;
        if t0 > t1 {
            std::mem::swap(&mut t0, &mut t1);
        }
        lo = lo.max(t0);
        hi = hi.min(t1);
        if lo > hi {
            return None;
        }
    }
    Some(lo as f32)
}

/// Ray vs an arbitrary ENU axis-aligned box given by min/max corners.
/// Used for props and settled payloads, which Manhattan keeps as real boxes
/// rather than footprint-plus-height prisms.
pub fn ray_aabb(from: Vec3, dir: Vec3, min: Vec3, max: Vec3, max_dist: f32) -> Option<f32> {
    let mut lo = 0.0f32;
    let mut hi = max_dist;
    let mins = [min.x, min.y, min.z];
    let maxs = [max.x, max.y, max.z];
    let o = [from.x, from.y, from.z];
    let d = [dir.x, dir.y, dir.z];
    for a in 0..3 {
        if d[a].abs() < 1e-9 {
            if o[a] < mins[a] || o[a] > maxs[a] {
                return None;
            }
            continue;
        }
        let inv = 1.0 / d[a];
        let mut t0 = (mins[a] - o[a]) * inv;
        let mut t1 = (maxs[a] - o[a]) * inv;
        if t0 > t1 {
            std::mem::swap(&mut t0, &mut t1);
        }
        lo = lo.max(t0);
        hi = hi.min(t1);
        if lo > hi {
            return None;
        }
    }
    Some(lo)
}
