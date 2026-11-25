## The thermal (LWIR) band for the aircraft cameras.
##
## A camera in the thermal band (fixedwing_manager.set_band) renders the same
## world as the EO cameras, but every surface draws its apparent temperature
## instead of its colour (shaders/thermal.gdshaderinc), into an HDR viewport,
## and sensor_fx.gd turns that into a sensor image: AGC, NETD noise, fixed-
## pattern noise, the optics' blur and a palette.
##
## How a surface knows which camera is drawing it: a thermal camera's cull mask
## lacks render layer 20 (MASK); every other camera keeps Godot's default of all
## 20 layers. Shaders branch on CAMERA_VISIBLE_LAYERS. A scene's own shaders
## can do this natively (include shaders/thermal.gdshaderinc and branch on
## thermal_pass()); everything else (StandardMaterial3D models, buildings,
## boats, cranes, props, aircraft) gets a material_overlay
## from here while any camera is thermal, carrying its class's heat as
## instance uniforms. Overlays come off again when the last thermal camera goes
## back to EO, so the EO cameras cost nothing extra when no one uses thermal.
## Transparent things (rotor discs, rain, clouds) move to layer 20 only, which
## hides them from thermal cameras and from no one else.
##
## Temperatures follow the scene's time of day when it keeps one (its
## "world_clock" child, or any child with a `_time` {"hour"} and
## `_sun_angles()`), else photo_hour.
## fw_set_thermal sets the climate (air by day and night, the sea) and the hour.
extends Node

const LAYER_BIT := 19
const MASK := 0xFFFFF & ~(1 << 19)
const ONLY_LAYER_20 := 1 << 19
const INC := "res://shaders/thermal.gdshaderinc"
## Godot's Lambert diffuse: albedo * light energy * N.L (energy is already
## normalised; measured against fw_set_thermal's sun term on flat sand).
const LAMBERT := 1.0

## The climate (C): a hot coastal one by default, days ~31, nights ~24, the sea ~28.
var air_day_c := 31.0
var air_night_c := 24.0
var sea_c := 27.5
## The hour of day used when the scene keeps no time of its own.
var photo_hour := 11.0

var _users: Dictionary = {}          # camera instance id -> true
var _env_cache: Environment = null
var _overlay_shader: Shader = null
var _building_shader: Shader = null
var _overlays: Array = []            # [GeometryInstance3D, previous overlay]
var _hidden: Array = []              # [GeometryInstance3D, previous layers]
var _rescan_due := 0.0
var _sun_ref := -1.0
var state := {}                      # what the globals were last set to


func _ready() -> void:
	_set_globals(_compute())


# ------------------------------------------------------------------ cameras

## Put `cam` (and its viewport `vp`) in the thermal band, or back to EO.
func set_camera(cam: Camera3D, vp: SubViewport, thermal: bool) -> void:
	if cam == null:
		return
	var key := cam.get_instance_id()
	if thermal:
		cam.cull_mask = MASK
		cam.environment = environment()
		var attrs := CameraAttributesPractical.new()
		attrs.auto_exposure_enabled = false
		attrs.exposure_multiplier = 1.0
		cam.attributes = attrs
		if vp != null:
			vp.use_hdr_2d = true      # temperatures, not 8-bit colour
		_users[key] = true
	else:
		cam.cull_mask = 0xFFFFF
		cam.environment = null
		cam.attributes = null
		if vp != null:
			vp.use_hdr_2d = false
		_users.erase(key)
	if _users.is_empty():
		_detach()
	else:
		_rescan_due = 0.0


func in_use() -> bool:
	return not _users.is_empty()


## The thermal camera's world: no ambient light, no sky light, no fog or glow,
## a linear tonemap, and a sky of its own (cold overhead, near the air at the
## horizon). The sun stays: it is how the surfaces' solar heating is drawn.
func environment() -> Environment:
	if _env_cache != null:
		return _env_cache
	var e := Environment.new()
	var sh := Shader.new()
	sh.code = "shader_type sky;\n#include \"%s\"\n" % INC + """
void sky() {
    // Low down the sky is the air's own warmth (a long, humid path); it falls to
    // the cold zenith as the path shortens, about half way by 20 deg up.
    float up = clamp(EYEDIR.y, 0.0, 1.0);
    float t = mix(thermal_air_c - 1.0, thermal_sky_c, smoothstep(0.0, 1.0, pow(up, 0.75)));
    if (EYEDIR.y < 0.0) { t = thermal_air_c; }
    COLOR = vec3(th_enc(t));
}
"""
	var sm := ShaderMaterial.new()
	sm.shader = sh
	var sky := Sky.new()
	sky.sky_material = sm
	sky.process_mode = Sky.PROCESS_MODE_REALTIME
	e.background_mode = Environment.BG_SKY
	e.sky = sky
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color.BLACK
	e.ambient_light_energy = 0.0
	e.reflected_light_source = Environment.REFLECTION_SOURCE_DISABLED
	e.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	e.tonemap_exposure = 1.0
	e.glow_enabled = false
	e.fog_enabled = false
	e.volumetric_fog_enabled = false
	e.ssao_enabled = false
	e.ssil_enabled = false
	e.sdfgi_enabled = false
	e.adjustment_enabled = false
	_env_cache = e
	return e


# ------------------------------------------------------------------ climate

func set_climate(req: Dictionary) -> Dictionary:
	air_day_c = float(req.get("air_day_c", air_day_c))
	air_night_c = float(req.get("air_night_c", air_night_c))
	sea_c = float(req.get("sea_c", sea_c))
	photo_hour = clampf(float(req.get("photo_hour", photo_hour)), 0.0, 24.0)
	_set_globals(_compute())
	return climate()


func climate() -> Dictionary:
	return {"air_day_c": air_day_c, "air_night_c": air_night_c, "sea_c": sea_c,
			"photo_hour": photo_hour, "now": state,
			"overlays": _overlays.size(), "hidden": _hidden.size(), "cameras": _users.size()}


## The scene's time-of-day keeper, if it has one: the env's "world_clock"
## (world_clock.gd), else any child with `_time` and `_sun_angles()` (older
## scenes kept time in world_life).
func _world_clock() -> Node:
	var env: Node = FixedWingManager._env
	if env == null:
		return null
	var wc := env.get_node_or_null("world_clock")
	if wc != null:
		return wc
	for c in env.get_children():
		if c.has_method("_sun_angles") and c.get("_time") != null:
			return c
	return null


## Hour, sun elevation, cloud: from the scene's time of day when it sets one,
## else photo_hour.
func _sky_now() -> Dictionary:
	var wl := _world_clock()
	var hour := photo_hour
	var el := 45.0
	var cloud := 0.0
	var suns: Array = []
	var root := get_tree().root if is_inside_tree() else null
	if root != null:
		suns = root.find_children("*", "DirectionalLight3D", true, false)
	if wl != null:
		var t: Dictionary = wl.get("_time")
		if t != null and not t.is_empty():
			hour = float(t.get("hour", photo_hour))
			if wl.has_method("_sun_angles"):
				el = float(wl.call("_sun_angles").y)
		var w = wl.get("_weather")
		if w is Dictionary:
			cloud = float(w.get("cloud", 0.0))
	if el == 45.0:
		# The photo's own sun: its elevation from the brightest light's direction.
		var best: DirectionalLight3D = null
		for s in suns:
			if best == null or (s as DirectionalLight3D).light_energy > best.light_energy:
				best = s
		if best != null:
			var to_sun := best.global_transform.basis.z
			el = rad_to_deg(asin(clampf(to_sun.y, -1.0, 1.0)))
	var energy := 0.0
	for s in suns:
		if (s as DirectionalLight3D).visible:
			energy += (s as DirectionalLight3D).light_energy
	return {"hour": hour, "el": el, "cloud": cloud, "energy": energy}


func _compute() -> Dictionary:
	var sky := _sky_now()
	var h: float = sky["hour"]
	var el: float = sky["el"]
	var mid := 0.5 * (air_day_c + air_night_c)
	var amp := 0.5 * (air_day_c - air_night_c)
	var air := mid + amp * cos(TAU * (h - 15.0) / 24.0)
	# Heat held in heavy things peaks late afternoon and is gone by dawn.
	var store := clampf(0.5 + 0.5 * cos(TAU * (h - 16.5) / 24.0), 0.0, 1.0)
	var day := smoothstep(-2.0, 12.0, el)
	var cool := (1.0 - day) * (1.0 - 0.7 * float(sky["cloud"]))
	if _sun_ref <= 0.0 and float(sky["energy"]) > 0.05:
		_sun_ref = float(sky["energy"])    # the scene's sun at its first look: full sun
	var ref := maxf(_sun_ref, 0.05)
	# ALBEDO x energy x N.L x LAMBERT = absorb x sun_c / 120 at full sun.
	var gain := smoothstep(-1.0, 3.0, el) / (120.0 * ref * LAMBERT)
	var sky_c := air - 30.0 + 22.0 * float(sky["cloud"])
	# Lamps are lit from dusk to dawn.
	var lamps := 1.0 - smoothstep(-6.0, 2.0, el)
	return {"hour": h, "sun_elevation_deg": el, "air_c": air, "sea_c": sea_c, "store": store,
			"cool": cool, "sun_gain": gain, "sky_c": sky_c, "lamps": lamps}


func _set_globals(s: Dictionary) -> void:
	state = s
	RenderingServer.global_shader_parameter_set("thermal_air_c", s["air_c"])
	RenderingServer.global_shader_parameter_set("thermal_sea_c", s["sea_c"])
	RenderingServer.global_shader_parameter_set("thermal_store", s["store"])
	RenderingServer.global_shader_parameter_set("thermal_cool", s["cool"])
	RenderingServer.global_shader_parameter_set("thermal_sun_gain", s["sun_gain"])
	RenderingServer.global_shader_parameter_set("thermal_sky_c", s["sky_c"])
	RenderingServer.global_shader_parameter_set("thermal_lamps", s["lamps"])


func _process(delta: float) -> void:
	if _users.is_empty():
		return
	_set_globals(_compute())
	_rescan_due -= delta
	if _rescan_due <= 0.0:
		_rescan_due = 2.0
		_attach()


# ------------------------------------------------------------------ heat by class

## Thermal contrast a detector gets from each class (0..1). Warm, compact
## things stand out.
const CONTRAST := {"tree": 0.1, "bush": 0.1, "house": 0.3, "warehouse": 0.35, "shed": 0.25,
	"car": 0.55, "pickup truck": 0.6, "truck": 0.65, "tank": 0.8, "person": 0.9, "animal": 0.85,
	"tent": 0.3, "boat": 0.5, "ship": 0.5}

## Surface parameters for a class: absorb, sun_c, mass, own_c (shaders/thermal.gdshaderinc),
## refl (how much cold sky a bare metal face shows), and a hot spot (own heat at
## a point: engine bay, exhaust, engine room), as [forward fraction, up, radius, C].
const HEAT := {
	"vehicle": {"absorb": 0.6, "sun_c": 22.0, "mass": 0.15, "own_c": 1.5, "refl": 0.15, "hot": [0.38, 0.6, 1.3, 22.0]},
	"tank": {"absorb": 0.75, "sun_c": 24.0, "mass": 0.5, "own_c": 3.0, "refl": 0.05, "hot": [-0.3, 1.4, 2.4, 32.0]},
	"person": {"absorb": 0.5, "sun_c": 6.0, "mass": 0.6, "own_c": 7.5, "refl": 0.0},
	"house": {"absorb": 0.35, "sun_c": 16.0, "mass": 0.65, "own_c": 1.0, "refl": 0.0},
	"tent": {"absorb": 0.6, "sun_c": 14.0, "mass": 0.05, "own_c": 1.5, "refl": 0.0},
	"boat": {"absorb": 0.55, "sun_c": 16.0, "mass": 0.45, "own_c": 0.5, "refl": 0.05, "hot": [-0.3, 0.8, 2.0, 14.0]},
	"steel": {"absorb": 0.5, "sun_c": 20.0, "mass": 0.2, "own_c": 0.0, "refl": 0.3},
	"aircraft": {"absorb": 0.5, "sun_c": 10.0, "mass": 0.1, "own_c": 9.0, "refl": 0.1},
	"traffic": {"absorb": 0.6, "sun_c": 20.0, "mass": 0.15, "own_c": 7.0, "refl": 0.15},
	"concrete": {"absorb": 0.5, "sun_c": 18.0, "mass": 0.7, "own_c": 0.0, "refl": 0.0},
	# Rooftop kit: AC units run warm (their condensers dump the rooms' heat),
	# water tanks hold their water's, cloth and awnings are light.
	"machinery": {"absorb": 0.45, "sun_c": 14.0, "mass": 0.2, "own_c": 9.0, "refl": 0.15},
	"water_tank": {"absorb": 0.6, "sun_c": 10.0, "mass": 0.95, "own_c": 0.5, "refl": 0.0},
	"cloth": {"absorb": 0.5, "sun_c": 12.0, "mass": 0.05, "own_c": 0.0, "refl": 0.0},
	"rock": {"absorb": 0.6, "sun_c": 20.0, "mass": 0.85, "own_c": 0.0, "refl": 0.0},
	# Palms, hedges and park trees transpire: a little below the air, day and night.
	"vegetation": {"absorb": 0.45, "sun_c": 5.0, "mass": 0.55, "own_c": -1.0, "refl": 0.0},
	# Shipping containers and anything else steel-boxed.
	"container": {"absorb": 0.6, "sun_c": 26.0, "mass": 0.12, "own_c": 0.0, "refl": 0.2},
	# Lamps: cool by day, hot when lit (th_lamp, thermal_lamps).
	"lamp": {"absorb": 0.3, "sun_c": 8.0, "mass": 0.1, "own_c": 0.0, "refl": 0.1, "lamp": 1.0},
	"default": {"absorb": 0.55, "sun_c": 18.0, "mass": 0.4, "own_c": 0.0, "refl": 0.05},
}

const PART_CLASS := {"ac_": "machinery", "tank_": "water_tank", "dish": "steel", "thin": "cloth",
	"canopy": "cloth", "pipe": "steel", "parapet": "concrete", "stair": "concrete", "room": "concrete",
	"balcony": "concrete", "yard": "concrete", "tile": "concrete", "door": "concrete",
	"lamp_heads": "lamp", "lamp_glare": "lamp", "mast_lamps": "lamp", "crane_lamps": "lamp",
	"pier_lamps": "lamp", "minaret_lights": "lamp", "lamp_poles": "steel", "mast_poles": "steel",
	"pier_poles": "steel",
	# Parks and palms.
	"park_lamp_heads": "lamp", "park_lamp_posts": "steel", "park_benches": "default",
	"park_": "vegetation", "hedges": "vegetation", "palm_": "vegetation", "containers_": "container"}

const LABEL_CLASS := {"pickup truck": "vehicle", "car": "vehicle", "truck": "vehicle", "tank": "tank",
	"person": "person", "house": "house", "shed": "house", "warehouse": "house", "tent": "tent"}


## Which HEAT entry a node draws with, and the node that defines its frame (for
## the hot spot): the nearest ancestor that says what it is.
func _classify(n: Node) -> Array:
	var own := str(n.name)
	# Node-name prefixes (PART_CLASS) say what a draw is: "ac_roof_3_4",
	# "tank_1_2", "lamp_heads", "park_crowns".
	for pre: String in PART_CLASS:
		if own.begins_with(pre):
			return [PART_CLASS[pre], null]
	var cur: Node = n
	while cur != null and cur != get_tree().root:
		if cur.has_meta("label"):
			return [str(LABEL_CLASS.get(str(cur.get_meta("label")), "default")), cur]
		var nm := str(cur.name)
		if nm.begins_with("fw_") or cur.has_meta("airframe"):
			return ["aircraft", cur]
		if nm.begins_with("traffic_"):
			return ["traffic", null]
		if nm.begins_with("boat_") or nm.begins_with("ship_"):
			return ["boat", cur]
		if nm.begins_with("deck_") or nm == "piles" or nm.begins_with("pier_"):
			return ["concrete", null]
		if nm.begins_with("breakwater_") or nm == "coastline":
			return ["rock", null]     # quay walls, copes, riprap, rock faces
		if nm in ["cranes", "hazards", "wires"] or nm.begins_with("crane"):
			return ["steel", null]
		cur = cur.get_parent()
	return ["default", null]


# ------------------------------------------------------------------ overlays

func _overlay_material() -> Shader:
	if _overlay_shader != null:
		return _overlay_shader
	var sh := Shader.new()
	sh.code = "shader_type spatial;\nrender_mode depth_draw_never, cull_back, specular_disabled, ambient_light_disabled;\n#include \"%s\"\n" % INC + """
instance uniform float th_absorb = 0.55;
instance uniform float th_sun_c = 18.0;
instance uniform float th_mass = 0.4;
instance uniform float th_own_c = 0.0;
instance uniform float th_refl = 0.0;
instance uniform vec4 th_hot = vec4(0.0, -10000.0, 0.0, 0.0);   // world centre, radius
instance uniform float th_hot_c = 0.0;
instance uniform float th_lamp = 0.0;    // a lamp: hot by the fraction lit (thermal_lamps)
varying vec3 v_world;
void vertex() {
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}
void fragment() {
    if (!thermal_pass(CAMERA_VISIBLE_LAYERS)) { discard; }
    vec2 th = th_surface(th_absorb, th_sun_c, th_mass, th_own_c);
    if (th_hot.w > 0.0) {
        float d = length(v_world - th_hot.xyz) / th_hot.w;
        th.y += th_hot_c * exp(-d * d * 2.0) / 120.0;
    }
    th.y += th_lamp * thermal_lamps * 45.0 / 120.0;
    // Bare metal reflects the sky it faces: a cold sky reads as cold metal.
    float up = clamp((INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).y, 0.0, 1.0);
    th.y = mix(th.y, th_enc(mix(thermal_air_c, thermal_sky_c, up)), th_refl);
    TH_OUT(th);
}
"""
	_overlay_shader = sh
	return sh


## Buildings drawn as box MultiMeshes with an orthophoto roof (a photo-draped
## city: "buildings" and "tanks"): roofs take in the sun as their photo says
## (dark roofs hotter); walls are heavy; tanks hold their liquid's heat below
## a fill line (seeded per tank) and follow the sun above it.
func _building_material(src: ShaderMaterial, tanks: bool) -> ShaderMaterial:
	if _building_shader == null:
		var sh := Shader.new()
		sh.code = "shader_type spatial;\nrender_mode depth_draw_never, cull_back, specular_disabled, ambient_light_disabled;\n#include \"%s\"\n" % INC + """
uniform sampler2D ortho : source_color, filter_linear_mipmap;
uniform float half_size = 1500.0;
uniform float tanks = 0.0;
varying vec3 v_world;
varying vec3 v_nrm;
varying vec3 v_centre;
varying float v_height;
varying vec4 v_custom;
void vertex() {
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    v_nrm = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
    v_centre = MODEL_MATRIX[3].xyz;
    v_height = length(MODEL_MATRIX[1].xyz);
    v_custom = INSTANCE_CUSTOM;
}
float h1(float x) { return fract(sin(x * 91.3458) * 47453.5453); }
void fragment() {
    if (!thermal_pass(CAMERA_VISIBLE_LAYERS)) { discard; }
    vec3 shift = vec3(v_custom.x, 0.0, -v_custom.y);
    vec2 uv = clamp((v_world.xz + shift.xz + vec2(half_size)) / (2.0 * half_size), vec2(0.0), vec2(0.9999));
    vec3 roof = textureLod(ortho, uv, 2.0).rgb;
    float lum = dot(roof, vec3(0.299, 0.587, 0.114));
    float seed = v_custom.z;
    vec2 th;
    if (v_nrm.y > 0.5) {
        // Roofs: dark tar and concrete hot, pale paint and sheet metal less so;
        // light roofs are light in mass too, and fall below the air at night.
        float absorb = clamp(1.05 - lum * 1.1, 0.2, 0.95);
        th = th_surface(absorb, 26.0, mix(0.25, 0.55, h1(seed * 3.3)), 0.6 * h1(seed * 7.1));
    } else if (tanks > 0.5) {
        float y = v_world.y - (v_centre.y - 0.5 * v_height);
        float fill = mix(0.2, 0.9, h1(seed * 5.7)) * v_height;
        bool liquid = y < fill;
        th = th_surface(0.35, 20.0, liquid ? 0.95 : 0.1, liquid ? 1.5 : 0.0);
    } else {
        th = th_surface(clamp(1.0 - lum, 0.25, 0.8), 18.0, 0.7, 0.8 + 1.5 * h1(seed * 11.3));
    }
    TH_OUT(th);
}
"""
		_building_shader = sh
	var m := ShaderMaterial.new()
	m.shader = _building_shader
	m.set_shader_parameter("ortho", src.get_shader_parameter("ortho"))
	m.set_shader_parameter("half_size", src.get_shader_parameter("half_size"))
	m.set_shader_parameter("tanks", 1.0 if tanks else 0.0)
	return m


static func _native(g: GeometryInstance3D) -> bool:
	var mats: Array = []
	if g.material_override != null:
		mats.append(g.material_override)
	if g is MeshInstance3D and (g as MeshInstance3D).mesh != null:
		var mi := g as MeshInstance3D
		for i in mi.mesh.get_surface_count():
			mats.append(mi.get_active_material(i))
	elif g is MultiMeshInstance3D and (g as MultiMeshInstance3D).multimesh != null \
			and (g as MultiMeshInstance3D).multimesh.mesh != null:
		var mm := (g as MultiMeshInstance3D).multimesh.mesh
		for i in mm.get_surface_count():
			mats.append(mm.surface_get_material(i))
	for m in mats:
		if m is ShaderMaterial and (m as ShaderMaterial).shader != null \
				and (m as ShaderMaterial).shader.code.contains("thermal_pass("):
			return true
	return false


static func _transparent(g: GeometryInstance3D) -> bool:
	var mats: Array = []
	if g.material_override != null:
		mats.append(g.material_override)
	if g is MeshInstance3D and (g as MeshInstance3D).mesh != null:
		for i in (g as MeshInstance3D).mesh.get_surface_count():
			mats.append((g as MeshInstance3D).get_active_material(i))
	for m in mats:
		# Blended (glass, glows, rotor discs) hides; a scissored or hashed cut-out
		# (palm fronds) is opaque where it draws and keeps its shape (_cut_pass).
		if m is BaseMaterial3D and (m as BaseMaterial3D).transparency in [BaseMaterial3D.TRANSPARENCY_ALPHA,
				BaseMaterial3D.TRANSPARENCY_ALPHA_DEPTH_PRE_PASS]:
			return true
		if m is ShaderMaterial and (m as ShaderMaterial).shader != null:
			var code := (m as ShaderMaterial).shader.code
			var scissor := code.contains("ALPHA_SCISSOR_THRESHOLD")
			if code.contains("blend_add") or code.contains("depth_draw_never") \
					or (not scissor and (code.contains("ALPHA =") or code.contains("ALPHA="))):
				return true
	return false


func _attach() -> void:
	var root := get_tree().root
	for n in root.find_children("*", "GeometryInstance3D", true, false):
		var g := n as GeometryInstance3D
		if g.has_meta("th_done"):
			continue
		g.set_meta("th_done", true)
		if g is Label3D or g is Sprite3D or g is GPUParticles3D or g is CPUParticles3D:
			continue
		if _native(g):
			continue
		if _transparent(g):
			_hidden.append([g, g.layers])
			g.layers = ONLY_LAYER_20
			continue
		var nm := str(g.name)
		if g is MultiMeshInstance3D and (nm == "buildings" or nm == "tanks") \
				and g.material_override is ShaderMaterial:
			_overlays.append([g, g.material_overlay])
			g.material_overlay = _building_material(g.material_override, nm == "tanks")
			continue
		var cls: Array = _classify(g)
		var heat: Dictionary = HEAT.get(cls[0], HEAT["default"])
		if _cut_pass(g, heat):
			continue
		var m := ShaderMaterial.new()
		m.shader = _overlay_material()
		_overlays.append([g, g.material_overlay])
		g.material_overlay = m
		g.set_instance_shader_parameter("th_absorb", heat["absorb"])
		g.set_instance_shader_parameter("th_sun_c", heat["sun_c"])
		g.set_instance_shader_parameter("th_mass", heat["mass"])
		g.set_instance_shader_parameter("th_own_c", heat["own_c"])
		g.set_instance_shader_parameter("th_refl", heat["refl"])
		g.set_instance_shader_parameter("th_lamp", float(heat.get("lamp", 0.0)))
		if heat.has("hot") and cls[1] is Node3D:
			var frame := cls[1] as Node3D
			var hot: Array = heat["hot"]
			var size := _extent(frame)
			# The frame's length runs along its +X (layout props) or +Z (models).
			var fwd := frame.global_transform.basis.x.normalized() if frame.has_meta("label") \
				else frame.global_transform.basis.z.normalized()
			var c := frame.global_position + fwd * float(hot[0]) * size + Vector3.UP * float(hot[1])
			g.set_instance_shader_parameter("th_hot", Vector4(c.x, c.y, c.z, float(hot[2])))
			g.set_instance_shader_parameter("th_hot_c", float(hot[3]))


## A thing's length (m), for placing its hot spot.
func _extent(n: Node3D) -> float:
	var best := 4.0
	for c in n.find_children("*", "CollisionShape3D", true, false):
		var s = (c as CollisionShape3D).shape
		if s is BoxShape3D:
			best = maxf((s as BoxShape3D).size.x, (s as BoxShape3D).size.z)
			break
	return best


## A mesh with cut-out surfaces (alpha scissor or hash: palm fronds) can't take
## an overlay, which would fill the cards in solid. Each surface material gets a
## thermal next pass instead, cut by its own albedo texture's alpha. The
## materials are shared by every copy of the model, so this is done once per
## material and undone in _detach().
var _cut_shader: Shader = null
var _cut_done: Dictionary = {}           # material instance id -> [material, previous next_pass]

func _cut_pass(g: GeometryInstance3D, heat: Dictionary) -> bool:
	var mats: Array = []
	if g is MeshInstance3D and (g as MeshInstance3D).mesh != null:
		var mi := g as MeshInstance3D
		for i in mi.mesh.get_surface_count():
			mats.append(mi.get_active_material(i))
	elif g is MultiMeshInstance3D and (g as MultiMeshInstance3D).multimesh != null \
			and (g as MultiMeshInstance3D).multimesh.mesh != null:
		var mm := (g as MultiMeshInstance3D).multimesh.mesh
		for i in mm.get_surface_count():
			mats.append(mm.surface_get_material(i))
	var cut := false
	for m in mats:
		if m is BaseMaterial3D and (m as BaseMaterial3D).transparency in [BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR,
				BaseMaterial3D.TRANSPARENCY_ALPHA_HASH]:
			cut = true
	if not cut or g.material_override != null:
		return false
	if _cut_shader == null:
		_cut_shader = Shader.new()
		_cut_shader.code = "shader_type spatial;\nrender_mode cull_disabled, specular_disabled, ambient_light_disabled;\n#include \"%s\"\n" % INC + """
uniform sampler2D cut_tex : source_color, filter_linear_mipmap;
uniform float cut = 0.0;              // 1: discard where the texture's alpha is below `cut_at`
uniform float cut_at = 0.5;
uniform vec4 heat = vec4(0.45, 5.0, 0.55, -1.0);   // absorb, sun_c, mass, own_c
void fragment() {
    if (!thermal_pass(CAMERA_VISIBLE_LAYERS)) { discard; }
    if (cut > 0.5 && texture(cut_tex, UV).a < cut_at) { discard; }
    vec2 th = th_surface(heat.x, heat.y, heat.z, heat.w);
    TH_OUT(th);
}
"""
	for m in mats:
		if not (m is BaseMaterial3D):
			continue
		var bm := m as BaseMaterial3D
		var key := bm.get_instance_id()
		if _cut_done.has(key):
			continue
		var tp := ShaderMaterial.new()
		tp.shader = _cut_shader
		var is_cut := bm.transparency in [BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR, BaseMaterial3D.TRANSPARENCY_ALPHA_HASH]
		tp.set_shader_parameter("cut", 1.0 if is_cut and bm.albedo_texture != null else 0.0)
		tp.set_shader_parameter("cut_tex", bm.albedo_texture)
		tp.set_shader_parameter("cut_at", bm.alpha_scissor_threshold if bm.transparency == BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR else 0.5)
		tp.set_shader_parameter("heat", Vector4(float(heat["absorb"]), float(heat["sun_c"]), float(heat["mass"]), float(heat["own_c"])))
		_cut_done[key] = [bm, bm.next_pass]
		bm.next_pass = tp
	return true


func _detach() -> void:
	for k in _cut_done:
		var e: Array = _cut_done[k]
		if is_instance_valid(e[0]):
			(e[0] as BaseMaterial3D).next_pass = e[1]
	_cut_done.clear()
	for e in _overlays:
		var g = e[0]
		if is_instance_valid(g):
			(g as GeometryInstance3D).material_overlay = e[1]
			(g as GeometryInstance3D).remove_meta("th_done")
	_overlays.clear()
	for e in _hidden:
		var g = e[0]
		if is_instance_valid(g):
			(g as GeometryInstance3D).layers = int(e[1])
			(g as GeometryInstance3D).remove_meta("th_done")
	_hidden.clear()
	for n in get_tree().root.find_children("*", "GeometryInstance3D", true, false):
		if n.has_meta("th_done"):
			n.remove_meta("th_done")


# ------------------------------------------------------------------ detection

## How well a thermal camera sees `label` now (0..1): the class's contrast,
## a little weaker around the thermal crossovers (after sunrise and sunset, when
## the ground passes through the targets' temperature).
func contrast(label: String) -> float:
	var c := float(CONTRAST.get(label, 0.3))
	var h := float(state.get("hour", 12.0))
	var cross := exp(-pow((h - 8.0) / 1.2, 2.0)) + exp(-pow((h - 19.0) / 1.2, 2.0))
	return clampf(c * (1.0 - 0.35 * cross), 0.05, 1.0)


## 0 by day .. 1 at night (for the EO band's detection range).
func night() -> float:
	return 1.0 - smoothstep(-6.0, 4.0, float(_compute()["sun_elevation_deg"]))
