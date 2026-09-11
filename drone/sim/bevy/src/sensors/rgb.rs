//! RGB capture: render target -> JPEG -> base64, the wire format every current
//! client expects (`{"ok": true, "jpg": "<base64>"}`).
//!
//! Uses Bevy's `Screenshot` component, which is the on-demand path: a capture
//! is requested for one frame and answered by observer. That is a better fit
//! than a continuous `Readback` because a frame grab is a discrete IPC request,
//! and it means the GPU is not paying for a readback on every tick of the 60 Hz
//! loop for a camera nobody is reading.

use bevy::prelude::*;
use bevy::render::view::screenshot::{Screenshot, ScreenshotCaptured};
use crossbeam_channel::Sender;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// JPEG quality, matching the Godot path's `save_jpg_to_buffer(85 / 100.0)`.
pub const JPEG_QUALITY: u8 = 85;

/// Frames to let a just-activated camera render before asking for its pixels.
/// One to apply the activation, one to actually draw.
const WARMUP_FRAMES: u32 = 2;

/// Frames a camera stays powered after its last capture.
///
/// Switching off the instant a capture finishes makes every grab pay the
/// warmup again, which cost more than always-on rendering did at low camera
/// counts. Lingering means the cost tracks the cameras actually BEING READ
/// rather than the cameras that exist: a client polling one sensor at 10 Hz
/// keeps it warm and pays nothing extra, while thirty idle sensors on the same
/// airframe stay dark. Two seconds at 60 Hz covers any realistic poll rate,
/// including a client round-robinning a dozen sensors.
const LINGER_FRAMES: u32 = 120;

/// A capture in flight.
pub struct Pending {
    pub sensor: Entity,
    pub target: Handle<Image>,
    pub reply: Sender<String>,
    pub field: &'static str,
    frames: u32,
    requested: bool,
    done: Arc<AtomicBool>,
}

/// Queue of captures, plus how many of them each sensor currently owes.
///
/// Sensor cameras are **inactive by default and rendered only while something
/// is reading them**. That is the difference between a sim that scales to many
/// sensors and one that does not: with every camera always on, frame time grows
/// linearly with camera count whether or not anyone looks at the pixels, and a
/// grab costs about three frames, so 32 idle cameras made every grab three
/// times slower than one. Godot's SubViewports use UPDATE_ALWAYS and have the
/// same problem.
#[derive(Resource, Default)]
pub struct Captures {
    pub queue: Vec<Pending>,
    pub outstanding: bevy::platform::collections::HashMap<Entity, u32>,
    /// Frames of warmth left on each powered camera.
    linger: bevy::platform::collections::HashMap<Entity, u32>,
}

/// Enqueue a capture. The reply goes out when the pixels come back.
pub fn request(
    captures: &mut Captures,
    sensor: Entity,
    target: Handle<Image>,
    reply: Sender<String>,
    field: &'static str,
) {
    *captures.outstanding.entry(sensor).or_insert(0) += 1;
    captures.queue.push(Pending {
        sensor,
        target,
        reply,
        field,
        frames: 0,
        requested: false,
        done: Arc::new(AtomicBool::new(false)),
    });
}

/// Drive queued captures: switch the camera on, let it draw, ask for the
/// pixels, then switch it off again once nothing else is waiting on it.
pub fn drive(
    mut commands: Commands,
    mut captures: ResMut<Captures>,
    mut cams: Query<&mut Camera>,
) {
    // Warmth has to be read BEFORE this frame's activation, or every camera
    // looks warm the moment it is queued and nothing ever waits to draw.
    let queued: Vec<Entity> = captures.queue.iter().map(|p| p.sensor).collect();
    let was_warm: Vec<Entity> = queued
        .iter()
        .copied()
        .filter(|e| captures.linger.get(e).copied().unwrap_or(0) > 0)
        .collect();
    for sensor in queued {
        if let Ok(mut cam) = cams.get_mut(sensor) {
            if !cam.is_active {
                cam.is_active = true;
            }
        }
        captures.linger.insert(sensor, LINGER_FRAMES);
    }

    let mut to_spawn: Vec<(Handle<Image>, Sender<String>, &'static str, Arc<AtomicBool>)> = Vec::new();
    for p in captures.queue.iter_mut() {
        if p.requested {
            continue;
        }
        // A camera that was already drawing needs no warmup; only one coming
        // back from cold does. A cold camera reaches this branch warm on its
        // second frame, which is exactly WARMUP_FRAMES.
        let need = if was_warm.contains(&p.sensor) {
            1
        } else {
            WARMUP_FRAMES
        };
        p.frames += 1;
        if p.frames >= need {
            p.requested = true;
            to_spawn.push((p.target.clone(), p.reply.clone(), p.field, p.done.clone()));
        }
    }
    for (target, reply, field, done) in to_spawn {
        commands
            .spawn(Screenshot::image(target))
            .observe(move |ev: On<ScreenshotCaptured>| {
                let resp = match encode_jpeg(&ev.image) {
                    Some(b64) => format!("{{\"ok\":true,\"{field}\":\"{b64}\"}}"),
                    // Matches Godot: the op succeeded, there just is no image.
                    None => format!("{{\"ok\":true,\"{field}\":null}}"),
                };
                let _ = reply.send(resp);
                done.store(true, Ordering::Release);
            });
    }

    // Retire finished captures.
    let mut finished: Vec<Entity> = Vec::new();
    captures.queue.retain(|p| {
        if p.done.load(Ordering::Acquire) {
            finished.push(p.sensor);
            false
        } else {
            true
        }
    });
    for sensor in finished {
        let n = captures.outstanding.entry(sensor).or_insert(1);
        *n = n.saturating_sub(1);
    }

    // Cool down cameras nobody has read lately, and switch off the cold ones.
    let mut cold: Vec<Entity> = Vec::new();
    for (sensor, frames) in captures.linger.iter_mut() {
        if *frames > 0 {
            *frames -= 1;
        }
        if *frames == 0 {
            cold.push(*sensor);
        }
    }
    for sensor in cold {
        // A camera with a capture still in flight must keep drawing.
        if captures.outstanding.get(&sensor).copied().unwrap_or(0) > 0 {
            captures.linger.insert(sensor, LINGER_FRAMES);
            continue;
        }
        captures.linger.remove(&sensor);
        if let Ok(mut cam) = cams.get_mut(sensor) {
            cam.is_active = false;
        }
    }
}

/// Bevy `Image` -> base64 JPEG. Handles both RGBA and RGB source layouts so a
/// format change in the render target cannot silently produce garbage.
pub fn encode_jpeg(img: &Image) -> Option<String> {
    use base64::Engine as _;
    let w = img.width();
    let h = img.height();
    let data = img.data.as_ref()?;
    let px = (w as usize).checked_mul(h as usize)?;
    if px == 0 {
        return None;
    }
    let channels = data.len() / px;
    let rgb: Vec<u8> = match channels {
        4 => data.chunks_exact(4).flat_map(|p| [p[0], p[1], p[2]]).collect(),
        3 => data.clone(),
        _ => return None,
    };
    let buf = image::RgbImage::from_raw(w, h, rgb)?;
    let mut out = std::io::Cursor::new(Vec::new());
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut out, JPEG_QUALITY)
        .encode_image(&image::DynamicImage::ImageRgb8(buf))
        .ok()?;
    Some(base64::engine::general_purpose::STANDARD.encode(out.into_inner()))
}
