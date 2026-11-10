# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Title: pytest configuration | Version: 0.0.1 | Author: William W. Wallace | 2026-11-05
# Revision: 0.0.1 View preferences (theme, time display, Veusz path) go to a temporary
#           workbench_view.ini, never beside the program; offscreen Qt.
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MHM_WORKBENCH_VIEW_INI"] = str(Path(tempfile.mkdtemp(prefix="mhm-view-")) / "workbench_view.ini")
