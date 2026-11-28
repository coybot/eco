// The Baylands ground (scripts/baylands_ground.gd): a camera-centred clipmap
// on the lidar terrain, coloured by the de-shadowed NAIP photo and grained,
// per surface class, by CC0 detail textures.
//
// MESH. Levels of N x N cells, the finest 0.5 m, each twice the last; every
// camera that draws it gets its own rings (the vertex shader centres them on
// CAMERA_POSITION_WORLD, snapped to twice the level's step so vertices never
// swim). A level's vertices strictly inside the next finer level drop 200 m,
// out of sight under it. Near a level's outer edge its odd vertices morph to
// the coarser level's line, so the seams close and nothing pops.
shader_type spatial;
render_mode cull_back, diffuse_burley;

#include "res://shaders/thermal.gdshaderinc"
#include "res://shaders/baylands_height.gdshaderinc"

uniform float base_step = 0.5;
uniform float n_half = 96.0;
uniform vec2 post_off = vec2(1.0, 1.0);
uniform sampler2D alb : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform vec4 alb_rect;           // e0, n0, e1, n1 of the photo
uniform sampler2D cls : filter_nearest, repeat_disable;
uniform sampler2DArray mats : source_color, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2DArray mats_n : filter_linear_mipmap_anisotropic, repeat_enable;
uniform float mat_size[11];
uniform vec3 mat_mean[11];
uniform float has_mats = 0.0;
uniform float has_aux = 0.0;
// NAIP -> albedo: linear = gain * photo_linear ^ gamma. The survey's stretch
// puts asphalt at sRGB 0.59; this brings it to ~0.11, concrete to ~0.3,
// cured grass to ~0.2.
uniform float photo_gain = 0.9;
uniform float photo_gamma = 1.8;
uniform float season = 0.8;      // 0 green (Jan-Apr) .. 1 gold (Jul-Oct)
uniform float wetness = 0.0;     // 0 dry .. 1 just rained
uniform float photo_season = 0.45;   // NAIP's 20 May 2024: partly cured

varying vec3 w;
varying float morphv;

float h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}

// NAIP -> albedo: the gamma on the brightness only, the hue kept (a gamma
// per channel turned the photo's warm cast into pink grass).
vec3 photo_albedo(vec3 ph) {
    ph = max(ph, vec3(1e-4));
    float y = dot(ph, vec3(0.2126, 0.7152, 0.0722));
    return photo_gain * pow(y, photo_gamma) * pow(ph / y, vec3(1.15));
}

// Season, per pixel: how cured the photo shows this grass (its own green
// against red, on the palette's scale), shifted by the season, the ratio of
// the two palette colours applied to the photo so its own detail stays.
vec3 season_ratio(int k, vec3 c) {
    vec3 g_col = vec3(0.065, 0.100, 0.035);
    vec3 y_col = vec3(0.26, 0.21, 0.105);
    float gain = 1.7;
    if (k == 6) { gain = 0.45; y_col = vec3(0.17, 0.15, 0.075); }
    float hue = (c.g - c.r) / max(c.g + c.r, 1e-4);
    float c0 = clamp((0.2 - hue) / 0.31, 0.0, 1.0);
    float c1 = clamp(c0 + (season - photo_season) * gain, 0.0, 1.0);
    if (k == 11) {
        // Pickleweed: green through summer, red-brown in autumn.
        g_col = vec3(0.06, 0.09, 0.035);
        y_col = vec3(0.12, 0.06, 0.035);
        c1 = clamp(c0 + (season - 0.55) * 1.5, 0.0, 1.0);
    }
    return clamp(mix(g_col, y_col, c1) / max(mix(g_col, y_col, c0), vec3(0.005)), vec3(0.3), vec3(4.0));
}

float cheb_to(vec2 p, vec2 cam) { vec2 d = abs(p - cam); return max(d.x, d.y); }

float hgt_at(vec2 pxz, vec2 cam) {
    return drawn_h(vec2(pxz.x, -pxz.y), cheb_to(pxz, cam));
}

void vertex() {
    float lvl = floor(UV2.x + 0.5);
    float s = base_step * exp2(lvl);
    vec2 cam = CAMERA_POSITION_WORLD.xz;
    // Every ring shifted by post_off (1 m): the terrain's 2 m posts stand at
    // odd metres (cell centres), so the 2 m ring's vertices sit exactly on
    // them and the drawn ground IS ground_height() there.
    vec2 c = floor((cam - post_off) / (2.0 * s)) * (2.0 * s) + post_off;
    vec2 p = c + UV * s;
    p = clamp(p, vec2(bounds.x, -bounds.w), vec2(bounds.z, -bounds.y));
    float h = hgt_at(p, cam);
    // Morph toward the coarser level near the outer edge.
    float m = smoothstep(0.72, 0.96, cheb_to(p, cam) / (n_half * s));
    if (m > 0.0) {
        vec2 gi = floor((p - post_off) / s + 0.5);
        bool ox = mod(gi.x, 2.0) > 0.5;
        bool oz = mod(gi.y, 2.0) > 0.5;
        float hc = h;
        if (ox && !oz) {
            hc = 0.5 * (hgt_at(p - vec2(s, 0.0), cam) + hgt_at(p + vec2(s, 0.0), cam));
        } else if (!ox && oz) {
            hc = 0.5 * (hgt_at(p - vec2(0.0, s), cam) + hgt_at(p + vec2(0.0, s), cam));
        } else if (ox && oz) {
            vec2 c1 = floor((cam - post_off) / (4.0 * s)) * (4.0 * s) + post_off;
            vec2 q = (p - c1) / (2.0 * s);
            vec2 dg = q.x * q.y > 0.0 ? vec2(s, s) : vec2(s, -s);
            hc = 0.5 * (hgt_at(p - dg, cam) + hgt_at(p + dg, cam));
        }
        h = mix(h, hc, m);
    }
    // Inside the next finer level: out of sight under it.
    if (lvl > 0.5) {
        float sf = 0.5 * s;
        vec2 cf = floor((cam - post_off) / (2.0 * sf)) * (2.0 * sf) + post_off;
        vec2 d = abs(p - cf);
        float hh = n_half * sf - 1e-3;
        if (d.x < hh && d.y < hh) { h -= 200.0; }
    }
    VERTEX = vec3(p.x, h, p.y);
    NORMAL = vec3(0.0, 1.0, 0.0);
    w = VERTEX;
    morphv = m;
}

vec3 nmap(int k, vec2 p) {
    vec3 n = texture(mats_n, vec3(p / mat_size[k], float(k))).xyz * 2.0 - 1.0;
    return vec3(n.x, n.z, n.y);     // tangent (east, image-up = south, out) -> world (x, y up, z = south)
}

// A CC0 texture's grain at p, as a multiplier around 1 (the texture over its
// own mean); a second, larger, turned sample breaks the tiling up.
vec3 grain(int k, vec2 p) {
    float s = mat_size[k];
    vec3 a = texture(mats, vec3(p / s, float(k))).rgb;
    vec2 q = mat2(vec2(0.8, 0.6), vec2(-0.6, 0.8)) * p / (s * 2.7);
    vec3 b = texture(mats, vec3(q, float(k))).rgb;
    return mix(a, b, smoothstep(0.35, 0.65, vn(p / (s * 3.1)))) / max(mat_mean[k], vec3(0.01));
}

// Class -> material layer (ground_tex order) and how strongly its grain shows.
//   0 asphalt 1 concrete 2 dg 3 gravel 4 lawn 5 drygrass 6 soil 7 mud 8 cracked 9 riprap 10 litter
const int LAYER[15] = int[](6, 7, 0, 1, 2, 3, 4, 4, 5, 7, 8, 4, 9, 1, 10);
const float AMT[15] = float[](0.9, 0.5, 0.45, 0.7, 0.9, 1.0, 0.9, 0.25, 1.0, 0.9, 0.9, 0.9, 1.0, 0.3, 0.8);
const float ROUGH[15] = float[](0.95, 0.1, 0.9, 0.85, 0.95, 0.95, 0.95, 0.9, 0.97, 0.75, 0.9, 0.95, 0.9, 0.85, 0.97);

void fragment() {
    vec2 en = vec2(w.x, -w.z);
    float dist = length(CAMERA_POSITION_WORLD - w);
    float cheb = cheb_to(w.xz, CAMERA_POSITION_WORLD.xz);
    float fw = max(length(fwidth(en)), 0.002);
    vec2 uv = bl_uv(en);

    // Surface class, its boundary jittered half a metre so the 1 m cells
    // read as ragged edges, not steps.
    vec2 jit = (vec2(vn(en * 1.7), vn(en * 1.7 + 31.0)) - 0.5) * 1.1;
    ivec2 csz = textureSize(cls, 0);
    ivec2 ci = clamp(ivec2(bl_uv(en + jit) * vec2(csz)), ivec2(0), csz - 1);
    int k = int(texelFetch(cls, ci, 0).r * 255.0 + 0.5);
    k = clamp(k, 0, 14);
    vec4 ax = has_aux > 0.5 ? texture(aux, uv) : vec4(0.4, 0.4, 0.0, 0.0);
    float ndvi = ax.r - 0.2;
    float sd = (ax.a * 255.0 - 128.0) * 0.25;

    // The photo, as albedo. Close to (a texel over ~0.3 m of screen... a
    // texel spanning many pixels) a cubic B-spline filter (4 bilinear taps)
    // so its 0.6 m grid does not show as a lattice.
    vec2 puv = vec2((en.x - alb_rect.x) / (alb_rect.z - alb_rect.x), (alb_rect.w - en.y) / (alb_rect.w - alb_rect.y));
    vec3 ph;
    if (fw < 0.15) {
        vec2 tsz = vec2(textureSize(alb, 0));
        vec2 st = puv * tsz - 0.5;
        vec2 f = fract(st);
        vec2 i0 = floor(st);
        vec2 f2 = f * f;
        vec2 f3 = f2 * f;
        vec2 w0 = (1.0 - 3.0 * f + 3.0 * f2 - f3) / 6.0;
        vec2 w1 = (4.0 - 6.0 * f2 + 3.0 * f3) / 6.0;
        vec2 w2 = (1.0 + 3.0 * f + 3.0 * f2 - 3.0 * f3) / 6.0;
        vec2 w3 = f3 / 6.0;
        vec2 g0 = w0 + w1;
        vec2 g1 = w2 + w3;
        vec2 h0 = (i0 - 0.5 + w1 / g0) / tsz;
        vec2 h1 = (i0 + 1.5 + w3 / g1) / tsz;
        ph = g0.y * (g0.x * textureLod(alb, vec2(h0.x, h0.y), 0.0).rgb + g1.x * textureLod(alb, vec2(h1.x, h0.y), 0.0).rgb)
           + g1.y * (g0.x * textureLod(alb, vec2(h0.x, h1.y), 0.0).rgb + g1.x * textureLod(alb, vec2(h1.x, h1.y), 0.0).rgb);
    } else {
        ph = texture(alb, puv).rgb;
    }
    vec3 c = photo_albedo(ph);

    // How much lawn and how much tree cover around (4 taps, 0.8 m apart):
    // the season's colour and the leaf litter blend across a class edge
    // instead of stepping at it.
    float wl = 0.0;
    float wc = 0.0;
    for (int t = 0; t < 4; t++) {
        vec2 o = vec2(t == 0 ? 0.8 : (t == 1 ? -0.8 : 0.0), t == 2 ? 0.8 : (t == 3 ? -0.8 : 0.0));
        ivec2 cj = clamp(ivec2(bl_uv(en + jit * 0.5 + o) * vec2(csz)), ivec2(0), csz - 1);
        int kk = int(texelFetch(cls, cj, 0).r * 255.0 + 0.5);
        wl += kk == 6 ? 0.25 : 0.0;
        wc += kk == 14 ? 0.25 : 0.0;
    }

    // Season: the grass's green or gold, per pixel from how cured the photo
    // shows it, shifted by the season, the photo's own detail kept.
    if (k == 8 || k == 6 || k == 14) {
        c *= mix(season_ratio(8, c), season_ratio(6, c), wl);
    } else if (k == 11) {
        c *= season_ratio(11, c);
    }
    // Under a tree (the bake filled the photo's crown with the ground beside
    // it; the tree itself is drawn above): leaf litter.
    c = mix(c, vec3(0.075, 0.062, 0.042), 0.3 * wc);

    // Grain, faded as it nears a pixel.
    float rough = ROUGH[k];
    vec3 nd = vec3(0.0, 1.0, 0.0);
    if (has_mats > 0.5 && fw < 1.5) {
        int L = LAYER[k];
        float near = 1.0 - smoothstep(0.25 * mat_size[L], 0.9 * mat_size[L], fw);
        if (near > 0.01) {
            vec3 gr = grain(L, en);
            // A little extra contrast in the grain close to, where the photo
            // is only a smooth 0.6 m colour.
            gr = mix(vec3(1.0), gr, 1.0 + 0.35 * (1.0 - smoothstep(0.02, 0.12, fw)));
            c *= max(mix(vec3(1.0), gr, AMT[k] * near), vec3(0.0));
            vec3 t = nmap(L, en);
            nd = normalize(mix(vec3(0.0, 1.0, 0.0), t, AMT[k] * near * 0.8));
        }
    }

    // Wet: bare ground darkens, low spots on paths and bare ground hold
    // puddles, the water's own margins are wet mud whatever the weather, and
    // the tide wets what it has just left.
    bool bare = k == 0 || k == 2 || k == 3 || k == 4 || k == 5 || k == 9 || k == 10 || k == 12;
    float dark = 0.0;
    if (bare) { dark = wetness * 0.45; }
    else if (k != 1 && k != 13) { dark = wetness * 0.15; }
    float margin = (1.0 - smoothstep(0.3, 2.5, -sd)) * step(sd, 0.0);
    float ft = flood_tide(en);
    float tide_wet = ft < 90.0 ? 1.0 - smoothstep(tide, tide + 0.35, ft) : 0.0;
    dark = max(dark, max(margin, tide_wet) * 0.5);
    c *= 1.0 - dark;
    rough = mix(rough, 0.45, max(margin, tide_wet) * 0.6 + (bare ? wetness * 0.3 : 0.0));
    float puddle = 0.0;
    if (wetness > 0.01 && bare && has_aux > 0.5) {
        float depth = ax.b * 255.0 / 800.0;
        // Rain fills the deeper hollows first; even a downpour leaves the
        // lidar's centimetre-deep noise dry.
        float need = mix(0.12, 0.025, pow(wetness, 0.7));
        puddle = smoothstep(need, need + 0.01, depth + (vn(en * 2.3) - 0.5) * 0.008);
        c = mix(c, c * 0.35, puddle);
        rough = mix(rough, 0.04, puddle);
        nd = normalize(mix(nd, vec3(0.0, 1.0, 0.0), puddle));
    }
    // Water floor (seen only where a falling tide bares a channel's bank):
    // the lidar's water class, drawn as wet mud.
    if (sd > 0.0) {
        c = vec3(0.07, 0.06, 0.045) * (0.85 + 0.3 * vn(en * 0.7));
        rough = 0.4;
    }

    // Normal: the drawn height's slope (2 m terrain + near detail), then the
    // material's own grain.
    // At least a metre: the derivative of the bilinear 0.5 m detail steps at
    // every texel, a lattice in the light at nadir.
    float e = mix(1.0, 2.0, smoothstep(40.0, 200.0, dist));
    float rw = res_w(cheb);
    float hE = base_h_lin(en + vec2(e, 0.0)) + rw * res_h(en + vec2(e, 0.0));
    float hW = base_h_lin(en - vec2(e, 0.0)) + rw * res_h(en - vec2(e, 0.0));
    float hN = base_h_lin(en + vec2(0.0, e)) + rw * res_h(en + vec2(0.0, e));
    float hS = base_h_lin(en - vec2(0.0, e)) + rw * res_h(en - vec2(0.0, e));
    vec3 nb = normalize(vec3(-(hE - hW) / (2.0 * e), 1.0, (hN - hS) / (2.0 * e)));
    vec3 nw = normalize(vec3(nb.x + nd.x, nb.y * nd.y, nb.z + nd.z));
    NORMAL = normalize((VIEW_MATRIX * vec4(nw, 0.0)).xyz);

    ALBEDO = clamp(c, vec3(0.0), vec3(1.0));
    ROUGHNESS = rough;
    SPECULAR = mix(0.35, 0.5, puddle);
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_ground(clamp(c * 2.2, 0.0, 1.0));
        if (puddle > 0.5 || sd > 0.0) { th = th_water(0.0, dot(NORMAL, VIEW)); }
        th = th_air(th, dist);
        TH_OUT(th);
    }
}
