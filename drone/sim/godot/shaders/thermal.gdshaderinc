// Thermal (LWIR, 8-14 um) band for the sim's cameras: scripts/thermal_kit.gd.
//
// A thermal camera's cull mask lacks render layer 20 (bit 19); every other
// camera keeps it (Godot's default mask is all 20 layers). A shader asks which
// kind of camera is drawing it with thermal_pass(CAMERA_VISIBLE_LAYERS) and,
// for a thermal one, outputs apparent temperature instead of colour:
//
//     EMISSION = (T + 40) / 120, T in degrees C (linear, into an HDR viewport)
//     ALBEDO   = how much the sun heats it (absorptance x sun response): the
//                thermal camera's environment has no ambient or sky light, so
//                the one thing that lights it is the sun, with its shadows.
//                Sunlit faces read warmer, shadowed ones cooler, the sun's
//                angle counts, and at night the term is zero.
//
// Surface temperature here is a lumped model, not a heat solver:
//
//     T = air + mass * store * 9 - (1 - mass) * cool * 7 + own   (+ sun term)
//
//   air    the air temperature now (thermal_air_c)
//   store  0..1, the day's heat held in heavy things (concrete, asphalt,
//          walls); it lags the sun by a few hours, so they stay warm at dusk
//   cool   0..1, clear-sky radiative cooling at night; light things (sand, roof
//          sheeting, cars' bodywork) fall below the air, heavy ones less
//   own    the object's own heat: engines, people, machinery
//
// Water is its own case: it barely changes over a day, so it reads cool beside
// sunlit sand at noon and warm beside it before dawn, and at grazing angles it
// reflects the cold sky.

global uniform float thermal_air_c;      // air temperature, C
global uniform float thermal_sea_c;      // sea surface temperature, C
global uniform float thermal_store;      // 0..1
global uniform float thermal_cool;       // 0..1
global uniform float thermal_sun_gain;   // ALBEDO units per C of solar rise, per unit sun energy; 0 at night
global uniform float thermal_sky_c;      // apparent temperature of the zenith sky, C
global uniform float thermal_lamps;      // 0..1, how lit the street and port lamps are

bool thermal_pass(uint layers) {
    return (layers & (1u << 19u)) == 0u;
}

float th_enc(float t_c) {
    return (t_c + 40.0) / 120.0;
}

// (albedo, emission) for a surface: absorb 0..1, sun_c the solar rise at full
// sun on a face square to it (C), mass 0 (light) .. 1 (heavy), own_c its own heat.
vec2 th_surface(float absorb, float sun_c, float mass, float own_c) {
    float t = thermal_air_c + mass * thermal_store * 9.0 - (1.0 - mass) * thermal_cool * 7.0 + own_c;
    return vec2(absorb * sun_c * thermal_sun_gain, th_enc(t));
}

// Water: the sea's temperature, a faint swell texture (`ripple` -1..1), and
// the cold sky in it at grazing angles (`ndotv` = dot(normal, view)).
vec2 th_water(float ripple, float ndotv) {
    float fres = pow(1.0 - clamp(ndotv, 0.0, 1.0), 5.0) * 0.9 + 0.02;
    float t = mix(thermal_sea_c + 0.25 * ripple, thermal_sky_c, fres);
    return vec2(0.0, th_enc(t));
}

// Ground from its photo colour: dark ground (asphalt, wet soil, rock) takes in
// more sun and is heavier than pale sand; green ground is vegetation, which
// transpires and stays near the air.
vec2 th_ground(vec3 photo) {
    float lum = dot(photo, vec3(0.299, 0.587, 0.114));
    float green = clamp((photo.g - max(photo.r, photo.b)) * 6.0, 0.0, 1.0);
    float absorb = clamp(1.0 - lum * 1.1, 0.15, 0.95);
    float mass = mix(0.2, 0.75, smoothstep(0.45, 0.12, lum));
    vec2 g = th_surface(absorb, 24.0, mass, 0.0);
    vec2 v = th_surface(0.5, 6.0, 0.55, -1.0);
    return mix(g, v, green);
}

// The air between: past a few kilometres the scene fades toward the air's own
// temperature (path radiance; 8-14 um transmits ~70 % over 5 km in a humid
// coastal haze), so far mountains read soft, not as blotches of hot rock.
vec2 th_air(vec2 th, float dist) {
    float k = 1.0 - exp(-max(dist, 0.0) / 9000.0);
    return vec2(th.x * (1.0 - k), mix(th.y, th_enc(thermal_air_c - 1.0), k));
}

// Irrigated park cover (parks.png: lawn, paving, beds, kerbs): lawn
// transpires and sits a little below the air day and night (wet grass is
// cold before dawn); paving is pale stone, heavy and sun-heated; beds are
// moist soil.
vec2 th_park(vec4 pk) {
    vec2 lawn = th_surface(0.35, 4.0, 0.45, -3.0);
    vec2 paving = th_surface(0.45, 20.0, 0.7, 0.0);
    vec2 bed = th_surface(0.6, 9.0, 0.6, -1.5);
    vec2 kerb = th_surface(0.45, 18.0, 0.75, 0.0);
    float s = max(pk.r + pk.g + pk.b + pk.a, 1e-3);
    return (lawn * pk.r + paving * pk.g + bed * pk.b + kerb * pk.a) / s;
}

// Put a thermal result in the fragment's outputs.
#define TH_OUT(th) ALBEDO = vec3((th).x); EMISSION = vec3((th).y); ROUGHNESS = 1.0; SPECULAR = 0.0; METALLIC = 0.0
