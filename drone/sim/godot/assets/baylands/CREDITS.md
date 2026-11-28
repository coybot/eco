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
| built_roofs.dat | Roof shapes (flat levels, parapets, rooftop units, hip/gable fits, measured surfaces) from the USGS 3DEP 2020 lidar inside each footprint; roof colour from USDA NAIP 2024; footprints from OpenStreetMap. Baked by `drone/sim/tools/bake_baylands_built.py` | Public domain (USGS, USDA) + ODbL (© OpenStreetMap contributors) |
| built_things.json | Poles, light standards, measured structures (rides, play frames, backstops), bridge decks and fence heights from the USGS 3DEP 2020 lidar; furniture, fences, playgrounds, pitches and car parks from OpenStreetMap; parked cars are a generated layout in the OSM car parks. Baked by `bake_baylands_built.py` | Public domain (USGS, USDA) + ODbL (© OpenStreetMap contributors) |
| built_detail.png | Greyscale detail of ambientCG RoofingTiles003, RoofingTiles014A, Plaster001, Concrete034 (https://ambientcg.com) | CC0 1.0 |

OpenStreetMap data is © OpenStreetMap contributors, available under the Open
Database License (https://opendatacommons.org/licenses/odbl/). The files
derived from it here are a Produced Work / Derivative Database under that
licence and stay ODbL.
