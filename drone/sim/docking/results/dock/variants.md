Nominal sensors, the 72 cells hover x wind x neighbours x box, seeds 1-60 (spec4: 1-50).

| variant | change | recovery | target cell (moderate, 6 m/s) | 0 m/s | 3 m/s | 6 m/s | 9 m/s | launch, spec criteria | launch without the post clause | launches touching a post | n recoveries |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | spec as built: 3 anchors, 38 mm lip, 1 post, wind from the nose | **0.581** | **0.581** | 0.844 | 0.845 | 0.555 | 0.082 | 0.117 | 1.000 | 0.883 | 4320 |
| spec4 | + dock anchor A4 | **0.609** | **0.623** | 0.894 | 0.852 | 0.590 | 0.100 | 0.111 | 1.000 | 0.889 | 3600 |
| lip10 | starboard lip 0.10 m (was 0.038 m) | **0.569** | **0.583** | 0.834 | 0.811 | 0.519 | 0.113 | 0.093 | 1.000 | 0.907 | 4320 |
| dual | two posts and two brackets per pocket | **0.262** | **0.239** | 0.356 | 0.369 | 0.270 | 0.055 | 0.044 | 0.520 | 0.956 | 4320 |
| windward | mothership yaws: wind from starboard | **0.617** | **0.692** | 0.844 | 0.824 | 0.664 | 0.136 | 0.055 | 1.000 | 0.945 | 4320 |
| slot20 | launch slot 20 mm (was 12 mm) | **0.621** | **0.678** | 0.853 | 0.845 | 0.647 | 0.140 | 0.166 | 0.984 | 0.818 | 4320 |
| still | mothership hovers with no motion at all | **0.614** | **0.636** | 0.833 | 0.889 | 0.636 | 0.097 | 0.043 | 1.000 | 0.957 | 1440 |
| drift | mothership hovers relative to the air (drifts with the mean wind) | **0.864** | **0.894** | 0.869 | 0.842 | 0.894 | 0.850 | 0.076 | 1.000 | 0.924 | 1440 |
| still_drift | both | **0.853** | **0.847** | 0.833 | 0.850 | 0.847 | 0.883 | 0.001 | 1.000 | 0.999 | 1440 |
| all | all geometry changes + A4 | **0.326** | **0.353** | 0.480 | 0.383 | 0.316 | 0.126 | 0.092 | 0.857 | 0.908 | 4320 |
| all_drift | all geometry changes + A4 + drifting hover | **0.491** | **0.472** | 0.486 | 0.508 | 0.472 | 0.497 | 0.151 | 0.878 | 0.849 | 1440 |
| all_drift_nodual | as all_drift, without the two-post bracket | **0.872** | **0.889** | 0.875 | 0.853 | 0.889 | 0.869 | 0.166 | 1.000 | 0.834 | 1440 |
| pocket_drift | 3 anchors + a short-range beacon per pocket (3 cm, +-2 cm bias), drifting hover | **0.974** | **0.981** | 0.972 | 0.978 | 0.981 | 0.967 | 0.087 | 1.000 | 0.912 | 1440 |
| pocket_same_drift | as pocket_drift, beacons as noisy as A1-A3 (5 cm, +-5 cm) | **0.965** | **0.961** | 0.975 | 0.972 | 0.961 | 0.953 | 0.087 | 1.000 | 0.912 | 1440 |
| pocket_all_drift_nodual | pocket beacons + all_drift_nodual | **0.980** | **0.978** | 0.978 | 0.981 | 0.978 | 0.983 | 0.172 | 1.000 | 0.828 | 1440 |

Main recovery failures:

- baseline: {'prop_strike': 484, 'touched_parked': 356, 'not_seated': 307, 'timeout_not_landed': 241, 'over_time': 138}
- spec4: {'prop_strike': 406, 'touched_parked': 297, 'not_seated': 229, 'timeout_not_landed': 151, 'post_speed': 104}
- lip10: {'prop_strike': 529, 'touched_parked': 384, 'timeout_not_landed': 289, 'not_seated': 228, 'post_speed': 221}
- dual: {'not_seated': 866, 'touched_parked': 548, 'final_position': 527, 'post_speed': 498, 'prop_strike': 300}
- windward: {'prop_strike': 611, 'timeout_not_landed': 341, 'touched_parked': 222, 'not_seated': 147, 'post_speed': 120}
- slot20: {'prop_strike': 479, 'touched_parked': 319, 'not_seated': 252, 'timeout_not_landed': 233, 'post_speed': 147}
- still: {'not_seated': 130, 'prop_strike': 118, 'touched_parked': 106, 'timeout_not_landed': 80, 'over_time': 55}
- drift: {'timeout_not_landed': 33, 'prop_strike': 32, 'not_seated': 30, 'over_time': 28, 'post_speed': 26}
- still_drift: {'timeout_not_landed': 74, 'over_time': 34, 'post_speed': 27, 'touched_parked': 24, 'prop_strike': 21}
- all: {'not_seated': 1114, 'post_speed': 635, 'touched_parked': 409, 'timeout_not_landed': 295, 'prop_strike': 229}
- all_drift: {'not_seated': 409, 'post_speed': 134, 'touched_parked': 93, 'timeout_not_landed': 38, 'over_time': 19}
- all_drift_nodual: {'touched_parked': 50, 'post_speed': 40, 'prop_strike': 36, 'timeout_not_landed': 20, 'touchdown_speed': 16}
- pocket_drift: {'touchdown_speed': 24, 'prop_strike': 4, 'over_time': 3, 'final_position': 2, 'not_seated': 2}
- pocket_same_drift: {'touchdown_speed': 11, 'over_time': 10, 'not_seated': 9, 'timeout_not_landed': 7, 'prop_strike': 6}
- pocket_all_drift_nodual: {'touchdown_speed': 14, 'prop_strike': 7, 'post_speed': 3, 'timeout_not_landed': 2, 'touched_parked': 2}
