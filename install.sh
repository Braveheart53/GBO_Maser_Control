#!/usr/bin/env bash
# ===========================================================================
# install.sh — installer for the AB Power Meter toolset
# ---------------------------------------------------------------------------
# Installs the headless monitor service (Mode A) and/or the Prometheus
# exporter service (Mode B), in system or per-user systemd, with a Python
# environment created by venv+pip, mamba+pip, or an interpreter you supply.
#
# Quick examples
# --------------
#   sudo ./install.sh                          # system, monitor, venv+pip (3.8+)
#   sudo ./install.sh --exporter               # system, monitor + exporter
#   sudo ./install.sh --only-exporter \
#        --ips 10.16.130.50,10.16.130.54       # system, exporter only
#   ./install.sh --user                        # per-user, monitor
#   sudo ./install.sh --py37                   # pin the 3.7 requirements
#   sudo ./install.sh --python /opt/conda/envs/abmeter/bin/python   # use a
#                                              #   pre-made (e.g. mamba) env
#
# Flags
# -----
#   --user              install to ~/.config/systemd/user (no sudo)
#   --exporter          also install the exporter service
#   --only-exporter     install ONLY the exporter (no monitor)
#   --python PATH       use this interpreter; skip venv/mamba creation
#   --mamba             create a mamba env, then pip-install requirements
#   --py37              use requirements-py37.txt (Python 3.7.x)
#   --prefix DIR        install root (default: /opt/abmeter system,
#                                     ~/.local/share/abmeter user)
#   --ips a,b,c         exporter device IPs (default: 10.16.130.50,10.16.130.54)
#   --port N            exporter port           (default: 9184)
#   --interval N        exporter poll interval  (default: 30)
#   --max-history N     exporter history depth  (default: 3)
#   -h|--help           this help
#
# @Author: W. Wallace — NRAO / Green Bank Observatory
# Version: 1.0.0
# ===========================================================================
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────
USER_MODE=0
INSTALL_EXPORTER=0
ONLY_EXPORTER=0
USE_MAMBA=0
PY37=0
PYTHON_OVERRIDE=""
PREFIX=""
IPS="10.16.130.50,10.16.130.54"
PORT="9184"
INTERVAL="30"
MAXHIST="3"
SVC_USER="abmeter"          # system-mode service account
ENV_NAME="abmeter"          # mamba env name when --mamba

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Arg parsing ─────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --user)          USER_MODE=1 ;;
    --exporter)      INSTALL_EXPORTER=1 ;;
    --only-exporter) ONLY_EXPORTER=1; INSTALL_EXPORTER=1 ;;
    --mamba)         USE_MAMBA=1 ;;
    --py37)          PY37=1 ;;
    --python)        PYTHON_OVERRIDE="${2:?--python needs a path}"; shift ;;
    --prefix)        PREFIX="${2:?--prefix needs a dir}"; shift ;;
    --ips)           IPS="${2:?--ips needs a value}"; shift ;;
    --port)          PORT="${2:?--port needs a value}"; shift ;;
    --interval)      INTERVAL="${2:?--interval needs a value}"; shift ;;
    --max-history)   MAXHIST="${2:?--max-history needs a value}"; shift ;;
    -h|--help)       sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

# ── Resolve install prefix + systemd locations ──────────────────────────────
if [ "$USER_MODE" -eq 1 ]; then
  INSTALL_DIR="${PREFIX:-$HOME/.local/share/abmeter}"
  UNIT_DIR="$HOME/.config/systemd/user"
  SYSTEMCTL="systemctl --user"
  WANTED_BY="default.target"
  USER_LINE=""                       # no User= in user mode
else
  [ "$(id -u)" -eq 0 ] || { echo "System mode needs root — use sudo, or pass --user." >&2; exit 1; }
  INSTALL_DIR="${PREFIX:-/opt/abmeter}"
  UNIT_DIR="/etc/systemd/system"
  SYSTEMCTL="systemctl"
  WANTED_BY="multi-user.target"
  USER_LINE="User=${SVC_USER}"
fi

echo "Install dir : $INSTALL_DIR"
echo "Unit dir    : $UNIT_DIR"
echo "Mode        : $([ "$USER_MODE" -eq 1 ] && echo user || echo system)"

# ── Create the service account (system mode only) ───────────────────────────
if [ "$USER_MODE" -eq 0 ]; then
  if ! id "$SVC_USER" >/dev/null 2>&1; then
    echo "Creating system user '$SVC_USER'…"
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
  fi
fi

# ── Lay down files ──────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cp -f "$SRC_DIR"/ab_power_meter_monitor.py \
      "$SRC_DIR"/ab_meter_caller.py \
      "$SRC_DIR"/ab_stop.py \
      "$SRC_DIR"/ab_prometheus_exporter.py \
      "$SRC_DIR"/abmeter_wrapper.sh \
      "$SRC_DIR"/requirements.txt \
      "$SRC_DIR"/requirements-py37.txt \
      "$INSTALL_DIR"/ 2>/dev/null || true
chmod +x "$INSTALL_DIR/abmeter_wrapper.sh"

# ── Python environment ──────────────────────────────────────────────────────
REQ_FILE="requirements.txt"; [ "$PY37" -eq 1 ] && REQ_FILE="requirements-py37.txt"

if [ -n "$PYTHON_OVERRIDE" ]; then
  PYTHON="$PYTHON_OVERRIDE"
  echo "Using supplied interpreter: $PYTHON (skipping env creation)"
  "$PYTHON" -c 'import requests, bs4, lxml' 2>/dev/null \
    || echo "WARN: core deps not importable in $PYTHON — install them (e.g. from $REQ_FILE)."
elif [ "$USE_MAMBA" -eq 1 ]; then
  command -v mamba >/dev/null 2>&1 || { echo "mamba not found on PATH." >&2; exit 1; }
  echo "Creating mamba env '$ENV_NAME' (interpreter) + pip deps ($REQ_FILE)…"
  mamba create -y -n "$ENV_NAME" "python=$([ "$PY37" -eq 1 ] && echo 3.7.11 || echo 3.11)" pip
  mamba run -n "$ENV_NAME" pip install -r "$INSTALL_DIR/$REQ_FILE"
  PYTHON="$(mamba run -n "$ENV_NAME" which python)"
else
  echo "Creating venv at $INSTALL_DIR/venv + pip deps ($REQ_FILE)…"
  python3 -m venv "$INSTALL_DIR/venv"
  "$INSTALL_DIR/venv/bin/pip" install --upgrade pip >/dev/null
  "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/$REQ_FILE"
  PYTHON="$INSTALL_DIR/venv/bin/python"
fi
echo "Interpreter : $PYTHON"

# ── Ownership (system mode) ─────────────────────────────────────────────────
[ "$USER_MODE" -eq 0 ] && chown -R "$SVC_USER":"$SVC_USER" "$INSTALL_DIR"

# ── Render + install unit files ─────────────────────────────────────────────
render() {  # render <template> <dest>
  sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
      -e "s|__PYTHON__|$PYTHON|g" \
      -e "s|__WANTED_BY__|$WANTED_BY|g" \
      -e "s|__USER_LINE__|$USER_LINE|g" \
      -e "s|__IPS__|$IPS|g" \
      -e "s|__PORT__|$PORT|g" \
      -e "s|__INTERVAL__|$INTERVAL|g" \
      -e "s|__MAXHIST__|$MAXHIST|g" \
      "$1" > "$2"
}

mkdir -p "$UNIT_DIR"
if [ "$ONLY_EXPORTER" -eq 0 ]; then
  render "$SRC_DIR/abmeter.service" "$UNIT_DIR/abmeter.service"
  echo "Installed unit: $UNIT_DIR/abmeter.service"
fi
if [ "$INSTALL_EXPORTER" -eq 1 ]; then
  render "$SRC_DIR/ab-exporter.service" "$UNIT_DIR/ab-exporter.service"
  echo "Installed unit: $UNIT_DIR/ab-exporter.service"
fi

# ── logrotate (system mode only, monitor only) ──────────────────────────────
if [ "$USER_MODE" -eq 0 ] && [ "$ONLY_EXPORTER" -eq 0 ]; then
  sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
      "$SRC_DIR/abmeter_logrotate.conf" > /etc/logrotate.d/abmeter
  echo "Installed logrotate: /etc/logrotate.d/abmeter"
fi

# ── Enable + (re)start ──────────────────────────────────────────────────────
$SYSTEMCTL daemon-reload
if [ "$ONLY_EXPORTER" -eq 0 ]; then
  $SYSTEMCTL enable --now abmeter.service
fi
if [ "$INSTALL_EXPORTER" -eq 1 ]; then
  $SYSTEMCTL enable --now ab-exporter.service
fi

echo
echo "Done."
echo "Status:"
[ "$ONLY_EXPORTER" -eq 0 ] && echo "  $SYSTEMCTL status abmeter"
[ "$INSTALL_EXPORTER" -eq 1 ] && echo "  $SYSTEMCTL status ab-exporter"
[ "$INSTALL_EXPORTER" -eq 1 ] && echo "  curl -s localhost:${PORT}/metrics | grep ab_meter_"
