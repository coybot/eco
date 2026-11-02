"""``onboard_l5.PeerProvider`` backed by a ``team_link.TeamLink``.

``OnboardL5Runtime`` takes any object with ``peers() -> list[Peer]``; this one
turns the link's last-known POSITION per teammate into ``onboard_l5.Peer``.
A teammate's vehicle class comes from its POSITION ``vclass`` field
(``TeamLink(vclass_name=...)`` on the sender) or ``default_vclass``.

    link = TeamLink.from_config(drone_id, cfg["team_link"]); link.start()
    rt = OnboardL5Runtime(..., peers=TeamLinkPeerProvider(link), ...)
"""
from __future__ import annotations

from typing import List

import numpy as np

try:
    from .onboard_l5 import Peer, get_class
    from .team_link import TeamLink
except ImportError:  # pragma: no cover - device flat-install path
    from onboard_l5 import Peer, get_class
    from team_link import TeamLink


def _known_class(name, default: str) -> str:
    """A teammate's ``vclass`` is remote input: an unknown name would raise
    KeyError inside the L5 step, so fall back to the default instead."""
    if isinstance(name, str):
        try:
            get_class(name)
            return name
        except KeyError:
            pass
    return default


class TeamLinkPeerProvider:
    def __init__(self, link: TeamLink, default_vclass: str = "quad"):
        get_class(default_vclass)   # fail at wiring time, not mid-flight
        self.link = link
        self.default_vclass = default_vclass

    def peers(self) -> List[Peer]:
        out = []
        for pid, ps in sorted(self.link.peers().items()):
            try:
                pos = np.asarray(ps.pos_enu, dtype=np.float32).reshape(3)
                vel = np.asarray(ps.vel, dtype=np.float32).reshape(3)
                conf = float(ps.confidence)
            except (TypeError, ValueError):
                continue   # malformed POSITION from a teammate: skip, don't crash the loop
            if not (np.all(np.isfinite(pos)) and np.all(np.isfinite(vel))):
                continue
            out.append(Peer(id=pid, pos_enu=pos, vel_enu=vel,
                            vclass_name=_known_class(ps.vclass_name, self.default_vclass),
                            confidence=conf, alive=True))
        return out
