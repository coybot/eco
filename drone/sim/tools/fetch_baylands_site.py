#!/usr/bin/env python3
"""Baylands Park SITE facts for the sim: the limits a policy trained in the
Baylands scene must respect when it flies the real field, as data.

Writes drone/sim/godot/assets/baylands/site.json, read by
scripts/baylands_site.gd (env ops "site", "check", "check_path") and by
drone/sim/baylands_site.py (the same checks for training code).

THIS IS REFERENCE DATA FOR SIMULATION, NOT A LEGAL DETERMINATION. Before any
real flight the pilot must check B4UFLY/LAANC, NOTAMs and TFRs, and get
permission from the park.

Sources (all fetched here, programmatically, each with its retrieval date):
  osm      OpenStreetMap via Overpass (ODbL): the park (way 28640922) and the
           seasonal wetlands east of it (way 28640930).
  faa      FAA public ArcGIS services (public domain, US Government):
           Class_Airspace, FAA_UAS_FacilityMap_Data, DoD_Mar_13 (14 CFR 99.7
           special security instructions), Stadiums (FDC NOTAM 0/0367 venues),
           Digital_Obstacle_File; and the layers that came back EMPTY over the
           region (listed in site.json so the absence is recorded too).
  notam    FDC 0/0367 text from tfr.faa.gov (stadium TFR dimensions).
  wind     Iowa Environmental Mesonet ASOS archive, station NUQ (Moffett),
           routine hourly METARs, local time, 2021-2025.
  pads     launch/landing pads chosen from the 0.5 m lidar grids
           (bake_baylands_lidar.py) and features.json: flat ground, no tree,
           pole or roof within 15 m, off paths, >60 m from power lines,
           outside the wetlands. Needs --rasters (hoopoe: ~/bay/data/rasters);
           without it the pads already in site.json are kept.

UNITS: the FAA publishes feet. Every height is kept in feet AS PUBLISHED and
in metres, converted explicitly with FT = 0.3048 m (exact). Wind in knots and
m/s (1 kt = 0.514444 m/s). Horizontal positions are scene ENU metres.

FRAME: ENU metres around DATUM, flat-earth formula identical to
env_baylands.gd's geo_to_sim() (and fetch_baylands.enu()).

    uv run --no-project --with numpy --with scipy --with shapely \\
        --with scikit-image --with pillow python fetch_baylands_site.py \\
        --work ~/bay/site/work --rasters ~/bay/data/rasters \\
        --ortho ~/bay/out/_hi/ortho.jpg --nir ~/bay/out/_hi/nir.jpg \\
        --out ../godot/assets/baylands/site.json
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import io
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATUM_LAT, DATUM_LON = 37.4125, -121.9970
REGION = (-2500.0, -2400.0, 2500.0, 2400.0)          # e0, n0, e1, n1 (scene)
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(DATUM_LAT))
FT = 0.3048                                           # metres per foot, exact
NM = 1852.0                                           # metres per nautical mile, exact
KT = 1852.0 / 3600.0                                  # m/s per knot
UA = "eco-sim/1.0 (drone simulator; baylands site builder)"

FAA = "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services"
OVERPASS = "https://overpass-api.de/api/interpreter"
IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
NOTAM_XML = "https://tfr.faa.gov/download/detail_0_0367.xml"
CITY_PAGE = ("https://web.archive.org/web/20260515121152/https://www.sunnyvale.ca.gov/"
             "recreation-and-community/parks-and-trails/baylands-park")
RULES_PAGE = ("https://web.archive.org/web/20260819174825/https://www.sunnyvale.ca.gov/"
              "recreation-and-community/parks-and-trails/park-use-rules")

PARK_WAY = 28640922
PRESERVE_WAY = 28640930
# Airspace, obstacles and restrictions are kept out to this pad around the
# scene: a fixed wing turning at the edge can leave it.
CLIP_PAD = 1500.0

DISCLAIMER = ("Reference data for simulation, NOT a legal determination. Before any real "
              "flight the pilot must check B4UFLY/LAANC, NOTAMs and TFRs, and get permission "
              "from the park.")


# ------------------------------------------------------------------ frame

def enu(lat: float, lon: float) -> tuple[float, float]:
    return ((lon - DATUM_LON) * M_PER_DEG_LON, (lat - DATUM_LAT) * M_PER_DEG_LAT)


def geo(e: float, n: float) -> tuple[float, float]:
    return (DATUM_LAT + n / M_PER_DEG_LAT, DATUM_LON + e / M_PER_DEG_LON)


def bbox_ll(pad: float) -> tuple[float, float, float, float]:
    e0, n0, e1, n1 = REGION
    s, w = geo(e0 - pad, n0 - pad)
    n, e = geo(e1 + pad, n1 + pad)
    return s, w, n, e


def ring_enu(pts_ll) -> list:
    """[(lon, lat), ...] -> [[e, n], ...] at 0.1 m."""
    out = []
    for lon, lat in pts_ll:
        e, n = enu(lat, lon)
        out.append([round(e, 1), round(n, 1)])
    return out


def ft_m(ft: float | None) -> float | None:
    return None if ft is None else round(ft * FT, 2)


# ------------------------------------------------------------------- http

def _get(url: str, data: bytes | None = None, timeout: int = 180, tries: int = 4) -> bytes:
    last = ""
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as ex:                       # noqa: BLE001 - retried, then raised
            last = str(ex)
            time.sleep(3 + 5 * k)
    raise RuntimeError(f"GET {url[:120]} failed: {last}")


def cached(work: Path, name: str, url: str, data: bytes | None = None, force: bool = False) -> bytes:
    p = work / name
    if p.exists() and not force:
        return p.read_bytes()
    b = _get(url, data)
    p.write_bytes(b)
    (work / (name + ".retrieved")).write_text(dt.date.today().isoformat())
    return b


def retrieved(work: Path, name: str) -> str:
    p = work / (name + ".retrieved")
    return p.read_text().strip() if p.exists() else dt.date.today().isoformat()


def faa_query(work: Path, svc: str, force: bool, geometry: bool = True) -> dict:
    s, w, n, e = bbox_ll(CLIP_PAD)
    q = {"where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
         "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
         "returnGeometry": "true" if geometry else "false", "outSR": "4326", "f": "json"}
    url = f"{FAA}/{svc}/FeatureServer/0/query?" + urllib.parse.urlencode(q)
    d = json.loads(cached(work, f"faa_{svc}.json", url, force=force))
    if "error" in d:
        raise RuntimeError(f"FAA {svc}: {d['error']}")
    return d


# -------------------------------------------------------------- geometry

def _shapely():
    import shapely.geometry as sg                     # noqa: PLC0415 - heavy, optional
    return sg


def clip_rings(rings_ll: list, tol: float = 1.0) -> list:
    """FAA rings (lon, lat) -> polygons in ENU clipped to the padded region:
    [[outer, hole, ...], ...]. Simplified at `tol` metres."""
    sg = _shapely()
    from shapely.ops import unary_union                # noqa: PLC0415
    e0, n0, e1, n1 = REGION
    box = sg.box(e0 - CLIP_PAD, n0 - CLIP_PAD, e1 + CLIP_PAD, n1 + CLIP_PAD)
    polys = []
    for r in rings_ll:
        pts = [enu(lat, lon) for lon, lat in r]
        if len(pts) >= 4:
            polys.append(sg.Polygon(pts).buffer(0))
    if not polys:
        return []
    # Esri rings: outer rings clockwise, holes counter-clockwise; buffer(0) of
    # each then a symmetric difference rebuilds holes correctly for both.
    geom = polys[0]
    for p in polys[1:]:
        geom = geom.symmetric_difference(p)
    geom = unary_union([geom]).intersection(box).simplify(tol)
    out = []
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty or g.geom_type != "Polygon" or g.area < 1.0:
            continue
        rings = [g.exterior] + list(g.interiors)
        out.append([[[round(x, 1), round(y, 1)] for x, y in list(rr.coords)[:-1]] for rr in rings])
    return out


def poly_area(ring: list) -> float:
    a = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i - 1]
        x1, y1 = ring[i]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


# -------------------------------------------------------------------- OSM

def fetch_osm(work: Path, force: bool) -> tuple[dict, dict, dict]:
    q = f"[out:json][timeout:60];way(id:{PARK_WAY},{PRESERVE_WAY});out geom tags;"
    d = json.loads(cached(work, "osm_site.json", OVERPASS, ("data=" + urllib.parse.quote(q)).encode(), force))
    ways = {e["id"]: e for e in d["elements"] if e["type"] == "way"}
    src = {"id": "osm", "title": "OpenStreetMap (Overpass API), ways %d and %d" % (PARK_WAY, PRESERVE_WAY),
           "url": "https://www.openstreetmap.org/way/%d" % PARK_WAY, "licence": "ODbL 1.0 (c) OpenStreetMap contributors",
           "retrieved": retrieved(work, "osm_site.json")}

    def ring(w):
        pts = [(g["lon"], g["lat"]) for g in w["geometry"]]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        return ring_enu(pts)

    pr = ring(ways[PARK_WAY])
    park = {
        "name": ways[PARK_WAY]["tags"].get("name", "Sunnyvale Baylands Park"),
        "osm_way": PARK_WAY,
        "ring": pr,
        "area_m2": round(poly_area(pr)),
        "area_acres": round(poly_area(pr) / 4046.8564224, 1),
        "note": ("OSM's park polygon covers the developed parkland (the city says 'over 70 acres'); "
                 "the 105-acre seasonal wetlands preserve is a separate polygon (preserve)."),
        "owner": "Santa Clara County (owns the property)",
        "manager": "City of Sunnyvale (manages and maintains the park)",
        "address": "999 E Caribbean Dr, Sunnyvale, CA 94089",
        "hours": {"open": "08:00", "close": "30 min after sunset", "days": "every day",
                  "text": "The park gate is open every day from 8:00 a.m. to one half hour after sunset."},
        "rules": [
            {"rule": "No balloons and no kites in the park", "reason": "to protect wildlife", "source": "city_baylands"},
            {"rule": "No pets; no dogs even on leash", "reason": "to protect wildlife", "source": "city_baylands"},
            {"rule": "Do not enter any areas posted as closed", "ref": "SMC 9.62.070 (h)", "source": "city_rules"},
            {"rule": "Motorized vehicles only on designated roads and parking lots", "ref": "SMC 9.62.040 (e)",
             "source": "city_rules"},
            {"rule": "Exclusive use of facilities by permit only", "ref": "SMC 9.62.120 (a)", "source": "city_rules"},
        ],
        "drone_area": None,
        "drone_note": ("No designated drone or model-aircraft area was found for this park (city Baylands "
                       "page, park site map, Park Use Rules). Kites and balloons are banned to protect "
                       "wildlife; expect a drone launch to need the City's permission."),
        "sources": ["osm", "city_baylands", "city_rules", "city_map"],
    }
    vr = ring(ways[PRESERVE_WAY])
    preserve = {
        "name": "Baylands Park seasonal wetlands preserve",
        "osm_way": PRESERVE_WAY,
        "osm_tags": ways[PRESERVE_WAY].get("tags", {}),
        "ring": vr,
        "area_m2": round(poly_area(vr)),
        "area_acres": round(poly_area(vr) / 4046.8564224, 1),
        "note": ("The city says 105 acres of seasonal wetlands are protected as a wetlands preserve. "
                 "No official boundary is published; this is OSM's marsh polygon east of the park "
                 "(the 'Seasonal Wetlands' and salt marsh harvest mouse habitat on the city's site "
                 "map), smaller than 105 acres. Treat it as a no-launch, no-low-overflight area."),
        "sources": ["osm", "city_baylands", "city_map"],
    }
    return park, preserve, src


# -------------------------------------------------------------------- FAA

def _alt(val, uom, code) -> dict:
    """An FAA airspace limit -> {ft, ref, m}. ref: SFC, MSL or AGL."""
    if code == "SFC" and (val in (None, 0)):
        return {"ft": 0, "ref": "SFC", "m": 0.0}
    if val == -9998:          # 'AA': up to, but not including, Class A (18,000 ft MSL)
        return {"ft": 17999, "ref": "MSL", "m": ft_m(17999), "text": "up to but not including 18,000 ft MSL"}
    if uom == "FL":
        return {"ft": val * 100, "ref": "MSL", "m": ft_m(val * 100), "text": f"FL{val}"}
    ref = "AGL" if code == "SFC" else "MSL"   # 'SFC' with a value = that many feet above the surface
    return {"ft": val, "ref": ref, "m": ft_m(val)}


def fetch_airspace(work: Path, force: bool) -> tuple[list, dict]:
    d = faa_query(work, "Class_Airspace", force)
    out = []
    for f in d["features"]:
        a = f["attributes"]
        if a.get("CLASS") == "A" or a.get("TYPE_CODE") != "CLASS":
            continue          # Class A is FL180+; the Mode C veil is not an airspace class
        polys = clip_rings(f["geometry"]["rings"])
        if not polys:
            continue
        lo = _alt(a.get("LOWER_VAL"), a.get("LOWER_UOM"), a.get("LOWER_CODE"))
        hi = _alt(a.get("UPPER_VAL"), a.get("UPPER_UOM"), a.get("UPPER_CODE"))
        out.append({
            "name": a.get("NAME"), "class": a.get("CLASS"), "type": a.get("LOCAL_TYPE"),
            "ident": a.get("IDENT"), "sector": a.get("SECTOR"),
            "floor": lo, "ceiling": hi,
            "hours": a.get("WKHR_CODE"), "hours_note": (a.get("WKHR_RMK") or "")[:300],
            "global_id": a.get("GLOBAL_ID"), "polys": polys,
        })
    out.sort(key=lambda s: ("BCDE".find(s["class"] or "E"), s["floor"]["ft"]))
    src = {"id": "faa_class", "title": "FAA Class Airspace (ArcGIS Class_Airspace/FeatureServer/0)",
           "url": f"{FAA}/Class_Airspace/FeatureServer/0", "licence": "Public domain (US Government)",
           "retrieved": retrieved(work, "faa_Class_Airspace.json")}
    return out, src


def fetch_uasfm(work: Path, force: bool) -> tuple[dict, dict]:
    d = faa_query(work, "FAA_UAS_FacilityMap_Data", force)
    cells = []
    eff = set()
    e0, n0, e1, n1 = REGION
    for f in d["features"]:
        a = f["attributes"]
        ring = f["geometry"]["rings"][0]
        rr = ring_enu([(p[0], p[1]) for p in ring[:-1]])
        es = [p[0] for p in rr]
        ns = [p[1] for p in rr]
        if max(es) < e0 - CLIP_PAD or min(es) > e1 + CLIP_PAD or max(ns) < n0 - CLIP_PAD or min(ns) > n1 + CLIP_PAD:
            continue
        apts = []
        for k in range(1, 6):
            if a.get(f"APT{k}_FAAID"):
                apts.append({"id": a[f"APT{k}_FAAID"], "name": a.get(f"APT{k}_NAME"),
                             "laanc": int(a.get(f"APT{k}_LAANC") or 0)})
        airs = [a[f"AIRSPACE_{k}"] for k in range(1, 6) if a.get(f"AIRSPACE_{k}")]
        eff.add(a.get("MAP_EFF"))
        cells.append({
            "ceiling_ft": a["CEILING"], "ceiling_m": ft_m(a["CEILING"]),
            "centre_ll": [round(a["LATITUDE"], 7), round(a["LONGITUDE"], 7)],
            "ring": rr, "airports": apts, "airspace": airs,
            # Conservative: LAANC only if every facility the cell lists takes it.
            "laanc": all(p["laanc"] == 1 for p in apts) if apts else False,
            "last_edit": a.get("LAST_EDIT"),
        })
    cells.sort(key=lambda c: (-c["centre_ll"][0], c["centre_ll"][1]))
    um = {
        "cell_deg": 1.0 / 120.0,
        "map_effective": sorted(x for x in eff if x),
        "note": ("UAS Facility Map: the maximum altitude, ft AGL, at which the FAA may authorize "
                 "Part 107 flight in each 30-arc-second cell of controlled airspace. 0 = no automatic "
                 "authorization. It is NOT permission: authorization (LAANC or FAA DroneZone) is still "
                 "needed. 'laanc' is derived conservatively: true only if every facility listed for "
                 "the cell is LAANC-enabled; Moffett (NUQ) is not, so its cells need DroneZone."),
        "cells": cells,
    }
    src = {"id": "faa_uasfm", "title": "FAA UAS Facility Map (ArcGIS FAA_UAS_FacilityMap_Data/FeatureServer/0)",
           "url": f"{FAA}/FAA_UAS_FacilityMap_Data/FeatureServer/0", "licence": "Public domain (US Government)",
           "retrieved": retrieved(work, "faa_FAA_UAS_FacilityMap_Data.json")}
    return um, src


def _agl_ft(text: str) -> int | None:
    m = re.search(r"(\d[\d,]*)\s*'?\s*(?:ft)?\s*AGL", text or "", re.I)
    return int(m.group(1).replace(",", "")) if m else None


def fetch_security(work: Path, force: bool) -> tuple[list, dict]:
    d = faa_query(work, "DoD_Mar_13", force)
    out = []
    for f in d["features"]:
        a = f["attributes"]
        polys = clip_rings(f["geometry"]["rings"], tol=0.5)
        if not polys:
            continue
        ceil_ft = _agl_ft(a.get("Ceiling", ""))
        out.append({
            "name": f"{a.get('Base')} ({a.get('Facility')})",
            "rule": "14 CFR 99.7 special security instructions (UAS flight restriction over a national security facility)",
            "proponent": a.get("Proponent"), "branch": a.get("Branch"), "reason": a.get("Reason"),
            "faa_id": a.get("FAA_ID"), "poc": a.get("POC"),
            "floor": {"ft": 0, "ref": "SFC", "m": 0.0, "text": a.get("Floor")},
            "ceiling": {"ft": ceil_ft, "ref": "AGL", "m": ft_m(ceil_ft), "text": a.get("Ceiling")},
            "polys": polys,
            "note": "UAS flight inside, surface to the ceiling, is prohibited without the proponent's approval.",
        })
    src = {"id": "faa_dod", "title": "FAA UAS flight restrictions over DoD facilities, 14 CFR 99.7 (ArcGIS DoD_Mar_13/FeatureServer/0)",
           "url": f"{FAA}/DoD_Mar_13/FeatureServer/0", "licence": "Public domain (US Government)",
           "retrieved": retrieved(work, "faa_DoD_Mar_13.json")}
    return out, src


def fetch_tfr(work: Path, force: bool) -> tuple[list, list]:
    d = faa_query(work, "Stadiums", force)
    x = cached(work, "notam_0_0367.xml", NOTAM_XML, force=force).decode("utf-8", "ignore")
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(x))))
    # The NOTAM's own words for the area, checked here so a reissue that
    # changes them stops the tool instead of silently keeping 3 NM / 3000 ft.
    m = re.search(r"WITHIN AN AREA DEFINED AS: (\d+) NMR OF A QUALIFYING STADIUM.*?UP TO AND INCLUDING (\d+)FT AGL", t)
    if not m:
        raise RuntimeError("FDC 0/0367 text changed: no 'N NMR ... UP TO AND INCLUDING N FT AGL'")
    r_nm, top_ft = int(m.group(1)), int(m.group(2))
    out = []
    for f in d["features"]:
        a = f["attributes"]
        g = f["geometry"]
        e, n = enu(g["y"], g["x"])
        pe, pn = 0.0, 0.0                                # the datum: the middle of the park
        out.append({
            "name": a.get("NAME"), "kind": "stadium TFR (event days only)",
            "notam": "FDC 0/0367 (14 CFR 99.7 special security instructions, sporting events)",
            "centre": [round(e, 1), round(n, 1)], "centre_ll": [round(g["y"], 6), round(g["x"], 6)],
            "radius_nm": r_nm, "radius_m": round(r_nm * NM, 1),
            "floor": {"ft": 0, "ref": "SFC", "m": 0.0},
            "ceiling": {"ft": top_ft, "ref": "AGL", "m": ft_m(top_ft)},
            "distance_to_park_m": round(math.hypot(e - pe, n - pn), 1),
            "distance_to_park_nm": round(math.hypot(e - pe, n - pn) / NM, 2),
            "active": ("ONLY on event days: from 1 hour before the scheduled start until 1 hour after "
                       "the end of an NFL, MLB or NCAA Division One football game (or a NASCAR Cup / "
                       "IndyCar / Champ race) at a venue of 30,000+ seats. Inactive otherwise."),
            "default_active": False,
        })
    srcs = [{"id": "faa_stadiums", "title": "FAA stadiums for NOTAM 0/0367 (ArcGIS Stadiums/FeatureServer/0)",
             "url": f"{FAA}/Stadiums/FeatureServer/0", "licence": "Public domain (US Government)",
             "retrieved": retrieved(work, "faa_Stadiums.json")},
            {"id": "notam_0_0367", "title": "FDC NOTAM 0/0367, special security instructions for select sporting events",
             "url": NOTAM_XML, "licence": "Public domain (US Government)",
             "retrieved": retrieved(work, "notam_0_0367.xml")}]
    return out, srcs


def fetch_dof(work: Path, force: bool) -> tuple[list, dict]:
    d = faa_query(work, "Digital_Obstacle_File", force)
    e0, n0, e1, n1 = REGION
    out = []
    for f in d["features"]:
        a = f["attributes"]
        lat, lon = float(a["Lat_DD"]), float(a["Long_DD"])
        e, n = enu(lat, lon)
        if not (e0 - CLIP_PAD <= e <= e1 + CLIP_PAD and n0 - CLIP_PAD <= n <= n1 + CLIP_PAD):
            continue
        out.append({"oas": a["OAS_Number"], "type": (a.get("Type_Code") or "").strip(),
                    "e": round(e, 1), "n": round(n, 1),
                    "agl_ft": a["AGL"], "amsl_ft": a["AMSL"], "agl_m": ft_m(a["AGL"]), "amsl_m": ft_m(a["AMSL"]),
                    "lit": (a.get("Lighting") or "").strip(), "verified": a.get("Verified"),
                    "horizontal_acc": a.get("Horizontal"), "vertical_acc": a.get("Vertical")})
    out.sort(key=lambda o: -o["agl_ft"])
    src = {"id": "faa_dof", "title": "FAA Digital Obstacle File (ArcGIS Digital_Obstacle_File/FeatureServer/0)",
           "url": f"{FAA}/Digital_Obstacle_File/FeatureServer/0", "licence": "Public domain (US Government)",
           "retrieved": retrieved(work, "faa_Digital_Obstacle_File.json")}
    return out, src


EMPTY_LAYERS = ["Special_Use_Airspace", "Prohibited_Areas", "National_Defense_Airspace_TFR_Areas",
                "Part_Time_National_Security_UAS_Flight_Restrictions", "FAA_Recognized_Identification_Areas",
                "Recreational_Flyer_Fixed_Sites"]


def check_empty(work: Path, force: bool) -> list:
    out = []
    for svc in EMPTY_LAYERS:
        d = faa_query(work, svc, force, geometry=False)
        out.append({"layer": svc, "features": len(d.get("features", [])),
                    "retrieved": retrieved(work, f"faa_{svc}.json")})
    return out


# ------------------------------------------------------------------- wind

SEASONS = {"winter": (12, 1, 2), "spring": (3, 4, 5), "summer": (6, 7, 8), "autumn": (9, 10, 11)}


def _p(vals: list, q: float) -> float:
    if not vals:
        return float("nan")
    v = sorted(vals)
    k = (len(v) - 1) * q
    lo = int(math.floor(k))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _stats(rows: list) -> dict:
    sp = [r[1] for r in rows]
    if not sp:
        return {"n": 0}
    calm = sum(1 for s in sp if s < 0.5)
    se = sum(r[1] * math.sin(math.radians(r[0])) for r in rows if r[1] >= 0.5 and r[0] is not None)
    ce = sum(r[1] * math.cos(math.radians(r[0])) for r in rows if r[1] >= 0.5 and r[0] is not None)
    wind = [r for r in rows if r[1] >= 0.5 and r[0] is not None]
    # Prevailing = the most frequent of 16 sectors among non-calm reports.
    sect = [0] * 16
    for r in wind:
        sect[int(((r[0] % 360) + 11.25) // 22.5) % 16] += 1
    names = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()
    mean = sum(sp) / len(sp)
    p90 = _p(sp, 0.9)
    res = math.hypot(se, ce) / max(sum(r[1] for r in wind), 1e-9)
    return {"n": len(sp), "mean_kt": round(mean, 1), "p90_kt": round(p90, 1),
            "mean_ms": round(mean * KT, 2), "p90_ms": round(p90 * KT, 2),
            "calm_pct": round(100.0 * calm / len(sp), 1),
            "prevailing_from": names[sect.index(max(sect))] if wind else None,
            "resultant_from_deg": round(math.degrees(math.atan2(se, ce)) % 360) if wind else None,
            "steadiness": round(res, 2)}


def fetch_wind(work: Path, force: bool, y0: int = 2021, y1: int = 2025) -> tuple[dict, dict]:
    q = [("station", "NUQ"), ("data", "drct"), ("data", "sknt"), ("data", "gust"),
         ("year1", y0), ("month1", 1), ("day1", 1), ("year2", y1 + 1), ("month2", 1), ("day2", 1),
         ("tz", "America/Los_Angeles"), ("format", "onlycomma"), ("latlon", "no"), ("elev", "no"),
         ("missing", "M"), ("trace", "T"), ("direct", "no"), ("report_type", 3)]
    url = IEM + "?" + urllib.parse.urlencode(q)
    raw = cached(work, f"iem_nuq_{y0}_{y1}.csv", url, force=force).decode()
    rows = []
    for r in csv.DictReader(io.StringIO(raw)):
        if r["sknt"] in ("M", ""):
            continue
        t = dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M")
        # Hourly METARs come at :53-:56; give each to the hour it ends.
        t = t + dt.timedelta(minutes=10)
        d = None if r["drct"] in ("M", "") else float(r["drct"])
        rows.append((t.month, t.hour, d, float(r["sknt"]), None if r["gust"] in ("M", "") else float(r["gust"])))
    by_mh = [[[] for _ in range(24)] for _ in range(12)]
    for mo, h, d, s, g in rows:
        by_mh[mo - 1][h].append((d, s))
    table = {k: [[None] * 24 for _ in range(12)] for k in ("mean_kt", "p90_kt", "calm_pct", "from_deg", "n")}
    for m in range(12):
        for h in range(24):
            st = _stats(by_mh[m][h])
            table["n"][m][h] = st["n"]
            if st["n"]:
                table["mean_kt"][m][h] = st["mean_kt"]
                table["p90_kt"][m][h] = st["p90_kt"]
                table["calm_pct"][m][h] = st["calm_pct"]
                table["from_deg"][m][h] = st["resultant_from_deg"]
    table["mean_ms"] = [[None if v is None else round(v * KT, 2) for v in row] for row in table["mean_kt"]]
    table["p90_ms"] = [[None if v is None else round(v * KT, 2) for v in row] for row in table["p90_kt"]]

    def pick(months, hours):
        return [(d, s) for mo, h, d, s, g in rows if mo in months and h in hours]

    aft, morn = range(13, 19), range(4, 10)
    seasons = {}
    for name, months in SEASONS.items():
        seasons[name] = {"months": list(months),
                         "afternoon_13_18": _stats(pick(months, aft)),
                         "morning_04_09": _stats(pick(months, morn)),
                         "all_hours": _stats(pick(months, range(24)))}
    gusts = [g for *_, g in rows if g is not None]
    wc = {
        "station": "NUQ (Moffett Federal Airfield ASOS), ~6 km west of the park; open bay-shore exposure like the park's",
        "period": f"{y0}-01-01 to {y1}-12-31, routine hourly METARs (IEM report_type 3)",
        "time": "local clock time, America/Los_Angeles (PST/PDT), hour the observation ends",
        "reports": len(rows),
        "calm_rule": "speed < 0.5 kt counts as calm (and as 0 kt in the means)",
        "direction": "the direction the wind blows FROM, degrees true; 'from_deg' = vector-mean direction",
        "units": "knots and m/s (1 kt = 0.514444 m/s)",
        "year": _stats([(d, s) for _, _, d, s, _ in rows]),
        "gust_reports_pct": round(100.0 * len(gusts) / max(len(rows), 1), 1),
        "gust_p90_kt": round(_p(gusts, 0.9), 1) if gusts else None,
        "seasons": seasons,
        "by_month_hour": table,
        "note": ("Afternoon sea breeze from the NNW-N, strongest in summer; mornings are often calm. "
                 "Use it to draw training wind (speed from the month-hour distribution, direction "
                 "from from_deg +/- spread); the park itself is ~6 km from NUQ and can differ."),
    }
    src = {"id": "iem_asos_nuq", "title": "Iowa Environmental Mesonet ASOS/AWOS archive, station NUQ",
           "url": "https://mesonet.agron.iastate.edu/request/download.phtml?network=CA_ASOS",
           "licence": "Public METAR observations (US NWS/FAA ASOS), archive by Iowa State University IEM",
           "retrieved": retrieved(work, f"iem_nuq_{y0}_{y1}.csv")}
    return wc, src


# ------------------------------------------------------------------- pads

def _surface(ndvi: float | None) -> str:
    if ndvi is None:
        return "unknown"
    if ndvi > 0.25:
        return "green turf (irrigated lawn in the May 2024 photo)"
    if ndvi > 0.08:
        return "grass meadow (part dry in the May 2024 photo)"
    return "dry grass or bare ground (May 2024 photo)"


def choose_pads(rasters: Path, features: Path, park: dict, preserve: dict, n_max: int = 6,
                ortho: Path | None = None, nir: Path | None = None) -> list:
    """Launch/landing pads in the park's open meadow, from the 0.5 m lidar grids."""
    import numpy as np                                   # noqa: PLC0415
    sg = _shapely()
    from shapely.ops import unary_union                  # noqa: PLC0415
    from shapely.prepared import prep                    # noqa: PLC0415

    grid = json.loads((rasters / "grid.json").read_text())
    R, C, cell = grid["rows"], grid["cols"], grid["cell_m"]
    ge0, gn0, ge1, gn1 = grid["region_enu"]
    park_poly = sg.Polygon(park["ring"])
    pres_poly = sg.Polygon(preserve["ring"])
    pe0, pn0, pe1, pn1 = park_poly.bounds
    m = 40.0                                             # margin for the window
    c0 = int((pe0 - m - ge0) / cell)
    c1 = int((pe1 + m - ge0) / cell)
    r0 = int((gn1 - (pn1 + m)) / cell)
    r1 = int((gn1 - (pn0 - m)) / cell)

    def win(name, dtype=np.float32):
        a = np.memmap(rasters / name, dtype=dtype, mode="r", shape=(R, C))
        return np.array(a[r0:r1, c0:c1])

    dtm = win("dtm.f32")
    veg = win("veg.f32")
    bld = win("bld.f32")
    oth = win("other.f32")
    wat = win("water_full.u8", np.uint8)
    h, w = dtm.shape
    es = ge0 + (c0 + np.arange(w) + 0.5) * cell
    ns = gn1 - (r0 + np.arange(h) + 0.5) * cell
    E, N = np.meshgrid(es, ns)

    # Solid things a pad must be 15 m clear of: trees/shrubs > 1.5 m, any
    # building return, any unclassified return > 1 m (poles, posts, fences,
    # signs), water.
    with np.errstate(invalid="ignore"):
        tall = (np.nan_to_num(veg - dtm, nan=0.0) > 1.5) | np.isfinite(bld) \
            | (np.nan_to_num(oth - dtm, nan=0.0) > 1.0) | (wat > 0)
    from scipy import ndimage                            # noqa: PLC0415
    clear = ndimage.distance_transform_edt(~tall) * cell     # m to nearest solid
    wet = ndimage.distance_transform_edt(wat == 0) * cell    # m to the nearest water

    # Flatness: plane fit over a 6 m radius disk -> slope and roughness.
    k = int(6.0 / cell)
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    disk = (xx ** 2 + yy ** 2) <= k * k
    z = np.nan_to_num(dtm, nan=np.nanmean(dtm))
    gy, gx = np.gradient(ndimage.uniform_filter(z, 2 * k + 1), cell)
    slope = np.hypot(gx, gy) * 100.0
    mean = ndimage.convolve(z, disk / disk.sum(), mode="nearest")
    sq = ndimage.convolve(z * z, disk / disk.sum(), mode="nearest")
    rough = np.sqrt(np.maximum(sq - mean * mean, 0.0))    # std of the ground; includes the tilt (small at < 3 %)
    # Basins hold water after rain (the park's seasonal ponds are dry in the
    # April lidar): depth of the closed depression each cell sits in = the
    # ground filled to its spill level (morphological reconstruction by
    # erosion from the window's edge) minus the ground.
    from skimage.morphology import reconstruction        # noqa: PLC0415
    seed = np.full_like(z, z.max())
    seed[0, :], seed[-1, :], seed[:, 0], seed[:, -1] = z[0, :], z[-1, :], z[:, 0], z[:, -1]
    basin = reconstruction(seed, z, method="erosion") - z
    # Surface: NDVI from the NAIP photo (May 2024): green irrigated lawn vs
    # dry meadow grass vs bare ground.
    ndvi = None
    if ortho is not None and nir is not None:
        from PIL import Image                            # noqa: PLC0415
        Image.MAX_IMAGE_PIXELS = None
        red = np.asarray(Image.open(ortho).convert("RGB"))[..., 0]
        ir = np.asarray(Image.open(nir).convert("L"))
        ores = (ge1 - ge0) / red.shape[1]
        oi = np.clip(((gn1 - ns) / ores).astype(int), 0, red.shape[0] - 1)
        oj = np.clip(((es - ge0) / ores).astype(int), 0, red.shape[1] - 1)
        r_ = red[np.ix_(oi, oj)].astype(np.float32)
        n_ = ir[np.ix_(oi, oj)].astype(np.float32)
        ndvi = ndimage.uniform_filter((n_ - r_) / np.maximum(n_ + r_, 1.0), int(5.0 / cell) * 2 + 1)
        del red, ir

    feats = json.loads(features.read_text())
    paths = [sg.LineString(p["pts"]) for p in feats["paths"] if len(p["pts"]) >= 2]
    roads = [sg.LineString(p["pts"]) for p in feats["roads"] if len(p["pts"]) >= 2]
    power = [sg.LineString(p["pts"]) for p in feats["power_lines"] if len(p["pts"]) >= 2]
    towers = [sg.Point(p["p"]) for p in feats["power_points"]]
    bad_areas = []
    for a in feats["areas"]:
        t = a["tags"]
        if len(a["pts"]) >= 4 and (t.get("leisure") in ("playground", "pitch", "picnic_table", "fitness_station")
                                   or t.get("amenity") in ("parking", "shelter", "bbq")
                                   or t.get("building") or t.get("natural") in ("water", "wetland")
                                   or t.get("landuse") in ("construction",)):
            try:
                bad_areas.append(sg.Polygon(a["pts"]).buffer(0))
            except Exception:                            # noqa: BLE001 - skip broken rings
                pass
    win_box = sg.box(es[0] - 100, ns[-1] - 100, es[-1] + 100, ns[0] + 100)
    paths_u = unary_union([p for p in paths if p.intersects(win_box)])
    roads_u = unary_union([p for p in roads if p.intersects(win_box)])
    power_u = unary_union(power + [t.buffer(1.0) for t in towers])
    bad_u = prep(unary_union([b for b in bad_areas if b.intersects(win_box)] or [sg.Point(1e9, 1e9).buffer(1)]))
    park_in = prep(park_poly.buffer(-10.0))
    pres_buf = prep(pres_poly.buffer(30.0))

    cand = []
    step = 4                                             # every 2 m
    for i in range(k, h - k, step):
        for j in range(k, w - k, step):
            if clear[i, j] < 15.0 or slope[i, j] > 3.0 or rough[i, j] > 0.12 or wet[i, j] < 30.0:
                continue
            if basin[i, j] > 0.15:
                continue
            p = sg.Point(E[i, j], N[i, j])
            if not park_in.contains(p) or pres_buf.contains(p) or bad_u.contains(p):
                continue
            dp = p.distance(paths_u)
            dr = p.distance(roads_u)
            dw = p.distance(power_u)
            if dp < 12.0 or dr < 30.0 or dw < 60.0:
                continue
            score = min(clear[i, j], 40.0) + 0.4 * min(dp, 40.0) - 4.0 * slope[i, j] - 20.0 * rough[i, j]
            if ndvi is not None:
                score += 20.0 * max(float(ndvi[i, j]), 0.0)     # prefer short green turf
            cand.append((score, i, j, dp, dr, dw))
    cand.sort(reverse=True)
    dsm = win("dsm.f32")
    pads = []
    for score, i, j, dp, dr, dw in cand:
        e, n = float(E[i, j]), float(N[i, j])
        if any(math.hypot(e - q["e"], n - q["n"]) < 60.0 for q in pads):
            continue
        g = float(np.nanmean(dtm[i - 6:i + 7, j - 6:j + 7]))
        # Steepest obstacle (lidar first surface) seen from 2 m above the pad,
        # out to 250 m, in 8 directions: how clean each departure is.
        ang = []
        for b in range(8):
            th = math.radians(b * 45.0)
            worst = 0.0
            for s in np.arange(5.0, 250.0, 1.0):
                ii = int(round(i - s * math.cos(th) / cell))
                jj = int(round(j + s * math.sin(th) / cell))
                if ii < 0 or jj < 0 or ii >= h or jj >= w:
                    break
                top = dsm[ii, jj]
                if np.isfinite(top):
                    worst = max(worst, math.degrees(math.atan2(float(top) - (g + 2.0), s)))
            ang.append(round(worst, 1))
        pads.append({"id": f"P{len(pads) + 1}", "e": round(e, 1), "n": round(n, 1),
                     "ground_m": round(g, 2), "radius_m": 5.0,
                     "clear_m": round(float(clear[i, j]), 1), "path_m": round(dp, 1),
                     "road_m": round(dr, 1), "power_line_m": round(dw, 1),
                     "slope_pct": round(float(slope[i, j]), 2), "rough_m": round(float(rough[i, j]), 3),
                     "basin_depth_m": round(float(basin[i, j]), 2),
                     "ndvi_2024": None if ndvi is None else round(float(ndvi[i, j]), 2),
                     "surface": _surface(None if ndvi is None else float(ndvi[i, j])),
                     "obstacle_angle_deg": ang,
                     "obstacle_angle_dirs": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]})
        if len(pads) >= n_max:
            break
    lat_lon = [geo(p["e"], p["n"]) for p in pads]
    for p, (la, lo) in zip(pads, lat_lon):
        p["lat_lon"] = [round(la, 7), round(lo, 7)]
    return pads


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, required=True, help="cache of raw downloads")
    ap.add_argument("--out", type=Path, default=HERE.parent / "godot/assets/baylands/site.json")
    ap.add_argument("--rasters", type=Path, help="0.5 m lidar grids (grid.json, dtm.f32, ...) for the pads")
    ap.add_argument("--features", type=Path, default=None, help="features.json (default: next to --out)")
    ap.add_argument("--ortho", type=Path, help="_hi/ortho.jpg (NAIP RGB over REGION) for the pads' surface")
    ap.add_argument("--nir", type=Path, help="_hi/nir.jpg (NAIP near-infrared over REGION)")
    ap.add_argument("--force", action="store_true", help="re-download everything")
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    feats = a.features or a.out.parent / "features.json"

    park, preserve, s_osm = fetch_osm(a.work, a.force)
    airspace, s_cls = fetch_airspace(a.work, a.force)
    uasfm, s_um = fetch_uasfm(a.work, a.force)
    security, s_dod = fetch_security(a.work, a.force)
    tfr, s_tfr = fetch_tfr(a.work, a.force)
    dof, s_dof = fetch_dof(a.work, a.force)
    empty = check_empty(a.work, a.force)
    wind, s_wind = fetch_wind(a.work, a.force)
    if a.rasters:
        pads = choose_pads(a.rasters, feats, park, preserve, ortho=a.ortho, nir=a.nir)
        pads_note = ("Chosen from the 2020 lidar (0.5 m grids): ground within a 6 m radius sloping < 3 % "
                     "and rough < 0.12 m; no tree/shrub > 1.5 m, building, pole/post/fence > 1 m or water "
                     "within 15 m (clear_m); >= 30 m from water; not in a closed depression deeper than 0.15 m "
                     "(basin_depth_m: the park's seasonal ponds are dry in the April lidar); "
                     ">= 12 m from OSM paths (path_m), >= 30 m from roads and "
                     "parking (road_m), >= 60 m from power lines and towers (power_line_m); >= 10 m inside "
                     "the park polygon and >= 30 m outside the wetlands preserve; >= 60 m apart; green turf "
                     "(NDVI from the May 2024 NAIP photo) preferred. "
                     "ground_m = metres above NAVD88. obstacle_angle_deg: steepest lidar surface seen from "
                     "2 m above the pad out to 250 m, per direction. Check the pad on the day: the lidar is "
                     "from April 2020.")
    else:
        old = json.loads(a.out.read_text()) if a.out.exists() else {}
        pads = old.get("pads", [])
        pads_note = old.get("pads_note", "")

    city = [
        {"id": "city_baylands", "title": "City of Sunnyvale, Baylands Park page (Wayback snapshot 2026-05-15)",
         "url": CITY_PAGE, "licence": "Facts only (hours, rules); no text reproduced beyond short rule quotes",
         "retrieved": "2026-11-27"},
        {"id": "city_rules", "title": "City of Sunnyvale, Park Use Rules (Wayback snapshot 2026-08-19)",
         "url": RULES_PAGE, "licence": "Facts only", "retrieved": "2026-11-27"},
        {"id": "city_map", "title": "City of Sunnyvale, Baylands Park site map (PDF, 2018)",
         "url": "https://web.archive.org/web/20260515121152/https://www.sunnyvale.ca.gov/home/showpublisheddocument/418/637815554180070000", "licence": "Facts only",
         "retrieved": "2026-11-27"},
    ]
    site = {
        "schema": "baylands_site/1",
        "disclaimer": DISCLAIMER,
        "generated": dt.date.today().isoformat(),
        "tool": "drone/sim/tools/fetch_baylands_site.py",
        "frame": {
            "datum_lat_lon": [DATUM_LAT, DATUM_LON], "m_per_deg_lat": M_PER_DEG_LAT,
            "m_per_deg_lon": M_PER_DEG_LON,
            "formula": "e = (lon - datum_lon) * m_per_deg_lon; n = (lat - datum_lat) * m_per_deg_lat "
                       "(env_baylands.gd geo_to_sim); Godot = (e, y, -n)",
            "region_enu": list(REGION), "clip_pad_m": CLIP_PAD,
            "heights": ("metres above NAVD88 (the lidar's datum). Aviation 'MSL' altitudes are taken as "
                        "NAVD88 heights here; local tidal mean sea level is ~1 m above NAVD88."),
            "units": "1 ft = 0.3048 m exactly; 1 NM = 1852 m; 1 kt = 0.514444 m/s",
        },
        "rules_general": {
            "part107_max_agl_ft": 400, "part107_max_agl_m": ft_m(400),
            "note": ("14 CFR 107.51: at most 400 ft AGL (or within 400 ft of a structure). Controlled "
                     "airspace (Class B, C, D, surface E) needs an FAA authorization: LAANC up to the UAS "
                     "Facility Map ceiling, or FAA DroneZone. Recreational flyers (49 USC 44809) also need "
                     "LAANC in controlled airspace and must pass TRUST."),
        },
        "park": park,
        "preserve": preserve,
        "airspace": airspace,
        "uasfm": uasfm,
        "security": security,
        "tfr": tfr,
        "obstacles_dof": dof,
        "dof_note": ("FAA Digital Obstacle File points (obstacles filed with the FAA). AGL/AMSL in feet as "
                     "published and metres. Accuracy codes: horizontal 1 = +/-20 ft ... 4 = +/-250 ft; "
                     "vertical A = +/-3 ft ... D = +/-50 ft. The lidar-built scene is the better geometry; "
                     "these are a cross-check."),
        "checked_empty": empty,
        "wind_climatology": wind,
        "pads": pads,
        "pads_note": pads_note,
        "sources": [s_osm] + city + [s_cls, s_um, s_dod] + s_tfr + [s_dof, s_wind],
        "carried_over": [
            "Sunnyvale Municipal Code chapter 9.62 (public parks) has no drone/model-aircraft section: "
            "from earlier research; the code site refused automated fetches this time.",
        ],
    }
    a.out.write_text(json.dumps(site, indent=1, ensure_ascii=False) + "\n")
    print(f"site.json: {a.out.stat().st_size / 1024:.0f} KB; {len(airspace)} airspace volumes, "
          f"{len(uasfm['cells'])} UASFM cells, {len(security)} 99.7 areas, {len(tfr)} TFR, "
          f"{len(dof)} DOF, {len(pads)} pads, {wind['reports']} wind reports")


if __name__ == "__main__":
    main()
