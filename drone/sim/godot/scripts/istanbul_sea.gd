## The water round historic Istanbul: the Bosphorus, the Golden Horn and the
## Sea of Marmara. Replaces env_istanbul's flat sea plane (the build() hook):
## one plane at SEA_Y, past the far plane every way, under the ground, which
## covers it wherever the land stands above the sea.
##
## Data (tools/bake_istanbul_sea.py, from the scene's OSM water mask):
##   sea_shore.png  signed distance to the shoreline, 4 m: the shallows'
##                  colour and the wash along the quays.
##   sea_flow.png   the surface current, 32 m: strength, direction and the
##                  broad distance to shore (open water is darker and bluer).
##
## The surface: three normal maps at 41 m, 13 m and 3.7 m drifting with the
## wind, and on top of that carried by the current (a two-phase flow map, so
## the texture never smears): the Bosphorus' stream runs south-south-west at
## ~1.5 m/s mid-channel, swings west along the peninsula into the Marmara,
## and the Golden Horn barely moves. Long slick lanes ride the stream, the
## streaks a fast current draws on open water. Low roughness, so the sky
## (whatever the scene's sky makes it) and the sun's glitter break up on the
## chop; water's own F0 (0.02), so it is not a mirror of a bright sky.
##
## Colour: deep blue-green in the Bosphorus and the Marmara, darker and
## bluer far from the shore, greener and murkier up the Golden Horn, lighter
## in the few tens of metres off the quays. A thin wash surges and drains
## along every shore.
##
## Night (env.night_amount()): the body darkens, the surface goes smoother
## so scene_look's night screen-space reflections draw the lit skyline on
## it as broken vertical streaks, and a sparse warm shimmer stands in for the
## city's lights on the chop at grazing angles (Manhattan's rivers' recipe).
extends MeshInstance3D

const DIR := "res://assets/istanbul/"

var env: Node3D
var _mat: ShaderMaterial
var _t := 0.0


## env_istanbul's hook: true = the sea is drawn here.
static func build(env_node: Node3D) -> bool:
	# The bridges' claim on OSM footprints they draw themselves: here, the
	# last hook before the buildings are drawn.
	if ResourceLoader.exists("res://scripts/istanbul_bridges.gd"):
		load("res://scripts/istanbul_bridges.gd").call("claim", env_node)
	var sea = load("res://scripts/istanbul_sea.gd").new()
	sea.env = env_node
	env_node.add_child(sea)
	return true


const SHADER := """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"

uniform float night = 0.0;
uniform sampler2D shore_sd : filter_linear;
uniform vec4 shore_rect;          // e0, n0, width, height (ENU m)
uniform sampler2D flow : filter_linear;
uniform vec4 flow_rect;
uniform sampler2D sea_n1 : hint_normal, repeat_enable, filter_linear_mipmap_anisotropic;
uniform sampler2D sea_n2 : hint_normal, repeat_enable, filter_linear_mipmap_anisotropic;
uniform sampler2D sea_foam : repeat_enable, filter_linear_mipmap;
uniform sampler2D sea_slick : repeat_enable, filter_linear_mipmap;
uniform vec3 col_deep : source_color = vec3(0.06, 0.20, 0.24);
uniform vec3 col_open : source_color = vec3(0.05, 0.16, 0.24);
uniform vec3 col_shallow : source_color = vec3(0.12, 0.29, 0.28);
uniform vec3 col_horn : source_color = vec3(0.10, 0.21, 0.17);
uniform float current_speed = 1.6;    // m/s at strength 1

varying vec3 w;
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }

vec3 sea_nmap(sampler2D t, vec2 uv) {
    vec3 n = texture(t, uv).xyz * 2.0 - 1.0;
    return vec3(n.x, n.z, n.y);     // tangent (x, y, up z) -> world (x, up y, z)
}

vec2 rect_uv(vec2 en, vec4 r) {
    return vec2((en.x - r.x) / r.z, 1.0 - (en.y - r.y) / r.w);
}

bool in01(vec2 uv) { return uv.x > 0.0 && uv.y > 0.0 && uv.x < 1.0 && uv.y < 1.0; }

// > 0 left of a->b.
float side(vec2 p, vec2 a, vec2 b) { return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x); }

// The three wind-drifted normal layers, sampled at world xz p (m).
vec3 chop(vec2 p, float near) {
    vec2 w1 = TIME * vec2(0.011, 0.007);
    vec2 w2 = TIME * vec2(-0.017, 0.012);
    vec2 w3 = TIME * vec2(0.045, -0.031);
    return sea_nmap(sea_n1, p / 41.0 + w1) + 0.7 * sea_nmap(sea_n2, p / 13.0 + w2)
         + 0.4 * near * sea_nmap(sea_n1, p / 3.7 + w3);
}

void fragment() {
    vec2 en = vec2(w.x, -w.z);
    float dist = length(CAMERA_POSITION_WORLD - w);

    // Shore distance (m, + over water); past the field, open water.
    float sd = 200.0;
    vec2 suv = rect_uv(en, shore_rect);
    if (in01(suv)) {
        float s = (texture(shore_sd, suv).r * 255.0 - 128.0) / 12.0;
        sd = sign(s) * s * s;
    }
    // The current: strength, direction (ENU), broad shore distance.
    float cur = 0.08;
    vec2 cdir = normalize(vec2(-0.95, -0.3));
    float far = 1.0;
    vec2 fuv = rect_uv(en, flow_rect);
    if (in01(fuv)) {
        vec4 f = texture(flow, fuv);
        cur = f.r;
        vec2 d = f.gb * 2.0 - 1.0;
        cdir = length(d) > 0.05 ? normalize(d) : cdir;
        far = f.a;
    }
    // Up the Golden Horn (Sarayburnu-Tophane, the creeks north of it).
    float horn = smoothstep(-60000.0, 120000.0, side(en, vec2(400.0, 880.0), vec2(-60.0, 1650.0)))
               * smoothstep(700.0, 1000.0, en.y);
    horn = max(horn, (1.0 - smoothstep(800.0, 1000.0, en.x)) * smoothstep(1600.0, 1800.0, en.y));

    float near = 1.0 - smoothstep(120.0, 600.0, dist);
    // World xz velocity of the surface (Godot z = -north).
    vec2 vel = vec2(cdir.x, -cdir.y) * cur * current_speed;
    // Two phases of the flow map, each reset while the other carries it.
    float period = 9.0;
    float ph0 = fract(TIME / period);
    float ph1 = fract(TIME / period + 0.5);
    float blend = abs(2.0 * ph0 - 1.0);
    vec3 n0 = chop(w.xz - vel * ph0 * period, near);
    vec3 n1 = chop(w.xz - vel * ph1 * period + vec2(17.3, 9.1), near);
    vec3 n = mix(n0, n1, blend);
    float ripple = n.x;

    // Slick lanes along the stream: noise stretched along the current
    // (coordinates about the strait's middle, so the slow turn of the
    // direction bends the lanes rather than tearing them).
    vec2 rel = en - vec2(2200.0, 1800.0);
    vec2 perp = vec2(-cdir.y, cdir.x);
    float t_al = TIME * cur * current_speed;
    float lane = texture(sea_slick, vec2((dot(rel, cdir) - t_al) / 520.0, dot(rel, perp) / 34.0)).r;
    float lane2 = texture(sea_slick, vec2((dot(rel, cdir) - t_al * 0.8) / 260.0, dot(rel, perp) / 15.0 + 0.37)).r;
    float slick = smoothstep(0.55, 0.75, lane * 0.65 + lane2 * 0.35) * smoothstep(0.15, 0.6, cur);

    float shore = 1.0 - smoothstep(0.0, 45.0, sd);
    float edge = 1.0 - smoothstep(0.3, 5.0, sd);
    // Calmer far off, where the chop is sub-pixel and would only alias;
    // flatter in a slick, where a surface film damps the ripples.
    float chop_k = mix(0.6, 0.85, shore) * mix(1.0, 0.45, smoothstep(400.0, 4000.0, dist))
                 * mix(1.0, 0.3, slick);
    vec3 wn = normalize(vec3(n.x * chop_k, 1.0, n.z * chop_k));

    vec3 body = mix(col_deep, col_open, smoothstep(0.15, 0.7, far));
    body = mix(body, col_horn, horn);
    body = mix(body, col_shallow, shore * 0.8);
    // The wash along the quays surges in and drains in ~7 s pulses.
    float pulse = 0.5 + 0.5 * sin(TIME * 0.9 + dot(en, vec2(0.031, 0.047)));
    float fn = texture(sea_foam, w.xz / 6.0 + TIME * vec2(-0.017, 0.012) * 0.6).r;
    float foam = edge * smoothstep(0.66 - 0.2 * pulse, 0.92, fn + 0.22 * edge) * 0.7;
    vec3 alb = mix(body, vec3(0.62, 0.66, 0.64), foam);
    float rough = mix(mix(0.05, 0.16, smoothstep(300.0, 3000.0, dist)), 0.7, foam);
    rough = mix(rough, rough * 0.55, slick);
    float spec = mix(0.25, 0.15, foam);

    ALBEDO = alb * mix(1.0, 0.45, night);
    ROUGHNESS = mix(rough, min(rough, 0.035), night);
    SPECULAR = mix(spec, 0.5, night);
    vec3 wn_n = normalize(vec3(wn.x * 1.6, 1.0, wn.z * 0.6));   // vertical streaks of lit windows
    NORMAL = normalize((VIEW_MATRIX * vec4(mix(wn, wn_n, night), 0.0)).xyz);
    if (night > 0.01) {
        float ndv = clamp(dot(NORMAL, VIEW), 0.0, 1.0);
        float graze = pow(1.0 - ndv, 3.0);
        float g = texture(sea_foam, w.xz / 23.0 + vec2(TIME * 0.01, 0.0)).r;
        float lit = smoothstep(0.7, 0.95, g + 0.25 * wn.x * 4.0);
        // The city's lights broken up on the chop, nearer the lit shores.
        float city = mix(0.35, 1.0, 1.0 - smoothstep(0.1, 0.6, far));
        EMISSION = vec3(1.0, 0.72, 0.42) * night * graze * lit * 0.35 * city;
        EMISSION += vec3(0.050, 0.038, 0.034) * night * graze;
    }
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_water(ripple, dot(NORMAL, VIEW));
        th = th_air(th, dist);
        TH_OUT(th);
    }
}
"""


func _ready() -> void:
	name = "sea"
	var pm := PlaneMesh.new()
	# Past the far plane in every direction: its own edge must never show.
	pm.size = Vector2(100000.0, 100000.0)
	mesh = pm
	position = Vector3(0.0, float(env.get("SEA_Y")), 0.0)
	cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var sh := Shader.new()
	sh.code = SHADER
	_mat = ShaderMaterial.new()
	_mat.shader = sh
	var meta: Dictionary = {}
	if FileAccess.file_exists(DIR + "sea.json"):
		meta = JSON.parse_string(FileAccess.get_file_as_string(DIR + "sea.json"))
	if meta.has("shore"):
		var img := Image.load_from_file(DIR + "sea_shore.png")
		if img != null:
			img.convert(Image.FORMAT_R8)
			_mat.set_shader_parameter("shore_sd", ImageTexture.create_from_image(img))
			_mat.set_shader_parameter("shore_rect", _rect4(meta["shore"]["bounds_enu"]))
	else:
		push_warning("[istanbul_sea] no sea.json - run drone/sim/tools/bake_istanbul_sea.py")
		_mat.set_shader_parameter("shore_rect", Vector4(0, 0, 1, 1))
	if meta.has("flow"):
		var img := Image.load_from_file(DIR + "sea_flow.png")
		if img != null:
			img.convert(Image.FORMAT_RGBA8)
			_mat.set_shader_parameter("flow", ImageTexture.create_from_image(img))
			_mat.set_shader_parameter("flow_rect", _rect4(meta["flow"]["bounds_enu"]))
	else:
		_mat.set_shader_parameter("flow_rect", Vector4(0, 0, 1, 1))
	_mat.set_shader_parameter("sea_n1", _normals(1, 0.012))
	_mat.set_shader_parameter("sea_n2", _normals(2, 0.022))
	_mat.set_shader_parameter("sea_foam", _noise(FastNoiseLite.TYPE_CELLULAR, 0.03, 7, 3))
	_mat.set_shader_parameter("sea_slick", _noise(FastNoiseLite.TYPE_SIMPLEX_SMOOTH, 0.006, 11, 2))
	material_override = _mat


static func _rect4(b: Array) -> Vector4:
	return Vector4(float(b[0]), float(b[1]), float(b[2]) - float(b[0]), float(b[3]) - float(b[1]))


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


static func _noise(kind: int, freq: float, seed_: int, octaves: int) -> NoiseTexture2D:
	var noise := FastNoiseLite.new()
	noise.noise_type = kind
	noise.frequency = freq
	noise.fractal_type = FastNoiseLite.FRACTAL_FBM
	noise.fractal_octaves = octaves
	noise.seed = seed_
	var nt := NoiseTexture2D.new()
	nt.width = 512
	nt.height = 512
	nt.seamless = true
	nt.generate_mipmaps = true
	nt.noise = noise
	return nt


func _process(delta: float) -> void:
	_t -= delta
	if _t > 0.0:
		return
	_t = 0.5
	_mat.set_shader_parameter("night", clampf(float(env.call("night_amount")), 0.0, 1.0))
