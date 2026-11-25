## Time of day and weather for the photoreal Manhattan scene (env_manhattan_photo).
##
## Split out of the moving world (world_life.gd, traffic/ships/people, which
## another script owns) so the sun, the sky and the city's lights have one
## keeper that does not depend on whether traffic is running.
##
## Starts at the plain day scene: the scene's own sun (scene_look.gd's photo
## sun, or SceneKit's under --look=photo), no weather. Nothing changes until
## one of these arrives over IPC (ipc_server.gd routes them to this node,
## which is the env's child "world_clock"):
##
##   fw_set_time    {"hour": 21.0, "day": 196 | "07-15" | "2026-07-15"}
##                  hour is LOCAL CLOCK time in New York (EDT in summer, EST
##                  in winter, switched on the US rule for the date's year).
##                  {"solar": true} reads hour as local solar time instead.
##                  {"photo": true} (or no hour) puts the photo sun back.
##   fw_set_weather {"haze", "fog", "cloud", "rain"}: each 0..1; keys left out
##                  keep their value.
##   fw_world_state (merged with world_life's counts by ipc_server.gd)
##
## The sun comes from Manhattan's real latitude/longitude and the date (NOAA's
## low-precision solar position: equation of time and declination from the
## fractional year, good to a few arc-minutes, far below what a frame shows).
## The moon is placed from its age, which comes from the date too, so the
## phase in the sky is the phase that night really had.
##
## IMPORTANT, the frame: the dataset is turned GRID_ROTATION_DEG (29) onto the
## street grid, so the sim's "north" (-Z) is Manhattan's avenue direction, not
## true north. A true compass azimuth A is sim bearing A - 29. Getting this
## wrong turns every shadow 29 deg off the shadows in the orthophoto.
##
## Read by: scene_look.gd (sky, haze, sun disk), night_lights.gd and the
## buildings' lit windows (through env.night_amount()), thermal_kit.gd
## (`_time`, `_sun_angles()`, `_weather`: the names world_life used, kept so
## the thermal band's day/night model needs no second code path).
extends Node3D

signal clock_changed

const SYNODIC_DAYS := 29.530589
## A new moon to count the age from: 2000-01-06 18:14 UTC.
const NEW_MOON_UNIX := 947182440.0
## Midtown, for the sun. The island spans 0.15 deg of latitude: the sun moves
## less than that across it, so one point is enough.
const LAT := 40.758
const LON := -73.985
const DEFAULT_DAY := 196       # 15 July: a summer day, like the orthophoto's
const DEFAULT_YEAR := 2026

var env: Node3D
var look := "photo"            # scene_look's, read once
var _grid_rot := 29.0
var _sun: DirectionalLight3D
var _other_suns: Array[DirectionalLight3D] = []
var _moon: DirectionalLight3D
var _we: Environment
var _sky: ProceduralSkyMaterial  # --look=photo's sky (main.tscn's); null under beauty
var _base := {}
var _night := 0.0
## {} = the photo's own sun. Else {"hour" (local clock), "day_of_year", "year",
## "solar_hour", "utc_offset"}. thermal_kit reads "hour".
var _time := {}
var _weather := {"haze": 0.0, "fog": 0.0, "cloud": 0.0, "rain": 0.0}
var _moon_age_override := -1.0
var _cloud_shadow: MeshInstance3D
var _cloud_shadow_mat: ShaderMaterial
var _clouds: MeshInstance3D        # photo look only; beauty's sky draws its own
var _cloud_mat: ShaderMaterial
var _rain: GPUParticles3D          # photo look only; beauty's haze pass draws streaks


func _init() -> void:
	name = "world_clock"


func _ready() -> void:
	env = get_parent()
	var gr = env.get("GRID_ROTATION_DEG")
	if gr != null:
		_grid_rot = float(gr)
	var sl := env.get_node_or_null("scene_look")
	if sl != null:
		look = str(sl.get("look"))
	var ma := IpcServer._get_launch_arg("--moon-age", "")
	if ma != "":
		_moon_age_override = float(ma)
	_find_lighting()
	_capture_base()
	print("[world_clock] %s look; photo sun az %.0f el %.0f (true); fw_set_time / fw_set_weather"
		% [look, _sun_angles().x, _sun_angles().y])


# =================================================================== IPC
func set_time(req: Dictionary) -> Dictionary:
	if req.get("photo", false) or not req.has("hour"):
		_time = {}
		_apply_light()
		clock_changed.emit()
		return world_state()
	var d := _parse_day(req)
	var hour := fposmod(float(req["hour"]), 24.0)
	var off := _utc_offset(int(d["year"]), int(d["doy"]))
	var t := {"hour": hour, "day_of_year": int(d["doy"]), "year": int(d["year"]), "utc_offset": off}
	if req.get("solar", false):
		t["solar_hour"] = hour
		t["hour"] = fposmod(hour - _solar_shift_h(int(d["doy"]), hour - off, off), 24.0)
	else:
		t["solar_hour"] = fposmod(hour + _solar_shift_h(int(d["doy"]), hour - off, off), 24.0)
	_time = t
	_apply_light()
	clock_changed.emit()
	return world_state()


func set_weather(req: Dictionary) -> Dictionary:
	for k in _weather.keys():
		if req.has(k):
			_weather[k] = clampf(float(req[k]), 0.0, 1.0)
	_apply_light()
	_apply_clouds_rain()
	clock_changed.emit()
	return world_state()


func world_state() -> Dictionary:
	var ang := _sun_angles()
	var md := moon_dir()
	return {"ok": true, "time": _time if not _time.is_empty() else "photo", "night": _night,
		"weather": _weather, "look": look,
		"sun": {"azimuth_deg": ang.x, "elevation_deg": ang.y},
		"moon": {"elevation_deg": rad_to_deg(asin(clampf(md.y, -1.0, 1.0))),
				 "age_days": moon_age(), "lit": moon_lit()}}


func night_amount() -> float:
	return _night


func weather() -> Dictionary:
	return _weather


## Toward the sun in Godot world space, where it REALLY is (the light itself is
## held just above the horizon after sunset; the sky and the hook want truth).
func sun_dir() -> Vector3:
	var a := _sun_angles()
	return to_godot(a.x, a.y)


## True compass azimuth (deg) and elevation (deg) -> toward it, Godot space.
func to_godot(az_true: float, el_deg: float) -> Vector3:
	var b := deg_to_rad(az_true - _grid_rot)
	var el := deg_to_rad(el_deg)
	return Vector3(sin(b) * cos(el), sin(el), -cos(b) * cos(el)).normalized()


# =================================================================== the sun
## (true azimuth deg clockwise from north toward the sun, elevation deg).
## With no time set: the photo sun's, read back off the light.
func _sun_angles() -> Vector2:
	if _time.is_empty():
		if _base.has("sun_xf"):
			var z: Vector3 = (_base["sun_xf"] as Transform3D).basis.z.normalized()
			var el := rad_to_deg(asin(clampf(z.y, -1.0, 1.0)))
			var b := rad_to_deg(atan2(z.x, -z.z))
			return Vector2(fposmod(b + _grid_rot, 360.0), el)
		return Vector2(138.0, 48.0)
	var doy := int(_time["day_of_year"])
	var utc_h := float(_time["hour"]) - float(_time["utc_offset"])
	var g := TAU / 365.0 * (doy - 1 + (utc_h - 12.0) / 24.0)
	var decl := 0.006918 - 0.399912 * cos(g) + 0.070257 * sin(g) - 0.006758 * cos(2 * g) \
		+ 0.000907 * sin(2 * g) - 0.002697 * cos(3 * g) + 0.00148 * sin(3 * g)
	var solar := float(_time["solar_hour"])
	var ha := deg_to_rad(15.0 * (solar - 12.0))
	var lat := deg_to_rad(LAT)
	var el := asin(sin(lat) * sin(decl) + cos(lat) * cos(decl) * cos(ha))
	var az := atan2(sin(ha), cos(ha) * sin(lat) - tan(decl) * cos(lat)) + PI
	return Vector2(fposmod(rad_to_deg(az), 360.0), rad_to_deg(el))


## Hours to add to clock time to get local solar time: longitude against the
## zone's meridian, plus the equation of time.
func _solar_shift_h(doy: int, utc_h: float, off: float) -> float:
	var g := TAU / 365.0 * (doy - 1 + (utc_h - 12.0) / 24.0)
	var eot := 229.18 * (0.000075 + 0.001868 * cos(g) - 0.032077 * sin(g)
		- 0.014615 * cos(2 * g) - 0.040849 * sin(2 * g))
	return (eot + 4.0 * LON - 60.0 * off) / 60.0


## New York's offset from UTC (h): -4 from the second Sunday of March to the
## first Sunday of November (the US rule since 2007), else -5.
static func _utc_offset(year: int, doy: int) -> float:
	var start := _nth_sunday_doy(year, 3, 2)
	var end := _nth_sunday_doy(year, 11, 1)
	return -4.0 if doy >= start and doy < end else -5.0


static func _nth_sunday_doy(year: int, month: int, nth: int) -> int:
	var u := Time.get_unix_time_from_datetime_dict({"year": year, "month": month, "day": 1,
		"hour": 12, "minute": 0, "second": 0})
	var wd: int = Time.get_datetime_dict_from_unix_time(u)["weekday"]   # 0 = Sunday
	var first := 1 + (7 - wd) % 7
	var day := first + 7 * (nth - 1)
	var jan1 := Time.get_unix_time_from_datetime_dict({"year": year, "month": 1, "day": 1,
		"hour": 12, "minute": 0, "second": 0})
	return int(round((u - jan1) / 86400.0)) + day


## "day": 196 | "07-15" | "2026-07-15"; "day_of_year" is accepted too (the
## older world_life name). -> {"doy", "year"}
static func _parse_day(req: Dictionary) -> Dictionary:
	var year := int(req.get("year", DEFAULT_YEAR))
	var v = req.get("day", req.get("day_of_year", DEFAULT_DAY))
	if v is String and (v as String).contains("-"):
		var p := (v as String).split("-")
		var mo := 0
		var dd := 0
		if p.size() == 3:
			year = int(p[0])
			mo = int(p[1])
			dd = int(p[2])
		elif p.size() == 2:
			mo = int(p[0])
			dd = int(p[1])
		if mo >= 1 and mo <= 12 and dd >= 1 and dd <= 31:
			var u := Time.get_unix_time_from_datetime_dict({"year": year, "month": mo, "day": dd,
				"hour": 12, "minute": 0, "second": 0})
			var jan1 := Time.get_unix_time_from_datetime_dict({"year": year, "month": 1, "day": 1,
				"hour": 12, "minute": 0, "second": 0})
			return {"doy": int(round((u - jan1) / 86400.0)) + 1, "year": year}
		return {"doy": DEFAULT_DAY, "year": year}
	return {"doy": clampi(int(v), 1, 366), "year": year}


# =================================================================== the moon
## Days since new moon at the set time (or --moon-age).
func moon_age() -> float:
	if _moon_age_override >= 0.0:
		return fposmod(_moon_age_override, SYNODIC_DAYS)
	var year := int(_time.get("year", DEFAULT_YEAR))
	var doy := int(_time.get("day_of_year", DEFAULT_DAY))
	var utc_h := float(_time.get("hour", 12.0)) - float(_time.get("utc_offset", -4.0))
	var jan1 := Time.get_unix_time_from_datetime_dict({"year": year, "month": 1, "day": 1,
		"hour": 0, "minute": 0, "second": 0})
	var u := float(jan1) + (doy - 1) * 86400.0 + utc_h * 3600.0
	return fposmod((u - NEW_MOON_UNIX) / 86400.0, SYNODIC_DAYS)


## Lit fraction of the disk, 0 new .. 1 full.
func moon_lit() -> float:
	return 0.5 * (1.0 - cos(TAU * moon_age() / SYNODIC_DAYS))


## Toward the moon, Godot space. The moon runs the sun's daily path, held back
## by its elongation (it transits ~50 min later each day), at the declination
## of its ecliptic longitude. Ignores the orbit's 5 deg tilt: a few degrees on
## a sky the camera rarely looks at. Without a set time (the photo's daylight)
## it is below the horizon.
func moon_dir() -> Vector3:
	if _time.is_empty():
		return Vector3.DOWN
	var elong := TAU * moon_age() / SYNODIC_DAYS
	var doy := float(_time["day_of_year"])
	var sun_lon := TAU / 365.2422 * (doy - 80.0)
	var decl := asin(sin(deg_to_rad(23.44)) * sin(sun_lon + elong))
	var ha := deg_to_rad(15.0 * (float(_time["solar_hour"]) - 12.0)) - elong
	var lat := deg_to_rad(LAT)
	var el := asin(sin(lat) * sin(decl) + cos(lat) * cos(decl) * cos(ha))
	var az := atan2(sin(ha), cos(ha) * sin(lat) - tan(decl) * cos(lat)) + PI
	return to_godot(rad_to_deg(az), rad_to_deg(el))


# =================================================================== lighting
func _find_lighting() -> void:
	for c in env.get_children():
		if c is DirectionalLight3D and _sun == null:
			_sun = c
	# The ACTIVE environment: main.tscn's WorldEnvironment enters the tree first
	# and wins; SceneKit's (an env child) is ignored by Godot.
	_we = get_world_3d().environment if get_world_3d() else null
	if _we and _we.sky and _we.sky.sky_material is ProceduralSkyMaterial:
		_sky = _we.sky.sky_material
	for n in get_tree().root.find_children("*", "DirectionalLight3D", true, false):
		if n != _sun and n is DirectionalLight3D and n.name != "moon":
			_other_suns.append(n)
	_moon = DirectionalLight3D.new()
	_moon.name = "moon"
	_moon.light_energy = 0.0
	_moon.light_color = Color(0.70, 0.78, 0.95)
	_moon.shadow_enabled = false
	_moon.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY   # the sky draws its own moon
	_moon.visible = false
	add_child(_moon)


func _capture_base() -> void:
	if _sun:
		_base["sun_xf"] = _sun.global_transform
		_base["sun_energy"] = _sun.light_energy
		_base["sun_color"] = _sun.light_color
	if _we:
		_base["ambient"] = _we.ambient_light_energy
		_base["exposure"] = _we.tonemap_exposure
		_base["glow"] = _we.glow_enabled
		_base["glow_intensity"] = _we.glow_intensity
		_base["glow_bloom"] = _we.glow_bloom
		_base["glow_threshold"] = _we.glow_hdr_threshold
		_base["fog_on"] = _we.fog_enabled
		_base["fog_density"] = _we.fog_density
		_base["fog_color"] = _we.fog_light_color
		_base["fog_aerial"] = _we.fog_aerial_perspective
		_base["fog_sky"] = _we.fog_sky_affect
		if not _we.fog_enabled:
			# No fog in the plain scene: --look=photo's weather starts from the
			# large-env haze LightingKit meant (~85% transmission at 1 km).
			_base["fog_density"] = 0.00016
			_base["fog_color"] = Color(0.80, 0.83, 0.86)
			_base["fog_aerial"] = 0.6
			_base["fog_sky"] = 0.15
	_base["other_suns"] = _other_suns.map(func(l: DirectionalLight3D) -> float: return l.light_energy)
	if _sky:
		_base["sky_top"] = _sky.sky_top_color
		_base["sky_horizon"] = _sky.sky_horizon_color
		_base["ground_horizon"] = _sky.ground_horizon_color
		_base["ground_bottom"] = _sky.ground_bottom_color


func _apply_light() -> void:
	if _sun == null or _we == null:
		return
	var day := 1.0
	var warm := 0.0
	var ang := _sun_angles()
	if _time.is_empty():
		_sun.global_transform = _base["sun_xf"]
		_sun.light_energy = _base["sun_energy"]
		_sun.light_color = _base["sun_color"]
		_sun.shadow_enabled = true
		_night = 0.0
	else:
		var el_deg := ang.y
		# Held just above the horizon once the sun sets (and dimmed out), so the
		# last light rakes across the city rather than coming up through it.
		var to_sun := to_godot(ang.x, maxf(el_deg, 0.5))
		_sun.global_transform = Transform3D(Basis.looking_at(-to_sun, Vector3.UP), _sun.global_transform.origin)
		day = smoothstep(-6.0, 8.0, el_deg)
		warm = (1.0 - smoothstep(3.0, 20.0, el_deg)) * smoothstep(-6.0, 2.0, el_deg)
		# One sun: it takes over the other suns' light (--look=photo has two).
		var extra := 0.0
		for e in _base["other_suns"]:
			extra += float(e)
		_sun.light_energy = (float(_base["sun_energy"]) + 0.6 * extra) * smoothstep(-1.0, 10.0, el_deg)
		# Low sun through 10-30 km of air: gold, then orange-red.
		_sun.light_color = (_base["sun_color"] as Color).lerp(Color(1.0, 0.68, 0.42), warm)
		_sun.shadow_enabled = el_deg > -1.0
		_night = 1.0 - smoothstep(-4.0, 3.0, el_deg)
	# The moon: faint, blue, and only up when it is.
	var md := moon_dir()
	if md.y > 0.02 and _night > 0.01:
		_moon.global_transform = Transform3D(Basis.looking_at(-md, Vector3.UP), Vector3.ZERO)
		_moon.light_energy = 0.14 * _night * (0.25 + 0.75 * moon_lit())
		_moon.visible = true
	else:
		_moon.light_energy = 0.0
		_moon.visible = false
	# Weather dims the sun and thickens the air.
	var w := _weather
	var dim: float = (1.0 - 0.75 * w["cloud"]) * (1.0 - 0.45 * w["rain"]) * (1.0 - 0.4 * w["fog"]) \
		* (1.0 - 0.15 * w["haze"])
	_sun.light_energy *= dim
	_moon.light_energy *= 1.0 - 0.8 * maxf(w["cloud"], w["rain"])
	for k in _other_suns.size():
		_other_suns[k].light_energy = (float(_base["other_suns"][k]) if _time.is_empty() else 0.0) * dim
	# Overcast light is soft: less sun, more sky.
	_we.ambient_light_energy = float(_base["ambient"]) * (0.18 + 0.82 * day) * (1.0 + 0.45 * w["cloud"])
	_we.tonemap_exposure = float(_base["exposure"]) * (1.0 + 0.9 * _night)
	_we.glow_enabled = _night > 0.3 or bool(_base["glow"])
	if _night > 0.3:
		_we.glow_intensity = 0.8
		_we.glow_bloom = 0.06
		_we.glow_hdr_threshold = 1.2
	elif _base.has("glow_intensity"):
		_we.glow_intensity = _base["glow_intensity"]
		_we.glow_bloom = _base["glow_bloom"]
		_we.glow_hdr_threshold = _base["glow_threshold"]
	if look != "beauty":
		_photo_atmosphere(day, warm)


## --look=photo's air: Godot's own fog and the procedural sky, as world_life
## did it. Beauty draws its air in scene_look.gd's haze pass instead, because
## Godot's distance fog cannot serve a 21 km city (env_manhattan.gd's note).
func _photo_atmosphere(day: float, warm: float) -> void:
	var w := _weather
	var fog_c := (_base["fog_color"] as Color)
	fog_c = fog_c.lerp(Color(0.86, 0.87, 0.88), 0.5 * w["haze"])
	fog_c = fog_c.lerp(Color(0.6, 0.62, 0.65), 0.5 * w["rain"])
	fog_c = fog_c.lerp(Color(1.0, 0.6, 0.4), 0.4 * warm)
	var weathered := false
	for k in w.keys():
		weathered = weathered or float(w[k]) > 0.0
	_we.fog_enabled = bool(_base["fog_on"]) or weathered or not _time.is_empty()
	_we.fog_light_color = fog_c * (0.15 + 0.85 * day)
	_we.fog_density = float(_base["fog_density"]) + 0.0007 * w["haze"] + 0.0035 * w["fog"] + 0.0009 * w["rain"]
	_we.fog_aerial_perspective = clampf(float(_base["fog_aerial"]) + 0.3 * w["haze"], 0.0, 1.0)
	_we.fog_sky_affect = clampf(float(_base["fog_sky"]) + 0.6 * w["fog"] + 0.3 * w["haze"], 0.0, 1.0)
	_we.fog_height = 60.0
	_we.fog_height_density = 0.04 * w["fog"]
	if _sky:
		var top := (_base["sky_top"] as Color)
		var hor := (_base["sky_horizon"] as Color)
		top = top.lerp(Color(0.25, 0.28, 0.42), 0.6 * warm).lerp(Color(0.01, 0.015, 0.04), 1.0 - day)
		hor = hor.lerp(Color(1.0, 0.55, 0.32), 0.75 * warm).lerp(Color(0.05, 0.045, 0.06), 1.0 - day)
		var grey := Color(0.62, 0.64, 0.66) * (0.2 + 0.8 * day)
		top = top.lerp(grey, 0.75 * maxf(w["cloud"], w["rain"]))
		hor = hor.lerp(grey, 0.6 * maxf(w["cloud"], w["rain"])).lerp(fog_c * day, 0.7 * w["haze"])
		_sky.sky_top_color = top
		_sky.sky_horizon_color = hor
		_sky.ground_horizon_color = (_base["ground_horizon"] as Color) * (0.1 + 0.9 * day)
		_sky.ground_bottom_color = (_base["ground_bottom"] as Color) * (0.1 + 0.9 * day)


# =================================================================== clouds, rain
## Cloud shadows in both looks (a shadow-only, alpha-scissored layer drifting
## 1.6 km up); in --look=photo also a visible layer and rain particles, which
## beauty's sky shader and haze pass draw for every camera instead.
func _apply_clouds_rain() -> void:
	var c: float = _weather["cloud"]
	if c > 0.01 and _cloud_shadow == null:
		var noise := FastNoiseLite.new()
		noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
		noise.frequency = 0.012
		noise.fractal_octaves = 4
		noise.seed = 4071
		var nt := NoiseTexture2D.new()
		nt.width = 512
		nt.height = 512
		nt.seamless = true
		nt.generate_mipmaps = true
		nt.noise = noise
		var common := """
uniform sampler2D noise : repeat_enable, filter_linear_mipmap;
uniform float cover = 0.5;
varying vec3 v_world;
void vertex() { v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
float density() {
    vec2 p = v_world.xz / 5200.0 + TIME * vec2(0.0009, 0.0005);
    float n = texture(noise, p).r * 0.6 + texture(noise, p * 2.7 + 0.37).r * 0.3 + texture(noise, p * 7.3 + 0.71).r * 0.1;
    return smoothstep(1.0 - cover, 1.0 - cover + 0.22, n);
}
"""
		_cloud_shadow_mat = ShaderMaterial.new()
		var sh2 := Shader.new()
		sh2.code = "shader_type spatial;\nrender_mode cull_disabled;\n" + common + """
void fragment() {
    ALBEDO = vec3(1.0);
    ALPHA = density();
    ALPHA_SCISSOR_THRESHOLD = 0.45;
}
"""
		_cloud_shadow_mat.shader = sh2
		_cloud_shadow_mat.set_shader_parameter("noise", nt)
		_cloud_shadow = _cloud_plane("cloud_shadows", _cloud_shadow_mat)
		_cloud_shadow.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY
		if look != "beauty":
			_cloud_mat = ShaderMaterial.new()
			var sh := Shader.new()
			sh.code = "shader_type spatial;\nrender_mode unshaded, cull_disabled, depth_draw_never, shadows_disabled;\nuniform float light = 1.0;\n" + common + """
void fragment() {
    float d = density();
    float far = 1.0 - smoothstep(3500.0, 9000.0, length(v_world.xz - CAMERA_POSITION_WORLD.xz));
    ALBEDO = mix(vec3(0.70, 0.72, 0.76), vec3(0.98, 0.98, 0.97), smoothstep(0.2, 0.9, d)) * light;
    ALPHA = d * 0.92 * far;
}
"""
			_cloud_mat.shader = sh
			_cloud_mat.set_shader_parameter("noise", nt)
			_clouds = _cloud_plane("clouds", _cloud_mat)
			_clouds.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	if _cloud_shadow:
		_cloud_shadow.visible = c > 0.01
		_cloud_shadow_mat.set_shader_parameter("cover", 0.15 + 0.75 * c)
	if _clouds:
		_clouds.visible = c > 0.01
		_cloud_mat.set_shader_parameter("cover", 0.15 + 0.75 * c)
		_cloud_mat.set_shader_parameter("light", 0.12 + 0.88 * (1.0 - _night))
	var r: float = _weather["rain"]
	if look == "beauty":
		return
	if r > 0.01 and _rain == null:
		_rain = GPUParticles3D.new()
		_rain.name = "rain"
		var pm := ParticleProcessMaterial.new()
		pm.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
		pm.emission_box_extents = Vector3(110.0, 1.0, 110.0)
		pm.direction = Vector3(0.08, -1.0, 0.04)
		pm.spread = 2.0
		pm.initial_velocity_min = 16.0
		pm.initial_velocity_max = 20.0
		pm.gravity = Vector3(0, -2.0, 0)
		_rain.process_material = pm
		var q := QuadMesh.new()
		q.size = Vector2(0.025, 0.9)
		var qm := StandardMaterial3D.new()
		qm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		qm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		qm.albedo_color = Color(0.8, 0.85, 0.9, 0.35)
		qm.billboard_mode = BaseMaterial3D.BILLBOARD_FIXED_Y
		qm.billboard_keep_scale = true
		q.material = qm
		_rain.draw_pass_1 = q
		_rain.lifetime = 4.0
		_rain.visibility_aabb = AABB(Vector3(-130, -120, -130), Vector3(260, 140, 260))
		_rain.local_coords = false
		add_child(_rain)
	if _rain:
		_rain.amount = maxi(1, int(9000.0 * r))
		_rain.emitting = r > 0.01
		_rain.visible = r > 0.01


func _cloud_plane(node_name: String, mat: ShaderMaterial) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.name = node_name
	var pm := PlaneMesh.new()
	pm.size = Vector2(32000.0, 32000.0)
	mi.mesh = pm
	mi.material_override = mat
	mi.position = Vector3(0.0, 1600.0, 0.0)
	add_child(mi)
	return mi


func _process(_dt: float) -> void:
	if _rain and _rain.emitting:
		var cam := get_viewport().get_camera_3d()
		if cam:
			_rain.global_position = cam.global_position + Vector3(0, 40, 0)
