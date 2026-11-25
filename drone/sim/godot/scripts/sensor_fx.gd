## A realistic EO camera for an aircraft's frames (fw_grab_frame).
##
## The aircraft's cameras render a perfect pinhole image: no lens, no sensor,
## no shutter. Real ISR cameras are none of those, and a detector trained or
## tested only on perfect frames is tested on the easy case. This node sits
## between the camera's SubViewport and the JPEG and models, on the GPU:
##
##   * lens: radial distortion (k1, k2, normalised so the frame stays full) and
##     vignetting
##   * sensor: shot noise (grows as the signal falls) and read noise, both
##     amplified by the ISO gain the exposure needs
##   * exposure: auto-exposure that meters the scene (centre-weighted, from a
##     32 x 24 copy of the camera's frame read every METER_S) and adapts with a
##     0.5 s time constant (86 % in 1 s), choosing shutter first (up to 1/250 s,
##     an airborne camera's limit against blur) and ISO after
##   * motion blur from the camera's angular rate (and its jitter) over that
##     shutter time; defocus / MTF softening
##   * gimbal and airframe jitter: small high-frequency pose noise
##   * JPEG at a video-link quality
##
## It is a second SubViewport holding a full-screen ColorRect with a canvas
## shader that samples the camera viewport's texture. The camera viewport is
## re-parented under it, so Godot draws the camera first and this pass in the
## same frame (child viewports render before their parent).
##
## Sim truth stays exact. Distortion and jitter move things in the image; the
## image <-> pinhole mapping they make is exposed as to_image()/to_pinhole() and
## a per-frame "lens" record (lens_state()). fw_detect reports pinhole
## positions, so use "photometric" where detections must line up with the
## frame pixel for pixel. Photometric effects do not move anything.
##
## Modes (fw_set_sensor {"realism": ...}): "off" (the default: the frames are
## byte-for-byte what they were), "photometric" (everything that does not move
## pixels: noise, exposure, blur, defocus, vignetting, JPEG) and "on" (all of it).
## Off by default, so detectors trained on clean frames and recorded datasets
## don't change under anyone's feet. --sensor-realism=<mode> sets the default for every
## aircraft spawned.
extends Node

const MODES := ["off", "photometric", "on"]

## What each mode turns on. Keys are also the per-effect overrides
## fw_set_sensor accepts.
const DEFAULTS := {
	"k1": -0.06, "k2": 0.008,      # barrel distortion of a ~70 deg zoom lens
	"vignette": 0.35,              # edge fall-off (1 - v r^2 at the corner)
	"noise": 1.0,                  # scale on the physical noise model
	"auto_exposure": true,
	"exposure_s": 0.0,             # >0 fixes the shutter (s); 0 = auto
	"motion_blur": true,
	"defocus_px": 0.6,             # MTF softening, blur radius in pixels
	"jpeg_quality": 75,            # a typical UAV video link; the clean path keeps 85
	"jitter_deg": 0.05,            # 1-sigma pitch/yaw jitter; roll gets twice this
}
const GEOMETRIC := ["k1", "k2", "jitter_deg"]

## Exposure model: shutter t = clamp(T_REF * G, T_MIN, T_MAX) for the total gain
## G the scene needs; ISO = G * T_REF / t once the shutter has run out.
const T_REF := 1.0 / 2000.0
const T_MIN := 1.0 / 8000.0
const T_MAX := 1.0 / 250.0
const AE_TAU_S := 0.5
const AE_TARGET := 0.18          # mid-grey, linear
const GAIN_MIN := 0.25
const GAIN_MAX := 64.0
## Sensor: full well at base ISO, in electrons per unit signal, and read noise.
const FULL_WELL_E := 8000.0
const READ_NOISE_E := 6.0
const JITTER_HZ := [6.3, 11.7, 17.1]
## How often auto-exposure reads the scene (sim s). Each read is a 32 x 24
## texture copy back from the GPU.
const METER_S := 0.5
const METER_W := 32
const METER_H := 24

var mode := "off"
var overrides := {}

var _post: SubViewport = null
var _mat: ShaderMaterial = null
var _src: SubViewport = null
var _src_parent: Node = null
var _nadir_post: SubViewport = null
var _nadir_mat: ShaderMaterial = null
var _meter_vp: SubViewport = null
var _meter_mat: ShaderMaterial = null
var _meter_due := 0.0
var _meter_pending := false

var gain := 1.0
var _gain_target := 1.0
var _last_t := -1.0
var _prev_look := Vector2.ZERO        # (yaw incl. sensor offset, elevation) last tick
var _rate := Vector2.ZERO             # rad/s
var _jit := Vector3.ZERO              # (yaw, pitch, roll) rad, now
var _jit_rate := Vector2.ZERO
var _phase: Array = []
var _frame := 0
var _shutter := T_REF
var _iso := 1.0
var _fov_v_deg := 70.0
var _aspect := 640.0 / 480.0


func _init() -> void:
	var rng := RandomNumberGenerator.new()
	rng.randomize()
	for i in 9:
		_phase.append(rng.randf() * TAU)


# ------------------------------------------------------------------ settings

func configure(req: Dictionary) -> Dictionary:
	if req.has("realism"):
		var m := str(req["realism"])
		if m in MODES:
			mode = m
	for k in DEFAULTS:
		if req.has(k):
			overrides[k] = req[k]
	if req.has("reset_overrides") and bool(req["reset_overrides"]):
		overrides.clear()
	return settings()


## Value of one effect in the current mode: the override if set, else the mode's.
func param(name: String):
	if overrides.has(name):
		return overrides[name]
	if mode == "off":
		return _zero(name)
	if mode == "photometric" and name in GEOMETRIC:
		return _zero(name)
	return DEFAULTS[name]


func _zero(name: String):
	match name:
		"auto_exposure", "motion_blur":
			return false
		"jpeg_quality":
			return 85
		_:
			return 0.0


func settings() -> Dictionary:
	var out := {"realism": mode}
	for k in DEFAULTS:
		out[k] = param(k)
	return out


func active() -> bool:
	return mode != "off" or not overrides.is_empty()


func geometric() -> bool:
	return active() and (float(param("k1")) != 0.0 or float(param("k2")) != 0.0 or float(param("jitter_deg")) > 0.0)


# ------------------------------------------------------------------ lens maths

## The lens record for the frame being drawn: everything to_image()/to_pinhole()
## need. Empty when nothing moves pixels.
func lens_state() -> Dictionary:
	if not geometric():
		return {}
	var f_h := 0.5 / tan(deg_to_rad(_fov_v_deg) * 0.5)     # focal length, image heights
	return {"k1": float(param("k1")), "k2": float(param("k2")), "aspect": _aspect,
			"jx": tan(_jit.x) * f_h, "jy": tan(_jit.y) * f_h, "roll": _jit.z}


static func _fill(k1: float, k2: float) -> float:
	return 1.0 / (1.0 + k1 + k2)


## Pinhole image point (0..1, top-left origin) -> where it lands in this frame.
static func to_image(p: Vector2, lens: Dictionary) -> Vector2:
	if lens.is_empty():
		return p
	var a: float = lens["aspect"]
	var d := 0.5 * sqrt(a * a + 1.0)
	var q := Vector2((p.x - 0.5) * a, p.y - 0.5)
	var c := cos(float(lens["roll"]))
	var s := sin(float(lens["roll"]))
	q = Vector2(c * q.x - s * q.y, s * q.x + c * q.y) + Vector2(float(lens["jx"]), float(lens["jy"]))
	var u := q / d
	var r2 := u.length_squared()
	var k1: float = lens["k1"]
	var k2: float = lens["k2"]
	var xd := u * (1.0 + k1 * r2 + k2 * r2 * r2) * _fill(k1, k2) * d
	return Vector2(xd.x / a + 0.5, xd.y + 0.5)


## A point in this frame -> the pinhole image point it shows (the inverse).
static func to_pinhole(p: Vector2, lens: Dictionary) -> Vector2:
	if lens.is_empty():
		return p
	var a: float = lens["aspect"]
	var d := 0.5 * sqrt(a * a + 1.0)
	var k1: float = lens["k1"]
	var k2: float = lens["k2"]
	var xd := Vector2((p.x - 0.5) * a, p.y - 0.5) / d
	var u := xd
	for i in 8:
		var r2 := u.length_squared()
		u = xd / ((1.0 + k1 * r2 + k2 * r2 * r2) * _fill(k1, k2))
	var t := u * d - Vector2(float(lens["jx"]), float(lens["jy"]))
	var c := cos(-float(lens["roll"]))
	var s := sin(-float(lens["roll"]))
	var q := Vector2(c * t.x - s * t.y, s * t.x + c * t.y)
	return Vector2(q.x / a + 0.5, q.y + 0.5)


## A pinhole box [x0, y0, x1, y1] in pixels of a w x h frame -> its box in the
## frame: the bounds of its outline mapped through the lens.
static func box_to_image(box: Array, w: float, h: float, lens: Dictionary) -> Array:
	if lens.is_empty():
		return box
	var lo := Vector2(INF, INF)
	var hi := Vector2(-INF, -INF)
	for i in 5:
		for j in 5:
			if i != 0 and i != 4 and j != 0 and j != 4:
				continue
			var p := Vector2(lerpf(box[0], box[2], i / 4.0) / w, lerpf(box[1], box[3], j / 4.0) / h)
			var m := to_image(p, lens)
			lo = lo.min(m)
			hi = hi.max(m)
	return [clampf(lo.x * w, 0.0, w), clampf(lo.y * h, 0.0, h), clampf(hi.x * w, 0.0, w), clampf(hi.y * h, 0.0, h)]


# ------------------------------------------------------------------ forward camera

## Put the post pass over `src` (the aircraft's forward camera viewport), or
## take it away when the mode is off. Call after configure() and whenever the
## camera viewport is rebuilt (fixedwing_manager.reset_camera).
func bind(src: SubViewport, fov_v_deg: float) -> void:
	_fov_v_deg = fov_v_deg
	if src == null or not is_instance_valid(src):
		return
	_aspect = float(src.size.x) / float(src.size.y)
	if not active():
		_unbind()
		return
	if _post == null or not is_instance_valid(_post):
		_post = _make_post()
		_mat = _post.get_meta("mat")
		get_parent().add_child(_post)
	if _src != src:
		if _src != null and is_instance_valid(_src) and _src.get_parent() == _post:
			_post.remove_child(_src)
			_src_parent.add_child(_src)
		_src = src
		_src_parent = src.get_parent()
		_src_parent.remove_child(src)
		_post.add_child(src)       # drawn before its parent: see the file header
	_post.size = src.size
	_mat.set_shader_parameter("src", src.get_texture())
	_post.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	if _meter_vp == null or not is_instance_valid(_meter_vp):
		_meter_vp = SubViewport.new()
		_meter_vp.name = "sensor_fx_meter"
		_meter_vp.disable_3d = true
		_meter_vp.size = Vector2i(METER_W, METER_H)
		_meter_vp.render_target_update_mode = SubViewport.UPDATE_DISABLED
		var rect := ColorRect.new()
		rect.set_anchors_preset(Control.PRESET_FULL_RECT)
		_meter_mat = ShaderMaterial.new()
		var sh := Shader.new()
		sh.code = "shader_type canvas_item;\nuniform sampler2D src : filter_linear;\n" \
			+ "void fragment() { COLOR = vec4(texture(src, UV).rgb, 1.0); }\n"
		_meter_mat.shader = sh
		rect.material = _meter_mat
		_meter_vp.add_child(rect)
		_post.add_child(_meter_vp)   # the camera is drawn before it too
	_meter_mat.set_shader_parameter("src", src.get_texture())
	_meter_due = 0.0


func _unbind() -> void:
	if _post != null and is_instance_valid(_post):
		if _src != null and is_instance_valid(_src) and _src.get_parent() == _post:
			_post.remove_child(_src)
			_src_parent.add_child(_src)
		_post.queue_free()
	_post = null
	_mat = null
	_src = null
	_meter_vp = null
	_meter_mat = null
	_meter_pending = false


func _make_post() -> SubViewport:
	var vp := SubViewport.new()
	vp.name = "sensor_fx"
	vp.disable_3d = true
	vp.transparent_bg = false
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	var rect := ColorRect.new()
	rect.set_anchors_preset(Control.PRESET_FULL_RECT)
	var mat := ShaderMaterial.new()
	mat.shader = _shader()
	rect.material = mat
	vp.add_child(rect)
	vp.set_meta("mat", mat)
	vp.set_meta("rect", rect)
	return vp


## Each physics tick, from fixedwing_manager._sync_camera: angular rate,
## exposure, jitter, and the shader's uniforms for the frame about to be drawn.
func tick(look_yaw: float, look_elev: float, sim_time: float) -> void:
	if not active():
		return
	var dt := 0.0 if _last_t < 0.0 else sim_time - _last_t
	_last_t = sim_time
	var look := Vector2(look_yaw, look_elev)
	if dt > 1e-4:
		var dl := Vector2(wrapf(look.x - _prev_look.x, -PI, PI), look.y - _prev_look.y)
		_rate = _rate.lerp(dl / dt, clampf(dt / 0.1, 0.0, 1.0))
		if bool(param("auto_exposure")):
			gain = lerpf(gain, _gain_target, 1.0 - exp(-dt / AE_TAU_S))
	_prev_look = look
	if not bool(param("auto_exposure")):
		gain = 1.0
	elif _meter_vp != null and is_instance_valid(_meter_vp):
		if _meter_pending:
			# Drawn on an earlier frame by now: a 32 x 24 read, no stall to speak of.
			_meter_scene(_meter_vp.get_texture().get_image(), 1.0)
			_meter_pending = false
		elif sim_time >= _meter_due:
			_meter_vp.render_target_update_mode = SubViewport.UPDATE_ONCE
			_meter_pending = true
			_meter_due = sim_time + METER_S
	# Jitter: three incommensurate sines per axis, unit variance overall.
	var sd := deg_to_rad(float(param("jitter_deg")))
	var j := Vector3.ZERO
	var jr := Vector2.ZERO
	for i in 3:
		var w: float = TAU * JITTER_HZ[i]
		j.x += sin(w * sim_time + _phase[i]) * 0.8165
		j.y += sin(w * sim_time + _phase[3 + i]) * 0.8165
		j.z += sin(w * sim_time + _phase[6 + i]) * 0.8165 * 2.0
		jr.x += cos(w * sim_time + _phase[i]) * 0.8165 * w
		jr.y += cos(w * sim_time + _phase[3 + i]) * 0.8165 * w
	_jit = j * sd
	_jit_rate = jr * sd
	_shutter = float(param("exposure_s"))
	if _shutter <= 0.0:
		_shutter = clampf(T_REF * gain, T_MIN, T_MAX)
	_iso = maxf(1.0, gain * T_REF / _shutter)
	_frame += 1
	if _mat != null:
		_apply(_mat, _aspect, _fov_v_deg, lens_state())


func _apply(mat: ShaderMaterial, aspect: float, fov_v_deg: float, lens: Dictionary) -> void:
	var f_h := 0.5 / tan(deg_to_rad(fov_v_deg) * 0.5)
	var blur := Vector2.ZERO
	if bool(param("motion_blur")):
		var r := _rate + _jit_rate
		# Image motion over the shutter, in uv units: yaw sweeps x, pitch sweeps y.
		blur = Vector2(r.x * f_h / aspect, r.y * f_h) * _shutter
	var k1 := float(param("k1"))
	var k2 := float(param("k2"))
	mat.set_shader_parameter("aspect", aspect)
	mat.set_shader_parameter("k1", k1)
	mat.set_shader_parameter("k2", k2)
	mat.set_shader_parameter("fill", _fill(k1, k2))
	mat.set_shader_parameter("jit", Vector3(lens.get("jx", 0.0), lens.get("jy", 0.0), lens.get("roll", 0.0)))
	mat.set_shader_parameter("blur", blur)
	mat.set_shader_parameter("defocus", float(param("defocus_px")))
	mat.set_shader_parameter("gain", gain)
	mat.set_shader_parameter("iso", _iso)
	mat.set_shader_parameter("noise_scale", float(param("noise")))
	mat.set_shader_parameter("vignette", float(param("vignette")))
	mat.set_shader_parameter("seed", float(_frame % 65536))


## The forward frame as JPEG bytes: the post pass's last drawn image. Also
## meters it for auto-exposure.
func grab_forward() -> PackedByteArray:
	if _post == null or not is_instance_valid(_post):
		return PackedByteArray()
	var img := _post.get_texture().get_image()
	if img == null:
		return PackedByteArray()
	img.convert(Image.FORMAT_RGB8)
	return img.save_jpg_to_buffer(float(param("jpeg_quality")) / 100.0)


## Centre-weighted mean of the frame's linear luminance, divided back by the
## gain it was drawn with: what the scene gives the sensor. The gain then heads
## for AE_TARGET at AE_TAU_S (tick()).
func _meter(img: Image) -> void:
	_meter_scene(img, gain)


## `drawn_gain`: the gain `img` was drawn with (1 for the camera's own frame).
func _meter_scene(img: Image, drawn_gain: float) -> void:
	if img == null or not bool(param("auto_exposure")):
		return
	var small := img.duplicate() as Image
	small.resize(16, 12, Image.INTERPOLATE_BILINEAR)
	var acc := 0.0
	var wsum := 0.0
	for y in 12:
		for x in 16:
			var c := small.get_pixel(x, y)
			var lin := 0.2126 * pow(c.r, 2.2) + 0.7152 * pow(c.g, 2.2) + 0.0722 * pow(c.b, 2.2)
			var dx := (x - 7.5) / 8.0
			var dy := (y - 5.5) / 6.0
			var w := exp(-1.5 * (dx * dx + dy * dy))
			acc += lin * w
			wsum += w
	var scene := maxf(1e-4, acc / wsum / maxf(drawn_gain, 1e-3))
	_gain_target = clampf(AE_TARGET / scene, GAIN_MIN, GAIN_MAX)


# ------------------------------------------------- a downward camera, if the host has one

## The nadir frame through the post pass: `src` has just drawn (UPDATE_ONCE),
## so draw this pass once over it and return the image; the lens record of the
## frame goes in `reply`. Rendered on request only, like the nadir camera.
func process_nadir(src: SubViewport, hfov_deg: float, reply: Dictionary) -> Image:
	var aspect := float(src.size.x) / float(src.size.y)
	var vfov := rad_to_deg(2.0 * atan(tan(deg_to_rad(hfov_deg) * 0.5) / aspect))
	if _nadir_post == null or not is_instance_valid(_nadir_post):
		_nadir_post = _make_post()
		_nadir_post.render_target_update_mode = SubViewport.UPDATE_DISABLED
		_nadir_mat = _nadir_post.get_meta("mat")
		get_parent().add_child(_nadir_post)
	_nadir_post.size = src.size
	_nadir_mat.set_shader_parameter("src", src.get_texture())
	var lens := {}
	if geometric():
		var f_h := 0.5 / tan(deg_to_rad(vfov) * 0.5)
		lens = {"k1": float(param("k1")), "k2": float(param("k2")), "aspect": aspect,
				"jx": tan(_jit.x) * f_h, "jy": tan(_jit.y) * f_h, "roll": _jit.z}
		reply["lens"] = lens
	_apply(_nadir_mat, aspect, vfov, lens)
	_nadir_post.render_target_update_mode = SubViewport.UPDATE_ONCE
	await RenderingServer.frame_post_draw
	if not is_instance_valid(_nadir_post):
		return null
	var img := _nadir_post.get_texture().get_image()
	if img != null:
		img.convert(Image.FORMAT_RGB8)
		_meter(img)
	return img


## GPU milliseconds of the post passes' last frames, for the perf op. Measuring
## is switched on by the first call.
func gpu_ms() -> float:
	var t := 0.0
	for vp in [_post, _nadir_post, _meter_vp]:
		if vp != null and is_instance_valid(vp):
			var rid: RID = vp.get_viewport_rid()
			RenderingServer.viewport_set_measure_render_time(rid, true)
			t += RenderingServer.viewport_get_measured_render_time_gpu(rid)
	return t


# ------------------------------------------------------------------ shader

static var _shader_cache: Shader = null

static func _shader() -> Shader:
	if _shader_cache != null:
		return _shader_cache
	var sh := Shader.new()
	sh.code = """
shader_type canvas_item;
uniform sampler2D src : filter_linear, repeat_disable;
uniform float aspect = 1.3333;
uniform float k1 = 0.0;
uniform float k2 = 0.0;
uniform float fill = 1.0;
uniform vec3 jit = vec3(0.0);       // image shift x, y (image heights), roll (rad)
uniform vec2 blur = vec2(0.0);      // motion over the shutter, uv
uniform float defocus = 0.0;        // px
uniform float gain = 1.0;
uniform float iso = 1.0;
uniform float noise_scale = 0.0;
uniform float vignette = 0.0;
uniform float seed = 0.0;

const float FULL_WELL = 8000.0;
const float READ_E = 6.0;

uvec3 pcg3d(uvec3 v) {
    v = v * 1664525u + 1013904223u;
    v.x += v.y * v.z; v.y += v.z * v.x; v.z += v.x * v.y;
    v ^= v >> 16u;
    v.x += v.y * v.z; v.y += v.z * v.x; v.z += v.x * v.y;
    return v;
}

// The frame's point -> the pinhole point it shows (SensorFx.to_pinhole).
vec2 to_pinhole(vec2 uv, out float rn) {
    float d = 0.5 * sqrt(aspect * aspect + 1.0);
    vec2 xd = vec2((uv.x - 0.5) * aspect, uv.y - 0.5) / d;
    rn = length(xd);
    vec2 u = xd;
    for (int i = 0; i < 6; i++) {
        float r2 = dot(u, u);
        u = xd / ((1.0 + k1 * r2 + k2 * r2 * r2) * fill);
    }
    vec2 t = u * d - jit.xy;
    float c = cos(-jit.z), s = sin(-jit.z);
    vec2 q = vec2(c * t.x - s * t.y, s * t.x + c * t.y);
    return vec2(q.x / aspect + 0.5, q.y + 0.5);
}

void fragment() {
    vec2 tex_px = 1.0 / vec2(textureSize(src, 0));
    float rn;
    vec2 base = to_pinhole(UV, rn);
    // Motion blur along the image motion over the shutter, and defocus as a
    // small spiral of taps: one 8-tap kernel does both.
    vec3 acc = vec3(0.0);
    const int N = 8;
    for (int i = 0; i < N; i++) {
        float f = (float(i) + 0.5) / float(N);
        float a = float(i) * 2.39996;
        vec2 o = blur * (f - 0.5) + defocus * sqrt(f) * vec2(cos(a), sin(a)) * tex_px;
        acc += texture(src, clamp(base + o, vec2(0.0), vec2(1.0))).rgb;
    }
    vec3 c = acc / float(N);
    // To linear, expose, vignette.
    vec3 lin = pow(c, vec3(2.2)) * gain * (1.0 - vignette * rn * rn);
    // Shot noise (Poisson, as Gaussian: variance = electrons) and read noise,
    // both scaled by the ISO gain.
    uvec3 h1 = pcg3d(uvec3(uvec2(FRAGCOORD.xy), uint(seed)));
    uvec3 h2 = pcg3d(h1 ^ uvec3(0x9E3779B9u));
    vec3 u1 = max(vec3(h1) / 4294967295.0, vec3(1e-7));
    vec3 u2 = vec3(h2) / 4294967295.0;
    vec3 g = sqrt(-2.0 * log(u1)) * cos(6.2831853 * u2);
    vec3 sigma = noise_scale * sqrt(iso * max(lin, vec3(0.0)) / FULL_WELL + pow(READ_E * iso / FULL_WELL, 2.0));
    lin += g * sigma;
    COLOR = vec4(pow(clamp(lin, vec3(0.0), vec3(1.0)), vec3(1.0 / 2.2)), 1.0);
}
"""
	_shader_cache = sh
	return sh
