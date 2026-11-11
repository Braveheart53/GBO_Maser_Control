# -*- coding: utf-8 -*-
"""vszh5_writer 0.0.29 from master 0.0.28 (MHM2010 / T4 Maser Timing Workbench
0.2.4): exact local ISO-8601 text.  Additions only; every 0.0.28 call writes
the same file.  Usage: python make_0029.py <0.0.28 file> <0.0.29 file>"""
import hashlib
import sys

src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8", newline="").read()
assert hashlib.sha256(s.encode()).hexdigest().startswith("13f68513ff47e93d"), "not master 0.0.28"


def rep(old, new, count=1):
    """Replace ``old`` (exactly ``count`` occurrences) by ``new``."""
    global s
    n = s.count(old)
    assert n == count, (old[:70], n)
    s = s.replace(old, new)


# --- header -----------------------------------------------------------------
rep(" Version        : 0.0.27 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)",
    " Version        : 0.0.29 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)")
rep(" Modified       : 2026-11-09\n", " Modified       : 2026-11-10\n")
rep("""                  settings checked in the 3.6.2 source; 0.0.27 gap rule:
                  4.2.1 with real GBT sampler2log files)
""", """                  settings checked in the 3.6.2 source; 0.0.27 gap rule:
                  4.2.1 with real GBT sampler2log files; 0.0.28 faster
                  save: files identical to 0.0.27; 0.0.29 exact ISO text:
                  3.6.2, every other output identical to 0.0.28)
""")
rep("""                  (FitsAutoPlot, FITS AutoPlot 0.4.0) the small/large gap
                  rule and exact DST change instants. One master copy: the
""", """                  (FitsAutoPlot, FITS AutoPlot 0.4.0) the small/large gap
                  rule and exact DST change instants; 0.0.28 (FITS / Franks
                  AutoPlot 0.4.1) saves faster with identical files; 0.0.29
                  (MHM2010 / T4 Maser Timing Workbench 0.2.4) writes local
                  ISO-8601 text without dropping digits on request
                  (iso_local_strings timespec="exact"). One master copy: the
""")
rep("""  fraction of the gap); local_time_axis names the minute a DST change
  happens.
""", """  fraction of the gap); local_time_axis names the minute a DST change
  happens.
* (0.0.28) Faster saving with identical files: a name set beside the name
  list, one create_dataset() per dataset, text in one call, UTC offsets
  cached per (zone, UTC hour).
* (0.0.29) iso_local_strings(timespec=): "seconds" (default, as before:
  fractions of a second are cut off), "milliseconds", "microseconds",
  "auto" (Python's isoformat) or "exact" - every digit of the shortest
  decimal that gives back the same float epoch (Python repr), so the text
  is never rounded or cut; ISO_TIMESPECS lists them. mjd_iso_local_strings:
  the same exact text straight from UTC MJD values, converted in decimal
  arithmetic (MJD 61234.10355 -> 22:29:06.72, not 06.7199999...).
""")

# --- revision entry 0.0.29 (lists as 0.0.28's, plus the new names) ----------
start = s.index("# %%%% 0.0.28: FitsAutoPlot suite speed round")
lists_start = s.index("# %%%%% Function Descriptions", start)
lists_end = s.index("# %%%% 0.0.27: FitsAutoPlot suite", start)
lists = s[lists_start:lists_end]
lists29 = lists.replace(
    "        iso_local_strings: ISO-8601 local date-times with offsets, e.g.\n",
    "        iso_local_strings: ISO-8601 local date-times with offsets, e.g.\n"
    "        _exact_wall_text: (0.0.29) ISO text (no offset suffix) of an exact wall-clock time in Unix-style seconds (Decimal).\n"
    "        mjd_iso_local_strings: (0.0.29) ISO-8601 local date-times of UTC MJD values, every digit kept.\n", 1)
lists29 = lists29.replace(
    "        ISO_DATETIME_FORMAT: Veusz tick format of an ISO-8601 date-time without offset (Veusz %VD codes).\n",
    "        ISO_DATETIME_FORMAT: Veusz tick format of an ISO-8601 date-time without offset (Veusz %VD codes).\n"
    "        ISO_TIMESPECS: (0.0.29) timespec values of iso_local_strings(): seconds (default), milliseconds, microseconds, auto, exact.\n"
    "        _EXACT: (0.0.29) Decimal context (80 digits) of the exact ISO text.\n", 1)
lists29 = lists29.replace(
    "    environments: REQUIREMENTS.md (mamba: environment-py38.yml,\n"
    "    environment-py312.yml).  Release notes: RELEASE_suite-2026-11-09.md.\n",
    "    environments: REQUIREMENTS.md of FitsAutoPlot (mamba:\n"
    "    environment-py38.yml, environment-py312.yml).  Release notes: RF project\n"
    "    claude/vszh5-writer-0.0.29-notes.md (workbench docs/vszh5_writer_0.0.29_NOTES.md).\n", 1)
assert lists29.count("_exact_wall_text") == 1 and lists29.count("ISO_TIMESPECS") == 1
assert lists29.count("mjd_iso_local_strings") == 1 and lists29.count("_EXACT:") == 1
assert "claude/vszh5-writer-0.0.29-notes.md" in lists29
entry29 = '''# %%%% 0.0.29: MHM2010 / T4 Maser Timing Workbench 0.2.4 ("in all exports,
Date: 2026-11-10
#              all floats or numbers should never be rounded or trimmed"),
#              built on master 0.0.28 (SHA-256 13f68513...f634a64b5).
#              Additions only: every 0.0.28 call writes the same file.
#              * NEW iso_local_strings(epoch_s, zone=None,
#                timespec="seconds"): "seconds" keeps the 0.0.24-0.0.28
#                text (datetime.isoformat cuts the fraction off, so
#                12:00:00.9 reads 12:00:00); "milliseconds",
#                "microseconds" and "auto" are isoformat's; "exact" writes
#                every digit of the shortest decimal that gives back the
#                same float epoch (Python repr, e.g.
#                2026-07-12T22:29:06.1234567-04:00), so the epoch can be
#                recovered bit for bit from the text. A float64 epoch near
#                2026 resolves 0.24 us and an MJD near 61220 0.63 us, below
#                the 1 us of datetime, so "microseconds" would still round.
#                ValueError for any other timespec; ISO_TIMESPECS lists
#                the accepted values.
#              * NEW mjd_iso_local_strings(mjd, zone=None): exact local ISO
#                text of UTC MJD values. The MJD's shortest decimal (repr)
#                becomes Unix seconds in decimal arithmetic, (MJD - 40587)
#                x 86400, so the text states the time the MJD states (MJD
#                61234.10355 -> 22:29:06.72-04:00; through the float epoch
#                06.7199999...); the zone offset is taken at that instant;
#                Decimal inversion gives the identical MJD float back.
#              * Header: the "Version :" line said 0.0.27 in 0.0.28 (only
#                __version__ was raised); now 0.0.29. Lineage, Status and
#                DIFFERENCES name 0.0.28 and 0.0.29.
#              Checked: tests/test_writer_0029.py (workbench): round trip
#              of 10 000 random epochs and of 10 000 random and the real
#              Franks MJD values to the identical float (and the Franks
#              text equals the decimal MJD the file states); the default
#              text equals 0.0.28's;
#              DST zones and NaN; Python 3.8 grammar; every other function
#              unchanged (diff limited to the ISO text and the header).
'''
s = s[:start] + entry29 + lists29 + s[start:]

# --- code --------------------------------------------------------------------
rep("from decimal import Decimal\n", "from decimal import Context, Decimal, ROUND_FLOOR\n")
rep('__version__ = "0.0.28"', '__version__ = "0.0.29"')
rep('''    "with_x", "time_pair_pages",
''', '''    "with_x", "time_pair_pages", "ISO_TIMESPECS", "mjd_iso_local_strings",
''')
rep('''ISO_DATETIME_FORMAT = "%VDY-%VDm-%VDdT%VDH:%VDM:%VDS"
''', '''ISO_DATETIME_FORMAT = "%VDY-%VDm-%VDdT%VDH:%VDM:%VDS"
#: (0.0.29) timespec values of iso_local_strings(): seconds (default),
#: milliseconds, microseconds, auto, exact.
ISO_TIMESPECS = ("seconds", "milliseconds", "microseconds", "auto", "exact")
# (0.0.29) Decimal arithmetic of the exact ISO text: 80 digits, far above the
# 17 + 5 digits an MJD or epoch times 86400 can need, so nothing is rounded.
_EXACT = Context(prec=80)
_MJD_UNIX_EPOCH_DECIMAL = Decimal("40587")
_SECONDS_PER_DAY_DECIMAL = Decimal("86400")
''')
rep('''def iso_local_strings(epoch_s: Any, zone: Any = None) -> list[str]:
    """ISO-8601 local date-times with offsets, e.g. "2026-11-03T06:31:15-05:00"."""
    epoch = _one_dimensional(epoch_s, "epoch_s")
    offsets = _zone_offsets(epoch, zone)
    out = []
    for value, offset in zip(epoch, offsets):
        if not np.isfinite(value):
            out.append("")
            continue
        stamp = _dt.datetime.fromtimestamp(value + offset, _dt.timezone.utc).replace(tzinfo=None)
        out.append(stamp.isoformat(timespec="seconds") + iso_offset_text(offset))
    return out
''', '''def iso_local_strings(epoch_s: Any, zone: Any = None, timespec: str = "seconds") -> list[str]:
    """ISO-8601 local date-times with offsets, e.g. "2026-11-03T06:31:15-05:00".

    Parameters
    ----------
    epoch_s:
        Unix epoch seconds (UTC), 1-D; NaN gives "".
    zone:
        As local_time_axis() (None = VSZH5_DISPLAY_TZ, else this computer).
    timespec:
        (0.0.29) "seconds" (default, the text of 0.0.24-0.0.28: the fraction
        of a second is cut off, not rounded), "milliseconds",
        "microseconds" or "auto" (datetime.isoformat; the epoch is first
        rounded to whole microseconds), or "exact": every digit of the
        shortest decimal that gives back the same float epoch (Python
        repr), e.g. "2026-07-12T22:29:06.1234567-04:00" - nothing rounded
        or cut, float(text seconds) recovers the value bit for bit. A whole
        second has no fraction in "exact" text.
    """
    if timespec not in ISO_TIMESPECS:
        raise ValueError(f"timespec must be one of {ISO_TIMESPECS}, not {timespec!r}")
    epoch = _one_dimensional(epoch_s, "epoch_s")
    offsets = _zone_offsets(epoch, zone)
    out = []
    for value, offset in zip(epoch, offsets):
        if not np.isfinite(value):
            out.append("")
            continue
        if timespec == "exact":
            wall = _EXACT.add(Decimal(repr(float(value))), Decimal(repr(float(offset))))
            out.append(_exact_wall_text(wall) + iso_offset_text(offset))
            continue
        stamp = _dt.datetime.fromtimestamp(value + offset, _dt.timezone.utc).replace(tzinfo=None)
        out.append(stamp.isoformat(timespec=timespec) + iso_offset_text(offset))
    return out


def _exact_wall_text(wall: Decimal) -> str:
    """(0.0.29) ISO text (no offset suffix) of an exact wall-clock time in
    Unix-style seconds (Decimal): whole seconds through datetime, then every
    digit of the fraction - nothing rounded, trailing zeros dropped.
    """
    whole = wall.to_integral_value(rounding=ROUND_FLOOR)
    fraction = _EXACT.subtract(wall, whole)         # 0 <= fraction < 1, exact
    stamp = _dt.datetime(1970, 1, 1) + _dt.timedelta(seconds=int(whole))
    text = stamp.isoformat(timespec="seconds")
    if fraction:
        digits = format(fraction, "f").partition(".")[2].rstrip("0")
        if digits:
            text += "." + digits
    return text


def mjd_iso_local_strings(mjd: Any, zone: Any = None) -> list[str]:
    """(0.0.29) ISO-8601 local date-times of UTC MJD values, every digit kept.

    The shortest round-trip decimal of each MJD (repr) is turned into Unix
    seconds in decimal arithmetic, (MJD - 40587) x 86400, with no binary
    rounding, so the text states the time the MJD states: MJD 61234.10355
    gives "2026-07-12T22:29:06.72-04:00" (America/New_York), where
    iso_local_strings(mjd_to_epoch(mjd), timespec="exact") gives the digits
    of the float epoch, "...06.7199999...", and the default text cuts it to
    "...06-04:00". The offset is the zone's offset at that instant (zone as
    local_time_axis()). Inverting the formula in Decimal arithmetic gives the
    identical MJD float back. NaN -> "".
    """
    values = _one_dimensional(mjd, "mjd")
    if zone is None:
        zone = display_zone()
    if isinstance(zone, str):
        zone = _zone_from_text(zone)
    seconds, hours = [], []
    for value in values.tolist():
        if not math.isfinite(value):
            seconds.append(None)
            continue
        epoch = _EXACT.multiply(_EXACT.subtract(Decimal(repr(value)), _MJD_UNIX_EPOCH_DECIMAL),
                                _SECONDS_PER_DAY_DECIMAL)
        seconds.append(epoch)
        hours.append(float(_EXACT.divide(epoch, 3600).to_integral_value(rounding=ROUND_FLOOR)))
    offsets = iter(_hour_offsets(np.array(hours, dtype=float), zone).tolist()) if hours else iter(())
    out = []
    for epoch in seconds:
        if epoch is None:
            out.append("")
            continue
        offset = next(offsets)
        out.append(_exact_wall_text(_EXACT.add(epoch, Decimal(repr(float(offset)))))
                   + iso_offset_text(offset))
    return out
''')
open(dst, "w", encoding="utf-8", newline="").write(s)
print(hashlib.sha256(s.encode("utf-8")).hexdigest(), len(s.encode("utf-8")))
