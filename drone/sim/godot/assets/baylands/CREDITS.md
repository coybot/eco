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
| site.json (park, preserve) | OpenStreetMap ways 28640922 (park) and 28640930 (seasonal wetlands), via Overpass | ODbL (© OpenStreetMap contributors) |
| site.json (hours, rules) | City of Sunnyvale Baylands Park page, Park Use Rules and park site map (facts only, via the Internet Archive) | Facts; no text reproduced beyond short rule quotes |
| site.json (airspace, UAS facility map, 14 CFR 99.7 areas, stadiums, obstacles) | FAA public ArcGIS services (Class_Airspace, FAA_UAS_FacilityMap_Data, DoD_Mar_13, Stadiums, Digital_Obstacle_File) and FDC NOTAM 0/0367 (tfr.faa.gov) | Public domain (US Government) |
| site.json (wind_climatology) | NUQ ASOS routine METARs 2021-2025, Iowa Environmental Mesonet archive | Public observations (NWS/FAA ASOS); archive by Iowa State University |
| site.json (pads) | Chosen from the USGS 3DEP 2020 lidar grids above + USDA NAIP 2024 (NDVI) + OpenStreetMap paths/power | Public domain + ODbL |
| wires.json | Power-line routes and tower/pole nodes from OpenStreetMap (power=line / minor_line, ODbL); every structure's position, height and arm rows measured in the USGS 3DEP 2020 lidar (public domain); conductors inferred (catenaries, see drone/sim/tools/bake_baylands_wires.py) | ODbL (© OpenStreetMap contributors) + public domain |

OpenStreetMap data is © OpenStreetMap contributors, available under the Open
Database License (https://opendatacommons.org/licenses/odbl/). The files
derived from it here are a Produced Work / Derivative Database under that
licence and stay ODbL.
