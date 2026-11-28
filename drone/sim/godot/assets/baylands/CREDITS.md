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
| climate.json | NOAA/NWS ASOS station KNUQ (Moffett Field), 2021-2025 wind, gusts, sky cover, visibility and present weather, via the Iowa Environmental Mesonet; binned by `drone/sim/tools/bake_baylands_climate.py` | Public domain (US Government work) |
| (in scripts/baylands_weather.gd) tide constants | NOAA CO-OPS station 9414575 Coyote Creek, Alviso Slough: harmonic constituents and tidal datums (1983-2001 epoch) | Public domain (US Government work) |
| far_near.zi16, far_wide.zi16, far.json | USGS 3DEP 1 arc-second DEM, tiles n38w122 n38w123 n37w122 n37w123; `drone/sim/tools/fetch_baylands_far.py` | Public domain (US Government work) |
| far_*_dry.jpg, far_*_green.jpg, far_*_water.png | Copernicus Sentinel-2 L2A (Collection 1) via Earth Search on AWS: medians of the clearest Jul-Sep and Feb-Mar scenes 2023-2025, TCI and SCL. "Contains modified Copernicus Sentinel data 2023-2025" | Copernicus Sentinel data licence (free, full and open; attribution as quoted) |
| far_city.zbin, _sky/far_city_all.zbin, far_bridges.json | OpenStreetMap buildings and bridges (Geofabrik norcal extract) | ODbL (© OpenStreetMap contributors) |

OpenStreetMap data is © OpenStreetMap contributors, available under the Open
Database License (https://opendatacommons.org/licenses/odbl/). The files
derived from it here are a Produced Work / Derivative Database under that
licence and stay ODbL.
