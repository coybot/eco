// Baylands water (scripts/baylands_water.gd): every pond, slough and
// channel as a flat surface at its own level; the tidal ones at the tide.
//
// Colour is measured: each body's own NAIP colour (the South Bay's salt
// ponds really are green, olive, brown and orange-red with their algae and
// brine shrimp), with the photo's slow variation across a big pond, and a
// murkier, browner band over the shallows along the banks. The surface is
// wind ripples (two drifting normal maps) on a low roughness, so the sky's
// reflection (Godot's Fresnel against the scene's sky) brightens it toward
// grazing angles as real water does; F0 = 0.02 (SPECULAR 0.25).
shader_type spatial;
render_mode cull_back;

#include "res://shaders/thermal.gdshaderinc"
#include "res://shaders/baylands_height.gdshaderinc"

uniform sampler2D alb : source_color, filter_linear_mipmap, repeat_disable;
uniform vec4 alb_rect;
uniform float has_alb = 0.0;
uniform float photo_gain = 0.9;
uniform float photo_gamma = 1.8;
uniform sampler2D wn1 : hint_normal, repeat_enable, filter_linear_mipmap_anisotropic;
uniform sampler2D wn2 : hint_normal, repeat_enable, filter_linear_mipmap_anisotropic;
uniform vec2 wind = vec2(3.0, -2.0);     // m/s toward (east, north)

varying vec3 w;
varying vec3 bcol;

void vertex() {
    float y = UV2.x;
    if (UV2.y > 0.5) {
        // Tidal: at the tide, but only where the tide reaches through
        // ground lower than itself (tidal.png); elsewhere sunk out of sight,
        // so a low hollow behind a berm stays dry.
        y = tide;
        if (flood_tide(vec2(VERTEX.x, -VERTEX.z)) > tide + 0.02) { y = tide - 3.0; }
    }
    VERTEX.y = y;
    w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    bcol = COLOR.rgb;
}

vec3 wnm(sampler2D t, vec2 uv) {
    vec3 n = texture(t, uv).xyz * 2.0 - 1.0;
    return vec3(n.x, n.z, n.y);
}

void fragment() {
    vec2 en = vec2(w.x, -w.z);
    float dist = length(CAMERA_POSITION_WORLD - w);
    float sd = water_sd(en);
    // The body's colour, and the photo's own across it away from the banks
    // (where its coarse mip would mix in the land).
    vec3 c = bcol;
    if (has_alb > 0.5) {
        vec2 puv = vec2((en.x - alb_rect.x) / (alb_rect.z - alb_rect.x), (alb_rect.w - en.y) / (alb_rect.w - alb_rect.y));
        vec3 ph = max(textureLod(alb, puv, 4.0).rgb, vec3(1e-4));
        float yy = dot(ph, vec3(0.2126, 0.7152, 0.0722));
        ph = photo_gain * pow(yy, photo_gamma) * pow(ph / yy, vec3(1.15));
        float lb = dot(bcol, vec3(0.3, 0.6, 0.1)) + 1e-4;
        float lp = dot(ph, vec3(0.3, 0.6, 0.1)) + 1e-4;
        // Its hue, but its brightness kept within 0.6-1.6 x the body's (no glints).
        ph = ph / lp * lb * clamp(lp / lb, 0.6, 1.6);
        c = mix(bcol, ph, smoothstep(6.0, 20.0, sd) * 0.8);
    }
    // Shallows: the mud shows through, browner and lighter in the last metres.
    float shallow = 1.0 - smoothstep(0.0, 4.0, sd);
    c = mix(c, vec3(0.055, 0.045, 0.03) + c * 0.4, shallow * 0.55);
    // Ripples drifting downwind; more chop in more wind, calmer far off
    // where it is below a pixel.
    float ws = length(wind);
    vec2 wd = ws > 0.01 ? wind / ws : vec2(1.0, 0.0);
    vec2 drift = vec2(wd.x, -wd.y) * TIME;
    float near = 1.0 - smoothstep(150.0, 700.0, dist);
    vec3 n = wnm(wn1, w.xz / 17.0 + drift * 0.012) + 0.8 * wnm(wn2, w.xz / 5.3 + drift * 0.03)
           + 0.5 * near * wnm(wn1, w.xz / 1.9 + drift * 0.07);
    float chop = clamp(ws / 6.0, 0.15, 1.3) * mix(1.0, 0.4, smoothstep(300.0, 3000.0, dist));
    chop *= mix(1.0, 0.5, shallow);
    vec3 nw = normalize(vec3(n.x * chop, 1.0, n.z * chop));
    NORMAL = normalize((VIEW_MATRIX * vec4(nw, 0.0)).xyz);
    ALBEDO = c;
    ROUGHNESS = mix(0.05, 0.17, smoothstep(250.0, 2500.0, dist)) + 0.04 * clamp(ws / 8.0, 0.0, 1.0);
    SPECULAR = 0.25;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_water(n.x, dot(NORMAL, VIEW));
        th = th_air(th, dist);
        TH_OUT(th);
    }
}
