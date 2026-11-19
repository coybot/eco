"""flight -- rigid-body flight dynamics for the headless sim.

General simulator features, usable by any vehicle (see ``dynamic_world.DynamicWorld``
for the TeamWorld backend that puts existing vehicle classes on them):

  frames       quaternions and frame conventions (world ENU, body FLU)
  rigid_body   6-DOF rigid body, fixed-step integrator
  aero         body drag (isotropic or per-axis), fixed-wing lift/drag/moments with stall
  actuators    motor lag and saturation, multirotor mixer, propeller
  wind         steady wind, von Karman turbulence field, discrete gusts, local sources
  sensors      noise, bias drift, delay, update rate, dropouts; IMU/GNSS/baro/airspeed/...
  estimation   delayed-measurement Kalman filter (own and relative navigation)
  multirotor   quadcopter model + cascaded flight controller (reads sensors only)
  fixedwing    fixed-wing 6-DOF airframe + autopilot (reads sensors only)
  randomize    seeded parameter randomisation
  batch        headless batch runner with CSV logging and unit-suffix checks

Conventions: SI units everywhere, and every parameter / CSV column name carries its unit
as a suffix (``speed_m_s``, ``tilt_deg``, ``mass_kg``). World frame is ENU (x east,
y north, z up); body frame is FLU (x forward, y left, z up). Angles are radians inside the
code (``_rad``) and degrees in configs and logs (``_deg``).

Pure numpy; no rendering; deterministic for a given seed and fixed timestep.
"""
