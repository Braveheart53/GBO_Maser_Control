#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Tests of vszh5_writer.VeuszAppender (growing a .vszh5 in place).

===============================================================================
 Title          : VeuszAppender tests
 Filename       : tests/test_vszh5_appender.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu
 Created        : 2026-10-26
 Modified       : 2026-10-26
 Language       : Python 3.8 through 3.12
 Requires       : numpy, h5py; pytest optional (also runs as a plain script)
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Status         : Internal draft
===============================================================================

What is checked
---------------
* A missing document is created valid (Data, Document, Tags present).
* Appends grow 1-D and date datasets; reserved-but-unwritten rows read NaN.
* A dataset added later is back-filled with NaN to the table length.
* A document written WITHOUT resizable datasets is converted on first append.
* The private /VszAppend bookkeeping survives reopen and is outside /Veusz.
* Optional (VEUSZ_EXE set): the appended document loads and renders in a
  real Veusz program (render_pages, PNG + PDF).

Run:  python tests/test_vszh5_appender.py     or     pytest tests/

REVISION HISTORY
----------------
0.0.1  2026-10-26  First version (vszh5_writer 0.0.18).
===============================================================================
"""

# %% IMPORTS ===================================================================
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # repo root
import vszh5_writer as vw  # noqa: E402


# %% TESTS =====================================================================
def test_create_append_reopen(tmp: Path = None) -> Path:
    """Create, append twice across reopen, add a late column, read back."""
    folder = Path(tmp or tempfile.mkdtemp())
    path = folder / "append_test.vszh5"
    # First "poll": create datasets with three rows.
    with vw.VeuszAppender(path) as app:
        app.create_date("t", vw.epoch_to_veusz([1.7e9, 1.7e9 + 30, 1.7e9 + 60]))
        app.create_1d("v", [1.0, 2.0, 3.0], tags=("raw",))
        app.set_meta("index", '{"v": "v"}')
        doc = vw.VszDoc("test")
        vw.xy_page(doc, "p1", [vw.Trace("t", "v", "v")], title="test", xmode="datetime")
        app.set_document(doc.text())
    # Second "poll" in a NEW handle: two more rows and a column that is new.
    with vw.VeuszAppender(path) as app:
        assert app.length("t") == 3
        app.append("t", vw.epoch_to_veusz([1.7e9 + 90, 1.7e9 + 120]))
        app.append("v", [4.0, 5.0])
        app.create_1d("w", [9.0, 9.5], prefix_nan=3)        # appeared at row 4
        assert app.get_meta("index") == '{"v": "v"}'
        doc = vw.VszDoc("test")
        vw.xy_page(doc, "p1", [vw.Trace("t", "v", "v"), vw.Trace("t", "w", "w")],
                   title="test", xmode="datetime")
        app.set_document(doc.text())
    with h5py.File(path, "r") as hdf:
        v = hdf["Veusz/Data/v/data"][()]
        w = hdf["Veusz/Data/w/data"][()]
        assert v.tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]
        assert np.isnan(w[:3]).all() and w[3:].tolist() == [9.0, 9.5]
        assert hdf["Veusz/Data/t/data"].attrs["vsz_convert_datetime"] == 1
        assert hdf["Veusz/Data/v/data"].maxshape == (None,)
        assert "Tags" in hdf["Veusz/Document"] and "raw" in hdf["Veusz/Document/Tags"]
        assert "VszAppend" in hdf and "VszAppend" not in hdf["Veusz"]
    # Reserved rows read as NaN (a crash between resize and write).
    with vw.VeuszAppender(path) as app:
        app.pad_to("v", 7)
    with h5py.File(path, "r") as hdf:
        assert np.isnan(hdf["Veusz/Data/v/data"][5:]).all()
    assert vw.page_names(path) == ["p1"]
    return path


def test_convert_fixed_layout() -> None:
    """A document written by the plain (non-resizable) writer can be appended."""
    path = Path(tempfile.mkdtemp()) / "fixed.vszh5"
    with vw.VeuszWriter(path) as writer:
        writer.dataset_1d("x", [1.0, 2.0])
        writer.finish("")
    with vw.VeuszAppender(path) as app:
        assert app.append("x", [3.0]) == 3
    with h5py.File(path, "r") as hdf:
        assert hdf["Veusz/Data/x/data"][()].tolist() == [1.0, 2.0, 3.0]
        # h5py returns this attribute as str (it does so for the writer's own
        # files too), so compare the decoded text.
        assert vw._decode(hdf["Veusz/Data/x/data"].attrs["vsz_name"]) == "x"


def test_resizable_writer() -> None:
    """VeuszWriter(resizable=True) writes datasets the appender grows directly."""
    path = Path(tempfile.mkdtemp()) / "resizable.vszh5"
    with vw.VeuszWriter(path, resizable=True) as writer:
        writer.dataset_date("t", [0.0, 1.0])
        writer.dataset_1d("y", [5.0, 6.0])
        writer.finish("")
    with h5py.File(path, "r") as hdf:
        assert hdf["Veusz/Data/y/data"].maxshape == (None,)
        assert hdf["Veusz/Data/t/data"].attrs["vsz_convert_datetime"] == 1


def test_render_in_real_veusz() -> None:
    """Load and render an appended document in a real Veusz (skipped if absent)."""
    exe = os.environ.get("VEUSZ_EXE", "")
    if not exe:
        print("  (VEUSZ_EXE not set - real-Veusz render skipped)")
        return
    path = test_create_append_reopen()
    written = vw.render_pages(path, png_pages="all", veusz_exe=exe, method="program", timeout=180)
    names = sorted(p.name for p in written)
    assert any(n.endswith(".pdf") for n in names), names
    assert any(n.endswith(".png") for n in names), names
    print("  real Veusz rendered:", names)


# %% RUN AS A SCRIPT ===========================================================
if __name__ == "__main__":
    for test in (test_create_append_reopen, test_convert_fixed_layout,
                 test_resizable_writer, test_render_in_real_veusz):
        test()
        print("PASS", test.__name__)
