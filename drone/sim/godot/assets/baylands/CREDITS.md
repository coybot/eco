# Baylands Park, Sunnyvale — data sources and licences

Every file here is built by `drone/sim/tools/fetch_baylands.py` (and
`bake_baylands_lidar.py`) from the sources below. Folders starting with `_`
are gitignored; the tools regenerate them.

| File | Source | Licence |
|---|---|---|
| terrain.zi16/.json, _hi/*.zf32 | USGS 3DEP lidar, CA_SantaClaraCounty_2020 (flown April 2020 by Sanborn for Santa Clara County), via NOAA Digital Coast's COPC mirror (dataset 9329). UTM 10N metres, NAVD88 (GEOID18) metres. | Public domain (US Government work, no use restrictions) |
| water.png/.json | The same lidar (water returns and no-return holes) + OpenStreetMap water areas | Public domain + ODbL |
| buildings.dat/.json | OpenStreetMap building footprints (ODbL) + footprints traced from the lidar; heights from the lidar | ODbL (© OpenStreetMap contributors) |
| features.json | OpenStreetMap (paths, roads, fences, power lines, areas, points) | ODbL (© OpenStreetMap contributors) |
| ortho.jpg/.json, _hi/ortho.jpg, _hi/nir.jpg | USDA NAIP 2024 California, 0.6 m, 20 May 2024 (quarter-quads m_3712240_se, m_3712133_sw), via the Microsoft Planetary Computer | Public domain (USDA Farm Service Agency) |
| trees.dat/.json (baked by `drone/sim/tools/bake_baylands_trees.py`) | Every tree, shrub and rush stand measured from the same 2020 lidar (canopy height model + class 3-5 points: position, height, 8-sector crown outline, crown base); species classified from crown shape, NAIP 2024 colour and near-infrared and OpenStreetMap context (land use, roads, water, tagged trees, current building footprints) | Public domain (lidar, NAIP) + ODbL (© OpenStreetMap contributors) |
| (drawn by baylands_nature.gd) palm models `assets/models/palm_fan.glb`, `palm_date.glb` | Sketchfab: "Realistic HD California fan palm (22/25)" by PlantCatalog; "Date Palm" by evolveduk (see assets/models/CREDITS.md) | CC BY 4.0 (credit the authors) |

OpenStreetMap data is © OpenStreetMap contributors, available under the Open
Database License (https://opendatacommons.org/licenses/odbl/). The files
derived from it here are a Produced Work / Derivative Database under that
licence and stay ODbL.
