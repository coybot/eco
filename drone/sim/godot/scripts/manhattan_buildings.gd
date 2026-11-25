## Manhattan's 45k buildings drawn as the city's own: brownstones, walk-ups
## with fire escapes, pre-war masonry, post-war white brick, glass towers, and
## what stands on their roofs. A child of env_manhattan_photo, which calls
## build() from its _render_city.
##
## THE BOXES ARE NOT TOUCHED. Every building is still the parent's axis-aligned
## box (env._rects / env._heights, one MultiMesh named "city"): that is what
## routing, occlusion and strikes are answered against. Everything here is
## either a material on those boxes or scenery on top of them (parapets,
## bulkheads, water towers, plant, crowns, spires), which has no collider: it
## is a few metres tall, and making the sim fly against it would change what a
## route through the analytic model means.
##
## WHAT A BUILDING IS. assets/manhattan/building_attrs.json
## (tools/fetch_manhattan_building_attrs.py: NYC Open Data footprints'
## construction year, PLUTO's building class, floors and retail area, and which
## faces stand against a neighbour) picks a style per building. A building it
## does not cover falls back to a style from its height and footprint.
##
## WINDOWS. Baked cells (assets/facade_cells_nyc, tools/
## bake_manhattan_facade_cells.py), one bay by one storey each, laid out in
## METRES from the box's own scale, and sampled mipmapped with the gradient of
## the continuous cell coordinate. Past the distance where a cell is under a
## pixel or so, each wall blends to its style's average cell: per-window
## choices (which window has its blind down, which is lit) are hashed per cell,
## and once cells are sub-pixel those choices switch from pixel to pixel and
## crawl. Both of those were tried the other way first: the CC0 photo facades
## this replaces (FACADE_SHADER) sparkled with bright specks that read as lit
## windows in daylight, and a step()-drawn grid shimmered past ~300 m.
##
## Party walls (a face against a neighbour) are blank brick: lot-line walls in
## New York carry no windows, so the bare side wall a walk-up shows above a
## lower neighbour is what it really looks like.
##
## NIGHT. A share of windows per building and per floor lights (offices more in
## the early evening, homes later), from env.night_amount(), which is 0 by day:
## the day scene has no emission at all. `--city-night=0..1` forces it, and dims
## the sun and sky to match, for checking the windows without the moving world.
##
## THERMAL. The building and kit shaders answer the thermal band themselves
## (shaders/thermal.gdshaderinc, thermal_pass): roofs by their membrane (tar
## hot, white membrane cool, green roofs at the air), glass reflecting the cold
## sky, masonry heavy. thermal_kit.gd skips anything native, so nothing there
## needs to know about these draws.
extends Node3D

const CELLS_DIR := "res://assets/facade_cells_nyc/"
const ATTRS_PATH := "res://assets/manhattan/building_attrs.json"
const INC := "res://shaders/thermal.gdshaderinc"

enum { BROWNSTONE, WALKUP, PREWAR, POSTWAR, GLASS, MODERN, LOFT, CIVIC, UTILITY }
enum { R_TAR, R_SILVER, R_WHITE, R_GRAVEL, R_GREEN, R_PAVERS }

## Instance flags (INSTANCE_CUSTOM.w, an integer in a float). Party-wall bits
## are building_attrs' (1 east, 2 west, 4 north, 8 south) times 16.
const F_SHOPS := 1
const F_AWNINGS := 2
const F_FIRE := 4
const F_CORNICE := 8
const F_TINT := 256          # x0..7: curtain-wall glass tint
const F_TRIM := 2048         # x0..15: trim colour (TRIM[] in the shader)
const F_UPPER := 32768       # no ground floor (a crown or setback on a roof)
const F_OFFICE := 65536
const F_DEEP := 131072       # 1955-80 curtain wall: deep opaque spandrels

## Rooftop kit parts: mesh, kind (the kit shader's), chunk size (m), draw range
## (m), shadows. Thermal_kit's name classes are kept in the node names.
const PARTS := {
	"bulkhead": ["box", 0, 1000.0, 1800.0, true],
	"tank_wood": ["tank", 1, 1000.0, 1600.0, true],
	"tank_legs": ["legs", 3, 800.0, 900.0, false],
	"ac_units": ["box", 4, 600.0, 650.0, false],
	"ac_cooling": ["box", 6, 1000.0, 1400.0, false],
	"ac_fans": ["fan", 3, 600.0, 650.0, false],
	"skylight": ["box", 5, 600.0, 450.0, false],
	"mast": ["thin", 3, 800.0, 900.0, false],
	"spire": ["thin", 8, 4000.0, 0.0, true],
	"crown_steel": ["crown", 7, 4000.0, 0.0, true],
	"crown_stone": ["box", 9, 4000.0, 0.0, true],
}

## Rooftop parts cast shadows only within this distance (m) of the camera.
const SHADOW_M := 700.0

var env: Node3D
var city_mat: ShaderMaterial
var kit_mat: ShaderMaterial
var parapet_mat: ShaderMaterial
var _night_force := -1.0
var _night_last := -1.0
var counts := {}


# ------------------------------------------------------------------ entry

## Build the city draw (named "city", a child of env as before), the parapets,
## the rooftop kit and the icons' tops. False if the cells are missing, and then
## the caller draws its old facades.
func build(e: Node3D) -> bool:
	env = e
	var t0 := Time.get_ticks_msec()
	var cells := _load_cells()
	if cells.is_empty():
		return false
	city_mat = _make_material(BUILDING_SHADER)
	city_mat.set_shader_parameter("cells_mask", cells["mask"])
	city_mat.set_shader_parameter("cells_col", cells["colour"])
	kit_mat = _make_material(KIT_SHADER)
	parapet_mat = _make_material(PARAPET_SHADER)
	var attrs := _load_attrs()
	var rects: Array[Rect2] = env.get("_rects")
	var heights: PackedFloat32Array = env.get("_heights")
	var off: Vector2 = env.get("_offset")

	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_colors = true
	mm.use_custom_data = true
	var box := BoxMesh.new()
	box.size = Vector3.ONE
	mm.mesh = box
	mm.instance_count = rects.size()
	var acc := {}           # part name -> {Vector2i chunk: [xforms, colours, customs]}
	var crowns: Array = []  # [Transform3D, Color, Color] drawn with the building material
	var parapets := {}      # Vector2i chunk -> [xforms, colours, customs]
	var rng := RandomNumberGenerator.new()
	var matched := 0
	for i in rects.size():
		var r: Rect2 = rects[i]
		var h: float = heights[i]
		var d := r.position + off
		var k := Vector2i(roundi(d.x * 20.0 + r.size.x * 10.0), roundi(d.y * 20.0 + r.size.y * 10.0))
		var a: PackedInt32Array = attrs.get(k, PackedInt32Array())
		if not a.is_empty():
			matched += 1
		rng.seed = hash(k)
		var info := _describe(r, h, a, rng)
		var c := r.get_center()
		mm.set_instance_transform(i, Transform3D(Basis.IDENTITY.scaled(Vector3(r.size.x, h, r.size.y)),
			Vector3(c.x, h * 0.5, -c.y)))
		mm.set_instance_color(i, info["wall"])
		mm.set_instance_custom_data(i, info["custom"])
		_parapet(r, h, info, rng, parapets)
		_roof_kit(r, h, info, rng, acc, crowns)
	var mmi := MultiMeshInstance3D.new()
	mmi.name = "city"
	mmi.multimesh = mm
	mmi.material_override = city_mat
	env.add_child(mmi)
	_icons(rects, heights, acc, crowns)
	_build_parapets(parapets)
	_build_parts(acc)
	_build_crowns(crowns)
	_night_force = float(IpcServer._get_launch_arg("--city-night", "-1"))
	if _night_force >= 0.0:
		_preview_night(_night_force)
	print("[manhattan_buildings] %d buildings (%d with attrs), kit %s, %d ms" %
		[rects.size(), matched, counts, Time.get_ticks_msec() - t0])
	return true


func _process(_delta: float) -> void:
	if city_mat == null:
		return
	var n := _night_force if _night_force >= 0.0 else float(env.call("night_amount"))
	if absf(n - _night_last) < 0.002:
		return
	_night_last = n
	city_mat.set_shader_parameter("night", n)
	kit_mat.set_shader_parameter("night", n)


## For --city-night only: the sun off and a dark sky, so lit windows can be
## judged. Deferred, because the env retunes its light after the city is built.
## The real night look is the moving world's (sun, sky, street lights).
func _preview_night(n: float) -> void:
	await get_tree().process_frame
	# Every light and environment in the tree: the main scene may hold the
	# active WorldEnvironment rather than the env.
	for ch in get_tree().root.find_children("*", "", true, false):
		if ch is DirectionalLight3D:
			(ch as DirectionalLight3D).light_energy *= (1.0 - 0.97 * n)
		elif ch is WorldEnvironment and (ch as WorldEnvironment).environment != null:
			var we := (ch as WorldEnvironment).environment
			we.background_mode = Environment.BG_COLOR
			we.background_color = Color(0.02, 0.025, 0.045).lerp(we.background_color, 1.0 - n)
			we.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
			we.ambient_light_color = Color(0.09, 0.10, 0.14)
			we.ambient_light_energy = 1.0 - 0.8 * n
			we.reflected_light_source = Environment.REFLECTION_SOURCE_DISABLED
			we.fog_enabled = false


# ------------------------------------------------------------------ data

static var _cells_cache: Dictionary = {}


## The cells as two mipmapped Texture2DArrays (layer k is rows k*px.. of a
## vertical strip). create_from_images wants every layer the same size and
## format with its own mipmaps.
func _load_cells() -> Dictionary:
	if not _cells_cache.is_empty():
		return _cells_cache
	var f := FileAccess.open(CELLS_DIR + "cells.json", FileAccess.READ)
	if f == null:
		push_warning("[manhattan_buildings] no %scells.json: run drone/sim/tools/bake_manhattan_facade_cells.py" % CELLS_DIR)
		return {}
	var doc: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var px := int(doc.get("px", 256))
	var n: int = (doc.get("layers", []) as Array).size()
	var out := {}
	for key: String in ["mask", "colour"]:
		var strip := Image.load_from_file(CELLS_DIR + key + ".png")
		if strip == null or strip.get_height() < px * n:
			push_error("[manhattan_buildings] %s%s.png missing or short" % [CELLS_DIR, key])
			return {}
		strip.convert(Image.FORMAT_RGBA8)
		var layers: Array[Image] = []
		for k in n:
			var layer := strip.get_region(Rect2i(0, k * px, px, px))
			layer.generate_mipmaps()
			layers.append(layer)
		var arr := Texture2DArray.new()
		arr.create_from_images(layers)
		out[key] = arr
	_cells_cache = out
	return out


## building_attrs.json as {Vector2i key: [year, floors, cls, retail, office, res, party]}.
func _load_attrs() -> Dictionary:
	var out := {}
	var f := FileAccess.open(ATTRS_PATH, FileAccess.READ)
	if f == null:
		push_warning("[manhattan_buildings] no building_attrs.json: styles from height only "
			+ "(drone/sim/tools/fetch_manhattan_building_attrs.py)")
		return out
	var doc: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var fields: Array = doc["fields"]
	var rows: Array = doc["rows"]
	var nf := fields.size()
	for i in range(0, rows.size(), nf):
		var a := PackedInt32Array()
		for j in range(2, nf):
			a.append(int(rows[i + j]))
		out[Vector2i(int(rows[i]), int(rows[i + 1]))] = a
	return out


# ------------------------------------------------------------------ what a building is

static func _cls(a: PackedInt32Array) -> String:
	if a.is_empty() or a[2] < 0:
		return "?"
	return char(65 + a[2])


## Style, colours and flags for one building. `a` is its attrs row (or empty).
func _describe(r: Rect2, h: float, a: PackedInt32Array, rng: RandomNumberGenerator) -> Dictionary:
	var year := a[0] if not a.is_empty() else 0
	var floors := a[1] if not a.is_empty() else 0
	var cls := _cls(a)
	var retail := not a.is_empty() and a[3] > 0
	var office := not a.is_empty() and a[4] > 0
	var party := a[6] if not a.is_empty() else 0
	var narrow := minf(r.size.x, r.size.y)
	var area := r.size.x * r.size.y
	var pre := year > 0 and year < 1946
	var old := year == 0 or pre
	var style := PREWAR
	if cls in ["M", "P", "W", "Y", "J", "N"]:
		style = CIVIC if old or h < 30.0 else MODERN
	elif cls in ["U", "T", "Q", "V", "Z"]:
		style = UTILITY if h < 25.0 else (GLASS if year >= 1960 else PREWAR)
	elif cls in ["E", "F", "G"]:
		style = UTILITY if h < 12.0 else LOFT
	elif cls in ["O", "H"]:
		if year >= 1955 or (year == 0 and h > 100.0):
			style = GLASS
		elif year >= 1946:
			style = POSTWAR
		else:
			style = LOFT if h <= 32.0 else PREWAR
	elif cls == "K":
		style = (LOFT if h < 25.0 else PREWAR) if old else (GLASS if h > 50.0 else MODERN)
	elif cls in ["A", "B"]:
		style = BROWNSTONE if old else MODERN
	elif cls in ["C", "S"]:
		if old:
			style = BROWNSTONE if (h <= 19.0 and narrow <= 9.0 and (year == 0 or year < 1905) and rng.randf() < 0.55) else WALKUP
		else:
			style = POSTWAR
	elif cls in ["D", "R"]:
		if old:
			style = WALKUP if h <= 24.0 else PREWAR
		elif year < 1986:
			style = POSTWAR
		else:
			style = GLASS if h > 80.0 else MODERN
	elif cls == "L":
		style = LOFT
	elif cls == "I":
		style = PREWAR if old else POSTWAR
	else:
		if year == 0:
			style = WALKUP if h < 22.0 else (PREWAR if h < 70.0 else GLASS)
		elif pre:
			style = WALKUP if h <= 22.0 else PREWAR
		elif year < 1980:
			style = GLASS if (h > 60.0 and office) else POSTWAR
		else:
			style = GLASS if h > 60.0 else MODERN
	if style == WALKUP and h > 40.0:
		style = PREWAR
	if style == BROWNSTONE and h > 24.0:
		style = WALKUP

	var flags := party * 16
	var wall := Color(0.5, 0.3, 0.22)
	var trim := 0
	var storey := 3.3
	var x := rng.randf()
	match style:
		BROWNSTONE:
			storey = 3.4
			if x < 0.55:
				wall = Color(0.36, 0.25, 0.20).lerp(Color(0.30, 0.21, 0.17), rng.randf())
				trim = 1
			elif x < 0.85:
				wall = Color(0.50, 0.26, 0.19).lerp(Color(0.43, 0.22, 0.17), rng.randf())
				trim = [0, 13, 1][rng.randi() % 3]
			else:
				wall = [Color(0.80, 0.78, 0.72), Color(0.66, 0.64, 0.60), Color(0.74, 0.66, 0.52),
						Color(0.55, 0.36, 0.30)][rng.randi() % 4]
				trim = [2, 3, 0][rng.randi() % 3]
			flags |= F_CORNICE if rng.randf() < 0.85 else 0
		WALKUP:
			storey = 3.15
			if x < 0.5:
				wall = Color(0.53, 0.27, 0.19).lerp(Color(0.44, 0.22, 0.16), rng.randf())
			elif x < 0.65:
				wall = Color(0.36, 0.23, 0.18).lerp(Color(0.30, 0.20, 0.16), rng.randf())
			elif x < 0.8:
				wall = Color(0.66, 0.56, 0.42).lerp(Color(0.58, 0.48, 0.36), rng.randf())
			else:
				wall = [Color(0.80, 0.79, 0.74), Color(0.76, 0.70, 0.58), Color(0.56, 0.56, 0.55),
						Color(0.60, 0.40, 0.34), Color(0.52, 0.58, 0.54), Color(0.66, 0.60, 0.48)][rng.randi() % 6]
			trim = [0, 0, 13, 3, 4, 10, 2, 5][rng.randi() % 8]
			flags |= F_CORNICE if rng.randf() < 0.8 else 0
			flags |= F_FIRE if (h >= 12.0 and rng.randf() < 0.8) else 0
		PREWAR:
			storey = 3.35
			if x < 0.4:
				wall = Color(0.72, 0.67, 0.57).lerp(Color(0.64, 0.60, 0.52), rng.randf())
				trim = [0, 5, 13][rng.randi() % 3]
			elif x < 0.75:
				wall = Color(0.62, 0.50, 0.38).lerp(Color(0.55, 0.44, 0.33), rng.randf())
				trim = [0, 13, 6][rng.randi() % 3]
			else:
				wall = Color(0.48, 0.30, 0.22).lerp(Color(0.42, 0.27, 0.21), rng.randf())
				trim = [0, 5, 6][rng.randi() % 3]
			flags |= F_CORNICE if rng.randf() < 0.55 else 0
			flags |= F_FIRE if (h < 45.0 and rng.randf() < 0.15) else 0
		POSTWAR:
			storey = 2.9
			if x < 0.45:
				wall = Color(0.75, 0.74, 0.69).lerp(Color(0.68, 0.67, 0.62), rng.randf())
			elif x < 0.7:
				wall = Color(0.64, 0.56, 0.45).lerp(Color(0.57, 0.50, 0.40), rng.randf())
			elif x < 0.9:
				wall = Color(0.48, 0.31, 0.24).lerp(Color(0.42, 0.28, 0.22), rng.randf())
			else:
				wall = Color(0.58, 0.58, 0.56)
			trim = [7, 7, 12, 9, 8][rng.randi() % 5]
		GLASS:
			storey = 3.9 if (office or cls == "O") else 3.1
			wall = Color(0.2, 0.2, 0.2)
			trim = [7, 9, 8, 15, 12, 7][rng.randi() % 6]
			flags |= F_TINT * (rng.randi() % 8)
			if year >= 1955 and year < 1982 and rng.randf() < 0.6:
				flags |= F_DEEP
		MODERN:
			storey = 3.0
			wall = [Color(0.72, 0.68, 0.60), Color(0.60, 0.60, 0.58), Color(0.50, 0.32, 0.24),
					Color(0.32, 0.33, 0.34), Color(0.66, 0.58, 0.48)][rng.randi() % 5]
			trim = [7, 9, 12, 8][rng.randi() % 4]
		LOFT:
			storey = 3.9
			wall = Color(0.50, 0.28, 0.20).lerp(Color(0.60, 0.48, 0.36), rng.randf())
			trim = [2, 13, 5, 4, 3, 11][rng.randi() % 6]
			flags |= F_CORNICE if rng.randf() < 0.6 else 0
		CIVIC:
			storey = 5.0
			wall = [Color(0.74, 0.70, 0.62), Color(0.56, 0.55, 0.53), Color(0.40, 0.28, 0.22),
					Color(0.66, 0.60, 0.50)][rng.randi() % 4]
			trim = 0 if wall.r > 0.6 else 5
			flags |= F_CORNICE if rng.randf() < 0.5 else 0
		UTILITY:
			storey = 4.0
			wall = [Color(0.55, 0.55, 0.53), Color(0.48, 0.30, 0.22), Color(0.62, 0.60, 0.55)][rng.randi() % 3]
			trim = 12
	if floors > 0:
		var s := h / float(floors)
		if s > 2.5 and s < 7.0:
			storey = clampf(s, 2.7, 5.5)
	# Shops along the ground floor: PLUTO's retail area, or the mixed-use and
	# retail classes; awnings on many low and mid-rise ones.
	var shops := (retail or cls in ["S", "K"]) and style != BROWNSTONE and style != CIVIC
	if style == WALKUP and cls == "?" and rng.randf() < 0.45:
		shops = true
	if shops and area > 40.0:
		flags |= F_SHOPS
		if h < 90.0 and rng.randf() < 0.6:
			flags |= F_AWNINGS
	if office or cls == "O":
		flags |= F_OFFICE
	flags |= F_TRIM * trim

	# Roofs: tar and silver coating on the old stock, white membrane and gravel
	# on the newer and commercial, a few green roofs and paved terraces.
	var roof := R_TAR
	var y := rng.randf()
	if style in [BROWNSTONE, WALKUP]:
		roof = R_TAR if y < 0.45 else (R_SILVER if y < 0.78 else (R_WHITE if y < 0.92 else (R_PAVERS if y < 0.97 else R_GREEN)))
	elif style in [GLASS, MODERN]:
		roof = R_WHITE if y < 0.35 else (R_GRAVEL if y < 0.65 else (R_TAR if y < 0.78 else (R_PAVERS if y < 0.9 else R_GREEN)))
	else:
		roof = R_TAR if y < 0.35 else (R_WHITE if y < 0.55 else (R_GRAVEL if y < 0.75 else (R_SILVER if y < 0.9 else (R_PAVERS if y < 0.96 else R_GREEN))))
	# Muted: from the air the city's brick and stone are dull and a shade dark
	# (soot, weather, haze); the first palette read as a toy town in full sun.
	var lum := wall.get_luminance()
	wall = wall.lerp(Color(lum, lum, lum), 0.28) * 0.9
	wall.a = 1.0
	var seed := rng.randf()
	return {"style": style, "roof": roof, "year": year, "storey": storey, "flags": flags,
		"wall": wall, "office": office, "cls": cls, "seed": seed,
		"custom": Color(seed, float(style + 16 * roof), storey, float(flags))}


# ------------------------------------------------------------------ parapets

## One ring per building: four walls round the roof edge with a coping on top,
## as one mesh whose inner face the vertex shader pulls in by a thickness in
## metres (INSTANCE_CUSTOM.x), so it stays 0.3 m thick on a 6 m brownstone and
## a 100 m block alike, at one instance a building instead of four.
func _parapet(r: Rect2, h: float, info: Dictionary, rng: RandomNumberGenerator, out: Dictionary) -> void:
	if h < 3.0 or r.size.x < 3.0 or r.size.y < 3.0:
		return
	var style: int = info["style"]
	var ph := rng.randf_range(0.7, 1.1)
	var t := 0.35
	var coping := Color(0.50, 0.48, 0.45)
	match style:
		BROWNSTONE, WALKUP:
			ph = rng.randf_range(0.6, 1.0)
		PREWAR, CIVIC:
			ph = rng.randf_range(0.9, 1.4)
			t = 0.45
		GLASS:
			ph = rng.randf_range(1.0, 1.6)
			t = 0.25
			coping = Color(0.55, 0.57, 0.58)
		UTILITY:
			ph = 0.5
	var wall: Color = info["wall"]
	if style == GLASS:
		wall = [Color(0.20, 0.22, 0.24), Color(0.5, 0.52, 0.54)][rng.randi() % 2]
	var c := r.get_center()
	var xf := Transform3D(Basis.IDENTITY.scaled(Vector3(r.size.x, ph, r.size.y)), Vector3(c.x, h, -c.y))
	_put(out, Vector2i(floori(c.x / 1000.0), floori(c.y / 1000.0)), xf, wall,
		Color(t, coping.r, float(info["seed"]), 0.0))
	counts["parapet"] = int(counts.get("parapet", 0)) + 1


static func _put(out: Dictionary, k: Vector2i, xf: Transform3D, col: Color, cust: Color) -> void:
	if not out.has(k):
		out[k] = [[], [], []]   # plain Arrays: a Packed array read out of an Array is a copy
	var d: Array = out[k]
	(d[0] as Array).append(xf)
	(d[1] as Array).append(col)
	(d[2] as Array).append(cust)


func _build_parapets(chunks: Dictionary) -> void:
	var root := Node3D.new()
	root.name = "parapets"
	env.add_child(root)
	var mesh := _ring_mesh()
	for k: Vector2i in chunks:
		var mmi := _mmi(mesh, chunks[k], parapet_mat)
		mmi.name = "parapet_%d_%d" % [k.x, k.y]
		mmi.visibility_range_end = 950.0
		mmi.visibility_range_end_margin = 150.0
		mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		root.add_child(mmi)


static func _mmi(mesh: Mesh, d: Array, mat: Material) -> MultiMeshInstance3D:
	var xf: Array = d[0]
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_colors = true
	mm.use_custom_data = true
	mm.mesh = mesh
	mm.instance_count = xf.size()
	var cols: Array = d[1]
	var cus: Array = d[2]
	for i in xf.size():
		mm.set_instance_transform(i, xf[i])
		mm.set_instance_color(i, cols[i])
		mm.set_instance_custom_data(i, cus[i])
	var mmi := MultiMeshInstance3D.new()
	mmi.multimesh = mm
	mmi.material_override = mat
	return mmi


## The parapet ring: a unit square -0.5..0.5 (x, z), 0..1 up. Inner-face
## vertices carry UV.x = 1 and sit on the outer square here; the parapet
## shader moves them in by the instance's thickness.
static func _ring_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var sides := [Vector3(1, 0, 0), Vector3(-1, 0, 0), Vector3(0, 0, 1), Vector3(0, 0, -1)]
	for n: Vector3 in sides:
		var rgt := (-n).cross(Vector3.UP)     # screen right seen from outside
		var l := n * 0.5 - rgt * 0.5
		var rr := n * 0.5 + rgt * 0.5
		# Outer face.
		_quad(st, [l, l + Vector3.UP, rr + Vector3.UP, rr], n, [0, 0, 0, 0])
		# Inner face, seen from the roof: the same corners, facing in.
		_quad(st, [rr, rr + Vector3.UP, l + Vector3.UP, l], -n, [1, 1, 1, 1])
		# Coping: outer top edge to inner top edge.
		_quad(st, [l + Vector3.UP, rr + Vector3.UP, rr + Vector3.UP, l + Vector3.UP], Vector3.UP, [0, 0, 1, 1])
	return st.commit()


## Two triangles a b c, a c d, wound clockwise seen from along `n` (Godot's
## front face). Winding is checked on the NOMINAL inner position (pulled in
## 0.1), since the real inner vertices coincide with the outer ones here.
static func _quad(st: SurfaceTool, p: Array, n: Vector3, inner: Array) -> void:
	var q: Array = []
	for i in 4:
		var v: Vector3 = p[i]
		if int(inner[i]) == 1:
			v = Vector3(v.x * 0.8, v.y, v.z * 0.8)
		q.append(v)
	var flip: bool = ((q[1] - q[0]) as Vector3).cross(q[2] - q[0]).dot(n) > 0.0
	var order := [0, 1, 2, 0, 2, 3] if not flip else [0, 2, 1, 0, 3, 2]
	for i: int in order:
		st.set_normal(n)
		st.set_uv(Vector2(float(inner[i]), (p[i] as Vector3).y))
		st.add_vertex(p[i])


# ------------------------------------------------------------------ rooftop kit

## The roof furniture of one building, in the sim frame (box centred at c, roof
## at h). Seeded by the building, so the same every run.
func _roof_kit(r: Rect2, h: float, info: Dictionary, rng: RandomNumberGenerator, acc: Dictionary, crowns: Array) -> void:
	if h < 4.0:
		return
	var style: int = info["style"]
	var year: int = info["year"]
	var w := r.size.x
	var l := r.size.y
	var area := w * l
	var c := r.get_center()
	var inner := Vector2(w - 1.6, l - 1.6)
	if inner.x < 2.0 or inner.y < 2.0:
		return
	var used: Array[Rect2] = []
	var wall: Color = info["wall"]
	var top := h

	# Tall towers: a set-back crown or a screened mechanical penthouse, drawn
	# with the building's own facade so its windows carry on up.
	if h > 90.0 and area > 300.0 and rng.randf() < 0.75:
		var tiers := 1 if (style == GLASS or rng.randf() < 0.5) else 2
		var cw := w
		var cl := l
		var y0 := h
		for t in tiers:
			cw *= rng.randf_range(0.55, 0.8)
			cl *= rng.randf_range(0.55, 0.8)
			var ch := rng.randf_range(4.0, 7.0) if style == GLASS else rng.randf_range(3.5, 6.5)
			var cust: Color = info["custom"]
			var fl := int(cust.a)
			fl = (fl & ~(0xF0 | F_SHOPS | F_AWNINGS | F_FIRE)) | F_UPPER
			if t < tiers - 1:
				fl &= ~F_CORNICE
			crowns.append([Transform3D(Basis.IDENTITY.scaled(Vector3(cw, ch, cl)), Vector3(c.x, y0 + ch * 0.5, -c.y)),
				wall, Color(cust.r, cust.g, cust.b, float(fl))])
			y0 += ch
		used.append(Rect2(-cw * 0.5, -cl * 0.5, cw, cl).grow(0.5))
		counts["crown"] = int(counts.get("crown", 0)) + tiers
		top = y0
		if h > 200.0 and rng.randf() < 0.6:
			for m in rng.randi_range(1, 3):
				var mh := rng.randf_range(8.0, 22.0)
				var p := Vector2(rng.randf_range(-cw, cw) * 0.3, rng.randf_range(-cl, cl) * 0.3)
				_part(acc, "mast", c, p, top, Vector3(0.5, mh, 0.5), Color(0.2, 0.2, 0.21), 0.0)

	# Elevator and stair bulkheads: brick boxes, most buildings over 5 storeys,
	# a small stair head on walk-ups, a hatch (skylight) on brownstones.
	var bulk_top := -1.0
	var bulk := Rect2()
	var elev := h >= 20.0 and style != BROWNSTONE
	if (elev or (style == WALKUP and h >= 12.0)) and rng.randf() < 0.85:
		var bw := rng.randf_range(3.0, 5.5) if elev else rng.randf_range(2.2, 3.0)
		var bl := rng.randf_range(3.5, 7.0) if elev else rng.randf_range(2.6, 3.4)
		bw = minf(bw, inner.x * 0.6)
		bl = minf(bl, inner.y * 0.6)
		var bh := rng.randf_range(3.2, 4.6) if elev else rng.randf_range(2.4, 2.9)
		var p := _spot(rng, used, inner, Vector2(bw, bl), 0.7)
		if p != Vector2.INF:
			var col := wall.darkened(0.08) if style != GLASS else Color(0.42, 0.42, 0.42)
			_part(acc, "bulkhead", c, p, h, Vector3(bw, bh, bl), col, 0.0)
			used.append(Rect2(p - Vector2(bw, bl) * 0.5, Vector2(bw, bl)).grow(0.6))
			bulk_top = h + bh
			bulk = Rect2(p - Vector2(bw, bl) * 0.5, Vector2(bw, bl))

	# Wooden water towers on conical-roof steel legs: the old 5-25 storey stock.
	var old := year == 0 or year < 1960
	var tower_ok := old and h >= 15.0 and h <= 100.0 and area >= 150.0 and style in [WALKUP, PREWAR, LOFT, POSTWAR, CIVIC, UTILITY]
	var p_tower := {WALKUP: 0.35, PREWAR: 0.6, LOFT: 0.7, POSTWAR: 0.25, CIVIC: 0.1, UTILITY: 0.3}
	if tower_ok and rng.randf() < float(p_tower.get(style, 0.0)):
		var nt := 2 if (area > 1500.0 and rng.randf() < 0.35) else 1
		for t in nt:
			var dia := rng.randf_range(3.6, 5.8) if area > 400.0 else rng.randf_range(3.0, 4.2)
			var bh := dia * rng.randf_range(0.85, 1.2)
			var legs := rng.randf_range(2.8, 5.5)
			var base := h
			var p: Vector2
			if bulk_top > 0.0 and t == 0 and rng.randf() < 0.45:
				# On a steel frame over the bulkhead, as so many are.
				p = bulk.get_center()
				base = bulk_top
				legs = rng.randf_range(1.5, 3.0)
			else:
				p = _spot(rng, used, inner, Vector2(dia, dia) + Vector2(0.6, 0.6), 0.4)
				if p == Vector2.INF:
					continue
				used.append(Rect2(p - Vector2(dia, dia) * 0.5, Vector2(dia, dia)).grow(0.5))
			var wood: Color = [Color(0.33, 0.27, 0.21), Color(0.40, 0.37, 0.33), Color(0.28, 0.22, 0.17),
						 Color(0.44, 0.33, 0.23), Color(0.34, 0.32, 0.30)][rng.randi() % 5]
			var steel: Color = [Color(0.07, 0.07, 0.07), Color(0.25, 0.14, 0.09), Color(0.33, 0.33, 0.33)][rng.randi() % 3]
			_part(acc, "tank_legs", c, p, base, Vector3(dia * 0.86, legs, dia * 0.86), steel, 0.0)
			# Barrel and conical roof are one mesh (the barrel is its lower 3/4):
			# one draw per chunk instead of two.
			_part(acc, "tank_wood", c, p, base + legs, Vector3(dia, bh / 0.75, dia), wood, rng.randf())
			counts["water_tower"] = int(counts.get("water_tower", 0)) + 1

	# Mechanical plant: condensers and packaged units on commercial and newer
	# roofs, a cooling tower on the big ones.
	var plant: bool = style in [GLASS, MODERN, POSTWAR, LOFT, UTILITY, CIVIC] or (style == PREWAR and h > 40.0) or info["office"]
	if plant and area > 200.0 and h > 10.0:
		if area > 1500.0 and h > 30.0 and rng.randf() < 0.65:
			var cw := minf(rng.randf_range(6.0, 12.0), inner.x * 0.5)
			var cl := minf(rng.randf_range(4.0, 7.0), inner.y * 0.5)
			var chh := rng.randf_range(3.5, 5.0)
			var p := _spot(rng, used, inner, Vector2(cw, cl), 1.0)
			if p != Vector2.INF:
				_part(acc, "ac_cooling", c, p, h, Vector3(cw, chh, cl),
					[Color(0.62, 0.64, 0.62), Color(0.55, 0.62, 0.58), Color(0.70, 0.67, 0.60)][rng.randi() % 3], 0.0)
				used.append(Rect2(p - Vector2(cw, cl) * 0.5, Vector2(cw, cl)).grow(0.6))
				var nf := maxi(1, int(cw / 3.2))
				for f in nf:
					var fp := p + Vector2((float(f) + 0.5) / float(nf) * cw - cw * 0.5, 0.0)
					_part(acc, "ac_fans", c, fp, h + chh, Vector3(2.4, 0.9, 2.4), Color(0.15, 0.15, 0.16), 0.0)
		var nu := clampi(int(area / 180.0), 1, 10)
		nu = rng.randi_range(maxi(1, nu / 2), nu)
		for u in nu:
			var s := Vector3(rng.randf_range(1.2, 3.5), rng.randf_range(0.9, 1.8), rng.randf_range(1.0, 2.4))
			var p := _spot(rng, used, inner, Vector2(s.x, s.z), 0.3)
			if p == Vector2.INF:
				continue
			_part(acc, "ac_units", c, p, h, s, Color(0.70, 0.71, 0.70).lerp(Color(0.56, 0.57, 0.56), rng.randf()), rng.randf())
			used.append(Rect2(p - Vector2(s.x, s.z) * 0.5, Vector2(s.x, s.z)).grow(0.3))
	elif style in [WALKUP, BROWNSTONE, PREWAR] and rng.randf() < 0.5:
		# A window-unit house still has a condenser or two on the roof.
		for u in rng.randi_range(1, 2):
			var s := Vector3(0.9, 0.8, 0.5) * rng.randf_range(0.9, 1.3)
			var p := _spot(rng, used, inner, Vector2(s.x, s.z), 0.2)
			if p != Vector2.INF:
				_part(acc, "ac_units", c, p, h, s, Color(0.74, 0.74, 0.72), rng.randf())
				used.append(Rect2(p - Vector2(s.x, s.z) * 0.5, Vector2(s.x, s.z)).grow(0.2))

	# Skylights and roof hatches on the old low stock and lofts.
	if style in [BROWNSTONE, WALKUP, LOFT] and rng.randf() < 0.7:
		for k in rng.randi_range(1, 3 if style == LOFT else 2):
			var s := Vector3(rng.randf_range(0.9, 1.6), rng.randf_range(0.35, 0.6), rng.randf_range(1.0, 2.2))
			if style == LOFT:
				s = Vector3(rng.randf_range(2.0, 4.0), rng.randf_range(0.6, 1.1), rng.randf_range(3.0, 6.0))
			var p := _spot(rng, used, inner, Vector2(s.x, s.z), 0.3)
			if p != Vector2.INF:
				_part(acc, "skylight", c, p, h, s, Color(0.30, 0.31, 0.32), 0.0)
				used.append(Rect2(p - Vector2(s.x, s.z) * 0.5, Vector2(s.x, s.z)).grow(0.3))

	# A small antenna or two on some.
	if rng.randf() < 0.12:
		var p := _spot(rng, used, inner, Vector2(0.5, 0.5), 0.2)
		if p != Vector2.INF:
			_part(acc, "mast", c, p, maxf(h, bulk_top if bulk.has_point(p) else h), Vector3(0.12, rng.randf_range(2.5, 5.0), 0.12), Color(0.35, 0.35, 0.35), 0.0)


## A free spot (roof-local x, z about the centre) for something `size` big, or INF.
static func _spot(rng: RandomNumberGenerator, used: Array[Rect2], inner: Vector2, size: Vector2, edge_bias: float) -> Vector2:
	var room := (inner - size) * 0.5
	if room.x < 0.0 or room.y < 0.0:
		return Vector2.INF
	for k in 10:
		# edge_bias > 0 pulls things toward the roof's edges, where plant and
		# bulkheads really sit (over the core at one side, clear of the middle).
		var u := rng.randf_range(-1.0, 1.0)
		var v := rng.randf_range(-1.0, 1.0)
		u = signf(u) * pow(absf(u), 1.0 - edge_bias * 0.7)
		v = signf(v) * pow(absf(v), 1.0 - edge_bias * 0.7)
		var p := Vector2(u * room.x, v * room.y)
		var rr := Rect2(p - size * 0.5, size)
		var ok := true
		for o in used:
			if o.intersects(rr):
				ok = false
				break
		if ok:
			return p
	return Vector2.INF


## A kit part at roof-local `p` (x east, y north about the building centre
## `c`), standing on `base` (m), `size` (x, y up, z north). Colour per instance;
## custom = (kind, seed, size.x, size.z).
func _part(acc: Dictionary, part: String, c: Vector2, p: Vector2, base: float, size: Vector3, col: Color, seed: float) -> void:
	var spec: Array = PARTS[part]
	var at := Vector3(c.x + p.x, base + size.y * 0.5, -(c.y + p.y))
	var xf := Transform3D(Basis.IDENTITY.scaled(size), at)
	var cs: float = spec[2]
	if not acc.has(part):
		acc[part] = {}
	_put(acc[part], Vector2i(floori(at.x / cs), floori(at.z / cs)), xf, col,
		Color(float(spec[1]), seed, size.x, size.z))
	counts[part] = int(counts.get(part, 0)) + 1


func _build_parts(acc: Dictionary) -> void:
	var root := Node3D.new()
	root.name = "rooftops"
	env.add_child(root)
	var meshes := _meshes()
	for part: String in acc:
		var spec: Array = PARTS[part]
		var chunks: Dictionary = acc[part]
		for k: Vector2i in chunks:
			var mmi := _mmi(meshes[spec[0]], chunks[k], kit_mat)
			mmi.name = "%s_%d_%d" % [part, k.x, k.y]
			if float(spec[3]) > 0.0:
				mmi.visibility_range_end = float(spec[3])
				mmi.visibility_range_end_margin = 80.0
				mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
			mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			root.add_child(mmi)
			if not spec[4]:
				continue
			if float(spec[3]) <= 0.0:
				mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
				continue
			# Shadows from the near chunks only: a shadow-only twin sharing the
			# same MultiMesh, culled at SHADOW_M. Casting from every chunk in
			# draw range put each bulkhead and tank into all four shadow splits
			# out to 1.8 km and cost ~1.5M triangles a frame over Midtown, for
			# shadows a few pixels long at that range.
			var sh := MultiMeshInstance3D.new()
			sh.name = mmi.name + "_shadow"
			sh.multimesh = mmi.multimesh
			sh.material_override = kit_mat
			sh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY
			sh.visibility_range_end = SHADOW_M
			root.add_child(sh)


## Crowns and setbacks: boxes on roofs, in the building material. One draw;
## a few thousand instances at most.
func _build_crowns(crowns: Array) -> void:
	if crowns.is_empty():
		return
	var d := [[], [], []]
	for cr: Array in crowns:
		(d[0] as Array).append(cr[0])
		(d[1] as Array).append(cr[1])
		(d[2] as Array).append(cr[2])
	var box := BoxMesh.new()
	box.size = Vector3.ONE
	var mmi := _mmi(box, d, city_mat)
	mmi.name = "city_crowns"
	env.add_child(mmi)


static func _meshes() -> Dictionary:
	var box := BoxMesh.new()
	box.size = Vector3.ONE
	# Meshes stand on y = -0.5 .. 0.5 like the box, so one placement rule fits all.
	var barrel := CylinderMesh.new()
	barrel.top_radius = 0.5
	barrel.bottom_radius = 0.5
	barrel.height = 1.0
	barrel.radial_segments = 10
	barrel.rings = 1
	var cone := CylinderMesh.new()
	cone.top_radius = 0.02
	cone.bottom_radius = 0.5
	cone.height = 1.0
	cone.radial_segments = 10
	cone.rings = 1
	cone.cap_bottom = true
	var thin := CylinderMesh.new()
	thin.top_radius = 0.35
	thin.bottom_radius = 0.5
	thin.height = 1.0
	thin.radial_segments = 6
	thin.rings = 1
	var fan := CylinderMesh.new()
	fan.top_radius = 0.5
	fan.bottom_radius = 0.5
	fan.height = 1.0
	fan.radial_segments = 12
	fan.rings = 1
	var crown := CylinderMesh.new()
	crown.top_radius = 0.38
	crown.bottom_radius = 0.5
	crown.height = 1.0
	crown.radial_segments = 16
	crown.rings = 1
	# A water tower: the barrel in y -0.5..0.25, its conical roof (a little
	# wider, overhanging) in 0.25..0.5; the kit shader tells them apart by height.
	var st := SurfaceTool.new()
	st.append_from(barrel, 0, Transform3D(Basis.IDENTITY.scaled(Vector3(1.0, 0.75, 1.0)), Vector3(0.0, -0.125, 0.0)))
	st.append_from(cone, 0, Transform3D(Basis.IDENTITY.scaled(Vector3(1.06, 0.25, 1.06)), Vector3(0.0, 0.375, 0.0)))
	return {"box": box, "tank": st.commit(), "thin": thin, "fan": fan, "crown": crown,
		"legs": _legs_mesh()}


## A water tower's steel stand in one mesh: four legs at the corners of the
## unit square, two ties and the deck at the top, in -0.5..0.5 (scaled to the tank's width and the legs' height).
static func _legs_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var lw := 0.05
	for cx: float in [-0.42, 0.42]:
		for cz: float in [-0.42, 0.42]:
			_box_into(st, Vector3(cx, 0.0, cz), Vector3(lw, 1.0, lw))
	# One tie round the legs low down; diagonal braces were tried and cost more
	# triangles than the 8.5k stands could justify for a part under 2 px at 600 m.
	_box_into(st, Vector3(0.42, 0.1, 0.0), Vector3(0.025, 0.025, 0.84))
	_box_into(st, Vector3(-0.42, 0.1, 0.0), Vector3(0.025, 0.025, 0.84))
	_box_into(st, Vector3(0.0, 0.48, 0.0), Vector3(1.0, 0.04, 1.0))   # the deck under the tank
	return st.commit()


static func _box_into(st: SurfaceTool, c: Vector3, s: Vector3) -> void:
	var h := s * 0.5
	var faces := [[Vector3(1, 0, 0), Vector3(0, 1, 0), Vector3(0, 0, 1)], [Vector3(-1, 0, 0), Vector3(0, 1, 0), Vector3(0, 0, -1)],
				  [Vector3(0, 0, 1), Vector3(0, 1, 0), Vector3(-1, 0, 0)], [Vector3(0, 0, -1), Vector3(0, 1, 0), Vector3(1, 0, 0)],
				  [Vector3(0, 1, 0), Vector3(0, 0, -1), Vector3(1, 0, 0)], [Vector3(0, -1, 0), Vector3(0, 0, 1), Vector3(1, 0, 0)]]
	for f: Array in faces:
		var n: Vector3 = f[0]
		var u: Vector3 = f[1]
		var v: Vector3 = f[2]
		var o := c + n * h
		var pu := u * h
		var pv := v * h
		var q := [o - pu - pv, o + pu - pv, o + pu + pv, o - pu + pv]
		var flip: bool = ((q[1] - q[0]) as Vector3).cross(q[2] - q[0]).dot(n) > 0.0
		var order := [0, 1, 2, 0, 2, 3] if not flip else [0, 2, 1, 0, 3, 2]
		for i: int in order:
			st.set_normal(n)
			st.set_uv(Vector2.ZERO)
			st.add_vertex(q[i])


# ------------------------------------------------------------------ icons

## The three tops anyone knows from the air, found from their real lat/lon:
## the Empire State's stepped crown and mast, the Chrysler's terraced steel
## crown and needle, One World Trade Center's spire. Real heights: Empire
## State 381 m roof / 443 m tip, Chrysler 319 m tip, One WTC 541 m tip; the
## parts are sized so each tip lands near its real height above the box the
## data gives.
func _icons(rects: Array[Rect2], heights: PackedFloat32Array, acc: Dictionary, crowns: Array) -> void:
	var icons := [["esb", 40.748440, -73.985664], ["chrysler", 40.751621, -73.975502], ["wtc1", 40.712742, -74.013382]]
	for ic: Array in icons:
		var p: Vector2 = env.call("geo_to_sim", float(ic[1]), float(ic[2]))
		var best := -1
		var best_d := 1e9
		for i in rects.size():
			var r: Rect2 = rects[i]
			if heights[i] < 150.0:
				continue
			var d := 0.0 if r.has_point(p) else r.get_center().distance_to(p)
			if d < best_d:
				best_d = d
				best = i
		if best < 0 or best_d > 80.0:
			continue
		var r: Rect2 = rects[best]
		var h: float = heights[best]
		var c := r.get_center()
		var tip := h
		match str(ic[0]):
			"esb":
				# Limestone setbacks, the lit lantern, the mooring mast, the antenna.
				var tiers := [[34.0, 5.0], [26.0, 5.0], [18.0, 8.0]]
				for t: Array in tiers:
					_part(acc, "crown_stone", c, Vector2.ZERO, tip, Vector3(t[0], t[1], t[0]), Color(0.72, 0.69, 0.62), 0.0)
					tip += float(t[1])
				_part(acc, "crown_steel", c, Vector2.ZERO, tip, Vector3(11.0, 22.0, 11.0), Color(0.70, 0.70, 0.68), 1.0)
				tip += 22.0
				_part(acc, "spire", c, Vector2.ZERO, tip, Vector3(2.2, 44.0, 2.2), Color(0.75, 0.75, 0.74), 0.0)
			"chrysler":
				# Seven terraced, tapering steel tiers, then the needle.
				var rad := minf(r.size.x, r.size.y) * 0.45
				for t in 7:
					var th := 3.2
					_part(acc, "crown_steel", c, Vector2.ZERO, tip, Vector3(rad * 2.0, th, rad * 2.0), Color(0.80, 0.81, 0.82), 0.0)
					tip += th
					rad *= 0.80
				_part(acc, "spire", c, Vector2.ZERO, tip, Vector3(1.6, 8.0, 1.6), Color(0.85, 0.86, 0.87), 0.0)
			"wtc1":
				# The spire's base ring and the white mast.
				_part(acc, "crown_stone", c, Vector2.ZERO, tip, Vector3(16.0, 3.0, 16.0), Color(0.55, 0.57, 0.58), 0.0)
				tip += 3.0
				_part(acc, "spire", c, Vector2.ZERO, tip, Vector3(4.0, 104.0, 4.0), Color(0.86, 0.86, 0.85), 0.0)
		counts["icon"] = int(counts.get("icon", 0)) + 1
		print("[manhattan_buildings] %s on the %.0f m box at (%.0f, %.0f), %.0f m off; tip %.0f m" %
			[ic[0], h, c.x, c.y, best_d, tip + (44.0 if ic[0] == "esb" else (8.0 if ic[0] == "chrysler" else 104.0))])


# ------------------------------------------------------------------ shaders

func _make_material(code: String) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = "shader_type spatial;\n#include \"%s\"\n" % INC + code
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


const BUILDING_SHADER := """
render_mode cull_back, diffuse_burley;
uniform sampler2DArray cells_mask : filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2DArray cells_col : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform float night = 0.0;   // 0 day .. 1 night (env.night_amount); 0 = no emission at all

varying vec3 v_local;
varying vec3 v_nl;
varying vec3 v_size;
varying vec3 v_world;
varying vec4 v_col;
varying vec4 v_cus;

void vertex() {
    v_size = vec3(length(MODEL_MATRIX[0].xyz), length(MODEL_MATRIX[1].xyz), length(MODEL_MATRIX[2].xyz));
    v_local = VERTEX;
    v_nl = NORMAL;
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    v_col = COLOR;
    v_cus = INSTANCE_CUSTOM;
}

float hh(vec4 p) {
    p = fract(p * vec4(0.1031, 0.1030, 0.0973, 0.1099));
    p += dot(p, p.wzxy + 33.33);
    return fract((p.x + p.y) * (p.z + p.w));
}
float hs(float seed, float k) { return hh(vec4(seed * 1000.0, k, 7.0, 3.0)); }
float bit(float flags, float b) { return mod(floor(flags / b + 0.001), 2.0); }
// Instance colours and the palettes here are written as sRGB, the way colours
// are picked; lighting wants linear. (The cells' colour texture is source_color,
// so it arrives linear already.) Skipping this washed every wall out to pastel.
vec3 lin(vec3 c) { return pow(max(c, vec3(0.0)), vec3(2.2)); }

float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hh(vec4(i, 1.3, 7.7));
    float b = hh(vec4(i + vec2(1.0, 0.0), 1.3, 7.7));
    float c = hh(vec4(i + vec2(0.0, 1.0), 1.3, 7.7));
    float d = hh(vec4(i + vec2(1.0, 1.0), 1.3, 7.7));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
// Noise at `period` metres, faded to its mean once a period is under ~3 px.
float fnoise(vec2 p, float period, float fw) {
    return mix(vnoise(p / period), 0.5, smoothstep(0.15, 0.45, fw / period));
}
float aaline(float v, float w) {
    float fw = fwidth(v);
    float d = abs(fract(v + 0.5) - 0.5);
    float l = 1.0 - smoothstep(w * 0.5 - fw, w * 0.5 + fw, d);
    return mix(l, w, smoothstep(w * 0.5, w * 2.0, fw));
}

const vec3 TRIM[16] = vec3[16](vec3(0.78, 0.74, 0.64), vec3(0.47, 0.36, 0.29), vec3(0.85, 0.84, 0.80),
    vec3(0.08, 0.08, 0.08), vec3(0.10, 0.20, 0.15), vec3(0.60, 0.60, 0.58), vec3(0.70, 0.45, 0.32),
    vec3(0.62, 0.64, 0.66), vec3(0.16, 0.13, 0.10), vec3(0.06, 0.06, 0.07), vec3(0.30, 0.20, 0.14),
    vec3(0.70, 0.62, 0.48), vec3(0.55, 0.54, 0.52), vec3(0.86, 0.80, 0.66), vec3(0.35, 0.12, 0.10),
    vec3(0.30, 0.35, 0.42));
const vec3 TINT[8] = vec3[8](vec3(0.55, 0.58, 0.60), vec3(0.48, 0.55, 0.64), vec3(0.50, 0.60, 0.58),
    vec3(0.62, 0.55, 0.46), vec3(0.34, 0.36, 0.38), vec3(0.72, 0.74, 0.76), vec3(0.45, 0.56, 0.60),
    vec3(0.66, 0.70, 0.72));
// Awnings and sign bands.
const vec3 SHOP[10] = vec3[10](vec3(0.55, 0.10, 0.09), vec3(0.08, 0.30, 0.16), vec3(0.07, 0.12, 0.30),
    vec3(0.06, 0.06, 0.06), vec3(0.82, 0.80, 0.74), vec3(0.80, 0.62, 0.12), vec3(0.36, 0.10, 0.12),
    vec3(0.75, 0.35, 0.10), vec3(0.20, 0.40, 0.45), vec3(0.45, 0.45, 0.45));

const float L_SASH = 0.0;
const float L_PAIR = 5.0;
const float L_FIRE_A = 9.0;
const float L_FIRE_B = 10.0;
const float L_RIBBON = 11.0;
const float L_CURTAIN = 14.0;
const float L_CURTAIN_DEEP = 17.0;
const float L_LOFT = 18.0;
const float L_PRECAST = 20.0;
const float L_CIVIC = 23.0;
const float L_SHOP_OPEN = 25.0;
const float L_SHOP_GATE = 26.0;
const float L_SHOP_AWNING = 27.0;
const float L_STOOP = 28.0;
const float L_GARDEN = 29.0;
const float L_LOBBY = 30.0;
const float L_ENTRANCE = 31.0;
const float L_BLANK = 32.0;
const float L_CORNICE = 33.0;
const float L_PARTY = 34.0;

// Styles (manhattan_buildings.gd).
const int BROWNSTONE = 0;
const int WALKUP = 1;
const int PREWAR = 2;
const int POSTWAR = 3;
const int GLASS = 4;
const int MODERN = 5;
const int LOFT = 6;
const int CIVIC = 7;
const int UTILITY = 8;

// The style's typical upper-floor cell, for the far-off average.
float base_layer(int s, bool deep) {
    if (s == PREWAR) return L_PAIR;
    if (s == POSTWAR) return L_RIBBON;
    if (s == GLASS) return deep ? L_CURTAIN_DEEP : L_CURTAIN;
    if (s == MODERN) return L_PRECAST;
    if (s == LOFT) return L_LOFT;
    if (s == CIVIC) return L_CIVIC;
    return L_SASH;
}
// A window's own variant: blind down, curtains, an AC unit, the sky in it.
float upper_layer(int s, float r, float seed, bool deep) {
    if (s == PREWAR) return L_PAIR + (r < 0.42 ? 0.0 : r < 0.68 ? 1.0 : r < 0.68 + 0.2 * hs(seed, 8.0) ? 2.0 : 3.0);
    if (s == POSTWAR) return L_RIBBON + (r < 0.45 ? 0.0 : r < 0.8 ? 1.0 : 2.0);
    if (s == GLASS) return deep ? L_CURTAIN_DEEP : L_CURTAIN + (r < 0.58 ? 0.0 : r < 0.74 ? 1.0 : 2.0);
    if (s == MODERN) return L_PRECAST + (r < 0.45 ? 0.0 : r < 0.7 ? 1.0 : 2.0);
    if (s == LOFT) return L_LOFT + (r < 0.6 ? 0.0 : 1.0);
    if (s == CIVIC) return L_CIVIC + (r < 0.6 ? 0.0 : 1.0);
    float ac = 0.05 + 0.22 * hs(seed, 8.0);
    return L_SASH + (r < 0.36 ? 0.0 : r < 0.56 ? 1.0 : r < 0.72 ? 2.0 : r < 0.72 + ac ? 3.0 : 4.0);
}
float bay_width(int s, float seed) {
    float k = hs(seed, 6.0);
    if (s == BROWNSTONE) return mix(1.9, 2.3, k);
    if (s == WALKUP) return mix(1.8, 2.5, k);
    if (s == PREWAR) return mix(3.2, 3.9, k);
    if (s == POSTWAR) return mix(3.0, 3.8, k);
    if (s == GLASS) return mix(1.5, 1.6, k);
    if (s == MODERN) return mix(2.6, 3.3, k);
    if (s == LOFT) return mix(3.0, 3.8, k);
    if (s == CIVIC) return mix(3.6, 4.8, k);
    return 4.0;
}

void fragment() {
    float seed = v_cus.x;
    int style = int(mod(v_cus.y + 0.01, 16.0));
    int roof = int(floor((v_cus.y + 0.01) / 16.0));
    float storey = v_cus.z;
    float flags = v_cus.w;
    vec3 wallc = lin(v_col.rgb);
    float h = v_size.y;
    vec3 nl = normalize(v_nl);
    bool is_roof = nl.y > 0.5;
    bool upper_only = bit(flags, 32768.0) > 0.5;
    bool office = bit(flags, 65536.0) > 0.5;
    bool deep = bit(flags, 131072.0) > 0.5;
    vec3 trim = lin(TRIM[int(mod(floor(flags / 2048.0 + 0.001), 16.0))]);
    vec3 tint = lin(TINT[int(mod(floor(flags / 256.0 + 0.001), 8.0))]);

    // Outputs shared by the EO and thermal branches.
    vec3 c = vec3(0.5);
    float rough = 0.9;
    float metal = 0.0;
    float spec = 0.4;
    vec3 emit = vec3(0.0);
    float glass_frac = 0.0;
    float absorb = 0.6;      // thermal: roof membrane absorptance
    float mass = 0.6;
    float veg = 0.0;

    if (is_roof) {
        vec2 p = v_world.xz;
        vec2 q = vec2(v_local.x * v_size.x, v_local.z * v_size.z);
        float fw = length(fwidth(p));
        float n1 = fnoise(p + seed * 311.0, 6.0, fw);
        float n2 = fnoise(p + seed * 97.0, 1.4, fw);
        float edge = min(0.5 * v_size.x - abs(q.x), 0.5 * v_size.z - abs(q.y));
        if (roof == 0) {            // tar / modified bitumen
            c = vec3(0.075, 0.072, 0.07) * (0.7 + 0.6 * n1) * (0.85 + 0.3 * n2);
            c *= 1.0 - 0.12 * aaline(q.x / 0.95, 0.04);
            rough = 0.92; absorb = 0.93; mass = 0.5;
        } else if (roof == 1) {     // silver aluminium coating, weathered
            c = mix(vec3(0.40, 0.41, 0.41), vec3(0.24, 0.23, 0.22), smoothstep(0.55, 0.8, n1)) * (0.85 + 0.25 * n2);
            rough = 0.55; metal = 0.2; absorb = 0.45; mass = 0.35;
        } else if (roof == 2) {     // white membrane, seams, dirt where water stands
            c = vec3(0.54, 0.55, 0.54) * (0.85 + 0.2 * n2);
            c = mix(c, vec3(0.42, 0.41, 0.39), smoothstep(0.62, 0.85, n1) * 0.7);
            c *= 1.0 - 0.10 * aaline(q.x / 3.0, 0.05);
            rough = 0.75; absorb = 0.22; mass = 0.25;
        } else if (roof == 3) {     // ballast gravel
            c = vec3(0.37, 0.35, 0.32) * (0.8 + 0.35 * n1) * (0.75 + 0.5 * n2);
            rough = 1.0; absorb = 0.7; mass = 0.8;
        } else if (roof == 4) {     // green roof: sedum mats inside a gravel border
            vec3 g = mix(vec3(0.22, 0.25, 0.14), vec3(0.36, 0.30, 0.20), n1) * (0.75 + 0.5 * n2);
            float border = smoothstep(1.0, 1.4, edge);
            c = mix(vec3(0.40, 0.38, 0.35) * (0.8 + 0.3 * n2), g, border);
            rough = 1.0; absorb = 0.5; mass = 0.6; veg = border;
        } else {                    // pavers on a terrace
            vec2 tile = floor(q / 0.61);
            float th = mix(hh(vec4(tile, seed * 100.0, 4.0)), 0.5, smoothstep(0.1, 0.3, fw / 0.61));
            c = mix(vec3(0.50, 0.47, 0.42), vec3(0.60, 0.58, 0.54), th);
            c *= 1.0 - 0.25 * max(aaline(q.x / 0.61, 0.06), aaline(q.y / 0.61, 0.06));
            rough = 0.85; absorb = 0.55; mass = 0.75;
        }
        // Darker in the lee of the parapet and where the edge flashing runs.
        c *= mix(0.72, 1.0, smoothstep(0.0, 1.0, edge));
        c = lin(c);
    } else {
        // Which face, and where along it from one corner (metres).
        bool side = abs(nl.x) > 0.5;
        float flen = side ? v_size.z : v_size.x;
        float u = (side ? v_local.z : v_local.x) * flen + 0.5 * flen;
        float face = side ? (nl.x > 0.0 ? 1.0 : 2.0) : (nl.z < 0.0 ? 4.0 : 8.0);
        float y = v_local.y * h + 0.5 * h;
        bool party = bit(flags, 16.0 * face) > 0.5 && !(style == GLASS && y > 30.0) && !upper_only;
        bool cornice = bit(flags, 8.0) > 0.5;
        bool shops = bit(flags, 1.0) > 0.5;
        bool awnings = bit(flags, 2.0) > 0.5;
        bool fire = bit(flags, 4.0) > 0.5;

        // Storeys: a taller ground floor (shops, a lobby), the upper storeys
        // fitted to what is left, and the cornice or parapet band on top.
        float top = cornice ? 1.3 : 0.45;
        float gfh = upper_only ? 0.0 : (style == GLASS ? max(storey * 1.4, 5.0)
                  : (shops ? max(storey * 1.25, 4.2) : storey * 1.05));
        if (!upper_only && h < gfh + 2.4) { gfh = h - top; }
        float upper = max(h - gfh - top, 0.0);
        float nup = floor(upper / storey + 0.5);
        float st = nup > 0.0 ? upper / nup : 1.0;
        float y_top = gfh + nup * st;

        float bay = bay_width(style, seed);
        float cell_h;
        float ycell;
        float zone;            // 0 ground, 1 upper, 2 top band
        if (y < gfh) { zone = 0.0; cell_h = gfh; ycell = y; if (shops) { bay = mix(4.5, 7.0, hs(seed, 40.0)); } }
        else if (y < y_top) { zone = 1.0; cell_h = st; ycell = y - gfh; }
        else { zone = 2.0; cell_h = max(h - y_top, 0.3); ycell = y - y_top; bay = 0.9; }
        float nb = max(1.0, floor(flen / bay + 0.5));
        float bw = flen / nb;
        vec2 cuv = vec2(u / bw, ycell / cell_h);
        vec2 dx = vec2(dFdx(u), dFdx(y)) / vec2(bw, cell_h);
        vec2 dy = vec2(dFdy(u), dFdy(y)) / vec2(bw, cell_h);
        float bi = clamp(floor(cuv.x), 0.0, nb - 1.0);
        float fi = zone < 0.5 ? 0.0 : (zone < 1.5 ? 1.0 + floor(cuv.y) : 1.0 + nup);
        vec2 f = vec2(cuv.x - bi, fract(cuv.y));
        if (zone == 0.0 || zone == 2.0) { f.y = clamp(cuv.y, 0.0, 1.0); }

        float r = hh(vec4(seed * 1000.0, fi, bi, face));
        // Office floors read as floors: blinds are set across a few bays at a time.
        if (style == GLASS) { r = hh(vec4(seed * 1000.0, fi, floor(bi / 5.0), face)); }
        vec3 tcol = trim;
        float layer;
        if (party) {
            layer = L_PARTY;
            wallc = style == GLASS || style == MODERN ? lin(vec3(0.52, 0.51, 0.49)) : mix(wallc, lin(vec3(0.45, 0.29, 0.23)), 0.6);
        } else if (zone == 2.0) {
            layer = cornice ? L_CORNICE : L_BLANK;
        } else if (zone == 0.0) {
            float dbi = floor(nb * hs(seed, 14.0 + face));
            bool door = abs(bi - dbi) < 0.5;
            if (style == GLASS) {
                layer = L_LOBBY;
            } else if (shops) {
                float sr = hh(vec4(seed * 1000.0, bi, face, 5.0));
                layer = door && nb > 2.0 ? L_ENTRANCE : (awnings && sr < 0.55 ? L_SHOP_AWNING : (sr < 0.85 ? L_SHOP_OPEN : L_SHOP_GATE));
                tcol = lin(SHOP[int(floor(hh(vec4(seed * 1000.0, bi, face, 9.0)) * 9.99))]);
            } else if (style == BROWNSTONE) {
                layer = door ? L_STOOP : L_GARDEN;
            } else if (style == LOFT) {
                layer = door ? L_ENTRANCE : L_LOFT + (r < 0.6 ? 0.0 : 1.0);
            } else if (style == UTILITY) {
                layer = door ? L_ENTRANCE : L_BLANK;
            } else {
                layer = door ? L_ENTRANCE : upper_layer(style, r, seed, deep);
            }
        } else {
            // Upper floors. Fire escapes stack up two bays near the middle of
            // a walk-up's street faces.
            float fb = floor(nb * mix(0.3, 0.6, hs(seed, 21.0 + face)));
            bool fe = fire && nb >= 3.0 && flen > 6.0 && (abs(bi - fb) < 0.5 || abs(bi - fb - 1.0) < 0.5);
            if (fe) {
                layer = mod(fi + bi, 2.0) < 0.5 ? L_FIRE_A : L_FIRE_B;
            } else if (style == UTILITY && r < 0.6) {
                layer = L_BLANK;
            } else {
                layer = upper_layer(style, r, seed, deep);
            }
        }

        vec4 m = textureGrad(cells_mask, vec3(f, layer), dx, dy);
        vec3 own = textureGrad(cells_col, vec3(f, layer), dx, dy).rgb;
        // Far off, a cell is a pixel or two: blend to the style's average cell,
        // so per-window choices do not crawl from pixel to pixel.
        float px = max(length(dx), length(dy));
        float far = smoothstep(0.2, 0.55, px);
        // The style's own cell, still mipmapped on the same grid: storey lines
        // and the window rhythm stay while they are resolved and fade by the
        // mips after (blending straight to a flat average lost every tower's
        // floors past ~400 m and left them plain coloured slabs).
        if (far > 0.0) {
            float avg_l = party ? L_PARTY : (zone == 2.0 ? (cornice ? L_CORNICE : L_BLANK)
                        : (zone == 0.0 ? (style == GLASS ? L_LOBBY : (shops ? L_SHOP_OPEN : base_layer(style, deep))) : base_layer(style, deep)));
            vec4 ma = textureGrad(cells_mask, vec3(f, avg_l), dx, dy);
            vec3 ca = textureGrad(cells_col, vec3(f, avg_l), dx, dy).rgb;
            m = mix(m, ma, far);
            own = mix(own, ca, far);
            tcol = mix(tcol, trim, far);
        }
        // Weathering: some walls fresh, some dull; grime near the street, a
        // little streaking under the cornice.
        float wear = 0.86 + 0.16 * hs(seed, 2.0);
        vec3 wall = wallc * wear * (1.0 - 0.16 * smoothstep(2.5, 0.0, y));
        wall *= 1.0 - 0.06 * smoothstep(h - 6.0, h, y) * (cornice ? 1.0 : 0.0);
        if (style == GLASS) { own *= tint * 1.1; }
        float rest = clamp(1.0 - m.r - m.g, 0.0, 1.0);
        c = (own * rest + wall * m.r + tcol * m.g) * m.a;
        glass_frac = m.b;
        // Glass reflects: a curtain wall like a mirror of the sky, a punched sash less.
        float gm = style == GLASS ? 0.55 : 0.3;
        float gr = style == GLASS ? 0.08 : 0.12;
        rough = mix(0.88, gr, m.b);
        metal = m.b * gm;
        spec = mix(0.35, 0.65, m.b);
        if (style == GLASS) {
            // Mullions and spandrels are metal and glass too.
            rough = mix(rough, 0.25, m.g);
            metal = max(metal, m.g * 0.5);
        }

        // At night some rooms are lit: a share per building, more or fewer per
        // floor; offices most at dusk and thinning out, homes filling up later;
        // shops nearly all. Warm bulbs, neutral LED, cool office strip lights.
        float dusk = 1.0 - smoothstep(0.55, 1.0, night);
        float share = (office || style == GLASS)
            ? mix(0.45, 0.85, hs(seed, 61.0)) * mix(0.35, 1.0, dusk)
            : mix(0.12, 0.45, hs(seed, 61.0)) * mix(0.75, 1.15, smoothstep(0.3, 0.8, night));
        float fl_k = hh(vec4(seed * 1000.0, fi, 3.0, 71.0));
        float shop_lit = 0.0;
        if (zone == 0.0 && (shops || style == GLASS)) { share = 0.85; shop_lit = 1.0; }
        else if (office || style == GLASS) { share *= step(fl_k, share * 1.2) * 1.15; }    // whole floors on or off
        else { share *= mix(0.55, 1.4, fl_k); }
        float wk = hh(vec4(seed * 1000.0, fi, bi, face + 17.0));
        float on = mix(step(wk, share), clamp(share, 0.0, 1.0), far);
        float ck = hh(vec4(seed * 1000.0, fi, bi, face + 29.0));
        vec3 lc = (office || style == GLASS) ? (ck < 0.75 ? vec3(0.82, 0.90, 1.0) : vec3(1.0, 0.88, 0.70))
                : (ck < 0.6 ? vec3(1.0, 0.70, 0.40) : (ck < 0.88 ? vec3(1.0, 0.86, 0.68) : vec3(0.75, 0.82, 1.0)));
        if (shop_lit > 0.5) { lc = vec3(1.0, 0.88, 0.70) * 1.4; }
        lc *= mix(0.5, 1.1, hh(vec4(seed * 1000.0, fi, bi, face + 37.0)));
        emit = lc * m.b * on * night * 1.6;
    }

    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th;
        if (is_roof) {
            th = th_surface(absorb, 26.0, mass, 0.6 * hs(seed, 7.1));
            th = mix(th, th_surface(0.45, 5.0, 0.55, -1.0), veg);
        } else {
            th = th_surface(0.6, 18.0, 0.7, 0.8 + 1.5 * hs(seed, 11.3));
            // Glass shows the cold sky; a lit room behind it reads a little warm.
            float sky = th_enc(mix(thermal_air_c, thermal_sky_c, 0.35));
            th.y = mix(th.y, sky, glass_frac * (style == GLASS ? 0.75 : 0.45));
            th.x *= 1.0 - glass_frac * 0.6;
            th.y += length(emit) * 0.004;
        }
        TH_OUT(th);
    } else {
        ALBEDO = c;
        ROUGHNESS = rough;
        METALLIC = metal;
        SPECULAR = spec;
        EMISSION = emit;
    }
}
"""


## Parapets: the building's wall colour outside, a lighter coping on top, and
## inside the dark flashing every flat roof runs up its parapet.
const PARAPET_SHADER := """
render_mode cull_back, diffuse_burley;
varying vec4 v_col;
varying vec4 v_cus;
varying float v_inner;
varying float v_y;
void vertex() {
    vec3 s = vec3(length(MODEL_MATRIX[0].xyz), length(MODEL_MATRIX[1].xyz), length(MODEL_MATRIX[2].xyz));
    float t = INSTANCE_CUSTOM.x;
    if (UV.x > 0.5) {
        VERTEX.x = sign(VERTEX.x) * max(0.5 - t / s.x, 0.02);
        VERTEX.z = sign(VERTEX.z) * max(0.5 - t / s.z, 0.02);
    }
    v_col = COLOR;
    v_cus = INSTANCE_CUSTOM;
    v_inner = UV.x;
    v_y = UV.y;
}
void fragment() {
    vec3 c;
    bool top = NORMAL.y > 0.5;
    vec3 wn = (INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).xyz;
    if (wn.y > 0.5) {
        c = vec3(v_cus.y) * vec3(1.0, 0.98, 0.94);
    } else if (v_inner > 0.5) {
        c = mix(vec3(0.07, 0.07, 0.07), v_col.rgb * 0.75, step(0.55, v_y));
    } else {
        c = v_col.rgb * 0.95;
    }
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.6, 18.0, 0.7, 0.5);
        TH_OUT(th);
    } else {
        ALBEDO = pow(c, vec3(2.2));
        ROUGHNESS = 0.9;
        SPECULAR = 0.35;
    }
}
"""


## Rooftop kit: one material for every part, the part's kind in
## INSTANCE_CUSTOM.x (0 bulkhead brick, 1 wooden tank staves and hoops,
## 2 tank roof, 3 plain steel, 4 condensing unit, 5 skylight, 6 cooling tower,
## 7 polished steel crown, 8 white mast, 9 stone crown).
const KIT_SHADER := """
render_mode cull_back, diffuse_burley;
uniform float night = 0.0;
varying vec4 v_col;
varying vec4 v_cus;
varying vec3 v_local;
varying vec3 v_size;
varying vec3 v_world;
void vertex() {
    v_size = vec3(length(MODEL_MATRIX[0].xyz), length(MODEL_MATRIX[1].xyz), length(MODEL_MATRIX[2].xyz));
    v_local = VERTEX;
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    v_col = COLOR;
    v_cus = INSTANCE_CUSTOM;
}
float aaline(float v, float w) {
    float fw = fwidth(v);
    float d = abs(fract(v + 0.5) - 0.5);
    float l = 1.0 - smoothstep(w * 0.5 - fw, w * 0.5 + fw, d);
    return mix(l, w, smoothstep(w * 0.5, w * 2.0, fw));
}
void fragment() {
    int kind = int(v_cus.x + 0.5);
    vec3 wn = (INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).xyz;
    bool up = wn.y > 0.6;
    vec3 c = v_col.rgb;
    float rough = 0.85;
    float metal = 0.0;
    vec3 emit = vec3(0.0);
    float y = (v_local.y + 0.5) * v_size.y;
    vec2 th;
    if (kind == 0) {            // brick bulkhead: tar top, a louvre band, a door
        if (up) { c = vec3(0.08, 0.08, 0.08); }
        else {
            c *= 1.0 - 0.08 * aaline(y / 0.2, 0.15);
            c *= 1.0 - 0.3 * step(v_size.y - 0.9, y) * step(y, v_size.y - 0.4) * step(0.3, abs(v_local.x + v_local.z));
        }
        th = th_surface(0.6, 18.0, 0.7, 0.5);
    } else if (kind == 1) {     // wooden tank: staves round, steel hoops up it; its roof above
        if (v_local.y > 0.25) {
            c *= v_cus.y < 0.6 ? 0.6 : 0.3;
        } else {
            float ang = atan(v_local.z, v_local.x) * 0.5 * v_size.x;
            c *= 0.9 + 0.12 * aaline(ang / 0.14, 0.2);
            float hoop = aaline(y / 0.55, 0.12);
            c = mix(c, vec3(0.10, 0.09, 0.08), hoop * 0.85);
            if (up) { c *= 0.5; }
        }
        rough = 0.95;
        th = th_surface(0.6, 10.0, 0.95, 0.5);
    } else if (kind == 2) {     // tank roof
        rough = 0.9;
        th = th_surface(0.7, 14.0, 0.2, 0.0);
    } else if (kind == 3) {     // steel
        rough = 0.6; metal = 0.4;
        th = th_surface(0.5, 20.0, 0.2, 0.0);
    } else if (kind == 4 || kind == 6) {   // condensers, cooling towers: louvred sides, fan grilles on top
        if (up) {
            vec2 q = v_local.xz * v_size.xz;
            float cell = kind == 6 ? 3.2 : min(v_size.x, v_size.z);
            vec2 g = (fract(q / cell + 0.5) - 0.5) * cell;
            float fan = 1.0 - smoothstep(cell * 0.32, cell * 0.36, length(g));
            c = mix(c * 0.9, vec3(0.12, 0.12, 0.13), fan * 0.85);
        } else {
            c *= 1.0 - 0.25 * aaline(y / (kind == 6 ? 0.25 : 0.09), 0.4);
        }
        rough = 0.6; metal = 0.15;
        th = th_surface(0.45, 14.0, 0.2, kind == 6 ? 6.0 : 9.0);
    } else if (kind == 5) {     // skylight: dark glass on a curb
        if (up) { c = vec3(0.16, 0.17, 0.18); rough = 0.25; metal = 0.2; }
        th = th_surface(0.5, 16.0, 0.3, 0.5);
    } else if (kind == 7) {     // polished steel crown, its windows in rings
        c = vec3(0.82, 0.83, 0.85);
        rough = 0.22; metal = 0.9;
        float ang = atan(v_local.z, v_local.x);
        float win = step(0.6, fract(ang * 4.0)) * step(0.25, fract(y / 3.2)) * step(fract(y / 3.2), 0.75);
        c = mix(c, vec3(0.05, 0.05, 0.06), win * 0.9);
        emit = vec3(1.0, 0.92, 0.78) * win * night * (1.5 + v_cus.y * 2.0);
        th = th_surface(0.3, 18.0, 0.3, 0.0);
    } else if (kind == 8) {     // white mast
        rough = 0.5;
        c *= 1.0 - 0.15 * aaline(y / 6.0, 0.05);
        emit = vec3(1.0, 0.25, 0.2) * step(v_size.y - 1.0, y) * night * 4.0;   // aviation light
        th = th_surface(0.4, 18.0, 0.3, 0.0);
    } else {                    // stone crown
        c *= 1.0 - 0.1 * aaline(y / 1.2, 0.08);
        if (up) { c *= 0.8; }
        emit = vec3(1.0, 0.95, 0.85) * 0.5 * night * step(0.5, y);
        th = th_surface(0.5, 18.0, 0.75, 0.5);
    }
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        TH_OUT(th);
    } else {
        ALBEDO = pow(max(c, vec3(0.0)), vec3(2.2));   // colours are written as sRGB
        ROUGHNESS = rough;
        METALLIC = metal;
        EMISSION = emit;
    }
}
"""
