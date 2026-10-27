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
#   sudo ./install.sh --py37                   # pin the 3.7 requirements (needs python3.7)
#   sudo ./install.sh --mamba                  # mamba env, Python 3.12 (default)
#   sudo ./install.sh --mamba --py37 --only-exporter     # RHEL 9 + Python 3.7
#   sudo ./install.sh --only-exporter --once-timer \
#        --push-gateway http://localhost:9091   # single runs every 60 s (timer)
#   sudo ./install.sh --python /opt/conda/envs/abmeter/bin/python   # use a
#                                              #   pre-made (e.g. mamba) env
#
# Flags
# -----
#   --user              install to ~/.config/systemd/user (no sudo)
#   --exporter          also install the exporter service
#   --only-exporter     install ONLY the exporter (no monitor)
#   --python PATH       use this interpreter; skip venv/mamba creation
#   --mamba             create a mamba env in <prefix>/env (python 3.12, or 3.7.12
#                       with --py37), then pip-install the requirements
#   --py37              use requirements-py37.txt and a Python 3.7 interpreter
#                       (python3.7 on PATH, --mamba, or --python). RHEL 9 has no
#                       Python 3.7 package: use --mamba --py37 there.
#                       (otherwise: requirements-py312.txt on Python >= 3.12,
#                        requirements.txt on 3.8 - 3.11)
#   --prefix DIR        install root (default: /opt/abmeter system,
#                                     ~/.local/share/abmeter user)
#   --ips a,b,c         exporter device IPs (default: 10.16.130.50,10.16.130.54)
#   --port N            exporter port           (default: 9184)
#   --interval N        exporter poll interval  (default: 30)
#   --max-history N     exporter history depth  (default: 3)
#   --once-timer        also install ab-exporter-once.service + .timer (single
#                       runs pushed to a Pushgateway; needs --push-gateway)
#   --push-gateway URL  Pushgateway for --once-timer (e.g. http://localhost:9091)
#   --once-period N     seconds between single runs (default: 60)
#   --open-port         open the exporter port in firewalld (RHEL default firewall)
#   -h|--help           this help
#
# @Author: W. Wallace — NRAO / Green Bank Observatory
# Version: 1.2.0   Modified: 2026-10-27
#   1.2.0  RHEL 9 / Python 3.7: --mamba env now in <prefix>/env (was root's
#          home, unreadable for the service user and not executable under
#          SELinux); --py37 requires a real 3.7 interpreter (RHEL 9's python3
#          is 3.9); pip upgraded first ("pip<24.1" on 3.7 - the pip bundled
#          with 3.7 cannot read manylinux_2_28 wheel tags); SELinux labels
#          (bin_t for the env and wrapper) when SELinux is enabled; optional
#          firewalld port; --once-timer installs the single-run timer.
#   1.1.0  Installs vszh5_writer.py (required by monitor 1.6.0 for .vszh5)
#          and ab_caller_Example.py; picks requirements-py312.txt on Python
#          >= 3.12; mamba envs default to Python 3.12; checks h5py/qtpy.
#   1.0.0  First version.
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
ONCE_TIMER=0
PUSH_GATEWAY=""
ONCE_PERIOD="60"
OPEN_PORT=0

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
    --once-timer)    ONCE_TIMER=1; INSTALL_EXPORTER=1 ;;
    --push-gateway)  PUSH_GATEWAY="${2:?--push-gateway needs a URL}"; shift ;;
    --once-period)   ONCE_PERIOD="${2:?--once-period needs seconds}"; shift ;;
    --open-port)     OPEN_PORT=1 ;;
    -h|--help)       sed -n '2,52p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$ONCE_TIMER" -eq 1 ] && [ -z "$PUSH_GATEWAY" ]; then
  echo "--once-timer needs --push-gateway URL (single runs push their values)." >&2; exit 2
fi

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
      "$SRC_DIR"/vszh5_writer.py \
      "$SRC_DIR"/ab_meter_caller.py \
      "$SRC_DIR"/ab_caller_Example.py \
      "$SRC_DIR"/ab_stop.py \
      "$SRC_DIR"/ab_prometheus_exporter.py \
      "$SRC_DIR"/abmeter_wrapper.sh \
      "$SRC_DIR"/requirements.txt \
      "$SRC_DIR"/requirements-py37.txt \
      "$SRC_DIR"/requirements-py312.txt \
      "$INSTALL_DIR"/ 2>/dev/null || true
[ -f "$INSTALL_DIR/vszh5_writer.py" ] || echo "WARN: vszh5_writer.py missing - Veusz .vszh5 output will be disabled." >&2
chmod +x "$INSTALL_DIR/abmeter_wrapper.sh"

# ── Python environment ──────────────────────────────────────────────────────
# Requirements file for an interpreter: py37 pins, 3.12+ file, or the 3.8+ file.
pick_req() {  # pick_req <python>
  if [ "$PY37" -eq 1 ]; then echo "requirements-py37.txt"; return; fi
  if "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "requirements-py312.txt"
  else
    echo "requirements.txt"
  fi
}

# pip_install <python> <requirements>: upgrade pip first (on 3.7 to pip 24.0,
# the last release for 3.7: the pip bundled with 3.7 cannot read the
# manylinux_2_28 wheel tags of e.g. PySide6 6.5.3), then wheels only - no
# compiler needed on the host.
pip_install() {
  local py="$1" req="$2" pipspec="pip"
  "$py" -c 'import sys; sys.exit(0 if sys.version_info < (3, 8) else 1)' && pipspec="pip<24.1"
  "$py" -m pip install --upgrade "$pipspec" >/dev/null
  "$py" -m pip install --only-binary=:all: -r "$req"
}

if [ -n "$PYTHON_OVERRIDE" ]; then
  PYTHON="$PYTHON_OVERRIDE"
  REQ_FILE="$(pick_req "$PYTHON")"
  echo "Using supplied interpreter: $PYTHON (skipping env creation)"
  "$PYTHON" -c 'import requests, bs4, lxml' 2>/dev/null \
    || echo "WARN: core deps not importable in $PYTHON — install them (e.g. from $REQ_FILE)."
  "$PYTHON" -c 'import h5py, numpy' 2>/dev/null \
    || echo "WARN: h5py/numpy missing in $PYTHON — needed for Veusz .vszh5 output."
elif [ "$USE_MAMBA" -eq 1 ]; then
  # The env lives under the install prefix (1.2.0): a named env in root's home
  # is unreadable for the service user and not executable under SELinux.
  CONDA=""
  for c in mamba micromamba conda; do command -v "$c" >/dev/null 2>&1 && { CONDA="$c"; break; }; done
  [ -n "$CONDA" ] || { echo "mamba / micromamba / conda not found on PATH (install Miniforge)." >&2; exit 1; }
  PYVER="3.12"; [ "$PY37" -eq 1 ] && PYVER="3.7.12"
  REQ_FILE="requirements-py312.txt"; [ "$PY37" -eq 1 ] && REQ_FILE="requirements-py37.txt"
  echo "Creating $CONDA env $INSTALL_DIR/env (python $PYVER) + pip deps ($REQ_FILE)…"
  "$CONDA" create -y -p "$INSTALL_DIR/env" -c conda-forge --override-channels "python=$PYVER" pip
  PYTHON="$INSTALL_DIR/env/bin/python"
  pip_install "$PYTHON" "$INSTALL_DIR/$REQ_FILE"
else
  BASE_PY="python3"
  if [ "$PY37" -eq 1 ]; then
    BASE_PY="$(command -v python3.7 || true)"
    [ -n "$BASE_PY" ] || { echo "--py37: no python3.7 on PATH. RHEL 9 has no Python 3.7 package -" \
                                "use --mamba --py37, or --python /path/to/python3.7." >&2; exit 1; }
  fi
  REQ_FILE="$(pick_req "$BASE_PY")"
  echo "Creating venv at $INSTALL_DIR/venv ($("$BASE_PY" -V 2>&1)) + pip deps ($REQ_FILE)…"
  "$BASE_PY" -m venv "$INSTALL_DIR/venv"
  PYTHON="$INSTALL_DIR/venv/bin/python"
  pip_install "$PYTHON" "$INSTALL_DIR/$REQ_FILE"
fi
echo "Interpreter : $PYTHON"

# ── Ownership (system mode) ─────────────────────────────────────────────────
[ "$USER_MODE" -eq 0 ] && chown -R "$SVC_USER":"$SVC_USER" "$INSTALL_DIR"

# ── SELinux (RHEL / Fedora, enforcing by default) ─────────────────────────────
# Services may execute bin_t files; label the environment's bin/ folder and the
# wrapper so systemd can start them, persistently when semanage is available.
if [ "$USER_MODE" -eq 0 ] && command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
  if command -v semanage >/dev/null 2>&1; then
    semanage fcontext -a -t bin_t "$INSTALL_DIR/(env|venv)/bin(/.*)?" 2>/dev/null \
      || semanage fcontext -m -t bin_t "$INSTALL_DIR/(env|venv)/bin(/.*)?"
    semanage fcontext -a -t bin_t "$INSTALL_DIR/abmeter_wrapper\.sh" 2>/dev/null \
      || semanage fcontext -m -t bin_t "$INSTALL_DIR/abmeter_wrapper\.sh"
    restorecon -R "$INSTALL_DIR"
    echo "SELinux: bin_t set for $INSTALL_DIR/{env,venv}/bin and abmeter_wrapper.sh"
  else
    for d in "$INSTALL_DIR/env/bin" "$INSTALL_DIR/venv/bin"; do [ -d "$d" ] && chcon -R -t bin_t "$d"; done
    chcon -t bin_t "$INSTALL_DIR/abmeter_wrapper.sh"
    echo "SELinux: bin_t set with chcon (install policycoreutils-python-utils and re-run for a persistent label)"
  fi
fi

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
if [ "$INSTALL_EXPORTER" -eq 1 ] && [ "$ONCE_TIMER" -eq 0 ]; then
  render "$SRC_DIR/ab-exporter.service" "$UNIT_DIR/ab-exporter.service"
  echo "Installed unit: $UNIT_DIR/ab-exporter.service"
fi
if [ "$ONCE_TIMER" -eq 1 ]; then
  for unit in ab-exporter-once.service ab-exporter-once.timer; do
    render "$SRC_DIR/monitoring/systemd/$unit" "$UNIT_DIR/$unit"
    sed -i -e "s|__PUSH_GATEWAY__|$PUSH_GATEWAY|g" -e "s|__ONCE_PERIOD__|$ONCE_PERIOD|g" "$UNIT_DIR/$unit"
    echo "Installed unit: $UNIT_DIR/$unit"
  done
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
if [ "$INSTALL_EXPORTER" -eq 1 ] && [ "$ONCE_TIMER" -eq 0 ]; then
  $SYSTEMCTL enable --now ab-exporter.service
fi
[ "$ONCE_TIMER" -eq 1 ] && $SYSTEMCTL enable --now ab-exporter-once.timer

# ── firewalld (RHEL default firewall): Prometheus must reach the exporter ─────
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1 \
   && [ "$INSTALL_EXPORTER" -eq 1 ] && [ "$ONCE_TIMER" -eq 0 ]; then
  if [ "$OPEN_PORT" -eq 1 ]; then
    firewall-cmd --permanent --add-port="${PORT}/tcp" && firewall-cmd --reload
    echo "firewalld: opened ${PORT}/tcp"
  else
    echo "firewalld is running: for a Prometheus on another host run"
    echo "  firewall-cmd --permanent --add-port=${PORT}/tcp && firewall-cmd --reload   (or re-run with --open-port)"
  fi
fi

echo
echo "Done."
echo "Status:"
[ "$ONLY_EXPORTER" -eq 0 ] && echo "  $SYSTEMCTL status abmeter"
[ "$INSTALL_EXPORTER" -eq 1 ] && [ "$ONCE_TIMER" -eq 0 ] && echo "  $SYSTEMCTL status ab-exporter"
[ "$INSTALL_EXPORTER" -eq 1 ] && [ "$ONCE_TIMER" -eq 0 ] && echo "  curl -s localhost:${PORT}/metrics | grep ab_meter_"
[ "$ONCE_TIMER" -eq 1 ] && echo "  $SYSTEMCTL list-timers ab-exporter-once.timer; journalctl -u ab-exporter-once"
exit 0
