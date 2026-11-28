## Every pond, slough and channel of the Baylands scene as a water surface at
## its own level. Replaces env_baylands' (empty) base water.
##
## Data (tools/bake_baylands_ground.py, step water; committed):
##   water_bodies.json  each body's level (m NAVD88: the lidar's water
##                      surface, flattened by fetch_baylands at its shore's
##                      10th-percentile ground), whether it is tidal, and its
##                      NAIP colour
##   water_quads.dat    the surface as squares of 2 m cells: each pond's
##                      cells grown 4 m under its banks; the tidal water
##                      (body 0: Guadalupe Slough and the channels into it)
##                      with every cell the tide can reach up to TIDE_MAX
##                      through ground lower than it (tidal.png), so a
##                      rising tide floods the mudflat and marsh plain along
##                      the slough and never the ground behind a levee
## Tidal water stands at env.look.tide (followed on look_changed); the ponds
## behind the levees keep their own levels. env.is_water() stays the
## lidar's own water mask: the ground there is not solid for landing, and
## the extra intertidal ground a high tide covers is still the mask's land.
##
## The ground shades itself under water (a shelving wet-mud floor) and along
## the banks (wet margins); this draws only the surface.
extends Node3D

const DIR := "res://assets/baylands/"
const CHUNK := 2000.0

var env: Node3D
var mat: ShaderMaterial
var bodies: Array = []
var quad_count := 0


static func build(e: Node3D) -> bool:
	var w = load("res://scripts/baylands_water.gd").new()
	w.env = e
	w.name = "water"
	e.add_child(w)
	return w._build()


func _build() -> bool:
	var meta: Dictionary = _json(DIR + "water_bodies.json")
	var raw := FileAccess.get_file_as_bytes(DIR + "water_quads.dat")
	if meta.is_empty() or raw.size() < 12 or raw.slice(0, 4).get_string_from_ascii() != "BWQ1":
		push_warning("[baylands_water] no water_quads.dat / water_bodies.json: run tools/bake_baylands_ground.py")
		return false
	bodies = meta["bodies"]
	var cell := float(meta.get("cell_m", 2.0))
	var bb: Array = meta["bounds_enu"]
	var e0 := float(bb[0])
	var n1 := float(bb[3])
	var nq := raw.decode_u32(4)
	quad_count = nq
	mat = ShaderMaterial.new()
	mat.shader = load("res://shaders/baylands_water.gdshader")
	var ground := env.get_node_or_null("ground")
	if ground != null:
		ground.call("bind", mat)
		(ground.get("look_mats") as Array).append(mat)
	mat.set_shader_parameter("wn1", _normals(1, 0.012))
	mat.set_shader_parameter("wn2", _normals(2, 0.024))
	# Per body: its colour as the ground's albedo mapping would make it, its
	# level, tidal or not.
	var cols := []
	var gain := 0.9
	var gamma := 1.8
	for b in bodies:
		var c: Array = b["colour"]
		var lin := Color(float(c[0]) / 255.0, float(c[1]) / 255.0, float(c[2]) / 255.0).srgb_to_linear()
		# As the shaders map the photo: the gamma on the brightness, the hue kept.
		var y := maxf(lin.r * 0.2126 + lin.g * 0.7152 + lin.b * 0.0722, 1e-4)
		var k := gain * pow(y, gamma)
		cols.append(Color(k * pow(lin.r / y, 1.15), k * pow(lin.g / y, 1.15), k * pow(lin.b / y, 1.15)))
	# Bucket the quads by chunk, then build each chunk's arrays in one go.
	var chunks := {}
	for q in nq:
		var off := 12 + q * 8
		var x := raw.decode_u16(off)
		var y := raw.decode_u16(off + 2)
		var s := raw.decode_u16(off + 4)
		var key := Vector2i(floori((e0 + (x + s * 0.5) * cell) / CHUNK), floori((n1 - (y + s * 0.5) * cell) / CHUNK))
		if not chunks.has(key):
			chunks[key] = []
		(chunks[key] as Array).append(q)
	for key in chunks:
		var qs: Array = chunks[key]
		var pos := PackedVector3Array()
		var col := PackedColorArray()
		var u2 := PackedVector2Array()
		var idx := PackedInt32Array()
		pos.resize(qs.size() * 4)
		col.resize(qs.size() * 4)
		u2.resize(qs.size() * 4)
		idx.resize(qs.size() * 6)
		for n in qs.size():
			var off := 12 + int(qs[n]) * 8
			var x := raw.decode_u16(off)
			var y := raw.decode_u16(off + 2)
			var s := raw.decode_u16(off + 4)
			var bi := raw.decode_u16(off + 6)
			var b: Dictionary = bodies[bi]
			var lvl := 0.0 if b["level"] == null else float(b["level"])
			var uv2 := Vector2(lvl, 1.0 if b["tidal"] else 0.0)
			var qe0 := e0 + x * cell
			var qn1 := n1 - y * cell
			var ee := qe0 + s * cell
			var ns := qn1 - s * cell
			var v := n * 4
			# NW, NE, SW, SE in Godot (x, y, -n); clockwise seen from above.
			pos[v] = Vector3(qe0, lvl, -qn1)
			pos[v + 1] = Vector3(ee, lvl, -qn1)
			pos[v + 2] = Vector3(qe0, lvl, -ns)
			pos[v + 3] = Vector3(ee, lvl, -ns)
			var cc: Color = cols[bi]
			for k in 4:
				col[v + k] = cc
				u2[v + k] = uv2
			var t := n * 6
			idx[t] = v
			idx[t + 1] = v + 1
			idx[t + 2] = v + 2
			idx[t + 3] = v + 1
			idx[t + 4] = v + 3
			idx[t + 5] = v + 2
		var nrm := PackedVector3Array()
		nrm.resize(pos.size())
		nrm.fill(Vector3.UP)
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = pos
		arr[Mesh.ARRAY_NORMAL] = nrm
		arr[Mesh.ARRAY_COLOR] = col
		arr[Mesh.ARRAY_TEX_UV2] = u2
		arr[Mesh.ARRAY_INDEX] = idx
		var m := ArrayMesh.new()
		m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
		var mi := MeshInstance3D.new()
		mi.name = "w_%d_%d" % [key.x, key.y]
		mi.mesh = m
		mi.material_override = mat
		# The tide moves the tidal surface up to ~3.5 m: keep it in the box.
		mi.extra_cull_margin = 4.0
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)
	visible = not "water" in str(IpcServer._get_launch_arg("--bl-hide", "")).split(",")
	if ground != null:
		ground.call("_on_look")
	print("[baylands_water] %d bodies (%d ponds + tidal), %d quads in %d chunks" %
		[bodies.size(), bodies.size() - 1, nq, chunks.size()])
	return true


static func _normals(seed_: int, freq: float) -> NoiseTexture2D:
	var noise := FastNoiseLite.new()
	noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	noise.frequency = freq
	noise.fractal_octaves = 4
	noise.seed = seed_ * 31
	var nt := NoiseTexture2D.new()
	nt.width = 512
	nt.height = 512
	nt.seamless = true
	nt.as_normal_map = true
	nt.bump_strength = 6.0
	nt.generate_mipmaps = true
	nt.noise = noise
	return nt


func _json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var d = JSON.parse_string(FileAccess.get_file_as_string(path))
	return d if d is Dictionary else {}
