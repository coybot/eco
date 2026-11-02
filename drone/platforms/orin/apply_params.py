#!/usr/bin/env python3
"""Apply an ArduPilot .param file to a flight controller - safely.

For the operator to run by hand (it never runs on its own):

  1. Connects and REFUSES unless the vehicle is disarmed (checked again
     before every write; it stops the moment the FC reports armed).
  2. Backs up EVERY parameter to JSON first, and stops if the backup is
     incomplete.
  3. Shows what will change, and asks (type APPLY) unless --yes.
  4. Writes only the values that differ, then reads every one back and
     verifies it. Exit status 0 only if all of them verified.

    # the serial port is exclusive: stop the daemon first
    sudo systemctl stop drone-api
    python3 apply_params.py params/gps_denied_extnav.param
    python3 apply_params.py params/gps_denied_flow_fallback.param --dry-run
    python3 apply_params.py --restore backups/fc-params-20261102-120000.json

--port/--baud default to config.yaml's serial_port/baud_rate (as drone_sdk),
then $COYBOT_SDK_SERIAL_PORT; e.g. --port tcp:127.0.0.1:5760 for SITL.

Parameters hidden until their group is enabled (VISO_POS_X until VISO_TYPE
is set, FLOW_* until FLOW_TYPE...) are written after the enabling ones and
read back by name; any the FC still does not know are reported, and the
usual fix is "reboot the FC and run this again". Parameters marked reboot
(VISO_TYPE, FLOW_TYPE, ...) only take effect after a reboot - it says so.

Python 3.8 (the companion's). File format: "NAME VALUE" or "NAME,VALUE" per
line, '#' comments (Mission Planner / MAVProxy / ArduPilot --defaults).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ARMED_FLAG = 128   # MAV_MODE_FLAG_SAFETY_ARMED
# Groups whose other parameters appear only once this one is non-zero
# (AP_PARAM_FLAG_ENABLE in ArduPilot) - written first.
ENABLE_SUFFIXES = ('_TYPE', '_ENABLE')
# Read at boot: changing them needs a reboot to take effect.
REBOOT_PREFIXES = ('VISO_TYPE', 'FLOW_TYPE', 'GPS1_TYPE', 'GPS2_TYPE', 'RNGFND', 'SERIAL',
                   'EK3_IMU_MASK', 'AHRS_EKF_TYPE', 'BATT_MONITOR')

MAV_PARAM_TYPE_REAL32 = 9


class ApplyError(Exception):
    pass


def parse_param_file(path) -> List[Tuple[str, float]]:
    """[(name, value)] in file order. Raises ApplyError on a malformed line
    or a name given twice with different values."""
    out, seen = [], {}
    for n, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.replace(',', ' ').replace('\t', ' ').split()
        if len(parts) < 2:
            raise ApplyError(f"{path}:{n}: expected 'NAME VALUE', got {raw!r}")
        name = parts[0].upper()
        if len(name) > 16:
            raise ApplyError(f"{path}:{n}: {name} is longer than 16 characters (MAVLink param_id)")
        try:
            value = float(parts[1])
        except ValueError:
            raise ApplyError(f"{path}:{n}: {parts[1]!r} is not a number")
        if not math.isfinite(value):
            raise ApplyError(f"{path}:{n}: {name} = {parts[1]} is not finite")
        if name in seen:
            if seen[name] != value:
                raise ApplyError(f"{path}:{n}: {name} given twice ({seen[name]:g} and {value:g})")
            continue
        seen[name] = value
        out.append((name, value))
    return out


def order_for_writing(items: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Enabling parameters (..._TYPE, ..._ENABLE) first, the rest in file order."""
    first = [i for i in items if i[0].endswith(ENABLE_SUFFIXES)]
    return first + [i for i in items if not i[0].endswith(ENABLE_SUFFIXES)]


def as_float32(v: float) -> float:
    """What the FC stores: PARAM_VALUE carries a float32."""
    return struct.unpack('<f', struct.pack('<f', float(v)))[0]


def same_value(a: float, b: float, ptype: Optional[int] = None) -> bool:
    if ptype is not None and ptype < MAV_PARAM_TYPE_REAL32:   # integer types: exact
        return int(round(a)) == int(round(b))
    fa, fb = as_float32(a), as_float32(b)
    return fa == fb or abs(fa - fb) <= 1e-6 * max(1.0, abs(fa), abs(fb))


def needs_reboot(name: str) -> bool:
    return name.startswith(REBOOT_PREFIXES)


class Fc:
    """The flight controller over pymavlink, with only what this script needs."""

    def __init__(self, conn):
        self.m = conn
        self._armed = None
        self._hb_time = 0.0

    @classmethod
    def connect(cls, port: str, baud: int, timeout: float = 10.0) -> 'Fc':
        os.environ.setdefault('MAVLINK20', '1')
        from pymavlink import mavutil
        if port.startswith(('tcp:', 'tcpin:', 'udp:', 'udpin:', 'udpout:')):
            m = mavutil.mavlink_connection(port, source_system=255)
        else:
            if not os.path.exists(port):
                raise ApplyError(f"no flight controller at {port}")
            m = mavutil.mavlink_connection(port, baud=baud, source_system=255)
        fc = cls(m)
        deadline = time.time() + timeout
        while time.time() < deadline:
            hb = fc._recv('HEARTBEAT', 1.0)
            if hb is not None and fc._is_autopilot(hb):
                m.target_system, m.target_component = hb.get_srcSystem(), hb.get_srcComponent()
                fc._armed = bool(hb.base_mode & ARMED_FLAG)
                fc._hb_time = time.time()
                return fc
        raise ApplyError(f"no autopilot heartbeat on {port} within {timeout:.0f}s "
                         f"(is drone-api still holding the port?)")

    @staticmethod
    def _is_autopilot(hb) -> bool:
        # ArduPilot (3) or PX4 (12), the autopilot component - not a GCS/companion/ADS-B.
        return hb.autopilot in (3, 12) and hb.get_srcSystem() != 0 and hb.get_srcComponent() == 1

    def _recv(self, mtype, timeout):
        """recv_match, tolerating this pymavlink build's TypeError on instance fields."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.m.recv_match(blocking=True, timeout=min(0.2, max(0.01, deadline - time.time())))
            except TypeError:
                continue
            if msg is None:
                continue
            t = msg.get_type()
            if t == 'HEARTBEAT' and self._is_autopilot(msg) and \
                    msg.get_srcSystem() == getattr(self.m, 'target_system', msg.get_srcSystem()):
                self._armed = bool(msg.base_mode & ARMED_FLAG)
                self._hb_time = time.time()
            if mtype is None or t == mtype or (isinstance(mtype, tuple) and t in mtype):
                return msg
        return None

    def armed(self, fresh_s: float = 2.5) -> bool:
        """From an autopilot heartbeat (1 Hz) seen within fresh_s, waiting up to
        fresh_s for one. No heartbeat counts as armed: never write blind."""
        deadline = time.time() + fresh_s
        while time.time() - self._hb_time > fresh_s and time.time() < deadline:
            self._recv('HEARTBEAT', 0.3)
        if time.time() - self._hb_time > fresh_s:
            return True
        return self._armed is not False

    def fetch_all(self, timeout: float = 60.0) -> Dict[str, dict]:
        """Every parameter: {name: {'value', 'type', 'index'}}. Raises if incomplete."""
        m = self.m
        params, count = {}, None
        m.mav.param_request_list_send(m.target_system, m.target_component)
        deadline = time.time() + timeout
        last_new = time.time()
        while time.time() < deadline:
            msg = self._recv('PARAM_VALUE', 0.5)
            if msg is not None:
                name = _pname(msg)
                count = msg.param_count
                if name not in params:
                    last_new = time.time()
                params[name] = {'value': float(msg.param_value), 'type': int(msg.param_type),
                                'index': int(msg.param_index)}
                if count and len(params) >= count:
                    break
            elif time.time() - last_new > 3.0 and count:
                # the stream went quiet: ask for the missing indices one by one
                have = {p['index'] for p in params.values()}
                missing = [i for i in range(count) if i not in have]
                if not missing:
                    break
                for i in missing[:50]:
                    m.mav.param_request_read_send(m.target_system, m.target_component, b'', i)
                last_new = time.time()
        if not count or len(params) < count:
            raise ApplyError(f"backup incomplete: {len(params)} of {count or '?'} parameters read")
        return params

    def read(self, name: str, timeout: float = 3.0) -> Optional[dict]:
        m = self.m
        for _ in range(3):
            m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
            deadline = time.time() + timeout / 3
            while time.time() < deadline:
                msg = self._recv('PARAM_VALUE', deadline - time.time())
                if msg is not None and _pname(msg) == name:
                    return {'value': float(msg.param_value), 'type': int(msg.param_type)}
        return None

    def write(self, name: str, value: float, ptype: Optional[int]) -> Optional[dict]:
        """PARAM_SET; returns the FC's PARAM_VALUE echo (the stored value) or None."""
        m = self.m
        for _ in range(3):
            m.mav.param_set_send(m.target_system, m.target_component, name.encode(),
                                 float(value), ptype or MAV_PARAM_TYPE_REAL32)
            deadline = time.time() + 1.5
            while time.time() < deadline:
                msg = self._recv('PARAM_VALUE', deadline - time.time())
                if msg is not None and _pname(msg) == name:
                    return {'value': float(msg.param_value), 'type': int(msg.param_type)}
        return None


def _pname(msg) -> str:
    pid = msg.param_id
    if isinstance(pid, bytes):
        pid = pid.decode('ascii', 'replace')
    return pid.rstrip('\x00')


def backup(fc: Fc, backup_dir: Path, label: str = 'fc-params') -> Path:
    params = fc.fetch_all()
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    doc = {'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
           'target_system': fc.m.target_system, 'count': len(params),
           'params': {k: v['value'] for k, v in sorted(params.items())},
           'types': {k: v['type'] for k, v in sorted(params.items())}}
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    os.replace(str(tmp), str(path))
    return path


def plan(items: List[Tuple[str, float]], current: Dict[str, dict]):
    """(changes, unchanged, unknown): changes = [(name, old or None, new)]."""
    changes, unchanged, unknown = [], [], []
    for name, value in order_for_writing(items):
        cur = current.get(name)
        if cur is None:
            unknown.append(name)
            changes.append((name, None, value))
        elif same_value(cur['value'], value, cur.get('type')):
            unchanged.append(name)
        else:
            changes.append((name, cur['value'], value))
    return changes, unchanged, unknown


def apply(fc: Fc, items: List[Tuple[str, float]], current: Dict[str, dict], log=print) -> dict:
    """Write and verify. Returns {'written', 'verified', 'failed', 'missing'}."""
    changes, _, _ = plan(items, current)
    report = {'written': [], 'verified': [], 'failed': [], 'missing': []}
    for name, old, new in changes:
        if fc.armed():
            raise ApplyError(f"the vehicle ARMED during the apply - stopped before {name}. "
                             f"Written so far: {report['written'] or 'none'}")
        ptype = current.get(name, {}).get('type')
        echo = fc.write(name, new, ptype)
        if echo is None:
            report['missing'].append(name)
            log(f"  {name}: no answer - the FC does not know it (yet)")
            continue
        report['written'].append(name)
        log(f"  {name}: {'(new)' if old is None else f'{old:g}'} -> {echo['value']:g}")
    # read every written value back, by name - not just trusting the echoes
    wanted = dict(items)
    for name, _, _ in changes:
        value = wanted[name]
        if name in report['missing']:
            continue
        got = fc.read(name)
        if got is None:
            report['missing'].append(name)
        elif same_value(got['value'], value, got['type']):
            report['verified'].append(name)
        else:
            report['failed'].append((name, value, got['value']))
    return report


def default_port() -> Tuple[str, int]:
    port, baud = '/dev/ttyACM0', 115200
    for cfg in (HERE.parents[1] / 'common' / 'config.yaml', Path.home() / 'drone-api' / 'config.yaml'):
        if cfg.exists():
            try:
                import yaml
                c = yaml.safe_load(cfg.read_text()) or {}
                port, baud = c.get('serial_port', port), int(c.get('baud_rate', baud))
                break
            except Exception:
                pass
    return os.environ.get('COYBOT_SDK_SERIAL_PORT', port), int(os.environ.get('COYBOT_SDK_BAUD_RATE', baud))


def restore_items(path: Path) -> List[Tuple[str, float]]:
    doc = json.loads(Path(path).read_text())
    return sorted((k, float(v)) for k, v in doc['params'].items())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('param_file', nargs='?', type=Path)
    ap.add_argument('--restore', type=Path, help="apply a backup JSON written by this script instead")
    ap.add_argument('--port')
    ap.add_argument('--baud', type=int)
    ap.add_argument('--backup-dir', type=Path, default=Path.home() / 'drone-api-backups')
    ap.add_argument('--dry-run', action='store_true', help="back up and show the changes; write nothing")
    ap.add_argument('--yes', action='store_true', help="do not ask before writing")
    args = ap.parse_args(argv)
    if bool(args.param_file) == bool(args.restore):
        ap.error("give a .param file or --restore BACKUP.json (one of them)")

    try:
        items = restore_items(args.restore) if args.restore else parse_param_file(args.param_file)
        if not items:
            raise ApplyError("nothing to apply")
        port, baud = default_port()
        port, baud = args.port or port, args.baud or baud
        print(f"Connecting to {port} ({baud})...")
        fc = Fc.connect(port, baud)
        print(f"Autopilot: system {fc.m.target_system}")
        if fc.armed():
            raise ApplyError("REFUSED: the vehicle is ARMED (or its state is unknown). Disarm first.")
        print(f"Disarmed. Backing up every parameter to {args.backup_dir} ...")
        path = backup(fc, args.backup_dir)
        current = json.loads(path.read_text())
        current = {k: {'value': v, 'type': current['types'].get(k)} for k, v in current['params'].items()}
        print(f"Backup: {path} ({len(current)} parameters). Restore with: "
              f"{Path(sys.argv[0]).name} --restore {path}")

        changes, unchanged, unknown = plan(items, current)
        print(f"\n{len(items)} in the file: {len(unchanged)} already set, {len(changes)} to write")
        for name, old, new in changes:
            print(f"  {name:16s} {'(not on FC)' if old is None else f'{old:g}':>12s} -> {new:g}"
                  f"{'   [reboot]' if needs_reboot(name) else ''}")
        if unknown:
            print(f"\nNot on the FC now: {', '.join(unknown)}. They are written after the enabling "
                  f"parameters; any the FC still does not know need a reboot and a second run.")
        if args.dry_run or not changes:
            print("\nDry run: nothing written." if args.dry_run else "\nNothing to change.")
            return 0
        if not args.yes:
            if input("\nType APPLY to write these to the flight controller: ").strip() != 'APPLY':
                print("Not applied.")
                return 1
        print("\nWriting...")
        report = apply(fc, items, current)
    except ApplyError as e:
        print(f"\n{e}")
        return 2

    print(f"\nRead back {len(report['verified'])} of the {len(report['verified']) + len(report['failed']) + len(report['missing'])} "
          f"values written: {'all match' if not report['failed'] and not report['missing'] else 'NOT all match'} "
          f"({len(items) - len(report['verified']) - len(report['failed']) - len(report['missing'])} "
          f"were already set).")
    for name, want, got in report['failed']:
        print(f"  MISMATCH {name}: wanted {want:g}, FC has {got:g}")
    if report['missing']:
        print(f"  Unknown to the FC: {', '.join(report['missing'])} - reboot it and run this again.")
    rb = [n for n in report['written'] if needs_reboot(n)]
    if rb:
        print(f"\nREBOOT the flight controller for {', '.join(rb)} to take effect.")
    return 0 if not report['failed'] and not report['missing'] else 3


if __name__ == '__main__':
    sys.exit(main())
