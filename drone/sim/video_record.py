"""Compatibility shim — the real module is now ``drone/common/video_record.py``.

It moved because the on-aircraft daemon needs it too: install scripts copy
``drone/common/*.py`` flat into ``~/drone-api/``, so a recorder living under
``drone/sim/`` could never reach real hardware. The code itself was always
generic (a ``grab_rgb`` callable in, frames or MP4 bytes out) and had no sim
dependency, so it moved rather than being duplicated.

This shim stays because the sim tree imports it in seven places, in two
different styles (flat ``from video_record import ...`` under a sys.path that
contains ``drone/sim``, and package-relative ``from .video_record import ...``).
Loading the common module by explicit path satisfies both without depending on
``drone/common`` being on sys.path, and without the self-import a plain
``from video_record import *`` would cause here.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import pathlib as _pathlib

_TARGET = _pathlib.Path(__file__).resolve().parent.parent / "common" / "video_record.py"
_spec = _importlib_util.spec_from_file_location("_eco_common_video_record", _TARGET)
if _spec is None or _spec.loader is None:  # pragma: no cover - packaging error
    raise ImportError(f"cannot load the moved video_record module from {_TARGET}")
_mod = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

record_frames = _mod.record_frames
encode_mp4 = _mod.encode_mp4
record_mp4 = _mod.record_mp4

__all__ = ["record_frames", "encode_mp4", "record_mp4"]
