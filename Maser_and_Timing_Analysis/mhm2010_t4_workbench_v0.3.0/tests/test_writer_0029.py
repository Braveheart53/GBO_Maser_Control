# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests of vszh5_writer 0.0.29 (exact local ISO-8601 text) in the workbench.

# %% Header Info
 Title    : vszh5_writer 0.0.29 checks
 Filename : tests/test_writer_0029.py
 Version  : 0.0.1
 Author   : William W. Wallace (NRAO/GBO)
 Created  : 2026-11-10

# %% Revisions
# %%%% 0.0.1: First version (workbench 0.2.4).
#              * timespec="exact" text gives back the identical float epoch
#                (10 000 random epochs, five zones, real Franks MJD values);
#              * mjd_iso_local_strings gives back the identical MJD float
#                (10 000 random MJD, five zones) and states the Franks MJD
#                decimals exactly (61234.10355 -> 22:29:06.72);
#              * the default text ("seconds") equals what 0.0.28 wrote;
#              * a document written with 0.0.29 is object-by-object the same
#                as one written with 0.0.28 (apart from the save stamps);
#              * NaN, whole seconds, invalid timespec, Python 3.8 grammar.

Run:  python -m pytest tests/test_writer_0029.py
The default-text test compares with the 0.0.24-0.0.28 text rebuilt below
(seconds_text_0028); the document test imports the byte-exact master 0.0.28
kept in docs/writer_0.0.29/.
"""
# %% Imports
import ast
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import h5py
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vszh5_writer as w                                   # noqa: E402
from maser_io import load_franks                           # noqa: E402

FRANKS = sorted((ROOT / "examples" / "franks_2026_01_07").glob("*.t00new"))
ZONES = (None, "UTC", "America/New_York", "-05:00", "+05:30")


# %% Helpers
def epoch_from_exact(text: str) -> float:
    """Float epoch from one 'exact' ISO text (inverse of timespec='exact')."""
    body, sign, offset = text[:-6], text[-6], text[-5:]
    assert sign in "+-" and offset[2] == ":", text
    offset_s = (int(offset[:2]) * 3600 + int(offset[3:]) * 60) * (-1 if sign == "-" else 1)
    whole, _, digits = body.partition(".")
    wall = dt.datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S")
    seconds = int((wall - dt.datetime(1970, 1, 1)).total_seconds())     # whole, exact
    value = Decimal(seconds - offset_s) + (Decimal("0." + digits) if digits else Decimal(0))
    return float(value)


def seconds_text_0028(epoch: np.ndarray, zone) -> list:
    """The text iso_local_strings() wrote in 0.0.24-0.0.28 (fraction cut off)."""
    offsets = w._zone_offsets(np.asarray(epoch, float), zone)
    out = []
    for value, offset in zip(epoch, offsets):
        if not np.isfinite(value):
            out.append("")
            continue
        stamp = dt.datetime.fromtimestamp(value + offset, dt.timezone.utc).replace(tzinfo=None)
        out.append(stamp.isoformat(timespec="seconds") + w.iso_offset_text(offset))
    return out


# %% Tests
def test_version_and_header():
    assert w.__version__ == "0.0.29"
    source = (ROOT / "vszh5_writer.py").read_text(encoding="utf-8")
    assert " Version        : 0.0.29 " in source
    assert "# %%%% 0.0.29:" in source and "# %%%% 0.0.28:" in source
    ast.parse(source, feature_version=(3, 8))                  # Python 3.8 grammar
    assert w.ISO_TIMESPECS == ("seconds", "milliseconds", "microseconds", "auto", "exact")


@pytest.mark.parametrize("zone", ZONES)
def test_exact_round_trip_random(zone):
    rng = np.random.default_rng(29)
    # 1990 .. 2100, with fractions down to the last bit of the float
    epoch = rng.uniform(631152000.0, 4102444800.0, 10_000)
    epoch[:50] = np.floor(epoch[:50])                          # whole seconds too
    texts = w.iso_local_strings(epoch, zone, timespec="exact")
    back = np.array([epoch_from_exact(t) for t in texts])
    assert np.array_equal(back, epoch)                          # bit for bit
    assert all("." not in t[:-6] for t in texts[:50])           # whole second: no fraction


def test_exact_round_trip_franks_mjd():
    collection = load_franks(FRANKS)
    series = next(iter(collection.series.values()))
    epoch = w.mjd_to_epoch(series.mjd)
    texts = w.iso_local_strings(epoch, "America/New_York", timespec="exact")
    back = np.array([epoch_from_exact(t) for t in texts])
    assert np.array_equal(back, epoch)
    # the MJD itself comes back through the same conversion
    assert np.array_equal(back / 86400.0 + w.MJD_UNIX_EPOCH, epoch / 86400.0 + w.MJD_UNIX_EPOCH)


def mjd_from_exact(text: str) -> float:
    """MJD float from mjd_iso_local_strings text (Decimal inversion)."""
    body, sign, offset = text[:-6], text[-6], text[-5:]
    offset_s = (int(offset[:2]) * 3600 + int(offset[3:]) * 60) * (-1 if sign == "-" else 1)
    whole, _, digits = body.partition(".")
    wall = dt.datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S")
    seconds = Decimal(int((wall - dt.datetime(1970, 1, 1)).total_seconds()) - offset_s)
    seconds += Decimal("0." + digits) if digits else Decimal(0)
    return float(seconds / Decimal(86400) + Decimal(40587))


@pytest.mark.parametrize("zone", ZONES)
def test_mjd_text_round_trip_random(zone):
    rng = np.random.default_rng(2029)
    mjd = rng.uniform(47892.0, 88069.0, 10_000)                 # 1990 .. 2100
    mjd[:20] = np.round(mjd[:20], 5)                            # Franks-like decimals
    texts = w.mjd_iso_local_strings(mjd, zone)
    assert np.array_equal(np.array([mjd_from_exact(t) for t in texts]), mjd)
    # the offset is the zone's offset at that instant (as local_time_axis)
    axis = w.local_time_axis(w.mjd_to_epoch(mjd), zone)
    assert [t[-6:] for t in texts] == [w.iso_offset_text(o) for o in axis.offsets_s]


def test_mjd_text_franks_states_the_file_decimals():
    collection = load_franks(FRANKS)
    series = next(iter(collection.series.values()))
    texts = w.mjd_iso_local_strings(series.mjd, "America/New_York")
    assert np.array_equal(np.array([mjd_from_exact(t) for t in texts]), series.mjd)
    assert all(len(t[:-6].partition(".")[2]) <= 3 for t in texts)       # k x 0.864 s
    assert w.mjd_iso_local_strings(np.array([61234.10355]), "America/New_York") == \
        ["2026-07-12T22:29:06.72-04:00"]
    assert w.mjd_iso_local_strings(np.array([np.nan, 40587.0]), "UTC") == \
        ["", "1970-01-01T00:00:00+00:00"]


@pytest.mark.parametrize("zone", ZONES)
def test_default_text_unchanged(zone):
    rng = np.random.default_rng(28)
    epoch = rng.uniform(1.7e9, 1.9e9, 2_000)
    epoch[5] = np.nan
    assert w.iso_local_strings(epoch, zone) == seconds_text_0028(epoch, zone)
    assert w.iso_local_strings(epoch, zone, timespec="seconds") == seconds_text_0028(epoch, zone)


def test_dst_change_and_examples():
    # 2026-03-08 07:00:00 UTC is the US spring-forward instant
    epoch = np.array([1772953199.25, 1772953200.0, 1772953200.5, np.nan])
    texts = w.iso_local_strings(epoch, "America/New_York", timespec="exact")
    assert texts == ["2026-03-08T01:59:59.25-05:00", "2026-03-08T03:00:00-04:00",
                     "2026-03-08T03:00:00.5-04:00", ""]
    assert w.iso_local_strings(np.array([0.1]), "UTC", timespec="exact") == ["1970-01-01T00:00:00.1+00:00"]
    assert w.iso_local_strings(np.array([-0.25]), "UTC", timespec="exact") == ["1969-12-31T23:59:59.75+00:00"]
    assert w.iso_local_strings(np.array([1.5]), "UTC", timespec="milliseconds") == ["1970-01-01T00:00:01.500+00:00"]
    assert w.iso_local_strings(np.array([1.9]), "UTC") == ["1970-01-01T00:00:01+00:00"]   # cut, as before


def test_invalid_timespec():
    with pytest.raises(ValueError):
        w.iso_local_strings(np.array([0.0]), "UTC", timespec="nanoseconds")


def _sample_document(module, path: Path) -> None:
    """The same small document through ``module`` (dates, 1-D, text, 2-D, pages)."""
    t = np.linspace(0.0, 100.0, 5000)
    doc = module.VszDoc(generator="writer comparison")
    module.xy_page(doc, "p1", [module.Trace("t", "v", "v")], title="t_title", xlabel="x", ylabel="y")
    axis = module.local_time_axis(module.mjd_to_epoch(61220.0 + t / 86400.0), "America/New_York")
    with module.VeuszWriter(path, compress=True) as writer:
        writer.dataset_1d("t", t, tags=("a",))
        writer.dataset_1d("v", np.sin(t), serr=np.full(t.size, 0.1))
        writer.dataset_date("d", axis.veusz_seconds, tags=("b",))
        writer.dataset_text("s", module.iso_local_strings(module.mjd_to_epoch(61220.0 + t[:20]), "UTC"))
        writer.dataset_2d("img", np.arange(12.0).reshape(3, 4), xrange=(0, 1), yrange=(0, 2))
        writer.finish(doc.text())


def _objects(path: Path) -> dict:
    """Every HDF5 object of a document: values and attributes (save stamps out)."""
    out = {}
    with h5py.File(path, "r") as hdf:
        def visit(name, obj):
            attrs = {k: (v.tobytes() if isinstance(v, np.ndarray) else v)
                     for k, v in obj.attrs.items() if k not in ("vsz_saved_at",) and not k.startswith("vsz_x")
                     and not k.startswith("vsz_y")}
            value = obj[()] if isinstance(obj, h5py.Dataset) else None
            if name == "Veusz/Document/document":
                value = [line for line in value[0].decode().splitlines() if not line.startswith("# Saved at")]
            elif isinstance(value, np.ndarray):
                value = value.tobytes() if value.dtype != object else list(value)
            out[name] = (value, attrs, getattr(obj, "compression", None))
        hdf.visititems(visit)
    return out


def test_document_identical_to_0028(tmp_path):
    # docs/writer_0.0.29/vszh5_writer_0.0.28.py is the RF-project master 0.0.28
    # (SHA-256 13f68513...f634a64b5); make_0029.py turns it into 0.0.29.
    old_path = ROOT / "docs" / "writer_0.0.29" / "vszh5_writer_0.0.28.py"
    if not old_path.is_file():
        pytest.skip("0.0.28 reference copy not shipped")
    import importlib.util
    spec = importlib.util.spec_from_file_location("vszh5_writer_0028", old_path)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old                 # dataclasses look the module up
    spec.loader.exec_module(old)
    assert old.__version__ == "0.0.28"
    _sample_document(old, tmp_path / "old.vszh5")
    _sample_document(w, tmp_path / "new.vszh5")
    assert _objects(tmp_path / "old.vszh5") == _objects(tmp_path / "new.vszh5")
