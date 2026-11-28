## Baylands built structures other than buildings and power lines: collision
## for the measured roofs, and (as this node) everything else people built
## here that an aircraft can see or hit.
##
## claim(env) runs before anything is drawn or the router's grid is cut:
##   * roofs (baylands_buildings' built_roofs.dat): pitched and measured-
##     surface roofs raise the footprint prism to their highest point; flat
##     roofs put the prism at the drawn roof/parapet and add every upper tier
##     and rooftop unit as its own solid (never below what the lidar saw: every
##     cell standing >0.5 m over the drawn roof is inside a unit);
##   * picnic shelters and carports open underneath (posts solid, roof slab
##     solid), bridge decks, poles and light standards, sports-field lights,
##     flagpoles, masts, fences/walls over 2 m, ride and stadium blobs.
## Everything solid here is drawn by this node; small things (benches,
## tables, bollards, low fences, parked cars) are drawn but not solid.
##
## Data: assets/baylands/built_roofs.dat, built_things.json, baked by
## drone/sim/tools/bake_baylands_built.py from the 2020 lidar, NAIP 2024 and
## OpenStreetMap. Power lines, their towers and poles are baylands_wires'.
extends Node3D

const BB = preload("res://scripts/baylands_buildings.gd")
const MS = preload("res://scripts/baylands_built_meshes.gd")
const THINGS := "res://assets/baylands/built_things.json"
const CHUNK := 1250.0
## Parked cars fade out past this (m); the photo under them carries the
## lots beyond.
const CAR_RANGE := 900.0

## Flat roofs: what the prism may sit above the drawn roof/parapet before a
## lone return counts (lidar noise, vents).
const ROOF_TOL := 0.4


static func things(env) -> Dictionary:
	if env.has_meta("bl_things"):
		return env.get_meta("bl_things")
	var d := {}
	if FileAccess.file_exists(THINGS):
		var v = JSON.parse_string(FileAccess.get_file_as_string(THINGS))
		if v is Dictionary:
			d = v
	else:
		push_warning("[baylands_built] %s missing: run drone/sim/tools/bake_baylands_built.py" % THINGS)
	env.set_meta("bl_things", d)
	return d


static func unpack_u16(b64: String, n: int) -> PackedByteArray:
	# Godot's DEFLATE mode reads the zlib stream Python's zlib.compress writes.
	return Marshalls.base64_to_raw(b64).decompress(n * 2, FileAccess.COMPRESSION_DEFLATE)


static func _square(c: Vector2, h: float) -> PackedVector2Array:
	return PackedVector2Array([c + Vector2(-h, -h), c + Vector2(h, -h), c + Vector2(h, h), c + Vector2(-h, h)])


static func _orect(c: Vector2, u: Vector2, a0: float, a1: float, hw: float) -> PackedVector2Array:
	var v := Vector2(-u.y, u.x)
	return PackedVector2Array([c + u * a0 - v * hw, c + u * a1 - v * hw, c + u * a1 + v * hw, c + u * a0 + v * hw])


static func claim(env) -> bool:
	var R: Dictionary = BB.load_roofs(env)
	var stats := {"raised": 0, "lowered": 0, "unit_solids": 0, "tier_solids": 0, "open_roofs": 0,
		"poles": 0, "structures": 0, "fences": 0, "bridge_tiles": 0, "lines": 0, "other": 0}
	var adds: Array = []
	if not R.is_empty():
		var tops: PackedFloat32Array = env.b_top
		var minh: PackedFloat32Array = env.b_minh
		var n := mini(int(R["n"]), tops.size())
		for i in n:
			var t := int(R["type"][i])
			if BB.is_open_roof(env, i):
				# picnic shelter / carport: the roof slab is solid, the posts
				# are solid, the space under it is open
				var under := BB.open_underside(env, R, i)
				minh[i] = maxf(under - env.b_base[i], 0.0)
				var g: float = env.ground_height(env.b_centre[i].x, env.b_centre[i].y)
				for p in BB.open_posts(env.footprint(i)):
					adds.append([_square(p, 0.15), g - 0.3, under + 0.05])
				stats["open_roofs"] += 1
			if t == 2 or t == 3:
				var z1: float = R["z1"][i]
				if z1 > tops[i]:
					tops[i] = z1
					stats["raised"] += 1
			elif t == 1:
				var L0: float = R["z0"][i]
				var drawn := L0 + float(R["parapet"][i])
				var new_top := drawn + ROOF_TOL
				for p in (R["parts"][i] as Array):
					adds.append([p["ring"], L0, float(p["top"]) + 0.1])
					stats["tier_solids"] += 1
				var ul: PackedFloat32Array = R["units"][i]
				for k in range(0, ul.size(), 7):
					var u := Vector2(cos(ul[k + 4]), sin(ul[k + 4]))
					var v := Vector2(-u.y, u.x)
					var c := Vector2(ul[k], ul[k + 1])
					var hl := ul[k + 2]
					var hw := ul[k + 3]
					adds.append([PackedVector2Array([c - u * hl - v * hw, c + u * hl - v * hw,
						c + u * hl + v * hw, c - u * hl + v * hw]), ul[k + 5] - 0.2, ul[k + 6]])
					stats["unit_solids"] += 1
				if new_top < tops[i]:
					stats["lowered"] += 1
				elif new_top > tops[i]:
					stats["raised"] += 1
				tops[i] = new_top
		env.b_top = tops
		env.b_minh = minh
	var T := things(env)
	# poles and light standards
	for p in (T.get("poles", []) as Array):
		var c := Vector2(p[0], p[1])
		var g: float = env.ground_height(c.x, c.y)
		var top := maxf(float(p[2]), g + 2.0)
		var spec: Array = MS.pole_solid(int(p[4]))
		adds.append([_square(c, spec[0]), g - 0.3, top])
		stats["poles"] += 1
		var hd: Array = spec[1]
		if not hd.is_empty():
			var u := Vector2(cos(p[5]), sin(p[5]))
			adds.append([_orect(c, u, -0.3, hd[0], hd[1]), top - hd[2], top + 0.2])
	# measured structures: 4 m tiles of 1 m columns
	for b in (T.get("blobs", []) as Array):
		var w: int = b["w"]
		var h: int = b["h"]
		var top := unpack_u16(b["top"], w * h)
		var bot := unpack_u16(b["bot"], w * h)
		var o := Vector2(b["o"][0], b["o"][1])
		for ty in range(0, h, 4):
			for tx in range(0, w, 4):
				var zt := -INF
				var zb := INF
				var x0 := 1 << 20
				var x1 := -1
				var y0 := 1 << 20
				var y1 := -1
				for yy in range(ty, mini(ty + 4, h)):
					for xx in range(tx, mini(tx + 4, w)):
						var k := (yy * w + xx) * 2
						var qt := top.decode_u16(k)
						if qt == 0:
							continue
						zt = maxf(zt, qt * 0.1 - 10.0)
						zb = minf(zb, bot.decode_u16(k) * 0.1 - 10.0)
						x0 = mini(x0, xx); x1 = maxi(x1, xx); y0 = mini(y0, yy); y1 = maxi(y1, yy)
				if x1 < 0:
					continue
				var e0 := o.x + x0 - 0.5
				var e1 := o.x + x1 + 0.5
				var n1 := o.y - y0 + 0.5
				var n0 := o.y - y1 - 0.5
				var gc: float = env.ground_height((e0 + e1) * 0.5, (n0 + n1) * 0.5)
				if zt - gc < 2.0:
					continue
				adds.append([PackedVector2Array([Vector2(e0, n0), Vector2(e1, n0), Vector2(e1, n1), Vector2(e0, n1)]),
					minf(zb, gc + 0.5) if zb - gc < 1.5 else zb, zt])
				stats["structures"] += 1
	# fences and walls over 2 m
	for f in (T.get("fences", []) as Array):
		if float(f["h"]) <= 2.0:
			continue
		var pts: Array = f["pts"]
		for k in pts.size() - 1:
			var a := Vector2(pts[k][0], pts[k][1])
			var bb := Vector2(pts[k + 1][0], pts[k + 1][1])
			var L := a.distance_to(bb)
			if L < 0.1:
				continue
			var u := (bb - a) / L
			var ns := maxi(1, int(ceil(L / 15.0)))
			for s in ns:
				var c0 := a + u * (L * s / ns)
				var c1 := a + u * (L * (s + 1) / ns)
				var g := minf(env.ground_height(c0.x, c0.y), env.ground_height(c1.x, c1.y))
				var gt := maxf(env.ground_height(c0.x, c0.y), env.ground_height(c1.x, c1.y))
				adds.append([_orect(c0, u, 0.0, L / ns, 0.15), g - 0.3, gt + float(f["h"])])
				stats["fences"] += 1
	# bridge decks: 6 m tiles from the deck's underside to its surface; the
	# parapets as line solids
	for br in (T.get("bridges", []) as Array):
		var deck := Deck.new(br)
		for ring in deck.rings:
			var bb := Rect2(ring[0], Vector2.ZERO)
			for p in ring:
				bb = bb.expand(p)
			var ex := floorf(bb.position.x / 6.0) * 6.0
			while ex < bb.end.x:
				var ny := floorf(bb.position.y / 6.0) * 6.0
				while ny < bb.end.y:
					var tile := PackedVector2Array([Vector2(ex, ny), Vector2(ex + 6, ny), Vector2(ex + 6, ny + 6), Vector2(ex, ny + 6)])
					for poly in Geometry2D.intersect_polygons(ring, tile):
						if Geometry2D.is_polygon_clockwise(poly):
							continue
						var zmin := INF
						var zmax := -INF
						var cc := Vector2.ZERO
						for p in poly:
							var z := deck.z(p)
							zmin = minf(zmin, z); zmax = maxf(zmax, z)
							cc += p
						cc /= poly.size()
						var zc := deck.z(cc)
						zmin = minf(zmin, zc); zmax = maxf(zmax, zc)
						adds.append([poly, zmin - Deck.THICK, zmax + 0.15])
						stats["bridge_tiles"] += 1
					ny += 6.0
				ex += 6.0
			for k in ring.size():
				var a: Vector2 = ring[k]
				var b2: Vector2 = ring[(k + 1) % ring.size()]
				if deck.at_grade(env, a, b2):
					continue
				var L := a.distance_to(b2)
				var ns := maxi(1, int(ceil(L / 10.0)))
				for s in ns:
					var p0 := a.lerp(b2, float(s) / ns)
					var p1 := a.lerp(b2, float(s + 1) / ns)
					env.add_line_obstacle(Vector3(p0.x, p0.y, deck.z(p0) + 0.55), Vector3(p1.x, p1.y, deck.z(p1) + 0.55), 0.5, "parapet")
					stats["lines"] += 1
	# soccer goals: posts and crossbar as line solids
	var gi := 0
	for gl in (T.get("goals", []) as Array):
		var c := Vector2(gl[0], gl[1])
		var g: float = env.ground_height(c.x, c.y)
		var u := Vector2(cos(gl[3]), sin(gl[3]))
		var v := Vector2(-u.y, u.x)
		var hw: float = float(gl[4]) * 0.5
		var hh := 2.44 if float(gl[4]) > 6.0 else 1.83
		var pa := c - v * hw
		var pb := c + v * hw
		env.add_line_obstacle(Vector3(pa.x, pa.y, g), Vector3(pa.x, pa.y, g + hh), 0.1, "goal")
		env.add_line_obstacle(Vector3(pb.x, pb.y, g), Vector3(pb.x, pb.y, g + hh), 0.1, "goal")
		env.add_line_obstacle(Vector3(pa.x, pa.y, g + hh), Vector3(pb.x, pb.y, g + hh), 0.1, "goal")
		stats["lines"] += 3
		gi += 1
	# furniture over 2 m (bus shelters, fitness bars) and traffic-signal masts
	for f in (T.get("furniture", []) as Array):
		var kind: String = f[3]
		var c := Vector2(f[0], f[1])
		var g: float = env.ground_height(c.x, c.y)
		var ang: float = f[4]
		var sd: Array = MS.furniture_solid(kind)
		if not sd.is_empty():
			var u := Vector2(cos(ang), sin(ang))
			adds.append([_orect(c, u, -sd[0], sd[0], sd[1]), g - 0.3, g + sd[2]])
			stats["other"] += 1
		elif kind == "signal" and not _pole_near(T, c, 8.0):
			adds.append([_square(c, 0.17), g - 0.3, g + 6.7])
			var u := Vector2(cos(ang + PI * 0.5), sin(ang + PI * 0.5))
			var tip := c + u * 8.0
			env.add_line_obstacle(Vector3(c.x, c.y, g + 6.3), Vector3(tip.x, tip.y, g + 6.4), 0.6, "signal")
			stats["other"] += 1
	for a in adds:
		env.add_obstacle(a[0], a[1], a[2])
	print("[baylands_built] claim: %s, %d solids" % [str(stats), adds.size()])
	return true


static func _pole_near(T: Dictionary, c: Vector2, r: float) -> bool:
	for p in (T.get("poles", []) as Array):
		if absf(float(p[0]) - c.x) < r and absf(float(p[1]) - c.y) < r:
			return true
	return false


## A bridge deck: its outline rings and the measured deck top on 1 m posts.
class Deck:
	const THICK := 1.4
	var rings: Array = []
	var o := Vector2.ZERO
	var w := 0
	var h := 0
	var q := PackedByteArray()
	var under := 0.0

	func _init(d: Dictionary) -> void:
		for r in (d["rings"] as Array):
			var ring := PackedVector2Array()
			for p in r:
				ring.append(Vector2(p[0], p[1]))
			if Geometry2D.is_polygon_clockwise(ring):
				ring.reverse()
			rings.append(ring)
		o = Vector2(d["o"][0], d["o"][1])
		w = d["w"]
		h = d["h"]
		under = d["under"]
		q = Marshalls.base64_to_raw(d["z"]).decompress(w * h * 2, FileAccess.COMPRESSION_DEFLATE)

	func z(p: Vector2) -> float:
		var fx := clampf(p.x - o.x, 0.0, float(w - 1))
		var fy := clampf(o.y - p.y, 0.0, float(h - 1))
		var x0 := int(fx)
		var y0 := int(fy)
		var x1 := mini(x0 + 1, w - 1)
		var y1 := mini(y0 + 1, h - 1)
		var tx := fx - x0
		var ty := fy - y0
		return lerpf(lerpf(_at(x0, y0), _at(x1, y0), tx), lerpf(_at(x0, y1), _at(x1, y1), tx), ty)

	func _at(x: int, y: int) -> float:
		return float(q.decode_u16((y * w + x) * 2)) / 20.0 - 10.0

	## An edge where the deck meets the road at grade (the abutment ends):
	## no parapet or fascia across the carriageway there.
	func at_grade(env, a: Vector2, b: Vector2) -> bool:
		var m := (a + b) * 0.5
		var e := b - a
		if e.length() < 0.01:
			return true
		var out := Vector2(e.y, -e.x).normalized()      # outward of a CCW ring
		var g: float = env.ground_height(m.x + out.x * 2.5, m.y + out.y * 2.5)
		return z(m) - g < 1.6


# ===================================================================== node

var env


func _ready() -> void:
	env = get_parent()
	if env == null or not env.has_method("ground_height"):
		return
	name = "baylands_built"
	var t0 := Time.get_ticks_msec()
	var T := things(env)
	# --bl-skip=cars,blobs,... leaves layers out (profiling)
	var skip := ""
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--bl-skip="):
			skip = a.substr(10)
	if not "poles" in skip: _build_poles(T)
	if not "blobs" in skip: _build_blobs(T)
	if not "fences" in skip: _build_fences(T)
	if not "bridges" in skip: _build_bridges(T)
	if not "furniture" in skip: _build_furniture(T)
	if not "cars" in skip: _build_cars(T)
	_flush()
	print("[baylands_built] drawn in %d ms" % (Time.get_ticks_msec() - t0))


## Static geometry per chunk: opaque structures (poles, furniture,
## measured blobs, walls, fence posts, bridges) in one mesh, chain-link in
## another; parked cars are one MultiMesh per chunk.
var _struct := {}
var _link := {}


func _key(e: float, n: float) -> Vector2i:
	return Vector2i(floori(e / CHUNK), floori(n / CHUNK))


func _sm(e: float, n: float) -> MS.Mesher:
	var k := _key(e, n)
	if not _struct.has(k):
		_struct[k] = MS.Mesher.new()
	return _struct[k]


func _lm(e: float, n: float) -> MS.Mesher:
	var k := _key(e, n)
	if not _link.has(k):
		_link[k] = MS.Mesher.new()
	return _link[k]


func _flush() -> void:
	var smat := struct_material()
	var lmat := fence_material()
	var tris := 0
	for k in _struct:
		var m: MS.Mesher = _struct[k]
		if m.pos.is_empty():
			continue
		var mi := MeshInstance3D.new()
		mi.name = "built_%d_%d" % [k.x, k.y]
		mi.mesh = m.mesh()
		mi.material_override = smat
		add_child(mi)
		tris += m.idx.size() / 3
	for k in _link:
		var m: MS.Mesher = _link[k]
		if m.pos.is_empty():
			continue
		var mi := MeshInstance3D.new()
		mi.name = "chainlink_%d_%d" % [k.x, k.y]
		mi.mesh = m.mesh()
		mi.material_override = lmat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mi.visibility_range_end = 900.0
		add_child(mi)
		tris += m.idx.size() / 3
	print("[baylands_built] %d structure chunks, %d chain-link chunks, %d triangles" % [_struct.size(), _link.size(), tris])


func _gp(e: float, n: float, y: float) -> Vector3:
	return Vector3(e, y, -n)


## ENU heading (rad from east, CCW) -> a Godot basis whose +X points there.
func _yaw(a: float) -> Basis:
	return Basis(Vector3.UP, a)


func _build_poles(T: Dictionary) -> void:
	var shafts := {}
	var heads := {}
	for p in (T.get("poles", []) as Array):
		var k := int(p[4])
		if not shafts.has(k):
			shafts[k] = MS.shaft(k)
			heads[k] = MS.head(k)
		var g: float = env.ground_height(p[0], p[1])
		var top := maxf(float(p[2]), g + 2.0)
		var m := _sm(p[0], p[1])
		m.append(shafts[k], Transform3D(Basis().scaled(Vector3(1, top - g + 0.3, 1)), _gp(p[0], p[1], g - 0.3)))
		m.append(heads[k], Transform3D(_yaw(float(p[5])), _gp(p[0], p[1], top)))


func _build_furniture(T: Dictionary) -> void:
	var meshes := {}
	for f in (T.get("furniture", []) as Array):
		var kind: String = f[3]
		if kind == "camera":
			continue
		if kind == "signal" and _pole_near(T, Vector2(f[0], f[1]), 8.0):
			continue
		if not meshes.has(kind):
			meshes[kind] = MS.furniture(kind)
		var g: float = env.ground_height(f[0], f[1])
		var a: float = float(f[4]) + (PI * 0.5 if kind == "signal" else 0.0)
		_sm(f[0], f[1]).append(meshes[kind], Transform3D(_yaw(a), _gp(f[0], f[1], g)))
	# soccer goals (pairs: the first at the -u end faces +u)
	var g7 := MS.goal(7.32)
	var g5 := MS.goal(5.0)
	var gi := 0
	for gl in (T.get("goals", []) as Array):
		var g: float = env.ground_height(gl[0], gl[1])
		var a: float = float(gl[3]) + (0.0 if gi % 2 == 0 else PI)
		_sm(gl[0], gl[1]).append(g7 if float(gl[4]) > 6.0 else g5, Transform3D(_yaw(a), _gp(gl[0], gl[1], g)))
		gi += 1


func _build_cars(T: Dictionary) -> void:
	var cars: Array = T.get("cars", [])
	if cars.is_empty():
		return
	var chunks := {}
	for c in cars:
		var key := _key(float(c[0]), float(c[1]))
		if not chunks.has(key):
			chunks[key] = [[], []]
		var g: float = env.ground_height(c[0], c[1])
		chunks[key][0].append(Transform3D(_yaw(float(c[3])), _gp(c[0], c[1], g)))
		var col: Color = CAR_COLOURS[int(c[5]) % CAR_COLOURS.size()]
		chunks[key][1].append(Color(float(c[4]), col.r, col.g, col.b))
	var mesh := MS.car_mesh()
	var mat := car_material()
	for key in chunks:
		var xf: Array = chunks[key][0]
		var cu: Array = chunks[key][1]
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = mesh
		mm.instance_count = xf.size()
		for k in xf.size():
			mm.set_instance_transform(k, xf[k])
			mm.set_instance_custom_data(k, cu[k])
		var mi := MultiMeshInstance3D.new()
		mi.name = "cars_%d_%d" % [key.x, key.y]
		mi.multimesh = mm
		mi.material_override = mat
		mi.visibility_range_end = CAR_RANGE
		mi.visibility_range_end_margin = 100.0
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)


## Weekday car-park colours (US fleet shares): white, black, grey, silver,
## blue, red, brown/beige, green.
const CAR_COLOURS := [Color(0.86, 0.86, 0.85), Color(0.05, 0.05, 0.06), Color(0.32, 0.33, 0.35),
	Color(0.62, 0.63, 0.64), Color(0.12, 0.20, 0.38), Color(0.50, 0.06, 0.06), Color(0.45, 0.37, 0.28),
	Color(0.15, 0.25, 0.18)]


# -------------------------------------------------------------- blobs

func _build_blobs(T: Dictionary) -> void:
	var ga := _area_named("California's Great America")
	var plays: Array = []
	for p in (T.get("playgrounds", []) as Array):
		var ring := PackedVector2Array()
		for q in p["ring"]:
			ring.append(Vector2(q[0], q[1]))
		plays.append(ring)
	for b in (T.get("blobs", []) as Array):
		var c := Vector2(b["c"][0], b["c"][1])
		var palette := 0
		if b.has("play"):
			palette = 2
		elif not ga.is_empty() and Geometry2D.is_point_in_polygon(c, ga):
			palette = 1
		else:
			for r in plays:
				if Geometry2D.is_point_in_polygon(c, r) or _dist_ring(c, r) < 6.0:
					palette = 2
					break
		_blob_mesh(_sm(c.x, c.y), b, palette)


func _dist_ring(p: Vector2, ring: PackedVector2Array) -> float:
	var best := INF
	for k in ring.size():
		best = minf(best, p.distance_to(Geometry2D.get_closest_point_to_segment(p, ring[k], ring[(k + 1) % ring.size()])))
	return best


func _area_named(nm: String) -> PackedVector2Array:
	var f := FileAccess.open("res://assets/baylands/features.json", FileAccess.READ)
	if f == null:
		return PackedVector2Array()
	var d = JSON.parse_string(f.get_as_text())
	if not (d is Dictionary):
		return PackedVector2Array()
	for a in d.get("areas", []):
		if a.get("tags", {}).get("name", "") == nm:
			var ring := PackedVector2Array()
			for p in a["pts"]:
				ring.append(Vector2(p[0], p[1]))
			return ring
	return PackedVector2Array()


const DX := [1, -1, 0, 0]
const DY := [0, 0, -1, 1]
const RIDE_COLS := [Color(0.85, 0.85, 0.82), Color(0.75, 0.15, 0.12), Color(0.15, 0.30, 0.65),
	Color(0.90, 0.70, 0.15), Color(0.20, 0.55, 0.35), Color(0.55, 0.56, 0.55)]
const PLAY_COLS := [Color(0.20, 0.45, 0.30), Color(0.85, 0.60, 0.15), Color(0.25, 0.40, 0.65), Color(0.55, 0.42, 0.30)]


## 1 m columns from the lowest to the highest class-1 return: tops, and the
## sides where a neighbour is empty or lower; an underside where it floats.
func _blob_mesh(m: MS.Mesher, b: Dictionary, palette: int) -> void:
	var w: int = b["w"]
	var h: int = b["h"]
	var top := unpack_u16(b["top"], w * h)
	var bot := unpack_u16(b["bot"], w * h)
	if top.size() != w * h * 2 or bot.size() != w * h * 2:
		return
	var o := Vector2(b["o"][0], b["o"][1])
	var hs := fposmod(o.x * 0.37 + o.y * 0.73, 1.0)
	var cl: Color = MS.GALV
	if palette == 1:
		cl = RIDE_COLS[int(hs * 59.0) % RIDE_COLS.size()]
	elif palette == 2:
		cl = PLAY_COLS[int(hs * 37.0) % PLAY_COLS.size()]
	var zt := PackedFloat32Array()
	var zb := PackedFloat32Array()
	zt.resize(w * h)
	zb.resize(w * h)
	for k in w * h:
		var qt := top.decode_u16(k * 2)
		zt[k] = qt * 0.1 - 10.0 if qt != 0 else -INF
		zb[k] = bot.decode_u16(k * 2) * 0.1 - 10.0
	for y in h:
		for x in w:
			var k := y * w + x
			if zt[k] == -INF:
				continue
			var e := o.x + x
			var n := o.y - y
			var t := zt[k]
			var bb := zb[k]
			var x0 := e - 0.5
			var x1 := e + 0.5
			var n0 := n - 0.5
			var n1 := n + 0.5
			m.quad(_gp(x0, n0, t), _gp(x1, n0, t), _gp(x1, n1, t), _gp(x0, n1, t), cl, Vector3.UP)
			var g: float = env.ground_height(e, n)
			if bb > g + 0.5:
				m.quad(_gp(x0, n0, bb), _gp(x0, n1, bb), _gp(x1, n1, bb), _gp(x1, n0, bb), cl, Vector3.DOWN)
			for side in 4:
				var nx: int = x + DX[side]
				var ny: int = y + DY[side]
				var lo := bb
				if nx >= 0 and ny >= 0 and nx < w and ny < h:
					var kk: int = ny * w + nx
					if zt[kk] != -INF:
						lo = maxf(bb, zt[kk])
				if lo >= t - 0.05:
					continue
				match side:
					0: m.quad(_gp(x1, n0, lo), _gp(x1, n1, lo), _gp(x1, n1, t), _gp(x1, n0, t), cl.darkened(0.06), Vector3(1, 0, 0))
					1: m.quad(_gp(x0, n1, lo), _gp(x0, n0, lo), _gp(x0, n0, t), _gp(x0, n1, t), cl.darkened(0.06), Vector3(-1, 0, 0))
					2: m.quad(_gp(x1, n1, lo), _gp(x0, n1, lo), _gp(x0, n1, t), _gp(x1, n1, t), cl.darkened(0.1), Vector3(0, 0, -1))
					3: m.quad(_gp(x0, n0, lo), _gp(x1, n0, lo), _gp(x1, n0, t), _gp(x0, n0, t), cl.darkened(0.1), Vector3(0, 0, 1))


# -------------------------------------------------------------- fences

func _build_fences(T: Dictionary) -> void:
	for f in (T.get("fences", []) as Array):
		var pts: Array = f["pts"]
		var kind: String = f["kind"]
		var hgt: float = f["h"]
		var mesh_l := _lm(pts[0][0], pts[0][1])
		var mesh_s := _sm(pts[0][0], pts[0][1])
		for k in pts.size() - 1:
			var a := Vector2(pts[k][0], pts[k][1])
			var b := Vector2(pts[k + 1][0], pts[k + 1][1])
			var L := a.distance_to(b)
			if L < 0.05:
				continue
			var u := (b - a) / L
			var nrm := Vector3(u.y, 0, u.x)
			var ga: float = env.ground_height(a.x, a.y)
			var gb: float = env.ground_height(b.x, b.y)
			if kind in ["fence", "gate"]:
				# chain-link fabric (both faces), galvanised posts every 3 m, top rail
				var A0 := _gp(a.x, a.y, ga)
				var B0 := _gp(b.x, b.y, gb)
				var A1 := _gp(a.x, a.y, ga + hgt)
				var B1 := _gp(b.x, b.y, gb + hgt)
				mesh_l.quad(A0, B0, B1, A1, MS.GALV, nrm)
				mesh_l.quad(B0, A0, A1, B1, MS.GALV, -nrm)
				var np_ := maxi(1, int(ceil(L / 3.0)))
				for s in np_ + (1 if k == pts.size() - 2 else 0):
					var p := a.lerp(b, float(s) / np_)
					var gp: float = env.ground_height(p.x, p.y)
					mesh_s.cyl(_gp(p.x, p.y, 0.0), 0.04, 0.04, gp, gp + hgt + 0.05, 4, MS.GALV, false)
				mesh_s.beam(A1, B1, 0.045, MS.GALV)
			elif kind == "guard_rail":
				mesh_s.beam(_gp(a.x, a.y, ga + 0.62), _gp(b.x, b.y, gb + 0.62), 0.3, MS.GALV)
				var np_ := maxi(1, int(ceil(L / 2.0)))
				for s in np_:
					var p := a.lerp(b, float(s) / np_)
					var gp: float = env.ground_height(p.x, p.y)
					mesh_s.box(_gp(p.x, p.y, gp) - Vector3(0.07, 0, 0.07), _gp(p.x, p.y, gp + 0.7) + Vector3(0.07, 0, 0.07), MS.DARK)
			else:
				# walls: sound walls, garden and retaining walls, barriers
				var t := 0.25 if kind != "jersey_barrier" else 0.6
				var v := Vector2(-u.y, u.x) * t * 0.5
				var cl := Color(0.72, 0.70, 0.66, 1) if kind != "wall" else Color(0.78, 0.74, 0.66, 1)
				var a0 := a + v
				var a1 := a - v
				var b0 := b + v
				var b1 := b - v
				var top_a := ga + hgt
				var top_b := gb + hgt
				mesh_s.quad(_gp(a1.x, a1.y, ga - 0.3), _gp(b1.x, b1.y, gb - 0.3), _gp(b1.x, b1.y, top_b), _gp(a1.x, a1.y, top_a), cl, nrm)
				mesh_s.quad(_gp(b0.x, b0.y, gb - 0.3), _gp(a0.x, a0.y, ga - 0.3), _gp(a0.x, a0.y, top_a), _gp(b0.x, b0.y, top_b), cl, -nrm)
				mesh_s.quad(_gp(a0.x, a0.y, top_a), _gp(a1.x, a1.y, top_a), _gp(b1.x, b1.y, top_b), _gp(b0.x, b0.y, top_b), cl.lightened(0.05), Vector3.UP)
				if k == 0:
					mesh_s.quad(_gp(a0.x, a0.y, ga - 0.3), _gp(a1.x, a1.y, ga - 0.3), _gp(a1.x, a1.y, top_a), _gp(a0.x, a0.y, top_a), cl, Vector3(-u.x, 0, u.y))


# -------------------------------------------------------------- bridges

func _build_bridges(T: Dictionary) -> void:
	var asphalt := Color(0.30, 0.30, 0.29, 1)
	var conc := Color(0.70, 0.69, 0.65, 1)
	for br in (T.get("bridges", []) as Array):
		var deck := Deck.new(br)
		for ring in deck.rings:
			var m := _sm(ring[0].x, ring[0].y)
			# deck surface: the outline plus 2 m posts inside it
			var pts := PackedVector2Array(ring)
			var bb := Rect2(ring[0], Vector2.ZERO)
			for p in ring:
				bb = bb.expand(p)
			var y := ceilf(bb.position.y)
			while y < bb.end.y:
				var x := ceilf(bb.position.x)
				while x < bb.end.x:
					var p := Vector2(x, y)
					if Geometry2D.is_point_in_polygon(p, ring) and _dist_ring(p, ring) > 0.4:
						pts.append(p)
					x += 2.0
				y += 2.0
			var tri := Geometry2D.triangulate_delaunay(pts)
			for k in range(0, tri.size(), 3):
				var a: Vector2 = pts[tri[k]]
				var b: Vector2 = pts[tri[k + 1]]
				var c: Vector2 = pts[tri[k + 2]]
				if not Geometry2D.is_point_in_polygon((a + b + c) / 3.0, ring):
					continue
				var A := _gp(a.x, a.y, deck.z(a))
				var B := _gp(b.x, b.y, deck.z(b))
				var C := _gp(c.x, c.y, deck.z(c))
				var n := (B - A).cross(C - A).normalized()
				if n.y < 0:
					n = -n
				m.tri(A, B, C, asphalt, n)
				var off := Vector3(0, -Deck.THICK, 0)
				m.tri(A + off, B + off, C + off, conc.darkened(0.25), -n)
			# fascia and parapets on the edges that are not at grade
			for k in ring.size():
				var a: Vector2 = ring[k]
				var b: Vector2 = ring[(k + 1) % ring.size()]
				var e := b - a
				if e.length() < 0.05:
					continue
				if deck.at_grade(env, a, b):
					continue
				var out := Vector3(e.y, 0, e.x).normalized()
				var za := deck.z(a)
				var zb := deck.z(b)
				var inn := Vector2(-e.y, e.x).normalized() * 0.35
				m.quad(_gp(a.x, a.y, za - Deck.THICK), _gp(b.x, b.y, zb - Deck.THICK), _gp(b.x, b.y, zb + 1.05), _gp(a.x, a.y, za + 1.05), conc, out)
				var a2 := a + inn
				var b2 := b + inn
				m.quad(_gp(b2.x, b2.y, zb), _gp(a2.x, a2.y, za), _gp(a2.x, a2.y, za + 1.05), _gp(b2.x, b2.y, zb + 1.05), conc, -out)
				m.quad(_gp(a.x, a.y, za + 1.05), _gp(b.x, b.y, zb + 1.05), _gp(b2.x, b2.y, zb + 1.05), _gp(a2.x, a2.y, za + 1.05), conc.lightened(0.05), Vector3.UP)


# -------------------------------------------------------------- materials

static var _struct_mat: ShaderMaterial
static var _fence_mat: ShaderMaterial
static var _car_mat: ShaderMaterial


static func struct_material() -> ShaderMaterial:
	if _struct_mat == null:
		_struct_mat = _shader_mat(STRUCT_SHADER)
	return _struct_mat


static func fence_material() -> ShaderMaterial:
	if _fence_mat == null:
		_fence_mat = _shader_mat(FENCE_SHADER)
	return _fence_mat


static func car_material() -> ShaderMaterial:
	if _car_mat == null:
		_car_mat = _shader_mat(CAR_SHADER)
	return _car_mat


static func _shader_mat(code: String) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = code
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


## Poles, furniture, structures, bridges: vertex colour (sRGB) with a little
## grime; alpha < 1 marks lenses (0.75) and glass (0.25).
const STRUCT_SHADER := """
shader_type spatial;
render_mode diffuse_burley, cull_back;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 v_w;
void vertex() { v_w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
float h21(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
void fragment() {
	vec3 c = pow(COLOR.rgb, vec3(2.2));
	float n = h21(floor(v_w.xz * 2.0) + floor(v_w.y * 2.0));
	c *= 0.94 + 0.08 * n;
	float lens = step(0.6, COLOR.a) * step(COLOR.a, 0.9);
	float glass = step(COLOR.a, 0.4);
	if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
		vec2 th = th_surface(0.6, 16.0, 0.6, 0.3);
		TH_OUT(th);
	} else {
		ALBEDO = c;
		float grey = 1.0 - smoothstep(0.02, 0.12, abs(COLOR.r - COLOR.b) + abs(COLOR.g - COLOR.b));
		METALLIC = grey * 0.35 + glass * 0.2;
		ROUGHNESS = mix(0.75, 0.15, max(glass, lens));
		EMISSION = vec3(0.0);
	}
}
"""

## Chain-link: a diamond mesh as dithered coverage, fading to its average
## coverage with distance so it never shimmers.
const FENCE_SHADER := """
shader_type spatial;
render_mode diffuse_burley, cull_disabled, depth_draw_opaque;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 v_w;
void vertex() { v_w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
	vec2 p = vec2(v_w.x + v_w.z, v_w.y) / 0.06;
	vec2 d = abs(fract(vec2(p.x + p.y, p.x - p.y) * 0.5) - 0.5);
	float wire = 1.0 - smoothstep(0.06, 0.14, min(d.x, d.y));
	float fw = length(fwidth(p));
	float cov = mix(wire, 0.28, smoothstep(0.3, 1.2, fw));
	if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
		vec2 th = th_surface(0.5, 10.0, 0.2, 0.0);
		TH_OUT(th);
	} else {
		ALBEDO = pow(COLOR.rgb, vec3(2.2)) * 0.9;
		METALLIC = 0.5;
		ROUGHNESS = 0.5;
	}
	ALPHA = cov;
	ALPHA_HASH_SCALE = 1.0;
}
"""

## Parked cars: one topology, four bodies (VERTEX sedan, CUSTOM0..2 SUV,
## pickup, van) chosen by INSTANCE_CUSTOM.r; paint colour INSTANCE_CUSTOM.gba.
const CAR_SHADER := """
shader_type spatial;
render_mode diffuse_burley, cull_back;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 v_paint;
varying float v_part;
vec3 unpack_n(float f) {
	float x = mod(f, 64.0);
	float y = mod(floor(f / 64.0), 64.0);
	float z = floor(f / 4096.0);
	return normalize(vec3(x, y, z) / 63.0 * 2.0 - 1.0);
}
void vertex() {
	int body = int(INSTANCE_CUSTOM.r + 0.5);
	if (body == 1) { VERTEX = CUSTOM0.xyz; NORMAL = unpack_n(CUSTOM0.w); }
	else if (body == 2) { VERTEX = CUSTOM1.xyz; NORMAL = unpack_n(CUSTOM1.w); }
	else if (body == 3) { VERTEX = CUSTOM2.xyz; NORMAL = unpack_n(CUSTOM2.w); }
	v_paint = INSTANCE_CUSTOM.gba;
	v_part = COLOR.g;
}
void fragment() {
	vec3 paint = pow(v_paint, vec3(2.2));
	vec3 c = paint; float rough = 0.32; float metal = 0.45;
	if (v_part > 0.12 && v_part < 0.37) { c = vec3(0.02, 0.025, 0.03); rough = 0.05; metal = 0.2; }
	else if (v_part >= 0.37 && v_part < 0.62) { c = vec3(0.025); rough = 0.8; metal = 0.0; }
	else if (v_part >= 0.62 && v_part < 0.9) { c = mix(vec3(0.5, 0.05, 0.03), vec3(0.8), 0.4); rough = 0.2; metal = 0.0; }
	else if (v_part >= 0.9) { c = vec3(0.02); rough = 1.0; metal = 0.0; }
	if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
		vec2 th = th_surface(0.7, 22.0, 0.25, 0.0);
		TH_OUT(th);
	} else {
		ALBEDO = c; ROUGHNESS = rough; METALLIC = metal;
	}
}
"""
