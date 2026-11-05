#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: GUI tests for T4 mode, reference tab, theme and live panel | Version: 0.0.1
# Author: William W. Wallace | Created/Modified: 2026-11-03 | Python: 3.12
# Revision: 0.0.1 Offscreen Qt: maser switch, SETFSREL candidate, reference
#           figures, light/dark menu, settings round trip, T4 file view,
#           live emulator polling, read-only state read-back, RAM flush.
# =============================================================================
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest
from MHM2010_Correction_App import MainWindow, QtWidgets
from maser_io import TimingSeries, TimingCollection


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_for(app, condition, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def window(app):
    window = MainWindow()
    days = np.r_[np.linspace(0, 12, 300), np.linspace(13, 30, 400)]       # one 1-day gap
    phase = (-357.49 - 2.719 * days) * 1e-9
    series = TimingSeries(61316.64903 + days, phase, "MSM-GPS", "Franks")
    window.accept_timing(TimingCollection({"MSM-GPS": series}, []))
    yield window
    window.t4_widget.shutdown()
    window.close()


def test_franks_bridges_and_reference_tab(window):
    assert window.bridge.isChecked()
    window.run_choice.setCurrentIndex(window.run_choice.count() - 1)      # "All data"
    assert window.fit is not None and window.fit.used.sum() == 700
    ref = window.reference
    assert ref is not None and ref.title == "MicroSemi Maser 1PPS Deltas"
    assert ref.fit.a1 == pytest.approx(-2.719, abs=1e-9)
    assert window.reference_widget.fit_box.toPlainText().startswith("num points = 700")
    assert "counts" in ref.synth_line


def test_t4_mode_candidate_and_rows(window):
    window.run_choice.setCurrentIndex(window.run_choice.count() - 1)
    window.maser.setCurrentIndex(1)
    assert not window.state_form.isRowVisible(window.dds)
    assert window.state_form.isRowVisible(window.t4_synth)
    assert window.candidate is None                                       # not designated yet
    window.t4_designated.setChecked(True)
    window.t4_synth.setText("405750.927598720")
    assert window.candidate["setfsrel_command"].startswith("SETFSREL=+")
    assert "RDFS should read" in window.results.toPlainText()
    assert window.reference.title == "T4 Maser 1PPS Deltas" and "SETFSREL" in window.reference.synth_line
    settings = window.settings()
    window.load_settings(settings)
    assert window.settings() == settings


def test_theme_menu(window):
    window.apply_theme("light")
    assert "#f4f6f8" in window.styleSheet() and window.theme_actions["light"].isChecked()
    window.apply_theme("dark")
    assert window.theme_actions["dark"].isChecked()


def test_t4_live_poll_state_and_flush(window, app, tmp_path):
    panel = window.t4_widget
    panel.output_dir.setText(str(tmp_path))
    panel.interval.setValue(0.5)
    panel.start()
    assert wait_for(app, lambda: panel.counts["ok"] >= 2)
    panel.refresh(force=True)
    assert panel.table.item(40, 2).text() == "1"                          # main PLL lock bit
    panel.stop()
    assert wait_for(app, lambda: panel.thread is None)
    assert (panel.recorder.folder / "t4_raw_frames.csv").exists()
    panel.flush_ram()
    assert len(panel.recorder.preview_mjd) == 0
    window.maser.setCurrentIndex(1)
    panel.read_state()
    assert wait_for(app, lambda: window.t4_synth.text() == "405750.927598720")
    assert window.t4_driftcomp.text() == "+0.0e+00"
