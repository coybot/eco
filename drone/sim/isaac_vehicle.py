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
            # ISHMAEL_RENDERER controls the rendering mode.
            # "RayTracedLighting" — default; required for OmniPBR/MDL materials
            #   (wilderness/forest scenes use MDL; they render black in Rasterized)
            # "RasterizedRendering" — fastest, only for scenes with UsdPreviewSurface
            renderer = os.environ.get("ISHMAEL_RENDERER", "RayTracedLighting")
            cls._app = SimulationApp({
                "headless": headless,
                "multi_gpu": False,
                "active_gpu": int(os.environ.get("WARP_CUDA_DEVICES", "0")),
                "renderer": renderer,
                # Disable synchronous USD/material loads so the first step_simulation
                # call returns quickly even when CDN vegetation isn't fully loaded yet.
                # Assets stream in progressively — early frames may be partially loaded
                # but non-black.  Without this, each step blocks waiting for every CDN
                # tree USD to download (minutes for 6000+ instances).
                "/rtx/materialDb/syncLoads": False,
                "/rtx/hydra/materialSyncLoads": False,
                "/omni.kit.plugin/syncUsdLoads": False,
            })
            print(f"[isaac] SimulationApp renderer={renderer} asyncLoads=True",
                  flush=True)
        return cls._app

    def _assets_root(self) -> str:
        from omni.isaac.core.utils.nucleus import get_assets_root_path
        root = get_assets_root_path()
        if not root:
            raise RuntimeError("Could not resolve Isaac assets root")
        return root

    def _vehicle_usd(self, vtype: str, root: str, photoreal: bool = False) -> str:
        if photoreal:
            try:
                from ishmael.assets import vehicle_usd
                return vehicle_usd(vtype, photoreal=True, assets_root=root)
            except Exception:
                pass
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
        # Wilderness/infinigen USDs contain CDN-referenced vegetation that breaks
        # Camera.get_rgba() in headless mode (all cameras return all-zero frames
        # regardless of lighting, materials, or warmup steps).  For these scenes we
        # skip loading the USD reference and build the scene procedurally instead.
        # The procedural scene renders immediately with UsdPreviewSurface geometry.
        _is_outdoor_custom = (
            env_url.startswith("/") and
            ("wilderness" in env_url or "infinigen" in env_url)
        )
        if _is_outdoor_custom:
            logger.info("Outdoor custom scene detected — using procedural geometry "
                        "(wilderness USD breaks headless camera capture): %s", env_url)
            self._world.scene.add_default_ground_plane()  # physics ground
        else:
            try:
                add_reference_to_stage(usd_path=env_url, prim_path="/World/Scene")
                logger.info("Loaded scene %s", environment)
            except Exception as e:
                logger.warning("Scene load failed (%s); ground plane", e)
                self._world.scene.add_default_ground_plane()

        # Wilderness / infinigen / custom outdoor scenes have no lights — add them.
        # Isaac built-in scenes (office/warehouse/hospital) already contain lighting;
        # we only inject when the env is a file path (not a CDN/s3 URL) OR explicitly
        # an outdoor type.  Adding extra lights to an already-lit scene is harmless
        # (slight overexposure at worst).
        _needs_light = (
            _is_outdoor_custom or
            "outdoor"    in env_url.lower() or
            os.environ.get("ISHMAEL_FORCE_LIGHTING") == "1"
        )
        if _needs_light:
            self._add_default_lighting()

        root = self._assets_root()
        cells = self._grid(len(roster))

        # Determine per-scene spawn adjustments.
        env_key = environment.lower().split("/")[-1].replace(".usd", "")
        # Office: origin is a dark entry corridor — shift +5 m into the main area.
        xy_offset = np.array([5.0, 0.0]) if "office" in env_key else np.zeros(2)

        # Wilderness / infinigen: when _is_outdoor_custom we build a procedural scene
        # whose ground plane is at z=0.  Spawn at z=2 (low hover/drive height).
        # Do NOT read manifest z_range — that was for the real wilderness USD terrain
        # which we no longer load.
        spawn_z_base = 0.0
        if _is_outdoor_custom:
            spawn_z_base = 2.0
            print(f"[isaac] procedural forest: spawn base z={spawn_z_base:.1f} m",
                  flush=True)
            # Store for vantage camera height calculation (trees are 7-22 m tall)
            self._outdoor_z_base = spawn_z_base
        else:
            self._outdoor_z_base = None

        for i, spec in enumerate(roster):
            vtype = spec["type"]
            gx, gy = cells[i]
            gx += xy_offset[0]; gy += xy_offset[1]
            if spawn_z_base > 0:
                # Outdoor: both rover and quad float above terrain (kinematic sim)
                z = spawn_z_base
            else:
                z = 0.0 if vtype == "rover" else 1.0
            spawn = np.array([gx, gy, z])
            prim_path = f"/World/veh_{i}"
            photoreal = bool(spec.get("photoreal", False))
            add_reference_to_stage(usd_path=self._vehicle_usd(vtype, root, photoreal),
                                   prim_path=prim_path)
            v = _Vehicle(spec["id"], vtype, spawn, prim_path)
            v.prim = XFormPrim(prim_path)
            # Scale quadcopters to a visible size. The Crazyflie CF2X asset is ~10 cm
            # — invisible in vantage recordings at 5–10 m. 8× gives ~80 cm, visible
            # and representative of a small inspection drone. Skip when photoreal=True
            # (the photoreal asset is already a full-size model).
            if vtype == "quadcopter" and not photoreal:
                v.prim.set_local_scale(np.array([8.0, 8.0, 8.0]))
            self.vehicles[spec["id"]] = v
            print(f"[isaac] spawned {spec['id']} ({vtype}) at {spawn.tolist()}"
                  f"{' [8× scale]' if vtype == 'quadcopter' and not photoreal else ''}",
                  flush=True)

        self._world.reset()
        for v in self.vehicles.values():
            self._apply_pose(v)

        # Initial warmup: kick the renderer enough to initialise the scene graph.
        # The real CDN convergence wait (for wilderness/forest) happens in
        # FleetWorker._wait_for_vantage_render() *after* cameras are created —
        # that is where it actually matters.  A large value here is useless because
        # cameras don't exist yet.
        n_warmup = 60 if _needs_light else 30
        for _ in range(n_warmup):
            self._world.step(render=True)
        logger.info("IsaacVehicleBridge ready: %d vehicles in %s",
                    len(self.vehicles), environment)

    # -- lighting ---------------------------------------------------------------
    def _add_default_lighting(self) -> None:
        """Add a sun + sky dome and force RaytracedLighting for outdoor scenes.

        Outdoor/wilderness scenes need:
        1. Explicit lights (they ship without any)
        2. RaytracedLighting render mode — PathTracing is default but renders
           12 500-tree vegetation scenes at ~1 fps, making warmup take hours.
           RaytracedLighting renders the same scene at real-time speeds.
        """
        try:
            import carb.settings
            s = carb.settings.get_settings()
            s.set("/rtx/rendermode", "RaytracedLighting")
            print("[isaac] render mode set to RaytracedLighting (outdoor scene)",
                  flush=True)
        except Exception as e:
            print(f"[isaac] could not set render mode: {e}", flush=True)

        try:
            from pxr import UsdLux, Gf
            stage = self._world.scene.stage

            # Sun — directional light at 45° elevation from NW
            dl_path = "/World/_IshmaeL_DistantLight"
            if not stage.GetPrimAtPath(dl_path):
                dl = UsdLux.DistantLight.Define(stage, dl_path)
                dl.CreateIntensityAttr(3000.0)
                dl.CreateAngleAttr(0.53)          # solar disc angular diameter
                dl.CreateColorAttr(Gf.Vec3f(1.0, 0.95, 0.85))  # warm sunlight
                xform = dl.GetPrim().GetAttribute("xformOp:rotateXYZ")
                if not xform:
                    from pxr import UsdGeom
                    xform_api = UsdGeom.XformCommonAPI(dl.GetPrim())
                    xform_api.SetRotate(Gf.Vec3f(-45.0, 0.0, -45.0))
                else:
                    xform.Set(Gf.Vec3f(-45.0, 0.0, -45.0))

            # NOTE: DomeLight without an HDR texture causes RTX to output all-black
            # frames in headless mode.  Use only DistantLight for outdoor scenes.
            print("[isaac] default lighting injected (sun DistantLight only)",
                  flush=True)

            # Add a PROCEDURAL outdoor scene using native Isaac/USD geometry.
            # The wilderness USD reference renders black because Camera.get_rgba()
            # cannot capture CDN-referenced vegetation in headless mode.  We create
            # a simple green terrain mesh + tree primitives directly in the stage —
            # these have UsdPreviewSurface materials that render in any mode.
            # The wilderness USD reference stays (vegetation may appear when CDN
            # loads), but the procedural layer guarantees non-black frames.
            self._setup_procedural_outdoor(stage)

            # Bind a grass-green UsdPreviewSurface to the terrain mesh.
            # The wilderness author_wilderness.py creates the mesh geometry but
            # never assigns a material — unbound meshes render black in RTX.
            # UsdPreviewSurface works with all renderers (RTX + rasterized).
            self._bind_terrain_material(stage)
        except Exception as e:
            logger.warning("Could not add default lighting: %s", e)

    # Hoopoe path to the pre-built patched tree USD (real mesh, UsdPreviewSurface materials).
    # Created by _patch_tree_usd() on first use and cached here.
    _PATCHED_TREE_USD: Optional[str] = None

    def _patch_tree_usd(self) -> Optional[str]:
        """Create /tmp/gant_tree_patched.usda: real gant_tree geometry + UsdPreviewSurface.

        The drive-sim staging gant_tree_inst.usd has a real 664-face tree mesh with
        UV-mapped bark texture, but uses MDL/SimPBR shaders which render black in
        headless Isaac.  This method creates a thin USD wrapper that references the
        original geometry and overrides the material binding with UsdPreviewSurface
        shaders pointing to the same local texture files.
        Returns the path on success, None if assets are missing.
        """
        import os
        TREE_SRC = (
            "/home/yusuf/drive-sim-staging/scene_herrenberg_urban/"
            "scene_assets/road_runner/props/gant_tree_inst.usd"
        )
        TEX_DIR = (
            "/home/yusuf/drive-sim-staging/scene_herrenberg_urban/"
            "scene_assets/road_runner/props/materials/textures"
        )
        BARK_TEX  = os.path.join(TEX_DIR, "gant_tree_basecolor.png")
        ROUGH_TEX = os.path.join(TEX_DIR, "gant_tree_roughness.png")
        OUT = "/tmp/gant_tree_patched.usda"

        if not os.path.exists(TREE_SRC) or not os.path.exists(BARK_TEX):
            print("[isaac] gant_tree assets not found — skipping real tree mesh",
                  flush=True)
            return None

        if os.path.exists(OUT):
            return OUT  # already built this session

        try:
            from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf

            stage = Usd.Stage.CreateNew(OUT)
            stage.SetMetadata("metersPerUnit", 1.0)
            stage.SetMetadata("upAxis", "Z")

            # Root references the real tree — real 664-face mesh comes in here
            root = UsdGeom.Xform.Define(stage, "/gant_tree")
            root.GetPrim().GetReferences().AddReference(TREE_SRC)
            stage.SetDefaultPrim(root.GetPrim())

            # UsdPreviewSurface material with textured bark.
            # UsdUVTexture file paths must be resolvable by the USD asset resolver;
            # in headless Isaac this means they must be either absolute POSIX paths
            # with the leading "@" asset notation or embedded in the layer.
            # We use UsdUVTexture + UsdPrimvarReader_float2 for the st primvar.
            mat_path = "/gant_tree/_Mat"
            mat = UsdShade.Material.Define(stage, mat_path)

            uv = UsdShade.Shader.Define(stage, mat_path + "/uv")
            uv.CreateIdAttr("UsdPrimvarReader_float2")
            uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            uv.CreateOutput("result", Sdf.ValueTypeNames.Float2)

            diff_tex = UsdShade.Shader.Define(stage, mat_path + "/diffTex")
            diff_tex.CreateIdAttr("UsdUVTexture")
            diff_tex.CreateInput(
                "file", Sdf.ValueTypeNames.Asset).Set(BARK_TEX)
            diff_tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
            diff_tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
            diff_tex.CreateInput(
                "st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                uv.ConnectableAPI(), "result")
            diff_tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)

            shd = UsdShade.Shader.Define(stage, mat_path + "/pbr")
            shd.CreateIdAttr("UsdPreviewSurface")
            # Primary: textured bark.  If texture load fails in headless the
            # renderer falls back to diffuseColor; set it to bark-brown so
            # even without the texture the tree reads as brown, not white.
            shd.CreateInput("diffuseColor",
                            Sdf.ValueTypeNames.Color3f).ConnectToSource(
                diff_tex.ConnectableAPI(), "rgb")
            shd.CreateInput("roughness",  Sdf.ValueTypeNames.Float).Set(0.88)
            shd.CreateInput("metallic",   Sdf.ValueTypeNames.Float).Set(0.0)
            mat.CreateSurfaceOutput().ConnectToSource(shd.ConnectableAPI(), "surface")

            # Override material binding on the mesh prim
            mesh_ovr = stage.OverridePrim("/gant_tree/gant_tree_01")
            UsdShade.MaterialBindingAPI(mesh_ovr).Bind(mat)

            stage.GetRootLayer().Save()
            print(f"[isaac] patched tree USD → {OUT}", flush=True)
            return OUT
        except Exception as e:
            print(f"[isaac] tree USD patch failed: {e}", flush=True)
            return None

    def _setup_procedural_outdoor(self, stage, extent: float = 250.0,
                                   n_trees: int = 80) -> None:
        """Build a forest using the real gant_tree mesh with UsdPreviewSurface materials.

        The gant_tree_inst.usd from NVIDIA's drive-sim staging contains a real 664-face
        tree mesh with UV-mapped bark texture.  Its MDL/SimPBR shader is replaced by a
        UsdPreviewSurface wrapper pointing to the same local texture files — these
        render correctly in headless Isaac.  If the drive-sim assets are not present,
        falls back to a dark green ground plane (no geometry trees).

        Each instance is placed at a random XY position with varied Z-rotation and
        scale so the forest doesn't look like a tiled copy.  The gant_tree asset is
        ~28 m wide at 1× scale (it is a billboard-cross group); we scale it to
        ~0.35× to get individual trees ~8–12 m wide.
        """
        try:
            from pxr import UsdGeom, UsdShade, Gf, Sdf, Vt
            import random as _rnd
            import math as _math
            _rnd.seed(42)
            h = extent / 2.0

            # ── ground: dark mossy forest floor ─────────────────────────────────
            gnd = UsdGeom.Mesh.Define(stage, "/World/_PF_Ground")
            gnd.CreatePointsAttr(Vt.Vec3fArray([
                Gf.Vec3f(-h, -h, 0), Gf.Vec3f( h, -h, 0),
                Gf.Vec3f( h,  h, 0), Gf.Vec3f(-h,  h, 0)]))
            gnd.CreateFaceVertexCountsAttr(Vt.IntArray([4]))
            gnd.CreateFaceVertexIndicesAttr(Vt.IntArray([0, 1, 2, 3]))
            gnd.CreateDoubleSidedAttr(True)
            gnd.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 1)] * 4))
            gnd.SetNormalsInterpolation("vertex")
            gnd_mat = UsdShade.Material.Define(stage, "/World/_PF_GndMat")
            gnd_shd = UsdShade.Shader.Define(stage, "/World/_PF_GndMat/s")
            gnd_shd.CreateIdAttr("UsdPreviewSurface")
            gnd_shd.CreateInput("diffuseColor",
                                Sdf.ValueTypeNames.Color3f).Set(
                Gf.Vec3f(0.08, 0.14, 0.05))   # dark forest floor
            gnd_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.97)
            gnd_mat.CreateSurfaceOutput().ConnectToSource(
                gnd_shd.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI(gnd.GetPrim()).Bind(gnd_mat)

            # ── real tree mesh ───────────────────────────────────────────────────
            tree_usd = self._patch_tree_usd()
            if not tree_usd:
                print("[isaac] No tree USD — forest ground only", flush=True)
                return

            UsdGeom.Xform.Define(stage, "/World/_PF_Trees")
            for i in range(n_trees):
                x = _rnd.uniform(-h * 0.88, h * 0.88)
                y = _rnd.uniform(-h * 0.88, h * 0.88)
                # keep spawn pad clear
                if abs(x) < 14 and abs(y) < 14:
                    x += 20 * (1 if x >= 0 else -1)

                # gant_tree bbox: 28.6 m wide × 6.4 m tall at 1× scale.
                # Scale 0.7–1.0 → 4.5–6.4 m tall trees, 20–28 m wide groups.
                # The wide footprint is expected (it's a street-tree row asset);
                # each instance is rotated so the billboard faces different angles.
                sc = _rnd.uniform(0.70, 1.00)
                # Random yaw — 0–180° covers both billboard planes in the cross
                yaw_deg = _rnd.uniform(0, 180)

                tree_path = f"/World/_PF_Trees/t{i:03d}"
                xform = UsdGeom.Xform.Define(stage, tree_path)
                xform.GetPrim().GetReferences().AddReference(tree_usd)
                api = UsdGeom.XformCommonAPI(xform.GetPrim())
                api.SetTranslate(Gf.Vec3d(x, y, 0.0))
                api.SetRotate(Gf.Vec3f(0.0, 0.0, yaw_deg))
                api.SetScale(Gf.Vec3f(sc, sc, sc))

            print(
                f"[isaac] forest: {n_trees} real tree meshes (gant_tree) "
                f"extent={extent} m", flush=True)
        except Exception as e:
            print(f"[isaac] forest setup failed: {e}", flush=True)

    def _bind_terrain_material(self, stage) -> None:
        """Assign a simple green grass material to the first Mesh named Terrain."""
        try:
            from pxr import UsdShade, Sdf, Gf as _Gf, UsdGeom
            # The scene USD is referenced at /World/Scene, so terrain is at
            # /World/Scene/World/Terrain — search by traversal rather than hardcoded path.
            terrain = None
            for prim in stage.Traverse():
                if prim.GetTypeName() == "Mesh" and "Terrain" in prim.GetName():
                    terrain = prim
                    break
                if prim.GetTypeName() == "Mesh" and not terrain:
                    terrain = prim   # fallback: first mesh (the terrain is usually first)
            if not terrain:
                print("[isaac] terrain material: no Mesh found in stage", flush=True)
                return
            print(f"[isaac] binding terrain material to {terrain.GetPath()}", flush=True)
            mat_path  = "/World/_IshmaeL_TerrainMat"
            shad_path = mat_path + "/shader"
            mat  = UsdShade.Material.Define(stage, mat_path)
            shad = UsdShade.Shader.Define(stage, shad_path)
            shad.CreateIdAttr("UsdPreviewSurface")
            shad.CreateInput("diffuseColor",
                             Sdf.ValueTypeNames.Color3f).Set(_Gf.Vec3f(0.22, 0.45, 0.15))
            shad.CreateInput("roughness",   Sdf.ValueTypeNames.Float).Set(0.85)
            shad.CreateInput("specularColor",
                             Sdf.ValueTypeNames.Color3f).Set(_Gf.Vec3f(0.02, 0.04, 0.01))
            mat.CreateSurfaceOutput().ConnectToSource(
                shad.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI(terrain).Bind(mat)
            # The wilderness terrain author_wilderness.py uses a face winding
            # that puts normals pointing DOWNWARD.  Cameras above see the backface
            # (which is culled → black).  Setting doubleSided=True fixes this.
            UsdGeom.Mesh(terrain).CreateDoubleSidedAttr(True)
            print("[isaac] terrain material bound (grass green, doubleSided=True)",
                  flush=True)
        except Exception as e:
            print(f"[isaac] terrain material binding failed: {e}", flush=True)

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

        # Use align_vectors([fwd, up], [[1,0,0], [0,0,1]]) — empirically verified to
        # produce correct camera orientation in Isaac Sim.  Guard against the
        # near-vertical degenerate case (fwd ≈ [0,0,±1]) by ensuring there is always
        # at least 15 % horizontal component before calling align_vectors.
        horiz = float(np.sqrt(fwd[0]**2 + fwd[1]**2))
        if horiz < 0.15:
            # Add a small +X nudge and re-normalise so align_vectors is well-conditioned
            fwd = fwd + np.array([0.15, 0.0, 0.0])
            fwd = fwd / float(np.linalg.norm(fwd))
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
