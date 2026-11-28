#!/usr/bin/env python3
"""Wind and sky climatology for Baylands Park from Moffett Field (NUQ) ASOS, 2021-2025.

Input: two Iowa Environmental Mesonet ASOS downloads of station NUQ, times
in LOCAL clock time (America/Los_Angeles, with DST): wind (drct, sknt, gust)
and sky (vsby, skyc1-3, skyl1-3, wxcodes); routine + special reports:
https://mesonet.agron.iastate.edu/request/download.phtml (public domain data,
NOAA/NWS ASOS). NUQ is 3.5 km west of the park on the same bay shore.

Output: drone/sim/godot/assets/baylands/climate.json, read by
scripts/baylands_weather.gd. Per calendar month x 3-hour block of PST
(the sim's world_clock runs on fixed PST, no DST, so observations taken in
PDT are shifted back one hour before binning):
  calm        fraction of reports with 0 kt
  q_kt        speed quantiles (knots, 10 m) of the non-calm reports at
              p05, p15, ..., p95 (10 values): sample by inverse CDF
  dir16       probability of the direction the wind blows FROM, 16 sectors
              of 22.5 deg starting at N (non-calm, non-variable reports)
  gust_p      fraction of reports with a gust group
  gust_ex_kt  mean gust excess over the mean wind when one is reported (kt)
Units stay KNOTS in the file (as reported); the sim converts with
1 kt = 0.514444 m/s.
And per month x block, `sky`: the fraction of reports in each weather
preset of baylands_weather.gd, classified from the METAR:
  light_rain     RA / DZ / SH / TS / UP present
  fog            FG, or visibility < 1 mi (1.6 km)
  post_rain      not raining, within 12 h after rain was last reported
  hazy           HZ / FU / BR, or visibility < 7 mi (11 km), no cloud below 3500 ft
  marine_layer   BKN/OVC based below 3500 ft (1067 m): stratus
  overcast       OVC at or above 3500 ft (frontal mid cloud; drawn as stratus higher up)
  broken_cumulus FEW/SCT/BKN below 12000 ft, not overcast
  clear          CLR (nothing below 12000 ft, the ceilometer's reach)
ASOS cannot see cirrus (above 12000 ft), so `clear` includes cirrus days;
baylands_weather.gd splits a share of clear off as high_cirrus.

    python3 drone/sim/tools/bake_baylands_climate.py nuq_wind.csv nuq_sky.csv

IEM URL for the sky file (the wind one is the same with data=drct,sknt,gust):
https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=NUQ&data=vsby&data=skyc1&data=skyc2&data=skyc3&data=skyl1&data=skyl2&data=skyl3&data=wxcodes&year1=2021&month1=1&day1=1&year2=2026&month2=1&day2=1&tz=America%2FLos_Angeles&format=onlycomma&latlon=no&missing=M&trace=T&direct=no&report_type=3
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "godot/assets/baylands/climate.json"


def _nth_sunday(y: int, m: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(days=7 * (n - 1))


def _is_dst(t: dt.datetime) -> bool:
    # US rule since 2007: 02:00 second Sunday of March to 02:00 first Sunday of November.
    a = dt.datetime.combine(_nth_sunday(t.year, 3, 2), dt.time(2))
    b = dt.datetime.combine(_nth_sunday(t.year, 11, 1), dt.time(2))
    return a <= t < b


def _f(x: str):
    try:
        return float(x)
    except ValueError:
        return None


PRESETS = ["clear", "hazy", "marine_layer", "overcast", "broken_cumulus", "light_rain", "post_rain", "fog"]


def _sky_class(r: dict) -> str:
    wx = r["wxcodes"] if r["wxcodes"] != "M" else ""
    v = _f(r["vsby"])
    if any(k in wx for k in ("RA", "DZ", "SH", "TS", "UP")):
        return "light_rain"
    if "FG" in wx or (v is not None and v < 1.0):
        return "fog"
    layers = []
    for i in (1, 2, 3):
        c, l = r[f"skyc{i}"], _f(r[f"skyl{i}"])
        if c in ("FEW", "SCT", "BKN", "OVC", "VV") and l is not None:
            layers.append((c, l))
    low_ceiling = any(c in ("BKN", "OVC", "VV") and l < 3500 for c, l in layers)
    if low_ceiling:
        return "marine_layer"
    if any(c == "OVC" for c, l in layers):
        return "overcast"
    if any(k in wx for k in ("HZ", "FU", "BR")) or (v is not None and v < 7.0):
        return "hazy"
    if layers:
        return "broken_cumulus"
    return "clear"


def sky_table(src: Path) -> dict:
    rows = []
    for r in csv.DictReader(src.open()):
        t = dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M")
        if _is_dst(t):
            t -= dt.timedelta(hours=1)
        rows.append((t, _sky_class(r)))
    rows.sort()
    cells = {}
    last_rain = None
    for t, k in rows:
        if k == "light_rain":
            last_rain = t
        elif last_rain is not None and (t - last_rain) < dt.timedelta(hours=12) and k != "fog":
            k = "post_rain"
        c = cells.setdefault((t.month, t.hour // 3), dict.fromkeys(PRESETS, 0))
        c[k] += 1
    out = []
    for m in range(1, 13):
        bl = []
        for b in range(8):
            c = cells[(m, b)]
            n = max(1, sum(c.values()))
            bl.append([round(c[p] / n, 3) for p in PRESETS])
        out.append(bl)
    return {"presets": PRESETS, "months": out}


def main(argv=None) -> int:
    args = (argv or sys.argv[1:])
    src = Path(args[0])
    cells = {}
    for r in csv.DictReader(src.open()):
        t = dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M")
        if _is_dst(t):
            t -= dt.timedelta(hours=1)     # PDT clock -> PST
        s = _f(r["sknt"])
        if s is None:
            continue
        c = cells.setdefault((t.month, t.hour // 3), {"s": [], "d": [], "g": []})
        c["s"].append(s)
        d = _f(r["drct"])
        if s > 0 and d is not None and d > 0:   # 0 with wind = variable
            c["d"].append(d % 360.0)
        g = _f(r["gust"])
        if g is not None and g > s:
            c["g"].append(g - s)
    months = []
    for m in range(1, 13):
        blocks = []
        for b in range(8):
            c = cells[(m, b)]
            s = sorted(x for x in c["s"] if x > 0)
            n = len(c["s"])
            q = [round(s[min(len(s) - 1, int(p / 100 * len(s)))], 1) for p in range(5, 100, 10)] if s else [0] * 10
            h = [0] * 16
            for d in c["d"]:
                h[int(((d + 11.25) % 360) // 22.5)] += 1
            tot = max(1, sum(h))
            blocks.append({"n": n, "calm": round(1 - len(s) / n, 3), "q_kt": q,
                           "dir16": [round(x / tot, 3) for x in h],
                           "gust_p": round(len(c["g"]) / n, 3),
                           "gust_ex_kt": round(sum(c["g"]) / len(c["g"]), 1) if c["g"] else 0.0})
        months.append(blocks)
    allk = [x for c in cells.values() for x in c["s"]]
    doc = {"source": "NOAA/NWS ASOS KNUQ (Moffett Field) via Iowa Environmental Mesonet, 2021-2025, public domain",
           "units": "knots at 10 m; 1 kt = 0.514444 m/s", "clock": "PST (UTC-8) all year",
           "block_hours": 3, "dir16_from_deg": [i * 22.5 for i in range(16)],
           "year_mean_kt": round(sum(allk) / len(allk), 2),
           "months": months}
    if len(args) > 1:
        doc["sky"] = sky_table(Path(args[1]))
        for m in (1, 4, 7, 10):
            print("sky", m, [(p, doc["sky"]["months"][m - 1][b][i]) for b in (2, 4) for i, p in enumerate(PRESETS) if doc["sky"]["months"][m - 1][b][i] > 0.04])
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print("wrote", OUT, OUT.stat().st_size, "bytes; year mean", doc["year_mean_kt"], "kt")
    for m in (1, 4, 7, 10):
        for b in (2, 5):
            c = months[m - 1][b]
            top = max(range(16), key=lambda i: c["dir16"][i])
            print(m, b * 3, "calm", c["calm"], "median", c["q_kt"][5], "p95", c["q_kt"][9], "from", top * 22.5, c["dir16"][top])
    return 0


if __name__ == "__main__":
    sys.exit(main())
