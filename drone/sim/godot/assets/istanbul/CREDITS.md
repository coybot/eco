# Istanbul scene data: sources and licences

Every file here is baked by a tool in `drone/sim/tools/`; rerun the tool
rather than editing a file by hand. Files and folders starting with `_` are
gitignored (caches and large downloads the tools fetch again).

| File | Source | Licence | Tool |
|---|---|---|---|
| `buildings.dat`, `buildings.json` | OpenStreetMap building footprints and tags (BBBike Istanbul extract); heights from OSM `height` / `building:levels`, else the European Commission JRC's GHSL GHS-BUILT-H R2023A average building height | Footprints: (c) OpenStreetMap contributors, ODbL 1.0 (<https://www.openstreetmap.org/copyright>). Heights: (c) European Union, 1995-2026, CC BY 4.0 (changes: sampled per building and scaled by footprint size) | `fetch_istanbul.py` |
| `terrain.zi16`, `terrain.json` | NASA SRTM 1 arc-second elevation (AWS Terrain Tiles); flattened to sea level from `water.png` | SRTM public domain | `fetch_istanbul.py` |
| `water.png`, `water.json` | OpenStreetMap coastline and water areas | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `landcover.png`, `landcover.json` | OpenStreetMap land use, leisure and natural areas | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `roads.json` | OpenStreetMap highways, railways (T1 tram), ferry routes, piers | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `features.json` | OpenStreetMap city walls, minarets, towers, aqueducts, historic sites, building parts, trees | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul.py` |
| `life.json` | OpenStreetMap access restrictions (`motor_vehicle`/`access` = no), `highway=traffic_signals` nodes and tram stops, for the moving world (`scripts/istanbul_life*.gd`) | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul_life.py` |
| `istanbul_facade_cells/` (`mask.png`, `colour.png`, `cells.json`) | Window, shop, balcony, clapboard and masonry cells drawn procedurally by the tool (no photographs, no third-party data) | Original work, under this repository's Apache-2.0 licence | `bake_istanbul_facade_cells.py` |
| `sea_shore.png`, `sea_flow.png`, `sea.json` | Derived from `water.png` (OpenStreetMap coastline): the shoreline's signed distance, and the surface current's strength and direction (direction from the channel's own axis; strengths drawn by hand) | (c) OpenStreetMap contributors, ODbL 1.0 | `bake_istanbul_sea.py` |
| (code) `scripts/istanbul_bridges.gd` | Bridge alignments from OpenStreetMap ways in `roads.json` (the Golden Horn Metro Bridge's pylons as OSM maps them); spans, heights and widths are published facts (Wikipedia) | alignments ODbL 1.0 | none (procedural) |
| (code) `scripts/istanbul_ships.gd` | Berths and ferry lines from OpenStreetMap ferry routes in `roads.json`; craft models from `../models/` | ODbL 1.0; models CC BY 4.0 (see `../models/CREDITS.md`) | none (procedural) |
| `trees.dat`, `trees.json` | Copernicus Sentinel-2 L2A (ESA), red and near-infrared at 10 m: summer and winter NDVI, from the Element84 earth-search STAC (AWS open data COGs); OpenStreetMap mapped trees (`natural=tree`), tree rows, cemeteries, mosque precincts and lawns; the base layers above | Contains modified Copernicus Sentinel data 2025 (free, full and open; changes: NDVI computed, resampled, turned into tree positions). OSM parts: (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul_trees.py` |
| (palms in `istanbul_nature.gd`) | `assets/models/palm_date.glb`, `palm_fan.glb`, already in the repo | CC BY 4.0, authors in `assets/models/CREDITS.md` | — |
| `ground_s2.jpg`, `ground_s2.json` | Copernicus Sentinel-2 L2A, scene S2B_35TPF_20250605_0_L2A (true colour B04/B03/B02, 10 m), via the Element84 earth-search STAC; `_ground/veg.jpg` from its NDVI (B08, B04) | Contains modified Copernicus Sentinel data 2025 (free and open, Copernicus data licence) | `fetch_istanbul_ground.py` |
| `ground_tex/*.jpg`, `ground_tex.json` | Poly Haven textures: asphalt_02, cobblestone_floor_08, patterned_concrete_pavers, concrete_pavers_02, leafy_grass, gravel_floor, dry_ground_01, coast_sand_01, forest_leaves_02 (diffuse, 1k, resized to 512 px) | CC0 1.0 | `fetch_istanbul_ground.py` |
| `coast.json` | OpenStreetMap `natural=coastline`, simplified to 0.4 m and marked quay / rocks | (c) OpenStreetMap contributors, ODbL 1.0 | `fetch_istanbul_ground.py` |
| `far.dat`, `far.json` (and `_ground/far_all.dat`) | OpenStreetMap building footprints outside the detailed scene, as minimum-area boxes; heights from OSM `height` / `building:levels`, else JRC GHSL GHS-BUILT-H R2023A | Footprints (c) OpenStreetMap contributors, ODbL 1.0; heights (c) European Union, CC BY 4.0 (changes: sampled per building, scaled by footprint size) | `fetch_istanbul_ground.py` |
| `_ground/fa_*.png`, `_ground/fb_*.png` (gitignored) | Street, paving, green, slab and shoreline distance fields from `roads.json`, OSM land use polygons and the OSM coastline; Sentinel-2 vegetation | (c) OpenStreetMap contributors, ODbL 1.0; contains modified Copernicus Sentinel data 2025 | `fetch_istanbul_ground.py` |

The OpenStreetMap-derived files are produced works / derivative databases of
OpenStreetMap and stay under the ODbL; credit "(c) OpenStreetMap
contributors" wherever the scene is shown.
