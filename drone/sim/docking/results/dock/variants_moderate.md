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
| pd_soft | pocket_drift + collective ramped to 0 over 0.5 s at cutoff | **0.988** | **0.994** | 0.989 | 0.994 | 0.994 | 0.972 | 0.087 | 1.000 | 0.912 | 1440 |
| pd_cal | pocket_drift + UWB bias calibrated while parked (half the bias left) | **0.976** | **0.975** | 0.981 | 0.978 | 0.975 | 0.972 | 0.087 | 1.000 | 0.912 | 1440 |
| pd_ff | pocket_drift + mothership acceleration feedforward | **0.984** | **0.978** | 0.983 | 0.983 | 0.978 | 0.992 | 0.087 | 1.000 | 0.912 | 1440 |
| pd_dob | pocket_drift + gust observer (0.1 s), free flight only | **0.968** | **0.956** | 0.972 | 0.964 | 0.956 | 0.981 | 0.087 | 1.000 | 0.912 | 1440 |
| pd_ctl | pocket_drift + all four controller options | **0.986** | **0.983** | 0.989 | 0.989 | 0.983 | 0.983 | 0.099 | 1.000 | 0.901 | 720 |
| pd_nodob | pd_ctl without the disturbance observer | **0.988** | **0.978** | 1.000 | 0.989 | 0.978 | 0.983 | 0.099 | 1.000 | 0.901 | 720 |
| sp_rotors | spec mothership: lift rotors 0.51 m clear of the dock (with pd_nodob's options) | **0.988** | **0.978** | 1.000 | 0.989 | 0.978 | 0.983 | 0.099 | 1.000 | 0.901 | 720 |
| sp_steady | spec mothership: half the hover motion | **0.992** | **0.994** | 0.989 | 1.000 | 0.994 | 0.983 | 0.057 | 1.000 | 0.943 | 720 |
| sp_bcast | spec mothership: motion broadcast at 50 Hz, 10-20 ms | **0.997** | **0.989** | 1.000 | 1.000 | 0.989 | 1.000 | 0.089 | 1.000 | 0.911 | 720 |
| sp_all | spec mothership: all three | **0.996** | **1.000** | 1.000 | 1.000 | 1.000 | 0.983 | 0.053 | 1.000 | 0.947 | 720 |
| ch_base | channel dock: one walled lane, 0.50 m funnel mouth at the rear, drones nose to tail | **0.528** | **0.222** | 0.956 | 0.933 | 0.222 | 0.003 | 0.805 | 0.807 | 0.000 | 1440 |
| ch_ctl | channel + soft cutoff, parked calibration, dock feedforward | **0.528** | **0.203** | 0.961 | 0.939 | 0.203 | 0.011 | 0.806 | 0.808 | 0.000 | 1440 |
| ch_drift | channel + drifting hover | **0.949** | **0.953** | 0.956 | 0.964 | 0.953 | 0.925 | 0.912 | 0.918 | 0.000 | 1440 |
| ch_drift_ctl | channel + drift + the three controller options | **0.965** | **0.961** | 0.978 | 0.972 | 0.961 | 0.950 | 0.908 | 0.912 | 0.000 | 720 |
| ch_wide_drift | as ch_drift_ctl, 0.80 m mouth | **0.967** | **0.975** | 0.978 | 0.972 | 0.975 | 0.944 | 0.915 | 0.919 | 0.000 | 1440 |
| ch_narrow_drift | as ch_drift_ctl, 0.35 m mouth | **0.960** | **0.972** | 0.969 | 0.958 | 0.972 | 0.942 | 0.915 | 0.919 | 0.000 | 1440 |
| ch_pb_drift | as ch_drift_ctl, plus a short-range beacon by each slot | **0.968** | **0.975** | 0.989 | 0.975 | 0.975 | 0.933 | 0.910 | 0.916 | 0.000 | 1440 |
| base_ctl | spec baseline + all four controller options | **0.670** | **0.672** | 0.944 | 0.947 | 0.672 | 0.117 | 0.111 | 1.000 | 0.889 | 1440 |

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
- pd_soft: {'final_position': 6, 'prop_strike': 4, 'over_time': 3, 'not_seated': 2, 'touchdown_speed': 1}
- pd_cal: {'touchdown_speed': 21, 'prop_strike': 3, 'final_position': 3, 'timeout_not_landed': 2, 'post_speed': 2}
- pd_ff: {'touchdown_speed': 15, 'prop_strike': 5, 'final_position': 1, 'timeout_not_landed': 1, 'over_time': 1}
- pd_dob: {'touchdown_speed': 25, 'prop_strike': 11, 'post_speed': 4, 'over_time': 2, 'touched_parked': 1}
- pd_ctl: {'prop_strike': 6, 'not_seated': 2, 'post_speed': 1, 'final_position': 1}
- pd_nodob: {'final_position': 4, 'not_seated': 2, 'prop_strike': 1, 'touchdown_speed': 1, 'timeout_not_landed': 1}
- sp_rotors: {'final_position': 4, 'timeout_not_landed': 2, 'not_seated': 2, 'touchdown_speed': 1}
- sp_steady: {'final_position': 4, 'timeout_not_landed': 1, 'not_seated': 1}
- sp_bcast: {'not_seated': 1, 'prop_strike': 1}
- sp_all: {'final_position': 2, 'timeout_not_landed': 1}
- ch_base: {'final_position': 352, 'fallback_after_aborts': 116, 'over_time': 85, 'timeout_not_landed': 54, 'prop_strike': 34}
- ch_ctl: {'final_position': 353, 'fallback_after_aborts': 111, 'over_time': 106, 'timeout_not_landed': 39, 'prop_strike': 30}
- ch_drift: {'final_position': 30, 'timeout_not_landed': 17, 'not_seated': 13, 'touchdown_speed': 6, 'over_time': 4}
- ch_drift_ctl: {'final_position': 14, 'not_seated': 4, 'over_time': 4, 'touchdown_speed': 2, 'timeout_not_landed': 1}
- ch_wide_drift: {'final_position': 33, 'not_seated': 8, 'over_time': 4, 'touchdown_speed': 1, 'timeout_not_landed': 1}
- ch_narrow_drift: {'final_position': 27, 'over_time': 16, 'timeout_not_landed': 6, 'fallback_after_aborts': 4, 'touchdown_speed': 2}
- ch_pb_drift: {'final_position': 31, 'over_time': 6, 'not_seated': 5, 'timeout_not_landed': 3, 'touchdown_speed': 1}
- base_ctl: {'prop_strike': 152, 'not_seated': 128, 'touched_parked': 83, 'timeout_not_landed': 40, 'final_position': 26}
