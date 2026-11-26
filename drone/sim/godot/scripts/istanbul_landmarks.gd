## Istanbul's landmarks: the skyline that makes the scene Istanbul. Every OSM
## mosque as an Ottoman mosque (stone hall, lead domes on windowed drums,
## semi-domes, domed porticoes and courtyards, pencil minarets with serefe
## balconies), the named imperial mosques and Hagia Sophia at their published
## dimensions, the towers (Galata, Beyazit, Maiden's, Justice), the columns
## and obelisks of the Hippodrome, Topkapi's pavilions and kitchens,
## Dolmabahce and Haydarpasa, the Valens Aqueduct and the Theodosian Land
## Walls with their towers, outer wall and moat.
##
## TWO PHASES (env_istanbul's hooks):
##   claim(env)   static, before anything is drawn: plans every landmark,
##                sets env.b_skip on the OSM footprints it replaces (and drops
##                their solid height to the part they still cover), and adds
##                the rest as env.add_obstacle solids — domes, drums, semi-
##                domes, minarets, towers, walls, aqueduct piers — so the
##                router and strike detection see what is drawn. The plan is
##                kept on the env (meta "istanbul_landmarks_plan").
##   the node     (added after the look and clock) draws that plan: merged
##                meshes per 1 km chunk, ONE shader for everything (the
##                material rides in the vertex colour), MultiMesh for the
##                repeated parts (cupolas, alem finials, merlons, wall
##                towers). Nothing goes in env.props.
##
## FOR THE NIGHT PART (istanbul_night.gd):
##   floodlit() -> Array of {"pos": Vector3 (Godot, at the foot), "radius": m,
##       "height": m (how tall the lit thing is), "kind": "mosque" | "tower"
##       | "palace" | "wall" | "column" | "fountain"}: what Istanbul floodlights.
##       Mosques: pos at the central dome's foot on the ground, radius the
##       hall's half-size, height the dome crown (or minaret tip).
##   minaret_balconies() -> PackedVector3Array: the centre of every serefe
##       ring (Godot, at the balcony floor); the ring radius is
##       balcony_radius(i) (~ shaft + 0.9 m). The real ones carry a ring of
##       lamps, and mahya strings hang between a mosque's minarets.
##   This node already gives its own surfaces their night look: windows glow
##   warm and stone, lead and gold take a warm floodlit emission scaled by
##   night_amount(). A night script that lights them some other way can call
##   set_flood(k) (0..1, -1 = back to automatic) to turn that down.
##
## Materials are thermal-aware (shaders/thermal.gdshaderinc): stone is heavy
## and holds the day's heat, lead and gold are low-emissivity metal and read
## cool, glass reflects the sky.
##
## Sources: OpenStreetMap (ODbL) footprints, minaret points, walls, the
## aqueduct line and named places (assets/istanbul/features.json,
## buildings.dat); published dimensions from Wikipedia/Wikidata (facts).
extends Node3D

const Kit = preload("res://scripts/istanbul_landmark_kit.gd")
const Mosque = preload("res://scripts/istanbul_landmark_mosque.gd")
const META := "istanbul_landmarks_plan"
const FEATURES := "res://assets/istanbul/features.json"
const CHUNK := 2000.0

var env: Node3D
var plan: Dictionary = {}
var _chunks: Dictionary = {}          # Vector2i -> Kit.Parts
var _units: Dictionary = {}           # unit name -> Array[Transform3D]
var _flood: Array = []
var _balc := PackedVector3Array()
var _balc_r := PackedFloat32Array()
var _mat: ShaderMaterial
var _night := -1.0
var _flood_override := -1.0
var _counts := {}


# ================================================================== claim

static func claim(env) -> bool:
	var t0 := Time.get_ticks_msec()
	var feats := {}
	var f := FileAccess.open(FEATURES, FileAccess.READ)
	if f != null:
		var d = JSON.parse_string(f.get_as_text())
		if d is Dictionary:
			feats = d
	var p := {"mosques": [], "sites": [], "walls": [], "features": feats}
	var names: Array = env.building_meta.get("names", [])
	var sites_sc = _module("istanbul_landmark_sites")
	# Named sites first: they claim Hagia Sophia and the palaces' footprints.
	if sites_sc != null:
		sites_sc.plan(env, p)
	var claimed := {}
	for s: Dictionary in p["sites"]:
		for i: int in s.get("claims", []):
			claimed[i] = true
	var mapped := Mosque.mapped_minarets(env, feats)
	var n: int = env.b_first.size()
	for i in n:
		if int(env.b_kind[i]) != 2 or claimed.has(i) or env.b_skip[i] != 0:
			continue
		var nm: String = names[env.b_name[i]] if env.b_name[i] >= 0 and env.b_name[i] < names.size() else ""
		var spec := Mosque.plan(env, i, nm, mapped.get(i, []))
		if not spec.is_empty():
			p["mosques"].append(spec)
	var walls_sc = _module("istanbul_landmark_walls")
	if walls_sc != null:
		walls_sc.plan(env, p)
	p.erase("features")
	env.set_meta(META, p)
	print("[istanbul_landmarks] claim: %d mosques, %d sites, %d wall runs in %d ms"
		% [p["mosques"].size(), p["sites"].size(), p["walls"].size(), Time.get_ticks_msec() - t0])
	return true


static func _module(nm: String):
	var path := "res://scripts/%s.gd" % nm
	if ResourceLoader.exists(path):
		return load(path)
	return null


# =================================================================== node

func _ready() -> void:
	name = "istanbul_landmarks"
	env = get_parent()
	if env == null or not env.has_meta(META):
		return
	var t0 := Time.get_ticks_msec()
	plan = env.get_meta(META)
	for spec: Dictionary in plan["mosques"]:
		Mosque.draw(self, spec)
		var c: Vector2 = spec["c"]
		var tip := float(spec["crown"])
		for m: Dictionary in spec["mins"]:
			tip = maxf(tip, float(m["h"]))
		add_flood(Kit.g(c, float(spec["g"])), maxf(float(spec.get("S", 10.0)) * 0.6, 5.0), tip, "mosque")
	_counts["mosques"] = plan["mosques"].size()
	var sites_sc = _module("istanbul_landmark_sites")
	if sites_sc != null:
		sites_sc.draw(self, plan)
	var walls_sc = _module("istanbul_landmark_walls")
	if walls_sc != null:
		walls_sc.draw(self, plan)
	_flush()
	print("[istanbul_landmarks] built in %d ms: %s, %d floodlit, %d balconies"
		% [Time.get_ticks_msec() - t0, str(_counts), _flood.size(), _balc.size()])


func _process(_dt: float) -> void:
	if _mat == null:
		return
	var nt := float(env.call("night_amount")) if env.has_method("night_amount") else 0.0
	if absf(nt - _night) < 0.01:
		return
	_night = nt
	_mat.set_shader_parameter("glow_e", 2.2 * nt)
	_mat.set_shader_parameter("flood", nt if _flood_override < 0.0 else _flood_override)


# ------------------------------------------------------------ night API

func floodlit() -> Array:
	return _flood


func minaret_balconies() -> PackedVector3Array:
	return _balc


func balcony_radius(i: int) -> float:
	return _balc_r[i] if i >= 0 and i < _balc_r.size() else 2.0


## 0..1 sets the landmarks' own floodlit emission; -1 returns it to
## night_amount().
func set_flood(k: float) -> void:
	_flood_override = k
	_night = -1.0


# ------------------------------------------------------- builder sinks

## The triangle accumulator of the 2 km chunk at sim p.
func parts(p: Vector2) -> Kit.Parts:
	var key := Vector2i(floori(p.x / CHUNK), floori(p.y / CHUNK))
	if not _chunks.has(key):
		_chunks[key] = Kit.Parts.new()
	return _chunks[key]


func inst(unit: String, xf: Transform3D) -> void:
	if not _units.has(unit):
		_units[unit] = []
	_units[unit].append(xf)


## A small lead dome on a low drum, base at `at` (Godot), radius r.
func cupola(at: Vector3, r: float) -> void:
	inst("cupola", Transform3D(Basis.IDENTITY.scaled(Vector3(r, r, r)), at))


## A gold finial, its foot at `at`, `s` metres tall.
func alem(at: Vector3, s: float) -> void:
	inst("alem", Transform3D(Basis.IDENTITY.scaled(Vector3(s, s, s)), at))


func balcony(at: Vector3, r: float) -> void:
	_balc.append(at)
	_balc_r.append(r)


func add_flood(pos: Vector3, radius: float, height: float, kind: String) -> void:
	_flood.append({"pos": pos, "radius": radius, "height": height, "kind": kind})


func count(k: String, n := 1) -> void:
	_counts[k] = int(_counts.get(k, 0)) + n


# ---------------------------------------------------------------- flush

func _flush() -> void:
	var mat := material()
	var tris := 0
	for key: Vector2i in _chunks:
		var pp: Kit.Parts = _chunks[key]
		if pp.v.is_empty():
			continue
		tris += pp.v.size() / 3
		var mi := MeshInstance3D.new()
		mi.name = "lm_%d_%d" % [key.x, key.y]
		mi.mesh = pp.mesh(mat)
		add_child(mi)
	_chunks.clear()
	_counts["tris"] = tris
	for unit: String in _units:
		var xfs: Array = _units[unit]
		var mesh := _unit_mesh(unit, mat)
		var vis := 2500.0 if unit == "alem" or unit == "merlon" else 4500.0
		_multi(unit, mesh, xfs, vis, unit != "alem")
		_counts[unit] = xfs.size()
	_units.clear()


## Instances binned into CHUNK cells, each its own MultiMesh so they cull
## and range-fade as groups.
func _multi(nm: String, mesh: Mesh, xfs: Array, vis_end: float, shadows: bool) -> void:
	var bins := {}
	for xf: Transform3D in xfs:
		var key := Vector2i(floori(xf.origin.x / CHUNK), floori(-xf.origin.z / CHUNK))
		if not bins.has(key):
			bins[key] = []
		bins[key].append(xf)
	for key: Vector2i in bins:
		var arr: Array = bins[key]
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = mesh
		mm.instance_count = arr.size()
		for i in arr.size():
			mm.set_instance_transform(i, arr[i])
		var mi := MultiMeshInstance3D.new()
		mi.name = "%s_%d_%d" % [nm, key.x, key.y]
		mi.multimesh = mm
		mi.visibility_range_end = vis_end
		mi.visibility_range_end_margin = vis_end * 0.15
		mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
		if not shadows:
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)


## Unit meshes for the instanced parts (radius / height 1 at the origin).
func _unit_mesh(unit: String, mat: Material) -> ArrayMesh:
	var P := Kit.Parts.new()
	match unit:
		"cupola":
			P.frustum("stone", Vector3.ZERO, 1.08, 1.08, 0.36, 8, false, 0.0, 0.0, TAU, PI / 8.0)
			P.dome("lead", Vector3(0, 0.36, 0), 1.0, 0.95, 8, 3, 8.0)
			P.frustum("gold", Vector3(0, 1.28, 0), 0.04, 0.02, 0.36, 3, false)
		"alem":
			P.frustum("gold", Vector3.ZERO, 0.035, 0.03, 0.62, 4, false)
			P.frustum("gold", Vector3(0, 0.16, 0), 0.03, 0.1, 0.08, 6, false)
			P.frustum("gold", Vector3(0, 0.24, 0), 0.1, 0.03, 0.08, 6, false)
			P.frustum("gold", Vector3(0, 0.38, 0), 0.03, 0.075, 0.06, 6, false)
			P.frustum("gold", Vector3(0, 0.44, 0), 0.075, 0.03, 0.06, 6, false)
			# The crescent, opening up: a short arc of boxes.
			for k in 4:
				var t := PI * (0.12 + 0.76 * float(k) / 3.0)
				var cpos := Vector3(cos(t) * 0.17, 0.78 - sin(t) * 0.17 + 0.17, 0)
				P.box("gold", cpos, Vector3(0.06, 0.04, 0.03), Basis(Vector3.BACK, t), false)
		_:
			var sc = _module("istanbul_landmark_walls")
			if sc != null and sc.has_method("unit"):
				sc.unit(unit, P)
	return P.mesh(mat)


# ============================================================== material

func material() -> ShaderMaterial:
	if _mat != null:
		return _mat
	var sh := Shader.new()
	sh.code = SHADER
	_mat = ShaderMaterial.new()
	_mat.shader = sh
	var pa := PackedVector4Array()
	var ha := PackedVector4Array()
	var fl := PackedFloat32Array()
	for k: String in Kit.MATS:
		var s: Array = Kit.MATS[k]
		pa.append(Vector4(float(s[1]), float(s[2]), float(s[3]), float(s[4])))
		var h: Array = s[5]
		ha.append(Vector4(h[0], h[1], h[2], h[3]))
		fl.append(float(s[6]))
	while pa.size() < 32:
		pa.append(Vector4(0.9, 0, 0, 0))
		ha.append(Vector4(0.55, 18, 0.4, 0))
		fl.append(0.0)
	_mat.set_shader_parameter("mat_p", pa)
	_mat.set_shader_parameter("mat_h", ha)
	_mat.set_shader_parameter("mat_f", fl)
	return _mat


const SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

// Per material (index in the vertex colour's alpha): rough, metal, grain,
// mode; heat [absorb, sun_c, mass, own_c]; floodlit gain. UV: metres along /
// up a face (or seams on domes); UV2: per-face parameters (wall height, bay;
// for arcades bay and spring).
uniform vec4 mat_p[32];
uniform vec4 mat_h[32];
uniform float mat_f[32];
uniform float glow_e = 0.0;
uniform float flood = 0.0;

varying vec3 w;
varying flat int mid;
void vertex() {
	w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	mid = clamp(int(COLOR.a * 255.0 + 0.5), 0, 31);
}

float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vn(vec2 p) {
	vec2 i = floor(p); vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}

// An arched opening centred in each bay: 1 inside. cx distance from the bay
// centre, y metres above the sill, hw half width, h height to the arch's top.
float arch(float cx, float y, float hw, float h) {
	if (y < 0.0 || cx > hw) return 0.0;
	float sp = h - hw;
	if (y < sp) return 1.0;
	return step(length(vec2(cx, y - sp)), hw);
}

void fragment() {
	vec4 mp = mat_p[mid];
	vec4 heat = mat_h[mid];
	int mode = int(mp.w + 0.5);
	vec3 c = COLOR.rgb;
	float rough = mp.x;
	float metal = mp.y;
	float win = 0.0;
	float fw = max(fwidth(UV.x), fwidth(UV.y));
	float far = smoothstep(0.08, 0.35, fw);
	float n = vn(w.xz * 1.3 + w.y * 0.7) * 0.6 + vn(w.xz * 0.21 + w.y * 0.13) * 0.4;
	c *= 1.0 + mp.z * (n - 0.5);
	if (mode == 1 || mode == 4 || mode == 5) {
		// Ashlar courses: a fine joint every 0.55 m, staggered blocks.
		float cy = UV.y / 0.55;
		float row = floor(cy);
		float bx = fract(UV.x / 1.1 + 0.5 * mod(row, 2.0));
		float j = max(step(fract(cy), 0.06), step(bx, 0.03));
		c *= 1.0 - 0.07 * j * (1.0 - far);
	}
	if (mode == 4) {
		// Hall walls: a row of tall rectangular windows low down, arched
		// ones above every 4.6 m, none within 1.6 m of the cornice.
		float H = UV2.x; float sp = UV2.y > 0.5 ? UV2.y : 4.2;
		float cx = abs(fract(UV.x / sp) - 0.5) * sp;
		float y = UV.y;
		win = arch(cx, y - 1.6, 0.58, 2.1);
		float ry = y - 6.2;
		if (ry > 0.0 && y < H - 1.8) {
			float k = mod(ry, 6.4);
			win = max(win, arch(cx, k, 0.5, 2.7) * step(ry - k + 2.8, H - 1.8));
		}
		if (H < 4.5) win = 0.0;
	} else if (mode == 5) {
		// Drum: a ring of arched windows between piers.
		float H = UV2.x; float sp = max(UV2.y, 0.8);
		float cx = abs(fract(UV.x / sp) - 0.5) * sp;
		win = arch(cx, UV.y - H * 0.16, sp * 0.24, H * 0.68);
	} else if (mode == 6) {
		// Arcade: columns and round arches, open between.
		float bay = max(UV2.x, 0.5); float spring = UV2.y;
		float cx = abs(fract(UV.x / bay) - 0.5) * bay;
		float hw = bay * 0.5 - clamp(bay * 0.09, 0.22, 0.6);
		if (arch(cx, UV.y, hw, spring + hw) > 0.5) { discard; }
		// Voussoirs: alternate light and dark stones round the arch.
		float d = length(vec2(cx, UV.y - spring));
		if (UV.y > spring && d < hw + 0.45) {
			float a = atan(UV.y - spring, cx);
			c *= mix(1.0, 0.82, step(0.5, fract(a * 3.0)));
		}
	} else if (mode == 7) {
		// Lead sheets: standing seams round the dome, laps up it, and the
		// pale bloom of weathered lead.
		float f = fract(UV.x);
		float seam = smoothstep(0.06, 0.0, min(f, 1.0 - f));
		float lap = smoothstep(0.03, 0.0, abs(fract(UV.y * 9.0) - 0.5) - 0.47);
		c *= 1.0 - 0.13 * seam * (1.0 - far) - 0.05 * lap * (1.0 - far);
		c *= 0.93 + 0.1 * vn(vec2(UV.x * 0.7, UV.y * 6.0));
		rough = mix(rough, 0.8, seam * 0.5);
	} else if (mode == 8) {
		// Byzantine banded masonry: 1.1 m of stone, 0.55 m of brick courses.
		float b = fract(w.y / 2.7);
		vec3 brick = vec3(0.33, 0.15, 0.11) * (0.85 + 0.3 * h2(floor(vec2(UV.x * 3.4, w.y * 14.0))));
		float isb = step(0.79, b) * (1.0 - far * 0.5);
		c = mix(c, brick, isb);
		c *= 1.0 - 0.15 * isb * step(fract(w.y * 14.0), 0.25) * (1.0 - far);
	} else if (mode == 9) {
		// Brick: thin pale mortar courses.
		float m = step(fract(w.y * 14.0), 0.2);
		c = mix(c, vec3(0.6, 0.55, 0.48), m * 0.5 * (1.0 - far));
	} else if (mode == 10) {
		// Palace front: tall windows every 3.6 m per 6 m storey, pilasters.
		float H = UV2.x;
		float cx = abs(fract(UV.x / 3.6) - 0.5) * 3.6;
		float k = mod(UV.y, 6.0);
		if (UV.y < H - 1.4) win = arch(cx, k - 1.3, 0.62, 3.4);
		float pil = step(1.55, cx);
		c *= 1.0 + 0.06 * pil;
		c *= 1.0 - 0.1 * step(abs(k - 5.6), 0.12);
	}
	vec3 glass = vec3(0.045, 0.05, 0.06);
	if (win > 0.0) {
		c = mix(c, glass, win * (1.0 - far * 0.55));
		rough = mix(rough, 0.25, win);
	}
	if (mode == 11) { rough = 0.12; }
	ALBEDO = c;
	ROUGHNESS = rough;
	METALLIC = metal;
	// Night: windows glow from the lamps inside; floodlights wash the stone,
	// lead and gold warm, brighter low down.
	vec3 warm = vec3(1.0, 0.78, 0.48);
	EMISSION = warm * glow_e * win * 1.4;
	float fl = mat_f[mid] * flood;
	if (fl > 0.0) {
		EMISSION += c * warm * fl * 1.3;
	}
	if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
		float own = heat.w + win * 4.0 * glow_e * 0.45;
		vec2 th = th_surface(heat.x, heat.y, heat.z, own);
		th = th_air(th, length(CAMERA_POSITION_WORLD - w));
		TH_OUT(th);
	}
}
"""
