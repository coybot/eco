## Baylands' road network for the moving world (baylands_life.gd): the
## Istanbul loader (istanbul_life_roads.gd: Manhattan's graph plus heights,
## driven bridges, OSM access closures and signals) on
## assets/baylands/life_roads.json (bake_baylands_life.py, from OpenStreetMap).
##
## What differs from Istanbul, and only that:
##   * POSTED SPEEDS. Each edge carries its way's OSM maxspeed (mph converted
##     to m/s; California defaults where OSM has none): d_speed.
##   * DECKS FROM THE LIDAR. Bridge nodes the 2020 lidar saw a deck over
##     (class 17) take that measured height; the rest are interpolated
##     between their anchors as Istanbul's are.
##   * THE GROUND. Wheels stand on env.ground_height() (the lidar's 2 m
##     bare earth), not Istanbul's 10 m posts.
extends "res://scripts/istanbul_life_roads.gd"

const PATH := "res://assets/baylands/life_roads.json"

var d_speed := PackedFloat32Array()         # posted m/s per directed edge
var decks := 0


func load_baylands(env_node: Node3D) -> bool:
	env = env_node
	if not load_from(env, PATH):
		return false
	var doc = JSON.parse_string(FileAccess.get_file_as_string(PATH))
	var raw_edges: Array = doc.get("edges", [])
	names = doc.get("names", [])
	_match_names(raw_edges)
	_match_speeds(raw_edges)
	_close_ways(doc.get("closed", []))
	_open_bridges()
	_derive_parking()
	_rebuild_nodes(doc.get("signals", []))
	_heights()
	var dz: Dictionary = doc.get("deck_z", {})
	for k in dz:
		var i := int(k)
		if i >= 0 and i < node_y.size():
			node_y[i] = float(dz[k])
			node_bridge[i] = 1
			decks += 1
	return loaded


## Directed edges come in pairs in file order (minus the loader's skips).
func _match_speeds(raw: Array) -> void:
	d_speed.resize(d_from.size())
	d_speed.fill(13.4)
	var k := 0
	for e: Array in raw:
		if k >= d_from.size():
			break
		if d_from[k] == int(e[0]) and d_from[k + 1] == int(e[1]):
			var v := float(e[9]) if e.size() > 9 else 13.4
			d_speed[k] = v
			d_speed[k + 1] = v
			k += 2


func surface_y(e: float, n: float) -> float:
	return float(env.call("ground_height", e, n))
