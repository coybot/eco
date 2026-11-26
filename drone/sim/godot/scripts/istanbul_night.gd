## Istanbul at night (env_istanbul): the city's lights, driven by
## env.night_amount() (world_clock's: 0 by day, 1 once the sun is 4 deg
## down). By day the whole node is hidden and no light is on, so the day scene
## is exactly as it was. Built on night_lights.gd (Manhattan's), whose sprite,
## pool and lamp-head materials and per-camera light pool it reuses; its hook
## (add_lights / add_pools) works here too, under the name "istanbul_night".
##
## WHAT IS LIT
##   STREET LAMPS (roads.json). Main roads (motorway, primary, secondary) keep
##     high-pressure sodium, 2000 K orange, on 10-12 m poles; a dual
##     carriageway (two one-way edges) lights from its median side, as the
##     coast roads do. The historic quarters (the walled peninsula and Galata,
##     OLD_CITY below) and every stone-paved street have warm 2700 K lanterns on
##     4.5 m posts, closer together; the other side streets 3000 K LED cobra-
##     heads. No lamps on bridges, in tunnels, below grade, on steps or paths,
##     or inside a building.
##   FLOODLIT MONUMENTS. Each is a FLOOD VOLUME: a box around the monument in
##     which every surface the depth buffer shows gets golden light from
##     fixtures at its foot (the surface normal from the depth derivatives,
##     lit from outside and below, brightest on faces turned out and up). It
##     needs no knowledge of the monument's mesh, so it lights whatever the
##     landmarks part draws there. From istanbul_landmarks.floodlit() when that
##     exists; else the mosques' footprints (b_kind 2) over 150 m2, each box
##     the footprint's minimum-area rectangle with room for the dome above the
##     data's flat 22 m (a dome rises to ~0.45 sqrt(area) + 12 m), Hagia
##     Irene, the palaces (b_kind 12) and Topkapi's courts, Galata Tower,
##     Beyazit Tower, the Maiden's Tower and the Valens Aqueduct.
##   MINARETS. Uplit (a slim flood volume each) and their serefe balconies as
##     rings of warm lamps: istanbul_landmarks.minaret_balconies() if present,
##     else features.json's minarets with heights and balcony counts by the
##     size of the mosque they stand by (66 m and three balconies beside an
##     imperial mosque, 30 m and one beside a neighbourhood mosque).
##   BRIDGES: istanbul_bridges.lights() if present (the necklaces).
##   SHIPS: istanbul_ships.lights() if present; else, when there is no
##     istanbul_ships node at all, the Ahirkapi anchorage off the peninsula:
##     a score of ships at anchor south of Sultanahmet with anchor, masthead
##     and deck lights (lights only: no hull is drawn, nothing is solid).
##     Ferry piers (roads.json piers) carry lamps along their edges.
##   RED OBSTRUCTION LIGHTS: buildings 45 m and taller that are not mosques,
##     churches or monuments (with Manhattan's shielding: a taller one within
##     150 m, by 10 m, covers it), the communication towers, and any red light
##     a bridge reports. L-864 rhythm on top, steady half way up past 100 m.
##   BEYAZIT TOWER's weather lights at its top: blue for clear, green for
##     rain, yellow for fog (the tower's real code), from fw_set_weather.
##   REFLECTIONS. Every light near the water (shore lamps, floodlit monuments,
##     balconies, bridge and ship lights) also lays a broken column of its
##     colour across the water toward the camera: where a ray from the camera
##     to the light's mirror image meets the sea, stretched by the chop's
##     slopes, cut by the env's water mask (no column on land) and the depth
##     buffer (none behind a building), shimmering. Screen-space reflection
##     (scene_look turns it on at night) cannot do this: the lamps are
##     additive sprites drawn after it.
##
## THERMAL. Sprites, pools, flood volumes and reflections are additive
## (blend_add): thermal_kit hides them from thermal cameras, as it does
## Manhattan's FAA lights. The only opaque geometry, the lamp heads and
## lanterns, is named lamp_heads* (hot when lit) and the posts lamp_poles*
## (steel). A floodlight warms nothing in the LWIR band.
##
## COST. All MultiMeshes in chunks with visibility ranges (as night_lights):
## the sprites carry the far city, the pools and geometry the near streets;
## the flood volumes and reflection columns are one draw each. --night-
## lights=off leaves all of it out.
extends "res://scripts/night_lights.gd"

const HPS := Color(1.0, 0.50, 0.17)          # high-pressure sodium, ~2000 K
const LANTERN := Color(1.0, 0.66, 0.34)      # 2700 K heritage lanterns
const LED_WARM := Color(1.0, 0.74, 0.50)     # 3000 K side-street LED
const FLOOD_GOLD := Color(1.0, 0.62, 0.26)   # the mosques' golden floodlight
const FLOOD_WHITE := Color(1.0, 0.85, 0.66)  # palaces, towers: warm white
const BALCONY := Color(1.0, 0.82, 0.55)
## istanbul_landmarks' floodlit() kinds: [colour, gain, box half-size / its
## radius]. A mosque's radius is its hall's half-size; the box reaches past
## the hall's corners to the domes' and the courtyard's edges.
const FLOOD_KINDS := {
	"mosque": [FLOOD_GOLD, 1.0, 1.45],
	"tower": [FLOOD_GOLD, 1.1, 1.0],
	"column": [FLOOD_WHITE, 0.9, 1.0],
	"palace": [FLOOD_WHITE, 0.8, 1.05],
	"wall": [FLOOD_GOLD, 0.55, 0.75],
	"fountain": [FLOOD_WHITE, 0.6, 1.1],
}
const IST_LAMPS := {
	# class: [spacing m, head height m, colour, both sides, style]
	"motorway": [44.0, 12.0, HPS, true, "cobra"],
	"motorway_link": [40.0, 10.0, HPS, false, "cobra"],
	"primary": [36.0, 10.0, HPS, true, "cobra"],
	"secondary": [34.0, 9.0, HPS, true, "cobra"],
	"street": [32.0, 8.0, LED_WARM, false, "cobra"],
	"pedestrian": [22.0, 4.5, LANTERN, false, "lantern"],
}
const OLD_STREET := [24.0, 4.5, LANTERN, false, "lantern"]
const SKIP_IST := 1 | 2 | 8 | 32        # bridge, tunnel, below grade, steps
const STONE := 16
## The historic quarters (lat, lon): the walled peninsula, then Galata and
## Beyoglu's old core. Edges run just offshore; only the land inside counts.
const OLD_CITY := [
	[[41.0425, 28.9375], [41.0335, 28.9470], [41.0290, 28.9520], [41.0230, 28.9590],
	 [41.0190, 28.9680], [41.0178, 28.9750], [41.0190, 28.9845], [41.0120, 28.9890],
	 [41.0040, 28.9865], [41.0000, 28.9720], [40.9985, 28.9500], [40.9950, 28.9300],
	 [40.9925, 28.9210], [41.0000, 28.9180], [41.0200, 28.9215], [41.0335, 28.9305]],
	[[41.0212, 28.9735], [41.0245, 28.9650], [41.0300, 28.9690], [41.0345, 28.9775],
	 [41.0310, 28.9855], [41.0255, 28.9848]],
]
const OBSTRUCTION_IST_H := 45.0
## How much of istanbul_landmarks' own floodlit emission to keep (its
## set_flood, x night_amount) under the flood volumes here; -1 leaves it alone.
## Both at full turned the domes white; 0.3 keeps its gilding and windows
## glowing and lets the volumes' directional light model the stone.
const LM_OWN_FLOOD := 0.3
const REFLECT_SHORE_M := 70.0

var ist                                  # the env, untyped: its b_* arrays and methods
var _old_city: Array = []                # of PackedVector2Array (ENU)
var _reflect: Array = []                 # [Vector3 godot, Color, strength, size_m]
var _floods: Array = []                  # [Transform3D box, Color, gain, kind]
var _beyazit: Dictionary = {}            # weather colour -> MultiMeshInstance3D
var _weather_key := ""
var _flood_mat: ShaderMaterial
var _lm_own := -1.0
var _lm_last := -2.0
var _refl_mat: ShaderMaterial


func _init() -> void:
	name = "istanbul_night"


# ------------------------------------------------------------------ build
func _build() -> void:
	var t0 := Time.get_ticks_msec()
	ist = env
	for ring: Array in OLD_CITY:
		var poly := PackedVector2Array()
		for ll: Array in ring:
			poly.append(env.geo_to_sim(float(ll[0]), float(ll[1])))
		_old_city.append(poly)
	# --night-skip=lamps,flood,refl,... leaves a layer out (for timing one).
	var skip := IpcServer._get_launch_arg("--night-skip", "").split(",", false)
	if not "lamps" in skip:
		_build_ist_lamps()
	if not "flood" in skip:
		_build_floodlit()
		_build_minarets()
		_flood_mat = _flood_material()
		_draw_floods()
		counts["flood_volumes"] = _floods.size()
	_build_bridges()
	_build_ships()
	_build_piers()
	_build_ist_obstruction()
	_build_beyazit()
	if not "refl" in skip:
		_build_reflections()
	for i in LAMP_LIGHTS:
		var l := OmniLight3D.new()
		l.name = "lamp_light_%d" % i
		l.omni_range = 24.0
		l.omni_attenuation = 1.3
		l.light_energy = 2.0
		l.shadow_enabled = false
		l.visible = false
		l.light_bake_mode = Light3D.BAKE_DISABLED
		l.set_meta("e0", 2.0)
		_root.add_child(l)
		_pool.append(l)
	print("[istanbul_night] %s (%d ms)" % [counts, Time.get_ticks_msec() - t0])


func _in_building(p: Vector2) -> bool:
	return ist.building_at(p.x, p.y) >= 0


func _in_old_city(p: Vector2) -> bool:
	for poly: PackedVector2Array in _old_city:
		if Geometry2D.is_point_in_polygon(p, poly):
			return true
	return false


## Within REFLECT_SHORE_M of open water (8 directions): its light reaches the sea.
func _near_water(p: Vector2, r: float = REFLECT_SHORE_M) -> bool:
	if env.is_water(p.x, p.y):
		return true
	for k in 8:
		var a := TAU * k / 8.0
		if env.is_water(p.x + cos(a) * r, p.y + sin(a) * r):
			return true
	return false


func _sky_reflect(pos: Vector3, col: Color, strength: float, size_m: float) -> void:
	_reflect.append([pos, col, strength, size_m])


# ------------------------------------------------------------------ streets
func _build_ist_lamps() -> void:
	var f := FileAccess.open("res://assets/istanbul/roads.json", FileAccess.READ)
	if f == null:
		return
	var doc: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var nodes: Array = doc.get("nodes", [])
	var ch := {}        # chunk -> {"cobra": [poles, heads, pools, cust], "lantern": [...]}
	var gch := {}       # glare chunk -> [xfs, cust]
	var seen := {}
	var n_lamps := 0
	var shore_n := 0
	for e: Array in doc.get("edges", []):
		var cls := str(e[2])
		var flags := int(e[8])
		if (flags & SKIP_IST) != 0 or (flags & 4) != 0:
			continue
		var stone := (flags & STONE) != 0
		if not IST_LAMPS.has(cls) and not (stone and cls in ["path", "service"]):
			continue
		var a := int(e[0])
		var b := int(e[1])
		var key := Vector2i(mini(a, b), maxi(a, b))
		if seen.has(key):
			continue
		seen[key] = true
		var pa := Vector2(float(nodes[a][0]), float(nodes[a][1]))
		var pb := Vector2(float(nodes[b][0]), float(nodes[b][1]))
		var L := pa.distance_to(pb)
		if L < 5.0:
			continue
		var spec: Array = IST_LAMPS.get(cls, OLD_STREET)
		if cls == "street" and (stone or _in_old_city((pa + pb) * 0.5)):
			spec = OLD_STREET
		elif stone and not cls in ["motorway", "motorway_link", "primary", "secondary"]:
			spec = OLD_STREET
		var dir := (pb - pa) / L
		var nrm := Vector2(-dir.y, dir.x)
		var width := float(e[5])
		var off := width * 0.5 + 0.6 if width > 2.0 else maxi(1, int(e[4])) * 1.7 + 1.4
		var spacing: float = spec[0]
		var h: float = spec[1]
		var col: Color = spec[2]
		var both: bool = spec[3]
		var style: String = spec[4]
		# A one-way main road is half a dual carriageway: light it from the
		# median (its left), so the pair of edges shares one central row.
		var one_side := -1.0 if int(e[3]) != 0 and cls in ["motorway", "primary", "secondary"] else 0.0
		if one_side != 0.0:
			both = false
		var s := fposmod(float(hash(key)) * 0.618, spacing) * 0.8 + spacing * 0.1
		var k := hash(key) & 1
		while s < L - 2.0:
			var sides := [1.0, -1.0] if both else [one_side if one_side != 0.0 else (1.0 if k % 2 == 0 else -1.0)]
			for sd: float in sides:
				var sa := s + (spacing * 0.5 if both and sd < 0.0 else 0.0)
				if sa >= L - 2.0:
					continue
				var p := pa + dir * sa + nrm * off * sd
				if _in_building(p):
					p = pa + dir * sa + nrm * (off * 0.6) * sd
				if env.is_water(p.x, p.y) or _in_building(p):
					continue
				var g: float = env.ground_height(p.x, p.y)
				var to_road := -nrm * sd
				var yaw := atan2(to_road.x, to_road.y)
				var basis := Basis(Vector3.UP, PI - yaw)
				var ck := Vector2i(int(floor(p.x / CHUNK)), int(floor(p.y / CHUNK)))
				if not ch.has(ck):
					ch[ck] = {"cobra": [[], [], [], []], "lantern": [[], [], [], []]}
				var c: Array = ch[ck][style]
				var top := Vector3(p.x, g + h, -p.y)
				var lens: Vector3
				var pool_r: float
				(c[0] as Array).append(Transform3D(Basis().scaled(Vector3(1.0, h, 1.0)), Vector3(p.x, g + h * 0.5, -p.y)))
				if style == "cobra":
					(c[1] as Array).append(Transform3D(basis, top))
					lens = Transform3D(basis, top) * Vector3(0, -0.12, 2.45)
					pool_r = h * 2.3
				else:
					(c[1] as Array).append(Transform3D(basis, top))
					lens = top + Vector3(0, 0.35, 0)
					pool_r = 9.0
				var foot := Vector3(lens.x, g, lens.z)
				(c[2] as Array).append(_pool_xf(foot, pool_r))
				(c[3] as Array).append(Color(col.r, col.g, col.b, 0.0))
				var gk := Vector2i(int(floor(p.x / GLARE_CHUNK)), int(floor(p.y / GLARE_CHUNK)))
				if not gch.has(gk):
					gch[gk] = [[], []]
				(gch[gk][0] as Array).append(Transform3D(Basis(), lens))
				(gch[gk][1] as Array).append(Color(col.r, col.g, col.b, 0.0))
				var cell := Vector2i(int(floor(p.x / 100.0)), int(floor(p.y / 100.0)))
				if not _cells.has(cell):
					_cells[cell] = []
				(_cells[cell] as Array).append([lens + Vector3(0, -0.6, 0), col])
				if _near_water(p):
					_sky_reflect(lens, col, 0.3, 1.0)
					shore_n += 1
				n_lamps += 1
				counts[style] = int(counts.get(style, 0)) + 1
			s += spacing
			k += 1
	var pole := CylinderMesh.new()
	pole.top_radius = 0.07
	pole.bottom_radius = 0.12
	pole.height = 1.0
	pole.radial_segments = 6
	pole.rings = 1
	var head := _head_mesh()
	var lantern := _lantern_mesh()
	var box := _pool_box()
	var quad := _quad()
	var pole_mat := _pole_material()
	var head_mat := _head_material()
	var pool_mat := _pool_material(float(IpcServer._get_launch_arg("--night-pool-gain", "0.022")))
	var glare_mat := _glare_material(1.0, float(IpcServer._get_launch_arg("--night-glare-gain", "8")))
	var half_diag := CHUNK * 0.7071
	var empty: Array = []
	for ck: Vector2i in ch:
		var holder := Node3D.new()
		holder.name = "lamps_%d_%d" % [ck.x, ck.y]
		_root.add_child(holder)
		for style: String in ["cobra", "lantern"]:
			var c: Array = ch[ck][style]
			if (c[0] as Array).is_empty():
				continue
			if "pools" in IpcServer._get_launch_arg("--night-skip", ""):
				c[2] = []
			_draw("lamp_poles_" + style, pole, c[0], empty, pole_mat, 450.0 + half_diag, holder)
			_draw("lamp_heads_" + style, head if style == "cobra" else lantern, c[1], c[3], head_mat,
				900.0 + half_diag, holder)
			_draw("lamp_pools_" + style, box, c[2], c[3], pool_mat, 3500.0 + half_diag, holder)
	for gk: Vector2i in gch:
		_draw("lamp_glare_%d_%d" % [gk.x, gk.y], quad, gch[gk][0], gch[gk][1], glare_mat)
	counts["street_lamps"] = n_lamps
	counts["shore_lamps"] = shore_n


## An Ottoman-style lantern on the post's top: a dark cap, glass all round
## (vertex alpha 1 = lit), a finial. Origin at the post's top.
func _lantern_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_box(st, Vector3(0, 0.04, 0), Vector3(0.22, 0.08, 0.22), 0.0)
	_box(st, Vector3(0, 0.36, 0), Vector3(0.34, 0.52, 0.34), 1.0)    # the glass
	_box(st, Vector3(0, 0.66, 0), Vector3(0.46, 0.08, 0.46), 0.0)    # the cap
	_box(st, Vector3(0, 0.78, 0), Vector3(0.10, 0.18, 0.10), 0.0)
	st.generate_normals()
	return st.commit()


# ------------------------------------------------------------------ floodlights
## A flood volume: an oriented box (ENU centre, half sizes along its own axes,
## yaw from east, absolute bottom/top), a colour, a gain, and how the light
## falls ("dome": fixtures all round; "tower": a slim shaft lit all round).
func _flood(c: Vector2, half: Vector2, yaw: float, bottom: float, top: float, col: Color,
		gain: float, kind := "dome") -> void:
	var ax := Vector3(cos(yaw), 0.0, -sin(yaw)) * (2.0 * half.x)
	var az := Vector3(-sin(yaw), 0.0, -cos(yaw)) * (2.0 * half.y)
	var ay := Vector3(0.0, top - bottom, 0.0)
	var xf := Transform3D(Basis(ax, ay, az), Vector3(c.x, (bottom + top) * 0.5, -c.y))
	_floods.append([xf, col, gain, 1.0 if kind == "tower" else 0.0])
	# The light reaches the water: a column of reflection from a few heights.
	var r := maxf(half.x, half.y)
	if _near_water(c, r + 120.0):
		for fr: float in [0.35, 0.65, 0.92]:
			_sky_reflect(Vector3(c.x, lerpf(bottom, top, fr), -c.y), col, 0.45 * gain, r * 0.7)


## Minimum-area rectangle around a footprint (rotating calipers on its hull):
## [centre, half sizes, yaw]. Most of the old city's mosques face Mecca, ~29
## deg off the grid, where an axis-aligned box would light the neighbours.
static func _min_rect(fp: PackedVector2Array) -> Array:
	var hull := Geometry2D.convex_hull(fp)
	var best := [Vector2.ZERO, Vector2.ZERO, 0.0]
	var best_a := INF
	for k in range(hull.size() - 1):
		var e := (hull[k + 1] - hull[k])
		if e.length() < 0.01:
			continue
		var u := e.normalized()
		var v := Vector2(-u.y, u.x)
		var lo := Vector2(INF, INF)
		var hi := Vector2(-INF, -INF)
		for p in hull:
			var q := Vector2(p.dot(u), p.dot(v))
			lo = lo.min(q)
			hi = hi.max(q)
		var a := (hi.x - lo.x) * (hi.y - lo.y)
		if a < best_a:
			best_a = a
			var mid := (lo + hi) * 0.5
			best = [u * mid.x + v * mid.y, (hi - lo) * 0.5, atan2(u.y, u.x)]
	return best


static func _area(fp: PackedVector2Array) -> float:
	var a := 0.0
	for k in fp.size():
		a += fp[k].cross(fp[(k + 1) % fp.size()])
	return absf(a) * 0.5


func _flood_footprint(i: int, col: Color, gain: float, extra_top: float, grow: float = 3.0) -> void:
	var fp: PackedVector2Array = ist.footprint(i)
	var r := _min_rect(fp)
	var top: float = maxf(float(ist.b_top[i]), float(ist.b_base[i]) + extra_top)
	_flood(r[0], (r[1] as Vector2) + Vector2(grow, grow), r[2], float(ist.b_base[i]) - 1.0, top + 2.0, col, gain)


func _landmarks_node() -> Node:
	return env.get_node_or_null("istanbul_landmarks")


func _build_floodlit() -> void:
	var lm := _landmarks_node()
	var n := 0
	var own := float(IpcServer._get_launch_arg("--night-lm-flood", str(LM_OWN_FLOOD)))
	if lm != null and lm.has_method("set_flood") and own >= 0.0:
		_lm_own = own
	if lm != null and lm.has_method("floodlit"):
		n = _floods_from(lm.call("floodlit"))
		counts["floodlit_from_landmarks"] = n
	# The data's own monuments that the landmarks did not cover.
	n += _floods_fallback()


## istanbul_landmarks.floodlit(): an Array of Dictionaries, each one of
##   {"ring": PackedVector2Array|Array (ENU footprint), "bottom", "top"}
##   {"p"|"centre": Vector2|[e, n], "r": radius m, "bottom", "top"}
## with optional "colour"/"color" (Color), "gain" (1 = a mosque's), "kind"
## ("dome", "minaret"/"tower" for a slim shaft). Heights are absolute.
func _floods_from(items) -> int:
	var n := 0
	if not (items is Array):
		return 0
	for it in items:
		if not (it is Dictionary):
			continue
		var d: Dictionary = it
		if d.has("pos") and d.get("pos") is Vector3:
			# istanbul_landmarks' own form: {"pos": Godot foot, "radius",
			# "height" above the foot, "kind": mosque|tower|palace|wall|
			# column|fountain}.
			var gp: Vector3 = d["pos"]
			var c0 := Vector2(gp.x, -gp.z)
			var rad := float(d.get("radius", 6.0))
			var hgt := float(d.get("height", 30.0))
			var kd := str(d.get("kind", "mosque"))
			var spec: Array = FLOOD_KINDS.get(kd, FLOOD_KINDS["mosque"])
			var hr := rad * float(spec[2]) + 2.5
			_flood(c0, Vector2(hr, hr), 0.0, gp.y - 1.5, gp.y + hgt + 2.0, spec[0], float(spec[1]),
				"tower" if kd in ["tower", "column"] else "dome")
			n += 1
			continue
		var col: Color = d.get("colour", d.get("color", FLOOD_GOLD))
		var gain := float(d.get("gain", 1.0))
		var kind := str(d.get("kind", "dome"))
		var bottom := float(d.get("bottom", -INF))
		var top := float(d.get("top", -INF))
		if d.has("ring"):
			var fp := PackedVector2Array()
			for p in d["ring"]:
				fp.append(_v2(p))
			if fp.size() < 3:
				continue
			var r := _min_rect(fp)
			var c: Vector2 = r[0]
			if bottom == -INF:
				bottom = env.ground_height(c.x, c.y) - 1.0
			if top == -INF:
				top = bottom + 0.45 * sqrt(_area(fp)) + 12.0
			_flood(c, (r[1] as Vector2) + Vector2(3, 3), r[2], bottom, top + 2.0, col, gain,
				"tower" if kind in ["minaret", "tower"] else "dome")
		elif d.has("p") or d.has("centre"):
			var c := _v2(d.get("p", d.get("centre")))
			var rr := float(d.get("r", 6.0))
			if bottom == -INF:
				bottom = env.ground_height(c.x, c.y) - 1.0
			if top == -INF:
				top = bottom + 40.0
			var tw := kind in ["minaret", "tower"] or rr < 10.0
			_flood(c, Vector2(rr + 2.5, rr + 2.5), 0.0, bottom, top + 1.5, col, gain, "tower" if tw else "dome")
		else:
			continue
		n += 1
	return n


## Whether an ENU point lies inside one of the first n flood volumes.
func _covered(p: Vector2, n: int) -> bool:
	var w := Vector3(p.x, 0.0, -p.y)
	for k in n:
		var xf: Transform3D = _floods[k][0]
		var q := w - xf.origin
		var ax := xf.basis.x
		var az := xf.basis.z
		if absf(q.dot(ax) / ax.length_squared()) <= 0.5 and absf(q.dot(az) / az.length_squared()) <= 0.5:
			return true
	return false


static func _v2(p) -> Vector2:
	if p is Vector2:
		return p
	if p is Vector3:
		return Vector2(p.x, p.y)
	return Vector2(float(p[0]), float(p[1]))


## From the data alone: mosques, big churches, palaces, Topkapi, the towers,
## the Maiden's Tower, the Valens Aqueduct.
func _floods_fallback() -> int:
	var n0 := _floods.size()
	var feats := _features()
	var topkapi := PackedVector2Array()
	var maiden := PackedVector2Array()
	for l: Dictionary in feats.get("landmarks", []):
		var nm := str(l.get("name", ""))
		if l.has("ring") and nm == "Topkapı Palace":
			for p in l["ring"]:
				topkapi.append(_v2(p))
		if l.has("ring") and nm == "Kız Kulesi":
			for p in l["ring"]:
				maiden.append(_v2(p))
	var nb: int = ist.b_first.size()
	var n_given := _floods.size()
	for i in nb:
		var kind := int(ist.b_kind[i])
		if kind == 250 or not kind in [0, 2, 3, 4, 6, 12]:
			continue
		var fp: PackedVector2Array
		var a := 0.0
		if kind in [2, 3, 12]:
			fp = ist.footprint(i)
			a = _area(fp)
			if a < (150.0 if kind == 2 else (600.0 if kind == 3 else 400.0)):
				continue
		elif topkapi.size() < 3 or not Geometry2D.is_point_in_polygon(ist.b_centre[i], topkapi):
			continue
		else:
			fp = ist.footprint(i)
		if n_given > 0 and _covered(ist.b_centre[i], n_given):
			continue
		if kind == 2:
			# Mosques: the dome above the data's flat roof.
			if a >= 150.0:
				var big := smoothstep(400.0, 4000.0, a)
				_flood_footprint(i, FLOOD_GOLD, lerpf(0.75, 1.15, big), 0.45 * sqrt(a) + 12.0)
		elif kind == 3:
			# Churches: the big ones (Hagia Irene, the Bulgarian, ...).
			if a >= 600.0:
				_flood_footprint(i, FLOOD_WHITE, 0.7, 0.35 * sqrt(a) + 10.0)
		elif kind == 12:
			# Palaces: Dolmabahce, Ciragan, Ibrahim Pasha.
			if a >= 400.0:
				_flood_footprint(i, FLOOD_WHITE, 0.9, 0.0, 2.0)
		elif topkapi.size() > 2 and kind in [0, 4, 6] \
				and Geometry2D.is_point_in_polygon(ist.b_centre[i], topkapi) and _area(fp) > 120.0:
			_flood_footprint(i, FLOOD_WHITE, 0.6, 0.0, 2.0)
	for t: Dictionary in feats.get("towers", []):
		var nm := str(t.get("name", ""))
		if nm in ["Galata Tower", "Beyazıt Tower", "Dolmabahçe Clock Tower"]:
			var p := _v2(t["p"])
			if n_given > 0 and _covered(p, n_given):
				continue
			var g: float = env.ground_height(p.x, p.y)
			var h := float(t["h"]) if t.get("h") != null else 30.0
			var r := float(t.get("r", 5.0))
			_flood(p, Vector2(r + 2.5, r + 2.5), 0.0, g - 1.0, g + h + 3.0, FLOOD_WHITE if nm != "Galata Tower" else FLOOD_GOLD,
				1.1, "tower")
	if maiden.size() > 2 and not (n_given > 0 and _covered(_min_rect(maiden)[0], n_given)):
		var r := _min_rect(maiden)
		_flood(r[0], (r[1] as Vector2) + Vector2(3, 3), r[2], -1.0, 30.0, FLOOD_WHITE, 1.2)
	for aq: Dictionary in feats.get("aqueducts", []):
		if str(aq.get("name", "")) != "Valens Aqueduct":
			continue
		var pts: Array = aq["pts"]
		for k in range(pts.size() - 1):
			var a2 := _v2(pts[k])
			var b2 := _v2(pts[k + 1])
			var L := a2.distance_to(b2)
			if L < 3.0:
				continue
			var g: float = minf(env.ground_height(a2.x, a2.y), env.ground_height(b2.x, b2.y))
			_flood((a2 + b2) * 0.5, Vector2(L * 0.5 + 1.0, 7.0), atan2(b2.y - a2.y, b2.x - a2.x),
				g - 1.0, g + 30.0, FLOOD_GOLD, 0.8)
	return _floods.size() - n0


var _feats_cache: Dictionary = {}

func _features() -> Dictionary:
	if _feats_cache.is_empty():
		var f := FileAccess.open("res://assets/istanbul/features.json", FileAccess.READ)
		if f != null:
			var d = JSON.parse_string(f.get_as_text())
			_feats_cache = d if d is Dictionary else {}
	return _feats_cache


func _draw_floods() -> void:
	if _floods.is_empty():
		return
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	var b := BoxMesh.new()
	b.size = Vector3.ONE
	mm.mesh = b
	mm.instance_count = _floods.size()
	for i in _floods.size():
		var f: Array = _floods[i]
		var c: Color = f[1]
		mm.set_instance_transform(i, f[0])
		# rgb: colour x gain; a: 0 a building (lit from all round), 1 a shaft.
		mm.set_instance_custom_data(i, Color(c.r * f[2], c.g * f[2], c.b * f[2], f[3]))
	var mmi := MultiMeshInstance3D.new()
	mmi.name = "flood_lights"
	mmi.multimesh = mm
	mmi.material_override = _flood_mat
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_root.add_child(mmi)


## The flood volume's light (header). Box from the instance's transform; the
## lit point from the depth buffer; its normal from the point's screen
## derivatives; the light from the fixtures at the foot, out from the centre.
func _flood_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode unshaded, blend_add, depth_draw_never, depth_test_disabled, cull_front, shadows_disabled, fog_disabled;
uniform sampler2D depth_tex : hint_depth_texture, filter_nearest;
uniform float night = 0.0;
uniform float gain = 1.0;
varying vec3 v_o;
varying vec3 v_ax;
varying vec3 v_ay;
varying vec3 v_az;
varying vec4 v_c;
void vertex() {
    v_o = MODEL_MATRIX[3].xyz;
    v_ax = MODEL_MATRIX[0].xyz;
    v_ay = MODEL_MATRIX[1].xyz;
    v_az = MODEL_MATRIX[2].xyz;
    v_c = INSTANCE_CUSTOM;
}
void fragment() {
    float depth = texture(depth_tex, SCREEN_UV).r;
    vec4 view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, depth, 1.0);
    view.xyz /= view.w;
    vec3 p = (INV_VIEW_MATRIX * vec4(view.xyz, 1.0)).xyz;
    vec3 q = p - v_o;
    vec3 u = vec3(dot(q, v_ax) / dot(v_ax, v_ax), dot(q, v_ay) / dot(v_ay, v_ay), dot(q, v_az) / dot(v_az, v_az));
    vec3 au = abs(u);
    if (au.x > 0.5 || au.y > 0.5 || au.z > 0.5) { discard; }
    // Soft at the box's sides and top, so no edge of it shows.
    float hx = length(v_ax) * 0.5;
    float hz = length(v_az) * 0.5;
    float side = (1.0 - smoothstep(max(hx - 4.0, 0.0), hx, au.x * 2.0 * hx))
               * (1.0 - smoothstep(max(hz - 4.0, 0.0), hz, au.z * 2.0 * hz));
    float t = u.y + 0.5;
    float vert = smoothstep(0.0, 0.03, t) * (1.0 - smoothstep(0.95, 1.0, t));
    // The surface's normal from the reconstructed point (facing the camera).
    vec3 n = normalize(cross(dFdx(p), dFdy(p)));
    if (dot(n, CAMERA_POSITION_WORLD - p) < 0.0) { n = -n; }
    // Out from the monument's axis, and up: where the fixtures at its foot
    // and on the roofs round it throw their light from.
    vec3 out_d = vec3(q.x, 0.0, q.z);
    float ol = length(out_d);
    out_d = ol > 0.01 ? out_d / ol : vec3(0.0);
    float shaft = v_c.a;
    vec3 L = normalize(out_d + vec3(0.0, mix(0.75, 0.25, shaft), 0.0));
    float lam = max(dot(n, L), 0.0);
    float k = 0.3 + 0.7 * lam;
    // The paving round it gets only spill.
    float ground = smoothstep(0.88, 0.98, n.y) * (1.0 - smoothstep(0.04, 0.14, t));
    k *= 1.0 - 0.75 * ground;
    // A little brighter low on the walls, near the lamps.
    k *= mix(1.15, 0.85, t);
    ALBEDO = v_c.rgb * gain * night * k * side * vert;
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("gain", float(IpcServer._get_launch_arg("--night-flood-gain", "0.15")))
	_mats.append(m)
	return m


# ------------------------------------------------------------------ minarets
func _build_minarets() -> void:
	var lm := _landmarks_node()
	var balconies: Array = []      # [Vector2 ENU, y abs, r]
	var shafts: Array = []         # [Vector2, bottom, top]
	if lm != null and lm.has_method("minaret_balconies"):
		var arr = lm.call("minaret_balconies")
		if arr is PackedVector3Array:
			# Godot centres; a minaret is the stack of balconies at one spot,
			# uplit from the ground to a cone's height over its top balcony.
			var tops := {}
			for i in (arr as PackedVector3Array).size():
				var b3: Vector3 = arr[i]
				var r3 := float(lm.call("balcony_radius", i)) if lm.has_method("balcony_radius") else 2.2
				var c2 := Vector2(b3.x, -b3.z)
				balconies.append([c2, b3.y, r3])
				var key := Vector2i(roundi(c2.x), roundi(c2.y))
				if not tops.has(key) or float(tops[key][1]) < b3.y:
					tops[key] = [c2, b3.y, r3]
			for key: Vector2i in tops:
				var t: Array = tops[key]
				var c2: Vector2 = t[0]
				shafts.append([c2, env.ground_height(c2.x, c2.y), float(t[1]) + 12.0])
		elif arr is Array:
			for it in arr:
				if it is Dictionary:
					balconies.append([_v2(it.get("p", it.get("centre", Vector2.ZERO))),
						float(it.get("y", it.get("height", 30.0))), float(it.get("r", 2.2))])
				elif it is Vector3:
					balconies.append([Vector2(it.x, -it.z), it.y, 2.2])
		counts["balconies_from_landmarks"] = balconies.size()
	var have_lm_floods := counts.has("floodlit_from_landmarks") and int(counts["floodlit_from_landmarks"]) > 0
	if balconies.is_empty():
		var n_now := _floods.size()
		for mn: Dictionary in _features().get("minarets", []):
			var p := _v2(mn["p"])
			if have_lm_floods and _covered(p, n_now):
				continue
			var g: float = env.ground_height(p.x, p.y)
			var a := _mosque_area_near(p)
			var h: float
			var nb: int
			if mn.get("h") != null and float(mn["h"]) > 5.0:
				h = float(mn["h"])
				nb = 3 if h > 60.0 else (2 if h > 45.0 else 1)
			elif a > 2500.0:
				h = 66.0
				nb = 3
			elif a > 1000.0:
				h = 52.0
				nb = 2
			elif a > 400.0:
				h = 40.0
				nb = 1
			else:
				h = 30.0
				nb = 1
			for k in nb:
				balconies.append([p, g + h * (0.80 - 0.14 * k), 2.3])
			shafts.append([p, g, g + h])
	if true:
		for s: Array in shafts:
			_flood(s[0], Vector2(4.5, 4.5), 0.0, float(s[1]) - 1.0, float(s[2]) + 1.0, FLOOD_GOLD, 0.95, "tower")
	var pts := PackedVector3Array()
	for bl: Array in balconies:
		var c: Vector2 = bl[0]
		var y: float = bl[1]
		var r: float = bl[2]
		for k in 8:
			var a := TAU * (k + 0.5) / 8.0
			pts.append(Vector3(c.x + cos(a) * r, y, -(c.y + sin(a) * r)))
		if _near_water(c, 250.0):
			_sky_reflect(Vector3(c.x, y, -c.y), BALCONY, 0.8, r * 2.0)
	if not pts.is_empty():
		var mmi := add_lights("minaret_lights", pts, BALCONY, 0.32, 0)
		if mmi != null:
			# Eight lamps a ring: from afar they merge into one point, which
			# must stay a point, not a flare over the minaret.
			(mmi.material_override as ShaderMaterial).set_shader_parameter("gain", 2.0)
			(mmi.material_override as ShaderMaterial).set_shader_parameter("min_px", 2.5)
	counts["serefe_balconies"] = balconies.size()


## Footprint area of the largest mosque within 70 m.
func _mosque_area_near(p: Vector2) -> float:
	var best := 0.0
	for i in ist.buildings_in(Rect2(p - Vector2(70, 70), Vector2(140, 140))):
		if int(ist.b_kind[i]) == 2:
			best = maxf(best, _area(ist.footprint(i)))
	return best


# ------------------------------------------------------------------ bridges, ships
## A node's lights(): Array of [Vector3 Godot position, Color] pairs, or of
## {"p"|"pos": Vector3, "colour"|"color": Color, "blink": 0|1|2}, or a
## PackedVector3Array (warm white). Red ones (and blink 1) flash as L-864s.
func _lights_from(node: Node, draw_prefix: String) -> int:
	if node == null or not node.has_method("lights"):
		return 0
	var arr = node.call("lights")
	var by := {}       # [Color, blink] key -> PackedVector3Array
	var n := 0
	if arr is PackedVector3Array:
		arr = Array(arr)
	if not (arr is Array):
		return 0
	for it in arr:
		var p := Vector3.ZERO
		var c := Color(1.0, 0.86, 0.66)
		var blink := 0
		if it is Vector3:
			p = it
		elif it is Array and (it as Array).size() >= 1:
			p = it[0]
			if it.size() > 1 and it[1] is Color:
				c = it[1]
			if it.size() > 2:
				blink = int(it[2])
		elif it is Dictionary:
			p = it.get("p", it.get("pos", Vector3.ZERO))
			c = it.get("colour", it.get("color", c))
			blink = int(it.get("blink", 0))
		else:
			continue
		if c.r > 0.9 and c.g < 0.3 and c.b < 0.3 and blink == 0 and draw_prefix == "bridge":
			blink = 1 if p.y > 40.0 else 0
		var key := [c, blink]
		if not by.has(key):
			by[key] = PackedVector3Array()
		(by[key] as PackedVector3Array).append(p)
		# Over the water already: every one of them reflects.
		_sky_reflect(p, c, 0.7 if blink == 0 else 0.0, 1.2)
		n += 1
	for key: Array in by:
		var c: Color = key[0]
		var red := c.r > 0.9 and c.g < 0.3
		add_lights("%s_%s_lights" % [draw_prefix, "obstruction" if red else "necklace"], by[key], c,
			0.7, int(key[1]))
	return n


## The bridges draw their own lights (the 15 July bridge's necklace cycles
## its colours there); here they only get their reflections on the water.
## A bridges node that has handed its drawing over (set_own_lights(false)
## called elsewhere) is drawn here instead.
func _build_bridges() -> void:
	var br := env.get_node_or_null("istanbul_bridges")
	if br == null or not br.has_method("lights"):
		return
	if br.get("_own_lights") == false:
		counts["bridge_lights"] = _lights_from(br, "bridge")
		return
	var n := 0
	for it in br.call("lights"):
		if it is Array and (it as Array).size() >= 2 and it[0] is Vector3:
			var p: Vector3 = it[0]
			var c: Color = it[1]
			if c.r > 0.9 and c.g < 0.3:
				continue        # the beacons: too small to matter on the water
			_sky_reflect(p, c, 0.45, 1.0)
			n += 1
	counts["bridge_reflections"] = n


func _build_ships() -> void:
	var ships := env.get_node_or_null("istanbul_ships")
	if ships == null:
		_build_anchorage()
		return
	if ships.has_method("lights"):
		counts["ship_lights"] = _lights_from(ships, "ship")
	elif ships.get("_s_pos") != null and ships.get("moving") == false:
		_ship_nav_lights(ships)


## istanbul_ships draws hulls and lit windows but no navigation lights. While
## the craft stand still (no --world-life) they get them here from its
## arrays: at anchor or moored, white anchor lights fore and aft and a big
## ship's deck lit in sodium; mid-crossing, a masthead light, red to port,
## green to starboard, a stern light. Each also lays its reflection.
func _ship_nav_lights(ships) -> void:
	var white := PackedVector3Array()
	var deck := PackedVector3Array()
	var red := PackedVector3Array()
	var green := PackedVector3Array()
	var kinds: Dictionary = ships.get("_kinds")
	var kind_of: Array = Array(ships.get("_s_kind"))
	var pos: Array = Array(ships.get("_s_pos"))
	var yaw: Array = Array(ships.get("_s_yaw"))
	var under: Array = Array(ships.get("_s_under"))
	var sea := float(env.get("SEA_Y"))
	for i in kind_of.size():
		if not kinds.has(kind_of[i]):
			continue
		var dims: Vector3 = kinds[kind_of[i]].get("dims", Vector3(20, 5, 2))
		var L := dims.x
		var B := dims.y
		var fb := maxf(dims.z, 1.0)
		var p: Vector2 = pos[i]
		var xf := Transform3D(Basis(Vector3.UP, float(yaw[i])), Vector3(p.x, sea, -p.y))
		var refl: Array = []
		if L < 25.0:
			var q := xf * Vector3(0, fb + 2.0, 0)
			white.append(q)
			refl.append(q)
		elif int(under[i]) != 0:
			var mast := xf * Vector3(0, fb + maxf(6.0, L * 0.12), L * 0.25)
			white.append(mast)
			white.append(xf * Vector3(0, fb + 1.0, -L * 0.5))
			red.append(xf * Vector3(B * 0.5, fb + 2.5, L * 0.05))
			green.append(xf * Vector3(-B * 0.5, fb + 2.5, L * 0.05))
			refl.append(mast)
		else:
			var bow := xf * Vector3(0, fb + 3.0, L * 0.48)
			var stern := xf * Vector3(0, fb + 1.5, -L * 0.48)
			white.append(bow)
			white.append(stern)
			refl.append(bow)
			if L > 90.0:
				for k in 6:
					var d := xf * Vector3(0, fb + 5.0, L * (0.38 - 0.14 * k))
					deck.append(d)
				refl.append(deck[deck.size() - 3])
		for q: Vector3 in refl:
			_sky_reflect(q, Color(1.0, 0.85, 0.6), 0.7, 2.0)
	add_lights("ship_anchor_lights", white, Color(1.0, 0.95, 0.85), 0.6, 0)
	add_lights("ship_deck_lights", deck, HPS, 1.0, 0)
	add_lights("ship_port_lights", red, Color(1.0, 0.1, 0.05), 0.5, 0)
	add_lights("ship_starboard_lights", green, Color(0.1, 1.0, 0.3), 0.5, 0)
	counts["ship_nav_lights"] = white.size() + deck.size() + red.size() + green.size()


## Ships at anchor off Ahirkapi and Kumkapi (a real anchorage: the night view
## from the peninsula is full of them). Lights only, no hulls: anchor lights
## fore and aft, a masthead light, the deck lit in sodium and the
## accommodation block's windows aft. Seeded, fixed.
func _build_anchorage() -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = 41008
	var white := PackedVector3Array()
	var deck := PackedVector3Array()
	var cabin := PackedVector3Array()
	var n := 0
	var tries := 0
	var placed: Array[Vector2] = []
	while n < 22 and tries < 400:
		tries += 1
		var c := Vector2(rng.randf_range(-3600.0, 2600.0), rng.randf_range(-4300.0, -1500.0))
		if not env.is_water(c.x, c.y) or _near_water_land(c, 700.0):
			continue
		var ok := true
		for q in placed:
			if q.distance_to(c) < 420.0:
				ok = false
				break
		if not ok:
			continue
		placed.append(c)
		var L := rng.randf_range(110.0, 230.0)
		var hd := rng.randf_range(0.0, TAU)     # swung to the current: any heading
		var u := Vector2(cos(hd), sin(hd))
		var bow := c + u * L * 0.5
		var stern := c - u * L * 0.5
		var hh := L * 0.11
		white.append(Vector3(bow.x, hh, -bow.y))
		white.append(Vector3(stern.x, hh * 0.7, -stern.y))
		var mast := c + u * L * 0.3
		white.append(Vector3(mast.x, hh + 14.0, -mast.y))
		for k in 7:
			var p := c + u * L * (0.42 - 0.13 * k) + Vector2(-u.y, u.x) * rng.randf_range(-6.0, 6.0)
			deck.append(Vector3(p.x, hh + 4.0, -p.y))
		var blk := c - u * L * 0.36
		for k in 9:
			var p := blk + Vector2(-u.y, u.x) * rng.randf_range(-9.0, 9.0) + u * rng.randf_range(-5.0, 5.0)
			cabin.append(Vector3(p.x, hh + rng.randf_range(4.0, 18.0), -p.y))
		for p in [white[white.size() - 1], deck[deck.size() - 4], cabin[cabin.size() - 1]]:
			_sky_reflect(p, Color(1.0, 0.8, 0.5), 0.8, 3.0)
		n += 1
	add_lights("ship_anchor_lights", white, Color(1.0, 0.95, 0.85), 0.9, 0)
	add_lights("ship_deck_lights", deck, HPS, 1.2, 0)
	add_lights("ship_cabin_lights", cabin, Color(1.0, 0.82, 0.58), 0.6, 0)
	counts["anchored_ships"] = n


func _near_water_land(c: Vector2, r: float) -> bool:
	for k in 12:
		var a := TAU * k / 12.0
		if not env.is_water(c.x + cos(a) * r, c.y + sin(a) * r):
			return true
	return false


## Lamps along the ferry piers' edges.
func _build_piers() -> void:
	var f := FileAccess.open("res://assets/istanbul/roads.json", FileAccess.READ)
	if f == null:
		return
	var doc: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var pts := PackedVector3Array()
	for pr: Dictionary in doc.get("piers", []):
		var ring: Array = pr.get("pts", [])
		for k in range(ring.size() - 1):
			var a := _v2(ring[k])
			var b := _v2(ring[k + 1])
			var L := a.distance_to(b)
			var s := 6.0
			while s < L:
				var p := a.lerp(b, s / L)
				var lp := Vector3(p.x, 5.5, -p.y)
				pts.append(lp)
				_sky_reflect(lp, LANTERN, 0.7, 1.0)
				s += 18.0
	add_lights("pier_lamps_lights", pts, LANTERN, 0.8, 0)
	counts["pier_lamps"] = pts.size()


# ------------------------------------------------------------------ red lights
func _build_ist_obstruction() -> void:
	var flash: Array = []
	var flash_c: Array = []
	var steady: Array = []
	var steady_c: Array = []
	var nb: int = ist.b_first.size()
	var tall: Array = []
	for i in nb:
		var kind := int(ist.b_kind[i])
		if kind in [2, 3, 4, 8, 12, 250]:
			continue
		var h: float = float(ist.b_top[i]) - float(ist.b_base[i])
		if h >= OBSTRUCTION_IST_H:
			tall.append(i)
	var n_bld := 0
	for i: int in tall:
		var top: float = ist.b_top[i]
		var c: Vector2 = ist.b_centre[i]
		var shielded := false
		for j: int in tall:
			if j != i and float(ist.b_top[j]) > top + SHIELD_DH and (ist.b_centre[j] as Vector2).distance_to(c) < SHIELD_M:
				shielded = true
				break
		if shielded:
			continue
		n_bld += 1
		var phase := fposmod(sin(c.x * 0.0131 + c.y * 0.0071) * 4373.17, 1.0)
		var r := _min_rect(ist.footprint(i))
		var u := Vector2(cos(r[2]), sin(r[2]))
		var v := Vector2(-u.y, u.x)
		var hs: Vector2 = (r[1] as Vector2) - Vector2(1, 1)
		var corners := [r[0] + u * hs.x + v * hs.y, r[0] - u * hs.x - v * hs.y]
		if minf(hs.x, hs.y) > 12.0:
			corners.append(r[0] + u * hs.x - v * hs.y)
			corners.append(r[0] - u * hs.x + v * hs.y)
		for q: Vector2 in corners:
			flash.append(Transform3D(Basis(), Vector3(q.x, top + 0.6, -q.y)))
			flash_c.append(Color(RED.r, RED.g, RED.b, phase))
			if top - float(ist.b_base[i]) >= 100.0:
				steady.append(Transform3D(Basis(), Vector3(q.x, lerpf(float(ist.b_base[i]), top, 0.5), -q.y)))
				steady_c.append(Color(RED.r, RED.g, RED.b, 0.0))
	# Masts and the radar station on Uskudar's hill.
	for t: Dictionary in _features().get("towers", []):
		if str(t.get("type", "")) in ["communication", "radar"]:
			var p := _v2(t["p"])
			var g: float = env.ground_height(p.x, p.y)
			var h := float(t["h"]) if t.get("h") != null else 40.0
			flash.append(Transform3D(Basis(), Vector3(p.x, g + h + 0.5, -p.y)))
			flash_c.append(Color(RED.r, RED.g, RED.b, fposmod(p.x * 0.37, 1.0)))
			n_bld += 1
	var quad := _quad()
	_draw("obstruction_lights_flash", quad, flash, flash_c, _glare_material(1.0, 4.0, 1))
	_draw("obstruction_lights_steady", quad, steady, steady_c, _glare_material(0.7, 2.5, 0))
	counts["obstruction_structures"] = n_bld
	counts["obstruction_lights"] = flash.size() + steady.size()


## Beyazit Tower's weather lights: a ring at its lantern, blue when it is
## clear, green for rain, yellow for fog (the tower's own code since the
## 1840s fire watch).
func _build_beyazit() -> void:
	for t: Dictionary in _features().get("towers", []):
		if str(t.get("name", "")) != "Beyazıt Tower":
			continue
		var p := _v2(t["p"])
		var g: float = env.ground_height(p.x, p.y)
		var h := float(t["h"]) if t.get("h") != null else 85.0
		var pts := PackedVector3Array()
		for k in 10:
			var a := TAU * k / 10.0
			pts.append(Vector3(p.x + cos(a) * 2.6, g + h - 6.0, -(p.y + sin(a) * 2.6)))
		var cols := {"clear": Color(0.15, 0.4, 1.0), "rain": Color(0.1, 1.0, 0.3), "fog": Color(1.0, 0.85, 0.1)}
		for key: String in cols:
			var mmi := add_lights("beyazit_%s_lights" % key, pts, cols[key], 0.7, 0)
			if mmi != null:
				(mmi.material_override as ShaderMaterial).set_shader_parameter("gain", 9.0)
				mmi.visible = key == "clear"
				_beyazit[key] = mmi
		return


func _weather_now() -> String:
	var wc := env.get_node_or_null("world_clock")
	if wc == null or not wc.has_method("weather"):
		return "clear"
	var w: Dictionary = wc.call("weather")
	if float(w.get("fog", 0.0)) > 0.3:
		return "fog"
	if float(w.get("rain", 0.0)) > 0.15:
		return "rain"
	return "clear"


# ------------------------------------------------------------------ reflections
func _build_reflections() -> void:
	if _reflect.is_empty():
		return
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	var q := QuadMesh.new()
	q.size = Vector2(1.0, 1.0)
	# Moved in the vertex shader to the water between camera and light.
	q.custom_aabb = AABB(Vector3(-1e5, -100, -1e5), Vector3(2e5, 400, 2e5))
	mm.mesh = q
	mm.instance_count = _reflect.size()
	for i in _reflect.size():
		var r: Array = _reflect[i]
		var c: Color = r[1]
		var s: float = r[2]
		# basis.x carries the light's apparent size (m) in its length.
		mm.set_instance_transform(i, Transform3D(Basis().scaled(Vector3(maxf(float(r[3]), 0.5), 1, 1)), r[0]))
		mm.set_instance_custom_data(i, Color(c.r * s, c.g * s, c.b * s, fposmod(float(i) * 0.618, 1.0)))
	var mmi := MultiMeshInstance3D.new()
	mmi.name = "water_reflection_lights"
	mmi.multimesh = mm
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	mmi.extra_cull_margin = 16384.0
	_refl_mat = _reflection_material()
	mmi.material_override = _refl_mat
	_root.add_child(mmi)
	counts["reflected_lights"] = _reflect.size()


## The column a light lays on the sea (header). Geometry: along the camera's
## line to the light, from where the water's slopes (sigma) first catch it to
## where they last do: depression angles theta +- 2 sigma around the mirror
## point's theta, each turned back into a distance on the water. Across it,
## a few metres plus the slopes' sideways spread. Fragment: brighter at the
## mirror point, broken into wavelets that move, only over water.
func _reflection_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode unshaded, blend_add, depth_draw_never, cull_disabled, shadows_disabled, fog_disabled;
uniform float night = 0.0;
uniform float gain = 1.0;
uniform float sigma = 0.022;
uniform float sea_y = 0.0;
uniform sampler2D water : filter_linear;
uniform vec4 w_rect;
varying vec3 v_col;
varying vec3 v_world;
varying float v_s;
varying float v_w;
varying float v_along;
varying float v_phase;
varying float v_wm;
float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1.0, 0.0)), f.x),
               mix(hash12(i + vec2(0.0, 1.0)), hash12(i + vec2(1.0, 1.0)), f.x), f.y);
}
void vertex() {
    vec3 P = MODEL_MATRIX[3].xyz;
    float size = length(MODEL_MATRIX[0].xyz);
    vec3 C = CAMERA_POSITION_WORLD;
    float hc = max(C.y - sea_y, 0.5);
    float hp = max(P.y - sea_y, 0.3);
    vec2 dxz = P.xz - C.xz;
    float D = max(length(dxz), 1.0);
    vec2 u = dxz / D;
    vec2 v = vec2(-u.y, u.x);
    float dh = D * hc / (hc + hp);
    float th = atan(hc, dh);
    // The quad's corner (+-0.5, +-0.5): along (near .. far) and across.
    float s = sign(VERTEX.x);
    float w = sign(VERTEX.y);
    float ths = clamp(th - s * 2.0 * sigma, 0.004, 1.55);
    float d = min(hc / tan(ths), D + 30.0);
    float hw = 0.28 * size + 0.004 * d + 0.3;
    vec2 xz = C.xz + u * d + v * w * hw;
    v_world = vec3(xz.x, sea_y + 0.35, xz.y);
    POSITION = PROJECTION_MATRIX * (VIEW_MATRIX * vec4(v_world, 1.0));
    v_s = s;
    v_w = w;
    v_wm = w * hw;
    v_along = d;
    v_col = INSTANCE_CUSTOM.rgb;
    v_phase = INSTANCE_CUSTOM.a;
}
void fragment() {
    vec2 en = vec2(v_world.x, -v_world.z);
    vec2 wuv = vec2((en.x - w_rect.x) / w_rect.z, 1.0 - (en.y - w_rect.y) / w_rect.w);
    float wet = (wuv.x < 0.0 || wuv.y < 0.0 || wuv.x > 1.0 || wuv.y > 1.0) ? 1.0 : texture(water, wuv).r;
    wet = smoothstep(0.55, 0.95, wet);
    float along = exp(-v_s * v_s * 2.2);
    float across = exp(-v_w * v_w * 2.6);
    // Wavelets: crests across the column catching the light and losing it,
    // drifting; finer near the camera than far off.
    float n = 0.6 * vnoise(vec2(v_along * 0.42 + TIME * 0.7, v_wm * 0.30 + v_phase * 13.0))
            + 0.4 * vnoise(vec2(v_along * 1.25 - TIME * 1.1, v_wm * 0.85 + v_phase * 7.0));
    float glint = smoothstep(0.42, 0.78, n);
    ALBEDO = v_col * gain * night * along * across * (0.12 + 1.1 * glint) * wet;
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("gain", float(IpcServer._get_launch_arg("--night-reflect-gain", "0.55")))
	var wm: Dictionary = env.water_mask()
	var b: Rect2 = wm["bounds"]
	m.set_shader_parameter("water", wm["tex"])
	m.set_shader_parameter("w_rect", Vector4(b.position.x, b.position.y, b.size.x, b.size.y))
	m.set_shader_parameter("sea_y", float(env.get("SEA_Y")))
	_mats.append(m)
	return m


# ------------------------------------------------------------------ per frame
func _process(delta: float) -> void:
	super._process(delta)
	if _lm_own >= 0.0:
		var k := _lm_own * clampf(_night_amount(), 0.0, 1.0)
		if absf(k - _lm_last) > 0.002:
			_lm_last = k
			var lm := _landmarks_node()
			if lm != null:
				lm.call("set_flood", k)
	if _night > 0.05 and not _beyazit.is_empty():
		var wk := _weather_now()
		if wk != _weather_key:
			_weather_key = wk
			for key: String in _beyazit:
				(_beyazit[key] as Node3D).visible = key == wk
