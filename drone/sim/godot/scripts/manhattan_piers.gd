## The piers: NYC Planimetric Hydrography Structures, feat_code 2800 ("decks
## supported by posts extending into the water"; piers.json from
## tools/fetch_manhattan_planimetric.py), as decks at street level standing on
## piles over the river.
##
## The shoreline field had every pier carved out of the land
## (tools/bake_manhattan_shore.py), because the OSM coastline wraps about a
## quarter of the big ones as if they were solid ground; so the ground and
## the bulkheads stop at the bulkhead line behind each pier and this draws
## the deck. Many Hudson piers carry buildings already in buildings.json
## (Chelsea Piers, Pier 40, Pier 57): those boxes stand on y = 0, which is
## the deck's top, so they now stand on something.
##
## Deck tops are painted from the same NAIP tile as the ground around them
## (the real pier surface: parking, lawns, sheds' roofs), falling back to
## concrete where the photo shows water (a deck mapped wider than the photo
## shows it, or the photo's lean). Sides are a concrete fascia; underneath is
## dark. Piles: a row 0.6 m in from the edge every 4.5 m and a 9 m grid under
## the rest, as MultiMeshes per 1000 m tile, gone past 600 m.
##
## Solid: one StaticBody3D on collision layer 1 (LAYER_STRUCTURE), a trimesh
## per deck, so the aircraft strike sweep hits them. env.is_water() answers
## false on a deck (manhattan_shore.pier_at) and ground_height street level.
extends Node3D

const LAYER_STRUCTURE := 1
const DECK_BOTTOM := -0.8
const EDGE_PILE_STEP := 4.5
const GRID_PILE_STEP := 9.0
const PILE_R := 0.22

var env: Node3D
var count := 0


func _ready() -> void:
	name = "piers"
	var ground := get_parent()
	env = ground.get_parent() as Node3D
	var shore: RefCounted = env.call("shore")
	if shore == null or not bool(shore.get("ok")):
		return
	var t0 := Time.get_ticks_msec()
	var piers: Array = shore.get("piers")
	var bounds: Rect2 = shore.get("far_rect")
	bounds.position -= env.call("data_offset")
	var sea_y := float(env.get("SEA_Y"))
	var body := StaticBody3D.new()
	body.name = "pier_colliders"
	body.collision_layer = LAYER_STRUCTURE
	body.collision_mask = 0
	add_child(body)
	var decks := {}        # [photo source, 3 km chunk] -> SurfaceTool
	var photos := {}       # same key -> photo dict (manhattan_ground.photo_at)
	var piles := {}        # 1 km tile -> Array[Transform3D]
	var tile := float(ground.get("tile_m"))
	var off: Vector2 = env.call("data_offset")
	_building_decks(shore)
	for ring: PackedVector2Array in piers:
		if not bounds.has_point(ring[0]):
			continue
		var poly := ring
		if _area(poly) < 0.0:
			poly = ring.duplicate()
			poly.reverse()
		var tris := Geometry2D.triangulate_polygon(poly)
		if tris.is_empty():
			poly = Geometry2D.convex_hull(poly)
			poly.remove_at(poly.size() - 1)
			if _area(poly) < 0.0:
				poly.reverse()
			tris = Geometry2D.triangulate_polygon(poly)
			if tris.is_empty():
				continue
		var c := Vector2.ZERO
		for v in poly:
			c += v
		c /= poly.size()
		var d := c + off
		var tk := Vector2i(int(floor(d.x / tile)), int(floor(d.y / tile)))
		var ph: Dictionary = ground.call("photo_at", c)
		# One draw per photo source per 3 km: decks in the same tile array
		# share a material and carry their layer per vertex.
		var src := str(ph.get("level", "none"))
		if src in ["far", "ortho"]:
			src += str((ph["rect"] as Rect2).position)
		var key := [src, Vector2i(floori(tk.x / 3.0), floori(tk.y / 3.0))]
		if not decks.has(key):
			var st := SurfaceTool.new()
			st.begin(Mesh.PRIMITIVE_TRIANGLES)
			decks[key] = st
			photos[key] = ph
		if not piles.has(tk):
			piles[tk] = [] as Array[Transform3D]
		var faces := _deck(decks[key], poly, tris, ph)
		var shape := ConcavePolygonShape3D.new()
		shape.set_faces(faces)
		var col := CollisionShape3D.new()
		col.shape = shape
		body.add_child(col)
		_piles(poly, piles[tk], sea_y)
		count += 1
	var pile_mesh := CylinderMesh.new()
	pile_mesh.top_radius = PILE_R
	pile_mesh.bottom_radius = PILE_R
	pile_mesh.height = 1.0
	pile_mesh.radial_segments = 6
	pile_mesh.rings = 1
	pile_mesh.cap_top = false
	pile_mesh.cap_bottom = false
	var pile_mat := _pile_material(sea_y)
	var n_piles := 0
	for key in decks:
		var mi := MeshInstance3D.new()
		mi.name = "pier_deck_%s_%d_%d" % [str(key[0]).get_slice("(", 0), key[1].x, key[1].y]
		(decks[key] as SurfaceTool).index()
		mi.mesh = (decks[key] as SurfaceTool).commit()
		mi.material_override = _deck_material(photos[key], sea_y)
		# A deck 1 m above the river casts a shadow no one sees from where
		# its shadow pass would cost a draw per tile.
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mi.visibility_range_end = 5000.0
		mi.visibility_range_end_margin = 500.0
		add_child(mi)
	for key in piles:
		var list: Array = piles[key]
		if list.is_empty():
			continue
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = pile_mesh
		mm.instance_count = list.size()
		for i in list.size():
			mm.set_instance_transform(i, list[i])
		var mmi := MultiMeshInstance3D.new()
		mmi.name = "pier_piles_%d_%d" % [key.x, key.y]
		mmi.multimesh = mm
		mmi.material_override = pile_mat
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mmi.visibility_range_end = 600.0
		mmi.visibility_range_end_margin = 60.0
		add_child(mmi)
		n_piles += list.size()
	print("[manhattan_piers] %d decks in %d draws, %d piles in %d, %d ms" %
		[count, decks.size(), n_piles, piles.size(), Time.get_ticks_msec() - t0])


## Decks under buildings that stand over the river. buildings.json's boxes
## are axis-aligned bounding rects, and on the piers (Chelsea Piers, Pier 57,
## Pier 40) they overhang the mapped deck, so a facade came down into open
## water. Each building with any of its footprint over water (sampled every
## ~5 m) gets a deck of its own rect, registered with the shoreline as a
## pier so is_water() says ground there too. ~80 of Manhattan's 45k.
func _building_decks(shore: RefCounted) -> void:
	var rects: Array = env.get("_rects")
	var added := 0
	for r: Rect2 in rects:
		# Most are inland: the field's distance at the centre clears the
		# whole rect when it exceeds the half-diagonal (it saturates at 32 m).
		var c := r.get_center()
		var half := r.size.length() * 0.5
		var d0 := float(shore.call("distance", c.x, c.y))
		if d0 < -half or (d0 <= -31.0 and half < 31.0):
			continue
		var nx := clampi(int(r.size.x / 5.0) + 2, 2, 20)
		var ny := clampi(int(r.size.y / 5.0) + 2, 2, 20)
		var wet := false
		for ix in nx:
			for iy in ny:
				var p := r.position + r.size * Vector2((ix + 0.5) / nx, (iy + 0.5) / ny)
				if float(shore.call("distance", p.x, p.y)) > 0.0 and int(shore.call("pier_at", p.x, p.y)) < 0:
					wet = true
					break
			if wet:
				break
		if not wet:
			continue
		var g := r.grow(0.4)
		shore.call("add_pier", PackedVector2Array([g.position, Vector2(g.end.x, g.position.y), g.end,
			Vector2(g.position.x, g.end.y)]))
		added += 1
	if added > 0:
		print("[manhattan_piers] %d decks under buildings over the water" % added)


static func _area(p: PackedVector2Array) -> float:
	var a := 0.0
	for i in p.size():
		var q := p[(i + 1) % p.size()]
		a += p[i].x * q.y - q.x * p[i].y
	return a * 0.5


static func _v(p: Vector2, y: float) -> Vector3:
	return Vector3(p.x, y, -p.y)


## Top, underside and fascia into `st`; returns the same triangles for the
## collider. poly is counter-clockwise in (e, n).
## UV2 = (photo layer, part: 0 top, 1 underside, 2 fascia); the top's UV
## is its place in the photo, the fascia's (metres along, height).
func _deck(st: SurfaceTool, poly: PackedVector2Array, tris: PackedInt32Array, ph: Dictionary) -> PackedVector3Array:
	var layer := float(ph.get("layer", 0))
	var r: Rect2 = ph.get("rect", Rect2(0, 0, 1, 1))
	var faces := PackedVector3Array()
	var top := float(env.get("LAND_Y"))
	# (e, n) CCW is CCW seen from above in Godot too (east right, north up),
	# and Godot's front faces wind clockwise: reversed for the top, in order
	# for the underside.
	for t in range(0, tris.size(), 3):
		var a := poly[tris[t]]
		var b := poly[tris[t + 1]]
		var c := poly[tris[t + 2]]
		for v in [a, c, b]:
			st.set_normal(Vector3.UP)
			st.set_uv(Vector2((v.x - r.position.x) / r.size.x, 1.0 - (v.y - r.position.y) / r.size.y))
			st.set_uv2(Vector2(layer, 0.0))
			st.add_vertex(_v(v, top))
			faces.append(_v(v, top))
		for v in [a, b, c]:
			st.set_normal(Vector3.DOWN)
			st.set_uv(Vector2.ZERO)
			st.set_uv2(Vector2(layer, 1.0))
			st.add_vertex(_v(v, DECK_BOTTOM))
			faces.append(_v(v, DECK_BOTTOM))
	var along := 0.0
	for i in poly.size():
		var p := poly[i]
		var q := poly[(i + 1) % poly.size()]
		var tt := (q - p).normalized()
		var nrm := Vector3(tt.y, 0.0, tt.x)     # outward (right of a CCW run), Godot frame
		var L := p.distance_to(q)
		var quad := [[_v(p, DECK_BOTTOM), Vector2(along, DECK_BOTTOM)], [_v(q, top), Vector2(along + L, top)],
			[_v(q, DECK_BOTTOM), Vector2(along + L, DECK_BOTTOM)],
			[_v(p, DECK_BOTTOM), Vector2(along, DECK_BOTTOM)], [_v(p, top), Vector2(along, top)],
			[_v(q, top), Vector2(along + L, top)]]
		for v in quad:
			st.set_normal(nrm)
			st.set_uv(v[1])
			st.set_uv2(Vector2(layer, 2.0))
			st.add_vertex(v[0])
			faces.append(v[0])
		along += L
	return faces


func _piles(poly: PackedVector2Array, out: Array, sea_y: float) -> void:
	var y0 := sea_y - 1.0
	var h := DECK_BOTTOM - y0
	var basis := Basis.IDENTITY.scaled(Vector3(1.0, h, 1.0))
	var y := y0 + h * 0.5
	var inset := Geometry2D.offset_polygon(poly, -0.6)
	if inset.is_empty():
		return
	var ring: PackedVector2Array = inset[0]
	var carry := 0.0
	for i in ring.size():
		var p := ring[i]
		var q := ring[(i + 1) % ring.size()]
		var L := p.distance_to(q)
		var s := carry
		while s < L:
			out.append(Transform3D(basis, _v(p.lerp(q, s / L), y)))
			s += EDGE_PILE_STEP
		carry = s - L
	var box := Rect2(poly[0], Vector2.ZERO)
	for v in poly:
		box = box.expand(v)
	var inner := Geometry2D.offset_polygon(poly, -3.0)
	if inner.is_empty():
		return
	var gx := ceilf(box.position.x / GRID_PILE_STEP) * GRID_PILE_STEP
	while gx < box.end.x:
		var gy := ceilf(box.position.y / GRID_PILE_STEP) * GRID_PILE_STEP
		while gy < box.end.y:
			var g := Vector2(gx, gy)
			if Geometry2D.is_point_in_polygon(g, inner[0]):
				out.append(Transform3D(basis, _v(g, y)))
			gy += GRID_PILE_STEP
		gx += GRID_PILE_STEP


func _deck_material(photo: Dictionary, sea_y: float) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"
uniform sampler2DArray photo_arr : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D photo : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform float use_array = 0.0;
uniform float has_photo = 0.0;
varying flat vec2 lk;
uniform float sea_y = -1.6;
varying vec3 w;
float h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; lk = UV2; }
void fragment() {
    vec3 conc = vec3(0.25, 0.245, 0.235) * (0.85 + 0.25 * vn(w.xz * 0.6));
    vec3 c;
    if (lk.y < 0.5) {
        c = conc;
        if (has_photo > 0.5) {
            vec2 uv = clamp(UV, vec2(0.0), vec2(1.0));
            vec3 ph = use_array > 0.5 ? texture(photo_arr, vec3(uv, lk.x)).rgb : texture(photo, uv).rgb;
            ph = clamp((ph - 0.04) * 1.12, 0.0, 1.0);
            float lum = dot(ph, vec3(0.3, 0.55, 0.15));
            // Photo water (dark, green-blue) is the deck mapped past where the
            // photo shows it: concrete there.
            float waterish = smoothstep(0.03, 0.0, ph.r - max(ph.g, ph.b) * 0.92) * smoothstep(0.3, 0.18, lum);
            // NAIP's pier tops are often blown-out white concrete; held to
            // the brightest the ground's own sidewalks reach.
            c = mix(min(ph * 0.62, vec3(0.26)), conc, waterish);
        }
    } else if (lk.y < 1.5) {
        c = vec3(0.05, 0.05, 0.045);
    } else {
        float y = UV.y;
        c = conc * (0.9 + 0.2 * vn(vec2(UV.x * 0.4, y * 3.0)));
        c *= 1.0 - 0.3 * smoothstep(0.0, -0.6, y);
    }
    ALBEDO = c;
    ROUGHNESS = 0.92;
    SPECULAR = 0.3;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.5, 18.0, 0.7, 0.0);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("sea_y", sea_y)
	if photo.has("array"):
		m.set_shader_parameter("photo_arr", photo["array"])
		m.set_shader_parameter("use_array", 1.0)
		m.set_shader_parameter("has_photo", 1.0)
	elif photo.has("texture"):
		m.set_shader_parameter("photo", photo["texture"])
		m.set_shader_parameter("has_photo", 1.0)
	return m


## Creosoted timber and concrete piles: dark, slimy green in the tidal band.
func _pile_material(sea_y: float) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_back;
uniform float sea_y = -1.6;
varying float wy;
void vertex() { wy = (MODEL_MATRIX * vec4(VERTEX, 1.0)).y; }
void fragment() {
    vec3 c = vec3(0.13, 0.11, 0.09);
    c = mix(c, vec3(0.05, 0.07, 0.04), 1.0 - smoothstep(sea_y + 0.2, sea_y + 0.8, wy));
    ALBEDO = c;
    ROUGHNESS = 0.85;
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("sea_y", sea_y)
	return m
