## What is on the water round historic Istanbul: the city's ferries, sea
## buses and excursion boats; the tankers, bulk carriers and freighters
## transiting the Bosphorus or riding at anchor off Ahirkapi in the Sea of
## Marmara; tugs and pilot boats; fishing boats and small craft moored along
## the Golden Horn, at Kumkapi, Kadikoy and Ortakoy.
##
##   vapur      a Sehir Hatlari passenger ferry: 70 m, double-ended, white
##              over a black boot-top, two glazed passenger decks, a
##              wheelhouse at each end, the yellow funnel with its black top.
##   seabus     an IDO sea bus (deniz otobusu): a 38 m white catamaran.
##   motorboat  a private passenger boat (Turyol, Dentur): 28 m, blue hull.
##   tour       a Bosphorus excursion boat: 40 m, an open top deck under an
##              awning.
##   tanker, bulk, container: 150..190 m merchant ships in a few hull paints.
##   models     assets/models (CC BY 4.0, see its CREDITS.md): ship_cargo
##              and ship_coaster freighters, ship_tug, boat_pilot, and the
##              small craft (boat_huri, boat_skiff, boat_cabin, boat_rusty,
##              boat_work, boat_trawler).
## No naval vessels.
##
## Where (all from the scene's own data): every ferry berth is an end of an
## OpenStreetMap ferry route (roads.json), and most have a boat alongside,
## beam-on to the quay; ferries, sea buses and motorboats are out on those
## routes mid-crossing; ships transit the Bosphorus in its two lanes (a
## centre line traced from the water mask: tools/bake_istanbul_sea.py's
## mask, by hand), and lie at anchor on a spread of anchorage points in the
## Marmara, all swung bow to the north-east into the Bosphorus' outflow;
## small craft are moored along the shores of a handful of harbours, placed
## by sampling env.is_water() for a berth a few metres off the land.
##
## STATIC BY DEFAULT: everything stands still (moored, at anchor, or
## mid-crossing with its wake drawn). With --world-life (all, or a list
## including "ships") the route ferries shuttle between their berths and the
## transiting ships run the Bosphorus lanes, turning into the other lane at
## the ends.
##
## Drawing: one MultiMesh per procedural kind (hull, superstructure and
## glazing in one vertex-coloured surface, its own thermal branch) and one
## per mesh of each downloaded model, so a hundred craft cost a few dozen
## draws. Wakes are one MultiMesh too.
##
## Solid: ships of 60 m and more (the ferries, the merchant ships) are
## kinematic bodies on collision layer 1 (hull and superstructure boxes),
## moved with them; smaller craft are visual props and do not change strike
## detection. Nothing goes into env.props.
extends Node3D

const ModelKit = preload("res://scripts/model_kit.gd")

const LAYER_STRUCTURE := 1
const SOLID_FROM_M := 60.0
const FERRY_DWELL_S := 45.0

var env: Node3D
var moving := false
var _rng := RandomNumberGenerator.new()

# Kinds: name -> {"mesh": ArrayMesh (procedural) or "model": String,
# "dims": Vector3(L, B, freeboard), "solids": [[centre, size]], "v": m/s}
var _kinds: Dictionary = {}
var _mmi: Dictionary = {}                   # kind -> Array[[MultiMeshInstance3D, Transform3D local]]

# Ships.
var _s_kind := PackedStringArray()
var _s_pos := PackedVector2Array()          # ENU
var _s_yaw := PackedFloat32Array()          # Godot yaw (+Z turned to the heading)
var _s_v := PackedFloat32Array()
var _s_under := PackedByteArray()           # 1 = underway (wake drawn)
var _s_route := PackedInt32Array()          # -1: moored / anchored
var _s_wp := PackedInt32Array()
var _s_dir := PackedInt32Array()
var _s_dwell := PackedFloat32Array()
var _s_body: Array[RID] = []                # RID() when not solid
var _shapes: Array[RID] = []
var _routes: Array = []                     # {"pts": PackedVector2Array, "kind", "next"}
var _wake_mm: MultiMesh
var _ship_mat: ShaderMaterial
var _night_last := -1.0
var _berths: Array = []                     # [Vector2 ENU, String kind]


func _ready() -> void:
	env = get_parent()
	name = "istanbul_ships"
	_rng.seed = 1923
	var wl = load("res://scripts/world_life.gd")
	var arg := str(wl.call("_world_life_arg")) if wl != null else ""
	moving = arg == "all" or arg.split(",").has("ships")
	_ship_mat = _make_material()
	_build_kinds()
	_place()
	_draw_all()
	set_process(true)
	set_physics_process(moving)
	print("[istanbul_ships] %d craft (%d underway), %s" % [_s_kind.size(), _count_under(),
		"moving (--world-life)" if moving else "static"])


func _exit_tree() -> void:
	for r in _s_body:
		if r.is_valid():
			PhysicsServer3D.free_rid(r)
	for r in _shapes:
		PhysicsServer3D.free_rid(r)
	_s_body.clear()
	_shapes.clear()


func _count_under() -> int:
	var n := 0
	for u in _s_under:
		n += u
	return n


## The first of each kind: {kind: [e, n, sea level, yaw]} (life_ships' API).
func samples() -> Dictionary:
	var out := {}
	for i in _s_kind.size():
		if not out.has(_s_kind[i]):
			out[_s_kind[i]] = [_s_pos[i].x, _s_pos[i].y, float(env.get("SEA_Y")), _s_yaw[i]]
	return out


func count() -> int:
	return _s_kind.size()


# ------------------------------------------------------------------ material

const SHIP_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"
uniform float night = 0.0;
varying vec3 v_world;
void vertex() { v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    float cls = COLOR.a;        // 0.2 glass, 0.5 deck / timber, 0.7 paint, 1 steel
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th;
        if (cls < 0.35) {
            th = th_surface(0.3, 8.0, 0.3, 5.0);       // heated cabins
        } else if (cls < 0.6) {
            th = th_surface(0.6, 16.0, 0.4, 0.0);
        } else {
            th = th_surface(0.55, 18.0, 0.5, 1.5);     // hull over the warm sea, engines
        }
        TH_OUT(th);
    } else {
        vec3 c = COLOR.rgb;
        if (cls < 0.35) {
            float k = fract(sin(dot(floor(v_world.xz / 4.0) + floor(v_world.y / 2.5), vec2(12.9898, 78.233))) * 43758.5);
            ALBEDO = c * 0.3; ROUGHNESS = 0.1; METALLIC = 0.2;
            EMISSION = vec3(1.0, 0.82, 0.58) * night * (0.6 + 1.2 * step(0.25, k));
        } else if (cls < 0.6) {
            ALBEDO = c; ROUGHNESS = 0.85;
        } else if (cls < 0.85) {
            ALBEDO = c; ROUGHNESS = 0.45;
        } else {
            ALBEDO = c; ROUGHNESS = 0.6; METALLIC = 0.3;
        }
    }
}
"""


func _make_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = SHIP_SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


# ------------------------------------------------------------------ geometry

const G := 0.2      # glass
const D := 0.5      # deck, timber
const P := 0.7      # paint
const S := 1.0      # steel


func _tri(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, nrm: Vector3, col: Color, cls: float) -> void:
	if (b - a).cross(c - a).dot(nrm) > 0.0:
		var t := b
		b = c
		c = t
	var lc := col.srgb_to_linear()
	lc.a = cls
	for p: Vector3 in [a, b, c]:
		st.set_normal(nrm)
		st.set_color(lc)
		st.add_vertex(p)


func _box(st: SurfaceTool, c: Vector3, s: Vector3, col: Color, cls: float) -> void:
	var h := s * 0.5
	for axis in 3:
		for sgn in [-1.0, 1.0]:
			var nrm := Vector3.ZERO
			nrm[axis] = sgn
			var u := Vector3.ZERO
			u[(axis + 1) % 3] = h[(axis + 1) % 3]
			var v := Vector3.ZERO
			v[(axis + 2) % 3] = h[(axis + 2) % 3]
			var o := c + nrm * h[axis]
			_tri(st, o - u - v, o + u - v, o + u + v, nrm, col, cls)
			_tri(st, o - u - v, o + u + v, o - u + v, nrm, col, cls)


## A pointed end: from a hull face (beam wide, bottom..top at z) to a stem
## `length` further along `dir` (+1 bow, -1 stern).
func _end(st: SurfaceTool, z: float, dir: float, bottom: float, top: float, beam: float, length: float,
		col: Color, cls: float, x: float = 0.0) -> void:
	var hb := beam * 0.5
	var bl := Vector3(x - hb, bottom, z)
	var br := Vector3(x + hb, bottom, z)
	var tl := Vector3(x - hb, top, z)
	var tr := Vector3(x + hb, top, z)
	var sb := Vector3(x, bottom, z + dir * length * 0.65)
	var stp := Vector3(x, top, z + dir * length)
	var tris := [[tl, stp, tr], [bl, br, sb], [tl, bl, sb], [tl, sb, stp], [tr, stp, sb], [tr, sb, br]]
	var centre := (bl + br + tl + tr + sb + stp) / 6.0
	for t in tris:
		var nrm: Vector3 = ((t[1] - t[0]).cross(t[2] - t[0])).normalized()
		if nrm.dot((t[0] + t[1] + t[2]) / 3.0 - centre) < 0.0:
			nrm = -nrm
		_tri(st, t[0], t[1], t[2], nrm, col, cls)


## A hull: a box amidships and a pointed end each way, `boot` the dark band
## at the waterline under the paint.
func _hull(st: SurfaceTool, L: float, B: float, top: float, body_f: float, bow_l: float, stern_l: float,
		paint: Color, boot: Color, boot_top: float) -> void:
	var body := L * body_f
	var zc := (stern_l - bow_l) * 0.5     # body centred between its ends
	_box(st, Vector3(0, (boot_top - 1.5) * 0.5, zc), Vector3(B, boot_top + 1.5, body), boot, S)
	_box(st, Vector3(0, (boot_top + top) * 0.5, zc), Vector3(B, top - boot_top, body), paint, P)
	_end(st, zc + body * 0.5, 1.0, -1.5, boot_top, B, bow_l, boot, S)
	_end(st, zc + body * 0.5, 1.0, boot_top, top, B, bow_l, paint, P)
	_end(st, zc - body * 0.5, -1.0, -1.5, boot_top, B, stern_l, boot, S)
	_end(st, zc - body * 0.5, -1.0, boot_top, top, B, stern_l, paint, P)


const WHITE := Color(0.93, 0.93, 0.91)
const GLASS := Color(0.32, 0.38, 0.42)
const BLACK := Color(0.07, 0.07, 0.08)


func _build_kinds() -> void:
	_proc_kind("vapur", 7.5, func(st: SurfaceTool) -> Array:
		var L := 70.0
		var B := 13.5
		_hull(st, L, B, 3.4, 0.74, 9.0, 9.0, WHITE, BLACK, 1.1)
		_box(st, Vector3(0, 3.55, 0), Vector3(B * 0.98, 0.3, L * 0.78), Color(0.55, 0.52, 0.48), D)
		# Main and upper passenger decks, glazed all round.
		_box(st, Vector3(0, 5.0, 0), Vector3(B * 0.92, 2.6, L * 0.70), WHITE, P)
		_box(st, Vector3(0, 5.0, 0), Vector3(B * 0.93, 1.2, L * 0.66), GLASS, G)
		_box(st, Vector3(0, 6.4, 0), Vector3(B * 0.97, 0.25, L * 0.74), WHITE, P)
		_box(st, Vector3(0, 7.65, 0), Vector3(B * 0.86, 2.3, L * 0.56), WHITE, P)
		_box(st, Vector3(0, 7.7, 0), Vector3(B * 0.87, 1.1, L * 0.52), GLASS, G)
		_box(st, Vector3(0, 8.9, 0), Vector3(B * 0.9, 0.2, L * 0.62), Color(0.62, 0.62, 0.6), P)
		# A wheelhouse at each end, the funnel amidships, life rafts.
		for z in [-L * 0.25, L * 0.25]:
			_box(st, Vector3(0, 10.0, z), Vector3(B * 0.5, 2.0, 3.6), WHITE, P)
			_box(st, Vector3(0, 10.2, z), Vector3(B * 0.51, 0.9, 3.7), GLASS, G)
		_box(st, Vector3(0, 11.0, 0), Vector3(3.0, 4.2, 4.4), Color(0.93, 0.72, 0.16), P)
		_box(st, Vector3(0, 13.6, 0), Vector3(3.05, 1.0, 4.45), BLACK, S)
		_box(st, Vector3(0, 11.5, L * 0.12), Vector3(0.25, 5.0, 0.25), Color(0.85, 0.85, 0.85), S)
		for sx in [-1.0, 1.0]:
			for z in [-L * 0.16, L * 0.16]:
				_box(st, Vector3(sx * B * 0.38, 9.35, z), Vector3(1.4, 0.7, 2.6), Color(0.95, 0.45, 0.1), P)
		return [[Vector3(0, 1.0, 0), Vector3(B, 5.0, L)], [Vector3(0, 6.5, 0), Vector3(B * 0.92, 6.0, L * 0.7)],
			[Vector3(0, 11.5, 0), Vector3(3.0, 5.0, 4.4)]]
	, Vector3(70.0, 13.5, 14.1))
	_proc_kind("seabus", 13.0, func(st: SurfaceTool) -> Array:
		var L := 38.0
		var B := 11.0
		for sx in [-1.0, 1.0]:
			_box(st, Vector3(sx * 3.9, 0.3, -1.5), Vector3(2.8, 3.6, L - 6.0), WHITE, P)
			_end(st, L * 0.5 - 4.5, 1.0, -1.5, 2.1, 2.8, 5.0, WHITE, P, sx * 3.9)
		_box(st, Vector3(0, 2.6, -1.0), Vector3(B, 1.2, L * 0.86), WHITE, P)
		_box(st, Vector3(0, 2.0, -1.0), Vector3(B * 1.01, 0.35, L * 0.87), Color(0.1, 0.25, 0.6), P)
		_box(st, Vector3(0, 2.4, -1.0), Vector3(B * 1.01, 0.2, L * 0.87), Color(0.8, 0.12, 0.1), P)
		_box(st, Vector3(0, 4.3, -2.0), Vector3(B * 0.94, 2.4, L * 0.72), WHITE, P)
		_box(st, Vector3(0, 4.4, -2.0), Vector3(B * 0.95, 1.3, L * 0.7), GLASS, G)
		_box(st, Vector3(0, 6.2, 3.0), Vector3(B * 0.6, 1.5, 6.0), WHITE, P)
		_box(st, Vector3(0, 6.3, 3.0), Vector3(B * 0.61, 0.8, 6.1), GLASS, G)
		return [[Vector3(0, 2.5, 0), Vector3(B, 7.0, L)]]
	, Vector3(38.0, 11.0, 7.0))
	_proc_kind("motorboat", 8.0, func(st: SurfaceTool) -> Array:
		var L := 28.0
		var B := 7.0
		_hull(st, L, B, 2.2, 0.72, 5.5, 2.0, WHITE, Color(0.1, 0.27, 0.6), 1.3)
		_box(st, Vector3(0, 3.4, -2.0), Vector3(B * 0.9, 2.4, L * 0.6), WHITE, P)
		_box(st, Vector3(0, 3.5, -2.0), Vector3(B * 0.91, 1.1, L * 0.58), GLASS, G)
		_box(st, Vector3(0, 4.7, -2.0), Vector3(B * 0.95, 0.15, L * 0.66), Color(0.6, 0.6, 0.6), P)
		for z in [-9.0, -3.0, 3.0]:
			for sx in [-1.0, 1.0]:
				_box(st, Vector3(sx * B * 0.42, 5.6, z), Vector3(0.12, 1.8, 0.12), Color(0.8, 0.8, 0.8), S)
		_box(st, Vector3(0, 6.55, -3.0), Vector3(B * 0.95, 0.12, 14.0), Color(0.15, 0.32, 0.62), P)
		return [[Vector3(0, 2.0, 0), Vector3(B, 5.0, L)]]
	, Vector3(28.0, 7.0, 6.6))
	_proc_kind("tour", 5.0, func(st: SurfaceTool) -> Array:
		var L := 40.0
		var B := 9.0
		_hull(st, L, B, 2.4, 0.74, 7.0, 3.0, WHITE, Color(0.55, 0.12, 0.1), 1.2)
		_box(st, Vector3(0, 3.6, -1.0), Vector3(B * 0.92, 2.4, L * 0.7), WHITE, P)
		_box(st, Vector3(0, 3.7, -1.0), Vector3(B * 0.93, 1.2, L * 0.66), GLASS, G)
		_box(st, Vector3(0, 4.9, -1.0), Vector3(B * 0.96, 0.15, L * 0.74), Color(0.55, 0.42, 0.3), D)
		for z in [-12.0, -6.0, 0.0, 6.0]:
			for sx in [-1.0, 1.0]:
				_box(st, Vector3(sx * B * 0.44, 5.9, z), Vector3(0.12, 2.0, 0.12), Color(0.8, 0.8, 0.8), S)
		for k in 6:
			var c := Color(0.85, 0.15, 0.12) if k % 2 == 0 else WHITE
			_box(st, Vector3(0, 7.0, -14.0 + k * 4.0), Vector3(B * 0.98, 0.15, 4.0), c, P)
		_box(st, Vector3(0, 5.5, 11.5), Vector3(B * 0.5, 1.6, 3.0), WHITE, P)
		_box(st, Vector3(0, 5.6, 11.5), Vector3(B * 0.51, 0.8, 3.1), GLASS, G)
		return [[Vector3(0, 2.5, 0), Vector3(B, 6.0, L)]]
	, Vector3(40.0, 9.0, 7.1))
	for paint in [["a", Color(0.12, 0.12, 0.13), Color(0.55, 0.1, 0.08)], ["b", Color(0.45, 0.08, 0.07), Color(0.12, 0.12, 0.13)],
			["c", Color(0.1, 0.2, 0.35), Color(0.5, 0.12, 0.1)]]:
		var hull_c: Color = paint[1]
		var boot_c: Color = paint[2]
		_proc_kind("tanker_" + str(paint[0]), 5.5, func(st: SurfaceTool) -> Array:
			var L := 183.0
			var B := 32.0
			_hull(st, L, B, 9.0, 0.84, 20.0, 9.0, hull_c, boot_c, 2.5)
			_box(st, Vector3(0, 9.05, -3.0), Vector3(B * 0.98, 0.1, L * 0.86), Color(0.32, 0.42, 0.32), D)
			_box(st, Vector3(0, 10.0, 6.0), Vector3(2.2, 1.6, L * 0.62), Color(0.6, 0.6, 0.55), S)
			for k in 6:
				_box(st, Vector3(0, 10.6, -40.0 + k * 22.0), Vector3(B * 0.7, 0.5, 1.0), Color(0.6, 0.6, 0.55), S)
			_box(st, Vector3(0, 10.5, L * 0.5 - 26.0), Vector3(B * 0.9, 3.0, 9.0), hull_c, P)
			_box(st, Vector3(0, 16.5, -L * 0.5 + 18.0), Vector3(B * 0.82, 15.0, 16.0), WHITE, P)
			for k in 5:
				_box(st, Vector3(0, 11.5 + k * 2.8, -L * 0.5 + 18.0), Vector3(B * 0.83, 1.0, 16.1), GLASS, G)
			_box(st, Vector3(0, 24.4, -L * 0.5 + 21.0), Vector3(B, 0.8, 5.0), WHITE, P)
			_box(st, Vector3(0, 26.0, -L * 0.5 + 9.0), Vector3(5.0, 8.0, 5.5), hull_c, P)
			return [[Vector3(0, 3.5, 0), Vector3(B, 11.0, L)], [Vector3(0, 17.0, -L * 0.5 + 16.0), Vector3(B * 0.85, 16.0, 20.0)]]
		, Vector3(183.0, 32.0, 30.0))
		_proc_kind("bulk_" + str(paint[0]), 5.5, func(st: SurfaceTool) -> Array:
			var L := 190.0
			var B := 32.0
			_hull(st, L, B, 10.0, 0.84, 20.0, 9.0, hull_c, boot_c, 3.0)
			_box(st, Vector3(0, 10.05, -3.0), Vector3(B * 0.98, 0.1, L * 0.86), Color(0.4, 0.38, 0.35), D)
			for k in 6:
				var z := -48.0 + k * 22.0
				_box(st, Vector3(0, 11.2, z), Vector3(B * 0.72, 2.2, 16.0), Color(0.55, 0.2, 0.12), P)
				if k < 4:
					# Deck cranes between the holds.
					_box(st, Vector3(0, 15.0, z + 11.0), Vector3(2.5, 10.0, 2.5), Color(0.9, 0.7, 0.15), P)
					_box(st, Vector3(0, 21.0, z + 2.0), Vector3(0.9, 0.9, 20.0), Color(0.9, 0.7, 0.15), P)
			_box(st, Vector3(0, 18.0, -L * 0.5 + 16.0), Vector3(B * 0.82, 16.0, 14.0), WHITE, P)
			for k in 5:
				_box(st, Vector3(0, 12.5 + k * 3.0, -L * 0.5 + 16.0), Vector3(B * 0.83, 1.0, 14.1), GLASS, G)
			_box(st, Vector3(0, 28.0, -L * 0.5 + 8.0), Vector3(5.0, 8.0, 5.0), hull_c, P)
			return [[Vector3(0, 4.0, 0), Vector3(B, 12.0, L)], [Vector3(0, 18.0, -L * 0.5 + 16.0), Vector3(B * 0.85, 18.0, 16.0)],
				[Vector3(0, 16.0, 0), Vector3(4.0, 12.0, L * 0.5)]]
		, Vector3(190.0, 32.0, 32.0))
	_proc_kind("container", 6.5, func(st: SurfaceTool) -> Array:
		var L := 150.0
		var B := 25.0
		_hull(st, L, B, 9.0, 0.82, 18.0, 8.0, Color(0.12, 0.22, 0.34), Color(0.5, 0.12, 0.1), 2.5)
		var cols := [Color(0.7, 0.15, 0.1), Color(0.15, 0.3, 0.6), Color(0.85, 0.55, 0.15), Color(0.3, 0.5, 0.3),
			Color(0.85, 0.85, 0.85), Color(0.5, 0.2, 0.45), Color(0.2, 0.2, 0.22)]
		var z := -L * 0.5 + 30.0
		var bay := 0
		while z < L * 0.5 - 20.0:
			var tiers := 2 + (bay * 7) % 3
			for row in 8:
				var x := (float(row) - 3.5) * (B / 8.4)
				for tier in tiers:
					_box(st, Vector3(x, 10.3 + tier * 2.6, z), Vector3(B / 8.4 - 0.15, 2.5, 12.0),
						cols[(bay * 3 + row * 5 + tier) % cols.size()], P)
			z += 12.6
			bay += 1
		_box(st, Vector3(0, 16.0, -L * 0.5 + 16.0), Vector3(B * 0.9, 14.0, 10.0), WHITE, P)
		for k in 4:
			_box(st, Vector3(0, 11.5 + k * 3.0, -L * 0.5 + 16.0), Vector3(B * 0.91, 1.0, 10.1), GLASS, G)
		_box(st, Vector3(0, 25.0, -L * 0.5 + 8.0), Vector3(4.0, 6.0, 5.0), BLACK, S)
		return [[Vector3(0, 4.0, 0), Vector3(B, 11.0, L)], [Vector3(0, 14.0, 0), Vector3(B, 9.0, L * 0.7)],
			[Vector3(0, 16.0, -L * 0.5 + 16.0), Vector3(B * 0.9, 14.0, 10.0)]]
	, Vector3(150.0, 25.0, 28.0))
	for m in ["ship_cargo", "ship_coaster", "ship_tug", "boat_pilot", "boat_huri", "boat_skiff",
			"boat_cabin", "boat_rusty", "boat_work", "boat_trawler"]:
		_model_kind(m)


func _proc_kind(kind: String, v: float, maker: Callable, dims: Vector3) -> void:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var solids: Array = maker.call(st)
	var mesh := st.commit()
	mesh.surface_set_material(0, _ship_mat)
	_kinds[kind] = {"mesh": mesh, "dims": dims, "solids": solids, "v": v}


func _model_kind(model: String) -> void:
	var m := ModelKit.instance(model)
	if m == null:
		return
	var ab := ModelKit.aabb(m)
	# One surface per material, every mesh's transform baked in: a model of
	# 300 little meshes (ship_coaster) is then a handful of draws however
	# many copies float.
	var by_mat := {}
	for mi in ModelKit.meshes(m):
		var xf := ModelKit._to(m, mi)
		for si in mi.mesh.get_surface_count():
			if mi.mesh.surface_get_primitive_type(si) != Mesh.PRIMITIVE_TRIANGLES:
				continue
			var mat := mi.get_active_material(si)
			if not by_mat.has(mat):
				var st := SurfaceTool.new()
				st.begin(Mesh.PRIMITIVE_TRIANGLES)
				by_mat[mat] = st
			(by_mat[mat] as SurfaceTool).append_from(mi.mesh, si, xf)
	var merged := ArrayMesh.new()
	for mat in by_mat:
		var st: SurfaceTool = by_mat[mat]
		st.commit(merged)
		merged.surface_set_material(merged.get_surface_count() - 1, mat)
	var parts: Array = [[merged, Transform3D.IDENTITY]]
	m.free()
	var v := {"ship_cargo": 5.0, "ship_coaster": 5.0, "ship_tug": 4.0, "boat_pilot": 7.0}.get(model, 2.5) as float
	_kinds[model] = {"model": model, "parts": parts, "dims": Vector3(ab.size.z, ab.size.x, ab.end.y),
		"solids": [[ab.get_center(), ab.size]], "v": v}


# ------------------------------------------------------------------ placement

func _add(kind: String, pos: Vector2, heading: Vector2, underway: bool, route: int = -1) -> int:
	if not _kinds.has(kind):
		return -1
	var h := heading.normalized()
	_s_kind.append(kind)
	_s_pos.append(pos)
	_s_yaw.append(atan2(h.x, -h.y))
	_s_under.append(1 if underway else 0)
	_s_v.append(float(_kinds[kind]["v"]) if underway else 0.0)
	_s_route.append(route)
	_s_wp.append(0)
	_s_dir.append(1)
	_s_dwell.append(0.0)
	var dims: Vector3 = _kinds[kind]["dims"]
	var rid := RID()
	if dims.x >= SOLID_FROM_M:
		rid = PhysicsServer3D.body_create()
		PhysicsServer3D.body_set_mode(rid, PhysicsServer3D.BODY_MODE_KINEMATIC)
		PhysicsServer3D.body_set_collision_layer(rid, LAYER_STRUCTURE)
		PhysicsServer3D.body_set_collision_mask(rid, 0)
		for part: Array in _kinds[kind]["solids"]:
			var sh := PhysicsServer3D.box_shape_create()
			PhysicsServer3D.shape_set_data(sh, (part[1] as Vector3) * 0.5)
			PhysicsServer3D.body_add_shape(rid, sh, Transform3D(Basis(), part[0]))
			_shapes.append(sh)
		PhysicsServer3D.body_set_space(rid, get_world_3d().space)
	_s_body.append(rid)
	return _s_kind.size() - 1


func _water(p: Vector2) -> bool:
	return bool(env.call("is_water", p.x, p.y))


## The hull's footprint (and `margin` m round it) all water.
func _fits(pos: Vector2, heading: Vector2, L: float, B: float, margin: float) -> bool:
	var f := heading.normalized()
	var r := Vector2(f.y, -f.x)
	for a in [-0.5, -0.25, 0.0, 0.25, 0.5]:
		for b in [-0.5, 0.0, 0.5]:
			var q: Vector2 = pos + f * (a * L + signf(a) * margin) + r * (b * B + signf(b) * margin)
			if not _water(q):
				return false
	return true


func _clear_of_others(pos: Vector2, L: float) -> bool:
	for i in _s_pos.size():
		var other: Vector3 = _kinds[_s_kind[i]]["dims"]
		if _s_pos[i].distance_to(pos) < (L + other.x) * 0.5 + 6.0:
			return false
	return true


## Unit vector from p toward open water (mean of the water directions on a ring).
func _to_water(p: Vector2, r: float) -> Vector2:
	var acc := Vector2.ZERO
	for k in 24:
		var d := Vector2.from_angle(TAU * k / 24.0)
		if _water(p + d * r):
			acc += d
	return acc.normalized() if acc.length() > 0.5 else Vector2.ZERO


func _place() -> void:
	var b: Rect2 = env.call("sim_bounds")
	var inner := b.grow(-150.0)
	_load_routes(inner)
	_place_berths()
	_place_route_traffic()
	_place_bosphorus()
	_place_anchorage()
	_place_small_craft()


func _load_routes(inner: Rect2) -> void:
	var f := FileAccess.open("res://assets/istanbul/roads.json", FileAccess.READ)
	if f == null:
		return
	var doc = JSON.parse_string(f.get_as_text())
	if not (doc is Dictionary):
		return
	var ends: Array = []
	for r: Array in doc.get("ferries", []):
		var pts := PackedVector2Array()
		for q: Array in r:
			pts.append(Vector2(float(q[0]), float(q[1])))
		if pts.size() < 2:
			continue
		var kind := _route_kind(pts)
		_routes.append({"pts": pts, "kind": kind, "len": _plen(pts)})
		for e in [pts[0], pts[pts.size() - 1]]:
			if not inner.has_point(e):
				continue
			var dup := false
			for o: Array in ends:
				if (o[0] as Vector2).distance_to(e) < 45.0:
					dup = true
					break
			if not dup:
				ends.append([e, kind])
	_berths = ends


static func _plen(pts: PackedVector2Array) -> float:
	var s := 0.0
	for i in pts.size() - 1:
		s += pts[i].distance_to(pts[i + 1])
	return s


## Which craft works a line: sea buses from the IDO terminals (Kadikoy's sea
## bus pier, Yenikapi), private motorboats from the Turyol piers, else the
## city's ferries.
func _route_kind(pts: PackedVector2Array) -> String:
	for e in [pts[0], pts[pts.size() - 1]]:
		if e.distance_to(Vector2(3206.0, -1940.0)) < 60.0 or e.distance_to(Vector2(-2058.0, -735.0)) < 120.0 \
				or e.distance_to(Vector2(-2019.0, -794.0)) < 120.0:
			return "seabus"
		if e.distance_to(Vector2(3672.0, -1647.0)) < 50.0 or e.distance_to(Vector2(3385.0, -1922.0)) < 50.0:
			return "motorboat"
	return "vapur"


## A boat alongside most berths, beam-on to the quay.
func _place_berths() -> void:
	for br: Array in _berths:
		if _rng.randf() > 0.8:
			continue
		var p: Vector2 = br[0]
		var kind: String = br[1]
		var dims: Vector3 = _kinds[kind]["dims"]
		var out := _to_water(p, 60.0)
		if out == Vector2.ZERO:
			continue
		# From the berth point out to clear water, then half a beam more.
		var t := 0.0
		while t < 80.0 and not (_water(p + out * t) and _water(p + out * (t + 3.0))):
			t += 1.0
		var along := Vector2(out.y, -out.x) * (1.0 if _rng.randf() < 0.5 else -1.0)
		for shift in [0.0, dims.x * 0.3, -dims.x * 0.3]:
			var c: Vector2 = p + out * (t + dims.y * 0.5 + 1.5) + along * shift
			if _fits(c, along, dims.x, dims.y, 1.0) and _clear_of_others(c, dims.x):
				_add(kind, c, along, false)
				break


## Ferries, sea buses and motorboats mid-crossing on their lines.
func _place_route_traffic() -> void:
	var b: Rect2 = (env.call("sim_bounds") as Rect2).grow(1500.0)
	var n := 0
	for ri in _routes.size():
		var r: Dictionary = _routes[ri]
		var L: float = r["len"]
		if L < 900.0 or L > 16000.0:
			continue
		var kind: String = r["kind"]
		var dims: Vector3 = _kinds[kind]["dims"]
		for attempt in 6:
			var frac := _rng.randf_range(0.25, 0.75)
			var at := _along(r["pts"], frac * L)
			var pos: Vector2 = at[0]
			if not b.has_point(pos):
				continue
			if _fits(pos, at[1], dims.x, dims.y, 25.0) and _clear_of_others(pos, dims.x * 3.0):
				var i := _add(kind, pos, at[1], true, ri)
				_s_wp[i] = int(at[2]) + 1
				n += 1
				break
		if n >= 16:
			break


## [position, heading, segment index] at distance d along pts.
static func _along(pts: PackedVector2Array, d: float) -> Array:
	var acc := 0.0
	for i in pts.size() - 1:
		var seg := pts[i].distance_to(pts[i + 1])
		if acc + seg >= d or i == pts.size() - 2:
			var t := clampf((d - acc) / maxf(seg, 0.01), 0.0, 1.0)
			return [pts[i].lerp(pts[i + 1], t), pts[i + 1] - pts[i], i]
		acc += seg
	return [pts[0], pts[1] - pts[0], 0]


## The Bosphorus' centre line, north to south (ENU m): the widest water on
## each 300 m row of the scene's mask, smoothed by hand; Kiz Kulesi stays to
## the east.
const BOSPHORUS := [[5912, 7000], [5420, 5500], [5060, 4500], [4560, 4050], [3760, 3700], [3180, 3400],
	[2670, 3100], [2300, 2650], [2000, 2200], [1720, 1900], [1640, 1400], [1750, 700], [1800, 0],
	[1900, -1200], [2100, -3000]]


func _lane(offset: float, southbound: bool) -> PackedVector2Array:
	var c := PackedVector2Array()
	for q in BOSPHORUS:
		c.append(Vector2(float(q[0]), float(q[1])))
	var out := PackedVector2Array()
	for i in c.size():
		var d := (c[mini(i + 1, c.size() - 1)] - c[maxi(i - 1, 0)]).normalized()
		var right := Vector2(d.y, -d.x)          # right of a southbound ship: the European (west) side
		out.append(c[i] + right * offset)
	if not southbound:
		out.reverse()
	return out


## Ships transiting the Bosphorus, keeping to starboard: southbound on the
## European side, northbound on the Asian.
func _place_bosphorus() -> void:
	# Southbound in the west lane, northbound in the east one.
	var lanes := [_lane(140.0, true), _lane(-140.0, false)]
	var ri0 := _routes.size()
	_routes.append({"pts": lanes[0], "kind": "fairway", "len": _plen(lanes[0]), "next": ri0 + 1})
	_routes.append({"pts": lanes[1], "kind": "fairway", "len": _plen(lanes[1]), "next": ri0})
	var traffic := [["tanker_a", 0, 0.12], ["bulk_b", 1, 0.30], ["container", 0, 0.55], ["ship_cargo", 1, 0.62],
		["tanker_c", 1, 0.08], ["ship_coaster", 0, 0.80], ["ship_tug", 1, 0.45], ["boat_pilot", 0, 0.38]]
	for t: Array in traffic:
		var ri: int = ri0 + int(t[1])
		var r: Dictionary = _routes[ri]
		var dims: Vector3 = _kinds[t[0]]["dims"] if _kinds.has(t[0]) else Vector3(100, 20, 20)
		for k in 8:
			var at := _along(r["pts"], (float(t[2]) + k * 0.03) * float(r["len"]))
			if _fits(at[0], at[1], dims.x, dims.y, 30.0) and _clear_of_others(at[0], dims.x * 2.0):
				var i := _add(t[0], at[0], at[1], true, ri)
				if i >= 0:
					_s_wp[i] = int(at[2]) + 1
				break


## At anchor in the Marmara off Ahirkapi and Yenikapi, bows to the
## north-east into the Bosphorus' outflow.
const ANCHORAGE := [[-5601, -5340], [1913, -3647], [-5512, -4378], [-1470, -5717], [1213, -5943],
	[-2392, -3968], [-1978, -3625], [-3516, -3322], [810, -5066], [-3367, -4961], [2863, -3633],
	[-1551, -2711], [-4324, -3413], [669, -2482]]


func _place_anchorage() -> void:
	var kinds := ["bulk_a", "tanker_b", "ship_cargo", "bulk_c", "tanker_a", "container", "ship_coaster",
		"bulk_b", "tanker_c"]
	var k := 0
	for q in ANCHORAGE:
		var p := Vector2(float(q[0]), float(q[1]))
		var kind: String = kinds[k % kinds.size()]
		var hdg := deg_to_rad(40.0 + _rng.randf_range(-18.0, 18.0))
		var f := Vector2(sin(hdg), cos(hdg))
		var dims: Vector3 = _kinds[kind]["dims"] if _kinds.has(kind) else Vector3(150, 25, 20)
		if _fits(p, f, dims.x, dims.y, 40.0):
			_add(kind, p, f, false)
		k += 1


## Small craft along the shores of the harbours (ENU rects) and a few tour
## boats waiting off Eminonu and Ortakoy.
const HARBOURS := [
	[Rect2(-700, 1250, 650, 450), 16],      # Karakoy's quays, the Golden Horn's mouth
	[Rect2(-1100, 950, 700, 260), 10],      # Eminonu
	[Rect2(-2700, 2200, 900, 700), 14],     # Fener and Balat
	[Rect2(-1900, 2000, 700, 700), 10],     # Kasimpasa and Haskoy
	[Rect2(-1500, -850, 700, 450), 16],     # Kumkapi fishing harbour
	[Rect2(2900, -2300, 800, 500), 12],     # Kadikoy and Moda
	[Rect2(3400, 4050, 700, 500), 8],       # Ortakoy
	[Rect2(2200, 1700, 900, 600), 6],       # Uskudar
]
const SMALL := ["boat_huri", "boat_skiff", "boat_cabin", "boat_rusty", "boat_work", "boat_huri", "boat_skiff",
	"boat_trawler"]


func _place_small_craft() -> void:
	for h: Array in HARBOURS:
		var r: Rect2 = h[0]
		var want: int = h[1]
		var got := 0
		var tries := 0
		while got < want and tries < 600:
			tries += 1
			var p := Vector2(_rng.randf_range(r.position.x, r.end.x), _rng.randf_range(r.position.y, r.end.y))
			if not _water(p):
				continue
			var out := _to_water(p, 14.0)
			if out == Vector2.ZERO:
				continue
			# A berth: land within ~12 m, on the land side only.
			var land := false
			for d in [6.0, 10.0, 14.0]:
				if not _water(p - out * d):
					land = true
					break
			if not land:
				continue
			var kind: String = SMALL[_rng.randi() % SMALL.size()]
			if not _kinds.has(kind):
				continue
			var dims: Vector3 = _kinds[kind]["dims"]
			var along := Vector2(out.y, -out.x) * (1.0 if _rng.randf() < 0.5 else -1.0)
			var c := p + out * (dims.y * 0.5)
			if _fits(c, along, dims.x, dims.y, 0.5) and _clear_of_others(c, dims.x):
				_add(kind, c, along, false)
				got += 1
				# Fishing boats raft up two or three abreast.
				var off := dims.y * 0.5
				for raft in 2:
					if _rng.randf() > 0.55 or got >= want:
						break
					var k2: String = SMALL[_rng.randi() % SMALL.size()]
					var d2: Vector3 = _kinds[k2]["dims"]
					var c2 := p + out * (off + 0.6 + d2.y * 0.5) + along * _rng.randf_range(-1.0, 1.0)
					if not _fits(c2, along, d2.x, d2.y, 0.3):
						break
					_add(k2, c2, along, false)
					off += 0.6 + d2.y
					got += 1
	# Tour boats waiting offshore (Bosphorus cruises leave Eminonu).
	for q in [[-150, 1180, 30.0], [3600, 3950, 140.0], [900, 2350, 200.0]]:
		var p := Vector2(float(q[0]), float(q[1]))
		var hdg := deg_to_rad(float(q[2]))
		var f := Vector2(sin(hdg), cos(hdg))
		if _fits(p, f, 40.0, 9.0, 10.0) and _clear_of_others(p, 40.0):
			_add("tour", p, f, false)


# ------------------------------------------------------------------ drawing

func _xf(i: int) -> Transform3D:
	var p := _s_pos[i]
	return Transform3D(Basis(Vector3.UP, _s_yaw[i]), Vector3(p.x, float(env.get("SEA_Y")), -p.y))


## Draws are grouped by kind and by CELL m square, so each MultiMesh has a
## small bounding box and its visibility range culls by distance (one
## MultiMesh across the whole city is drawn whole or not at all). Ships
## that move (--world-life) share one group per kind.
const CELL := 1500.0


func _draw_all() -> void:
	var groups := {}
	for i in _s_kind.size():
		var cell := Vector2i(floori(_s_pos[i].x / CELL), floori(_s_pos[i].y / CELL))
		if moving and _s_route[i] >= 0:
			cell = Vector2i(99999, 99999)
		var key := [_s_kind[i], cell]
		if not groups.has(key):
			groups[key] = PackedInt32Array()
		var a: PackedInt32Array = groups[key]
		a.append(i)
		groups[key] = a
	var j := 0
	for key: Array in groups:
		var kind: String = key[0]
		var ids: PackedInt32Array = groups[key]
		var k: Dictionary = _kinds[kind]
		var parts: Array = [[k["mesh"], Transform3D.IDENTITY]] if k.has("mesh") else k["parts"]
		var L: float = (k["dims"] as Vector3).x
		for part: Array in parts:
			var mm := MultiMesh.new()
			mm.transform_format = MultiMesh.TRANSFORM_3D
			mm.mesh = part[0]
			mm.instance_count = ids.size()
			var mmi := MultiMeshInstance3D.new()
			# "ship_"/"boat_" names: thermal_kit reads the models as boats.
			mmi.name = ("ship_" if not kind.begins_with("boat_") else "") + "%s_%d" % [kind, j]
			mmi.multimesh = mm
			mmi.visibility_range_end = 9000.0 if L > 100.0 else (4000.0 if L > 30.0 else 1300.0)
			mmi.visibility_range_end_margin = 150.0
			mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
			# Shadows from the procedural hulls (a few hundred triangles); the
			# downloaded models are 10-30 k triangles each, drawn again per
			# shadow cascade, so they cast none.
			mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if k.has("mesh") \
				else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			add_child(mmi)
			if not _mmi.has(kind):
				_mmi[kind] = []
			(_mmi[kind] as Array).append([mmi, part[1], ids])
			j += 1
	# Wakes of everything underway.
	_wake_mm = MultiMesh.new()
	_wake_mm.transform_format = MultiMesh.TRANSFORM_3D
	var qm := PlaneMesh.new()
	qm.size = Vector2(1.0, 1.0)
	var wm := ShaderMaterial.new()
	var sh := Shader.new()
	sh.code = WAKE_SHADER
	wm.shader = sh
	qm.material = wm
	_wake_mm.mesh = qm
	_wake_mm.instance_count = _s_kind.size()
	var wmi := MultiMeshInstance3D.new()
	wmi.name = "wakes"
	wmi.multimesh = _wake_mm
	wmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(wmi)
	_update()


func _update() -> void:
	for kind: String in _mmi:
		for d: Array in _mmi[kind]:
			var mm: MultiMesh = (d[0] as MultiMeshInstance3D).multimesh
			var local: Transform3D = d[1]
			var ids: PackedInt32Array = d[2]
			for n in ids.size():
				mm.set_instance_transform(n, _xf(ids[n]) * local)
	var sea_y := float(env.get("SEA_Y"))
	for i in _s_kind.size():
		var dims: Vector3 = _kinds[_s_kind[i]]["dims"]
		var v := _s_v[i] if _s_v[i] > 0.0 else 0.0
		if _s_under[i] == 0 or (moving and v < 0.5):
			_wake_mm.set_instance_transform(i, Transform3D(Basis.from_scale(Vector3(0.001, 0.001, 0.001)),
				Vector3(0, -50, 0)))
		else:
			var spd := float(_kinds[_s_kind[i]]["v"])
			var wl := clampf(dims.x * 1.8, 40.0, 320.0) * clampf(spd / 6.0, 0.5, 1.4)
			var yb := Basis(Vector3.UP, _s_yaw[i])
			var fwd := yb.z
			var p := _s_pos[i]
			var stern := Vector3(p.x, sea_y + 0.05, -p.y) - fwd * (dims.x * 0.42)
			_wake_mm.set_instance_transform(i, Transform3D(yb * Basis.from_scale(
				Vector3(maxf(dims.y * 2.2, wl * 0.55), 1.0, wl)), stern - fwd * (wl * 0.5)))
		if _s_body[i].is_valid():
			PhysicsServer3D.body_set_state(_s_body[i], PhysicsServer3D.BODY_STATE_TRANSFORM, _xf(i))


func _process(_dt: float) -> void:
	var nt := clampf(float(env.call("night_amount")), 0.0, 1.0)
	if absf(nt - _night_last) > 0.01:
		_night_last = nt
		_ship_mat.set_shader_parameter("night", nt)


# ------------------------------------------------------------------ moving (--world-life)

func _physics_process(dt: float) -> void:
	if not moving:
		return
	for i in _s_kind.size():
		var ri := _s_route[i]
		if ri < 0:
			continue
		if _s_dwell[i] > 0.0:
			_s_dwell[i] -= dt
			_s_v[i] = 0.0
			continue
		var r: Dictionary = _routes[ri]
		var pts: PackedVector2Array = r["pts"]
		var wp := clampi(_s_wp[i], 0, pts.size() - 1)
		var to := pts[wp] - _s_pos[i]
		var dims: Vector3 = _kinds[_s_kind[i]]["dims"]
		if to.length() < maxf(30.0, dims.x * 0.6):
			var nxt := wp + _s_dir[i]
			if nxt >= pts.size() or nxt < 0:
				if r.has("next"):
					_s_route[i] = int(r["next"])
					nxt = 1
				else:
					# A ferry at its berth: tie up, then back the other way.
					_s_dir[i] = -_s_dir[i]
					nxt = wp + _s_dir[i]
					_s_dwell[i] = FERRY_DWELL_S
			_s_wp[i] = clampi(nxt, 0, (_routes[_s_route[i]]["pts"] as PackedVector2Array).size() - 1)
			continue
		var want := atan2(to.x, -to.y)
		var rate := clampf(9.0 / dims.x, 0.02, 0.25)
		var err := wrapf(want - _s_yaw[i], -PI, PI)
		_s_yaw[i] += clampf(err, -rate * dt, rate * dt)
		var v0 := float(_kinds[_s_kind[i]]["v"])
		_s_v[i] = move_toward(_s_v[i], v0 * (0.4 if absf(err) > 0.8 else 1.0), 0.4 * dt)
		var fwd := Vector2(sin(_s_yaw[i]), -cos(_s_yaw[i]))
		_s_pos[i] += fwd * _s_v[i] * dt
	_update()


const WAKE_SHADER := """
shader_type spatial;
render_mode blend_mix, cull_disabled, depth_draw_never, shadows_disabled;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 v_world;
void vertex() {
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}
void fragment() {
    // UV.y 0 at the far end, 1 at the stern; UV.x across. Two arms at the
    // Kelvin angle fading astern, churned water down the middle.
    float along = 1.0 - UV.y;
    float across = abs(UV.x - 0.5) * 2.0;
    float arm = exp(-pow((across - along * 0.95) * 9.0, 2.0)) * (1.0 - along);
    float churn = exp(-pow(across * 3.5, 2.0)) * pow(1.0 - along, 2.5);
    vec2 q = v_world.xz / 2.5 + vec2(TIME * 0.4, 0.0);
    vec2 i = floor(q);
    vec2 f = q - i;
    f = f * f * (3.0 - 2.0 * f);
    float h00 = fract(sin(dot(i, vec2(12.98, 78.23))) * 43758.5);
    float h10 = fract(sin(dot(i + vec2(1.0, 0.0), vec2(12.98, 78.23))) * 43758.5);
    float h01 = fract(sin(dot(i + vec2(0.0, 1.0), vec2(12.98, 78.23))) * 43758.5);
    float h11 = fract(sin(dot(i + vec2(1.0, 1.0), vec2(12.98, 78.23))) * 43758.5);
    float n = mix(mix(h00, h10, f.x), mix(h01, h11, f.x), f.y);
    float a = clamp(max(arm * 0.7, churn) * (0.45 + 0.55 * n), 0.0, 1.0);
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Churned water is the sea's own temperature: no contrast to draw.
        discard;
    }
    ALBEDO = vec3(0.92, 0.95, 0.96);
    ROUGHNESS = 0.9;
    ALPHA = a * 0.5;
}
"""
