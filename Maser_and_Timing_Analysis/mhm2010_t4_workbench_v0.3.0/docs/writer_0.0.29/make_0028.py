# -*- coding: utf-8 -*-
"""Rebuild vszh5_writer 0.0.28 from the 0.0.27 file with the exact edits of the
RF project master (claude/vszh5_writer.py); the result is checked by SHA-256."""
import hashlib
import sys

src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8", newline="").read()


def rep(old, new, count=1):
    """Replace ``old`` (which must occur exactly ``count`` times) by ``new``."""
    global s
    n = s.count(old)
    assert n == count, (old[:70], n)
    s = s.replace(old, new)


# --- revision block: a 0.0.28 entry above 0.0.27, with its own lists -------
start = s.index("# %%%% 0.0.27: FitsAutoPlot suite")
lists_start = s.index("# %%%%% Function Descriptions", start)
lists_end = s.index("# %%%% 0.0.26:", start)
lists = s[lists_start:lists_end]
lists28 = lists.replace(
    "        local_utc_offsets: Local UTC offset in seconds for every timestamp, DST-aware.\n",
    "        local_utc_offsets: Local UTC offset in seconds for every timestamp, DST-aware.\n"
    "        _hour_offsets: UTC offset (s) at the middle of each UTC hour number in ``hours``.\n", 1)
lists28 = lists28.replace(
    "        COMPRESS_MIN_ELEMENTS: Arrays at least this long are gzip+shuffle compressed when compress=True.\n",
    "        COMPRESS_MIN_ELEMENTS: Arrays at least this long are gzip+shuffle compressed when compress=True.\n"
    "        _VLEN_BYTES: text datasets\n", 1)
lists28 = lists28.replace(
    "        UNIX_TO_VEUSZ_S: -1230768000\n",
    "        UNIX_TO_VEUSZ_S: -1230768000\n"
    "        _HOUR_OFFSET_CACHE: 0.0.28: UTC offset of each whole UTC hour, per zone, kept between calls. A project with thousands of time traces asked the zone database for the sa...\n"
    "        _HOUR_OFFSET_CACHE_MAX: entries (about 114 years of hours)\n", 1)
assert lists28 != lists
entry28 = '''# %%%% 0.0.28: FitsAutoPlot suite speed round (FITS / Franks AutoPlot 0.4.1):
Date: 2026-11-09
#              faster saving, the same file contents. Built on master 0.0.27.
#              * VeuszWriter keeps a set beside the ordered name list: the
#                "written twice" check was a list search per dataset
#                (O(n^2); 9 s of a 40 000-dataset save).
#              * dataset_1d / dataset_date: create_dataset() returns the
#                dataset (one HDF5 lookup less per dataset); text datasets
#                are made in one call with the variable-length bytes type.
#              * local_utc_offsets / _zone_offsets: UTC offsets cached per
#                (zone, UTC hour) between calls (_hour_offsets); the
#                30-day OnePpsDeltas set asked the zone database 183 000
#                times.  The computer's zone is keyed with time.tzname /
#                timezone / altzone, so a TZ change is seen.
#              * block_reduce(): docstring says "mean" is for linear values
#                and "power_mean_db" for dB values (no behaviour change).
#              Checked: every dataset, attribute and the document script of
#              the tack (776 datasets), UWBR Temps (384) and 30-day 1PPS
#              (40 195) projects are identical to 0.0.27's (object-by-object
#              comparison; only the version comment of the script differs);
#              the 30-day save took 14.2 s (was 34.2 s, 2 CPUs).
'''
s = s[:start] + entry28 + lists28 + s[start:]

rep('__version__ = "0.0.27"', '__version__ = "0.0.28"')
rep("COMPRESS_MIN_ELEMENTS = 4096\n",
    "COMPRESS_MIN_ELEMENTS = 4096\n"
    "_VLEN_BYTES = h5py.special_dtype(vlen=bytes) if h5py is not None else None   # text datasets\n")
rep('''    uniq, inverse = np.unique(hours, return_inverse=True)
    table = np.array([
        _dt.datetime.fromtimestamp(h * 3600.0 + 1800.0, _dt.timezone.utc)
        .astimezone().utcoffset().total_seconds()
        for h in uniq
    ])
    out[good] = table[inverse]
    return out
''', '''    uniq, inverse = np.unique(hours, return_inverse=True)
    out[good] = _hour_offsets(uniq, None)[inverse]
    return out


# 0.0.28: UTC offset of each whole UTC hour, per zone, kept between calls.
# A project with thousands of time traces asked the zone database for the
# same hours again for every trace (about 183 000 astimezone() calls for the
# 30-day GBT 1PPS set).  Offsets only change on whole hours.
_HOUR_OFFSET_CACHE: dict = {}
_HOUR_OFFSET_CACHE_MAX = 1_000_000          # entries (about 114 years of hours)


def _hour_offsets(hours: np.ndarray, zone: Any) -> np.ndarray:
    """UTC offset (s) at the middle of each UTC hour number in ``hours``.

    ``zone`` None means this computer's zone.  Results are cached per
    (zone, hour); the cache is cleared when it grows past
    _HOUR_OFFSET_CACHE_MAX entries.  The computer's zone is keyed with its
    current names and offsets, so a change of TZ (time.tzset) is seen.
    """
    if zone is None:
        key_zone = ("local", tuple(time.tzname), time.timezone, time.altzone)
    else:
        key_zone = zone
    try:
        hash(key_zone)
    except TypeError:                         # an unhashable tzinfo: no cache
        key_zone = None
    out = np.empty(len(hours), dtype=float)
    if len(_HOUR_OFFSET_CACHE) > _HOUR_OFFSET_CACHE_MAX:
        _HOUR_OFFSET_CACHE.clear()
    for i, h in enumerate(hours.tolist()):
        key = (key_zone, h) if key_zone is not None else None
        value = _HOUR_OFFSET_CACHE.get(key) if key is not None else None
        if value is None:
            when = _dt.datetime.fromtimestamp(h * 3600.0 + 1800.0, _dt.timezone.utc)
            local = when.astimezone() if zone is None else when.astimezone(zone)
            value = local.utcoffset().total_seconds()
            if key is not None:
                _HOUR_OFFSET_CACHE[key] = value
        out[i] = value
    return out
''')
rep('''        self.names: list[str] = []
        self._finished = False
''', '''        self.names: list[str] = []
        # 0.0.28: set beside the ordered list - "name in list" was O(n) per
        # dataset (O(n^2) per file: 9 s of a 40 000-dataset save).
        self._name_set: set = set()
        self._finished = False
''')
rep('''        if name in self.names:
            raise ValueError(f"dataset {name!r} written twice")
        self.names.append(name)
''', '''        if name in self._name_set:
            raise ValueError(f"dataset {name!r} written twice")
        self.names.append(name)
        self._name_set.add(name)
''')
rep('''        else:
            group[key] = values
            ds = group[key]
''', '''        else:
            # 0.0.28: create_dataset returns the dataset; "group[key] = values;
            # group[key]" made the same contiguous dataset with one more HDF5
            # lookup per dataset (about 25 % of the object-creation time).
            ds = group.create_dataset(key, data=values)
''')
rep('''        g = self._group(name, "text")
        g["data"] = encoded
        g["data"].attrs["vsz_name"] = name.encode("utf-8")
''', '''        g = self._group(name, "text")
        # 0.0.28: the same variable-length bytes dataset as "g['data'] =
        # encoded", without h5py guessing the type element by element (and
        # without the second lookup).
        if encoded:
            ds = g.create_dataset("data", data=np.array(encoded, dtype=object),
                                  dtype=_VLEN_BYTES)
        else:                                  # empty: exactly as before
            ds = g.create_dataset("data", data=encoded)
        ds.attrs["vsz_name"] = name.encode("utf-8")
''')
rep('''    unique, inverse = np.unique(hours, return_inverse=True)
    table = np.array([_dt.datetime.fromtimestamp(h * 3600.0 + 1800.0, _dt.timezone.utc)
                      .astimezone(zone).utcoffset().total_seconds() for h in unique])
    out[good] = table[inverse] if unique.size else out[good]
    return out
''', '''    unique, inverse = np.unique(hours, return_inverse=True)
    if unique.size:
        out[good] = _hour_offsets(unique, zone)[inverse]     # 0.0.28: cached
    return out
''')
rep('''        "mean"               arithmetic mean of the values;
''', '''        "mean"               arithmetic mean of the values - for linear
                             quantities only: averaging dB numbers directly
                             understates the power (0 and -10 dB give -5 dB,
                             the power mean is -2.6 dB); use "power_mean_db"
                             for dB, dBm, dBuV ... values;
''')
open(dst, "w", encoding="utf-8", newline="").write(s)
print(hashlib.sha256(s.encode("utf-8")).hexdigest(), len(s.encode("utf-8")))
