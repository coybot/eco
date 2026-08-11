"""State/action contract for the fixed-wing mid-level pilot.

Mirrors contract.py (the quad/rover contract) field-for-field so the ONNX interface and
Orin deploy path are unchanged — only the *vehicle* flag and depth range differ. Ray
geometry (DEPTH_COLS/DEPTH_ROWS/FOV) is reused as-is; only the clip range grows, because
a fixed-wing at cruise speed needs far more lookahead than the quad's 10 m (0.4 s of
reaction time at 25 m/s). Sensing hardware to hit this range on the real aircraft is a
separate, deferred problem (see plan) — this only affects the training/sim contract.

State (STATE_DIM, body frame, SI units before normalization): identical layout to
contract.py, with `vehicle` = VEHICLE_FIXEDWING and depth values in [0, DEPTH_MAX_FW].

Action (4-dim, body frame): [vx (fwd), vy (left, ignored by the coordinated-turn
integrator), vz (up, +climb), yaw_rate].
"""

from __future__ import annotations

import numpy as np

try:  # packaged (drone.training) in the repo; flat (training/ on path) for scripts like sim_validate.py
    from .contract import (  # noqa: F401  (re-exported for callers that only need fw_contract)
        DEPTH_COLS, DEPTH_ROWS, DEPTH_RAYS, DEPTH_HFOV, DEPTH_VFOV,
        RAY_DIRS, STATE_FIELDS, DEPTH_FIELDS, ACTION_FIELDS,
        STATE_DIM, ACTION_DIM, State, wrap_pi, normalize,
    )
except ImportError:
    from contract import (  # noqa: F401
        DEPTH_COLS, DEPTH_ROWS, DEPTH_RAYS, DEPTH_HFOV, DEPTH_VFOV,
        RAY_DIRS, STATE_FIELDS, DEPTH_FIELDS, ACTION_FIELDS,
        STATE_DIM, ACTION_DIM, State, wrap_pi, normalize,
    )

# --- vehicle kind (extends contract.py's VEHICLE_QUAD=0.0 / VEHICLE_ROVER=1.0) ---
VEHICLE_FIXEDWING = 2.0

# --- depth range: same ray geometry, much longer clip/"clear" value -------
DEPTH_MAX_FW = 80.0   # m: clip / "no obstacle" value (vs. 10.0 m for the quad)


def clear_grid_fw() -> np.ndarray:
    return np.full(DEPTH_RAYS, DEPTH_MAX_FW, dtype=np.float32)


def build_state_fw(
    target_xyz: tuple[float, float, float] | None,
    velocity_fwd_left_up: tuple[float, float, float] = (0.0, 0.0, 0.0),
    yaw_rate: float = 0.0,
    depth_fan=None,
    altitude_m: float | None = None,
) -> State:
    """Assemble a fixed-wing State. `depth_fan` is DEPTH_RAYS values clipped to DEPTH_MAX_FW."""
    import math

    if target_xyz is None:
        tf = tl = tu = 0.0
        dist = 0.0
        yaw_err = 0.0
    else:
        tf, tl, tu = (float(x) for x in target_xyz)
        dist = math.sqrt(tf * tf + tl * tl + tu * tu)
        yaw_err = wrap_pi(math.atan2(tl, tf)) if (tf or tl) else 0.0

    vf, vl, vu = (float(x) for x in velocity_fwd_left_up)
    if depth_fan is None:
        depth = clear_grid_fw()
    else:
        depth = np.asarray(depth_fan, dtype=np.float32).reshape(-1)
        assert depth.shape == (DEPTH_RAYS,), f"depth grid must be {DEPTH_RAYS} values"
        depth = np.clip(depth, 0.0, DEPTH_MAX_FW)
    altitude = 0.0 if altitude_m is None else float(altitude_m)

    return State(
        target_fwd=tf, target_left=tl, target_up=tu, dist=dist,
        vel_fwd=vf, vel_left=vl, vel_up=vu,
        yaw_err=yaw_err, yaw_rate=float(yaw_rate),
        altitude=altitude, vehicle=VEHICLE_FIXEDWING, depth=depth,
    )


# --- normalization (scaled for fixed-wing ranges/speeds, not the quad's) --
DEFAULT_STATE_MEAN_FW = np.zeros(STATE_DIM, dtype=np.float32)
DEFAULT_STATE_STD_FW = np.array(
    [150.0, 150.0, 40.0, 200.0, 25.0, 5.0, 5.0, 3.14159, 0.6, 120.0, 1.0]
    + [DEPTH_MAX_FW] * DEPTH_RAYS,
    dtype=np.float32,
)

assert DEFAULT_STATE_STD_FW.shape == (STATE_DIM,)
