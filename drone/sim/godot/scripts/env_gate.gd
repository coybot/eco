## VLM gate staging environment (milestone M1 of the two-drone SAR demo plan).
##
## Purpose: answer, before any other demo work is built, the one question the
## whole "big AI on device, not just YOLO" claim depends on — can the shipped
## on-device VLM (Qwen3-VL-8B Q4) actually tell "the guy in the RED jacket"
## from an ordinarily-dressed bystander, from a fixed-wing camera, at the
## slant ranges the mission will fly at? If it cannot, the storyboard's
## attribute-identification beat is fiction and the plan branches to fine-tuning.
##
## This is a measurement rig, not a mission environment: there is no wall, no
## tunnel and no flight, only well-separated humanoid subjects that the harness
## (drone/sim/fw_gate_vlm.py) stages the aircraft in front of via fw_spawn.
## Subjects are spaced far enough apart (>=100 m) that at the gate's staging
## distances only the intended one is ever inside the 60°-HFOV / 80 m detect
## cone, so each probe is unambiguous — except SUBJECT_PAIR_*, which is
## deliberately a red-jacket figure and a bystander close enough to appear in
## the SAME frame (the hardest and most demo-relevant discrimination case).
##
## Scene setup comes from scene_kit.gd, the SAME builder env_sar.gd uses.
## That sharing is deliberate and load-bearing: colour fidelity is the entire
## point of this rig, so if the gate rendered differently from the scene the
## video is actually shot in, its measured recognition rate would be evidence
## about a world that does not appear on screen.
##
## Coordinate convention: ENU (x=east, y=north) metres, same as every other env.
extends Node3D

# Preloaded rather than referenced by `class_name` — this project has no global
# script class cache (never opened in the editor), so a bare `Humanoid` fails
# to resolve when Godot is launched from the CLI. See humanoid.gd's note.
const Humanoid = preload("res://scripts/humanoid.gd")
const SceneKit = preload("res://scripts/scene_kit.gd")

var WALLS: Array = []
var doors: Dictionary = {}

# Subject positions (ENU). Kept in sync with fw_gate_vlm.py's SUBJECTS table,
# which cross-checks these against fw_prop_truth() at startup rather than
# trusting the duplication — a silent drift here would quietly invalidate every
# score the gate reports.
const SUBJECT_RED := Vector2(200.0, 0.0)
const SUBJECT_GRAY := Vector2(200.0, -120.0)
const SUBJECT_NAVY := Vector2(200.0, 120.0)
# Same-frame pair: 9 m apart, far from the singles.
const SUBJECT_PAIR_RED := Vector2(60.0, 0.0)
const SUBJECT_PAIR_GRAY := Vector2(60.0, 9.0)

var props: Array = []


func _ready() -> void:
	SceneKit.build(self, true, 0)
	_build_subjects()
	FixedWingManager.register_env(self)
	print("[env_gate] ready — %d humanoid subjects" % props.size())


func _build_subjects() -> void:
	# facing_deg is varied per subject so the gate is not accidentally measuring
	# one single silhouette; the harness additionally stages each subject from
	# several bearings.
	_add(Humanoid.build("red_jacket", "person", SUBJECT_RED, Humanoid.JACKET_RED, 30.0))
	_add(Humanoid.build("gray_bystander", "person", SUBJECT_GRAY, Humanoid.JACKET_GRAY, 200.0))
	_add(Humanoid.build("navy_bystander", "person", SUBJECT_NAVY, Humanoid.JACKET_NAVY, 110.0))
	_add(Humanoid.build("pair_red", "person", SUBJECT_PAIR_RED, Humanoid.JACKET_RED, 0.0))
	_add(Humanoid.build("pair_gray", "person", SUBJECT_PAIR_GRAY, Humanoid.JACKET_GRAY, 180.0))


func _add(node: StaticBody3D) -> void:
	add_child(node)
	props.append(node)


func add_wall(rect: Rect2, height: float = 60.0) -> void:
	# The gate env has no walls of its own, but FixedWingManager.inject()'s
	# "raise_wall" verb calls this on whatever env is registered, so it must
	# exist. Kept as a no-op-shaped real implementation for parity with the
	# other envs rather than crashing if something injects into the rig.
	var body := StaticBody3D.new()
	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(rect.size.x, height, rect.size.y)
	col.shape = shape
	body.add_child(col)
	var mesh := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = shape.size
	mesh.mesh = bm
	body.add_child(mesh)
	body.position = Vector3(rect.position.x + rect.size.x * 0.5, height * 0.5,
		-(rect.position.y + rect.size.y * 0.5))
	body.collision_layer = 1
	add_child(body)
	WALLS.append(rect)
	FixedWingManager.register_env(self)
