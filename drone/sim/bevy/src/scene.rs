//! Scene construction: sky, light, ground slabs, and the city itself.
//!
//! Colours and light angles are matched to the Godot build rather than chosen
//! fresh. That is deliberate: the perception gate's measured recognition rate
//! is only evidence about the scene it was actually shot in, so the two
//! backends have to render the same world before any capability number
//! measured on one can be compared with the other.

use crate::airframe;
use crate::env::manhattan::{Manhattan, Y_LAND, Y_PARK, Y_WATER};
use crate::sensors::WORLD_LAYER;
use bevy::asset::RenderAssetUsages;
use bevy::camera::visibility::RenderLayers;
use bevy::light::{CascadeShadowConfigBuilder, GlobalAmbientLight};
use bevy::mesh::{Indices, PrimitiveTopology};
use bevy::prelude::*;
use rand::{Rng, SeedableRng};
use rand_chacha::ChaCha8Rng;

/// Marks the spinning propeller disc so one system can animate every airframe.
#[derive(Component)]
pub struct PropDisc;

/// Marks an airframe root so its transform can be driven from flight state.
#[derive(Component)]
pub struct Airframe {
    pub vehicle: String,
}

/// Sun and ambient, from `main.tscn`'s WorldEnvironment plus its
/// DirectionalLight transform.
pub fn spawn_sky(commands: &mut Commands, ambient: &mut GlobalAmbientLight) {
    // Godot: ambient_light_color (0.4, 0.4, 0.5) at energy 0.5 from the sky.
    ambient.color = Color::srgb(0.4, 0.4, 0.5);
    ambient.brightness = 1200.0;

    // Lower sun: long shadows model the terrain and, more usefully, throw a
    // figure's shadow clear of the figure so it is legible from height.
    commands.spawn((
        DirectionalLight {
            color: Color::srgb(1.0, 0.97, 0.90),
            illuminance: 11_000.0,
            shadow_maps_enabled: true,
            ..default()
        },
        Transform::from_rotation(Quat::from_euler(
            EulerRot::YXZ,
            (-42.0f32).to_radians(),
            (-34.0f32).to_radians(),
            0.0,
        )),
        // Cascades sized for a city rather than a courtyard. Godot's
        // directional_shadow_max_distance was 400 m for the small envs; the
        // near cascades still carry the detail that matters on a low run-in.
        CascadeShadowConfigBuilder {
            num_cascades: 4,
            minimum_distance: 1.0,
            maximum_distance: 2_000.0,
            first_cascade_far_bound: 120.0,
            overlap_proportion: 0.2,
        }
        .build(),
        RenderLayers::layer(WORLD_LAYER),
    ));
}

/// One flat quad in the east/north plane at height `y`, in ENU, appended to a
/// growing triangle soup. Mirrors `_land_quad`.
fn push_quad(
    positions: &mut Vec<[f32; 3]>,
    normals: &mut Vec<[f32; 3]>,
    uvs: &mut Vec<[f32; 2]>,
    e0: f32,
    e1: f32,
    n0: f32,
    n1: f32,
    y: f32,
) {
    // ENU (east, north) -> render (east, up, -north).
    let a = [e0, y, -n0];
    let b = [e1, y, -n0];
    let c = [e1, y, -n1];
    let d = [e0, y, -n1];
    for v in [a, b, c, a, c, d] {
        positions.push(v);
        normals.push([0.0, 1.0, 0.0]);
        uvs.push([0.0, 0.0]);
    }
}

fn soup_to_mesh(
    positions: Vec<[f32; 3]>,
    normals: Vec<[f32; 3]>,
    uvs: Vec<[f32; 2]>,
) -> Mesh {
    let n = positions.len() as u32;
    let mut mesh = Mesh::new(PrimitiveTopology::TriangleList, RenderAssetUsages::default());
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
    mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, normals);
    mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, uvs);
    mesh.insert_indices(Indices::U32((0..n).collect()));
    mesh
}

/// Water, land and Central Park, then the city.
pub fn spawn_manhattan(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    city: &Manhattan,
    seed: u64,
) {
    let world = RenderLayers::layer(WORLD_LAYER);

    // ---- water ----
    // Water is the one surface here that should catch the sun, so unlike the
    // asphalt it gets a low roughness and some metallic for a specular sheen.
    let (centre, size) = city.water_extent();
    let mut p = Vec::new();
    let mut n = Vec::new();
    let mut u = Vec::new();
    push_quad(
        &mut p,
        &mut n,
        &mut u,
        centre.x - size.x * 0.5,
        centre.x + size.x * 0.5,
        centre.y - size.y * 0.5,
        centre.y + size.y * 0.5,
        Y_WATER,
    );
    commands.spawn((
        Mesh3d(meshes.add(soup_to_mesh(p, n, u))),
        MeshMaterial3d(materials.add(StandardMaterial {
            base_color: Color::srgb(0.05, 0.13, 0.30),
            perceptual_roughness: 0.22,
            metallic: 0.45,
            ..default()
        })),
        world.clone(),
        Name::new("water"),
    ));

    // ---- land ----
    // Asphalt, but lighter than a literal reading would suggest: too dark and
    // it reads as a black void next to the water, making the shoreline look
    // like a hole rather than a bank. Streets from the air are lighter than
    // deep water.
    let mut p = Vec::new();
    let mut n = Vec::new();
    let mut u = Vec::new();
    for (e0, e1, n0) in &city.land_bands {
        // Bands are emitted across their FULL north extent so neighbours share
        // an edge exactly and the seam between them cannot leak water through.
        push_quad(&mut p, &mut n, &mut u, *e0, *e1, *n0, *n0 + 80.0, Y_LAND);
    }
    if !p.is_empty() {
        commands.spawn((
            Mesh3d(meshes.add(soup_to_mesh(p, n, u))),
            MeshMaterial3d(materials.add(StandardMaterial {
                base_color: Color::srgb(0.30, 0.30, 0.29),
                perceptual_roughness: 1.0,
                double_sided: true,
                cull_mode: None,
                ..default()
            })),
            world.clone(),
            Name::new("land"),
        ));
        println!(
            "[env_manhattan] land: {} bands of 80 m north",
            city.land_bands.len()
        );
    }

    // ---- Central Park ----
    // Brighter and more saturated than a "correct" park green needs to be:
    // aerial haze is blue and desaturating, so a dark green came out blue-grey
    // at 2 km and read as water rather than grass. Colour here has to survive
    // the distance, not match a paint chip.
    let r = city.park_rect();
    let mut p = Vec::new();
    let mut n = Vec::new();
    let mut u = Vec::new();
    push_quad(
        &mut p,
        &mut n,
        &mut u,
        r.pos.x,
        r.pos.x + r.size.x,
        r.pos.y,
        r.pos.y + r.size.y,
        Y_PARK,
    );
    commands.spawn((
        Mesh3d(meshes.add(soup_to_mesh(p, n, u))),
        MeshMaterial3d(materials.add(StandardMaterial {
            base_color: Color::srgb(0.30, 0.60, 0.24),
            perceptual_roughness: 1.0,
            double_sided: true,
            cull_mode: None,
            ..default()
        })),
        world.clone(),
        Name::new("central_park"),
    ));
    println!(
        "[env_manhattan] central park: {:.0} x {:.0} m at east {:.0}, north {:.0}",
        r.size.x, r.size.y, r.pos.x, r.pos.y
    );

    // ---- the city ----
    //
    // One unit cube mesh scaled per building. Godot drew all 45k as a single
    // MultiMesh with a per-instance colour tint over a concrete-grey base
    // albedo. Bevy batches identical mesh+material pairs automatically, so the
    // tint is approximated with a small palette of materials instead of a
    // per-instance vertex colour: the facades still vary, and the city stays a
    // handful of draw batches rather than 45k.
    let cube = meshes.add(Cuboid::new(1.0, 1.0, 1.0));
    const TINTS: usize = 8;
    let mut rng = ChaCha8Rng::seed_from_u64(seed ^ 7);
    let palette: Vec<Handle<StandardMaterial>> = (0..TINTS)
        .map(|i| {
            // A tint around 1.0 multiplying the base concrete grey, as the
            // MultiMesh instance colours did: values near 1 vary the facades
            // without deciding how dark the city is.
            let g = 0.80 + (i as f32 / (TINTS - 1) as f32) * 0.32;
            materials.add(StandardMaterial {
                base_color: Color::srgb(0.52 * g, 0.53 * g, 0.55 * g),
                perceptual_roughness: 0.92,
                ..default()
            })
        })
        .collect();

    let rects = city.rects();
    let heights = city.heights();
    let mut batch: Vec<(Mesh3d, MeshMaterial3d<StandardMaterial>, Transform, RenderLayers)> =
        Vec::with_capacity(rects.len());
    for i in 0..rects.len() {
        let r = rects[i];
        let h = heights[i];
        let cx = r.pos.x + r.size.x * 0.5;
        let cy = r.pos.y + r.size.y * 0.5;
        let tint = rng.gen_range(0..TINTS);
        batch.push((
            Mesh3d(cube.clone()),
            MeshMaterial3d(palette[tint].clone()),
            Transform {
                // ENU (east, north) centre, half-height up; north negated.
                translation: Vec3::new(cx, h * 0.5, -cy),
                scale: Vec3::new(r.size.x, h, r.size.y),
                ..default()
            },
            world.clone(),
        ));
    }
    commands.spawn_batch(batch);
}

/// Props (the target truck, the decoy car, settled payloads) as boxes.
pub fn spawn_prop(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    prop: &crate::scenario::Prop,
) -> Entity {
    let world = RenderLayers::layer(WORLD_LAYER);
    let material = materials.add(StandardMaterial {
        base_color: Color::srgb(prop.color[0], prop.color[1], prop.color[2]),
        perceptual_roughness: 0.7,
        metallic: 0.2,
        ..default()
    });
    let e = commands
        .spawn((
            Mesh3d(meshes.add(Cuboid::new(prop.size.x, prop.size.y, prop.size.z))),
            MeshMaterial3d(material.clone()),
            Transform::from_translation(crate::enu::to_engine(prop.pos)),
            world.clone(),
            Name::new(prop.name.clone()),
        ))
        .id();
    if let Some((cab_size, cab_offset)) = prop.cab {
        // Child box, positioned in the parent's local frame exactly as the
        // GDScript's `_box_mesh(cab, Vector3(...))` did.
        let child = commands
            .spawn((
                Mesh3d(meshes.add(Cuboid::new(cab_size.x, cab_size.y, cab_size.z))),
                MeshMaterial3d(materials.add(StandardMaterial {
                    base_color: Color::srgb(
                        prop.color[0] * 0.9,
                        prop.color[1] * 0.9,
                        prop.color[2] * 0.9,
                    ),
                    perceptual_roughness: 0.7,
                    metallic: 0.2,
                    ..default()
                })),
                Transform::from_xyz(cab_offset.x, cab_offset.y, cab_offset.z),
                world,
            ))
            .id();
        commands.entity(e).add_child(child);
    }
    e
}

/// Build one aircraft's visual model. Each airframe lives on its own render
/// layer so its own onboard camera can exclude it while every other view still
/// sees it.
pub fn spawn_airframe(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    vehicle: &str,
    layer: usize,
) -> Entity {
    let layers = RenderLayers::layer(layer);
    let root = commands
        .spawn((
            Transform::default(),
            Visibility::default(),
            Airframe {
                vehicle: vehicle.to_string(),
            },
            Name::new(format!("fw_{vehicle}")),
        ))
        .id();
    for part in airframe::PARTS.iter() {
        let mesh = meshes.add(airframe::part_mesh(part));
        let material = materials.add(StandardMaterial {
            base_color: Color::srgb(part.color[0], part.color[1], part.color[2]),
            metallic: 0.2,
            perceptual_roughness: 0.5,
            // A little emission so the airframe keeps its colour in shadow and
            // against a bright sky: the aircraft is what the eye is meant to
            // follow, and a flat grey model over green ground at sixty metres
            // reads as a smudge.
            emissive: LinearRgba::rgb(
                part.color[0] * 0.12,
                part.color[1] * 0.12,
                part.color[2] * 0.12,
            ),
            ..default()
        });
        let mut child = commands.spawn((
            Mesh3d(mesh),
            MeshMaterial3d(material),
            Transform::default(),
            layers.clone(),
            Name::new(part.name),
        ));
        if part.name == airframe::PROP_PART {
            child.insert(PropDisc);
        }
        let child = child.id();
        commands.entity(root).add_child(child);
    }
    root
}

/// Spin every propeller disc. Purely visual.
pub fn spin_props(time: Res<Time>, mut q: Query<&mut Transform, With<PropDisc>>) {
    let d = airframe::PROP_SPEED * time.delta_secs();
    for mut t in &mut q {
        t.rotate_local_z(d);
    }
}
