# Regression: existing scenes and vehicles

pytest (drone/sim/tests + drone/common/tests): PASS -- 947 passed, 5 skipped, 31 warnings in 189.67s (0:03:09)

e2e fast tier `quadcopter`: PASS ([PASS] behaviour gate; [PASS] quadcopter (fast): ok)
e2e fast tier `rover`: PASS ([PASS] behaviour gate; [PASS] rover (fast): ok)
e2e fast tier `fixedwing`: PASS ([PASS] behaviour gate; [PASS] fixedwing (fast): ok)

Seed scenarios on the default kinematic backend vs the base commit: 27 run, 0 differ.

| scenario | kinematic: complete / targets / collisions / t_end s | rigid-body (opt-in): complete / targets / collisions / t_end s |
|---|---|---|
| scenarios/asymmetric_extract.yaml | True / 0/0 / 0 / 10.4 | True / 0/0 / 0 / 14.8 |
| scenarios/cascading_doom.yaml | True / 0/0 / 0 / 11.1 | True / 0/0 / 0 / 11.3 |
| scenarios/dense_urban.yaml | True / 0/0 / 45 / 21.9 | True / 0/0 / 73 / 36.3 |
| scenarios/final_boss.yaml | False / 0/0 / 46 / 60.0 | False / 0/0 / 12 / 60.0 |
| scenarios/formation_crush.yaml | False / 0/0 / 0 / 45.0 | False / 0/0 / 0 / 45.0 |
| scenarios/gauntlet_alpha.yaml | False / 0/0 / 28 / 45.0 | False / 0/0 / 16 / 45.0 |
| scenarios/gauntlet_beta.yaml | True / 0/0 / 8 / 9.2 | True / 0/0 / 7 / 9.6 |
| scenarios/gauntlet_delta.yaml | False / 0/0 / 10 / 40.0 | False / 0/0 / 0 / 40.0 |
| scenarios/gauntlet_gamma.yaml | True / 0/0 / 12 / 10.6 | True / 0/0 / 6 / 10.8 |
| scenarios/hostile_recon.yaml | True / 0/0 / 8 / 10.7 | True / 0/0 / 12 / 10.3 |
| scenarios/multi_chokepoint.yaml | True / 0/0 / 0 / 13.3 | True / 0/0 / 0 / 14.0 |
| scenarios/night_shift.yaml | True / 0/0 / 25 / 13.4 | True / 0/0 / 91 / 13.3 |
| scenarios/pursuit_corridor.yaml | False / 0/0 / 0 / 60.0 | True / 0/0 / 0 / 20.0 |
| scenarios/relay_dependency.yaml | True / 0/0 / 15 / 11.0 | True / 0/0 / 15 / 11.0 |
| scenarios/sensor_hell.yaml | True / 0/0 / 0 / 7.6 | True / 0/0 / 0 / 7.7 |
| scenarios/time_siege.yaml | True / 0/0 / 1 / 12.7 | True / 0/0 / 4 / 12.7 |
| scenarios_demo/cf_team_search.yaml | True / 8/8 / 48 / 62.1 | n/a (no dynamics model for this class) |
| scenarios_fw/fw_replan.yaml | True / 0/0 / 28 / 20.6 | False / 0/0 / 24 / 60.0 |
| scenarios_fw/fw_sweep.yaml | True / 0/0 / 0 / 20.0 | False / 0/0 / 13 / 45.0 |
| scenarios_indoor/cf_apartment_search.yaml | False / 1/2 / 513 / 60.0 | n/a (no dynamics model for this class) |
| scenarios_indoor/cf_doorway.yaml | True / 0/0 / 0 / 9.5 | n/a (no dynamics model for this class) |
| scenarios_indoor/cf_flow_drift.yaml | True / 2/2 / 0 / 11.8 | n/a (no dynamics model for this class) |
| scenarios_indoor/cf_furniture_slalom.yaml | True / 0/0 / 0 / 6.6 | n/a (no dynamics model for this class) |
| scenarios_indoor/cf_hover_room.yaml | True / 0/0 / 0 / 4.3 | n/a (no dynamics model for this class) |
| scenarios_indoor/cf_low_beam.yaml | True / 0/0 / 0 / 6.4 | n/a (no dynamics model for this class) |
| scenarios_indoor/cf_texture_blackout.yaml | True / 0/0 / 0 / 5.4 | n/a (no dynamics model for this class) |
| scenarios_indoor/cf_two_cf_corridor.yaml | False / 0/0 / 0 / 45.0 | n/a (no dynamics model for this class) |

The rigid-body column is a smoke test: the scenarios' reactive controller was tuned on the kinematic model, so different numbers there are expected and are not a regression.

OVERALL: PASS
