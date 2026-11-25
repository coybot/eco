## Central Park: its real relief, its lakes and its landmarks, for the
## photoreal Manhattan scene. Created by env_manhattan_photo.central_park().
##
## DATA (tools/fetch_central_park.py, all in the dataset frame):
##   central_park.json         outline, lakes (outline + surface level),
##                             landmarks, drives, bridges, ballfields
##   central_park_height.zi16   USGS 3DEP bare-earth relief RELATIVE TO STREET
##                             LEVEL, 2 m, 0 at the walls (committed)
##   central_park_fields.png   2 m: inside-park, water distance, rock, lawn
##                             kind (committed)
##   _park/photo.jpg           NAIP at 0.5 m (gitignored; ortho.jpg if absent)
##   _park/cover.png           1 m: paved ways, water, rock, lawn (gitignored)
##
## WHY RELATIVE TO STREET LEVEL. The scene's streets are all y = 0, but real
## Manhattan rises 5-35 m across the park's length. Taking absolute DEM heights
## would stand the park on a 30 m plinth at 90th St. Instead each point is its
## elevation minus a membrane through the perimeter avenues' own elevation
## (Laplace-solved inward), ramped to exactly 0 over the last 14 m inside the
## wall. What is left is what a person in the park sees: the Lake 9 m down in
## its hollow, the Reservoir's embankment 8 m up, Vista Rock, the Great Hill.
##
## THE CUT. The city ground (one plane, or the NAIP tiles) is flat at street
## level and would hide everything of the park below it. The park's outline is
## published as two global shader parameters (park_hole_mask, park_hole_rect)
## and every flat ground surface crossing the park discards inside it; the
## terrain discards outside it with the same test on the same texture, so the
## two meet along the wall with neither a gap nor an overlap.
## See _punch_ground() for how other scripts' ground materials get the test.
extends Node3D

const DIR := "res://assets/manhattan/"
const Landmarks = preload("res://scripts/central_park_landmarks.gd")

## Terrain LOD. Chunks of CHUNK_CELLS height samples (2 m) square-ish; each has
## three meshes on its own visibility ranges. Ranges are measured to the
## chunk's AABB, so all three tiers of one chunk switch together and only the
## seams between chunks can differ: skirts hide those.
const CHUNK_CELLS := Vector2i(232, 263)
const LODS := [[1, 0.0, 450.0], [3, 450.0, 2200.0], [8, 2200.0, 0.0]]
const SKIRT := 4.0

var env: Node3D
var ok := false
var doc: Dictionary = {}
var res := 2.0
var W := 0
var H := 0
## Raster rect in SIM coordinates: (e0, n0) south-west corner, size.
var rect := Rect2()
var heights := PackedFloat32Array()
var fields: Image = null
## [{name, level, polys: Array[PackedVector2Array] (outer then inner), box: Rect2}]
var lakes: Array = []
var _hole_tex: ImageTexture = null
var _punched := {}


## Load the baked data. False (and the park stays the city's flat ground)
## without it.
func load_data(e: Node3D) -> bool:
	env = e
	if not FileAccess.file_exists(DIR + "central_park.json"):
		return false
	doc = JSON.parse_string(FileAccess.get_file_as_string(DIR + "central_park.json"))
	if doc == null or doc.is_empty():
		return false
	var off: Vector2 = env.call("data_offset")
	var r: Array = doc["rect"]
	rect = Rect2(Vector2(r[0], r[1]) - off, Vector2(float(r[2]) - float(r[0]), float(r[3]) - float(r[1])))
	var hd: Dictionary = doc["height"]
	res = float(hd["res_m"])
	W = int(hd["size_px"][0])
	H = int(hd["size_px"][1])
	var raw := FileAccess.get_file_as_bytes(DIR + str(hd["file"]))
	var pb := raw.decompress(W * H * 2, FileAccess.COMPRESSION_DEFLATE)
	if pb.size() != W * H * 2:
		push_warning("[central_park] height file is %d bytes, expected %d" % [pb.size(), W * H * 2])
		return false
	heights.resize(W * H)
	for i in W * H:
		heights[i] = float(pb.decode_s16(i * 2)) * 0.01
	var fpath := DIR + str(doc["fields"]["file"])
	fields = Image.load_from_file(fpath)
	if fields == null:
		return false
	fields.convert(Image.FORMAT_RGBA8)
	for l: Dictionary in doc.get("lakes", []):
		var polys: Array[PackedVector2Array] = []
		var box := Rect2()
		for rr: Dictionary in l["rings"]:
			var pts := PackedVector2Array()
			for p: Array in rr["pts"]:
				pts.append(Vector2(p[0], p[1]) - off)
			if rr["role"] == "outer":
				polys.push_front(pts)
				var b := _bbox(pts)
				box = b if box.size == Vector2.ZERO else box.merge(b)
			else:
				polys.append(pts)
		lakes.append({"name": l["name"], "label": l.get("label", ""), "level": float(l["level"]),
			"polys": polys, "box": box, "n_outer": l["rings"].filter(func(x): return x["role"] == "outer").size()})
	ok = true
	return true


static func _bbox(pts: PackedVector2Array) -> Rect2:
	var b := Rect2(pts[0], Vector2.ZERO)
	for p in pts:
		b = b.expand(p)
	return b


# ---------------------------------------------------------------- queries
## Inside the park wall (the line the ground is cut at).
func covers(e: float, n: float) -> bool:
	return ok and rect.has_point(Vector2(e, n)) and _field(e, n, 0) > 0.5


## Terrain height relative to street level (bilinear over the 2 m grid).
func terrain_at(e: float, n: float) -> float:
	var fx := (e - rect.position.x) / res - 0.5
	var fy := (rect.end.y - n) / res - 0.5
	var x0 := clampi(int(floor(fx)), 0, W - 2)
	var y0 := clampi(int(floor(fy)), 0, H - 2)
	var tx := clampf(fx - x0, 0.0, 1.0)
	var ty := clampf(fy - y0, 0.0, 1.0)
	var i := y0 * W + x0
	var a := lerpf(heights[i], heights[i + 1], tx)
	var b := lerpf(heights[i + W], heights[i + W + 1], tx)
	return lerpf(a, b, ty)


## The lake at (e, n), or {} on dry ground.
func lake_at(e: float, n: float) -> Dictionary:
	var p := Vector2(e, n)
	for l: Dictionary in lakes:
		if not (l["box"] as Rect2).has_point(p):
			continue
		var polys: Array = l["polys"]
		var no := int(l["n_outer"])
		var inside := false
		for k in no:
			if Geometry2D.is_point_in_polygon(p, polys[k]):
				inside = true
		for k in range(no, polys.size()):
			if Geometry2D.is_point_in_polygon(p, polys[k]):
				inside = false
		if inside:
			return l
	return {}


func water_at(e: float, n: float) -> bool:
	return not lake_at(e, n).is_empty()


## Surface height: the lake's level on water, the terrain elsewhere.
func ground_at(e: float, n: float) -> float:
	var l := lake_at(e, n)
	if not l.is_empty():
		return float(l["level"])
	return terrain_at(e, n)


func _field(e: float, n: float, ch: int) -> float:
	var x := clampi(int((e - rect.position.x) / res), 0, W - 1)
	var y := clampi(int((rect.end.y - n) / res), 0, H - 1)
	return fields.get_pixel(x, y)[ch]


# ---------------------------------------------------------------- build
func _ready() -> void:
	name = "central_park"
	if not ok:
		return
	var t0 := Time.get_ticks_msec()
	_publish_hole()
	_punch_ground.call_deferred()
	# Scenery added by other scripts after this one (and the ground, if it is
	# built late) gets the cut on a second pass.
	get_tree().create_timer(1.0).timeout.connect(_punch_ground)
	var mat := _terrain_material()
	var tris := _build_terrain(mat)
	_build_collision()
	var lk := Landmarks.new()
	lk.park = self
	add_child(lk)
	print("[central_park] relief %d x %d @ %.0f m, %d lakes, terrain %d tris (high LOD), %d ms" %
		[W, H, res, lakes.size(), tris, Time.get_ticks_msec() - t0])


## The outline as a texture + where it lies, for every ground shader.
func _publish_hole() -> void:
	var m := Image.create(W, H, false, Image.FORMAT_R8)
	for y in H:
		for x in W:
			m.set_pixel(x, y, Color(fields.get_pixel(x, y).r, 0, 0))
	_hole_tex = ImageTexture.create_from_image(m)
	var rv := Vector4(rect.position.x, rect.position.y, rect.size.x, rect.size.y)
	# Declared in project.godot [shader_globals]; adding them at run time
	# works too but Godot warns it is editor-only.
	RenderingServer.global_shader_parameter_set(&"park_hole_mask", _hole_tex)
	RenderingServer.global_shader_parameter_set(&"park_hole_rect", rv)


## GLSL any ground shader can include to leave the park to this script: the
## declarations after `shader_type`, the test at the top of fragment().
const HOLE_DECL := """
global uniform sampler2D park_hole_mask;
global uniform vec4 park_hole_rect;
bool in_park_hole(vec3 wpos) {
    vec2 u = (vec2(wpos.x, -wpos.z) - park_hole_rect.xy) / park_hole_rect.zw;
    if (any(lessThan(u, vec2(0.0))) || any(greaterThan(u, vec2(1.0)))) { return false; }
    return textureLod(park_hole_mask, vec2(u.x, 1.0 - u.y), 0.0).r > 0.5;
}
"""
const HOLE_TEST := "\n    if (in_park_hole((INV_VIEW_MATRIX * vec4(VERTEX, 1.0)).xyz)) { discard; }\n"


## Give every flat, large ground surface crossing the park the cut.
##
## Ground belongs to other scripts (the parent's land/water quads, the ortho
## plane, the NAIP tiles), so rather than each of them learning about the
## park, their materials are patched here at run time: a StandardMaterial3D
## becomes an equivalent ShaderMaterial with the test; a ShaderMaterial's
## shader gets HOLE_DECL after its `shader_type` line and HOLE_TEST at the top
## of fragment(). Each Shader is patched once (tiles share one). Owners that
## would rather do it themselves can include HOLE_DECL/HOLE_TEST and nothing
## here will touch them (the test finds `in_park_hole` already present).
func _punch_ground() -> void:
	if not is_inside_tree():
		return
	var park_box := AABB(Vector3(rect.position.x, -50, -rect.end.y), Vector3(rect.size.x, 100, rect.size.y))
	var n := 0
	for c in env.find_children("*", "MeshInstance3D", true, false):
		var mi := c as MeshInstance3D
		if mi.mesh == null or is_ancestor_of(mi) or _punched.has(mi.get_instance_id()):
			continue
		var box := mi.global_transform * mi.get_aabb()
		if box.size.y > 0.6 or box.size.x < 250.0 or box.size.z < 250.0 or not box.intersects(park_box):
			continue
		_punched[mi.get_instance_id()] = true
		if mi.material_override != null:
			var m2 := _punched_material(mi.material_override)
			if m2 != null:
				mi.material_override = m2
				n += 1
		else:
			for s in mi.mesh.get_surface_count():
				var m := mi.get_surface_override_material(s)
				if m == null:
					m = mi.mesh.surface_get_material(s)
				var m2 := _punched_material(m)
				if m2 != null:
					mi.set_surface_override_material(s, m2)
					n += 1
	if n > 0:
		print("[central_park] cut the park out of %d ground surfaces" % n)


static var _shader_cache := {}


func _punched_material(m: Material) -> Material:
	if m is ShaderMaterial:
		var sm := m as ShaderMaterial
		if sm.shader == null:
			return null
		var code := sm.shader.code
		if code.contains("in_park_hole") or not code.contains("shader_type spatial"):
			return null
		var key := sm.shader.get_instance_id()
		if not _shader_cache.has(key):
			var rx := RegEx.create_from_string("void\\s+fragment\\s*\\(\\s*\\)\\s*\\{")
			var hit := rx.search(code)
			if hit == null:
				return null
			code = code.substr(0, hit.get_end()) + HOLE_TEST + code.substr(hit.get_end())
			var st := code.find(";", code.find("shader_type"))
			code = code.substr(0, st + 1) + "\n" + HOLE_DECL + code.substr(st + 1)
			var sh := Shader.new()
			sh.code = code
			_shader_cache[key] = sh
		sm.shader = _shader_cache[key]
		return sm
	if m is StandardMaterial3D:
		var std := m as StandardMaterial3D
		var sh := Shader.new()
		var cull := "cull_disabled" if std.cull_mode == BaseMaterial3D.CULL_DISABLED else "cull_back"
		# Native in the thermal band (thermal_kit leaves it alone): its
		# overlay would redraw the whole plane, cut and all, over the park.
		sh.code = "shader_type spatial;\nrender_mode %s;\n#include \"res://shaders/thermal.gdshaderinc\"\n" % cull + HOLE_DECL + """
uniform vec4 albedo_color : source_color = vec4(1.0);
uniform sampler2D albedo_tex : source_color, filter_linear_mipmap_anisotropic;
uniform bool has_tex = false;
uniform float roughness = 1.0;
uniform float metallic = 0.0;
void fragment() {""" + HOLE_TEST + """
    vec4 c = albedo_color;
    if (has_tex) { c *= texture(albedo_tex, UV); }
    ALBEDO = c.rgb;
    ROUGHNESS = roughness;
    METALLIC = metallic;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec3 wp = (INV_VIEW_MATRIX * vec4(VERTEX, 1.0)).xyz;
        vec2 th = th_air(th_ground(clamp(c.rgb / 0.6, 0.0, 1.0)), length(CAMERA_POSITION_WORLD - wp));
        TH_OUT(th);
    }
}
"""
		var out := ShaderMaterial.new()
		out.shader = sh
		out.set_shader_parameter("albedo_color", std.albedo_color)
		out.set_shader_parameter("roughness", std.roughness)
		out.set_shader_parameter("metallic", std.metallic)
		if std.albedo_texture != null:
			out.set_shader_parameter("albedo_tex", std.albedo_texture)
			out.set_shader_parameter("has_tex", true)
		return out
	return null


# ---------------------------------------------------------------- terrain
func _terrain_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = TERRAIN_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = sh
	# The relief, for per-pixel normals: the mesh LODs then change the
	# silhouette only, never the lighting.
	var him := Image.create_from_data(W, H, false, Image.FORMAT_RF, heights.to_byte_array())
	him.convert(Image.FORMAT_RH)
	mat.set_shader_parameter("height_tex", ImageTexture.create_from_image(him))
	mat.set_shader_parameter("fields", fields_tex())
	mat.set_shader_parameter("rect", Vector4(rect.position.x, rect.position.y, rect.size.x, rect.size.y))
	mat.set_shader_parameter("res", res)
	# The photo: our own 0.5 m NAIP crop, else the city's 4.5 m ortho.
	var off: Vector2 = env.call("data_offset")
	var pp := DIR + str(doc["photo"]["file"])
	if FileAccess.file_exists(pp):
		mat.set_shader_parameter("photo", ImageTexture.create_from_image(_compressed(pp, Image.COMPRESS_S3TC)))
		mat.set_shader_parameter("photo_rect", Vector4(rect.position.x, rect.position.y, rect.size.x, rect.size.y))
	elif FileAccess.file_exists(DIR + "ortho.jpg"):
		var meta: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(DIR + "ortho.json"))
		var b: Array = meta["bounds_enu"]
		var img := Image.load_from_file(DIR + "ortho.jpg")
		img.generate_mipmaps()
		mat.set_shader_parameter("photo", ImageTexture.create_from_image(img))
		mat.set_shader_parameter("photo_rect", Vector4(float(b[0]) - off.x, float(b[1]) - off.y,
			float(b[2]) - float(b[0]), float(b[3]) - float(b[1])))
		push_warning("[central_park] no _park/photo.jpg (run tools/fetch_central_park.py); using ortho.jpg")
	var cp := DIR + str(doc["cover"]["file"])
	if FileAccess.file_exists(cp):
		mat.set_shader_parameter("cover", ImageTexture.create_from_image(_compressed(cp, Image.COMPRESS_BPTC)))
		mat.set_shader_parameter("has_cover", true)
	return mat


## The 2 m fields as one mipmapped texture, shared by the terrain and water.
var _fields_tex: ImageTexture = null


func fields_tex() -> ImageTexture:
	if _fields_tex == null:
		var fim := fields.duplicate() as Image
		fim.generate_mipmaps()
		_fields_tex = ImageTexture.create_from_image(fim)
	return _fields_tex


## Mipmapped and VRAM-compressed, the result cached as a .dds beside the
## source (gitignored, rebuilt when the source is newer): BC7 for the cover
## fields (it keeps the signed distances to ~1/8 m, where BC1 would step the
## path edges), BC1 for the photo. Uncompressed they were ~100 MB of VRAM.
static func _compressed(path: String, mode: int) -> Image:
	var dds := path.get_basename() + ".dds"
	var img: Image = null
	if FileAccess.file_exists(dds) and FileAccess.get_modified_time(dds) >= FileAccess.get_modified_time(path):
		img = Image.new()
		if img.load_dds_from_buffer(FileAccess.get_file_as_bytes(dds)) != OK:
			img = null
	if img == null:
		img = Image.load_from_file(path)
		img.convert(Image.FORMAT_RGBA8 if mode == Image.COMPRESS_BPTC else Image.FORMAT_RGB8)
		img.generate_mipmaps()
		img.compress(mode)
		img.save_dds(ProjectSettings.globalize_path(dds))
	return img


func _build_terrain(mat: ShaderMaterial) -> int:
	var root := Node3D.new()
	root.name = "terrain"
	add_child(root)
	var tris := 0
	var cx := 0
	while cx < W - 1:
		var x1 := mini(cx + CHUNK_CELLS.x, W - 1)
		var cy := 0
		while cy < H - 1:
			var y1 := mini(cy + CHUNK_CELLS.y, H - 1)
			if _chunk_has_park(cx, cy, x1, y1):
				for lod: Array in LODS:
					var mesh := _chunk_mesh(cx, cy, x1, y1, int(lod[0]))
					if lod[0] == 1:
						tris += mesh.surface_get_array_len(0) / 3
					var mi := MeshInstance3D.new()
					mi.name = "terrain_%d_%d_l%d" % [cx, cy, lod[0]]
					mi.mesh = mesh
					mi.material_override = mat
					# Rolling ground shadowing itself is a few metres of
					# relief under a 21 km shadow map: invisible, and the
					# biggest single caster here.
					mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
					mi.visibility_range_begin = float(lod[1])
					mi.visibility_range_end = float(lod[2])
					root.add_child(mi)
			cy = y1
		cx = x1
	return tris


func _chunk_has_park(x0: int, y0: int, x1: int, y1: int) -> bool:
	for y in range(y0, y1 + 1, 4):
		for x in range(x0, x1 + 1, 4):
			if fields.get_pixel(x, y).r > 0.0:
				return true
	return false


func _vtx(x: int, y: int) -> Vector3:
	return Vector3(rect.position.x + (x + 0.5) * res, heights[y * W + x], -(rect.end.y - (y + 0.5) * res))


## One chunk at a step of `s` samples: a grid, plus a skirt down its four
## edges so a neighbour at another LOD never shows a crack.
func _chunk_mesh(x0: int, y0: int, x1: int, y1: int, s: int) -> ArrayMesh:
	var xs: Array[int] = []
	var x := x0
	while x < x1:
		xs.append(x)
		x += s
	xs.append(x1)
	var ys: Array[int] = []
	var y := y0
	while y < y1:
		ys.append(y)
		y += s
	ys.append(y1)
	var nx := xs.size()
	var ny := ys.size()
	# Only the grid points some in-park quad uses (the margin past the wall
	# is ~15 % of the grid), re-indexed compactly.
	var used := PackedInt32Array()
	used.resize(nx * ny)
	used.fill(-1)
	var verts := PackedVector3Array()
	var idx := PackedInt32Array()
	var vid := func(i: int, j: int) -> int:
		var k := j * nx + i
		if used[k] < 0:
			used[k] = verts.size()
			verts.append(_vtx(xs[i], ys[j]))
		return used[k]
	for j in ny - 1:
		for i in nx - 1:
			# Quads entirely outside the wall are never seen (the shader
			# discards there anyway): skip them.
			if fields.get_pixel(xs[i], ys[j]).r == 0.0 and fields.get_pixel(xs[i + 1], ys[j + 1]).r == 0.0 \
					and fields.get_pixel(xs[i + 1], ys[j]).r == 0.0 and fields.get_pixel(xs[i], ys[j + 1]).r == 0.0:
				continue
			var a: int = vid.call(i, j)
			var b: int = vid.call(i + 1, j)
			var c: int = vid.call(i, j + 1)
			var d: int = vid.call(i + 1, j + 1)
			idx.append_array([a, b, c, b, d, c])
	# Skirts down the chunk's four edges, where a neighbour at another LOD
	# could otherwise show a crack.
	var edges := [[], [], [], []]
	for i in nx:
		edges[0].append(Vector2i(i, 0))
		edges[1].append(Vector2i(nx - 1 - i, ny - 1))
	for j in ny:
		edges[2].append(Vector2i(0, ny - 1 - j))
		edges[3].append(Vector2i(nx - 1, j))
	for e: Array in edges:
		for k in e.size() - 1:
			var ka: Vector2i = e[k]
			var kb: Vector2i = e[k + 1]
			var a := used[ka.y * nx + ka.x]
			var b := used[kb.y * nx + kb.x]
			if a < 0 or b < 0:
				continue
			var base := verts.size()
			verts.append(verts[a] - Vector3(0, SKIRT, 0))
			verts.append(verts[b] - Vector3(0, SKIRT, 0))
			idx.append_array([a, b, base, b, base + 1, base])
			idx.append_array([a, base, b, b, base, base + 1])
	# No normals: the shader takes them from the height texture.
	var arr := []
	arr.resize(Mesh.ARRAY_MAX)
	arr[Mesh.ARRAY_VERTEX] = verts
	arr[Mesh.ARRAY_INDEX] = idx
	var m := ArrayMesh.new()
	m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
	return m


## Solid to a strike: one HeightMapShape3D on the structure layer. Physics
## wants uniform scale, so the shape is scaled by the grid spacing on all
## three axes and the heights divided by it.
func _build_collision() -> void:
	var hs := HeightMapShape3D.new()
	hs.map_width = W
	hs.map_depth = H
	var d := PackedFloat32Array()
	d.resize(W * H)
	for i in W * H:
		d[i] = heights[i] / res
	hs.map_data = d
	var body := StaticBody3D.new()
	body.name = "park_ground_body"
	body.collision_layer = 1
	body.collision_mask = 0
	var cs := CollisionShape3D.new()
	cs.shape = hs
	body.add_child(cs)
	var c := rect.get_center()
	body.transform = Transform3D(Basis.IDENTITY.scaled(Vector3(res, res, res)), Vector3(c.x, 0, -c.y))
	add_child(body)


const TERRAIN_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

global uniform sampler2D park_hole_mask;
global uniform vec4 park_hole_rect;

uniform sampler2D height_tex : filter_linear, repeat_disable;
uniform sampler2D fields : filter_linear_mipmap, repeat_disable;
uniform sampler2D photo : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D cover : filter_linear_mipmap_anisotropic, repeat_disable;
uniform bool has_cover = false;
uniform vec4 rect;
uniform vec4 photo_rect;
uniform float res = 2.0;

varying vec3 w;

float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}
float fbm(vec2 p) { return vn(p) * 0.5 + vn(p * 2.03) * 0.3 + vn(p * 4.1) * 0.2; }

void vertex() {
    w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    // Schist outcrops stand proud of the 2 m relief: lumpy, a metre or so,
    // with the glacier's grain (striations NW-SE) in their shape.
    vec2 en0 = vec2(w.x, -w.z);
    vec2 fu0 = (en0 - rect.xy) / rect.zw;
    float rk0 = smoothstep(0.2, 0.6, textureLod(fields, vec2(fu0.x, 1.0 - fu0.y), 0.0).b);
    if (rk0 > 0.0) {
        vec2 sp0 = vec2(en0.x * 0.9 + en0.y * 0.42, en0.y * 0.9 - en0.x * 0.42);
        float lump = vn(sp0 / vec2(3.0, 7.0)) * 0.7 + vn(sp0 / 1.7) * 0.3;
        VERTEX.y += rk0 * (0.3 + 1.3 * lump);
        w.y += rk0 * (0.3 + 1.3 * lump);
    }
}

// Fade detail out before it aliases: 1 when a feature of `size` metres spans
// several pixels, 0 when it is under one.
float lod(float size, float fw) { return 1.0 - smoothstep(size * 0.25, size * 0.9, fw); }

float hgt(vec2 uv) { return texture(height_tex, uv).r; }

void fragment() {
    vec2 en = vec2(w.x, -w.z);
    vec2 u = (en - park_hole_rect.xy) / park_hole_rect.zw;
    if (textureLod(park_hole_mask, vec2(u.x, 1.0 - u.y), 0.0).r <= 0.5) { discard; }
    vec2 fu = (en - rect.xy) / rect.zw;
    vec2 fuv = vec2(fu.x, 1.0 - fu.y);
    float fw = max(length(fwidth(en)), 0.003);

    // Relief normal from the height field (2 m), so LODs only move the
    // silhouette.
    vec2 tx = 1.0 / vec2(textureSize(height_tex, 0));
    float hl = hgt(fuv - vec2(tx.x, 0.0)), hr = hgt(fuv + vec2(tx.x, 0.0));
    float hn = hgt(fuv - vec2(0.0, tx.y)), hs = hgt(fuv + vec2(0.0, tx.y));
    // Image rows run north->south: +v is south (Godot +Z).
    vec3 nw = normalize(vec3(hl - hr, 2.0 * res, hn - hs));

    vec4 fl = texture(fields, fuv);
    vec4 cv = has_cover ? texture(cover, fuv) : vec4(0.0, fl.g, fl.b, fl.a);
    float way_d = has_cover ? (cv.r * 255.0 - 128.0) / 8.0 : -10.0;
    float water_d = (cv.g * 255.0 - 128.0) / 8.0;
    float rock = cv.b;
    float lawn_kind = cv.a;

    vec2 pu = (en - photo_rect.xy) / photo_rect.zw;
    vec3 ph = texture(photo, vec2(pu.x, 1.0 - pu.y)).rgb;
    // Broad photo colour: what a patch is, without the tree crowns' and their
    // shadows' high-frequency pattern.
    vec3 phb = textureLod(photo, vec2(pu.x, 1.0 - pu.y), 5.0).rgb;
    float lum = dot(ph, vec3(0.299, 0.587, 0.114));
    float green = clamp((ph.g - max(ph.r, ph.b)) * 8.0, 0.0, 1.0);
    // NAIP is hazy and lifted.
    ph = clamp((ph - 0.035) * 1.12, 0.0, 1.0);
    vec3 c = ph * 0.62;

    // ---- vegetation. The photo's colour is kept (it is the real park: the
    // Great Lawn's worn ovals, the Sheep Meadow's sheen, the Ramble's
    // canopy, which the nature layer stands real trees on); what it lacks is
    // made here: detail below its 0.5 m pixel, and light where it has a
    // shadow. NAIP's tree shadows are near black and the scene draws its
    // own, so a photographed one would be a second, wrong-way shadow; dark
    // pixels are lifted to the local average colour instead.
    //   Tried and dropped: replacing the canopy and lawn colours with
    //   procedural ones. Every lawn came out the same olive and the woods
    //   went bald; from 100 m up the photo is simply the better grass.
    float shade = smoothstep(0.15, 0.05, lum) * (1.0 - smoothstep(0.1, 0.3, phb.r - phb.b));
    vec3 lifted = mix(phb, vec3(dot(phb, vec3(0.33))), 0.3) * 0.48 * (0.85 + 0.3 * fbm(en / 3.0));
    c = mix(c, lifted, shade * 0.85);
    float canopy = green * smoothstep(0.30, 0.16, lum) * (1.0 - smoothstep(0.75, 1.0, lawn_kind));
    float g_fine = vn(en * 3.1) * 0.6 + vn(en * 9.7) * 0.4;
    float g_mid = fbm(en / 5.0);
    float vegm = max(green, shade);
    // Blade-level grain and mowing/clump variation near the camera; meadow
    // (lawn kind ~0.55) a little tawnier and clumpier than mown lawn.
    float meadow = 1.0 - abs(lawn_kind - 0.55) / 0.45;
    // Grass at the scale of a tuft (0.25 m) and of a blade cluster (8 cm),
    // each fading out before it would alias.
    float tuft = vn(en * 4.3 + vec2(vn(en * 1.1) * 2.0));
    float blade = vn(vec2(en.x * 13.0, en.y * 11.0));
    vec3 detail = vec3(1.0 + (g_fine - 0.5) * 0.5 * lod(0.4, fw));
    detail *= 1.0 + (tuft - 0.5) * 0.5 * lod(0.25, fw) + (blade - 0.5) * 0.35 * lod(0.08, fw);
    detail *= 1.0 + (g_mid - 0.5) * mix(0.15, 0.35, meadow);
    detail *= mix(vec3(1.0), vec3(1.08, 1.0, 0.85), meadow * smoothstep(0.5, 0.8, g_mid));
    c = mix(c, c * detail, vegm);
    float under_d = 0.0;

    // ---- infields and bare earth (ballfield diamonds, worn paths): tan, from
    // the photo, with grit.
    float tan_ = clamp((ph.r - ph.b) * 5.0, 0.0, 1.0) * smoothstep(0.28, 0.42, lum) * (1.0 - green);
    vec3 dirt = vec3(0.42, 0.31, 0.20) * (0.85 + 0.3 * vn(en * 1.7)) * (1.0 + (vn(en * 13.0) - 0.5) * 0.3 * lod(0.2, fw));
    c = mix(c, dirt, tan_ * 0.8);

    // ---- rock: grey Manhattan schist, mica glinting, glacial striations
    // running NW-SE (about along the street grid's north here).
    float rk = smoothstep(0.25, 0.7, rock) * (1.0 - canopy * 0.6);
    vec2 sp = vec2(en.x * 0.9 + en.y * 0.42, en.y * 0.9 - en.x * 0.42);
    float stri = vn(vec2(sp.x * 2.2, sp.y * 0.18)) * 0.6 + vn(vec2(sp.x * 7.0, sp.y * 0.5)) * 0.4;
    float blot = fbm(en / 2.3);
    vec3 schist = mix(vec3(0.15, 0.148, 0.14), vec3(dot(ph, vec3(0.33))) * 0.45, 0.45) * (0.7 + 0.45 * blot) * (0.88 + 0.24 * stri * lod(0.3, fw));
    schist = mix(schist, vec3(0.12, 0.14, 0.10), smoothstep(0.62, 0.8, fbm(en / 1.1)) * 0.6);   // lichen
    c = mix(c, schist, rk);
    float rock_rough = 1.0 - 0.35 * rk * step(0.93, vn(en * 23.0));

    // ---- paved ways: asphalt and hex pavers, crisp edges from the 1 m field.
    float aa = fw * 0.8;
    float way = smoothstep(-aa, aa, way_d);
    vec3 asph = vec3(0.105, 0.102, 0.098) * (0.8 + 0.35 * fbm(en / 9.0)) * (1.0 + (vn(en * 6.0) - 0.5) * 0.25 * lod(0.2, fw));
    // The photo says how light a way is (pale concrete plazas, the Mall's
    // stone, Bethesda's brick) as long as it is not tree shade over it.
    float wl = dot(phb, vec3(0.299, 0.587, 0.114));
    asph *= clamp(wl / 0.24, 0.85, 1.3);
    asph = mix(asph, ph * 0.5, smoothstep(0.08, 0.2, ph.r - ph.b) * 0.7);
    // Kerb/edge: Belgian-block gutters along the drives read as a dark line.
    float edge = (1.0 - smoothstep(0.0, 0.35 + aa, way_d)) * way;
    asph *= 1.0 - 0.3 * edge * lod(0.3, fw);
    c = mix(c, asph, way);

    // ---- shores: damp dark band, then the water mesh takes over.
    float shore = 1.0 - smoothstep(0.0, 2.5, -water_d);
    c = mix(c, c * vec3(0.55, 0.58, 0.5), shore * (1.0 - way));

    // Relief lighting plus a little per-pixel bump on rock.
    vec3 nb = nw;
    if (rk > 0.01) {
        float e1 = fbm(en / 1.3 + 0.37), e2 = fbm((en + vec2(0.4, 0.0)) / 1.3 + 0.37), e3 = fbm((en + vec2(0.0, 0.4)) / 1.3 + 0.37);
        nb = normalize(nw + rk * vec3(-(e2 - e1), 0.0, (e3 - e1)) * 3.5 * lod(0.8, fw));
    }
    NORMAL = normalize((VIEW_MATRIX * vec4(nb, 0.0)).xyz);
    ALBEDO = c;
    ROUGHNESS = mix(0.95, 0.85, way) * rock_rough;
    SPECULAR = mix(0.3, 0.45, rk);

    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Lawn transpires (below the air); schist and asphalt soak up the sun
        // and stay warm into the evening; wet shores cool.
        vec2 t_grass = th_surface(0.4, 4.0, 0.45, -2.5);
        vec2 t_canopy = th_surface(0.45, 3.0, 0.55, -1.5);
        vec2 t_rock = th_surface(0.65, 24.0, 0.9, 0.5);
        vec2 t_way = th_surface(0.8, 22.0, 0.75, 0.0);
        vec2 t_dirt = th_surface(0.6, 16.0, 0.5, 0.0);
        vec2 th = mix(t_grass, t_canopy, canopy);
        th = mix(th, t_dirt, tan_);
        th = mix(th, t_rock, rk);
        th = mix(th, t_way, way);
        th = mix(th, th_surface(0.4, 2.0, 0.8, -2.0), shore);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
