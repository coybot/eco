## Istanbul's street network for the moving world (istanbul_life.gd): the
## Manhattan loader (life_roads.gd) run on assets/istanbul/roads.json, then
## adjusted for a city on hills with no kerbside data.
##
## roads.json's edges have Manhattan's layout ([a, b, class, oneway, lanes,
## width, -, name, flags]; fetch_istanbul.bake_roads) with no parking count
## and flags 1 bridge, 2 tunnel, 4 layer > 0, 8 layer < 0, 16 stone paving,
## 32 steps. Classes "pedestrian" and "steps" are new: neither carries
## vehicles (Istiklal, Ordu Caddesi and Divan Yolu are mapped pedestrian,
## the tram's).
##
## What this adds on top of the Manhattan loader:
##   * HEIGHTS. node_y (sea-level metres) per node and y_at() along an edge:
##     the ground on land, a deck on a bridge. Grade for pitching a vehicle.
##   * BRIDGES DRIVEN. Manhattan keeps vehicles off every bridge (no deck
##     heights there). Here a bridge chain's deck is interpolated between the
##     ground at its two ends, lifted to DECK_ABOVE_SEA over the water (the
##     Galata and Ataturk bridges), and corrected by a raycast onto whatever
##     solid deck the bridges script built (refine_decks). Long spans over
##     open water (the Bosphorus bridge, deck ~64 m) stay closed: their end
##     ground says nothing about the deck.
##   * ACCESS. life.json (fetch_istanbul_life.py) lists the ways OSM closes
##     to traffic (motor_vehicle=no...): closed here.
##   * SIGNALS. Only junctions with an OSM traffic_signals node within
##     SIGNAL_R are signalled; the rest give way by class (node_motorway is
##     the loader's "no signals here" flag and is reused as such). Each
##     signalled node gets an axis (its busiest road) so the two phases split
##     the approaches whatever the angle (node_axis).
##   * PARKING. d_park from the street's width and direction: a one-way
##     street of 6 m or more parks one kerb, a two-way one of 9.5 m or more
##     one or both; stone-paved lanes and the old city's alleys none.
extends "res://scripts/life_roads.gd"

const DECK_ABOVE_SEA := 6.0
const LONG_SPAN_M := 700.0
const SIGNAL_R := 28.0
const ROAD_FLAG_STONE := 16
const POST := 10.0                          # env_istanbul's terrain mesh posts

var env: Node3D
var node_y := PackedFloat32Array()
var node_axis := PackedVector2Array()
var node_bridge := PackedByteArray()        # 1: on a bridge deck (y is not the ground)
var d_name := PackedInt32Array()            # roads.json name index per directed edge
var names: Array = []
var signals := 0
var closed_edges := 0
var bridge_edges := 0
var _closed := {}                           # way-0 edges OSM closes to traffic


func load_istanbul(env_node: Node3D) -> bool:
	env = env_node
	if not load_from(env, "res://assets/istanbul/roads.json"):
		return false
	var doc = JSON.parse_string(FileAccess.get_file_as_string("res://assets/istanbul/roads.json"))
	var raw_edges: Array = doc.get("edges", [])
	names = doc.get("names", [])
	var life := {}
	var lf := FileAccess.open("res://assets/istanbul/life.json", FileAccess.READ)
	if lf != null:
		var l = JSON.parse_string(lf.get_as_text())
		life = l if l is Dictionary else {}
	_match_names(raw_edges)
	_close_ways(life.get("closed", []))
	_open_bridges()
	_derive_parking()
	_rebuild_nodes(life.get("signals", []))
	_heights()
	return loaded


## Directed edges are appended in pairs in roads.json order, minus the ones
## the loader skipped (outside the slice, or under 0.3 m); match them back by
## their end points.
func _match_names(raw: Array) -> void:
	d_name.resize(d_from.size())
	d_name.fill(-1)
	var k := 0
	for e: Array in raw:
		if k >= d_from.size():
			break
		if d_from[k] == int(e[0]) and d_from[k + 1] == int(e[1]):
			var nm := int(e[7])
			d_name[k] = nm
			d_name[k + 1] = nm
			k += 2


static func _key(p: Vector2) -> Vector2i:
	return Vector2i(roundi(p.x * 10.0), roundi(p.y * 10.0))


func _set_drivable(d: int, want: bool) -> void:
	var to := d_from[d ^ 1]
	var now := d_to[d] >= 0
	if want == now:
		return
	if want:
		d_to[d] = to
		out[d_from[d]].append(d)
	else:
		d_to[d] = -1
		out[d_from[d]].erase(d)


func _close_ways(closed: Array) -> void:
	var keys := {}
	for w: Array in closed:
		var flat: Array = w[0]
		for i in range(0, flat.size() - 1, 2):
			keys[_key(Vector2(float(flat[i]), float(flat[i + 1])))] = true
	if keys.is_empty():
		return
	for d in range(0, d_from.size(), 2):
		if keys.has(_key(nodes[d_from[d]])) and keys.has(_key(nodes[d_from[d + 1]])):
			if d_to[d] >= 0 or d_to[d + 1] >= 0:
				closed_edges += 1
			_closed[d] = true
			_set_drivable(d, false)
			_set_drivable(d + 1, false)


## Bridge chains (connected bridge edges of a vehicle class): drivable unless
## the chain crosses a long stretch of open water.
func _open_bridges() -> void:
	var bridge_of := {}                     # node -> Array of way-0 edges
	for d in range(0, d_from.size(), 2):
		if (d_flags[d] & FLAG_BRIDGE) == 0 or (d_flags[d] & (FLAG_TUNNEL | FLAG_BELOW)) != 0:
			continue
		if not (d_class[d] in VEHICLE_CLASSES):
			continue
		for nd in [d_from[d], d_from[d + 1]]:
			if not bridge_of.has(nd):
				bridge_of[nd] = []
			bridge_of[nd].append(d)
	var seen := {}
	for start in bridge_of:
		for d0: int in bridge_of[start]:
			if seen.has(d0):
				continue
			var comp: Array = []
			var stack: Array = [d0]
			while not stack.is_empty():
				var d: int = stack.pop_back()
				if seen.has(d):
					continue
				seen[d] = true
				comp.append(d)
				for nd in [d_from[d], d_from[d + 1]]:
					for d2: int in bridge_of[nd]:
						if not seen.has(d2):
							stack.append(d2)
			if _water_run(comp) > LONG_SPAN_M:
				continue
			for d: int in comp:
				var oneway := d_oneway[d] == 1
				if d_to[d] < 0 and d_to[d + 1] < 0 and not _closed_by_access(d):
					_set_drivable(d, true)
					if not oneway:
						_set_drivable(d + 1, true)
					bridge_edges += 1


func _closed_by_access(d: int) -> bool:
	return _closed.has(d)


## Longest stretch of a chain's edges over water, sampled every 20 m.
func _water_run(comp: Array) -> float:
	var best := 0.0
	for d: int in comp:
		var run := 0.0
		var n := maxi(1, int(d_len[d] / 20.0))
		for k in n + 1:
			var p := nodes[d_from[d]] + d_dir[d] * d_len[d] * float(k) / float(n)
			if env.call("is_water", p.x, p.y):
				run += d_len[d] / float(n + 1)
		best += run
	return best


func _derive_parking() -> void:
	for d in range(0, d_from.size(), 2):
		var park := 0
		var cls := d_class[d]
		var w := d_halfw[d] * 2.0
		var veh := d_to[d] >= 0 or d_to[d + 1] >= 0
		if veh and cls in ["primary", "secondary", "street", "service"] \
				and (d_flags[d] & (FLAG_BRIDGE | FLAG_TUNNEL | FLAG_BELOW | ROAD_FLAG_STONE)) == 0:
			if d_oneway[d] == 1:
				park = 2 if w >= 12.0 else (1 if w >= 6.0 and cls != "primary" else 0)
			else:
				park = 2 if w >= 12.0 else (1 if w >= 9.5 else 0)
		d_park[d] = park
		d_park[d + 1] = park


## node_deg, node_rank, node_clear, node_motorway (= no signals) and node_axis
## from the edges as they now are.
func _rebuild_nodes(sig: Array) -> void:
	var n := nodes.size()
	node_deg.fill(0)
	node_rank.fill(0)
	node_clear.fill(3.0)
	node_motorway.fill(1)
	node_axis.resize(n)
	node_axis.fill(Vector2.ZERO)
	var axis_rank := PackedInt32Array()
	axis_rank.resize(n)
	axis_rank.fill(-1)
	for d in range(0, d_from.size(), 2):
		if d_to[d] < 0 and d_to[d + 1] < 0:
			continue
		for nd in [d_from[d], d_from[d + 1]]:
			node_deg[nd] += 1
			node_rank[nd] = maxi(node_rank[nd], d_rank[d])
			node_clear[nd] = maxf(node_clear[nd], d_halfw[d] + 1.5)
			if d_rank[d] > axis_rank[nd]:
				axis_rank[nd] = d_rank[d]
				node_axis[nd] = d_dir[d]
	# Signalled: a traffic_signals node within SIGNAL_R of a junction.
	var grid := {}
	for s: Array in sig:
		var p := Vector2(float(s[0]), float(s[1]))
		var key := Vector2i(floori(p.x / 50.0), floori(p.y / 50.0))
		if not grid.has(key):
			grid[key] = PackedVector2Array()
		var arr: PackedVector2Array = grid[key]
		arr.append(p)
		grid[key] = arr
	for i in n:
		if node_deg[i] < 3 or node_rank[i] < 2:
			continue
		var p := nodes[i]
		var k := Vector2i(floori(p.x / 50.0), floori(p.y / 50.0))
		var hit := false
		for dy in [-1, 0, 1]:
			for dx in [-1, 0, 1]:
				for q: Vector2 in grid.get(k + Vector2i(dx, dy), PackedVector2Array()):
					if q.distance_to(p) < SIGNAL_R:
						hit = true
		if hit:
			node_motorway[i] = 0
			signals += 1


# ------------------------------------------------------------------ heights

## The ground as env_istanbul's terrain mesh draws it: its 10 m posts and
## their triangle split, so a wheel sits on the drawn ground, not on the
## smoother bilinear field between posts.
func surface_y(e: float, n: float) -> float:
	var fx := e / POST
	var fy := n / POST
	var i := floorf(fx)
	var j := floorf(fy)
	var tx := fx - i
	var ty := fy - j
	var e0 := i * POST
	var n0 := j * POST
	var a: float = env.call("ground_height", e0, n0)
	var b: float = env.call("ground_height", e0 + POST, n0)
	var c: float = env.call("ground_height", e0, n0 + POST)
	var d: float = env.call("ground_height", e0 + POST, n0 + POST)
	# Triangles (a, c, b) and (b, c, d): the split runs from b to c.
	if tx + ty <= 1.0:
		return a + (b - a) * tx + (c - a) * ty
	return d + (c - d) * (1.0 - tx) + (b - d) * (1.0 - ty)


## Node heights: the ground, except on bridge decks, where the deck is
## interpolated between the chain's anchors (nodes where a bridge meets
## ground roads) by path distance, and lifted over water.
func _heights() -> void:
	var n := nodes.size()
	node_y.resize(n)
	node_bridge.resize(n)
	node_bridge.fill(0)
	var on_ground := PackedByteArray()
	on_ground.resize(n)
	on_ground.fill(0)
	var badj := {}
	for d in range(0, d_from.size(), 2):
		var br := (d_flags[d] & FLAG_BRIDGE) != 0
		for nd in [d_from[d], d_from[d + 1]]:
			if not br:
				on_ground[nd] = 1
		if br:
			for w in 2:
				var a := d_from[d + w]
				if not badj.has(a):
					badj[a] = []
				badj[a].append(d + w)
	for i in n:
		node_y[i] = surface_y(nodes[i].x, nodes[i].y)
	for i: int in badj:
		if on_ground[i] == 0:
			node_bridge[i] = 1
	# Per bridge component: Dijkstra from each anchor, inverse-distance blend.
	var done := {}
	for start: int in badj:
		if node_bridge[start] == 0 or done.has(start):
			continue
		var comp: Array = []
		var stack: Array = [start]
		var anchors: Array = []
		var inc := {}
		while not stack.is_empty():
			var v: int = stack.pop_back()
			if inc.has(v):
				continue
			inc[v] = true
			if node_bridge[v] == 0:
				anchors.append(v)
				continue
			comp.append(v)
			done[v] = true
			for d: int in badj.get(v, []):
				stack.append(d_from[d ^ 1])
		if anchors.is_empty():
			for v: int in comp:
				node_y[v] = DECK_ABOVE_SEA
			continue
		var wsum := {}
		var ysum := {}
		for a: int in anchors:
			var dist := _dijkstra(a, badj)
			for v: int in comp:
				if dist.has(v):
					var w := 1.0 / maxf(float(dist[v]), 1.0)
					wsum[v] = float(wsum.get(v, 0.0)) + w
					ysum[v] = float(ysum.get(v, 0.0)) + w * node_y[a]
		for v: int in comp:
			var y: float = float(ysum[v]) / float(wsum[v]) if wsum.has(v) else node_y[v]
			if env.call("is_water", nodes[v].x, nodes[v].y):
				y = maxf(y, DECK_ABOVE_SEA)
			node_y[v] = y


func _dijkstra(src: int, badj: Dictionary) -> Dictionary:
	var dist := {src: 0.0}
	var open: Array = [src]
	while not open.is_empty():
		var bi := 0
		for k in open.size():
			if float(dist[open[k]]) < float(dist[open[bi]]):
				bi = k
		var v: int = open[bi]
		open.remove_at(bi)
		if v != src and node_bridge[v] == 0:
			continue                        # through bridge nodes only
		for d: int in badj.get(v, []):
			var u := d_from[d ^ 1]
			var nd: float = float(dist[v]) + d_len[d]
			if nd < float(dist.get(u, INF)):
				dist[u] = nd
				open.append(u)
	return dist


func is_bridge(d: int) -> bool:
	return (d_flags[d] & FLAG_BRIDGE) != 0


## Height (sea-level m) of the point `s` metres along directed edge d.
func y_at(d: int, s: float, p: Vector2) -> float:
	if is_bridge(d):
		return lerpf(node_y[d_from[d]], node_y[d_from[d ^ 1]], clampf(s / maxf(d_len[d], 0.01), 0.0, 1.0))
	return surface_y(p.x, p.y)


## Rise per metre along d at s (for pitching a vehicle).
func grade_at(d: int, s: float, p: Vector2) -> float:
	if is_bridge(d):
		return (node_y[d_from[d ^ 1]] - node_y[d_from[d]]) / maxf(d_len[d], 0.01)
	var dir := d_dir[d]
	return (surface_y(p.x + dir.x * 2.5, p.y + dir.y * 2.5) - surface_y(p.x - dir.x * 2.5, p.y - dir.y * 2.5)) / 5.0


## Move bridge-deck nodes onto the solid deck the bridges script built, if it
## built one: a ray down from above each deck node onto collision layer 1.
## Returns how many nodes moved.
func refine_decks(space: PhysicsDirectSpaceState3D) -> int:
	if space == null:
		return 0
	var moved := 0
	var q := PhysicsRayQueryParameters3D.new()
	q.collision_mask = 1
	q.hit_back_faces = false
	for i in nodes.size():
		if node_bridge[i] == 0:
			continue
		var p := nodes[i]
		var y := node_y[i]
		q.from = Vector3(p.x, y + 25.0, -p.y)
		q.to = Vector3(p.x, maxf(y - 15.0, -2.0), -p.y)
		var hit := space.intersect_ray(q)
		if hit.is_empty():
			continue
		var hy: float = (hit["position"] as Vector3).y
		if absf(hy - y) > 0.15 and hy > 0.5:
			node_y[i] = hy
			moved += 1
	return moved


func name_of(d: int) -> String:
	var k := d_name[d] if d < d_name.size() else -1
	return str(names[k]) if k >= 0 and k < names.size() else ""
