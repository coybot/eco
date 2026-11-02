"""platforms/orin/apply_params.py and the GPS-denied .param files it applies.

The script is run by hand against the real Cube, so its refusals are the
point: armed (or unknown) -> nothing written; an incomplete backup -> nothing
written; every write read back and compared as the FC stores it (float32).

Run: cd eco && python3 -m pytest drone/common/tests/test_apply_params.py -v
"""

import json
import sys
from pathlib import Path

import pytest

ORIN = Path(__file__).resolve().parents[2] / "platforms" / "orin"
sys.path.insert(0, str(ORIN))

import apply_params as ap  # noqa: E402

PARAMS = ORIN / "params"


class FakeFc:
    """An FC that stores float32s, knows some names, and can arm mid-way."""

    def __init__(self, params, arm_after_writes=None, hidden=(), unhidden_by=None, corrupt=()):
        self.p = {k: {'value': ap.as_float32(v), 'type': 9} for k, v in params.items()}
        self.writes = []
        self.arm_after_writes = arm_after_writes
        self.hidden = set(hidden)            # appear only once `unhidden_by` is written
        self.unhidden_by = unhidden_by
        self.corrupt = set(corrupt)          # the FC stores something else
        self.m = type('M', (), {'target_system': 1})()

    def armed(self, fresh_s=2.5):
        return self.arm_after_writes is not None and len(self.writes) >= self.arm_after_writes

    def write(self, name, value, ptype):
        self.writes.append((name, value))
        if name == self.unhidden_by:
            for h in self.hidden:
                self.p[h] = {'value': 0.0, 'type': 9}
            self.hidden = set()
        if name not in self.p:
            return None
        self.p[name]['value'] = ap.as_float32(value + (1 if name in self.corrupt else 0))
        return dict(self.p[name])

    def read(self, name, timeout=3.0):
        return dict(self.p[name]) if name in self.p else None

    def fetch_all(self, timeout=60.0):
        return {k: dict(v, index=i) for i, (k, v) in enumerate(sorted(self.p.items()))}


def write_file(tmp_path, text):
    p = tmp_path / "x.param"
    p.write_text(text)
    return p


# -- parsing -------------------------------------------------------------------

def test_parse_formats_and_comments(tmp_path):
    p = write_file(tmp_path, "# comment\n\nEK3_SRC1_POSXY 6\nVISO_TYPE,1\nwp_spd\t10.5  # trailing\n")
    assert ap.parse_param_file(p) == [('EK3_SRC1_POSXY', 6.0), ('VISO_TYPE', 1.0), ('WP_SPD', 10.5)]


@pytest.mark.parametrize("text", ["EK3_SRC1_POSXY\n", "EK3_SRC1_POSXY six\n", "A 1\nA 2\n",
                                  "THIS_NAME_IS_TOO_LONG 1\n", "X nan\n"])
def test_parse_refuses_malformed(tmp_path, text):
    with pytest.raises(ap.ApplyError):
        ap.parse_param_file(write_file(tmp_path, text))


def test_enablers_are_written_first():
    items = [('VISO_POS_X', 0.1), ('EK3_SRC1_POSXY', 6), ('VISO_TYPE', 1), ('SIM_GPS1_ENABLE', 0)]
    assert [n for n, _ in ap.order_for_writing(items)] == ['VISO_TYPE', 'SIM_GPS1_ENABLE',
                                                           'VISO_POS_X', 'EK3_SRC1_POSXY']


def test_float32_comparison():
    assert ap.same_value(0.2, ap.as_float32(0.2))
    assert not ap.same_value(0.2, 0.21)
    assert ap.same_value(6, 6.0, ptype=2) and not ap.same_value(6, 7, ptype=2)


# -- the shipped param files ------------------------------------------------------

def _values(name):
    return dict(ap.parse_param_file(PARAMS / name))


def test_extnav_file_sources():
    v = _values("gps_denied_extnav.param")
    assert v['VISO_TYPE'] == 1                                   # MAVLink
    assert (v['EK3_SRC1_POSXY'], v['EK3_SRC1_VELXY'], v['EK3_SRC1_VELZ'], v['EK3_SRC1_YAW']) == (6, 6, 6, 6)
    assert v['EK3_SRC1_POSZ'] == 1                               # baro
    # GPS in no set at all (3 = GPS; yaw 2/3 GPS, 8 GSF)
    for n in (1, 2, 3):
        assert v[f'EK3_SRC{n}_POSXY'] != 3 and v[f'EK3_SRC{n}_VELXY'] != 3
        assert v[f'EK3_SRC{n}_POSZ'] != 3 and v[f'EK3_SRC{n}_VELZ'] != 3
        assert v[f'EK3_SRC{n}_YAW'] not in (2, 3, 8)


def test_flow_fallback_file_sources():
    v = _values("gps_denied_flow_fallback.param")
    assert (v['EK3_SRC2_POSXY'], v['EK3_SRC2_VELXY'], v['EK3_SRC2_POSZ'], v['EK3_SRC2_VELZ'],
            v['EK3_SRC2_YAW']) == (0, 5, 1, 0, 1)
    assert int(v['EK3_SRC_OPTIONS']) & 2 and not int(v['EK3_SRC_OPTIONS']) & 1
    assert 'EK3_SRC1_POSXY' not in v, "layered on the extnav file, must not redo set 1"


@pytest.mark.parametrize("name", ["gps_denied_extnav.param", "gps_denied_flow_fallback.param"])
def test_param_files_parse_the_way_ardupilot_does(name):
    """AP_Param::parse_param_line skips only lines whose FIRST character is '#',
    and reads NAME then VALUE: an indented or trailing comment would be a
    parameter called '#' (or worse). Same for SITL's --defaults."""
    for raw in (PARAMS / name).read_text().splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith('#'):
            assert raw.startswith('#'), f"indented comment: {raw!r}"
        else:
            assert '#' not in raw and len(raw.split()) == 2, raw


# -- applying -----------------------------------------------------------------

def test_apply_writes_only_changes_and_verifies(tmp_path):
    fc = FakeFc({'EK3_SRC1_POSXY': 3, 'VISO_TYPE': 0, 'WP_SPD': 10})
    items = [('EK3_SRC1_POSXY', 6), ('VISO_TYPE', 1), ('WP_SPD', 10)]
    report = ap.apply(fc, items, fc.fetch_all(), log=lambda s: None)
    assert [n for n, _ in fc.writes] == ['VISO_TYPE', 'EK3_SRC1_POSXY']    # enabler first, no-op skipped
    assert sorted(report['verified']) == ['EK3_SRC1_POSXY', 'VISO_TYPE']
    assert report['failed'] == [] and report['missing'] == []


def test_params_hidden_until_enabled_are_written_after_the_enabler():
    fc = FakeFc({'VISO_TYPE': 0}, hidden=['VISO_POS_X'], unhidden_by='VISO_TYPE')
    report = ap.apply(fc, [('VISO_POS_X', 0.12), ('VISO_TYPE', 1)], fc.fetch_all(), log=lambda s: None)
    assert report['missing'] == [] and sorted(report['verified']) == ['VISO_POS_X', 'VISO_TYPE']


def test_unknown_params_are_reported_not_ignored():
    fc = FakeFc({'VISO_TYPE': 0})
    report = ap.apply(fc, [('VISO_TYPE', 1), ('NOT_A_PARAM', 2)], fc.fetch_all(), log=lambda s: None)
    assert report['missing'] == ['NOT_A_PARAM']


def test_readback_mismatch_is_a_failure():
    fc = FakeFc({'WP_SPD': 5}, corrupt=['WP_SPD'])
    report = ap.apply(fc, [('WP_SPD', 10)], fc.fetch_all(), log=lambda s: None)
    assert report['failed'] == [('WP_SPD', 10, 11.0)]


def test_arming_mid_apply_stops_it():
    fc = FakeFc({'A_TYPE': 0, 'B': 0, 'C': 0}, arm_after_writes=1)
    with pytest.raises(ap.ApplyError, match="ARMED"):
        ap.apply(fc, [('A_TYPE', 1), ('B', 1), ('C', 1)], fc.fetch_all(), log=lambda s: None)
    assert len(fc.writes) == 1


def _run_main(monkeypatch, tmp_path, fc, *args):
    monkeypatch.setattr(ap.Fc, 'connect', classmethod(lambda cls, port, baud, timeout=10.0: fc))
    return ap.main(list(args) + ['--port', 'tcp:127.0.0.1:1', '--backup-dir', str(tmp_path / 'bk')])


def test_main_refuses_when_armed_and_writes_nothing(monkeypatch, tmp_path):
    fc = FakeFc({'VISO_TYPE': 0}, arm_after_writes=0)
    p = write_file(tmp_path, "VISO_TYPE 1\n")
    assert _run_main(monkeypatch, tmp_path, fc, str(p), '--yes') == 2
    assert fc.writes == [] and not (tmp_path / 'bk').exists()


def test_main_backs_up_everything_before_writing(monkeypatch, tmp_path):
    fc = FakeFc({'VISO_TYPE': 0, 'WP_SPD': 10, 'EK3_SRC1_POSXY': 3})
    p = write_file(tmp_path, "VISO_TYPE 1\nEK3_SRC1_POSXY 6\n")
    assert _run_main(monkeypatch, tmp_path, fc, str(p), '--yes') == 0
    (backup,) = (tmp_path / 'bk').glob('*.json')
    doc = json.loads(backup.read_text())
    assert doc['params'] == {'EK3_SRC1_POSXY': 3.0, 'VISO_TYPE': 0.0, 'WP_SPD': 10.0}   # the BEFORE values
    assert fc.p['VISO_TYPE']['value'] == 1.0
    # and the backup restores
    assert _run_main(monkeypatch, tmp_path, fc, '--restore', str(backup), '--yes') == 0
    assert fc.p['VISO_TYPE']['value'] == 0.0 and fc.p['EK3_SRC1_POSXY']['value'] == 3.0


def test_main_dry_run_writes_nothing(monkeypatch, tmp_path):
    fc = FakeFc({'VISO_TYPE': 0})
    p = write_file(tmp_path, "VISO_TYPE 1\n")
    assert _run_main(monkeypatch, tmp_path, fc, str(p), '--dry-run') == 0
    assert fc.writes == []


def test_main_asks_before_writing(monkeypatch, tmp_path):
    fc = FakeFc({'VISO_TYPE': 0})
    p = write_file(tmp_path, "VISO_TYPE 1\n")
    monkeypatch.setattr('builtins.input', lambda prompt: 'no')
    assert _run_main(monkeypatch, tmp_path, fc, str(p)) == 1
    assert fc.writes == []


def test_incomplete_backup_stops_everything(monkeypatch, tmp_path):
    fc = FakeFc({'VISO_TYPE': 0})

    def partial(timeout=60.0):
        raise ap.ApplyError("backup incomplete: 900 of 1200 parameters read")
    fc.fetch_all = partial
    p = write_file(tmp_path, "VISO_TYPE 1\n")
    assert _run_main(monkeypatch, tmp_path, fc, str(p), '--yes') == 2
    assert fc.writes == []
