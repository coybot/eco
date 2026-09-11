# presidio-bevy-sim — headless Bevy backend for the drone simulator

A drop-in replacement for the Godot sim **at the TCP seam**. It speaks the same
newline-delimited JSON protocol as `drone/sim/godot/scripts/ipc_server.gd`, so
`engine_client.py`, `rover/sim/depot_client.py` and `fw_manhattan_figure8.py`
drive it unchanged. **Nothing under `drone/sim/*.py` was modified for this.**

Scope today: the **Manhattan fixed-wing** scenario. Godot remains the default
backend for everything.

## Why this exists

Godot cannot render to a texture without a display server. On Linux the sim
needs Xvfb (`DEPLOY.md`, `presidio-godot-sim.service`, `platforms/sim_host/install.sh`);
on macOS it must run **windowed**, because `--headless` leaves every camera
SubViewport blank (`BRINGUP_MAC.md`). Godot's offscreen-rendering proposals
([#4134](https://github.com/godotengine/godot-proposals/discussions/4134),
[#5790](https://github.com/godotengine/godot-proposals/issues/5790)) are still
open. wgpu renders offscreen with no window and no display server at all.

Measured on the Linux GPU host (Ubuntu 24.04, RTX 5090), same box, no `DISPLAY` in the
process environment and no Xvfb involved:

| Backend | `fw_grab_frame` result |
|---|---|
| Bevy | real 26 KB JPEG of the city |
| Godot `--headless` | `jpg: null` — no image at all |

## Running it

```bash
cargo build --release
env -u DISPLAY ./target/release/presidio-bevy-sim -- \
    --fleet= --env=manhattan --seed=0 --ipc-port=9979
```

Flags match the Godot build's, in both `--key=value` and `--key value` form, so
the argv `fw_eval.launch_flightline` builds works as-is: `--fleet=`, `--env`,
`--seed`, `--ipc-port`, `--env-region=e0,n0,e1,n1`, `--building-scale`.
It prints `[IpcServer] IPC ready on port N`, the line the harnesses wait for.

Extra flag, not in the Godot build:

- `--tick-hz` (default 60, matching `physics_ticks_per_second`). The flight
  model's easing constants are all in seconds against the measured dt, so the
  rate does not change how the aircraft flies — but it does change frame-grab
  latency, which is frame-boundary bound on unified memory. See below.

The city asset is read from the Godot project by default
(`../godot/assets/manhattan/buildings.json`) so both backends fly the identical
city; a sibling `assets/` copy is used when the crate is deployed alone, and
`PRESIDIO_MANHATTAN_JSON` overrides both.

### Build notes

`bevy` is pulled with `default-features = false` and no `bevy_winit`, `x11`,
`wayland`, `bevy_audio` or `bevy_gilrs`. That is not just trimming: that host has
no `libudev-dev`, `libasound2-dev` or `libwayland-dev`, and no sudo was
available to add them. Cold build there: **1 m 20 s** on 32 cores.

`tonemapping_luts` requires an explicit zstd backend; `zstd_rust` is used to
avoid needing a C toolchain on the GPU hosts.

## Verification status

| Check | Result |
|---|---|
| Headless, no display server, macOS (M5 Pro, Metal) | real frames, no window |
| Headless, no display server, Linux host (RTX 5090, Vulkan) | real frames, `DISPLAY` absent from `/proc/<pid>/environ` |
| Startup banner vs Godot | byte-identical (45k buildings, 472 m tallest, 1078x4316 @ 5 m grid, 270 land bands, park 803x4130 at east -901 north -2926) |
| `fw_manhattan_figure8.py --minutes 3`, unmodified | 0 building strikes, 0 link dropouts, 0 respawns, altitude held 100.0-100.7 m vs 100 m commanded, 10 climb-overs |
| Protocol parity vs Godot (`fw_env_state`, `fw_prop_truth`, `fw_detect` at 4 poses, `fw_unproject`, grid geometry) | matches; detection `world` exact, `nx`/`ny` within 1e-3 |
| Occupancy grid | byte-identical to Godot (1,146,735 of 4,652,648 cells occupied); both match exact geometry |
| Frames change with pose and with gimbal offset | yes (3 distinct hashes) |

### The occupancy grid divergence was ours, and is fixed

`fw_grid`'s occupancy array used to differ from Godot's in **649 cells out of
4,652,648**. It was arbitrated a second time, per cell, with exact rational
arithmetic over the f32-exact footprint values, and the first arbitration had
the sign backwards:

- **584 cells only this backend marked: all 584 genuinely FREE.** This backend
  invented obstacles.
- **65 cells only Godot marked: all 65 genuinely OCCUPIED.** This backend lost
  real obstacles.

Godot's grid was byte-identical to exact geometry in all 4,652,648 cells. The
earlier note here blamed Godot's cell-range arithmetic and recommended
"fixing" it; doing that would have injected all 649 errors into the one backend
that was right. GDScript already promotes each `Vector2` component to double
separately, which is exactly what `rebuild_occ_grid` does deliberately — the
two range computations always agreed, and an over-wide range is harmless
regardless because `intersects` re-tests every cell in it.

The actual cause was upstream of the grid, in `env::manhattan`'s slice
recentring. Godot accumulates the slice extents as
`maxf(max_x, rect.position.x + rect.size.x)` — a double, with each component
widened separately, so the edge sum is exact. This backend accumulated
`max_x.max(rect.pos.x + rect.size.x)` in f32. For this dataset the widest
building's f32 edge sum rounds up 9.7e-05 m, which moved the recentring offset
4.6e-05 m and slid **every** footprint that far east of where Godot puts it —
enough to flip the 649 cells whose footprint edge lands on a 5 m cell boundary.

Fixed by accumulating the extents in f64 (`env/manhattan.rs`). Verified by
launching both backends on the manhattan env and diffing the base64 `occ` from
`fw_grid`: now byte-identical, 1,146,735 occupied, matching the independent
exact-geometry count. `datum_offset_enu` agrees to f32 as well.

The 97-cell figure quoted in `rebuild_occ_grid` is real and reproducible, but it
measures a pure-f32 *range* variant — it was never evidence about Godot.

### Divergences inherited on purpose

- **`fw_grid` nests under a `grid` key** while the rover's `phrover_grid`
  returns the same fields flat. That asymmetry is in the Godot protocol and
  `depot_client.fw_grid` depends on it; flattening it made the client hand back
  an empty dict and `fw_eval`'s coverage metric silently compute nothing.
- **The collidable ground is a finite 2000 m slab.** Godot's only ground body
  comes from `scene_kit._add_ground`; Manhattan's own water/land/park are meshes
  with no bodies, and the buildings have no bodies either. Reproduced so a
  parity check compares like with like.
- **`unproject` does not consult buildings.** In Godot they are not in the
  physics layer, so a pixel aimed at a tower unprojects to the ground behind it.
  Worth fixing, but it would change every existing answer.
- **`detect`/`unproject` use an equiangular pixel model** (`nx` linear in
  bearing) while the renderer is a pinhole. The depth sensor below uses the
  pinhole model so it registers with the RGB image; closing the gap in
  `detect()` would move every existing `nx`/`ny`.
- **`fw_reset_camera` is a no-op.** It exists in Godot because a SubViewport's
  render target could freeze permanently once another GPU client started heavy
  work, returning identical bytes forever while `detect()` reported the truth.
  Nothing here holds a persistent render target open across frames.

## Performance

`fw_grab_frame` is the client-visible cost. One engine at a time — both
rendering this city at once on a single integrated GPU contend, which confounded
the first attempt at this measurement.

| Configuration | median grab (640x480) | effective fps |
|---|---|---|
| Mac M5 Pro (Metal), tick 60 Hz | 59 ms | 17 |
| Mac M5 Pro (Metal), tick 240 Hz | 14 ms | 70 |
| Linux host, RTX 5090 (Vulkan), tick 60 Hz | 50 ms | 20 |
| Linux host, RTX 5090 (Vulkan), tick 240 Hz | 13 ms | 75 |

Real-time factor stays at 1.00 in every case, i.e. the sim keeps up with
wall-clock while serving grabs. The grab cost is **frame-boundary bound, not
GPU bound**, on both unified and discrete memory: a grab takes about three
ticks, so raising `--tick-hz` is the lever, not reducing scene cost. Neither
machine is near its rendering limit for this scene.

Analytic depth (`fw_grab_depth`) on the Linux host, CPU-only, one ray per pixel:

| Resolution | time |
|---|---|
| 160x120 | 10 ms |
| 320x240 | 29 ms |
| 640x480 | 103 ms |

Note that **Godot's own `sim_t` cannot be compared this way**: it advances by
the real frame delta, so its real-time factor is 1.0 by construction even at 5
fps, while this backend uses a fixed timestep and therefore reports when it
falls behind. Godot's headless grab measured 7 ms — of an empty image.

## Many aircraft, many sensors

Each aircraft carries a forward camera; `add_sensor` mounts more on the same
airframe, each with its own static yaw, and `grab_sensor` reads one by name.
Godot has no equivalent: it creates exactly one camera per aircraft and offers
no way to add a second, so a multi-sensor airframe is not expressible there.

Sensor cameras are **inactive unless something is reading them**, and stay warm
for two seconds after a read. That makes cost track the sensors actually in use
rather than the sensors that exist. With every camera always on, as Godot's
`UPDATE_ALWAYS` SubViewports are, frame time grows with camera count whether or
not anyone wants the pixels, and since a grab costs about three frames, 32 idle
cameras made every grab three times slower than one.

Flight progress, measured as distance covered against airspeed times wall
clock, stayed at 1.00 in every configuration tested on both machines and in
Godot too, up to 16 aircraft and 64 mounted sensors. **No sim falls behind real
time and no frame came back empty.** What degrades is frame-grab latency.

Median grab latency on the Linux host, reading every camera round-robin. Godot's extra
cameras are vantages, since it cannot mount a second camera on an aircraft;
total camera count is what is matched.

| cameras read | Godot (Xvfb) | this crate, tick 60 Hz | this crate, tick 240 Hz |
|---|---|---|---|
| 1 | 20 ms | 50 ms | 13 ms |
| 8 | 36 ms | 50 ms | 29 ms |
| 16 | 66 ms | 50 ms | 56 ms |
| 32 | 86 ms | 126 ms | 125 ms |

Two things to read off that. At the default tick this backend is **flat** from 1
to 16 cameras because it is tick-bound, where Godot degrades linearly; they
cross at roughly a dozen cameras. And at 32 cameras all being read hard, Godot
still wins.

Where the on-demand design pays, Linux host, tick 240 Hz:

| mounted | read | grab median | RSS |
|---|---|---|---|
| 32 | 32 | 125 ms | 1813 MB |
| 32 | 8 | 29 ms | 1140 MB |
| 64 | 16 | 55 ms | 1456 MB |
| 32 | 4 | 19 ms | 914 MB |

Sixty-four sensors mounted across sixteen aircraft, reading sixteen of them,
costs what sixteen cameras alone cost. The other forty-eight are free.

**Memory is this backend's clear weakness.** At 32 active cameras it holds
about 1.8 GB against Godot's 509 MB, roughly 40 MB per active render target
against Godot's 1.3 MB. On-demand rendering cuts it substantially, 914 MB at
4-of-32, but not to Godot's level.

### CPU-side work is where Rust pays

Measured on this Mac, one engine at a time, same pose inside the densest
cluster of 70 m-plus buildings with six structure detections in frame:

| op | this crate | Godot | what it does |
|---|---|---|---|
| IPC round trip (`fw_state`) | 5.0 ms at tick 240, 20 ms at tick 60 | 7.0 ms | nothing; dispatch latency only |
| `fw_detect` | below the 0.05 ms floor | 0.07 ms | analytic structure pass, capped at 6 rows |
| `fw_grid` | 20.7 ms | 65.5 ms | base64 of two 4.65 MB grids |
| `fw_inject raise_wall` | **4.2 ms** | **115.8 ms** | rebuild the 45k tile index and the 4.65M-cell grid |

The last row is the real signal: it is the only op that loops over the whole
dataset, and it is 28x faster. That ceiling already shapes the Godot sim's
design rather than being a hypothetical. `MAX_STRUCTURE_ROWS` is 6, the
structure pass rejects whole tiles before touching buildings, and
`_rebuild_occ_grid` carries a comment saying the straightforward form "would
hang on load rather than run slowly". Analytic sensors that cast a ray per
pixel are only practical on the Rust side: the 320x240 depth image is 76,800
rays in 29 ms.

Note also that this backend's IPC latency is **tick-bound**. At the default 60 Hz
every request waits for the next dispatch, so it is slower than Godot's; at 240
Hz it is faster. That is a knob Godot does not have.

### What "many sensors" costs

Per-sensor overhead, from the sweeps above:

| | this crate | Godot |
|---|---|---|
| RSS per active render target | ~40 MB | ~1.3 MB |
| grab latency, 1 camera, default tick | 50 ms | 20 ms |
| grab latency, 16 cameras read | 50 ms (flat, tick-bound) | 66 ms (linear) |
| mount a second camera on an aircraft | `add_sensor` | not possible |

## Layout

```
src/
  main.rs          app wiring, CLI, plugin set, IPC dispatch, pose push
  ipc.rs           TCP listener; one reply per request, in request order
  args.rs          Godot-compatible launch-argument parsing
  enu.rs           ENU <-> render-space conversion, in one place
  geom.rs          Rect2 + exact ray/slab tests (f64, as the GDScript's own)
  scenario.rs      Scenario, Prop, and the Environment trait (the seam)
  env/manhattan.rs the city: load, clip, recentre, tile index, slab raycast,
                   analytic structure detections, coastline recovery
  vehicle side:
  fixedwing.rs     flight model, detect/unproject, grids, events, injects,
                   payload ballistics
  airframe.rs      delta-wing mesh, mechanically generated from the GDScript
  sensors/mod.rs   Sensor component: modality + mount + render layers
  sensors/rgb.rs   render target -> JPEG -> base64
  sensors/depth.rs analytic range image, registered with the RGB frame
  scene.rs         sky, light, ground slabs, 45k city instances, props
```

## Growth seams

The sim is expected to grow more views, more sensors, procedural scenarios and
possibly a photorealistic tier. What is in place:

- **Sensors.** One `Sensor` component carries a modality, a mount and a render
  target. `Modality::Depth` is implemented as a second modality through the
  same mount (op `fw_grab_depth`, 16-bit PNG, 1 cm fixed point over 655 m,
  pinhole model so it is registered with the RGB frame). It is **analytic**,
  not a GPU prepass: the city is a set of axis-aligned prisms answered by an
  exact slab test, so depth is exact arithmetic with no readback and no
  z-buffer precision problem at a 40 km far plane. A textured env later wants
  the GPU path — that is a new branch in `sensors/`, not a new subsystem.
- **Views.** Onboard, vantage and chase views are all the same machinery with
  different mounts. `add_chase` (additive op) follows an aircraft and resolves
  in the same frame as the pose it films, where Godot had the client call
  `move_vantage` every tick, putting the camera a round trip behind.
- **Render layers.** Each airframe gets its own layer, assigned once and never
  reused, so an onboard camera can exclude *its own* airframe while still
  seeing every other aircraft — peer sighting is the only channel a
  comms-denied formation has.
- **Scenarios.** `Environment` is a trait; Manhattan is the first implementer.
  A procedural generator implements the same trait and the vehicle model does
  not change. The `as_any` downcast is confined to scene construction.
- **Vehicles.** Only fixed-wing today. Quad/rover/phrover are the next
  implementers.

A straight-down view is handled explicitly: `looking_at` is degenerate when the
view direction is parallel to `up`, which rotated a nadir vantage ~90 degrees —
the one view most likely to be read as a map. It falls back to ENU north.

## Not implemented

Quad/rover fleet, phrover, every env except Manhattan, engine audio, runtime
`load_env` switching (the harnesses pass `--env` on the command line; swapping
would mean tearing down 45k entities mid-flight, so it reports honestly about
what is loaded instead of silently flying a different world), and the Kenney
GridMap city assets.
