"""Kinematic multi-vehicle Isaac Sim world for the eco sim host.

Spawns N Isaac-shipped vehicles (Crazyflie cf2x quads, Nova Carter rovers) in a
single Isaac `World` and moves each kinematically toward per-vehicle goals. One
`World.step()` advances all of them. Physics timeline is NOT played — we override
pose each frame and only render — so kinematics never fight gravity/articulation.

"Sim is just sim": this is a dumb physics+camera backend. All intelligence lives
in the cloud and per-drone command logic (see fleet_worker/fleet_mqtt). Each
vehicle is keyed by its eco drone_id and is indistinguishable from an IRL drone
to the cloud/app.

Threading: pose-target setters and state getters are lock-guarded and safe to
call from per-command threads. Anything that touches the GPU (world.step, camera
creation, get_rgba) is Isaac-thread-only and lives in grab_frame/step_simulation
— callers funnel those through FleetWorker.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_ENV_USD_MAP = {
    "warehouse": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Environments/Simple_Warehouse/warehouse_with_forklifts.usd",
    "hospital":  "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Environments/Hospital/hospital.usd",
    "office":    "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Environments/Office/office.usd",
}
_ENV_USD_MAP["hangar"] = _ENV_USD_MAP["warehouse"]
_ENV_USD_MAP["outdoor"] = _ENV_USD_MAP["warehouse"]


def _resolve_env(environment: str) -> str:
    """Accept either a known scene name or a direct USD URL/path/omniverse path.

    The Ishmael scene resolver passes a full USD reference for Yonder/SimReady scenes;
    legacy callers pass a name. Anything that isn't a known name and looks like a USD
    reference is used verbatim; otherwise we fall back to warehouse.
    """
    if not environment:
        return _ENV_USD_MAP["warehouse"]
    if environment in _ENV_USD_MAP:
        return _ENV_USD_MAP[environment]
    low = environment.lower()
    if (low.endswith(".usd") or low.endswith(".usdc") or low.endswith(".usda")
            or low.startswith(("http://", "https://", "omniverse://", "file:", "/"))):
        return environment
    return _ENV_USD_MAP["warehouse"]

_QUAD_USD_REL = "/Isaac/Robots/Bitcraze/Crazyflie/cf2x.usd"
_ROVER_USD_RELS = [
    "/Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd",
    "/Isaac/Robots/NVIDIA/Carter/carter_v1.usd",
    "/Isaac/Robots/NVIDIA/Jetbot/jetbot.usd",
]


class _Vehicle:
    """Per-vehicle state. Pose/target fields are guarded by `lock`."""

    def __init__(self, drone_id: str, vtype: str, spawn: np.ndarray, prim_path: str):
        self.id = drone_id
        self.vtype = vtype                       # "quadcopter" | "rover"
        self.prim_path = prim_path
        self.lock = threading.Lock()
        self.position = np.asarray(spawn, dtype=float)
        self.yaw = 0.0
        self.goal: Optional[np.ndarray] = None
        self.goal_yaw: Optional[float] = None
        self.velocity_cmd: Optional[np.ndarray] = None
        self.armed = False
        self.prim = None
        self.camera = None                       # created lazily (Isaac thread)
        self.max_lin = 3.0 if vtype == "quadcopter" else 1.5
        self.max_yaw = math.radians(90.0)


class IsaacVehicleBridge:
    """Kinematic Isaac world hosting many Crazyflie/Carter vehicles."""

    _app = None  # SimulationApp singleton

    def __init__(self, headless: bool = True, physics_dt: float = 1.0 / 240.0):
        self._headless = headless
        self._physics_dt = physics_dt
        self._world = None
        self.vehicles: dict[str, _Vehicle] = {}
        self.vantages: dict[str, object] = {}   # name -> Camera (fixed world cameras)

    # -- app / assets -----------------------------------------------------------
    @classmethod
    def get_app(cls, headless: bool = True):
        if cls._app is None:
            from isaacsim import SimulationApp
            cls._app = SimulationApp({
                "headless": headless,
                "multi_gpu": False,
                "active_gpu": int(os.environ.get("WARP_CUDA_DEVICES", "0")),
            })
        return cls._app

    def _assets_root(self) -> str:
        from omni.isaac.core.utils.nucleus import get_assets_root_path
        root = get_assets_root_path()
        if not root:
            raise RuntimeError("Could not resolve Isaac assets root")
        return root

    def _vehicle_usd(self, vtype: str, root: str, photoreal: bool = False) -> str:
        # Photorealistic path: delegate to the Ishmael asset registry.
        if photoreal:
            try:
                from ishmael.assets import vehicle_usd
                return vehicle_usd(vtype, photoreal=True, assets_root=root)
            except Exception:
                pass  # fall through to standard assets
        if vtype == "rover":
            from omni.isaac.core.utils.nucleus import is_file
            for rel in _ROVER_USD_RELS:
                try:
                    if is_file(root + rel):
                        return root + rel
                except Exception:
                    continue
            return root + _ROVER_USD_RELS[0]
        return root + _QUAD_USD_REL

    @staticmethod
    def _grid(n: int, spacing: float = 3.0):
        """Spread n vehicles on a square grid centered near the origin."""
        cols = max(1, int(math.ceil(math.sqrt(n))))
        out = []
        for i in range(n):
            r, c = divmod(i, cols)
            out.append((c * spacing, r * spacing))
        return out

    # -- setup ------------------------------------------------------------------
    def setup(self, environment: str, roster: list[dict]) -> None:
        """roster: [{'id': str, 'type': 'quadcopter'|'rover'}, ...]"""
        self.get_app(self._headless)
        from omni.isaac.core.world import World
        from omni.isaac.core.utils.stage import add_reference_to_stage
        from omni.isaac.core.prims import XFormPrim

        self._world = World(physics_dt=self._physics_dt,
                            rendering_dt=self._physics_dt * 4,
                            stage_units_in_meters=1.0)

        # `environment` may be a known name (office/warehouse/...) or, when the
        # Ishmael scene resolver ran, a full USD URL / file path / omniverse path.
        env_url = _resolve_env(environment)
        try:
            add_reference_to_stage(usd_path=env_url, prim_path="/World/Scene")
            logger.info("Loaded scene %s", environment)
        except Exception as e:
            logger.warning("Scene load failed (%s); ground plane", e)
            self._world.scene.add_default_ground_plane()

        root = self._assets_root()
        cells = self._grid(len(roster))
        for i, spec in enumerate(roster):
            vtype = spec["type"]
            photoreal = bool(spec.get("photoreal", False))
            gx, gy = cells[i]
            z = 0.0 if vtype == "rover" else 1.0
            spawn = np.array([gx, gy, z])
            prim_path = f"/World/veh_{i}"
            add_reference_to_stage(usd_path=self._vehicle_usd(vtype, root, photoreal),
                                   prim_path=prim_path)
            v = _Vehicle(spec["id"], vtype, spawn, prim_path)
            v.prim = XFormPrim(prim_path)
            self.vehicles[spec["id"]] = v
            print(f"[isaac] spawned {spec['id']} ({vtype}) at {spawn.tolist()}", flush=True)

        self._world.reset()
        for v in self.vehicles.values():
            self._apply_pose(v)
        for _ in range(30):
            self._world.step(render=True)
        logger.info("IsaacVehicleBridge ready: %d vehicles in %s",
                    len(self.vehicles), environment)

    # -- camera (Isaac thread only) ---------------------------------------------
    def ensure_camera(self, drone_id: str) -> None:
        v = self.vehicles[drone_id]
        if v.camera is not None:
            return
        import math as m
        from omni.isaac.sensor import Camera
        cam = Camera(prim_path=f"{v.prim_path}/eco_camera",
                     position=np.array([0.25, 0.0, 0.10]),
                     resolution=(640, 480),
                     orientation=np.array([1.0, 0.0, 0.0, 0.0]))
        cam.initialize()
        hfov = m.radians(70.0)
        focal = cam.get_focal_length() or 24.0
        h_ap = 2.0 * focal * m.tan(hfov / 2.0)
        cam.set_horizontal_aperture(h_ap)
        cam.set_vertical_aperture(h_ap * 480 / 640)
        for _ in range(3):
            self._world.step(render=True)
        v.camera = cam
        print(f"[isaac] camera ready for {drone_id}", flush=True)

    # -- vantage cameras (fixed world viewpoints, Isaac thread only) ------------
    def add_vantage_camera(self, name: str, position, look_at,
                           resolution=(1280, 720), hfov_deg: float = 60.0) -> None:
        """Create a fixed camera at ``position`` aimed at ``look_at`` (world coords).

        Unlike per-vehicle cameras, vantage cameras are not parented to anything —
        they observe the whole scene (e.g. an overhead or corner shot of the drones).
        Idempotent: re-adding a name is a no-op.
        """
        if name in self.vantages:
            return
        import math as m
        from omni.isaac.sensor import Camera
        from scipy.spatial.transform import Rotation
        pos = np.asarray(position, dtype=float)
        target = np.asarray(look_at, dtype=float)
        fwd = target - pos
        n = float(np.linalg.norm(fwd))
        fwd = fwd / n if n > 1e-6 else np.array([1.0, 0.0, 0.0])
        # Map the camera's default optical axis (+X) onto fwd, keeping world +Z up.
        rot, _ = Rotation.align_vectors([fwd, [0, 0, 1]], [[1, 0, 0], [0, 0, 1]])
        q_xyzw = rot.as_quat()
        q_wxyz = np.array([q_xyzw[3], q_xyzw[0], q_xyzw[1], q_xyzw[2]])
        cam = Camera(prim_path=f"/World/vantage_{name}", position=pos,
                     resolution=resolution, orientation=q_wxyz)
        cam.initialize()
        hfov = m.radians(hfov_deg)
        focal = cam.get_focal_length() or 24.0
        h_ap = 2.0 * focal * m.tan(hfov / 2.0)
        cam.set_horizontal_aperture(h_ap)
        cam.set_vertical_aperture(h_ap * resolution[1] / resolution[0])
        for _ in range(3):
            self._world.step(render=True)
        self.vantages[name] = cam
        print(f"[isaac] vantage camera '{name}' at {pos.tolist()} -> {target.tolist()}",
              flush=True)

    def grab_vantage_frame(self, name: str):
        """Return latest RGB (H,W,3) for a vantage camera. Isaac thread only."""
        cam = self.vantages.get(name)
        if cam is None:
            return None
        try:
            rgba = cam.get_rgba()
            if rgba is not None and rgba.size:
                return np.ascontiguousarray(rgba[:, :, :3])
        except Exception:
            pass
        return None

    def scene_center_and_extent(self) -> tuple[np.ndarray, float]:
        """Rough center + radius of the spawned vehicles, for auto-placing vantages."""
        if not self.vehicles:
            return np.zeros(3), 5.0
        pts = np.array([v.position for v in self.vehicles.values()])
        center = pts.mean(axis=0)
        radius = float(np.max(np.linalg.norm(pts - center, axis=1))) + 5.0
        return center, radius

    def grab_frame(self, drone_id: str):
        """Return latest RGB (H,W,3) for a vehicle. Isaac thread only."""
        v = self.vehicles[drone_id]
        if v.camera is None:
            self.ensure_camera(drone_id)
        try:
            rgba = v.camera.get_rgba()
            if rgba is not None and rgba.size:
                return np.ascontiguousarray(rgba[:, :, :3])
        except Exception:
            pass
        return None

    # -- kinematics (Isaac thread) ----------------------------------------------
    def _apply_pose(self, v: _Vehicle) -> None:
        from scipy.spatial.transform import Rotation
        q_xyzw = Rotation.from_euler("Z", v.yaw).as_quat()
        q_wxyz = np.array([q_xyzw[3], q_xyzw[0], q_xyzw[1], q_xyzw[2]])
        v.prim.set_world_pose(position=v.position, orientation=q_wxyz)

    def integrate_all(self, dt: float) -> None:
        for v in self.vehicles.values():
            with v.lock:
                if v.velocity_cmd is not None:
                    v.position = v.position + v.velocity_cmd * dt
                    if v.vtype == "rover":
                        v.position[2] = 0.0
                elif v.goal is not None:
                    delta = v.goal - v.position
                    dist = float(np.linalg.norm(delta))
                    if dist < 0.05:
                        v.position = v.goal.copy()
                        v.goal = None
                    else:
                        v.position = v.position + delta / dist * min(v.max_lin * dt, dist)
                if v.goal_yaw is not None:
                    dy = (v.goal_yaw - v.yaw + math.pi) % (2 * math.pi) - math.pi
                    if abs(dy) < math.radians(1):
                        v.yaw = v.goal_yaw
                        v.goal_yaw = None
                    else:
                        v.yaw += max(-v.max_yaw * dt, min(v.max_yaw * dt, dy))
            self._apply_pose(v)

    def step_simulation(self, n_steps: int = 1, render: bool = True) -> None:
        self.integrate_all(self._physics_dt * n_steps)
        self._world.step(render=render)

    # -- thread-safe target setters / state getters (any thread) ----------------
    def set_drone_goal(self, drone_id: str, position) -> None:
        v = self.vehicles[drone_id]
        with v.lock:
            g = np.asarray(position, dtype=float)
            if v.vtype == "rover":
                g[2] = 0.0
            v.goal = g
            v.velocity_cmd = None

    def set_drone_velocity(self, drone_id: str, velocity) -> None:
        v = self.vehicles[drone_id]
        with v.lock:
            vel = np.asarray(velocity, dtype=float)
            n = float(np.linalg.norm(vel))
            if n > v.max_lin:
                vel = vel / n * v.max_lin
            v.velocity_cmd = vel
            v.goal = None

    def clear_velocity(self, drone_id: str) -> None:
        v = self.vehicles[drone_id]
        with v.lock:
            v.velocity_cmd = None

    def set_drone_yaw(self, drone_id: str, yaw_rad: float) -> None:
        v = self.vehicles[drone_id]
        with v.lock:
            v.goal_yaw = float(yaw_rad)

    def get_drone_state(self, drone_id: str) -> dict:
        v = self.vehicles[drone_id]
        with v.lock:
            return {"position": v.position.copy(), "yaw": float(v.yaw),
                    "vtype": v.vtype}

    def teardown(self) -> None:
        self.vehicles.clear()

    @classmethod
    def shutdown(cls) -> None:
        if cls._app is not None:
            cls._app.close()
            cls._app = None
