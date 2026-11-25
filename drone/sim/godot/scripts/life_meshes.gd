## Meshes and shaders for the moving world's vehicles and people
## (world_life.gd and its parts): Manhattan's street mix, built once and
## shared by moving traffic and parked cars.
##
## WHY THE MODELS ARE BAKED INTO ONE VERTEX-COLOURED SURFACE
##
## The car models (assets/models/car_*.glb) arrive as seven mesh nodes and four
## materials each. Drawn through MultiMeshes that is four draws per car type per
## chunk, and it would need the stock materials, which cannot move a car
## between the sim's ticks (below). So each model is flattened here: every
## surface transformed into the model's frame and merged into ONE surface, its
## texture sampled into the vertex colour and its material recorded in the
## vertex alpha as a class (paint, glass, lamp...). One draw per MultiMesh, and
## one shader for every vehicle in the scene. A texture sampled per vertex is
## coarser than the texture, but from 30 m up a car's texture is a few pixels
## and its shape and paint are everything.
##
## WHY VEHICLES MOVE IN THE SHADER BETWEEN TICKS
##
## Traffic is simulated in GDScript at about 5 Hz per vehicle (life_traffic.gd
## staggers the vehicles over frames). Writing hundreds of transforms every
## frame costs more than the simulation. Instead each instance carries its
## speed, yaw rate and the time of its last tick, and the vertex shader carries
## it forward along its arc until the next tick replaces the transform.
##
## Vertex alpha classes (CLS_*): the fragment shader keys off ranges, so a value
## survives interpolation and 8-bit storage.
##
## Lod bands for static parked cars: a MultiMesh's visibility range is judged
## for the whole chunk, so an instance is also dropped in the vertex shader
## when its own distance to the camera is outside [lod_min, lod_max). The
## near, mid and far meshes of a chunk then hand over car by car, not chunk by
## chunk. (The camera position in the shadow pass is the light's, which is why
## parked cars do not cast real shadows; they carry a dark blob instead.)
extends RefCounted

const ModelKit = preload("res://scripts/model_kit.gd")

const CLS_PAINT := 1.0
const CLS_GLASS := 0.85
const CLS_SIGN := 0.72
const CLS_HEAD := 0.58
const CLS_TAIL := 0.44
const CLS_BLOB := 0.3
const CLS_OTHER := 0.0

## Manhattan's street mix. model: a car_*.glb; proc: built from boxes here.
## variant: taxi (roof light), police (side stripe, light bar).
const KINDS := {
	"taxi": {"model": "car_sedan", "variant": "taxi"},
	"taxi_van": {"model": "car_minivan", "variant": "taxi"},
	"sedan": {"model": "car_sedan"},
	"suv": {"model": "car_suv"},
	"hatch": {"model": "car_hatchback"},
	"minivan": {"model": "car_minivan"},
	"wagon": {"model": "car_wagon"},
	"compact": {"model": "car_compact"},
	"police": {"model": "car_suv", "variant": "police"},
	"van": {"proc": "van"},
	"truck": {"proc": "truck"},
	"bus": {"proc": "bus"},
}

## NYC's yellow (the TLC's "taxi yellow" is close to this under the scene's tonemap).
const TAXI_YELLOW := Color(0.96, 0.72, 0.06)
const POLICE_WHITE := Color(0.93, 0.94, 0.95)
const BLACK_CAR := Color(0.035, 0.037, 0.04)
## Private car paints, weighted toward what fills a US street: white, black,
## greys and silvers, then a few colours.
const PAINTS := [Color(0.9, 0.9, 0.89), Color(0.86, 0.87, 0.87), Color(0.06, 0.06, 0.065),
	Color(0.09, 0.09, 0.1), Color(0.5, 0.51, 0.53), Color(0.62, 0.63, 0.64), Color(0.35, 0.36, 0.38),
	Color(0.55, 0.08, 0.07), Color(0.12, 0.2, 0.42), Color(0.2, 0.24, 0.27), Color(0.42, 0.38, 0.33),
	Color(0.72, 0.72, 0.7)]

static var _cache: Dictionary = {}
static var _material: ShaderMaterial = null
static var _person_material: ShaderMaterial = null


## {"lod0", "lod1", "lod2": ArrayMesh, "dims": Vector3(length, height, width)}
## for a KINDS entry, built on first use. Empty when its model is missing.
static func kind(name: String) -> Dictionary:
	if _cache.has(name):
		return _cache[name]
	var spec: Dictionary = KINDS.get(name, {})
	var out := {}
	if spec.has("model"):
		out = _bake_model(str(spec["model"]), str(spec.get("variant", "")))
	elif spec.has("proc"):
		out = _proc(str(spec["proc"]))
	_cache[name] = out
	return out


## One shader material for every vehicle. `now` and `night` are set per frame
## by world_life; parked cars use per-instance lod bands via their own copies
## (lod_material).
static func material() -> ShaderMaterial:
	if _material == null:
		var sh := Shader.new()
		sh.code = VEHICLE_SHADER
		_material = ShaderMaterial.new()
		_material.shader = sh
	return _material


## A copy of the vehicle material drawing only instances whose distance to the
## camera is in [lo, hi). Copies share the shader; world_life keeps their
## `now` / `night` in step.
static func lod_material(lo: float, hi: float) -> ShaderMaterial:
	var m := material().duplicate() as ShaderMaterial
	m.set_shader_parameter("lod_min", lo)
	m.set_shader_parameter("lod_max", hi)
	return m


## Paint as one float for INSTANCE_CUSTOM.x: 8 bits a channel, exact in a float32.
static func pack_rgb(c: Color) -> float:
	return float(int(clampf(c.r, 0, 1) * 255.0) * 65536 + int(clampf(c.g, 0, 1) * 255.0) * 256
		+ int(clampf(c.b, 0, 1) * 255.0))


# ------------------------------------------------------------------ models

static func _bake_model(model: String, variant: String) -> Dictionary:
	var root := ModelKit.instance(model)
	if root == null:
		return {}
	var ab := ModelKit.aabb(root)
	var L := ab.size.z
	var H := ab.size.y
	var W := ab.size.x
	var centre := Vector3(ab.get_center().x, 0.0, ab.get_center().z)
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var ax := Vector3(2.0 / L, 1.0 / H, 0.0)
	for mi in ModelKit.meshes(root):
		var xf: Transform3D = ModelKit._to(root, mi)
		for s in mi.mesh.get_surface_count():
			var mat := mi.mesh.surface_get_material(s)
			var mname := str(mat.resource_name) if mat != null else ""
			var cls := CLS_OTHER
			if mname.begins_with("PAINT"):
				cls = CLS_PAINT
			elif mname.begins_with("Glass"):
				cls = CLS_GLASS
			elif mname.begins_with("Optics"):
				cls = CLS_HEAD
			var base := Color(1, 1, 1)
			var img: Image = null
			if mat is BaseMaterial3D:
				base = (mat as BaseMaterial3D).albedo_color
				var tex := (mat as BaseMaterial3D).albedo_texture
				if tex != null:
					img = tex.get_image()
					if img != null and img.is_compressed():
						img.decompress()
			var arr := mi.mesh.surface_get_arrays(s)
			var verts: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
			var norms: PackedVector3Array = arr[Mesh.ARRAY_NORMAL] if arr[Mesh.ARRAY_NORMAL] != null else PackedVector3Array()
			var uvs: PackedVector2Array = arr[Mesh.ARRAY_TEX_UV] if arr[Mesh.ARRAY_TEX_UV] != null else PackedVector2Array()
			var idx: PackedInt32Array = arr[Mesh.ARRAY_INDEX] if arr[Mesh.ARRAY_INDEX] != null else PackedInt32Array()
			var count := idx.size() if not idx.is_empty() else verts.size()
			# The paint textures are coloured (the pack's cars came cyan, yellow,
			# maroon...), which tinted every repaint: a white car came out teal.
			# For paint only the texture's shading is kept, as grey about 1, so
			# the instance's paint is the colour.
			var mean_lum := 1.0
			if img != null and cls == CLS_PAINT:
				var acc := 0.0
				var k := 0
				for y in range(0, img.get_height(), 8):
					for x in range(0, img.get_width(), 8):
						acc += img.get_pixel(x, y).get_luminance()
						k += 1
				mean_lum = maxf(acc / maxf(k, 1.0), 0.02)
			# Colour per source vertex once, not per corner.
			var cols := PackedColorArray()
			cols.resize(verts.size())
			for v in verts.size():
				var c := base
				if img != null and v < uvs.size():
					var uv := uvs[v]
					var px := int(fposmod(uv.x, 1.0) * img.get_width()) % img.get_width()
					var py := int(fposmod(uv.y, 1.0) * img.get_height()) % img.get_height()
					var t := img.get_pixel(px, py)
					if cls == CLS_PAINT:
						var g := clampf(t.get_luminance() / mean_lum, 0.15, 1.15)
						c = Color(g, g, g)
					else:
						c = base * t
				cols[v] = c
			for k in count:
				var v := idx[k] if not idx.is_empty() else k
				var p := xf * verts[v] - centre
				var n := (xf.basis * norms[v]).normalized() if v < norms.size() else Vector3.UP
				var c := cols[v]
				var vc := cls
				if cls == CLS_HEAD and p.z < 0.0:
					vc = CLS_TAIL
				if variant == "police" and cls == CLS_PAINT and absf(p.x) > W * 0.36 \
						and p.y > H * 0.36 and p.y < H * 0.5:
					c = Color(0.06, 0.2, 0.62)       # the blue side stripe
					vc = CLS_OTHER
				st.set_color(Color(c.r, c.g, c.b, vc))
				st.set_normal(n)
				st.set_uv(Vector2(p.z * ax.x, p.y * ax.y))
				st.add_vertex(p)
	var roof := _roof_height(root, centre, H)
	if variant == "taxi":
		# The roof light (the medallion sign), lit at night.
		_box(st, Vector3(0, roof + 0.14, -0.15), Vector3(0.82, 0.28, 0.32), Color(0.98, 0.93, 0.7), CLS_SIGN, ax)
	elif variant == "police":
		_box(st, Vector3(-0.3, roof + 0.07, -0.05), Vector3(0.6, 0.14, 0.3), Color(0.08, 0.15, 0.7), CLS_OTHER, ax)
		_box(st, Vector3(0.3, roof + 0.07, -0.05), Vector3(0.6, 0.14, 0.3), Color(0.7, 0.06, 0.05), CLS_OTHER, ax)
	_blob(st, L, W, ax)
	st.index()
	var lod0 := st.commit()
	return {"lod0": lod0, "lod1": _decimate(lod0, 0.22), "lod2": _box_car(Vector3(L, H, W)),
			"dims": Vector3(L, H, W)}


## The highest point of the model near its middle: where a roof sign stands.
static func _roof_height(root: Node3D, centre: Vector3, H: float) -> float:
	var top := 0.0
	for mi in ModelKit.meshes(root):
		var xf: Transform3D = ModelKit._to(root, mi)
		for s in mi.mesh.get_surface_count():
			var verts: PackedVector3Array = mi.mesh.surface_get_arrays(s)[Mesh.ARRAY_VERTEX]
			for v in verts:
				var p := xf * v - centre
				if absf(p.x) < 0.3 and absf(p.z) < 0.4:
					top = maxf(top, p.y)
	return top if top > 0.5 else H


## A reduced copy of a single-surface mesh for the mid distances, by vertex
## clustering: vertices are snapped to a `cell`-metre grid and merged per cell
## and per class, and triangles that collapse are dropped.
##
## Tried first: SurfaceTool.generate_lod and ImporterMesh.generate_lods
## (meshoptimizer). Both left the cars at 99 % and 50 % of their triangles:
## the baked models are islands of flat-shaded faces with their own normals
## and colours, so almost every edge is a locked border. Clustering ignores
## topology, which is what a car seen from 200 m can afford; keeping the
## class in the key keeps glass, lamps and paint apart.
static func _decimate(mesh: ArrayMesh, cell: float) -> ArrayMesh:
	var arr := mesh.surface_get_arrays(0)
	var verts: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
	var norms: PackedVector3Array = arr[Mesh.ARRAY_NORMAL]
	var cols: PackedColorArray = arr[Mesh.ARRAY_COLOR]
	var uvs: PackedVector2Array = arr[Mesh.ARRAY_TEX_UV]
	var idx: PackedInt32Array = arr[Mesh.ARRAY_INDEX]
	var key_of := {}
	var remap := PackedInt32Array()
	remap.resize(verts.size())
	var psum := PackedVector3Array()
	var nsum := PackedVector3Array()
	var cnt := PackedInt32Array()
	var ocol := PackedColorArray()
	var ouv := PackedVector2Array()
	for v in verts.size():
		var p := verts[v]
		var key := (int(floor(p.x / cell)) + 512) + (int(floor(p.y / cell)) + 512) * 1024 \
			+ (int(floor(p.z / cell)) + 512) * 1048576 + int(cols[v].a * 20.0) * 1073741824
		var k: int = key_of.get(key, -1)
		if k < 0:
			k = psum.size()
			key_of[key] = k
			psum.append(Vector3.ZERO)
			nsum.append(Vector3.ZERO)
			cnt.append(0)
			ocol.append(cols[v])
			ouv.append(uvs[v])
		psum[k] += p
		nsum[k] += norms[v]
		cnt[k] += 1
		remap[v] = k
	var opos := PackedVector3Array()
	var onrm := PackedVector3Array()
	opos.resize(psum.size())
	onrm.resize(psum.size())
	for k in psum.size():
		opos[k] = psum[k] / float(cnt[k])
		onrm[k] = nsum[k].normalized() if nsum[k].length() > 1e-4 else Vector3.UP
	var oidx := PackedInt32Array()
	var seen := {}
	for t in range(0, idx.size(), 3):
		var a := remap[idx[t]]
		var b := remap[idx[t + 1]]
		var c := remap[idx[t + 2]]
		if a == b or b == c or a == c:
			continue
		var lo := mini(a, mini(b, c))
		var hi := maxi(a, maxi(b, c))
		var tk := Vector3i(lo, a + b + c - lo - hi, hi)
		if seen.has(tk):
			continue
		seen[tk] = true
		oidx.append_array([a, b, c])
	var out_arr := []
	out_arr.resize(Mesh.ARRAY_MAX)
	out_arr[Mesh.ARRAY_VERTEX] = opos
	out_arr[Mesh.ARRAY_NORMAL] = onrm
	out_arr[Mesh.ARRAY_COLOR] = ocol
	out_arr[Mesh.ARRAY_TEX_UV] = ouv
	out_arr[Mesh.ARRAY_INDEX] = oidx
	var out := ArrayMesh.new()
	out.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, out_arr)
	return out


## The far car: a painted body, a dark glasshouse, a painted roof and its
## shadow, in metres for `dims` (length, height, width). About forty triangles.
static func _box_car(dims: Vector3) -> ArrayMesh:
	var L := dims.x
	var H := dims.y
	var W := dims.z
	var ax := Vector3(2.0 / L, 1.0 / H, 0.0)
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var white := Color(1, 1, 1)
	_box(st, Vector3(0, H * 0.36, 0), Vector3(W, H * 0.48, L * 0.98), white, CLS_PAINT, ax)
	_box(st, Vector3(0, H * 0.78, -L * 0.04), Vector3(W * 0.86, H * 0.36, L * 0.5), white, CLS_GLASS, ax)
	_box(st, Vector3(0, H * 0.975, -L * 0.04), Vector3(W * 0.82, H * 0.04, L * 0.44), white, CLS_PAINT, ax)
	_blob(st, L, W, ax)
	return st.commit()


# ------------------------------------------------------------------ procedural

## Buses, box trucks and vans are built from boxes: none of the downloaded
## models is one, and at the sizes they are drawn a box with the right
## proportions, glazing and livery reads as the vehicle.
static func _proc(kind_name: String) -> Dictionary:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var white := Color(1, 1, 1)
	var dark := Color(0.06, 0.06, 0.07)
	var dims := Vector3.ZERO
	match kind_name:
		"bus":
			# An MTA 40-footer: white over a dark window band, a blue stripe,
			# the roof's air-conditioning pod aft, destination sign in front.
			# Engine at the back, so the thermal axis runs backwards.
			var L := 12.2
			var H := 3.2
			var W := 2.6
			dims = Vector3(L, H, W)
			var ax := Vector3(-2.0 / L, 1.0 / H, 0.0)
			_box(st, Vector3(0, 0.35 + 0.55, 0), Vector3(W, 1.1, L), white, CLS_PAINT, ax)
			_box(st, Vector3(0, 1.45 + 0.55, 0), Vector3(W * 1.004, 1.1, L * 0.995), white, CLS_GLASS, ax)
			_box(st, Vector3(0, 2.55 + 0.3, 0), Vector3(W, 0.6, L), white, CLS_PAINT, ax)
			_box(st, Vector3(0, 1.32, 0), Vector3(W * 1.01, 0.14, L * 0.99), Color(0.08, 0.28, 0.7), CLS_OTHER, ax)
			_box(st, Vector3(0, H + 0.17, -L * 0.3), Vector3(W * 0.72, 0.34, 3.2), Color(0.72, 0.74, 0.75), CLS_OTHER, ax)
			_box(st, Vector3(0, H + 0.1, L * 0.15), Vector3(W * 0.5, 0.2, 1.6), Color(0.6, 0.62, 0.63), CLS_OTHER, ax)
			_box(st, Vector3(0, 1.85, L * 0.5 + 0.01), Vector3(W * 0.92, 1.5, 0.04), white, CLS_GLASS, ax)
			_box(st, Vector3(0, 2.85, L * 0.5 + 0.02), Vector3(W * 0.72, 0.26, 0.04), Color(1.0, 0.62, 0.12), CLS_SIGN, ax)
			_wheels(st, W, L, 0.5, [-0.3, 0.27], ax)
			_lamps(st, W, L, 0.75, 0.9, ax)
		"truck":
			# A box truck: painted cab, white box body behind.
			var L := 8.0
			var H := 3.5
			var W := 2.5
			dims = Vector3(L, H, W)
			var ax := Vector3(2.0 / L, 1.0 / H, 0.0)
			_box(st, Vector3(0, 0.55, 0), Vector3(W * 0.9, 0.35, L * 0.98), dark, CLS_OTHER, ax)
			_box(st, Vector3(0, 1.45, L * 0.5 - 1.0), Vector3(W, 1.6, 2.0), white, CLS_PAINT, ax)
			_box(st, Vector3(0, 2.0, L * 0.5 + 0.01), Vector3(W * 0.9, 0.7, 0.04), white, CLS_GLASS, ax)
			_box(st, Vector3(0, 2.0, L * 0.5 - 0.6), Vector3(W * 1.006, 0.6, 0.9), white, CLS_GLASS, ax)
			_box(st, Vector3(0, 2.12, -1.05), Vector3(W * 1.02, 2.75, L - 2.1), Color(0.88, 0.88, 0.86), CLS_OTHER, ax)
			_wheels(st, W, L, 0.5, [-0.33, 0.3], ax)
			_lamps(st, W, L, 0.8, 0.9, ax)
		_:
			# A high-roof delivery van.
			var L := 6.0
			var H := 2.6
			var W := 2.0
			dims = Vector3(L, H, W)
			var ax := Vector3(2.0 / L, 1.0 / H, 0.0)
			_box(st, Vector3(0, 0.85, L * 0.5 - 0.5), Vector3(W * 0.96, 0.9, 1.0), white, CLS_PAINT, ax)
			_box(st, Vector3(0, 1.48, -0.5), Vector3(W, 2.15, L - 1.0), white, CLS_PAINT, ax)
			_box(st, Vector3(0, 1.62, L * 0.5 - 0.96), Vector3(W * 0.9, 0.72, 0.1), white, CLS_GLASS, ax)
			_box(st, Vector3(0, 1.7, L * 0.5 - 1.45), Vector3(W * 1.006, 0.6, 0.8), white, CLS_GLASS, ax)
			_wheels(st, W, L, 0.36, [-0.32, 0.3], ax)
			_lamps(st, W, L, 0.8, 1.0, ax)
	_blob(st, dims.x, dims.z, Vector3(2.0 / dims.x, 1.0 / dims.y, 0.0))
	var m := st.commit()
	return {"lod0": m, "lod1": m, "lod2": m, "dims": dims}


static func _wheels(st: SurfaceTool, W: float, L: float, r: float, at: Array, ax: Vector3) -> void:
	for zf in at:
		for sx in [-1.0, 1.0]:
			_box(st, Vector3(sx * W * 0.43, r, float(zf) * L), Vector3(0.3, r * 2.0, r * 2.0),
				Color(0.04, 0.04, 0.04), CLS_OTHER, ax)


static func _lamps(st: SurfaceTool, W: float, L: float, yh: float, yt: float, ax: Vector3) -> void:
	for sx in [-1.0, 1.0]:
		_box(st, Vector3(sx * W * 0.38, yh, L * 0.5 + 0.02), Vector3(0.3, 0.16, 0.04), Color(1, 1, 1), CLS_HEAD, ax)
		_box(st, Vector3(sx * W * 0.4, yt, -L * 0.5 - 0.02), Vector3(0.24, 0.2, 0.04), Color(1, 1, 1), CLS_TAIL, ax)


## The dark patch under a vehicle (its shadow and the dirt under it). Seen from
## above it is most of what grounds a car on the street.
static func _blob(st: SurfaceTool, L: float, W: float, ax: Vector3) -> void:
	var h := Vector2(W * 0.53, L * 0.51)
	var y := 0.03
	_quad(st, Vector3(-h.x, y, -h.y), Vector3(h.x, y, -h.y), Vector3(h.x, y, h.y), Vector3(-h.x, y, h.y),
		Vector3.UP, Color(0.02, 0.02, 0.02), CLS_BLOB, ax)


## A box into a SurfaceTool: flat normals, vertex colour, the class in alpha
## and the thermal axis (`ax`: x per metre forward, y per metre up) in UV.
static func _box(st: SurfaceTool, c: Vector3, s: Vector3, col: Color, cls: float, ax: Vector3) -> void:
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
			_quad(st, o - u - v, o + u - v, o + u + v, o - u + v, nrm, col, cls, ax)


static func _quad(st: SurfaceTool, p0: Vector3, p1: Vector3, p2: Vector3, p3: Vector3, nrm: Vector3,
		col: Color, cls: float, ax: Vector3) -> void:
	_tri(st, p0, p1, p2, nrm, col, cls, ax)
	_tri(st, p0, p2, p3, nrm, col, cls, ax)


## Godot draws clockwise faces as the front.
static func _tri(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, nrm: Vector3, col: Color,
		cls: float, ax: Vector3) -> void:
	if (b - a).cross(c - a).dot(nrm) > 0.0:
		var t := b
		b = c
		c = t
	for p: Vector3 in [a, b, c]:
		st.set_normal(nrm)
		st.set_color(Color(col.r, col.g, col.b, cls))
		st.set_uv(Vector2(p.z * ax.x, p.y * ax.y))
		st.add_vertex(p)


# ------------------------------------------------------------------ people

## A walker, +Z forward, feet at the origin, ~1.72 m, about 130 triangles.
## UV.x tags the limbs (+-1 legs, +-2 arms) for the walk in the vertex shader;
## vertex alpha 0 takes the instance's shirt colour.
static func person_mesh() -> ArrayMesh:
	if _cache.has("_person"):
		return _cache["_person"]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var pants := Color(0.16, 0.16, 0.19, 1.0)
	var skin := Color(0.5, 0.36, 0.27, 1.0)
	var shirt := Color(1, 1, 1, 0.0)
	var hair := Color(0.08, 0.06, 0.05, 1.0)
	for sx in [-1.0, 1.0]:
		_pbox(st, Vector3(sx * 0.1, 0.43, 0.0), Vector3(0.14, 0.86, 0.18), pants, sx)
		_pbox(st, Vector3(sx * 0.27, 1.12, 0.0), Vector3(0.09, 0.6, 0.11), shirt, sx * 2.0)
	_pbox(st, Vector3(0, 1.15, 0), Vector3(0.42, 0.6, 0.24), shirt, 0.0)
	_pbox(st, Vector3(0, 1.58, 0), Vector3(0.19, 0.22, 0.21), skin, 0.0)
	_pbox(st, Vector3(0, 1.7, -0.01), Vector3(0.2, 0.05, 0.22), hair, 0.0)
	var m := st.commit()
	_cache["_person"] = m
	return m


static func _pbox(st: SurfaceTool, c: Vector3, s: Vector3, col: Color, tag: float) -> void:
	var h := s * 0.5
	for axis in 3:
		for sgn in [-1.0, 1.0]:
			if axis == 1 and sgn < 0.0:
				continue                    # nobody sees the soles
			var nrm := Vector3.ZERO
			nrm[axis] = sgn
			var u := Vector3.ZERO
			u[(axis + 1) % 3] = h[(axis + 1) % 3]
			var v := Vector3.ZERO
			v[(axis + 2) % 3] = h[(axis + 2) % 3]
			var o := c + nrm * h[axis]
			var q := [o - u - v, o + u - v, o + u + v, o - u + v]
			for t: Array in [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]:
				var a: Vector3 = t[0]
				var b: Vector3 = t[1]
				var cc: Vector3 = t[2]
				if (b - a).cross(cc - a).dot(nrm) > 0.0:
					var tmp := b
					b = cc
					cc = tmp
				for p: Vector3 in [a, b, cc]:
					st.set_normal(nrm)
					st.set_color(col)
					st.set_uv(Vector2(tag, 0.0))
					st.add_vertex(p)


static func person_material() -> ShaderMaterial:
	if _person_material == null:
		var sh := Shader.new()
		sh.code = PERSON_SHADER
		_person_material = ShaderMaterial.new()
		_person_material.shader = sh
	return _person_material


const VEHICLE_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

// Seconds (mod 1000) on the sim's tick clock, and the night (0 day .. 1).
uniform float now = 0.0;
uniform float night = 0.0;
// Parked cars: drawn only when this instance is lod_min..lod_max from the camera.
uniform float lod_min = 0.0;
uniform float lod_max = 1.0e9;

// INSTANCE_CUSTOM: x paint (packed rgb), y speed (m/s), z tick time (s, mod 1000),
// w: 0..1 a parked car's warmth, or 20 + yaw rate (rad/s) + 10 if braking for a
// moving one.
varying vec3 v_paint;
varying vec2 v_axis;
varying float v_brake;
varying float v_heat;
varying float v_cls;

vec3 unpack_rgb(float f) {
    float v = floor(f + 0.5);
    return vec3(floor(v / 65536.0), mod(floor(v / 256.0), 256.0), mod(v, 256.0)) / 255.0;
}

// Paint and the baked colours are written as sRGB (as picked, and as the
// textures they came from); lighting wants linear. Without this every colour
// came out pale: a black hull read grey, a red car pink.
vec3 to_linear(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}

void vertex() {
    vec4 cu = INSTANCE_CUSTOM;
    v_paint = to_linear(unpack_rgb(cu.x));
    v_axis = UV;
    v_cls = COLOR.a;
    v_brake = 0.0;
    v_heat = clamp(cu.w, 0.0, 1.0);
    float d = distance(MODEL_MATRIX[3].xyz, CAMERA_POSITION_WORLD);
    if (d < lod_min || d >= lod_max) {
        VERTEX = vec3(0.0);
    } else if (cu.w > 15.0) {
        float m = cu.w - 20.0;
        v_brake = step(5.0, m);
        float yr = m - 10.0 * v_brake;
        v_heat = 1.0;
        float dt = now - cu.z;
        if (dt < 0.0) { dt += 1000.0; }
        dt = clamp(dt, 0.0, 0.6);
        float th = yr * dt;
        float c = cos(th);
        float s = sin(th);
        VERTEX = vec3(VERTEX.x * c + VERTEX.z * s, VERTEX.y, -VERTEX.x * s + VERTEX.z * c);
        NORMAL = vec3(NORMAL.x * c + NORMAL.z * s, NORMAL.y, -NORMAL.x * s + NORMAL.z * c);
        float run = cu.y * dt;
        VERTEX += vec3(sin(th * 0.5) * run / max(length(MODEL_MATRIX[0].xyz), 1e-3), 0.0,
                       cos(th * 0.5) * run / max(length(MODEL_MATRIX[2].xyz), 1e-3));
    }
}

void fragment() {
    vec3 alb = to_linear(COLOR.rgb);
    float rough = 0.7;
    float metal = 0.0;
    vec3 em = vec3(0.0);
    float cls = v_cls;
    if (cls > 0.92) {
        alb *= v_paint;
        rough = 0.3;
        metal = 0.2;
    } else if (cls > 0.78) {
        alb = vec3(0.025, 0.03, 0.035);
        rough = 0.06;
        metal = 0.4;
    } else if (cls > 0.65) {
        em = alb * night * 2.5;
    } else if (cls > 0.51) {
        alb = vec3(0.85);
        rough = 0.1;
        em = vec3(1.0, 0.94, 0.82) * night * 6.0;
    } else if (cls > 0.37) {
        alb = vec3(0.45, 0.04, 0.03);
        rough = 0.2;
        em = vec3(1.0, 0.06, 0.03) * (night * 2.0 + v_brake * (1.0 + 3.0 * night));
    } else if (cls > 0.15) {
        alb = vec3(0.012);
        rough = 1.0;
    }
    ALBEDO = alb;
    ROUGHNESS = rough;
    METALLIC = metal;
    EMISSION = em;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Bodywork is light sheet metal: sun-heated, cold at night. The engine
        // bay (front, low) and exhaust (rear, under) carry the vehicle's own
        // heat, scaled by how long it has run (a parked car cools in an hour).
        vec2 th = th_surface(0.6, 22.0, 0.15, 0.5 + 2.0 * v_heat);
        float engine = smoothstep(0.25, 0.8, v_axis.x) * (1.0 - smoothstep(0.2, 0.75, v_axis.y));
        float exhaust = (1.0 - smoothstep(-0.98, -0.72, v_axis.x)) * (1.0 - smoothstep(0.08, 0.32, v_axis.y));
        th.y += v_heat * (22.0 * engine + 36.0 * exhaust) / 120.0;
        if (cls > 0.78 && cls <= 0.92) {
            // Glass reflects the cold sky it faces.
            float up = clamp((INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).y, 0.0, 1.0);
            th.y = mix(th.y, th_enc(mix(thermal_air_c, thermal_sky_c, up)), 0.55);
        } else if (cls > 0.15 && cls <= 0.37) {
            // The road under a car: shaded asphalt, warmed by a running engine.
            th = th_surface(0.0, 0.0, 0.7, 1.0 + 4.0 * v_heat);
        }
        TH_OUT(th);
    }
}
"""

const PERSON_SHADER := """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"

// 0..1 of the crowd out: fewer people at night.
uniform float crowd = 1.0;

// INSTANCE_CUSTOM: x shirt (packed rgb), y walk length (m, 0 standing),
// z speed (m/s), w phase (0..1).
varying vec3 v_col;
varying float v_skin;

vec3 unpack_rgb(float f) {
    float v = floor(f + 0.5);
    return vec3(floor(v / 65536.0), mod(floor(v / 256.0), 256.0), mod(v, 256.0)) / 255.0;
}

vec3 to_linear(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}

void vertex() {
    vec4 cu = INSTANCE_CUSTOM;
    v_col = to_linear(mix(unpack_rgb(cu.x), COLOR.rgb, COLOR.a));
    v_skin = step(1.45, VERTEX.y);
    float walking = step(0.5, cu.y);
    float phase = cu.w;
    if (phase > crowd) {
        VERTEX = vec3(0.0);
    } else {
        float cyc = TIME * 7.0 + phase * 62.8;
        float tag = UV.x;
        if (walking > 0.5 && tag != 0.0) {
            float side = sign(tag);
            float pivot = abs(tag) > 1.5 ? 1.42 : 0.86;
            float amp = abs(tag) > 1.5 ? -0.35 : 0.45;
            float a = sin(cyc) * amp * side;
            vec3 p = VERTEX - vec3(0.0, pivot, 0.0);
            VERTEX = vec3(p.x, p.y * cos(a) - p.z * sin(a), p.y * sin(a) + p.z * cos(a)) + vec3(0.0, pivot, 0.0);
        }
        if (walking > 0.5) {
            // Along the walk, from its start; out of sight for the half metre
            // at each end, where the walk wraps round (at a corner).
            float s = mod(phase * 977.0 + TIME * cu.z, cu.y);
            float k = smoothstep(0.0, 0.6, s) * (1.0 - smoothstep(cu.y - 0.6, cu.y, s));
            VERTEX *= k;
            VERTEX.y += abs(sin(cyc)) * 0.03;
            VERTEX.z += s;
        }
    }
}

void fragment() {
    ALBEDO = v_col;
    ROUGHNESS = 0.9;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Skin ~33 C, clothes a few degrees cooler, warmed a little by the sun.
        vec2 th = th_surface(0.5, 5.0, 0.6, mix(5.5, 8.0, v_skin));
        TH_OUT(th);
    }
}
"""
