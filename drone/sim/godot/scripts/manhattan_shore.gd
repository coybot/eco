## The shoreline of the photoreal Manhattan scene as data: one signed-distance
## field (tools/bake_manhattan_shore.py) that the ground cuts the land with,
## the water shades its shallows and wash with, the bulkheads stand on, and
## env.is_water() answers from — so what is drawn and what is answered are
## the same line. Plus the pier decks (piers.json), which stand over water
## but are ground to anything asking.
##
## Two fields: 4 m/px over Manhattan and its rivers, 8 m/px over the whole
## mask extent. Both encode metres as 128 + 4 * d (+ over water), +-32 m.
## Sampled bilinearly on the CPU here exactly as a GPU's linear filter does
## (texel centres at +0.5), so the CPU's shoreline and the drawn one agree to
## the 8-bit step (0.25 m) plus BC4's error (about the same).
extends RefCounted

const DIR := "res://assets/manhattan/"
const PIER_CELL := 64.0

var ok := false
var offset := Vector2.ZERO       # dataset -> sim: subtract
var steps := 4.0
var fine_img: Image = null
var far_img: Image = null
var fine_rect := Rect2()          # dataset frame (SW corner, size)
var far_rect := Rect2()
## Piers, sim frame: Array of PackedVector2Array (outer rings), their
## bounding boxes, and a hash of cells -> indices.
var piers: Array[PackedVector2Array] = []
var pier_boxes: Array[Rect2] = []
var _pier_hash: Dictionary = {}
var _tex_cache: Dictionary = {}


static func load_for(sim_offset: Vector2) -> RefCounted:
	var s = load("res://scripts/manhattan_shore.gd").new()
	s.offset = sim_offset
	s._load()
	return s


func _load() -> void:
	if not FileAccess.file_exists(DIR + "shore.json"):
		return
	var meta: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(DIR + "shore.json"))
	steps = float(meta.get("steps_per_m", 4.0))
	fine_img = _gray(DIR + "shore.png")
	far_img = _gray(DIR + "shore_far.png")
	if fine_img == null or far_img == null:
		return
	fine_rect = _rect(meta["fine"]["bounds_enu"])
	far_rect = _rect(meta["far"]["bounds_enu"])
	ok = true
	_load_piers()


static func _gray(path: String) -> Image:
	if not FileAccess.file_exists(path):
		return null
	var img := Image.load_from_file(path)
	if img != null:
		img.convert(Image.FORMAT_L8)
	return img


static func _rect(b: Array) -> Rect2:
	return Rect2(float(b[0]), float(b[1]), float(b[2]) - float(b[0]), float(b[3]) - float(b[1]))


func _load_piers() -> void:
	var path := DIR + "piers.json"
	if not FileAccess.file_exists(path):
		return
	var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
	for p: Dictionary in doc.get("piers", []):
		var ring := PackedVector2Array()
		for q: Array in p["rings"][0]:
			ring.append(Vector2(float(q[0]), float(q[1])) - offset)
		if ring.size() > 3 and ring[0].is_equal_approx(ring[ring.size() - 1]):
			ring.remove_at(ring.size() - 1)
		if ring.size() < 3:
			continue
		var box := Rect2(ring[0], Vector2.ZERO)
		for v in ring:
			box = box.expand(v)
		var idx := piers.size()
		piers.append(ring)
		pier_boxes.append(box)
		for cx in range(int(floor(box.position.x / PIER_CELL)), int(floor(box.end.x / PIER_CELL)) + 1):
			for cy in range(int(floor(box.position.y / PIER_CELL)), int(floor(box.end.y / PIER_CELL)) + 1):
				var k := Vector2i(cx, cy)
				if not _pier_hash.has(k):
					_pier_hash[k] = PackedInt32Array()
				_pier_hash[k].append(idx)


## Index of the pier deck at sim (e, n), or -1.
func pier_at(e: float, n: float) -> int:
	var k := Vector2i(int(floor(e / PIER_CELL)), int(floor(n / PIER_CELL)))
	if not _pier_hash.has(k):
		return -1
	var p := Vector2(e, n)
	for i in _pier_hash[k]:
		if pier_boxes[i].has_point(p) and Geometry2D.is_point_in_polygon(p, piers[i]):
			return i
	return -1


## Signed distance to the shore at sim (e, n), metres, + over water.
## Outside both fields: open water.
func distance(e: float, n: float) -> float:
	var d := Vector2(e, n) + offset
	if fine_rect.grow(-8.0).has_point(d):
		return _sample(fine_img, fine_rect, d)
	if far_rect.grow(-16.0).has_point(d):
		return _sample(far_img, far_rect, d)
	return 32.0


func _sample(img: Image, r: Rect2, d: Vector2) -> float:
	var w := img.get_width()
	var h := img.get_height()
	# Texel centres at +0.5, row 0 the north edge: the GPU's linear filter.
	var fx := (d.x - r.position.x) / r.size.x * w - 0.5
	var fy := (r.end.y - d.y) / r.size.y * h - 0.5
	var x0 := clampi(int(floor(fx)), 0, w - 1)
	var y0 := clampi(int(floor(fy)), 0, h - 1)
	var x1 := mini(x0 + 1, w - 1)
	var y1 := mini(y0 + 1, h - 1)
	var tx := clampf(fx - floor(fx), 0.0, 1.0)
	var ty := clampf(fy - floor(fy), 0.0, 1.0)
	var a := lerpf(img.get_pixel(x0, y0).r, img.get_pixel(x1, y0).r, tx)
	var b := lerpf(img.get_pixel(x0, y1).r, img.get_pixel(x1, y1).r, tx)
	return (lerpf(a, b, ty) * 255.0 - 128.0) / steps


## Water at sim (e, n): the field says water and no pier deck stands there.
func is_water(e: float, n: float) -> bool:
	return distance(e, n) > 0.0 and pier_at(e, n) < 0


# ------------------------------------------------------------------ shaders
## Paste into a spatial shader; call shore_sd(sim_en) (sim e, n -> metres).
const FUNCS := """
uniform sampler2D shore_fine : filter_linear, repeat_disable;
uniform sampler2D shore_far : filter_linear, repeat_disable;
uniform vec4 shore_fine_rect;   // sim e0, n0, e1, n1
uniform vec4 shore_far_rect;
uniform float shore_steps = 4.0;
float shore_sd(vec2 en) {
    vec2 uv = (en - shore_fine_rect.xy) / (shore_fine_rect.zw - shore_fine_rect.xy);
    if (all(greaterThan(uv, vec2(0.002))) && all(lessThan(uv, vec2(0.998)))) {
        return (texture(shore_fine, vec2(uv.x, 1.0 - uv.y)).r * 255.0 - 128.0) / shore_steps;
    }
    uv = (en - shore_far_rect.xy) / (shore_far_rect.zw - shore_far_rect.xy);
    if (all(greaterThan(uv, vec2(0.002))) && all(lessThan(uv, vec2(0.998)))) {
        return (texture(shore_far, vec2(uv.x, 1.0 - uv.y)).r * 255.0 - 128.0) / shore_steps;
    }
    return 32.0;
}
"""


## Set FUNCS's uniforms on a material. Textures are made once and shared.
## BC4 (RGTC R): half a byte per texel, 8 MB for both fields instead of 16.
func bind(mat: ShaderMaterial) -> void:
	if not ok:
		return
	if _tex_cache.is_empty():
		for key in ["fine", "far"]:
			var img: Image = (fine_img if key == "fine" else far_img).duplicate()
			img.convert(Image.FORMAT_R8)
			img.compress(Image.COMPRESS_S3TC)
			_tex_cache[key] = ImageTexture.create_from_image(img)
	mat.set_shader_parameter("shore_fine", _tex_cache["fine"])
	mat.set_shader_parameter("shore_far", _tex_cache["far"])
	var f := Rect2(fine_rect.position - offset, fine_rect.size)
	var g := Rect2(far_rect.position - offset, far_rect.size)
	mat.set_shader_parameter("shore_fine_rect", Vector4(f.position.x, f.position.y, f.end.x, f.end.y))
	mat.set_shader_parameter("shore_far_rect", Vector4(g.position.x, g.position.y, g.end.x, g.end.y))
	mat.set_shader_parameter("shore_steps", steps)
