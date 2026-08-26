"""Per-ray-group scan impairment — modelling what a real sensor suite actually returns.

Sim-only. On hardware the sensor *is* the noise source; this module exists so a sim score
predicts hardware instead of flattering it.

The existing ``TeamWorld.set_sensor_noise`` applies one uniform (range_std, dropout) pair to
every ray. That is the right shape for a single homogeneous modality, but wrong for a hybrid
suite: the Crazyflie's 50-ray scan is 45 monocular-depth rays (scale-ambiguous, latent,
frequently blind) followed by 5 laser ToF beams (metric, prompt, reliable). Averaging those
into one noise term would both flatter the camera and slander the ToF. So impairment is
declared per *ray group*, using the group spans that ``vehicle_class.ray_table`` already
publishes.

Three terms here are not just "more noise", and are the substance of the honesty claim:

``scale_bias_sigma``
    Monocular metric depth is **scale-ambiguous**: a network trained to predict metres from
    a single image gets relative structure right and absolute scale wrong, and the error is a
    slowly-varying *common factor* over the whole frame, not independent per-ray jitter. So
    this is drawn once per agent per episode and applied multiplicatively. It is the term
    that says out loud: we do not assume the hardware will solve monocular scale. On the real
    vehicle, ray 45 (front ToF) shares a direction with ray 22 (grid centre) precisely so
    this factor can be estimated online — see vehicle_class.Sensor.MONO_DEPTH_PLUS_TOF.

``latency_s``
    Held as a ring buffer of past scans rather than a shifted array. Each buffered scan was
    cast at the pose the agent actually had on that tick, so replaying an older one yields
    correct *pose-stale* semantics for free — the measurement is not merely late, it describes
    somewhere the vehicle no longer is. That is the failure mode that puts a drone into a wall.

``frame_dropout_p``
    Correlated, whole-group blindness: a low-texture wall or a dropped JPEG takes out all 45
    camera rays on the same tick, leaving the vehicle on 5 ToF beams. Distinct from per-ray
    ``dropout_p``, and much harder to fly through.

Deferred (hook present, not yet modelled): a VL53L1x is a ~27° cone, not a pencil ray, so one
beam really returns the minimum over a cone. Treating it as a single ray is optimistic about
coverage and pessimistic about detection. Raise ``tof_cone_subrays`` above 1 to model it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

try:
    from .vehicle_class import VehicleClass, ray_table
except ImportError:  # pragma: no cover - flat-install path
    from vehicle_class import VehicleClass, ray_table


@dataclass(frozen=True)
class GroupImpairment:
    """Degradation applied to one named ray group of a scan."""
    range_std_m: float = 0.0        # additive Gaussian, independent per ray, per tick
    dropout_p: float = 0.0          # independent per-ray miss → reports clear
    frame_dropout_p: float = 0.0    # whole group drops at once (correlated blindness)
    scale_bias_sigma: float = 0.0   # log-normal multiplicative bias, frozen per episode
    latency_s: float = 0.0          # measurement age (pose-stale, via ring buffer)
    tof_cone_subrays: int = 1       # reserved: model a finite beam cone (1 = pencil ray)


@dataclass(frozen=True)
class ScanImpairment:
    """Impairment per ray-group name, matching ray_table(sensor).groups."""
    groups: dict[str, GroupImpairment] = field(default_factory=dict)


class ScanImpairmentModel:
    """Stateful, seeded impairment. One instance per world; state is per agent id.

    State is per agent because the scale bias is per-episode-per-vehicle and the latency
    buffer is a history of that vehicle's own measurements.
    """

    def __init__(self, impairment: ScanImpairment, dt: float, seed: int = 0):
        self.impairment = impairment
        self.dt = float(dt)
        self._rng = np.random.default_rng(seed)
        self._scale: dict[str, dict[str, float]] = {}   # agent -> group -> factor
        self._hist: dict[str, list[np.ndarray]] = {}    # agent -> recent true scans

    def _scale_factor(self, agent_id: str, gname: str, sigma: float) -> float:
        """Per-episode multiplicative bias for one agent's ray group (drawn once)."""
        per_agent = self._scale.setdefault(agent_id, {})
        if gname not in per_agent:
            per_agent[gname] = (float(np.exp(self._rng.normal(0.0, sigma)))
                                if sigma > 0 else 1.0)
        return per_agent[gname]

    def reset(self, agent_id: str | None = None) -> None:
        """Forget per-episode state (new scale draw, empty latency history)."""
        if agent_id is None:
            self._scale.clear()
            self._hist.clear()
        else:
            self._scale.pop(agent_id, None)
            self._hist.pop(agent_id, None)

    def apply(self, agent_id: str, scan: np.ndarray, vclass: VehicleClass,
              max_d: float) -> np.ndarray:
        """Impair a freshly-cast scan. Returns a new array; input is not modified."""
        if not self.impairment.groups:
            return scan
        rt = ray_table(vclass.sensor)

        # Latency: keep this tick's *true* scan, then read back an older one per group.
        max_lat = max((g.latency_s for g in self.impairment.groups.values()), default=0.0)
        depth = int(round(max_lat / self.dt)) + 1 if max_lat > 0 else 1
        hist = self._hist.setdefault(agent_id, [])
        hist.append(scan.astype(np.float32).copy())
        if len(hist) > depth:
            del hist[:-depth]

        out = scan.astype(np.float32).copy()
        rng = self._rng
        for gname, start, stop in rt.groups:
            g = self.impairment.groups.get(gname)
            if g is None:
                continue
            n = stop - start

            # 1. Latency — replay this group from an older tick (pose-stale by construction).
            if g.latency_s > 0:
                back = int(round(g.latency_s / self.dt))
                if back > 0:
                    idx = max(0, len(hist) - 1 - back)
                    out[start:stop] = hist[idx][start:stop]

            # 2. Whole-group blindness (correlated) — short-circuits the rest.
            if g.frame_dropout_p > 0 and rng.random() < g.frame_dropout_p:
                out[start:stop] = max_d
                continue

            # 3. Multiplicative scale bias, frozen per episode (monocular ambiguity).
            if g.scale_bias_sigma > 0:
                out[start:stop] *= self._scale_factor(agent_id, gname, g.scale_bias_sigma)

            # 4. Additive per-ray range noise.
            if g.range_std_m > 0:
                out[start:stop] += rng.normal(0.0, g.range_std_m, size=n).astype(np.float32)

            # 5. Independent per-ray misses → report clear.
            if g.dropout_p > 0:
                out[start:stop][rng.random(n) < g.dropout_p] = max_d

        return np.clip(out, 0.0, max_d)


# --------------------------------------------------------------------------- presets
# Crazyflie 2.1 Brushless + AI-deck 1.1 (camera) + Multi-ranger (ToF).
# These are engineering estimates, NOT measured on the aircraft. Every number here should be
# replaced with a bench measurement once the hardware is flying — the point of the preset is
# that the sim degrades in the right *shape*, and that the shape is stated rather than assumed
# away. VERIFY ON HARDWARE.
CRAZYFLIE_DECKS = ScanImpairment(groups={
    # Monocular metric depth over a 324x244 grayscale JPEG stream on shared 2.4 GHz.
    "camera": GroupImpairment(
        range_std_m=0.08,        # per-ray depth jitter
        dropout_p=0.03,          # isolated bad pixels / reduction artefacts
        frame_dropout_p=0.10,    # low-texture wall or dropped frame → all 45 rays blind
        scale_bias_sigma=0.20,   # ~+/-20% 1-sigma absolute-scale error, frozen per episode
        latency_s=0.2,           # WiFi stream + offboard inference, ~2 ticks at 10 Hz
    ),
    # VL53L1x laser ToF: metric and prompt. mm precision, but a real cone and ambient-light
    # sensitive; modelled as a small additive term plus occasional misses.
    "tof": GroupImpairment(
        range_std_m=0.02,
        dropout_p=0.02,
        frame_dropout_p=0.0,
        scale_bias_sigma=0.0,    # laser ranging is metric — no scale ambiguity to model
        latency_s=0.0,
    ),
})

# Ablation: the camera fan is permanently blind, leaving only the 5 ToF beams. Answers
# "is the monocular-depth pipeline actually load-bearing, or is the Multi-ranger doing the
# work?" — which decides whether the AI-deck depth path is worth building for navigation
# (as opposed to for perception/VLM, where it is the only option).
CRAZYFLIE_TOF_ONLY = ScanImpairment(groups={
    "camera": GroupImpairment(frame_dropout_p=1.0),
    "tof": GroupImpairment(range_std_m=0.02, dropout_p=0.02),
})

# Ablation the other way: camera only, no ToF beams. Isolates how much the 4 m laser
# ranging contributes on its own.
CRAZYFLIE_CAMERA_ONLY = ScanImpairment(groups={
    "camera": GroupImpairment(range_std_m=0.08, dropout_p=0.03, frame_dropout_p=0.10,
                              scale_bias_sigma=0.20, latency_s=0.2),
    "tof": GroupImpairment(frame_dropout_p=1.0),
})

IMPAIRMENTS: dict[str, ScanImpairment | None] = {
    "none": None,
    "crazyflie_decks": CRAZYFLIE_DECKS,
    "crazyflie_tof_only": CRAZYFLIE_TOF_ONLY,
    "crazyflie_camera_only": CRAZYFLIE_CAMERA_ONLY,
}


def get_impairment(spec: str | ScanImpairment | None) -> ScanImpairment | None:
    """Resolve a preset name (or a literal ScanImpairment) to an impairment."""
    if spec is None or isinstance(spec, ScanImpairment):
        return spec
    try:
        return IMPAIRMENTS[spec]
    except KeyError:
        raise KeyError(f"unknown impairment {spec!r}; known: {sorted(IMPAIRMENTS)}") from None
