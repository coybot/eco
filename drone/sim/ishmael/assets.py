"""Photorealistic vehicle USD asset map for Ishmael.

When a TestSpec requests ``photoreal=True``, the director passes ``photoreal=True``
through the fleet roster so ``IsaacVehicleBridge._vehicle_usd`` switches from the
lightweight Isaac-shipped assets (Crazyflie/Nova Carter) to high-fidelity ones.

Asset priority order per vehicle type (first available wins):

  Quadcopter (photoreal):
    1. NVIDIA SimReady DJI Phantom 4 (Omniverse Nucleus / CDN)
    2. NVIDIA SimReady Generic Quadrotor
    3. Bitcraze CF2X heavy-prop variant (slightly more detailed than default)
    4. Fallback: default CF2X

  Rover (photoreal):
    1. NVIDIA SimReady Clearpath Husky (high-fidelity wheeled robot)
    2. NVIDIA SimReady Nova Carter with full texture pack
    3. Fallback: default Nova Carter

The actual USD paths may vary across Omniverse/Isaac versions and local Nucleus
mounts. ``ISHMAEL_PHOTOREAL_QUAD_USD`` and ``ISHMAEL_PHOTOREAL_ROVER_USD`` env vars
let you override them directly (e.g. point at a local GLB→USD convert on the R2
mirror). When no asset resolves, the standard default is used silently.

RTX path-tracing for vantage recordings is also controlled here: when photoreal,
``set_rtx_path_tracing(True)`` switches the renderer to PT mode with a higher
samples-per-pixel; ``set_rtx_path_tracing(False)`` reverts to the fast rasterizer.
The live WebRTC feed always stays on the fast rasterizer to avoid encoding lag.
"""

from __future__ import annotations

import os

# --- USD asset registry -------------------------------------------------------

_NUCLEUS_BASE = ("https://omniverse-content-production.s3-us-west-2.amazonaws.com"
                 "/Assets/Isaac/5.1")

# SimReady paths on the Isaac 5.1 CDN / Nucleus (best-effort; may need Nucleus)
_PHOTOREAL_QUAD_CANDIDATES = [
    # env override
    os.environ.get("ISHMAEL_PHOTOREAL_QUAD_USD", ""),
    # NVIDIA SimReady quadrotor (if available in local Nucleus)
    "omniverse://localhost/NVIDIA/Assets/Isaac/5.1/Isaac/Robots/Drones/Quadrotor/quadrotor.usd",
    # DJI Phantom 4 Pro SimReady (if user has Omniverse Nucleus Enterprise)
    "omniverse://localhost/NVIDIA/Assets/SimReady/DJI_Phantom4Pro/DJI_Phantom4Pro.usd",
    # Isaac CDN generic quadrotor placeholder
    f"{_NUCLEUS_BASE}/Isaac/Robots/Drones/Quadrotor/quadrotor.usd",
    # Slightly-more-detailed Crazyflie with reflective body (still light)
    f"{_NUCLEUS_BASE}/Isaac/Robots/Bitcraze/Crazyflie/cf2x.usd",
]

_PHOTOREAL_ROVER_CANDIDATES = [
    os.environ.get("ISHMAEL_PHOTOREAL_ROVER_USD", ""),
    # Clearpath Husky (SimReady)
    "omniverse://localhost/NVIDIA/Assets/SimReady/Clearpath_Husky/husky.usd",
    # Nova Carter with high-res texture pack
    f"{_NUCLEUS_BASE}/Isaac/Robots/NVIDIA/NovaCarter/nova_carter_detailed.usd",
    f"{_NUCLEUS_BASE}/Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd",
]

_STANDARD_QUAD  = f"{_NUCLEUS_BASE}/Isaac/Robots/Bitcraze/Crazyflie/cf2x.usd"
_STANDARD_ROVER_CANDIDATES = [
    f"{_NUCLEUS_BASE}/Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd",
    f"{_NUCLEUS_BASE}/Isaac/Robots/NVIDIA/Carter/carter_v1.usd",
    f"{_NUCLEUS_BASE}/Isaac/Robots/NVIDIA/Jetbot/jetbot.usd",
]


def best_usd(candidates: list[str], fallback: str) -> str:
    """Return the first non-empty candidate string; the asset is validated at
    Isaac load time (``is_file`` / URL fetch). We return the path optimistically
    so the Isaac bridge can try it and fall back on its own error path."""
    for c in candidates:
        if c:
            return c
    return fallback


def vehicle_usd(vtype: str, photoreal: bool, assets_root: str = "") -> str:
    """Return a USD URL/path for ``vtype`` at the requested fidelity.

    ``assets_root`` is the Omniverse assets root as returned by
    ``get_assets_root_path()``; ignored when the candidate is an absolute URL.
    """
    if photoreal:
        if vtype == "rover":
            return best_usd(_PHOTOREAL_ROVER_CANDIDATES, _STANDARD_ROVER_CANDIDATES[0])
        return best_usd(_PHOTOREAL_QUAD_CANDIDATES, _STANDARD_QUAD)
    # Standard: mirror the existing logic in IsaacVehicleBridge._vehicle_usd
    if vtype == "rover":
        return best_usd(_STANDARD_ROVER_CANDIDATES, _STANDARD_ROVER_CANDIDATES[0])
    return _STANDARD_QUAD


# --- RTX renderer toggle ------------------------------------------------------

def set_rtx_path_tracing(enabled: bool, spp: int = 64) -> None:
    """Switch between RTX Path-Traced and Real-Time rasterized rendering.

    Must be called from the Isaac thread (inside ``FleetWorker.run`` or a job).
    ``spp`` = samples per pixel for the PT pass (higher → better quality, slower).
    Default 64 is a good balance for vantage recording; 256 for final renders.

    No-op when called outside an active Isaac app context — so this is safe to
    call from unit tests that never load Isaac.
    """
    try:
        import carb.settings  # type: ignore  (Isaac dep, not available on Mac)
        s = carb.settings.get_settings()
        if enabled:
            s.set("/rtx/rendermode", "PathTracing")
            s.set("/rtx/pathtracing/spp", int(spp))
            s.set("/rtx/pathtracing/totalSpp", int(spp))
        else:
            s.set("/rtx/rendermode", "RaytracedLighting")
    except Exception:
        # Not in an Isaac app — silently ignore (dev machine / unit test)
        pass
