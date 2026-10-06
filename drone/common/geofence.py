"""On-board geofence for a planned mission: the search area it must stay in
and the no-fly zones it must stay out of, enforced at the backend's goto() -
the one call every move the mission loop makes goes through, typed phases and
the vision model's own decisions alike.

The planner already routes every leg it can check (aws/src/planning.py); this
is the backstop for what it cannot: an open-ended search phase deciding where
to go next. Pure stdlib, so it installs flat next to reasoning_loop.py.

Frames: the backends' poses are ENU metres (x east, y north) about their own
origin. The fence arrives in lat/lon and is converted into that frame once,
from one point known in both (Geofence.from_latlon).

What it does:
  - a move whose target is in a no-fly zone, or whose leg crosses one, is
    refused (goto returns False and the loop replans);
  - a move that would carry the vehicle out of the search area, once it is in
    it, is shortened to stop just inside the boundary;
  - the leg in from outside (the transit from a home outside the area) is only
    held to the no-fly zones;
  - a velocity command (drive) is refused while the vehicle is outside the area
    it had entered, and it is sent back inside instead.
return_home, rtl and land do not go through goto and are never held back.
"""
from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

XY = Tuple[float, float]
EARTH_RADIUS_M = 6_371_000.0


def _to_xy(lat: float, lon: float, ref_lat: float, ref_lon: float) -> XY:
    x = math.radians(lon - ref_lon) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat))
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_M
    return (x, y)


def _latlon(p) -> Optional[Tuple[float, float]]:
    if isinstance(p, dict):
        lat, lon = p.get("lat", p.get("latitude")), p.get("lon", p.get("longitude"))
    elif isinstance(p, (list, tuple)) and len(p) >= 2:
        lat, lon = p[0], p[1]
    else:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def inside(p: XY, poly: Sequence[XY]) -> bool:
    if len(poly) < 3:
        return False
    x, y = p
    res = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi:
            res = not res
        j = i
    return res


def _ccw(a, b, c):
    return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])


def _cross(p1, p2, p3, p4) -> bool:
    d1, d2 = _ccw(p3, p4, p1), _ccw(p3, p4, p2)
    d3, d4 = _ccw(p1, p2, p3), _ccw(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def enters(a: XY, b: XY, poly: Sequence[XY]) -> bool:
    if inside(a, poly) or inside(b, poly):
        return True
    n = len(poly)
    if any(_cross(a, b, poly[i], poly[(i + 1) % n]) for i in range(n)):
        return True
    return inside(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), poly)


class Geofence:
    def __init__(self, keep_in: Optional[List[XY]] = None,
                 no_fly: Sequence[List[XY]] = (), inset_m: float = 2.0):
        self.keep_in = keep_in if keep_in and len(keep_in) >= 3 else None
        self.no_fly = [z for z in no_fly if len(z) >= 3]
        self.inset_m = inset_m

    @classmethod
    def from_latlon(cls, fence: Optional[dict], ref_latlon: Tuple[float, float],
                    ref_xy: XY = (0.0, 0.0)) -> Optional["Geofence"]:
        """`fence` is the mission's {"keep_in": [lat/lon...], "no_fly": [[...]]};
        `ref_latlon` and `ref_xy` are one point in both frames (the sim's datum
        and (0, 0), or home and the pose at mission start)."""
        if not fence:
            return None
        rlat, rlon = ref_latlon

        def conv(poly):
            out = []
            for p in poly or []:
                ll = _latlon(p)
                if ll is None:
                    return []
                x, y = _to_xy(ll[0], ll[1], rlat, rlon)
                out.append((x + ref_xy[0], y + ref_xy[1]))
            return out

        g = cls(conv(fence.get("keep_in")), [conv(z) for z in fence.get("no_fly") or []])
        return g if (g.keep_in or g.no_fly) else None

    def in_area(self, p: XY) -> bool:
        return self.keep_in is None or inside(p, self.keep_in)

    def check_move(self, frm: XY, to: XY) -> Tuple[bool, Optional[XY], str]:
        """(allowed, target to fly instead or None, why). `target` differs from
        `to` when the move was shortened at the area's boundary."""
        for k, z in enumerate(self.no_fly):
            if inside(to, z):
                return False, None, f"the target is inside no-fly zone {k}"
            if enters(frm, to, z):
                return False, None, f"the way there crosses no-fly zone {k}"
        if self.keep_in is None or not inside(frm, self.keep_in):
            return True, to, ""
        if inside(to, self.keep_in) and not any(
                _cross(frm, to, self.keep_in[i], self.keep_in[(i + 1) % len(self.keep_in)])
                for i in range(len(self.keep_in))):
            return True, to, ""
        # Shorten: the furthest point along the leg that is still inside, less
        # the inset, found by bisection (the leg can leave and re-enter a
        # concave area, so only the part before the first exit counts).
        d = math.hypot(to[0] - frm[0], to[1] - frm[1])
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            p = (frm[0] + mid * (to[0] - frm[0]), frm[1] + mid * (to[1] - frm[1]))
            if inside(p, self.keep_in) and not any(
                    _cross(frm, p, self.keep_in[i], self.keep_in[(i + 1) % len(self.keep_in)])
                    for i in range(len(self.keep_in))):
                lo = mid
            else:
                hi = mid
        t = max(0.0, lo - (self.inset_m / d if d > 0 else 0.0))
        if t * d < 1.0:
            return False, None, "it would leave the search area"
        return True, (frm[0] + t * (to[0] - frm[0]), frm[1] + t * (to[1] - frm[1])), \
            "shortened at the search-area boundary"

    def way_back_in(self, p: XY) -> Optional[XY]:
        """A point just inside the area, toward its middle, for a vehicle that
        has drifted out of it."""
        if self.keep_in is None:
            return None
        n = len(self.keep_in)
        c = (sum(q[0] for q in self.keep_in) / n, sum(q[1] for q in self.keep_in) / n)
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            q = (p[0] + mid * (c[0] - p[0]), p[1] + mid * (c[1] - p[1]))
            if inside(q, self.keep_in):
                hi = mid
            else:
                lo = mid
        d = math.hypot(c[0] - p[0], c[1] - p[1]) or 1.0
        t = min(1.0, hi + self.inset_m / d)
        return (p[0] + t * (c[0] - p[0]), p[1] + t * (c[1] - p[1]))


class GeofencedBackend:
    """Wraps a mission backend so every goto() and drive() is checked against
    a Geofence. Everything else passes straight through."""

    def __init__(self, inner, fence: Geofence, report: Callable[[str], None] = print):
        self._inner = inner
        self._fence = fence
        self._report = report
        self._entered = False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def inner(self):
        return self._inner

    def _here(self) -> Optional[XY]:
        try:
            pose = self._inner.get_pose()
        except Exception:
            pose = None
        if pose is None:
            return None
        here = (float(pose[0]), float(pose[1]))
        if self._fence.in_area(here):
            self._entered = True
        return here

    def goto(self, north_m, east_m, alt_m, *args, **kwargs):
        here = self._here()
        if here is None:
            # Cannot place the vehicle: the planner's own check of this leg is
            # all there is. Say so rather than pretend.
            self._report("Geofence: position unknown, move not checked")
            return self._inner.goto(north_m, east_m, alt_m, *args, **kwargs)
        ok, target, why = self._fence.check_move(here, (float(east_m), float(north_m)))
        if not ok:
            self._report(f"Geofence: refused a move - {why}")
            return False
        if why:
            self._report(f"Geofence: {why}")
            east_m, north_m = target
        return self._inner.goto(north_m, east_m, alt_m, *args, **kwargs)

    def drive(self, *args, **kwargs):
        here = self._here()
        if here is not None and self._entered and not self._fence.in_area(here):
            back = self._fence.way_back_in(here)
            self._report("Geofence: outside the search area - heading back in")
            if back is not None:
                try:
                    pose = self._inner.get_pose()
                    alt = float(pose[2]) if pose is not None else 0.0
                except Exception:
                    alt = 0.0
                self._inner.goto(back[1], back[0], alt)
            return None
        return self._inner.drive(*args, **kwargs)
