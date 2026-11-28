// Near grass (scripts/baylands_ground.gd): blade clumps on the grass
// classes, what a quad sees on take-off and landing. A MultiMesh of fixed
// grid offsets; each camera that draws it re-centres the grid on itself
// (snapped to the spacing, so clumps stay put on the ground), jitters each
// clump by its world cell's hash, and drops the clumps that are not on
// grass, too far, or seen from too high (all vertices to one point: nothing
// is rasterised).
shader_type spatial;
render_mode cull_disabled, world_vertex_coords, diffuse_burley;

#include "res://shaders/thermal.gdshaderinc"
#include "res://shaders/baylands_height.gdshaderinc"

uniform sampler2D alb : source_color, filter_linear_mipmap, repeat_disable;
uniform vec4 alb_rect;
uniform sampler2D cls : filter_nearest, repeat_disable;
uniform float spacing = 0.2;
uniform float fade_in0 = 0.0;    // max-norm distance (m) where this ring starts ...
uniform float fade_in1 = 0.0;    // ... and is full
uniform float fade_out0 = 10.0;  // where it starts to thin ...
uniform float fade_out1 = 12.0;  // ... and is gone
uniform float max_agl = 45.0;    // camera height above the ground beyond which no grass is drawn
uniform float photo_gain = 0.9;
uniform float photo_gamma = 1.8;
uniform float season = 0.8;
uniform float photo_season = 0.45;
uniform float wetness = 0.0;
uniform vec2 wind = vec2(3.0, -2.0);   // m/s toward (east, north)

varying vec3 col;
varying float tipv;

float h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

vec3 photo_at(vec2 en) {
    vec2 puv = vec2((en.x - alb_rect.x) / (alb_rect.z - alb_rect.x), (alb_rect.w - en.y) / (alb_rect.w - alb_rect.y));
    vec3 ph = max(textureLod(alb, puv, 1.0).rgb, vec3(1e-4));
    float y = dot(ph, vec3(0.2126, 0.7152, 0.0722));
    return photo_gain * pow(y, photo_gamma) * pow(ph / y, vec3(1.15));
}

void vertex() {
    vec3 o = MODEL_MATRIX[3].xyz;            // this clump's grid offset
    vec3 loc = VERTEX - o;                   // blade vertex: x, z clump radius 1, y 0..1
    vec2 cam = CAMERA_POSITION_WORLD.xz;
    vec2 cell = floor(cam / spacing) * spacing + o.xz;
    vec2 gi = floor(cell / spacing + 0.5);
    float r1 = h2(gi);
    float r2 = h2(gi + vec2(17.31, 3.7));
    float r3 = h2(gi + vec2(5.13, 41.9));
    vec2 a = cell + (vec2(r1, r2) - 0.5) * spacing;
    vec2 en = vec2(a.x, -a.y);
    vec2 dd = abs(a - cam);
    float d = max(dd.x, dd.y);
    float keep = smoothstep(fade_in0, fade_in1, d) * (1.0 - smoothstep(fade_out0, fade_out1, d));
    if (fade_in1 <= 0.0) { keep = 1.0 - smoothstep(fade_out0, fade_out1, d); }
    // Cheapest tests first: most clumps are dropped here, before any height,
    // colour or water lookup (every vertex of a clump repeats them).
    int k = -1;
    if (r3 <= keep) {
        ivec2 csz = textureSize(cls, 0);
        ivec2 ci = clamp(ivec2(bl_uv(en) * vec2(csz)), ivec2(0), csz - 1);
        k = int(texelFetch(cls, ci, 0).r * 255.0 + 0.5);
    }
    col = vec3(0.0);
    tipv = 0.0;
    if (k != 8 && k != 11 && k != 6) {
        VERTEX = vec3(a.x, -1000.0, a.y);
    } else {
        vec4 ax = texture(aux, bl_uv(en));
        float ndvi = ax.r - 0.2;
        float sd = (ax.a * 255.0 - 128.0) * 0.25;
        float g = base_h(en) + res_w(d) * res_h(en);
        // Not measured (the 2020 lidar's ground class lies on the sward's
        // top): annual grass 0.1-0.6 m (taller where it grew lush),
        // pickleweed 0.15-0.3 m, mown lawn 6-9 cm.
        float bh = 0.06 + 0.03 * r3;
        float wid = 0.08;
        if (k == 8) { bh = mix(0.15, 0.45, clamp((ndvi + 0.05) / 0.45, 0.0, 1.0)) * (0.6 + 0.7 * r3); wid = 0.16; }
        else if (k == 11) { bh = 0.15 + 0.15 * r3; wid = 0.13; }
        // None at the water's edge, nor seen from high up.
        // From higher up the clumps are below a pixel and their grid would
        // only shimmer as moire: they shrink away from 25 m above the ground.
        float agl = CAMERA_POSITION_WORLD.y - g;
        if (sd > -0.5 || agl > max_agl) { bh = 0.0; }
        bh *= mix(0.6, 1.0, keep) * (1.0 - smoothstep(25.0, max_agl, agl));
        float ang = r1 * 6.2832;
        mat2 rot = mat2(vec2(cos(ang), sin(ang)), vec2(-sin(ang), cos(ang)));
        vec2 xz = rot * loc.xz * wid * (bh > 0.0 ? 1.0 : 0.0);
        // Wind: the tips lean and sway with gusts that travel downwind.
        float gust = 0.6 + 0.4 * sin(TIME * 1.7 - dot(a, wind) * 0.08 + r2 * 2.0);
        vec2 wv = vec2(wind.x, -wind.y) * 0.012 * gust * loc.y * loc.y * bh;
        VERTEX = vec3(a.x + xz.x + wv.x, g + loc.y * bh, a.y + xz.y + wv.y);
        // Colour: the ground's own photo colour under the clump, seasoned as
        // the ground is.
        vec3 c = photo_at(en);
        vec3 g_col = vec3(0.065, 0.100, 0.035);
        vec3 y_col = vec3(0.26, 0.21, 0.105);
        float sg = k == 6 ? 0.45 : 1.7;
        if (k == 6) { y_col = vec3(0.17, 0.15, 0.075); }
        float hue = (c.g - c.r) / max(c.g + c.r, 1e-4);
        float c0 = clamp((0.2 - hue) / 0.31, 0.0, 1.0);
        // Some stems stay greener, some go paler: a sward is not one colour.
        float c1 = clamp(c0 + (season - photo_season) * sg + (r2 - 0.5) * 0.3, 0.0, 1.0);
        if (k == 11) {
            g_col = vec3(0.06, 0.09, 0.035);
            y_col = vec3(0.12, 0.06, 0.035);
            c1 = clamp(c0 + (season - 0.55) * 1.5, 0.0, 1.0);
        }
        c *= clamp(mix(g_col, y_col, c1) / max(mix(g_col, y_col, c0), vec3(0.005)), vec3(0.3), vec3(4.0));
        // Dry stems are paler than the ground they stand on.
        c *= mix(1.0, 1.35, c1);
        col = c * (1.0 - wetness * 0.15);
        tipv = loc.y;
    }
}

void fragment() {
    vec3 c = col * mix(0.7, 1.1, tipv);
    ALBEDO = clamp(c, vec3(0.0), vec3(1.0));
    // Lit as the sward is, not as each blade's back face would be.
    NORMAL = normalize((VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
    ROUGHNESS = 0.85;
    SPECULAR = 0.3;
    BACKLIGHT = c * 0.6;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.5, 5.0, 0.4, -1.5);
        th = th_air(th, length(CAMERA_POSITION_WORLD - (INV_VIEW_MATRIX * vec4(VERTEX, 1.0)).xyz));
        TH_OUT(th);
    }
}
