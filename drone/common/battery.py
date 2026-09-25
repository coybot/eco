"""Battery state-of-charge estimation and the flight-budget governor.

Pure stdlib, no pymavlink, so it imports and tests anywhere. drone_sdk.py feeds
it raw flight-controller readings; reasoning_loop.py and drone_sdk.py ask it
whether the next action is affordable.

Why this exists. The number the app showed was SYS_STATUS.battery_remaining,
which ArduPilot (BATT_MONITOR=4) derives purely from integrated current:
100% - consumed_mAh / BATT_CAPACITY. That has two failure modes, and the
aircraft hit both:

  * It resets to 100% on every FC boot, whatever pack is plugged in.
  * With a current sensor that reads ~0 A (wrong BATT_CURR_PIN or
    BATT_AMP_PERVLT for the board), consumed_mAh never moves, so the
    percentage freezes wherever it started. A pack flown flat still reads 87%.

Nothing needs a multimeter to fix that. This module:

  1. Estimates charge from resting pack VOLTAGE on the ground (a LiPo's
     open-circuit voltage is a reliable charge indicator), anchors that at
     arm time, and in flight takes current integration only once the current
     sensor has been seen producing flight-level current. Otherwise it falls
     back to sag-compensated voltage.
  2. Judges each reading for plausibility and says, in words, which FC
     parameter is wrong when it isn't.
  3. Prices each action (takeoff, climb, transit, hover/record) in percent of
     pack, and refuses any action that would leave too little to get home and
     land with a reserve.

Units: volts, amps, mAh, metres, m/s, seconds, percent of pack (0-100).
"""
from __future__ import annotations

import bisect
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple


# Resting (open-circuit) LiPo cell voltage -> state of charge, %. The usual
# hobby-LiPo curve; it only holds for a pack that has rested (no load for a
# minute or so), which is why it is used on the ground and only with a sag
# allowance in the air.
_OCV_TABLE = [
    (3.27, 0), (3.61, 5), (3.69, 10), (3.71, 15), (3.73, 20), (3.75, 25),
    (3.77, 30), (3.79, 35), (3.80, 40), (3.82, 45), (3.84, 50), (3.85, 55),
    (3.87, 60), (3.91, 65), (3.95, 70), (3.98, 75), (4.02, 80), (4.08, 85),
    (4.11, 90), (4.15, 95), (4.20, 100),
]
_OCV_V = [v for v, _ in _OCV_TABLE]
_OCV_P = [p for _, p in _OCV_TABLE]

# A cell outside this band means the reading, not the pack, is wrong: either
# BATT_VOLT_MULT / BATT_VOLT_PIN or the configured cell count.
PLAUSIBLE_CELL_V = (3.0, 4.35)

# What a charger terminates a LiPo at. The no-multimeter voltage reference.
FULL_CHARGE_CELL_V = 4.20

# A flight-controller analog input at or above this is pinned at the ADC's
# 3.3 V ceiling: nothing (or the wrong thing) is wired to that pin, and
# reading x multiplier is a constant that only looks like a voltage. Found on
# the quadcopter: 3.289 V x BATT_VOLT_MULT 5.0 = a permanent "16.446 V".
ADC_RAIL_V = 3.25

# A pack that has not sagged this much after this long in the air is not
# being measured at all.
MIN_FLIGHT_SAG_V = 0.05
SAG_PROOF_AFTER_S = 20.0


def pct_from_cell_voltage(cell_v: float) -> float:
    """Resting cell voltage (V) -> state of charge (%), linearly interpolated."""
    if cell_v <= _OCV_V[0]:
        return 0.0
    if cell_v >= _OCV_V[-1]:
        return 100.0
    i = bisect.bisect_right(_OCV_V, cell_v)
    v0, v1 = _OCV_V[i - 1], _OCV_V[i]
    p0, p1 = _OCV_P[i - 1], _OCV_P[i]
    return p0 + (p1 - p0) * (cell_v - v0) / (v1 - v0)


def cell_voltage_for_pct(pct: float) -> float:
    """Inverse of pct_from_cell_voltage: state of charge (%) -> resting cell V."""
    pct = max(0.0, min(100.0, pct))
    i = bisect.bisect_left(_OCV_P, pct)
    if i == 0:
        return _OCV_V[0]
    p0, p1 = _OCV_P[i - 1], _OCV_P[i]
    v0, v1 = _OCV_V[i - 1], _OCV_V[i]
    return v0 + (v1 - v0) * (pct - p0) / (p1 - p0)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class BatteryConfig:
    """Pack description, safety thresholds and the per-action cost model.

    Loaded from the `battery:` section of config.yaml; every field has a
    conservative default. The cost-model numbers are starting points for a
    ~5000 mAh 4S quad and are meant to be tuned from flight logs: after a
    flight, compare the estimate's drop against `hover_pct_per_min`.
    """
    cells: int = 4
    capacity_mah: float = 5000.0

    # Safety thresholds, % of pack.
    min_takeoff_pct: float = 35.0     # refuse takeoff below this
    landing_reserve_pct: float = 20.0  # must still be in the pack at touchdown
    critical_pct: float = 10.0        # below this after RTL -> land where you are
    low_battery_pct: float = 35.0     # "low battery mode" starts here
    low_mode_max_alt_m: float = 10.0  # no climbing above this in low mode

    # Cost model: percent per minute of hover, and power multipliers relative
    # to hover for the other flight regimes.
    hover_pct_per_min: float = 7.0
    climb_mult: float = 1.5
    cruise_mult: float = 1.1
    descend_mult: float = 0.7
    cruise_speed_mps: float = 3.0
    climb_rate_mps: float = 1.0
    descent_rate_mps: float = 0.7
    takeoff_overhead_pct: float = 1.0  # spool-up, arming, settling
    landing_overhead_pct: float = 1.0  # final slow descent and touchdown

    # Estimator.
    hover_sag_per_cell_v: float = 0.15  # loaded-vs-resting drop at hover
    min_flight_current_a: float = 2.0   # a flying quad always draws more

    # Escape hatch for a pack that can't be read at all. Default off: an
    # unknown battery is treated as not fit to fly.
    allow_unknown_battery: bool = False

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "BatteryConfig":
        d = dict(d or {})
        # config.yaml has used these names since before this module.
        aliases = {"capacity": "capacity_mah"}
        out = cls()
        for key, value in d.items():
            key = aliases.get(key, key)
            if hasattr(out, key) and value is not None:
                current = getattr(out, key)
                try:
                    if isinstance(current, bool):
                        value = bool(value)
                    elif isinstance(current, int):
                        value = int(value)
                    elif isinstance(current, float):
                        value = float(value)
                except (TypeError, ValueError):
                    continue
                setattr(out, key, value)
        return out

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "BatteryConfig":
        """Read config.yaml's `battery:` section; defaults if absent/unreadable."""
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            return cls.from_dict(cfg.get("battery"))
        except Exception:
            return cls()


# =============================================================================
# Estimation
# =============================================================================

@dataclass
class BatterySample:
    """One raw reading from the flight controller."""
    voltage: Optional[float] = None       # pack V (SYS_STATUS.voltage_battery)
    current_a: Optional[float] = None     # A, None if the FC has no sensor
    consumed_mah: Optional[float] = None  # BATTERY_STATUS.current_consumed
    fc_remaining: Optional[float] = None  # the FC's own %, -1/None if unknown
    armed: bool = False
    t: float = field(default_factory=time.time)
    # Raw analog-input volts behind voltage/current_a (reading / FC scale), when
    # the FC scale is known. Used only to spot a pin pinned at the ADC rail.
    volt_adc_v: Optional[float] = None
    curr_adc_v: Optional[float] = None
    # Actually in the air (not just armed). Armed-and-idle on the ground draws
    # little current and does not sag the pack, so the sensor-health checks
    # below only count flying time. None = unknown, treated as `armed`.
    flying: Optional[bool] = None


@dataclass
class BatteryEstimate:
    pct: Optional[float]                  # None = cannot be trusted at all
    source: str                           # 'voltage_rest' | 'voltage_loaded' | 'current' | 'fc' | 'unknown'
    voltage: Optional[float] = None
    cell_voltage: Optional[float] = None
    current_a: Optional[float] = None
    fc_remaining: Optional[float] = None
    voltage_trusted: bool = False
    current_trusted: bool = False
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pct": None if self.pct is None else round(self.pct, 1),
            "source": self.source,
            "voltage": None if self.voltage is None else round(self.voltage, 2),
            "cell_voltage": None if self.cell_voltage is None else round(self.cell_voltage, 3),
            "current_a": None if self.current_a is None else round(self.current_a, 2),
            "fc_remaining": self.fc_remaining,
            "voltage_trusted": self.voltage_trusted,
            "current_trusted": self.current_trusted,
            "warnings": list(self.warnings),
        }


class BatteryEstimator:
    """Turns a stream of FC samples into one trustworthy state of charge.

    Ground: resting voltage through the LiPo curve. That is also the anchor
    taken at the moment of arming. Air: current integration from the anchor
    once the current sensor has proven itself, else voltage with a sag
    allowance. While armed the estimate only ever goes down (sag recovers
    when the throttle drops, and that must not read as charge coming back).
    """

    # How long armed with no flight-level current before calling the
    # current sensor dead. Long enough to cover arming-to-takeoff idle.
    CURRENT_DEAD_AFTER_S = 8.0
    # How long a low reading must persist before it counts as charge used.
    SAG_WINDOW_S = 15.0

    def __init__(self, config: Optional[BatteryConfig] = None):
        self.config = config or BatteryConfig()
        self._was_armed = False
        self._armed_since: Optional[float] = None
        self._anchor_pct: Optional[float] = None
        self._anchor_mah: Optional[float] = None
        self._current_proven = False
        self._current_dead = False
        self._flight_min_pct: Optional[float] = None
        self._window: Deque[Tuple[float, float]] = deque()
        self._last: Optional[BatteryEstimate] = None
        # Consumed mAh at the last arm, and the flight total at the last
        # disarm: what calibrate_current() compares a charger's mAh against.
        self._arm_consumed_mah: Optional[float] = None
        self.last_flight_consumed_mah: Optional[float] = None
        # Voltage range seen while armed, and whether the voltage has been
        # caught not sagging in flight (sticky until the next arm).
        self._flight_v_min: Optional[float] = None
        self._flight_v_max: Optional[float] = None
        self._voltage_flat = False
        self._flying_since: Optional[float] = None
        # The reading that failed to sag in flight. Kept after landing so the
        # same constant is not believed at rest either, until it changes (a
        # pack swap, or the sensor being fixed).
        self._flat_voltage: Optional[float] = None

    @property
    def last(self) -> Optional[BatteryEstimate]:
        return self._last

    def _voltage_ok(self, v: Optional[float]) -> bool:
        if v is None or v <= 0:
            return False
        lo, hi = PLAUSIBLE_CELL_V
        return lo * self.config.cells <= v <= hi * self.config.cells

    def update(self, s: BatterySample) -> BatteryEstimate:
        cfg = self.config
        warnings: List[str] = []

        # Arm / disarm edges.
        if s.armed and not self._was_armed:
            self._armed_since = s.t
            self._flight_min_pct = None
            self._window.clear()
            self._arm_consumed_mah = s.consumed_mah
            # Anchor at arm time from the last resting estimate.
            if self._last is not None and self._last.source == "voltage_rest":
                self._anchor_pct = self._last.pct
                self._anchor_mah = s.consumed_mah
        elif not s.armed and self._was_armed:
            if self._arm_consumed_mah is not None and s.consumed_mah is not None:
                used = s.consumed_mah - self._arm_consumed_mah
                if used >= 0:
                    self.last_flight_consumed_mah = used
            self._armed_since = None
        self._was_armed = s.armed
        flying = s.armed if s.flying is None else (s.armed and s.flying)
        if flying and self._flying_since is None:
            self._flying_since = s.t
            self._flight_v_min = self._flight_v_max = None
        elif not flying:
            self._flying_since = None
            self._voltage_flat = False

        voltage_ok = self._voltage_ok(s.voltage)
        cell_v = (s.voltage / cfg.cells) if (s.voltage and s.voltage > 0) else None
        current_a = s.current_a
        if s.curr_adc_v is not None and s.curr_adc_v >= ADC_RAIL_V:
            warnings.append(
                f"current input is pinned at the {s.curr_adc_v:.2f} V ADC limit - nothing "
                f"is wired to BATT_CURR_PIN; the {s.current_a:.0f} A it shows is not real")
            current_a = None
        voltage_railed = s.volt_adc_v is not None and s.volt_adc_v >= ADC_RAIL_V
        if voltage_railed:
            warnings.append(
                f"voltage input is pinned at the {s.volt_adc_v:.2f} V ADC limit - nothing "
                f"is wired to BATT_VOLT_PIN; the {s.voltage:.2f} V it shows is a constant, "
                f"not the pack")
        # A real pack sags under flight load. One that stays flat after
        # SAG_PROOF_AFTER_S in the air is not being measured.
        if flying and s.voltage:
            self._flight_v_min = s.voltage if self._flight_v_min is None else min(self._flight_v_min, s.voltage)
            self._flight_v_max = s.voltage if self._flight_v_max is None else max(self._flight_v_max, s.voltage)
            if (s.t - self._flying_since >= SAG_PROOF_AFTER_S
                    and self._flight_v_max - self._flight_v_min < MIN_FLIGHT_SAG_V
                    and not self._current_proven):
                self._voltage_flat = True
                self._flat_voltage = s.voltage
        if self._voltage_flat:
            warnings.append(
                f"pack voltage has not moved ({self._flight_v_min:.2f}-{self._flight_v_max:.2f} V) "
                f"in {SAG_PROOF_AFTER_S:.0f} s of flight - the FC is not measuring the battery")
        stale_flat = (not flying and self._flat_voltage is not None and s.voltage
                      and abs(s.voltage - self._flat_voltage) < 0.1)
        if not flying and self._flat_voltage is not None and not stale_flat:
            self._flat_voltage = None  # the reading moved: pack swapped or sensor fixed
        if stale_flat:
            warnings.append(
                f"{s.voltage:.2f} V is the reading that did not sag in the last flight - "
                f"not trusted until it changes")
        if voltage_railed or self._voltage_flat or stale_flat:
            voltage_ok = False
        elif s.voltage is not None and not voltage_ok:
            if not s.voltage or s.voltage <= 0.5:
                warnings.append(
                    "FC reports no pack voltage - BATT_MONITOR is off or BATT_VOLT_PIN "
                    "does not match this board's power-module input")
            else:
                warnings.append(
                    f"pack reads {s.voltage:.2f} V, impossible for a {cfg.cells}S LiPo "
                    f"({cell_v:.2f} V/cell) - BATT_VOLT_MULT/BATT_VOLT_PIN or the "
                    f"configured cell count is wrong")

        # Current sensor plausibility. Only judgeable in the air: armed and
        # idling on the ground can draw well under the flight threshold.
        if flying and current_a is not None and current_a >= cfg.min_flight_current_a:
            self._current_proven = True
            self._current_dead = False
        if (flying and not self._current_proven and self._flying_since is not None
                and s.t - self._flying_since >= self.CURRENT_DEAD_AFTER_S):
            self._current_dead = True
        if current_a is None:
            warnings.append("FC has no current sensor configured - using voltage only")
        elif self._current_dead:
            warnings.append(
                f"flying but current never exceeded {cfg.min_flight_current_a:.1f} A - "
                f"current sensor not reading (BATT_CURR_PIN/BATT_AMP_PERVLT); the FC's "
                f"own % will not move, using voltage instead")
        current_trusted = (self._current_proven and s.consumed_mah is not None
                           and self._anchor_pct is not None)

        pct: Optional[float] = None
        source = "unknown"
        if not s.armed:
            if voltage_ok:
                pct = pct_from_cell_voltage(cell_v)
                source = "voltage_rest"
        else:
            candidates = []
            if current_trusted:
                used = s.consumed_mah - (self._anchor_mah or 0.0)
                pct_c = self._anchor_pct - 100.0 * used / cfg.capacity_mah
                candidates.append(("current", pct_c))
            if voltage_ok:
                pct_v = pct_from_cell_voltage(cell_v + cfg.hover_sag_per_cell_v)
                candidates.append(("voltage_loaded", pct_v))
            if len(candidates) == 2:
                # Trust the integrated current, but not past what the voltage
                # says with generous headroom: an old pack with less real
                # capacity than BATT_CAPACITY shows up as voltage falling
                # faster than the coulomb count, and that must win.
                (_, pct_c), (_, pct_v) = candidates
                if pct_v + 15.0 < pct_c:
                    pct, source = pct_v + 15.0, "voltage_loaded"
                    warnings.append(
                        "pack voltage is falling faster than the current count - "
                        "pack may be worn or BATT_CAPACITY too high")
                else:
                    pct, source = pct_c, "current"
            elif candidates:
                source, pct = candidates[0]
            if pct is not None:
                # Reject sag, then never let it rise. Load can only pull the
                # voltage DOWN, so the highest reading over a short window is
                # the one closest to the truth: a climb's sag spike drops out
                # of the window instead of sticking as lost charge, while real
                # depletion persists across it and comes through.
                self._window.append((s.t, pct))
                while self._window and s.t - self._window[0][0] > self.SAG_WINDOW_S:
                    self._window.popleft()
                pct = max(p for _, p in self._window)
                if self._flight_min_pct is not None:
                    pct = min(pct, self._flight_min_pct)
                self._flight_min_pct = pct

        if pct is None and s.fc_remaining is not None and s.fc_remaining >= 0 \
                and cfg.allow_unknown_battery:
            pct, source = float(s.fc_remaining), "fc"
            warnings.append("only the FC's own % is available and it is unverified")

        if pct is not None:
            pct = max(0.0, min(100.0, pct))

        est = BatteryEstimate(
            pct=pct, source=source, voltage=s.voltage, cell_voltage=cell_v,
            current_a=current_a,
            fc_remaining=s.fc_remaining if (s.fc_remaining is not None and s.fc_remaining >= 0) else None,
            voltage_trusted=voltage_ok, current_trusted=current_trusted,
            warnings=warnings,
        )
        self._last = est
        return est


# =============================================================================
# Cost model and governor
# =============================================================================

@dataclass
class Verdict:
    ok: bool
    action: str          # 'ok' | 'deny' | 'return_home' | 'land_now'
    reason: str = ""
    cost_pct: float = 0.0
    remaining_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "action": self.action, "reason": self.reason,
                "cost_pct": round(self.cost_pct, 1),
                "remaining_pct": None if self.remaining_pct is None else round(self.remaining_pct, 1)}


class BatteryGovernor:
    """Prices actions in % of pack and decides what is still affordable.

    The rule is one line: after this action you must still be able to fly
    home and land with `landing_reserve_pct` left. Everything else is the
    cost model that makes "able to fly home" a number.
    """

    def __init__(self, config: Optional[BatteryConfig] = None):
        self.config = config or BatteryConfig()

    # ---- cost model (all return % of pack) --------------------------------

    def _pct_for(self, seconds: float, mult: float) -> float:
        return max(0.0, seconds) / 60.0 * self.config.hover_pct_per_min * mult

    def hover_cost(self, seconds: float) -> float:
        return self._pct_for(seconds, 1.0)

    def climb_cost(self, dz_m: float) -> float:
        if dz_m <= 0:
            return self._pct_for(-dz_m / self.config.descent_rate_mps, self.config.descend_mult)
        return self._pct_for(dz_m / self.config.climb_rate_mps, self.config.climb_mult)

    def transit_cost(self, dist_m: float) -> float:
        return self._pct_for(dist_m / self.config.cruise_speed_mps, self.config.cruise_mult)

    def takeoff_cost(self, alt_m: float) -> float:
        return self.config.takeoff_overhead_pct + self.climb_cost(alt_m)

    def land_cost(self, alt_m: float) -> float:
        return self.config.landing_overhead_pct + self.climb_cost(-max(0.0, alt_m))

    def home_cost(self, dist_home_m: float, alt_m: float) -> float:
        """Fly home at the current altitude, then descend and land."""
        return self.transit_cost(dist_home_m) + self.land_cost(alt_m)

    def move_cost(self, dist_m: float, dz_m: float = 0.0) -> float:
        return self.transit_cost(dist_m) + self.climb_cost(dz_m)

    # ---- decisions --------------------------------------------------------

    def _unknown(self, pct: Optional[float]) -> Optional[Verdict]:
        if pct is None and not self.config.allow_unknown_battery:
            return Verdict(False, "deny",
                           "battery level cannot be determined - fix the FC battery "
                           "monitor (see batteryWarnings) or set "
                           "battery.allow_unknown_battery")
        return None

    def preflight(self, pct: Optional[float], planned_alt_m: float) -> Verdict:
        """May we take off at all?"""
        cfg = self.config
        bad = self._unknown(pct)
        if bad:
            return bad
        if pct is None:
            return Verdict(True, "ok", "battery unknown - allowed by config")
        if pct < cfg.min_takeoff_pct:
            return Verdict(False, "deny",
                           f"battery {pct:.0f}% is below the {cfg.min_takeoff_pct:.0f}% "
                           f"takeoff minimum - charge or swap the pack",
                           remaining_pct=pct)
        cost = self.takeoff_cost(planned_alt_m) + self.land_cost(planned_alt_m)
        if pct - cost < cfg.landing_reserve_pct:
            return Verdict(False, "deny",
                           f"battery {pct:.0f}% cannot cover takeoff to {planned_alt_m:.0f} m "
                           f"and landing ({cost:.0f}%) with the {cfg.landing_reserve_pct:.0f}% "
                           f"reserve", cost_pct=cost, remaining_pct=pct)
        return Verdict(True, "ok", cost_pct=cost, remaining_pct=pct)

    def in_flight(self, pct: Optional[float], dist_home_m: float, alt_m: float) -> Verdict:
        """Standing check, independent of any action: must we go home now?"""
        cfg = self.config
        if pct is None:
            if cfg.allow_unknown_battery:
                return Verdict(True, "ok", "battery unknown - allowed by config")
            return Verdict(False, "return_home", "battery level lost in flight - returning home")
        home = self.home_cost(dist_home_m, alt_m)
        if pct - home >= cfg.landing_reserve_pct:
            return Verdict(True, "ok", cost_pct=home, remaining_pct=pct)
        if pct - home >= cfg.critical_pct:
            return Verdict(False, "return_home",
                           f"battery {pct:.0f}% only just covers the trip home "
                           f"({home:.0f}%) - returning to land",
                           cost_pct=home, remaining_pct=pct)
        return Verdict(False, "land_now",
                       f"battery {pct:.0f}% cannot safely reach home ({home:.0f}%) - "
                       f"landing where it is", cost_pct=home, remaining_pct=pct)

    def check_action(self, pct: Optional[float], cost_pct: float,
                     dist_home_after_m: float, alt_after_m: float,
                     kind: str = "action") -> Verdict:
        """Can we afford `cost_pct` now and still get home from where it leaves us?"""
        cfg = self.config
        bad = self._unknown(pct)
        if bad:
            return bad
        if pct is None:
            return Verdict(True, "ok", "battery unknown - allowed by config", cost_pct=cost_pct)
        if pct < cfg.low_battery_pct:
            if kind in ("takeoff", "start_recording"):
                return Verdict(False, "deny",
                               f"low battery ({pct:.0f}%) - no {kind.replace('_', ' ')}",
                               cost_pct=cost_pct, remaining_pct=pct)
            if alt_after_m > cfg.low_mode_max_alt_m + 0.5:
                return Verdict(False, "deny",
                               f"low battery ({pct:.0f}%) - staying at or below "
                               f"{cfg.low_mode_max_alt_m:.0f} m",
                               cost_pct=cost_pct, remaining_pct=pct)
        after = pct - cost_pct
        home_after = self.home_cost(dist_home_after_m, alt_after_m)
        if after - home_after < cfg.landing_reserve_pct:
            return Verdict(False, "deny",
                           f"{kind} costs ~{cost_pct:.0f}% and would leave {after:.0f}%, "
                           f"not enough to get home ({home_after:.0f}%) with the "
                           f"{cfg.landing_reserve_pct:.0f}% reserve",
                           cost_pct=cost_pct, remaining_pct=pct)
        return Verdict(True, "ok", cost_pct=cost_pct, remaining_pct=pct)

    def spare_pct(self, pct: Optional[float], dist_home_m: float, alt_m: float) -> Optional[float]:
        """What is left to spend on the mission before the trip home."""
        if pct is None:
            return None
        return max(0.0, pct - self.home_cost(dist_home_m, alt_m) - self.config.landing_reserve_pct)

    def actions_left(self, pct: Optional[float], dist_home_m: float, alt_m: float,
                     typical_cost_pct: Optional[float] = None) -> Optional[int]:
        """Roughly how many typical actions (default: 30 s of flying) remain."""
        spare = self.spare_pct(pct, dist_home_m, alt_m)
        if spare is None:
            return None
        typical = typical_cost_pct if typical_cost_pct is not None else \
            self._pct_for(30.0, self.config.cruise_mult)
        return int(math.floor(spare / typical)) if typical > 0 else None


# =============================================================================
# Calibration arithmetic (the FC I/O lives in drone_sdk.py)
# =============================================================================

def voltage_mult_correction(measured_v: float, reference_v: float,
                            max_correction: float = 0.15) -> float:
    """New/old BATT_VOLT_MULT ratio so `measured_v` would read `reference_v`.

    Refuses corrections bigger than `max_correction`: an error that size is
    not a scale problem but a wrong pin or a wrong cell count, and scaling it
    away would hide the real fault.
    """
    if measured_v <= 0.5:
        raise ValueError("FC reads no voltage - BATT_VOLT_PIN is wrong for this "
                         "board; a scale factor cannot fix that")
    ratio = reference_v / measured_v
    if abs(ratio - 1.0) > max_correction:
        raise ValueError(
            f"FC reads {measured_v:.2f} V against a {reference_v:.2f} V reference "
            f"(x{ratio:.2f}) - too far off to be a scale error; check the cell "
            f"count and BATT_VOLT_PIN")
    return ratio


def current_scale_correction(fc_consumed_mah: float, charger_mah: float,
                             bounds=(0.5, 2.0)) -> float:
    """New/old BATT_AMP_PERVLT ratio from mAh the charger put back vs FC count."""
    if fc_consumed_mah < 100:
        raise ValueError(
            f"FC counted only {fc_consumed_mah:.0f} mAh for the flight - the current "
            f"sensor is not reading at all (BATT_CURR_PIN), which scaling cannot fix")
    if charger_mah <= 0:
        raise ValueError("charger mAh must be positive")
    ratio = charger_mah / fc_consumed_mah
    if not bounds[0] <= ratio <= bounds[1]:
        raise ValueError(
            f"charger put back {charger_mah:.0f} mAh but the FC counted "
            f"{fc_consumed_mah:.0f} mAh (x{ratio:.2f}) - outside {bounds[0]}-{bounds[1]}x, "
            f"check it was the same pack and flight")
    return ratio
