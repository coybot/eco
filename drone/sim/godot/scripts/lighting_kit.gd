## Lighting adjustments for the kilometre-scale envs (baylands, manhattan_photo).
##
## DELIBERATELY NOT IN scene_kit.gd. That file's header is explicit that its
## Filmic/0.85 block is load-bearing rather than taste, and that env_gate (the
## perception gate) and env_sar (the demo scene) MUST render identically — if
## they drift, the gate's measured recognition rate stops being evidence about
## the scene the video is shot in. So nothing here touches the shared setup;
## these are per-env overrides applied after SceneKit.build(), and only the two
## photoreal envs call them. Every other env renders exactly as before.
##
## The tonemap and exposure are left ALONE for the same reason. What is wrong
## in the big envs is not the tone curve, it is three things that are all
## scale-dependent:
##
##   1. Shadows stop at 400 m. That is a sensible budget for an office
##      courtyard and invisible in a city: from 120 m up, looking across
##      kilometres, essentially the whole frame is beyond the shadow range, so
##      the scene renders shadowless and reads flat.
##   2. Sky ambient at full energy fills the shadows back in. Even where a
##      shadow is cast, an unoccluded ambient term of 1.0 lifts it almost to
##      the lit value, so the shadow costs performance and buys no contrast.
##   3. Fog at 0.0012/m is tuned for a few hundred metres. Over 4 km its
##      transmittance is ~0.008 — the far half of Manhattan is a flat blue
##      wash. env_manhattan's own header records this exact failure from its
##      haze experiment, at a density only slightly higher.
extends RefCounted


## Retune an already-built SceneKit scene for a world kilometres across.
## `reach_m` is roughly how far the camera needs shadows to hold.
static func for_large_env(parent: Node3D, reach_m: float = 6000.0) -> void:
	_retune_sun(parent, reach_m)
	_retune_environment(parent, reach_m)


static func _retune_sun(parent: Node3D, reach_m: float) -> void:
	var sun: DirectionalLight3D = null
	for child in parent.get_children():
		if child is DirectionalLight3D:
			sun = child
			break
	if sun == null:
		return

	sun.directional_shadow_max_distance = reach_m
	# Four splits, because one shadow map stretched over kilometres has metre-
	# sized texels and every building edge crawls. Splits let the near field
	# keep fine texels while the far field gets coarse ones.
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
	sun.directional_shadow_split_1 = 0.04
	sun.directional_shadow_split_2 = 0.12
	sun.directional_shadow_split_3 = 0.35
	sun.directional_shadow_blend_splits = true
	# Bias has to rise with texel size or a shadow map covering kilometres
	# self-shadows every roof into stripes. Normal bias does most of the work
	# on the box geometry here, which is all large flat faces.
	sun.shadow_bias = 0.08
	sun.shadow_normal_bias = 2.0
	# Fade the far edge instead of ending shadows at a hard line across the
	# ground, which is very visible from the air.
	sun.directional_shadow_fade_start = 0.9


static func _retune_environment(parent: Node3D, reach_m: float) -> void:
	var we: WorldEnvironment = null
	for child in parent.get_children():
		if child is WorldEnvironment:
			we = child
			break
	if we == null or we.environment == null:
		return
	var e: Environment = we.environment

	# Let the shadows actually be dark. Sky ambient at 1.0 fills them in
	# almost completely; 0.45 keeps the sky's colour in the shade without
	# erasing the contrast the shadows exist to provide.
	e.ambient_light_energy = 0.45

	# Fog re-scaled from hundreds of metres to kilometres. Not switched off:
	# some aerial perspective is what stops a 20 km island rendering at
	# identical contrast from end to end. This density leaves ~85% transmission
	# at 1 km, against ~30% before.
	e.fog_density = 0.00016
	e.fog_aerial_perspective = 0.7
	e.fog_sky_affect = 0.15

	# Contact darkening where buildings meet ground and each other. This is
	# what makes a box look like it is standing ON the photograph rather than
	# floating above it, and it costs nothing at these view distances because
	# SSAO is screen-space.
	e.ssao_enabled = true
	e.ssao_radius = 6.0
	e.ssao_intensity = 1.6
	e.ssao_power = 1.4
