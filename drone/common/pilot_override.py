"""Notice when the pilot takes the aircraft back with the transmitter.

A pilot landing by hand mid-mission used to fight coybot: ArduCopter ignores
the sticks in GUIDED, and when the pilot flipped the mode switch instead,
coybot's next stop/hold/move put the aircraft straight back into GUIDED (or
BRAKE, or RTL for the battery guard). This decides WHEN the pilot has taken
over; drone_sdk decides what that means (stop commanding, hand the FC a
pilot-flown mode).

Two signals, both from the flight controller's own telemetry:

  * Sticks: RC_CHANNELS for roll/pitch/throttle/yaw move more than a deadband
    away from where they sat when coybot took command, in two readings running
    (one glitched frame is not a pilot). The baseline, not the stick centre,
    because a throttle stick rests wherever the pilot left it.
  * Mode: the FC changed flight mode to one coybot did not ask for. That is the
    transmitter's mode switch, or one of the FC's own failsafes (EKF, fence,
    radio) - coybot must not undo either.

Pure logic: no MAVLink, no clock of its own.
"""

from typing import Dict, Optional

# ArduPilot's default RCMAP: 1 roll, 2 pitch, 3 throttle, 4 yaw.
STICKS = {1: 'roll', 2: 'pitch', 3: 'throttle', 4: 'yaw'}
DEADBAND_US = 100       # 10% of a 1000-2000 us stick throw
CONFIRM_READINGS = 2    # consecutive deflected readings before it counts
VALID_US = (800, 2200)  # outside this the receiver has no signal (0, 65535, failsafe)
# A mode coybot asked for within this long is its own, not the pilot's. Mode
# requests are resent until confirmed, so this is measured from the last one.
REQUEST_WINDOW_S = 5.0


class PilotWatch:
    def __init__(self, sticks=None, deadband_us=DEADBAND_US, confirm=CONFIRM_READINGS):
        self.sticks = dict(sticks or STICKS)
        self.deadband_us = deadband_us
        self.confirm = confirm
        self._requested: Dict[str, float] = {}
        self._mode: Optional[str] = None
        self.rebaseline()

    def rebaseline(self):
        """Take the sticks' next valid position as where they rest."""
        self._baseline = None
        self._streak = 0

    def requested(self, mode: str, now: float):
        """coybot is asking the FC for `mode`."""
        self._requested[mode] = now

    def on_rc(self, channels: Dict[int, int], now: float) -> Optional[str]:
        """channels: {channel number: pulse width in us}. Returns why the pilot
        has taken over, or None."""
        vals = [channels.get(n) for n in self.sticks]
        if any(v is None or not VALID_US[0] <= v <= VALID_US[1] for v in vals):
            self._streak = 0
            return None
        if self._baseline is None:
            self._baseline = vals
            return None
        moved = [(name, v - b) for name, v, b in zip(self.sticks.values(), vals, self._baseline)
                 if abs(v - b) > self.deadband_us]
        if not moved:
            self._streak = 0
            return None
        self._streak += 1
        if self._streak < self.confirm:
            return None
        name, delta = max(moved, key=lambda m: abs(m[1]))
        return f"the pilot moved the {name} stick on the transmitter ({delta:+d} us)"

    def on_mode(self, mode: str, now: float) -> Optional[str]:
        """The FC's current flight mode, from each of its heartbeats. Returns
        why the pilot has taken over, or None."""
        prev, self._mode = self._mode, mode
        if prev is None or mode == prev:
            return None
        asked = self._requested.get(mode)
        if asked is not None and now - asked <= REQUEST_WINDOW_S:
            return None
        return (f"the flight mode changed from {prev} to {mode} without coybot asking "
                f"(the transmitter's mode switch, or a flight-controller failsafe)")

    def handed_back(self, mode: str) -> bool:
        """While the pilot flies: did they select GUIDED themselves? coybot asks
        for no mode while the pilot has control, so a switch into GUIDED is
        theirs - the transmitter's way of saying "coybot, fly it"."""
        return mode == 'GUIDED' and self._mode != 'GUIDED'
