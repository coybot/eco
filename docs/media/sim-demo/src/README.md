# Sim demo source

`sim-demo.mov` is a real screen recording of `drone/sim`'s Godot-rendered
depot scenario: a fixed-wing does a wide-area sweep, tasks a quadcopter to
investigate a candidate object, and the ground rover (Phrover) closes in and
confirms a hazard. Not staged or hand-animated — this is the actual sim
output for one run of the kind of scenario in `drone/sim/scenarios/`.

Re-encode the GIF with `./encode.sh` (see the script for the color/dither
tradeoffs — this source has real shading and on-screen text, unlike the flat
vector panels in `../../autonomy/src/`).
