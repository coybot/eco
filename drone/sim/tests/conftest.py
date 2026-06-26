"""Put the flat sim/ dir on sys.path so tests import like the on-device layout.

The sim modules import each other flatly (`from vehicle_class import ...`), matching
how install scripts copy them. Tests therefore run with sim/ on the path. Run:
    eco/drone/sim/.venv-mac/bin/python -m pytest eco/drone/sim/tests -q
"""
import sys
from pathlib import Path

_SIM = Path(__file__).resolve().parent.parent
if str(_SIM) not in sys.path:
    sys.path.insert(0, str(_SIM))
