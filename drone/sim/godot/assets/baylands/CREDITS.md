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
| ground_cls.png, tidal.png, water_quads.dat, water_bodies.json, _ground/* | Built by `drone/sim/tools/bake_baylands_ground.py` from the lidar, NAIP and OSM above (surface classes, the tidal flood map, the water surfaces and their colours, the de-shadowed photo); tide datums from NOAA CO-OPS station 9414575 (Coyote Creek, Alviso Slough) | Public domain + ODbL |
| ground_tex/asphalt.jpg, asphalt_n.jpg | Poly Haven "asphalt_02" (Rob Tuytel), https://polyhaven.com/a/asphalt_02, resized to 512 px | CC0 1.0 |
| ground_tex/concrete.jpg, concrete_n.jpg | Poly Haven "concrete_pavement" (Charlotte Baglioni), https://polyhaven.com/a/concrete_pavement, resized to 512 px | CC0 1.0 |
| ground_tex/dg.jpg, dg_n.jpg | Poly Haven "gravel_floor" (Jenelle van Heerden, Matterfield), https://polyhaven.com/a/gravel_floor, resized to 512 px | CC0 1.0 |
| ground_tex/gravel.jpg, gravel_n.jpg | Poly Haven "rocky_trail" (Amal Kumar), https://polyhaven.com/a/rocky_trail, resized to 512 px | CC0 1.0 |
| ground_tex/lawn.jpg, lawn_n.jpg | Poly Haven "leafy_grass" (Charlotte Baglioni), https://polyhaven.com/a/leafy_grass, resized to 512 px | CC0 1.0 |
| ground_tex/drygrass.jpg, drygrass_n.jpg | Poly Haven "sparse_grass" (Amal Kumar), https://polyhaven.com/a/sparse_grass, resized to 512 px | CC0 1.0 |
| ground_tex/soil.jpg, soil_n.jpg | Poly Haven "brown_mud_dry" (Rob Tuytel), https://polyhaven.com/a/brown_mud_dry, resized to 512 px | CC0 1.0 |
| ground_tex/mud.jpg, mud_n.jpg | Poly Haven "brown_mud_02" (Rob Tuytel), https://polyhaven.com/a/brown_mud_02, resized to 512 px | CC0 1.0 |
| ground_tex/cracked.jpg, cracked_n.jpg | Poly Haven "mud_cracked_dry_03" (Dario Barresi, Dimitrios Savva), https://polyhaven.com/a/mud_cracked_dry_03, resized to 512 px | CC0 1.0 |
| ground_tex/riprap.jpg, riprap_n.jpg | Poly Haven "gray_rocks" (Dimitrios Savva), https://polyhaven.com/a/gray_rocks, resized to 512 px | CC0 1.0 |
| ground_tex/litter.jpg, litter_n.jpg | Poly Haven "forest_leaves_02" (Rob Tuytel), https://polyhaven.com/a/forest_leaves_02, resized to 512 px | CC0 1.0 |

OpenStreetMap data is © OpenStreetMap contributors, available under the Open
Database License (https://opendatacommons.org/licenses/odbl/). The files
derived from it here are a Produced Work / Derivative Database under that
licence and stay ODbL.
