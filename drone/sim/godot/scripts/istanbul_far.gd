## The city beyond the detailed scene: every OpenStreetMap building in the
## terrain's reach (wide_bounds) outside sim_bounds, so the horizon is a real
## Istanbul skyline: Levent's and Maslak's towers to the north, Besiktas and
## Sisli, the Asian side's hills from Uskudar to Kadikoy, the Marmara shore
## west past Zeytinburnu.
##
## Data: assets/istanbul/far.dat (committed: footprints >= 220 m2 or >= 18 m
## tall, ~81k) or _ground/far_all.dat (every one, ~236k, when
## tools/fetch_istanbul_ground.py has been run), each an oriented box: the
## footprint's minimum-area rectangle and its height (OSM height or levels,
## else the JRC GHSL 100 m average height). OSM ODbL, GHSL CC BY 4.0.
##
## Drawn as MultiMeshes of one unit box per building in three tiers, a
## MultiMesh per tier per chunk so whole chunks are frustum- and range-culled:
##   tall  (>= 25 m)  8 km chunks, to 30 km: the skyline from anywhere
##   mid              2 km chunks, to 7 km
##   small (far_all only: < 220 m2 and < 18 m)  2 km chunks, to 3.5 km
## Beyond those ranges the Sentinel-2 ground (istanbul_ground.gd) carries the
## city's roofs. The look: plaster walls in the city's palette (cream, white,
## ochre, rose, pale grey), a window grid by floor, terracotta hipped roofs on
## most low and mid-rise blocks, flat grey roofs on big footprints, blue-grey
## glass on the towers; lit windows at night from night_amount(); thermal.
##
## SOLID: the tall tier (>= 25 m) is collision layer 1 (LAYER_STRUCTURE,
## fixedwing_manager), one StaticBody3D of box shapes per chunk, so an aircraft
## flown out to Levent hits the towers and occlusion sees them. The rest is
## visual only: outside the router's grid and lower than any sane flight.
extends Node3D

const TALL_M := 25.0
const TALL_CHUNK := 8000.0
const LOW_CHUNK := 2000.0
const TALL_RANGE := 30000.0
const MID_RANGE := 7000.0
const SMALL_RANGE := 3500.0
const LEAN_AREA := 220.0
const LEAN_H := 18.0

var env: Node3D
var count := 0
var solid := 0
var _mat: ShaderMaterial
var _night_last := -1.0


func _ready() -> void:
	name = "istanbul_far"
	env = get_parent() as Node3D
	var t0 := Time.get_ticks_msec()
	var path := "res://assets/istanbul/_ground/far_all.dat"
	if not FileAccess.file_exists(path):
		path = "res://assets/istanbul/far.dat"
	var raw := FileAccess.get_file_as_bytes(path)
	if raw.size() < 8 or raw.slice(0, 4).get_string_from_ascii() != "ISF1":
		push_warning("[istanbul_far] no far.dat: run drone/sim/tools/fetch_istanbul_ground.py")
		return
	var n := raw.decode_u32(4)
	_mat = _material()
	var core: Rect2 = env.call("sim_bounds")
	var chunks := {}        # Vector3i(cx, cy, tier) -> [[xf, custom], ...]
	var bodies := {}        # Vector2i -> StaticBody3D
	for i in n:
		var o := 8 + i * 13
		var c := Vector2(raw.decode_s16(o) * 0.5, raw.decode_s16(o + 2) * 0.5)
		var w := raw.decode_u16(o + 4) * 0.1
		var d := raw.decode_u16(o + 6) * 0.1
		var yaw := deg_to_rad(raw.decode_u16(o + 8) * 0.01)
		var h := raw.decode_u16(o + 10) * 0.1
		var kind := raw[o + 12]
		if core.has_point(c) or w < 1.0 or d < 1.0 or h < 2.0:
			continue
		var tier := 2 if h >= TALL_M else (1 if (w * d >= LEAN_AREA or h >= LEAN_H) else 0)
		var ch := TALL_CHUNK if tier == 2 else LOW_CHUNK
		# Stand it on its lowest corner, sunk a little into the slope.
		var base := INF
		var u := Vector2(cos(yaw), sin(yaw))
		var v := Vector2(-u.y, u.x)
		for k in [Vector2(1, 1), Vector2(1, -1), Vector2(-1, 1), Vector2(-1, -1)]:
			var p: Vector2 = c + u * (w * 0.5 * k.x) + v * (d * 0.5 * k.y)
			base = minf(base, float(env.call("ground_height", p.x, p.y)))
		base -= 0.6
		var top := base + h + 0.6
		# Godot: x east, z = -north; a yaw counter-clockwise from east in ENU
		# is a rotation about +y by the same angle.
		var basis := Basis(Vector3.UP, yaw) * Basis.from_scale(Vector3(w, top - base, d))
		var xf := Transform3D(basis, Vector3(c.x, (base + top) * 0.5, -c.y))
		var seed := fposmod(c.x * 0.0137 + c.y * 0.0291, 1.0)
		var roof := 0.0
		# Terracotta hipped roofs on most houses and mid-rise blocks; flat
		# on big footprints, industry and towers.
		if h < 32.0 and w * d < 1600.0 and kind != 5 and fposmod(seed * 7.31, 1.0) < 0.78:
			roof = 1.0
		var key := Vector3i(int(floor(c.x / ch)), int(floor(c.y / ch)), tier)
		if not chunks.has(key):
			chunks[key] = []
		chunks[key].append([xf, Color(seed, float(kind) / 255.0, roof, minf(h / 300.0, 1.0))])
		count += 1
		if tier == 2:
			var bk := Vector2i(key.x, key.y)
			if not bodies.has(bk):
				var body := StaticBody3D.new()
				body.name = "far_solid_%d_%d" % [bk.x, bk.y]
				body.collision_layer = 1     # LAYER_STRUCTURE (fixedwing_manager.gd)
				body.collision_mask = 0
				bodies[bk] = body
				add_child(body)
			var cs := CollisionShape3D.new()
			var shape := BoxShape3D.new()
			shape.size = Vector3(w, top - base, d)
			cs.shape = shape
			cs.transform = Transform3D(Basis(Vector3.UP, yaw), xf.origin)
			(bodies[bk] as StaticBody3D).add_child(cs)
			solid += 1
	var box := _box_mesh()
	for key: Vector3i in chunks:
		var list: Array = chunks[key]
		var ch := TALL_CHUNK if key.z == 2 else LOW_CHUNK
		var centre := Vector3((key.x + 0.5) * ch, 0.0, -(key.y + 0.5) * ch)
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = box
		mm.instance_count = list.size()
		for i in list.size():
			var xf: Transform3D = list[i][0]
			xf.origin -= centre
			mm.set_instance_transform(i, xf)
			mm.set_instance_custom_data(i, list[i][1])
		var mmi := MultiMeshInstance3D.new()
		mmi.name = "far_%s_%d_%d" % [["small", "mid", "tall"][key.z], key.x, key.y]
		mmi.multimesh = mm
		mmi.position = centre
		mmi.material_override = _mat
		mmi.visibility_range_end = [SMALL_RANGE, MID_RANGE, TALL_RANGE][key.z]
		mmi.visibility_range_end_margin = 400.0
		# No shadows: each far chunk would be a draw in every cascade too.
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mmi)
	print("[istanbul_far] %d far buildings (%s) in %d chunks, %d solid, %d ms" %
		[count, path.get_file(), chunks.size(), solid, Time.get_ticks_msec() - t0])


func _process(_dt: float) -> void:
	if _mat == null:
		return
	var nt := float(env.call("night_amount"))
	if absf(nt - _night_last) > 0.01:
		_night_last = nt
		_mat.set_shader_parameter("night", nt)


## A unit box without a bottom; UV.x = 0..1 around the walls' faces, UV.y
## 0 at the foot, the top face marked by its normal.
func _box_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var h := 0.5
	var c := [Vector3(-h, -h, -h), Vector3(h, -h, -h), Vector3(h, -h, h), Vector3(-h, -h, h)]
	for k in 4:
		var a: Vector3 = c[k]
		var b: Vector3 = c[(k + 1) % 4]
		var a2 := a + Vector3(0, 1, 0)
		var b2 := b + Vector3(0, 1, 0)
		var n := (b - a).cross(Vector3.UP).normalized()
		if n.dot((a + b) * 0.5) < 0.0:
			n = -n
		# Outward and clockwise seen from outside.
		var tri := [a, a2, b, b, a2, b2]
		if (tri[1] - tri[0]).cross(tri[2] - tri[0]).dot(n) > 0.0:
			tri = [a, b, a2, b, b2, a2]
		for p: Vector3 in tri:
			st.set_normal(n)
			st.add_vertex(p)
	var top := [Vector3(-h, h, -h), Vector3(h, h, -h), Vector3(h, h, h), Vector3(-h, h, h)]
	var tt := [top[0], top[1], top[2], top[0], top[2], top[3]]
	if (tt[1] - tt[0]).cross(tt[2] - tt[0]).y > 0.0:
		tt = [top[0], top[2], top[1], top[0], top[3], top[2]]
	for p: Vector3 in tt:
		st.set_normal(Vector3.UP)
		st.add_vertex(p)
	return st.commit()


func _material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


const SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

uniform float night = 0.0;

varying vec3 v_m;        // metres from the box's centre, in its own axes
varying vec3 v_n;
varying vec3 v_size;
varying vec4 v_c;
varying vec3 v_w;

float h1(float n) { return fract(sin(n * 91.345) * 43758.5453123); }
float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void vertex() {
    v_size = vec3(length(MODEL_MATRIX[0].xyz), length(MODEL_MATRIX[1].xyz), length(MODEL_MATRIX[2].xyz));
    v_m = VERTEX * v_size;
    v_n = NORMAL;
    v_c = INSTANCE_CUSTOM;
    v_w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
    float seed = v_c.x;
    float kind = v_c.y * 255.0;
    bool tower = v_size.y >= 60.0;
    bool roof = v_n.y > 0.5;
    vec3 c;
    float rough = 0.85;
    float metal = 0.0;
    if (roof) {
        if (v_c.z > 0.5) {
            // Terracotta tiles, hipped: four planes shaded by which edge is
            // nearest, so the ridge reads from the air.
            vec2 q = v_m.xz / max(v_size.xz * 0.5, vec2(0.5));
            vec3 tile = mix(vec3(0.42, 0.17, 0.09), vec3(0.50, 0.25, 0.14), h1(seed * 13.7));
            float side = abs(q.x) > abs(q.y) ? sign(q.x) * 0.10 : sign(q.y) * -0.06;
            c = tile * (0.95 + side) * (0.9 + 0.2 * h2(floor(v_w.xz * 2.5)));
        } else {
            c = mix(vec3(0.20, 0.20, 0.19), vec3(0.34, 0.33, 0.31), h1(seed * 3.1));
            rough = 0.95;
        }
    } else {
        float across = abs(v_n.x) > 0.5 ? v_m.z : v_m.x;
        float up = v_m.y + v_size.y * 0.5;
        if (tower) {
            c = mix(vec3(0.16, 0.20, 0.24), vec3(0.32, 0.36, 0.38), h1(seed * 5.3));
            float band = step(0.82, fract(up / 3.6));
            c = mix(c, c * 1.5, band);
            rough = 0.25;
            metal = 0.5;
        } else {
            // Plaster in the city's palette, by seed.
            vec3 p0 = vec3(0.70, 0.64, 0.52);   // cream
            vec3 p1 = vec3(0.74, 0.73, 0.70);   // white
            vec3 p2 = vec3(0.66, 0.50, 0.32);   // ochre
            vec3 p3 = vec3(0.68, 0.52, 0.47);   // rose
            vec3 p4 = vec3(0.56, 0.56, 0.54);   // pale grey
            float s = h1(seed * 9.1);
            c = s < 0.28 ? p0 : (s < 0.52 ? p1 : (s < 0.66 ? p2 : (s < 0.78 ? p3 : p4)));
            c *= 0.88 + 0.2 * h1(seed * 4.7);
            // A window grid by floor, fading to its average when it would alias.
            float px = max(fwidth(across), fwidth(up));
            vec2 f = fract(vec2(across / 3.0, up / 3.1));
            float win = step(0.3, f.x) * step(f.x, 0.72) * step(0.32, f.y) * step(f.y, 0.82) * step(2.6, up);
            float detail = 1.0 - smoothstep(0.4, 1.2, px);
            c = mix(c, mix(c * 0.78, vec3(0.10, 0.11, 0.12), win), detail);
            c = mix(c, c * 0.82, 1.0 - detail);
        }
        if (night > 0.02) {
            float px = max(fwidth(across), fwidth(up));
            float lod = exp2(max(0.0, ceil(log2(max(px, 1e-4) / 0.6))));
            vec2 q = vec2(across / 3.0, up / 3.1) / lod;
            vec2 cell = floor(q);
            vec2 f = fract(q);
            float edge = 0.2 / lod;
            float win = step(0.5 - (0.22 + edge), f.x) * step(f.x, 0.5 + 0.22 + edge)
                      * step(0.3, f.y) * step(f.y, 0.82) * step(2.6, up);
            float share = mix(0.12, 0.45, h1(seed * 17.9));
            float lit = win * step(h2(cell + seed * 91.0 + lod * 7.0), share);
            vec3 warm = mix(vec3(1.0, 0.72, 0.42), vec3(0.85, 0.9, 1.0), step(0.8, h2(cell * 1.7 + 3.0)));
            EMISSION = warm * lit * night * mix(1.6, 1.1, clamp(lod / 8.0, 0.0, 1.0));
            c *= 1.0 - 0.6 * night;
        }
    }
    ALBEDO = c;
    ROUGHNESS = rough;
    METALLIC = metal;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = roof ? th_surface(0.75, 26.0, mix(0.25, 0.55, seed), 0.4)
                       : th_surface(0.55, 18.0, 0.7, 0.8 + 1.5 * h1(seed * 11.3));
        th = th_air(th, length(CAMERA_POSITION_WORLD - v_w));
        TH_OUT(th);
    }
}
"""
