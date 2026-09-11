//! Scenario description and the environment seam.
//!
//! The Godot sim hard-wires one GDScript file per environment and reaches into
//! it by duck-typing (`_env.has_method("structure_detections")`). The sim is
//! expected to grow procedurally generated scenarios, so that duck-typing is
//! replaced here by an explicit trait: anything that can answer these questions
//! can be flown, whether it was loaded from NYC Open Data or generated from a
//! seed.

use crate::geom::Rect2;
use bevy::math::{Vec2, Vec3};

/// What to build, before anything is built. Parsed from the launch arguments so
/// the same flags the Godot build accepts keep working.
#[derive(Clone, Debug)]
pub struct Scenario {
    /// Environment name, as passed in `--env`.
    pub name: String,
    /// `--seed`. Every random choice must derive from this and nothing else, so
    /// a scenario is reproducible.
    pub seed: u64,
    /// `--env-region` clip window in ENU metres (east0, north0, east1, north1).
    pub region: Option<[f32; 4]>,
    /// `--building-scale` vertical exaggeration. 1.0 is the real city.
    pub building_scale: f32,
}

/// A sensable object. Mirrors the metadata a Godot prop node carried
/// (`label`, `is_anomaly`, `top_z`) plus the box it occupies, because the
/// detection and occlusion code needs its extent, not just its point.
#[derive(Clone, Debug)]
pub struct Prop {
    pub name: String,
    pub label: String,
    pub is_anomaly: bool,
    /// Height of the top of the object, when it is known. A sensed point says
    /// where something is, not how tall it is, and for structure that
    /// difference decides whether it can be overflown at all.
    pub top_z: Option<f32>,
    /// ENU position of the SENSED POINT. Matches Godot's convention exactly:
    /// a prop's node sits at mid-height, so `pos.z` is half the box height and
    /// `detect()`/`prop_truth()` report that value as the object's altitude.
    pub pos: Vec3,
    /// Box extents in engine axes: (east, up, north), i.e. `size.y` is height.
    pub size: Vec3,
    /// Rendered colour.
    pub color: [f32; 3],
    /// Truck cab, for the target vehicle's second box.
    pub cab: Option<(Vec3, Vec3)>,
}

impl Prop {
    /// ENU axis-aligned bounds, for occlusion rays.
    pub fn aabb(&self) -> (Vec3, Vec3) {
        let half = Vec3::new(self.size.x * 0.5, self.size.z * 0.5, self.size.y * 0.5);
        (self.pos - half, self.pos + half)
    }
}

/// Grid geometry an environment wants its occupancy/observed grids sized to.
/// An open-field scene wants 1 m cells over a few hundred metres; a city wants
/// coarser cells over kilometres, and a structure outside the grid is invisible
/// to the envelope guard, which fails open rather than closed.
#[derive(Clone, Copy, Debug)]
pub struct GridConfig {
    pub res: f32,
    pub origin: Vec2,
    pub w: usize,
    pub h: usize,
}

/// Sensor cone the structure pass should cull against.
#[derive(Clone, Copy, Debug)]
pub struct SensorCfg {
    pub range: f32,
    pub hfov_half: f32,
    pub vfov_half: f32,
    pub look_down: f32,
}

/// One synthesised detection row, in the same shape the props pass produces.
#[derive(Clone, Debug)]
pub struct DetectionRow {
    pub label: String,
    pub confidence: f32,
    pub nx: f32,
    pub ny: f32,
    pub world: [f32; 3],
    pub top_z: Option<f32>,
    pub peer_id: Option<String>,
    pub peer_alt: Option<f32>,
}

/// Everything the vehicle models need from the world they fly in.
pub trait Environment: Send + Sync {
    /// Escape hatch for scene construction only.
    ///
    /// The render-side geometry of a city (45k instanced prisms, a recovered
    /// coastline) is specific to that city, while everything the FLIGHT MODEL
    /// needs is already behind this trait. Keeping the downcast confined to
    /// startup is what stops env-specific rendering leaking into the vehicle
    /// code, which is how the GDScript ended up duck-typing `_env` at runtime.
    fn as_any(&self) -> &dyn core::any::Any;

    /// Occupancy-grid geometry for this world.
    fn grid_config(&self) -> GridConfig;

    /// Footprint rects that count as blocking, for the occupancy grid.
    fn walls(&self) -> &[Rect2];

    /// Heights matching `walls()`, when the env has them. Used by the payload
    /// ballistics to land a bottle on a roof rather than through it.
    fn wall_heights(&self) -> &[f32];

    fn props(&self) -> &[Prop];
    fn props_mut(&mut self) -> &mut Vec<Prop>;

    /// Nearest structure hit along a normalised ENU `dir`, or `None`.
    fn raycast_structures(&self, from: Vec3, dir: Vec3, max_dist: f32) -> Option<f32>;

    /// Buildings as "wall" detection rows. An env whose structure has no
    /// per-object node answers this analytically; one that has real prop nodes
    /// returns nothing and lets the props pass do the work.
    fn structure_detections(&self, _pos: Vec3, _sensor_yaw: f32, _cfg: SensorCfg) -> Vec<DetectionRow> {
        Vec::new()
    }

    /// Scenario introspection for harnesses, so they can assert on real scene
    /// truth instead of inferring it from detections.
    fn env_state(&self) -> serde_json::Value;

    /// Add a blocking rect mid-flight (the "wall went up" replan inject).
    fn add_wall(&mut self, rect: Rect2, height: f32);

    fn advance(&mut self, dt: f32);
}
