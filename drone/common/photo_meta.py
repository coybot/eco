"""photo_meta.py - stamp a survey photo with where and when it was taken.

A crop survey is only useful if each image can be placed on the ground and in
time. cv2.imwrite stores no metadata at all, so the JPEGs the aircraft uploaded
carried neither a position nor a capture time (only the second-resolution
upload time in the S3 key).

This writes a standard EXIF APP1 segment - readable by Photos, Preview, QGIS,
exiftool, Pillow and the iOS ImageIO framework - holding:

  * DateTimeOriginal (+ sub-second, UTC offset) = when the frame was grabbed
  * GPS latitude / longitude / altitude (above sea level) / date+time (UTC)
  * GPSImgDirection = the aircraft's heading, true north. A multirotor yaws
    toward its direction of travel, so downward images are rotated by a
    different angle each shot; analysis needs the angle to put them together.
  * ImageDescription = a short human-readable line (mission, cell, altitude
    above home), since EXIF has no field for height above the take-off point.

No dependency on Pillow or piexif: neither is guaranteed on a JetPack Python
3.8 companion, and a JPEG with an APP1 segment spliced in after SOI is all
this needs. Pure functions throughout so the layout is unit-tested against
Pillow's own reader.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# TIFF field types
_BYTE, _ASCII, _SHORT, _LONG, _RATIONAL = 1, 2, 3, 4, 5

_SOFTWARE = "coybot"


def _ascii(text: str) -> bytes:
    return text.encode("ascii", "replace") + b"\x00"


def _rational(value: float, scale: int = 1000000) -> bytes:
    """One unsigned RATIONAL (numerator, denominator) for a non-negative value."""
    return struct.pack("<II", int(round(abs(value) * scale)), scale)


def _dms(deg: float) -> bytes:
    """Three RATIONALs: degrees, minutes, seconds of a non-negative angle."""
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = (deg - d - m / 60.0) * 3600.0
    return struct.pack("<II", d, 1) + struct.pack("<II", m, 1) + _rational(s, 100000)


# An IFD entry: (tag, type, count, value bytes)
_Entry = Tuple[int, int, int, bytes]


def _build_ifd(entries: List[_Entry], ifd_offset: int) -> bytes:
    """Serialise one IFD that will sit at `ifd_offset` in the TIFF block.

    Values of 4 bytes or fewer are stored in the entry itself; larger values go
    in a data area right after the entry table, referenced by offset.
    """
    entries = sorted(entries, key=lambda e: e[0])
    table_len = 2 + 12 * len(entries) + 4
    data_at = ifd_offset + table_len
    table = struct.pack("<H", len(entries))
    data = b""
    for tag, typ, count, value in entries:
        if len(value) <= 4:
            table += struct.pack("<HHI", tag, typ, count) + value.ljust(4, b"\x00")
        else:
            table += struct.pack("<HHII", tag, typ, count, data_at + len(data))
            data += value
            if len(data) % 2:
                data += b"\x00"
    return table + struct.pack("<I", 0) + data


def build_exif(
    when: float,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    alt_msl_m: Optional[float] = None,
    heading_deg: Optional[float] = None,
    description: Optional[str] = None,
) -> bytes:
    """The TIFF block of an EXIF segment (without the JPEG APP1 wrapper).

    `when` is Unix time. GPS fields are written only for what is known: a
    missing fix leaves the GPS IFD out entirely rather than writing 0,0, which
    is a real place (and an obviously wrong one for a farm).
    """
    utc = datetime.fromtimestamp(when, timezone.utc)
    stamp = utc.strftime("%Y:%m:%d %H:%M:%S")
    subsec = "%03d" % int(utc.microsecond / 1000)

    ifd0: List[_Entry] = [
        (0x0131, _ASCII, len(_ascii(_SOFTWARE)), _ascii(_SOFTWARE)),
        (0x0132, _ASCII, 20, _ascii(stamp)),
    ]
    if description:
        d = _ascii(description)
        ifd0.append((0x010E, _ASCII, len(d), d))
    exif_ifd: List[_Entry] = [
        (0x9003, _ASCII, 20, _ascii(stamp)),
        (0x9004, _ASCII, 20, _ascii(stamp)),
        (0x9011, _ASCII, 7, _ascii("+00:00")),
        (0x9291, _ASCII, len(_ascii(subsec)), _ascii(subsec)),
    ]

    gps: List[_Entry] = []
    if lat is not None and lon is not None:
        gps += [
            (0x0000, _BYTE, 4, bytes([2, 3, 0, 0])),
            (0x0001, _ASCII, 2, _ascii("N" if lat >= 0 else "S")),
            (0x0002, _RATIONAL, 3, _dms(lat)),
            (0x0003, _ASCII, 2, _ascii("E" if lon >= 0 else "W")),
            (0x0004, _RATIONAL, 3, _dms(lon)),
            (0x0007, _RATIONAL, 3,
             struct.pack("<II", utc.hour, 1) + struct.pack("<II", utc.minute, 1)
             + _rational(utc.second + utc.microsecond / 1e6, 1000)),
            (0x0012, _ASCII, 7, _ascii("WGS-84")),
            (0x001D, _ASCII, 11, _ascii(utc.strftime("%Y:%m:%d"))),
        ]
        if alt_msl_m is not None:
            gps += [(0x0005, _BYTE, 1, bytes([0 if alt_msl_m >= 0 else 1])),
                    (0x0006, _RATIONAL, 1, _rational(alt_msl_m, 1000))]
        if heading_deg is not None:
            gps += [(0x0010, _ASCII, 2, _ascii("T")),
                    (0x0011, _RATIONAL, 1, _rational(heading_deg % 360.0, 100))]

    # Layout: header(8) | IFD0 | Exif IFD | GPS IFD. Pointers need the sizes,
    # so size each IFD with a placeholder pointer first (pointer values are LONG
    # and inline, so they do not change an IFD's size).
    ifd0_ptrs = [(0x8769, _LONG, 1, struct.pack("<I", 0))]
    if gps:
        ifd0_ptrs.append((0x8825, _LONG, 1, struct.pack("<I", 0)))
    ifd0_len = len(_build_ifd(ifd0 + ifd0_ptrs, 8))
    exif_off = 8 + ifd0_len
    exif_len = len(_build_ifd(exif_ifd, exif_off))
    gps_off = exif_off + exif_len

    ptrs = [(0x8769, _LONG, 1, struct.pack("<I", exif_off))]
    if gps:
        ptrs.append((0x8825, _LONG, 1, struct.pack("<I", gps_off)))
    out = b"II" + struct.pack("<HI", 42, 8)
    out += _build_ifd(ifd0 + ptrs, 8)
    out += _build_ifd(exif_ifd, exif_off)
    if gps:
        out += _build_ifd(gps, gps_off)
    return out


def insert_exif(jpeg: bytes, tiff: bytes) -> bytes:
    """`jpeg` with an Exif APP1 segment right after SOI (any existing Exif
    segment is dropped, so re-stamping a file replaces rather than stacks)."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    payload = b"Exif\x00\x00" + tiff
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("EXIF block too large for one APP1 segment")
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    rest = jpeg[2:]
    # Skip an existing Exif APP1 (and only that) at the head.
    while rest[:2] == b"\xff\xe1":
        length = struct.unpack(">H", rest[2:4])[0]
        if rest[4:10] != b"Exif\x00\x00":
            break
        rest = rest[2 + length:]
    return b"\xff\xd8" + seg + rest


def stamp_jpeg_file(path: str, when: float, **fields: Any) -> None:
    """Rewrite the JPEG at `path` with EXIF for `fields` (see build_exif)."""
    with open(path, "rb") as f:
        data = f.read()
    stamped = insert_exif(data, build_exif(when, **fields))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(stamped)
    import os
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

MANIFEST_COLUMNS = [
    "n", "file", "url", "utc_time", "unix_time", "lat", "lon", "alt_msl_m",
    "alt_above_home_m", "heading_deg", "ground_speed_mps", "cell", "cell_forward_m",
    "cell_right_m", "gps",
]


def utc_iso(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def manifest_csv(rows: List[Dict[str, Any]]) -> str:
    """CSV text of per-photo rows (keys per MANIFEST_COLUMNS; missing = blank)."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(MANIFEST_COLUMNS)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in MANIFEST_COLUMNS])
    return buf.getvalue()


def survey_row(n: int, url: str, cell: int, cell_forward_m: float, cell_right_m: float,
               meta: Optional[Dict[str, Any]], now: float) -> Dict[str, Any]:
    """One manifest row for a survey photo.

    `meta` is drone_sdk.last_photo_meta() - {'when': unix time, 'fix': dict or None} - or
    None for an SDK that reports no position at all (sim shims, test fakes); `now` stands
    in for the capture time then. The gps column says which: 'yes', 'no' (this SDK reports
    position and had no fix) or '' (it does not report position)."""
    when = (meta or {}).get("when") or now
    fix = (meta or {}).get("fix")
    clean = url.split("?")[0]
    row: Dict[str, Any] = {
        "n": n, "file": clean.rsplit("/", 1)[-1], "url": clean,
        "utc_time": utc_iso(when), "unix_time": round(when, 3),
        "cell": cell, "cell_forward_m": round(cell_forward_m, 2), "cell_right_m": round(cell_right_m, 2),
        "gps": "yes" if fix else ("no" if meta is not None else ""),
    }
    if fix:
        h = fix.get("heading_deg")
        row.update({
            "lat": round(fix["lat"], 7), "lon": round(fix["lon"], 7),
            "alt_msl_m": round(fix["alt_msl_m"], 2),
            "alt_above_home_m": round(fix["alt_above_home_m"], 2),
            "heading_deg": None if h is None else round(h, 1),
            "ground_speed_mps": round(fix["ground_speed_mps"], 2),
        })
    return row
