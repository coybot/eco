//! Sensors: the seam the simulator is expected to grow along.
//!
//! Godot's sim has exactly one kind of sensor — an RGB `SubViewport` — and the
//! onboard camera, the vantage cameras and the chase camera are three separate
//! hand-rolled copies of the same code (`fleet_manager.add_vantage`,
//! `fleet_manager._spawn_vehicle`, `fixedwing_manager._sync_camera`). Adding a
//! depth or segmentation channel there means a fourth copy plus shader
//! overrides.
//!
//! Here a sensor is one component: a modality, a mount, and a render target.
//! Every view in the sim is an instance of it, so a new modality is a new
//! branch in `capture`, not a new subsystem, and a new view is a new mount.
//!
//! Mounts are resolved every frame rather than parented into the transform
//! hierarchy, for the same reason `_sync_camera` does it by hand: the look
//! direction is derived from flight state (yaw + gimbal offset, pitch minus the
//! sensor's own down-tilt), which is not a rigid offset from the airframe.

pub mod depth;
pub mod rgb;

use bevy::camera::visibility::RenderLayers;
use bevy::camera::RenderTarget;
use bevy::core_pipeline::tonemapping::Tonemapping;
use bevy::prelude::*;
use bevy::render::render_resource::TextureFormat;

/// What a sensor measures.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Modality {
    /// Colour image, JPEG over IPC. What every current client consumes.
    Rgb,
    /// Linear depth, for the obstacle channel the VLM currently has to infer
    /// from a monocular grey image. Wired through the same mount and readback
    /// path as `Rgb`.
    Depth,
}

/// Where a sensor sits and where it points.
#[derive(Clone, Debug)]
pub enum Mount {
    /// Rigidly follows a vehicle, offset forward of the airframe origin along
    /// its own look vector.
    ///
    /// `NOSE_OFFSET_M` is load-bearing, not cosmetic: at zero offset the camera
    /// sits inside the fuselage mesh, which was the actual cause of every
    /// "washed out" forward capture in the Godot build (not a lighting or
    /// tonemap bug — those were eliminated first).
    VehicleNose {
        vehicle: String,
        offset_m: f32,
        /// Downward tilt off the flight-path vector, radians.
        look_down: f32,
        /// Static yaw offset on top of the vehicle's own gimbal, so one
        /// airframe can carry several fixed cameras (port, starboard, aft)
        /// alongside the steerable forward one.
        extra_yaw: f32,
    },
    /// A fixed pose in ENU with a look-at point. Vantage cameras.
    Fixed { pos: Vec3, look: Vec3 },
    /// Follows a vehicle from behind and above, looking at it. The chase view.
    Chase {
        vehicle: String,
        back_m: f32,
        up_m: f32,
    },
}

#[derive(Component, Clone)]
pub struct Sensor {
    pub modality: Modality,
    /// Identifies the sensor for IPC: `(owner, name)`. `owner` is empty for a
    /// free-standing view like a vantage.
    pub owner: String,
    pub name: String,
    pub target: Handle<Image>,
    pub width: u32,
    pub height: u32,
    /// Vertical field of view, radians.
    pub fov: f32,
    pub mount: Mount,
}

/// Render layer bookkeeping.
///
/// Layer 0 is the world. Each aircraft's airframe gets its own layer so a
/// camera can be told to exclude it: an onboard camera must not see its own
/// airframe (which matters the moment the gimbal is turned sideways or aft,
/// the very thing `sensor_yaw_offset` exists to do) while still seeing every
/// OTHER aircraft, because peer sighting is the only channel a comms-denied
/// formation has.
pub const WORLD_LAYER: usize = 0;
pub const MAX_AIRFRAME_LAYERS: usize = 30;

/// Layer for the nth airframe ever spawned. Assigned once and never reused
/// while the aircraft lives, so despawning one aircraft cannot renumber
/// another's meshes out from under it.
pub fn airframe_layer(counter: usize) -> usize {
    1 + (counter % MAX_AIRFRAME_LAYERS)
}

/// Layers an onboard camera should see: the world, plus every airframe except
/// its own.
pub fn onboard_layers(own_layer: usize, all: &[usize]) -> RenderLayers {
    let mut layers = RenderLayers::layer(WORLD_LAYER);
    for &l in all {
        if l != own_layer {
            layers = layers.with(l);
        }
    }
    layers
}

/// Layers an observer view should see: the world and every airframe.
pub fn observer_layers(all: &[usize]) -> RenderLayers {
    let mut layers = RenderLayers::layer(WORLD_LAYER);
    for &l in all {
        layers = layers.with(l);
    }
    layers
}

/// Allocate a render target sized for a sensor.
pub fn make_target(images: &mut Assets<Image>, w: u32, h: u32, modality: Modality) -> Handle<Image> {
    let format = match modality {
        Modality::Rgb => TextureFormat::Rgba8Unorm,
        // Depth is read back as a single float channel; the colour target is
        // still allocated so the pass has somewhere to write.
        Modality::Depth => TextureFormat::Rgba8Unorm,
    };
    images.add(Image::new_target_texture(
        w.max(64),
        h.max(64),
        format,
        Some(TextureFormat::Rgba8UnormSrgb),
    ))
}

/// Spawn a camera entity for a sensor. `order` keeps offscreen sensor passes
/// deterministic relative to each other.
pub fn spawn_sensor(
    commands: &mut Commands,
    sensor: Sensor,
    layers: RenderLayers,
    order: isize,
) -> Entity {
    let target = sensor.target.clone();
    let fov = sensor.fov;
    commands
        .spawn((
            Camera3d::default(),
            Camera {
                order,
                // Off until something reads this sensor. See rgb::Captures for
                // why: with every camera always on, frame time grows with
                // camera count whether or not anyone wants the pixels.
                is_active: false,
                ..default()
            },
            Projection::Perspective(PerspectiveProjection {
                fov,
                aspect_ratio: sensor.width as f32 / sensor.height as f32,
                near: 0.1,
                // The default 1000 m far plane silently cuts a 21 km city in
                // half; a vantage is precisely the camera used for wide
                // establishing shots.
                far: 40_000.0,
                ..default()
            }),
            RenderTarget::Image(target.into()),
            // Filmic tonemapping, matching the Godot WorldEnvironment's
            // TONE_MAPPER_FILMIC. This is load-bearing rather than taste: it is
            // what fixed the washed-out forward captures, and the perception
            // gate's measured recognition rate is only evidence about the scene
            // it was actually shot in.
            Tonemapping::AcesFitted,
            layers,
            Transform::default(),
            sensor,
        ))
        .id()
}
