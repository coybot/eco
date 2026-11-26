## The look of the photoreal Manhattan scene (env_manhattan_photo): sky, sun,
## post-processing, the air, and the sky at night. Godot-only.
##
## "beauty" (manhattan_photo's default):
##   - a sky shader with a sun disk and its glow, fair-weather cumulus, dusk
##     colours, and at night a few stars (it is New York), the moon in its
##     real phase and the city's glow low in the haze. It follows world_clock's
##     sun every frame, so fw_set_time moves it with no further call.
##   - ONE shadowed sun at a summer mid-morning azimuth (shadows fall north-
##     west, as they do in the orthophoto under the buildings). main.tscn's
##     second light gives up its energy and its shadow maps to it.
##   - AgX tonemapping, a gentle bloom, SSAO, sky-lit ambient with a little
##     blue of its own so night shade isn't black, and a grade toward summer
##     aerial photographs of the city (slightly warm, mid contrast).
##   - aerial perspective as a full-screen pass (AIR_SHADER), NOT Godot's fog.
##     See "WHY NOT FOG" below.
## "photo" (--look=photo) changes nothing: today's look, LightingKit's retune.
##
## Launch args: --look=photo|beauty, --look-sun-az=<true deg, default 138>,
## --look-sun-el=<deg, default 48>, --look-ssao=0|1 (1), --look-glow=0|1 (1),
## --look-ssil=0|1 (0: costly at 4K), --look-haze=0|1 (1, the air pass),
## --look-ssr=auto|on|off (auto: screen-space reflections at night only),
## --moon-age=<days since new moon> (default: from fw_set_time's date).
##
## WHY NOT FOG. env_manhattan.gd records that exponential fog was tried here
## and removed: at a density that read as haze near the ground (0.0016) a top-
## down of the island from 4.2 km was a blue rectangle, and even at 0.00012
## Central Park's green was gone from 4 km. That is not a tuning problem, it is
## the model: Godot's fog is a function of distance only, so looking straight
## down through 4 km of air costs exactly what looking 4 km along the street
## does. Real haze lives in the bottom kilometre or so of the atmosphere and
## thins with height. A nadir view from 4 km crosses about one scale height
## of it; a horizontal view across the island crosses twenty kilometres of
## its densest part. The air pass integrates an exponential atmosphere along
## each pixel's actual ray (closed form, from the depth buffer), so:
##     nadir from 4.2 km          ~11% haze  (the park stays green)
##     300 m along the street      ~3%       (near detail, what a VLM uses, untouched)
##     4 km across midtown        ~30%
##     the far end of the island  ~80%, fading into a sky horizon of the same colour
## Its haze colour is the sky's own horizon colour for that direction (same
## GLSL), so the skyline sinks into the sky rather than into a grey band.
##
## THE NIGHT HOOK, for night_lights.gd and anything else that follows the light:
##   var look = env.get_node_or_null("scene_look")
##   look.light_changed.connect(func(night, sun_dir, moon_dir): ...)
##   look.light_state() -> {"night": 0..1, "sun_dir": Vector3, "moon_dir": Vector3,
##                          "moon_lit": 0..1, "look": "beauty"|"photo"}
## night is world_clock's (0 by day, 1 once the sun is 4 deg down). Directions
## point TOWARD the sun and the moon, Godot world space (+Y up; sim east +X,
## sim north -Z; the sim frame is turned 29 deg onto the street grid).
extends Node

signal light_changed(night: float, sun_dir: Vector3, moon_dir: Vector3)

const LOOKS := {"manhattan": "beauty", "istanbul": "beauty"}
const INC := "res://shaders/thermal.gdshaderinc"

## The air (see WHY NOT FOG). Extinction per metre at street level, its scale
## height, and a thin uniform term for the air above the boundary layer.
## 0.00009/m is a clean summer day (~40 km visibility at the ground).
const BETA0 := 0.00009
const SCALE_H := 1100.0
const BETA_HI := 0.0000035

## Per-region tuning. A region with no entry (manhattan) gets exactly the
## values above and below: every lookup falls back to the Manhattan constant
## and its shaders are the shared source unchanged (REGION_GLSL is applied
## only to a region listed there).
##
## istanbul: a clear Mediterranean summer day. The base look (New York's wet
## harbour air) washed the city out to a milky pastel: a pale grey-blue sky
## whose horizon colour is also the haze colour, haze thick enough to veil a
## city seen across 2 km, and a full sky-ambient term lifting every shadow.
## Here: a deeper, drier blue overhead, a cleaner horizon, about half New
## York's land haze, a warmer and stronger sun against less sky fill (crisp
## shadows), exposure and contrast set for the limestone, lead domes and
## terracotta. The depth comes from a separate SEA layer instead (sea_beta):
## a low, sunlit marine haze that only builds over the Marmara, the
## Bosphorus and the Golden Horn (the air pass weights it by how much of each
## ray's path lies over water, from the env's water_mask()), so the far
## shores and the open sea soften while a city seen over land stays crisp.
const TUNE := {
	"istanbul": {
		"sun_az": 128.0, "sun_el": 56.0,           # ~11:15 local on 15 July
		"sun_energy": 2.35, "sun_color": Color(1.0, 0.92, 0.80),
		"exposure": 0.86, "agx_contrast": 1.7,
		# Shade is sky blue AND the warm bounce off sunlit stone and paving:
		# all sky made every shadow side a saturated blue.
		"ambient_color": Color(0.30, 0.27, 0.23), "ambient_sky": 0.55, "ambient_energy": 0.45,
		"contrast": 1.1, "saturation": 1.18, "brightness": 1.0,
		"beta0": 0.000045, "scale_h": 900.0, "beta_hi": 0.0000025,
		"sea_beta": 0.00012, "sea_h": 160.0,
		"cloud_base": 0.07,
	},
}

## GLSL lines swapped per region: [shared line, region line]. Each shared line
## must be found verbatim (a warning names any that is not).
const REGION_GLSL := {
	"istanbul": [
		# A drier, deeper blue overhead; a clean pale horizon that turns blue
		# faster with height (less of the sky is milk).
		["    vec3 zen = vec3(0.045, 0.165, 0.47);", "    vec3 zen = vec3(0.030, 0.135, 0.50);"],
		["    vec3 hor = vec3(0.55, 0.66, 0.80);", "    vec3 hor = vec3(0.56, 0.68, 0.84);"],
		["    vec3 c_day = mix(hor, zen, pow(h, 0.45));", "    vec3 c_day = mix(hor, zen, pow(h, 0.36));"],
		# Sunset over the Marmara: a broader, richer gold-orange on the sun's
		# side, rose then violet away from it.
		["    hor = mix(hor, mix(vec3(0.44, 0.34, 0.50), vec3(1.20, 0.56, 0.26), pow(toward, 3.0)), dusk);",
		 "    hor = mix(hor, mix(vec3(0.50, 0.36, 0.48), vec3(1.40, 0.62, 0.24), pow(max(toward, 0.0), 2.2)), dusk);"],
		["    zen = mix(zen, vec3(0.08, 0.10, 0.30), dusk);", "    zen = mix(zen, vec3(0.09, 0.10, 0.32), dusk);"],
		# The sea layer (TUNE sea_beta), in the air pass only.
		["uniform float fog_h = 140.0;", """uniform float fog_h = 140.0;
uniform sampler2D sea_mask : filter_linear, repeat_disable;   // 1 = water, row 0 north
uniform vec4 sea_rect = vec4(-1.0, -1.0, 2.0, 2.0);            // e0, n0, width, height (m)
uniform float sea_beta = 0.0;
uniform float sea_h = 160.0;
float sea_frac(vec3 p) {
    vec2 uv = vec2((p.x - sea_rect.x) / sea_rect.z, 1.0 - (-p.z - sea_rect.y) / sea_rect.w);
    if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) { return 1.0; }
    return texture(sea_mask, uv).r;
}"""],
		["        float tau = layer(beta0, scale_h, cam.y, wp.y, L) + layer(fog_beta, fog_h, cam.y, wp.y, L)",
		 """        float over_sea = 0.25 * (sea_frac(mix(cam, wp, 0.25)) + sea_frac(mix(cam, wp, 0.5))
                                + sea_frac(mix(cam, wp, 0.75)) + sea_frac(wp));
        float tau = layer(beta0, scale_h, cam.y, wp.y, L) + layer(fog_beta, fog_h, cam.y, wp.y, L)
                  + over_sea * layer(sea_beta, sea_h, cam.y, wp.y, L)"""],
	],
}

var look := "photo"
var region := ""
var night := 0.0
var sun_dir := Vector3.UP
var moon_dir := Vector3.DOWN
var moon_lit := 0.0

var _env_node: Node3D
var _we: Environment
var _sun: DirectionalLight3D
var _sky_mat: ShaderMaterial
var _air_mat: ShaderMaterial
var _air: MeshInstance3D
var _last := PackedFloat32Array()
var _ssr_mode := "auto"


## Build the look for `env` and add it as its child "scene_look". Call after
## SceneKit's sun exists and before world_clock.gd is added, so the clock's
## "photo" sun is this look's sun.
static func setup(env: Node3D, region: String) -> Node:
	var node: Node = load("res://scripts/scene_look.gd").new()
	node.name = "scene_look"
	node._env_node = env
	node.region = region
	node._parse_tune_args()
	node.look = IpcServer._get_launch_arg("--look", str(LOOKS.get(region, "photo")))
	env.add_child(node)
	if node.look == "beauty":
		node._apply_beauty()
	print("[scene_look] %s" % node.look)
	return node


## A tuning value for this region, or the shared (Manhattan) default.
## --look-tune=key=value,... overrides a number for a tuning session.
func _tv(key: String, default_value: Variant) -> Variant:
	if _tune_args.has(key):
		return _tune_args[key]
	return (TUNE.get(region, {}) as Dictionary).get(key, default_value)


var _tune_args := {}

func _parse_tune_args() -> void:
	for kv in IpcServer._get_launch_arg("--look-tune", "").split(",", false):
		var p := kv.split("=")
		if p.size() == 2:
			_tune_args[p[0]] = float(p[1])


## Shader source with this region's REGION_GLSL swaps; unchanged otherwise.
func _region_code(code: String) -> String:
	for pair: Array in REGION_GLSL.get(region, []):
		if code.contains(str(pair[0])):
			code = code.replace(str(pair[0]), str(pair[1]))
		elif not str(pair[0]).begins_with("uniform float fog_h") and not str(pair[0]).contains("float tau"):
			push_warning("[scene_look] %s: GLSL line not found: %s" % [region, pair[0]])
	return code


func light_state() -> Dictionary:
	return {"night": night, "sun_dir": sun_dir, "moon_dir": moon_dir, "moon_lit": moon_lit, "look": look}


# ------------------------------------------------------------------ beauty
func _apply_beauty() -> void:
	_we = _env_node.get_world_3d().environment if _env_node.get_world_3d() else null
	for c in _env_node.get_children():
		if c is DirectionalLight3D:
			_sun = c
			break
	if _we == null or _sun == null:
		push_warning("[scene_look] no environment or sun; beauty not applied")
		look = "photo"
		return
	_place_sun()
	_build_sky()
	var e := _we
	e.tonemap_mode = Environment.TONE_MAPPER_AGX
	e.tonemap_exposure = _tv("exposure", 1.0)
	e.tonemap_agx_contrast = _tv("agx_contrast", 1.35)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	e.ambient_light_color = _tv("ambient_color", Color(0.10, 0.13, 0.22))
	e.ambient_light_sky_contribution = _tv("ambient_sky", 0.85)
	# Lower than SceneKit's 1.0 for the reason LightingKit gives: a full sky
	# term fills the shadows in, and the shadows are what make it read aerial.
	e.ambient_light_energy = _tv("ambient_energy", 0.6)
	e.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	e.glow_enabled = IpcServer._get_launch_arg("--look-glow", "1") == "1"
	e.glow_intensity = 0.4
	e.glow_strength = 0.9
	e.glow_bloom = 0.0
	e.glow_hdr_threshold = 1.1
	e.glow_blend_mode = Environment.GLOW_BLEND_MODE_SOFTLIGHT
	# Contact shadow where walls meet the street and each other (half res).
	e.ssao_enabled = IpcServer._get_launch_arg("--look-ssao", "1") == "1"
	e.ssao_radius = 4.0
	e.ssao_intensity = 1.6
	e.ssao_power = 1.3
	e.ssao_detail = 0.4
	e.ssil_enabled = IpcServer._get_launch_arg("--look-ssil", "0") == "1"
	e.ssil_radius = 6.0
	e.ssil_intensity = 0.8
	# No Godot fog: the air pass replaces it (header). --look-haze=0 leaves the
	# scene with no aerial perspective at all, which is what --look=photo has.
	e.fog_enabled = false
	e.volumetric_fog_enabled = false
	# Summer aerial photographs of the city: mid contrast, colour a touch up
	# from AgX's neutral (it desaturates highlights by design).
	e.adjustment_enabled = true
	e.adjustment_brightness = _tv("brightness", 1.0)
	e.adjustment_contrast = _tv("contrast", 1.05)
	e.adjustment_saturation = _tv("saturation", 1.1)
	# Screen-space reflections at night only (--look-ssr=auto|on|off): what
	# puts the skyline's lit windows and lamps on the rivers. By day the
	# water's sky reflection is most of what the river shows and SSR's cost
	# buys little; at night without it the Hudson is a black slab.
	_ssr_mode = IpcServer._get_launch_arg("--look-ssr", "auto")
	e.ssr_max_steps = 96
	e.ssr_fade_in = 0.08
	e.ssr_fade_out = 1.2
	e.ssr_depth_tolerance = 2.0
	e.ssr_enabled = _ssr_mode == "on"
	if IpcServer._get_launch_arg("--look-haze", "1") == "1":
		_build_air()
	_update(true)


## One shadowed sun. The others (main.tscn has one) give their light up to it
## and stop drawing shadow maps, which a dark light still does every frame.
func _place_sun() -> void:
	var az := float(IpcServer._get_launch_arg("--look-sun-az", str(_tv("sun_az", 138))))
	var el := float(IpcServer._get_launch_arg("--look-sun-el", str(_tv("sun_el", 48))))
	var rot := float(_env_node.get("GRID_ROTATION_DEG")) if _env_node.get("GRID_ROTATION_DEG") != null else 0.0
	var b := deg_to_rad(az - rot)
	var er := deg_to_rad(el)
	var to_sun := Vector3(sin(b) * cos(er), sin(er), -cos(b) * cos(er))
	for n in _env_node.get_tree().root.find_children("*", "DirectionalLight3D", true, false):
		var l := n as DirectionalLight3D
		l.global_transform = Transform3D(Basis.looking_at(-to_sun, Vector3.UP), l.global_transform.origin)
		if l != _sun:
			l.light_energy = 0.0
			l.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY
			l.shadow_enabled = false
			l.visible = false
	_sun.light_energy = _tv("sun_energy", 2.1)
	_sun.light_color = _tv("sun_color", Color(1.0, 0.93, 0.83))
	_sun.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_AND_SKY
	_sun.light_angular_distance = 0.5
	_sun.shadow_enabled = true
	_sun.shadow_blur = 1.2


func _build_sky() -> void:
	var noise := FastNoiseLite.new()
	noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	noise.frequency = 0.008
	noise.fractal_octaves = 5
	noise.fractal_gain = 0.55
	noise.seed = 1124
	var nt := NoiseTexture2D.new()
	nt.width = 1024
	nt.height = 1024
	nt.seamless = true
	nt.generate_mipmaps = true
	nt.noise = noise
	var sh := Shader.new()
	sh.code = _region_code(SKY_SHADER.replace("//AIR", AIR_GLSL))
	_sky_mat = ShaderMaterial.new()
	_sky_mat.shader = sh
	_sky_mat.set_shader_parameter("clouds", nt)
	var sky := Sky.new()
	sky.sky_material = _sky_mat
	sky.radiance_size = Sky.RADIANCE_SIZE_256
	sky.process_mode = Sky.PROCESS_MODE_AUTOMATIC
	_we.background_mode = Environment.BG_SKY
	_we.sky = sky


## The air pass: one full-screen triangle pair drawn last in the transparent
## pass for every camera (main window, vantages, aircraft), alpha-blending the
## sky's horizon colour over the frame by each pixel's transmittance. No
## screen-texture copy: blending does the mix, so the cost is one depth read
## per pixel. Thermal cameras discard it (they have their own air model).
func _build_air() -> void:
	var sh := Shader.new()
	sh.code = _region_code(AIR_SHADER.replace("//AIR", AIR_GLSL).replace("//INC", "#include \"%s\"" % INC))
	_air_mat = ShaderMaterial.new()
	_air_mat.shader = sh
	if float(_tv("sea_beta", 0.0)) > 0.0:
		_bind_sea_mask()
	_air_mat.render_priority = Material.RENDER_PRIORITY_MAX
	var q := QuadMesh.new()
	q.size = Vector2(2.0, 2.0)
	# The quad's real position is set in clip space; its AABB must just never
	# be culled, wherever the camera is in a 21 km world.
	q.custom_aabb = AABB(Vector3(-1e6, -1e6, -1e6), Vector3(2e6, 2e6, 2e6))
	_air = MeshInstance3D.new()
	_air.name = "aerial_haze"
	_air.mesh = q
	_air.material_override = _air_mat
	_air.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_air.extra_cull_margin = 16384.0
	_air.ignore_occlusion_culling = true
	_env_node.add_child.call_deferred(_air)


## The sea layer's mask: the env's water_mask() boxed down to ~64 m, so each
## ray's over-water fraction is smooth (no shimmer along the coasts).
func _bind_sea_mask() -> void:
	if not _env_node.has_method("water_mask"):
		return
	var wm: Dictionary = _env_node.call("water_mask")
	var tex: Texture2D = wm.get("tex")
	if tex == null:
		return
	var img := tex.get_image()
	img.resize(maxi(8, img.get_width() / 16), maxi(8, img.get_height() / 16), Image.INTERPOLATE_BILINEAR)
	var b: Rect2 = wm["bounds"]
	_air_mat.set_shader_parameter("sea_mask", ImageTexture.create_from_image(img))
	_air_mat.set_shader_parameter("sea_rect", Vector4(b.position.x, b.position.y, b.size.x, b.size.y))


# ------------------------------------------------------------------ per frame
func _process(_delta: float) -> void:
	_update(false)


func _clock() -> Node:
	return _env_node.get_node_or_null("world_clock")


func _update(force: bool) -> void:
	if _sun == null:
		for c in _env_node.get_children():
			if c is DirectionalLight3D:
				_sun = c
				break
		if _sun == null:
			return
	var wc := _clock()
	var w := {"haze": 0.0, "fog": 0.0, "cloud": 0.0, "rain": 0.0}
	if wc != null:
		night = float(wc.call("night_amount"))
		sun_dir = wc.call("sun_dir")
		moon_dir = wc.call("moon_dir")
		moon_lit = float(wc.call("moon_lit"))
		w = wc.call("weather")
	else:
		night = 0.0
		sun_dir = _sun.global_transform.basis.z.normalized()
		moon_dir = Vector3.DOWN
	var key := PackedFloat32Array([night, sun_dir.x, sun_dir.y, sun_dir.z, moon_dir.x, moon_dir.y,
		moon_dir.z, moon_lit, w["haze"], w["fog"], w["cloud"], w["rain"]])
	if not force and key == _last:
		return
	_last = key
	if look == "beauty":
		if _we != null:
			_we.ssr_enabled = _ssr_mode == "on" or (_ssr_mode == "auto" and night > 0.2)
		var moon_age: float = float(wc.call("moon_age")) / 29.530589 if wc != null else 0.34
		# Light pollution: more of the city's light comes back off a low,
		# thick sky. Overcast nights over New York are orange-grey, not black.
		var glow: float = 1.0 + 1.6 * w["cloud"] + 0.8 * w["haze"] + 1.2 * w["fog"] + 0.6 * w["rain"]
		for m: ShaderMaterial in [_sky_mat, _air_mat]:
			if m == null:
				continue
			m.set_shader_parameter("sun_dir", sun_dir)
			m.set_shader_parameter("w_haze", w["haze"])
			m.set_shader_parameter("w_fog", w["fog"])
			m.set_shader_parameter("w_cloud", w["cloud"])
			m.set_shader_parameter("w_rain", w["rain"])
			m.set_shader_parameter("city_glow", glow)
		if _sky_mat:
			_sky_mat.set_shader_parameter("moon_dir", moon_dir)
			_sky_mat.set_shader_parameter("moon_lit", moon_lit)
			_sky_mat.set_shader_parameter("moon_age", moon_age)
			_sky_mat.set_shader_parameter("cloud_cover", _tv("cloud_base", 0.16) + 0.8 * w["cloud"])
		if _air_mat:
			# Haze: more and lower aerosol; rain: washes contrast out, greys
			# it; fog: a dense layer a tower's height deep, so the tops of
			# the tall ones stand out of it as they do on real fog mornings.
			_air_mat.set_shader_parameter("beta0", _tv("beta0", BETA0) * (1.0 + 3.5 * w["haze"] + 3.0 * w["rain"]))
			_air_mat.set_shader_parameter("scale_h", _tv("scale_h", SCALE_H) * (1.0 - 0.3 * w["haze"]))
			_air_mat.set_shader_parameter("beta_hi", _tv("beta_hi", BETA_HI))
			if float(_tv("sea_beta", 0.0)) > 0.0:
				# The marine layer thickens with haze and rain like the rest.
				_air_mat.set_shader_parameter("sea_beta", float(_tv("sea_beta", 0.0)) * (1.0 + 2.0 * w["haze"] + 1.5 * w["rain"]))
				_air_mat.set_shader_parameter("sea_h", float(_tv("sea_h", 160.0)))
			_air_mat.set_shader_parameter("fog_beta", 0.008 * pow(w["fog"], 1.5))
			_air_mat.set_shader_parameter("fog_h", 90.0)
	light_changed.emit(night, sun_dir, moon_dir)


# ------------------------------------------------------------------ shaders
## The sky's colour for a direction, shared by the sky shader and the air pass
## so the far city fades into exactly the sky behind it.
const AIR_GLSL := """
uniform vec3 sun_dir = vec3(0.4, 0.6, 0.6);
uniform float w_haze = 0.0;
uniform float w_fog = 0.0;
uniform float w_cloud = 0.0;
uniform float w_rain = 0.0;
uniform float city_glow = 1.0;

// How bright the air itself is under this sun: the colour an overcast or a
// fog takes. At night it is the city's light coming back off it.
vec3 air_light(vec3 s) {
    float day = smoothstep(-0.06, 0.10, s.y);
    float twil = smoothstep(-0.21, -0.03, s.y);
    vec3 glow = vec3(0.042, 0.029, 0.019) * city_glow;
    vec3 c = mix(glow + vec3(0.004, 0.005, 0.008), vec3(0.16, 0.17, 0.24), twil * (1.0 - day));
    return mix(c, vec3(0.70, 0.73, 0.77) * (0.55 + 0.45 * smoothstep(0.0, 0.5, s.y)), day);
}

vec3 sky_colour(vec3 d, vec3 s) {
    float h = clamp(d.y, 0.0, 1.0);
    float day = smoothstep(-0.06, 0.08, s.y);
    float twil = smoothstep(-0.21, -0.03, s.y);
    float dusk = (1.0 - smoothstep(-0.02, 0.20, s.y)) * smoothstep(-0.16, 0.0, s.y);
    float toward = 0.5 + 0.5 * dot(normalize(d.xz + 1e-4), normalize(s.xz + 1e-4));
    // Day: a clear summer blue overhead to the pale, humid horizon over the
    // harbour (never as deep as a desert sky: New York's air is wet).
    vec3 zen = vec3(0.045, 0.165, 0.47);
    vec3 hor = vec3(0.55, 0.66, 0.80);
    // Dusk: the sun's side over New Jersey burns orange and rose, the other
    // side goes violet.
    zen = mix(zen, vec3(0.08, 0.10, 0.30), dusk);
    hor = mix(hor, mix(vec3(0.44, 0.34, 0.50), vec3(1.20, 0.56, 0.26), pow(toward, 3.0)), dusk);
    vec3 c_day = mix(hor, zen, pow(h, 0.45));
    // Blue hour: deep blue overhead, the last glow low on the sun's side.
    vec3 t_hor = mix(vec3(0.075, 0.085, 0.19), vec3(0.42, 0.25, 0.21), pow(toward, 4.0));
    vec3 c_twil = mix(t_hor, vec3(0.020, 0.045, 0.14), pow(h, 0.5));
    // Night: the city's glow low in the haze, sodium-orange fading to LED-grey,
    // a dark but never black zenith.
    vec3 glow = vec3(0.042, 0.029, 0.019) * city_glow;
    vec3 c_night = mix(vec3(0.010, 0.011, 0.016) + glow * pow(1.0 - h, 4.0),
                       vec3(0.004, 0.005, 0.011), pow(h, 0.4));
    vec3 col = mix(mix(c_night, c_twil, twil), c_day, day);
    col += glow * pow(1.0 - h, 6.0) * twil * (1.0 - day);
    // Weather: an overcast greys the whole sky; haze and fog milk the horizon.
    vec3 air = air_light(s);
    float oc = clamp(smoothstep(0.35, 1.0, w_cloud) * 0.95 + 0.6 * w_rain, 0.0, 1.0);
    col = mix(col, air * (0.82 - 0.3 * w_rain), oc);
    float milk = clamp(w_haze * 0.65 * pow(1.0 - h, 2.0) + w_fog * pow(1.0 - h, 1.2), 0.0, 1.0);
    col = mix(col, air, milk);
    return col;
}

// The sun's own light, white high up, gold then red near the horizon.
vec3 sun_colour(vec3 s) {
    return mix(vec3(1.25, 0.60, 0.30), vec3(1.0, 0.94, 0.86), smoothstep(0.0, 0.45, s.y)) * 1.6;
}

// What a long path of air looks like in direction d: the horizon's colour
// that way, brighter toward the sun (forward scattering).
vec3 haze_colour(vec3 d, vec3 s) {
    vec3 c = sky_colour(vec3(d.x, 0.015, d.z), s);
    float cs = max(dot(normalize(vec3(d.x, d.y * 0.5, d.z)), s), 0.0);
    float day = smoothstep(-0.04, 0.10, s.y);
    float clear = 1.0 - 0.8 * clamp(w_cloud + w_rain, 0.0, 1.0);
    return c + sun_colour(s) * (0.10 * pow(cs, 6.0) + 0.12 * pow(cs, 40.0)) * day * clear;
}
"""

const SKY_SHADER := """
shader_type sky;
render_mode use_debanding;
uniform sampler2D clouds : repeat_enable, filter_linear_mipmap;
uniform vec3 moon_dir = vec3(0.0, -1.0, 0.0);
uniform float moon_lit = 0.6;
uniform float moon_age = 0.34;          // 0..1 of the synodic month
uniform float cloud_cover = 0.16;
//AIR

float hash13(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.zyx + 31.32);
    return fract((p.x + p.y) * p.z);
}

void sky() {
    vec3 d = normalize(EYEDIR);
    vec3 s = normalize(sun_dir);
    float day = smoothstep(-0.06, 0.08, s.y);
    float twil = smoothstep(-0.21, -0.03, s.y);
    float dusk = (1.0 - smoothstep(-0.02, 0.20, s.y)) * smoothstep(-0.16, 0.0, s.y);
    float night = 1.0 - smoothstep(-0.10, 0.03, s.y);
    float clear = 1.0 - clamp(smoothstep(0.4, 1.0, w_cloud) + w_rain + 0.8 * w_fog, 0.0, 1.0);
    vec3 sc = sun_colour(s);
    vec3 col = sky_colour(d, s);

    // Sun: its glow (Mie) and the disk itself. The disk is not drawn into the
    // radiance cubemap, where a near-point source sparkles on glossy glass.
    float cs = max(dot(d, s), 0.0);
    col += sc * (0.18 * pow(cs, 10.0) + 0.5 * pow(cs, 180.0)) * day * (1.0 + 1.5 * dusk) * (0.3 + 0.7 * clear)
         * (1.0 + 2.0 * w_haze * pow(cs, 4.0));
    if (!AT_CUBEMAP_PASS) {
        col += sc * 60.0 * smoothstep(0.999955, 0.999985, cs) * smoothstep(-0.02, 0.01, s.y) * clear;
    }

    if (d.y > -0.02) {
        float up = max(d.y, 0.0);
        // Stars: only the brightest get through the city's sky glow.
        if (!AT_CUBEMAP_PASS && night > 0.01) {
            vec3 p = d * 520.0;
            vec3 cell = floor(p);
            float r = hash13(cell);
            if (r > 0.9991) {
                vec3 centre = cell + 0.5 + 0.35 * (vec3(hash13(cell + 7.1), hash13(cell + 3.3), hash13(cell + 5.7)) - 0.5);
                float k = smoothstep(0.42, 0.0, length(p - centre));
                float mag = pow((r - 0.9991) / 0.0009, 3.0);
                vec3 tint = mix(vec3(0.75, 0.82, 1.0), vec3(1.0, 0.88, 0.7), hash13(cell + 1.9));
                col += tint * k * (0.3 + 4.0 * mag) * night * smoothstep(0.08, 0.45, up) * clear;
            }
        }
        // The moon: a disk lit on its sun side, earthshine on the rest, a halo.
        float cm = dot(d, normalize(moon_dir));
        if (moon_dir.y > -0.05) {
            float rad = 0.0085;                  // ~0.5 deg, a little larger than life
            float r2 = (1.0 - cm) * 2.0;
            if (r2 < rad * rad && cm > 0.0) {
                vec3 fwd = normalize(moon_dir);
                vec3 right = normalize(cross(vec3(0.0, 1.0, 0.0), fwd));
                vec3 upv = cross(fwd, right);
                vec2 q = vec2(dot(d - fwd, right), dot(d - fwd, upv)) / rad;
                vec3 nrm = normalize(vec3(q, -sqrt(max(1.0 - dot(q, q), 0.0))));
                vec3 sun_m = vec3(dot(s, right), dot(s, upv), dot(s, fwd));
                float lit = smoothstep(-0.05, 0.12, dot(nrm, sun_m));
                float maria = 0.82 + 0.18 * hash13(floor(vec3(q * 6.0, 1.0)));
                col = mix(col, vec3(1.0, 0.97, 0.9) * (0.02 + 2.6 * lit * maria),
                          smoothstep(1.0, 0.92, length(q)) * (0.3 + 0.7 * night) * (0.15 + 0.85 * clear));
            }
            col += vec3(0.55, 0.62, 0.8) * 0.12 * pow(max(cm, 0.0), 900.0) * moon_lit * night;
            col += vec3(0.55, 0.62, 0.8) * 0.03 * pow(max(cm, 0.0), 40.0) * moon_lit * night;
        }
        // Cumulus on a layer ~1.5 km up: fbm thresholded to puffs, lit on the
        // sun's side, warm underneath at dusk, lit orange from below at night.
        vec2 uv = d.xz / (up + 0.06) * 0.55;
        float n = texture(clouds, uv * 0.35).r * 0.62 + texture(clouds, uv * 1.1 + 0.37).r * 0.28
                + texture(clouds, uv * 3.3 + 0.71).r * 0.10;
        float dens = smoothstep(1.0 - cloud_cover, 1.0 - cloud_cover + 0.22, n) * smoothstep(0.0, 0.10, up);
        float thick = smoothstep(1.0 - cloud_cover + 0.05, 1.0 - cloud_cover + 0.4, n);
        vec3 lit_c = sc * (0.95 + 0.25 * pow(cs, 4.0)) * (0.35 + 0.65 * (1.0 - w_cloud));
        vec3 base_c = mix(vec3(0.55, 0.60, 0.68), vec3(0.95, 0.55, 0.35), dusk) * (1.0 - 0.45 * w_cloud);
        vec3 cloud = mix(lit_c, base_c, 0.55 * thick) * (0.25 + 0.75 * day);
        vec3 glow = vec3(0.042, 0.029, 0.019) * city_glow;
        vec3 cn = vec3(0.010, 0.012, 0.018) * (0.3 + moon_lit) + glow * (0.8 + 0.8 * thick);
        cloud = mix(cloud, mix(vec3(0.05, 0.06, 0.12), vec3(0.30, 0.16, 0.12), pow(0.5 + 0.5 * dot(normalize(d.xz + 1e-4), normalize(s.xz + 1e-4)), 4.0)) * 0.5, twil * (1.0 - day));
        cloud = mix(cloud, cn, night);
        cloud = mix(cloud, col, 1.0 - smoothstep(0.0, 0.25, up));
        col = mix(col, cloud, dens * 0.9);
    } else {
        // Below the horizon: past the far plane (40 km). The air pass fades the
        // last of the water into haze_colour, so this must be the same.
        col = haze_colour(d, s);
    }
    COLOR = col;
}
"""

const AIR_SHADER := """
shader_type spatial;
render_mode unshaded, blend_mix, depth_draw_never, depth_test_disabled, cull_disabled, shadows_disabled, fog_disabled;
//INC
uniform sampler2D depth_tex : hint_depth_texture, filter_nearest;
uniform float beta0 = 0.00009;   // extinction at street level, 1/m
uniform float scale_h = 1100.0;  // m
uniform float beta_hi = 0.0000035;
uniform float fog_beta = 0.0;
uniform float fog_h = 140.0;
//AIR

void vertex() {
    POSITION = vec4(VERTEX.xy, 1.0, 1.0);
}

// Optical depth of an exponential layer (b at y = 0, scale height H) along a
// straight segment of length L from height y0 to y1. Closed form.
float layer(float b, float H, float y0, float y1, float L) {
    y0 = max(y0, 0.0);
    y1 = max(y1, 0.0);
    float dy = y1 - y0;
    float e0 = exp(-y0 / H);
    if (abs(dy) < 0.5) { return b * L * e0; }
    return b * L * H * (e0 - exp(-y1 / H)) / dy;
}

float h12(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void fragment() {
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) { discard; }
    float depth = texture(depth_tex, SCREEN_UV).r;
    vec4 view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, depth, 1.0);
    view.xyz /= view.w;
    vec3 wp = (INV_VIEW_MATRIX * vec4(view.xyz, 1.0)).xyz;
    vec3 cam = CAMERA_POSITION_WORLD;
    vec3 r = wp - cam;
    float L = length(r);
    vec3 d = r / max(L, 1e-3);
    bool sky = depth <= 0.0;
    float a = 0.0;
    vec3 s = normalize(sun_dir);
    vec3 c = haze_colour(d, s);
    if (!sky) {
        float tau = layer(beta0, scale_h, cam.y, wp.y, L) + layer(fog_beta, fog_h, cam.y, wp.y, L)
                  + beta_hi * L;
        a = 1.0 - exp(-tau);
        // Fog is lit by the air, not the sky's blue.
        c = mix(c, air_light(s), clamp(fog_beta * 120.0, 0.0, 0.8));
    }
    // Rain: thin falling streaks, faster near the bottom of the frame.
    if (w_rain > 0.01) {
        vec2 uv = SCREEN_UV * vec2(VIEWPORT_SIZE.x / VIEWPORT_SIZE.y, 1.0);
        float streak = 0.0;
        for (int k = 0; k < 2; k++) {
            float cols = 260.0 + 180.0 * float(k);
            float col = floor(uv.x * cols + 0.15 * uv.y);
            float rnd = h12(vec2(col, float(k)));
            float y = fract(uv.y * (1.6 + rnd) - TIME * (2.2 + 1.4 * rnd + float(k)) + rnd * 7.0);
            float x = abs(fract(uv.x * cols + 0.15 * uv.y) - 0.5);
            streak += step(0.86, rnd + 0.12 * w_rain) * smoothstep(0.1, 0.0, x) * smoothstep(0.0, 0.25, y) * (1.0 - step(0.45, y));
        }
        c = mix(c, air_light(s) * 1.6, clamp(streak, 0.0, 1.0));
        a = max(a, clamp(streak, 0.0, 1.0) * 0.22 * w_rain);
    }
    if (a <= 0.0005) { discard; }
    ALBEDO = c;
    ALPHA = a;
}
"""
