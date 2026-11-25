## The far shores round Manhattan: Brooklyn, Queens, the Bronx and the New
## Jersey waterfront, so the island no longer stands alone in an empty sea.
##
## Data: assets/manhattan/far_nyc.json (NYC Open Data building footprints,
## boroughs 2-4, public domain) and far_nj.json (OpenStreetMap, ODbL), both
## from tools/fetch_manhattan_far.py, as oriented boxes in the dataset frame.
##
## VISUAL ONLY. These buildings are outside the sim's occupancy grid (which
## covers the loaded Manhattan slice) and are not in the analytic structure
## model (_rects / raycast_structures), so an aircraft routed over Brooklyn is
## not stopped by them and occlusion does not see them. Adding them to the
## model would also grow the grid to the whole 16 x 32 km mask, per aircraft.
## Fly the far shores only with that in mind.
##
## Drawn as MultiMeshes of one unit box per building, a MultiMesh per tier per
## chunk so whole chunks are frustum- and range-culled:
##   * tall (>= TALL_M): 8 km chunks, drawn to 30 km — the
##     Downtown Brooklyn, Long Island City and Jersey City skylines must read
##     from anywhere on the island;
##   * low: 2 km chunks, drawn to LOW_RANGE — the waterfront's
##     rowhouses and warehouses, which only matter close to.
## The facade shader is manhattan_photo's family: the same CC0 photographs on
## metric UVs (a storey is the same height on every building), a dark roof,
## and at night a lit-window layer from the same metric grid, faded to its
## average where a window is under a pixel so the far shore twinkles rather
## than shimmers.
extends Node3D

const FILES := ["res://assets/manhattan/far_nyc.json", "res://assets/manhattan/far_nj.json"]
const FacadeKit = preload("res://scripts/facade_kit.gd")

const TALL_M := 20.0
const TALL_CHUNK := 8000.0
const LOW_CHUNK := 2000.0
const LOW_RANGE := 4500.0
const TALL_RANGE := 30000.0
## Boxes start this far below the street, so a building on a bank lower than
## the scene's flat ground does not float.
const SINK := 2.0

var env: Node3D
var _mat: ShaderMaterial
var _night_forced := -1.0
var _night_last := -1.0
var count := 0


func _ready() -> void:
	env = get_parent()
	name = "far_shore"
	var arg := str(IpcServer._get_launch_arg("--far-night", ""))
	if arg != "":
		_night_forced = clampf(float(arg), 0.0, 1.0)
	_mat = _material()
	# chunk key -> [transforms...]
	var chunks := {}
	for path in FILES:
		if not FileAccess.file_exists(path):
			push_warning("[far_shore] no %s — run drone/sim/tools/fetch_manhattan_far.py" % path)
			continue
		var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
		var b: Array = doc.get("b", [])
		var off: Vector2 = env.call("data_offset")
		for i in range(0, b.size() - 5, 6):
			var c := Vector2(float(b[i]), float(b[i + 1])) - off
			var w: float = float(b[i + 2])
			var d: float = float(b[i + 3])
			var yaw := deg_to_rad(float(b[i + 4]))
			var h: float = float(b[i + 5])
			var tall := h >= TALL_M
			var ch := TALL_CHUNK if tall else LOW_CHUNK
			var key := Vector3i(int(floor(c.x / ch)), int(floor(c.y / ch)), 1 if tall else 0)
			var basis := Basis(Vector3.UP, yaw) * Basis.from_scale(Vector3(w, h + SINK, d))
			var xf := Transform3D(basis, Vector3(c.x, (h - SINK) * 0.5, -c.y))
			if not chunks.has(key):
				chunks[key] = []
			chunks[key].append(xf)
			count += 1
	var box := BoxMesh.new()
	box.size = Vector3.ONE
	box.material = _mat
	for key: Vector3i in chunks:
		var list: Array = chunks[key]
		var tall := key.z == 1
		var ch := TALL_CHUNK if tall else LOW_CHUNK
		var centre := Vector3((key.x + 0.5) * ch, 0.0, -(key.y + 0.5) * ch)
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = box
		mm.instance_count = list.size()
		for i in list.size():
			var xf: Transform3D = list[i]
			xf.origin -= centre
			mm.set_instance_transform(i, xf)
		var mmi := MultiMeshInstance3D.new()
		mmi.name = "far_%s_%d_%d" % ["tall" if tall else "low", key.x, key.y]
		mmi.multimesh = mm
		mmi.position = centre
		mmi.visibility_range_end = TALL_RANGE if tall else LOW_RANGE
		mmi.visibility_range_end_margin = 400.0
		# No shadows: every far chunk was a draw in each shadow cascade too,
		# for shade a kilometre off that no one sees.
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mmi)
	print("[far_shore] %d far-shore buildings in %d chunks (visual only)" % [count, chunks.size()])


func _process(_dt: float) -> void:
	var nt := _night_forced if _night_forced >= 0.0 else float(env.call("night_amount"))
	if absf(nt - _night_last) > 0.01:
		_night_last = nt
		_mat.set_shader_parameter("night", nt)


## The facade set the island's own buildings use when it is there (so the
## two shores match and the texture array is not loaded twice), else a fresh
## load, else plain procedural walls.
func _material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	var facades: Variant = null
	var spans: Variant = null
	var layers := 0.0
	var city := env.get_node_or_null("city") as MultiMeshInstance3D
	if city != null and city.multimesh != null and city.multimesh.mesh != null:
		var cm := city.multimesh.mesh.surface_get_material(0)
		if cm == null and city.multimesh.mesh is PrimitiveMesh:
			cm = (city.multimesh.mesh as PrimitiveMesh).material
		if cm is ShaderMaterial:
			facades = (cm as ShaderMaterial).get_shader_parameter("facades")
			spans = (cm as ShaderMaterial).get_shader_parameter("spans")
			var l: Variant = (cm as ShaderMaterial).get_shader_parameter("layers")
			layers = float(l) if l != null else 0.0
	if facades == null:
		var kit := FacadeKit.load_set()
		if not kit.is_empty():
			facades = kit["array"]
			spans = kit["spans"]
			layers = float(kit["count"])
	if facades != null:
		m.set_shader_parameter("facades", facades)
		m.set_shader_parameter("spans", spans)
		m.set_shader_parameter("layers", layers)
		m.set_shader_parameter("has_facades", 1.0)
	return m


const SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

uniform sampler2DArray facades : source_color, filter_linear_mipmap_anisotropic;
uniform float spans[8];
uniform float layers = 8.0;
uniform float has_facades = 0.0;
uniform float night = 0.0;

varying vec3 v_metres;
varying vec3 v_nrm;
varying float v_layer;
varying float v_span;
varying float v_seed;

float h1(float n) { return fract(sin(n) * 43758.5453123); }
float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void vertex() {
    vec3 s = vec3(length(MODEL_MATRIX[0].xyz), length(MODEL_MATRIX[1].xyz),
                  length(MODEL_MATRIX[2].xyz));
    // Metres from the box's bottom-front corner: windows keep their size on
    // every building whatever its scale.
    v_metres = (VERTEX + 0.5) * s;
    v_nrm = NORMAL;
    v_seed = h1(dot(MODEL_MATRIX[3].xyz, vec3(0.031, 0.017, 0.043)));
    v_layer = floor(v_seed * max(layers, 1.0));
    v_span = spans[int(clamp(v_layer, 0.0, 7.0))];
}

void fragment() {
    bool roof = abs(v_nrm.y) > 0.5;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // As the island's buildings (thermal_kit's building overlay): dark
        // roofs take the sun, walls are heavy and hold the day's heat.
        vec2 th = roof ? th_surface(0.75, 26.0, mix(0.25, 0.55, v_seed), 0.4)
                       : th_surface(0.55, 18.0, 0.7, 0.8 + 1.5 * h1(v_seed * 11.3));
        TH_OUT(th);
    } else if (roof) {
        // Tar, gravel and the odd pale membrane, like the island's roofs.
        // Darker than they look: a mid-grey albedo under the full sun
        // tonemaps to near white (as the island's roof note says).
        float pale = step(0.85, h1(v_seed * 7.7));
        ALBEDO = mix(vec3(0.10, 0.10, 0.105), vec3(0.21, 0.20, 0.19), h1(v_seed * 3.1));
        ALBEDO = mix(ALBEDO, vec3(0.36, 0.36, 0.35), pale);
        ROUGHNESS = 0.95;
    } else {
        float across = abs(v_nrm.x) > 0.5 ? v_metres.z : v_metres.x;
        float up = v_metres.y - 2.0;
        vec3 c;
        if (has_facades > 0.5) {
            c = texture(facades, vec3(vec2(across, v_metres.y) / v_span, v_layer)).rgb;
        } else {
            // No photographs on this checkout: brick, brownstone or concrete
            // with a window grid.
            vec3 wall = mix(vec3(0.45, 0.30, 0.24), vec3(0.62, 0.60, 0.56), h1(v_seed * 5.3));
            vec2 f = fract(vec2(across / 3.0, up / 3.3));
            float win = step(0.25, f.x) * step(f.x, 0.75) * step(0.3, f.y) * step(f.y, 0.85);
            c = mix(wall, vec3(0.12, 0.14, 0.16), win);
        }
        ALBEDO = c;
        float gloss = 1.0 - clamp(dot(c, vec3(0.33)), 0.0, 1.0);
        ROUGHNESS = mix(0.85, 0.3, gloss);
        METALLIC = gloss * 0.3;
        if (night > 0.02 && up > 0.5) {
            // Lit rooms: a share of its own per building, each window its own
            // warmth. Far off, the window cell doubles in size per octave of
            // range (lit with the same odds) so a window stays about a pixel:
            // a distant shore is points of light on dark walls. Averaging the
            // windows into a glow was tried first and turned the whole far
            // shore into lit beige boxes.
            float px = max(fwidth(across), fwidth(up));
            float lod = exp2(max(0.0, ceil(log2(max(px, 1e-4) / 0.6))));
            vec2 sz = vec2(3.0, 3.3) * lod;
            vec2 q = vec2(across, up) / sz;
            vec2 cell = floor(q);
            vec2 f = fract(q);
            float edge = 0.22 / lod;
            float win = step(0.5 - (0.28 + edge), f.x) * step(f.x, 0.5 + 0.28 + edge)
                      * step(0.28, f.y) * step(f.y, 0.85);
            float share = mix(0.10, 0.45, h1(v_seed * 17.9));
            float r = h2(cell + v_seed * 91.0 + lod * 7.0);
            float lit = win * step(r, share);
            vec3 warm = mix(vec3(1.0, 0.72, 0.42), vec3(0.85, 0.9, 1.0), step(0.85, h2(cell * 1.7 + 3.0)));
            EMISSION = warm * lit * night * mix(1.6, 1.1, clamp(lod / 8.0, 0.0, 1.0));
            ALBEDO *= 1.0 - 0.6 * night;
        }
    }
}
"""

