# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Release 0.2.4 checks: nothing rounded in the remaining export paths.

# %% Header Info
 Title    : Workbench 0.2.4 regression tests
 Filename : tests/test_release_024.py
 Version  : 0.0.2
 Author   : William W. Wallace (NRAO/GBO)
 Created  : 2026-11-10

# %% Revisions
# %%%% 0.0.2: Release 0.3.0: versions checked as 0.2.4 or later.
# %%%% 0.0.1: First version.
#              * T4 recorder (t4_monitor 0.0.4): every number in both CSVs is
#                repr() of the in-memory float; the UTC text gives back the
#                host clock value bit for bit; re-loading keeps the MJD;
#              * <stem>_time_local_ISO_text (maser_export 0.2.4) gives back
#                the MJD dataset bit for bit;
#              * T4 monitoring-log ISO text = the whole seconds the T4 wrote;
#              * the fit-figure box epoch and the "MJD - MJD0" labels carry
#                every digit (maser_plots 0.0.3, time_format 0.0.2);
#              * versions of the workbench, the writer and t4_monitor.

Run:  python -m pytest tests/test_release_024.py
"""
# %% Imports
import csv
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vszh5_writer as w                                          # noqa: E402
from t4_emulator import T4Emulator                                # noqa: E402
from t4_monitor import MonitorConfig, T4Monitor, T4Recorder, UNIX_EPOCH_MJD  # noqa: E402
from t4_protocol import load_schema                               # noqa: E402
from maser_io import load_t4_telemetry                            # noqa: E402
import maser_export                                               # noqa: E402

SCHEMA = load_schema()


# %% Helpers
def epoch_from_iso(text: str) -> float:
    """Float Unix seconds from ISO text 'YYYY-MM-DDTHH:MM:SS[.digits]+HH:MM'."""
    body, sign, offset = text[:-6], text[-6], text[-5:]
    offset_s = (int(offset[:2]) * 3600 + int(offset[3:]) * 60) * (-1 if sign == "-" else 1)
    whole, _, digits = body.partition(".")
    wall = dt.datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S")
    seconds = int((wall - dt.datetime(1970, 1, 1)).total_seconds())
    return float(Decimal(seconds - offset_s) + (Decimal("0." + digits) if digits else Decimal(0)))


def mjd_from_iso(text: str) -> float:
    """UTC MJD float from exact ISO text, inverting (MJD - 40587) x 86400 in Decimal."""
    body, sign, offset = text[:-6], text[-6], text[-5:]
    offset_s = (int(offset[:2]) * 3600 + int(offset[3:]) * 60) * (-1 if sign == "-" else 1)
    whole, _, digits = body.partition(".")
    wall = dt.datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S")
    seconds = Decimal(int((wall - dt.datetime(1970, 1, 1)).total_seconds()) - offset_s)
    seconds += Decimal("0." + digits) if digits else Decimal(0)
    return float(seconds / Decimal(86400) + Decimal(40587))


def rows(path: Path) -> list:
    """CSV rows as text (header first), exactly as written."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


# %% Tests
def test_versions():
    import MHM2010_Correction_App as app
    import t4_monitor
    release = lambda text: tuple(int(part) for part in text.split("."))
    assert release(app.__version__) >= (0, 2, 4) and release(maser_export.__version__) >= (0, 2, 4)
    assert w.__version__ == "0.0.29" and t4_monitor.__version__ == "0.0.4"


def test_recorder_csvs_keep_every_digit(tmp_path):
    with T4Emulator() as emulator:
        host, port = emulator.address
        config = MonitorConfig(host, port, timeout_s=0.2, attempts=3, backoff=1.0, interval_s=0.2)
        monitor = T4Monitor(config)
        recorder = T4Recorder(tmp_path, SCHEMA, config)
        records = []
        for _ in range(4):
            record = monitor.poll_once()
            assert recorder.write(record)
            records.append(record)
    raw = rows(recorder.raw_path)
    header = raw[0]
    good = [r for r in records if r.ok]
    assert len(good) == 4
    for record, row in zip(records, raw[1:]):
        line = dict(zip(header, row))
        assert line["request_mjd"] == repr(record.request_mjd)
        assert line["receive_mjd"] == repr(record.receive_mjd)
        assert line["rtt_ms"] == repr(record.rtt_ms)
        # the UTC text carries the host clock value: same MJD bit for bit
        for kind in ("request", "receive"):
            epoch = epoch_from_iso(line[f"{kind}_utc"])
            assert epoch / 86400.0 + UNIX_EPOCH_MJD == getattr(record, f"{kind}_mjd")
    values = rows(recorder.values_path)
    names = values[0]
    for record, row in zip(good, values[1:]):
        line = dict(zip(names, row))
        assert line["receive_mjd"] == repr(record.receive_mjd)
        texts = row[3:3 + len(record.frame.values)]
        assert texts == ["" if v is None else repr(float(v)) for v in record.frame.values]
        assert any("." in t and len(t.split(".")[1]) > 0 for t in texts)
    reloaded = load_t4_telemetry([recorder.raw_path])
    assert np.array_equal(reloaded.mjd, np.array([r.receive_mjd for r in good]))


def test_local_iso_text_dataset_is_exact(tmp_path):
    rng = np.random.default_rng(24)
    mjd = np.sort(61220.0 + rng.uniform(0.0, 30.0, 500))             # fractions of a second
    path = tmp_path / "t.vszh5"
    with w.VeuszWriter(path) as writer:
        mjd_name, local_name, _ = maser_export._time_datasets(writer, "Selected_Test", "Franks", mjd,
                                                             zone="America/New_York")
        writer.finish(w.VszDoc().text())
    texts = w.read_text_dataset(path, "Selected_Test_time_local_ISO_text")
    with h5py.File(path, "r") as hdf:
        stored = hdf["Veusz/Data"][mjd_name]["data"][()]
    assert np.array_equal(stored, mjd)
    back = np.array([mjd_from_iso(t) for t in texts])
    assert np.array_equal(back, mjd)                                     # the same MJD floats
    assert sum("." in t[:-6] for t in texts) == len(texts)              # fractions kept


def test_t4_log_iso_text_is_the_logged_clock(tmp_path):
    from maser_export import export_t4_only
    from t4_native import load_native_logs
    logs = load_native_logs([ROOT / "examples" / "t4_monitoring_2026_11_05" / "MonitHCB.dat"])
    export_t4_only(tmp_path / "t4", None, logs)
    path = tmp_path / "t4" / "t4_telemetry.vszh5"
    texts = w.read_text_dataset(path, "T4log_MonitHCB_time_local_ISO_text")
    log = logs[0]
    seconds = log.utc[log.clock_valid].astype("datetime64[s]").astype(np.int64)
    assert len(texts) == len(seconds) > 0
    assert all("." not in t[:-6] for t in texts)                  # whole seconds, as the T4 wrote
    assert [int(epoch_from_iso(t)) for t in texts] == seconds.tolist()
    assert np.array_equal(np.array([epoch_from_iso(t) for t in texts]), seconds.astype(float))


def test_box_epoch_and_axis_labels_are_exact():
    from maser_plots import epoch_text, mjd_axis_label
    from time_format import axis_label, mjd_text
    assert epoch_text(61234.10355, "America/New_York") == "2026-07-12T22:29:06.72-04:00 (MJD 61234.10355)"
    assert mjd_axis_label(61204.14695) == "MJD - 61204.14695"            # Franks: as before
    fits_mjd0 = 61349.712345678901
    assert float(mjd_axis_label(fits_mjd0).split(" - ")[1]) == fits_mjd0   # FITS: every digit
    assert float(axis_label("mjd", origin=fits_mjd0).split(" - ")[1]) == fits_mjd0
    assert axis_label("mjd", origin=61222.0) == "MJD - 61222.00000"         # figure style kept
    rng = np.random.default_rng(3)
    for value in rng.uniform(40000.0, 90000.0, 1000):
        assert float(mjd_text(value)) == value
