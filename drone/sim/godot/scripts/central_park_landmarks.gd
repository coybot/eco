## Central Park's water and built things: the lakes, the perimeter wall, the
## drives' lamp posts, the Reservoir fence and the landmarks that read from
## the air. Child of central_park.gd (which owns the relief and the queries).
##
## Everything stands on park.ground_at(), so on the real relief. Positions
## are OSM's (central_park.json) or, where OSM had nothing, lat/lon read off
## the NAIP photo. Scenery only: none of it is in env.props.
extends Node3D

var park: Node3D       # central_park.gd
var env: Node3D
var doc: Dictionary
var off := Vector2.ZERO
var _mats := {}
var _night := -1.0
var _hide: Array[Rect2] = []   # city boxes replaced by a model here
var _counts := {}

## Materials, all one shader (LM_SHADER) so each draws natively in the
## thermal band: albedo (sRGB), roughness, metallic, grain (noise), mode,
## and heat [absorb, sun_c, mass, own_c] (shaders/thermal.gdshaderinc).
const MATS := {
	"stone": [Color(0.55, 0.52, 0.47), 0.9, 0.0, 0.25, 0, [0.5, 18.0, 0.8, 0.0]],
	"sandstone": [Color(0.62, 0.52, 0.40), 0.85, 0.0, 0.2, 0, [0.55, 18.0, 0.8, 0.0]],
	"schist": [Color(0.44, 0.43, 0.41), 0.9, 0.0, 0.45, 0, [0.65, 22.0, 0.85, 0.0]],
	"granite": [Color(0.56, 0.46, 0.41), 0.7, 0.0, 0.3, 0, [0.6, 20.0, 0.9, 0.0]],
	"brick": [Color(0.55, 0.31, 0.25), 0.9, 0.0, 0.3, 0, [0.6, 18.0, 0.7, 0.5]],
	"slate": [Color(0.27, 0.28, 0.30), 0.7, 0.0, 0.15, 0, [0.8, 24.0, 0.4, 0.0]],
	"copper": [Color(0.42, 0.60, 0.53), 0.6, 0.3, 0.3, 0, [0.6, 18.0, 0.2, 0.0]],
	"cream": [Color(0.86, 0.83, 0.72), 0.55, 0.2, 0.06, 0, [0.35, 16.0, 0.2, 0.0]],
	"cream_lattice": [Color(0.86, 0.83, 0.72), 0.55, 0.2, 0.0, 1, [0.35, 16.0, 0.2, 0.0]],
	"iron": [Color(0.10, 0.11, 0.10), 0.5, 0.6, 0.0, 0, [0.6, 20.0, 0.2, 0.0]],
	"iron_fence": [Color(0.09, 0.10, 0.09), 0.5, 0.6, 0.0, 1, [0.6, 20.0, 0.2, 0.0]],
	"bronze": [Color(0.34, 0.46, 0.40), 0.5, 0.6, 0.25, 0, [0.6, 18.0, 0.3, 0.0]],
	"ice": [Color(0.86, 0.90, 0.93), 0.12, 0.0, 0.08, 2, [0.1, 1.0, 0.95, -22.0]],
	"boards": [Color(0.84, 0.84, 0.82), 0.4, 0.0, 0.0, 0, [0.3, 12.0, 0.2, 0.0]],
	"wood": [Color(0.42, 0.31, 0.21), 0.85, 0.0, 0.25, 0, [0.5, 12.0, 0.2, 0.0]],
	"lamp": [Color(0.95, 0.92, 0.82), 0.3, 0.0, 0.0, 3, [0.3, 6.0, 0.1, 0.0]],
}


func _ready() -> void:
	name = "landmarks"
	env = park.env
	doc = park.get("doc")
	off = env.call("data_offset")
	_lakes()
	_wall()
	# Lamp posts and benches along the paths and the Mall are the nature
	# layer's (nature.gd, from OSM's mapped furniture); _drive_lamps and
	# _mall_benches below are the fallback when it is absent.
	if env.get_node_or_null("nature") == null and not FileAccess.file_exists("res://assets/manhattan/trees.json"):
		_drive_lamps()
		_mall_benches()
	_reservoir_fence()
	_bethesda()
	_bow_bridge()
	_gapstow()
	_castle()
	_wollman()
	_bandshell()
	_carousel()
	_dairy()
	_needle()
	_flush_pool()
	_fix_buildings.call_deferred()
	print("[central_park] landmarks: %s" % str(_counts))


func _process(_dt: float) -> void:
	var nt := float(env.call("night_amount")) if env.has_method("night_amount") else 0.0
	if absf(nt - _night) < 0.02:
		return
	_night = nt
	_material().set_shader_parameter("glow_e", 5.0 * nt)


## The one landmark material: every part carries its material in its vertex
## colour (albedo, and the MATS index in alpha), and the shader looks the
## rest up in two uniform arrays. All of the park's built things are then a
## couple of draws (plus their shadow passes), instead of one per material
## per landmark: 15 landmarks x 3-4 materials x 4 shadow cascades was ~60
## draw calls.
var _lm_mat: ShaderMaterial = null


func _material() -> ShaderMaterial:
	if _lm_mat != null:
		return _lm_mat
	var sh := Shader.new()
	sh.code = LM_SHADER
	_lm_mat = ShaderMaterial.new()
	_lm_mat.shader = sh
	var pa := PackedVector4Array()
	var ha := PackedVector4Array()
	for k: String in MATS:
		var spec: Array = MATS[k]
		pa.append(Vector4(float(spec[1]), float(spec[2]), float(spec[3]), float(spec[4])))
		var ht: Array = spec[5]
		ha.append(Vector4(ht[0], ht[1], ht[2], ht[3]))
	while pa.size() < 24:
		pa.append(Vector4(0.9, 0, 0, 0))
		ha.append(Vector4(0.55, 18, 0.4, 0))
	_lm_mat.set_shader_parameter("mat_p", pa)
	_lm_mat.set_shader_parameter("mat_h", ha)
	return _lm_mat


## Dataset-frame point from the json -> sim.
func _p(a: Array) -> Vector2:
	return Vector2(float(a[0]), float(a[1])) - off


func _poly(a: Array) -> PackedVector2Array:
	var out := PackedVector2Array()
	for q: Array in a:
		out.append(_p(q))
	return out


func _lm(key: String) -> Dictionary:
	return doc.get("landmarks", {}).get(key, {})


## Godot position of sim (e, n) at height y.
static func _g(p: Vector2, y: float) -> Vector3:
	return Vector3(p.x, y, -p.y)


# ------------------------------------------------------------------ parts
## Accumulates triangles per material, committed as ONE MeshInstance3D with a
## surface per material: a landmark is a handful of draw calls, not a node per
## stone.
class Parts:
	var v := PackedVector3Array()
	var nrm := PackedVector3Array()
	var uv := PackedVector2Array()
	var col := PackedColorArray()

	static func colour(k: String) -> Color:
		var c: Color = (MATS[k] as Array)[0]
		var lin := c.srgb_to_linear()
		lin.a = float(MATS.keys().find(k)) / 255.0
		return lin

	func tri(k: String, a: Vector3, b: Vector3, c: Vector3, ua := Vector2.ZERO, ub := Vector2.ZERO, uc := Vector2.ZERO) -> void:
		# Godot's front faces wind clockwise; the normal follows that.
		var n := (c - a).cross(b - a)
		if n.length_squared() < 1e-12:
			return
		n = n.normalized()
		var cl := colour(k)
		v.append_array([a, b, c])
		nrm.append_array([n, n, n])
		uv.append_array([ua, ub, uc])
		col.append_array([cl, cl, cl])

	func append(o: Parts) -> void:
		v.append_array(o.v)
		nrm.append_array(o.nrm)
		uv.append_array(o.uv)
		col.append_array(o.col)

	func mesh(mat: Material) -> ArrayMesh:
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = v
		arr[Mesh.ARRAY_NORMAL] = nrm
		arr[Mesh.ARRAY_TEX_UV] = uv
		arr[Mesh.ARRAY_COLOR] = col
		var m := ArrayMesh.new()
		m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
		m.surface_set_material(0, mat)
		return m

	## Quad a-b-c-d counter-clockwise seen from the front.
	func quad(k: String, a: Vector3, b: Vector3, c: Vector3, d: Vector3, uv := Rect2(0, 0, 1, 1)) -> void:
		var u0 := uv.position
		var u1 := uv.end
		tri(k, a, c, b, Vector2(u0.x, u1.y), Vector2(u1.x, u0.y), Vector2(u1.x, u1.y))
		tri(k, a, d, c, Vector2(u0.x, u1.y), Vector2(u0.x, u0.y), Vector2(u1.x, u0.y))

	## Box of `size` centred on xf.origin, oriented by xf.basis.
	func box(k: String, xf: Transform3D, size: Vector3) -> void:
		var h := size * 0.5
		var c := []
		for i in 8:
			c.append(xf * Vector3(h.x if i & 1 else -h.x, h.y if i & 2 else -h.y, h.z if i & 4 else -h.z))
		quad(k, c[4], c[5], c[7], c[6])   # +z
		quad(k, c[1], c[0], c[2], c[3])   # -z
		quad(k, c[5], c[1], c[3], c[7])   # +x
		quad(k, c[0], c[4], c[6], c[2])   # -x
		quad(k, c[6], c[7], c[3], c[2])   # +y
		quad(k, c[0], c[1], c[5], c[4])   # -y

	## Frustum of a cone, base at `at`, axis +Y; `seg` sides (4 = square).
	func cyl(k: String, at: Vector3, r0: float, r1: float, h: float, seg := 12, cap := true, rot := 0.0) -> void:
		for i in seg:
			var a0 := rot + TAU * i / seg
			var a1 := rot + TAU * (i + 1) / seg
			var d0 := Vector3(cos(a0), 0, sin(a0))
			var d1 := Vector3(cos(a1), 0, sin(a1))
			quad(k, at + d1 * r0, at + d0 * r0, at + d0 * r1 + Vector3(0, h, 0), at + d1 * r1 + Vector3(0, h, 0))
			if cap and r1 > 0.0:
				tri(k, at + Vector3(0, h, 0), at + d0 * r1 + Vector3(0, h, 0), at + d1 * r1 + Vector3(0, h, 0))

	## A vertical strip (wall, fence) along a polyline of Godot points, from
	## each point's y up `h`, both faces. UV.x = metres along, UV.y = 0 top .. 1 foot.
	func strip(k: String, pts: Array, h: float, both := true) -> void:
		var along := 0.0
		for i in pts.size() - 1:
			var a: Vector3 = pts[i]
			var b: Vector3 = pts[i + 1]
			var l := Vector2(b.x - a.x, b.z - a.z).length()
			var uv := Rect2(along, 0.0, l, 1.0)
			quad(k, a, b, b + Vector3(0, h, 0), a + Vector3(0, h, 0), uv)
			if both:
				quad(k, b, a, a + Vector3(0, h, 0), b + Vector3(0, h, 0), Rect2(along + l, 0.0, -l, 1.0))
			along += l


func _new_parts() -> Parts:
	return Parts.new()


## Landmarks pool into two meshes (shadow-casting and not), drawn at the end
## of _ready. `vis_end` is kept per landmark for the record but the pooled
## mesh fades at 3 km; at that range all of it is a few pixels anyway.
var _pool := {true: Parts.new(), false: Parts.new()}


func _commit(pp: Parts, nm: String, _vis_end := 3000.0, shadows := true) -> void:
	if pp.v.is_empty():
		return
	(_pool[shadows] as Parts).append(pp)
	_counts[nm] = pp.v.size() / 3


func _flush_pool() -> void:
	for sh: bool in _pool:
		var pp: Parts = _pool[sh]
		if pp.v.is_empty():
			continue
		var mi := MeshInstance3D.new()
		mi.name = "park_built" if sh else "park_built_low"
		mi.mesh = pp.mesh(_material())
		mi.visibility_range_end = 3500.0
		mi.visibility_range_end_margin = 300.0
		mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
		if not sh:
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)


## Transform at sim p, height y, local +X along sim direction `dir`, +Z
## across it (to its right), +Y up.
static func _xf(p: Vector2, y: float, dir: Vector2) -> Transform3D:
	return Transform3D(Basis(Vector3.UP, atan2(dir.y, dir.x)), _g(p, y))


## Unit vector along the longest edge of a polygon (its long axis).
static func _axis(poly: PackedVector2Array) -> Vector2:
	var best := Vector2.RIGHT
	var bl := 0.0
	for i in poly.size():
		var d := poly[(i + 1) % poly.size()] - poly[i]
		if d.length() > bl:
			bl = d.length()
			best = d.normalized()
	return best


static func _centroid(poly: PackedVector2Array) -> Vector2:
	var c := Vector2.ZERO
	var n := poly.size()
	if n > 2 and poly[0].is_equal_approx(poly[n - 1]):
		n -= 1
	for i in n:
		c += poly[i]
	return c / maxf(n, 1)


## Lowest terrain under a polygon (vertices and centroid): where a building's
## foot goes so no corner floats.
func _foot(poly: PackedVector2Array) -> float:
	var lo := _terrain(_centroid(poly))
	for q in poly:
		lo = minf(lo, _terrain(q))
	return lo


## Extrude a footprint polygon into walls from y0 to y1 (+ flat top).
static func _prism(pp: Parts, k: String, poly: PackedVector2Array, y0: float, y1: float, top_k := "") -> void:
	var n := poly.size()
	if n > 2 and poly[0].is_equal_approx(poly[n - 1]):
		n -= 1
	var ccw := _area(poly) > 0.0
	for i in n:
		var a := poly[i]
		var b := poly[(i + 1) % n]
		if not ccw:
			var t := a
			a = b
			b = t
		# Sim (e, n) is counter-clockwise from above; in Godot (x, -z) the
		# outward face is a->b seen from outside.
		pp.quad(k, _g(a, y0), _g(b, y0), _g(b, y1), _g(a, y1), Rect2(0, 0, (b - a).length(), y1 - y0))
	if top_k != "":
		var pts := PackedVector2Array()
		for i in n:
			pts.append(poly[i])
		var tri := Geometry2D.triangulate_polygon(pts)
		for i in range(0, tri.size(), 3):
			var a := _g(pts[tri[i]], y1)
			var b := _g(pts[tri[i + 1]], y1)
			var c := _g(pts[tri[i + 2]], y1)
			if (b - a).cross(c - a).y < 0:
				pp.tri(top_k, a, b, c)
			else:
				pp.tri(top_k, a, c, b)


## Crenellations along a footprint's edges at height y.
static func _merlons(pp: Parts, k: String, poly: PackedVector2Array, y: float, inset: float) -> void:
	var c := _centroid(poly)
	var n := poly.size()
	for i in n:
		var a := poly[i]
		var b := poly[(i + 1) % n]
		var l := (b - a).length()
		if l < 0.5:
			continue
		var d := (b - a) / l
		var m := int(l / 1.3)
		for j in m:
			var p := a + d * (0.65 + j * 1.3)
			p += (c - p).normalized() * inset
			var xf := _xf(p, y + 0.35, d)
			pp.box(k, xf, Vector3(0.7, 0.7, 0.45))


func _at(p: Vector2, lift: float = 0.0) -> Vector3:
	return Vector3(p.x, float(park.call("ground_at", p.x, p.y)) + lift, -p.y)


func _terrain(p: Vector2) -> float:
	return float(park.call("terrain_at", p.x, p.y))


# ------------------------------------------------------------------ lakes
func _lakes() -> void:
	var sh := Shader.new()
	sh.code = LAKE_SHADER
	var ftex: ImageTexture = park.call("fields_tex")
	var r: Rect2 = park.get("rect")
	# Two meshes: the Reservoir, and every other pond together (one draw).
	var groups := {}
	for l: Dictionary in park.get("lakes"):
		var polys: Array = l["polys"]
		var no := int(l["n_outer"])
		var gk := "reservoir" if str(l["name"]) == "reservoir" else "ponds"
		if not groups.has(gk):
			var st0 := SurfaceTool.new()
			st0.begin(Mesh.PRIMITIVE_TRIANGLES)
			groups[gk] = st0
		var st: SurfaceTool = groups[gk]
		var y := float(l["level"])
		for k in no:
			# A metre and a half past the mapped shore: the bank (terrain)
			# rises through the water there and draws the real shoreline.
			var grown := Geometry2D.offset_polygon(polys[k], 1.5)
			# offset_polygon returns the grown outline plus any holes it
			# opened; the outline is the largest. (Its winding follows the
			# input's, which OSM does not fix, so it cannot be told by that.)
			var best := PackedVector2Array()
			var best_a := 0.0
			for cand: PackedVector2Array in grown:
				var a := absf(_area(cand))
				if a > best_a:
					best_a = a
					best = cand
			for g: PackedVector2Array in [best]:
				if g.size() < 3:
					continue
				var tri := Geometry2D.triangulate_polygon(g)
				if tri.is_empty():
					# Ear clipping gives up on a ring that touches itself
					# (OSM's Reservoir does, at its gatehouse): Delaunay of
					# its points, keeping triangles whose centre is inside.
					var dl := Geometry2D.triangulate_delaunay(g)
					for i in range(0, dl.size(), 3):
						var cc := (g[dl[i]] + g[dl[i + 1]] + g[dl[i + 2]]) / 3.0
						if Geometry2D.is_point_in_polygon(cc, g):
							tri.append_array([dl[i], dl[i + 1], dl[i + 2]])
				for i in range(0, tri.size(), 3):
					var a := Vector3(g[tri[i]].x, y, -g[tri[i]].y)
					var b := Vector3(g[tri[i + 1]].x, y, -g[tri[i + 1]].y)
					var c := Vector3(g[tri[i + 2]].x, y, -g[tri[i + 2]].y)
					# Clockwise from above is Godot's front face.
					if (b - a).cross(c - a).y > 0.0:
						var t := b
						b = c
						c = t
					for v in [a, b, c]:
						st.set_normal(Vector3.UP)
						st.add_vertex(v)
	for gk: String in groups:
		var mat := ShaderMaterial.new()
		mat.shader = sh
		mat.set_shader_parameter("fields", ftex)
		mat.set_shader_parameter("rect", Vector4(r.position.x, r.position.y, r.size.x, r.size.y))
		# The Reservoir is deep, clear and open to the sky: bluer. The others
		# are shallow, fed, and green-brown with algae and silt.
		var big := gk == "reservoir"
		mat.set_shader_parameter("deep", Color(0.04, 0.085, 0.10) if big else Color(0.055, 0.075, 0.045))
		mat.set_shader_parameter("shallow", Color(0.10, 0.14, 0.12) if big else Color(0.13, 0.15, 0.07))
		mat.set_shader_parameter("weed", 0.15 if big else 1.0)
		var mi := MeshInstance3D.new()
		mi.name = "water_%s" % gk
		mi.mesh = (groups[gk] as SurfaceTool).commit()
		mi.material_override = mat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)


## The city's footprints include the park's own buildings (the Arsenal, the
## Dairy, the boathouse, the castle, the Delacorte). They stand on y = 0 as
## every building does, which on the real relief floats the boathouse 8 m
## over its lake shore and buries the castle in Vista Rock. Re-seat each of
## those boxes on the ground under it, same height above its own ground.
## Only the drawing moves; the analytic occlusion keeps its boxes on 0.
func _fix_buildings() -> void:
	var seated: Array = []    # [Rect2 footprint (sim), delta y or NAN when hidden]
	var moved := 0
	for c in env.find_children("city", "MultiMeshInstance3D", true, false):
		var mmi := c as MultiMeshInstance3D
		var mm := mmi.multimesh
		if mm == null or mm.transform_format != MultiMesh.TRANSFORM_3D:
			continue
		var g := mmi.global_transform
		for i in mm.instance_count:
			var t := mm.get_instance_transform(i)
			var o := g * t.origin
			if not bool(park.call("covers", o.x, -o.z)):
				continue
			var hx := t.basis.x.length() * 0.5
			var hz := t.basis.z.length() * 0.5
			var fp := Rect2(Vector2(o.x - hx, -o.z - hz), Vector2(hx * 2.0, hz * 2.0))
			var hidden := false
			for hr in _hide:
				if hr.has_point(Vector2(o.x, -o.z)):
					hidden = true
			if hidden:
				# Modelled here instead; zero scale drops it from the draw.
				mm.set_instance_transform(i, Transform3D(Basis.IDENTITY.scaled(Vector3.ZERO), t.origin))
				seated.append([fp, NAN])
				moved += 1
				continue
			# Lowest ground under the footprint (corners and centre), so no
			# corner hangs in the air on a slope.
			var lo := INF
			for d: Vector2 in [Vector2.ZERO, Vector2(hx, hz), Vector2(-hx, hz), Vector2(hx, -hz), Vector2(-hx, -hz)]:
				lo = minf(lo, _terrain(Vector2(o.x + d.x, -o.z + d.y)))
			if absf(lo) < 0.05:
				continue
			t.origin.y += lo
			mm.set_instance_transform(i, t)
			seated.append([fp, lo])
			moved += 1
	# The same buildings' rooftop kit, parapets and crowns (the building
	# layer's MultiMeshes) follow their building. Nothing else is touched:
	# trees, lamps and benches already stand on env.ground_height().
	var kit := 0
	for root_name in ["manhattan_buildings", "parapets", "rooftops"]:
		for root in env.find_children(root_name, "Node", true, false):
			for c in root.find_children("*", "MultiMeshInstance3D", true, false):
				if str(c.name) == "city":
					continue
				kit += _follow(c as MultiMeshInstance3D, seated)
	for c in env.find_children("city_crowns", "MultiMeshInstance3D", true, false):
		kit += _follow(c as MultiMeshInstance3D, seated)
	if moved > 0:
		print("[central_park] re-seated %d park buildings (+%d rooftop parts) on the relief" % [moved, kit])


func _follow(mmi: MultiMeshInstance3D, seated: Array) -> int:
	var mm := mmi.multimesh
	if mm == null or mm.transform_format != MultiMesh.TRANSFORM_3D or seated.is_empty():
		return 0
	var g := mmi.global_transform
	var n := 0
	for i in mm.instance_count:
		var t := mm.get_instance_transform(i)
		var o := g * t.origin
		var p := Vector2(o.x, -o.z)
		if not rect_has(park.get("rect"), p):
			continue
		for sd: Array in seated:
			if (sd[0] as Rect2).grow(0.5).has_point(p):
				if is_nan(float(sd[1])):
					t = Transform3D(Basis.IDENTITY.scaled(Vector3.ZERO), t.origin)
				else:
					t.origin.y += float(sd[1])
				mm.set_instance_transform(i, t)
				n += 1
				break
	return n


static func rect_has(r: Rect2, p: Vector2) -> bool:
	return r.has_point(p)




# ------------------------------------------------------------------ the wall
## The perimeter wall: a low stone wall with a coping, on the park side of
## the outline, open wherever a path, drive or transverse crosses it.
func _wall() -> void:
	var outline := _poly(doc.get("outline", []))
	if outline.size() < 4:
		return
	var gates: Array = []
	for g: Array in doc.get("gates", []):
		gates.append([_p(g), float(g[2]) * 0.5 + 1.0])
	var pp := _new_parts()
	var c := _centroid(outline)
	var run: Array = []
	var nseg := 0
	for i in outline.size() - 1:
		var a := outline[i]
		var b := outline[i + 1]
		var l := (b - a).length()
		if l < 0.01:
			continue
		var d := (b - a) / l
		var inward := Vector2(-d.y, d.x)
		if inward.dot(c - a) < 0.0:
			inward = -inward
		var steps := maxi(1, int(ceil(l / 2.0)))
		for j in steps + 1:
			var q := a + d * (l * j / steps) + inward * 0.45
			var open := false
			for g: Array in gates:
				if q.distance_to(g[0]) < float(g[1]):
					open = true
					break
			if open:
				nseg += _wall_run(pp, run)
				run = []
			else:
				run.append(q)
	nseg += _wall_run(pp, run)
	_commit(pp, "wall", 1800.0, false)
	_counts["wall_runs"] = nseg


func _wall_run(pp: Parts, run: Array) -> int:
	if run.size() < 2:
		return 0
	# One box per straight stretch: the outline is mostly long straight
	# avenues, and a box per 2 m sample was 10k boxes.
	var keep: Array = [run[0]]
	for i in range(1, run.size() - 1):
		var d0: Vector2 = (run[i] - keep[keep.size() - 1]).normalized()
		var d1: Vector2 = (run[i + 1] - run[i]).normalized()
		if d0.dot(d1) < 0.9995 or (run[i] - keep[keep.size() - 1]).length() > 120.0:
			keep.append(run[i])
	keep.append(run[run.size() - 1])
	run = keep
	for i in run.size() - 1:
		var a: Vector2 = run[i]
		var b: Vector2 = run[i + 1]
		var l := (b - a).length()
		if l < 0.05:
			continue
		var m := (a + b) * 0.5
		var y := _terrain(m)
		pp.box("stone", _xf(m, y + 0.35, (b - a) / l), Vector3(l + 0.02, 1.1, 0.55))
		pp.box("sandstone", _xf(m, y + 0.95, (b - a) / l), Vector3(l + 0.02, 0.14, 0.72))
	return 1


# ------------------------------------------------------------------ lamps
## The drives' lamp posts: the park's cast-iron post with a glass globe,
## ~3.8 m, every 30 m alternating sides. One MultiMesh per part per 500 m
## band; the globes glow at night (night_amount).
func _drive_lamps() -> void:
	var pts: Array[Transform3D] = []
	var k := 0
	for line: Array in doc.get("drives", []):
		var poly := _poly(line)
		var acc := 0.0
		for i in poly.size() - 1:
			var a := poly[i]
			var b := poly[i + 1]
			var l := (b - a).length()
			if l < 0.01:
				continue
			var d := (b - a) / l
			var t := 30.0 - acc
			while t < l:
				var side := 1.0 if k % 2 == 0 else -1.0
				var q := a + d * t + Vector2(-d.y, d.x) * side * 5.6
				k += 1
				if bool(park.call("covers", q.x, q.y)) and not bool(park.call("water_at", q.x, q.y)):
					pts.append(Transform3D(Basis.IDENTITY, _g(q, _terrain(q))))
				t += 30.0
			acc = fmod(acc + l, 30.0)
	if pts.is_empty():
		return
	var post := _new_parts()
	post.cyl("iron", Vector3.ZERO, 0.17, 0.12, 0.7, 8)
	post.cyl("iron", Vector3(0, 0.7, 0), 0.075, 0.055, 2.9, 6)
	post.cyl("iron", Vector3(0, 3.6, 0), 0.12, 0.09, 0.12, 6)
	var head := _new_parts()
	head.cyl("lamp", Vector3(0, 3.7, 0), 0.12, 0.24, 0.22, 8, false)
	head.cyl("lamp", Vector3(0, 3.92, 0), 0.24, 0.12, 0.25, 8)
	head.cyl("iron", Vector3(0, 4.17, 0), 0.13, 0.03, 0.15, 6)
	post.append(head)
	_multi("park_lamps", _parts_mesh(post), pts, 900.0, false)
	_counts["lamps"] = pts.size()


func _parts_mesh(pp: Parts) -> ArrayMesh:
	return pp.mesh(_material())


## Instances binned into 500 m cells along north, each its own MultiMesh so
## they cull and range-fade as groups.
func _multi(nm: String, mesh: Mesh, xfs: Array[Transform3D], vis_end: float, shadows: bool) -> void:
	var bins := {}
	for xf in xfs:
		var key := int(floor(-xf.origin.z / 1000.0))
		if not bins.has(key):
			bins[key] = []
		bins[key].append(xf)
	for key: int in bins:
		var arr: Array = bins[key]
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = mesh
		mm.instance_count = arr.size()
		for i in arr.size():
			mm.set_instance_transform(i, arr[i])
		var mi := MultiMeshInstance3D.new()
		mi.name = "%s_%d" % [nm, key]
		mi.multimesh = mm
		mi.visibility_range_end = vis_end
		mi.visibility_range_end_margin = vis_end * 0.15
		mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
		if not shadows:
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)


# ------------------------------------------------------------------ the Mall
## The Mall (Literary Walk): park benches lining both sides of its 400 m from
## 66th St to the Bandshell, facing the walk, with a gap at each crossing.
func _mall_benches() -> void:
	var mall := _poly(_lm("the_mall").get("poly", []))
	var fc: Vector2 = _p(_lm("bethesda_fountain").get("en", [0, 0]))
	if mall.size() < 4:
		return
	var u := _axis(mall)
	if u.y < 0.0:
		u = -u
	var c0 := _centroid(mall)
	# South end of the Mall: the polygon's southernmost point projected on
	# the axis; the north end is the Terrace, 70 m short of the fountain.
	var s0 := INF
	for q in mall:
		s0 = minf(s0, (q - c0).dot(u))
	var start := c0 + u * s0
	var length := (fc - start).dot(u) - 75.0
	var v := Vector2(u.y, -u.x)
	var xfs: Array[Transform3D] = []
	var t := 4.0
	while t < length:
		if fmod(t, 48.0) > 6.0:
			for side: float in [-1.0, 1.0]:
				var q := start + u * t + v * side * 6.4
				var face := -v * side
				xfs.append(_xf(q, _terrain(q), Vector2(face.y, -face.x)))
		t += 2.6
	var b := _new_parts()
	b.box("wood", Transform3D(Basis.IDENTITY, Vector3(0, 0.45, 0)), Vector3(1.9, 0.06, 0.45))
	b.box("wood", Transform3D(Basis(Vector3.RIGHT, -0.25), Vector3(0, 0.78, 0.22)), Vector3(1.9, 0.38, 0.05))
	for x: float in [-0.85, 0.85]:
		b.box("iron", Transform3D(Basis.IDENTITY, Vector3(x, 0.22, 0.0)), Vector3(0.06, 0.45, 0.5))
	_multi("park_benches", _parts_mesh(b), xfs, 350.0, false)
	_counts["benches"] = xfs.size()


# ------------------------------------------------------------------ Reservoir
## The Reservoir's iron fence, between the water's stone embankment and the
## running track.
func _reservoir_fence() -> void:
	for l: Dictionary in park.get("lakes"):
		if str(l["name"]) != "reservoir":
			continue
		var outer: PackedVector2Array = l["polys"][0]
		var grown := Geometry2D.offset_polygon(outer, 2.5)
		var ring := PackedVector2Array()
		for g: PackedVector2Array in grown:
			if g.size() > ring.size():
				ring = g
		if ring.size() < 3:
			return
		var pts: Array = []
		for i in ring.size() + 1:
			var q := ring[i % ring.size()]
			pts.append(_g(q, maxf(_terrain(q), float(l["level"])) - 0.05))
		var pp := _new_parts()
		pp.strip("iron_fence", pts, 1.35)
		_commit(pp, "reservoir_fence", 1200.0, false)
		return


# ------------------------------------------------------------------ Bethesda
## Bethesda Terrace and Fountain: the lower plaza on the Lake with the Angel
## of the Waters over its basin, the arcade wall up to Terrace Drive with the
## grand stairs either side, the balustrade along the water. Laid out on the
## Mall's axis, which runs straight through the fountain.
func _bethesda() -> void:
	var fe: Array = _lm("bethesda_fountain").get("en", [])
	if fe.is_empty():
		return
	var f := _p(fe)
	var mall := _poly(_lm("the_mall").get("poly", []))
	var u := _axis(mall) if mall.size() > 3 else Vector2.UP
	if u.y < 0.0:
		u = -u
	var v := Vector2(u.y, -u.x)
	var plaza := _terrain(f + v * 16.0)
	_clear(Rect2(f - Vector2(30, 30), Vector2(60, 52)))
	var pp := _new_parts()
	# Basin rim, 14 m radius.
	var r := 14.0
	for i in 40:
		var a0 := TAU * i / 40.0
		var a1 := TAU * (i + 1) / 40.0
		var q0 := f + Vector2(cos(a0), sin(a0)) * r
		var q1 := f + Vector2(cos(a1), sin(a1)) * r
		pp.box("sandstone", _xf((q0 + q1) * 0.5, plaza + 0.2, (q1 - q0).normalized()), Vector3((q1 - q0).length() + 0.05, 0.75, 0.9))
	# Pedestal, bowls, the angel.
	var at := _g(f, plaza - 0.4)
	pp.cyl("sandstone", at, 3.4, 3.0, 1.4, 16)
	pp.cyl("bronze", at + Vector3(0, 1.4, 0), 1.0, 0.9, 1.6, 12)
	pp.cyl("bronze", at + Vector3(0, 3.0, 0), 0.8, 3.0, 0.55, 16)
	pp.cyl("bronze", at + Vector3(0, 3.55, 0), 0.5, 0.45, 1.3, 10)
	pp.cyl("bronze", at + Vector3(0, 4.85, 0), 0.4, 1.7, 0.4, 12)
	pp.cyl("bronze", at + Vector3(0, 5.25, 0), 0.4, 0.35, 0.7, 8)
	pp.cyl("bronze", at + Vector3(0, 5.95, 0), 0.42, 0.2, 1.7, 8)     # robed figure
	pp.cyl("bronze", at + Vector3(0, 7.65, 0), 0.16, 0.14, 0.32, 8)   # head
	var face := _g(u, 0.0)
	var side := _g(v, 0.0)
	for sgn: float in [-1.0, 1.0]:
		var wb := Basis.looking_at(-face, Vector3.UP).rotated(face.normalized(), sgn * 0.55)
		pp.box("bronze", Transform3D(wb, at + Vector3(0, 7.2, 0) + side * sgn * 0.75 - face * 0.2), Vector3(1.4, 0.9, 0.06))
	pp.cyl("bronze", at + Vector3(0, 7.5, 0) + side * 0.35 + face * 0.2, 0.06, 0.05, 0.9, 6)    # raised arm
	# Arcade wall 47 m south on the axis, from the plaza up to Terrace Drive,
	# 56 m wide, three dark arches in its face; balustrade on top.
	var wc := f - u * 47.0
	var upper := maxf(_terrain(f - u * 60.0), plaza + 3.0)
	var hgt := upper - plaza + 1.0
	pp.box("sandstone", _xf(wc, plaza + hgt * 0.5 - 0.3, v), Vector3(56.0, hgt + 0.6, 3.0))
	for k: int in [-1, 0, 1]:
		var q := wc + v * (k * 5.2) + u * 1.52
		pp.box("slate", _xf(q, plaza + 2.1, v), Vector3(3.8, 4.2, 0.08))
	pp.box("sandstone", _xf(wc + u * 1.2, upper + 0.55, v), Vector3(56.0, 0.9, 0.35))
	# The grand stairs either side of the arcade, down to the plaza.
	for sgn: float in [-1.0, 1.0]:
		var steps := 12
		for k in steps:
			var t := float(k) / steps
			var q := wc + v * sgn * 20.0 + u * (2.0 + t * 15.0)
			var y := lerpf(upper, plaza, t)
			pp.box("sandstone", _xf(q, y - 0.5, v), Vector3(9.0, 1.0 + (upper - plaza) / steps, 15.0 / steps + 0.05))
		for w: float in [-4.8, 4.8]:
			var q0 := wc + v * (sgn * 20.0 + w) + u * 9.5
			pp.box("sandstone", _xf(q0, (upper + plaza) * 0.5 + 0.2, u), Vector3(15.5, 1.2, 0.5))
	# Balustrade on the Lake side, open at the centre for the boat landing.
	for sgn: float in [-1.0, 1.0]:
		var q := f + u * 21.0 + v * sgn * 19.0
		pp.box("sandstone", _xf(q, plaza + 0.45, v), Vector3(24.0, 0.9, 0.5))
	_commit(pp, "bethesda", 2500.0)


# ------------------------------------------------------------------ Bow Bridge
## Bow Bridge (1862): a cast-iron arch, painted cream, 26.5 m span across the
## Lake from Cherry Hill to the Ramble. Deck with its bow-shaped camber,
## lattice railings, urns, and the arch ribs below.
func _bow_bridge() -> void:
	var pl := _poly(_lm("bow_bridge").get("poly", []))
	if pl.size() < 2:
		return
	var a := pl[0]
	var b := pl[pl.size() - 1]
	var lake := 0.0
	for l: Dictionary in park.get("lakes"):
		if str(l["name"]) == "lake":
			lake = float(l["level"])
	var ya := maxf(_terrain(a), lake + 1.5)
	var yb := maxf(_terrain(b), lake + 1.5)
	_arch_bridge("bow_bridge", a, b, ya, yb, lake, 4.0, 1.0, "cream", "cream_lattice", "cream", true)


func _gapstow() -> void:
	var pl := _poly(_lm("gapstow_bridge").get("poly", []))
	if pl.size() < 4:
		return
	var c := _centroid(pl)
	var d := _axis(pl)
	var half := 0.0
	for q in pl:
		half = maxf(half, absf((q - c).dot(d)))
	var a := c - d * (half + 3.0)
	var b := c + d * (half + 3.0)
	var pond := INF
	for l: Dictionary in park.get("lakes"):
		if str(l["name"]) == "pond":
			pond = float(l["level"])
	if pond == INF:
		pond = _terrain(c) - 3.0
	_arch_bridge("gapstow_bridge", a, b, maxf(_terrain(a), pond + 2.0), maxf(_terrain(b), pond + 2.0),
		pond, 4.2, 1.4, "schist", "schist", "schist", false)


## An arched bridge from a to b (sim), deck ends at ya/yb, over water at
## `water`; `camber` is the rise of the deck at mid-span. Iron (lattice
## railing, open spandrels) or stone (solid parapets and spandrels).
func _arch_bridge(nm: String, a: Vector2, b: Vector2, ya: float, yb: float, water: float,
		width: float, camber: float, deck_k: String, rail_k: String, rib_k: String, iron: bool) -> void:
	_clear(Rect2(a, Vector2.ZERO).expand(b).grow(width * 0.5 + 1.0))
	var pp := _new_parts()
	var d := (b - a).normalized()
	var side := Vector2(-d.y, d.x)
	var L := a.distance_to(b)
	var n := 18
	var deck := func(t: float) -> float: return lerpf(ya, yb, t) + camber * sin(PI * t)
	# The arch's intrados: springs from just above the water at 12 % and 88 %.
	var arch := func(t: float) -> float:
		var s := clampf((t - 0.12) / 0.76, 0.0, 1.0)
		return lerpf(water + 0.4, deck.call(t) - (0.7 if iron else 1.0), sin(PI * s))
	for i in n:
		var t0 := float(i) / n
		var t1 := float(i + 1) / n
		var p0 := a + d * L * t0
		var p1 := a + d * L * t1
		var y0: float = deck.call(t0)
		var y1: float = deck.call(t1)
		var m := (p0 + p1) * 0.5
		var seg := Vector3(p1.x - p0.x, y1 - y0, -(p1.y - p0.y))
		var bas := Basis.looking_at(seg.normalized(), Vector3.UP)
		# looking_at gives -Z along seg; turn so +X runs along it.
		bas = bas * Basis(Vector3.UP, -PI * 0.5)
		pp.box(deck_k, Transform3D(bas, Vector3(m.x, (y0 + y1) * 0.5 - 0.15, -m.y)), Vector3(seg.length() + 0.04, 0.32, width))
		for sgn: float in [-1.0, 1.0]:
			var e0 := p0 + side * sgn * width * 0.5
			var e1 := p1 + side * sgn * width * 0.5
			# Railing (iron: open lattice; stone: a solid parapet).
			pp.strip(rail_k, [_g(e0, y0), _g(e1, y1)], 1.05 if iron else 0.95, true)
			if not iron:
				pp.box("schist", Transform3D(bas, Vector3((e0.x + e1.x) * 0.5, (y0 + y1) * 0.5 + 1.0, -(e0.y + e1.y) * 0.5)),
					Vector3(seg.length() + 0.05, 0.14, 0.5))
			# Spandrel: between the arch and the deck.
			var ya0: float = arch.call(t0)
			var ya1: float = arch.call(t1)
			if ya0 < y0 - 0.25 or ya1 < y1 - 0.25:
				var q0 := _g(e0 - side * sgn * 0.3, ya0)
				var q1 := _g(e1 - side * sgn * 0.3, ya1)
				var top0 := _g(e0 - side * sgn * 0.3, y0 - 0.3)
				var top1 := _g(e1 - side * sgn * 0.3, y1 - 0.3)
				var k := "cream_lattice" if iron else "schist"
				pp.quad(k, q0, q1, top1, top0, Rect2(t0 * L, 0, (t1 - t0) * L, 1))
				pp.quad(k, q1, q0, top0, top1, Rect2(t1 * L, 0, (t0 - t1) * L, 1))
				# Arch rib (iron) / intrados (stone).
				pp.box(rib_k, Transform3D(Basis.looking_at((q1 - q0).normalized(), Vector3.UP) * Basis(Vector3.UP, -PI * 0.5),
					(q0 + q1) * 0.5), Vector3(q0.distance_to(q1) + 0.05, 0.35, 0.35 if iron else 0.6))
		if not iron:
			var ya0: float = arch.call(t0)
			var ya1: float = arch.call(t1)
			if ya0 < y0 - 0.25:
				var l0 := _g(p0 - side * width * 0.5, ya0)
				var r0 := _g(p0 + side * width * 0.5, ya0)
				var l1 := _g(p1 - side * width * 0.5, ya1)
				var r1 := _g(p1 + side * width * 0.5, ya1)
				pp.quad("schist", l0, r0, r1, l1)
	if iron:
		# Urn planters on the railing posts at quarter points.
		for t: float in [0.06, 0.25, 0.5, 0.75, 0.94]:
			for sgn: float in [-1.0, 1.0]:
				var q := a + d * L * t + side * sgn * width * 0.5
				var y: float = deck.call(t)
				pp.cyl("cream", _g(q, y), 0.22, 0.22, 1.1, 6)
				pp.cyl("cream", _g(q, y + 1.1), 0.18, 0.35, 0.45, 8)
	_commit(pp, nm, 2000.0)


# ------------------------------------------------------------------ castle
## Belvedere Castle (1869) on Vista Rock: grey schist walls with
## crenellations, the square tower and the round turret with its pointed
## roof. The footprint is OSM's; the city box there is hidden.
func _castle() -> void:
	var pl := _poly(_lm("belvedere_castle").get("poly", []))
	if pl.size() < 4:
		return
	_hide.append(_bbox(pl).grow(2.0))
	_clear(_bbox(pl).grow(4.0))
	var y0 := _foot(pl) - 0.5
	var c := _centroid(pl)
	var pp := _new_parts()
	var grown := Geometry2D.offset_polygon(pl, 0.3)
	var fp := grown[0] if not grown.is_empty() else pl
	_prism(pp, "schist", fp, y0, y0 + 7.5, "slate")
	_merlons(pp, "schist", fp, y0 + 7.5, 0.3)
	# Tower at the north-west corner, turret at the north-east.
	var tw := fp[0]
	var tr := fp[0]
	for q in fp:
		if q.y - q.x > tw.y - tw.x:
			tw = q
		if q.y + q.x > tr.y + tr.x:
			tr = q
	var tcen := tw + (c - tw).normalized() * 3.2
	var sq := PackedVector2Array([tcen + Vector2(-3, -3), tcen + Vector2(3, -3), tcen + Vector2(3, 3), tcen + Vector2(-3, 3)])
	_prism(pp, "schist", sq, y0, y0 + 15.5, "slate")
	_merlons(pp, "schist", sq, y0 + 15.5, 0.25)
	var rc := tr + (c - tr).normalized() * 2.2
	pp.cyl("schist", _g(rc, y0), 2.3, 2.3, 12.0, 12)
	pp.cyl("slate", _g(rc, y0 + 12.0), 2.6, 0.0, 4.0, 12, false)
	pp.cyl("iron", _g(tcen, y0 + 15.5), 0.05, 0.04, 4.0, 4)
	_commit(pp, "belvedere_castle", 3000.0)


# ------------------------------------------------------------------ rink
## Wollman Rink. Ice in skating season (the scene's default: November); a
## season hook can swap in the summer amusement park later.
func _wollman() -> void:
	var pl := _poly(_lm("wollman_rink").get("poly", []))
	if pl.size() < 4:
		return
	var y := _terrain(_centroid(pl)) + 0.15
	_clear(_bbox(pl).grow(1.0))
	var pp := _new_parts()
	var pts := PackedVector2Array()
	var n := pl.size()
	if pl[0].is_equal_approx(pl[n - 1]):
		n -= 1
	for i in n:
		pts.append(pl[i])
	var tri := Geometry2D.triangulate_polygon(pts)
	for i in range(0, tri.size(), 3):
		var a := _g(pts[tri[i]], y)
		var b := _g(pts[tri[i + 1]], y)
		var c := _g(pts[tri[i + 2]], y)
		if (b - a).cross(c - a).y < 0:
			pp.tri("ice", a, b, c, Vector2(a.x, a.z), Vector2(b.x, b.z), Vector2(c.x, c.z))
		else:
			pp.tri("ice", a, c, b, Vector2(a.x, a.z), Vector2(c.x, c.z), Vector2(b.x, b.z))
	var ring: Array = []
	for i in n + 1:
		ring.append(_g(pts[i % n], y - 0.1))
	pp.strip("boards", ring, 1.2)
	_commit(pp, "wollman_rink", 2500.0, false)


# ------------------------------------------------------------------ bandshell
## Naumburg Bandshell (1923): a limestone half-dome over a raised stage,
## opening onto the Concert Ground beside the Mall.
func _bandshell() -> void:
	var pl := _poly(_lm("bandshell").get("poly", []))
	if pl.size() < 4:
		return
	_hide.append(_bbox(pl).grow(1.5))
	_clear(_bbox(pl).grow(3.0))
	var c := _centroid(pl)
	var y0 := _foot(pl)
	# Open toward the Mall's axis.
	var mall := _poly(_lm("the_mall").get("poly", []))
	var mc := _centroid(mall) if mall.size() > 3 else c + Vector2(-50, 0)
	var u := _axis(mall) if mall.size() > 3 else Vector2.UP
	var foot := mc + u * (c - mc).dot(u)
	var open := (foot - c).normalized()
	var pp := _new_parts()
	pp.box("stone", _xf(c, y0 + 0.6, open), Vector3(10.0, 1.2, 15.0))
	# Quarter-sphere shell: rings of quads, from the back (open side faces
	# `open`).
	var R := 7.2
	var back := c - open * 1.5
	var rows := 8
	var cols := 14
	for i in rows:
		var e0 := PI * 0.5 * i / rows
		var e1 := PI * 0.5 * (i + 1) / rows
		for j in cols:
			var a0 := PI * j / cols
			var a1 := PI * (j + 1) / cols
			var pts: Array = []
			for ea: Array in [[e0, a0], [e0, a1], [e1, a1], [e1, a0]]:
				var el: float = ea[0]
				var az: float = ea[1]
				# az sweeps from one side through the back to the other.
				var dir2 := (-open) * sin(az) + Vector2(-open.y, open.x) * cos(az)
				var q := back + dir2 * R * cos(el)
				pts.append(_g(q, y0 + 1.2 + R * sin(el) * 1.1))
			pp.quad("stone", pts[0], pts[1], pts[2], pts[3])
			pp.quad("stone", pts[1], pts[0], pts[3], pts[2])
	_commit(pp, "bandshell", 1500.0, false)


# ------------------------------------------------------------------ carousel
## The Friedsam Memorial Carousel's octagonal brick house and its roof.
func _carousel() -> void:
	var pl := _poly(_lm("carousel").get("poly", []))
	if pl.size() < 4:
		return
	_hide.append(_bbox(pl).grow(1.5))
	_clear(_bbox(pl).grow(3.0))
	var c := _centroid(pl)
	var r := 0.0
	for q in pl:
		r = maxf(r, q.distance_to(c))
	r = minf(r, 14.0)
	var y0 := _foot(pl)
	var pp := _new_parts()
	pp.cyl("brick", _g(c, y0), r * 0.92, r * 0.92, 4.6, 8, false, PI / 8.0)
	pp.cyl("slate", _g(c, y0 + 4.6), r * 1.02, r * 0.35, 3.0, 8, false, PI / 8.0)
	pp.cyl("brick", _g(c, y0 + 7.6), r * 0.35, r * 0.35, 1.2, 8, false, PI / 8.0)
	pp.cyl("slate", _g(c, y0 + 8.8), r * 0.42, 0.0, 1.8, 8, false, PI / 8.0)
	_commit(pp, "carousel", 1500.0, false)


# ------------------------------------------------------------------ Dairy
## The Dairy (1870), a Victorian Gothic cottage: stone walls under a steep
## slate roof with the ridge along its length.
func _dairy() -> void:
	var pl := PackedVector2Array()
	for b: Dictionary in doc.get("buildings", []):
		if str(b.get("name", "")).begins_with("The Dairy"):
			pl = _poly(b["pts"])
	if pl.size() < 4:
		return
	_hide.append(_bbox(pl).grow(1.5))
	_clear(_bbox(pl).grow(3.0))
	var c := _centroid(pl)
	var u := _axis(pl)
	var v := Vector2(-u.y, u.x)
	var lu := 0.0
	var lv := 0.0
	for q in pl:
		lu = maxf(lu, absf((q - c).dot(u)))
		lv = maxf(lv, absf((q - c).dot(v)))
	var y0 := _foot(pl)
	var pp := _new_parts()
	pp.box("sandstone", _xf(c, y0 + 2.6, u), Vector3(lu * 2.0, 5.2, lv * 1.8))
	var ridge := y0 + 5.2 + lv * 1.1
	var hw := lv * 0.95
	for sgn: float in [-1.0, 1.0]:
		var e0 := c - u * (lu + 0.4) + v * sgn * hw
		var e1 := c + u * (lu + 0.4) + v * sgn * hw
		var r0 := c - u * (lu + 0.4)
		var r1 := c + u * (lu + 0.4)
		var a := _g(e0, y0 + 5.0)
		var b := _g(e1, y0 + 5.0)
		var rr1 := _g(r1, ridge)
		var rr0 := _g(r0, ridge)
		pp.quad("slate", a, b, rr1, rr0)
		pp.quad("slate", b, a, rr0, rr1)
	for sgn: float in [-1.0, 1.0]:
		var e := c + u * sgn * lu
		pp.tri("sandstone", _g(e + v * hw, y0 + 5.2), _g(e - v * hw, y0 + 5.2), _g(e, ridge - 0.2))
		pp.tri("sandstone", _g(e - v * hw, y0 + 5.2), _g(e + v * hw, y0 + 5.2), _g(e, ridge - 0.2))
	_commit(pp, "dairy", 1500.0, false)


# ------------------------------------------------------------------ obelisk
## Cleopatra's Needle: the 21 m red granite obelisk (c. 1450 BC) on its
## stepped base behind the Met.
func _needle() -> void:
	var pl := _poly(_lm("cleopatras_needle").get("poly", []))
	if pl.size() < 3:
		return
	var c := _centroid(pl)
	var rot := atan2(_axis(pl).y, _axis(pl).x)
	var y := _foot(pl)
	_clear(Rect2(c - Vector2(5, 5), Vector2(10, 10)))
	var pp := _new_parts()
	var hs := [3.6, 3.0, 2.4]
	for k in 3:
		pp.cyl("granite", _g(c, y + k * 0.45), hs[k], hs[k], 0.45, 4, true, rot + PI / 4.0)
	y += 1.35
	pp.cyl("granite", _g(c, y), 1.95, 1.95, 1.6, 4, true, rot + PI / 4.0)
	y += 1.6
	# 2.35 m square at the foot, 1.6 m at the top, 19.6 m of shaft.
	pp.cyl("granite", _g(c, y), 1.66, 1.13, 19.6, 4, false, rot + PI / 4.0)
	pp.cyl("granite", _g(c, y + 19.6), 1.13, 0.0, 1.6, 4, false, rot + PI / 4.0)
	_commit(pp, "cleopatras_needle", 3000.0)


func _clear(r: Rect2) -> void:
	(park.get("clear_zones") as Array).append(r)


static func _bbox(pts: PackedVector2Array) -> Rect2:
	var b := Rect2(pts[0], Vector2.ZERO)
	for q in pts:
		b = b.expand(q)
	return b

static func _area(g: PackedVector2Array) -> float:
	var a := 0.0
	for i in g.size():
		var p := g[i]
		var q := g[(i + 1) % g.size()]
		a += p.x * q.y - q.x * p.y
	return a * 0.5


const LAKE_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley, specular_schlick_ggx;
#include "res://shaders/thermal.gdshaderinc"

uniform sampler2D fields : filter_linear_mipmap, repeat_disable;
uniform vec4 rect;
uniform vec3 deep : source_color = vec3(0.055, 0.075, 0.045);
uniform vec3 shallow : source_color = vec3(0.13, 0.15, 0.07);
uniform float weed = 1.0;

varying vec3 w;
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }

float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}

void fragment() {
    vec2 en = vec2(w.x, -w.z);
    vec2 fu = (en - rect.xy) / rect.zw;
    float d = (texture(fields, vec2(fu.x, 1.0 - fu.y)).g * 255.0 - 128.0) / 8.0;   // m from shore, + water
    // The mesh is each lake's outer ring only (no holes): its islands are
    // cut here, a metre onto land so the bank still meets the water.
    if (d < -1.0) { discard; }
    float fw = max(length(fwidth(en)), 0.003);
    // Calm water: two slow cat's-paw ripple sets, gone at range.
    float t = TIME;
    vec2 p1 = en * 0.9 + vec2(t * 0.11, t * 0.07);
    vec2 p2 = en * 2.3 - vec2(t * 0.05, t * 0.13);
    float e = 0.15;
    float r0 = vn(p1) + 0.5 * vn(p2);
    float rx = vn(p1 + vec2(e, 0.0)) + 0.5 * vn(p2 + vec2(e, 0.0)) - r0;
    float rz = vn(p1 + vec2(0.0, e)) + 0.5 * vn(p2 + vec2(0.0, e)) - r0;
    float amp = 0.10 * (1.0 - smoothstep(0.05, 0.6, fw));
    vec3 nw = normalize(vec3(-rx * amp / e, 1.0, rz * amp / e));
    // Wind lanes: patches of slightly rougher water.
    float lane = smoothstep(0.55, 0.8, vn(en / 40.0 + t * 0.01));
    vec3 c = mix(shallow, deep, smoothstep(0.0, 9.0, d));
    // Algae and duckweed along the banks; lily pads in the shallows of the
    // smaller ponds (the Pool, the Meer's coves, Turtle Pond).
    float edge = (1.0 - smoothstep(0.0, 1.5 + 2.5 * vn(en / 9.0), d)) * smoothstep(0.3, 0.7, vn(en / 6.0 + 3.1));
    vec3 algae = vec3(0.075, 0.11, 0.035) * (0.8 + 0.4 * vn(en * 1.3));
    c = mix(c, algae, edge * 0.7 * weed);
    vec2 cell = floor(en / 1.4);
    vec2 fc = fract(en / 1.4) - 0.5;
    vec2 jit = vec2(h2(cell), h2(cell + 7.1)) - 0.5;
    float rr = 0.18 + 0.22 * h2(cell + 3.3);
    float pad = (1.0 - smoothstep(rr - fw, rr + fw, length(fc - jit * 0.6)))
        * step(0.3, h2(cell + 1.9)) * (1.0 - smoothstep(0.5, 4.0, d)) * step(0.5, weed)
        * step(0.62, vn(en / 7.0)) * (1.0 - smoothstep(0.08, 0.25, fw));
    vec3 lily = vec3(0.045, 0.10, 0.025) * (0.8 + 0.5 * h2(cell + 5.0));
    c = mix(c, lily, pad);
    float on_pad = max(pad, edge * 0.6 * weed);
    ALBEDO = c;
    ROUGHNESS = mix(mix(0.06, 0.14, lane), 0.85, on_pad);
    METALLIC = 0.0;
    SPECULAR = mix(0.4, 0.2, on_pad);
    NORMAL = normalize((VIEW_MATRIX * vec4(mix(nw, vec3(0.0, 1.0, 0.0), on_pad), 0.0)).xyz);
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Inland ponds sit a degree or so under the sea; a still surface
        // mirrors the cold sky at grazing angles like any water.
        vec3 vw = normalize(CAMERA_POSITION_WORLD - w);
        vec2 th = th_water(r0 - 0.75, dot(vec3(0.0, 1.0, 0.0), vw));
        th.y -= 1.0 / 120.0;
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""


const LM_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

// Per material (index in the vertex colour's alpha): rough, metal, grain,
// mode; and heat [absorb, sun_c, mass, own_c] (thermal.gdshaderinc).
// mode: 0 solid, 1 lattice (railings/fences: posts, rails, open between),
// 2 ice (scored), 3 lamp globe (glows at night).
uniform vec4 mat_p[24];
uniform vec4 mat_h[24];
uniform float glow_e = 0.0;

varying vec3 w;
varying flat int mid;
void vertex() {
    w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    mid = clamp(int(COLOR.a * 255.0 + 0.5), 0, 23);
}

float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}

void fragment() {
    vec4 mp = mat_p[mid];
    vec4 heat = mat_h[mid];
    float rough = mp.x;
    float metal = mp.y;
    float grain = mp.z;
    int mode = int(mp.w + 0.5);
    vec3 c = COLOR.rgb;
    float fw = max(length(fwidth(w)), 0.002);
    if (mode == 1) {
        // UV.x metres along, UV.y 0 top..1 foot. Pickets every 0.13 m, a top
        // and bottom rail, a post every 2.4 m. Past a few hundred metres the
        // pickets are sub-pixel: draw a thin screen instead of shimmer.
        float a = UV.x;
        float y = UV.y;
        float picket = step(abs(fract(a / 0.13) - 0.5), 0.13);
        float post = step(abs(fract(a / 2.4) - 0.5) * 2.4, 0.05);
        float rail = step(y, 0.06) + step(abs(y - 0.85), 0.03);
        float solid = max(max(picket, post), rail);
        float far = smoothstep(0.03, 0.08, fw);
        if (solid < 0.5 && h2(floor(FRAGCOORD.xy)) > far * 0.45) { discard; }
    }
    float n = vn(w.xz * 1.7 + w.y * 0.9) * 0.6 + vn(w.xz * 0.3 + w.y * 0.2) * 0.4;
    c *= 1.0 + grain * (n - 0.5);
    // Weathering: darker toward the ground.
    float rgh = rough;
    if (mode == 2) {
        float sc = vn(vec2(w.x * 4.0 + w.z * 9.0, w.z * 0.5)) * vn(vec2(w.z * 5.0 - w.x * 7.0, w.x * 0.4));
        c *= 0.92 + 0.12 * sc;
        rgh = mix(0.08, 0.35, sc);
    }
    ALBEDO = c;
    ROUGHNESS = rgh;
    METALLIC = metal;
    if (mode == 3) { EMISSION = vec3(1.0, 0.82, 0.55) * glow_e; }
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        float own = heat.w + (mode == 3 ? 30.0 * thermal_lamps : 0.0);
        vec2 th = th_surface(heat.x, heat.y, heat.z, own);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
