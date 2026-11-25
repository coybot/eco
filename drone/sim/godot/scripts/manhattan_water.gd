## The rivers and the harbour round Manhattan: one plane at SEA_Y with a real
## water surface, under the ground, which cuts itself away over the water
## (manhattan_ground.gd), so no photographed water is ever drawn.
##
## The surface (WATER_FUNCS, after a harbour scene's sea): three normal maps
## drifting different ways at 41 m, 13 m and 3.7 m (the smallest faded with
## distance, where it would only alias), on a low roughness, so the sky's
## reflection (Godot's fresnel against the scene's own sky, whatever the sky
## script makes of it) and the sun's glitter break up the way open water
## does. Colour is the Hudson's and the East River's: silty green-grey, not
## ocean blue, lighter in the few metres along the walls. Along the bulkheads
## a thin wash surges and drains (the shoreline field's 0..4 m).
##
## Night (env.night_amount()): the body goes darker than the sky would make
## it on its own (no skylight under the bulkheads), and a sparse warm
## shimmer stands in for the city's lights broken up on the chop, strongest
## at grazing angles where real reflections are.
extends MeshInstance3D

const Shore = preload("res://scripts/manhattan_shore.gd")

var env: Node3D
var _mat: ShaderMaterial
var _t := 0.0

const WATER_FUNCS := """
uniform sampler2D sea_n1 : hint_normal, repeat_enable, filter_linear_mipmap_anisotropic;
uniform sampler2D sea_n2 : hint_normal, repeat_enable, filter_linear_mipmap_anisotropic;
uniform sampler2D sea_foam : repeat_enable, filter_linear_mipmap;
uniform vec3 sea_deep : source_color = vec3(0.040, 0.047, 0.036);
uniform vec3 sea_shallow : source_color = vec3(0.058, 0.066, 0.048);

vec3 sea_nmap(sampler2D t, vec2 uv) {
    vec3 n = texture(t, uv).xyz * 2.0 - 1.0;
    return vec3(n.x, n.z, n.y);     // tangent (x, y, up z) -> world (x, up y, z)
}

// p: world xz (m); shore: 1 at the wall .. 0 mid-river; edge: the wash band
// (0..1); dist: from the camera (m). World normal out.
void sea_surface(vec2 p, float shore, float edge, float dist, float time,
         out vec3 albedo, out vec3 nrm, out float rough, out float spec, out float ripple) {
    vec2 w1 = time * vec2(0.011, 0.007);
    vec2 w2 = time * vec2(-0.017, 0.012);
    vec2 w3 = time * vec2(0.045, -0.031);
    float near = 1.0 - smoothstep(120.0, 600.0, dist);
    vec3 n = sea_nmap(sea_n1, p / 41.0 + w1) + 0.7 * sea_nmap(sea_n2, p / 13.0 + w2)
           + 0.4 * near * sea_nmap(sea_n1, p / 3.7 + w3);
    ripple = n.x;
    // Calmer far off, where the chop is sub-pixel and would only alias.
    float chop = mix(0.6, 0.9, shore) * mix(1.0, 0.45, smoothstep(400.0, 4000.0, dist));
    nrm = normalize(vec3(n.x * chop, 1.0, n.z * chop));
    albedo = mix(sea_deep, sea_shallow, smoothstep(0.0, 1.0, shore));
    // The wash along the wall surges in and drains in ~7 s pulses.
    float pulse = 0.5 + 0.5 * sin(time * 0.9 + dot(p, vec2(0.031, 0.047)));
    float fn = texture(sea_foam, p / 6.0 + w2 * 0.6).r;
    // A wash, not surf: a sheltered river slaps its walls, it does not break.
    float foam = edge * smoothstep(0.72 - 0.2 * pulse, 0.95, fn + 0.15 * edge) * 0.45;
    albedo = mix(albedo, vec3(0.5, 0.52, 0.48), foam);
    // Rougher far off: there the chop is below a pixel but still blurs the
    // sky it reflects; a mirror-smooth far river came out sky-blue.
    rough = mix(mix(0.05, 0.16, smoothstep(300.0, 3000.0, dist)), 0.7, foam);
    // 0.25 is F0 = 0.02, water's own; 0.5 (Godot's default, F0 = 0.04) made
    // the rivers a mirror of a bright blue sky from every angle.
    spec = mix(0.25, 0.15, foam);
}
"""

const SHADER_HEAD := """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"
uniform float night = 0.0;
varying vec3 w;
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
"""

const SHADER_BODY := """
void fragment() {
    vec2 en = vec2(w.x, -w.z);
    float sd = shore_sd(en);
    float dist = length(CAMERA_POSITION_WORLD - w);
    float shore = 1.0 - smoothstep(0.0, 30.0, sd);
    float edge = 1.0 - smoothstep(0.2, 2.5, sd);
    vec3 alb; vec3 wn; float wr; float ws; float rip;
    sea_surface(w.xz, shore, edge, dist, TIME, alb, wn, wr, ws, rip);
    ALBEDO = alb * mix(1.0, 0.45, night);
    ROUGHNESS = wr;
    SPECULAR = ws;
    NORMAL = normalize((VIEW_MATRIX * vec4(wn, 0.0)).xyz);
    if (night > 0.01) {
        float ndv = clamp(dot(NORMAL, VIEW), 0.0, 1.0);
        float graze = pow(1.0 - ndv, 3.0);
        float g = texture(sea_foam, w.xz / 23.0 + vec2(TIME * 0.01, 0.0)).r;
        float lit = smoothstep(0.7, 0.95, g + 0.25 * wn.x * 4.0);
        EMISSION = vec3(1.0, 0.72, 0.42) * night * graze * lit * 0.35;
    }
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_water(rip, dot(NORMAL, VIEW));
        th = th_air(th, dist);
        TH_OUT(th);
    }
}
"""


func _ready() -> void:
	name = "water"
	env = get_parent().get_parent() as Node3D
	var shore: RefCounted = env.call("shore")
	var pm := PlaneMesh.new()
	# Past the 40 km far plane in every direction, as the parent's was: its
	# own edge must never show as a line on the horizon.
	pm.size = Vector2(100000.0, 100000.0)
	mesh = pm
	position = Vector3(0.0, float(env.get("SEA_Y")), 0.0)
	cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var sh := Shader.new()
	sh.code = SHADER_HEAD + Shore.FUNCS + WATER_FUNCS + SHADER_BODY
	_mat = ShaderMaterial.new()
	_mat.shader = sh
	shore.call("bind", _mat)
	_mat.set_shader_parameter("sea_n1", _normals(1, 0.012))
	_mat.set_shader_parameter("sea_n2", _normals(2, 0.022))
	var noise := FastNoiseLite.new()
	noise.noise_type = FastNoiseLite.TYPE_CELLULAR
	noise.frequency = 0.03
	noise.fractal_type = FastNoiseLite.FRACTAL_FBM
	noise.fractal_octaves = 3
	noise.seed = 7
	var nt := NoiseTexture2D.new()
	nt.width = 512
	nt.height = 512
	nt.seamless = true
	nt.generate_mipmaps = true
	nt.noise = noise
	_mat.set_shader_parameter("sea_foam", nt)
	material_override = _mat


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


func _process(delta: float) -> void:
	_t -= delta
	if _t > 0.0:
		return
	_t = 0.5
	_mat.set_shader_parameter("night", clampf(float(env.call("night_amount")), 0.0, 1.0))
