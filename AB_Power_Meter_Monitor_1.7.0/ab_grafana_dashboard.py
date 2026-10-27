#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace <wwallace@nrao.edu>
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Build (and optionally upload) the Grafana dashboard for the AB power meters.

===============================================================================
 Title          : AB Power Meter Grafana Dashboard Builder
 Filename       : ab_grafana_dashboard.py
 Version        : 0.0.1 (Semantic Versioning 2.0.0, MAJOR.MINOR.PATCH)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Email          : wwallace@nrao.edu   (alt: naval.antennas@gmail.com)
 Created        : 2026-10-27
 Modified       : 2026-10-27
 Language       : Python 3.7.11+ and 3.12 (standard library only, except
                  --from-poll / --from-exporter, which use the exporter and
                  prometheus_client)
 Requires       : ab_prometheus_exporter.py >= 0.2.0 for --from-poll
 Licence        : GPL-3.0-or-later (GNU General Public License v3.0 or later)
 Copyright      : Copyright (C) 2026 William W. Wallace
 Status         : Internal draft - verified with Grafana 12.2 and Prometheus 3.5
===============================================================================

WHAT IT MAKES
-------------
One Grafana dashboard (JSON model, schemaVersion 36 = Grafana 9+; newer
Grafana versions migrate it on load):

  * "Overview" row: per-meter UP/DOWN, seconds since the last poll, poll
    duration, fresh tables per meter.
  * One row PER METER (IP address). In it, one time-series panel PER UNIT:
    every parameter of that meter with the same unit is a trace of the same
    plot (V, A, W, VAR, VA, %, Hz, kWh, ...), legend as a table with last /
    min / max / mean. Parameters without a unit (configuration numbers,
    status words, counters) go to a table panel instead of a plot.

The panels query the exporter's metric ab_meter_value{ip,table,parameter,unit}
with a unit selector, so a panel picks up every series of that unit by itself;
the meter inventory only decides which panels exist, their order, titles,
Grafana units and legend format.

WHERE THE INVENTORY COMES FROM (one of)
--------------------------------------
  --from-poll        (default) poll the meters ONCE through the exporter's
                     poll_once() - the returned dict, headless, NO files
                     written apart from the dashboard JSON itself
  --from-exporter U  a running exporter's /metrics, e.g. http://host:9184/metrics
  --from-prometheus U Prometheus' series API, e.g. http://host:9090 (works for
                     pushed single-run data as well)
  --from-json P      a line written by `ab_prometheus_exporter.py --print-json`
                     ('-' = stdin)

LAYOUTS
-------
  --layout per-ip    (default) one fixed row per meter found - exactly the
                     meters and units of the inventory; --names gives rows
                     friendly titles
  --layout repeat    one row repeated by Grafana for every value of the $ip
                     variable (meters added later appear without rebuilding);
                     panels = union of the units of all meters

OUTPUT
------
  --out PATH         write the JSON (default monitoring/grafana/dashboards/
                     ab_power_meters.json; '-' = stdout). Import it in Grafana
                     (Dashboards > New > Import) or let file provisioning load it.
  --grafana-url URL  upload through Grafana's HTTP API instead / as well
                     (POST /api/dashboards/db). The service-account token is
                     read from the environment variable named by --token-env
                     (default GRAFANA_TOKEN), never from the command line.

USAGE
-----
    python ab_grafana_dashboard.py                                   # real meters (IP_LIST)
    python ab_grafana_dashboard.py --ips 10.16.130.50,10.16.130.54 \\
           --names "10.16.130.50=Maser bldg,10.16.130.54=Lab"
    python ab_grafana_dashboard.py --from-prometheus http://localhost:9090
    python ab_grafana_dashboard.py --layout repeat --out monitoring/grafana/dashboards/ab_power_meters.json
    set GRAFANA_TOKEN=glsa_...  (Windows)   /   export GRAFANA_TOKEN=glsa_...
    python ab_grafana_dashboard.py --grafana-url http://localhost:3000 --out ""

REFERENCES
----------
[G1] Grafana Labs, "JSON model" (dashboard JSON, schemaVersion, panels, rows).
     https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/view-dashboard-json-model/
[G2] Grafana Labs, "Provision Grafana" (data source and dashboard providers).
     https://grafana.com/docs/grafana/latest/administration/provisioning/
[G3] Grafana Labs, "Dashboard HTTP API" (POST /api/dashboards/db).
     https://grafana.com/docs/grafana/latest/developers/http_api/dashboard/
[G4] Grafana Labs, "Variables" (data source variable, query variable, repeat).
     https://grafana.com/docs/grafana/latest/dashboards/variables/
[G5] Prometheus, "HTTP API - Finding series by label matchers" (/api/v1/series).
     https://prometheus.io/docs/prometheus/latest/querying/api/

REVISION HISTORY
----------------
0.0.1  2026-10-27  First internal draft (exporter 0.2.0, monitor 1.6.4).
===============================================================================
"""

# %% ===========================================================================
#    IMPORTS
# =============================================================================
from __future__ import annotations

import sys

sys.dont_write_bytecode = True          # leave no __pycache__ behind

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

__version__ = "0.0.1"

# %% ===========================================================================
#    SETTINGS
# =============================================================================
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "monitoring", "grafana", "dashboards", "ab_power_meters.json")
DEFAULT_UID = "ab-power-meters"
DEFAULT_TITLE = "AB Power Meters"
DEFAULT_JOB = "ab_power_meter"          # job label in Prometheus (pull job name = push --push-job)
DEFAULT_DS_UID = "ab-prometheus"        # uid of the provisioned Prometheus data source
METRIC = "ab_meter_value"

#: Unit as reported by the monitor -> (panel title, Grafana unit id). Grafana
#: scales automatically (33 000 W shows as 33 kW). Unknown units use a suffix.
UNIT_INFO: Dict[str, Tuple[str, str]] = {
    "V": ("Voltage", "volt"),
    "A": ("Current", "amp"),
    "W": ("Real power", "watt"),
    "VAR": ("Reactive power", "voltampreact"),
    "VA": ("Apparent power", "voltamp"),
    "%": ("Percent", "percent"),
    "Hz": ("Frequency", "hertz"),
    "kWh": ("Real energy", "kwatth"),
    "kVARh": ("Reactive energy", "suffix: kVARh"),
    "kVAh": ("Apparent energy", "suffix: kVAh"),
    "s": ("Time", "s"),
    "ms": ("Time", "ms"),
    "min": ("Time", "m"),
}
#: Panel order inside a meter's row; other units follow alphabetically.
UNIT_ORDER = ["V", "A", "W", "VAR", "VA", "%", "Hz", "kWh", "kVARh", "kVAh", "s", "min", "ms"]

#: Inventory: ip -> unit -> list of (table, parameter)
Inventory = Dict[str, Dict[str, List[Tuple[str, str]]]]


# %% ===========================================================================
#    INVENTORY SOURCES
# =============================================================================
def inventory_from_snapshot(snap: Dict[str, Any]) -> Inventory:
    """Inventory from the exporter's dict (snapshot() / a --print-json line)."""
    inv: Inventory = {}
    for ip, meter in snap.get("meters", {}).items():
        for table, entry in meter.get("tables", {}).items():
            for param, value_unit in entry.get("values", {}).items():
                unit = value_unit[1] if isinstance(value_unit, (list, tuple)) else ""
                inv.setdefault(ip, {}).setdefault(unit or "", []).append((table, param))
    return inv


def inventory_from_labels(label_sets: List[Dict[str, str]]) -> Inventory:
    """Inventory from ab_meter_value label sets (exporter text or Prometheus API)."""
    inv: Inventory = {}
    seen = set()
    for labels in label_sets:
        key = (labels.get("ip", ""), labels.get("unit", ""), labels.get("table", ""),
               labels.get("parameter", ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        inv.setdefault(key[0], {}).setdefault(key[1], []).append((key[2], key[3]))
    return inv


def inventory_from_poll(ips: Optional[List[str]], url_template: Optional[str],
                        timeout: Optional[float]) -> Inventory:
    """Poll the meters once (headless, no files) and use the returned dict."""
    import ab_prometheus_exporter as exp            # also imports the monitor
    if url_template:
        exp.abm.METER_URL_TEMPLATE = url_template
    snap = exp.poll_once(ips, timeout=timeout)
    down = [ip for ip, m in snap["meters"].items() if not m["up"]]
    if down:
        sys.stderr.write("WARNING: no answer from {0} - not in the dashboard "
                         "(use --layout repeat, or rebuild when they answer).\n".format(", ".join(down)))
    return inventory_from_snapshot(snap)


def inventory_from_exporter(url: str) -> Inventory:
    """Inventory from a running exporter's /metrics text."""
    from prometheus_client.parser import text_string_to_metric_families
    with urllib.request.urlopen(url, timeout=15) as response:   # nosec - user-given URL
        text = response.read().decode("utf-8")
    labels = [s.labels for fam in text_string_to_metric_families(text)
              if fam.name == METRIC for s in fam.samples]
    return inventory_from_labels(labels)


def inventory_from_prometheus(url: str, job: str, lookback_s: float = 3600.0) -> Inventory:
    """Inventory from Prometheus' series API (pull or pushed data) [G5]."""
    now = time.time()
    query = urllib.parse.urlencode({"match[]": '{0}{{job="{1}"}}'.format(METRIC, job),
                                    "start": "{0:.0f}".format(now - lookback_s), "end": "{0:.0f}".format(now)})
    with urllib.request.urlopen(url.rstrip("/") + "/api/v1/series?" + query, timeout=15) as response:  # nosec
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError("Prometheus series API: {0}".format(payload))
    return inventory_from_labels(payload.get("data", []))


def inventory_from_json(path: str) -> Inventory:
    """Inventory from the LAST line of an --print-json stream ('-' = stdin)."""
    handle = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        lines = [line for line in handle if line.strip()]
    finally:
        if handle is not sys.stdin:
            handle.close()
    if not lines:
        raise ValueError("no JSON line in {0}".format(path))
    return inventory_from_snapshot(json.loads(lines[-1]))


# %% ===========================================================================
#    PANEL BUILDERS
# =============================================================================
def _ordered_units(units) -> List[str]:
    """Units in dashboard order: known order first, then the rest A-Z; '' never."""
    known = [u for u in UNIT_ORDER if u in units]
    rest = sorted(u for u in units if u and u not in UNIT_ORDER)
    return known + rest


def _unit_title(unit: str, params: List[Tuple[str, str]]) -> str:
    """Panel title such as "Voltage [V]" ("Power factor [%]" for PF readings)."""
    name, _ = UNIT_INFO.get(unit, (unit, ""))
    if unit == "%" and params and all(" pf" in " " + p.lower() for _, p in params):
        name = "Power factor"
    return "{0} [{1}]".format(name, unit)


def _grafana_unit(unit: str) -> str:
    """Grafana unit id for a monitor unit (suffix for unknown units)."""
    return UNIT_INFO.get(unit, ("", "suffix: " + unit))[1]


def _legend(params: List[Tuple[str, str]]) -> str:
    """{{parameter}}, or {{parameter}} ({{table}}) when a name repeats across tables."""
    names = [p for _, p in params]
    return "{{parameter}}" if len(names) == len(set(names)) else "{{parameter}} ({{table}})"


def _ds() -> Dict[str, str]:
    """Panel data source: the dashboard's $datasource variable."""
    return {"type": "prometheus", "uid": "${datasource}"}


def timeseries_panel(pid: int, title: str, expr: str, legend: str, unit: str,
                     grid: Dict[str, int], description: str = "") -> Dict[str, Any]:
    """Time-series panel: all series of one meter and unit, table legend."""
    return {
        "id": pid, "type": "timeseries", "title": title, "description": description,
        "datasource": _ds(), "gridPos": grid,
        "targets": [{"refId": "A", "datasource": _ds(), "expr": expr, "legendFormat": legend,
                     "range": True, "instant": False}],
        "fieldConfig": {
            "defaults": {
                "unit": unit, "decimals": None,
                "color": {"mode": "palette-classic"},
                "custom": {"drawStyle": "line", "lineInterpolation": "linear", "lineWidth": 1,
                           "fillOpacity": 0, "showPoints": "auto", "pointSize": 4,
                           "spanNulls": False, "axisPlacement": "auto",
                           "stacking": {"mode": "none", "group": "A"}},
            },
            "overrides": [],
        },
        "options": {
            "legend": {"showLegend": True, "displayMode": "table", "placement": "bottom",
                       "calcs": ["lastNotNull", "min", "max", "mean"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def table_panel(pid: int, title: str, expr: str, grid: Dict[str, int]) -> Dict[str, Any]:
    """Instant table of the values without a unit (configuration, status)."""
    return {
        "id": pid, "type": "table", "title": title, "datasource": _ds(), "gridPos": grid,
        "description": "Readings without a physical unit: configuration numbers, status words, counters.",
        "targets": [{"refId": "A", "datasource": _ds(), "expr": expr, "format": "table",
                     "instant": True, "range": False}],
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"Time": True, "__name__": True, "job": True, "instance": True,
                              "ip": True, "unit": True},
            "indexByName": {"table": 0, "parameter": 1, "Value": 2},
            "renameByName": {"table": "Table", "parameter": "Parameter", "Value": "Value"}}}],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm",
                    "sortBy": [{"displayName": "Table", "desc": False}]},
    }


def stat_panel(pid: int, title: str, expr: str, legend: str, grid: Dict[str, int],
               unit: str = "none", mappings: Optional[List[Dict[str, Any]]] = None,
               thresholds: Optional[List[Dict[str, Any]]] = None, color_mode: str = "value") -> Dict[str, Any]:
    """Stat panel (overview row)."""
    return {
        "id": pid, "type": "stat", "title": title, "datasource": _ds(), "gridPos": grid,
        "targets": [{"refId": "A", "datasource": _ds(), "expr": expr, "legendFormat": legend,
                     "instant": True, "range": False}],
        "fieldConfig": {"defaults": {
            "unit": unit, "mappings": mappings or [],
            "thresholds": {"mode": "absolute",
                           "steps": thresholds or [{"color": "green", "value": None}]},
            "color": {"mode": "thresholds"}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": color_mode, "graphMode": "none", "textMode": "value_and_name",
                    "justifyMode": "auto", "orientation": "auto"},
    }


def row_panel(pid: int, title: str, y: int, repeat: Optional[str] = None) -> Dict[str, Any]:
    """Expanded row header; the panels that follow it belong to it."""
    row = {"id": pid, "type": "row", "title": title, "collapsed": False, "panels": [],
           "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}}
    if repeat:
        row["repeat"] = repeat
    return row


# %% ===========================================================================
#    DASHBOARD
# =============================================================================
def _sel(ip_matcher: str, unit: str) -> str:
    """PromQL selector of one meter and unit; ip_matcher is 'ip="10.16.130.50"' or
    'ip=~"$ip"' (repeat layout: Grafana regex-escapes multi-value variables for
    Prometheus, so a repeated $ip only matches with =~ [G4])."""
    return '{0}{{job=~"$job", {1}, unit="{2}"}}'.format(METRIC, ip_matcher, unit.replace('"', '\\"'))


#: Panels with more traces than this span the full width with the legend on
#: the right: Grafana gives a bottom legend only about a third of the panel
#: height, so a long legend table would be cut off (seen in Grafana 12.2).
WIDE_ABOVE_TRACES = 6


def _panel_layout(traces: int) -> Tuple[int, int, str]:
    """(width, height, legend placement) for a time-series panel with ``traces`` lines.

    Height in 30 px grid units (~38 px with the gap). A bottom legend line is
    ~24 px plus a ~26 px header and gets about 1/3 of the panel; a right
    legend uses the full height beside the plot.
    """
    if traces > WIDE_ABOVE_TRACES:
        return 24, int(max(10, min(24, -(-(60 + 24 * traces) // 38)))), "right"
    return 12, int(max(10, min(24, -(-(26 + 24 * traces) // 12)))), "bottom"


def _meter_panels(next_id: int, y: int, ip_label: str, ip_matcher: str,
                  units: Dict[str, List[Tuple[str, str]]], unitless: str) -> Tuple[List[Dict[str, Any]], int, int]:
    """Panels of one meter's row: one per unit + the unitless table.

    Up to WIDE_ABOVE_TRACES traces: half width, two per line, legend below.
    More: full width, legend on the right. Returns (panels, next id, next y).
    """
    panels: List[Dict[str, Any]] = []
    slots: List[Tuple[str, List[Tuple[str, str]]]] = [(u, units[u]) for u in _ordered_units(units)]
    if "" in units and unitless == "plot":
        slots.append(("", units[""]))
    x, line_h = 0, 0
    for unit, params in slots:
        w, h, legend_at = _panel_layout(len(params))
        if w == 24 and x == 12:                                # finish the half-filled line
            x, y, line_h = 0, y + line_h, 0
        grid = {"x": x, "y": y, "w": w, "h": h}
        title = (_unit_title(unit, params) if unit else "No unit") + " - " + ip_label
        panel = timeseries_panel(next_id, title, _sel(ip_matcher, unit), _legend(params),
                                 _grafana_unit(unit) if unit else "none", grid,
                                 "{0} trace(s): every parameter of this meter with unit "
                                 "'{1}'.".format(len(params), unit))
        panel["options"]["legend"]["placement"] = legend_at
        panels.append(panel)
        next_id += 1
        line_h = max(line_h, h)
        if w == 24 or x == 12:                                 # line complete
            x, y, line_h = 0, y + line_h, 0
        else:
            x = 12
    if x == 12:                                                # odd panel left on its line
        y += line_h
    if "" in units and unitless == "table":
        h = int(max(6, min(24, 3 + round(1.2 * len(units[""])))))   # ~36 px per table row
        panels.append(table_panel(next_id, "Configuration and status (no unit) - " + ip_label,
                                  _sel(ip_matcher, ""), {"x": 0, "y": y, "w": 24, "h": h}))
        next_id += 1
        y += h
    return panels, next_id, y


def build_dashboard(inv: Inventory, layout: str = "per-ip", names: Optional[Dict[str, str]] = None,
                    title: str = DEFAULT_TITLE, uid: str = DEFAULT_UID, job: str = DEFAULT_JOB,
                    datasource_uid: str = DEFAULT_DS_UID, refresh: str = "30s",
                    time_from: str = "now-6h", interval_s: float = 30.0, stale_s: float = 0.0,
                    unitless: str = "table", exclude_tables: Optional[List[str]] = None) -> Dict[str, Any]:
    """The dashboard JSON model [G1] for an inventory (see module header)."""
    names = names or {}
    excluded = set(exclude_tables or [])
    inv = {ip: {u: [tp for tp in params if tp[0] not in excluded] for u, params in units.items()}
           for ip, units in inv.items()}
    inv = {ip: {u: p for u, p in units.items() if p} for ip, units in inv.items()}
    if unitless == "skip":
        inv = {ip: {u: p for u, p in units.items() if u} for ip, units in inv.items()}
    panels: List[Dict[str, Any]] = []
    pid, y = 1, 0

    # --- overview row ------------------------------------------------------------
    panels.append(row_panel(pid, "Overview", y)); pid += 1; y += 1
    up_map = [{"type": "value", "options": {"0": {"text": "DOWN / STALE", "color": "red"},
                                            "1": {"text": "UP", "color": "green"}}}]
    # UP only when the meter answered AND that poll is recent: pushed (single-run)
    # values stay in the Pushgateway, so ab_meter_up alone would stay 1 forever.
    answering = ('ab_meter_up{{job=~"$job"}} * on(job, instance) group_left() '
                 '(time() - ab_meter_last_poll_timestamp_seconds{{job=~"$job"}} < bool {0:g})'
                 ).format(stale_s if stale_s else 3 * interval_s)
    panels.append(stat_panel(pid, "Meter answering", answering, "{{ip}}",
                             {"x": 0, "y": y, "w": 8, "h": 4}, mappings=up_map,
                             thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
                             color_mode="background")); pid += 1
    panels.append(stat_panel(pid, "Seconds since last poll",
                             'time() - max(ab_meter_last_poll_timestamp_seconds{job=~"$job"})', "age",
                             {"x": 8, "y": y, "w": 5, "h": 4}, unit="s",
                             thresholds=[{"color": "green", "value": None},
                                         {"color": "orange", "value": 2.5 * interval_s},
                                         {"color": "red", "value": 10 * interval_s}])); pid += 1
    panels.append(stat_panel(pid, "Poll duration", 'max(ab_meter_poll_duration_seconds{job=~"$job"})',
                             "poll", {"x": 13, "y": y, "w": 4, "h": 4}, unit="s",
                             thresholds=[{"color": "green", "value": None},
                                         {"color": "orange", "value": 0.8 * interval_s}])); pid += 1
    panels.append(stat_panel(pid, "Fresh tables per meter",
                             'sum by (ip) (ab_meter_table_up{job=~"$job"})', "{{ip}}",
                             {"x": 17, "y": y, "w": 7, "h": 4},
                             thresholds=[{"color": "red", "value": None},
                                         {"color": "green", "value": 1}])); pid += 1
    y += 4

    # --- one row per meter ------------------------------------------------------
    if layout == "repeat":
        union: Dict[str, List[Tuple[str, str]]] = {}
        for units in inv.values():
            for unit, params in units.items():
                known = set(union.get(unit, []))
                union.setdefault(unit, []).extend(tp for tp in params if tp not in known)
        panels.append(row_panel(pid, "Meter $ip", y, repeat="ip")); pid += 1; y += 1
        body, pid, y = _meter_panels(pid, y, "$ip", 'ip=~"$ip"', union, unitless)
        panels.extend(body)
    else:
        for ip in sorted(inv, key=_ip_key):
            label = "{0} ({1})".format(names[ip], ip) if ip in names else ip
            panels.append(row_panel(pid, "Meter " + label, y)); pid += 1; y += 1
            body, pid, y = _meter_panels(pid, y, names.get(ip, ip), "ip=" + json.dumps(ip), inv[ip],
                                         unitless)
            panels.extend(body)

    variables: List[Dict[str, Any]] = [
        {"type": "datasource", "name": "datasource", "label": "Data source", "query": "prometheus",
         "current": {"text": datasource_uid, "value": datasource_uid}, "hide": 0, "refresh": 1,
         "regex": "", "options": []},
        {"type": "query", "name": "job", "label": "Job", "datasource": _ds(),
         "query": {"query": "label_values(ab_meter_up, job)", "refId": "job"},
         "definition": "label_values(ab_meter_up, job)", "refresh": 2, "sort": 1,
         "current": {"text": job, "value": job}, "includeAll": False, "multi": False,
         "hide": 0, "options": []},
    ]
    if layout == "repeat":
        variables.append({"type": "query", "name": "ip", "label": "Meter", "datasource": _ds(),
                          "query": {"query": 'label_values(ab_meter_up{job=~"$job"}, ip)', "refId": "ip"},
                          "definition": 'label_values(ab_meter_up{job=~"$job"}, ip)', "refresh": 2,
                          "sort": 1, "includeAll": True, "multi": True,
                          "current": {"text": ["All"], "value": ["$__all"]}, "hide": 0, "options": []})
    return {
        "uid": uid, "title": title, "tags": ["ab-power-meter", "gbo", "prometheus"],
        "description": "Allen-Bradley site power meters - ab_prometheus_exporter {0}, built by "
                       "ab_grafana_dashboard.py {1}.".format(_exporter_version(), __version__),
        "timezone": "browser", "editable": True, "graphTooltip": 1, "refresh": refresh,
        "time": {"from": time_from, "to": "now"}, "schemaVersion": 36, "version": 1,
        "templating": {"list": variables}, "annotations": {"list": []}, "links": [],
        "panels": panels,
    }


def _ip_key(ip: str):
    """Sort key placing 10.16.130.9 before 10.16.130.50 (numeric octets)."""
    try:
        return (0, tuple(int(part) for part in ip.split(".")))
    except ValueError:
        return (1, ip)


def _exporter_version() -> str:
    """The exporter version written into the description (without importing it)."""
    path = os.path.join(HERE, "ab_prometheus_exporter.py")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("__version__"):
                    return line.split("=")[1].strip().strip('"\'')
    except OSError:
        pass
    return "?"


# %% ===========================================================================
#    GRAFANA HTTP API UPLOAD
# =============================================================================
def upload(dashboard: Dict[str, Any], grafana_url: str, token: str,
           folder_uid: Optional[str] = None, message: str = "") -> Dict[str, Any]:
    """Create or overwrite the dashboard through POST /api/dashboards/db [G3]."""
    body = {"dashboard": dict(dashboard, id=None), "overwrite": True,
            "message": message or "ab_grafana_dashboard.py {0}".format(__version__)}
    if folder_uid:
        body["folderUid"] = folder_uid
    request = urllib.request.Request(grafana_url.rstrip("/") + "/api/dashboards/db",
                                     data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=30) as response:     # nosec - user-given URL
        return json.loads(response.read().decode("utf-8"))


# %% ===========================================================================
#    COMMAND LINE
# =============================================================================
def _parse_names(text: str) -> Dict[str, str]:
    """"ip=Name,ip2=Name 2" -> {ip: name}."""
    names: Dict[str, str] = {}
    for item in (text or "").split(","):
        if "=" in item:
            ip, name = item.split("=", 1)
            if ip.strip() and name.strip():
                names[ip.strip()] = name.strip()
    return names


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--from-exporter", dest="from_exporter", metavar="URL",
                     help="read a running exporter's /metrics")
    src.add_argument("--from-prometheus", dest="from_prometheus", metavar="URL",
                     help="read Prometheus' series API (e.g. http://localhost:9090)")
    src.add_argument("--from-json", dest="from_json", metavar="PATH",
                     help="read a --print-json line ('-' = stdin)")
    parser.add_argument("--ips", help="meters for the default --from-poll (default: monitor IP_LIST)")
    parser.add_argument("--url-template", dest="url_template",
                        help='meter page URL template for --from-poll (default "http://{ip}/{page}")')
    parser.add_argument("--timeout", type=float, default=None, help="HTTP timeout per meter page (s)")
    parser.add_argument("--layout", choices=["per-ip", "repeat"], default="per-ip")
    parser.add_argument("--names", default="", help='row titles: "10.16.130.50=Maser bldg,..."')
    parser.add_argument("--unitless", choices=["table", "plot", "skip"], default="table",
                        help="values without a unit: one table per meter, one plot, or left out")
    parser.add_argument("--exclude-tables", dest="exclude_tables", default="",
                        help="comma-separated meter tables to leave out")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--uid", default=DEFAULT_UID)
    parser.add_argument("--job", default=DEFAULT_JOB, help="Prometheus job label of the meter data")
    parser.add_argument("--datasource-uid", dest="datasource_uid", default=DEFAULT_DS_UID)
    parser.add_argument("--refresh", default="30s", help="dashboard auto-refresh")
    parser.add_argument("--interval", type=float, default=30.0,
                        help="exporter poll interval (s) - colours of 'Seconds since last poll'")
    parser.add_argument("--stale-after", dest="stale_after", type=float, default=0.0,
                        help="'Meter answering' shows DOWN / STALE when the last poll is older "
                             "(s; default 3 x --interval; single runs: 3 x their schedule period)")
    parser.add_argument("--time-from", dest="time_from", default="now-6h")
    parser.add_argument("--out", default=DEFAULT_OUT, help="JSON file ('-' = stdout, '' = none)")
    parser.add_argument("--grafana-url", dest="grafana_url", default=None,
                        help="also upload via the Grafana HTTP API")
    parser.add_argument("--token-env", dest="token_env", default="GRAFANA_TOKEN",
                        help="environment variable holding the Grafana service-account token")
    parser.add_argument("--folder-uid", dest="folder_uid", default=None)
    args = parser.parse_args(argv)

    if args.from_exporter:
        inv = inventory_from_exporter(args.from_exporter)
    elif args.from_prometheus:
        inv = inventory_from_prometheus(args.from_prometheus, args.job)
    elif args.from_json:
        inv = inventory_from_json(args.from_json)
    else:
        ips = [ip.strip() for ip in args.ips.split(",")] if args.ips else None
        inv = inventory_from_poll(ips, args.url_template, args.timeout)
    if not inv:
        sys.stderr.write("ERROR: no meter data found - nothing to build.\n")
        return 1
    dash = build_dashboard(inv, layout=args.layout, names=_parse_names(args.names), title=args.title,
                           uid=args.uid, job=args.job, datasource_uid=args.datasource_uid,
                           refresh=args.refresh, time_from=args.time_from, interval_s=args.interval,
                           stale_s=args.stale_after,
                           unitless=args.unitless,
                           exclude_tables=[t.strip() for t in args.exclude_tables.split(",") if t.strip()])
    text = json.dumps(dash, indent=2) + "\n"
    summary = ", ".join("{0}: {1} unit panel(s)".format(ip, len([u for u in units if u]))
                        for ip, units in sorted(inv.items(), key=lambda kv: _ip_key(kv[0])))
    if args.out == "-":
        sys.stdout.write(text)
    elif args.out:
        folder = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(folder, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        sys.stderr.write("Dashboard written: {0} ({1} panels; {2})\n".format(
            args.out, len(dash["panels"]), summary))
    if args.grafana_url:
        token = os.environ.get(args.token_env, "")
        if not token:
            sys.stderr.write("ERROR: set the Grafana token in the environment variable {0}.\n".format(
                args.token_env))
            return 2
        result = upload(dash, args.grafana_url, token, args.folder_uid)
        sys.stderr.write("Uploaded to {0}: {1}\n".format(args.grafana_url, result.get("url", result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
