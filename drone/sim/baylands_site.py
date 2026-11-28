"""Baylands Park site limits for training code: airspace, the UAS Facility
Map ceilings, 14 CFR 99.7 security areas, the stadium TFR, the park and its
wetlands preserve, launch pads and the wind climatology, from
drone/sim/godot/assets/baylands/site.json (drone/sim/tools/fetch_baylands_site.py).

Reference data for simulation, NOT a legal determination. Before any real
flight the pilot must check B4UFLY/LAANC, NOTAMs and TFRs, and get permission
from the park.

Same answers as the Godot env ops "check" / "check_path"
(scripts/baylands_site.gd), except what needs the 3D scene: wire clearance
and roofs are Godot-only.

Frame: scene ENU metres around the datum, identical to env_baylands.gd's
geo_to_sim(). Heights: metres above NAVD88; 'MSL' altitudes are taken as
NAVD88 heights. Units: 1 ft = 0.3048 m exactly.

    import baylands_site as bs
    site = bs.load()
    r = site.check(0.0, 0.0, alt_agl=100.0)        # metres
    r["ok"], r["violations"], r["airspace"]["class"], r["uasfm"]["ceiling_ft"]
    p = site.check_path([(0, 0, 30), (400, 0, 30)], alt_ref="agl")

Pure standard library (no numpy).
"""
from __future__ import annotations

import array
import json
import math
import zlib
from pathlib import Path

DISCLAIMER = ("Reference data for simulation, NOT a legal determination. Before any real "
              "flight the pilot must check B4UFLY/LAANC, NOTAMs and TFRs, and get permission "
              "from the park.")

ASSETS = Path(__file__).resolve().parent / "godot" / "assets" / "baylands"
DATUM_LAT, DATUM_LON = 37.4125, -121.9970
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
FT = 0.3048                         # metres per foot, exact
PART107_MAX_AGL_M = 400 * FT        # 121.92 m
CLASS_ORDER = "BCDE"                # most restrictive first; nothing = G


# ------------------------------------------------------------------ frame

def geo_to_enu(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) degrees -> scene (e, n) metres, exactly as env_baylands.gd."""
    return ((lon - DATUM_LON) * M_PER_DEG_LON, (lat - DATUM_LAT) * M_PER_DEG_LAT)


def enu_to_geo(e: float, n: float) -> tuple[float, float]:
    """Scene (e, n) metres -> (lat, lon) degrees, exactly as env_baylands.gd."""
    return (DATUM_LAT + n / M_PER_DEG_LAT, DATUM_LON + e / M_PER_DEG_LON)


def godot_pos(e: float, n: float, y: float = 0.0) -> tuple[float, float, float]:
    return (e, y, -n)


# --------------------------------------------------------------- geometry

def point_in_ring(e: float, n: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for k in range(len(ring)):
        ax, ay = ring[k]
        bx, by = ring[j]
        if (ay > n) != (by > n) and e < (bx - ax) * (n - ay) / (by - ay) + ax:
            inside = not inside
        j = k
    return inside


def point_in_poly(e: float, n: float, poly) -> bool:
    """poly = [outer, hole, ...]."""
    if not point_in_ring(e, n, poly[0]):
        return False
    return not any(point_in_ring(e, n, h) for h in poly[1:])


def _bbox(rings) -> tuple[float, float, float, float]:
    es = [p[0] for r in rings for p in r]
    ns = [p[1] for r in rings for p in r]
    return min(es), min(ns), max(es), max(ns)


def _in_bbox(e, n, b) -> bool:
    return b[0] <= e <= b[2] and b[1] <= n <= b[3]


# ---------------------------------------------------------------- terrain

class Terrain:
    """The committed 2 m ground grid (terrain.zi16), sampled exactly like
    env_baylands.gd's ground_height()."""

    def __init__(self, folder: Path = ASSETS):
        m = json.loads((folder / "terrain.json").read_text())
        self.w, self.h = int(m["size_px"][0]), int(m["size_px"][1])
        b = m["bounds_enu"]
        self.x0, self.y0, self.x1, self.y1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        self.res = float(m["res_m"])
        self.q = float(m.get("scale_m", 0.1))
        raw = zlib.decompress((folder / "terrain.zi16").read_bytes())
        self.t = array.array("h")
        self.t.frombytes(raw[: self.w * self.h * 2])
        if array.array("h", [1]).tobytes() != b"\x01\x00":
            self.t.byteswap()                                 # the file is little-endian

    def height(self, e: float, n: float) -> float:
        fx = min(max((e - self.x0) / self.res - 0.5, 0.0), float(self.w - 1))
        fy = min(max((self.y1 - n) / self.res - 0.5, 0.0), float(self.h - 1))
        x0, y0 = int(fx), int(fy)
        x1, y1 = min(x0 + 1, self.w - 1), min(y0 + 1, self.h - 1)
        tx, ty = fx - x0, fy - y0
        t, w, q = self.t, self.w, self.q
        a = t[y0 * w + x0] * q + (t[y0 * w + x1] * q - t[y0 * w + x0] * q) * tx
        b = t[y1 * w + x0] * q + (t[y1 * w + x1] * q - t[y1 * w + x0] * q) * tx
        return a + (b - a) * ty


# ------------------------------------------------------------------- site

def _limit_m(lim: dict, ground: float, top: bool) -> float:
    """An airspace floor/ceiling -> metres above NAVD88 at this ground."""
    ref = lim.get("ref")
    if ref == "SFC":
        return -math.inf if not top else ground
    if ref == "AGL":
        return ground + float(lim["m"])
    return float(lim["m"])


class Site:
    def __init__(self, data: dict, terrain: Terrain | None = None, assets: Path = ASSETS):
        self.data = data
        self.disclaimer = data.get("disclaimer", DISCLAIMER)
        self._terrain = terrain
        self._assets = assets
        self.park_ring = data["park"]["ring"]
        self.preserve_ring = data["preserve"]["ring"]
        self._airspace = [(a, [_bbox([p[0]]) for p in a["polys"]]) for a in data["airspace"]]
        self._cells = [(c, _bbox([c["ring"]])) for c in data["uasfm"]["cells"]]
        self._security = [(s, [_bbox([p[0]]) for p in s["polys"]]) for s in data["security"]]
        self.pads = data.get("pads", [])

    # -- ground
    @property
    def terrain(self) -> Terrain:
        if self._terrain is None:
            self._terrain = Terrain(self._assets)
        return self._terrain

    def ground_height(self, e: float, n: float) -> float:
        return self.terrain.height(e, n)

    # -- single point
    def in_park(self, e: float, n: float) -> bool:
        return point_in_ring(e, n, self.park_ring)

    def in_preserve(self, e: float, n: float) -> bool:
        return point_in_ring(e, n, self.preserve_ring)

    def uasfm_cell(self, e: float, n: float) -> dict | None:
        for c, b in self._cells:
            if _in_bbox(e, n, b) and point_in_ring(e, n, c["ring"]):
                return c
        return None

    def check(self, e: float, n: float, alt_agl: float | None = None, alt_msl: float | None = None,
              ground: float | None = None, tfr_active: bool = False) -> dict:
        """Which limits apply at scene (e, n) and this altitude (metres; give
        alt_agl OR alt_msl). tfr_active: treat the event-day stadium TFR as on."""
        g = self.ground_height(e, n) if ground is None else float(ground)
        if alt_msl is None:
            agl = float(alt_agl or 0.0)
            msl = g + agl
        else:
            msl = float(alt_msl)
            agl = msl - g
        lat, lon = enu_to_geo(e, n)
        viol: list[dict] = []
        warn: list[str] = []
        ceilings = [("part107", PART107_MAX_AGL_M)]
        if agl < 0.0:
            viol.append({"code": "below_ground", "text": "below the ground (%.1f m AGL)" % agl})

        # Airspace classes
        vols = []
        for a, bbs in self._airspace:
            if not any(_in_bbox(e, n, bb) and point_in_poly(e, n, p) for p, bb in zip(a["polys"], bbs)):
                continue
            lo = _limit_m(a["floor"], g, False)
            hi = _limit_m(a["ceiling"], g, True)
            if lo <= msl <= hi:
                vols.append({"name": a["name"], "class": a["class"], "type": a["type"],
                             "floor_msl_m": None if lo == -math.inf else round(lo, 1),
                             "ceiling_msl_m": round(hi, 1), "hours": a.get("hours")})
        cls = "G"
        for c in CLASS_ORDER:
            if any(v["class"] == c for v in vols):
                cls = c
                break
        controlled = cls in "BCD" or any(v["type"] == "CLASS_E2" for v in vols)

        # UAS Facility Map
        um = None
        cell = self.uasfm_cell(e, n)
        if cell is not None:
            over = agl > cell["ceiling_m"] + 1e-6
            um = {"ceiling_ft": cell["ceiling_ft"], "ceiling_m": cell["ceiling_m"], "laanc": cell["laanc"],
                  "airports": [p["id"] for p in cell["airports"]], "over": over}
            ceilings.append(("uasfm", cell["ceiling_m"]))
            if over and controlled:
                viol.append({"code": "uasfm_ceiling",
                             "text": "above the UAS facility map ceiling %d ft (%.1f m) AGL" % (cell["ceiling_ft"], cell["ceiling_m"])})
        if controlled:
            if cell is not None and cell["laanc"]:
                warn.append("controlled airspace (Class %s): FAA authorization needed; LAANC up to %d ft AGL"
                            % (cls, cell["ceiling_ft"]))
            else:
                warn.append("controlled airspace (Class %s): FAA authorization needed; no LAANC here (FAA DroneZone)" % cls)
        if agl > PART107_MAX_AGL_M + 1e-6:
            viol.append({"code": "part107_ceiling", "text": "above 400 ft (121.92 m) AGL, 14 CFR 107.51"})

        # 14 CFR 99.7 security areas
        sec = []
        for s, bbs in self._security:
            if not any(_in_bbox(e, n, bb) and point_in_poly(e, n, p) for p, bb in zip(s["polys"], bbs)):
                continue
            inside = agl <= float(s["ceiling"]["m"]) + 1e-6
            sec.append({"name": s["name"], "ceiling_ft": s["ceiling"]["ft"], "ceiling_m": s["ceiling"]["m"],
                        "inside": inside})
            if inside:
                ceilings.append(("security", 0.0))
                viol.append({"code": "security_99_7", "text": "inside the 14 CFR 99.7 area %s (surface to %s ft AGL)"
                             % (s["name"], s["ceiling"]["ft"])})

        # Stadium TFR (event days only)
        tfr = []
        for t in self.data.get("tfr", []):
            d = math.hypot(e - t["centre"][0], n - t["centre"][1])
            inside = d <= t["radius_m"] and agl <= t["ceiling"]["m"]
            tfr.append({"name": t["name"], "inside": inside, "distance_m": round(d, 1), "active": bool(tfr_active)})
            if inside and tfr_active:
                ceilings.append(("tfr", 0.0))
                viol.append({"code": "tfr", "text": "inside the %s stadium TFR (event day)" % t["name"]})
            elif inside:
                warn.append("inside the %s stadium TFR area: prohibited on event days (1 h before to 1 h after)"
                            % t["name"])

        park = self.in_park(e, n)
        pres = self.in_preserve(e, n)
        if pres:
            warn.append("over the wetlands preserve: no launch or landing; keep wildlife undisturbed")
        if not park and not pres:
            warn.append("outside Baylands Park")
        limit = min(ceilings, key=lambda c: c[1])
        return {
            "e": e, "n": n, "lat": round(lat, 7), "lon": round(lon, 7),
            "ground_m": round(g, 2), "alt_agl_m": round(agl, 2), "alt_msl_m": round(msl, 2),
            "alt_agl_ft": round(agl / FT, 1), "alt_msl_ft": round(msl / FT, 1),
            "airspace": {"class": cls, "controlled": controlled, "volumes": vols},
            "uasfm": um, "security": sec, "tfr": tfr,
            "park": park, "preserve": pres,
            "park_hours": self.data["park"]["hours"]["text"] if park else None,
            "max_agl_m": round(limit[1], 2), "max_agl_by": limit[0],
            "violations": viol, "warnings": warn, "ok": not viol,
            "disclaimer": self.disclaimer,
        }

    # -- a path
    def check_path(self, points, alt_ref: str = "agl", step: float = 5.0, tfr_active: bool = False) -> dict:
        """points: [(e, n, alt), ...] metres, alt above ground ('agl') or NAVD88
        ('msl'); sampled every <= step m (3D) along each leg, altitude linear.
        s_m / length_m are 3D distances along the path."""
        pts = [tuple(float(v) for v in p) for p in points]
        if len(pts) < 1:
            return {"ok": True, "samples": 0, "length_m": 0.0, "violations": [], "disclaimer": self.disclaimer}
        s_total = 0.0
        samples = []
        for k in range(len(pts)):
            if k == 0:
                samples.append((pts[0], 0.0, 0))
                continue
            a, b = pts[k - 1], pts[k]
            seg = math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2)
            m = max(1, int(math.ceil(seg / step)))
            for i in range(1, m + 1):
                t = i / m
                samples.append(((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t),
                                s_total + seg * t, k - 1))
            s_total += seg
        spans: dict[str, list] = {}       # code -> [[s_start, s_end], ...]
        prev: set = set()
        first = None
        max_agl = -math.inf
        classes = set()
        zones = set()
        for (e, n, alt), s, leg in samples:
            r = self.check(e, n, alt_agl=alt if alt_ref == "agl" else None,
                           alt_msl=alt if alt_ref != "agl" else None, tfr_active=tfr_active)
            max_agl = max(max_agl, r["alt_agl_m"])
            classes.add(r["airspace"]["class"])
            if r["preserve"]:
                zones.add("preserve")
            for x in r["security"]:
                if x["inside"]:
                    zones.add(x["name"])
            codes = {v["code"] for v in r["violations"]}
            for c in codes:
                if c in prev:
                    spans[c][-1][1] = s
                else:
                    spans.setdefault(c, []).append([s, s])
            prev = codes
            if codes and first is None:
                first = {"s_m": round(s, 1), "leg": leg, "e": round(e, 1), "n": round(n, 1),
                         "violations": r["violations"]}
        viol = {c: [[round(a, 1), round(b, 1)] for a, b in sp] for c, sp in spans.items()}
        return {"ok": first is None, "samples": len(samples), "length_m": round(s_total, 1),
                "first_violation": first, "violations": viol,
                "max_agl_m": round(max_agl, 2), "classes": sorted(classes), "zones": sorted(zones),
                "disclaimer": self.disclaimer}

    # -- wind
    def wind(self, month: int, hour: int) -> dict:
        """Climatology at NUQ for a month (1-12) and local hour (0-23): mean and
        p90 speed (kt and m/s), calm %, vector-mean direction it blows FROM."""
        t = self.data["wind_climatology"]["by_month_hour"]
        m, h = month - 1, hour % 24
        return {k: t[k][m][h] for k in ("mean_kt", "p90_kt", "mean_ms", "p90_ms", "calm_pct", "from_deg", "n")}


def load(path: str | Path | None = None, terrain: Terrain | None = None) -> Site:
    p = Path(path) if path else ASSETS / "site.json"
    return Site(json.loads(p.read_text()), terrain=terrain, assets=p.parent)
