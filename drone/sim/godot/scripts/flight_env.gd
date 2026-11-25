## The air and the signals an aircraft flies through, for
## fixedwing_manager.gd. Wind with gusts and turbulence, a GPS receiver that
## suffers in urban canyons and near jammers, and the radio link to the ground
## station over the terrain. Everything defaults to off (calm air, a perfect
## fix, a perfect link), so a client that never sets them flies exactly as
## before. Set by IPC: fw_set_wind, fw_set_gps, fw_set_link (ipc_server.gd).
##
## Frames are ENU metres, like the rest of the fixed-wing sim.
extends RefCounted

const G := 9.81

# ------------------------------------------------------------------ wind
## Mean wind at 10 m above ground (m/s) and the direction it blows FROM
## (degrees clockwise from north, the meteorological convention).
var wind_speed := 0.0
var wind_from_deg := 0.0
## Peak of the discrete gusts (m/s, on top of the mean), and how often one
## starts (per minute). A gust is a 1-cosine pulse over gust_s seconds.
var gust_peak := 0.0
var gust_per_min := 0.0
var gust_s := 4.0
## Turbulence: "none", "light", "moderate" or "severe" (MIL-F-8785C intensity
## at low altitude: sigma_w = 0.1 * W20 scaled by intensity).
var turbulence := "none"
## Surface roughness length (m) for the log wind profile: ~0.0002 over open
## sea, ~0.03 over open desert or farmland, ~0.5-1 over a city.
var z0 := 0.03

var _rng := RandomNumberGenerator.new()
var _turb: Dictionary = {}    # id -> Vector3 (u, v, w) Gauss-Markov state
var _gust: Dictionary = {}    # id -> [t_start, peak, dir_offset]
var _t := 0.0


func _init() -> void:
	_rng.seed = 20261124


func set_wind(req: Dictionary) -> Dictionary:
	wind_speed = maxf(0.0, float(req.get("speed", wind_speed)))
	wind_from_deg = fmod(float(req.get("from_deg", wind_from_deg)), 360.0)
	gust_peak = maxf(0.0, float(req.get("gust", gust_peak)))
	gust_per_min = maxf(0.0, float(req.get("gusts_per_min", gust_per_min)))
	gust_s = clampf(float(req.get("gust_s", gust_s)), 0.5, 30.0)
	turbulence = str(req.get("turbulence", turbulence))
	z0 = clampf(float(req.get("z0", z0)), 0.0001, 2.0)
	if req.has("seed"):
		_rng.seed = int(req["seed"])
	return wind_state()


func wind_state() -> Dictionary:
	return {"speed": wind_speed, "from_deg": wind_from_deg, "gust": gust_peak,
			"gusts_per_min": gust_per_min, "gust_s": gust_s, "turbulence": turbulence, "z0": z0}


func calm() -> bool:
	return wind_speed <= 0.0 and gust_peak <= 0.0 and turbulence == "none"


## The mean wind (ENU, m/s, the direction it blows TOWARD) at `agl` metres above
## the ground, by the log law: weaker near the ground, ~W20 above it.
func mean_wind(agl: float) -> Vector3:
	if wind_speed <= 0.0:
		return Vector3.ZERO
	var h := maxf(agl, z0 * 2.0 + 0.5)
	var k := log(h / z0) / log(10.0 / z0)
	var to := deg_to_rad(wind_from_deg + 180.0)
	return Vector3(sin(to), cos(to), 0.0) * wind_speed * clampf(k, 0.0, 2.0)


func _sigma(agl: float) -> Vector3:
	var scale: float = float({"none": 0.0, "light": 1.0, "moderate": 2.0, "severe": 4.0}.get(turbulence, 0.0))
	if scale <= 0.0:
		return Vector3.ZERO
	# MIL-F-8785C low altitude (h in feet in the standard, converted here).
	var w20 := maxf(wind_speed, 5.0 * scale)
	var hf := clampf(agl, 3.0, 300.0) * 3.281
	var sw: float = 0.1 * w20 * scale * 0.5
	var su: float = sw / pow(0.177 + 0.000823 * hf, 0.4)
	return Vector3(su, su, sw)


## Wind plus gusts plus turbulence for aircraft `id` this tick (ENU m/s).
## Turbulence is a first-order Gauss-Markov process per axis with the Dryden
## length scales, so it is correlated over L / V seconds, not white noise.
func air_motion(id: String, agl: float, airspeed: float, dt: float) -> Vector3:
	_t += dt / maxf(1.0, float(_turb.size()))
	var w := mean_wind(agl)
	# Discrete gusts, along the mean wind with some scatter.
	if gust_peak > 0.0:
		var g: Array = _gust.get(id, [])
		if g.is_empty() and _rng.randf() < gust_per_min / 60.0 * dt:
			g = [_t, gust_peak * _rng.randf_range(0.5, 1.0), _rng.randf_range(-0.6, 0.6)]
			_gust[id] = g
		if not g.is_empty():
			var age := _t - float(g[0])
			if age > gust_s:
				_gust.erase(id)
			else:
				var to := deg_to_rad(wind_from_deg + 180.0) + float(g[2])
				var amp := float(g[1]) * 0.5 * (1.0 - cos(TAU * age / gust_s))
				w += Vector3(sin(to), cos(to), 0.0) * amp
	var sig := _sigma(agl)
	if sig != Vector3.ZERO:
		var hf := clampf(agl, 3.0, 300.0) * 3.281
		var lu := hf / pow(0.177 + 0.000823 * hf, 1.2) / 3.281
		var lw := maxf(agl, 3.0)
		var v := maxf(airspeed, 3.0)
		var s: Vector3 = _turb.get(id, Vector3.ZERO)
		var au := exp(-v * dt / lu)
		var aw := exp(-v * dt / lw)
		s.x = au * s.x + sig.x * sqrt(1.0 - au * au) * _rng.randfn()
		s.y = au * s.y + sig.y * sqrt(1.0 - au * au) * _rng.randfn()
		s.z = aw * s.z + sig.z * sqrt(1.0 - aw * aw) * _rng.randfn()
		_turb[id] = s
		w += s
	return w


func forget(id: String) -> void:
	_turb.erase(id)
	_gust.erase(id)
	_gps.erase(id)
	_link.erase(id)


# ------------------------------------------------------------------- GPS
var gps_on := false
var gps_sigma_m := 1.2          # horizontal 1-sigma in open sky
var jammers: Array = []         # [{e, n, radius_m, spoof: [de, dn] or null}]
var _gps: Dictionary = {}       # id -> {bias: Vector3, t: float, last: Dictionary}


func set_gps(req: Dictionary) -> Dictionary:
	gps_on = bool(req.get("on", gps_on))
	gps_sigma_m = maxf(0.0, float(req.get("sigma_m", gps_sigma_m)))
	if req.has("jammers"):
		jammers = req["jammers"]
	return {"on": gps_on, "sigma_m": gps_sigma_m, "jammers": jammers}


## How much of the sky round the aircraft is blocked by structure: rays at 8
## azimuths, 20 deg up, 250 m long. `cast(from, to)` is the manager's raycast.
func _sky_blocked(pos: Vector3, cast: Callable) -> float:
	var blocked := 0
	for k in 8:
		var a := TAU * float(k) / 8.0
		var d := Vector3(cos(a) * cos(deg_to_rad(20.0)), sin(a) * cos(deg_to_rad(20.0)), sin(deg_to_rad(20.0)))
		if bool(cast.call(pos, pos + d * 250.0)):
			blocked += 1
	return float(blocked) / 8.0


## What the receiver reports this tick: the true position plus a slowly
## wandering bias and noise, worse the more sky is blocked (multipath), no fix
## inside a jammer's radius, or a fix shifted by a spoofer's offset.
func gps_fix(id: String, pos: Vector3, dt: float, cast: Callable) -> Dictionary:
	var g: Dictionary = _gps.get(id, {"bias": Vector3.ZERO, "t": 0.0, "blocked": 0.0})
	g["t"] = float(g["t"]) + dt
	if float(g["t"]) >= 0.5 or not g.has("last"):
		g["t"] = 0.0
		g["blocked"] = _sky_blocked(pos, cast)
	var blocked := float(g["blocked"])
	for j: Dictionary in jammers:
		var dist := Vector2(pos.x - float(j["e"]), pos.y - float(j["n"])).length()
		if dist < float(j.get("radius_m", 0.0)):
			var sp = j.get("spoof", null)
			if sp == null:
				g["last"] = {"fix": "none", "sats": 0, "hdop": 99.0, "jammed": true}
				_gps[id] = g
				return g["last"]
			var off := Vector3(float(sp[0]), float(sp[1]), 0.0)
			g["last"] = {"fix": "3d", "e": pos.x + off.x, "n": pos.y + off.y, "u": pos.z,
						 "sats": 9, "hdop": 0.9, "spoofed": true}
			_gps[id] = g
			return g["last"]
	var sats := int(round(lerpf(16.0, 3.0, blocked)))
	if sats < 4:
		g["last"] = {"fix": "none", "sats": sats, "hdop": 99.0, "sky_blocked": blocked}
		_gps[id] = g
		return g["last"]
	var sigma := gps_sigma_m * (1.0 + 4.0 * blocked)
	var a := exp(-dt / 30.0)    # bias decorrelates over ~30 s
	var b: Vector3 = g["bias"]
	b = b * a + Vector3(_rng.randfn(), _rng.randfn(), 1.6 * _rng.randfn()) * sigma * sqrt(1.0 - a * a)
	g["bias"] = b
	var hdop := 0.8 * (1.0 + 3.0 * blocked)
	g["last"] = {"fix": "3d", "e": pos.x + b.x + 0.3 * sigma * _rng.randfn(),
				 "n": pos.y + b.y + 0.3 * sigma * _rng.randfn(), "u": pos.z + b.z,
				 "sats": sats, "hdop": hdop, "sky_blocked": blocked}
	_gps[id] = g
	return g["last"]


# ------------------------------------------------------------------ link
var link_on := false
var station := Vector3.ZERO     # ENU of the ground antenna
var tx_dbm := 20.0              # 100 mW
var freq_mhz := 2400.0
var floor_dbm := -95.0          # receiver sensitivity: below this, no link
var _link: Dictionary = {}      # id -> {t, last}


func set_link(req: Dictionary) -> Dictionary:
	link_on = bool(req.get("on", link_on))
	if req.has("station"):
		var s: Array = req["station"]
		station = Vector3(float(s[0]), float(s[1]), float(s[2]))
	tx_dbm = float(req.get("tx_dbm", tx_dbm))
	freq_mhz = float(req.get("freq_mhz", freq_mhz))
	floor_dbm = float(req.get("floor_dbm", floor_dbm))
	return {"on": link_on, "station": [station.x, station.y, station.z], "tx_dbm": tx_dbm,
			"freq_mhz": freq_mhz, "floor_dbm": floor_dbm}


## Received signal at the aircraft: free-space loss over the distance, 25 dB
## more when terrain or a building stands in the line of sight, and fading.
func link_state(id: String, pos: Vector3, dt: float, cast: Callable) -> Dictionary:
	var l: Dictionary = _link.get(id, {"t": 1.0})
	l["t"] = float(l["t"]) + dt
	if float(l["t"]) >= 0.2 or not l.has("last"):
		l["t"] = 0.0
		var d := maxf(1.0, pos.distance_to(station))
		var fspl := 20.0 * log(d / 1000.0) / log(10.0) + 20.0 * log(freq_mhz) / log(10.0) + 32.44
		var los: bool = not bool(cast.call(station, pos))
		var rssi := tx_dbm - fspl - (0.0 if los else 25.0) + 2.0 * _rng.randfn()
		var q := clampf((rssi - floor_dbm) / 20.0, 0.0, 1.0)
		l["last"] = {"rssi_dbm": snappedf(rssi, 0.1), "los": los, "range_m": snappedf(d, 0.1),
					 "quality": snappedf(q, 0.01), "up": rssi > floor_dbm}
	_link[id] = l
	return l["last"]
