//! Headless Bevy backend for the Presidio drone simulator.
//!
//! A drop-in replacement for the Godot sim at the TCP seam: it speaks the same
//! newline-JSON protocol as `drone/sim/godot/scripts/ipc_server.gd`, so
//! `engine_client.py`, `rover/sim/depot_client.py` and
//! `fw_manhattan_figure8.py` drive it unchanged. Nothing under `drone/sim/*.py`
//! needs to know which engine is running.
//!
//! The reason this exists: Godot cannot render to a texture without a display
//! server, so the Godot sim needs Xvfb on Linux and must run windowed on macOS
//! (where `--headless` leaves every camera SubViewport blank). wgpu renders
//! offscreen with no window and no display server at all.
//!
//! Scope today is the Manhattan fixed-wing scenario. See README.md.

mod airframe;
mod args;
mod enu;
mod env;
mod fixedwing;
mod geom;
mod ipc;
mod scenario;
mod scene;
mod sensors;

use bevy::app::{AppExit, ScheduleRunnerPlugin};
use bevy::camera::visibility::RenderLayers;
use bevy::light::GlobalAmbientLight;
use bevy::prelude::*;
use bevy::window::ExitCondition;
use fixedwing::{Fleet, FixedWingState};
use scenario::{Environment, Scenario};
use sensors::{Modality, Mount, Sensor};
use std::time::Duration;

/// Default fixed simulation tick, matching the Godot project's
/// `physics_ticks_per_second = 60`. Overridable with `--tick-hz`.
///
/// The flight model's easing constants are all expressed in seconds against
/// the measured dt, so the rate does not change how the aircraft flies. It does
/// change frame-grab latency, which is frame-boundary bound rather than
/// GPU bound: a grab costs about three ticks, so 60 Hz puts a floor of ~50 ms
/// on it regardless of how fast the scene renders.
const DEFAULT_TICK_HZ: f64 = 60.0;

#[derive(Resource)]
struct Ipc(ipc::IpcChannel);

/// The loaded world. Boxed behind the `Environment` trait so a procedural
/// scenario can take Manhattan's place without touching the vehicle model.
#[derive(Resource)]
struct World(Box<dyn Environment>);

#[derive(Resource)]
struct FleetRes(Fleet);

#[derive(Resource)]
struct Scen(Scenario);

/// Free-standing observer views, by name. Godot's `_vantages` dictionary.
#[derive(Resource, Default)]
struct Vantages(std::collections::HashMap<String, Entity>);

fn main() {
    let port = args::launch_arg_u16("--ipc-port", 9999);
    let env_name = args::launch_arg("--env", "office");
    let seed = args::launch_arg_u64("--seed", 0);
    let building_scale = args::launch_arg_f32("--building-scale", 1.0);
    let region = parse_region(&args::launch_arg("--env-region", ""));
    let tick_hz = {
        let v = args::launch_arg("--tick-hz", "")
            .parse::<f64>()
            .unwrap_or(DEFAULT_TICK_HZ);
        v.clamp(1.0, 1000.0)
    };

    let scenario = Scenario {
        name: env_name,
        seed,
        region,
        building_scale,
    };

    let Some(world) = env::build(&scenario) else {
        eprintln!(
            "[env] '{}' is not implemented by this backend (only 'manhattan' is). \
             Refusing to start rather than silently flying a different world.",
            scenario.name
        );
        std::process::exit(2);
    };

    let grid = world.grid_config();
    let mut fleet = Fleet::new(grid);
    fleet.rebuild_occ_grid(world.as_ref());

    let channel = match ipc::start(port) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[IpcServer] Failed to listen on port {port}: {e}");
            std::process::exit(1);
        }
    };

    App::new()
        .insert_resource(Ipc(channel))
        .insert_resource(World(world))
        .insert_resource(FleetRes(fleet))
        .insert_resource(Scen(scenario))
        .init_resource::<Vantages>()
        .init_resource::<sensors::rgb::Captures>()
        // The sky's ground half is matched to how the distant water actually
        // RENDERS rather than to its albedo: the water is a lit, faintly
        // specular surface and comes out far brighter than the raw colour fed
        // to it. There is ALWAYS a band of this above the horizon, because the
        // water plane is finite and the far plane is 40 km; enlarging the plane
        // cannot close it.
        .insert_resource(ClearColor(Color::srgb(0.37, 0.52, 0.70)))
        .add_plugins(DefaultPlugins.set(WindowPlugin {
            primary_window: None,
            exit_condition: ExitCondition::DontExit,
            ..default()
        }))
        .add_plugins(ScheduleRunnerPlugin::run_loop(Duration::from_secs_f64(
            1.0 / tick_hz,
        )))
        .insert_resource(Time::<Fixed>::from_hz(tick_hz))
        .add_systems(Startup, setup)
        .add_systems(FixedUpdate, step_sim)
        .add_systems(
            Update,
            (
                dispatch,
                sync_sensor_mounts,
                // Must run after dispatch so a capture queued this frame starts
                // warming its camera immediately.
                sensors::rgb::drive.after(dispatch),
                scene::spin_props,
            ),
        )
        .run();
}

fn parse_region(arg: &str) -> Option<[f32; 4]> {
    if arg.is_empty() {
        return None;
    }
    let parts: Vec<&str> = arg.split(',').collect();
    if parts.len() != 4 {
        eprintln!("[env] --env-region needs east0,north0,east1,north1");
        return None;
    }
    let mut out = [0.0f32; 4];
    for (i, p) in parts.iter().enumerate() {
        out[i] = p.trim().parse().ok()?;
    }
    Some(out)
}

fn setup(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut ambient: ResMut<GlobalAmbientLight>,
    world: Res<World>,
    scen: Res<Scen>,
    fleet: Res<FleetRes>,
) {
    scene::spawn_sky(&mut commands, &mut ambient);

    // Downcast is confined to scene construction: the render-side geometry of a
    // city is specific to that city, while everything the flight model needs is
    // already behind the `Environment` trait.
    let city = world
        .0
        .as_any()
        .downcast_ref::<env::manhattan::Manhattan>()
        .expect("only the manhattan env builds a scene today");
    scene::spawn_manhattan(
        &mut commands,
        &mut meshes,
        &mut materials,
        city,
        scen.0.seed,
    );
    for prop in world.0.props() {
        scene::spawn_prop(&mut commands, &mut meshes, &mut materials, prop);
    }

    let g = fleet.0.grid;
    println!(
        "[env_manhattan] ready — {} buildings, tallest {:.0} m (scale {:.1}x), grid {}x{} @ {:.0} m",
        city.building_count(),
        city.tallest(),
        scen.0.building_scale,
        g.w,
        g.h,
        g.res
    );
}

/// One fixed tick: fly every aircraft, advance the world clock, integrate
/// payloads, then push poses into the transform hierarchy.
fn step_sim(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut fleet: ResMut<FleetRes>,
    mut world: ResMut<World>,
    time: Res<Time<Fixed>>,
    mut airframes: Query<(&scene::Airframe, &mut Transform)>,
) {
    let dt = time.delta_secs();
    world.0.advance(dt);
    fleet.0.step(world.0.as_ref(), dt);
    let new_props = fleet.0.update_bottles(world.0.as_mut(), dt);
    for prop in &new_props {
        scene::spawn_prop(&mut commands, &mut meshes, &mut materials, prop);
    }

    for (af, mut tf) in &mut airframes {
        if let Some(st) = fleet.0.get(&af.vehicle) {
            *tf = airframe_transform(st);
        }
    }
}

/// Pose an airframe from flight state.
///
/// Translation is the plain ENU -> render mapping. Rotation is YXZ Euler, and
/// two of the three signs are non-obvious:
///
///   * yaw is `yaw + 90 deg`, not `-yaw + 90 deg`. The mesh's nose is local +Z,
///     and under a +Y rotation phi local +Z points to (sin phi, 0, cos phi); to
///     make the nose follow the ENU heading the rotation must be yaw + 90. The
///     negated form reflects the north component, so the nose pointed correctly
///     only for due east/west travel and backwards for north/south.
///   * pitch is NEGATED here and only here. `pitch` is atan2(climb, airspeed),
///     so positive means climbing, which is the sign the sensor code wants; but
///     a positive rotation about X drives a local +Z nose DOWN. Negating at the
///     render site keeps "positive = climbing" true everywhere else.
fn airframe_transform(st: &FixedWingState) -> Transform {
    Transform {
        translation: enu::to_engine(st.position),
        rotation: Quat::from_euler(
            EulerRot::YXZ,
            st.yaw + std::f32::consts::FRAC_PI_2,
            -st.pitch,
            st.roll,
        ),
        scale: Vec3::ONE,
    }
}

/// Up vector for a look-at, picked so a straight-down view has a defined roll.
///
/// `looking_at` is degenerate when the view direction is parallel to `up`, and
/// it then produces an arbitrary roll: a nadir vantage over the island came out
/// rotated ~90 deg, which is exactly the view most likely to be read as a map.
/// Falling back to ENU north (engine -Z) puts north up, the convention anyone
/// reading a top-down frame expects. Godot's `look_at` has the same degeneracy.
fn up_for(dir: Vec3) -> Vec3 {
    if dir.normalize_or_zero().dot(Vec3::Y).abs() > 0.999 {
        Vec3::NEG_Z
    } else {
        Vec3::Y
    }
}

/// Resolve every sensor's mount into a camera transform, every frame.
fn sync_sensor_mounts(fleet: Res<FleetRes>, mut q: Query<(&Sensor, &mut Transform)>) {
    for (sensor, mut tf) in &mut q {
        match &sensor.mount {
            Mount::VehicleNose {
                vehicle,
                offset_m,
                look_down,
                extra_yaw,
            } => {
                let Some(st) = fleet.0.get(vehicle) else {
                    continue;
                };
                let fwd_enu = st.look_dir_enu_yawed(*look_down, *extra_yaw);
                let fwd = enu::to_engine(fwd_enu);
                let pos = enu::to_engine(st.position) + fwd * *offset_m;
                *tf = Transform::from_translation(pos)
                    .looking_at(pos + fwd * 50.0, up_for(fwd));
            }
            Mount::Fixed { pos, look } => {
                let p = enu::to_engine(*pos);
                let l = enu::to_engine(*look);
                *tf = Transform::from_translation(p).looking_at(l, up_for(l - p));
            }
            Mount::Chase {
                vehicle,
                back_m,
                up_m,
            } => {
                let Some(st) = fleet.0.get(vehicle) else {
                    continue;
                };
                let heading = Vec3::new(st.yaw.cos(), st.yaw.sin(), 0.0);
                let cam_enu = st.position - heading * *back_m + Vec3::new(0.0, 0.0, *up_m);
                let p = enu::to_engine(cam_enu);
                let l = enu::to_engine(st.position);
                *tf = Transform::from_translation(p).looking_at(l, up_for(l - p));
            }
        }
    }
}

// ---------------------------------------------------------------- IPC dispatch

fn ok() -> String {
    "{\"ok\":true}".to_string()
}

fn err(msg: &str) -> String {
    serde_json::json!({"ok": false, "error": msg}).to_string()
}

fn arr3(v: Option<&serde_json::Value>, default: [f32; 3]) -> [f32; 3] {
    let mut out = default;
    if let Some(a) = v.and_then(|v| v.as_array()) {
        for i in 0..3.min(a.len()) {
            out[i] = a[i].as_f64().unwrap_or(default[i] as f64) as f32;
        }
    }
    out
}

fn arr2(v: Option<&serde_json::Value>, default: [f32; 2]) -> [f32; 2] {
    let mut out = default;
    if let Some(a) = v.and_then(|v| v.as_array()) {
        for i in 0..2.min(a.len()) {
            out[i] = a[i].as_f64().unwrap_or(default[i] as f64) as f32;
        }
    }
    out
}

fn f32_of(v: Option<&serde_json::Value>, default: f32) -> f32 {
    v.and_then(|v| v.as_f64()).map(|f| f as f32).unwrap_or(default)
}

fn detections_json(rows: &[scenario::DetectionRow]) -> serde_json::Value {
    serde_json::Value::Array(
        rows.iter()
            .map(|r| {
                let mut o = serde_json::json!({
                    "label": r.label,
                    "confidence": r.confidence,
                    "nx": r.nx,
                    "ny": r.ny,
                    "world": r.world,
                });
                if let Some(t) = r.top_z {
                    o["top_z"] = serde_json::json!(t);
                }
                if let Some(p) = &r.peer_id {
                    o["peer_id"] = serde_json::json!(p);
                }
                if let Some(a) = r.peer_alt {
                    o["peer_alt"] = serde_json::json!(a);
                }
                o
            })
            .collect(),
    )
}

#[allow(clippy::too_many_arguments)]
fn dispatch(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut images: ResMut<Assets<Image>>,
    ipc: Res<Ipc>,
    mut fleet: ResMut<FleetRes>,
    mut world: ResMut<World>,
    mut vantages: ResMut<Vantages>,
    mut captures: ResMut<sensors::rgb::Captures>,
    mut exit: MessageWriter<AppExit>,
    sensors_q: Query<(Entity, &Sensor)>,
    mut layers_q: Query<&mut RenderLayers, With<Sensor>>,
) {
    while let Ok(req) = ipc.0.rx.try_recv() {
        let b = &req.body;
        let op = b.get("op").and_then(|v| v.as_str()).unwrap_or("");
        let did = b.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();

        let resp: Option<String> = match op {
            // ---------------------------------------------------- environment
            "load_env" => {
                let want = b.get("env").and_then(|v| v.as_str()).unwrap_or("office");
                // Swapping the world at runtime would mean tearing down 45k
                // entities and every aircraft mid-flight. The harnesses all
                // pass --env on the command line instead, so this only has to
                // answer honestly about what is loaded.
                if want == "manhattan" {
                    Some(serde_json::json!({"ok": true, "env": want}).to_string())
                } else {
                    Some(err(&format!(
                        "load_env: only 'manhattan' is available in this backend (asked for '{want}')"
                    )))
                }
            }
            "fw_env_state" => Some(
                serde_json::json!({"ok": true, "env": world.0.env_state()}).to_string(),
            ),
            "fw_prop_truth" => Some(
                serde_json::json!({"ok": true, "props": fleet.0.prop_truth(world.0.as_ref())})
                    .to_string(),
            ),

            // -------------------------------------------------------- vantages
            "add_vantage" => {
                let name = b
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("v")
                    .to_string();
                if vantages.0.contains_key(&name) {
                    Some(ok()) // Godot no-ops on a duplicate name.
                } else {
                    let p = arr3(b.get("p"), [0.0, 0.0, 10.0]);
                    let look = arr3(b.get("look"), [0.0, 0.0, 0.0]);
                    let w = b.get("w").and_then(|v| v.as_u64()).unwrap_or(1280) as u32;
                    let h = b.get("h").and_then(|v| v.as_u64()).unwrap_or(720) as u32;
                    let e = spawn_vantage(
                        &mut commands,
                        &mut images,
                        &name,
                        Vec3::from(p),
                        Vec3::from(look),
                        w,
                        h,
                        &fleet.0.airframe_layers(),
                    );
                    vantages.0.insert(name.clone(), e);
                    println!("[FleetManager] vantage '{name}' at ENU {p:?} -> {look:?}");
                    Some(ok())
                }
            }
            "auto_overhead" => {
                let name = b
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("overhead")
                    .to_string();
                if !vantages.0.contains_key(&name) {
                    // Centroid of the fleet, pulled straight up. Matches the
                    // fallback branch of Godot's auto_overhead for a non-city
                    // env; the aircraft here are hundreds of metres apart, so
                    // the height is scaled to the spread rather than fixed at
                    // 60 m, which framed a whole scenario as a single dot.
                    let (centre, spread) = fleet_extent(&fleet.0);
                    let alt = (spread * 1.2).max(200.0);
                    let e = spawn_vantage(
                        &mut commands,
                        &mut images,
                        &name,
                        Vec3::new(centre.x, centre.y, alt),
                        Vec3::new(centre.x, centre.y, 0.0),
                        1280,
                        720,
                        &fleet.0.airframe_layers(),
                    );
                    vantages.0.insert(name.clone(), e);
                }
                Some(serde_json::json!({"ok": true, "name": name}).to_string())
            }
            "move_vantage" => {
                let name = b.get("name").and_then(|v| v.as_str()).unwrap_or("overhead");
                let p = arr3(b.get("p"), [0.0, 0.0, 10.0]);
                let look = arr3(b.get("look"), [0.0, 0.0, 0.0]);
                // Repositions an EXISTING view in place rather than
                // recreating its render target — for a chase cam that tracks a
                // moving aircraft every tick.
                if let Some(&e) = vantages.0.get(name) {
                    if let Ok((_, sensor)) = sensors_q.get(e) {
                        let mut sensor = sensor.clone();
                        sensor.mount = Mount::Fixed {
                            pos: Vec3::from(p),
                            look: Vec3::from(look),
                        };
                        commands.entity(e).insert(sensor);
                    }
                }
                Some(ok())
            }
            "remove_vantage" => {
                let name = b.get("name").and_then(|v| v.as_str()).unwrap_or("overhead");
                if let Some(e) = vantages.0.remove(name) {
                    commands.entity(e).despawn();
                }
                Some(ok())
            }
            // Additive op, not in the Godot protocol: a view that tracks an
            // aircraft instead of standing still. Godot did this by having the
            // client call move_vantage every tick, which puts the camera a
            // round trip behind the aircraft it is filming. Here it is a mount,
            // resolved in the same frame as the pose it follows. Grabbed with
            // grab_vantage like any other view.
            "add_chase" => {
                let name = b
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("chase")
                    .to_string();
                if vantages.0.contains_key(&name) {
                    Some(ok())
                } else if fleet.0.get(&did).is_none() {
                    Some(err(&format!("unknown fw drone {did}")))
                } else {
                    let w = b.get("w").and_then(|v| v.as_u64()).unwrap_or(1280) as u32;
                    let h = b.get("h").and_then(|v| v.as_u64()).unwrap_or(720) as u32;
                    let target = sensors::make_target(&mut images, w, h, Modality::Rgb);
                    let sensor = Sensor {
                        modality: Modality::Rgb,
                        owner: String::new(),
                        name: name.clone(),
                        target,
                        width: w,
                        height: h,
                        fov: 60.0f32.to_radians(),
                        mount: Mount::Chase {
                            vehicle: did.clone(),
                            back_m: f32_of(b.get("back"), 40.0),
                            up_m: f32_of(b.get("up"), 12.0),
                        },
                    };
                    let layers = sensors::observer_layers(&fleet.0.airframe_layers());
                    let e = sensors::spawn_sensor(&mut commands, sensor, layers, -10);
                    vantages.0.insert(name.clone(), e);
                    Some(serde_json::json!({"ok": true, "name": name}).to_string())
                }
            }
            "grab_vantage" => {
                let name = b.get("name").and_then(|v| v.as_str()).unwrap_or("overhead");
                match vantages
                    .0
                    .get(name)
                    .and_then(|e| sensors_q.get(*e).ok())
                    .map(|(e, s)| (e, s.target.clone()))
                {
                    Some((e, target)) => {
                        sensors::rgb::request(&mut captures, e, target, req.reply.clone(), "jpg");
                        None // replied by the capture observer
                    }
                    None => Some("{\"ok\":true,\"jpg\":null}".to_string()),
                }
            }

            // ------------------------------------------------------ fixed-wing
            "fw_spawn" => {
                let p = arr3(b.get("p"), [0.0, 0.0, 0.0]);
                let yaw = f32_of(b.get("yaw"), 0.0);
                spawn_fixedwing(
                    &mut commands,
                    &mut meshes,
                    &mut materials,
                    &mut images,
                    &mut fleet.0,
                    &did,
                    Vec3::from(p),
                    yaw,
                );
                rebuild_view_layers(&mut layers_q, &sensors_q, &fleet.0);
                Some(ok())
            }
            "fw_despawn" => {
                if let Some(i) = fleet.0.index_of(&did) {
                    let st = fleet.0.fw.remove(i);
                    if let Some(e) = st.body {
                        commands.entity(e).despawn();
                    }
                    for e in st.sensors {
                        commands.entity(e).despawn();
                    }
                    rebuild_view_layers(&mut layers_q, &sensors_q, &fleet.0);
                }
                Some(ok())
            }
            "fw_state" => match fleet.0.get(&did) {
                Some(st) => {
                    let mut o = st.state_json();
                    o["ok"] = serde_json::json!(true);
                    Some(o.to_string())
                }
                None => Some(err(&format!("unknown fw drone {did}"))),
            },
            "fw_all_states" => Some(
                serde_json::json!({"ok": true, "states": fleet.0.all_states()}).to_string(),
            ),
            "fw_detect" => {
                let rows = fleet.0.detect(world.0.as_ref(), &did);
                Some(
                    serde_json::json!({"ok": true, "objects": detections_json(&rows)}).to_string(),
                )
            }
            "fw_unproject" => {
                let nx = f32_of(b.get("nx"), 0.5);
                let ny = f32_of(b.get("ny"), 0.5);
                let w = fleet.0.unproject(world.0.as_ref(), &did, nx, ny);
                Some(serde_json::json!({"ok": true, "world": w}).to_string())
            }
            // NOTE the nesting: fw_grid answers {"ok", "grid": {...}}, while
            // the rover's phrover_grid answers the same fields FLAT. That
            // asymmetry is in the Godot protocol and `depot_client.fw_grid`
            // depends on it (`return r.get("grid", {})`), so flattening it here
            // made the client hand back an empty dict and fw_eval's coverage
            // metric silently compute nothing. Caught by the parity check.
            "fw_grid" => match fleet.0.grid_json(&did) {
                Some(g) => Some(serde_json::json!({"ok": true, "grid": g}).to_string()),
                None => Some(err(&format!("unknown fw drone {did}"))),
            },
            "fw_drive" => {
                fleet.0.drive(
                    &did,
                    f32_of(b.get("airspeed"), 0.0),
                    f32_of(b.get("yaw_rate"), 0.0),
                    // Defaults to 0 (hold altitude) so pre-existing clients
                    // that never send the field keep their exact behaviour.
                    f32_of(b.get("climb"), 0.0),
                );
                Some(ok())
            }
            "fw_stop" => {
                fleet.0.stop(&did);
                Some(ok())
            }
            "fw_set_sensor" => {
                // Either an explicit offset off the nose, or "aim at this ENU
                // point", which is what an orbit uses to keep its centre in
                // frame.
                if b.get("at").is_some() {
                    let at = arr2(b.get("at"), [0.0, 0.0]);
                    fleet.0.aim_sensor_at(&did, at[0], at[1]);
                } else {
                    fleet
                        .0
                        .set_sensor_yaw_offset(&did, f32_of(b.get("offset"), 0.0));
                }
                Some(ok())
            }
            "fw_grab_frame" => {
                match fleet
                    .0
                    .get(&did)
                    .and_then(|st| st.sensors.first().copied())
                    .and_then(|e| sensors_q.get(e).ok())
                    .map(|(e, s)| (e, s.target.clone()))
                {
                    Some((e, target)) => {
                        sensors::rgb::request(&mut captures, e, target, req.reply.clone(), "jpg");
                        None
                    }
                    None => Some("{\"ok\":true,\"jpg\":null}".to_string()),
                }
            }
            // Additive ops: mount an extra sensor on a vehicle, and read it.
            // The Godot build has exactly one camera per aircraft and no way to
            // add a second, so a multi-sensor airframe (port/starboard/aft, or
            // an RGB and a range channel on the same mast) is not expressible
            // there at all. Here it is the same component with a different
            // mount.
            "add_sensor" => {
                let name = b
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("aux")
                    .to_string();
                if fleet.0.get(&did).is_none() {
                    Some(err(&format!("unknown fw drone {did}")))
                } else if sensors_q
                    .iter()
                    .any(|(_, sn)| sn.owner == did && sn.name == name)
                {
                    Some(ok()) // idempotent, like add_vantage
                } else {
                    let w = b.get("w").and_then(|v| v.as_u64()).unwrap_or(640) as u32;
                    let h = b.get("h").and_then(|v| v.as_u64()).unwrap_or(480) as u32;
                    let target = sensors::make_target(&mut images, w, h, Modality::Rgb);
                    let sensor = Sensor {
                        modality: Modality::Rgb,
                        owner: did.clone(),
                        name: name.clone(),
                        target,
                        width: w,
                        height: h,
                        fov: f32_of(b.get("fov_deg"), fixedwing::CAM_FOV_DEG).to_radians(),
                        mount: Mount::VehicleNose {
                            vehicle: did.clone(),
                            offset_m: f32_of(b.get("offset_m"), fixedwing::NOSE_OFFSET_M),
                            look_down: f32_of(b.get("look_down"), fixedwing::DETECT_LOOK_DOWN),
                            extra_yaw: f32_of(b.get("yaw"), 0.0),
                        },
                    };
                    let layer = fleet.0.get(&did).map(|st| st.layer).unwrap_or(1);
                    let layers = sensors::onboard_layers(layer, &fleet.0.airframe_layers());
                    let n = sensors_q.iter().count() as isize;
                    let e = sensors::spawn_sensor(&mut commands, sensor, layers, -20 - n);
                    if let Some(st) = fleet.0.get_mut(&did) {
                        st.sensors.push(e);
                    }
                    Some(serde_json::json!({"ok": true, "name": name}).to_string())
                }
            }
            "grab_sensor" => {
                let name = b.get("name").and_then(|v| v.as_str()).unwrap_or("onboard");
                match sensors_q
                    .iter()
                    .find(|(_, sn)| sn.owner == did && sn.name == name)
                    .map(|(e, sn)| (e, sn.target.clone()))
                {
                    Some((e, target)) => {
                        sensors::rgb::request(&mut captures, e, target, req.reply.clone(), "jpg");
                        None
                    }
                    None => Some("{\"ok\":true,\"jpg\":null}".to_string()),
                }
            }
            // Additive op: the second sensor modality, through the same mount
            // as the RGB camera and registered with it pixel-for-pixel. Proves
            // the sensor seam is real rather than asserted. See sensors::depth
            // for why it is analytic rather than a GPU prepass.
            "fw_grab_depth" => {
                let w = b.get("w").and_then(|v| v.as_u64()).unwrap_or(160) as u32;
                let h = b.get("h").and_then(|v| v.as_u64()).unwrap_or(120) as u32;
                let max_range = f32_of(b.get("max_range"), fixedwing::detect_range("wall"));
                match sensors::depth::render(
                    &fleet.0,
                    world.0.as_ref(),
                    &did,
                    w.clamp(8, 1024),
                    h.clamp(8, 1024),
                    fixedwing::CAM_FOV_DEG.to_radians(),
                    fixedwing::DETECT_LOOK_DOWN,
                    fixedwing::NOSE_OFFSET_M,
                    max_range,
                ) {
                    Some(img) => {
                        let png = sensors::depth::to_png_base64(&img);
                        Some(
                            serde_json::json!({
                                "ok": true,
                                "png": png,
                                "w": img.width,
                                "h": img.height,
                                "scale": sensors::depth::DEPTH_SCALE,
                                "min_m": img.min_m,
                                "max_m": img.max_m,
                                "hit_fraction": img.hit_fraction,
                            })
                            .to_string(),
                        )
                    }
                    None => Some(err(&format!("unknown fw drone {did}"))),
                }
            }
            "fw_reset_camera" => {
                // Godot needed this because a SubViewport's render target could
                // freeze permanently once another GPU client started heavy work
                // — grab_frame kept returning identical bytes forever while
                // detect() carried on reporting the truth, which reads as a
                // blind model rather than a stale camera. Nothing here holds a
                // persistent render target open across frames, so this is a
                // no-op kept for protocol compatibility.
                Some(ok())
            }
            "fw_drop" => {
                let rel = fleet.0.drop_payload(&did);
                Some(
                    serde_json::json!({"ok": rel.is_some(), "release": rel}).to_string(),
                )
            }
            "fw_events" => {
                Some(serde_json::json!({"ok": true, "events": fleet.0.events_for(&did)}).to_string())
            }
            "fw_log_event" => {
                let kind = b.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                let mut data = b
                    .get("data")
                    .cloned()
                    .unwrap_or_else(|| serde_json::json!({}));
                data["id"] = serde_json::json!(did);
                fleet.0.log_event(kind, data);
                Some(ok())
            }
            "fw_reset" => {
                fleet.0.reset(&did);
                Some(ok())
            }
            "fw_inject" => {
                let name = b.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let params = b
                    .get("params")
                    .cloned()
                    .unwrap_or_else(|| serde_json::json!({}));
                apply_inject(
                    &mut commands,
                    &mut fleet.0,
                    world.0.as_mut(),
                    name,
                    &params,
                );
                fleet
                    .0
                    .log_event("inject_fired", serde_json::json!({"name": name, "params": params}));
                Some(ok())
            }

            "quit" => {
                exit.write(AppExit::Success);
                Some(ok())
            }
            other => Some(err(&format!("unknown op {other}"))),
        };

        if let Some(resp) = resp {
            let _ = req.reply.send(resp);
        }
    }
}

fn fleet_extent(fleet: &Fleet) -> (Vec2, f32) {
    if fleet.fw.is_empty() {
        return (Vec2::ZERO, 0.0);
    }
    let mut centre = Vec2::ZERO;
    for st in &fleet.fw {
        centre += Vec2::new(st.position.x, st.position.y);
    }
    centre /= fleet.fw.len() as f32;
    let spread = fleet
        .fw
        .iter()
        .map(|st| Vec2::new(st.position.x, st.position.y).distance(centre))
        .fold(0.0f32, f32::max);
    (centre, spread)
}

#[allow(clippy::too_many_arguments)]
fn spawn_vantage(
    commands: &mut Commands,
    images: &mut Assets<Image>,
    name: &str,
    pos: Vec3,
    look: Vec3,
    w: u32,
    h: u32,
    airframe_layers: &[usize],
) -> Entity {
    let target = sensors::make_target(images, w, h, Modality::Rgb);
    let sensor = Sensor {
        modality: Modality::Rgb,
        owner: String::new(),
        name: name.to_string(),
        target,
        width: w,
        height: h,
        fov: 60.0f32.to_radians(),
        mount: Mount::Fixed { pos, look },
    };
    // An observer view sees every airframe, unlike an onboard camera.
    let layers = sensors::observer_layers(airframe_layers);
    sensors::spawn_sensor(commands, sensor, layers, -10)
}

#[allow(clippy::too_many_arguments)]
fn spawn_fixedwing(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    images: &mut Assets<Image>,
    fleet: &mut Fleet,
    id: &str,
    pos: Vec3,
    yaw: f32,
) {
    // Re-spawning an id that is still registered must actually MOVE the
    // aircraft back to pos/yaw. A no-op here let it silently keep whatever
    // position it had drifted to, so a 3-attempt "identical scenario" loop was
    // secretly three different scenarios with progressively worse geometry.
    if let Some(st) = fleet.get_mut(id) {
        st.position = pos;
        st.yaw = yaw;
        st.velocity = Vec3::ZERO;
        st.airspeed = fixedwing::MIN_AIRSPEED;
        st.climb_rate = 0.0;
        st.cmd_climb_rate = 0.0;
        st.pitch = 0.0;
        st.roll = 0.0;
        return;
    }

    let layer = sensors::airframe_layer(fleet.spawn_counter);
    fleet.spawn_counter += 1;
    let cells = fleet.cells();
    let mut st = FixedWingState::new(id.to_string(), pos, yaw, cells, layer);
    let body = scene::spawn_airframe(commands, meshes, materials, id, layer);
    st.body = Some(body);

    // The onboard forward camera. Mounted a nose length ahead of the airframe
    // origin and pitched down, exactly as the Godot build's `_sync_camera`.
    let target = sensors::make_target(
        images,
        fixedwing::CAM_VIEWPORT_W,
        fixedwing::CAM_VIEWPORT_H,
        Modality::Rgb,
    );
    let sensor = Sensor {
        modality: Modality::Rgb,
        owner: id.to_string(),
        name: "onboard".to_string(),
        target,
        width: fixedwing::CAM_VIEWPORT_W,
        height: fixedwing::CAM_VIEWPORT_H,
        fov: fixedwing::CAM_FOV_DEG.to_radians(),
        mount: Mount::VehicleNose {
            vehicle: id.to_string(),
            offset_m: fixedwing::NOSE_OFFSET_M,
            look_down: fixedwing::DETECT_LOOK_DOWN,
            extra_yaw: 0.0,
        },
    };
    let mut all_layers = fleet.airframe_layers();
    all_layers.push(layer);
    let layers = sensors::onboard_layers(layer, &all_layers);
    let order = -20 - (fleet.fw.len() as isize);
    let cam = sensors::spawn_sensor(commands, sensor, layers, order);
    st.sensors.push(cam);

    fleet.fw.push(st);
    println!("[FixedWingManager] spawned {id} at ENU {pos:?}");
}

/// Point every view at the right set of airframe layers: an onboard camera sees
/// the world plus every OTHER aircraft, an observer view sees them all.
fn rebuild_view_layers(
    layers_q: &mut Query<&mut RenderLayers, With<Sensor>>,
    sensors_q: &Query<(Entity, &Sensor)>,
    fleet: &Fleet,
) {
    let all = fleet.airframe_layers();
    for (e, sensor) in sensors_q.iter() {
        let Ok(mut layers) = layers_q.get_mut(e) else {
            continue;
        };
        *layers = match fleet.get(&sensor.owner) {
            Some(st) => sensors::onboard_layers(st.layer, &all),
            // A free-standing observer view, or an onboard camera whose
            // aircraft is gone: show everything.
            None => sensors::observer_layers(&all),
        };
    }
}

fn apply_inject(
    commands: &mut Commands,
    fleet: &mut Fleet,
    world: &mut dyn Environment,
    name: &str,
    params: &serde_json::Value,
) {
    match name {
        "raise_wall" => {
            // A real obstacle appears mid-flight, not just a no-fly zone in the
            // fleet DSL. center/half are ENU (east, north) metres, the same
            // convention prop_truth and detect already use.
            let c = arr2(params.get("center"), [0.0, 0.0]);
            let half = arr2(params.get("half"), [30.0, 30.0]);
            let height = f32_of(params.get("height"), 60.0);
            world.add_wall(
                geom::Rect2::new(c[0] - half[0], c[1] - half[1], half[0] * 2.0, half[1] * 2.0),
                height,
            );
            fleet.rebuild_occ_grid(world);
        }
        "battery_drain" => {
            let rate = f32_of(params.get("rate"), 1.0);
            let target = params.get("id").and_then(|v| v.as_str()).unwrap_or("");
            for st in &mut fleet.fw {
                if target.is_empty() || st.id == target {
                    st.drain_mult = rate;
                }
            }
        }
        "kill_fixedwing" => {
            let rid = params
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if let Some(i) = fleet.index_of(&rid) {
                let st = fleet.fw.remove(i);
                if let Some(e) = st.body {
                    commands.entity(e).despawn();
                }
                for e in st.sensors {
                    commands.entity(e).despawn();
                }
            }
        }
        // block_door / open_door / tip_ladder / sar_config belong to the depot
        // and SAR envs and have no meaning in a city. Godot's match fell
        // through silently for an env that lacked them; so does this.
        _ => {}
    }
}
