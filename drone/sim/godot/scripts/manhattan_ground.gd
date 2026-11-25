## The ground under the photoreal Manhattan scene, and everything at its
## edge: sharp NAIP imagery, the streets drawn over it, the rivers, the
## bulkheads and the piers. Added by env_manhattan_photo._add_city_ground in
## place of the parent's flat water/land/park quads and the 4.5 m ortho.
##
## LAYERS, all at street level (y = LAND_Y) and all cut by the one shoreline
## field (manhattan_shore.gd), so nothing of the photo's own water is drawn:
##   hi tiles   1000 m, 0.98 m/px, over Manhattan's own land
##   mid tiles  1000 m, 1.95 m/px, the facing shores of NJ, Brooklyn, Queens,
##              the Bronx
##              (each level one Texture2DArray, drawn in 3 km chunks)
##   far        one 16 x 32 km plane at 8 m/px over the rest of the mask, with
##              the tiles' cells punched out of it (coverage texture)
## Without the tiles (fresh checkout: tools/fetch_manhattan_naip.py not run)
## the committed ortho.jpg is the one photo, under the same shader.
##
## STREETS. NAIP is a central projection from ~3 km, so Midtown's towers lean
## tens of metres across the streets beside them in the photo. The streets are
## therefore drawn, not photographed: the planimetric roadbed / sidewalk /
## median polygons, baked as signed-distance fields (2 m/px, exact for straight
## edges under bilinear filtering), become asphalt, a kerb and concrete
## sidewalk slabs in the shader. Lane markings are geometry
## (manhattan_markings.gd) because 10 cm paint lines are far below any texture
## resolution this VRAM budget allows.
##
## VRAM (Jetson-class GPU): photos BC1 + mips (0.67 B/px): hi 86 x 0.7 MB,
## mid 58 x 0.17 MB, far 5.3 MB; street fields BC7 + mips 86 x 0.33 MB; shore
## fields BC4 ~10 MB: ~110 MB of textures, ~150 MB with the markings, coast
## and pier meshes (measured: scene video memory +124 MB over the old ground,
## which itself held a 30 MB uncompressed ortho).
extends Node3D

const Shore = preload("res://scripts/manhattan_shore.gd")
const SceneKit = preload("res://scripts/scene_kit.gd")
const HI_DIR := "res://assets/manhattan/_hi/"
const ORTHO := "res://assets/manhattan/ortho.jpg"
const LAYER_TINT := 0.62

var env: Node3D
var shore: RefCounted
var tile_m := 1000.0
var vram_bytes := 0

static var _shader: Shader = null


func _ready() -> void:
	name = "ground"
	env = get_parent() as Node3D
	shore = env.call("shore")
	var t0 := Time.get_ticks_msec()
	# --ground-skip=tiles,water,coast,piers,markings leaves parts out, for
	# measuring what each costs (and for falling back to the ortho: tiles).
	var skip := str(IpcServer._get_launch_arg("--ground-skip", "")).split(",")
	_hide_old_ground()
	var n := 0 if "tiles" in skip else _build_tiles()
	if n == 0:
		_build_ortho_fallback()
	for part in ["water", "coast", "piers", "markings"]:
		if not part in skip:
			add_child(load("res://scripts/manhattan_%s.gd" % part).new())
	print("[manhattan_ground] %d photo tiles, %.0f MB of ground textures, %d ms" %
		[n, vram_bytes / 1048576.0, Time.get_ticks_msec() - t0])


## The parent's three stacked quads (water 0.1, land 0.6, park 1.1) are never
## built here (env_manhattan_photo skips them), but SceneKit's 2 km green
## field at y 0 is, and would z-fight the streets at the origin.
func _hide_old_ground() -> void:
	for c in env.get_children():
		if c is MeshInstance3D and (c as MeshInstance3D).mesh is PlaneMesh:
			var pm := (c as MeshInstance3D).mesh as PlaneMesh
			if is_equal_approx(pm.size.x, SceneKit.GROUND_SIZE):
				(c as MeshInstance3D).visible = false


# ------------------------------------------------------------------ tiles
## Tiles are drawn from Texture2DArrays, one per level, a handful of meshes
## per level: one draw per 3 km chunk instead of one per tile. Per tile
## materials were 144 draws in a wide shot (the base scene drew 58 in all).
const CHUNK_TILES := 3

## level ("hi", "mid") -> {"array": Texture2DArray, "layers": {Vector2i(i, j): layer}}
var arrays: Dictionary = {}
var street_array: Texture2DArray = null
var street_layers: Dictionary = {}       # Vector2i(i, j) -> layer
var _single: Array = []                  # [{"texture", "rect", "level"}] far / ortho


func _build_tiles() -> int:
	if not FileAccess.file_exists(HI_DIR + "tiles.json"):
		return 0
	var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(HI_DIR + "tiles.json"))
	tile_m = float(doc.get("tile_m", 1000.0))
	var off: Vector2 = env.call("data_offset")
	var imgs := {"hi": [] as Array[Image], "mid": [] as Array[Image]}
	var keys := {"hi": [] as Array[Vector2i], "mid": [] as Array[Vector2i]}
	var fields: Array[Image] = []
	for t: Dictionary in doc.get("tiles", []):
		var lv := str(t["level"])
		var k := Vector2i(int(t["i"]), int(t["j"]))
		var path := HI_DIR + "photo_%d_%d.jpg" % [k.x, k.y]
		if not imgs.has(lv) or not FileAccess.file_exists(path):
			continue
		var img := _compressed(path, Image.COMPRESS_S3TC)
		# A tile fetched at another size than the rest cannot join the array.
		if not imgs[lv].is_empty() and img.get_size() != (imgs[lv][0] as Image).get_size():
			push_warning("[manhattan_ground] %s is %s, not %s: rerun fetch_manhattan_naip.py" %
				[path, img.get_size(), (imgs[lv][0] as Image).get_size()])
			continue
		imgs[lv].append(img)
		keys[lv].append(k)
		var sp := HI_DIR + "streets_%d_%d.png" % [k.x, k.y]
		if lv == "hi" and FileAccess.file_exists(sp):
			street_layers[k] = fields.size()
			fields.append(_compressed(sp, Image.COMPRESS_BPTC))
	if not fields.is_empty():
		street_array = Texture2DArray.new()
		street_array.create_from_images(fields)
	var n := 0
	for lv in ["hi", "mid"]:
		if (imgs[lv] as Array).is_empty():
			continue
		var arr := Texture2DArray.new()
		arr.create_from_images(imgs[lv])
		var layers := {}
		for li in (keys[lv] as Array).size():
			layers[keys[lv][li]] = li
		arrays[lv] = {"array": arr, "layers": layers}
		_tile_meshes(lv, arr, layers, off)
		n += layers.size()
	var far: Dictionary = doc.get("far", {})
	if FileAccess.file_exists(HI_DIR + "far.jpg") and far.has("bounds_enu"):
		var b: Array = far["bounds_enu"]
		var r := Rect2(Vector2(float(b[0]), float(b[1])) - off,
			Vector2(float(b[2]) - float(b[0]), float(b[3]) - float(b[1])))
		var tex := ImageTexture.create_from_image(_compressed(HI_DIR + "far.jpg", Image.COMPRESS_S3TC))
		_single.append({"texture": tex, "rect": r, "level": "far"})
		var mat := _material(null, tex, true)
		# Which 1000 m cells the tiles already draw: the far plane is
		# discarded there rather than z-fighting them at the same height.
		var cw := int(round(r.size.x / tile_m))
		var ch := int(round(r.size.y / tile_m))
		var cov := Image.create(cw, ch, false, Image.FORMAT_R8)
		var c0 := Vector2i(int(round(float(b[0]) / tile_m)), int(round(float(b[1]) / tile_m)))
		for lv in arrays:
			for k: Vector2i in arrays[lv]["layers"]:
				var x := k.x - c0.x
				var y := ch - 1 - (k.y - c0.y)
				if x >= 0 and y >= 0 and x < cw and y < ch:
					cov.set_pixel(x, y, Color(1, 1, 1))
		mat.set_shader_parameter("coverage", ImageTexture.create_from_image(cov))
		mat.set_shader_parameter("cover_rect", Vector4(r.position.x, r.position.y, r.end.x, r.end.y))
		_quad("ground_far", r, mat)
	return n


## One mesh per CHUNK_TILES x CHUNK_TILES block of a level's tiles. Each
## quad's UV runs 0..1 over its tile; UV2 = (photo layer, street layer or -1).
func _tile_meshes(lv: String, arr: Texture2DArray, layers: Dictionary, off: Vector2) -> void:
	var groups := {}
	for k: Vector2i in layers:
		var g := Vector2i(floori(float(k.x) / CHUNK_TILES), floori(float(k.y) / CHUNK_TILES))
		if not groups.has(g):
			groups[g] = []
		groups[g].append(k)
	var mat := _material(arr, null, false)
	for g: Vector2i in groups:
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		for k: Vector2i in groups[g]:
			var r := Rect2(Vector2(k) * tile_m - off, Vector2(tile_m, tile_m))
			var lay := Vector2(float(layers[k]), float(street_layers.get(k, -1)) if lv == "hi" else -1.0)
			# Corners (x east, z = -north); UV (0,0) the tile's north-west.
			var nw := [Vector3(r.position.x, 0.0, -r.end.y), Vector2(0, 0)]
			var ne := [Vector3(r.end.x, 0.0, -r.end.y), Vector2(1, 0)]
			var se := [Vector3(r.end.x, 0.0, -r.position.y), Vector2(1, 1)]
			var sw := [Vector3(r.position.x, 0.0, -r.position.y), Vector2(0, 1)]
			# Clockwise seen from above: Godot's front face.
			for v in [nw, ne, se, nw, se, sw]:
				st.set_normal(Vector3.UP)
				st.set_uv(v[1])
				st.set_uv2(lay)
				st.add_vertex(v[0])
		var mi := MeshInstance3D.new()
		mi.name = "ground_%s_%d_%d" % [lv, g.x, g.y]
		mi.mesh = st.commit()
		mi.material_override = mat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)


## Without the fetched tiles: the committed 4.5 m ortho, same shader (the
## water still cut out by the shoreline), no drawn streets.
func _build_ortho_fallback() -> void:
	if not FileAccess.file_exists(ORTHO):
		return
	var meta: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://assets/manhattan/ortho.json"))
	var b: Array = meta.get("bounds_enu", [])
	if b.size() != 4:
		return
	var off: Vector2 = env.call("data_offset")
	var r := Rect2(Vector2(float(b[0]), float(b[1])) - off,
		Vector2(float(b[2]) - float(b[0]), float(b[3]) - float(b[1])))
	var img := Image.load_from_file(ORTHO)
	img.generate_mipmaps()
	img.compress(Image.COMPRESS_S3TC)
	vram_bytes += img.get_data_size()
	var tex := ImageTexture.create_from_image(img)
	_quad("ground_ortho", r, _material(null, tex, false))
	_single.append({"texture": tex, "rect": r, "level": "ortho"})
	print("[manhattan_ground] no NAIP tiles (run tools/fetch_manhattan_naip.py); using ortho.jpg")


## FOR OTHER SCENERY (the park's terrain, pier decks): the ground photo at
## sim (e, n), as one of
##   {"level": "hi" | "mid", "array": Texture2DArray, "layer": int, "rect": Rect2}
##   {"level": "far" | "ortho", "texture": Texture2D, "rect": Rect2}
## or {} where there is none. rect is the tile's (or image's) sim e, n SW
## corner and size. Every image is BC1, mipmapped, row 0 the NORTH edge, so a
## world point p (Godot xyz) samples at
##     uv = (vec2(p.x, -p.z) - rect.position) / rect.size;  uv.y = 1.0 - uv.y
##     texture(array, vec3(uv, layer))   or   texture(texture, uv)
## Shade it as this ground does and the two match at any seam:
##     albedo = clamp((photo - 0.04) * 1.12, 0.0, 1.0) * 0.62   (LAYER_TINT)
## All hi tiles share one array (arrays["hi"]), all mid ones another, so a
## terrain mesh can take the array once and put the layer in a vertex
## attribute. On disk (gitignored, tools/fetch_manhattan_naip.py):
## _hi/tiles.json lists the tiles; _hi/photo_<i>_<j>.jpg covers dataset east
## [i, i+1) x tile_m, north [j, j+1) x tile_m (dataset = sim + data_offset);
## _hi/far.jpg the far bounds in tiles.json. This node is
## env.get_node("ground"), added before the city is rendered.
func photo_at(p: Vector2) -> Dictionary:
	var off: Vector2 = env.call("data_offset")
	var d := p + off
	var k := Vector2i(int(floor(d.x / tile_m)), int(floor(d.y / tile_m)))
	for lv in ["hi", "mid"]:
		if arrays.has(lv) and arrays[lv]["layers"].has(k):
			return {"level": lv, "array": arrays[lv]["array"], "layer": int(arrays[lv]["layers"][k]),
				"rect": Rect2(Vector2(k) * tile_m - off, Vector2(tile_m, tile_m))}
	for s: Dictionary in _single:
		if (s["rect"] as Rect2).has_point(p):
			return s
	return {}


## Load an image mipmapped and VRAM-compressed, caching the compressed result
## as a .dds beside it. Compressing at load took 19 s for the 230 ground
## textures (BC7 is most of it); reading the cached .dds is a memcpy. The
## cache is in _hi/ (gitignored) and rebuilt when the source is newer.
## Street fields are BC7: it keeps three smooth channels to ~1 step of
## 0.125 m, where BC1's 5:6:5 would put 0.25-0.5 m steps in the kerb.
func _compressed(path: String, mode: int) -> Image:
	var dds := path.get_basename() + ".dds"
	var img: Image = null
	if FileAccess.file_exists(dds) and FileAccess.get_modified_time(dds) >= FileAccess.get_modified_time(path):
		img = Image.new()
		if img.load_dds_from_buffer(FileAccess.get_file_as_bytes(dds)) != OK:
			img = null
	if img == null:
		img = Image.load_from_file(path)
		if mode == Image.COMPRESS_BPTC:
			img.convert(Image.FORMAT_RGBA8)
		img.generate_mipmaps()
		img.compress(mode)
		img.save_dds(ProjectSettings.globalize_path(dds))
	vram_bytes += img.get_data_size()
	return img


func _quad(nm: String, r: Rect2, mat: ShaderMaterial) -> void:
	var pm := PlaneMesh.new()
	pm.size = r.size
	var mi := MeshInstance3D.new()
	mi.name = nm
	mi.mesh = pm
	mi.material_override = mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	# PlaneMesh UV (0,0) is its -x,-z corner: west, NORTH, as the images' row 0.
	mi.position = Vector3(r.position.x + r.size.x * 0.5, 0.0, -(r.position.y + r.size.y * 0.5))
	add_child(mi)


## `arr` for the tile meshes (layers in UV2), else the single `tex` (far
## plane, ortho fallback).
func _material(arr: Texture2DArray, tex: Texture2D, far: bool) -> ShaderMaterial:
	if _shader == null:
		_shader = Shader.new()
		_shader.code = SHADER_HEAD + Shore.FUNCS + NOISE_FUNCS + SHADER_BODY
	var m := ShaderMaterial.new()
	m.shader = _shader
	if arr != null:
		m.set_shader_parameter("photo_arr", arr)
		m.set_shader_parameter("use_array", 1.0)
		if street_array != null:
			m.set_shader_parameter("street_arr", street_array)
	else:
		m.set_shader_parameter("photo", tex)
	m.set_shader_parameter("tint", LAYER_TINT * (0.92 if far else 1.0))
	m.set_shader_parameter("far_mode", 1.0 if far else 0.0)
	shore.call("bind", m)
	return m


## Value noise and a hash, shared by the ground, the coast and the piers.
const NOISE_FUNCS := """
float g_h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float g_vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(g_h2(i), g_h2(i + vec2(1, 0)), f.x), mix(g_h2(i + vec2(0, 1)), g_h2(i + vec2(1, 1)), f.x), f.y);
}
// Fade a detail of `size` metres out as it nears a pixel, so it averages
// instead of shimmering. `fw` is metres per pixel here.
float g_lod(float size, float fw) { return 1.0 - smoothstep(0.25 * size, 0.7 * size, fw); }
"""

const SHADER_HEAD := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"
uniform sampler2DArray photo_arr : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2DArray street_arr : filter_linear_mipmap, repeat_disable;
uniform sampler2D photo : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform float use_array = 0.0;
uniform float tint = 0.62;
uniform float far_mode = 0.0;
uniform sampler2D coverage : filter_nearest, repeat_disable;
uniform vec4 cover_rect;
varying vec3 w;
varying flat vec2 layer;
void vertex() {
    w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    layer = use_array > 0.5 ? UV2 : vec2(0.0, -1.0);
}
"""

## Street surfaces, in metres from the planimetric fields:
##   rd  roadbed (+ inside), pd  any paved public way, md  median.
## The paved field is grown 1.2 m: NYC's sidewalk polygons stop short of the
## building line (areaways, stoops), and that strip in the photo is exactly
## where the leaning facades lie.
const SHADER_BODY := """
vec3 asphalt(vec2 p, float fw, float rd) {
    vec3 c = vec3(0.105, 0.105, 0.11);
    // Broad blotches of older, greyer and newer, blacker asphalt.
    float b = g_vn(p / 23.0) * 0.6 + g_vn(p / 7.0) * 0.4;
    c *= 0.78 + 0.5 * b;
    // Utility cuts: rectangular patches on a 6 x 3 m lattice along the grid.
    vec2 cell = floor(p / vec2(6.0, 3.0));
    float hc = g_h2(cell);
    vec2 f = fract(p / vec2(6.0, 3.0));
    float inpatch = step(0.94, hc) * step(0.15, f.x) * step(f.x, 0.85) * step(0.2, f.y) * step(f.y, 0.9);
    c = mix(c, c * (hc > 0.975 ? 0.78 : 1.16), inpatch * g_lod(2.0, fw));
    // Grain and fine cracks, gone before they would alias.
    float grain = g_vn(p * 2.7) - 0.5;
    c *= 1.0 + 0.22 * grain * g_lod(0.8, fw);
    float crack = 1.0 - smoothstep(0.0, 0.035, abs(g_vn(p / 2.3) - 0.5));
    c *= 1.0 - 0.35 * crack * step(0.55, g_vn(p / 17.0)) * g_lod(0.3, fw);
    // Gutters collect dirt along the kerb.
    c *= mix(0.8, 1.0, smoothstep(0.1, 0.7, rd));
    return c;
}

vec3 concrete(vec2 p, float fw, float rd) {
    vec3 c = vec3(0.25, 0.243, 0.228);
    // 1.5 m slabs on the street grid, each a little different, dark joints.
    vec2 cell = floor(p / 1.5);
    c *= 0.88 + 0.2 * g_h2(cell);
    vec2 j = abs(fract(p / 1.5) - 0.5) * 1.5;
    float joint = 1.0 - smoothstep(0.0, max(fw, 0.02), 0.75 - max(j.x, j.y) - 0.01);
    c *= 1.0 - 0.35 * joint * g_lod(0.15, fw);
    c *= 0.82 + 0.3 * g_vn(p / 5.0);
    // Gum and stains.
    c *= 1.0 - 0.18 * step(0.82, g_vn(p * 1.9)) * g_lod(0.5, fw);
    return c;
}

void fragment() {
    vec2 en = vec2(w.x, -w.z);
    float sd = shore_sd(en);
    if (sd > 0.0) { discard; }
    if (far_mode > 0.5) {
        vec2 cu = (en - cover_rect.xy) / (cover_rect.zw - cover_rect.xy);
        if (texture(coverage, vec2(cu.x, 1.0 - cu.y)).r > 0.5) { discard; }
    }
    vec3 ph = use_array > 0.5 ? texture(photo_arr, vec3(UV, layer.x)).rgb : texture(photo, UV).rgb;
    // NAIP is hazy and lifted; a touch of contrast before the shared tint.
    ph = clamp((ph - 0.04) * 1.12, 0.0, 1.0);
    vec3 c = ph * tint;
    float rough = 1.0;
    vec3 heat_c = ph;
    if (layer.y > -0.5) {
        vec3 s = texture(street_arr, vec3(UV, layer.y)).rgb * 255.0;
        float rd = (s.r - 128.0) / 8.0;
        float pd = (s.g - 128.0) / 8.0 + 1.2;
        float md = (s.b - 128.0) / 8.0;
        float fw = max(length(fwidth(en)), 0.005);
        float aa = fw * 0.7;
        float road = smoothstep(-aa, aa, rd);
        float paved = max(smoothstep(-aa, aa, pd), road);
        float med = smoothstep(-aa, aa, md) * (1.0 - road);
        vec3 a = asphalt(en, fw, rd);
        vec3 k = concrete(en, fw, rd);
        // Planted medians (Park Avenue, Broadway's malls) keep the photo's green.
        float green = clamp((ph.g - max(ph.r, ph.b)) * 12.0, 0.0, 1.0);
        vec3 side = mix(k, c, med * green);
        // The kerb: a 0.2 m granite edge on the sidewalk side, a shadowed
        // face just inside the roadbed.
        float kerb = (1.0 - smoothstep(0.18, 0.18 + aa + 0.02, -rd)) * (1.0 - road);
        side = mix(side, vec3(0.31, 0.30, 0.29), kerb * g_lod(0.2, fw));
        a *= mix(0.65, 1.0, smoothstep(0.0, 0.16 + aa, rd) + (1.0 - g_lod(0.2, fw)));
        vec3 street = mix(side, a, road);
        c = mix(c, street, paved);
        rough = mix(1.0, mix(0.85, 0.92, road), paved);
        heat_c = mix(ph, street / 0.6, paved);
    }
    ALBEDO = c;
    ROUGHNESS = rough;
    SPECULAR = 0.35;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_ground(clamp(heat_c, 0.0, 1.0));
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
