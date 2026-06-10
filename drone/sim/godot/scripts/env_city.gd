## City environment built from the Kenney Starter Kit City Builder assets.
## Uses the pre-built sample map (GridMap) that ships with the kit.
##
## Assets: CC0 (Kenney.nl). The map.res and GLBs live at:
##   res://assets/kenney_city/
##
## Env name keys: "city", "outdoor", "neighbourhood"
extends Node3D

# Structure order must match the index order used when map.res was saved.
# This matches the `structures` array in Kenney's main.tscn exactly.
const STRUCTURE_ORDER: Array[String] = [
	"road-straight",
	"road-straight-lightposts",
	"road-corner",
	"road-split",
	"road-intersection",
	"pavement",
	"pavement-fountain",
	"building-small-a",
	"building-small-b",
	"building-small-c",
	"building-small-d",
	"building-garage",
	"grass",
	"grass-trees",
	"grass-trees-tall",
]

const ASSETS_BASE := "res://assets/kenney_city/"


func _ready() -> void:
	_build_city()
	_add_sky_and_lighting()
	print("[env_city] ready")


func _build_city() -> void:
	# Build MeshLibrary from the GLBs, preserving Kenney's index order so
	# map.res cell indices map to the correct mesh.
	var mesh_library := MeshLibrary.new()
	for i in range(STRUCTURE_ORDER.size()):
		var path := ASSETS_BASE + STRUCTURE_ORDER[i] + ".glb"
		mesh_library.create_item(i)
		if not ResourceLoader.exists(path):
			push_warning("[env_city] missing GLB: " + path)
			continue
		var packed: PackedScene = load(path)
		var mesh := _extract_mesh(packed)
		if mesh:
			mesh_library.set_item_mesh(i, mesh)
			mesh_library.set_item_mesh_transform(i, Transform3D())

	# GridMap matching Kenney's cell_size / centering settings.
	var gridmap := GridMap.new()
	gridmap.mesh_library = mesh_library
	gridmap.cell_size = Vector3(1, 1, 1)
	gridmap.cell_center_x = false
	gridmap.cell_center_y = false
	gridmap.cell_center_z = false
	add_child(gridmap)

	# Load the pre-assembled city layout.
	var map_path := ASSETS_BASE + "map.res"
	if not ResourceLoader.exists(map_path):
		push_warning("[env_city] map.res not found — falling back to flat ground")
		_add_ground_plane()
		return
	var map = ResourceLoader.load(map_path)
	if not map or not map.get("structures"):
		push_warning("[env_city] could not parse map.res")
		_add_ground_plane()
		return
	for cell in map.structures:
		gridmap.set_cell_item(
			Vector3i(cell.position.x, 0, cell.position.y),
			cell.structure,
			cell.orientation
		)
	print("[env_city] city loaded — %d cells" % map.structures.size())


## Extract the first MeshInstance3D mesh from a PackedScene without instantiating it.
## This is the same technique Kenney's builder.gd uses to populate a MeshLibrary.
func _extract_mesh(packed: PackedScene) -> Mesh:
	var state: SceneState = packed.get_state()
	for i in range(state.get_node_count()):
		if state.get_node_type(i) == "MeshInstance3D":
			for j in range(state.get_node_property_count(i)):
				if state.get_node_property_name(i, j) == "mesh":
					var val = state.get_node_property_value(i, j)
					if val is Mesh:
						return val.duplicate()
	return null


func _add_ground_plane() -> void:
	var mi := MeshInstance3D.new()
	mi.mesh = PlaneMesh.new()
	(mi.mesh as PlaneMesh).size = Vector2(200.0, 200.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.3, 0.32, 0.28)
	mi.material_override = mat
	add_child(mi)


func _add_sky_and_lighting() -> void:
	var sun := DirectionalLight3D.new()
	sun.light_energy = 1.8
	sun.light_color = Color(1.0, 0.97, 0.88)
	sun.shadow_enabled = true
	sun.rotation_degrees = Vector3(-45.0, 30.0, 0.0)
	add_child(sun)

	var env_node := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = Color(0.3, 0.5, 0.9)
	sky_mat.sky_horizon_color = Color(0.7, 0.8, 0.95)
	sky_mat.ground_bottom_color = Color(0.2, 0.2, 0.2)
	sky.sky_material = sky_mat
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_energy = 0.5
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env_node.environment = environment
	add_child(env_node)
