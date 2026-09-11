//! Analytic depth sensor: a second modality through the same mount, which is
//! what the `Sensor` seam exists to make cheap.
//!
//! Deliberately NOT a GPU depth prepass. The city is a set of axis-aligned
//! prisms answered by an exact slab test (see `env::manhattan`), so a range per
//! pixel is exact arithmetic — no prepass plumbing, no readback, and no
//! z-buffer precision loss at the 40 km far plane this scene needs. A textured
//! or mesh-heavy env later would want the GPU path instead; that is a new
//! branch here, not a new subsystem.
//!
//! The ray model is the PINHOLE model the renderer uses, so the depth image is
//! registered pixel-for-pixel with the RGB frame from the same sensor. Note
//! that `detect()` and `unproject()` use an equiangular approximation instead
//! (nx is linear in bearing, not in tan bearing); that inconsistency is
//! inherited from the Godot build and is worth closing, but closing it would
//! change every existing nx/ny and is out of scope here.

use crate::fixedwing::Fleet;
use crate::scenario::Environment;
use bevy::math::Vec3;

/// Centimetre fixed point, so a u16 covers 0..655.35 m. 0 means "no surface"
/// (the ray escaped to sky), which a consumer must treat as "unknown", not
/// "zero range".
pub const DEPTH_SCALE: f32 = 100.0;
pub const DEPTH_NO_HIT: u16 = 0;

pub struct DepthImage {
    pub width: u32,
    pub height: u32,
    pub data: Vec<u16>,
    pub min_m: f32,
    pub max_m: f32,
    pub hit_fraction: f32,
}

/// Cast one ray per pixel through the sensor's pinhole frustum.
///
/// `look_down` and `nose_offset_m` must match the RGB sensor's mount, or the
/// two images are of different scenes.
#[allow(clippy::too_many_arguments)]
pub fn render(
    fleet: &Fleet,
    env: &dyn Environment,
    id: &str,
    width: u32,
    height: u32,
    vfov: f32,
    look_down: f32,
    nose_offset_m: f32,
    max_range: f32,
) -> Option<DepthImage> {
    let st = fleet.get(id)?;
    let forward = st.look_dir_enu(look_down);
    // Camera basis in ENU. `up` is derived from the world up so the depth image
    // shares the RGB camera's roll (which is zero: the sensor is gimballed, it
    // does not roll with the airframe).
    let world_up = Vec3::Z;
    let right = forward.cross(world_up).normalize_or_zero();
    let up = right.cross(forward).normalize_or_zero();
    let eye = st.position + forward * nose_offset_m;

    let half_v = (vfov * 0.5).tan();
    let half_h = half_v * width as f32 / height as f32;

    let mut data = vec![DEPTH_NO_HIT; (width * height) as usize];
    let (mut min_m, mut max_m) = (f32::INFINITY, 0.0f32);
    let mut hits = 0usize;

    for j in 0..height {
        // Pixel centres, image convention: row 0 is the TOP of the frame, so v
        // runs from +half_v down to -half_v.
        let ny = (j as f32 + 0.5) / height as f32;
        let v = (1.0 - 2.0 * ny) * half_v;
        for i in 0..width {
            let nx = (i as f32 + 0.5) / width as f32;
            let u = (2.0 * nx - 1.0) * half_h;
            let dir = (forward + right * u + up * v).normalize();
            if let Some(d) = fleet.range_along(env, eye, dir, max_range) {
                data[(j * width + i) as usize] =
                    ((d * DEPTH_SCALE).round() as i64).clamp(1, u16::MAX as i64) as u16;
                min_m = min_m.min(d);
                max_m = max_m.max(d);
                hits += 1;
            }
        }
    }
    Some(DepthImage {
        width,
        height,
        data,
        min_m: if hits > 0 { min_m } else { 0.0 },
        max_m,
        hit_fraction: hits as f32 / (width * height) as f32,
    })
}

/// 16-bit greyscale PNG, base64. Chosen over a raw array because it is
/// directly viewable, and over 8-bit because 1 cm resolution over 655 m is the
/// point of a range image.
pub fn to_png_base64(img: &DepthImage) -> Option<String> {
    use base64::Engine as _;
    let mut bytes: Vec<u8> = Vec::with_capacity(img.data.len() * 2);
    for v in &img.data {
        bytes.extend_from_slice(&v.to_be_bytes());
    }
    let buf: image::ImageBuffer<image::Luma<u16>, Vec<u16>> =
        image::ImageBuffer::from_raw(img.width, img.height, img.data.clone())?;
    let mut out = std::io::Cursor::new(Vec::new());
    image::DynamicImage::ImageLuma16(buf)
        .write_to(&mut out, image::ImageFormat::Png)
        .ok()?;
    Some(base64::engine::general_purpose::STANDARD.encode(out.into_inner()))
}
