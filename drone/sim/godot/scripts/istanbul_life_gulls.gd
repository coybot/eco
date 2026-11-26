## Gulls over the Golden Horn and the Bosphorus shore (a part of
## istanbul_life.gd): yellow-legged gulls circling and gliding over the
## water by the bridges, the piers and the waterfront squares, where the
## ferries and the anglers keep them.
##
## Cheap by construction: one MultiMesh, one draw. Each gull is an instance
## whose origin is the centre of its circle; the gull shader
## (istanbul_life_meshes.GULL_SHADER) flies it round, banks it, flaps it in
## bursts and drifts its circle, all from TIME. Nothing per gull runs on the
## CPU after the flock is placed. Not solid (a gull is not an obstacle the
## strike sweep should report) and not a prop.
extends Node3D

const IM = preload("res://scripts/istanbul_life_meshes.gd")

## [lat, lon, spread m, gulls]: where they gather.
const FLOCKS := [
	[41.0205, 28.9727, 420.0, 170],    # Galata Bridge, both sides
	[41.0185, 28.9760, 260.0, 90],     # Eminonu piers
	[41.0228, 28.9775, 260.0, 80],     # Karakoy waterfront
	[41.0245, 28.9640, 380.0, 70],     # Golden Horn up to the Ataturk Bridge
	[41.0160, 28.9860, 300.0, 50],     # Sarayburnu
	[41.0330, 28.9935, 300.0, 60],     # Kabatas
	[41.0265, 29.0110, 320.0, 80],     # Uskudar
	[40.9925, 29.0215, 380.0, 90],     # Kadikoy
	[40.9985, 29.0165, 260.0, 40],     # Haydarpasa
	[41.0250, 28.9950, 700.0, 70],     # mid-Bosphorus, following the ferries
]
const DRAW_M := 1600.0

var env: Node3D
var on := false
var _mm: MultiMesh
var _mmi: MultiMeshInstance3D
var _n := 0


func setup(env_node: Node3D) -> void:
	env = env_node
	name = "gulls"


func set_on(want: bool) -> void:
	if want and _mm == null:
		_build()
	on = want and _mm != null
	if _mmi != null:
		_mmi.visible = on


func count() -> int:
	return _n if on else 0


func set_night(n: float) -> void:
	IM.gull_material().set_shader_parameter("flock", lerpf(1.0, 0.3, n))


func _build() -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = 0x6011
	var buf := PackedFloat32Array()
	for f: Array in FLOCKS:
		var c: Vector2 = env.call("geo_to_sim", float(f[0]), float(f[1]))
		var spread: float = f[2]
		var placed := 0
		var tries := 0
		while placed < int(f[3]) and tries < int(f[3]) * 12:
			tries += 1
			var p := c + Vector2(rng.randfn(0.0, spread * 0.5), rng.randfn(0.0, spread * 0.5))
			# Over the water, or the shore's first few tens of metres.
			var wet: bool = env.call("is_water", p.x, p.y)
			if not wet and rng.randf() < 0.85:
				continue
			var ground: float = 0.0 if wet else float(env.call("ground_height", p.x, p.y))
			var y := ground + rng.randf_range(6.0, 42.0)
			var r := rng.randf_range(9.0, 55.0)
			var v := rng.randf_range(7.0, 11.0)
			var w := v / r * (1.0 if rng.randf() < 0.5 else -1.0)
			var sc := rng.randf_range(0.9, 1.1)
			var b := Basis.from_scale(Vector3(sc, sc, sc))
			buf.append_array([b.x.x, b.y.x, b.z.x, p.x, b.x.y, b.y.y, b.z.y, y, b.x.z, b.y.z, b.z.z, -p.y,
				r, w, rng.randf_range(0.0, TAU), rng.randf()])
			placed += 1
	_n = buf.size() / 16
	if _n == 0:
		return
	_mm = MultiMesh.new()
	_mm.transform_format = MultiMesh.TRANSFORM_3D
	_mm.use_custom_data = true
	_mm.mesh = IM.gull_mesh()
	_mm.instance_count = _n
	_mm.buffer = buf
	_mm.custom_aabb = AABB(Vector3(-20000, -10, -20000), Vector3(40000, 300, 40000))
	_mmi = MultiMeshInstance3D.new()
	_mmi.name = "gulls_mm"
	_mmi.multimesh = _mm
	_mmi.material_override = IM.gull_material()
	_mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_mmi.visible = false
	add_child(_mmi)
	print("[istanbul_life] gulls: %d over the Golden Horn and the Bosphorus" % _n)
