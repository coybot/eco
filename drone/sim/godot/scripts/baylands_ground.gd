## The ground of Baylands Park and the 5 x 4.8 km around it, from measured
## data. Replaces env_baylands' base terrain (static build(env) -> true).
##
## SHAPE. A clipmap on the GPU (shaders/baylands_ground.gdshader): 7 rings of
## 192 x 192 cells around each camera that draws it, the finest 0.5 m posts,
## the coarsest 32 m, so the 2020 lidar's levee crests, ditches, berms and
## the park's mounds are there under a quad at 2-30 m while the far ground
## costs little. One draw for the whole ground. The height is
## env.ground_height() exactly (the same 2 m bilinear, by texelFetch) plus,
## within ~85 m of the camera, the 0.5 m lidar ground's difference from it
## (tools/bake_baylands_ground.py, step height), clamped so the drawn ground
## never stands more than 0.28 m above the collision ground: an aircraft on
## a levee's edge is never hidden by more than that. Under water the floor
## shelves away from the bank (the lidar sees only the water's top).
##
## SURFACE. The NAIP 2024 photo (0.6 m, 20 May 2024) with its cast shadows
## and slope shading taken out by the bake (this scene's sun puts its own
## back), mapped to albedo; on it, per surface class (1 m, baked from NAIP,
## its near infrared, the lidar's intensity and OSM), the grain and normals
## of CC0 Poly Haven textures. Classes: soil, water, asphalt, concrete,
## decomposed-granite paths, levee gravel, lawn, synthetic turf (the Twin
## Creeks fields: green to the eye, dark to the near infrared), annual
## grass, mudflat, cracked mud, salt marsh, riprap, building, under-canopy.
##
## LOOK (env.look, followed on look_changed): season turns the annual grass
## green (0) or gold (1) per pixel from how cured the photo shows it; lawns
## barely; pickleweed reddens in autumn. wetness darkens bare ground and
## fills the lidar's low spots on paths and bare ground with puddles. tide
## wets the ground the tide has just left along the sloughs.
##
## NEAR GRASS. Two MultiMeshes of blade clumps (0.18 m spacing to 12 m, 0.45 m
## to 36 m) on the grass classes, re-centred on each camera by the shader,
## shrinking away from 25 to 45 m AGL (hidden altogether while no camera is
## below 50 m). Its height is not measured (the 2020 lidar's ground class lies
## on the sward's top): annual grass 0.1-0.6 m, pickleweed 0.15-0.3 m, lawn
## 6-9 cm. env_op "ground" {"grass": false} turns it off.
##
## DATA: assets/baylands/_ground/ (gitignored, the bake tool's) with
## committed fallbacks (ground_cls.png 2 m, the 1 m ortho.jpg): without
## _ground/ the ground is the 2 m terrain, the plain photo, 2 m classes.
extends Node3D

const DIR := "res://assets/baylands/"
const HI := "res://assets/baylands/_ground/"
const N := 192
const LEVELS := 7
const BASE_STEP := 0.5
const MATS := ["asphalt", "concrete", "dg", "gravel", "lawn", "drygrass", "soil", "mud", "cracked", "riprap", "litter"]
const BIG_AABB := AABB(Vector3(-20000, -500, -20000), Vector3(40000, 2000, 40000))

var env: Node3D
var mat: ShaderMaterial
var grass_mats: Array = []
## Every material that reads the shared height/look uniforms (the water's too).
var look_mats: Array = []
## Textures the water shares: hgt, hres, aux, tidal, alb; bounds; alb_rect.
var shared := {}
var vram_bytes := 0
var _hres_img: Image


static func build(e: Node3D) -> bool:
	var g = load("res://scripts/baylands_ground.gd").new()
	g.env = e
	g.name = "ground"
	e.add_child(g)
	g._build()
	return true


func _build() -> void:
	var t0 := Time.get_ticks_msec()
	_textures()
	mat = ShaderMaterial.new()
	mat.shader = load("res://shaders/baylands_ground.gdshader")
	mat.set_shader_parameter("base_step", BASE_STEP)
	mat.set_shader_parameter("n_half", float(N / 2))
	# The terrain posts' offset from a multiple of 2 x their spacing (cell
	# centres: 1 m for the 2 m terrain from -2500, -2400); z = -north.
	var tb: Rect2 = env.call("wide_bounds")
	var tr := float(env.get("_tres"))
	mat.set_shader_parameter("post_off", Vector2(fposmod(tb.position.x + tr * 0.5, 2.0 * tr),
		fposmod(-(tb.end.y - tr * 0.5), 2.0 * tr)))
	bind(mat)
	_bind_mats(mat)
	var mi := MeshInstance3D.new()
	mi.name = "clipmap"
	mi.mesh = _clipmap_mesh()
	mi.material_override = mat
	mi.custom_aabb = BIG_AABB
	# Levees are 3-5 m and the sun high: their shadows are not worth the
	# shadow passes of a camera-centred mesh (whose rings would centre on the
	# light's camera there). Buildings and trees still shadow the ground.
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	_grass()
	look_mats.append(mat)
	# env_op "ground" {"grass": bool, "clipmap": bool, "water": bool}: show
	# or hide the parts (A/B timing; grass off for a policy that should not
	# rely on it). --bl-hide=clipmap,grass,water at launch.
	env.call("register_op", "ground", _op)
	var hide := str(IpcServer._get_launch_arg("--bl-hide", "")).split(",")
	_op({"clipmap": not "clipmap" in hide, "grass": not "grass" in hide})
	env.connect("look_changed", _on_look)
	_on_look()
	print("[baylands_ground] clipmap %d levels x %d cells (%.1f-%.0f m posts), photo %s, 0.5 m detail %s, %d grass clumps, %.0f MB of ground textures, %d ms" %
		[LEVELS, N, BASE_STEP, BASE_STEP * pow(2.0, LEVELS - 1), shared.get("alb_src", "none"),
		 "yes" if _hres_img != null else "NO (run tools/bake_baylands_ground.py)", _grass_count,
		 vram_bytes / 1048576.0, Time.get_ticks_msec() - t0])


## The shared height/class/look uniforms onto a material (the water's too).
func bind(m: ShaderMaterial) -> void:
	for k in ["hgt", "hres", "aux", "tidal", "alb", "cls"]:
		if shared.get(k) != null:
			m.set_shader_parameter(k, shared[k])
	m.set_shader_parameter("bounds", shared["bounds"])
	m.set_shader_parameter("hgt_res", shared["hgt_res"])
	m.set_shader_parameter("has_hres", 1.0 if shared.get("hres") != null else 0.0)
	m.set_shader_parameter("has_aux", 1.0 if shared.get("aux") != null else 0.0)
	m.set_shader_parameter("has_alb", 1.0 if shared.get("alb") != null else 0.0)
	m.set_shader_parameter("alb_rect", shared["alb_rect"])


func _on_look() -> void:
	var lk: Dictionary = env.get("look")
	var w = lk.get("wind", [3.0, -2.0])
	var wind := Vector2(float(w[0]), float(w[1])) if w is Array and w.size() >= 2 else Vector2(3.0, -2.0)
	for m in look_mats:
		var sm := m as ShaderMaterial
		sm.set_shader_parameter("season", float(lk.get("season", 0.8)))
		sm.set_shader_parameter("wetness", float(lk.get("wetness", 0.0)))
		sm.set_shader_parameter("tide", float(lk.get("tide", 1.0)))
		sm.set_shader_parameter("wind", wind)


## The grass costs its vertices whatever the shader drops: hide it while no
## current camera is within 50 m of the ground (checked 4 times a second).
var _cams: Array = []
var _cam_t := 0.0
var _chk_t := 0.0
var _grass_on := true


func _process(delta: float) -> void:
	_chk_t -= delta
	if _chk_t > 0.0:
		return
	_chk_t = 0.25
	_cam_t -= 0.25
	if _cam_t <= 0.0:
		_cam_t = 3.0
		_cams = get_tree().root.find_children("*", "Camera3D", true, false)
	var low := _cams.is_empty()
	for c in _cams:
		if not is_instance_valid(c) or not (c as Camera3D).current:
			continue
		var p: Vector3 = (c as Camera3D).global_position
		if p.y - float(env.call("ground_height", p.x, -p.z)) < 50.0:
			low = true
			break
	for c in get_children():
		if c is MultiMeshInstance3D:
			c.visible = low and _grass_on


func _op(args: Dictionary) -> Dictionary:
	for c in get_children():
		if c.name == "clipmap" and args.has("clipmap"):
			c.visible = bool(args["clipmap"])
		if c is MultiMeshInstance3D and args.has("grass"):
			_grass_on = bool(args["grass"])
			c.visible = _grass_on
	var w := env.get_node_or_null("water")
	if w != null and args.has("water"):
		w.visible = bool(args["water"])
	return {"ok": true}


## The drawn ground's height at (e, n) near a camera (the 0.5 m lidar
## ground): env.ground_height() + the detail the clipmap adds within ~55 m.
## For props that must sit on what is drawn rather than on the 2 m terrain.
func detail_height(e: float, n: float) -> float:
	var g := float(env.call("ground_height", e, n))
	if _hres_img == null:
		return g
	var b: Rect2 = env.call("wide_bounds")
	var x := clampi(int((e - b.position.x) / 0.5), 0, _hres_img.get_width() - 1)
	var y := clampi(int((b.end.y - n) / 0.5), 0, _hres_img.get_height() - 1)
	return g + (_hres_img.get_pixel(x, y).r * 255.0 - 200.0) * 0.01


# ================================================================ textures

func _textures() -> void:
	var tb: Rect2 = env.call("wide_bounds")
	shared["bounds"] = Vector4(tb.position.x, tb.position.y, tb.end.x, tb.end.y)
	shared["hgt_res"] = float(env.get("_tres"))
	# env's own 2 m terrain, as float texels.
	var t: PackedFloat32Array = env.get("_t")
	var tw := int(env.get("_tw"))
	var th := int(env.get("_th"))
	if t.size() == tw * th and tw > 0:
		var img := Image.create_from_data(tw, th, false, Image.FORMAT_RF, t.to_byte_array())
		shared["hgt"] = ImageTexture.create_from_image(img)
		vram_bytes += img.get_data_size()
	_hres_img = _png(HI + "hres.png")
	if _hres_img != null:
		shared["hres"] = ImageTexture.create_from_image(_hres_img)
		vram_bytes += _hres_img.get_data_size()
	var aux := _png(HI + "aux.png")
	if aux != null:
		aux.convert(Image.FORMAT_RGBA8)
		shared["aux"] = ImageTexture.create_from_image(aux)
		vram_bytes += aux.get_data_size()
	var tid := _png(DIR + "tidal.png")
	if tid != null:
		shared["tidal"] = ImageTexture.create_from_image(tid)
	var c := _png(HI + "cls.png")
	if c == null:
		c = _png(DIR + "ground_cls.png")
	if c != null:
		c.convert(Image.FORMAT_R8)
		shared["cls"] = ImageTexture.create_from_image(c)
		vram_bytes += c.get_data_size()
	# The photo: the de-shadowed 0.6 m albedo (BC7, cached as .dds beside
	# it), else the committed 1 m ortho.
	var om: Dictionary = _json(DIR + "ortho.json")
	var r: Rect2 = tb
	if om.has("bounds_enu"):
		var bb: Array = om["bounds_enu"]
		r = Rect2(float(bb[0]), float(bb[1]), float(bb[2]) - float(bb[0]), float(bb[3]) - float(bb[1]))
	shared["alb_rect"] = Vector4(r.position.x, r.position.y, r.end.x, r.end.y)
	var a := _cached_bc7(HI + "albedo.jpg")
	shared["alb_src"] = "de-shadowed NAIP 0.6 m"
	if a == null:
		a = _cached_bc7(DIR + "ortho.jpg")
		shared["alb_src"] = "NAIP 1 m (committed)"
	if a != null:
		shared["alb"] = ImageTexture.create_from_image(a)
		vram_bytes += a.get_data_size()


func _bind_mats(m: ShaderMaterial) -> void:
	var meta: Dictionary = _json(DIR + "ground_tex.json")
	var cols: Array[Image] = []
	var nrms: Array[Image] = []
	var sizes := PackedFloat32Array()
	var means := PackedVector3Array()
	for nm in MATS:
		var img := _jpg(DIR + "ground_tex/%s.jpg" % nm)
		var nimg := _jpg(DIR + "ground_tex/%s_n.jpg" % nm)
		if img == null or nimg == null:
			push_warning("[baylands_ground] ground_tex/%s missing: photo only" % nm)
			return
		img.convert(Image.FORMAT_RGBA8)
		nimg.convert(Image.FORMAT_RGBA8)
		var acc := Vector3.ZERO
		var n := 0
		for y in range(0, img.get_height(), 8):
			for x in range(0, img.get_width(), 8):
				var cc := img.get_pixel(x, y).srgb_to_linear()
				acc += Vector3(cc.r, cc.g, cc.b)
				n += 1
		means.append(acc / float(n))
		img.generate_mipmaps()
		nimg.generate_mipmaps()
		cols.append(img)
		nrms.append(nimg)
		vram_bytes += img.get_data_size() + nimg.get_data_size()
		sizes.append(float(meta.get("textures", {}).get(nm, {}).get("size_m", 2.0)))
	var ca := Texture2DArray.new()
	ca.create_from_images(cols)
	var na := Texture2DArray.new()
	na.create_from_images(nrms)
	m.set_shader_parameter("mats", ca)
	m.set_shader_parameter("mats_n", na)
	m.set_shader_parameter("mat_size", sizes)
	m.set_shader_parameter("mat_mean", means)
	m.set_shader_parameter("has_mats", 1.0)


func _png(path: String) -> Image:
	if not FileAccess.file_exists(path):
		return null
	var img := Image.new()
	return img if img.load_png_from_buffer(FileAccess.get_file_as_bytes(path)) == OK else null


func _jpg(path: String) -> Image:
	if not FileAccess.file_exists(path):
		return null
	var img := Image.new()
	return img if img.load_jpg_from_buffer(FileAccess.get_file_as_bytes(path)) == OK else null


## A photo mipmapped and BC7-compressed, cached as .dds beside it (reading
## the .dds is a copy; compressing 67 M pixels takes a while, once). BC1's
## 4 x 4 blocks showed as a 2.4 m lattice on the ground at nadir.
func _cached_bc7(path: String) -> Image:
	if not FileAccess.file_exists(path):
		return null
	var dds := path.get_basename() + ".dds"
	if FileAccess.file_exists(dds) and FileAccess.get_modified_time(dds) >= FileAccess.get_modified_time(path):
		var d := Image.new()
		if d.load_dds_from_buffer(FileAccess.get_file_as_bytes(dds)) == OK:
			return d
	var img := _jpg(path)
	if img == null:
		return null
	img.generate_mipmaps()
	var t0 := Time.get_ticks_msec()
	img.compress(Image.COMPRESS_BPTC, Image.COMPRESS_SOURCE_SRGB)
	print("[baylands_ground] compressed %s (BC7) in %d ms" % [path.get_file(), Time.get_ticks_msec() - t0])
	if path.begins_with(HI):
		img.save_dds(ProjectSettings.globalize_path(dds))
	return img


func _json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var d = JSON.parse_string(FileAccess.get_file_as_string(path))
	return d if d is Dictionary else {}


# ================================================================== meshes

## LEVELS grids of N x N cells in one mesh: UV = the vertex's (i, j) in
## [-N/2, N/2] (j grows south, with Godot's z), UV2.x = its level. Levels
## past the first leave out the cells always inside the finer level.
## Cells split along the diagonal through the centre's quadrant (the
## "radial" split), so no triangle of a level has all three corners on the
## finer level's edge.
func _clipmap_mesh() -> ArrayMesh:
	var uv := PackedVector2Array()
	var uv2 := PackedVector2Array()
	var idx := PackedInt32Array()
	var side := N + 1
	var h := N / 2
	var q := N / 4
	for lvl in LEVELS:
		var base := uv.size()
		for j in side:
			for i in side:
				uv.append(Vector2(i - h, j - h))
				uv2.append(Vector2(lvl, 0.0))
		for j in N:
			for i in N:
				var ii := i - h
				var jj := j - h
				if lvl > 0 and ii >= -q + 1 and ii <= q - 1 and jj >= -q + 1 and jj <= q - 1:
					continue
				var a := base + j * side + i
				var b := a + 1
				var c := a + side
				var d := c + 1
				if (ii + 0.5) * (jj + 0.5) > 0.0:
					idx.append_array(PackedInt32Array([a, b, d, a, d, c]))
				else:
					idx.append_array(PackedInt32Array([a, b, c, b, d, c]))
	var pos := PackedVector3Array()
	pos.resize(uv.size())
	var nrm := PackedVector3Array()
	nrm.resize(uv.size())
	nrm.fill(Vector3.UP)
	var arr := []
	arr.resize(Mesh.ARRAY_MAX)
	arr[Mesh.ARRAY_VERTEX] = pos
	arr[Mesh.ARRAY_NORMAL] = nrm
	arr[Mesh.ARRAY_TEX_UV] = uv
	arr[Mesh.ARRAY_TEX_UV2] = uv2
	arr[Mesh.ARRAY_INDEX] = idx
	var m := ArrayMesh.new()
	m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
	m.custom_aabb = BIG_AABB
	return m


# =================================================================== grass

var _grass_count := 0


## Two rings of clumps: [spacing, half-size, blades, segments, fade in, fade out].
const GRASS_RINGS := [[0.18, 12.6, 6, 2, 0.0, 0.0, 9.0, 12.0], [0.45, 36.45, 4, 1, 9.0, 12.0, 28.0, 36.0]]


func _grass() -> void:
	if shared.get("cls") == null or shared.get("aux") == null:
		return
	var sh: Shader = load("res://shaders/baylands_grass.gdshader")
	for r in GRASS_RINGS:
		var sp: float = r[0]
		var half: float = r[1]
		var gm := ShaderMaterial.new()
		gm.shader = sh
		bind(gm)
		gm.set_shader_parameter("spacing", sp)
		gm.set_shader_parameter("fade_in0", r[4])
		gm.set_shader_parameter("fade_in1", r[5])
		gm.set_shader_parameter("fade_out0", r[6])
		gm.set_shader_parameter("fade_out1", r[7])
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = _clump(int(r[2]), int(r[3]))
		var k := int(round(half / sp))
		var inner: float = r[4] - 1.0
		var xf: Array[Transform3D] = []
		for j in range(-k, k + 1):
			for i in range(-k, k + 1):
				if inner > 0.0 and absf(i * sp) < inner and absf(j * sp) < inner:
					continue
				xf.append(Transform3D(Basis(), Vector3(i * sp, 0.0, j * sp)))
		mm.instance_count = xf.size()
		for n in xf.size():
			mm.set_instance_transform(n, xf[n])
		mm.custom_aabb = BIG_AABB
		var mmi := MultiMeshInstance3D.new()
		mmi.name = "grass_%.2f" % sp
		mmi.multimesh = mm
		mmi.material_override = gm
		mmi.custom_aabb = BIG_AABB
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mmi)
		grass_mats.append(gm)
		look_mats.append(gm)
		_grass_count += xf.size()


## A clump of `nb` blades, each a tapered, curving strip of `seg` segments;
## x, z within a unit radius, y 0..1 (the shader scales both).
func _clump(nb: int, seg: int) -> ArrayMesh:
	var pos := PackedVector3Array()
	var nrm := PackedVector3Array()
	var idx := PackedInt32Array()
	var rng := RandomNumberGenerator.new()
	rng.seed = 11 + nb
	for b in nb:
		var ang := TAU * (float(b) + rng.randf() * 0.6) / float(nb)
		var rad := rng.randf_range(0.1, 0.55)
		var root := Vector2(cos(ang), sin(ang)) * rad
		var lean := Vector2(cos(ang), sin(ang)) * rng.randf_range(0.3, 0.9)
		var facing := ang + PI * 0.5 + rng.randf_range(-0.5, 0.5)
		var side := Vector2(cos(facing), sin(facing))
		var hb := rng.randf_range(0.7, 1.0)
		var width := 0.035
		var first := pos.size()
		for s in seg + 1:
			var t := float(s) / float(seg)
			var cxy := root + lean * t * t
			var wv := width * (1.0 - t * 0.85)
			var y := t * hb
			var n3 := Vector3(-side.y, 0.35, side.x).normalized()
			pos.append(Vector3(cxy.x - side.x * wv, y, cxy.y - side.y * wv))
			pos.append(Vector3(cxy.x + side.x * wv, y, cxy.y + side.y * wv))
			nrm.append(n3)
			nrm.append(n3)
		for s in seg:
			var a := first + s * 2
			idx.append_array(PackedInt32Array([a, a + 1, a + 2, a + 1, a + 3, a + 2]))
	var arr := []
	arr.resize(Mesh.ARRAY_MAX)
	arr[Mesh.ARRAY_VERTEX] = pos
	arr[Mesh.ARRAY_NORMAL] = nrm
	arr[Mesh.ARRAY_INDEX] = idx
	var m := ArrayMesh.new()
	m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
	return m
