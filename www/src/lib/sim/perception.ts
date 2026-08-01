// BVH-accelerated raycast perception for the flight policies. Neither policy
// takes an image — both take scalar range measurements (a 45-ray depth grid
// for the quad, a 72-ray 360deg ring for the rover), so this samples real
// distances against the GLB environment mesh via three-mesh-bvh.

import * as THREE from "three";
import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from "three-mesh-bvh";
import {
  RAY_DIRS,
  DEPTH_RAYS,
  DEPTH_MAX,
  LIDAR_DIRS,
  LIDAR_RAYS,
  LIDAR_MAX,
  bodyForward,
  bodyLeft,
  BODY_UP,
} from "./contract";

let patched = false;
function ensureBvhPatched() {
  if (patched) return;
  // three-mesh-bvh's own module augmentation and function signatures disagree slightly
  // (GeometryBVH vs MeshBVH) — this is the library's documented global-patch idiom, cast
  // through unknown to sidestep the upstream typing mismatch rather than the assignment itself.
  (THREE.BufferGeometry.prototype as unknown as { computeBoundsTree: unknown }).computeBoundsTree =
    computeBoundsTree;
  (THREE.BufferGeometry.prototype as unknown as { disposeBoundsTree: unknown }).disposeBoundsTree =
    disposeBoundsTree;
  (THREE.Mesh.prototype as unknown as { raycast: unknown }).raycast = acceleratedRaycast;
  patched = true;
}

/** Walk `root`, build (or reuse) a BVH per mesh geometry, and return the flat mesh list to raycast against. */
export function buildBvh(root: THREE.Object3D): THREE.Mesh[] {
  ensureBvhPatched();
  const meshes: THREE.Mesh[] = [];
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if ((mesh as THREE.Mesh & { isMesh?: boolean }).isMesh && mesh.geometry) {
      if (!mesh.geometry.boundsTree) {
        mesh.geometry.computeBoundsTree();
      }
      meshes.push(mesh);
    }
  });
  return meshes;
}

const _raycaster = new THREE.Raycaster();
const _dir = new THREE.Vector3();

function castRay(
  origin: THREE.Vector3,
  fwd: readonly [number, number, number],
  left: readonly [number, number, number],
  up: readonly [number, number, number],
  rf: number,
  rl: number,
  ru: number,
  maxDist: number,
  targets: THREE.Mesh[],
): number {
  _dir
    .set(
      rf * fwd[0] + rl * left[0] + ru * up[0],
      rf * fwd[1] + rl * left[1] + ru * up[1],
      rf * fwd[2] + rl * left[2] + ru * up[2],
    )
    .normalize();
  _raycaster.set(origin, _dir);
  _raycaster.far = maxDist;
  const hits = _raycaster.intersectObjects(targets, false);
  if (hits.length === 0) return maxDist;
  return Math.min(hits[0].distance, maxDist);
}

/** 45-ray forward depth grid (9x5, +-45deg yaw, +-35deg pitch), metres, row-major top->bottom/left->right. */
export function sampleDepthGrid(
  origin: THREE.Vector3,
  yaw: number,
  targets: THREE.Mesh[],
  out: Float32Array = new Float32Array(DEPTH_RAYS),
): Float32Array {
  const fwd = bodyForward(yaw);
  const left = bodyLeft(yaw);
  for (let i = 0; i < DEPTH_RAYS; i++) {
    out[i] = castRay(
      origin,
      fwd,
      left,
      BODY_UP,
      RAY_DIRS[i * 3 + 0],
      RAY_DIRS[i * 3 + 1],
      RAY_DIRS[i * 3 + 2],
      DEPTH_MAX,
      targets,
    );
  }
  return out;
}

/** 72-ray 360deg horizontal lidar ring, metres, ray 0 = forward, CCW. */
export function sampleLidarRing(
  origin: THREE.Vector3,
  yaw: number,
  targets: THREE.Mesh[],
  out: Float32Array = new Float32Array(LIDAR_RAYS),
): Float32Array {
  const fwd = bodyForward(yaw);
  const left = bodyLeft(yaw);
  for (let i = 0; i < LIDAR_RAYS; i++) {
    out[i] = castRay(
      origin,
      fwd,
      left,
      BODY_UP,
      LIDAR_DIRS[i * 2 + 0],
      LIDAR_DIRS[i * 2 + 1],
      0,
      LIDAR_MAX,
      targets,
    );
  }
  return out;
}
