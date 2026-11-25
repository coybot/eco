## Manhattan, photoreal variant — same city, textured facades and a real
## orthophoto on the ground.
##
## Subclasses env_manhattan and overrides exactly two methods, `_render_city`
## and `_add_city_ground`. Everything that matters for the sim — the NYC Open
## Data footprints, the tile index, the analytic slab test, the occupancy grid,
## structure_detections — is inherited untouched, so this env and the plain one
## are the SAME WORLD to routing, occlusion and collision. Only the pixels
## differ. That is deliberate: the box representation is what makes occlusion
## exact and free at 45k buildings, and photorealism is not worth giving it up.
##
## WHY FACADES RATHER THAN FOG OR PHOTOGRAMMETRY MESH
##
## env_manhattan records, at length, that exponential fog was tried here to give
## a monocular camera some depth cue and was removed: it did not move the VLM's
## score (8/18 -> 9/18 against a 6/18 chance floor) and it destroyed the wide
## shots. Its closing suggestion was "texture the facades instead; that adds
## gradient at every range rather than trading near against far". This is that.
##
## The windows are generated in a shader from METRIC coordinates, not from a
## tiled image. A unit cube scaled to a 20 x 300 m tower and one scaled to a
## 60 x 15 m block would stretch any fixed UV differently, so a storey would be
## a different size on every building in the city. Deriving the UV from the
## instance's own scale keeps a floor 3.6 m everywhere, which is the entire
## point: the apparent size of a window IS the depth cue.
extends "res://scripts/env_manhattan.gd"

## Optional. Drop a NAIP/DoITT orthophoto here and the ground uses it; without
## it the parent's flat land/water quads are used instead, which is why this
## env still works on a fresh checkout with no imagery fetched.
const ORTHO_PATH := "res://assets/manhattan/ortho.jpg"

const FACADE_DIR := "res://assets/facades/"
const FacadeKit = preload("res://scripts/facade_kit.gd")
const LightingKit = preload("res://scripts/lighting_kit.gd")
const SceneLook = preload("res://scripts/scene_look.gd")

## Photographs, not a procedural window grid.
##
## An earlier version of this drew windows arithmetically — a step function on
## fract(metres / bay). It gave a correct grid and still looked synthetic,
## because what makes a building read as real is everything a grid has none of:
## grime, uneven reflection between panes, floors that are not identical. Those
## are in a photograph and cannot be reasoned out.
##
## Each building picks one of eight CC0 facades by a hash of its own position,
## so the city is varied but a given building never changes. UVs are METRIC:
## `spans` is how many metres tall one tile of each photograph is, so a storey
## comes out the same height on a 20 m block and a 380 m tower. Without that,
## a unit cube scaled to a tower stretches one photo over 380 m and every
## window becomes a storey high.
const FACADE_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;

uniform sampler2DArray facades : source_color, filter_linear_mipmap_anisotropic;
uniform sampler2D roof_tex : source_color, filter_linear_mipmap_anisotropic;
uniform float spans[8];
uniform float layers = 8.0;
uniform float roof_span = 12.0;
uniform float facade_gain = 1.0;

varying vec3 v_metres;
varying vec3 v_nrm;
varying float v_layer;
varying float v_span;
varying vec3 v_world;

float hash1(float n) { return fract(sin(n) * 43758.5453123); }

void vertex() {
    vec3 s = vec3(length(MODEL_MATRIX[0].xyz),
                  length(MODEL_MATRIX[1].xyz),
                  length(MODEL_MATRIX[2].xyz));
    v_metres = VERTEX * s;
    v_nrm = NORMAL;
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    float seed = hash1(dot(MODEL_MATRIX[3].xyz, vec3(0.031, 0.017, 0.043)));
    v_layer = floor(seed * layers);
    v_span = spans[int(v_layer)];
}

void fragment() {
    if (abs(v_nrm.y) > 0.5) {
        // Roofs, which is most of what a drone sees. Mapped in WORLD space so
        // neighbouring buildings do not all share an identical roof origin.
        vec2 uv = v_world.xz / roof_span;
        // 0.45, not 0.85. The CC0 asphalt averages mid-grey, and mid-grey under
        // a full directional sun tonemaps to near-white — the first render had
        // a city of white rooftops. Real NYC roofs are dark tar, gravel and
        // plant, and roofs are most of what a drone ever sees.
        ALBEDO = texture(roof_tex, uv).rgb * 0.45;
        ROUGHNESS = 0.95;
    } else {
        // Whichever horizontal axis runs along this face.
        float across = abs(v_nrm.x) > 0.5 ? v_metres.z : v_metres.x;
        vec2 uv = vec2(across, v_metres.y) / v_span;
        vec3 c = texture(facades, vec3(uv, v_layer)).rgb * facade_gain;
        ALBEDO = c;
        // Glass is darker and shinier than masonry; using the sampled
        // brightness to drive roughness gets specular on the windows without
        // needing a separate map per facade.
        float gloss = 1.0 - clamp(dot(c, vec3(0.33)), 0.0, 1.0);
        ROUGHNESS = mix(0.85, 0.25, gloss);
        METALLIC = gloss * 0.35;
    }
}
"""


## ---------------------------------------------------------------- scene API
## What the scenery scripts (children of this env) may read. Kept in one place
## so every one of them agrees on the frame and the levels.
##
## FRAME. Baked scenery data (assets/manhattan/*.json) is in the DATASET frame
## of buildings.json: metres east/north of the Battery Park datum, turned
## GRID_ROTATION_DEG onto the street grid. The loaded slice is re-centred by
## `_offset` (see env_manhattan._load_city), so a data point p is at
## `p - data_offset()` in the sim, and Godot (e, up, -n) from there.
## Use to_sim() / geo_to_sim() rather than doing this by hand.
##
## LEVELS. Street level is y = 0 (buildings stand on it, altitudes are above
## it). The rivers are SEA_Y below: Manhattan's bulkheads stand about that
## far above the water.
const region := "manhattan"
const GEO_DATUM := [40.7005, -74.0170]        # lat, lon of the dataset origin
const GRID_ROTATION_DEG := 29.0
const M_PER_DEG_LAT := 111132.0
const LAND_Y := 0.0
const SEA_Y := -1.6

## Half the larger side of the loaded slice, for scripts that only need "how
## big is the scene".
var _half := 1000.0


func data_offset() -> Vector2:
	return _offset


## Dataset-frame (e, n) -> sim (e, n).
func to_sim(p: Vector2) -> Vector2:
	return p - _offset


## Sim (e, n) -> Godot position at height y above street level.
func godot_pos(p: Vector2, y: float = 0.0) -> Vector3:
	return Vector3(p.x, y, -p.y)


## lat/lon -> sim (e, n). Same equirectangular projection and rotation as
## tools/fetch_manhattan.py, so baked footprints and anything placed from
## lat/lon agree to well under a metre.
func geo_to_sim(lat: float, lon: float) -> Vector2:
	var m_lon := 111320.0 * cos(deg_to_rad(float(GEO_DATUM[0])))
	var x := (lon - float(GEO_DATUM[1])) * m_lon
	var y := (lat - float(GEO_DATUM[0])) * M_PER_DEG_LAT
	var t := deg_to_rad(GRID_ROTATION_DEG)
	return Vector2(x * cos(t) - y * sin(t), x * sin(t) + y * cos(t)) - _offset


## Sim (e, n) -> [lat, lon].
func sim_to_geo(p: Vector2) -> Array:
	var q := p + _offset
	var t := deg_to_rad(GRID_ROTATION_DEG)
	var x := q.x * cos(t) + q.y * sin(t)
	var y := -q.x * sin(t) + q.y * cos(t)
	var m_lon := 111320.0 * cos(deg_to_rad(float(GEO_DATUM[0])))
	return [float(GEO_DATUM[0]) + y / M_PER_DEG_LAT, float(GEO_DATUM[1]) + x / m_lon]


## Extent of the loaded slice in sim (e, n).
func sim_bounds() -> Rect2:
	return Rect2(_origin, Vector2(_grid_w, _grid_h) * OCC_RES)


## Land/water mask (assets/manhattan/water.png, tools/fetch_manhattan_water.py):
## 255 = water, row 0 the north edge, dataset frame. Null on a checkout
## without it, and then everything is land, as before.
const WATER_PATH := "res://assets/manhattan/water.png"
var _water_img: Image = null
var _water_b := PackedFloat32Array([0, 0, 1, 1])   # e0, n0, e1, n1 (dataset frame)
var _water_res := 4.0


func _load_water() -> void:
	if not FileAccess.file_exists(WATER_PATH):
		return
	var jf := FileAccess.open("res://assets/manhattan/water.json", FileAccess.READ)
	if jf == null:
		return
	var meta: Dictionary = JSON.parse_string(jf.get_as_text())
	jf.close()
	var img := Image.load_from_file(WATER_PATH)
	if img == null:
		return
	img.convert(Image.FORMAT_L8)
	_water_img = img
	var b: Array = meta["bounds_enu"]
	_water_b = PackedFloat32Array([b[0], b[1], b[2], b[3]])
	_water_res = float(meta["res_m"])


## The mask as a texture plus where it lies in the sim, for shaders:
## {"tex": ImageTexture, "rect": Rect2 (sim e, n of the image's SW corner and
## size)}. Empty without a mask.
func water_mask() -> Dictionary:
	if _water_img == null:
		return {}
	var sw := to_sim(Vector2(_water_b[0], _water_b[1]))
	return {"tex": ImageTexture.create_from_image(_water_img),
		"rect": Rect2(sw, Vector2(_water_b[2] - _water_b[0], _water_b[3] - _water_b[1]))}


## The shoreline the ground is drawn with (manhattan_shore.gd: a signed-
## distance field baked from the mask, piers carved out of the land), loaded
## on first use, once the slice's offset is known. Null without shore.png.
var _shore: RefCounted = null
var _shore_tried := false


func shore() -> RefCounted:
	if not _shore_tried and not _rects.is_empty():
		_shore_tried = true
		var s: RefCounted = preload("res://scripts/manhattan_shore.gd").load_for(_offset)
		if bool(s.get("ok")):
			_shore = s
	return _shore


## Water or land at sim (e, n). With the shoreline field: exactly the line the
## ground is cut at and the bulkheads stand on, and a pier deck is not water.
## Without it, from the mask (nearest pixel). Outside the mask: water, since
## past the far shores is the open harbour and the sound.
func is_water(e: float, n: float) -> bool:
	var s := shore()
	if s != null:
		return bool(s.call("is_water", e, n))
	if _water_img == null:
		return false
	var d := Vector2(e, n) + _offset
	var x := int((d.x - _water_b[0]) / _water_res)
	var y := int((_water_b[3] - d.y) / _water_res)
	if x < 0 or y < 0 or x >= _water_img.get_width() or y >= _water_img.get_height():
		return true
	return _water_img.get_pixel(x, y).r > 0.5


## Surface height at sim (e, n): street level on land, the river on water.
func ground_height(e: float, n: float) -> float:
	return SEA_Y if is_water(e, n) else LAND_Y


## 0 by day .. 1 at night: world_clock.gd's (fw_set_time), else day.
func night_amount() -> float:
	var wc := get_node_or_null("world_clock")
	if wc != null and wc.has_method("night_amount"):
		return float(wc.call("night_amount"))
	return 0.0


func _ready() -> void:
	_load_water()
	super()
	var b := sim_bounds()
	_half = maxf(b.size.x, b.size.y) * 0.5
	# After super(), so SceneKit's sun and WorldEnvironment already exist.
	# Manhattan is 21 km end to end; 8 km of shadow reach covers the part of
	# it any one shot actually contains.
	LightingKit.for_large_env(self, 8000.0)
	# Sky, sun, post and the air (--look=beauty, the default; --look=photo is
	# the look above unchanged), then the clock that moves them, then the
	# city's lights, which follow the clock. In that order: the clock's
	# "photo" sun is the look's.
	SceneLook.setup(self, region)
	add_child(preload("res://scripts/world_clock.gd").new())
	add_child(preload("res://scripts/night_lights.gd").new())
	# What surrounds the island: the far shores' skylines and the bridges.
	add_child(preload("res://scripts/far_shore.gd").new())
	add_child(preload("res://scripts/bridges.gd").new())
	# The target and decoy as real vehicles (same props, same bodies), and the
	# moving world: traffic, parked cars, people, ships (off by default).
	preload("res://scripts/life_props.gd").dress(self)
	add_child(preload("res://scripts/world_life.gd").new())
	add_child(preload("res://scripts/nature.gd").new())   # trees, park lamps and benches
	print("[env_manhattan_photo] facade shader active; ortho %s" %
		("loaded" if FileAccess.file_exists(ORTHO_PATH) else "absent (flat ground)"))


## Same geometry and same instancing as the parent — only the material differs.
## Kept as a full override rather than a post-hoc material swap because the
## parent bakes its grey tint into per-instance colours, and those would
## multiply against the facade shader's output.
func _render_city() -> void:
	# The city's own styles, roofs and rooftop kit (scripts/manhattan_buildings.gd),
	# when its baked window cells are there; else the CC0 photo facades below.
	var mb: Node3D = preload("res://scripts/manhattan_buildings.gd").new()
	mb.name = "manhattan_buildings"
	add_child(mb)
	if mb.call("build", self):
		return
	mb.queue_free()
	var mat := _facade_material()
	if mat == null:
		push_warning("[env_manhattan_photo] no facade textures — run "
			+ "drone/sim/tools/fetch_facades.py; falling back to plain city")
		super()
		return

	var box := BoxMesh.new()
	box.size = Vector3.ONE
	box.material = mat

	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = box
	mm.instance_count = _rects.size()
	for i in _rects.size():
		var r: Rect2 = _rects[i]
		var h: float = _heights[i]
		var cx := r.position.x + r.size.x * 0.5
		var cy := r.position.y + r.size.y * 0.5
		var basis := Basis.IDENTITY.scaled(Vector3(r.size.x, h, r.size.y))
		mm.set_instance_transform(i, Transform3D(basis, Vector3(cx, h * 0.5, -cy)))

	var mmi := MultiMeshInstance3D.new()
	mmi.multimesh = mm
	mmi.name = "city"
	add_child(mmi)


## Build the facade material from the shared CC0 set.
## Returns null when there are none, so a fresh checkout still renders.
func _facade_material() -> ShaderMaterial:
	var kit := FacadeKit.load_set()
	if kit.is_empty():
		return null
	var sh := Shader.new()
	sh.code = FACADE_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = sh
	mat.set_shader_parameter("facades", kit["array"])
	mat.set_shader_parameter("spans", kit["spans"])
	mat.set_shader_parameter("layers", float(kit["count"]))
	if kit["roof"] != null:
		mat.set_shader_parameter("roof_tex", kit["roof"])
		mat.set_shader_parameter("roof_span", float(kit["roof_span"]))
	print("[env_manhattan_photo] %d CC0 facades loaded" % kit["count"])
	return mat


## Orthophoto ground when one has been baked, else the parent's flat quads.
##
## Placement comes from ortho.json's bounds, NOT from the loaded buildings:
## the image was resampled to cover the whole dataset, while --env-region may
## have clipped the city to a slice. Sizing the plane to the slice would
## stretch the whole island's photo across a few blocks.
##
## The parent re-centres its slice by subtracting `_offset` from every rect,
## so the raw ENU bounds recorded by the tool have to have the same offset
## applied or the ground sits kilometres from the buildings standing on it.
func _add_city_ground() -> void:
	# With the shoreline field, manhattan_ground.gd draws the ground, the
	# rivers, bulkheads, piers and street markings, and the parent's stacked
	# water/land/park quads are not built at all (they sat 0.1-1.5 m above
	# street level and would fight it).
	if shore() != null:
		add_child(preload("res://scripts/manhattan_ground.gd").new())
		return
	super()   # water plane + correctly-sized land underneath, always
	# FileAccess, not ResourceLoader: the ortho is read with
	# Image.load_from_file and may never have been imported as a resource,
	# in which case ResourceLoader.exists() is false for a file that is right
	# there on disk.
	if not FileAccess.file_exists(ORTHO_PATH):
		return
	var jf := FileAccess.open("res://assets/manhattan/ortho.json", FileAccess.READ)
	if jf == null:
		return
	var meta: Dictionary = JSON.parse_string(jf.get_as_text())
	jf.close()
	var b: Array = meta.get("bounds_enu", [])
	if b.size() != 4:
		return

	var img := Image.load_from_file(ORTHO_PATH)
	if img == null:
		return
	img.generate_mipmaps()

	var e0 := float(b[0]); var n0 := float(b[1])
	var e1 := float(b[2]); var n1 := float(b[3])
	var mesh := PlaneMesh.new()
	mesh.size = Vector2(e1 - e0, n1 - n0)
	var mi := MeshInstance3D.new()
	mi.name = "ortho_ground"
	mi.mesh = mesh
	# Above ALL of the parent's ground quads, not just the land one. The parent
	# stacks water at 0.1, land at 0.6 and Central Park at 1.1; sitting at 0.75
	# put the photo under the park, so the real park was hidden behind a flat
	# green rectangle. 1.5 clears the lot and is still far below any building.
	mi.position = Vector3((e0 + e1) * 0.5 - _offset.x, 1.5,
						  -((n0 + n1) * 0.5 - _offset.y))

	var mat := StandardMaterial3D.new()
	mat.albedo_texture = ImageTexture.create_from_image(img)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	# LIT, not unshaded — with a darkened albedo to compensate.
	#
	# Unshaded was the first fix here, to stop the scene sun double-lighting a
	# photograph that already contains one. It worked and cost more than it
	# bought: an unshaded material does not RECEIVE shadows, so the city cast
	# nothing onto its own ground, which is most of what makes an aerial shot
	# read as real. Scaling albedo down instead lands at about the same
	# brightness under sun+ambient while keeping the surface in the shadow
	# pass.
	mat.albedo_color = Color(0.55, 0.55, 0.55)
	mat.roughness = 1.0
	mat.metallic = 0.0
	mi.material_override = mat
	add_child(mi)
	print("[env_manhattan_photo] ortho ground %.0f x %.0f m" % [e1 - e0, n1 - n0])


## Extent of the buildings actually loaded, which is the slice the ortho should
## cover. Computed from the rects rather than assumed, because --env-region
## clips the city and the parent already sizes its ground the same way.
func _slice_bounds() -> Rect2:
	if _rects.is_empty():
		return Rect2(0, 0, 1000, 1000)
	var r0: Rect2 = _rects[0]
	var min_e := r0.position.x
	var min_n := r0.position.y
	var max_e := r0.position.x + r0.size.x
	var max_n := r0.position.y + r0.size.y
	for r in _rects:
		min_e = minf(min_e, r.position.x)
		min_n = minf(min_n, r.position.y)
		max_e = maxf(max_e, r.position.x + r.size.x)
		max_n = maxf(max_n, r.position.y + r.size.y)
	return Rect2(min_e, min_n, max_e - min_e, max_n - min_n)

## NOT DONE: tiling a texture over the parent's land plane.
##
## Tried, and it was worse. The parent paints land one flat colour, which from
## altitude reads as dark voids between the buildings, so the idea was to tile
## the roof asphalt over it. At the uv1_scale needed to cover an 11 x 22 km
## island the mips average the photo down to a flat dark grey, so the ground
## went from dark-with-a-green-Central-Park to uniformly black — it removed
## information rather than adding it.
##
## The fix for the ground is a real orthophoto (ORTHO_PATH above), not a tiled
## material. Nothing else will put Manhattan's actual streets and lots there.
