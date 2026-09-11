//! Fixed-wing vehicle model: kinematics, sensing, grids, battery, injects,
//! event log and payload ballistics.
//!
//! Port of `drone/sim/godot/scripts/fixedwing_manager.gd`. Every constant and
//! every sign convention below is carried over deliberately — that file's
//! comments record a long list of bugs found in flight (a bank angle taken from
//! the small-angle ratio, a velocity vector pitched twice, an image-convention
//! `ny` that was vertically mirrored for the whole life of the function), and
//! the tuning that resulted is the actual asset. Where the GDScript explains
//! why a sign is what it is, that reasoning is kept here rather than
//! re-derived.
//!
//! Coordinate convention: all IPC ops speak ENU {x=east, y=north, z=up, yaw
//! CCW from east}. Conversion to render space happens only in `crate::enu`.

use crate::enu;
use crate::geom::{ray_aabb, Rect2};
use crate::scenario::{DetectionRow, Environment, GridConfig, Prop, SensorCfg};
use bevy::math::{Vec2, Vec3};
use bevy::prelude::Entity;

pub const MAX_AIRSPEED: f32 = 25.0; // m/s
pub const MIN_AIRSPEED: f32 = 12.0; // m/s
pub const MAX_YAW_RATE: f32 = 0.6; // rad/s
pub const MAX_ROLL_DEG: f32 = 45.0;
pub const MAX_CLIMB_RATE: f32 = 8.0; // m/s
pub const MIN_CLIMB_RATE: f32 = -12.0; // m/s
pub const ALT_FLOOR_M: f32 = 2.0;
pub const ALT_CEILING_M: f32 = 120.0;

/// Default sensing range. See `DETECT_RANGE_BY_LABEL` for the per-class values,
/// which exist because one cutoff was wrong in both directions: a 50 m wall is
/// visible far further off than a person, a dropped bottle from far less.
pub const DETECT_RANGE: f32 = 80.0;

/// Per-class sensing range. Keeping people at 80 m is deliberate — it is
/// exactly what forces the aircraft to descend for a close identification pass
/// instead of calling the target from cruise altitude.
pub fn detect_range(label: &str) -> f32 {
    match label {
        // Warning distance is thinking time: at 220 m the model could describe
        // a wall perfectly and still not finish choosing before reaching it.
        "wall" => 400.0,
        "aircraft" => 300.0,
        "water_bottle" => 40.0,
        // Ground vehicles are bigger and higher-contrast than a person, so they
        // are picked up from farther than the 80 m default, but still well
        // inside cruise range: the aircraft must fly out and close in to
        // confirm, it cannot call a truck from the launch point.
        "pickup truck" | "truck" | "car" => 150.0,
        _ => DETECT_RANGE,
    }
}

/// A longer range than ground objects is a disclosed stand-in for the fact that
/// real aircraft carry transponders and are far easier to spot against sky than
/// a person is against terrain.
pub const PEER_DETECT_RANGE: f32 = 300.0;

pub const CAM_VIEWPORT_W: u32 = 640;
pub const CAM_VIEWPORT_H: u32 = 480;
pub const CAM_FOV_DEG: f32 = 70.0;
/// Forward of the body's own origin. Without it the camera renders from inside
/// the fuselage mesh.
pub const NOSE_OFFSET_M: f32 = 3.0;
pub const DETECT_LOOK_DOWN: f32 = 15.0 * std::f32::consts::PI / 180.0;

/// The sensing cone MUST be the camera's cone.
///
/// `detect()` decides what the mission is told exists and the frame decides
/// what the model can see; when those disagree the model is looking straight at
/// something the system swears is not there, and a grounding guard then
/// overrides its perfectly true report as unfounded. They disagreed badly in
/// the Godot build before these were derived from `CAM_FOV` rather than
/// hand-set. `fov` is the VERTICAL angle; horizontal follows from the aspect
/// ratio, so the two cannot drift apart again.
pub fn detect_vfov_half() -> f32 {
    (CAM_FOV_DEG * 0.5).to_radians()
}
pub fn detect_hfov_half() -> f32 {
    ((CAM_FOV_DEG * 0.5).to_radians().tan() * CAM_VIEWPORT_W as f32 / CAM_VIEWPORT_H as f32).atan()
}

const BASE_DRAIN_IDLE: f32 = 0.01; // %/s
const BASE_DRAIN_PER_M: f32 = 0.005; // %/m

/// Collision layers, as in the GDScript. Only these two are ever tested: the
/// airframes carry no collision shape at all, so aircraft never occlude
/// anything.
pub const LAYER_STRUCTURE: u32 = 1;
pub const LAYER_PROPS: u32 = 2;

/// Extent of the collidable ground slab.
///
/// The Godot scene gets its only collidable ground from `scene_kit._add_ground`
/// — a 2000 m box centred on the origin. Manhattan's own water/land/park slabs
/// are meshes with no bodies, and the buildings have no bodies either. That
/// finite extent is a real quirk of the Godot build (a ray past 1 km from
/// origin finds no ground), and it is reproduced here so a parity check between
/// the two backends compares like with like.
const GROUND_HALF: f32 = 1000.0;
const GROUND_TOP: f32 = 0.0;
const GROUND_BOTTOM: f32 = -0.2;

#[derive(Clone, Debug)]
pub struct Bottle {
    pub pos: Vec3, // ENU
    pub vel: Vec3, // ENU
    pub t0: f32,
    pub landed: bool,
    pub owner: String,
    pub release_enu: [f32; 3],
    pub entity: Option<Entity>,
}

pub struct FixedWingState {
    pub id: String,
    /// Airframe root, for the transform sync.
    pub body: Option<Entity>,
    /// Sensor camera entities owned by this aircraft.
    pub sensors: Vec<Entity>,
    pub position: Vec3, // ENU
    pub velocity: Vec3, // ENU
    pub airspeed: f32,
    pub climb_rate: f32,
    pub pitch: f32,
    pub yaw: f32,
    pub roll: f32,
    pub altitude: f32,
    pub battery_level: f32,
    pub cmd_airspeed: f32,
    pub cmd_yaw_rate: f32,
    pub cmd_climb_rate: f32,
    pub observed: Vec<u8>,
    /// Where the sensor points relative to the airframe's nose, radians.
    ///
    /// Without this the demo's central beat is impossible: the camera AND
    /// `detect()` both derive from `yaw`, so an aircraft flying a circle can
    /// never see the point it is circling — it always looks along the tangent.
    /// A real aircraft would use a gimbal; this is that gimbal, and it must be
    /// applied IDENTICALLY to the camera and to `detect()`, or the model
    /// reasons about a picture it was never shown.
    pub sensor_yaw_offset: f32,
    pub payload_remaining: i32,
    pub alive: bool,
    pub last_pose_trace: f32,
    pub drain_mult: f32,
    /// Render layer this aircraft's airframe meshes live on, so a camera can
    /// be told to exclude exactly one airframe. Stable for the aircraft's
    /// lifetime.
    pub layer: usize,
}

impl FixedWingState {
    pub fn new(id: String, pos: Vec3, yaw: f32, cells: usize, layer: usize) -> Self {
        Self {
            id,
            body: None,
            sensors: Vec::new(),
            position: pos,
            velocity: Vec3::ZERO,
            airspeed: MIN_AIRSPEED,
            climb_rate: 0.0,
            pitch: 0.0,
            yaw,
            roll: 0.0,
            altitude: pos.z,
            battery_level: 100.0,
            cmd_airspeed: 0.0,
            cmd_yaw_rate: 0.0,
            cmd_climb_rate: 0.0,
            observed: vec![0u8; cells],
            sensor_yaw_offset: 0.0,
            payload_remaining: 1,
            alive: true,
            last_pose_trace: 0.0,
            drain_mult: 1.0,
            layer,
        }
    }

    pub fn state_json(&self) -> serde_json::Value {
        serde_json::json!({
            "position": [self.position.x, self.position.y, self.position.z],
            "velocity": [self.velocity.x, self.velocity.y, self.velocity.z],
            "airspeed": self.airspeed,
            "climb_rate": self.climb_rate,
            "pitch": self.pitch,
            "yaw": self.yaw,
            "roll": self.roll,
            "altitude": self.altitude,
            "battery_level": self.battery_level,
            "payload_remaining": self.payload_remaining,
            "sensor_yaw_offset": self.sensor_yaw_offset,
        })
    }

    /// Where the sensor looks, as an ENU unit vector. Shared by the camera
    /// mount and by `detect()` so the two cannot disagree.
    pub fn look_dir_enu(&self, look_down: f32) -> Vec3 {
        self.look_dir_enu_yawed(look_down, 0.0)
    }

    /// As `look_dir_enu`, plus a static yaw offset for a fixed secondary
    /// camera. The vehicle's own gimbal still applies on top, so a port-facing
    /// camera stays port-facing when the gimbal slews.
    pub fn look_dir_enu_yawed(&self, look_down: f32, extra_yaw: f32) -> Vec3 {
        let elevation = self.pitch - look_down;
        let look_yaw = self.yaw + self.sensor_yaw_offset + extra_yaw;
        Vec3::new(
            look_yaw.cos() * elevation.cos(),
            look_yaw.sin() * elevation.cos(),
            elevation.sin(),
        )
    }
}

#[derive(Clone, Debug)]
pub struct LoggedEvent {
    pub t: f32,
    pub kind: String,
    pub data: serde_json::Value,
}

impl LoggedEvent {
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({"t": self.t, "kind": self.kind, "data": self.data})
    }
}

/// Fleet-wide state. Mirrors the Godot autoload singleton: the aircraft table,
/// the shared occupancy grid, the event log and the airborne payloads.
pub struct Fleet {
    pub fw: Vec<FixedWingState>,
    pub events: Vec<LoggedEvent>,
    pub sim_time: f32,
    pub occ: Vec<u8>,
    pub grid: GridConfig,
    pub bottles: Vec<Bottle>,
    /// Monotonic airframe counter, for stable render-layer assignment.
    pub spawn_counter: usize,
}

impl Fleet {
    pub fn new(grid: GridConfig) -> Self {
        Self {
            fw: Vec::new(),
            events: Vec::new(),
            sim_time: 0.0,
            occ: vec![0u8; grid.w * grid.h],
            grid,
            bottles: Vec::new(),
            spawn_counter: 0,
        }
    }

    /// Render layers of every live airframe.
    pub fn airframe_layers(&self) -> Vec<usize> {
        self.fw.iter().map(|s| s.layer).collect()
    }

    pub fn cells(&self) -> usize {
        self.grid.w * self.grid.h
    }

    pub fn index_of(&self, id: &str) -> Option<usize> {
        self.fw.iter().position(|s| s.id == id)
    }
    pub fn get(&self, id: &str) -> Option<&FixedWingState> {
        self.fw.iter().find(|s| s.id == id)
    }
    pub fn get_mut(&mut self, id: &str) -> Option<&mut FixedWingState> {
        self.fw.iter_mut().find(|s| s.id == id)
    }

    pub fn log_event(&mut self, kind: &str, data: serde_json::Value) {
        self.events.push(LoggedEvent {
            t: self.sim_time,
            kind: kind.to_string(),
            data,
        });
    }

    /// Events belonging to one aircraft. Matches Godot: an event is this
    /// aircraft's if its data carries a matching `id`.
    pub fn events_for(&self, id: &str) -> Vec<serde_json::Value> {
        self.events
            .iter()
            .filter(|e| e.data.get("id").and_then(|v| v.as_str()) == Some(id))
            .map(|e| e.to_json())
            .collect()
    }

    pub fn all_states(&self) -> Vec<serde_json::Value> {
        self.fw
            .iter()
            .filter(|s| s.alive)
            .map(|s| {
                serde_json::json!({
                    "id": s.id,
                    "position": [s.position.x, s.position.y, s.position.z],
                    "yaw": s.yaw,
                    "altitude": s.altitude,
                })
            })
            .collect()
    }

    // ----------------------------------------------------------------- commands

    /// `climb` is the commanded climb rate in m/s, positive up. It defaults to
    /// zero at the call site so pre-existing two-argument callers keep their
    /// current behaviour of holding altitude rather than silently descending.
    pub fn drive(&mut self, id: &str, airspeed: f32, yaw_rate: f32, climb: f32) {
        if let Some(st) = self.get_mut(id) {
            st.cmd_airspeed = airspeed.clamp(-MAX_AIRSPEED, MAX_AIRSPEED);
            st.cmd_yaw_rate = yaw_rate.clamp(-MAX_YAW_RATE, MAX_YAW_RATE);
            st.cmd_climb_rate = climb.clamp(MIN_CLIMB_RATE, MAX_CLIMB_RATE);
        }
    }

    /// Loiter, not a true stop — a fixed-wing cannot hover.
    pub fn stop(&mut self, id: &str) {
        if let Some(st) = self.get_mut(id) {
            st.cmd_airspeed = MIN_AIRSPEED;
            st.cmd_yaw_rate = 0.0;
            st.cmd_climb_rate = 0.0;
            st.climb_rate = 0.0;
        }
    }

    pub fn set_sensor_yaw_offset(&mut self, id: &str, offset: f32) {
        if let Some(st) = self.get_mut(id) {
            st.sensor_yaw_offset = enu::wrap_pi(offset);
        }
    }

    /// Aim the sensor at an ENU ground point whatever the aircraft's heading.
    /// This is what an orbit uses to keep its centre in frame while flying a
    /// circle around it.
    pub fn aim_sensor_at(&mut self, id: &str, east: f32, north: f32) {
        let target = if let Some(st) = self.get(id) {
            Some(enu::wrap_pi(
                (north - st.position.y).atan2(east - st.position.x) - st.yaw,
            ))
        } else {
            None
        };
        if let Some(o) = target {
            self.set_sensor_yaw_offset(id, o);
        }
    }

    pub fn reset(&mut self, id: &str) {
        if let Some(st) = self.get_mut(id) {
            st.battery_level = 100.0;
            st.cmd_airspeed = 0.0;
            st.cmd_yaw_rate = 0.0;
            st.cmd_climb_rate = 0.0;
            st.position = Vec3::ZERO;
            st.velocity = Vec3::ZERO;
            st.airspeed = MIN_AIRSPEED;
            st.climb_rate = 0.0;
            st.pitch = 0.0;
            st.yaw = 0.0;
            st.roll = 0.0;
            st.altitude = 0.0;
            st.observed.fill(0);
        }
    }

    // ------------------------------------------------------------------ physics

    pub fn step(&mut self, env: &dyn Environment, dt: f32) {
        self.sim_time += dt;
        let now = self.sim_time;
        let n = self.fw.len();
        // Events are collected and appended after the per-aircraft mutable
        // borrows end, rather than logged inline as the GDScript does.
        let mut pending: Vec<LoggedEvent> = Vec::new();
        for i in 0..n {
            let mut envelope: Vec<(&'static str, f32, f32)> = Vec::new();
            {
                let st = &mut self.fw[i];
                apply_flight_dynamics(st, dt, &mut envelope);
                st.position += st.velocity * dt;
                // Backstop the envelope in POSITION as well as in rate: zeroing
                // the climb rate only stops further descent, it cannot undo the
                // fraction of a tick that already carried the aircraft past the
                // limit. Without this the altitude floor leaks by a few
                // centimetres and "z never below the floor" stops being a
                // checkable invariant.
                st.position.z = st.position.z.clamp(ALT_FLOOR_M, ALT_CEILING_M);
                st.altitude = st.position.z;
                let moved = st.velocity.length() * dt;
                st.battery_level = (st.battery_level
                    - (BASE_DRAIN_IDLE * dt + BASE_DRAIN_PER_M * moved) * st.drain_mult)
                    .max(0.0);
            }
            let id = self.fw[i].id.clone();
            for (limit, alt, bound) in envelope {
                let mut data = serde_json::json!({"id": id, "limit": limit, "alt": alt});
                let bound_key = if limit == "alt_floor" { "floor" } else { "ceiling" };
                data[bound_key] = serde_json::json!(bound);
                pending.push(LoggedEvent {
                    t: now,
                    kind: "envelope_protection".into(),
                    data,
                });
            }
            self.sweep_observed(env, i);
            if now - self.fw[i].last_pose_trace >= 1.0 {
                self.fw[i].last_pose_trace = now;
                let p = self.fw[i].position;
                pending.push(LoggedEvent {
                    t: now,
                    kind: "pose_trace".into(),
                    data: serde_json::json!({"id": id, "x": p.x, "y": p.y, "z": p.z}),
                });
            }
        }
        self.events.extend(pending);
    }

    // -------------------------------------------------------------------- grids

    /// Rebuild the shared occupancy grid from the env's blocking rects.
    ///
    /// Iterates per RECT over just the cells it covers, rather than per cell
    /// over every rect. Same result, but the cost is the total footprint area
    /// instead of cells x rects — the latter is fine for an env with a dozen
    /// walls and completely infeasible for a city with tens of thousands of
    /// buildings, where it would hang on load rather than merely run slowly.
    pub fn rebuild_occ_grid(&mut self, env: &dyn Environment) {
        let g = self.grid;
        self.occ = vec![0u8; g.w * g.h];
        // The cell range is computed in f64, matching GDScript's promotion of
        // scalar float expressions to double. This is not cosmetic: 924 of the
        // 45k footprint edges land close enough to a 5 m cell boundary that f32
        // and f64 floor/ceil disagree, and computing it in f32 left 97 cells
        // unoccupied that the Godot build marks. Those cells are lethal-obstacle
        // truth for routing, so "almost the same grid" is not the same grid.
        // Each term is widened to f64 SEPARATELY before being combined, which
        // is what reading a `Vector2.x` into a GDScript `float` expression
        // does.
        //
        // This loop is NOT where the old 649-cell divergence from Godot came
        // from, despite what an earlier version of this comment claimed. The
        // range here and Godot's are the same computation, and an over-wide
        // range is harmless anyway because `intersects` re-tests every cell —
        // only a too-NARROW range can lose a cell. The real cause was upstream
        // in `env::manhattan`, which accumulated the recentring extents in f32
        // and so shifted every footprint 4.6e-05 m; see the comment there.
        let res = g.res as f64;
        let cell_lo = |v: f32, o: f32| (((v as f64 - o as f64) / res).floor() as isize).max(0) as usize;
        let cell_hi = |v: f32, sz: f32, o: f32| {
            (((v as f64 + sz as f64 - o as f64) / res).ceil() as isize).max(0) as usize
        };
        for r in env.walls() {
            let gx0 = cell_lo(r.pos.x, g.origin.x);
            let gy0 = cell_lo(r.pos.y, g.origin.y);
            let gx1 = cell_hi(r.pos.x, r.size.x, g.origin.x);
            let gy1 = cell_hi(r.pos.y, r.size.y, g.origin.y);
            let gx1 = gx1.min(g.w);
            let gy1 = gy1.min(g.h);
            for gy in gy0..gy1 {
                let row = gy * g.w;
                let wy = g.origin.y + gy as f32 * g.res;
                for gx in gx0..gx1 {
                    if self.occ[row + gx] == 1 {
                        continue;
                    }
                    // Cell-vs-rect overlap, not cell-centre containment: a wall
                    // thinner than a cell can otherwise fall entirely between
                    // two cell centres and never register as occupied.
                    let cell = Rect2::new(g.origin.x + gx as f32 * g.res, wy, g.res, g.res);
                    if r.intersects(&cell) {
                        self.occ[row + gx] = 1;
                    }
                }
            }
        }
    }

    fn sweep_observed(&mut self, env: &dyn Environment, i: usize) {
        const N_RAYS: usize = 40;
        let (pos, yaw) = {
            let st = &self.fw[i];
            (st.position, st.yaw)
        };
        let hfov = detect_hfov_half();
        // Boresight elevation, signed positive-up: pitched down by the sensor's
        // own tilt.
        let bearing_v = -DETECT_LOOK_DOWN;
        let mut ends: Vec<Vec2> = Vec::with_capacity(N_RAYS);
        for k in 0..N_RAYS {
            let t = k as f32 / (N_RAYS - 1) as f32;
            let bearing_h = -hfov + (hfov - -hfov) * t;
            let dir_h = Vec2::new((yaw + bearing_h).cos(), (yaw + bearing_h).sin());
            let dir = Vec3::new(dir_h.x, dir_h.y, bearing_v.tan()).normalize();
            let hit = self.raycast_physics(env, pos, dir, DETECT_RANGE, LAYER_STRUCTURE, None);
            let end = match hit {
                Some((d, _)) => {
                    let p = pos + dir * d;
                    Vec2::new(p.x, p.y)
                }
                None => Vec2::new(pos.x, pos.y) + dir_h * DETECT_RANGE,
            };
            ends.push(end);
        }
        let start = Vec2::new(pos.x, pos.y);
        for end in ends {
            self.mark_line_observed(i, start, end);
        }
    }

    fn mark_line_observed(&mut self, i: usize, a: Vec2, b: Vec2) {
        let g = self.grid;
        let steps = (a.distance(b) / (g.res * 0.5)) as usize + 1;
        for k in 0..=steps {
            let t = k as f32 / steps.max(1) as f32;
            let p = a.lerp(b, t);
            let gx = ((p.x - g.origin.x) / g.res) as isize;
            let gy = ((p.y - g.origin.y) / g.res) as isize;
            if gx >= 0 && (gx as usize) < g.w && gy >= 0 && (gy as usize) < g.h {
                self.fw[i].observed[gy as usize * g.w + gx as usize] = 1;
            }
        }
    }

    pub fn grid_json(&self, id: &str) -> Option<serde_json::Value> {
        use base64::Engine as _;
        let st = self.get(id)?;
        let b64 = base64::engine::general_purpose::STANDARD;
        Some(serde_json::json!({
            "res": self.grid.res,
            "origin": [self.grid.origin.x, self.grid.origin.y],
            "w": self.grid.w,
            "h": self.grid.h,
            "occ": b64.encode(&self.occ),
            "obs": b64.encode(&st.observed),
        }))
    }

    // ------------------------------------------------------------------ sensing

    /// Ray against the things that actually have collision in this scene: the
    /// finite ground slab (`LAYER_STRUCTURE`) and the props, settled payloads
    /// included (`LAYER_PROPS`). Buildings are NOT here — they are tested
    /// analytically by the env. `dir` must be normalised.
    ///
    /// Returns `(distance, prop index or None for ground)`.
    fn raycast_physics(
        &self,
        env: &dyn Environment,
        from: Vec3,
        dir: Vec3,
        max_dist: f32,
        mask: u32,
        exclude_prop: Option<usize>,
    ) -> Option<(f32, Option<usize>)> {
        let mut best: Option<(f32, Option<usize>)> = None;
        if mask & LAYER_STRUCTURE != 0 {
            if let Some(d) = ray_aabb(
                from,
                dir,
                Vec3::new(-GROUND_HALF, -GROUND_HALF, GROUND_BOTTOM),
                Vec3::new(GROUND_HALF, GROUND_HALF, GROUND_TOP),
                max_dist,
            ) {
                best = Some((d, None));
            }
        }
        if mask & LAYER_PROPS != 0 {
            for (idx, p) in env.props().iter().enumerate() {
                if Some(idx) == exclude_prop {
                    continue;
                }
                let (min, max) = p.aabb();
                if let Some(d) = ray_aabb(from, dir, min, max, max_dist) {
                    if best.is_none_or(|(b, _)| d < b) {
                        best = Some((d, Some(idx)));
                    }
                }
            }
        }
        best
    }

    /// Is the straight segment from `from` to `to` blocked, excluding one prop
    /// (the detection target itself)? Godot's `_raycast(from, to, mask, …)`
    /// reduced to the predicate every caller actually used.
    fn occluded(
        &self,
        env: &dyn Environment,
        from: Vec3,
        to: Vec3,
        mask: u32,
        exclude_prop: Option<usize>,
    ) -> bool {
        let delta = to - from;
        let span = delta.length();
        if span < 1e-6 {
            return false;
        }
        self.raycast_physics(env, from, delta / span, span, mask, exclude_prop)
            .is_some()
    }

    /// Distance to the nearest surface along a normalised ENU ray: the
    /// analytic city, the props, and the ground slab together. `None` means the
    /// ray escaped (sky).
    ///
    /// This is the one query a range sensor needs, and it exists because the
    /// city is analytic: the same exact slab test that answers occlusion for
    /// `detect()` answers depth, so a depth image needs no GPU prepass and has
    /// no z-buffer precision problem at a 40 km far plane.
    pub fn range_along(
        &self,
        env: &dyn Environment,
        from: Vec3,
        dir: Vec3,
        max_dist: f32,
    ) -> Option<f32> {
        let mut best = env.raycast_structures(from, dir, max_dist);
        if let Some((d, _)) =
            self.raycast_physics(env, from, dir, max_dist, LAYER_STRUCTURE | LAYER_PROPS, None)
        {
            if best.is_none_or(|b| d < b) {
                best = Some(d);
            }
        }
        best
    }

    pub fn detect(&self, env: &dyn Environment, id: &str) -> Vec<DetectionRow> {
        let Some(st) = self.get(id) else {
            return Vec::new();
        };
        let hfov = detect_hfov_half();
        let vfov = detect_vfov_half();
        // Same sensor offset the camera uses.
        let sensor_yaw = st.yaw + st.sensor_yaw_offset;
        let forward = Vec2::new(sensor_yaw.cos(), sensor_yaw.sin());
        let here = Vec2::new(st.position.x, st.position.y);
        let mut out: Vec<DetectionRow> = Vec::new();

        // --- props pass ---
        for (idx, node) in env.props().iter().enumerate() {
            let wp = Vec2::new(node.pos.x, node.pos.y);
            let to_target = wp - here;
            let dist = to_target.length();
            // Range is per class, so the label has to be read before the cutoff.
            let max_range = detect_range(&node.label);
            if dist > max_range || dist < 0.01 {
                continue;
            }
            let rel = node.pos - st.position;
            let horiz_dist = Vec2::new(rel.x, rel.y).length();
            if horiz_dist < 0.01 {
                continue;
            }
            let angle = enu::angle_to(forward, Vec2::new(rel.x, rel.y).normalize());
            // `atan2(rel_up, horiz)` is signed positive-up; the boresight is
            // pitched down to elevation -look_down, so centring on that
            // boresight means ADDING look_down here: a target level with the
            // aircraft should read near the edge of the downward-pitched FOV,
            // not need to be near the aircraft's own altitude.
            let vert_angle = rel.z.atan2(horiz_dist) + DETECT_LOOK_DOWN;
            if angle.abs() > hfov || vert_angle.abs() > vfov {
                continue;
            }
            if self.occluded(
                env,
                st.position,
                node.pos,
                LAYER_STRUCTURE | LAYER_PROPS,
                Some(idx),
            ) {
                continue;
            }
            let range_penalty = ((dist - 40.0) / 40.0).clamp(0.0, 1.0) * 0.3;
            out.push(DetectionRow {
                label: node.label.clone(),
                confidence: (0.9 - range_penalty).clamp(0.2, 0.9),
                // nx/ny are IMAGE convention: ny 0 is the TOP row, so it runs
                // opposite to elevation. Getting this backwards mirrored every
                // pixel the model picked.
                nx: (0.5 + (angle / hfov) * 0.5).clamp(0.0, 1.0),
                ny: (0.5 - (vert_angle / vfov) * 0.5).clamp(0.0, 1.0),
                world: [wp.x, wp.y, node.pos.z],
                top_z: node.top_z,
                peer_id: None,
                peer_alt: None,
            });
        }

        // --- structure pass ---
        // Buildings are not props, and in a city that silence is dangerous:
        // detect() is the only obstacle channel the VLM has, so without this a
        // model flying Manhattan would be told the route ahead was clear while
        // heading into midtown.
        out.extend(env.structure_detections(
            st.position,
            sensor_yaw,
            SensorCfg {
                range: detect_range("wall"),
                hfov_half: hfov,
                vfov_half: vfov,
                look_down: DETECT_LOOK_DOWN,
            },
        ));

        // --- peer pass ---
        // With no radio link between them, the only way one aircraft can learn
        // anything from another is by LOOKING at it, so a teammate that has
        // descended and started circling is readable as "it has probably found
        // something". Same FOV and occlusion rules as props, longer range.
        for other in &self.fw {
            if other.id == st.id || !other.alive {
                continue;
            }
            let opos = Vec2::new(other.position.x, other.position.y);
            let odist = opos.distance(here);
            if odist > PEER_DETECT_RANGE || odist < 0.01 {
                continue;
            }
            let rel = other.position - st.position;
            let ohoriz = Vec2::new(rel.x, rel.y).length();
            if ohoriz < 0.01 {
                continue;
            }
            let oangle = enu::angle_to(forward, Vec2::new(rel.x, rel.y).normalize());
            let overt = rel.z.atan2(ohoriz) + DETECT_LOOK_DOWN;
            if oangle.abs() > hfov || overt.abs() > vfov {
                continue;
            }
            if self.occluded(env, st.position, other.position, LAYER_STRUCTURE, None) {
                continue;
            }
            out.push(DetectionRow {
                label: "aircraft".into(),
                confidence: (0.9 - ((odist - 100.0) / 200.0).clamp(0.0, 1.0) * 0.4).clamp(0.2, 0.9),
                nx: (0.5 + (oangle / hfov) * 0.5).clamp(0.0, 1.0),
                ny: (0.5 - (overt / vfov) * 0.5).clamp(0.0, 1.0),
                world: [other.position.x, other.position.y, other.position.z],
                top_z: None,
                peer_id: Some(other.id.clone()),
                peer_alt: Some(other.altitude),
            });
        }
        out
    }

    /// Turn a picked pixel into an ENU world point.
    pub fn unproject(&self, env: &dyn Environment, id: &str, nx: f32, ny: f32) -> Option<[f32; 3]> {
        let st = self.get(id)?;
        let hfov = detect_hfov_half();
        let vfov = detect_vfov_half();
        let bearing_h = (nx - 0.5) * 2.0 * hfov;
        // Inverse of detect()'s `vert_angle = atan2(rel_up, horiz) + look_down`,
        // so bearing_v is absolute signed-positive-up elevation and look_down
        // is SUBTRACTED. ny is image convention (0 = top row) while elevation
        // runs the other way, hence `0.5 - ny`: this was `ny - 0.5` for the
        // whole life of the Godot function, i.e. vertically mirrored, so every
        // navigate_to_point was aimed at the mirror image of what was picked
        // and a sensible low-in-frame pick came back unresolvable.
        let bearing_v = (0.5 - ny) * 2.0 * vfov - DETECT_LOOK_DOWN;
        let sensor_yaw = st.yaw + st.sensor_yaw_offset;
        let forward = Vec2::new(sensor_yaw.cos(), sensor_yaw.sin());

        // First, try to match a known visible object at this bearing.
        let mut best_node: Option<usize> = None;
        let mut best_diff = 0.15f32; // radians
        for (idx, node) in env.props().iter().enumerate() {
            let wp = Vec2::new(node.pos.x, node.pos.y);
            let dist = wp.distance(Vec2::new(st.position.x, st.position.y));
            if dist > DETECT_RANGE || dist < 0.01 {
                continue;
            }
            let rel = node.pos - st.position;
            let horiz_dist = Vec2::new(rel.x, rel.y).length();
            if horiz_dist < 0.01 {
                continue;
            }
            let angle = enu::angle_to(forward, Vec2::new(rel.x, rel.y).normalize());
            let vert_angle = rel.z.atan2(horiz_dist) + DETECT_LOOK_DOWN;
            if angle.abs() > hfov || vert_angle.abs() > vfov {
                continue;
            }
            if self.occluded(
                env,
                st.position,
                node.pos,
                LAYER_STRUCTURE | LAYER_PROPS,
                Some(idx),
            ) {
                continue;
            }
            // Angular distance in BOTH axes. Comparing horizontal bearing only
            // made every pixel in a column snap to whatever prop stood in that
            // column, so pointing at open ground below someone, or at the sky
            // above them, both returned the person. `vert_angle` is measured
            // from the boresight while `bearing_v` is absolute elevation, hence
            // the look_down term putting them in the same frame.
            let diff = Vec2::new(angle - bearing_h, vert_angle - (bearing_v + DETECT_LOOK_DOWN))
                .length();
            if diff < best_diff {
                best_diff = diff;
                best_node = Some(idx);
            }
        }
        if let Some(idx) = best_node {
            let p = env.props()[idx].pos;
            return Some([p.x, p.y, p.z]);
        }

        // Fallback: cast the picked ray.
        let dir_h = Vec2::new(
            (sensor_yaw + bearing_h).cos(),
            (sensor_yaw + bearing_h).sin(),
        );
        let dir = Vec3::new(dir_h.x, dir_h.y, bearing_v.tan()).normalize();
        // Reach as far as the longest thing this sensor can see, not the
        // default people range: a pixel near the horizon corresponds to a point
        // hundreds of metres out, and clipping at 80 m made every such pick
        // unresolvable.
        let reach = DETECT_RANGE.max(PEER_DETECT_RANGE);
        if let Some((d, _)) = self.raycast_physics(
            env,
            st.position,
            dir,
            reach,
            LAYER_STRUCTURE | LAYER_PROPS,
            None,
        ) {
            let p = st.position + dir * d;
            return Some([p.x, p.y, p.z]);
        }

        // Nothing solid along the ray. If it points downward at all it still
        // meets the ground, and that intersection is the honest answer:
        // returning null instead made the caller treat an ordinary "point at
        // open terrain ahead" pick as unresolvable and fall through to a no-op
        // navigation path that let the aircraft keep flying straight on.
        if dir.z < -0.01 {
            let t = st.position.z / -dir.z;
            if t > 0.0 && t < reach * 4.0 {
                let g = st.position + dir * t;
                return Some([g.x, g.y, 0.0]);
            }
        }
        None
    }

    /// Ground-truth oracle: harness and scoring only, never fed to the brain's
    /// sensing API.
    pub fn prop_truth(&self, env: &dyn Environment) -> Vec<serde_json::Value> {
        env.props()
            .iter()
            .map(|p| {
                serde_json::json!({
                    "label": p.label,
                    "world": [p.pos.x, p.pos.y, p.pos.z],
                    "is_anomaly": p.is_anomaly,
                })
            })
            .collect()
    }

    // ------------------------------------------------------------------ payload

    /// Release the payload: a real ballistic body carrying the aircraft's
    /// velocity, not a teleport to the aim point. Where it lands is therefore a
    /// genuine consequence of the release solution the aircraft flew, and a bad
    /// release visibly misses.
    pub fn drop_payload(&mut self, id: &str) -> Option<serde_json::Value> {
        let (pos, vel, airspeed) = {
            let st = self.get(id)?;
            if st.payload_remaining <= 0 {
                let id = id.to_string();
                self.log_event(
                    "payload_release_refused",
                    serde_json::json!({"id": id, "reason": "no payload remaining"}),
                );
                return None;
            }
            (st.position, st.velocity, st.airspeed)
        };
        if let Some(st) = self.get_mut(id) {
            st.payload_remaining -= 1;
        }
        let release = serde_json::json!({
            "id": id,
            "release_enu": [pos.x, pos.y, pos.z],
            "velocity_enu": [vel.x, vel.y, vel.z],
            "airspeed": airspeed,
            "t": self.sim_time,
        });
        self.bottles.push(Bottle {
            // Released from just below the airframe, carrying its velocity.
            pos: Vec3::new(pos.x, pos.y, (pos.z - 0.6).max(0.2)),
            vel,
            t0: self.sim_time,
            landed: false,
            owner: id.to_string(),
            release_enu: [pos.x, pos.y, pos.z],
            entity: None,
        });
        self.log_event("payload_released", release.clone());
        Some(release)
    }

    /// Integrate airborne payloads, then freeze the ones at rest and promote
    /// them to sensable props.
    ///
    /// Promotion is the demo's stigmergy channel: with no radio link, a bottle
    /// on the ground is a message the second drone can read from the
    /// environment itself. Time-capped as well as rest-checked, because a body
    /// that ends up on a slope can jitter indefinitely without settling and an
    /// un-promoted bottle silently breaks that channel.
    pub fn update_bottles(&mut self, env: &mut dyn Environment, dt: f32) -> Vec<Prop> {
        const G: f32 = 9.81;
        const REST_Z: f32 = 0.14; // half the 0.28 m cylinder
        let mut landed_now: Vec<(usize, Vec3)> = Vec::new();
        for (i, b) in self.bottles.iter_mut().enumerate() {
            if b.landed {
                continue;
            }
            b.vel.z -= G * dt;
            let prev = b.pos;
            b.pos += b.vel * dt;
            // The only thing a payload can land on in this scene is the ground
            // slab: buildings carry no collision, exactly as in the Godot
            // build, so a bottle released over a roof falls past it.
            let over_ground = b.pos.x.abs() <= GROUND_HALF && b.pos.y.abs() <= GROUND_HALF;
            if over_ground && b.pos.z <= REST_Z && prev.z > REST_Z - 1.0 {
                b.pos.z = REST_Z;
                b.vel = Vec3::ZERO;
            }
            let settled = b.vel.length() < 0.25;
            if settled || self.sim_time - b.t0 > 6.0 {
                b.landed = true;
                landed_now.push((i, b.pos));
            }
        }
        let mut new_props = Vec::new();
        for (i, rest) in landed_now {
            let b = &self.bottles[i];
            let miss = Vec2::new(rest.x, rest.y)
                - Vec2::new(b.release_enu[0], b.release_enu[1]);
            let owner = b.owner.clone();
            let prop = Prop {
                name: format!("payload_{}_{}", owner, i + 1),
                label: "water_bottle".into(),
                is_anomaly: false,
                top_z: Some(rest.z + 0.14),
                pos: rest,
                size: Vec3::new(0.1, 0.28, 0.1),
                color: [0.25, 0.55, 0.95],
                cab: None,
            };
            env.props_mut().push(prop.clone());
            new_props.push(prop);
            self.log_event(
                "payload_landed",
                serde_json::json!({
                    "id": owner,
                    "rest_enu": [rest.x, rest.y, rest.z],
                    "throw_m": miss.length(),
                }),
            );
        }
        new_props
    }
}

/// Flight dynamics for one aircraft.
///
/// Envelope interventions are pushed to `envelope` rather than logged inline so
/// the caller owns the event log; they MUST stay visible in telemetry, since
/// "zero envelope interventions" is one of the demo's take-selection gates.
fn apply_flight_dynamics(
    st: &mut FixedWingState,
    dt: f32,
    envelope: &mut Vec<(&'static str, f32, f32)>,
) {
    // Smooth airspeed toward command.
    let airspeed_delta = st.cmd_airspeed - st.airspeed;
    let airspeed_acc = 5.0; // m/s^2
    st.airspeed += (airspeed_delta * airspeed_acc * dt)
        .clamp(-airspeed_delta.abs(), airspeed_delta.abs());
    st.airspeed = st.airspeed.clamp(MIN_AIRSPEED, MAX_AIRSPEED);

    st.yaw = enu::wrap_pi(st.yaw + st.cmd_yaw_rate * dt);

    st.pitch = st
        .climb_rate
        .atan2(st.airspeed)
        .clamp((-30.0f32).to_radians(), (20.0f32).to_radians());

    // Bank angle for a coordinated turn: tan(phi) = v * omega / g.
    //
    // Two fixes over the naive form. Using the raw ratio as an angle is only
    // valid for small banks — at 25 m/s and 0.6 rad/s the ratio is 1.53 (88
    // deg), so it pinned to the limit for any real turn. And assigning the
    // result straight to roll snapped the aircraft from level to fully banked
    // in one frame; bank is the whole visual language of a turning aircraft, so
    // it is eased like the climb rate is.
    //
    // Sign: the visual model's nose is local +Z and its wings run along local
    // +-X, so with up at +Y the RIGHT wing is -X and a positive roll about +Z
    // lifts the LEFT wing, i.e. banks right. A positive yaw rate is CCW in ENU,
    // i.e. a LEFT turn. Taking the bank straight from the yaw rate therefore
    // banked the aircraft away from its own turn; hence the leading minus.
    let mut bank_target = -(st.cmd_yaw_rate * st.airspeed / 9.81).atan();
    bank_target = bank_target.clamp(-MAX_ROLL_DEG.to_radians(), MAX_ROLL_DEG.to_radians());
    let roll_tc = 1.0; // ~1 s to roll in or out, as real light aircraft manage
    st.roll += (bank_target - st.roll) * (dt / roll_tc).min(1.0);

    // Climb rate: ease toward the command, then clamp. Eased with a ~1.5 s time
    // constant rather than applied instantly, because a step change would snap
    // the pitch computed from it and make a chase camera jerk, which shows up
    // directly in the footage.
    let climb_tc = 1.5;
    st.climb_rate += (st.cmd_climb_rate - st.climb_rate) * (dt / climb_tc).min(1.0);
    // Aerodynamic limit: a fixed-wing cannot out-climb its own airspeed.
    // Capping to a 20 deg flight-path angle keeps commanded climbs physically
    // honest at low airspeed instead of letting a 12 m/s aircraft climb at
    // 8 m/s (a 42 deg angle).
    let climb_limit = (20.0f32).to_radians().tan() * st.airspeed;
    st.climb_rate = st.climb_rate.clamp(-climb_limit, climb_limit);
    st.climb_rate = st.climb_rate.clamp(MIN_CLIMB_RATE, MAX_CLIMB_RATE);

    // Envelope protection: hard altitude floor and ceiling. The demo
    // deliberately flies low run-ins, so a model that commands an
    // over-aggressive descent must be caught by the vehicle.
    if st.position.z <= ALT_FLOOR_M && st.climb_rate < 0.0 {
        st.climb_rate = 0.0;
        st.cmd_climb_rate = 0.0;
        envelope.push(("alt_floor", st.position.z, ALT_FLOOR_M));
    } else if st.position.z >= ALT_CEILING_M && st.climb_rate > 0.0 {
        st.climb_rate = 0.0;
        st.cmd_climb_rate = 0.0;
        envelope.push(("alt_ceiling", st.position.z, ALT_CEILING_M));
    }

    // The nose must point where the aircraft is going. Vertical motion is
    // already carried by climb_rate and pitch is DERIVED from it, so pitching
    // the velocity vector as well would double-count the climb; horizontal
    // velocity is along the nose, vertical is the climb rate.
    let forward = Vec3::new(st.yaw.cos(), st.yaw.sin(), 0.0);
    st.velocity = forward * st.airspeed + Vec3::new(0.0, 0.0, st.climb_rate);
}
