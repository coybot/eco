## The ground of historic Istanbul: streets, pavements, setts, plazas, parks,
## cemeteries and the shore, drawn from data, and the far ground to the
## horizon. Replaces env_istanbul's base terrain (static build(env) -> true).
##
## WHY DRAWN. There is no open high-resolution aerial photo of Istanbul (no
## NAIP equivalent), so nothing here is a photograph except the 10 m
## Copernicus Sentinel-2 colour. The ground is painted in the shader from
## signed-distance fields baked from OpenStreetMap by
## tools/fetch_istanbul_ground.py (the Manhattan street technique):
##   field A, 1 m/px   R carriageway, G any paved way (carriageway, pavements,
##                     footways, plazas, car parks), B granite setts (stone-
##                     surfaced ways, and the old quarters' lanes), A track bed
##                     (the T1 tram, the Sirkeci line)
##   field B, 2 m/px   R green ground (parks, gardens, woods, cemeteries,
##                     pitches), G stone-slab ground (squares, mosque and
##                     school courts), B Sentinel-2 vegetation, A the shoreline
##   landcover.png     which kind of ground (base data, 2 m, nearest)
##   ground_s2.jpg     Sentinel-2 true colour, 10 m: the far ground outright,
##                     a broad tint over the drawn ground so districts differ,
##                     and what the drawn ground fades to past ~2 m a pixel
##   ground_tex/       CC0 Poly Haven textures (asphalt, setts, pavers, slabs,
##                     grass, gravel, dry ground, sand, leaf litter), each
##                     normalised to its mean so the shader sets the colour
##                     and the photo only the grain
## A signed distance edge reconstructs straight under bilinear filtering, so
## a 1 m texel still gives a kerb sharp to a few centimetres on screen.
##
## MESH. Inside sim_bounds: 1000 m tiles of 10 m posts set exactly on the
## terrain's own samples, so the mesh equals ground_height() at every post
## (between posts the two triangles of a cell and the bilinear differ by the
## cell's twist, a few cm; mesh_height() is the mesh's own surface, for the
## markings and the quays). Beyond: 2 km chunks of 50 m posts to the edge of
## the terrain, 0.3 m lower so the inner tiles win where they overlap.
## Posts in the water next to land take the land's height and the shader cuts
## the ground at the shoreline field's zero, so the land ends at the quay line,
## not on a 10 m smear down to the seabed; posts in open water are the seabed.
##
## Children: istanbul_ground_marks.gd (lane paint, zebra crossings, tram and
## rail lines, step nosings) and istanbul_ground_shore.gd (quays, rock armour,
## piers, bollards). Without _ground/ (fresh checkout, tool not run) the whole
## ground is the Sentinel-2 colour with the land cover classes; the children
## still build.
extends Node3D

const DIR := "res://assets/istanbul/"
const HI := "res://assets/istanbul/_ground/"

const STEP := 10.0
const FAR_STEP := 50.0
const FAR_CHUNK := 2000.0
const FAR_SINK := 0.3
const SEABED := -8.0
## Material layers in ground_tex (the shader's order).
const MATS := ["asphalt", "setts", "pavers", "slabs", "grass", "gravel", "dry", "sand", "leaves"]

var env: Node3D
var has_fields := false
var vram_bytes := 0
var mat_near: ShaderMaterial        # inland: no discard, so the depth prepass stays cheap
var mat_shore: ShaderMaterial       # cells near the water: cut at the shoreline
var mat_far: ShaderMaterial
var _wet := PackedByteArray()       # of the last _post_grid: 1 = a water post

## The mesh's own heights over the inner grid (posts at the terrain samples).
var _h := PackedFloat32Array()
var _hw := 0
var _hh := 0
var _h0 := Vector2.ZERO     # e, n of post (0, 0): the SOUTH-west post
var _h_wet := PackedByteArray()


static func build(e: Node3D) -> bool:
	# --ground-skip=all: the base terrain instead, for A/B timing.
	if "all" in str(IpcServer._get_launch_arg("--ground-skip", "")).split(","):
		return false
	var g = load("res://scripts/istanbul_ground.gd").new()
	g.env = e
	g.name = "ground"
	e.add_child(g)
	g._build()
	return true


func _build() -> void:
	var t0 := Time.get_ticks_msec()
	var skip := str(IpcServer._get_launch_arg("--ground-skip", "")).split(",")
	_make_materials()
	_heights()
	var nt := _inner_tiles()
	var nf := _far_ring()
	_seabed()
	print("[istanbul_ground] %d inner tiles, %d far chunks, fields %s, %.0f MB of ground textures, %d ms" %
		[nt, nf, "yes" if has_fields else "NO (run tools/fetch_istanbul_ground.py)", vram_bytes / 1048576.0,
		 Time.get_ticks_msec() - t0])
	for part in ["marks", "shore"]:
		if not part in skip:
			var n: Node = load("res://scripts/istanbul_ground_%s.gd" % part).new()
			n.set("ground", self)
			add_child(n)


# ================================================================ heights

## Posts of the inner grid: the terrain samples (e = tb.x + 5 + 10 k) covering
## sim_bounds, water posts next to land raised to the land beside them.
func _heights() -> void:
	var tb: Rect2 = env.call("wide_bounds")
	var b: Rect2 = env.call("sim_bounds")
	var c0 := int(floor((b.position.x - tb.position.x) / STEP))
	var r0 := int(floor((b.position.y - tb.position.y) / STEP))
	_h0 = Vector2(tb.position.x + (c0 + 0.5) * STEP, tb.position.y + (r0 + 0.5) * STEP)
	_hw = int(ceil(b.size.x / STEP)) + 1
	_hh = int(ceil(b.size.y / STEP)) + 1
	_h = _post_grid(_h0, STEP, _hw, _hh, 0.0)
	_h_wet = _wet


## Heights of a w x h grid of posts from p0 (SW) at `step`: the terrain on
## land, the mean of the land around a water post that touches land, else the
## seabed. Row 0 = south.
func _post_grid(p0: Vector2, step: float, w: int, h: int, sink: float) -> PackedFloat32Array:
	var g := PackedFloat32Array()
	g.resize(w * h)
	var wet := PackedByteArray()
	wet.resize(w * h)
	for j in h:
		for i in w:
			var e := p0.x + i * step
			var n := p0.y + j * step
			var k := j * w + i
			if env.call("is_water", e, n):
				wet[k] = 1
				g[k] = SEABED
			else:
				g[k] = float(env.call("ground_height", e, n)) - sink
	_wet = wet
	var out := g.duplicate()
	for j in h:
		for i in w:
			var k := j * w + i
			if wet[k] == 0:
				continue
			var s := 0.0
			var c := 0
			for dj in [-1, 0, 1]:
				for di in [-1, 0, 1]:
					var ii: int = i + di
					var jj: int = j + dj
					if ii < 0 or jj < 0 or ii >= w or jj >= h:
						continue
					var kk := jj * w + ii
					if wet[kk] == 0:
						s += g[kk]
						c += 1
			if c > 0:
				out[k] = s / c
			elif i > 0 and j > 0 and i < w - 1 and j < h - 1:
				out[k] = SEABED
			else:
				# Off the grid's edge we cannot see the land beside it: ask
				# the terrain one post further out.
				out[k] = _edge_guess(p0.x + i * step, p0.y + j * step, step, sink)
	return out


func _edge_guess(e: float, n: float, step: float, sink: float) -> float:
	var s := 0.0
	var c := 0
	for d in [Vector2(step, 0), Vector2(-step, 0), Vector2(0, step), Vector2(0, -step)]:
		if not env.call("is_water", e + d.x, n + d.y):
			s += float(env.call("ground_height", e + d.x, n + d.y)) - sink
			c += 1
	return s / c if c > 0 else SEABED


## The drawn ground's height at (e, n): the inner mesh's own triangles
## (cells split b-c, as _tile_mesh winds them), else ground_height().
func mesh_height(e: float, n: float) -> float:
	var fx := (e - _h0.x) / STEP
	var fy := (n - _h0.y) / STEP
	var i := int(floor(fx))
	var j := int(floor(fy))
	if i < 0 or j < 0 or i >= _hw - 1 or j >= _hh - 1:
		return float(env.call("ground_height", e, n))
	fx -= i
	fy -= j
	var a := _h[j * _hw + i]
	var b := _h[j * _hw + i + 1]
	var c := _h[(j + 1) * _hw + i]
	var d := _h[(j + 1) * _hw + i + 1]
	if fx + fy <= 1.0:
		return a + (b - a) * fx + (c - a) * fy
	return d + (c - d) * (1.0 - fx) + (b - d) * (1.0 - fy)


# ================================================================== meshes

## 1000 m tiles of the inner grid (100 x 100 cells each), skipping cells
## wholly in open water.
func _inner_tiles() -> int:
	var root := Node3D.new()
	root.name = "inner"
	add_child(root)
	var per := 100
	var n := 0
	var tj := 0
	while tj < _hh - 1:
		var ti := 0
		while ti < _hw - 1:
			var mi := _grid_mesh(_h, _h_wet, _hw, _h0, STEP, ti, tj, mini(per, _hw - 1 - ti), mini(per, _hh - 1 - tj),
				Rect2(), mat_near, mat_shore)
			if mi != null:
				mi.name = "g_%d_%d" % [ti / per, tj / per]
				root.add_child(mi)
				n += 1
			ti += per
		tj += per
	return n


## 2 km chunks of 50 m posts over the terrain's whole reach, cells inside the
## inner grid left out.
func _far_ring() -> int:
	var tb: Rect2 = env.call("wide_bounds")
	var root := Node3D.new()
	root.name = "far"
	add_child(root)
	var inner := Rect2(_h0, Vector2((_hw - 1) * STEP, (_hh - 1) * STEP))
	var w := int(round(tb.size.x / FAR_STEP)) + 1
	var h := int(round(tb.size.y / FAR_STEP)) + 1
	var g := _post_grid(tb.position, FAR_STEP, w, h, FAR_SINK)
	var wet := _wet
	var per := int(FAR_CHUNK / FAR_STEP)
	var n := 0
	var tj := 0
	while tj < h - 1:
		var ti := 0
		while ti < w - 1:
			var mi := _grid_mesh(g, wet, w, tb.position, FAR_STEP, ti, tj, mini(per, w - 1 - ti), mini(per, h - 1 - tj),
				inner, mat_far, mat_far)
			if mi != null:
				mi.name = "f_%d_%d" % [ti / per, tj / per]
				root.add_child(mi)
				n += 1
			ti += per
		tj += per
	return n


## Cells [i0, i0 + ci) x [j0, j0 + cj) of a post grid, as one mesh of two
## surfaces: cells within a post of the water (drawn with `shore_mat`, which
## cuts at the shoreline) and the rest (`mat`, no discard). Cells whose four
## posts are all seabed are left out (the seabed plane is under them), and so
## are cells wholly inside `hole`.
func _grid_mesh(g: PackedFloat32Array, wet: PackedByteArray, w: int, p0: Vector2, step: float, i0: int, j0: int,
		ci: int, cj: int, hole: Rect2, mat: Material, shore_mat: Material) -> MeshInstance3D:
	var pos := PackedVector3Array()
	var nrm := PackedVector3Array()
	pos.resize((ci + 1) * (cj + 1))
	nrm.resize(pos.size())
	var hh := g.size() / w
	for j in cj + 1:
		for i in ci + 1:
			var gi := i0 + i
			var gj := j0 + j
			var y := g[gj * w + gi]
			pos[j * (ci + 1) + i] = Vector3(p0.x + gi * step, y, -(p0.y + gj * step))
			var xl := g[gj * w + maxi(gi - 1, 0)]
			var xr := g[gj * w + mini(gi + 1, w - 1)]
			var ys := g[maxi(gj - 1, 0) * w + gi]
			var yn := g[mini(gj + 1, hh - 1) * w + gi]
			# Godot (x, y, -n): d/dx = east slope, d/dz = -north slope.
			nrm[j * (ci + 1) + i] = Vector3(-(xr - xl), 2.0 * step, (yn - ys)).normalized()
	var dry := PackedInt32Array()
	var cut := PackedInt32Array()
	for j in cj:
		for i in ci:
			var a := j * (ci + 1) + i
			var b := a + 1
			var c := a + ci + 1
			var d := c + 1
			if pos[a].y <= SEABED + 0.01 and pos[b].y <= SEABED + 0.01 \
					and pos[c].y <= SEABED + 0.01 and pos[d].y <= SEABED + 0.01:
				continue
			if hole.size.x > 0.0:
				var e0 := p0.x + (i0 + i) * step
				var n0 := p0.y + (j0 + j) * step
				if e0 >= hole.position.x and n0 >= hole.position.y \
						and e0 + step <= hole.end.x and n0 + step <= hole.end.y:
					continue
			# Any water post in the 4 x 4 posts around the cell: the
			# shoreline may cross it.
			var near := false
			for dj in range(-1, 3):
				for di in range(-1, 3):
					var gi := clampi(i0 + i + di, 0, w - 1)
					var gj := clampi(j0 + j + dj, 0, hh - 1)
					if wet[gj * w + gi] != 0:
						near = true
			# Clockwise seen from above (Godot's front): a, c, b and b, c, d.
			(cut if near else dry).append_array(PackedInt32Array([a, c, b, b, c, d]))
	if dry.is_empty() and cut.is_empty():
		return null
	var m := ArrayMesh.new()
	for part in [[dry, mat], [cut, shore_mat]]:
		if (part[0] as PackedInt32Array).is_empty():
			continue
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = pos
		arr[Mesh.ARRAY_NORMAL] = nrm
		arr[Mesh.ARRAY_INDEX] = part[0]
		m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
		m.surface_set_material(m.get_surface_count() - 1, part[1])
	var mi := MeshInstance3D.new()
	mi.mesh = m
	# The hills are 0-80 m and the sun high: their shadows are not worth a
	# draw per tile in every cascade. Buildings still shadow the ground.
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	return mi


## A seabed under the whole scene, so the sea has a floor to show through.
func _seabed() -> void:
	var mi := MeshInstance3D.new()
	mi.name = "seabed"
	var pm := PlaneMesh.new()
	pm.size = Vector2(60000.0, 60000.0)
	mi.mesh = pm
	mi.position = Vector3(0.0, SEABED - 0.05, 0.0)
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(0.16, 0.15, 0.12)
	m.roughness = 1.0
	mi.material_override = m
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)


# =============================================================== materials

func _make_materials() -> void:
	var sh := Shader.new()
	sh.code = SHADER.replace("//CUT", "")
	mat_near = ShaderMaterial.new()
	mat_near.shader = sh
	var b: Rect2 = env.call("sim_bounds")
	# Sentinel-2 colour and the city's mean land colour (the tint's 1.0).
	var s2m: Dictionary = _json(DIR + "ground_s2.json")
	var s2 := _image(DIR + "ground_s2.jpg")
	var mean := Color(0.5, 0.45, 0.4)
	if s2 != null and s2m.has("bounds_enu"):
		var r := _rect(s2m["bounds_enu"])
		mean = _land_mean(s2, r, b)
		s2.generate_mipmaps()
		s2.compress(Image.COMPRESS_S3TC)
		vram_bytes += s2.get_data_size()
		mat_near.set_shader_parameter("s2", ImageTexture.create_from_image(s2))
		mat_near.set_shader_parameter("s2_rect", Vector4(r.position.x, r.position.y, r.size.x, r.size.y))
		mat_near.set_shader_parameter("has_s2", 1.0)
	mat_near.set_shader_parameter("s2_mean", Vector3(mean.r, mean.g, mean.b))
	# Water mask (the far ground's shoreline, and the near one's without fields).
	var wm: Dictionary = env.call("water_mask")
	if wm.get("tex") != null:
		var wr: Rect2 = wm["bounds"]
		mat_near.set_shader_parameter("water", wm["tex"])
		mat_near.set_shader_parameter("w_rect", Vector4(wr.position.x, wr.position.y, wr.size.x, wr.size.y))
	# Land cover classes, nearest.
	var lcm: Dictionary = _json(DIR + "landcover.json")
	var lc := _image(DIR + "landcover.png")
	if lc != null and lcm.has("bounds_enu"):
		lc.convert(Image.FORMAT_R8)
		vram_bytes += lc.get_data_size()
		var r := _rect(lcm["bounds_enu"])
		mat_near.set_shader_parameter("lc", ImageTexture.create_from_image(lc))
		mat_near.set_shader_parameter("lc_rect", Vector4(r.position.x, r.position.y, r.size.x, r.size.y))
	_load_textures()
	_load_fields()
	mat_near.set_shader_parameter("core", Vector4(b.position.x, b.position.y, b.end.x, b.end.y))
	var cut := Shader.new()
	cut.code = SHADER.replace("//CUT", "#define CUT")
	mat_shore = mat_near.duplicate() as ShaderMaterial
	mat_shore.shader = cut
	mat_far = mat_shore.duplicate() as ShaderMaterial
	mat_far.set_shader_parameter("far_mode", 1.0)


## The CC0 ground textures as one array, each with its mean (linear) so the
## shader can take the grain and set the colour itself.
func _load_textures() -> void:
	var meta: Dictionary = _json(DIR + "ground_tex.json")
	var imgs: Array[Image] = []
	var sizes := PackedFloat32Array()
	var means := PackedVector3Array()
	for nm in MATS:
		var img := _image(DIR + "ground_tex/%s.jpg" % nm)
		if img == null:
			imgs.clear()
			break
		img.convert(Image.FORMAT_RGB8)
		var acc := Vector3.ZERO
		for y in range(0, img.get_height(), 8):
			for x in range(0, img.get_width(), 8):
				var c := img.get_pixel(x, y).srgb_to_linear()
				acc += Vector3(c.r, c.g, c.b)
		acc /= float((img.get_height() / 8) * (img.get_width() / 8))
		means.append(acc)
		img.generate_mipmaps()
		img.compress(Image.COMPRESS_S3TC)
		vram_bytes += img.get_data_size()
		imgs.append(img)
		var t: Dictionary = meta.get("textures", {}).get(nm, {})
		sizes.append(float(t.get("size_m", 2.0)))
	if imgs.size() != MATS.size():
		push_warning("[istanbul_ground] ground_tex/ incomplete: procedural ground only")
		return
	var arr := Texture2DArray.new()
	arr.create_from_images(imgs)
	mat_near.set_shader_parameter("mats", arr)
	mat_near.set_shader_parameter("mat_size", sizes)
	mat_near.set_shader_parameter("mat_mean", means)
	mat_near.set_shader_parameter("has_mats", 1.0)


func _load_fields() -> void:
	var doc: Dictionary = _json(HI + "tiles.json")
	if doc.is_empty():
		return
	var grid: Array = doc["grid"]
	var origin: Array = doc["origin"]
	var idx := Image.create(int(grid[0]), int(grid[1]), false, Image.FORMAT_R8)
	var fa: Array[Image] = []
	var fb: Array[Image] = []
	for t: Array in doc["tiles"]:
		var i := int(t[0])
		var j := int(t[1])
		var a := _compressed(HI + "fa_%d_%d.png" % [i, j])
		var b := _compressed(HI + "fb_%d_%d.png" % [i, j])
		if a == null or b == null:
			continue
		idx.set_pixel(i, j, Color8(fa.size() + 1, 0, 0))
		fa.append(a)
		fb.append(b)
	if fa.is_empty():
		return
	var aa := Texture2DArray.new()
	aa.create_from_images(fa)
	var ba := Texture2DArray.new()
	ba.create_from_images(fb)
	mat_near.set_shader_parameter("fa", aa)
	mat_near.set_shader_parameter("fb", ba)
	mat_near.set_shader_parameter("tiles", ImageTexture.create_from_image(idx))
	mat_near.set_shader_parameter("tile_geo", Vector4(float(origin[0]), float(origin[1]), float(grid[0]), float(grid[1])))
	mat_near.set_shader_parameter("tile_m", float(doc.get("tile_m", 1000.0)))
	mat_near.set_shader_parameter("has_fields", 1.0)
	has_fields = true


## A field PNG mipmapped and BC7-compressed, cached as .dds beside it (as
## manhattan_ground does: compressing ~150 fields takes most of a minute,
## reading the .dds is a copy). BC7 keeps the smooth channels to ~1 step.
func _compressed(path: String) -> Image:
	if not FileAccess.file_exists(path):
		return null
	var dds := path.get_basename() + ".dds"
	var img: Image = null
	if FileAccess.file_exists(dds) and FileAccess.get_modified_time(dds) >= FileAccess.get_modified_time(path):
		img = Image.new()
		if img.load_dds_from_buffer(FileAccess.get_file_as_bytes(dds)) != OK:
			img = null
	if img == null:
		img = Image.new()
		if img.load_png_from_buffer(FileAccess.get_file_as_bytes(path)) != OK:
			return null
		img.convert(Image.FORMAT_RGBA8)
		img.generate_mipmaps()
		img.compress(Image.COMPRESS_BPTC)
		img.save_dds(ProjectSettings.globalize_path(dds))
	vram_bytes += img.get_data_size()
	return img


## Mean linear colour of the Sentinel image's land inside rect `b`.
func _land_mean(img: Image, r: Rect2, b: Rect2) -> Color:
	var acc := Vector3.ZERO
	var n := 0
	var step := 60.0
	var e := b.position.x
	while e < b.end.x:
		var nn := b.position.y
		while nn < b.end.y:
			if not env.call("is_water", e, nn):
				var x := int((e - r.position.x) / r.size.x * img.get_width())
				var y := int((r.end.y - nn) / r.size.y * img.get_height())
				var c := img.get_pixel(clampi(x, 0, img.get_width() - 1), clampi(y, 0, img.get_height() - 1)).srgb_to_linear()
				acc += Vector3(c.r, c.g, c.b)
				n += 1
			nn += step
		e += step
	acc /= float(maxi(n, 1))
	return Color(acc.x, acc.y, acc.z)


func _image(path: String) -> Image:
	if not FileAccess.file_exists(path):
		return null
	var img := Image.new()
	var bytes := FileAccess.get_file_as_bytes(path)
	var err := img.load_jpg_from_buffer(bytes) if path.ends_with(".jpg") else img.load_png_from_buffer(bytes)
	return img if err == OK else null


func _json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var d = JSON.parse_string(FileAccess.get_file_as_string(path))
	return d if d is Dictionary else {}


static func _rect(b: Array) -> Rect2:
	return Rect2(float(b[0]), float(b[1]), float(b[2]) - float(b[0]), float(b[3]) - float(b[1]))


# ================================================================== shader

const SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
//CUT
#include "res://shaders/thermal.gdshaderinc"

uniform sampler2DArray fa : filter_linear_mipmap, repeat_disable;
uniform sampler2DArray fb : filter_linear_mipmap, repeat_disable;
uniform sampler2D tiles : filter_nearest, repeat_disable;
uniform vec4 tile_geo;            // origin e, n; tiles across, up
uniform float tile_m = 1000.0;
uniform float has_fields = 0.0;
uniform sampler2D lc : filter_nearest, repeat_disable;
uniform vec4 lc_rect;
uniform sampler2D s2 : source_color, filter_linear_mipmap, repeat_disable;
uniform vec4 s2_rect;
uniform vec3 s2_mean = vec3(0.2);
uniform float has_s2 = 0.0;
uniform sampler2D water : filter_linear, repeat_disable;
uniform vec4 w_rect;
uniform sampler2DArray mats : source_color, filter_linear_mipmap_anisotropic, repeat_enable;
uniform float mat_size[9];
uniform vec3 mat_mean[9];
uniform float has_mats = 0.0;
uniform vec4 core;                // the detailed scene, e0 n0 e1 n1
uniform float far_mode = 0.0;

varying vec3 w;

void vertex() {
    w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

float h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}
// Fade a detail of `size` metres out as it nears a pixel (fw m/px).
float lod(float size, float fw) { return 1.0 - smoothstep(0.25 * size, 0.7 * size, fw); }

// A CC0 texture's grain at world p, recoloured to `col`: the texture over its
// own mean, so the shader sets the colour. A second, larger and turned
// sample breaks the tiling up.
vec3 grain(int k, vec2 p, vec3 col, float amt) {
    if (has_mats < 0.5) { return col * (0.9 + 0.2 * vn(p * 1.3)); }
    float s = mat_size[k];
    vec3 a = texture(mats, vec3(p / s, float(k))).rgb;
    vec2 q = mat2(vec2(0.8, 0.6), vec2(-0.6, 0.8)) * p / (s * 2.7);
    vec3 b = texture(mats, vec3(q, float(k))).rgb;
    vec3 t = mix(a, b, smoothstep(0.35, 0.65, vn(p / (s * 3.1)))) / max(mat_mean[k], vec3(0.01));
    return col * mix(vec3(1.0), t, amt);
}

vec3 s2_at(vec2 en, float l) {
    vec2 uv = (en - s2_rect.xy) / s2_rect.zw;
    return textureLod(s2, vec2(uv.x, 1.0 - uv.y), l).rgb;
}

// --- surfaces --------------------------------------------------------------
vec3 asphalt(vec2 p, float fw, float rd) {
    vec3 c = grain(0, p, vec3(0.085, 0.085, 0.09), 0.7);
    float b = vn(p / 23.0) * 0.6 + vn(p / 7.0) * 0.4;
    c *= 0.8 + 0.45 * b;
    // Patches over utility cuts and potholes.
    vec2 cell = floor(p / vec2(5.0, 3.0));
    float hc = h2(cell);
    vec2 f = fract(p / vec2(5.0, 3.0));
    float pat = step(0.93, hc) * step(0.15, f.x) * step(f.x, 0.8) * step(0.2, f.y) * step(f.y, 0.9);
    c = mix(c, c * (hc > 0.97 ? 0.75 : 1.2), pat * lod(2.0, fw));
    // Wheel-polished lanes are darker; dust gathers by the kerb.
    c *= mix(1.25, 1.0, smoothstep(0.0, 0.9, rd));
    return c;
}

// Granite setts (Arnavut kaldirimi): grey with a warm cast, dark joints.
vec3 setts(vec2 p, float fw) {
    vec3 c = grain(1, p, vec3(0.20, 0.19, 0.175), 1.0);
    c *= 0.85 + 0.3 * vn(p / 9.0);
    return c;
}

// Interlocking concrete pavers, the city's pavement: grey with runs of
// faded red.
vec3 pavers(vec2 p, float fw) {
    float red = smoothstep(0.8, 0.86, vn(p / 37.0)) * 0.7;
    vec3 col = mix(vec3(0.27, 0.265, 0.25), vec3(0.30, 0.21, 0.18), red);
    vec3 c = grain(2, p, col, 0.9);
    c *= 0.88 + 0.24 * vn(p / 6.0);
    return c;
}

// Pale stone slabs: Sultanahmet's squares, the Hippodrome, mosque courts.
vec3 slabs(vec2 p, float fw) {
    vec3 c = grain(3, p, vec3(0.42, 0.40, 0.37), 0.75);
    c *= 0.9 + 0.18 * vn(p / 11.0);
    return c;
}

vec3 grass(vec2 p, float fw, float veg) {
    // Watered lawns green, the rest summer-dry: Sentinel's vegetation says which.
    vec3 col = mix(vec3(0.17, 0.15, 0.07), vec3(0.075, 0.14, 0.04), smoothstep(0.25, 0.75, veg));
    vec3 c = grain(4, p, col, 0.9);
    c *= 0.82 + 0.36 * vn(p / 13.0);
    return c;
}

vec3 dry(vec2 p, float fw) {
    return grain(6, p, vec3(0.24, 0.20, 0.155), 0.9) * (0.85 + 0.3 * vn(p / 17.0));
}

vec3 leaves(vec2 p, float fw) {
    return grain(8, p, vec3(0.07, 0.075, 0.04), 0.9) * (0.8 + 0.4 * vn(p / 9.0));
}

vec3 gravel(vec2 p, float fw) {
    return grain(5, p, vec3(0.24, 0.225, 0.20), 0.9);
}

vec3 sand(vec2 p, float fw) {
    return grain(7, p, vec3(0.42, 0.37, 0.29), 0.8);
}

// An Ottoman cemetery from above: dark ivy and cypress-shaded earth, rows of
// upright white marble stones (a tall headstone, a low footstone).
vec3 cemetery(vec2 p, float fw, float veg) {
    vec3 c = mix(leaves(p, fw), grass(p, fw, veg) * 0.7, 0.4);
    vec2 q = mat2(vec2(0.87, 0.5), vec2(-0.5, 0.87)) * p;
    vec2 cell = floor(q / vec2(1.3, 2.2));
    vec2 f = fract(q / vec2(1.3, 2.2)) * vec2(1.3, 2.2);
    float has = step(0.3, h2(cell));
    float head = step(0.4, f.x) * step(f.x, 0.85) * step(0.25, f.y) * step(f.y, 0.42);
    float foot = step(0.5, f.x) * step(f.x, 0.75) * step(1.75, f.y) * step(f.y, 1.86);
    float shade = step(0.4, f.x) * step(f.x, 0.85) * step(0.42, f.y) * step(f.y, 0.75);
    vec3 marble = vec3(0.52, 0.51, 0.47) * (0.8 + 0.3 * h2(cell + 3.1));
    float l = lod(0.3, fw);
    c = mix(c, c * 0.55, has * shade * l);
    c = mix(c, marble, has * max(head, foot) * l);
    // Far off: the stones' share of the ground, as an average.
    c = mix(c, mix(c, marble, 0.1 * 0.7), 1.0 - l);
    return c;
}

vec3 turf(vec2 p, float fw) {
    vec3 c = vec3(0.07, 0.17, 0.05) * (0.92 + 0.16 * step(0.5, fract(p.x / 6.0)));
    return grain(4, p, c, 0.25);
}

void fragment() {
    vec2 en = vec2(w.x, -w.z);
    float fw = max(length(fwidth(en)), 0.003);
    float aa = fw * 0.75;
    float dist = length(CAMERA_POSITION_WORLD - w);
    vec3 sat = has_s2 > 0.5 ? s2_at(en, 0.0) : s2_mean;
    vec3 c;
    float rough = 0.95;

    // Which tile of the fields, if any.
    float layer = -1.0;
    vec2 tuv = vec2(0.0);
    if (has_fields > 0.5 && far_mode < 0.5) {
        vec2 tg = (en - tile_geo.xy) / tile_m;
        if (tg.x >= 0.0 && tg.y >= 0.0 && tg.x < tile_geo.z && tg.y < tile_geo.w) {
            layer = texelFetch(tiles, ivec2(floor(tg)), 0).r * 255.0 - 1.0;
            tuv = vec2(fract(tg.x), 1.0 - fract(tg.y));
        }
    }
    vec4 A = vec4(0.0);
    vec4 B = vec4(0.0);
    float shore;
    if (layer > -0.5) {
        A = (texture(fa, vec3(tuv, layer)) * 255.0 - 128.0) / 8.0;
        B = texture(fb, vec3(tuv, layer)) * 255.0;
        shore = (B.a - 128.0) / 8.0;
    } else {
        vec2 wuv = (en - w_rect.xy) / w_rect.zw;
        shore = (0.5 - texture(water, vec2(wuv.x, 1.0 - wuv.y)).r) * 8.0;
    }
    // The land ends at the shoreline: beyond it is the quay's face or the
    // water. Below the sea (the seabed) the ground stays.
#ifdef CUT
    if (shore < 0.0 && w.y > -0.6) { discard; }
#endif

    if (layer < -0.5) {
        // FAR GROUND (or no fields): Sentinel-2 as it is, with the grain of
        // what it shows (grass where it is green, worn paving elsewhere) so
        // its 10 m pixels do not read as a blur close to.
        vec3 g = sat * 0.85;
        float l = lod(4.0, fw);
        if (l > 0.01) {
            float gr = clamp((sat.g - max(sat.r, sat.b)) * 14.0, 0.0, 1.0);
            vec3 d = gr > 0.5 ? grain(4, en, vec3(1.0), 0.8) : grain(2, en, vec3(1.0), 0.6);
            g *= mix(vec3(1.0), d * (0.88 + 0.24 * vn(en / 9.0)), l);
        }
        c = g;
    } else {
        float rd = A.r;     // carriageway
        float pd = A.g;     // any paved way
        float sd = A.b;     // setts
        float td = A.a;     // track bed
        float gd = (B.r - 128.0) / 8.0;
        float veg = B.b / 255.0;
        vec2 lu = (en - lc_rect.xy) / lc_rect.zw;
        int cls = 0;
        if (lu.x > 0.0 && lu.y > 0.0 && lu.x < 1.0 && lu.y < 1.0) {
            ivec2 px = ivec2(vec2(lu.x, 1.0 - lu.y) * vec2(textureSize(lc, 0)));
            cls = int(texelFetch(lc, px, 0).r * 255.0 + 0.5);
        }
        // Coverage of each surface; only the surfaces a pixel shows are
        // evaluated (each is a few texture reads), the rest branch away.
        float green = smoothstep(-aa, aa, gd);
        float road = smoothstep(-aa, aa, rd);
        float paved = max(smoothstep(-aa, aa, pd), road);
        float stone = smoothstep(-aa, aa, sd) * paved;
        float bed = smoothstep(-aa, aa, td);
        // Stone-slab ground (squares, mosque and school courts), its own
        // smooth edge rather than the 2 m land cover's steps.
        float slab = smoothstep(-aa, aa, (B.g - 128.0) / 8.0);
        vec3 g = vec3(0.0);
        if (paved < 0.999) {
            // Ground between the ways: back yards and light wells (worn
            // concrete and pavers, packed earth in patches), scraps of garden
            // where Sentinel sees green.
            if (cls == 5) { g = asphalt(en, fw, 2.0) * 1.3; }
            else if (cls == 7) { g = sand(en, fw); }
            else if (cls == 9) { g = gravel(en, fw); }
            else if (cls == 10) { g = mix(dry(en, fw), grain(2, en, vec3(0.25, 0.24, 0.225), 0.6), 0.5); }
            else if (cls == 12) { g = mix(sand(en, fw), vec3(0.28, 0.12, 0.08), step(0.5, vn(en / 3.0))); }
            else {
                g = grain(2, en, vec3(0.25, 0.24, 0.225), 0.6) * (0.85 + 0.3 * vn(en / 5.0));
                float earth = smoothstep(0.7, 0.9, vn(en / 14.0)) * 0.5;
                if (earth > 0.01) { g = mix(g, dry(en, fw) * vec3(0.85, 0.85, 0.88), earth); }
            }
            if (slab > 0.001) { g = mix(g, slabs(en, fw), slab); }
            // Patches of planting where the satellite sees green: a ragged
            // edge, not the 10 m pixel's blur.
            float gv = smoothstep(0.42, 0.52, veg + (vn(en / 3.7) - 0.5) * 0.35);
            if (green < 0.999 && gv > 0.01 && cls != 5 && cls != 9) { g = mix(g, grass(en, fw, veg) * 0.85, gv * (1.0 - green)); }
            if (green > 0.001) {
                vec3 gr;
                if (cls == 3) { gr = cemetery(en, fw, veg); }
                else if (cls == 2) { gr = mix(leaves(en, fw), grass(en, fw, veg) * 0.8, 0.3); }
                else if (cls == 6) { gr = turf(en, fw); }
                else { gr = grass(en, fw, max(veg, 0.45)); }
                g = mix(g, gr, green);
            }
        }
        if (paved > 0.001) {
            vec3 way = vec3(0.0);
            if (road < 0.999 && stone < 0.999) {
                vec3 walk;
                if (cls == 5) { walk = asphalt(en, fw, 2.0) * 1.25; }
                else if (green > 0.5) { walk = grain(5, en, vec3(0.30, 0.28, 0.245), 0.9); }
                else { walk = pavers(en, fw); }
                if (slab > 0.001) { walk = mix(walk, slabs(en, fw), slab); }
                // A kerb where a pavement meets the carriageway: 0.15 m of
                // pale granite.
                float kerb = (1.0 - smoothstep(0.15, 0.15 + aa + 0.02, -rd)) * (1.0 - road) * step(rd + 0.8, pd);
                way = mix(walk, vec3(0.40, 0.39, 0.37), kerb * lod(0.15, fw));
            }
            if (road > 0.001 && stone < 0.999) {
                // Asphalt, its edge in the kerb's shadow.
                vec3 carr = asphalt(en, fw, rd) * mix(0.7, 1.0, smoothstep(0.0, 0.15 + aa, rd) + (1.0 - lod(0.2, fw)));
                way = mix(way, carr, road);
            }
            if (stone > 0.001) { way = mix(way, setts(en, fw), stone); }
            g = mix(g, way, paved);
        }
        if (bed > 0.001) {
            // Track bed: in the street (T1) grey concrete between the rails;
            // elsewhere (the Sirkeci line) ballast.
            vec3 b2 = paved > 0.5 ? grain(3, en, vec3(0.2, 0.195, 0.19), 0.6)
                                  : grain(5, en, vec3(0.16, 0.145, 0.13), 1.0);
            g = mix(g, b2, bed);
        }
        rough = mix(0.97, 0.88, paved * (1.0 - stone));
        // Districts are not one colour: a broad Sentinel tint (160 m), and
        // past ~2 m a pixel the satellite's own 10 m colour takes over, as
        // the street detail would only alias.
        if (has_s2 > 0.5) {
            vec3 broad = s2_at(en, 4.0);
            vec3 tint = clamp(broad / max(s2_mean, vec3(0.01)), vec3(0.8), vec3(1.25));
            g *= mix(vec3(1.0), tint, 0.25);
            float k = smoothstep(4.0, 14.0, fw) * 0.45;
            // ... and at the edge of the detailed scene, so it joins the far ground.
            vec2 ed = min(en - core.xy, core.zw - en);
            k = max(k, 1.0 - smoothstep(0.0, 200.0, min(ed.x, ed.y)));
            g = mix(g, sat * 0.85, k);
        }
        c = g;
    }
    ALBEDO = c;
    ROUGHNESS = rough;
    SPECULAR = 0.3;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_ground(clamp(c * 2.2, 0.0, 1.0));
        th = th_air(th, dist);
        TH_OUT(th);
    }
}
"""
