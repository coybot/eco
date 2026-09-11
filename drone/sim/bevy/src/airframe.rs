//! Fixed-wing (delta-wing) visual model.
//!
//! Mechanically transcribed from `fixedwing_visuals.gd`, which in turn was
//! ported by hand from the same vertex/face data as the Isaac asset
//! `assets/delta_wing.usda` — one source of geometry for both simulators.
//!
//! Local convention matches the GDScript exactly: +Z forward, +Y up. A
//! body-frame (fwd, left, up) point (x, y, z) maps to local (y, z, x), a pure
//! axis permutation with no mirroring (right-handed: Y x Z = X). The Euler
//! mapping in `fixedwing::apply_pose` is calibrated against this +Z-forward
//! mesh, so the two must change together.
//!
//! Faces are polygons, fanned into triangles from their first vertex, the same
//! way `_build_part` does it.

use bevy::asset::RenderAssetUsages;
use bevy::mesh::{Indices, Mesh, PrimitiveTopology};
use bevy::math::Vec3;

/// Livery. Split so the two surfaces read differently in motion: the fuselage
/// is the roll axis and the wings are what actually show the bank, so
/// contrasting hues make a turn legible from a chase camera at distance.
pub const BODY_COLOR: [f32; 3] = [0.42, 0.10, 0.78];
pub const WING_COLOR: [f32; 3] = [1.00, 0.38, 0.70];
pub const PROP_COLOR: [f32; 3] = [0.15, 0.15, 0.18];

/// Body-frame (fwd, left, up) -> mesh local (x=left, y=up, z=fwd).
#[inline]
fn to_local(p: [f32; 3]) -> Vec3 {
    Vec3::new(p[1], p[2], p[0])
}

pub struct Part {
    pub name: &'static str,
    pub points: &'static [[f32; 3]],
    pub faces: &'static [&'static [u16]],
    pub color: [f32; 3],
}


const FUSELAGE_POINTS: [[f32; 3]; 58] = [
    [1.75, 0.0, 0.01],
    [1.75, 0.0071, 0.0071],
    [1.75, 0.01, 0.0],
    [1.75, 0.0071, -0.0071],
    [1.75, 0.0, -0.01],
    [1.75, -0.0071, -0.0071],
    [1.75, -0.01, -0.0],
    [1.75, -0.0071, 0.0071],
    [1.4, 0.0, 0.08],
    [1.4, 0.0566, 0.0566],
    [1.4, 0.08, 0.0],
    [1.4, 0.0566, -0.0566],
    [1.4, 0.0, -0.08],
    [1.4, -0.0566, -0.0566],
    [1.4, -0.08, -0.0],
    [1.4, -0.0566, 0.0566],
    [0.8, 0.0, 0.15],
    [0.8, 0.1061, 0.1061],
    [0.8, 0.15, 0.0],
    [0.8, 0.1061, -0.1061],
    [0.8, 0.0, -0.15],
    [0.8, -0.1061, -0.1061],
    [0.8, -0.15, -0.0],
    [0.8, -0.1061, 0.1061],
    [0.0, 0.0, 0.16],
    [0.0, 0.1131, 0.1131],
    [0.0, 0.16, 0.0],
    [0.0, 0.1131, -0.1131],
    [0.0, 0.0, -0.16],
    [0.0, -0.1131, -0.1131],
    [0.0, -0.16, -0.0],
    [0.0, -0.1131, 0.1131],
    [-0.8, 0.0, 0.15],
    [-0.8, 0.1061, 0.1061],
    [-0.8, 0.15, 0.0],
    [-0.8, 0.1061, -0.1061],
    [-0.8, 0.0, -0.15],
    [-0.8, -0.1061, -0.1061],
    [-0.8, -0.15, -0.0],
    [-0.8, -0.1061, 0.1061],
    [-1.4, 0.0, 0.1],
    [-1.4, 0.0707, 0.0707],
    [-1.4, 0.1, 0.0],
    [-1.4, 0.0707, -0.0707],
    [-1.4, 0.0, -0.1],
    [-1.4, -0.0707, -0.0707],
    [-1.4, -0.1, -0.0],
    [-1.4, -0.0707, 0.0707],
    [-1.75, 0.0, 0.02],
    [-1.75, 0.0141, 0.0141],
    [-1.75, 0.02, 0.0],
    [-1.75, 0.0141, -0.0141],
    [-1.75, 0.0, -0.02],
    [-1.75, -0.0141, -0.0141],
    [-1.75, -0.02, -0.0],
    [-1.75, -0.0141, 0.0141],
    [1.75, 0.0, 0.0],
    [-1.75, 0.0, 0.0],
];

const FUSELAGE_FACES: [&[u16]; 64] = [
    &[0, 1, 9, 8],
    &[1, 2, 10, 9],
    &[2, 3, 11, 10],
    &[3, 4, 12, 11],
    &[4, 5, 13, 12],
    &[5, 6, 14, 13],
    &[6, 7, 15, 14],
    &[7, 0, 8, 15],
    &[8, 9, 17, 16],
    &[9, 10, 18, 17],
    &[10, 11, 19, 18],
    &[11, 12, 20, 19],
    &[12, 13, 21, 20],
    &[13, 14, 22, 21],
    &[14, 15, 23, 22],
    &[15, 8, 16, 23],
    &[16, 17, 25, 24],
    &[17, 18, 26, 25],
    &[18, 19, 27, 26],
    &[19, 20, 28, 27],
    &[20, 21, 29, 28],
    &[21, 22, 30, 29],
    &[22, 23, 31, 30],
    &[23, 16, 24, 31],
    &[24, 25, 33, 32],
    &[25, 26, 34, 33],
    &[26, 27, 35, 34],
    &[27, 28, 36, 35],
    &[28, 29, 37, 36],
    &[29, 30, 38, 37],
    &[30, 31, 39, 38],
    &[31, 24, 32, 39],
    &[32, 33, 41, 40],
    &[33, 34, 42, 41],
    &[34, 35, 43, 42],
    &[35, 36, 44, 43],
    &[36, 37, 45, 44],
    &[37, 38, 46, 45],
    &[38, 39, 47, 46],
    &[39, 32, 40, 47],
    &[40, 41, 49, 48],
    &[41, 42, 50, 49],
    &[42, 43, 51, 50],
    &[43, 44, 52, 51],
    &[44, 45, 53, 52],
    &[45, 46, 54, 53],
    &[46, 47, 55, 54],
    &[47, 40, 48, 55],
    &[56, 0, 1],
    &[56, 1, 2],
    &[56, 2, 3],
    &[56, 3, 4],
    &[56, 4, 5],
    &[56, 5, 6],
    &[56, 6, 7],
    &[56, 7, 0],
    &[57, 49, 48],
    &[57, 50, 49],
    &[57, 51, 50],
    &[57, 52, 51],
    &[57, 53, 52],
    &[57, 54, 53],
    &[57, 55, 54],
    &[57, 48, 55],
];

const LEFTWING_POINTS: [[f32; 3]; 6] = [
    [0.8, -0.16, 0.02],
    [0.8, -0.16, -0.02],
    [-1.4, -0.16, 0.02],
    [-1.4, -0.16, -0.02],
    [-1.75, -1.25, 0.0075],
    [-1.75, -1.25, -0.0075],
];

const LEFTWING_FACES: [&[u16]; 5] = [
    &[0, 2, 4],
    &[5, 3, 1],
    &[0, 1, 5, 4],
    &[4, 5, 3, 2],
    &[0, 2, 3, 1],
];

const RIGHTWING_POINTS: [[f32; 3]; 6] = [
    [0.8, 0.16, 0.02],
    [0.8, 0.16, -0.02],
    [-1.4, 0.16, 0.02],
    [-1.4, 0.16, -0.02],
    [-1.75, 1.25, 0.0075],
    [-1.75, 1.25, -0.0075],
];

const RIGHTWING_FACES: [&[u16]; 5] = [
    &[0, 2, 4],
    &[5, 3, 1],
    &[0, 1, 5, 4],
    &[4, 5, 3, 2],
    &[0, 2, 3, 1],
];

const LEFTELEVON_POINTS: [[f32; 3]; 8] = [
    [-1.4, -0.16, 0.0125],
    [-1.4, -0.16, -0.0125],
    [-1.55, -0.16, 0.0125],
    [-1.55, -0.16, -0.0125],
    [-1.5734, -0.7, 0.0125],
    [-1.5734, -0.7, -0.0125],
    [-1.7234, -0.7, 0.0125],
    [-1.7234, -0.7, -0.0125],
];

const LEFTELEVON_FACES: [&[u16]; 6] = [
    &[0, 2, 3, 1],
    &[4, 5, 7, 6],
    &[0, 1, 5, 4],
    &[2, 6, 7, 3],
    &[0, 4, 6, 2],
    &[1, 3, 7, 5],
];

const RIGHTELEVON_POINTS: [[f32; 3]; 8] = [
    [-1.4, 0.16, 0.0125],
    [-1.4, 0.16, -0.0125],
    [-1.55, 0.16, 0.0125],
    [-1.55, 0.16, -0.0125],
    [-1.5734, 0.7, 0.0125],
    [-1.5734, 0.7, -0.0125],
    [-1.7234, 0.7, 0.0125],
    [-1.7234, 0.7, -0.0125],
];

const RIGHTELEVON_FACES: [&[u16]; 6] = [
    &[0, 2, 3, 1],
    &[4, 5, 7, 6],
    &[0, 1, 5, 4],
    &[2, 6, 7, 3],
    &[0, 4, 6, 2],
    &[1, 3, 7, 5],
];

const PROPDISC_POINTS: [[f32; 3]; 13] = [
    [-1.78, 0.0, 0.0],
    [-1.78, 0.0, 0.35],
    [-1.78, 0.175, 0.3031],
    [-1.78, 0.3031, 0.175],
    [-1.78, 0.35, 0.0],
    [-1.78, 0.3031, -0.175],
    [-1.78, 0.175, -0.3031],
    [-1.78, 0.0, -0.35],
    [-1.78, -0.175, -0.3031],
    [-1.78, -0.3031, -0.175],
    [-1.78, -0.35, -0.0],
    [-1.78, -0.3031, 0.175],
    [-1.78, -0.175, 0.3031],
];

const PROPDISC_FACES: [&[u16]; 12] = [
    &[0, 1, 2],
    &[0, 2, 3],
    &[0, 3, 4],
    &[0, 4, 5],
    &[0, 5, 6],
    &[0, 6, 7],
    &[0, 7, 8],
    &[0, 8, 9],
    &[0, 9, 10],
    &[0, 10, 11],
    &[0, 11, 12],
    &[0, 12, 1],
];

/// Every part of the airframe, in build order.
pub const PARTS: [Part; 6] = [
    Part { name: "Fuselage", points: &FUSELAGE_POINTS, faces: &FUSELAGE_FACES, color: BODY_COLOR },
    Part { name: "LeftWing", points: &LEFTWING_POINTS, faces: &LEFTWING_FACES, color: WING_COLOR },
    Part { name: "RightWing", points: &RIGHTWING_POINTS, faces: &RIGHTWING_FACES, color: WING_COLOR },
    Part { name: "LeftElevon", points: &LEFTELEVON_POINTS, faces: &LEFTELEVON_FACES, color: WING_COLOR },
    Part { name: "RightElevon", points: &RIGHTELEVON_POINTS, faces: &RIGHTELEVON_FACES, color: WING_COLOR },
    Part { name: "PropDisc", points: &PROPDISC_POINTS, faces: &PROPDISC_FACES, color: PROP_COLOR },
];

/// Which part spins. The prop disc is rotated about local +Z (the thrust axis)
/// so the aircraft reads as powered in motion.
pub const PROP_PART: &str = "PropDisc";
pub const PROP_SPEED: f32 = 60.0; // rad/s

/// Build one part's mesh, fanning each polygon face into triangles from its
/// first vertex and generating flat normals, as `SurfaceTool.generate_normals`
/// does for a non-smoothed surface.
pub fn part_mesh(part: &Part) -> Mesh {
    let local: Vec<Vec3> = part.points.iter().map(|p| to_local(*p)).collect();
    let mut positions: Vec<[f32; 3]> = Vec::new();
    let mut normals: Vec<[f32; 3]> = Vec::new();
    for face in part.faces {
        if face.len() < 3 {
            continue;
        }
        let a = local[face[0] as usize];
        for i in 1..face.len() - 1 {
            let b = local[face[i] as usize];
            let c = local[face[i + 1] as usize];
            let n = (b - a).cross(c - a).normalize_or_zero();
            for v in [a, b, c] {
                positions.push([v.x, v.y, v.z]);
                normals.push([n.x, n.y, n.z]);
            }
        }
    }
    let n = positions.len() as u32;
    let mut mesh = Mesh::new(
        PrimitiveTopology::TriangleList,
        RenderAssetUsages::default(),
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
    mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, normals);
    mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, vec![[0.0f32, 0.0f32]; n as usize]);
    mesh.insert_indices(Indices::U32((0..n).collect()));
    mesh
}

