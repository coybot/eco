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
| life.json, life_roads.json (moving life: roads with posted speeds, paths, car-park stalls, picnic points, pitches, open grass, bird habitats) | OpenStreetMap via features.json (ODbL); stall clutter, open grass, bridge decks and the water mask from the USGS 3DEP 2020 lidar (public domain). Built by `drone/sim/tools/bake_baylands_life.py` | ODbL (© OpenStreetMap contributors) + public domain |
| People, dogs, bicycles, birds (baylands_life_meshes.gd) | Procedural, made here; bird sizes from published species ranges (length, wingspan, mass) | Apache-2.0 (this repo) |
| ortho.jpg/.json, _hi/ortho.jpg, _hi/nir.jpg | USDA NAIP 2024 California, 0.6 m, 20 May 2024 (quarter-quads m_3712240_se, m_3712133_sw), via the Microsoft Planetary Computer | Public domain (USDA Farm Service Agency) |
| site.json (park, preserve) | OpenStreetMap ways 28640922 (park) and 28640930 (seasonal wetlands), via Overpass | ODbL (© OpenStreetMap contributors) |
| site.json (hours, rules) | City of Sunnyvale Baylands Park page, Park Use Rules and park site map (facts only, via the Internet Archive) | Facts; no text reproduced beyond short rule quotes |
| site.json (airspace, UAS facility map, 14 CFR 99.7 areas, stadiums, obstacles) | FAA public ArcGIS services (Class_Airspace, FAA_UAS_FacilityMap_Data, DoD_Mar_13, Stadiums, Digital_Obstacle_File) and FDC NOTAM 0/0367 (tfr.faa.gov) | Public domain (US Government) |
| site.json (wind_climatology) | NUQ ASOS routine METARs 2021-2025, Iowa Environmental Mesonet archive | Public observations (NWS/FAA ASOS); archive by Iowa State University |
| site.json (pads) | Chosen from the USGS 3DEP 2020 lidar grids above + USDA NAIP 2024 (NDVI) + OpenStreetMap paths/power | Public domain + ODbL |
| wires.json | Power-line routes and tower/pole nodes from OpenStreetMap (power=line / minor_line, ODbL); every structure's position, height and arm rows measured in the USGS 3DEP 2020 lidar (public domain); conductors inferred (catenaries, see drone/sim/tools/bake_baylands_wires.py) | ODbL (© OpenStreetMap contributors) + public domain |
| climate.json | NOAA/NWS ASOS station KNUQ (Moffett Field), 2021-2025 wind, gusts, sky cover, visibility and present weather, via the Iowa Environmental Mesonet; binned by `drone/sim/tools/bake_baylands_climate.py` | Public domain (US Government work) |
| (in scripts/baylands_weather.gd) tide constants | NOAA CO-OPS station 9414575 Coyote Creek, Alviso Slough: harmonic constituents and tidal datums (1983-2001 epoch) | Public domain (US Government work) |
| far_near.zi16, far_wide.zi16, far.json | USGS 3DEP 1 arc-second DEM, tiles n38w122 n38w123 n37w122 n37w123; `drone/sim/tools/fetch_baylands_far.py` | Public domain (US Government work) |
| far_*_dry.jpg, far_*_green.jpg, far_*_water.png | Copernicus Sentinel-2 L2A (Collection 1) via Earth Search on AWS: medians of the clearest Jul-Sep and Feb-Mar scenes 2023-2025, TCI and SCL. "Contains modified Copernicus Sentinel data 2023-2025" | Copernicus Sentinel data licence (free, full and open; attribution as quoted) |
| far_city.zbin, _sky/far_city_all.zbin, far_bridges.json | OpenStreetMap buildings and bridges (Geofabrik norcal extract) | ODbL (© OpenStreetMap contributors) |
| built_roofs.dat | Roof shapes (flat levels, parapets, rooftop units, hip/gable fits, measured surfaces) from the USGS 3DEP 2020 lidar inside each footprint; roof colour from USDA NAIP 2024; footprints from OpenStreetMap. Baked by `drone/sim/tools/bake_baylands_built.py` | Public domain (USGS, USDA) + ODbL (© OpenStreetMap contributors) |
| built_things.json | Poles, light standards, measured structures (rides, play frames, backstops), bridge decks and fence heights from the USGS 3DEP 2020 lidar; furniture, fences, playgrounds, pitches and car parks from OpenStreetMap; parked cars are a generated layout in the OSM car parks. Baked by `bake_baylands_built.py` | Public domain (USGS, USDA) + ODbL (© OpenStreetMap contributors) |
| built_detail.png | Greyscale detail of ambientCG RoofingTiles003, RoofingTiles014A, Plaster001, Concrete034 (https://ambientcg.com) | CC0 1.0 |
| trees.dat/.json (baked by `drone/sim/tools/bake_baylands_trees.py`) | Every tree, shrub and rush stand measured from the same 2020 lidar (canopy height model + class 3-5 points: position, height, 8-sector crown outline, crown base); species classified from crown shape, NAIP 2024 colour and near-infrared and OpenStreetMap context (land use, roads, water, tagged trees, current building footprints) | Public domain (lidar, NAIP) + ODbL (© OpenStreetMap contributors) |
| (drawn by baylands_nature.gd) palm models `assets/models/palm_fan.glb`, `palm_date.glb` | Sketchfab: "Realistic HD California fan palm (22/25)" by PlantCatalog; "Date Palm" by evolveduk (see assets/models/CREDITS.md) | CC BY 4.0 (credit the authors) |
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
