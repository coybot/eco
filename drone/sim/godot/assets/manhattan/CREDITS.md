# Manhattan scene data: sources and licences

Every file here is baked by a tool in `drone/sim/tools/`; rerun the tool
rather than editing a file by hand. Files and folders starting with `_` are
gitignored (caches and large imagery the tools fetch again).

| File | Source | Licence | Tool |
|---|---|---|---|
| `buildings.json` | NYC Open Data, Building Footprints (5zhs-2jue) | Public domain (NYC Open Data terms of use) | `fetch_manhattan.py` |
| `ortho.jpg`, `ortho.json` | USGS National Map imagery | Public domain | `fetch_manhattan_ortho.py` |
| `water.png`, `water.json` | OpenStreetMap coastline; USGS National Hydrography Dataset waterbodies | Map data (c) OpenStreetMap contributors, ODbL 1.0 (<https://www.openstreetmap.org/copyright>); NHD public domain | `fetch_manhattan_water.py` |
| `roads.json` | NYC Open Data, Centerline (CSCL, inkn-q76z) | Public domain (NYC Open Data terms of use) | `fetch_manhattan_roads.py` |
| `far_nyc.json` | NYC Open Data, Building Footprints (5zhs-2jue): Brooklyn, Queens and the Bronx (BBL borough 2-4) | Public domain (NYC Open Data terms of use) | `fetch_manhattan_far.py` |
| `far_nj.json` | Overture Maps Foundation, buildings theme (release 2026-11-18.0), New Jersey from Jersey City to Fort Lee; its source datasets here are OpenStreetMap and Microsoft ML Buildings | ODbL 1.0: (c) OpenStreetMap contributors, Overture Maps Foundation (<https://docs.overturemaps.org/attribution/>) | `fetch_manhattan_far.py` |
| `bridges.json` | Alignments: OpenStreetMap bridge roadways and tracks, via the Overpass API. Spans, heights, deck levels and colours: the bridges' published dimensions, entered by hand in the tool | Alignments (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_manhattan_bridges.py` |

`far_nj.json` and `bridges.json` are derived databases of ODbL data
(Overture's buildings theme; OpenStreetMap) and stay under the ODbL; they are kept as separate files so that the public
domain NYC data is not mixed into them.
