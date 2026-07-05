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
from pathlib import Path
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
_FW_ALIASES = ("fixedwing", "fw", "plane")
# Not a Nucleus-hosted asset: shipped locally alongside this module.
_FW_USD_LOCAL = Path(__file__).parent / "assets" / "delta_wing.usda"


class _Vehicle:
    """Per-vehicle state. Pose/target fields are guarded by `lock`."""

    def __init__(self, drone_id: str, vtype: str, spawn: np.ndarray, prim_path: str):
        self.id = drone_id
        self.vtype = vtype                       # "quadcopter" | "rover" | "fixedwing"
        self.prim_path = prim_path
        self.lock = threading.Lock()
        self.position = np.asarray(spawn, dtype=float)
        self.yaw = 0.0
        self.roll = 0.0                          # bank angle (rad), fixed-wing visual only
        self.goal: Optional[np.ndarray] = None
        self.goal_yaw: Optional[float] = None
        self.velocity_cmd: Optional[np.ndarray] = None
        self.armed = False
        self.prim = None
        self.camera = None                       # created lazily (Isaac thread)
        if vtype in _FW_ALIASES:
            self.max_lin = 25.0
            self.max_yaw = 0.6   # matches FIXEDWING.max_yaw_rate_radps (bank-limited)
        else:
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
        if vtype in _FW_ALIASES:
            return str(_FW_USD_LOCAL)
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

        # `environment` may be a known name (office/warehouse/...), a full USD URL/path, or one
        # of {"", "none", "ground"} to request a bare lit ground plane (used by the obstacle
        # course eval, where our own cuboids are the challenge and we want guaranteed free space).
        if environment in ("", "none", "ground"):
            self._world.scene.add_default_ground_plane()
            logger.info("Bare ground plane (no USD scene)")
        else:
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
        # Depth channel for training-data capture (distance to image plane, meters).
        try:
            cam.add_distance_to_image_plane_to_frame()
        except Exception as e:
            logger.warning("depth annotator unavailable for %s: %s", drone_id, e)
        for _ in range(3):
            self._world.step(render=True)
        v.camera = cam
        print(f"[isaac] camera ready for {drone_id}", flush=True)

    # -- vantage cameras (fixed world viewpoints, Isaac thread only) ------------
    @staticmethod
    def _look_at_quat(pos: np.ndarray, target: np.ndarray) -> np.ndarray:
        """wxyz orientation mapping the camera's default +X optical axis onto (target-pos),
        keeping world +Z "up" as the secondary reference. Shared by add_vantage_camera and
        update_vantage_camera so a moving (chase-style) vantage camera re-aims identically to
        how it was first framed.

        Degenerate for a near-vertical look direction (fwd ~= +-world Z): aligning both fwd
        and the up-reference to the same axis is ill-posed and scipy silently returns an
        arbitrary roll (warns "Optimal rotation is not uniquely...defined"), which showed up
        as a broken top-down/bird's-eye vantage camera. Falls back to world +Y as the up
        reference in that case (any axis not parallel to fwd would do).
        """
        from scipy.spatial.transform import Rotation
        fwd = target - pos
        n = float(np.linalg.norm(fwd))
        fwd = fwd / n if n > 1e-6 else np.array([1.0, 0.0, 0.0])
        up_ref = np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(fwd, up_ref))) > 0.99:   # fwd ~parallel to +-Z: pick another ref
            up_ref = np.array([0.0, 1.0, 0.0])
        rot, _ = Rotation.align_vectors([fwd, up_ref], [[1, 0, 0], [0, 0, 1]])
        q_xyzw = rot.as_quat()
        return np.array([q_xyzw[3], q_xyzw[0], q_xyzw[1], q_xyzw[2]])

    def add_vantage_camera(self, name: str, position, look_at,
                           resolution=(1280, 720), hfov_deg: float = 60.0) -> None:
        """Create a fixed camera at ``position`` aimed at ``look_at`` (world coords).

        Unlike per-vehicle cameras, vantage cameras are not parented to anything —
        they observe the whole scene (e.g. an overhead or corner shot of the drones).
        Idempotent: re-adding a name is a no-op. Call `update_vantage_camera` afterward
        per-tick to turn this into a chase/trailing camera for a moving vehicle.
        """
        if name in self.vantages:
            return
        import math as m
        from omni.isaac.sensor import Camera
        pos = np.asarray(position, dtype=float)
        target = np.asarray(look_at, dtype=float)
        q_wxyz = self._look_at_quat(pos, target)
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

    def update_vantage_camera(self, name: str, position, look_at) -> None:
        """Reposition + re-aim an existing vantage camera (Isaac thread only).

        Call once per tick with the desired trailing offset from a moving vehicle to
        turn a vantage camera into a chase camera — cheaper than re-parenting, and
        keeps the same aperture/FOV set at creation time.
        """
        cam = self.vantages.get(name)
        if cam is None:
            return
        pos = np.asarray(position, dtype=float)
        target = np.asarray(look_at, dtype=float)
        cam.set_world_pose(position=pos, orientation=self._look_at_quat(pos, target))

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

    def grab_depth(self, drone_id: str):
        """Return latest depth (H,W) float meters for a vehicle, or None. Isaac thread only.

        Used by the sim dataset recorder to write paired RGB+depth training samples and to
        bootstrap the monocular-depth model (Model 4). Convert to uint16 mm at the writer.
        """
        v = self.vehicles[drone_id]
        if v.camera is None:
            self.ensure_camera(drone_id)
        try:
            frame = v.camera.get_current_frame()
            depth = frame.get("distance_to_image_plane")
            if depth is not None and getattr(depth, "size", 0):
                return np.ascontiguousarray(depth)
        except Exception:
            pass
        return None

    def camera_intrinsics(self, drone_id: str) -> dict:
        """Pinhole intrinsics for a vehicle camera (fx, fy, cx, cy, width, height).

        Derived from the 70° HFOV + 640x480 set in ensure_camera; used for ground-truth
        bbox projection and as the metric-depth prior.
        """
        v = self.vehicles[drone_id]
        if v.camera is None:
            self.ensure_camera(drone_id)
        w, h = 640, 480
        hfov = math.radians(70.0)
        fx = (w / 2.0) / math.tan(hfov / 2.0)
        fy = fx  # square pixels (vertical aperture set proportionally)
        return {"fx": fx, "fy": fy, "cx": w / 2.0, "cy": h / 2.0, "width": w, "height": h}

    def camera_world_pose(self, drone_id: str):
        """(position[3], quat_wxyz[4]) of the vehicle camera in world frame, or None."""
        v = self.vehicles[drone_id]
        if v.camera is None:
            self.ensure_camera(drone_id)
        try:
            pos, quat = v.camera.get_world_pose()
            return np.asarray(pos, dtype=float), np.asarray(quat, dtype=float)
        except Exception:
            return None

    def vehicle_world_positions(self) -> dict:
        """{drone_id: (position[3], vtype, radius_m)} for all vehicles — ground-truth anchors.

        The dataset recorder projects these into each camera to auto-label the drone/UAV and
        rover classes (the rare classes sim is best at generating). `radius_m` is a coarse
        bounding sphere used to size the projected 2D box.
        """
        out = {}
        for vid, v in self.vehicles.items():
            with v.lock:
                if v.vtype == "quadcopter":
                    radius = 0.15
                elif v.vtype in _FW_ALIASES:
                    radius = 1.0
                else:
                    radius = 0.5
                out[vid] = (v.position.copy(), v.vtype, radius)
        return out

    # -- kinematics (Isaac thread) ----------------------------------------------
    def _apply_pose(self, v: _Vehicle) -> None:
        from scipy.spatial.transform import Rotation
        # Extrinsic 'ZX' = Rz(yaw) @ Rx(roll) applied to a vector: yaw to heading first, then
        # roll about the (fixed-world) X axis -- gives a banked look for fixed-wing turns that
        # holds regardless of heading. roll stays 0 for quad/rover. NOTE: scipy's lowercase
        # ("intrinsic") sequence composes in the OPPOSITE matrix order from what the name
        # suggests here -- 'zx' intrinsic gives Rx(roll) @ Rz(yaw), i.e. roll gets applied in
        # world frame *before* yaw and washes out for any non-zero heading. Verified numerically
        # against a hand-built Rz @ Rx matrix product before fixing.
        q_xyzw = Rotation.from_euler("ZX", [v.yaw, v.roll]).as_quat()
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

    # -- obstacles (Isaac thread only) ------------------------------------------
    def add_obstacle_box(self, name: str, position, half_extents,
                         color=(0.8, 0.3, 0.2)) -> None:
        """Spawn a static visible cuboid obstacle at world `position` (Isaac thread only).

        `half_extents` is (hx, hy, hz) in meters. Used by the obstacle-course eval to place
        things the drone must fly around; the cuboid renders in the camera and shows up in the
        depth image exactly like a real obstacle.
        """
        from omni.isaac.core.objects import FixedCuboid
        pos = np.asarray(position, dtype=float)
        he = np.asarray(half_extents, dtype=float)
        cube = FixedCuboid(
            prim_path=f"/World/obstacle_{name}",
            name=f"obstacle_{name}",
            position=pos,
            scale=2.0 * he,                # FixedCuboid size is full extent
            color=np.asarray(color, dtype=float),
        )
        self._world.scene.add(cube)
        for _ in range(3):
            self._world.step(render=True)
        print(f"[isaac] obstacle '{name}' at {pos.tolist()} half={he.tolist()}", flush=True)

    def add_marker(self, drone_id: str, color=(0.1, 0.9, 0.2), radius: float = 0.5) -> None:
        """Parent a bright visual sphere to a drone so it's clearly visible in third-person
        renders (the stock Crazyflie body is only ~15 cm and reads as a speck). Isaac thread.
        """
        v = self.vehicles[drone_id]
        from omni.isaac.core.objects import VisualSphere
        sph = VisualSphere(
            prim_path=f"{v.prim_path}/eco_marker",
            name=f"marker_{drone_id}",
            radius=radius,
            color=np.asarray(color, dtype=float),
        )
        self._world.scene.add(sph)
        for _ in range(2):
            self._world.step(render=True)
        print(f"[isaac] marker on {drone_id}", flush=True)

    def add_lighting(self, sun_intensity: float = 3000.0, dome_intensity: float = 1500.0) -> None:
        """Add a bright distant 'sun' + dome fill so renders aren't murky (Isaac thread)."""
        try:
            import omni.usd
            from pxr import UsdLux, Gf, UsdGeom
            stage = omni.usd.get_context().get_stage()
            sun = UsdLux.DistantLight.Define(stage, "/World/eco_sun")
            sun.CreateIntensityAttr(sun_intensity)
            sun.CreateAngleAttr(1.0)
            UsdGeom.Xformable(sun.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(-45.0, 10.0, 0.0))
            dome = UsdLux.DomeLight.Define(stage, "/World/eco_dome")
            dome.CreateIntensityAttr(dome_intensity)
            for _ in range(3):
                self._world.step(render=True)
            print("[isaac] lighting added", flush=True)
        except Exception as e:
            print(f"[isaac] lighting failed: {e}", flush=True)

    def drop_trail(self, name: str, position, color=(0.1, 0.85, 1.0), radius: float = 0.12) -> None:
        """Drop a small static sphere breadcrumb at a world position to trace the flight path.

        Does NOT register in world.scene (static visual only) — registering keeps the name
        reserved after delete_prim and collides when a path is reused on the next pass.
        """
        from omni.isaac.core.objects import VisualSphere
        import numpy as _np
        VisualSphere(prim_path=f"/World/trail_{name}", name=f"trail_{name}",
                     radius=radius, color=_np.asarray(color, dtype=float),
                     position=_np.asarray(position, dtype=float))

    def clear_trails(self) -> None:
        """Delete all breadcrumb prims (between passes/courses)."""
        try:
            from omni.isaac.core.utils.prims import delete_prim, get_prim_path
            import omni.usd
            stage = omni.usd.get_context().get_stage()
            for prim in stage.Traverse():
                p = str(prim.GetPath())
                if p.startswith("/World/trail_"):
                    delete_prim(p)
        except Exception as e:
            print(f"[isaac] clear_trails: {e}", flush=True)

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

    def set_drone_roll(self, drone_id: str, roll_rad: float) -> None:
        """Set the visual bank angle (rad, +right wing down). Applied immediately (not
        rate-limited like goal_yaw) -- callers should already derive a physically bounded
        bank from speed/yaw-rate (e.g. atan2(v * yaw_rate, g)) before calling this."""
        v = self.vehicles[drone_id]
        with v.lock:
            v.roll = float(roll_rad)

    def set_drone_pose(self, drone_id: str, position, yaw_rad: float = 0.0,
                       roll_rad: float = 0.0) -> None:
        """Teleport a vehicle to an exact pose and clear its motion (Isaac thread).

        Used to reset a drone to the course start between evaluation passes.
        """
        v = self.vehicles[drone_id]
        with v.lock:
            p = np.asarray(position, dtype=float)
            if v.vtype == "rover":
                p[2] = 0.0
            v.position = p
            v.yaw = float(yaw_rad)
            v.roll = float(roll_rad)
            v.goal = None
            v.goal_yaw = None
            v.velocity_cmd = None
        self._apply_pose(v)
        self._world.step(render=True)

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
