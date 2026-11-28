## Baylands' trees, shrubs and rushes, every one MEASURED from the 2020
## county lidar: where it stands, how tall it is, how far its crown reaches
## in eight directions and how high its crown starts. Eucalyptus windbreaks
## and groves, coast live oaks, Monterey pines and cypresses, redwood rows by
## the offices and apartments, fan and date palms in the business park's car
## parks, the street trees of Sunnyvale (London plane, Chinese pistache,
## Raywood ash), willows by the water, coyote brush on the levees, oleander
## along the freeways and tule at the pond edges.
##
## Data: assets/baylands/trees.dat + trees.json, baked by
## drone/sim/tools/bake_baylands_trees.py from the USGS 3DEP 2020 lidar
## (public domain), USDA NAIP 2024 colour + near-infrared (public domain) and
## OpenStreetMap (ODbL). Species are CLASSIFIED from crown shape, colour and
## context (no public species map covers the area); positions, heights and
## crowns are measured. Without the files this does nothing.
##
## SOLID: claim() adds every tree to the scene as obstacles (a crown prism
## from the measured crown base to the top over the measured 8-radius
## outline, plus a trunk prism), and every shrub as one prism, so the strike
## sweep and the router's occupancy see them. Rushes are soft and not solid.
##
## Drawn the way istanbul_nature.gd draws its trees (procedural crowns, crown
## shape per instance in the vertex shader, near / mid / far LODs), with each
## tree's own crown base and crown outline (an ellipse plus a lean toward the
## side the crown reaches furthest). The near crowns (2 mesh variants) and a
## dappled shadow-only proxy are camera-following sets, one draw call each;
## mid/far crowns and rushes are big chunks. Bare deciduous crowns show twigs.
## Palms are the CC BY models in assets/models near a camera, PALM heads past
## PALM_NEAR_M. env_op "nature" reports counts and per-viewport draw calls.
##
## SEASON follows env.look (env_baylands.gd):
##   season 0 (wet-season green, Jan-Apr) .. 1 (dry-season gold, Jul-Oct).
##   Deciduous trees (plane, pistache, ash, willow, sycamore, generic) are bare
##   at season 0.1 and below, leaf out to 0.3, full above. Optional look keys
##   override: "leaf" 0 bare .. 1 full, "autumn" 0..1 (autumn colour; the
##   season scalar alone never shows it). Rushes go tan in the dry season and
##   stand brown in winter.
##
## Launch args: --trees 0 (no trees, and no tree solids), --tree-lod
## near,mid,far (m), --tree-shadows <m>|off (default 500).
extends Node3D

const DAT := "res://assets/baylands/trees.dat"
const META := "res://assets/baylands/trees.json"
const INC := "res://shaders/thermal.gdshaderinc"
const ModelKit = preload("res://scripts/model_kit.gd")

## Mid/far crowns and rushes in big chunks: draw calls are this scene's budget
## (every shadowed chunk is drawn again per cascade, and per viewport).
const MID_CHUNK_M := 1000.0
const FAR_CHUNK_M := 2500.0
const RUSH_CHUNK_M := 500.0
const LOD_NEAR_M := 260.0
const LOD_MID_M := 900.0
const FAR_END_M := 5000.0
const MID_HALF_DIAG := 708.0
const FAR_HALF_DIAG := 1768.0
## Palms within this of a camera are drawn as their models (one MultiMesh per
## species, refilled as the cameras move: the models have 12 surfaces between
## them, so chunking them would cost 12 draw calls a chunk).
const PALM_NEAR_M := 350.0
const RUSH_NEAR_M := 300.0
const NEAR_VARIANTS := 2
## Trees and shrubs cast shadows to here (m; --tree-shadows).
const SHADOW_M := 500.0

## PALM (5) is a palm's head on its bare trunk: past PALM_NEAR_M a palm is
## drawn by the crown LODs, inside it by its model.
enum { ROUND, VASE, COLUMNAR, CONICAL, UMBRELLA, PALM, MOUND, OPEN }

## Per species: crown family, summer colour, autumn colour (sRGB), deciduous,
## airy crown, trunk radius = a * height + b (m).
const SPECIES := {
	"eucalyptus": [OPEN, Color8(100, 114, 88), Color8(100, 114, 88), false, true, 0.022, 0.12],
	"live_oak": [ROUND, Color8(50, 66, 36), Color8(50, 66, 36), false, false, 0.03, 0.10],
	"monterey_pine": [UMBRELLA, Color8(42, 60, 38), Color8(42, 60, 38), false, false, 0.022, 0.08],
	"monterey_cypress": [UMBRELLA, Color8(38, 56, 40), Color8(38, 56, 40), false, false, 0.025, 0.10],
	"redwood": [CONICAL, Color8(46, 64, 38), Color8(46, 64, 38), false, false, 0.03, 0.10],
	"fan_palm": [PALM, Color8(92, 104, 66), Color8(92, 104, 66), false, false, 0.0, 0.25],
	"date_palm": [PALM, Color8(82, 102, 58), Color8(82, 102, 58), false, false, 0.0, 0.40],
	"london_plane": [ROUND, Color8(86, 110, 58), Color8(156, 124, 62), true, false, 0.022, 0.08],
	"pistache": [ROUND, Color8(78, 104, 50), Color8(196, 80, 42), true, false, 0.018, 0.06],
	"raywood_ash": [VASE, Color8(76, 102, 50), Color8(118, 50, 60), true, false, 0.018, 0.06],
	"willow": [ROUND, Color8(108, 128, 60), Color8(170, 158, 70), true, true, 0.025, 0.08],
	"evergreen": [ROUND, Color8(56, 78, 42), Color8(56, 78, 42), false, false, 0.02, 0.06],
	"deciduous": [ROUND, Color8(80, 104, 52), Color8(176, 140, 60), true, false, 0.018, 0.06],
	"sycamore": [ROUND, Color8(90, 112, 62), Color8(152, 122, 70), true, true, 0.022, 0.08],
}
## Shrubs (trees.json "shrubs"): family, colour, solid. Tule shrubs are drawn as rush stands.
const SHRUBS := {
	"coyote_brush": [MOUND, Color8(64, 82, 44)],
	"oleander": [ROUND, Color8(50, 72, 42)],
	"landscape": [MOUND, Color8(58, 82, 46)],
	"tule": [MOUND, Color8(84, 96, 52)],
}
const TWIG := Color8(96, 88, 78)
const PALMS := {"fan_palm": "palm_fan", "date_palm": "palm_date"}

## Parsed once by claim() (which runs before this node exists), reused by _ready().
static var _data: Dictionary = {}

var env: Node3D
var count := 0
var n_trees := 0
var n_shrubs := 0
var _rows: Array = []            # per instance kind: [family, summer, autumn, deciduous, airy]
var _pts := PackedVector2Array()
var _ys := PackedFloat32Array()
var _basis := PackedFloat32Array()
var _kind := PackedByteArray()   # index into _rows
var _cbf := PackedFloat32Array()
var _shade := PackedFloat32Array()
var _custom := PackedFloat32Array()
var _mms: Array = []             # [MultiMesh, idx, centre]
var _rush_pts := PackedVector2Array()
var _rush_ys := PackedFloat32Array()
var _rush_hw := PackedFloat32Array()   # height, width per clump
var _mid_mesh_ref: ArrayMesh
var _mid_solid: ShaderMaterial
var _mid_holes: ShaderMaterial
var _any_thin := false
var _look_key := ""
var _shadow_chunks: Array = []
var _shadow_t := 0.0
var _cams: Array[Camera3D] = []
var _cams_t := 0.0
var _rush_mats: Array = []


# =================================================================== data

static func _enabled() -> bool:
	return IpcServer._get_launch_arg("--trees", "1") != "0"


## Reads trees.dat (layout in tools/bake_baylands_trees.py) into plain arrays.
static func _load() -> Dictionary:
	if not _data.is_empty():
		return _data
	if not FileAccess.file_exists(DAT) or not FileAccess.file_exists(META):
		push_warning("[baylands_nature] no trees: run drone/sim/tools/bake_baylands_trees.py")
		return {}
	var meta: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(META))
	var b := FileAccess.get_file_as_bytes(DAT)
	if b.size() < 20 or b.slice(0, 4).get_string_from_ascii() != "BLT1":
		push_error("[baylands_nature] bad trees.dat")
		return {}
	var nt := b.decode_u32(4)
	var ns := b.decode_u32(8)
	var nr := b.decode_u32(12)
	var q := b.decode_float(16)
	var org: Array = meta.get("origin", [-2500.0, -2400.0])
	var o := Vector2(float(org[0]), float(org[1]))
	var d := {"meta": meta, "t_p": PackedVector2Array(), "t_h": PackedFloat32Array(),
		"t_cb": PackedFloat32Array(), "t_r": PackedFloat32Array(), "t_sp": PackedByteArray(),
		"t_shade": PackedByteArray(), "s_p": PackedVector2Array(), "s_h": PackedFloat32Array(),
		"s_r": PackedFloat32Array(), "s_sp": PackedByteArray(), "r_p": PackedVector2Array(),
		"r_h": PackedFloat32Array(), "r_d": PackedFloat32Array()}
	var at := 20
	var tp: PackedVector2Array = d["t_p"]
	var th: PackedFloat32Array = d["t_h"]
	var tcb: PackedFloat32Array = d["t_cb"]
	var tr: PackedFloat32Array = d["t_r"]
	var tsp: PackedByteArray = d["t_sp"]
	var tsh: PackedByteArray = d["t_shade"]
	tp.resize(nt); th.resize(nt); tcb.resize(nt); tr.resize(nt * 8); tsp.resize(nt); tsh.resize(nt)
	for i in nt:
		tp[i] = o + Vector2(b.decode_u16(at), b.decode_u16(at + 2)) * q
		th[i] = b.decode_u16(at + 4) * 0.01
		tcb[i] = b.decode_u16(at + 6) * 0.01
		for k in 8:
			tr[i * 8 + k] = b[at + 8 + k] * 0.1
		tsp[i] = b[at + 16]
		tsh[i] = b[at + 17]
		at += 18
	var sp: PackedVector2Array = d["s_p"]
	var sh: PackedFloat32Array = d["s_h"]
	var sr: PackedFloat32Array = d["s_r"]
	var ssp: PackedByteArray = d["s_sp"]
	sp.resize(ns); sh.resize(ns); sr.resize(ns); ssp.resize(ns)
	for i in ns:
		sp[i] = o + Vector2(b.decode_u16(at), b.decode_u16(at + 2)) * q
		sh[i] = b[at + 4] * 0.05
		sr[i] = b[at + 5] * 0.05
		ssp[i] = b[at + 6]
		at += 8
	var rp: PackedVector2Array = d["r_p"]
	var rh: PackedFloat32Array = d["r_h"]
	var rd: PackedFloat32Array = d["r_d"]
	rp.resize(nr); rh.resize(nr); rd.resize(nr)
	for i in nr:
		rp[i] = o + Vector2(b.decode_u16(at), b.decode_u16(at + 2)) * q
		rh[i] = b[at + 4] * 0.05
		rd[i] = b[at + 5] / 255.0
		at += 6
	d["t_p"] = tp; d["t_h"] = th; d["t_cb"] = tcb; d["t_r"] = tr; d["t_sp"] = tsp; d["t_shade"] = tsh
	d["s_p"] = sp; d["s_h"] = sh; d["s_r"] = sr; d["s_sp"] = ssp
	d["r_p"] = rp; d["r_h"] = rh; d["r_d"] = rd
	_data = d
	return d


static func _trunk_r(row: Array, h: float) -> float:
	return maxf(0.08, float(row[5]) * h + float(row[6]))


## Every tree and shrub becomes solid before the router's occupancy is cut:
## a crown prism (measured crown base to top, the measured 8-radius outline)
## and a trunk prism; a crown that reaches within 1 m of the ground is one
## prism to the ground. Shrubs are one prism.
static func claim(env) -> bool:
	if not _enabled():
		return false
	var t0 := Time.get_ticks_msec()
	var d := _load()
	if d.is_empty():
		return false
	var names: Array = (d["meta"] as Dictionary).get("species", [])
	var snames: Array = (d["meta"] as Dictionary).get("shrubs", [])
	var tp: PackedVector2Array = d["t_p"]
	var th: PackedFloat32Array = d["t_h"]
	var tcb: PackedFloat32Array = d["t_cb"]
	var tr: PackedFloat32Array = d["t_r"]
	var tsp: PackedByteArray = d["t_sp"]
	var solids := 0
	var dirs: Array[Vector2] = []
	for k in 8:
		dirs.append(Vector2(cos(k * PI / 4.0), sin(k * PI / 4.0)))
	var hexes: Array[Vector2] = []
	for k in 6:
		hexes.append(Vector2(cos(k * TAU / 6.0), sin(k * TAU / 6.0)))
	var ring := PackedVector2Array()
	ring.resize(8)
	var tring := PackedVector2Array()
	tring.resize(6)
	for i in tp.size():
		var p := tp[i]
		var g := float(env.ground_height(p.x, p.y))
		var h := th[i]
		var cb := tcb[i]
		for k in 8:
			ring[k] = p + dirs[k] * tr[i * 8 + k]
		var sname: String = names[tsp[i]] if tsp[i] < names.size() else "evergreen"
		if cb < 1.0:
			env.add_obstacle(ring, g - 0.3, g + h)
			solids += 1
			continue
		env.add_obstacle(ring, g + cb, g + h)
		var r := _trunk_r(SPECIES.get(sname, SPECIES["evergreen"]), h)
		for k in 6:
			tring[k] = p + hexes[k] * r
		env.add_obstacle(tring, g - 0.3, g + cb + 0.3)
		solids += 2
	var sp: PackedVector2Array = d["s_p"]
	var sh: PackedFloat32Array = d["s_h"]
	var sr: PackedFloat32Array = d["s_r"]
	var ssp: PackedByteArray = d["s_sp"]
	for i in sp.size():
		if ssp[i] < snames.size() and snames[ssp[i]] == "tule":
			continue
		var p := sp[i]
		var g := float(env.ground_height(p.x, p.y))
		for k in 8:
			ring[k] = p + dirs[k] * sr[i]
		env.add_obstacle(ring, g - 0.3, g + sh[i])
		solids += 1
	d["solids"] = solids
	print("[baylands_nature] claim: %d trees, %d shrubs -> %d solids in %d ms"
		% [tp.size(), sp.size(), solids, Time.get_ticks_msec() - t0])
	return true


# ================================================================== build

func _ready() -> void:
	env = get_parent()
	name = "baylands_nature"
	if env.has_method("register_op"):
		env.register_op("nature", _op)
	if not _enabled():
		return
	var d := _load()
	if d.is_empty():
		return
	_build.call_deferred(d)
	if env.has_signal("look_changed"):
		env.look_changed.connect(_on_look)


## env_op "nature": counts, solids, the camera-following sets' sizes and each
## viewport's own draw calls / primitives (the global perf line sums them).
func _op(_args: Dictionary) -> Dictionary:
	var vps: Array = []
	for vp in [get_tree().root] + get_tree().root.find_children("*", "SubViewport", true, false):
		var v := vp as Viewport
		var row := {"name": str(v.get_path()).get_file()}
		for t in [["vis", Viewport.RENDER_INFO_TYPE_VISIBLE], ["shadow", Viewport.RENDER_INFO_TYPE_SHADOW]]:
			row[t[0] + "_draws"] = v.get_render_info(t[1], Viewport.RENDER_INFO_DRAW_CALLS_IN_FRAME)
			row[t[0] + "_prims"] = v.get_render_info(t[1], Viewport.RENDER_INFO_PRIMITIVES_IN_FRAME)
		vps.append(row)
	var mine := 0
	for c in get_children():
		if c is GeometryInstance3D:
			mine += 1
	return {"ok": true, "trees": n_trees, "shrubs": n_shrubs, "rush_clumps": _rush_pts.size(),
		"solids": int(_data.get("solids", 0)), "nodes": mine, "viewports": vps,
		"near_set": _near_sets.map(func(m): return m.instance_count),
		"shadow_set": _shadow_set.instance_count if _shadow_set != null else 0}


func _build(d: Dictionary) -> void:
	var t0 := Time.get_ticks_msec()
	var lods := IpcServer._get_launch_arg("--tree-lod", "").split(",")
	var near_m := float(lods[0]) if lods.size() > 0 and lods[0] != "" else LOD_NEAR_M
	var mid_m := float(lods[1]) if lods.size() > 1 else LOD_MID_M
	var far_m := float(lods[2]) if lods.size() > 2 else FAR_END_M
	var sh_arg := IpcServer._get_launch_arg("--tree-shadows", str(SHADOW_M))
	var shadows := 0.0 if sh_arg == "off" else float(sh_arg)
	var palms := _instances(d)
	_rushes(d)
	_look_key = _look_signature()
	_fill_custom()
	_build_trees(near_m, mid_m, far_m, shadows)
	var n_palms := _build_palms(palms)
	_build_rushes()
	print("[baylands_nature] %d trees (%d palms) + %d shrubs + %d rush clumps in %d ms; LOD %.0f/%.0f/%.0f m, shadows to %.0f m, look %s"
		% [n_trees, n_palms, n_shrubs, _rush_pts.size(), Time.get_ticks_msec() - t0,
		near_m, mid_m, far_m, shadows, str(_look())])


func _process(delta: float) -> void:
	_refresh_cams(delta)
	_shadow_t -= delta
	if _shadow_t <= 0.0 and not _shadow_chunks.is_empty():
		_shadow_t = 0.25
		_update_shadows()
	_palm_t -= delta
	if _palm_t <= 0.0 and not _palm_sets.is_empty():
		_palm_t = 0.3
		_update_palms()
	if count > 0 and not _set_bins.is_empty():
		_update_sets()


func _look() -> Dictionary:
	var l = env.get("look")
	return l if l is Dictionary else {"season": 0.8}


func _look_signature() -> String:
	var l := _look()
	return "%.3f/%s/%s" % [float(l.get("season", 0.8)), str(l.get("leaf", "")), str(l.get("autumn", ""))]


func _on_look() -> void:
	var k := _look_signature()
	if k == _look_key or count == 0:
		_rush_colour()
		return
	_look_key = k
	_fill_custom()
	_pick_mid()
	for e: Array in _mms:
		(e[0] as MultiMesh).buffer = _buffer(e[1], e[2])
	_set_dirty = true
	_rush_colour()


## Leaf level (0 bare .. 1 full) and autumn colour (0..1) of the deciduous
## trees for the current look.
func _leaf_now() -> Vector2:
	var l := _look()
	var s := float(l.get("season", 0.8))
	var leaf := clampf((s - 0.1) / 0.2, 0.0, 1.0)
	var autumn := 0.0
	if l.has("leaf"):
		leaf = clampf(float(l["leaf"]), 0.0, 1.0)
	if l.has("autumn"):
		autumn = clampf(float(l["autumn"]), 0.0, 1.0)
	return Vector2(leaf, autumn)


## The per-instance lists: trees (palms apart, returned for the models) and
## shrubs, each with its basis (crown ellipse + lean), crown-base fraction
## and colour row.
func _instances(d: Dictionary) -> Dictionary:
	var names: Array = (d["meta"] as Dictionary).get("species", [])
	var snames: Array = (d["meta"] as Dictionary).get("shrubs", [])
	var kind_of := {}
	for s in names:
		var row: Array = SPECIES.get(s, SPECIES["evergreen"])
		kind_of["t:" + str(s)] = _rows.size()
		_rows.append([row[0], row[1], row[2], row[3], row[4]])
	for s in snames:
		var row: Array = SHRUBS.get(s, SHRUBS["landscape"])
		kind_of["s:" + str(s)] = _rows.size()
		_rows.append([row[0], row[1], row[1], false, false])
	# Pines under 11 m are still conical; draw them so.
	var young_pine := _rows.size()
	_rows.append([CONICAL, SPECIES["monterey_pine"][1], SPECIES["monterey_pine"][1], false, false])
	var palms := {}
	var tp: PackedVector2Array = d["t_p"]
	var th: PackedFloat32Array = d["t_h"]
	var tcb: PackedFloat32Array = d["t_cb"]
	var tr: PackedFloat32Array = d["t_r"]
	var tsp: PackedByteArray = d["t_sp"]
	var tsh: PackedByteArray = d["t_shade"]
	for i in tp.size():
		var p := tp[i]
		var sname: String = names[tsp[i]] if tsp[i] < names.size() else "evergreen"
		var y := float(env.ground_height(p.x, p.y)) - 0.15
		var h := th[i]
		if PALMS.has(sname):
			if not palms.has(sname):
				palms[sname] = []
			var rm := 0.0
			for k in 8:
				rm += tr[i * 8 + k]
			(palms[sname] as Array).append([p, y, h, _hash(p, 4.0) * TAU, rm / 8.0])
		var r8 := tr.slice(i * 8, i * 8 + 8)
		var kind: int = kind_of["t:" + sname]
		if sname == "monterey_pine" and h < 11.0:
			kind = young_pine
		_add(p, y, h, r8, tcb[i] / maxf(h, 0.5), kind, float(tsh[i]) / 128.0)
	n_trees = _pts.size()
	var sp: PackedVector2Array = d["s_p"]
	var sh: PackedFloat32Array = d["s_h"]
	var sr: PackedFloat32Array = d["s_r"]
	var ssp: PackedByteArray = d["s_sp"]
	var r8 := PackedFloat32Array()
	r8.resize(8)
	for i in sp.size():
		var sname: String = snames[ssp[i]] if ssp[i] < snames.size() else "landscape"
		var p := sp[i]
		if sname == "tule":
			continue
		r8.fill(sr[i])
		_add(p, float(env.ground_height(p.x, p.y)) - 0.1, sh[i], r8, 0.0, kind_of["s:" + sname], 1.0)
	n_shrubs = _pts.size() - n_trees
	count = _pts.size()
	return palms


## One instance: the crown is an ellipse of the E-W and N-S extents, its
## centre shifted (a lean of the whole tree, trunk too) toward the side the
## crown reaches further; the mesh spins freely inside the ellipse.
func _add(p: Vector2, y: float, h: float, r8: PackedFloat32Array, cbf: float, kind: int, shade: float) -> void:
	var rx := (r8[0] + r8[4]) * 0.5 * 0.7 + (r8[1] + r8[3] + r8[5] + r8[7]) * 0.25 * 0.3
	var rz := (r8[2] + r8[6]) * 0.5 * 0.7 + (r8[1] + r8[3] + r8[5] + r8[7]) * 0.25 * 0.3
	var off := Vector2(r8[0] - r8[4], r8[2] - r8[6]) * 0.5
	off += Vector2(r8[1] - r8[5] - r8[3] + r8[7], r8[1] - r8[5] + r8[3] - r8[7]) * 0.5 * 0.7071 * 0.5
	var a := _hash(p, 1.0) * TAU
	var c := cos(a)
	var s := sin(a)
	var wx := 2.0 * rx * 1.08
	var wz := 2.0 * rz * 1.08
	# The crown's middle is ~(1 + cbf) / 2 of the way up: shear so it lands on `off`.
	# Most of the measured asymmetry is the crown's, not the trunk's: lean by
	# 60 % of it, at most 7 degrees.
	var ln := off * 0.6 / clampf((1.0 + cbf) * 0.5, 0.4, 1.0)
	ln = ln.limit_length(0.12 * h)
	# Rows of the basis (x, y, z) -> world (east, up, -north): R(a) then scale.
	# 0.96: the lumps reach a little past the unit crown; the top stays at the measured height.
	_basis.append_array(PackedFloat32Array([c * wx, ln.x, s * wx, 0.0, h * 0.96, 0.0, -s * wz, -ln.y, c * wz]))
	_pts.append(p)
	_ys.append(y)
	_kind.append(kind)
	_cbf.append(clampf(cbf, 0.0, 0.92))
	_shade.append(clampf(shade, 0.7, 1.35))


static func _hash(p: Vector2, k: float) -> float:
	return fposmod(sin(p.x * 12.9898 + p.y * 78.233 + k * 37.719) * 43758.5453, 1.0)


## Instance custom data: linear colour and (family * 4 + leaf level + crown
## base) / 32, the crown base fraction riding in the fractional part.
func _fill_custom() -> void:
	_custom.resize(count * 4)
	_any_thin = false
	var la := _leaf_now()
	var season := float(_look().get("season", 0.8))
	for i in count:
		var row: Array = _rows[_kind[i]]
		var fam: int = row[0]
		var c: Color = row[1]
		var lvl := 3
		var p := _pts[i]
		var jit := _hash(p, 7.0)
		if row[3]:
			# Each tree a little ahead or behind the others.
			var lf := clampf(la.x + (jit - 0.5) * 0.3, 0.0, 1.0)
			if la.x >= 1.0:
				lf = 1.0
			elif la.x <= 0.0:
				lf = 0.0
			lvl = clampi(int(round(lf * 3.0)), 0, 3)
			c = c.lerp(row[2], la.y * (0.7 + 0.3 * jit))
			# Fresh pale green as the leaves come out.
			c = c.lerp(Color8(132, 160, 72), (1.0 - smoothstep(0.25, 0.45, season)) * 0.45 * float(lvl > 0))
			if lvl < 3:
				c = c.lerp(TWIG, [0.85, 0.5, 0.2][lvl])
		else:
			# Evergreens dull and dust over in the dry season.
			c = c.lerp(Color8(92, 96, 70), smoothstep(0.6, 1.0, season) * 0.18)
		if lvl < 2:
			_any_thin = true
		var t := c.srgb_to_linear()
		var g := _shade[i] * (0.9 + 0.2 * _hash(p, 2.0))
		_custom[i * 4] = clampf(t.r * g, 0.0, 1.0)
		_custom[i * 4 + 1] = clampf(t.g * g, 0.0, 1.0)
		_custom[i * 4 + 2] = clampf(t.b * g, 0.0, 1.0)
		_custom[i * 4 + 3] = (float(fam * 4 + lvl) + 0.02 + _cbf[i] * 0.96) / 32.0


# ------------------------------------------------------------------ trees

func _build_trees(near_m: float, mid_m: float, far_m: float, shadows: float) -> void:
	if count == 0:
		return
	var leaf_tex := _leaf_texture()
	_twig_tex = _twig_texture()
	var near_meshes: Array[ArrayMesh] = []
	for v in NEAR_VARIANTS:
		var nm := _near_mesh(11 + v * 17, leaf_tex)
		for sidx in nm.get_surface_count():
			var sm := nm.surface_get_material(sidx) as ShaderMaterial
			sm.set_shader_parameter("lod_begin", 0.0)
			sm.set_shader_parameter("lod_end", near_m)
		near_meshes.append(nm)
	var mid_mesh := _mid_mesh()
	var far_mesh := _far_mesh()
	var shadow_mesh := _shadow_mesh()
	_mid_mesh_ref = mid_mesh
	_mid_solid = mid_mesh.surface_get_material(0) as ShaderMaterial
	_mid_holes = _shader(MID_SHADER.replace("shader_type spatial;", "shader_type spatial;\n#define LEAF_HOLES"))
	for m: ShaderMaterial in [_mid_solid, _mid_holes]:
		m.set_shader_parameter("lod_begin", near_m)
		m.set_shader_parameter("lod_end", mid_m)
		m.set_shader_parameter("palm_begin", PALM_NEAR_M)
	_pick_mid()
	var fm_mat := far_mesh.surface_get_material(0) as ShaderMaterial
	fm_mat.set_shader_parameter("lod_begin", mid_m)
	fm_mat.set_shader_parameter("lod_end", far_m)
	# Near and shadow: camera-following sets (see _update_sets), one draw each.
	_near_r = near_m + SET_MARGIN_M
	_shadow_r = shadows + SET_MARGIN_M if shadows > 0.0 else 0.0
	for i in count:
		var k := Vector2i(floori(_pts[i].x / SET_BIN_M), floori(_pts[i].y / SET_BIN_M))
		if not _set_bins.has(k):
			_set_bins[k] = PackedInt32Array()
		var arr: PackedInt32Array = _set_bins[k]
		arr.append(i)
		_set_bins[k] = arr
	_variant.resize(count)
	for i in count:
		_variant[i] = 255 if int(_rows[_kind[i]][0]) == PALM else int(_hash(_pts[i], 9.0) * NEAR_VARIANTS) % NEAR_VARIANTS
	for v in NEAR_VARIANTS:
		_near_sets.append(_set_mmi("trees_near%d" % v, near_meshes[v], GeometryInstance3D.SHADOW_CASTING_SETTING_OFF))
	if _shadow_r > 0.0:
		_shadow_set = _set_mmi("trees_shadow", shadow_mesh, GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY)
	var all := PackedInt32Array()
	all.resize(count)
	for i in count:
		all[i] = i
	var mid_chunks := _bucket(all, MID_CHUNK_M)
	for key: Vector2i in mid_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * MID_CHUNK_M
		_mmi("trees_mid_%d_%d" % [key.x, key.y], mid_mesh, mid_chunks[key], centre,
			0.0, mid_m + MID_HALF_DIAG + 20.0)
	var far_chunks := _bucket(all, FAR_CHUNK_M)
	for key: Vector2i in far_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * FAR_CHUNK_M
		_mmi("trees_far_%d_%d" % [key.x, key.y], far_mesh, far_chunks[key], centre,
			maxf(0.0, mid_m - FAR_HALF_DIAG - 20.0), far_m + FAR_HALF_DIAG)


## The near crowns and the shadow proxies follow the cameras: every tree
## within reach of any current camera, refilled when one has moved
## SET_STEP_M (or the look changed). A shadow pass has no camera distance to
## cull by, so a chunked proxy would cast every tree of every chunk in range.
const SET_BIN_M := 250.0
const SET_MARGIN_M := 60.0
const SET_STEP_M := 20.0
var _set_bins: Dictionary = {}
var _variant := PackedByteArray()
var _near_sets: Array[MultiMesh] = []
var _shadow_set: MultiMesh
var _near_r := 0.0
var _shadow_r := 0.0
var _set_eyes: Array[Vector3] = []
var _set_dirty := true


func _set_mmi(name_: String, mesh: Mesh, shadow: int) -> MultiMesh:
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	mm.mesh = mesh
	var mi := MultiMeshInstance3D.new()
	mi.name = name_
	mi.multimesh = mm
	mi.cast_shadow = shadow
	mi.extra_cull_margin = 16384.0
	add_child(mi)
	return mm


func _update_sets() -> void:
	var eyes: Array[Vector3] = []
	for c in _cams:
		if is_instance_valid(c) and c.current and c.is_inside_tree():
			eyes.append(c.global_position)
	var moved := _set_dirty or eyes.size() != _set_eyes.size()
	if not moved:
		for k in eyes.size():
			if eyes[k].distance_to(_set_eyes[k]) > SET_STEP_M:
				moved = true
				break
	if not moved:
		return
	_set_dirty = false
	_set_eyes = eyes
	var reach := maxf(_near_r, _shadow_r)
	# Each tree's distance to its nearest camera.
	var reach2 := reach * reach
	var best := {}
	for eye in eyes:
		var ep := Vector2(eye.x, -eye.z)
		for gx in range(floori((ep.x - reach) / SET_BIN_M), floori((ep.x + reach) / SET_BIN_M) + 1):
			for gy in range(floori((ep.y - reach) / SET_BIN_M), floori((ep.y + reach) / SET_BIN_M) + 1):
				var key := Vector2i(gx, gy)
				if not _set_bins.has(key):
					continue
				for i in (_set_bins[key] as PackedInt32Array):
					var d2 := ep.distance_squared_to(_pts[i])
					if d2 < reach2 and d2 < float(best.get(i, INF)):
						best[i] = d2
	var near: Array = []
	for v in NEAR_VARIANTS:
		near.append([])
	var shadow := PackedInt32Array()
	var nr2 := _near_r * _near_r
	var sr2 := _shadow_r * _shadow_r
	for i: int in best:
		var d2: float = best[i]
		if d2 < sr2:
			shadow.append(i)
		if d2 < nr2 and _variant[i] != 255:
			(near[_variant[i]] as Array).append(i)
	for v in NEAR_VARIANTS:
		_fill(_near_sets[v], PackedInt32Array(near[v]))
	if _shadow_set != null:
		_fill(_shadow_set, shadow)


func _fill(mm: MultiMesh, idx: PackedInt32Array) -> void:
	mm.instance_count = idx.size()
	if idx.size() > 0:
		mm.buffer = _buffer(idx, Vector2.ZERO)


func _pick_mid() -> void:
	if _mid_mesh_ref != null:
		_mid_mesh_ref.surface_set_material(0, _mid_holes if _any_thin else _mid_solid)


func _bucket(idx: PackedInt32Array, size: float) -> Dictionary:
	var lists := {}
	for i in idx:
		var k := Vector2i(floori(_pts[i].x / size), floori(_pts[i].y / size))
		if not lists.has(k):
			lists[k] = []
		(lists[k] as Array).append(i)
	var out := {}
	for k: Vector2i in lists:
		out[k] = PackedInt32Array(lists[k])
	return out


func _buffer(idx: PackedInt32Array, centre: Vector2) -> PackedFloat32Array:
	var buf := PackedFloat32Array()
	buf.resize(idx.size() * 16)
	var o := 0
	for i in idx:
		var k := i * 9
		var p := _pts[i] - centre
		buf[o] = _basis[k]; buf[o + 1] = _basis[k + 1]; buf[o + 2] = _basis[k + 2]; buf[o + 3] = p.x
		buf[o + 4] = _basis[k + 3]; buf[o + 5] = _basis[k + 4]; buf[o + 6] = _basis[k + 5]; buf[o + 7] = _ys[i]
		buf[o + 8] = _basis[k + 6]; buf[o + 9] = _basis[k + 7]; buf[o + 10] = _basis[k + 8]; buf[o + 11] = -p.y
		buf[o + 12] = _custom[i * 4]; buf[o + 13] = _custom[i * 4 + 1]
		buf[o + 14] = _custom[i * 4 + 2]; buf[o + 15] = _custom[i * 4 + 3]
		o += 16
	return buf


func _mmi(name_: String, mesh: Mesh, idx: PackedInt32Array, centre: Vector2,
		begin: float, end: float) -> MultiMeshInstance3D:
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	mm.mesh = mesh
	mm.instance_count = idx.size()
	mm.buffer = _buffer(idx, centre)
	_mms.append([mm, idx, centre])
	var mi := MultiMeshInstance3D.new()
	mi.name = name_
	mi.multimesh = mm
	mi.position = Vector3(centre.x, 0.0, -centre.y)
	mi.visibility_range_begin = begin
	mi.visibility_range_end = end
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	return mi


func _refresh_cams(delta: float) -> void:
	_cams_t -= delta
	if _cams_t <= 0.0:
		_cams_t = 2.0
		_cams.clear()
		for c in get_tree().root.find_children("*", "Camera3D", true, false):
			_cams.append(c as Camera3D)


func _update_shadows() -> void:
	var eyes: Array[Vector3] = []
	for c in _cams:
		if is_instance_valid(c) and c.current and c.is_inside_tree():
			eyes.append(c.global_position)
	for e: Array in _shadow_chunks:
		var mi := e[0] as GeometryInstance3D
		var at: Vector3 = e[1]
		var reach: float = e[2]
		var on := false
		for eye in eyes:
			if Vector2(eye.x - at.x, eye.z - at.z).length() < reach:
				on = true
				break
		var want := GeometryInstance3D.SHADOW_CASTING_SETTING_ON if on else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		if mi.cast_shadow != want:
			mi.cast_shadow = want


# ------------------------------------------------------------------ palms

## Palms: the CC BY models (assets/models/CREDITS.md), scaled to each
## palm's measured height. One MultiMesh per species holds the palms within
## PALM_NEAR_M of a camera, refilled every 0.3 s; farther palms are the crown
## LODs' PALM heads, and every palm's shadow is the shadow proxy's.
var _palm_sets: Array = []      # [MultiMesh, list, lo, part xf, scale]
var _palm_t := 0.0


func _build_palms(palms: Dictionary) -> int:
	var n := 0
	for sname: String in palms:
		var root := ModelKit.instance(PALMS[sname])
		if root == null:
			continue
		var parts: Array = []
		var lo := INF
		var hi := -INF
		for mi in root.find_children("*", "MeshInstance3D", true, false):
			var m := mi as MeshInstance3D
			var xf := Transform3D()
			var node: Node = m
			while node != null and node != root:
				if node is Node3D:
					xf = (node as Node3D).transform * xf
				node = node.get_parent()
			var mesh := m.mesh.duplicate() as Mesh
			for k in mesh.get_surface_count():
				var mat := m.get_surface_override_material(k)
				if mat == null and m.material_override != null:
					mat = m.material_override
				if mat != null:
					mesh.surface_set_material(k, mat)
			var ab := xf * m.get_aabb()
			lo = minf(lo, ab.position.y)
			hi = maxf(hi, ab.end.y)
			parts.append([mesh, xf])
		root.free()
		if parts.is_empty() or hi <= lo:
			continue
		var list: Array = palms[sname]
		for part: Array in parts:
			var mm := MultiMesh.new()
			mm.transform_format = MultiMesh.TRANSFORM_3D
			mm.mesh = part[0]
			mm.instance_count = list.size()
			mm.visible_instance_count = 0
			var mi := MultiMeshInstance3D.new()
			mi.name = "palms_%s" % sname
			mi.multimesh = mm
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			mi.extra_cull_margin = 16384.0
			add_child(mi)
			_palm_sets.append([mm, list, lo, part[1], 1.0 / (hi - lo)])
		n += list.size()
		print("[baylands_nature] %s: %d palms, %d parts, model %.1f m tall" % [sname, list.size(), parts.size(), hi - lo])
	return n


func _update_palms() -> void:
	var eyes: Array[Vector3] = []
	for c in _cams:
		if is_instance_valid(c) and c.current and c.is_inside_tree():
			eyes.append(c.global_position)
	var r2 := PALM_NEAR_M * PALM_NEAR_M
	for set_: Array in _palm_sets:
		var mm: MultiMesh = set_[0]
		var list: Array = set_[1]
		var lo: float = set_[2]
		var pxf: Transform3D = set_[3]
		var k := 0
		for e: Array in list:
			var p: Vector2 = e[0]
			var near := false
			for eye in eyes:
				if (eye.x - p.x) * (eye.x - p.x) + (eye.z + p.y) * (eye.z + p.y) < r2:
					near = true
					break
			if not near:
				continue
			var s: float = float(e[2]) * float(set_[4])
			var b := Basis(Vector3.UP, float(e[3])).scaled(Vector3(s, s, s))
			mm.set_instance_transform(k, Transform3D(b, Vector3(p.x, float(e[1]) - lo * s, -p.y)) * pxf)
			k += 1
		mm.visible_instance_count = k


# ------------------------------------------------------------------ rushes

## Tule and cattail stands: the baked clumps plus the shrub-sized tule
## patches spread into clumps over their measured footprint.
func _rushes(d: Dictionary) -> void:
	var rp: PackedVector2Array = d["r_p"]
	var rh: PackedFloat32Array = d["r_h"]
	for i in rp.size():
		var p := rp[i]
		_rush_pts.append(p)
		_rush_ys.append(float(env.ground_height(p.x, p.y)) - 0.05)
		_rush_hw.append(rh[i])
		_rush_hw.append(1.3 + 0.9 * _hash(p, 3.0))
	var snames: Array = (d["meta"] as Dictionary).get("shrubs", [])
	var sp: PackedVector2Array = d["s_p"]
	var sh: PackedFloat32Array = d["s_h"]
	var sr: PackedFloat32Array = d["s_r"]
	var ssp: PackedByteArray = d["s_sp"]
	for i in sp.size():
		if ssp[i] >= snames.size() or snames[ssp[i]] != "tule":
			continue
		var n := clampi(int(sr[i] * sr[i] * PI / 1.5), 1, 24)
		for k in n:
			var a := _hash(sp[i], float(k) * 3.1) * TAU
			var r := sqrt(_hash(sp[i], float(k) * 5.7)) * sr[i]
			var p := sp[i] + Vector2(cos(a), sin(a)) * r
			_rush_pts.append(p)
			_rush_ys.append(float(env.ground_height(p.x, p.y)) - 0.05)
			_rush_hw.append(sh[i] * (0.75 + 0.25 * _hash(p, 2.0)))
			_rush_hw.append(1.3 + 0.9 * _hash(p, 3.0))


func _build_rushes() -> void:
	if _rush_pts.is_empty():
		return
	var near := _rush_mesh()
	var mat := near.surface_get_material(0) as ShaderMaterial
	mat.set_shader_parameter("lod_begin", 0.0)
	mat.set_shader_parameter("lod_end", RUSH_NEAR_M)
	_rush_mats = [mat]
	_rush_colour()
	var lists := {}
	for i in _rush_pts.size():
		var k := Vector2i(floori(_rush_pts[i].x / RUSH_CHUNK_M), floori(_rush_pts[i].y / RUSH_CHUNK_M))
		if not lists.has(k):
			lists[k] = []
		(lists[k] as Array).append(i)
	for key: Vector2i in lists:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * RUSH_CHUNK_M
		var idx := PackedInt32Array(lists[key])
		var buf := PackedFloat32Array()
		buf.resize(idx.size() * 16)
		var o := 0
		for i in idx:
			var p := _rush_pts[i] - centre
			var a := _hash(_rush_pts[i], 8.0) * TAU
			var w := _rush_hw[i * 2 + 1]
			var h := _rush_hw[i * 2]
			var c := cos(a) * w
			var s := sin(a) * w
			buf[o] = c; buf[o + 1] = 0.0; buf[o + 2] = s; buf[o + 3] = p.x
			buf[o + 4] = 0.0; buf[o + 5] = h; buf[o + 6] = 0.0; buf[o + 7] = _rush_ys[i]
			buf[o + 8] = -s; buf[o + 9] = 0.0; buf[o + 10] = c; buf[o + 11] = -p.y
			buf[o + 12] = _hash(_rush_pts[i], 5.0); buf[o + 13] = 0.0; buf[o + 14] = 0.0; buf[o + 15] = 0.0
			o += 16
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = near
		mm.instance_count = idx.size()
		mm.buffer = buf
		var mi := MultiMeshInstance3D.new()
		mi.name = "rush_%d_%d" % [key.x, key.y]
		mi.multimesh = mm
		mi.position = Vector3(centre.x, 0.0, -centre.y)
		mi.visibility_range_end = RUSH_NEAR_M + RUSH_CHUNK_M * 0.71
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)
		_shadow_chunks.append([mi, mi.position, 150.0 + RUSH_CHUNK_M * 0.71])


## Rush colour for the season: green in spring, tan through the dry season,
## standing brown in winter.
func _rush_colour() -> void:
	if _rush_mats.is_empty():
		return
	var s := float(_look().get("season", 0.8))
	# Tule stays mostly green through the summer, its tips browning; cattail
	# leaves go tan in autumn and stand brown through the winter.
	var dry := 0.45 * smoothstep(0.5, 1.0, s) + 0.6 * (1.0 - smoothstep(0.0, 0.18, s))
	var green := Color8(66, 90, 44)
	var tan := Color8(140, 124, 80)
	var c := green.lerp(tan, clampf(dry, 0.0, 1.0)).srgb_to_linear()
	for m: ShaderMaterial in _rush_mats:
		m.set_shader_parameter("tint", Vector3(c.r, c.g, c.b))
		m.set_shader_parameter("dry", clampf(dry, 0.0, 1.0))


## A clump of 48 tapered blades, 1 unit tall, about 1 unit across.
func _rush_mesh() -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = 31
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for k in 48:
		var a := rng.randf() * TAU
		var r := sqrt(rng.randf()) * 0.32
		var base := Vector3(cos(a) * r, 0.0, sin(a) * r)
		var lean := Vector3(cos(a), 0.0, sin(a)) * rng.randf_range(0.05, 0.3)
		var tall := rng.randf_range(0.55, 1.0)
		var w := rng.randf_range(0.022, 0.05)
		var side := Vector3(-sin(a + 1.2), 0.0, cos(a + 1.2)) * w
		var top := base + lean * tall + Vector3(0.0, tall, 0.0)
		var mid := base + lean * tall * 0.35 + Vector3(0.0, tall * 0.5, 0.0)
		var n := Vector3(cos(a), 0.6, sin(a)).normalized()
		var quad := [[base - side, Vector2(0, 1)], [base + side, Vector2(1, 1)],
			[mid + side * 0.7, Vector2(1, 0.5)], [mid - side * 0.7, Vector2(0, 0.5)],
			[top, Vector2(0.5, 0.0)]]
		for tri: Array in [[0, 2, 1], [0, 3, 2], [3, 4, 2]]:
			for j: int in tri:
				st.set_normal(n)
				st.set_uv(quad[j][1])
				st.add_vertex(quad[j][0])
	var mesh := st.commit()
	var sh := Shader.new()
	sh.code = RUSH_SHADER % [INC]
	var m := ShaderMaterial.new()
	m.shader = sh
	mesh.surface_set_material(0, m)
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.4, 1.6))
	return mesh


const RUSH_SHADER := """
shader_type spatial;
render_mode cull_disabled, diffuse_lambert_wrap;
#include "%s"
uniform float lod_begin = 0.0;
uniform float lod_end = 220.0;
uniform vec3 tint = vec3(0.08, 0.11, 0.03);
uniform float dry = 0.0;
varying float v_seed;
varying float v_y;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    v_seed = INSTANCE_CUSTOM.r;
    v_y = VERTEX.y;
    float ph = v_seed * 6.2832;
    float g = sin(TIME * 1.1 + ph + base.x * 0.05) * 0.6 + sin(TIME * 2.9 + ph * 1.7) * 0.3;
    VERTEX.xz += vec2(0.8, 0.45) * g * 0.06 * VERTEX.y * VERTEX.y;
    float d = distance(CAMERA_POSITION_WORLD, base);
    if (PROJECTION_MATRIX[3][3] < 0.5 && (d < lod_begin || d >= lod_end)) { VERTEX = vec3(0.0); }
}
void fragment() {
    if (!FRONT_FACING) { NORMAL = -NORMAL; }
    // Base darker and greener (wet, shaded), tips paler; dry stems go tan from the top.
    float up = clamp(v_y, 0.0, 1.0);
    vec3 c = tint * mix(0.55, 1.15, up) * (0.85 + 0.3 * v_seed);
    c = mix(c, c * vec3(1.25, 1.1, 0.85), dry * up);
    ALBEDO = c;
    ROUGHNESS = 0.85;
    SPECULAR = 0.2;
    BACKLIGHT = c * 0.4;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.5, 4.0, 0.6, -1.0);
        TH_OUT(th);
        BACKLIGHT = vec3(0.0);
    }
}
"""


# ------------------------------------------------------------------ shaders

## istanbul_nature's crown families (FLAME replaced by PALM, 5, a round head)
## plus MOUND (6, a shrub dome from the ground) and OPEN (7, a eucalyptus'
## tall, open, high-shouldered crown).
## The crown base is per tree (the fraction in the custom channel).
const COMMON := """
uniform float lod_begin = 0.0;
uniform float lod_end = 240.0;
uniform float wind_amp = 1.0;

float t_hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float vn(vec3 p) {
    vec3 i = floor(p); vec3 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    float n = dot(i, vec3(1.0, 57.0, 113.0));
    vec4 a = fract(sin(vec4(n, n + 1.0, n + 57.0, n + 58.0)) * 43758.5453);
    vec4 b = fract(sin(vec4(n + 113.0, n + 114.0, n + 170.0, n + 171.0)) * 43758.5453);
    vec4 m = mix(a, b, f.z);
    vec2 r = mix(m.xy, m.zw, f.y);
    return mix(r.x, r.y, f.x);
}

float t_code(float a) { return floor(a * 32.0); }
float t_fam(float a) { return floor(t_code(a) * 0.25); }
float t_leaf(float a) { return mod(t_code(a), 4.0) / 3.0; }
float t_cb(float a) { return clamp((fract(a * 32.0) - 0.02) / 0.96, 0.0, 0.92); }

// Crown half-width (0..1 of the widest) at height t (0 crown bottom .. 1 top).
float crown_profile(float t, float fam) {
    float e = sqrt(max(1.0 - (2.0 * t - 1.0) * (2.0 * t - 1.0), 0.0));
    if (fam < 0.5) return e;
    if (fam < 1.5) {
        float w = mix(0.28, 1.0, smoothstep(0.0, 0.72, t));
        float k = max(t - 0.72, 0.0) / 0.28;
        return w * sqrt(max(1.0 - k * k, 0.0));
    }
    if (fam < 2.5) return pow(e, 0.55);
    if (fam < 3.5) return pow(1.0 - t, 0.85) * sqrt(smoothstep(0.0, 0.18, t)) * 1.12;
    if (fam < 4.5) {                 // umbrella: flat underside, low dome on top
        float k = max(t - 0.35, 0.0) / 0.65;
        return sqrt(smoothstep(0.0, 0.3, t)) * sqrt(max(1.0 - k * k, 0.0));
    }
    if (fam < 5.5) return e;         // palm head
    if (fam < 6.5) {                 // mound: widest just above the ground
        float k = max(t - 0.15, 0.0) / 0.85;
        return sqrt(max(1.0 - k * k, 0.0)) * mix(0.8, 1.0, smoothstep(0.0, 0.15, t));
    }
    // open: narrow below, broad high shoulders, ragged rounded top
    float k = max(t - 0.62, 0.0) / 0.38;
    return mix(0.35, 1.0, smoothstep(0.0, 0.55, t)) * sqrt(max(1.0 - k * k, 0.0));
}
vec3 crown_centre(float cb) {
    return vec3(0.0, cb + (1.0 - cb) * 0.5, 0.0);
}
vec3 crown_map(vec4 a, float fam, float cb) {
    if (a.w < 0.5) return vec3(0.0, a.y * cb, 0.0);
    float m = length(a.xyz);
    vec3 d = m > 1e-4 ? a.xyz / m : vec3(0.0, 1.0, 0.0);
    float t = d.y * 0.5 + 0.5;
    float s = sqrt(max(1.0 - d.y * d.y, 1e-4));
    vec2 xz = d.xz / s * crown_profile(t, fam) * 0.5;
    vec3 c = crown_centre(cb);
    return c + (vec3(xz.x, cb + t * (1.0 - cb), xz.y) - c) * m;
}
bool lod_out(vec3 base, vec3 cam, mat4 proj) {
    if (proj[3][3] > 0.5) return false;
    float d = distance(cam, base);
    return d < lod_begin || d >= lod_end;
}
vec3 t_wind(vec3 p, vec3 base, float time) {
    float ph = t_hash(base.xz) * 6.2832;
    float g = sin(time * 0.8 + ph) * 0.65 + sin(time * 2.3 + ph * 1.7) * 0.25;
    return p + vec3(0.8, 0.0, 0.45) * g * 0.012 * wind_amp * p.y * p.y;
}
"""

const LEAF_SHADER := """
shader_type spatial;
render_mode cull_disabled, diffuse_lambert_wrap;
#include "%s"
%s
uniform sampler2D leaf_tex : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D twig_tex : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
varying vec3 v_tint;
varying float v_leaf;
varying float v_ao;
varying float v_shell;
varying float v_under;
varying vec3 v_obj;

void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float fam = t_fam(INSTANCE_CUSTOM.a);
    float cb = t_cb(INSTANCE_CUSTOM.a);
    v_leaf = t_leaf(INSTANCE_CUSTOM.a);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_shell = step(1.5, CUSTOM0.w);
    vec3 anchor = crown_map(CUSTOM0, fam, cb);
    vec3 centre = crown_centre(cb);
    vec3 sn = normalize((anchor - centre) * vec3(1.0, 1.6, 1.0) + vec3(0.0, 0.12, 0.0));
    v_under = smoothstep(-0.05, -0.55, sn.y);
    v_obj = anchor * vec3(7.0, 6.0, 7.0) + VERTEX * 6.0 + base * 0.05;
    NORMAL = normalize(mix(NORMAL, sn, 0.7));
    float depth = length(CUSTOM0.xyz);
    float t = clamp(CUSTOM0.y * 0.5 + 0.5, 0.0, 1.0);
    v_ao = mix(0.7, 1.0, smoothstep(0.2, 1.0, depth)) * mix(0.8, 1.08, t);
    vec3 p = t_wind(anchor + VERTEX, base, TIME);
    p += NORMAL * sin(TIME * 5.0 + dot(p, vec3(31.0, 17.0, 23.0))) * 0.004 * (1.0 - v_shell);
    VERTEX = p;
    // A thinning crown drops whole leaf cards (the anchor is shared by a card's corners).
    if (v_shell < 0.5 && t_hash(CUSTOM0.xz * 13.0 + CUSTOM0.y) > 0.65 + 0.35 * v_leaf) { VERTEX = vec3(0.0); }
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}

void fragment() {
    // Cards: leaf sprays in leaf, twigs when bare (blended as the leaves come).
    float leafy = smoothstep(0.15, 0.6, v_leaf);
    vec4 tx = mix(texture(twig_tex, UV), texture(leaf_tex, UV), leafy);
    bool front = FRONT_FACING;
    if (!front) { NORMAL = -NORMAL; }
    if (v_shell > 0.5) {
        // The shell: foliage tiled over it (three sprays across a lump), with
        // clumps in the colour and a bumped normal, not a flat facet.
        vec2 tuv = fract(UV * 3.0);
        vec4 t2 = mix(texture(twig_tex, tuv), texture(leaf_tex, tuv), leafy);
        float n0 = vn(v_obj * 2.0) * 0.5 + vn(v_obj * 5.0) * 0.3 + vn(v_obj * 11.0) * 0.2;
        vec3 g = vec3(vn(v_obj * 5.0 + vec3(0.3, 0.0, 0.0)), vn(v_obj * 5.0 + vec3(0.0, 0.3, 0.0)),
            vn(v_obj * 5.0 + vec3(0.0, 0.0, 0.3))) - vn(v_obj * 5.0);
        NORMAL = normalize(NORMAL - (VIEW_MATRIX * vec4(g * 1.6, 0.0)).xyz);
        vec3 fol = mix(vec3(0.62, 0.64, 0.55) * mix(0.4, 1.15, n0), t2.rgb * mix(0.7, 1.1, n0), t2.a);
        // Outside and above: covered in leaves. Inside or beneath: a lattice
        // of leaf sprays with the sky through the gaps, never a solid lid.
        float under = max(front ? 0.0 : 1.0, v_under);
        vec4 top = vec4(mix(fol, tx.rgb, step(0.5, tx.a) * 0.5), max(tx.a, t2.a));
        vec4 bot = vec4(t2.rgb * mix(0.6, 1.0, n0), t2.a * step(0.52, n0));
        tx = mix(top, bot, under);
    }
    float ndv = abs(dot(NORMAL, VIEW));
    float full = smoothstep(0.5, 1.0, v_leaf);
    // Seen from below or from inside, a crown is leaves against the sky, not
    // a solid lid.
    float solid = v_shell * smoothstep(0.2, 0.55, ndv) * full * (1.0 - 0.9 * max(front ? 0.0 : 1.0, v_under));
    ALPHA = max(tx.a * (v_shell > 0.5 ? mix(0.45, 1.0, v_leaf) : 1.0), solid);
    ALPHA_SCISSOR_THRESHOLD = 0.5;
    ALBEDO = v_tint * tx.rgb * 1.5 * v_ao * (v_shell > 0.5 ? 0.85 : 1.0) * mix(1.0, 1.3, v_under);
    ROUGHNESS = 0.8;
    SPECULAR = 0.15;
    BACKLIGHT = v_tint * 0.6 * v_leaf;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.45, 5.0, 0.55, -1.5);
        TH_OUT(th);
        BACKLIGHT = vec3(0.0);
    }
}
"""

const WOOD_SHADER := """
shader_type spatial;
render_mode cull_back;
#include "%s"
%s
varying float v_shade;
varying float v_fam;
varying vec3 v_obj;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float fam = t_fam(INSTANCE_CUSTOM.a);
    v_fam = fam;
    v_shade = 0.75 + 0.5 * t_hash(base.xz + 3.0);
    v_obj = VERTEX * 40.0 + CUSTOM0.xyz * 3.0;
    VERTEX = t_wind(crown_map(CUSTOM0, fam, t_cb(INSTANCE_CUSTOM.a)) + VERTEX, base, TIME);
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}
void fragment() {
    // Eucalyptus: pale, peeling cream-grey bark; redwood: fibrous red-brown;
    // the rest dark grey-brown.
    vec3 bark = vec3(0.035, 0.03, 0.025);
    if (v_fam > 6.5) {
        float patchy = fract(sin(dot(floor(v_obj.xy * 0.6), vec2(12.9, 78.2))) * 43758.5);
        bark = mix(vec3(0.30, 0.28, 0.24), vec3(0.16, 0.14, 0.11), step(0.65, patchy));
    } else if (v_fam > 2.5 && v_fam < 3.5) {
        bark = vec3(0.12, 0.05, 0.03);
    }
    ALBEDO = bark * v_shade;
    ROUGHNESS = 1.0;
    SPECULAR = 0.05;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.5, 8.0, 0.6, -0.5);
        TH_OUT(th);
    }
}
"""

const MID_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_lambert_wrap;
#include "%s"
%s
varying vec3 v_tint;
varying float v_ao;
varying vec3 v_obj;
varying float v_wood;
varying float v_leaf;
uniform float palm_begin = 350.0;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float fam = t_fam(INSTANCE_CUSTOM.a);
    float cb = t_cb(INSTANCE_CUSTOM.a);
    v_leaf = t_leaf(INSTANCE_CUSTOM.a);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_wood = 1.0 - CUSTOM0.w;
    vec3 anchor = crown_map(CUSTOM0, fam, cb);
    vec3 centre = crown_centre(cb);
    if (CUSTOM0.w > 0.5) {
        vec3 sn = normalize((anchor - centre) * vec3(1.0, 1.6, 1.0) + vec3(0.0, 0.1, 0.0));
        NORMAL = normalize(mix(NORMAL, sn, 0.5));
    }
    float t = clamp(CUSTOM0.y * 0.5 + 0.5, 0.0, 1.0);
    v_ao = mix(0.75, 1.05, t);
    v_obj = (anchor + VERTEX) * vec3(6.0, 5.0, 6.0) + base * 0.05;
    VERTEX = t_wind(anchor + VERTEX, base, TIME);
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
    // A palm is its model up close.
    if (fam > 4.5 && fam < 5.5 && PROJECTION_MATRIX[3][3] < 0.5
            && distance(CAMERA_POSITION_WORLD, base) < palm_begin) { VERTEX = vec3(0.0); }
}
void fragment() {
    float n = vn(v_obj) * 0.6 + vn(v_obj * 2.3) * 0.4;
#ifdef LEAF_HOLES
    float hole = vn(v_obj * 3.1 + 7.0);
    ALPHA = v_wood > 0.5 ? 1.0 : step((1.0 - v_leaf) * 0.75, hole);
    ALPHA_SCISSOR_THRESHOLD = 0.5;
#endif
#ifdef SHADOW_PROXY
    // Dappled: sun through the gaps of a crown, more when it thins.
    float hole = vn(v_obj * 2.2 + 3.0) * 0.7 + vn(v_obj * 5.3) * 0.3;
    ALPHA = v_wood > 0.5 ? 1.0 : step(0.22 + (1.0 - v_leaf) * 0.6, hole);
    ALPHA_SCISSOR_THRESHOLD = 0.5;
#endif
    vec3 leaf = v_tint * v_ao * mix(0.55, 1.15, n);
    ALBEDO = mix(leaf, vec3(0.032, 0.028, 0.024), v_wood);
    ROUGHNESS = 0.85;
    SPECULAR = 0.25;
    BACKLIGHT = v_tint * 0.25 * (1.0 - v_wood) * v_leaf;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.45, 5.0, 0.55, -1.5);
        TH_OUT(th);
        BACKLIGHT = vec3(0.0);
    }
}
"""

const FAR_SHADER := """
shader_type spatial;
render_mode skip_vertex_transform, cull_disabled, diffuse_lambert_wrap;
#include "%s"
%s
varying vec3 v_tint;
varying float v_seed;
varying float v_leaf;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float fam = t_fam(INSTANCE_CUSTOM.a);
    float cb = t_cb(INSTANCE_CUSTOM.a);
    v_leaf = t_leaf(INSTANCE_CUSTOM.a);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_seed = t_hash(base.xz);
    float r = 0.5 * (length(MODEL_MATRIX[0].xyz) + length(MODEL_MATRIX[2].xyz)) * 0.5
        * (fam > 1.5 && fam < 3.5 ? 0.8 : (fam > 4.5 && fam < 5.5 ? 0.75 : 0.95));
    float h = length(MODEL_MATRIX[1].xyz);
    float hh = h * (1.0 - cb) * 0.5;
    vec3 cw = (MODEL_MATRIX * vec4(crown_centre(cb), 1.0)).xyz;
    vec3 cv = (VIEW_MATRIX * vec4(cw, 1.0)).xyz;
    vec3 upv = (VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz;
    float ry = mix(r, hh, clamp(abs(upv.y), 0.0, 1.0));
    vec2 c = UV * 2.0 - 1.0;
    VERTEX = cv + vec3(c.x * r, -c.y * ry, 0.0);
    NORMAL = vec3(0.0, 0.0, 1.0);
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}
void fragment() {
    vec2 c = UV * 2.0 - 1.0;
    float a = atan(c.y, c.x);
    float rim = (0.86 + 0.1 * sin(a * 5.0 + v_seed * 40.0) * sin(a * 3.0 + v_seed * 17.0)) * mix(0.72, 1.0, v_leaf);
    float rr = length(c);
    float spec = fract(sin(dot(floor(c * 4.0), vec2(12.9, 78.2)) + v_seed) * 43758.5);
    ALPHA = rr < rim && spec > (1.0 - v_leaf) * 0.55 ? 1.0 : 0.0;
    ALPHA_SCISSOR_THRESHOLD = 0.5;
    vec3 n = vec3(c.x, -c.y, sqrt(max(1.0 - rr * rr, 0.05)));
    NORMAL = normalize(n);
    ALBEDO = v_tint * mix(0.62, 1.0, 1.0 - rr * 0.5) * (0.85 + 0.3 * spec);
    ROUGHNESS = 0.9;
    SPECULAR = 0.2;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.45, 5.0, 0.55, -1.5);
        TH_OUT(th);
    }
}
"""


func _shader(code: String) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = code % [INC, COMMON]
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


# ------------------------------------------------------------------ meshes
# The crown meshes are istanbul_nature's / nature.gd's (lumps, limbs, leaf
# cards), built against this script's shaders, in NEAR_VARIANTS seeds.

var _twig_tex: ImageTexture


## Bare twigs for a leafless card: a few forked sprays of thin, tapering,
## grey-brown lines with buds, on transparent.
func _twig_texture() -> ImageTexture:
	const SZ := 256
	var img := Image.create(SZ, SZ, false, Image.FORMAT_RGBA8)
	img.fill(Color(0.5, 0.45, 0.4, 0.0))
	var rng := RandomNumberGenerator.new()
	rng.seed = 911
	var stack: Array = []
	for k in 5:
		var a := rng.randf() * TAU
		var r := rng.randf_range(0.05, 0.3) * SZ
		stack.append([Vector2(SZ * 0.5 + cos(a) * r, SZ * 0.5 + sin(a) * r), rng.randf() * TAU,
			rng.randf_range(38.0, 60.0), 2.4])
	while not stack.is_empty():
		var b: Array = stack.pop_back()
		var p: Vector2 = b[0]
		var ang: float = b[1]
		var ln: float = b[2]
		var w: float = b[3]
		var steps := int(ln / 1.5)
		for st in steps:
			ang += rng.randf_range(-0.18, 0.18)
			p += Vector2(cos(ang), sin(ang)) * 1.5
			var ww := w * (1.0 - 0.5 * float(st) / float(steps))
			var e := int(ceil(ww))
			for y in range(int(p.y) - e, int(p.y) + e + 1):
				for x in range(int(p.x) - e, int(p.x) + e + 1):
					if x < 2 or y < 2 or x >= SZ - 2 or y >= SZ - 2:
						continue
					if Vector2(x, y).distance_to(p) <= ww * 0.5 + 0.35:
						var sh := rng.randf_range(0.75, 1.0)
						img.set_pixel(x, y, Color(0.55 * sh, 0.5 * sh, 0.45 * sh, 1.0))
		if w > 0.9:
			for k in 2:
				stack.append([p, ang + rng.randf_range(0.35, 0.8) * (1.0 if k == 0 else -1.0),
					ln * rng.randf_range(0.5, 0.75), w * 0.62])
	return _coverage_mips(img)


func _leaf_texture() -> ImageTexture:
	const SZ := 256
	var img := Image.create(SZ, SZ, false, Image.FORMAT_RGBA8)
	img.fill(Color(0.62, 0.64, 0.55, 0.0))
	var rng := RandomNumberGenerator.new()
	rng.seed = 4507
	var sprays: Array[Vector3] = []
	for k in 7:
		var a := rng.randf() * TAU
		var r := rng.randf_range(0.0, 0.25) * SZ
		sprays.append(Vector3(SZ * 0.5 + cos(a) * r, SZ * 0.5 + sin(a) * r, rng.randf_range(0.14, 0.24) * SZ))
	for k in 2600:
		var sp: Vector3 = sprays[k % sprays.size()]
		var ang := rng.randf() * TAU
		var rad := sqrt(rng.randf()) * sp.z
		var cx := clampf(sp.x + cos(ang) * rad, 6.0, SZ - 6.0)
		var cy := clampf(sp.y + sin(ang) * rad, 6.0, SZ - 6.0)
		var ln := rng.randf_range(4.0, 7.0)
		var wd := ln * rng.randf_range(0.42, 0.6)
		var rot := rng.randf() * TAU
		var shade := rng.randf_range(0.6, 1.0)
		var ca := cos(rot)
		var sa := sin(rot)
		var ext := int(ceil(ln))
		for y in range(int(cy) - ext, int(cy) + ext + 1):
			for x in range(int(cx) - ext, int(cx) + ext + 1):
				if x < 0 or y < 0 or x >= SZ or y >= SZ:
					continue
				var dx := x - cx
				var dy := y - cy
				var u := (dx * ca + dy * sa) / ln
				var v := (-dx * sa + dy * ca) / wd
				if u * u + v * v * (1.0 + maxf(u, 0.0) * 1.5) <= 1.0:
					var vein := 0.85 if absf(v) < 0.12 else 1.0
					var light := shade * vein * (0.85 + 0.15 * u)
					img.set_pixel(x, y, Color(light, light * 1.02, light * 0.9, 1.0))
	return _coverage_mips(img)


## Mipmaps that keep the alpha-tested coverage of level 0 (otherwise leaves
## and twigs thin out and vanish with distance).
func _coverage_mips(img: Image) -> ImageTexture:
	const SZ := 256
	var levels: Array[Image] = [img]
	var cov0 := _coverage(img)
	var cur := img
	while cur.get_width() > 1:
		var nxt := cur.duplicate() as Image
		nxt.resize(maxi(1, cur.get_width() / 2), maxi(1, cur.get_height() / 2), Image.INTERPOLATE_BILINEAR)
		_fit_coverage(nxt, cov0)
		levels.append(nxt)
		cur = nxt
	var data := PackedByteArray()
	for l in levels:
		data.append_array(l.get_data())
	var full := Image.create_from_data(SZ, SZ, true, Image.FORMAT_RGBA8, data)
	return ImageTexture.create_from_image(full)


func _coverage(img: Image) -> float:
	var n := 0
	var w := img.get_width()
	var h := img.get_height()
	var d := img.get_data()
	for i in w * h:
		if d[i * 4 + 3] >= 128:
			n += 1
	return float(n) / float(w * h)


func _fit_coverage(img: Image, target: float) -> void:
	var d := img.get_data()
	var n := img.get_width() * img.get_height()
	var lo := 0.5
	var hi := 4.0
	var best := 1.0
	for it in 10:
		var s := (lo + hi) * 0.5
		var c := 0
		for i in n:
			if float(d[i * 4 + 3]) * s >= 128.0:
				c += 1
		if float(c) / float(n) < target:
			lo = s
		else:
			hi = s
		best = s
	for i in n:
		d[i * 4 + 3] = mini(255, int(float(d[i * 4 + 3]) * best))
	img.set_data(img.get_width(), img.get_height(), false, Image.FORMAT_RGBA8, d)


func _blobs(seed_: int) -> Array:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_
	var out := [[Vector3(0, 0.05, 0), 0.62]]
	var a0 := rng.randf() * TAU
	for k in 5:
		var az := a0 + TAU * float(k) / 5.0 + rng.randf_range(-0.4, 0.4)
		var el := rng.randf_range(-0.35, 0.55)
		var dist := rng.randf_range(0.38, 0.5)
		var dir := Vector3(cos(az) * cos(el), sin(el), sin(az) * cos(el))
		out.append([dir * dist, rng.randf_range(0.42, 0.56)])
	return out


func _near_mesh(seed_: int, leaf_tex: Texture2D) -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_ * 7 + 1
	var blobs := _blobs(seed_)
	var wood := SurfaceTool.new()
	wood.begin(Mesh.PRIMITIVE_TRIANGLES)
	wood.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	_limb(wood, Vector4(0, 0, 0, 0), Vector4(0, 1, 0, 0), 0.022, 0.018, 6, 1.0)
	_limb(wood, Vector4(0, -1, 0, 1), Vector4(0, 0.3, 0, 1), 0.018, 0.008, 5, 0.9)
	for k in range(1, blobs.size()):
		var c: Vector3 = blobs[k][0]
		var from := Vector4(0, lerpf(-0.9, -0.1, float(k) / 5.0), 0, 1)
		_limb(wood, from, Vector4(c.x * 1.1, c.y * 1.1, c.z * 1.1, 1), 0.011, 0.004, 4, 0.85)
	var mesh := wood.commit()
	mesh.surface_set_material(0, _shader(WOOD_SHADER))
	var leaf := SurfaceTool.new()
	leaf.begin(Mesh.PRIMITIVE_TRIANGLES)
	leaf.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	for bl: Array in blobs:
		_blob(leaf, bl[0], float(bl[1]) * 0.94, 1, rng, 1.0)
	for k in 18:
		var bl: Array = blobs[k % blobs.size()]
		var dir := Vector3(rng.randf_range(-1, 1), rng.randf_range(-0.6, 1), rng.randf_range(-1, 1)).normalized()
		var c: Vector3 = bl[0] + dir * float(bl[1]) * 0.85
		for j in 4:
			var out := (c + Vector3(rng.randf_range(-0.6, 0.6), rng.randf_range(-0.4, 0.8),
				rng.randf_range(-0.6, 0.6))).normalized()
			var size := rng.randf_range(0.24, 0.34)
			var off := Vector3(rng.randf_range(-0.05, 0.05), rng.randf_range(-0.04, 0.04),
				rng.randf_range(-0.05, 0.05))
			_card(leaf, c + Vector3(0.0, 0.0, float(j) * 1e-3), off, out, size, rng.randf() * TAU)
	leaf.commit(mesh)
	var lm := _shader(LEAF_SHADER)
	lm.set_shader_parameter("leaf_tex", leaf_tex)
	lm.set_shader_parameter("twig_tex", _twig_tex)
	mesh.surface_set_material(1, lm)
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


func _limb(st: SurfaceTool, a: Vector4, b: Vector4, ra: float, rb: float, sides: int, _shade: float) -> void:
	var axis := Vector3(b.x - a.x, b.y - a.y, b.z - a.z)
	if a.w < 0.5 and b.w < 0.5:
		axis = Vector3.UP
	axis = axis.normalized()
	var side := axis.cross(Vector3.FORWARD if absf(axis.y) > 0.9 else Vector3.UP).normalized()
	var up := axis.cross(side).normalized()
	for s in sides:
		var a0 := TAU * float(s) / float(sides)
		var a1 := TAU * float(s + 1) / float(sides)
		var d0 := side * cos(a0) + up * sin(a0)
		var d1 := side * cos(a1) + up * sin(a1)
		var quad := [[a, d0 * ra, d0], [b, d0 * rb, d0], [b, d1 * rb, d1],
			[a, d0 * ra, d0], [b, d1 * rb, d1], [a, d1 * ra, d1]]
		for q: Array in quad:
			st.set_custom(0, Color(q[0].x, q[0].y, q[0].z, q[0].w))
			st.set_normal(q[2])
			st.set_uv(Vector2.ZERO)
			st.add_vertex(q[1])


func _blob(st: SurfaceTool, centre: Vector3, rad: float, subdiv: int, rng: RandomNumberGenerator, shell: float) -> void:
	var tris := _icosphere(subdiv)
	var lump := {}
	for tri: Array in tris:
		for v: Vector3 in tri:
			var key := Vector3i(roundi(v.x * 1000), roundi(v.y * 1000), roundi(v.z * 1000))
			if not lump.has(key):
				lump[key] = rng.randf_range(0.88, 1.08)
	for tri: Array in tris:
		for j in [0, 2, 1]:
			var v: Vector3 = tri[j]
			var key := Vector3i(roundi(v.x * 1000), roundi(v.y * 1000), roundi(v.z * 1000))
			var p := centre + v * rad * float(lump[key])
			st.set_custom(0, Color(p.x, p.y, p.z, 1.0 + shell))
			st.set_normal(v)
			st.set_uv(Vector2(atan2(v.z, v.x) / TAU * 2.0 + 0.5, v.y * 1.0 + 0.5))
			st.add_vertex(Vector3.ZERO)


func _card(st: SurfaceTool, anchor: Vector3, off: Vector3, n: Vector3, size: float, spin: float) -> void:
	var t := n.cross(Vector3.UP if absf(n.y) < 0.95 else Vector3.RIGHT).normalized()
	var b := n.cross(t).normalized()
	var ct := cos(spin)
	var sn := sin(spin)
	var u := (t * ct + b * sn) * size * 0.5
	var v := (-t * sn + b * ct) * size * 0.5
	var corners := [off - u - v, off + u - v, off + u + v, off - u + v]
	var uvs := [Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), Vector2(0, 0)]
	for j in [0, 2, 1, 0, 3, 2]:
		st.set_custom(0, Color(anchor.x, anchor.y, anchor.z, 1.0))
		st.set_normal(n)
		st.set_uv(uvs[j])
		st.add_vertex(corners[j])


func _mid_mesh() -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	st.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	for bl: Array in _blobs(11):
		_blob(st, bl[0], float(bl[1]) * 1.04, 0, rng, 0.0)
	_limb(st, Vector4(0, 0, 0, 0), Vector4(0, 1, 0, 0), 0.022, 0.018, 5, 1.0)
	var mesh := st.commit()
	mesh.surface_set_material(0, _shader(MID_SHADER))
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


## The shadow proxy: the mid crown's centre and three lobes and a trunk,
## dappled (MID_SHADER's SHADOW_PROXY holes).
func _shadow_mesh() -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	st.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	var bl := _blobs(11)
	for k in [0, 1, 3, 5]:
		_blob(st, bl[k][0], float(bl[k][1]) * (1.12 if k == 0 else 1.04), 0, rng, 0.0)
	_limb(st, Vector4(0, 0, 0, 0), Vector4(0, 1, 0, 0), 0.03, 0.025, 4, 1.0)
	var mesh := st.commit()
	mesh.surface_set_material(0, _shader(MID_SHADER.replace("shader_type spatial;",
		"shader_type spatial;\n#define SHADOW_PROXY")))
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


func _far_mesh() -> ArrayMesh:
	var q := QuadMesh.new()
	var st := SurfaceTool.new()
	st.append_from(q, 0, Transform3D())
	var mesh := st.commit()
	mesh.surface_set_material(0, _shader(FAR_SHADER))
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


static func _icosphere(subdiv: int) -> Array:
	var t := (1.0 + sqrt(5.0)) / 2.0
	var v := [Vector3(-1, t, 0), Vector3(1, t, 0), Vector3(-1, -t, 0), Vector3(1, -t, 0),
		Vector3(0, -1, t), Vector3(0, 1, t), Vector3(0, -1, -t), Vector3(0, 1, -t),
		Vector3(t, 0, -1), Vector3(t, 0, 1), Vector3(-t, 0, -1), Vector3(-t, 0, 1)]
	for i in v.size():
		v[i] = (v[i] as Vector3).normalized()
	var f := [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4],
		[11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
		[4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]]
	var tris := []
	for tri: Array in f:
		tris.append([v[tri[0]], v[tri[1]], v[tri[2]]])
	for s in subdiv:
		var nt := []
		for tri: Array in tris:
			var a: Vector3 = tri[0]
			var b: Vector3 = tri[1]
			var c: Vector3 = tri[2]
			var ab := ((a + b) * 0.5).normalized()
			var bc := ((b + c) * 0.5).normalized()
			var ca := ((c + a) * 0.5).normalized()
			nt.append([a, ab, ca])
			nt.append([b, bc, ab])
			nt.append([c, ca, bc])
			nt.append([ab, bc, ca])
		tris = nt
	return tris
