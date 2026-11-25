## Manhattan's street network for the moving world (world_life.gd and its
## parts): roads.json (tools/fetch_manhattan_roads.py, NYC Open Data
## Centerline, public domain) loaded once into flat packed arrays, in the SIM
## frame (the env's re-centred slice), and shared by traffic, parked cars and
## people.
##
## Directed edges: edge i of roads.json is 2i (a -> b) and 2i + 1 (b -> a). The
## reverse of a one-way street, and both ways of anything vehicles may not use,
## are kept as placeholders with `to` = -1 so the pairing stays exact.
##
## What vehicles may use. Not paths, not tunnels or below-grade roadways (the
## Battery Park Underpass, the FDR under the UN), and not bridges: a bridge
## deck stands tens of metres over the river and nothing in this scene knows
## its height, so a car on one drove across the water at street level. Bridge
## approaches on land are kept. Elevated roadways are kept at street level:
## nothing draws the viaduct either, and from the air a car on the road's line
## is where the eye expects it.
extends RefCounted

const LANE_W := 3.2
## Lane-km a vehicle class is worth when spreading traffic: how busy it is in
## midday Manhattan relative to a side street.
const CLASS_RANK := {"motorway": 6, "motorway_link": 5, "primary": 4, "secondary": 3, "street": 2,
	"service": 1, "path": 0}
const VEHICLE_CLASSES := ["motorway", "motorway_link", "primary", "secondary", "street", "service"]
const FLAG_BRIDGE := 1
const FLAG_TUNNEL := 2
const FLAG_ELEVATED := 4
const FLAG_BELOW := 8

var nodes := PackedVector2Array()           # sim (e, n)
var node_deg := PackedInt32Array()          # vehicle edges meeting at a node
var node_rank := PackedInt32Array()         # highest vehicle class rank there
var node_motorway := PackedByteArray()      # 1: a motorway meets here (no signals)
var node_clear := PackedFloat32Array()      # half the widest carriageway there + a margin: the stop line
var out: Array = []                         # node -> Array of vehicle edges leaving it (plain Arrays:
                                            # a packed array taken out of an Array is a copy)
var walk_out: Array = []                    # node -> Array of any edges leaving it

# Directed edges.
var d_from := PackedInt32Array()
var d_to := PackedInt32Array()              # -1: not drivable this way
var d_len := PackedFloat32Array()
var d_dir := PackedVector2Array()
var d_lanes := PackedInt32Array()           # lanes this way
var d_off0 := PackedFloat32Array()          # lane 0's centre, metres right of the centreline
var d_rank := PackedInt32Array()
var d_class := PackedStringArray()
var d_halfw := PackedFloat32Array()         # half the carriageway, kerb to centreline
var d_park := PackedInt32Array()            # parking lanes on the whole road (both sides)
var d_oneway := PackedByteArray()
var d_flags := PackedInt32Array()

var loaded := false


func load_from(env: Node, path: String = "res://assets/manhattan/roads.json") -> bool:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return false
	var doc = JSON.parse_string(f.get_as_text())
	f.close()
	if not (doc is Dictionary):
		return false
	var off: Vector2 = env.call("data_offset")
	# Only the loaded slice (--env-region clips the city), with a margin so a
	# street along the slice's edge is still whole.
	var keep: Rect2 = (env.call("sim_bounds") as Rect2).grow(150.0)
	for p: Array in doc.get("nodes", []):
		nodes.append(Vector2(float(p[0]), float(p[1])) - off)
	var n := nodes.size()
	node_deg.resize(n)
	node_rank.resize(n)
	node_motorway.resize(n)
	node_clear.resize(n)
	node_clear.fill(3.0)
	out.resize(n)
	walk_out.resize(n)
	for i in n:
		out[i] = []
		walk_out[i] = []
	for e: Array in doc.get("edges", []):
		var a := int(e[0])
		var b := int(e[1])
		var cls := str(e[2])
		var oneway := int(e[3]) == 1
		var lanes := maxi(1, int(e[4]))
		var width := float(e[5])
		var park := int(e[6])
		var flags := int(e[8]) if e.size() > 8 else 0
		var d := nodes[b] - nodes[a]
		var length := d.length()
		var mid := (nodes[a] + nodes[b]) * 0.5
		if length < 0.3 or not keep.has_point(mid):
			continue
		var veh: bool = cls in VEHICLE_CLASSES and (flags & (FLAG_BRIDGE | FLAG_TUNNEL | FLAG_BELOW)) == 0
		var rank := int(CLASS_RANK.get(cls, 0))
		# Half the roadway: the measured kerb-to-kerb width where CSCL has one,
		# else lanes plus parking lanes at standard widths.
		var halfw := width * 0.5 if width > 2.0 else (float(lanes) * LANE_W + float(park) * 2.4) * 0.5
		if cls == "path":
			halfw = maxf(halfw, 1.5)
		if veh:
			node_deg[a] += 1
			node_deg[b] += 1
			node_rank[a] = maxi(node_rank[a], rank)
			node_rank[b] = maxi(node_rank[b], rank)
			node_clear[a] = maxf(node_clear[a], halfw + 1.5)
			node_clear[b] = maxf(node_clear[b], halfw + 1.5)
			if cls.begins_with("motorway"):
				node_motorway[a] = 1
				node_motorway[b] = 1
		var per_dir: int = lanes if oneway else maxi(1, lanes / 2)
		for way in 2:
			var from := a if way == 0 else b
			var to := b if way == 0 else a
			var drivable := veh and not (way == 1 and oneway)
			var dir := d / length * (1.0 if way == 0 else -1.0)
			# Two-way: lane 0 just right of the centreline. One-way: the lanes
			# centred on the street, so parking (both kerbs) stays clear.
			var off0: float = (-(float(per_dir) - 1.0) * 0.5 * LANE_W) if oneway else (0.5 * LANE_W)
			d_from.append(from)
			d_to.append(to if drivable else -1)
			d_len.append(length)
			d_dir.append(dir)
			d_lanes.append(per_dir)
			d_off0.append(off0)
			d_rank.append(rank)
			d_class.append(cls)
			d_halfw.append(halfw)
			d_park.append(park)
			d_oneway.append(1 if oneway else 0)
			d_flags.append(flags)
			var idx := d_from.size() - 1
			if drivable:
				out[from].append(idx)
			if (flags & (FLAG_TUNNEL | FLAG_BELOW)) == 0:
				walk_out[from].append(idx)
	loaded = d_from.size() > 0
	return loaded


## The point `s` metres along directed edge `d`, `off` metres right of it.
func lane_pos(d: int, s: float, off: float) -> Vector2:
	var dir := d_dir[d]
	return nodes[d_from[d]] + dir * s + Vector2(dir.y, -dir.x) * off


func drivable(d: int) -> bool:
	return d_to[d] >= 0


func edge_count() -> int:
	return d_from.size()
