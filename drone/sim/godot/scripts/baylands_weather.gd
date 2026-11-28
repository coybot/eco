## Baylands' day: the sun, the light, the weather, the season, the tide and
## the wind, kept coherent, and the daytime randomisation for training.
##
## Everything here is DAY ONLY: no night look exists for this scene.
##
## TIME. world_clock runs on the env's fixed UTC_OFFSET (-8, PST) with no
## daylight saving: every `hour` here is PACIFIC STANDARD TIME all year. In
## summer the clocks in Sunnyvale read one hour later (13:00 here is 14:00
## PDT on a July wall clock). The sun is placed from the real latitude and
## longitude (world_clock's NOAA solar position).
##
## LIGHT. world_clock's sun is a single-colour lamp dimmed by a smoothstep;
## here it is replaced after every clock or weather change (clock_changed) by
## a clear-sky model: direct-beam transmission per colour channel through
## Rayleigh + aerosol optical depth along the Kasten-Young air mass, so the
## morning and evening sun is weaker and goldener by the physics, not a
## curve. Exposure follows the light like a camera's auto-exposure that
## adapts most of the way (an overcast or low-sun frame is a little darker
## and flatter than noon, as it is in a real drone's video).
##
## WEATHER PRESETS (op "weather" or picked by "randomize"):
##   clear           fair-weather sky, a few small cumulus, ~45 km visibility
##   hazy            valley inversion haze / smoke: ~8-15 km visibility, a pale sky
##   marine_layer    morning stratus 250-650 m up, burning off toward noon;
##                   under a full deck no sun and no hard shadows, holes in a
##                   breaking deck let sun (and the deck's shadows) through
##   overcast        a higher frontal deck (1.2-2.5 km)
##   broken_cumulus  bigger cumulus cells, their shadows on the ground
##   high_cirrus     thin streaks ~8 km up, a slightly milkier, softer sun
##   light_rain      low deck, falling streaks, wet ground (env.look.wetness 1)
##   post_rain       the clean air after a front, broken cumulus, wet ground drying
##   fog             tule / advection fog: 0.3-2 km visibility in a layer
##                   60-250 m deep, the sun a pale disc at most
## How often each happens is MEASURED: assets/baylands/climate.json, built by
## drone/sim/tools/bake_baylands_climate.py from Moffett Field (NUQ) ASOS
## 2021-2025 per month and 3-hour block. ASOS cannot see cirrus, so a share
## of the "clear" reports (CIRRUS_SHARE, an estimate) is drawn as cirrus.
##
## SEASON, WETNESS, TIDE (env.look, which ground, water and plants follow):
##   season  from the date: the South Bay's annual grass, green Jan-Mar,
##           browning Apr-Jun, gold Jul-Oct, greening after the first rains
##           (SEASON_BY_MONTH)
##   wetness from the weather (rain 1, drying after rain, dew under fog)
##   tide    metres above NAVD88: NOAA CO-OPS 9414575 Coyote Creek, Alviso
##           Slough's harmonic constants (25 constituents) with the
##           astronomical arguments and nodal corrections for the date;
##           checked against NOAA's own predictions for 2026 (3-5 cm RMS).
##           Datums (1983-2001 epoch, metres above NAVD88): MLLW -0.081,
##           MSL 1.402, MHHW 2.616 (range 2.70 m); lowest -0.71, highest 3.06.
##
## WIND (FixedWingManager.air.set_wind: m/s at 10 m, the direction it blows
## FROM): sampled from the same NUQ climatology by month and PST hour
## (calm fraction, speed quantiles, 16-sector direction, gusts). The file is
## in KNOTS as reported; converted here with 1 kt = 0.514444 m/s. If
## assets/baylands/site.json carries a `wind_climatology` in the same shape
## ({"months": [12][8 blocks] of {calm, q_kt, dir16, gust_p, gust_ex_kt}}),
## it is used instead.
##
## IPC (env_op):
##   weather   {"preset": <name>, optional "cover", "base_m", "vis_km",
##              "cirrus", "haze", "rain", "wetness"} -> state
##   day       {"date": "2026-07-15" | doy, "hour": 13.5 (PST)} -> sets the
##             sun, season and tide; keeps the weather
##   randomize {"seed": N, optional "date", "hour", "weather", "season",
##              "tide", "wetness", "wind": {"speed" m/s, "from_deg", "gust"} |
##              "calm" | false (leave the wind alone)} -> what it chose.
##             Deterministic per seed. Picks a daylight hour (sun >= 3 deg).
##   sky_state {} -> the current state
## Launch: --bay-date=YYYY-MM-DD --bay-hour=<PST> --bay-weather=<preset>
##         --bay-seed=<N> (randomise at start).
## Default at start: 2024-05-20 11:00 PST (the NAIP photo's day), clear,
## season and tide for that moment, the wind left as it is (calm).
extends Node3D

const WorldClock = preload("res://scripts/world_clock.gd")
const SceneLook = preload("res://scripts/scene_look.gd")

## Metres per second in one knot (1852 m / 3600 s), exactly.
const KT := 0.514444

const PRESETS := ["clear", "hazy", "marine_layer", "overcast", "broken_cumulus",
	"high_cirrus", "light_rain", "post_rain", "fog"]

## Share of ASOS "clear" reports drawn as high cirrus, by month (an estimate:
## the ceilometer sees nothing above 12 000 ft). Winter and spring jets bring
## more; the summer high fewer.
const CIRRUS_SHARE := [0.18, 0.18, 0.18, 0.16, 0.10, 0.06, 0.07, 0.08, 0.10, 0.14, 0.16, 0.18]

## env.look.season at mid-month: 0 wet-season green .. 1 dry-season gold.
const SEASON_BY_MONTH := [0.05, 0.0, 0.0, 0.15, 0.55, 0.85, 0.95, 1.0, 1.0, 1.0, 0.75, 0.30]

## NOAA CO-OPS 9414575 Coyote Creek, Alviso Slough: [amplitude m, Greenwich
## phase deg]. Mean sea level 1.402 m above NAVD88 (station datums).
const TIDE_MSL := 1.402
const TIDE_HC := {
	"M2": [0.953, 250.1], "S2": [0.213, 273.8], "N2": [0.191, 232.7], "K1": [0.397, 246.9],
	"M4": [0.049, 1.6], "O1": [0.225, 232.8], "M6": [0.021, 44.1], "MK3": [0.029, 66.8],
	"MN4": [0.022, 342.8], "NU2": [0.046, 227.1], "MU2": [0.039, 357.2], "2N2": [0.018, 220.2],
	"OO1": [0.011, 307.7], "M1": [0.018, 283.5], "J1": [0.018, 300.3], "Q1": [0.041, 235.1],
	"T2": [0.012, 229.0], "P1": [0.122, 246.1], "L2": [0.066, 241.2], "2MK3": [0.049, 68.0],
	"K2": [0.067, 254.3], "M8": [0.006, 96.2], "MS4": [0.028, 24.3],
	"SA": [0.0, 0.0], "SSA": [0.0, 0.0],
}
## Doodson numbers (tau, s, h, p, N', p1) and phase offset (deg).
const DOODSON := {
	"M2": [2, 0, 0, 0, 0, 0, 0], "S2": [2, 2, -2, 0, 0, 0, 0], "N2": [2, -1, 0, 1, 0, 0, 0],
	"K2": [2, 2, 0, 0, 0, 0, 0], "K1": [1, 1, 0, 0, 0, 0, 90], "O1": [1, -1, 0, 0, 0, 0, -90],
	"P1": [1, 1, -2, 0, 0, 0, -90], "Q1": [1, -2, 0, 1, 0, 0, -90], "2N2": [2, -2, 0, 2, 0, 0, 0],
	"NU2": [2, -1, 2, -1, 0, 0, 0], "MU2": [2, -2, 2, 0, 0, 0, 0], "L2": [2, 1, 0, -1, 0, 0, 180],
	"T2": [2, 2, -3, 0, 0, 1, 0], "J1": [1, 2, 0, -1, 0, 0, 90], "OO1": [1, 3, 0, 0, 0, 0, 90],
	"M1": [1, 0, 0, 0, 0, 0, 90], "SA": [0, 0, 1, 0, 0, 0, 0], "SSA": [0, 0, 2, 0, 0, 0, 0],
}
const COMPOUND := {"M4": {"M2": 2}, "MS4": {"M2": 1, "S2": 1}, "MN4": {"M2": 1, "N2": 1},
	"M6": {"M2": 3}, "MK3": {"M2": 1, "K1": 1}, "2MK3": {"M2": 2, "K1": -1}, "M8": {"M2": 4}}

var env: Node3D
var wc: Node
var sl: Node
var clim: Dictionary = {}
var wind_clim: Dictionary = {}

## The current day: {"year", "doy", "date", "hour"} and weather parameters.
var day := {}
var w := {}
var _deck: MeshInstance3D
var _deck_shadow: MeshInstance3D
var _deck_mat: ShaderMaterial
var _deck_shadow_mat: ShaderMaterial
var _light := {}


func _ready() -> void:
	name = "baylands_weather"
	env = get_parent() as Node3D
	wc = env.get_node_or_null("world_clock")
	sl = env.get_node_or_null("scene_look")
	clim = _read_json("res://assets/baylands/climate.json")
	wind_clim = clim
	var site := _read_json("res://assets/baylands/site.json")
	if site.has("wind_climatology") and (site["wind_climatology"] as Dictionary).has("months"):
		wind_clim = site["wind_climatology"]
	_build_deck()
	if wc != null:
		wc.clock_changed.connect(_post_light)
	if sl != null and sl.has_signal("light_changed"):
		sl.light_changed.connect(_post_air)
	if env.has_method("register_op"):
		env.register_op("weather", op_weather)
		env.register_op("day", op_day)
		env.register_op("randomize", op_randomize)
		env.register_op("sky_state", func(_a: Dictionary) -> Dictionary: return state())
	var seed_arg := IpcServer._get_launch_arg("--bay-seed", "")
	if seed_arg != "":
		print("[baylands_weather] randomize ", op_randomize({"seed": int(seed_arg)}))
	else:
		op_day({"date": IpcServer._get_launch_arg("--bay-date", "2024-05-20"),
			"hour": float(IpcServer._get_launch_arg("--bay-hour", "11.0"))})
		op_weather({"preset": IpcServer._get_launch_arg("--bay-weather", "clear")})
	print("[baylands_weather] %s %.2f PST, %s, season %.2f, tide %.2f m NAVD88" % [day.get("date"),
		float(day.get("hour", 0.0)), w.get("preset"), float(env.look.get("season", 0.0)), float(env.look.get("tide", 0.0))])


func _read_json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var d = JSON.parse_string(FileAccess.get_file_as_string(path))
	return d if d is Dictionary else {}


func state() -> Dictionary:
	var st := {"ok": true, "day": day, "weather": w, "look": env.look, "light": _light}
	if wc != null:
		st["sun"] = wc.call("world_state")["sun"]
	st["wind"] = FixedWingManager.air.wind_state()
	return st


# ====================================================================== ops
func op_day(a: Dictionary) -> Dictionary:
	var req := {}
	var dv = a.get("date", day.get("date", "2024-05-20"))
	req["day"] = dv
	if a.has("year"):
		req["year"] = int(a["year"])
	var d := WorldClock._parse_day(req)
	var hour := clampf(float(a.get("hour", day.get("hour", 11.0))), 0.0, 24.0)
	day = {"year": int(d["year"]), "doy": int(d["doy"]), "date": _date_str(int(d["year"]), int(d["doy"])), "hour": hour}
	var look_ch := {"tide": snappedf(tide_at(int(d["year"]), int(d["doy"]), hour), 0.001)}
	if not a.has("keep_season"):
		look_ch["season"] = snappedf(season_for(int(d["doy"])), 0.001)
	for k in ["season", "tide", "wetness"]:
		if a.has(k):
			look_ch[k] = float(a[k])
	env.call("set_look", look_ch)
	if wc != null:
		wc.call("set_time", {"hour": hour, "day": day["doy"], "year": day["year"]})
	return state()


func op_weather(a: Dictionary) -> Dictionary:
	var preset := str(a.get("preset", "clear"))
	if not PRESETS.has(preset):
		return {"ok": false, "error": "unknown preset %s; have %s" % [preset, str(PRESETS)]}
	var rng := RandomNumberGenerator.new()
	rng.seed = int(a.get("seed", 7))
	var p := _preset_params(preset, rng, float(day.get("hour", 12.0)), int(day.get("doy", 140)))
	for k in a:
		if p.has(k):
			p[k] = a[k]
	w = p
	if p.has("wetness"):
		env.call("set_look", {"wetness": float(p["wetness"])})
	_apply_weather()
	return state()


## A coherent daylight scene for training. Every random draw comes from one
## RandomNumberGenerator seeded by `seed`, in a fixed order, so a seed is a
## scene: date -> season and sun; hour (daylight); weather by month and hour;
## wind by month and hour; tide for that exact moment; wetness.
func op_randomize(a: Dictionary) -> Dictionary:
	var rng := RandomNumberGenerator.new()
	rng.seed = int(a.get("seed", 0))
	# 1. Date (2024-2026, any day) and a daylight hour.
	var year := 2024 + rng.randi_range(0, 2)
	var doy := rng.randi_range(1, 365)
	if a.has("date"):
		var d := WorldClock._parse_day({"day": a["date"], "year": int(a.get("year", year))})
		year = int(d["year"])
		doy = int(d["doy"])
	var span := daylight(doy)
	var hour := rng.randf_range(span.x, span.y)
	if a.has("hour"):
		hour = float(a["hour"])
	var month := _month(year, doy)
	var block := clampi(int(hour / 3.0), 0, 7)
	# 2. Weather from the measured frequencies for that month and 3 hours.
	var preset := _pick_preset(month, block, rng)
	if a.has("weather"):
		preset = str(a["weather"])
	# 3. Season: the month's state, +-0.1 for an early or late year.
	var season := clampf(season_for(doy) + rng.randf_range(-0.1, 0.1), 0.0, 1.0)
	if a.has("season"):
		season = float(a["season"])
	var wrng := RandomNumberGenerator.new()
	wrng.seed = rng.randi()
	var wind := sample_wind(month, hour, rng)
	var look_args := {"date": _date_str(year, doy), "hour": hour, "season": season}
	if a.has("tide"):
		look_args["tide"] = float(a["tide"])
	op_day(look_args)
	var wa := {"preset": preset, "seed": wrng.randi()}
	for k in ["cover", "base_m", "vis_km", "cirrus", "haze", "rain", "wetness"]:
		if a.has(k):
			wa[k] = a[k]
	var res := op_weather(wa)
	if not res.get("ok", false):
		return res
	# 4. Wind: sampled, given, calm, or left alone (false).
	var wa_in = a.get("wind", null)
	if wa_in is Dictionary:
		for k in wa_in:
			wind[k] = wa_in[k]
	elif str(wa_in) == "calm":
		wind = {"speed": 0.0, "from_deg": 0.0, "gust": 0.0, "gusts_per_min": 0.0, "turbulence": "none", "z0": 0.03}
	if not (wa_in is bool and wa_in == false):
		wind["seed"] = int(a.get("seed", 0))
		FixedWingManager.air.set_wind(wind)
	var st := state()
	st["seed"] = int(a.get("seed", 0))
	st["chosen"] = {"date": day["date"], "hour_pst": snappedf(hour, 0.01), "weather": preset,
		"season": snappedf(season, 0.01), "tide_m_navd88": env.look["tide"], "wetness": env.look.get("wetness", 0.0),
		"wind": wind}
	return st


# ================================================================ climate
func _month(year: int, doy: int) -> int:
	var u := Time.get_unix_time_from_datetime_dict({"year": year, "month": 1, "day": 1, "hour": 12,
		"minute": 0, "second": 0}) + (doy - 1) * 86400
	return int(Time.get_datetime_dict_from_unix_time(u)["month"])


func _date_str(year: int, doy: int) -> String:
	var u := Time.get_unix_time_from_datetime_dict({"year": year, "month": 1, "day": 1, "hour": 12,
		"minute": 0, "second": 0}) + (doy - 1) * 86400
	var d := Time.get_datetime_dict_from_unix_time(u)
	return "%04d-%02d-%02d" % [d["year"], d["month"], d["day"]]


## Mid-month interpolation of SEASON_BY_MONTH.
func season_for(doy: int) -> float:
	var m := fposmod((float(doy) - 15.0) / 30.44, 12.0)
	var i := int(m)
	return lerpf(SEASON_BY_MONTH[i], SEASON_BY_MONTH[(i + 1) % 12], m - float(i))


func _pick_preset(month: int, block: int, rng: RandomNumberGenerator) -> String:
	var sky: Dictionary = clim.get("sky", {})
	if sky.is_empty():
		return "clear"
	var names: Array = sky["presets"]
	var probs: Array = sky["months"][month - 1][block]
	var r := rng.randf()
	var acc := 0.0
	var pick := "clear"
	for i in names.size():
		acc += float(probs[i])
		if r <= acc:
			pick = str(names[i])
			break
	if pick == "clear" and rng.randf() < float(CIRRUS_SHARE[month - 1]):
		pick = "high_cirrus"
	return pick


## Wind for month (1-12) and PST hour from the climatology; m/s at 10 m.
func sample_wind(month: int, hour: float, rng: RandomNumberGenerator) -> Dictionary:
	var out := {"speed": 0.0, "from_deg": 0.0, "gust": 0.0, "gusts_per_min": 0.0,
		"gust_s": 4.0, "turbulence": "none", "z0": 0.03}
	var months: Array = wind_clim.get("months", [])
	if months.is_empty():
		return out
	var c: Dictionary = months[month - 1][clampi(int(hour / 3.0), 0, 7)]
	var kt := 0.0
	if rng.randf() < float(c["calm"]):
		# ASOS reports anything under 3 kt as calm: 0-2.5 kt, direction anywhere.
		kt = rng.randf_range(0.0, 2.5)
		out["from_deg"] = rng.randf_range(0.0, 360.0)
	else:
		# Inverse CDF through the p05..p95 quantiles; tails to 3 kt and 1.25 x p95.
		var q: Array = c["q_kt"]
		var u := rng.randf()
		var pts := [0.0]
		var vals := [3.0]
		for i in q.size():
			pts.append(0.05 + 0.1 * i)
			vals.append(float(q[i]))
		pts.append(1.0)
		vals.append(float(q[q.size() - 1]) * 1.25)
		for i in pts.size() - 1:
			if u <= pts[i + 1]:
				kt = lerpf(vals[i], vals[i + 1], (u - pts[i]) / (pts[i + 1] - pts[i]))
				break
		var dirs: Array = c["dir16"]
		var r := rng.randf()
		var acc := 0.0
		var sector := 0
		for i in dirs.size():
			acc += float(dirs[i])
			if r <= acc:
				sector = i
				break
		out["from_deg"] = fposmod(sector * 22.5 + rng.randf_range(-11.25, 11.25), 360.0)
	var speed := kt * KT                # knots -> m/s (1 kt = 0.514444 m/s)
	out["speed"] = snappedf(speed, 0.01)
	# Gusts: a reported gust group (ASOS: peaks >= 10 kt over the lulls) at
	# its measured frequency, else the open-marsh gust factor (~1.4).
	var gust := 0.4 * speed
	if rng.randf() < float(c["gust_p"]):
		gust = maxf(gust, float(c["gust_ex_kt"]) * KT * rng.randf_range(0.8, 1.2))
	out["gust"] = snappedf(gust, 0.01)
	out["gusts_per_min"] = snappedf(0.0 if speed < 1.5 else rng.randf_range(0.5, 2.0), 0.01)
	out["turbulence"] = "none" if speed < 2.0 else ("light" if speed < 7.0 else "moderate")
	# Open grass and marsh, the sea breeze crossing ponds and bay to the NNW.
	out["z0"] = 0.03
	out["kt"] = snappedf(kt, 0.1)
	return out


## Daylight hours (PST) with the sun at least 3 deg up: Vector2(first, last).
func daylight(doy: int) -> Vector2:
	var first := 12.0
	var last := 12.0
	var h := 3.0
	while h < 21.0:
		if sun_elevation(doy, h) >= 3.0:
			if first >= 12.0:
				first = h
			last = h
		h += 0.05
	return Vector2(first, last)


## Sun elevation (deg) at Baylands for day-of-year and PST hour (world_clock's
## NOAA formulas, kept here so the hour can be chosen before it is set).
func sun_elevation(doy: int, hour_pst: float) -> float:
	var lat := float(env.SUN_LATLON[0])
	var lon := float(env.SUN_LATLON[1])
	var g := TAU / 365.0 * (doy - 1 + (hour_pst + 8.0 - 12.0) / 24.0)
	var decl := 0.006918 - 0.399912 * cos(g) + 0.070257 * sin(g) - 0.006758 * cos(2 * g) \
		+ 0.000907 * sin(2 * g) - 0.002697 * cos(3 * g) + 0.00148 * sin(3 * g)
	var eot := 229.18 * (0.000075 + 0.001868 * cos(g) - 0.032077 * sin(g)
		- 0.014615 * cos(2 * g) - 0.040849 * sin(2 * g))
	var solar := hour_pst + (eot + 4.0 * lon + 60.0 * 8.0) / 60.0
	var ha := deg_to_rad(15.0 * (solar - 12.0))
	var la := deg_to_rad(lat)
	return rad_to_deg(asin(sin(la) * sin(decl) + cos(la) * cos(decl) * cos(ha)))


# ===================================================================== tide
## Water level (m above NAVD88) at Coyote Creek / Alviso Slough, NOAA 9414575.
func tide_at(year: int, doy: int, hour_pst: float) -> float:
	var jan1 := Time.get_unix_time_from_datetime_dict({"year": year, "month": 1, "day": 1, "hour": 0,
		"minute": 0, "second": 0})
	var unix := float(jan1) + (doy - 1) * 86400.0 + (hour_pst + 8.0) * 3600.0
	var tdays := (unix - 946728000.0) / 86400.0          # days from J2000.0 (2000-01-01 12:00 UTC)
	var T := tdays / 36525.0
	var ut := fposmod((unix / 3600.0), 24.0)
	var s := 218.3164 + 481267.8812 * T
	var h := 280.4661 + 36000.7698 * T
	var p := 83.3535 + 4069.0137 * T
	var N := 125.0445 - 1934.1363 * T
	var p1 := 282.9384 + 1.7195 * T
	var ast := [15.0 * ut + h - s, s, h, p, -N, p1]
	var z := TIDE_MSL
	for nm in TIDE_HC:
		var A: float = TIDE_HC[nm][0]
		if A <= 0.0:
			continue
		var G: float = TIDE_HC[nm][1]
		var f := 1.0
		var u := 0.0
		var v := 0.0
		if DOODSON.has(nm):
			var fu := _nodal(nm, N)
			f = fu.x
			u = fu.y
			v = _v(nm, ast)
		else:
			for k in COMPOUND[nm]:
				var m: int = COMPOUND[nm][k]
				var fu := _nodal(k, N)
				f *= pow(fu.x, absi(m))
				u += m * fu.y
				v += m * _v(k, ast)
		z += f * A * cos(deg_to_rad(v + u - G))
	return z


func _v(nm: String, ast: Array) -> float:
	var d: Array = DOODSON[nm]
	var v := float(d[6])
	for i in 6:
		v += float(d[i]) * float(ast[i])
	return v


## Schureman's nodal factor f and angle u (deg) for the main constituents.
func _nodal(nm: String, N_deg: float) -> Vector2:
	var n := deg_to_rad(N_deg)
	match nm:
		"M2", "N2", "2N2", "MU2", "NU2", "L2":
			return Vector2(1.0004 - 0.0373 * cos(n) + 0.0002 * cos(2 * n), -2.14 * sin(n))
		"K1":
			return Vector2(1.0060 + 0.1150 * cos(n) - 0.0088 * cos(2 * n) + 0.0006 * cos(3 * n),
				-8.86 * sin(n) + 0.68 * sin(2 * n) - 0.07 * sin(3 * n))
		"O1", "Q1":
			return Vector2(1.0089 + 0.1871 * cos(n) - 0.0147 * cos(2 * n) + 0.0014 * cos(3 * n),
				10.80 * sin(n) - 1.34 * sin(2 * n) + 0.19 * sin(3 * n))
		"K2":
			return Vector2(1.0241 + 0.2863 * cos(n) + 0.0083 * cos(2 * n) - 0.0015 * cos(3 * n),
				-17.74 * sin(n) + 0.68 * sin(2 * n) - 0.04 * sin(3 * n))
	return Vector2(1.0, 0.0)


# ================================================================== presets
## The parameters of a preset: what world_clock/scene_look draw (haze, fog,
## cloud, rain 0..1) plus this script's own: deck cover/base, cirrus, the
## cumulus cell scale, visibility, aerosol optical depth (tau_a, 550 nm),
## wetness.
func _preset_params(preset: String, rng: RandomNumberGenerator, hour: float, doy: int) -> Dictionary:
	var summer := doy > 120 and doy < 290
	var p := {"preset": preset, "haze": 0.0, "fog": 0.0, "cloud": 0.0, "rain": 0.0,
		"cover": 0.0, "base_m": 500.0, "cirrus": 0.0, "cu_scale": 1.0, "vis_km": 45.0,
		"tau_a": 0.08, "fog_top_m": 120.0, "wetness": 0.0}
	match preset:
		"clear":
			p["vis_km"] = rng.randf_range(30.0, 60.0)
			p["tau_a"] = rng.randf_range(0.05, 0.10)
		"hazy":
			p["haze"] = rng.randf_range(0.6, 1.0)
			p["vis_km"] = rng.randf_range(7.0, 15.0)
			p["tau_a"] = rng.randf_range(0.25, 0.45)
		"marine_layer":
			# Base 250-650 m; full in the early morning, breaking after ~9:30.
			p["base_m"] = rng.randf_range(250.0, 650.0)
			var brk := smoothstep(9.0, 12.5, hour)
			p["cover"] = clampf(rng.randf_range(0.93, 1.0) - brk * rng.randf_range(0.2, 0.5), 0.45, 1.0)
			p["cloud"] = p["cover"]
			p["haze"] = 0.25
			p["vis_km"] = rng.randf_range(12.0, 25.0)
			p["wetness"] = 0.12 if hour < 10.0 else 0.04
		"overcast":
			p["base_m"] = rng.randf_range(1200.0, 2500.0)
			p["cover"] = 1.0
			p["cloud"] = 1.0
			p["vis_km"] = rng.randf_range(20.0, 40.0)
		"broken_cumulus":
			p["cloud"] = rng.randf_range(0.35, 0.55)
			p["cu_scale"] = rng.randf_range(1.2, 1.6)
			p["vis_km"] = rng.randf_range(30.0, 55.0)
		"high_cirrus":
			p["cirrus"] = rng.randf_range(0.4, 1.0)
			p["vis_km"] = rng.randf_range(30.0, 50.0)
			p["tau_a"] = 0.08 + 0.12 * float(p["cirrus"])
		"light_rain":
			p["base_m"] = rng.randf_range(500.0, 1100.0)
			p["cover"] = 1.0
			p["cloud"] = 1.0
			p["rain"] = rng.randf_range(0.25, 0.6)
			p["haze"] = 0.2
			p["vis_km"] = rng.randf_range(6.0, 15.0)
			p["wetness"] = 1.0
		"post_rain":
			p["cloud"] = rng.randf_range(0.3, 0.5)
			p["cu_scale"] = rng.randf_range(1.3, 1.8)
			p["vis_km"] = rng.randf_range(60.0, 100.0)
			p["tau_a"] = 0.03
			p["wetness"] = rng.randf_range(0.3, 0.85)
		"fog":
			p["fog"] = 1.0
			p["vis_km"] = rng.randf_range(0.3, 2.0)
			p["fog_top_m"] = rng.randf_range(60.0, 250.0)
			p["cloud"] = 1.0
			p["wetness"] = 0.25
	if not summer and preset == "marine_layer":
		p["base_m"] = float(p["base_m"]) + 200.0
	for k in p:
		if p[k] is float:
			p[k] = snappedf(p[k], 0.001)
	return p


func _apply_weather() -> void:
	if wc != null:
		wc.call("set_weather", {"haze": w["haze"], "fog": w["fog"], "cloud": w["cloud"], "rain": w["rain"]})
	_post_light()
	_post_air(0.0, Vector3.UP, Vector3.DOWN)


# ==================================================================== light
## After world_clock has placed the sun: the beam's colour and strength from
## the air mass, the deck's cover and the fog; the sky fill; the exposure.
func _post_light() -> void:
	if wc == null or w.is_empty():
		return
	var sun: DirectionalLight3D = wc.get("_sun")
	var we: Environment = wc.get("_we")
	if sun == null or we == null:
		return
	var el := float(wc.call("_sun_angles").y)
	var tau_a := float(w.get("tau_a", 0.08))
	# Kasten-Young air mass; Rayleigh optical depth at 650/550/450 nm
	# (0.0088 lambda^-4.05) and aerosol with Angstrom exponent 1.3.
	var e := maxf(el, 0.3)
	var m := 1.0 / (sin(deg_to_rad(e)) + 0.50572 * pow(e + 6.07995, -1.6364))
	var lam := [0.65, 0.55, 0.45]
	var t: Array[float] = []
	for l in lam:
		var tr := 0.0088 * pow(l, -4.05)
		var ta := tau_a * pow(l / 0.55, -1.3)
		t.append(exp(-m * (tr + ta)))
	var tmax: float = maxf(t[0], maxf(t[1], t[2]))
	var col := Color(t[0] / tmax, t[1] / tmax, t[2] / tmax)
	var lum: float = 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2]
	# 2.2 at the reference noon beam (el 60, tau_a 0.08: lum ~0.82).
	var energy: float = 2.2 * lum / 0.82 * smoothstep(-0.5, 2.0, el)
	# A full deck stops the beam; a breaking one lets it through its holes
	# (the deck's own shadow does the rest). Fog: the beam through the layer.
	var cover := float(w.get("cover", 0.0))
	var through := 1.0 - smoothstep(0.93, 0.99, cover) * 0.97
	if float(w.get("fog", 0.0)) > 0.0:
		var beta := 3.912 / (float(w["vis_km"]) * 1000.0)
		var path := float(w.get("fog_top_m", 120.0)) / maxf(sin(deg_to_rad(e)), 0.05)
		through *= exp(-beta * path)
	through *= 1.0 - 0.25 * float(w.get("cirrus", 0.0))
	through *= 1.0 - 0.6 * float(w.get("rain", 0.0))
	energy *= through
	sun.light_energy = energy
	sun.light_color = col
	sun.shadow_enabled = energy > 0.12
	# The near cascades only take casters within the pancake distance toward
	# the sun; the cloud-shadow planes sit up to ~70 m over a low camera.
	sun.directional_shadow_pancake_size = 3000.0
	# Sky fill: brighter as the sky turns to cloud (an overcast is brighter
	# than blue sky and is the only light), and with the sun higher.
	var overcast := clampf(maxf(cover, float(w.get("fog", 0.0))), 0.0, 1.0)
	var sky_day := 0.35 + 0.65 * smoothstep(-2.0, 30.0, el)
	var amb := lerpf(0.5, 1.05, overcast) * sky_day
	we.ambient_light_energy = amb
	# Exposure: a camera's auto-exposure, adapting half-way (in log terms) to
	# the light in the frame: the ground's (beam on the flat + sky fill) and
	# the sky's own, which the sky shader keeps near constant by day (0.6).
	# A low sun or an overcast frame ends a little darker and flatter than
	# noon, as a drone's video does, instead of blowing the sky out.
	var e_ground: float = energy * maxf(sin(deg_to_rad(el)), 0.0) + 0.55 * amb + 0.6
	var e_ref := 2.2 * sin(deg_to_rad(60.0)) + 0.55 * 0.5 + 0.6
	we.tonemap_exposure = float(SceneLook.TUNE["baylands"]["exposure"]) * pow(e_ref / maxf(e_ground, 0.05), 0.5)
	we.tonemap_exposure = clampf(we.tonemap_exposure, 0.3, 1.6)
	# The cumulus shadow layer is world_clock's; a deck draws its own.
	var cs := wc.get_node_or_null("cloud_shadows")
	if cs != null:
		# Low enough that the sun's shadow cascades keep it (SHADOW_PLANE_Y).
		(cs as Node3D).position.y = SHADOW_PLANE_Y
		if cover > 0.0:
			cs.visible = false
	_light = {"sun_energy": snappedf(energy, 0.001), "sun_color": [snappedf(col.r, 0.001), snappedf(col.g, 0.001),
		snappedf(col.b, 0.001)], "ambient": snappedf(amb, 0.001), "exposure": snappedf(we.tonemap_exposure, 0.001),
		"air_mass": snappedf(m, 0.01), "sun_el": snappedf(el, 0.01)}
	_update_deck(el)


## After scene_look's per-change update (light_changed): the visibility, the
## fog layer and the cloud kinds, which scene_look does not know about.
func _post_air(_n: float, _s: Vector3, _m: Vector3) -> void:
	if sl == null or w.is_empty():
		return
	var air: ShaderMaterial = sl.get("_air_mat")
	var sky: ShaderMaterial = sl.get("_sky_mat")
	if air != null:
		# Ground extinction from the visibility (Koschmieder, 2 % contrast:
		# beta = 3.912 / V), the layer's scale height left to the look.
		var vis := float(w.get("vis_km", 45.0))
		if float(w.get("fog", 0.0)) > 0.0:
			air.set_shader_parameter("fog_beta", 3.912 / (vis * 1000.0))
			air.set_shader_parameter("fog_h", float(w.get("fog_top_m", 120.0)) * 0.6)
			vis = 25.0
		air.set_shader_parameter("beta0", 3.912 / (vis * 1000.0))
	if sky != null:
		sky.set_shader_parameter("w_cirrus", float(w.get("cirrus", 0.0)))
		sky.set_shader_parameter("cu_scale", float(w.get("cu_scale", 1.0)))
		if float(w.get("cover", 0.0)) > 0.0 or float(w.get("fog", 0.0)) > 0.0:
			# A deck (or the fog) hides the cumulus layer above it.
			sky.set_shader_parameter("cloud_cover", 0.0)


# ===================================================================== deck
## Cloud shadows are cast by shadow-only planes at this height (m NAVD88),
## not at the clouds' own: Godot's directional shadow cascades are fitted to
## the camera's view, and a caster hundreds of metres above a low camera
## falls outside them (the deck's and the cumulus' shadows vanished below
## ~300 m AGL). The deck's shadow pattern is shifted toward the sun by the
## difference, so the shadow still lies where the real deck's would. Above
## the tallest roofs here (~45 m); a drone above it is not itself shaded.
const SHADOW_PLANE_Y := 70.0
const DECK_SHADER := """
shader_type spatial;
render_mode unshaded, blend_mix, depth_draw_never, cull_disabled, shadows_disabled, fog_disabled;
#include "res://shaders/thermal.gdshaderinc"
uniform sampler2D noise : repeat_enable, filter_linear_mipmap;
uniform float cover = 1.0;
uniform vec3 under = vec3(0.6, 0.62, 0.66);
uniform vec3 horizon = vec3(0.7, 0.72, 0.75);
uniform vec2 drift = vec2(0.0009, 0.0004);
varying vec3 v_world;
void vertex() { v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
float dens(vec2 p) {
    float n = texture(noise, p).r * 0.55 + texture(noise, p * 2.9 + 0.31).r * 0.3 + texture(noise, p * 8.1 + 0.67).r * 0.15;
    return clamp(smoothstep(1.0 - cover, 1.0 - cover + 0.16, n) + step(0.985, cover), 0.0, 1.0);
}
void fragment() {
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) { discard; }
    vec2 p = v_world.xz / 7000.0 + TIME * drift;
    float a = dens(p);
    // Stratus from below: soft mottling, thicker cells darker, thin edges
    // bright where the sun soaks through.
    float dist = length(v_world.xz - CAMERA_POSITION_WORLD.xz);
    // Mottling fades with distance (seen edge-on it would only alias).
    float th = texture(noise, p * 3.1 + 0.19).r;
    float mot = 1.0 - smoothstep(2000.0, 15000.0, dist);
    vec3 c = under * (1.0 + mot * 0.30 * (0.5 - th)) * mix(1.22, 0.95, a);
    c = mix(c, horizon, smoothstep(4000.0, 45000.0, dist) * 0.85);
    ALBEDO = c;
    ALPHA = a * (1.0 - smoothstep(52000.0, 60000.0, dist));
}
"""

const DECK_SHADOW_SHADER := """
shader_type spatial;
render_mode cull_disabled;
uniform sampler2D noise : repeat_enable, filter_linear_mipmap;
uniform float cover = 1.0;
uniform vec2 drift = vec2(0.0009, 0.0004);
uniform vec2 shift = vec2(0.0);    // toward the sun, to the deck's real height
varying vec3 v_world;
void vertex() { v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    vec2 p = (v_world.xz + shift) / 7000.0 + TIME * drift;
    float n = texture(noise, p).r * 0.55 + texture(noise, p * 2.9 + 0.31).r * 0.3 + texture(noise, p * 8.1 + 0.67).r * 0.15;
    ALBEDO = vec3(1.0);
    ALPHA = clamp(smoothstep(1.0 - cover, 1.0 - cover + 0.16, n) + step(0.985, cover), 0.0, 1.0);
    ALPHA_SCISSOR_THRESHOLD = 0.5;
}
"""


func _build_deck() -> void:
	var noise := FastNoiseLite.new()
	noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	noise.frequency = 0.01
	noise.fractal_octaves = 5
	noise.fractal_gain = 0.5
	noise.seed = 3307
	var nt := NoiseTexture2D.new()
	nt.width = 512
	nt.height = 512
	nt.seamless = true
	nt.generate_mipmaps = true
	nt.noise = noise
	var pm := PlaneMesh.new()
	pm.size = Vector2(124000.0, 124000.0)
	_deck_mat = ShaderMaterial.new()
	_deck_mat.shader = Shader.new()
	_deck_mat.shader.code = DECK_SHADER
	_deck_mat.set_shader_parameter("noise", nt)
	_deck = MeshInstance3D.new()
	_deck.name = "stratus_deck"
	_deck.mesh = pm
	_deck.material_override = _deck_mat
	_deck.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_deck.extra_cull_margin = 16384.0
	_deck.visible = false
	add_child(_deck)
	_deck_shadow_mat = ShaderMaterial.new()
	_deck_shadow_mat.shader = Shader.new()
	_deck_shadow_mat.shader.code = DECK_SHADOW_SHADER
	_deck_shadow_mat.set_shader_parameter("noise", nt)
	_deck_shadow = MeshInstance3D.new()
	_deck_shadow.name = "stratus_shadow"
	var sm := PlaneMesh.new()
	# Shadows reach 9 km (LightingKit); the region and its edges are inside.
	sm.size = Vector2(26000.0, 26000.0)
	_deck_shadow.mesh = sm
	_deck_shadow.material_override = _deck_shadow_mat
	_deck_shadow.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY
	_deck_shadow.visible = false
	add_child(_deck_shadow)


## The deck's underside: the overcast's own grey (scene_look's air_light),
## darker for a thick low deck and in rain, warmer with a low sun.
func _update_deck(el: float) -> void:
	var cover := float(w.get("cover", 0.0))
	_deck.visible = cover > 0.01
	_deck_shadow.visible = cover > 0.01 and cover < 0.985
	if cover <= 0.01:
		return
	var y := float(w.get("base_m", 500.0))
	_deck.position = Vector3(0.0, y, 0.0)
	_deck_shadow.position = Vector3(0.0, SHADOW_PLANE_Y, 0.0)
	var sd: Vector3 = wc.call("sun_dir")
	var k := (y - SHADOW_PLANE_Y) / maxf(sd.y, 0.05)
	_deck_shadow_mat.set_shader_parameter("shift", Vector2(sd.x, sd.z) * k)
	var s := sin(deg_to_rad(maxf(el, 0.0)))
	var lit := 0.55 + 0.45 * smoothstep(0.0, 0.5, s)
	var under := Color(0.70, 0.73, 0.77) * lit * (0.80 - 0.25 * float(w.get("rain", 0.0)))
	var warm := 1.0 - smoothstep(4.0, 18.0, el)
	under = under.lerp(Color(0.80, 0.70, 0.62) * lit * 0.8, 0.35 * warm)
	_deck_mat.set_shader_parameter("cover", cover)
	_deck_mat.set_shader_parameter("under", Vector3(under.r, under.g, under.b))
	var hz := Color(0.70, 0.73, 0.77) * lit * 0.82
	_deck_mat.set_shader_parameter("horizon", Vector3(hz.r, hz.g, hz.b))
	_deck_shadow_mat.set_shader_parameter("cover", cover)

