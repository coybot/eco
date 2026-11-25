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
| `building_attrs.json` | NYC Open Data: Building Footprints (5zhs-2jue) `construction_year`; PLUTO (64uk-42ks) `bldgclass`, `numfloors`, retail/office/residential area | Public domain (NYC Open Data terms of use) | `fetch_manhattan_building_attrs.py` |
| `../facade_cells_nyc/*` (window cells for the building shader) | Drawn by the tool, not photographed | No restrictions (original work, Apache-2.0 with the repo) | `bake_manhattan_facade_cells.py` |
| `far_nyc.json` | NYC Open Data, Building Footprints (5zhs-2jue): Brooklyn, Queens and the Bronx (BBL borough 2-4) | Public domain (NYC Open Data terms of use) | `fetch_manhattan_far.py` |
| `far_nj.json` | Overture Maps Foundation, buildings theme (release 2026-11-18.0), New Jersey from Jersey City to Fort Lee; its source datasets here are OpenStreetMap and Microsoft ML Buildings | ODbL 1.0: (c) OpenStreetMap contributors, Overture Maps Foundation (<https://docs.overturemaps.org/attribution/>) | `fetch_manhattan_far.py` |
| `bridges.json` | Alignments: OpenStreetMap bridge roadways and tracks, via the Overpass API. Spans, heights, deck levels and colours: the bridges' published dimensions, entered by hand in the tool | Alignments (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_manhattan_bridges.py` |
| `_hi/photo_*.jpg`, `_hi/far.jpg`, `_hi/tiles.json` (gitignored) | USGS National Map, NAIP Imagery (USDA National Agriculture Imagery Program) | Public domain | `fetch_manhattan_naip.py` |
| `_hi/streets_*.png` (gitignored) | NYC Open Data, NYC Planimetric Database: Roadbed (i36f-5ih7), Sidewalk (52n9-sdep), Median (ees7-4ufv) | Public domain (NYC Open Data terms of use) | `fetch_manhattan_planimetric.py` |
| `piers.json` | NYC Open Data, NYC Planimetric Database: Hydrography Structures (6hbv-tek4), feat_code 2800 piers | Public domain (NYC Open Data terms of use) | `fetch_manhattan_planimetric.py` |
| `shore.png`, `shore_far.png`, `shore.json`, `coast.json` | Derived from `water.png` (OpenStreetMap coastline, ODbL 1.0, (c) OpenStreetMap contributors; USGS NHD, public domain) and `piers.json` | ODbL 1.0 (produced work of the OSM coastline): attribute "(c) OpenStreetMap contributors" | `bake_manhattan_shore.py` |
| `ships.json` | Derived: ferry lines from `roads.json` (NYC Open Data Centerline); river fairways from `water.png` (OpenStreetMap coastline, USGS NHD); Upper Bay legs, Staten Island ferry and harbour tour as hand waypoints | Public domain (NYC Open Data); map data (c) OpenStreetMap contributors, ODbL 1.0; NHD public domain | `bake_manhattan_ships.py` |

`far_nj.json` and `bridges.json` are derived databases of ODbL data
(Overture's buildings theme; OpenStreetMap) and stay under the ODbL; they are kept as separate files so that the public
domain NYC data is not mixed into them.
