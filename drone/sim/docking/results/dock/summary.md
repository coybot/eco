
## baseline

**recovery**: overall 0.377 (0.373-0.381, n=57600); target cell (moderate, 6 m/s) 0.348 (0.335-0.362, n=4800); nominal sensors in the target cell 0.560 (0.532-0.588, n=1200); cells at >= 0.95: 0/288

| variable | value | pass fraction (95 % CI, n) |
|---|---|---|
| hover | calm | 0.407 (0.400-0.414, n=19200) |
| hover | moderate | 0.393 (0.386-0.400, n=19200) |
| hover | gusty | 0.331 (0.324-0.337, n=19200) |
| wind_m_s | 0 | 0.572 (0.564-0.580, n=14400) |
| wind_m_s | 3 | 0.559 (0.551-0.567, n=14400) |
| wind_m_s | 6 | 0.336 (0.329-0.344, n=14400) |
| wind_m_s | 9 | 0.040 (0.037-0.043, n=14400) |
| noise_scale | 1.0 | 0.481 (0.476-0.487, n=28800) |
| noise_scale | 2.0 | 0.272 (0.267-0.277, n=28800) |
| anchor_loss_label | none | 0.458 (0.453-0.464, n=28800) |
| anchor_loss_label | one | 0.295 (0.290-0.301, n=28800) |
| parked_neighbours | 0 | 0.389 (0.382-0.396, n=19200) |
| parked_neighbours | 1 | 0.375 (0.368-0.381, n=19200) |
| parked_neighbours | 2 | 0.367 (0.360-0.374, n=19200) |
| launch_box_cell_bool | False | 0.378 (0.372-0.384, n=28800) |
| launch_box_cell_bool | True | 0.376 (0.370-0.381, n=28800) |

Fail reasons: {'prop_strike': 11026, 'timeout_not_landed': 6815, 'touched_parked': 6704, 'not_seated': 3932, 'post_speed': 2748, 'over_time': 1720, 'touchdown_speed': 1314, 'final_position': 887, 'fallback_after_aborts': 751}

Other: {"cells_meeting_95": 0, "cells": 288, "time_s_median_p90_passed": [20.52, 25.48], "aborts_mean": 0.6939756944444444, "peak_contact_force_n_median_p95": [50.34, 230.35], "final_pos_err_m_median_passed": 0.003, "post_contact_speed_m_s_p95": 0.442, "anchor_lost": {"A1": {"n": 9462, "pass_frac": 0.3883956880152188, "ci95": [0.3786222913867937, 0.39825967122707473]}, "A2": {"n": 9738, "pass_frac": 0.38632162661737524, "ci95": [0.37669737516737, 0.3960355339778577]}, "A3": {"n": 9600, "pass_frac": 0.1109375, "ci95": [0.1048100535518541, 0.11737620158171662]}}}

**launch**: overall 0.112 (0.110-0.115, n=57600); target cell (moderate, 6 m/s) 0.119 (0.110-0.129, n=4800); nominal sensors in the target cell 0.111 (0.094-0.130, n=1200); cells at >= 0.95: 0/288

| variable | value | pass fraction (95 % CI, n) |
|---|---|---|
| hover | calm | 0.083 (0.080-0.087, n=19200) |
| hover | moderate | 0.108 (0.103-0.112, n=19200) |
| hover | gusty | 0.146 (0.141-0.151, n=19200) |
| wind_m_s | 0 | 0.084 (0.079-0.088, n=14400) |
| wind_m_s | 3 | 0.085 (0.081-0.090, n=14400) |
| wind_m_s | 6 | 0.123 (0.118-0.129, n=14400) |
| wind_m_s | 9 | 0.157 (0.151-0.163, n=14400) |
| noise_scale | 1.0 | 0.113 (0.110-0.117, n=28800) |
| noise_scale | 2.0 | 0.111 (0.108-0.115, n=28800) |
| anchor_loss_label | none | 0.112 (0.109-0.116, n=28800) |
| anchor_loss_label | one | 0.112 (0.108-0.116, n=28800) |
| parked_neighbours | 0 | 0.113 (0.108-0.117, n=19200) |
| parked_neighbours | 1 | 0.114 (0.109-0.118, n=19200) |
| parked_neighbours | 2 | 0.110 (0.106-0.115, n=19200) |
| launch_box_cell_bool | False | 0.022 (0.020-0.024, n=28800) |
| launch_box_cell_bool | True | 0.203 (0.198-0.207, n=28800) |

Fail reasons: {'touched_post': 51131, 'never_left_magnets': 2, 'slow_clear': 1}

Other: {"cells_meeting_95": 0, "cells": 288, "post_contact_s_median_p95": [0.026, 0.076], "post_force_peak_n_median_p95": [136.15, 430.38], "pass_frac_excluding_post_contact": 0.9999479166666667, "thrust_at_free_n_median": 19.52, "time_free_to_clear_s_p95": 0.548}


## spec4

**recovery**: overall 0.480 (0.472-0.488, n=14400); target cell (moderate, 6 m/s) 0.463 (0.435-0.492, n=1200); nominal sensors in the target cell 0.623 (0.567-0.676, n=300); cells at >= 0.95: 9/288

| variable | value | pass fraction (95 % CI, n) |
|---|---|---|
| hover | calm | 0.513 (0.499-0.527, n=4800) |
| hover | moderate | 0.501 (0.487-0.515, n=4800) |
| hover | gusty | 0.426 (0.412-0.440, n=4800) |
| wind_m_s | 0 | 0.719 (0.704-0.734, n=3600) |
| wind_m_s | 3 | 0.706 (0.691-0.721, n=3600) |
| wind_m_s | 6 | 0.434 (0.418-0.450, n=3600) |
| wind_m_s | 9 | 0.061 (0.053-0.069, n=3600) |
| noise_scale | 1.0 | 0.583 (0.572-0.595, n=7200) |
| noise_scale | 2.0 | 0.377 (0.366-0.388, n=7200) |
| anchor_loss_label | none | 0.517 (0.505-0.528, n=7200) |
| anchor_loss_label | one | 0.443 (0.432-0.455, n=7200) |
| parked_neighbours | 0 | 0.515 (0.500-0.529, n=4800) |
| parked_neighbours | 1 | 0.494 (0.479-0.508, n=4800) |
| parked_neighbours | 2 | 0.432 (0.418-0.446, n=4800) |
| launch_box_cell_bool | False | 0.479 (0.468-0.491, n=7200) |
| launch_box_cell_bool | True | 0.481 (0.469-0.492, n=7200) |

Fail reasons: {'prop_strike': 2497, 'touched_parked': 1520, 'not_seated': 940, 'timeout_not_landed': 863, 'post_speed': 703, 'over_time': 404, 'touchdown_speed': 324, 'final_position': 169, 'fallback_after_aborts': 67}

Other: {"cells_meeting_95": 9, "cells": 288, "time_s_median_p90_passed": [20.53, 25.03], "aborts_mean": 0.49472222222222223, "peak_contact_force_n_median_p95": [49.12, 229.44], "final_pos_err_m_median_passed": 0.003, "post_contact_speed_m_s_p95": 0.418, "anchor_lost": {"A1": {"n": 1761, "pass_frac": 0.4497444633730835, "ci95": [0.4266439628171278, 0.4730637503283466]}, "A2": {"n": 1783, "pass_frac": 0.4542905215928211, "ci95": [0.4313019503589314, 0.47747563798465764]}, "A3": {"n": 1798, "pass_frac": 0.407119021134594, "ci95": [0.38463100327410427, 0.4300030910880194]}, "A4": {"n": 1858, "pass_frac": 0.461248654467169, "ci95": [0.43868481956260136, 0.4839724032645178]}}}

**launch**: overall 0.114 (0.108-0.119, n=14400); target cell (moderate, 6 m/s) 0.136 (0.118-0.156, n=1200); nominal sensors in the target cell 0.150 (0.114-0.195, n=300); cells at >= 0.95: 0/288

| variable | value | pass fraction (95 % CI, n) |
|---|---|---|
| hover | calm | 0.080 (0.072-0.088, n=4800) |
| hover | moderate | 0.110 (0.101-0.119, n=4800) |
| hover | gusty | 0.151 (0.142-0.162, n=4800) |
| wind_m_s | 0 | 0.088 (0.079-0.098, n=3600) |
| wind_m_s | 3 | 0.087 (0.078-0.097, n=3600) |
| wind_m_s | 6 | 0.130 (0.120-0.142, n=3600) |
| wind_m_s | 9 | 0.149 (0.137-0.161, n=3600) |
| noise_scale | 1.0 | 0.114 (0.106-0.121, n=7200) |
| noise_scale | 2.0 | 0.113 (0.106-0.121, n=7200) |
| anchor_loss_label | none | 0.112 (0.105-0.120, n=7200) |
| anchor_loss_label | one | 0.115 (0.108-0.122, n=7200) |
| parked_neighbours | 0 | 0.114 (0.106-0.124, n=4800) |
| parked_neighbours | 1 | 0.109 (0.101-0.118, n=4800) |
| parked_neighbours | 2 | 0.117 (0.108-0.126, n=4800) |
| launch_box_cell_bool | False | 0.023 (0.020-0.027, n=7200) |
| launch_box_cell_bool | True | 0.204 (0.195-0.213, n=7200) |

Fail reasons: {'touched_post': 12765}

Other: {"cells_meeting_95": 0, "cells": 288, "post_contact_s_median_p95": [0.026, 0.076], "post_force_peak_n_median_p95": [136.25, 430.94], "pass_frac_excluding_post_contact": 1.0, "thrust_at_free_n_median": 19.52, "time_free_to_clear_s_p95": 0.548}

