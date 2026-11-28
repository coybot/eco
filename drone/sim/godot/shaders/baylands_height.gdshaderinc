// Baylands ground height, shared by the ground (scripts/baylands_ground.gd),
// its near grass and anything else that must stand exactly on it.
//
//   hgt   env_baylands' own 2 m terrain (float, posts at cell centres, row 0
//         north): base_h() is env.ground_height() exactly (the same clamped
//         bilinear, by texelFetch, so no filtering precision is lost).
//   hres  the 0.5 m lidar ground minus that, in cm + 200 (R8), clamped to
//         [-2.0, +0.28] m: levee crests, ditches, berms, kerbs and mounds near
//         the camera. Faded out by 55-85 m from the camera, where the mesh is
//         2 m posts and the drawn ground IS ground_height().
//   aux   1 m RGBA: NDVI, lidar intensity, puddle depth, signed distance to
//         water (A: 128 + 4 x metres, + into the water).
// World frame: Godot (x, y, z) = (east, height, -north).

uniform sampler2D hgt : filter_linear, repeat_disable;
uniform sampler2D hres : filter_linear, repeat_disable;
uniform sampler2D aux : filter_linear, repeat_disable;
uniform sampler2D tidal : filter_linear, repeat_disable;   // 2 m: tide (m) at which the cell floods = v * 255 / 60 - 1
uniform vec4 bounds;            // e0, n0, e1, n1 of the data grids (ENU m)
uniform float hgt_res = 2.0;
uniform float has_hres = 0.0;
uniform float tide = 1.0;       // env.look.tide, m NAVD88

vec2 bl_uv(vec2 en) {
    return vec2((en.x - bounds.x) / (bounds.z - bounds.x), (bounds.w - en.y) / (bounds.w - bounds.y));
}

// env.ground_height(e, n), exactly.
float base_h(vec2 en) {
    ivec2 sz = textureSize(hgt, 0);
    vec2 f = vec2((en.x - bounds.x) / hgt_res - 0.5, (bounds.w - en.y) / hgt_res - 0.5);
    f = clamp(f, vec2(0.0), vec2(sz - 1));
    ivec2 i0 = ivec2(f);
    ivec2 i1 = min(i0 + 1, sz - 1);
    vec2 t = f - vec2(i0);
    float a = texelFetch(hgt, i0, 0).r;
    float b = texelFetch(hgt, ivec2(i1.x, i0.y), 0).r;
    float c = texelFetch(hgt, ivec2(i0.x, i1.y), 0).r;
    float d = texelFetch(hgt, i1, 0).r;
    return mix(mix(a, b, t.x), mix(c, d, t.x), t.y);
}

// The same, filtered by the hardware (fragment normals; ~1/256 of a post's step).
float base_h_lin(vec2 en) {
    vec2 sz = vec2(textureSize(hgt, 0));
    vec2 uv = (vec2(en.x - bounds.x, bounds.w - en.y) / hgt_res) / sz;
    return texture(hgt, uv).r;
}

float res_h(vec2 en) {
    if (has_hres < 0.5) { return 0.0; }
    return (texture(hres, bl_uv(en)).r * 255.0 - 200.0) * 0.01;
}

// Signed distance to water, m (+ inside).
float water_sd(vec2 en) {
    return (texture(aux, bl_uv(en)).a * 255.0 - 128.0) * 0.25;
}

// The tide at which this ground joins the bay (99 = never).
float flood_tide(vec2 en) {
    float v = texture(tidal, bl_uv(en)).r;
    return v > 0.995 ? 99.0 : v * 255.0 / 60.0 - 1.0;
}

// How far under its surface a water body's floor is drawn: the lidar sees
// only the water's top, so the floor shelves away from the bank (deeper in
// the tidal channels, whose banks the falling tide bares).
float bed_depth(vec2 en) {
    float sd = water_sd(en);
    if (sd <= 0.0) { return 0.0; }
    float tidal_ = flood_tide(en) < 0.0 ? 1.0 : 0.0;
    return smoothstep(0.0, 1.0, sd) * 0.15 + min(sd * mix(0.05, 0.14, tidal_), mix(1.0, 2.6, tidal_));
}

// Weight of the 0.5 m detail at distance `cheb` (m, max-norm) from the camera.
float res_w(float cheb) {
    return 1.0 - smoothstep(55.0, 85.0, cheb);
}

// The drawn ground: ground_height + the near detail - the water floor.
float drawn_h(vec2 en, float cheb) {
    return base_h(en) + res_w(cheb) * res_h(en) - bed_depth(en);
}
