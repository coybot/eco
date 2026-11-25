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
## The band. "eo" (everything above) or "thermal": the camera
## renders apparent temperature (thermal_kit.gd) and this pass is a thermal
## sensor's: AGC, NETD and fixed-pattern noise, the optics' blur, a palette.
var band := "eo"
var palette := "white_hot"
const BANDS := ["eo", "thermal"]
const PALETTES := ["white_hot", "black_hot", "ironbow"]
## The thermal sensor: a 640 x 512-class uncooled microbolometer.
const TH_RES := Vector2(640.0, 512.0)
const TH_NETD_C := 0.05        # temporal noise, 1 sigma (C)
const TH_FPN_C := 0.08         # column/row fixed-pattern noise left after NUC (C)
const TH_MIN_SPAN_C := 4.0     # AGC never stretches less than this
const TH_AGC_TAU_S := 0.4
var halo := 0.15               # edge halo (the sharpening real cores apply)
var _th_lo := 0.55             # AGC window, encoded (C + 40) / 120
var _th_hi := 0.70
var _th_lo_t := 0.55
var _th_hi_t := 0.70
var _fpn_seed := 0.0
var _probe := false
## Plateau histogram equalisation, as thermal cores do it (a sky in frame would
## otherwise flatten the ground): the AGC window's CDF at 33 points, eased like
## the window, and mixed with the linear stretch by TH_EQ_MIX.
const TH_LUT_N := 33
const TH_EQ_MIX := 0.6
var _lut := PackedFloat32Array()
var _lut_t := PackedFloat32Array()

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
## The AGC takes the scene as it is on its next reading, without easing from
## what it last showed: after the band switches to thermal, or the camera is
## rebuilt (a respawn), the old window can be a whole day's temperatures off.
var _th_snap := true
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
	_fpn_seed = float(rng.randi() % 9973)
	for i in TH_LUT_N:
		_lut.append(float(i) / float(TH_LUT_N - 1))
		_lut_t.append(float(i) / float(TH_LUT_N - 1))


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
	if req.has("band") and str(req["band"]) in BANDS:
		if str(req["band"]) != band:
			_restart_agc()
		band = str(req["band"])
	if req.has("palette") and str(req["palette"]) in PALETTES:
		palette = str(req["palette"])
	if req.has("halo"):
		halo = clampf(float(req["halo"]), 0.0, 1.0)
	_probe = bool(req.get("probe", false))
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
	var out := {"realism": mode, "band": band}
	if band == "thermal":
		if _probe and _src != null and is_instance_valid(_src):
			var im := _src.get_texture().get_image()
			var pts := []
			for q in [Vector2(0.5, 0.15), Vector2(0.5, 0.5), Vector2(0.3, 0.7), Vector2(0.5, 0.9)]:
				var px := im.get_pixel(int(q.x * (im.get_width() - 1)), int(q.y * (im.get_height() - 1)))
				pts.append(snappedf(px.r * 120.0 - 40.0, 0.1))
			out["probe_c"] = pts
		out["palette"] = palette
		out["halo"] = halo
		out["agc_c"] = [_th_lo * 120.0 - 40.0, _th_hi * 120.0 - 40.0]
	for k in DEFAULTS:
		out[k] = param(k)
	return out


func active() -> bool:
	return mode != "off" or not overrides.is_empty() or band == "thermal"


func thermal() -> bool:
	return band == "thermal"


func geometric() -> bool:
	return band == "eo" and active() and (float(param("k1")) != 0.0 or float(param("k2")) != 0.0 or float(param("jitter_deg")) > 0.0)


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
	_mat.shader = _thermal_shader() if thermal() else _shader()
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
	if thermal():
		_restart_agc()
	_meter_vp.use_hdr_2d = thermal()   # thermal meters temperatures (above 1.0 too)
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
	if thermal():
		_tick_thermal(sim_time)
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


## Thermal: the AGC window from the meter (a 32 x 24 copy of the temperatures,
## read every quarter second), eased over TH_AGC_TAU_S, and the noise's seed.
func _tick_thermal(sim_time: float) -> void:
	var dt := 0.0 if _last_t < 0.0 else sim_time - _last_t
	_last_t = sim_time
	if _meter_vp != null and is_instance_valid(_meter_vp):
		if _meter_pending:
			_agc_from(_meter_vp.get_texture().get_image())
			_meter_pending = false
			if _th_snap:
				_th_snap = false
				dt = -1.0          # this reading is taken as it stands
		elif sim_time >= _meter_due:
			_meter_vp.render_target_update_mode = SubViewport.UPDATE_ONCE
			_meter_pending = true
			_meter_due = sim_time + 0.25
	var k := (1.0 - exp(-dt / TH_AGC_TAU_S)) if dt > 0.0 else 1.0
	_th_lo = lerpf(_th_lo, _th_lo_t, k)
	_th_hi = lerpf(_th_hi, _th_hi_t, k)
	for i in TH_LUT_N:
		_lut[i] = lerpf(_lut[i], _lut_t[i], k)
	_frame += 1
	if _mat != null:
		_apply_thermal(_mat, Vector2(_src.size) if _src != null and is_instance_valid(_src) else TH_RES)


## Histogram AGC: the window spans the 1st to 99th percentile of the scene's
## temperatures (a little wider), never less than TH_MIN_SPAN_C.
func _restart_agc() -> void:
	_th_snap = true
	_meter_pending = false
	_meter_due = 0.0


func _agc_from(img: Image) -> void:
	if img == null:
		return
	var vals := PackedFloat32Array()
	for y in img.get_height():
		for x in img.get_width():
			vals.append(img.get_pixel(x, y).r)
	if vals.is_empty():
		return
	vals.sort()
	var lo := vals[int(0.005 * (vals.size() - 1))]
	var hi := vals[int(0.995 * (vals.size() - 1))]
	var span := maxf(hi - lo, TH_MIN_SPAN_C / 120.0)
	var mid := 0.5 * (lo + hi)
	_th_lo_t = mid - 0.52 * span
	_th_hi_t = mid + 0.52 * span
	# The histogram over the window, each bin capped at the plateau, as a CDF.
	var bins := TH_LUT_N - 1
	var h := PackedFloat32Array()
	h.resize(bins)
	h.fill(0.0)
	var w := _th_hi_t - _th_lo_t
	for v in vals:
		var b := clampi(int((v - _th_lo_t) / w * bins), 0, bins - 1)
		h[b] += 1.0
	var plateau := 1.6 * float(vals.size()) / float(bins)
	var total := 0.0
	for b in bins:
		h[b] = minf(h[b], plateau)
		total += h[b]
	var acc := 0.0
	_lut_t[0] = 0.0
	for b in bins:
		acc += h[b]
		_lut_t[b + 1] = acc / maxf(total, 1.0)


## `src_px`: the size of the frame being processed; the sensor grid is TH_RES,
## or proportionally less for a smaller frame.
func _apply_thermal(mat: ShaderMaterial, src_px: Vector2) -> void:
	var span := maxf(_th_hi - _th_lo, 1e-4)
	mat.set_shader_parameter("lo", _th_lo)
	mat.set_shader_parameter("span", span)
	mat.set_shader_parameter("netd", TH_NETD_C / 120.0 / span)
	mat.set_shader_parameter("fpn", TH_FPN_C / 120.0 / span)
	var k := minf(1.0, src_px.x / TH_RES.x)
	mat.set_shader_parameter("res", Vector2(TH_RES.x * k, TH_RES.x * k * src_px.y / maxf(src_px.x, 1.0)))
	mat.set_shader_parameter("halo", halo)
	mat.set_shader_parameter("palette", PALETTES.find(palette))
	mat.set_shader_parameter("seed", float(_frame % 65536))
	mat.set_shader_parameter("fpn_seed", _fpn_seed)
	mat.set_shader_parameter("lut", _lut)
	mat.set_shader_parameter("eq_mix", TH_EQ_MIX)


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
	if thermal():
		# The ground below sets its own AGC window (the forward view has the sky in it).
		_nadir_mat.shader = _thermal_shader()
		var keep := [_th_lo, _th_hi, _th_lo_t, _th_hi_t]
		var keep_lut := _lut.duplicate()
		_agc_from(_small(src.get_texture().get_image()))
		_th_lo = _th_lo_t
		_th_hi = _th_hi_t
		_lut = _lut_t.duplicate()
		reply["band"] = "thermal"
		_apply_thermal(_nadir_mat, Vector2(src.size))
		_lut = keep_lut
		_th_lo = keep[0]
		_th_hi = keep[1]
		_th_lo_t = keep[2]
		_th_hi_t = keep[3]
		_nadir_post.render_target_update_mode = SubViewport.UPDATE_ONCE
		await RenderingServer.frame_post_draw
		if not is_instance_valid(_nadir_post):
			return null
		var timg := _nadir_post.get_texture().get_image()
		if timg != null:
			timg.convert(Image.FORMAT_RGB8)
		return timg
	_nadir_mat.shader = _shader()
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


static func _small(img: Image) -> Image:
	if img == null:
		return null
	var s := img.duplicate() as Image
	s.resize(METER_W, METER_H, Image.INTERPOLATE_BILINEAR)
	return s


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


# ------------------------------------------------------------------ thermal shader

static var _thermal_cache: Shader = null

## Temperatures (encoded (C + 40) / 120, linear, from an HDR viewport) -> a
## thermal sensor's picture. The scene is sampled on the sensor's own pixel
## grid (`res`) with a small optical blur, so a 1280-wide request still looks
## like an upscaled 640 x 512 core; then AGC, an edge halo, the temporal NETD
## and the fixed column/row pattern, a little shading, and the palette.
static func _thermal_shader() -> Shader:
	if _thermal_cache != null:
		return _thermal_cache
	var sh := Shader.new()
	sh.code = """
shader_type canvas_item;
uniform sampler2D src : filter_linear, repeat_disable;
uniform float lo = 0.55;
uniform float span = 0.15;
uniform float netd = 0.01;
uniform float fpn = 0.01;
uniform vec2 res = vec2(640.0, 512.0);
uniform float halo = 0.15;
uniform int palette = 0;
uniform float seed = 0.0;
uniform float fpn_seed = 0.0;
uniform float lut[33];
uniform float eq_mix = 0.6;

uvec3 pcg3d(uvec3 v) {
    v = v * 1664525u + 1013904223u;
    v.x += v.y * v.z; v.y += v.z * v.x; v.z += v.x * v.y;
    v ^= v >> 16u;
    v.x += v.y * v.z; v.y += v.z * v.x; v.z += v.x * v.y;
    return v;
}
float gauss(uvec3 k) {
    uvec3 h1 = pcg3d(k);
    uvec3 h2 = pcg3d(h1 ^ uvec3(0x9E3779B9u));
    float u1 = max(float(h1.x) / 4294967295.0, 1e-7);
    float u2 = float(h2.x) / 4294967295.0;
    return sqrt(-2.0 * log(u1)) * cos(6.2831853 * u2);
}
float temp_at(vec2 uv) {
    return texture(src, clamp(uv, vec2(0.0), vec2(1.0))).r;
}
// One sensor pixel: the scene averaged over its footprint (a 2 x 2 box plus the
// optics' spread), sampled at the pixel's centre.
float sensor_px(vec2 px) {
    vec2 c = (px + 0.5) / res;
    vec2 d = 0.35 / res;
    return 0.25 * (temp_at(c + vec2(-d.x, -d.y)) + temp_at(c + vec2(d.x, -d.y))
                 + temp_at(c + vec2(-d.x, d.y)) + temp_at(c + vec2(d.x, d.y)));
}
vec3 ironbow(float t) {
    t = clamp(t, 0.0, 1.0);
    vec3 c0 = vec3(0.0, 0.0, 0.05);
    vec3 c1 = vec3(0.3, 0.0, 0.55);
    vec3 c2 = vec3(0.85, 0.1, 0.35);
    vec3 c3 = vec3(1.0, 0.55, 0.0);
    vec3 c4 = vec3(1.0, 0.95, 0.6);
    if (t < 0.25) { return mix(c0, c1, t / 0.25); }
    if (t < 0.5) { return mix(c1, c2, (t - 0.25) / 0.25); }
    if (t < 0.8) { return mix(c2, c3, (t - 0.5) / 0.3); }
    return mix(c3, c4, (t - 0.8) / 0.2);
}
void fragment() {
    // Bilinear between the four sensor pixels round this output pixel.
    vec2 p = UV * res - 0.5;
    vec2 i = floor(p);
    vec2 f = p - i;
    float v = mix(mix(sensor_px(i), sensor_px(i + vec2(1.0, 0.0)), f.x),
                  mix(sensor_px(i + vec2(0.0, 1.0)), sensor_px(i + vec2(1.0, 1.0)), f.x), f.y);
    float n = (v - lo) / span;
    // Plateau equalisation (the window's CDF), mixed with the linear stretch.
    float x = clamp(n, 0.0, 1.0) * 32.0;
    int k0 = int(floor(min(x, 31.999)));
    float eq = mix(lut[k0], lut[k0 + 1], x - float(k0));
    float lin_n = n;
    n = mix(n, eq, eq_mix);
    // The halo the core's sharpening throws round hot and cold edges.
    if (halo > 0.0) {
        float r = 3.0;
        float ring = 0.125 * (sensor_px(i + vec2(r, 0.0)) + sensor_px(i - vec2(r, 0.0))
                            + sensor_px(i + vec2(0.0, r)) + sensor_px(i - vec2(0.0, r))
                            + sensor_px(i + vec2(r, r)) + sensor_px(i - vec2(r, r))
                            + sensor_px(i + vec2(r, -r)) + sensor_px(i - vec2(r, -r)));
        n += halo * (lin_n - (ring - lo) / span);
    }
    // Temporal noise per sensor pixel per frame; the residual fixed pattern
    // (columns stronger than rows, as on real cores) stays put frame to frame.
    uvec2 sp = uvec2(clamp(i + 0.5, vec2(0.0), res - 1.0));
    n += netd * gauss(uvec3(sp, uint(seed)));
    n += fpn * gauss(uvec3(sp.x, 7u, uint(fpn_seed))) + 0.5 * fpn * gauss(uvec3(3u, sp.y, uint(fpn_seed) + 1u));
    // A faint warm centre (lens and housing radiance the NUC leaves).
    vec2 q = UV - 0.5;
    n -= 0.12 * dot(q, q);
    n = clamp(n, 0.0, 1.0);
    vec3 outc = palette == 2 ? ironbow(n) : vec3(palette == 1 ? 1.0 - n : n);
    COLOR = vec4(outc, 1.0);
}
"""
	_thermal_cache = sh
	return sh
