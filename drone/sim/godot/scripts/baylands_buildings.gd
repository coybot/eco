## Baylands buildings with MEASURED roofs: replaces env_baylands' plain prisms
## (static build(env) -> true).
##
## Every footprint's roof comes from the 2020 lidar inside it, baked by
## drone/sim/tools/bake_baylands_built.py into assets/baylands/built_roofs.dat:
##   * flat roofs at their measured level, a parapet where the edge ring stands
##     above it, upper tiers (a 5-storey block on a 2-storey podium) as traced
##     polygons at their own heights, and every rooftop unit or penthouse the
##     lidar saw as a box at its measured top;
##   * pitched roofs (the Sunnyvale houses): the footprint covered by up to
##     four rectangles, each hipped or gabled, ridge direction, eave and pitch
##     fitted to the lidar cells;
##   * roofs no plane model fits (sawtooth, barrel, curved): the measured
##     surface itself on 1 m posts.
## Roof colour is the NAIP 2024 colour of that roof. Walls are plausible
## facades for what each building is here (Moffett Park glass and ribbon-window
## offices, tilt-up concrete warehouses, stucco houses and apartments, parking
## structures), drawn by one procedural shader, so the whole town is one draw
## per 500 m chunk, like the base.
##
## Collision is baylands_built.claim()'s job (it runs first): pitched and
## measured-surface roofs keep the prism at the roof's highest point; flat
## roofs get the prism at their drawn roof/parapet and every tier and rooftop
## unit as its own solid, so what is drawn is what is solid.
##
## Textures: ambientCG RoofingTiles003, RoofingTiles014A, Plaster001,
## Concrete034 (CC0), as greyscale detail in assets/baylands/built_detail.png.
extends RefCounted

const ROOFS := "res://assets/baylands/built_roofs.dat"
const DETAIL := "res://assets/baylands/built_detail.png"
const CHUNK := 500.0

# Facade classes (bake_baylands_built.WALL_*).
const W_HOUSE := 0
const W_APART := 1
const W_GLASS := 2
const W_BAND := 3
const W_TILTUP := 4
const W_SHOP := 5
const W_GARAGE := 6
const W_BLOCK := 7
const W_METAL := 8
const W_HOTEL := 9

# Surface kinds (CUSTOM0.x).
const M_WALL := 0
const M_SHINGLE := 1
const M_TILE := 2
const M_FLAT := 3
const M_UNIT := 4
const M_COPING := 5
const M_METALROOF := 6


static func build(env) -> bool:
	var R: Dictionary = load_roofs(env)
	if R.is_empty():
		return false
	var chunks := {}
	for i in env.b_first.size():
		if env.b_skip[i] != 0:
			continue
		var c: Vector2 = env.b_centre[i]
		var key := Vector2i(floori(c.x / CHUNK), floori(c.y / CHUNK))
		if not chunks.has(key):
			chunks[key] = PackedInt32Array()
		var arr: PackedInt32Array = chunks[key]
		arr.append(i)
		chunks[key] = arr
	var mat := material()
	var root: Node3D = env._building_root
	var tris := 0
	for key in chunks:
		var mb := MB.new()
		for i in (chunks[key] as PackedInt32Array):
			_building(env, R, i, mb)
		if mb.pos.is_empty():
			continue
		var mi := MeshInstance3D.new()
		mi.name = "b_%d_%d" % [key.x, key.y]
		mi.mesh = mb.commit()
		mi.material_override = mat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		root.add_child(mi)
		tris += mb.pos.size() / 3
	print("[baylands_buildings] %d chunks, %d triangles; roofs: %s" % [chunks.size(), tris, str(R["counts"])])
	return true


# ------------------------------------------------------------------ data

## Parsed built_roofs.dat, cached on the env (claim() and build() share it).
## Heights are metres above NAVD88.
static func load_roofs(env) -> Dictionary:
	if env.has_meta("bl_roofs"):
		return env.get_meta("bl_roofs")
	var R := {}
	if not FileAccess.file_exists(ROOFS):
		push_warning("[baylands_buildings] %s missing: run drone/sim/tools/bake_baylands_built.py" % ROOFS)
		env.set_meta("bl_roofs", R)
		return R
	var raw := FileAccess.get_file_as_bytes(ROOFS)
	if raw.size() < 8 or raw.slice(0, 4).get_string_from_ascii() != "BLR1":
		push_warning("[baylands_buildings] bad %s" % ROOFS)
		env.set_meta("bl_roofs", R)
		return R
	var n := raw.decode_u32(4)
	if n != env.b_first.size() and n > env.b_first.size():
		push_warning("[baylands_buildings] roofs for %d buildings, env has %d" % [n, env.b_first.size()])
	var typ := PackedByteArray(); typ.resize(n)
	var wall := PackedByteArray(); wall.resize(n)
	var col := PackedColorArray(); col.resize(n)
	var par := PackedFloat32Array(); par.resize(n)
	var z0 := PackedFloat32Array(); z0.resize(n)
	var z1 := PackedFloat32Array(); z1.resize(n)
	var parts: Array = []; parts.resize(n)
	var units: Array = []; units.resize(n)
	var counts := {"none": 0, "flat": 0, "pitched": 0, "surface": 0, "units": 0, "tiers": 0}
	var o := 8
	for i in n:
		typ[i] = raw[o]
		wall[i] = raw[o + 1]
		col[i] = Color8(raw[o + 2], raw[o + 3], raw[o + 4])
		par[i] = float(raw[o + 5]) * 0.05
		z0[i] = _zq(raw.decode_u16(o + 6))
		z1[i] = _zq(raw.decode_u16(o + 8))
		var np_ := raw[o + 10]
		var nu := raw.decode_u16(o + 11)
		o += 13
		var pl: Array = []
		for k in np_:
			if typ[i] == 2:
				pl.append({"c": Vector2(raw.decode_s16(o), raw.decode_s16(o + 2)) * 0.2,
					"ang": float(raw[o + 4]) * PI / 256.0,
					"a": float(raw.decode_u16(o + 5)) * 0.01, "b": float(raw.decode_u16(o + 7)) * 0.01,
					"kind": raw[o + 9], "ridge": _zq(raw.decode_u16(o + 10))})
				o += 12
			elif typ[i] == 1:
				var nv := raw[o]
				var ring := PackedVector2Array()
				ring.resize(nv)
				for v in nv:
					ring[v] = Vector2(raw.decode_s16(o + 1 + v * 4), raw.decode_s16(o + 3 + v * 4)) * 0.2
				o += 1 + nv * 4
				pl.append({"ring": ring, "top": _zq(raw.decode_u16(o))})
				o += 2
			elif typ[i] == 3:
				var w := raw[o + 4]
				var h := raw[o + 5]
				pl.append({"o": Vector2(raw.decode_s16(o), raw.decode_s16(o + 2)) * 0.1, "w": w, "h": h,
					"base": _zq(raw.decode_u16(o + 6)), "q": raw.slice(o + 8, o + 8 + w * h)})
				o += 8 + w * h
		parts[i] = pl
		var ul := PackedFloat32Array()
		for k in nu:
			# cx, cy, half_len, half_wid, angle, bottom, top
			ul.append_array(PackedFloat32Array([raw.decode_s16(o) * 0.2, raw.decode_s16(o + 2) * 0.2,
				raw[o + 4] * 0.1, raw[o + 5] * 0.1, raw[o + 6] * PI / 256.0,
				_zq(raw.decode_u16(o + 7)), _zq(raw.decode_u16(o + 9))]))
			o += 11
		units[i] = ul
		match int(typ[i]):
			0: counts["none"] += 1
			1:
				counts["flat"] += 1
				counts["tiers"] += np_
			2: counts["pitched"] += 1
			3: counts["surface"] += 1
		counts["units"] += nu
	R = {"n": n, "type": typ, "wall": wall, "col": col, "parapet": par, "z0": z0, "z1": z1,
		"parts": parts, "units": units, "counts": counts}
	env.set_meta("bl_roofs", R)
	return R


static func _zq(v: int) -> float:
	return float(v) / 50.0 - 10.0


# ------------------------------------------------------------------ mesh

## Mesh builder: Godot positions, normals, UV (metric: along-wall/height or
## roof plane), COLOR (sRGB base colour), CUSTOM0 (kind, facade class, seed,
## wall length).
class MB:
	var pos := PackedVector3Array()
	var nrm := PackedVector3Array()
	var uv := PackedVector2Array()
	var col := PackedColorArray()
	var cus := PackedFloat32Array()

	func tri(a: Vector3, b: Vector3, c: Vector3, n: Vector3, ua: Vector2, ub: Vector2, uc: Vector2,
			cl: Color, c0: Vector4) -> void:
		if (b - a).cross(c - a).dot(n) > 0.0:
			var t := b; b = c; c = t
			var tu := ub; ub = uc; uc = tu
		pos.append_array(PackedVector3Array([a, b, c]))
		nrm.append_array(PackedVector3Array([n, n, n]))
		uv.append_array(PackedVector2Array([ua, ub, uc]))
		col.append_array(PackedColorArray([cl, cl, cl]))
		for k in 3:
			cus.append_array(PackedFloat32Array([c0.x, c0.y, c0.z, c0.w]))

	## A planar quad a-b-c-d (in order around it).
	func quad(a: Vector3, b: Vector3, c: Vector3, d: Vector3, n: Vector3, ua: Vector2, ub: Vector2,
			uc: Vector2, ud: Vector2, cl: Color, c0: Vector4) -> void:
		tri(a, b, c, n, ua, ub, uc, cl, c0)
		tri(a, c, d, n, ua, uc, ud, cl, c0)

	func commit() -> ArrayMesh:
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = pos
		arr[Mesh.ARRAY_NORMAL] = nrm
		arr[Mesh.ARRAY_TEX_UV] = uv
		arr[Mesh.ARRAY_COLOR] = col
		arr[Mesh.ARRAY_CUSTOM0] = cus
		var m := ArrayMesh.new()
		m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr, [], {},
			Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT)
		return m


static func G(e: float, n: float, y: float) -> Vector3:
	return Vector3(e, y, -n)


static func _hash(c: Vector2) -> float:
	var v := sin(c.x * 12.9898 + c.y * 78.233) * 43758.5453
	return v - floorf(v)


## Plausible wall colours per facade class (sRGB). Sunnyvale stucco runs
## beige, cream, grey, tan, pale sage; offices concrete and painted panels.
const HOUSE_COLS := [Color(0.86, 0.80, 0.68), Color(0.90, 0.87, 0.78), Color(0.78, 0.77, 0.73),
	Color(0.76, 0.66, 0.52), Color(0.92, 0.91, 0.87), Color(0.70, 0.72, 0.62), Color(0.66, 0.70, 0.74),
	Color(0.83, 0.74, 0.62), Color(0.60, 0.56, 0.50)]
const OFFICE_COLS := [Color(0.82, 0.80, 0.76), Color(0.74, 0.73, 0.70), Color(0.88, 0.86, 0.80),
	Color(0.68, 0.66, 0.62), Color(0.80, 0.74, 0.66)]
const TILT_COLS := [Color(0.84, 0.82, 0.76), Color(0.78, 0.76, 0.70), Color(0.86, 0.80, 0.70),
	Color(0.72, 0.72, 0.70), Color(0.90, 0.89, 0.85), Color(0.70, 0.66, 0.58)]


static func wall_colour(cls: int, seed: float) -> Color:
	var k := int(seed * 997.0)
	match cls:
		W_HOUSE, W_APART, W_SHOP, W_HOTEL:
			return HOUSE_COLS[k % HOUSE_COLS.size()]
		W_TILTUP, W_GARAGE:
			return TILT_COLS[k % TILT_COLS.size()]
		W_BLOCK:
			return Color(0.74, 0.66, 0.54)
		W_METAL:
			return Color(0.78, 0.79, 0.78)
		W_GLASS:
			return [Color(0.40, 0.50, 0.56), Color(0.36, 0.44, 0.44), Color(0.48, 0.52, 0.56),
				Color(0.30, 0.36, 0.42)][k % 4]
	return OFFICE_COLS[k % OFFICE_COLS.size()]


## Walls of a closed ring from `lo` up to top(p) (a Callable(Vector2) ->
## float, or a constant when top_c >= -1e8). Edges are split at `step` m so a
## sloped top follows the roof. u runs along the perimeter.
static func _walls(mb: MB, ring: PackedVector2Array, lo: float, g: float, top_c: float, top_fn: Callable,
		cl: Color, c0: Vector4, step: float = 0.0, inward: bool = false) -> void:
	var c := ring.size()
	var u := 0.0
	for k in c:
		var p: Vector2 = ring[k]
		var q: Vector2 = ring[(k + 1) % c]
		var e := q - p
		var L := e.length()
		if L < 0.05:
			continue
		var n := Vector3(e.y, 0.0, e.x).normalized()   # ENU outward (e.y, -e.x) of a CCW ring
		if inward:
			n = -n
		var cc := Vector4(c0.x, c0.y, c0.z, minf(L, 999.0))
		var segs := 1
		if step > 0.0:
			segs = maxi(1, int(ceil(L / step)))
		for s in segs:
			var t0 := float(s) / segs
			var t1 := float(s + 1) / segs
			var a := p + e * t0
			var b := p + e * t1
			var ha := top_c if top_c > -1e8 else float(top_fn.call(a))
			var hb := top_c if top_c > -1e8 else float(top_fn.call(b))
			var ua := u + L * t0
			var ub := u + L * t1
			# CUSTOM0.w: wall length + 1000 * the wall's top (0.25 m steps above
			# the ground), so the shader keeps windows below the eave/parapet
			cc.w = minf(L, 999.0) + 1000.0 * floorf(maxf(minf(ha, hb) - g, 0.0) * 4.0)
			mb.quad(G(a.x, a.y, lo), G(b.x, b.y, lo), G(b.x, b.y, hb), G(a.x, a.y, ha), n,
				Vector2(ua - 0.0, lo - g), Vector2(ub, lo - g), Vector2(ub, hb - g), Vector2(ua, ha - g), cl, cc)
		u += L


## Flat polygon at height y, facing up (UV = world e, n).
static func _cap(mb: MB, ring: PackedVector2Array, y: float, cl: Color, c0: Vector4) -> void:
	var tri := Geometry2D.triangulate_polygon(ring)
	if tri.is_empty():
		for k in range(1, ring.size() - 1):
			tri.append_array(PackedInt32Array([0, k, k + 1]))
	for k in range(0, tri.size(), 3):
		var a: Vector2 = ring[tri[k]]
		var b: Vector2 = ring[tri[k + 1]]
		var c: Vector2 = ring[tri[k + 2]]
		mb.tri(G(a.x, a.y, y), G(b.x, b.y, y), G(c.x, c.y, y), Vector3.UP, a, b, c, cl, c0)


## An oriented box (rooftop unit, penthouse) from bottom to top.
static func _box(mb: MB, c: Vector2, hl: float, hw: float, ang: float, bottom: float, top: float,
		cl: Color, c0: Vector4) -> void:
	var u := Vector2(cos(ang), sin(ang))
	var v := Vector2(-u.y, u.x)
	var ring := PackedVector2Array([c - u * hl - v * hw, c + u * hl - v * hw, c + u * hl + v * hw, c - u * hl + v * hw])
	_walls(mb, ring, bottom, bottom, top, Callable(), cl, c0)
	_cap(mb, ring, top, cl, c0)


static func _ccw(ring: PackedVector2Array) -> PackedVector2Array:
	var a := 0.0
	for k in ring.size():
		var p: Vector2 = ring[k]
		var q: Vector2 = ring[(k + 1) % ring.size()]
		a += p.x * q.y - q.x * p.y
	if a < 0.0:
		ring.reverse()
	return ring


## Miter inset of a CCW ring by d metres (parapets); offsets clamped.
static func _inset(ring: PackedVector2Array, d: float) -> PackedVector2Array:
	var out := PackedVector2Array()
	var c := ring.size()
	for k in c:
		var p0: Vector2 = ring[(k - 1 + c) % c]
		var p1: Vector2 = ring[k]
		var p2: Vector2 = ring[(k + 1) % c]
		var e0 := (p1 - p0).normalized()
		var e1 := (p2 - p1).normalized()
		var n0 := Vector2(-e0.y, e0.x)       # inward normal of a CCW ring
		var n1 := Vector2(-e1.y, e1.x)
		var bis := (n0 + n1)
		if bis.length() < 1e-3:
			bis = n0
		bis = bis.normalized()
		var cosh := maxf(bis.dot(n1), 0.35)
		out.append(p1 + bis * (d / cosh))
	return out


static func _building(env, R: Dictionary, i: int, mb: MB) -> void:
	var fp: PackedVector2Array = _ccw(env.footprint(i))
	if fp.size() < 3:
		return
	var c: Vector2 = env.b_centre[i]
	var seed := _hash(c)
	var g: float = env.ground_height(c.x, c.y)
	var lo: float = env.b_base[i] + env.b_minh[i]
	var typ := 0
	var cls := W_SHOP
	var roof_col := Color(0.55, 0.54, 0.52)
	if i < int(R["n"]):
		typ = int(R["type"][i])
		cls = int(R["wall"][i])
		roof_col = R["col"][i]
	var wcol := wall_colour(cls, seed)
	var cw := Vector4(M_WALL, cls, seed, 0.0)
	var top: float = env.b_top[i]
	if is_open_roof(env, i):
		_open_roof(env, R, i, mb, fp, g, cls, roof_col, seed)
		return
	match typ:
		1:
			_flat(env, R, i, mb, fp, g, lo, cls, wcol, roof_col, seed)
		2:
			_pitched(env, R, i, mb, fp, g, lo, cls, wcol, roof_col, seed)
		3:
			_surface(env, R, i, mb, fp, g, lo, cls, wcol, roof_col, seed)
		_:
			_walls(mb, fp, lo, g, top, Callable(), wcol, cw)
			_cap(mb, fp, top, roof_col, Vector4(M_FLAT, cls, seed, 0.0))


static func _flat(env, R: Dictionary, i: int, mb: MB, fp: PackedVector2Array, g: float, lo: float,
		cls: int, wcol: Color, rcol: Color, seed: float) -> void:
	var L0: float = R["z0"][i]
	var par: float = R["parapet"][i]
	var cw := Vector4(M_WALL, cls, seed, 0.0)
	var cr := Vector4(M_FLAT, cls, seed, 0.0)
	var cc := Vector4(M_COPING, cls, seed, 0.0)
	var wall_top := L0 + par
	_walls(mb, fp, lo, g, wall_top, Callable(), wcol, cw)
	if par > 0.05:
		var inner := _inset(fp, 0.3)
		# parapet's inner face, coping on top, roof inside it
		_walls(mb, inner, L0, g, wall_top, Callable(), wcol.darkened(0.08), cc, 0.0, true)
		for k in fp.size():
			var a: Vector2 = fp[k]
			var b: Vector2 = fp[(k + 1) % fp.size()]
			var a2: Vector2 = inner[k]
			var b2: Vector2 = inner[(k + 1) % fp.size()]
			mb.quad(G(a.x, a.y, wall_top), G(b.x, b.y, wall_top), G(b2.x, b2.y, wall_top), G(a2.x, a2.y, wall_top),
				Vector3.UP, a, b, b2, a2, Color(0.80, 0.79, 0.76), cc)
		if Geometry2D.triangulate_polygon(inner).is_empty():
			_cap(mb, fp, L0, rcol, cr)
		else:
			_cap(mb, inner, L0, rcol, cr)
	else:
		_cap(mb, fp, L0, rcol, cr)
	for p in (R["parts"][i] as Array):
		var ring: PackedVector2Array = _ccw(p["ring"])
		var t: float = p["top"]
		_walls(mb, ring, L0, g, t, Callable(), wcol, cw)
		_cap(mb, ring, t, rcol, cr)
	_units(mb, R["units"][i], cls, seed)


## Picnic shelters, pavilions and carports (OSM building=roof): a roof on
## posts, open underneath.
static func is_open_roof(env, i: int) -> bool:
	if int(env.b_kind[i]) != 9:
		return false
	var k4 := i * 4
	var area: float = (env.b_aabb[k4 + 2] - env.b_aabb[k4]) * (env.b_aabb[k4 + 3] - env.b_aabb[k4 + 1])
	# taller than 7 m is a tree's returns over a small roof, not a shelter:
	# those keep the measured prism
	return area < 3000.0 and float(env.b_h[i]) > 2.6 and float(env.b_h[i]) < 7.0


## Height (NAVD88) of the open roof's underside.
static func open_underside(env, R: Dictionary, i: int) -> float:
	var g: float = env.ground_height(env.b_centre[i].x, env.b_centre[i].y)
	var u: float = env.b_top[i] - 0.6
	if not R.is_empty() and i < int(R["n"]):
		match int(R["type"][i]):
			1: u = float(R["z0"][i]) - 0.35
			2: u = float(R["z0"][i]) - 0.25
	return maxf(u, g + 2.2)


## Post positions: the footprint's corners, 0.3 m in, and every <= 6 m between.
static func open_posts(fp: PackedVector2Array) -> PackedVector2Array:
	var ring := _inset(_ccw(fp), 0.3)
	var out := PackedVector2Array()
	for k in ring.size():
		var a: Vector2 = ring[k]
		var b: Vector2 = ring[(k + 1) % ring.size()]
		var L := a.distance_to(b)
		var ns := maxi(1, int(ceil(L / 6.0)))
		if L < 1.0:
			continue
		for s in ns:
			out.append(a.lerp(b, float(s) / ns))
	return out


static func _open_roof(env, R: Dictionary, i: int, mb: MB, fp: PackedVector2Array, g: float,
		cls: int, rcol: Color, seed: float) -> void:
	var under := open_underside(env, R, i)
	var top: float = env.b_top[i]
	var typ := int(R["type"][i]) if i < int(R["n"]) else 0
	var cp := Vector4(M_UNIT, W_METAL, seed, 0.0)
	var post := Color(0.55, 0.52, 0.48)
	for p in open_posts(fp):
		_box(mb, p, 0.1, 0.1, 0.0, g - 0.3, under + 0.02, post, Vector4(M_COPING, cls, seed, 0.0))
	var trim := Color(0.62, 0.58, 0.52)
	if typ == 2:
		var eave: float = R["z0"][i]
		_walls(mb, fp, under, under, eave, Callable(), trim, Vector4(M_COPING, cls, seed, 0.0))
		_pitched(env, R, i, mb, fp, g, eave, cls, trim, rcol, seed, true)
	else:
		var t := float(R["z0"][i]) if typ == 1 else top
		_walls(mb, fp, under, under, t, Callable(), trim, Vector4(M_COPING, cls, seed, 0.0))
		_cap(mb, fp, t, rcol, Vector4(M_FLAT, cls, seed, 0.0))
	# underside, facing down
	var tri := Geometry2D.triangulate_polygon(fp)
	for k in range(0, tri.size(), 3):
		var a: Vector2 = fp[tri[k]]
		var b: Vector2 = fp[tri[k + 1]]
		var c: Vector2 = fp[tri[k + 2]]
		mb.tri(G(a.x, a.y, under), G(b.x, b.y, under), G(c.x, c.y, under), Vector3.DOWN, a, b, c,
			trim.darkened(0.3), Vector4(M_COPING, cls, seed, 0.0))


static func _units(mb: MB, ul: PackedFloat32Array, cls: int, seed: float) -> void:
	for k in range(0, ul.size(), 7):
		var h := ul[k + 6] - ul[k + 5]
		var big := ul[k + 2] * ul[k + 3] > 40.0
		# penthouses / stair towers read as building, small boxes as HVAC
		var useed := _hash(Vector2(ul[k], ul[k + 1]))
		var cl := Color(0.80, 0.80, 0.77) if useed < 0.6 else Color(0.70, 0.71, 0.70)
		var c0 := Vector4(M_UNIT, cls, useed, 0.0)
		if big and h > 2.2:
			cl = wall_colour(cls, seed).darkened(0.05)
			c0 = Vector4(M_WALL, W_BLOCK if cls == W_HOUSE else cls, seed, 0.0)
		_box(mb, Vector2(ul[k], ul[k + 1]), ul[k + 2], ul[k + 3], ul[k + 4], ul[k + 5] - 0.05, ul[k + 6], cl, c0)


## Roof material of a pitched roof from its photo colour: orange-red clay
## tile, otherwise composition shingle; large ones standing-seam metal.
static func _pitched_mat(rcol: Color, area: float) -> int:
	if rcol.r > rcol.g * 1.12 and rcol.r > rcol.b * 1.25 and rcol.r > 0.32:
		return M_TILE
	if area > 1500.0:
		return M_METALROOF
	return M_SHINGLE


static func _pitched(env, R: Dictionary, i: int, mb: MB, fp: PackedVector2Array, g: float, lo: float,
		cls: int, wcol: Color, rcol: Color, seed: float, roof_only := false) -> void:
	var eave: float = R["z0"][i]
	var cw := Vector4(M_WALL, cls, seed, 0.0)
	var k4 := i * 4
	var area: float = (env.b_aabb[k4 + 2] - env.b_aabb[k4]) * (env.b_aabb[k4 + 3] - env.b_aabb[k4 + 1])
	var rm := _pitched_mat(rcol, area)
	var cr := Vector4(rm, cls, seed, 0.0)
	if not roof_only:
		_walls(mb, fp, lo, g, eave, Callable(), wcol, cw)
		# eave-level cap under the rectangles (shows only where they do not reach)
		_cap(mb, fp, eave - 0.02, rcol.darkened(0.1), Vector4(M_FLAT, cls, seed, 0.0))
	for p in (R["parts"][i] as Array):
		var ctr: Vector2 = p["c"]
		var r := Vector2(cos(p["ang"]), sin(p["ang"]))
		var a: float = p["a"]
		var b: float = p["b"]
		var ridge: float = p["ridge"]
		if int(p["kind"]) == 0 and a < b:
			# a hip is symmetric: put its ridge along the longer side
			r = Vector2(-r.y, r.x)
			var t := a; a = b; b = t
		var v := Vector2(-r.y, r.x)
		var P := func(s: float, t: float) -> Vector2: return ctr + r * s + v * t
		var rise := ridge - eave
		if int(p["kind"]) == 1:
			# gable: two slopes, two gable-end walls
			for sgn in [-1.0, 1.0]:
				var e1: Vector2 = P.call(-a, sgn * b)
				var e2: Vector2 = P.call(a, sgn * b)
				var r1: Vector2 = P.call(-a, 0.0)
				var r2: Vector2 = P.call(a, 0.0)
				var n3 := _plane_n(G(e1.x, e1.y, eave), G(e2.x, e2.y, eave), G(r2.x, r2.y, ridge))
				var sl := sqrt(b * b + rise * rise)
				mb.quad(G(e1.x, e1.y, eave), G(e2.x, e2.y, eave), G(r2.x, r2.y, ridge), G(r1.x, r1.y, ridge),
					n3, Vector2(-a, 0), Vector2(a, 0), Vector2(a, sl), Vector2(-a, sl), rcol, cr)
			for sgn in [-1.0, 1.0]:
				var q1: Vector2 = P.call(sgn * a, -b)
				var q2: Vector2 = P.call(sgn * a, b)
				var q3: Vector2 = P.call(sgn * a, 0.0)
				var nn := Vector3(r.x * sgn, 0.0, -r.y * sgn)
				mb.tri(G(q1.x, q1.y, eave), G(q2.x, q2.y, eave), G(q3.x, q3.y, ridge), nn,
					Vector2(0, eave - g), Vector2(2.0 * b, eave - g), Vector2(b, ridge - g), wcol, Vector4(M_WALL, cls, seed, 0.0))
		else:
			var ra := maxf(a - b, 0.0)
			var c00: Vector2 = P.call(-a, -b)
			var c10: Vector2 = P.call(a, -b)
			var c11: Vector2 = P.call(a, b)
			var c01: Vector2 = P.call(-a, b)
			var r0: Vector2 = P.call(-ra, 0.0)
			var r1: Vector2 = P.call(ra, 0.0)
			var sl := sqrt(b * b + rise * rise)
			# long slopes (trapezoids)
			var n1 := _plane_n(G(c00.x, c00.y, eave), G(c10.x, c10.y, eave), G(r1.x, r1.y, ridge))
			mb.quad(G(c00.x, c00.y, eave), G(c10.x, c10.y, eave), G(r1.x, r1.y, ridge), G(r0.x, r0.y, ridge),
				n1, Vector2(-a, 0), Vector2(a, 0), Vector2(ra, sl), Vector2(-ra, sl), rcol, cr)
			var n2 := _plane_n(G(c11.x, c11.y, eave), G(c01.x, c01.y, eave), G(r0.x, r0.y, ridge))
			mb.quad(G(c11.x, c11.y, eave), G(c01.x, c01.y, eave), G(r0.x, r0.y, ridge), G(r1.x, r1.y, ridge),
				n2, Vector2(a, 0), Vector2(-a, 0), Vector2(-ra, sl), Vector2(ra, sl), rcol, cr)
			# hip ends (triangles)
			var n3 := _plane_n(G(c10.x, c10.y, eave), G(c11.x, c11.y, eave), G(r1.x, r1.y, ridge))
			mb.tri(G(c10.x, c10.y, eave), G(c11.x, c11.y, eave), G(r1.x, r1.y, ridge), n3,
				Vector2(-b, 0), Vector2(b, 0), Vector2(0, sl), rcol, cr)
			var n4 := _plane_n(G(c01.x, c01.y, eave), G(c00.x, c00.y, eave), G(r0.x, r0.y, ridge))
			mb.tri(G(c01.x, c01.y, eave), G(c00.x, c00.y, eave), G(r0.x, r0.y, ridge), n4,
				Vector2(-b, 0), Vector2(b, 0), Vector2(0, sl), rcol, cr)


## Upward-facing unit normal of the plane through three points.
static func _plane_n(a: Vector3, b: Vector3, c: Vector3) -> Vector3:
	var n := (b - a).cross(c - a).normalized()
	return -n if n.y < 0.0 else n


static func _surface(env, R: Dictionary, i: int, mb: MB, fp: PackedVector2Array, g: float, lo: float,
		cls: int, wcol: Color, rcol: Color, seed: float) -> void:
	var parts: Array = R["parts"][i]
	if parts.is_empty():
		return
	var p: Dictionary = parts[0]
	var o: Vector2 = p["o"]
	var w: int = p["w"]
	var h: int = p["h"]
	var base: float = p["base"]
	var q: PackedByteArray = p["q"]
	# Heights at posts; 255 (outside) takes the nearest valid post.
	var zs := PackedFloat32Array()
	zs.resize(w * h)
	var valid := PackedByteArray()
	valid.resize(w * h)
	var lowest := INF
	for k in w * h:
		if q[k] != 255:
			zs[k] = base + float(q[k]) * 0.1
			valid[k] = 1
			lowest = minf(lowest, zs[k])
	for k in w * h:
		if valid[k] == 0:
			var best := INF
			var bz := lowest
			var x := k % w
			var y := k / w
			for dy in range(-2, 3):
				for dx in range(-2, 3):
					var xx := x + dx
					var yy := y + dy
					if xx < 0 or yy < 0 or xx >= w or yy >= h:
						continue
					var kk := yy * w + xx
					if valid[kk] == 1 and dx * dx + dy * dy < best:
						best = dx * dx + dy * dy
						bz = zs[kk]
			zs[k] = bz
	var zat := func(pt: Vector2) -> float:
		var fx := clampf(pt.x - o.x, 0.0, float(w - 1))
		var fy := clampf(o.y - pt.y, 0.0, float(h - 1))
		var x0 := int(fx)
		var y0 := int(fy)
		var x1 := mini(x0 + 1, w - 1)
		var y1 := mini(y0 + 1, h - 1)
		var tx := fx - x0
		var ty := fy - y0
		return lerpf(lerpf(zs[y0 * w + x0], zs[y0 * w + x1], tx), lerpf(zs[y1 * w + x0], zs[y1 * w + x1], tx), ty)
	var cw := Vector4(M_WALL, cls, seed, 0.0)
	_walls(mb, fp, lo, g, -1e9, zat, wcol, cw, 1.0)
	var k4 := i * 4
	var area: float = (env.b_aabb[k4 + 2] - env.b_aabb[k4]) * (env.b_aabb[k4 + 3] - env.b_aabb[k4 + 1])
	var rm := M_FLAT if area > 1500.0 else _pitched_mat(rcol, area)
	var cr := Vector4(rm, cls, seed, 0.0)
	# Triangulate the footprint's vertices plus the posts inside it.
	var pts := PackedVector2Array(fp)
	for y in h:
		for x in w:
			var pt := Vector2(o.x + x, o.y - y)
			if Geometry2D.is_point_in_polygon(pt, fp) and _edge_dist(pt, fp) > 0.35:
				pts.append(pt)
	var tri := Geometry2D.triangulate_delaunay(pts)
	for k in range(0, tri.size(), 3):
		var a: Vector2 = pts[tri[k]]
		var b: Vector2 = pts[tri[k + 1]]
		var c: Vector2 = pts[tri[k + 2]]
		if not Geometry2D.is_point_in_polygon((a + b + c) / 3.0, fp):
			continue
		var A := G(a.x, a.y, zat.call(a))
		var B := G(b.x, b.y, zat.call(b))
		var C := G(c.x, c.y, zat.call(c))
		mb.tri(A, B, C, _plane_n(A, B, C), a, b, c, rcol, cr)


static func _edge_dist(p: Vector2, ring: PackedVector2Array) -> float:
	var best := INF
	for k in ring.size():
		var q := Geometry2D.get_closest_point_to_segment(p, ring[k], ring[(k + 1) % ring.size()])
		best = minf(best, p.distance_to(q))
	return best


# --------------------------------------------------------------- material

static func material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	if ResourceLoader.exists(DETAIL) or FileAccess.file_exists(DETAIL):
		var img := Image.new()
		if img.load_png_from_buffer(FileAccess.get_file_as_bytes(DETAIL)) == OK:
			img.generate_mipmaps()
			m.set_shader_parameter("detail", ImageTexture.create_from_image(img))
	return m


const SHADER := """
shader_type spatial;
render_mode diffuse_burley, cull_back;
#include "res://shaders/thermal.gdshaderinc"

// RGBA greyscale detail, mean 0.5: shingle, clay tile, stucco, concrete.
uniform sampler2D detail : filter_linear_mipmap_anisotropic, repeat_enable;

varying vec4 v_c0;
varying vec3 v_world;
varying vec3 v_wn;

void vertex() {
	v_c0 = CUSTOM0;
	v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	v_wn = NORMAL;
}

float h11(float n) { return fract(sin(n * 91.345) * 47453.5453); }
float h21(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vnoise(vec2 p) {
	vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
	return mix(mix(h21(i), h21(i + vec2(1, 0)), f.x), mix(h21(i + vec2(0, 1)), h21(i + vec2(1, 1)), f.x), f.y);
}
// 1 inside [a, b] (antialiased with the pixel footprint w)
float band(float x, float a, float b, float w) {
	return clamp((x - a) / w + 0.5, 0.0, 1.0) * clamp((b - x) / w + 0.5, 0.0, 1.0);
}
// thin line at every multiple of `period` (width wd metres)
float lines(float x, float period, float wd, float w) {
	float f = abs(fract(x / period + 0.5) - 0.5) * period;
	return 1.0 - clamp((f - wd * 0.5) / max(w, 1e-4) + 0.5, 0.0, 1.0);
}

void fragment() {
	int kind = int(v_c0.x + 0.5);
	int cls = int(v_c0.y + 0.5);
	float seed = v_c0.z;
	float wl = mod(v_c0.w, 1000.0);
	float wtop = floor(v_c0.w / 1000.0) * 0.25;
	// NAIP is a display stretch, not reflectance: keep the brightest roofs
	// off the clip (white membrane ~0.75)
	vec3 base = min(pow(COLOR.rgb, vec3(2.2)), vec3(0.75));
	vec3 alb = base;
	float rough = 0.9;
	float metal = 0.0;
	float spec = 0.4;
	float glass = 0.0;
	float absorb = 0.6;
	float mass = 0.7;
	float sunc = 18.0;
	float own = 0.5;
	vec2 m = UV;
	float aw = max(fwidth(m.x), fwidth(m.y)) * 1.2 + 1e-4;
	if (kind == 0) {
		// ---------------------------------------------------- facades
		float u = m.x;
		float v = m.y;                       // metres above the ground at the building
		float st = texture(detail, vec2(u, v) / 3.0).b * 2.0;
		float conc = texture(detail, vec2(u, v) / 4.0).a * 2.0;
		float grime = 0.92 + 0.08 * vnoise(vec2(u * 0.3, v * 0.6) + seed * 40.0);
		float floor_h = 3.0; float bay = 3.4; float ww = 1.4; float sill = 0.9; float head = 2.2;
		float skip = 0.0; float first = 0.0;
		vec3 glass_c = vec3(0.06, 0.08, 0.10);
		vec3 wallc = base * mix(1.0, st, 0.35);
		if (cls == 0) {          // single-family house: one or two storeys, few windows
			floor_h = 2.9; bay = 3.8; ww = 1.5; sill = 0.95; head = 2.15; skip = 0.45;
		} else if (cls == 1 || cls == 9) {   // apartments, hotels
			floor_h = cls == 9 ? 3.2 : 3.0; bay = cls == 9 ? 3.8 : 3.3; ww = cls == 9 ? 2.0 : 1.6;
			sill = 0.9; head = 2.3; skip = 0.08;
		} else if (cls == 2) {   // glass curtain wall
			floor_h = 4.0; bay = 1.5; ww = 1.44; sill = 0.0; head = 4.0;
			wallc = base * 0.5;
		} else if (cls == 3) {   // ribbon windows between concrete bands
			floor_h = 4.0; bay = 1.6; ww = 1.55; sill = 1.25; head = 3.55;
			wallc = base * mix(1.0, conc, 0.3);
		} else if (cls == 4) {   // tilt-up concrete
			floor_h = 99.0; bay = 7.6; ww = 0.0;
			wallc = base * mix(1.0, conc, 0.35);
		} else if (cls == 5) {   // shop: storefront glass at street level
			floor_h = 99.0; bay = 6.0; ww = 5.2; sill = 0.45; head = 3.0;
		} else if (cls == 6) {   // parking structure: open floors
			floor_h = 3.1; bay = 9.0; ww = 8.4; sill = 1.05; head = 2.95;
			wallc = base * mix(1.0, conc, 0.4); glass_c = vec3(0.025);
		} else if (cls == 7) {   // concrete block
			floor_h = 99.0; ww = 0.0;
			float course = lines(v, 0.2, 0.012, aw) + lines(u + step(0.5, fract(v / 0.4)) * 0.2, 0.4, 0.012, aw);
			wallc = base * mix(1.0, conc, 0.3) * (1.0 - 0.18 * clamp(course, 0.0, 1.0));
		} else if (cls == 8) {   // corrugated metal
			floor_h = 99.0; ww = 0.0;
			wallc = base * (0.86 + 0.14 * sin(u / 0.2 * 6.2832));
			metal = 0.5; rough = 0.45;
		}
		alb = wallc * grime;
		// wall top trim and plinth
		alb *= 1.0 - 0.25 * band(v, -1.0, 0.25, aw);
		// windows
		float L = max(wl, 0.01);
		float nb = floor(L / bay);
		float margin = (L - nb * bay) * 0.5;
		float uu = u - margin;
		float bi = floor(uu / bay);
		float lu = uu - bi * bay;
		float fi = floor(v / floor_h);
		float lv = v - fi * floor_h;
		float in_wall = step(0.0, uu) * step(uu, nb * bay);
		float rnd = h21(vec2(bi, seed * 97.0));
		float rndf = h21(vec2(bi + fi * 13.1, seed * 31.0));
		float win = 0.0;
		if (ww > 0.0 && wl > 0.5) {
			win = band(lu, (bay - ww) * 0.5, (bay + ww) * 0.5, aw) * band(lv, sill, head, aw) * in_wall;
			win *= step(skip, rnd);
			if (cls == 5) { win *= step(fi, 0.5); }
			if (cls == 0 && fi > 1.5) { win = 0.0; }
			// nothing above the eave / into the parapet
			win *= step(fi * floor_h + head, wtop - 0.35);
		}
		if (cls == 2) {
			// spandrel: opaque dark glass across each floor line, mullions every bay
			float sp = band(lv, -0.1, 0.85, aw);
			float mull = lines(u, bay, 0.07, aw) + lines(v, floor_h, 0.07, aw);
			vec3 gl = mix(vec3(0.05, 0.07, 0.09), base * 0.35, 0.6) * (0.85 + 0.3 * rndf);
			alb = mix(gl, gl * 0.6, sp);
			alb = mix(alb, vec3(0.28, 0.29, 0.30), clamp(mull, 0.0, 1.0));
			glass = 1.0 - clamp(mull, 0.0, 1.0);
		} else if (cls == 4) {
			// panel joints, painted reveal bands, a storefront entry and dock doors
			float joint = lines(u, bay, 0.03, aw);
			alb *= 1.0 - 0.35 * joint;
			alb *= 1.0 - 0.10 * band(v, 4.4, 4.7, aw) - 0.10 * band(v, 7.6, 7.9, aw);
			float pi_ = floor(u / bay);
			float pr = h21(vec2(pi_, seed * 53.0));
			float lp = u - pi_ * bay;
			if (pr < 0.18) {
				float g = band(lp, 0.6, bay - 0.6, aw) * band(v, 0.2, 3.4, aw);
				win = max(win, g);
			} else if (pr > 0.72 && seed > 0.35) {
				float dk = band(lp, 1.6, 5.8, aw) * band(v, 0.2, 4.4, aw);
				alb = mix(alb, vec3(0.30, 0.31, 0.32) * (0.9 + 0.1 * lines(v, 0.15, 0.02, aw)), dk);
			}
		}
		if (cls != 2) {
			vec3 gl = glass_c * (0.7 + 0.6 * rndf);
			// frames
			float fr = win * (1.0 - band(lu, (bay - ww) * 0.5 + 0.06, (bay + ww) * 0.5 - 0.06, aw)
				* band(lv, sill + 0.06, head - 0.06, aw));
			alb = mix(alb, gl, win);
			alb = mix(alb, cls == 0 ? vec3(0.85) : vec3(0.25), fr);
			glass = win * (1.0 - fr) * (cls == 6 ? 0.0 : 1.0);
		}
		rough = mix(rough, 0.08, glass);
		metal = mix(metal, 0.25, glass);
		spec = mix(spec, 0.6, glass);
		absorb = 0.55; mass = 0.75; sunc = 16.0;
	} else if (kind == 1 || kind == 2 || kind == 6) {
		// ---------------------------------------------------- pitched roofs
		// UV: x along the ridge, y up the slope (metres)
		float d;
		if (kind == 1) {
			d = texture(detail, m / vec2(2.4, 2.4)).r * 2.0;
			alb = base * mix(1.0, d, 0.55) * (0.9 + 0.2 * vnoise(m * 0.25 + seed * 9.0));
			rough = 0.93;
		} else if (kind == 2) {
			d = texture(detail, m / vec2(2.0, 2.0)).g * 2.0;
			alb = base * mix(1.0, d, 0.6);
			rough = 0.8;
		} else {
			alb = base * (0.9 + 0.1 * (1.0 - lines(m.x, 0.45, 0.04, aw)));
			metal = 0.35; rough = 0.5;
		}
		absorb = 0.85; mass = 0.35; sunc = 28.0;
	} else if (kind == 3) {
		// ---------------------------------------------------- flat roofs (membrane, gravel, panels)
		vec2 w = v_world.xz;
		float d = texture(detail, w / 6.0).a * 2.0;
		float stain = vnoise(w * 0.08 + seed * 13.0);
		alb = base * mix(1.0, d, 0.25) * (0.88 + 0.22 * stain);
		rough = 0.9;
		absorb = clamp(1.0 - dot(base, vec3(0.33)) * 1.2, 0.2, 0.95); mass = 0.4; sunc = 30.0;
	} else if (kind == 4) {
		// ---------------------------------------------------- rooftop units
		float side = 1.0 - step(0.5, abs(v_wn.y));
		float louv = lines(m.y, 0.12, 0.04, aw) * side;
		alb = base * (1.0 - 0.25 * louv);
		metal = 0.4; rough = 0.45;
		absorb = 0.5; mass = 0.3; sunc = 20.0; own = 6.0 * seed;
	} else {
		// ---------------------------------------------------- coping, trims
		alb = base * (texture(detail, v_world.xz / 3.0).a * 0.5 + 0.5);
		rough = 0.85;
	}
	if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
		vec2 th = th_surface(absorb, sunc, mass, own);
		float sky = th_enc(mix(thermal_air_c, thermal_sky_c, 0.35));
		th.y = mix(th.y, sky, glass * 0.6);
		TH_OUT(th);
	} else {
		ALBEDO = alb;
		ROUGHNESS = rough;
		METALLIC = metal;
		SPECULAR = spec;
	}
}
"""
