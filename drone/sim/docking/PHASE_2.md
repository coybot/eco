# Phase 2: add the vehicles and the scenario

Branch `docking/phase2` (local, not pushed), on top of `docking/phase1`.

## 1. The existing quadcopter (left unchanged)

The simulator's existing `quadcopter` class (`vehicle_class.QUADCOPTER`) is a kinematic
descriptor. Its fields:

- collision radius **0.15 m**, so **0.30 m across**;
- maximum speed 3.0 m/s;
- yaw rate 1.5 rad/s;
- ceiling 30 m;
- payload 0.5 kg;
- a 45-ray forward depth sensor.

**It has no mass, inertia, thrust or drag:** the kinematic sim never needed them. The
hardware it stands for (Cube Orange autopilot, Jetson Orin, D435i camera, 4S battery) has
no recorded mass or frame size anywhere in the repo. For Phase 1's opt-in rigid-body
backend, `vehicles.yaml` carries ESTIMATED dynamics for it in `quadcopter_existing_dynamics`
(1.2 kg, T/W 2.0, 0.04 m^2). The class itself is untouched, and the Phase 1 regression shows
its scenarios unchanged.

Size comparison, stated plainly: by the repo's own numbers, the new small quadcopter
(0.30 m across including guards) has the **same footprint** as the existing class's
collision circle. It is "smaller" than the existing quadcopter only relative to the real
airframe that class represents, whose size the repo does not record. It is a separate
class with its own dynamics, so nothing about the existing one changes.

## 2. The new vehicles

All numbers are in `vehicles.yaml`, each tagged [P] published, [D] derived from published
numbers, or [E] estimate.

### Small quadcopter (`small_quadcopter`, NEW)

| item | value | basis |
|---|---|---|
| mass empty / with drink | 1.0 / 1.4 kg | task (the empty mass includes probe and latch fitting) |
| size | 0.30 m across including guards | task |
| props | 4 in (0.102 m) | [D] the largest that fits 0.30 m with guards; 5 in props overlap |
| arm (motor to centre) | 0.089 m | [D] 0.15 - 0.051 - 0.010 |
| thrust-to-weight | 2.5 at 1.4 kg = 34.3 N total, 8.6 N per motor | task. The reference mass is my reading; T/W 2.5 at 1.0 kg (24.5 N) is a Phase 3 sensitivity case |
| drag area | 0.05 m^2, isotropic | task |
| inertia | 0.0045 / 0.0045 / 0.0070 kg m^2 at 1.0 kg | [E] |
| motor lag up / down | 25 / 40 ms | [E] |
| maximum tilt | 65 deg | [E] (ArduCopter ANGLE_MAX allows up to 80) |
| sensors | IMU 400 Hz; AHRS 200 Hz; RTK GNSS 10 Hz with 0.1 s delay, 1.5 cm noise, dropouts and outage bursts; barometer | [E] |

It is registered as a vehicle class (`docking/vehicles_register.py`, through the existing
`register_class`), so team layers and `TeamWorld` can place it. `DynamicWorld` flies it on
the rigid-body model.

### Quadplane mothership (`quadplane`): Makeflyeasy Pioneer 3200

Published figures and their sources. Retrieved 2026-11-19. makeflyeasy.com itself refused
connections, so the manufacturer page was read from the Internet Archive.

| figure | value | source |
|---|---|---|
| span, fuselage length / height, wing area, MTOW, max payload, payload bay | 3.2 m; 1.68 m / 0.41 m; 1.0 m^2; 24.9 kg; 5 kg; 395 x 216 x 151 mm | task brief |
| stall speed | 14 m/s (mass not stated) | Makeflyeasy Pioneer page, archived 2026-03-05, web.archive.org/web/20260305232939/http://en.makeflyeasy.com/index.php/pioneer/ ; same on bzbuas.com, austars-model.com, uavmodel.com |
| maximum airspeed | 35 m/s | same pages |
| economic airspeed | 22 m/s at 23 kg; 21 m/s at 20 kg | austars-model.com and uavmodel.com Pioneer 3200 listings |
| autopilot settings | TRIM_ARSPD 21.5 m/s, ARSPD_FBW_MIN 16, ARSPD_FBW_MAX 25, Q_FRAME quad X | Makeflyeasy GitHub `MFE_ArduPlane/Pioneer3200/SITL_Models_RealFlight` parameter file |
| propulsion | lift: 4 x MFE 6518 kv200, 12S 100 A ESCs, 22 in props; cruise: MFE 6525 kv180, 12S 160 A, APC 21x13 | same GitHub README; austars-model.com parts list |
| battery | 4 x 6S 30 Ah semi-solid, 2P2S = 12S 60 Ah (one wiring diagram says 30 Ah) | uavmodel.com, GitHub param file |
| endurance tests | 1 kg payload: 20.5 m/s, 20.6 kg, 230 min. 5 kg payload: 22.5 m/s, 24.6 kg, 176 min (a second table: 198 min) | austars-model.com, uavmodel.com, uavmodel.com blog |
| layout | lift motors on a 1 m square, arms through the wing; tailplane and vertical fin on the fuselage, fin projecting **below** the fuselage; cruise prop at the fuselage tail | uavmodel.com text ("standard quadrilateral format with a 1m side"); BZB kit list; render and side drawing (my reading of the pictures) |
| airframe weight | 9.2 kg without battery and payload | austars-model.com, uavmodel.com |

Derived and estimated values:

| item | value | basis |
|---|---|---|
| airframe + batteries | 19.6 kg | [D] from the endurance tables: 20.6 kg take-off at 1 kg payload; 24.6 kg at 5 kg |
| chord | 0.3125 m (aspect ratio 10.24) | [D] |
| CLmax | **1.63** baseline; 1.30 conservative | [D] The published 14 m/s stall at 24.9 kg would need CLmax 2.0, which is not credible for a clean wing. At 20 kg (the lightest published mass) it implies 1.63, which I take as the baseline. 1.30 is a typical clean-wing value, carried as the conservative case throughout. With CLmax 1.63 the model's empty stall speed is 13.9 m/s, against the published 14 |
| CD0 | 0.045 | [D] the 5 kg endurance point (2.66 kWh over 176 min at 22.5 m/s) gives 0.037-0.048 for 55-65 % total propulsive efficiency |
| lift slope | 5.18 per rad | [D] Helmbold, aspect ratio 10.24 |
| pusher static thrust / zero-thrust speed | 120 N / 55 m/s | [E] APC 21x13 at about 6400 rpm. The fall-off is sized so that thrust = drag at the published 35 m/s |
| stability derivatives, inertia, fuselage width, tail and fin size, positions | see yaml | [E] |

The lift rotors are present as mass, drag (inside CD0) and geometry, and they are strike
targets: stopped in cruise, but still blades. They are not actuators, because the
scenario never leaves wing-borne cruise.

**Autopilot:** airspeed to throttle; altitude to pitch to elevator; racetrack path
following; course to bank to aileron; rudder turn coordination.

Phase 2 added **stall protection**, which took two tries:

- **Why it was needed.** Without it, in moderate turbulence the altitude hold pitched up
  into downdrafts and reached the CLmax angle at every speed tested up to 27 m/s.
- **First version, wrong.** It capped pitch at the GNSS flight-path angle + 10.5 deg. A
  large-scale downdraft makes the ground-referenced flight path steep while the true
  alpha stays low. The cap then kept lowering the pitch, and in one light-turbulence run
  the mothership followed a downdraft from 50 m down to 28 m over 30 m-tall buildings.
- **Final version.** Alpha is estimated **aerodynamically**: the measured normal load
  factor (accelerometer) and dynamic pressure (pitot) give the lift coefficient, and the
  known lift curve gives alpha. If that exceeds 10.5 deg, pitch is reduced by 1.5x the
  excess. Full throttle when 2 m/s slow.
- **Result.** The same run now deviates 3.2 m. It also starts each run
trimmed, and flies turns 15 % faster than straights at a 25 deg bank limit, so the bank's
load factor does not eat the stall margin.

### External rail

The quadcopters (0.30 m across) do not fit the 395 x 216 x 151 mm bay, so they hang from
an external rail under the fuselage:

- rail base: 0.30 kg [E];
- each dock: 0.25 kg [E] (funnel, latch servo, fittings);
- drag: the rail 0.012 m^2 plus 0.05 m^2 per quad on it.

Each quad is a payload at its dock: its mass, its inertia by the parallel-axis theorem,
its weight moment about the CG, and its drag at the dock. Releasing it removes all of
those, so **mass and balance change at release**, and the autopilot has to re-trim.

## 3. Scene

`docking/scene.py`: a street along x, building faces at y = +/-10 m.

- **Buildings:** two rows of six apartment buildings, each 16-24 m wide, 5-10 floors of
  3 m, 15 m deep, with 3-6 m gaps. Seeded; default seed 7.
- **Balcony pads:** two per building on the street side, each 1.2 x 0.9 m sticking out of
  the facade at a floor level: 24 pads at 6-27 m height.
- **Route:** the mothership flies a racetrack 15 m above the tallest roof (50 m altitude).
  It has a straight leg along the street centreline, from x = -150 m to 150 m past the last
  building. The turn radius comes from the turn speed and a 25 deg bank (90 m at 23 m/s).
  The second straight runs parallel, 180 m to the side.
- **Compatibility:** buildings and pads are `team_world.Box`, so the existing ray casting
  and collision code can use them.

Delivery guidance (`docking/quad_mission.py`), from the quad's own sensors and the
mothership's radio broadcast:

1. Spin up on the rail to the trim thrust for the broadcast airspeed, ramped over 1.2 s.
2. Release.
3. Drop 3 m below the release point at the mothership's velocity.
4. Fly to a point over the street abeam the pad, then 2 m out from the pad, then over it.
5. Descend at 0.4 m/s onto spring-damper landing gear.
6. Hand over the drink (mass 1.4 to 1.0 kg) and wait 5 s.
7. Take off, back out to the street, and climb to a hold point under the route.

## 4. Results

TBD after run.

## 5. Problems found and fixed in this phase

- **Navigation initialisation (a Phase 1 bug).** The filter initialised from the first
  GNSS fix without allowing for its 0.1 s delay, which at 21 m/s is a permanent 2.1 m
  error. Its innovation gate then rejected every later fix. The fix extrapolates the fix to
  the present, and adds gate recovery: after 1 s of rejections, the filter re-initialises.
  Found when a delivery quad hovered 1.8 m off the pad and descended to the ground beside it.
- **Mothership started untrimmed**, and was still 2.8 m low and 1.5 m/s slow at the first
  release. It now starts trimmed.
- **Spinning up a pitched quad on the rail pitches the mothership nose-up.** About 18 N of
  thrust acts 0.5 m below the CG, roughly -7.6 N m. Releasing it then removes the
  nose-down moment of its drag. Together they took alpha to 13.6 deg against a 15.5 deg
  CLmax angle. The spin-up is now ramped over 1.2 s. Carried into Phase 3: dock height
  and a pitch-moment feed-forward.
- **Turns at the docking speed ate the stall margin**, with alpha up to 14 deg at 19.6 m/s
  and 34 deg bank. The fix is the turn speed schedule and bank limit above.
- **No stall protection** in moderate turbulence (see Autopilot above).

## 6. What is still uncertain

TBD.
