"""Localization fabric — GPS-denial / drift / spoof model (Phase 2).

The sim has perfect ground-truth pose; real flight does not. This wraps each
agent's true pose with an *estimator* so the agent navigates on what it *believes*,
and exposes a confidence scalar the policy / team runtime can react to.

Modes (per agent, settable by scenario injects):
  - GPS_OK:     belief = truth + small noise; confidence ~1.
  - GPS_DENIED: fall back to VIO/odometry — integrate motion with an accumulating
                random-walk bias at the class's gps_denied_drift_mps
                (vehicle_class.LocalizationProfile). Belief drifts; confidence decays.
                Mirrors cuVSLAM behavior used by the IRL stack (nav2_bridge.py).
  - GPS_SPOOF:  inject a slowly-ramping false-position offset; belief is confidently
                wrong (confidence stays high — that is what makes spoofing dangerous).

Wire into TeamWorld: ``world.localization = fabric``. ``TeamWorld.step`` calls
``fabric.update(world)`` each tick; ``TeamWorld.observe`` reads ``believed_pos`` /
``confidence``. Body-frame goal vectors already drive the policies, so drift degrades
goal-tracking realistically with no contract change.

Pure stdlib + numpy; deterministic given a seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class LocMode(str, Enum):
    GPS_OK = "gps_ok"
    GPS_DENIED = "gps_denied"
    GPS_SPOOF = "gps_spoof"


@dataclass
class _EstState:
    mode: LocMode = LocMode.GPS_OK
    bias: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    confidence: float = 1.0
    spoof_vel: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))


class LocalizationFabric:
    """Per-agent pose estimator with scriptable GPS conditions."""

    GPS_NOISE_M = 0.05          # std of belief noise when GPS healthy
    CONF_DECAY_PER_M = 0.6      # confidence falls as drift magnitude grows (denied)
    CONF_RECOVER_RATE = 1.0     # per-second confidence recovery when GPS restored

    def __init__(self, seed: int = 0):
        self._rng = np.random.default_rng(seed)
        self._st: dict[str, _EstState] = {}

    def _state(self, agent_id: str) -> _EstState:
        return self._st.setdefault(agent_id, _EstState())

    # -- scenario inject hooks --------------------------------------------------
    def set_mode(self, agent_id: str, mode: LocMode) -> None:
        st = self._state(agent_id)
        st.mode = mode
        if mode is LocMode.GPS_SPOOF and not st.spoof_vel.any():
            # default slow drift of the spoofed fix: ~0.3 m/s in a random direction
            ang = float(self._rng.uniform(0, 2 * np.pi))
            st.spoof_vel = np.array([0.3 * np.cos(ang), 0.3 * np.sin(ang), 0.0])

    def gps_loss(self, agent_id: str) -> None:
        self.set_mode(agent_id, LocMode.GPS_DENIED)

    def gps_spoof(self, agent_id: str, vel=None) -> None:
        self.set_mode(agent_id, LocMode.GPS_SPOOF)
        if vel is not None:
            self._state(agent_id).spoof_vel = np.asarray(vel, dtype=np.float64)

    def restore(self, agent_id: str) -> None:
        self.set_mode(agent_id, LocMode.GPS_OK)

    def set_team_mode(self, world, mode: LocMode) -> None:
        for aid in world.agents:
            self.set_mode(aid, mode)

    # -- per-tick update --------------------------------------------------------
    def update(self, world) -> None:
        dt = world.dt
        for a in world.agents.values():
            st = self._state(a.id)
            if st.mode is LocMode.GPS_OK:
                # bias relaxes to zero, confidence recovers
                st.bias *= max(0.0, 1.0 - self.CONF_RECOVER_RATE * dt)
                st.confidence = min(1.0, st.confidence + self.CONF_RECOVER_RATE * dt)
            elif st.mode is LocMode.GPS_DENIED:
                drift = a.vclass.localization.gps_denied_drift_mps
                step = self._rng.normal(0.0, drift * dt, size=3)
                if a.vclass.planar:
                    step[2] = 0.0
                st.bias += step
                mag = float(np.linalg.norm(st.bias))
                st.confidence = float(1.0 / (1.0 + self.CONF_DECAY_PER_M * mag))
            elif st.mode is LocMode.GPS_SPOOF:
                st.bias += st.spoof_vel * dt        # confidently wrong
                st.confidence = max(st.confidence, 0.9)

    # -- reads (TeamWorld.observe uses these) -----------------------------------
    def believed_pos(self, agent) -> np.ndarray:
        st = self._state(agent.id)
        p = agent.pos.astype(np.float64) + st.bias
        if st.mode is LocMode.GPS_OK:
            p = p + self._rng.normal(0.0, self.GPS_NOISE_M, size=3)
            if agent.vclass.planar:
                p[2] = agent.pos[2]
        return p.astype(np.float32)

    def confidence(self, agent) -> float:
        return self._state(agent.id).confidence

    def error(self, agent) -> float:
        """True localization error magnitude (for the scorecard / debugging)."""
        return float(np.linalg.norm(self.believed_pos(agent) - agent.pos))

    def mode(self, agent_id: str) -> LocMode:
        return self._state(agent_id).mode
