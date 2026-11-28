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

OpenStreetMap data is © OpenStreetMap contributors, available under the Open
Database License (https://opendatacommons.org/licenses/odbl/). The files
derived from it here are a Produced Work / Derivative Database under that
licence and stay ODbL.
