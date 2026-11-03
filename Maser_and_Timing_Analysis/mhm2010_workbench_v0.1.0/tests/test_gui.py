#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: GUI integration tests | Version: 0.1.0 | Python: 3.12
# Author: William W. Wallace | Created/Modified: 2026-11-03
# Revision: 0.1.0 Offscreen Qt selection, telemetry, safety and settings tests.
# =============================================================================
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import pytest
from MHM2010_Correction_App import MainWindow, QtWidgets
from maser_io import TimingSeries, TimingCollection, Telemetry


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app):
    window = MainWindow()
    days = np.linspace(61000, 61030, 1000)
    phase = (20+2*(days-61015)+.02*(days-61015)**2)*1e-9
    series = TimingSeries(days, phase, "MicroSem -> CNS_GPS1", "FITS")
    collection = TimingCollection({series.label: series}, [])
    window.accept_timing(collection)
    frame = pd.DataFrame({"MJD": days, "IFAmp_0": np.ones(1000)*4.8,
                          "ExtSynth_32": np.r_[np.ones(500)*5751.677, np.ones(500)*5751.678]})
    window.accept_hmd(Telemetry(frame, [], []))
    yield window
    window.close()


def test_controls_and_both_units(window):
    window.dds.setText("5751.6775490")
    window.drift.setText("975")
    assert window.candidate is not None
    text = window.results.toPlainText()
    assert "DDS units/day" in text
    assert "parts in 1e18/day" in text
    assert "NOT REVIEWED" in text
    assert "DDS/selector events" in text


def test_settings_roundtrip(window):
    window.dds.setText("5751.6775490")
    window.drift.setText("975")
    settings = window.settings()
    window.load_settings(settings)
    assert window.settings() == settings
    assert not window.confirm.isChecked()


def test_selection_invalidates_stability(window):
    window.calculate_stability()
    assert window.stability_result is not None
    window.region.setRegion((61000, 61010))
    window.refit()
    assert window.stability_result is None
    assert window.fit.used.sum() < 1000


def test_historical_dds_not_current(window):
    window.apply_epoch.setValue(61030)
    window.nearest_dds()
    assert window.dds.text() == "5751.6780000"
    assert not window.confirm.isChecked()


def test_setting_change_clears_confirmation(window):
    window.dds.setText("5751.6775490")
    window.drift.setText("975")
    window.confirm.setChecked(True)
    window.dds.setText("5751.6775491")
    assert not window.confirm.isChecked()


def test_stability_option_change_invalidates(window):
    window.calculate_stability()
    assert window.stability_result is not None
    window.detrend.setCurrentIndex(2)
    assert window.stability_result is None


def test_fit_change_clears_state_review(window):
    window.state_review.setChecked(True)
    window.region.setRegion((61001, 61029))
    assert not window.state_review.isChecked()
