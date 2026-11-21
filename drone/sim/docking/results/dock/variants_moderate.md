Nominal sensors, moderate hover only (24 cells), seeds 1-60 (spec4: 1-50).

| variant | change | recovery | target cell (moderate, 6 m/s) | 0 m/s | 3 m/s | 6 m/s | 9 m/s | launch, spec criteria | launch without the post clause | launches touching a post | n recoveries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | spec as built: 3 anchors, 38 mm lip, 1 post, wind from the nose | **0.601** | **0.581** | 0.869 | 0.872 | 0.581 | 0.081 | 0.108 | 1.000 | 0.892 | 1440 |
| spec4 | + dock anchor A4 | **0.631** | **0.623** | 0.903 | 0.893 | 0.623 | 0.103 | 0.117 | 1.000 | 0.883 | 1200 |
| lip10 | starboard lip 0.10 m (was 0.038 m) | **0.604** | **0.583** | 0.853 | 0.844 | 0.583 | 0.136 | 0.090 | 1.000 | 0.910 | 1440 |
| dual | two posts and two brackets per pocket | **0.272** | **0.239** | 0.383 | 0.403 | 0.239 | 0.061 | 0.047 | 0.531 | 0.953 | 1440 |
| windward | mothership yaws: wind from starboard | **0.640** | **0.692** | 0.869 | 0.858 | 0.692 | 0.142 | 0.055 | 1.000 | 0.945 | 1440 |
| slot20 | launch slot 20 mm (was 12 mm) | **0.642** | **0.678** | 0.878 | 0.856 | 0.678 | 0.156 | 0.160 | 0.985 | 0.825 | 1440 |
| still | mothership hovers with no motion at all | **0.614** | **0.636** | 0.833 | 0.889 | 0.636 | 0.097 | 0.043 | 1.000 | 0.957 | 1440 |
| drift | mothership hovers relative to the air (drifts with the mean wind) | **0.864** | **0.894** | 0.869 | 0.842 | 0.894 | 0.850 | 0.076 | 1.000 | 0.924 | 1440 |
| still_drift | both | **0.853** | **0.847** | 0.833 | 0.850 | 0.847 | 0.883 | 0.001 | 1.000 | 0.999 | 1440 |
| all | all geometry changes + A4 | **0.331** | **0.353** | 0.492 | 0.361 | 0.353 | 0.119 | 0.106 | 0.846 | 0.894 | 1440 |
| all_drift | all geometry changes + A4 + drifting hover | **0.491** | **0.472** | 0.486 | 0.508 | 0.472 | 0.497 | 0.151 | 0.878 | 0.849 | 1440 |
| all_drift_nodual | as all_drift, without the two-post bracket | **0.872** | **0.889** | 0.875 | 0.853 | 0.889 | 0.869 | 0.166 | 1.000 | 0.834 | 1440 |
| pocket_drift | 3 anchors + a short-range beacon per pocket (3 cm, +-2 cm bias), drifting hover | **0.974** | **0.981** | 0.972 | 0.978 | 0.981 | 0.967 | 0.087 | 1.000 | 0.912 | 1440 |
| pocket_same_drift | as pocket_drift, beacons as noisy as A1-A3 (5 cm, +-5 cm) | **0.965** | **0.961** | 0.975 | 0.972 | 0.961 | 0.953 | 0.087 | 1.000 | 0.912 | 1440 |
| pocket_all_drift_nodual | pocket beacons + all_drift_nodual | **0.980** | **0.978** | 0.978 | 0.981 | 0.978 | 0.983 | 0.172 | 1.000 | 0.828 | 1440 |

Main recovery failures:

- baseline: {'prop_strike': 133, 'touched_parked': 116, 'not_seated': 111, 'timeout_not_landed': 79, 'over_time': 50}
- spec4: {'prop_strike': 131, 'touched_parked': 93, 'not_seated': 75, 'timeout_not_landed': 49, 'post_speed': 36}
- lip10: {'prop_strike': 146, 'touched_parked': 118, 'timeout_not_landed': 90, 'not_seated': 74, 'post_speed': 68}
- dual: {'not_seated': 296, 'final_position': 189, 'touched_parked': 182, 'post_speed': 153, 'prop_strike': 90}
- windward: {'prop_strike': 174, 'timeout_not_landed': 114, 'touched_parked': 68, 'not_seated': 50, 'over_time': 40}
- slot20: {'prop_strike': 150, 'not_seated': 94, 'touched_parked': 89, 'timeout_not_landed': 76, 'post_speed': 48}
- still: {'not_seated': 130, 'prop_strike': 118, 'touched_parked': 106, 'timeout_not_landed': 80, 'over_time': 55}
- drift: {'timeout_not_landed': 33, 'prop_strike': 32, 'not_seated': 30, 'over_time': 28, 'post_speed': 26}
- still_drift: {'timeout_not_landed': 74, 'over_time': 34, 'post_speed': 27, 'touched_parked': 24, 'prop_strike': 21}
- all: {'not_seated': 397, 'post_speed': 219, 'touched_parked': 121, 'timeout_not_landed': 99, 'prop_strike': 62}
- all_drift: {'not_seated': 409, 'post_speed': 134, 'touched_parked': 93, 'timeout_not_landed': 38, 'over_time': 19}
- all_drift_nodual: {'touched_parked': 50, 'post_speed': 40, 'prop_strike': 36, 'timeout_not_landed': 20, 'touchdown_speed': 16}
- pocket_drift: {'touchdown_speed': 24, 'prop_strike': 4, 'over_time': 3, 'final_position': 2, 'not_seated': 2}
- pocket_same_drift: {'touchdown_speed': 11, 'over_time': 10, 'not_seated': 9, 'timeout_not_landed': 7, 'prop_strike': 6}
- pocket_all_drift_nodual: {'touchdown_speed': 14, 'prop_strike': 7, 'post_speed': 3, 'timeout_not_landed': 2, 'touched_parked': 2}
