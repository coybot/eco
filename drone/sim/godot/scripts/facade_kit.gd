## Shared loader for the CC0 photographic facade set.
##
## Both photoreal envs want the same Texture2DArray — manhattan_photo for whole
## buildings, baylands for walls only (its roofs come from the orthophoto, which
## is better than any generic roof texture because it is the ACTUAL roof). The
## loading is fiddly enough to be worth having in one place: Texture2DArray
## demands every layer share a size and format, and the source photographs do
## not, so a missed convert/resize makes create_from_images fail outright and
## the caller silently falls back to flat grey.
##
## Textures come from drone/sim/tools/fetch_facades.py (ambientCG, CC0).
##
## Loaded by preload rather than class_name: a global class only resolves once
## Godot has rescanned the project, so a fresh checkout hits a parse error on
## first run. preload is what SceneKit does here for the same reason.
extends RefCounted

const DIR := "res://assets/facades/"


## Returns {"array": Texture2DArray, "spans": PackedFloat32Array,
##          "count": int, "roof": ImageTexture|null, "roof_span": float}
## or an empty Dictionary when no facades have been fetched, so every caller
## can still render on a fresh checkout.
static func load_set() -> Dictionary:
	var f := FileAccess.open(DIR + "facades.json", FileAccess.READ)
	if f == null:
		return {}
	var doc: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var list: Array = doc.get("facades", [])
	if list.is_empty():
		return {}

	var images: Array[Image] = []
	var spans := PackedFloat32Array()
	for item in list:
		var img := Image.load_from_file(DIR + str(item["file"]))
		if img == null:
			continue
		img.convert(Image.FORMAT_RGB8)
		img.resize(1024, 1024, Image.INTERPOLATE_LANCZOS)
		img.generate_mipmaps()
		images.append(img)
		spans.append(float(item.get("span_m", 18.0)))
	if images.is_empty():
		return {}

	var arr := Texture2DArray.new()
	arr.create_from_images(images)

	# The shaders declare a fixed-size spans[] array, so pad rather than send
	# short — a short array is a driver-dependent read past the end.
	var count := images.size()
	while spans.size() < 8:
		spans.append(18.0)

	var roof_tex: ImageTexture = null
	var roof_span := 12.0
	var roof: Variant = doc.get("roof", null)
	if roof != null:
		var rimg := Image.load_from_file(DIR + str(roof["file"]))
		if rimg != null:
			rimg.convert(Image.FORMAT_RGB8)
			rimg.generate_mipmaps()
			roof_tex = ImageTexture.create_from_image(rimg)
			roof_span = float(roof.get("span_m", 12.0))

	return {"array": arr, "spans": spans, "count": count,
			"roof": roof_tex, "roof_span": roof_span}
