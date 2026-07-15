"""Render photorealistic Isaac Sim videos of the fixed-wing (delta-wing) aircraft flying
instructed missions with live obstacle avoidance, in an urban and a forest environment.

Navigation: the trained mid-level pilot (policy_fw.onnx, exported by train_rl_fixedwing.py)
drives velocity/yaw via LearnedPlanner, exactly as the quad demos in render_session1.py do —
target + a forward depth-grid in, [vx,vy,vz,yaw_rate] out. Obstacle geometry is declared once
as Box3D obstacles (world3d.depth_grid) and used for BOTH the sensed depth grid and the visible
Isaac props placed at the same positions, so what the aircraft "sees" matches what's rendered.

Physical realism the raw network output doesn't enforce on its own is applied here explicitly
(coordinated-turn clamp): forward speed floored at stall speed (can't hover/stop), yaw rate
bank-limited by current speed, climb bounded by a fixed flight-path angle — mirrors
KinematicWorld.integrate's COORDINATED_TURN_3D branch (team_world.py / l5_core.py).

"Instructions" are short natural-language mission descriptions mapped to a waypoint list (no
live NLU call here — this renders offline video, not an interactive session); each is logged
before its flight runs.

Environments:
  urban  — NVIDIA Rivermark (photoreal outdoor town/plaza, Isaac 5.1 standard content) with two
           declared obstacles (radio masts) along the patrol route.
  forest — bare ground + ~18 real PBR conifers (Assets/Vegetation/Trees) scattered along a
           transect, sensed as their approximate canopy bounding boxes.

Run on hoopoe (each environment is a fresh Isaac boot; run urban/forest on separate GPUs to
parallelize with WARP_CUDA_DEVICES):
    cd ~/astral-training
    WARP_CUDA_DEVICES=0 /opt/ml/isaac-sim-env/bin/python3 \
        -m eco.drone.training.render_fixedwing_demo --mission urban --out /tmp/fw_videos
    WARP_CUDA_DEVICES=1 /opt/ml/isaac-sim-env/bin/python3 \
        -m eco.drone.training.render_fixedwing_demo --mission forest --out /tmp/fw_videos
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
_repo = _here.parent.parent.parent
for p in (_repo / "eco" / "drone" / "common", _repo / "eco" / "drone" / "sim", str(_here)):
    sys.path.insert(0, str(p))

from reactive_planner import LearnedPlanner            # noqa: E402
from world3d import Box3D, depth_grid, min_dist_to_boxes  # noqa: E402
from vehicle_class import get_class                    # noqa: E402
from fw_contract import VEHICLE_FIXEDWING              # noqa: E402

FW = get_class("fixedwing")

DT = 0.1
PHYS_DT = 1.0 / 240.0
STEPS_PER_TICK = int(round(DT / PHYS_DT))
FPS = 24
WAYPOINT_REACH_M = 15.0     # can't hover to "arrive" precisely at speed; advance within this radius
MAX_TICKS_PER_LEG = 300     # 30 s/leg cap -- bounds worst-case RTX render wall-clock per leg
SENSE_MAX = FW.sense_range_m  # 80 m — long-range forward depth grid
G = 9.81
MAX_BANK_RAD = math.radians(40.0)  # visual bank cap for a coordinated turn
RENDER_SETTLE_STEPS = 4  # extra static render passes per tick so RTX resolves thin foliage

RIVERMARK = ("https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
             "Assets/Isaac/5.1/Isaac/Environments/Outdoor/Rivermark/rivermark.usd")
FLAT_TERRAIN = ("https://omniverse-content-production.s3-us-west-2.amazonaws.com/"
                "Assets/Isaac/5.1/Isaac/Environments/Terrains/flat_plane.usd")
# Local procedural heightfield (no mountainous Isaac standard-content terrain exists --
# confirmed by listing the Environments/Terrains bucket: only flat/rough/slope/stairs planes).
# Hand-authored the same way as delta_wing.usda. terrain_height() below must exactly match
# the height() function used to generate this mesh (see gen_terrain.py) so tree/obstacle
# z-placement and flight altitude agree with the actual ground surface.
MOUNTAIN_TERRAIN = str(Path(__file__).resolve().parent.parent / "sim" / "assets" / "mountain_terrain.usda")
TREES_BASE = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Vegetation/Trees"


def terrain_height(x: float, y: float) -> float:
    """Ground elevation at (x,y). MUST match gen_terrain.py's height() exactly -- this is
    what places trees on the surface and keeps the flight path above the ground."""
    ridge = 26.0 * (abs(y) / 60.0) ** 1.6
    ridge_ripple = 6.0 * math.sin(x / 22.0) * math.cos(y / 30.0)
    peaks = 5.0 * math.sin(x / 9.0 + y / 13.0) + 3.0 * math.sin(x / 5.0 - y / 7.0)
    return max(0.0, ridge + ridge_ripple + peaks)
# Red_Cedar.usd and Norway_Spruce.usd have degenerate/empty bounding boxes on this Isaac
# content build (confirmed via an instrumented placement check: valid=True but
# ComputeWorldBound returns the FLT_MAX "empty" sentinel) -- they place with zero visible
# geometry. Dropped rather than spending tree slots on invisible placements.
CONIFER_SPECIES = ["Douglas_Fir.usd", "White_Pine.usd", "Colorado_Spruce.usd",
                   "Eastern_Hemlock.usd"]


def _urban_mission():
    """Patrol the Rivermark plaza area at safe altitude above rooftops, with two declared
    mast obstacles along the route requiring a visible avoidance maneuver."""
    start = (-90.0, -70.0, 42.0)
    waypoints = [
        (-30.0, -60.0, 42.0),
        (10.0, -20.0, 45.0),
        (40.0, 20.0, 42.0),
        (0.0, 50.0, 45.0),
        (-60.0, 10.0, 42.0),
    ]
    # Two "radio mast" obstacles placed directly on-route, offset so neither is a degenerate
    # dead-ahead symmetric approach (see l5_core._avoid_radius_for commit note on that failure mode).
    obstacles = [(-10.0, -40.0, 30.0, 1.5, 1.5, 30.0),
                 (25.0, 5.0, 34.0, 1.5, 1.5, 34.0)]
    return {
        "instruction": "Patrol the plaza district, staying clear of the radio masts.",
        "environment": RIVERMARK,
        "start": start,
        "waypoints": waypoints,
        "obstacles": obstacles,
        "trees": [],
        "chase_offset": (-26.0, 0.0, 11.0),   # trails behind (in +fwd body frame at spawn yaw), above
    }


def _forest_mission(seed: int = 0):
    """A ~110 m low transect over a mountain valley's conifer stand, forcing real avoidance.

    Render-distance constraint (measured directly, not assumed): a tree rendered through
    IsaacVehicleBridge's offscreen vantage camera is crisp at 8 m and INVISIBLE by 20 m --
    confirmed with a static multi-distance test (8/20/40/70/110 m: only the 8 m tree
    rendered). This is why the earlier wide 44 m-lateral scatter read as almost empty no
    matter how many trees were placed -- most were simply beyond render range of a chase
    camera trailing close behind the plane. Trees are now kept within +-10 m of the flight
    line so whichever ones the plane is actually near stay inside that render-safe radius;
    the along-track (x) extent can stay long since distant-ahead trees coming into range as
    the plane approaches is the normal/expected look of a flythrough.

    Two tree populations: "obstacles" (close to the flight corridor, matched 1:1 with a
    Box3D so the sensed depth grid agrees with what's rendered there) and a denser
    "background" scatter that's purely visual -- real forests aren't a handful of trees, and
    the background fill is what actually reads as a forest on camera without changing what
    the aircraft is avoiding. Both sit on the local terrain surface (terrain_height(x,y)).
    """
    rng = np.random.default_rng(seed)
    # Trees are sensed/avoided (Box3D obstacles); the terrain mesh itself is NOT sensed by the
    # policy at all. AGL_CLEARANCE is held above the LOCAL terrain height each tick (see
    # run_mission's lookahead alt_target), so cruise altitude tracks the ground like real
    # terrain-following flight. 9m keeps the aircraft mid original-canopy (see add_obstacle_tree
    # for why the SENSED canopy height is deliberately NOT doubled even though the rendered
    # tree is).
    AGL_CLEARANCE = 9.0
    # Corridor extended from +-58 (~116m) to +-100 (~200m) so the video runs well past 3s
    # instead of ending almost as soon as it starts -- at this policy's typical cruise speed
    # (~12 m/s, close to stall floor) the old corridor covered in ~95 ticks =~ 4s of video at
    # FPS=24; 200m takes roughly 165+ ticks =~ 7s. Capped at +-100, not further: the hand-
    # authored terrain mesh only covers x in [-110, 109] -- flying past that would put the
    # aircraft's terrain-following target (a pure math function, no x bound) over ground that
    # was never actually rendered.
    start = (-100.0, 0.0, terrain_height(-100.0, 0.0) + AGL_CLEARANCE)
    waypoints = [(100.0, 0.0, terrain_height(100.0, 0.0) + AGL_CLEARANCE)]
    trees = []
    obstacles = []

    def add_obstacle_tree(x, y, sp):
        gz = terrain_height(x, y)
        trees.append((x, y, gz, sp))
        # "Twice as tall" is a RENDERING change only (PointInstancer scale, in build_scene) --
        # the sensed collision box below is deliberately left at its original size. The
        # aircraft's flight envelope/sensing was validated at this exact box size; changing what
        # it senses is a different (much riskier) change than changing what's drawn on screen.
        obstacles.append((x, y, gz + 9.0, 2.5, 2.5, 7.0))

    # ONE tree per position, not a 3-tree row -- a packed row reads as "pick a lane through a
    # wall," a single isolated tree reads as "swerve around this specific tree." Halved again
    # (4, was 8) by reusing the ~24m spacing from the earlier 4-cluster layout -- that spacing +
    # the +-2.5m alternating offset is the combination already validated safe for this policy
    # checkpoint (a wider amplitude with fewer, more widely-spaced encounters reproducibly broke
    # it -- see git history for the telemetry), so sparsify by dropping trees, not by touching
    # the geometry that's proven to work.
    #
    # First tree kept 16m from start (was only 8m, at the old shorter corridor): starting
    # dead-on-axis too close left no room to actually turn -- at this airframe's turn radius
    # (~20m at cruise speed), a clean +-2.5m dodge needs on the order of 13m+ of standoff even
    # reacting at max authority from tick 0, so an 8m gap was geometrically unavoidable, not an
    # avoidance-tuning problem (confirmed via telemetry: avoidance was already saturated at max
    # yaw rate from t=0 and still grazed it). Extended to 7 trees (was 4) at the same validated
    # 24m spacing to fill the now-longer corridor, ending 40m clear of the waypoint (goal-
    # seeking behavior overrides avoidance margin right at leg completion).
    single_tree_x = [-84.0, -60.0, -36.0, -12.0, 12.0, 36.0, 60.0]
    for i, cx in enumerate(single_tree_x):
        x = cx + rng.uniform(-2.0, 2.0)
        y = (-2.5 if i % 2 == 0 else 2.5) + rng.uniform(-1.0, 1.0)
        add_obstacle_tree(x, y, CONIFER_SPECIES[i % len(CONIFER_SPECIES)])

    # Dense background fill -- visual only, not sensed/avoided -- so the corridor reads as an
    # actual forest stand rather than a chain of isolated trees on bare ground. Rendered via a
    # UsdGeom.PointInstancer (many copies of a repeated asset is exactly what it's for) rather
    # than one add_reference_to_stage() per tree, which only reliably rendered the first few
    # dozen instances the renderer ever resolved regardless of tree count.
    #
    # Covers the whole visible mountainside (+-105 x, +-55 y), not just a +-20m strip around the
    # flight line -- a narrow strip left most of the mountain (everything the chase/FPV camera
    # sees beyond the immediate corridor) reading as bare terrain. CELL widened progressively
    # (2.2 -> 3.1 -> 4.4 -> 6.2) for a visibly sparser forest each time (count scales as
    # 1/CELL^2, so each step roughly halves the background tree count again).
    #
    # BUG FIX: background trees are visual-only and were previously excluded only near the
    # SENSED obstacles (the `abs(x-ox)<3.5` check below) -- nothing stopped one from landing
    # directly in the default flight lane, where the aircraft flies with no avoidance applied
    # at all (only the sensed obstacles trigger dodging). That produced an aircraft visually
    # flying straight through a "tree" with no collision ever registered, because the tree was
    # never a real obstacle to begin with. Excluding a fixed band around y=0 is a blunt
    # instrument -- +-5m and then +-15m both proved too narrow, because the policy's avoidance
    # swerves saturate at max yaw rate for a stretch (confirmed via telemetry: yr pinned at 0.6
    # rad/s through the whole turn), so tightening the centerline-seeking bias in run_mission
    # doesn't shrink the excursion once it's already turning as fast as the airframe allows --
    # only the total heading change integrated over the swerve does, and that's set by how far
    # off-axis the first reaction commits, not by any gain in this script. +-28m matches the
    # worst excursion actually measured (24.5m) with margin. This does thin out decorative
    # cover near the corridor more than earlier passes -- if that reads as too bare, the real
    # fix is retraining a policy with tighter avoidance, not chasing the exclusion radius higher.
    CELL = 6.2
    for gx in np.arange(-105.0, 105.0, CELL):
        for gy in np.arange(-55.0, 55.0, CELL):
            x = float(gx + rng.uniform(-0.7, 0.7))
            y = float(gy + rng.uniform(-0.7, 0.7))
            if abs(y) < 28.0:
                continue  # keep the flight corridor itself free of unavoidable decorative trees
            if any(abs(x - ox) < 3.5 and abs(y - oy) < 3.5 for ox, oy, *_ in obstacles):
                continue  # don't overlap a sensed obstacle tree's footprint
            trees.append((x, y, terrain_height(x, y),
                         CONIFER_SPECIES[rng.integers(0, len(CONIFER_SPECIES))]))

    return {
        "instruction": "Fly the transect through the mountain valley forest and avoid the trees.",
        "environment": MOUNTAIN_TERRAIN,
        "start": start,
        "waypoints": waypoints,
        "obstacles": obstacles,
        "trees": trees,
        # 3/4 rear-side angle. +64 (matching the full 4x tree scale) put the camera SO high
        # above the aircraft that the plane became an unrecognizable speck -- a good "never
        # clip a tree" answer but a bad chase shot. Trees are now sparse (4 sensed + thin
        # background), so the odds of a specific tall tree sitting exactly in the camera's path
        # are much lower than when the forest was dense; +36 stays close enough to read as a
        # real chase cam while still clearing most nearby canopy.
        "chase_offset": (-22.0, 12.0, 36.0),
        "agl_clearance": AGL_CLEARANCE,  # terrain-following target height; None = absolute Z
    }


MISSIONS = {"urban": _urban_mission, "forest": _forest_mission}


def coordinated_turn_clamp(vx: float, vy: float, vz: float, yaw_rate: float, dt: float,
                           pos_z: float | None = None, alt_target: float | None = None):
    """Post-process a raw planner action into physically-valid fixed-wing motion. Mirrors
    KinematicWorld.integrate's COORDINATED_TURN_3D branch: forward speed floored at stall
    speed (never zero/reverse), yaw rate bank-limited by current speed (~1/v falloff), climb
    bounded by a fixed flight-path angle. vy is dropped (no sideslip).

    If pos_z/alt_target are given, holds altitude against alt_target. Ground clearance is a
    hard safety requirement the policy has no way to satisfy on its own -- its state has no
    terrain input at all, only sensed tree obstacles -- so whenever the aircraft is off target
    by more than 1m in EITHER direction, this OVERRIDES the network's vz and moves at the max
    rate the airframe allows, rather than blending 50/50 with it. A 50/50 blend (the original
    version of this function) is too weak against locally steep terrain: this mountain's
    ripple/peak terms can rise faster than the aircraft's climb-angle limit allows to react to
    instantaneously, so the target passed in must ALSO be a forward-looking ceiling (see
    run_mission), not just the height directly underneath -- the two fixes only work together.
    Symmetric climb/descend authority matters: an earlier asymmetric version (fast climb, only
    a gentle blended descent) ratcheted altitude upward every time it cleared a rise and never
    came back down, so the aircraft climbed away from the terrain/canopy for the whole flight
    instead of tracking it. Only within 1m of target does the network's own vz get any say.
    """
    v = max(FW.min_speed_mps, min(FW.max_speed_mps, vx))
    v_ref = max(FW.min_speed_mps, 1e-3)
    yaw_limit = FW.max_yaw_rate_radps * (v_ref / max(v, v_ref))
    yr = max(-yaw_limit, min(yaw_limit, yaw_rate))
    climb_cap = v * math.tan(FW.max_climb_angle_rad)
    if pos_z is not None and alt_target is not None:
        err = alt_target - pos_z
        if abs(err) > 1.0:
            vz = climb_cap if err > 0 else -climb_cap
        else:
            vz_hold = max(-climb_cap, min(climb_cap, err * 0.5))
            vz = 0.5 * vz + 0.5 * vz_hold
    vzc = max(-climb_cap, min(climb_cap, vz))
    return v, 0.0, vzc, yr


def lateral_avoid_bias(px, py, yaw, boxes, avoid_radius=26.0):
    """Deterministic repulsion away from the nearest obstacle ahead, as a safety net over the
    learned policy's own lateral avoidance. This checkpoint is an early ~47%-success network;
    at cruise altitude it sits at the exact vertical center of every tree's canopy by design
    (AGL_CLEARANCE matches the canopy midpoint), so climbing can never dodge a tree here --
    avoidance is ENTIRELY lateral, and relying solely on the network produced flights that
    swerved once, drifted for a while, then flew straight into a later tree it never reacted
    to (observed directly on video). Returns a signed fraction in [-1, 1] of max yaw rate (0 if
    nothing relevant is within range); added to the policy's own yaw_rate, not a full override,
    so the network still drives normal cruise/steering.
    """
    best_d = math.inf
    best_left = 0.0
    c, s = math.cos(-yaw), math.sin(-yaw)
    for b in boxes:
        dx, dy = b.cx - px, b.cy - py
        fwd = c * dx - s * dy
        left = s * dx + c * dy
        if fwd < 0.5:
            continue  # behind/alongside -- already past it, can't usefully react
        d = math.hypot(fwd, left)
        if d < best_d:
            best_d, best_left = d, left
    if best_d > avoid_radius:
        return 0.0
    strength = 1.0 - best_d / avoid_radius
    # Turn AWAY from the obstacle: if it's to the left (left>0), yaw right (negative bias).
    return -math.copysign(strength, best_left) if best_left != 0 else strength


def body_target(goal_world, pos, yaw):
    dx, dy, dz = goal_world[0] - pos[0], goal_world[1] - pos[1], goal_world[2] - pos[2]
    c, s = math.cos(-yaw), math.sin(-yaw)
    return (c * dx - s * dy, s * dx + c * dy, dz)


def world_vel(bvx, bvy, bvz, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return (c * bvx - s * bvy, s * bvx + c * bvy, bvz)


def build_scene(bridge, mission):
    from omni.isaac.core.utils.stage import add_reference_to_stage
    import omni.usd
    from pxr import UsdGeom, Gf, Sdf

    if mission["trees"]:
        # UsdGeom.PointInstancer, not one add_reference_to_stage() per tree. Per-tree
        # references is the "obvious" approach and DOES produce valid, correctly-positioned
        # prims (confirmed via an instrumented bbox check across the whole tree list) -- but
        # on this Isaac build only the first handful the renderer ever resolves actually draw;
        # every tree beyond that stays invisible for the rest of the video regardless of tree
        # count (tested at 51, 245, 313 -- same cutoff each time), camera distance, or extra
        # render-settle passes. PointInstancer is the purpose-built USD mechanism for many
        # copies of a repeated asset and renders reliably at this count (confirmed).
        stage = omni.usd.get_context().get_stage()
        species = sorted({sp for *_, sp in mission["trees"]})
        proto_paths = []
        for i, sp in enumerate(species):
            p = f"/World/TreeProtos/proto_{i}"
            add_reference_to_stage(usd_path=f"{TREES_BASE}/{sp}", prim_path=p)
            proto_paths.append(p)
        bridge.step_simulation(n_steps=5, render=False)

        instancer = UsdGeom.PointInstancer.Define(stage, "/World/Forest")
        instancer.CreatePrototypesRel().SetTargets([Sdf.Path(p) for p in proto_paths])
        sp_to_idx = {sp: i for i, sp in enumerate(species)}
        positions = [Gf.Vec3f(x, y, z) for x, y, z, sp in mission["trees"]]
        proto_indices = [sp_to_idx[sp] for *_, sp in mission["trees"]]
        instancer.CreatePositionsAttr().Set(positions)
        instancer.CreateProtoIndicesAttr().Set(proto_indices)
        instancer.CreateOrientationsAttr().Set([Gf.Quath(1, 0, 0, 0)] * len(positions))
        # Height-only scale, x/y footprint unchanged -- rendering-only, doubled again each time
        # ("twice as tall" x2 => 4x original). The sensed collision box in add_obstacle_tree is
        # deliberately left untouched throughout: it's the exact size this policy checkpoint was
        # validated against, and enlarging it previously pushed the aircraft's state out of
        # anything the network had seen, causing an unrecovering divergence (see git history).
        instancer.CreateScalesAttr().Set([Gf.Vec3f(1, 1, 4) for _ in positions])

    for j, (cx, cy, cz, hx, hy, hz) in enumerate(mission["obstacles"]):
        if mission["trees"]:
            continue  # trees ARE the obstacle geometry; don't also draw a box over them
        bridge.add_obstacle_box(f"mast_{j}", [cx, cy, cz], [hx, hy, hz], color=(0.75, 0.15, 0.1))


def run_mission(bridge, planner, drone_id, mission, out_dir, tag):
    boxes = [Box3D(*o) for o in mission["obstacles"]]
    obstacle_xy = {(round(cx, 1), round(cy, 1)) for cx, cy, *_ in mission["obstacles"]}
    bg_trees = [(x, y) for x, y, *_ in mission["trees"]
                if (round(x, 1), round(y, 1)) not in obstacle_xy]
    dbg_min_bg_dist = [math.inf]
    dbg_max_py = [0.0]
    start = mission["start"]
    yaw0 = math.atan2(mission["waypoints"][0][1] - start[1], mission["waypoints"][0][0] - start[0])
    bridge.set_drone_pose(drone_id, list(start), yaw_rad=yaw0)
    planner.reset()

    bridge.add_vantage_camera("chase",
                              position=[start[0] - 20, start[1], start[2] + 5],
                              look_at=list(start), resolution=(1920, 1080), hfov_deg=90.0)

    print(f"[{tag}] instruction: {mission['instruction']}", flush=True)

    pos = list(start)
    yaw = yaw0
    roll = 0.0
    frames_fpv, frames_chase = [], []
    wp_idx = 0
    total_ticks = 0
    collided = False
    ox, oy, oz = mission["chase_offset"]

    while wp_idx < len(mission["waypoints"]) and total_ticks < MAX_TICKS_PER_LEG * len(mission["waypoints"]):
        goal = mission["waypoints"][wp_idx]
        st = bridge.get_drone_state(drone_id)
        pos = [float(v) for v in st["position"]]
        px, py, pz = pos
        fan = depth_grid(px, py, pz, yaw, boxes, max_range=SENSE_MAX)
        clear = min_dist_to_boxes(px, py, pz, boxes) if boxes else SENSE_MAX
        if boxes and clear < FW.radius_m + 0.5:
            collided = True
        dbg_max_py[0] = max(dbg_max_py[0], abs(py))
        if bg_trees:
            d = min(math.hypot(px - tx, py - ty) for tx, ty in bg_trees)
            dbg_min_bg_dist[0] = min(dbg_min_bg_dist[0], d)
        tgt = body_target(goal, pos, yaw)

        agl = mission.get("agl_clearance")
        if agl is not None:
            # Forward-looking ceiling, not just the height directly underneath: this terrain's
            # ripple/peak terms can rise faster over the next few meters than the climb-angle
            # limit lets the aircraft react to, so target the highest point within a ~60m look-
            # ahead cone along the current heading. 0.366*60 =~ 22m of achievable climb over
            # that distance comfortably covers this terrain's rises when paired with the
            # authoritative (non-blended) climb in coordinated_turn_clamp.
            fx, fy = math.cos(yaw), math.sin(yaw)
            ceiling = max(terrain_height(px + fx * d, py + fy * d) for d in (0.0, 15.0, 30.0, 45.0, 60.0))
            alt_target = ceiling + agl
        else:
            alt_target = goal[2]
        plan = planner.step(tgt, depth_fan=fan, altitude_m=pz, dt=DT)
        avoid = lateral_avoid_bias(px, py, yaw, boxes) if boxes else 0.0
        # Gentle pull back toward the corridor centerline (y=0, where the goal sits) whenever
        # not actively dodging. Without this, one avoidance swerve sends the aircraft 20-30m
        # off centerline and the network's own slow return-to-goal drift keeps it wide of every
        # subsequent tree for the rest of the corridor -- it "avoids" the first tree, then just
        # never gets close enough to react to the other seven. Strengthened (0.4->0.5 cap,
        # py/10->py/7 gain) after telemetry showed the old setting still let excursions reach
        # +-24.5m off centerline -- far outside the fixed-radius background-tree exclusion band
        # around the corridor, so the aircraft was visually clipping decorative trees that were
        # never sensed/avoided obstacles (confirmed directly: min_dist_to_background_tree=0.2m
        # in that run despite collided=False, since collision is only checked against sensed
        # obstacles). Tighter centerline pull keeps typical excursions smaller so the aircraft
        # reads as swerving around trees instead of wandering off across the whole corridor.
        centerline = max(-0.5, min(0.5, -py / 7.0))
        raw_yr = plan.yaw_rate + (avoid + centerline) * FW.max_yaw_rate_radps
        v, vy0, vzc, yr = coordinated_turn_clamp(plan.vx, plan.vy, plan.vz, raw_yr, DT,
                                                 pos_z=pz, alt_target=alt_target)

        wvx, wvy, wvz = world_vel(v, vy0, vzc, yaw)
        bridge.set_drone_velocity(drone_id, [wvx, wvy, wvz])
        yaw += yr * DT
        bridge.set_drone_yaw(drone_id, yaw)

        # Coordinated-turn bank angle for the visual: tan(bank) = v*yaw_rate/g. Sign: in this
        # body frame (fwd=+X, left=+Y, up=+Z) a positive roll about +X lifts the left wing, so
        # a left turn (yaw_rate > 0) needs a NEGATIVE roll (left wing down). Low-pass filtered
        # so it doesn't jitter tick-to-tick with an imperfect policy's noisy yaw_rate.
        target_roll = max(-MAX_BANK_RAD, min(MAX_BANK_RAD, -math.atan2(v * yr, G)))
        roll = 0.7 * roll + 0.3 * target_roll
        bridge.set_drone_roll(drone_id, roll)

        bridge.step_simulation(n_steps=STEPS_PER_TICK, render=True)

        # chase cam trails behind+above along current heading
        c, s = math.cos(yaw), math.sin(yaw)
        chase_pos = [px + c * ox - s * oy, py + s * ox + c * oy, pz + oz]
        bridge.update_vantage_camera("chase", chase_pos, [px, py, pz])

        # Extra render-only passes at this now-fixed pose before capturing. RTX accumulates
        # samples across frames to resolve fine/thin geometry (tree needles); with the camera
        # moving every tick it never converges and thin foliage silently disappears -- confirmed
        # by a controlled test where a static camera with many render passes showed full detail
        # at 8 m, while the in-flight loop showed almost no trees at any distance. A few static
        # passes per tick lets it converge without materially changing the flight physics (dt=0).
        for _ in range(RENDER_SETTLE_STEPS):
            bridge.step_simulation(n_steps=0, render=True)

        f_fpv = bridge.grab_frame(drone_id)
        f_chase = bridge.grab_vantage_frame("chase")
        if f_fpv is not None:
            frames_fpv.append(f_fpv)
        if f_chase is not None:
            frames_chase.append(f_chase)

        dist = math.sqrt(sum((goal[i] - pos[i]) ** 2 for i in range(3)))
        if dist < WAYPOINT_REACH_M:
            wp_idx += 1
        total_ticks += 1

    result = {"legs_completed": wp_idx, "legs_total": len(mission["waypoints"]),
              "collided": collided, "ticks": total_ticks,
              "frames_fpv": len(frames_fpv), "frames_chase": len(frames_chase),
              "max_abs_py": round(dbg_max_py[0], 1),
              "min_dist_to_background_tree": round(dbg_min_bg_dist[0], 1)}
    print(f"[{tag}] {result}", flush=True)

    from video_record import encode_mp4
    if frames_fpv:
        p = out_dir / f"{tag}_fpv.mp4"
        p.write_bytes(encode_mp4(frames_fpv, fps=FPS))
        print(f"[{tag}] wrote {p} ({len(frames_fpv)} frames)", flush=True)
    if frames_chase:
        p = out_dir / f"{tag}_chase.mp4"
        p.write_bytes(encode_mp4(frames_chase, fps=FPS))
        print(f"[{tag}] wrote {p} ({len(frames_chase)} frames)", flush=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission", choices=sorted(MISSIONS), required=True)
    ap.add_argument("--models-dir", default=str(Path(__file__).parent.parent / "models"))
    ap.add_argument("--onnx-name", default="policy_fw.onnx")
    ap.add_argument("--out", default="/tmp/fw_videos")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    from isaac_vehicle import IsaacVehicleBridge

    mission = (MISSIONS[args.mission](args.seed) if args.mission == "forest"
              else MISSIONS[args.mission]())

    DRONE = "fw_01"
    bridge = IsaacVehicleBridge(headless=True)
    bridge.setup(environment=mission["environment"], roster=[{"id": DRONE, "type": "fixedwing"}])
    bridge.ensure_camera(DRONE)
    bridge.add_lighting(sun_intensity=3000.0, dome_intensity=900.0)

    build_scene(bridge, mission)
    if mission["trees"]:
        # Cloud-hosted tree assets (Vegetation/Trees) stream in asynchronously after
        # add_reference_to_stage returns -- with 100+ of them requested at once, most
        # aren't resolved yet after only a few sim steps (confirmed via an overhead
        # diagnostic: nearly all trees were still missing after 80 steps). Give the
        # stage real time to settle before the recorded mission starts.
        print(f"[{args.mission}] settling {len(mission['trees'])} tree references...", flush=True)
        for _ in range(200):
            bridge.step_simulation(n_steps=1, render=True)

    planner = LearnedPlanner(
        models_dir=args.models_dir,
        onnx_name=args.onnx_name,
        reach_threshold=WAYPOINT_REACH_M,
        max_speed=FW.max_speed_mps,
        range_gate=(0.3, 500.0),
        vehicle=VEHICLE_FIXEDWING,
        stall_steps=200,
        stall_progress=0.05,
    )
    print(f"[{args.mission}] loaded {args.onnx_name}", flush=True)

    run_mission(bridge, planner, DRONE, mission, out_dir, args.mission)

    bridge.teardown()
    IsaacVehicleBridge.shutdown()
    print(f"[{args.mission}] done -> {out_dir}", flush=True)


if __name__ == "__main__":
    main()
