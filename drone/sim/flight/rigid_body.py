"""6-DOF rigid body with a fixed-step integrator.

Translation uses the constant-acceleration update over each step
    p += v dt + a dt^2 / 2 ;  v += a dt
which is exact when the force is constant across the step (free fall, so the energy
check in checks_phase1 tests the force bookkeeping, not integrator luck), and second order
otherwise. Rotation integrates Euler's equations (with the gyroscopic term) semi-implicitly
and advances the attitude with the exact exponential map of the body rate.

Forces are given in the world frame, moments in the body frame (FLU), both excluding
gravity, which the body adds itself. The non-gravitational specific force is kept for the
IMU model (an accelerometer measures it, not the total acceleration).
"""
from __future__ import annotations

import numpy as np

from .frames import GRAVITY_M_S2, quat_from_rotvec, quat_mul, quat_normalize, quat_to_rot


class RigidBody:
    def __init__(self, mass_kg: float, inertia_kg_m2, pos_m=(0, 0, 0), vel_m_s=(0, 0, 0),
                 quat=(1, 0, 0, 0), omega_rad_s=(0, 0, 0), gravity_m_s2: float = GRAVITY_M_S2):
        self.mass_kg = float(mass_kg)
        self.inertia_kg_m2 = np.asarray(inertia_kg_m2, dtype=float).reshape(3)
        self.pos = np.asarray(pos_m, dtype=float).copy()
        self.vel = np.asarray(vel_m_s, dtype=float).copy()
        self.quat = quat_normalize(np.asarray(quat, dtype=float))
        self.omega = np.asarray(omega_rad_s, dtype=float).copy()   # body rates, FLU
        self.gravity = np.array([0.0, 0.0, -gravity_m_s2])
        self.R = quat_to_rot(self.quat)
        self.specific_force_body = np.array([0.0, 0.0, 0.0])   # accelerometer truth
        self.accel_world = np.zeros(3)
        self.omega_max_rad_s = 200.0

    def set_mass(self, mass_kg: float, inertia_kg_m2=None) -> None:
        """Change mass (and optionally inertia) between steps, e.g. a payload release."""
        self.mass_kg = float(mass_kg)
        if inertia_kg_m2 is not None:
            self.inertia_kg_m2 = np.asarray(inertia_kg_m2, dtype=float).reshape(3)

    def step(self, force_world_n: np.ndarray, moment_body_nm: np.ndarray, dt_s: float) -> None:
        a = force_world_n / self.mass_kg + self.gravity
        self.accel_world = a
        self.specific_force_body = self.R.T @ (force_world_n / self.mass_kg)
        self.pos += self.vel * dt_s + 0.5 * a * dt_s * dt_s
        self.vel += a * dt_s
        I = self.inertia_kg_m2
        w = self.omega
        wdot = (moment_body_nm - np.cross(w, I * w)) / I
        self.omega = w + wdot * dt_s
        n = float(np.sqrt(self.omega @ self.omega))
        if n > self.omega_max_rad_s:              # guard: no airframe spins faster than this
            self.omega *= self.omega_max_rad_s / n
        self.quat = quat_normalize(quat_mul(self.quat, quat_from_rotvec(self.omega * dt_s)))
        self.R = quat_to_rot(self.quat)

    # energy bookkeeping for checks
    def kinetic_energy_j(self) -> float:
        return 0.5 * self.mass_kg * float(self.vel @ self.vel) + \
            0.5 * float(self.inertia_kg_m2 @ (self.omega * self.omega))

    def potential_energy_j(self) -> float:
        return -self.mass_kg * float(self.gravity @ self.pos)
