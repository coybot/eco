## Meshes and shaders for Baylands' moving life (baylands_life.gd): the
## park's people (walkers, joggers, cyclists, dog walkers, picnickers, soccer
## players), their bicycles and dogs, a ball, and the birds of the South Bay
## shore: Canada geese, gulls, great and snowy egrets, great blue herons,
## American white pelicans, mallards, avocets and stilts, swallows and a
## red-tailed hawk.
##
## Everything is procedural (no downloaded models): low-poly ellipsoid bodies,
## tapered necks and flat double-sided wings, sized to each species' published
## length and wingspan (Sibley / Cornell Lab "All About Birds" ranges). From a
## drone at 10-30 m a goose is 20-60 pixels: the proportions, colours and
## the wing pose are what a detector and a pilot see.
##
## ONE SHADER PER FAMILY, NOTHING PER BIRD ON THE CPU WHILE IT RESTS
##
## People (PEOPLE_SHADER, also dogs, bicycles and the ball): each instance
## carries its speed along its own +Z and the life clock's time of its last
## CPU tick; the vertex shader carries it on between ticks (as life_meshes.gd
## does for cars) and poses it: stand, walk, jog, sit, pedal, lie, run.
## Birds (BIRD_SHADER): per instance x = how open the wings are (0 folded,
## 1 spread), y = flap amplitude (0 glides), z = feeding (head-down bouts),
## w = variant + 4 * floating + seed. The CPU writes a bird's transform only
## while its flock is in the air. Circling birds (gulls over the ponds and
## the transfer station, swallows, a hawk) use the same shader with
## `circling` on: the instance origin is the centre of the circle and the
## whole flight is in the shader; baylands_life_birds.circle_pos() is the
## same formula on the CPU, so a strike check sees the bird where it is drawn.
##
## Vertex tags: UV.x signed span fraction on wings (+-1 at the tips), limbs on
## people (+-1 legs, +-2 arms; dogs +-1 front legs, +-3 hind legs); UV.y the
## bird part (0 body, 1 neck/head/bill, 2 legs, 3 wing).
extends RefCounted

const LM = preload("res://scripts/life_meshes.gd")

static var _cache: Dictionary = {}
static var _mats: Dictionary = {}

## Bird species: sizes in metres (span, half-length rl, half-width rw,
## half-height rh, leg length, neck rise and reach, head radius, bill length),
## sRGB colours, wingbeat Hz, flight neck (+rad forward, <0 retracted).
const SPECIES := {
	# Canada goose: 76-110 cm long, span 127-185 cm, 3-6.5 kg; ~2.7 beats/s.
	"goose": {"span": 1.65, "rl": 0.36, "rw": 0.15, "rh": 0.14, "leg": 0.17, "neck": [0.40, 0.14],
		"head": 0.055, "bill": 0.055, "body": Color(0.47, 0.40, 0.32), "belly": Color(0.80, 0.76, 0.68),
		"neckc": Color(0.05, 0.05, 0.05), "headc": Color(0.05, 0.05, 0.05), "billc": Color(0.05, 0.05, 0.05),
		"wing": Color(0.42, 0.36, 0.29), "tip": Color(0.18, 0.16, 0.14), "tail": Color(0.07, 0.07, 0.07),
		"legc": Color(0.08, 0.08, 0.08), "cheek": Color(0.95, 0.95, 0.93), "hz": 2.7, "fly_neck": 1.15, "mass": 4.5},
	# Great egret: 94-104 cm, span 131-145 cm, ~1 kg. Snowy (variant 1): 56-66 cm, black bill.
	"egret": {"span": 1.40, "rl": 0.19, "rw": 0.085, "rh": 0.10, "leg": 0.52, "neck": [0.46, 0.12],
		"head": 0.04, "bill": 0.12, "body": Color(0.96, 0.96, 0.94), "belly": Color(0.94, 0.94, 0.92),
		"neckc": Color(0.96, 0.96, 0.94), "headc": Color(0.96, 0.96, 0.94), "billc": Color(0.93, 0.78, 0.18),
		"wing": Color(0.97, 0.97, 0.95), "tip": Color(0.95, 0.95, 0.93), "tail": Color(0.95, 0.95, 0.93),
		"legc": Color(0.06, 0.06, 0.06), "hz": 2.0, "fly_neck": -1.0, "mass": 0.9},
	# Great blue heron: 97-137 cm, span 167-201 cm, 2-2.5 kg.
	"heron": {"span": 1.85, "rl": 0.25, "rw": 0.11, "rh": 0.12, "leg": 0.60, "neck": [0.50, 0.16],
		"head": 0.05, "bill": 0.14, "body": Color(0.44, 0.49, 0.57), "belly": Color(0.38, 0.40, 0.46),
		"neckc": Color(0.60, 0.58, 0.62), "headc": Color(0.90, 0.90, 0.88), "billc": Color(0.85, 0.65, 0.25),
		"wing": Color(0.46, 0.52, 0.60), "tip": Color(0.18, 0.20, 0.26), "tail": Color(0.40, 0.45, 0.52),
		"legc": Color(0.35, 0.30, 0.22), "hz": 2.0, "fly_neck": -1.0, "mass": 2.3},
	# American white pelican: 127-178 cm, span 244-290 cm, 5-9 kg; black flight feathers.
	"pelican": {"span": 2.70, "rl": 0.55, "rw": 0.23, "rh": 0.21, "leg": 0.16, "neck": [0.30, 0.22],
		"head": 0.085, "bill": 0.34, "body": Color(0.97, 0.97, 0.95), "belly": Color(0.95, 0.95, 0.93),
		"neckc": Color(0.97, 0.97, 0.95), "headc": Color(0.97, 0.97, 0.95), "billc": Color(0.96, 0.62, 0.22),
		"wing": Color(0.97, 0.97, 0.95), "tip": Color(0.06, 0.06, 0.06), "tail": Color(0.96, 0.96, 0.94),
		"legc": Color(0.95, 0.6, 0.25), "hz": 1.6, "fly_neck": -1.0, "mass": 7.0},
	# Mallard: 50-65 cm, span 82-95 cm, ~1.1 kg; ~5 beats/s. Variant 1: the brown hen.
	"duck": {"span": 0.90, "rl": 0.25, "rw": 0.12, "rh": 0.10, "leg": 0.06, "neck": [0.13, 0.08],
		"head": 0.055, "bill": 0.055, "body": Color(0.60, 0.57, 0.52), "belly": Color(0.75, 0.73, 0.70),
		"neckc": Color(0.08, 0.30, 0.15), "headc": Color(0.08, 0.30, 0.15), "billc": Color(0.85, 0.75, 0.2),
		"wing": Color(0.50, 0.46, 0.40), "tip": Color(0.35, 0.32, 0.28), "tail": Color(0.15, 0.15, 0.15),
		"legc": Color(0.9, 0.5, 0.15), "hz": 5.0, "fly_neck": 0.9, "mass": 1.1},
	# American avocet: 40-51 cm, span 68-76 cm, ~0.3 kg; variant 1 black-necked stilt.
	"shorebird": {"span": 0.72, "rl": 0.16, "rw": 0.065, "rh": 0.065, "leg": 0.32, "neck": [0.14, 0.06],
		"head": 0.03, "bill": 0.09, "body": Color(0.95, 0.95, 0.93), "belly": Color(0.96, 0.96, 0.94),
		"neckc": Color(0.78, 0.55, 0.35), "headc": Color(0.78, 0.55, 0.35), "billc": Color(0.05, 0.05, 0.05),
		"wing": Color(0.08, 0.08, 0.08), "tip": Color(0.06, 0.06, 0.06), "tail": Color(0.9, 0.9, 0.88),
		"legc": Color(0.45, 0.55, 0.62), "hz": 4.5, "fly_neck": 0.8, "mass": 0.3},
	# Western / California gull: 46-66 cm, span 122-144 cm, 0.6-1.2 kg.
	"gull": {"span": 1.35, "rl": 0.24, "rw": 0.09, "rh": 0.09, "leg": 0.11, "neck": [0.09, 0.08],
		"head": 0.055, "bill": 0.06, "body": Color(0.96, 0.96, 0.95), "belly": Color(0.97, 0.97, 0.96),
		"neckc": Color(0.96, 0.96, 0.95), "headc": Color(0.96, 0.96, 0.95), "billc": Color(0.95, 0.80, 0.2),
		"wing": Color(0.55, 0.58, 0.62), "tip": Color(0.07, 0.07, 0.08), "tail": Color(0.95, 0.95, 0.95),
		"legc": Color(0.85, 0.6, 0.55), "hz": 3.0, "fly_neck": 0.2, "mass": 0.9},
	# Barn / cliff swallow: 15-19 cm, span 29-32 cm, ~20 g.
	"swallow": {"span": 0.32, "rl": 0.075, "rw": 0.028, "rh": 0.026, "leg": 0.01, "neck": [0.02, 0.03],
		"head": 0.018, "bill": 0.008, "body": Color(0.10, 0.14, 0.32), "belly": Color(0.85, 0.66, 0.48),
		"neckc": Color(0.55, 0.25, 0.12), "headc": Color(0.10, 0.14, 0.32), "billc": Color(0.05, 0.05, 0.05),
		"wing": Color(0.09, 0.12, 0.26), "tip": Color(0.06, 0.08, 0.18), "tail": Color(0.08, 0.10, 0.24),
		"legc": Color(0.1, 0.1, 0.1), "hz": 8.0, "fly_neck": 0.0, "mass": 0.02},
	# Red-tailed hawk: 45-65 cm, span 114-133 cm, 0.7-1.5 kg.
	"hawk": {"span": 1.25, "rl": 0.24, "rw": 0.11, "rh": 0.10, "leg": 0.10, "neck": [0.08, 0.08],
		"head": 0.06, "bill": 0.035, "body": Color(0.42, 0.28, 0.17), "belly": Color(0.86, 0.80, 0.68),
		"neckc": Color(0.42, 0.28, 0.17), "headc": Color(0.38, 0.25, 0.15), "billc": Color(0.15, 0.15, 0.15),
		"wing": Color(0.45, 0.32, 0.20), "tip": Color(0.18, 0.12, 0.08), "tail": Color(0.72, 0.36, 0.20),
		"legc": Color(0.85, 0.75, 0.3), "hz": 2.5, "fly_neck": 0.0, "mass": 1.1},
}


## A bird mesh for SPECIES[name], +Z forward, feet at y = 0.
static func bird_mesh(name: String) -> ArrayMesh:
	var key := "bird_" + name
	if _cache.has(key):
		return _cache[key]
	var s: Dictionary = SPECIES[name]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var rl: float = s["rl"]
	var rw: float = s["rw"]
	var rh: float = s["rh"]
	var leg: float = s["leg"]
	var by := leg + rh * 0.8
	# Body: an ellipsoid, belly-coloured underneath.
	_ellipsoid(st, Vector3(0, by, 0), Vector3(rw, rh, rl), s["body"], s["belly"], 0.0, 7, 4)
	# Neck: from the breast up and forward to the head.
	var nb := Vector3(0, by + rh * 0.45, rl * 0.72)
	var nk: Array = s["neck"]
	var hp := nb + Vector3(0, float(nk[0]), float(nk[1]))
	var hr: float = s["head"]
	_tube(st, nb, hp, maxf(rw * 0.42, hr * 0.8), hr * 0.75, s["neckc"], 1.0, 5)
	_ellipsoid(st, hp, Vector3(hr * 0.85, hr, hr * 1.15), s["headc"], s["headc"], 1.0, 5, 3)
	if s.has("cheek"):
		_ellipsoid(st, hp + Vector3(0, -hr * 0.25, -hr * 0.1), Vector3(hr * 0.9, hr * 0.5, hr * 0.6),
			s["cheek"], s["cheek"], 1.0, 5, 2)
	var bl: float = s["bill"]
	_tube(st, hp + Vector3(0, -hr * 0.1, hr * 0.8), hp + Vector3(0, -hr * 0.3, hr * 0.8 + bl),
		hr * (0.55 if name == "pelican" else 0.35), hr * 0.08, s["billc"], 1.0, 4)
	# Tail.
	var tl := rl * (0.55 if name != "swallow" else 1.1)
	_tri2(st, Vector3(-rw * 0.6, by + rh * 0.15, -rl * 0.75), Vector3(rw * 0.6, by + rh * 0.15, -rl * 0.75),
		Vector3(0, by + rh * 0.1, -rl * 0.75 - tl), s["tail"], Vector2(0, 0))
	if name == "swallow":
		for sx in [-1.0, 1.0]:
			_tri2(st, Vector3(sx * rw * 0.2, by, -rl), Vector3(sx * rw * 1.6, by, -rl - tl * 1.1),
				Vector3(0, by, -rl - tl * 0.4), s["tail"], Vector2(0, 0))
	# Legs.
	if leg > 0.03:
		for sx in [-1.0, 1.0]:
			_tube(st, Vector3(sx * rw * 0.35, by - rh * 0.5, -rl * 0.05), Vector3(sx * rw * 0.35, 0.0, 0.0),
				maxf(leg * 0.035, 0.008), maxf(leg * 0.03, 0.007), s["legc"], 2.0, 4)
	# Wings: an inner panel (the arm) and an outer (the hand, the primaries).
	var half: float = float(s["span"]) * 0.5
	var wy := by + rh * 0.45
	var chord := rl * (0.75 if name != "pelican" else 0.62)
	for sx in [-1.0, 1.0]:
		var r0 := Vector3(sx * rw * 0.7, wy, rl * 0.35)
		var r1 := Vector3(sx * rw * 0.7, wy, rl * 0.35 - chord)
		var m0 := Vector3(sx * half * 0.5, wy, rl * 0.32)
		var m1 := Vector3(sx * half * 0.5, wy, rl * 0.32 - chord * 0.95)
		var t0 := Vector3(sx * half, wy, rl * 0.05)
		var t1 := Vector3(sx * half * 0.94, wy, rl * 0.05 - chord * 0.55)
		var inner: Color = s["wing"]
		if name == "pelican":
			# The secondaries are black too: only the forward half of the arm is white.
			var mid0 := r0.lerp(r1, 0.55)
			var mid1 := m0.lerp(m1, 0.55)
			_quad2(st, r0, m0, mid1, mid0, inner, [0.0, sx * 0.5, sx * 0.5, 0.0])
			_quad2(st, mid0, mid1, m1, r1, s["tip"], [0.0, sx * 0.5, sx * 0.5, 0.0])
		else:
			_quad2(st, r0, m0, m1, r1, inner, [0.0, sx * 0.5, sx * 0.5, 0.0])
		_quad2(st, m0, t0, t1, m1, s["tip"] if name != "gull" else inner, [sx * 0.5, sx * 1.0, sx * 1.0, sx * 0.5])
		if name == "gull":
			# Black wingtips: the last fifth.
			var k0 := m0.lerp(t0, 0.7)
			var k1 := m1.lerp(t1, 0.7)
			_quad2(st, k0 + Vector3(0, 0.002, 0), t0 + Vector3(0, 0.002, 0), t1 + Vector3(0, 0.002, 0),
				k1 + Vector3(0, 0.002, 0), s["tip"], [sx * 0.85, sx * 1.0, sx * 1.0, sx * 0.85])
	var m := st.commit()
	_cache[key] = m
	return m


## Neck pivot (y, z) and hip pivot y of SPECIES[name], for the shader.
static func bird_pivots(name: String) -> Vector4:
	var s: Dictionary = SPECIES[name]
	var by: float = float(s["leg"]) + float(s["rh"]) * 0.8
	return Vector4(by + float(s["rh"]) * 0.45, float(s["rl"]) * 0.72, by - float(s["rh"]) * 0.5,
		float(s["leg"]) + float(s["rh"]) * 0.5)


## The bird material for a species: `circling` draws gulls/swallows/hawks
## flying circles about their instance origins (all in the shader).
static func bird_material(name: String, circling: bool) -> ShaderMaterial:
	var key := "%s_%d" % [name, int(circling)]
	if _mats.has(key):
		return _mats[key]
	var sh: Shader
	if _cache.has("_bird_shader"):
		sh = _cache["_bird_shader"]
	else:
		sh = Shader.new()
		sh.code = BIRD_SHADER
		_cache["_bird_shader"] = sh
	var m := ShaderMaterial.new()
	m.shader = sh
	var s: Dictionary = SPECIES[name]
	var pv := bird_pivots(name)
	m.set_shader_parameter("neck_pivot", Vector2(pv.x, pv.y))
	m.set_shader_parameter("hip_y", pv.z)
	m.set_shader_parameter("float_drop", pv.w)
	m.set_shader_parameter("flap_hz", float(s["hz"]))
	m.set_shader_parameter("fly_neck", float(s["fly_neck"]))
	m.set_shader_parameter("circling", circling)
	m.set_shader_parameter("swallow", name == "swallow")
	m.set_shader_parameter("soar", name == "hawk")
	m.set_shader_parameter("species", ["goose", "egret", "heron", "pelican", "duck", "shorebird",
		"gull", "swallow", "hawk"].find(name))
	_mats[key] = m
	return m


static func all_bird_materials() -> Array:
	return _mats.values()


# ------------------------------------------------------------------ people kit

static func people_material(kind: int) -> ShaderMaterial:
	var key := "people_%d" % kind
	if _mats.has(key):
		return _mats[key]
	var sh: Shader
	if _cache.has("_people_shader"):
		sh = _cache["_people_shader"]
	else:
		sh = Shader.new()
		sh.code = PEOPLE_SHADER
		_cache["_people_shader"] = sh
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("kind", kind)
	_mats[key] = m
	return m


static func people_materials() -> Array:
	var out: Array = []
	for k in _mats:
		if str(k).begins_with("people_"):
			out.append(_mats[k])
	return out


## A road bicycle, +Z forward, wheels on the ground: 1.75 m long, 700c wheels.
static func bike_mesh() -> ArrayMesh:
	if _cache.has("bike"):
		return _cache["bike"]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var tyre := Color(0.05, 0.05, 0.05, 1.0)
	var frame := Color(1, 1, 1, 0.0)            # alpha 0: the instance's paint
	var metal := Color(0.55, 0.56, 0.58, 1.0)
	var r := 0.34
	for zc in [-0.51, 0.51]:
		var n := 12
		for k in n:
			var a0 := TAU * float(k) / n
			var a1 := TAU * float(k + 1) / n
			var p0 := Vector3(0, r + r * cos(a0), zc + r * sin(a0))
			var p1 := Vector3(0, r + r * cos(a1), zc + r * sin(a1))
			_tube(st, p0, p1, 0.022, 0.022, tyre, 0.0, 4)
	var bb := Vector3(0, 0.30, 0.0)              # bottom bracket
	var seat := Vector3(0, 0.90, -0.16)
	var head := Vector3(0, 0.86, 0.40)
	_tube(st, Vector3(0, r, -0.51), bb, 0.016, 0.016, frame, 0.0, 4)
	_tube(st, Vector3(0, r, -0.51), seat + Vector3(0, -0.06, 0), 0.014, 0.014, frame, 0.0, 4)
	_tube(st, bb, seat, 0.02, 0.02, frame, 0.0, 4)
	_tube(st, bb, head + Vector3(0, -0.12, 0), 0.025, 0.025, frame, 0.0, 4)
	_tube(st, seat + Vector3(0, -0.06, 0), head, 0.02, 0.02, frame, 0.0, 4)
	_tube(st, head + Vector3(0, -0.12, 0), Vector3(0, r, 0.51), 0.016, 0.016, metal, 0.0, 4)
	_tube(st, head, head + Vector3(0, 0.08, 0.04), 0.016, 0.016, metal, 0.0, 4)
	_tube(st, head + Vector3(-0.22, 0.08, 0.06), head + Vector3(0.22, 0.08, 0.06), 0.014, 0.014, metal, 0.0, 4)
	_tube(st, seat + Vector3(0, 0.02, -0.06), seat + Vector3(0, 0.02, 0.14), 0.04, 0.03, tyre, 0.0, 4)
	st.generate_normals()
	var m := st.commit()
	_cache["bike"] = m
	return m


## A medium dog (a Labrador's size: 55-60 cm at the shoulder), +Z forward.
## Legs tagged +-1 (front) and +-3 (hind) for the trot.
static func dog_mesh() -> ArrayMesh:
	if _cache.has("dog"):
		return _cache["dog"]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var coat := Color(1, 1, 1, 0.0)
	var dark := Color(0.08, 0.07, 0.06, 1.0)
	_ellipsoid(st, Vector3(0, 0.50, 0), Vector3(0.13, 0.14, 0.33), coat, coat, 0.0, 8, 5)
	_tube(st, Vector3(0, 0.55, 0.26), Vector3(0, 0.66, 0.40), 0.08, 0.07, coat, 0.0, 6)
	_ellipsoid(st, Vector3(0, 0.68, 0.44), Vector3(0.08, 0.08, 0.10), coat, coat, 0.0, 6, 4)
	_tube(st, Vector3(0, 0.66, 0.50), Vector3(0, 0.63, 0.62), 0.045, 0.035, coat, 0.0, 4)
	_ellipsoid(st, Vector3(0, 0.63, 0.625), Vector3(0.025, 0.02, 0.02), dark, dark, 0.0, 4, 3)
	for sx in [-1.0, 1.0]:
		_ellipsoid(st, Vector3(sx * 0.07, 0.70, 0.40), Vector3(0.025, 0.06, 0.04), coat, coat, 0.0, 4, 3)
	_tube(st, Vector3(0, 0.56, -0.30), Vector3(0, 0.62, -0.55), 0.03, 0.015, coat, 0.0, 4)
	for sx in [-1.0, 1.0]:
		for zf in [0.22, -0.22]:
			var tag: float = sx * (1.0 if zf > 0.0 else 3.0)
			var a := Vector3(sx * 0.075, 0.44, zf)
			_tube_tagged(st, a, Vector3(sx * 0.075, 0.0, zf), 0.032, 0.026, coat, tag)
	st.generate_normals()
	var m := st.commit()
	_cache["dog"] = m
	return m


## A size-5 football (22 cm), sitting on the ground.
static func ball_mesh() -> ArrayMesh:
	if _cache.has("ball"):
		return _cache["ball"]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_ellipsoid(st, Vector3(0, 0.11, 0), Vector3(0.11, 0.11, 0.11), Color(0.97, 0.97, 0.97, 1.0),
		Color(0.9, 0.9, 0.9, 1.0), 0.0, 8, 5)
	var m := st.commit()
	_cache["ball"] = m
	return m


# ------------------------------------------------------------------ builders

static func _put(st: SurfaceTool, p: Vector3, n: Vector3, c: Color, uv: Vector2) -> void:
	st.set_normal(n)
	st.set_color(c)
	st.set_uv(uv)
	st.add_vertex(p)


## A low ellipsoid; `under` colours the lower half; UV.y = part.
static func _ellipsoid(st: SurfaceTool, c: Vector3, r: Vector3, col: Color, under: Color, part: float,
		seg: int, rings: int) -> void:
	var pts: Array = []
	var nrm: Array = []
	for j in rings + 1:
		var v := PI * float(j) / rings
		var row: Array = []
		var nrow: Array = []
		for i in seg + 1:
			var u := TAU * float(i) / seg
			var d := Vector3(sin(v) * cos(u), cos(v), sin(v) * sin(u))
			row.append(c + Vector3(d.x * r.x, d.y * r.y, d.z * r.z))
			nrow.append(Vector3(d.x / r.x, d.y / r.y, d.z / r.z).normalized())
		pts.append(row)
		nrm.append(nrow)
	for j in rings:
		for i in seg:
			var q := [[j, i], [j + 1, i], [j + 1, i + 1], [j, i + 1]]
			for t: Array in [[0, 1, 2], [0, 2, 3]]:
				var vs := []
				var ns := []
				for k: int in t:
					vs.append(pts[q[k][0]][q[k][1]])
					ns.append(nrm[q[k][0]][q[k][1]])
				var a: Vector3 = vs[0]
				var b: Vector3 = vs[1]
				var cc: Vector3 = vs[2]
				var na: Vector3 = ns[0]
				var nb: Vector3 = ns[1]
				var nc: Vector3 = ns[2]
				if (b - a).cross(cc - a).dot(na + nb + nc) > 0.0:
					var tv := b
					b = cc
					cc = tv
					var tn := nb
					nb = nc
					nc = tn
				for pr: Array in [[a, na], [b, nb], [cc, nc]]:
					var p: Vector3 = pr[0]
					_put(st, p, pr[1], col if p.y >= c.y - r.y * 0.15 else under, Vector2(0.0, part))


## A tapered tube from a to b (radii ra, rb), open ends.
static func _tube(st: SurfaceTool, a: Vector3, b: Vector3, ra: float, rb: float, col: Color, part: float,
		seg: int) -> void:
	_tube_uv(st, a, b, ra, rb, col, Vector2(0.0, part), seg)


static func _tube_tagged(st: SurfaceTool, a: Vector3, b: Vector3, ra: float, rb: float, col: Color,
		tag: float) -> void:
	_tube_uv(st, a, b, ra, rb, col, Vector2(tag, 0.0), 4)


static func _tube_uv(st: SurfaceTool, a: Vector3, b: Vector3, ra: float, rb: float, col: Color, uv: Vector2,
		seg: int) -> void:
	var ax := (b - a)
	if ax.length() < 1e-4:
		return
	ax = ax.normalized()
	var ref := Vector3.UP if absf(ax.dot(Vector3.UP)) < 0.9 else Vector3.RIGHT
	var u := ax.cross(ref).normalized()
	var v := ax.cross(u).normalized()
	for i in seg:
		var a0 := TAU * float(i) / seg
		var a1 := TAU * float(i + 1) / seg
		var d0 := u * cos(a0) + v * sin(a0)
		var d1 := u * cos(a1) + v * sin(a1)
		var p00 := a + d0 * ra
		var p01 := a + d1 * ra
		var p10 := b + d0 * rb
		var p11 := b + d1 * rb
		for t: Array in [[p00, p10, p11, d0, d0, d1], [p00, p11, p01, d0, d1, d1]]:
			var pa: Vector3 = t[0]
			var pb: Vector3 = t[1]
			var pc: Vector3 = t[2]
			var n: Vector3 = (t[3] + t[4] + t[5]) as Vector3
			if (pb - pa).cross(pc - pa).dot(n) > 0.0:
				var tmp := pb
				pb = pc
				pc = tmp
			_put(st, pa, (pa - a - ax * (pa - a).dot(ax)).normalized(), col, uv)
			_put(st, pb, (pb - a - ax * (pb - a).dot(ax)).normalized(), col, uv)
			_put(st, pc, (pc - a - ax * (pc - a).dot(ax)).normalized(), col, uv)


## A flat quad drawn from both sides by the cull_disabled bird shader.
## tags: UV.x per corner (span fraction); UV.y = 3 (wing).
static func _quad2(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3, col: Color, tags: Array) -> void:
	var pts := [a, b, c, d]
	for t: Array in [[0, 1, 2], [0, 2, 3]]:
		for k: int in t:
			_put(st, pts[k], Vector3.UP, col, Vector2(float(tags[k]), 3.0))


static func _tri2(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, col: Color, uv: Vector2) -> void:
	for p: Vector3 in [a, b, c]:
		_put(st, p, Vector3.UP, col, uv)


# ------------------------------------------------------------------ shaders

const PEOPLE_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

// 0 person, 1 dog, 2 bicycle, 3 ball.
uniform int kind = 0;
// The life clock (s, mod 3600), set every frame by baylands_life.
uniform float now = 0.0;

// INSTANCE_CUSTOM: x colour (packed rgb), y speed (m/s along local +Z),
// z time of the last CPU tick (s, mod 3600), w pose + phase (fract).
// Poses: 0 stand, 1 walk, 2 jog, 3 sit on the ground, 4 pedal, 5 sit on a
// bench, 6 lie on the back, 7 run (soccer), 8 crouch (picnic, children).
varying vec3 v_col;
varying float v_skin;

vec3 unpack_rgb(float f) {
    float v = floor(f + 0.5);
    return vec3(floor(v / 65536.0), mod(floor(v / 256.0), 256.0), mod(v, 256.0)) / 255.0;
}

vec3 to_linear(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}

// Rotate p about the X axis through (0, py, pz) by a (positive swings +Y toward +Z).
vec3 rot_x(vec3 p, float py, float pz, float a) {
    vec3 q = p - vec3(0.0, py, pz);
    float c = cos(a);
    float s = sin(a);
    return vec3(q.x, q.y * c - q.z * s, q.y * s + q.z * c) + vec3(0.0, py, pz);
}

void vertex() {
    vec4 cu = INSTANCE_CUSTOM;
    float pose = floor(cu.w + 1e-3);
    float phase = fract(cu.w);
    vec3 paint = unpack_rgb(cu.x);
    float h = mod(floor(cu.x + 0.5), 7.0);
    float sk = mod(floor(cu.x / 7.0), 5.0);
    vec3 skin = sk < 1.0 ? vec3(0.87, 0.68, 0.55) : (sk < 2.0 ? vec3(0.70, 0.50, 0.37)
        : (sk < 3.0 ? vec3(0.50, 0.35, 0.25) : (sk < 4.0 ? vec3(0.32, 0.22, 0.16) : vec3(0.80, 0.62, 0.48))));
    vec3 pants = h < 2.0 ? vec3(0.17, 0.22, 0.34) : (h < 3.0 ? vec3(0.62, 0.55, 0.42)
        : (h < 4.0 ? vec3(0.07, 0.07, 0.08) : (h < 5.0 ? vec3(0.40, 0.40, 0.42) : vec3(0.12, 0.14, 0.22))));
    vec3 col = COLOR.rgb;
    v_skin = 0.0;
    if (kind == 0) {
        if (COLOR.a < 0.5) { col = paint; }
        else if (COLOR.r > 0.4) { col = skin; v_skin = 1.0; }
        else if (COLOR.b > 0.17) { col = pants; }
        if ((pose == 2.0 || pose == 7.0) && COLOR.b > 0.17 && COLOR.a > 0.5 && COLOR.r < 0.4 && VERTEX.y < 0.5) {
            col = skin;   // runners' bare legs below the shorts
        }
    } else if (COLOR.a < 0.5) {
        col = paint;
    }
    v_col = to_linear(col);

    float sc = max(length(MODEL_MATRIX[2].xyz), 1e-3);
    float dt = now - cu.z;
    if (dt < -1800.0) { dt += 3600.0; }
    dt = clamp(dt, 0.0, 1.5);
    vec3 v = VERTEX;
    vec3 n = NORMAL;
    float tag = UV.x;
    if (kind == 0) {
        float rate = pose == 2.0 ? 9.6 : (pose == 7.0 ? 11.0 : (pose == 4.0 ? 5.5 : 7.0));
        float cyc = now * rate + phase * 62.8;
        if (pose == 1.0 || pose == 2.0 || pose == 7.0) {
            float big = pose == 1.0 ? 1.0 : 1.6;
            if (tag != 0.0) {
                float side = sign(tag);
                bool arm = abs(tag) > 1.5;
                float pivot = arm ? 1.42 : 0.86;
                float amp = arm ? -0.35 * big : 0.45 * big;
                float bend = arm && pose != 1.0 ? -0.9 : 0.0;
                v = rot_x(v, pivot, 0.0, sin(cyc) * amp * side + bend);
                n = rot_x(n, 0.0, 0.0, sin(cyc) * amp * side + bend);
            }
            if (pose != 1.0 && v.y > 0.86) {
                v = rot_x(v, 0.86, 0.0, 0.18);
            }
            v.y += abs(sin(cyc)) * (pose == 1.0 ? 0.03 : 0.07);
        } else if (pose == 3.0 || pose == 5.0 || pose == 8.0) {
            float drop = pose == 3.0 ? 0.74 : (pose == 5.0 ? 0.40 : 0.45);
            if (tag != 0.0 && abs(tag) < 1.5) {
                float a = pose == 5.0 ? -1.35 : (pose == 8.0 ? -2.2 : -1.5);
                v = rot_x(v, 0.86, 0.0, a);
                n = rot_x(n, 0.0, 0.0, a);
            } else if (tag != 0.0) {
                v = rot_x(v, 1.42, 0.0, -0.5 + 0.15 * sin(now * 0.7 + phase * 40.0));
            }
            v.y -= drop;
        } else if (pose == 6.0) {
            v = rot_x(v, 0.0, 0.0, -1.5708);
            n = rot_x(n, 0.0, 0.0, -1.5708);
            v.y += 0.12;
            v.z += 0.86;
        } else if (pose == 4.0) {
            if (tag != 0.0 && abs(tag) < 1.5) {
                float a = -0.95 + sin(cyc + (tag > 0.0 ? 0.0 : 3.1416)) * 0.55;
                v = rot_x(v, 0.86, 0.0, a);
                n = rot_x(n, 0.0, 0.0, a);
            } else if (tag != 0.0) {
                v = rot_x(v, 1.42, 0.0, -1.05);
            }
            if (v.y > 0.86 || abs(tag) > 1.5) {
                v = rot_x(v, 0.86, 0.0, 0.42);
            }
            v.y += 0.10;
            v.z -= 0.14;
        } else {
            // Standing: weight shifts.
            v.x += sin(now * 0.6 + phase * 30.0) * 0.015 * v.y;
        }
    } else if (kind == 1) {
        if (cu.y > 0.2 && tag != 0.0) {
            float cyc = now * 11.0 + phase * 62.8;
            float side = sign(tag) * (abs(tag) > 2.0 ? -1.0 : 1.0);
            float a = sin(cyc) * 0.5 * side;
            v = rot_x(v, 0.44, abs(tag) > 2.0 ? -0.22 : 0.22, a);
        }
        if (cu.y < 0.2 && fract(phase * 7.0) < 0.4) {
            // Sitting dog.
            if (v.z < 0.0) { v.y *= 0.6; }
        }
    } else if (kind == 2) {
        // Bicycle: nothing moves but the whole.
    }
    v.z += cu.y * dt / sc;
    VERTEX = v;
    NORMAL = normalize(n);
}

void fragment() {
    ALBEDO = v_col;
    ROUGHNESS = kind == 2 ? 0.45 : 0.85;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Skin ~33 C, clothes and fur a few degrees cooler; the bicycle is metal at air temperature.
        vec2 th = kind == 2 ? th_surface(0.6, 15.0, 0.2, 0.0)
            : (kind == 3 ? th_surface(0.3, 8.0, 0.3, 0.0) : th_surface(0.5, 5.0, 0.6, mix(5.5, 8.5, v_skin)));
        TH_OUT(th);
    }
}
"""

const BIRD_SHADER := """
shader_type spatial;
render_mode cull_disabled, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

uniform float now = 0.0;              // life clock, s mod 3600
uniform vec2 neck_pivot = vec2(0.5, 0.2);
uniform float hip_y = 0.3;
uniform float float_drop = 0.2;       // how far a floating bird's body sits below its standing height
uniform float flap_hz = 3.0;
uniform float fly_neck = 0.0;         // + rad forward in flight; < 0: drawn in (herons, egrets, pelicans)
uniform bool circling = false;
uniform bool swallow = false;
uniform bool soar = false;
uniform int species = 0;
uniform float flock = 1.0;            // share of circling birds out (0..1)

varying vec3 v_col;

vec3 to_linear(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}

vec3 rot_x(vec3 p, float py, float pz, float a) {
    vec3 q = p - vec3(0.0, py, pz);
    float c = cos(a);
    float s = sin(a);
    return vec3(q.x, q.y * c - q.z * s, q.y * s + q.z * c) + vec3(0.0, py, pz);
}

// Keep in step with baylands_life_birds.circle_pos().
vec3 circle_pos(float t, vec4 cu) {
    float seed = fract(cu.w);
    float r = cu.x * (1.0 + 0.25 * sin(t * 0.11 + seed * 20.0));
    float a = cu.z + cu.y * t;
    vec3 c = vec3(sin(t * 0.021 + seed * 6.0), 0.0, cos(t * 0.017 + seed * 9.0)) * cu.x * 0.7;
    vec3 p = c + vec3(cos(a) * r, 3.0 * sin(t * 0.23 + seed * 13.0), sin(a) * r);
    if (swallow) {
        p += vec3(sin(t * 1.7 + seed * 5.0) * 4.0, sin(t * 2.3 + seed * 3.0) * 1.5, cos(t * 1.9 + seed * 7.0) * 4.0);
    }
    return p;
}

void vertex() {
    vec4 cu = INSTANCE_CUSTOM;
    float seed = fract(cu.w);
    float var_ = mod(floor(cu.w), 4.0);
    bool floating = floor(cu.w) >= 4.0;
    vec3 col = COLOR.rgb;
    // Variants: snowy egret (black bill), mallard hen (brown head), stilt (black back, pink legs).
    if (species == 1 && var_ > 0.5 && UV.y > 0.5 && UV.y < 1.5 && COLOR.g < 0.85 && COLOR.r > 0.8) { col = vec3(0.04); }
    if (species == 4 && var_ > 0.5) {
        if (COLOR.g > COLOR.r + 0.1) { col = vec3(0.45, 0.34, 0.22); }
        else if (UV.y < 0.5 && COLOR.r > 0.5) { col = vec3(0.50, 0.38, 0.25); }
    }
    if (species == 5 && var_ > 0.5) {
        if (UV.y > 1.5 && UV.y < 2.5) { col = vec3(0.95, 0.5, 0.55); }
        if (UV.y > 0.5 && UV.y < 1.5 && COLOR.r > 0.5) { col = vec3(0.05); }
    }
    v_col = to_linear(col);

    float open = cu.x;
    float amp = cu.y;
    float feed = cu.z;
    if (circling) {
        open = 1.0;
        amp = soar ? 0.0 : (swallow ? 0.9 : 0.6 * smoothstep(0.55, 0.7, sin(now * 0.31 + seed * 31.0)));
    }
    bool hidden = circling && seed > flock;
    vec3 v = VERTEX;
    float part = UV.y;
    // Wings: fold to the body at rest, beat in flight (the hand bends more).
    if (part > 2.5) {
        float span = UV.x;
        float folded = mix(0.13, 1.0, open);
        v.x *= folded;
        v.z -= abs(VERTEX.x) * 0.35 * (1.0 - open);
        v.y += 0.02 * (1.0 - open);
        // About +-35 deg at the shoulder (a goose's stroke is ~80-90 deg tip to tip).
        float beat = amp * 0.6 * sin(now * flap_hz * 6.2832 + seed * 40.0);
        float dih = soar ? 0.12 : 0.06;
        float a = (beat + dih) * open * (abs(span) > 0.5 ? 1.15 : 1.0);
        v.y += abs(v.x) * tan(clamp(a, -1.2, 1.2));
    }
    // Neck: head-down feeding bouts at rest; stretched or drawn in when flying.
    if (part > 0.5 && part < 1.5) {
        float bout = smoothstep(-0.2, 0.5, sin(now * 0.37 + seed * 31.0));
        float down = feed * bout * (1.0 - open) * 1.25;
        float a = down + (fly_neck > 0.0 ? fly_neck * open : 0.0);
        v = rot_x(v, neck_pivot.x, neck_pivot.y, a);
        if (fly_neck < 0.0) {
            vec3 pv = vec3(0.0, neck_pivot.x, neck_pivot.y);
            v = mix(v, pv + (v - pv) * 0.45, open);
        }
    }
    // Legs trail behind in flight.
    if (part > 1.5 && part < 2.5) {
        v = rot_x(v, hip_y, 0.0, 1.45 * open);
    }
    if (floating) {
        v.y -= float_drop;
        v.x += sin(now * 0.05 + seed * 7.0) * 0.4;
        v.z += cos(now * 0.04 + seed * 5.0) * 0.4;
    }
    if (circling) {
        vec3 p = circle_pos(now, cu);
        vec3 q = circle_pos(now + 0.05, cu);
        vec3 fwd = normalize(q - p + vec3(1e-4, 0.0, 0.0));
        float w = cu.y;
        float r = cu.x;
        float bank = -sign(w) * atan(w * w * r / 9.8);
        float cb = cos(bank);
        float sb = sin(bank);
        v = vec3(v.x * cb - v.y * sb, v.x * sb + v.y * cb, v.z);
        float hh = atan(fwd.x, fwd.z);
        float pitch = -asin(clamp(fwd.y, -0.6, 0.6));
        v = rot_x(v, 0.0, 0.0, pitch);
        float ch = cos(hh);
        float sh = sin(hh);
        v = vec3(v.x * ch + v.z * sh, v.y, -v.x * sh + v.z * ch) + p;
    }
    VERTEX = hidden ? vec3(0.0) : v;
}

void fragment() {
    ALBEDO = v_col;
    ROUGHNESS = 0.8;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Feathers insulate: a bird's outside runs a few degrees over the air.
        TH_OUT(th_surface(0.4, 4.0, 0.2, 5.0));
    }
}
"""
