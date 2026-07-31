#!/usr/bin/env bash
# ===========================================================================
# abmeter_wrapper.sh — single-instance wrapper for the headless monitor
# ---------------------------------------------------------------------------
# Launched by abmeter.service (Mode A).  Provides:
#   • flock --nonblock single-instance guard (belt to systemd's PID tracking)
#   • a PID file operators can read
#   • journald-friendly stdout/stderr (systemd captures them)
#
# Tunables are read from the environment (set in abmeter.service or a
# drop-in), with sane defaults below.
#
# @Author: W. Wallace — NRAO / Green Bank Observatory
# Version: 1.0.0
# ===========================================================================
set -euo pipefail

# ── Configuration (overridable via the service Environment= lines) ─────────
INSTALL_DIR="${INSTALL_DIR:-/opt/abmeter}"
PYTHON="${PYTHON:-${INSTALL_DIR}/venv/bin/python}"
RUN_DIR="${RUN_DIR:-/run/abmeter}"          # user mode overrides to $XDG_RUNTIME_DIR
POLL_COUNT="${POLL_COUNT:-0}"               # 0 = infinite
POLL_INTERVAL="${POLL_INTERVAL:-30}"        # seconds between polls

LOCK_FILE="${RUN_DIR}/abmeter.lock"
PID_FILE="${RUN_DIR}/abmeter.pid"

mkdir -p "$RUN_DIR"

# ── Single-instance lock (non-blocking; exit 1 if already held) ────────────
exec 9>"$LOCK_FILE"
if ! flock --nonblock 9; then
  echo "abmeter_wrapper: another instance already holds $LOCK_FILE — exiting." >&2
  exit 1
fi

# ── PID file, cleaned up on any exit ───────────────────────────────────────
echo "$$" >"$PID_FILE"
cleanup() { rm -f "$PID_FILE"; }
trap cleanup EXIT INT TERM

# ── Exec the monitor in headless mode ──────────────────────────────────────
# --headless / --count are consumed by ab_power_meter_monitor.py; IP list and
# ENABLE_* outputs are configured inside that script.  exec replaces this shell
# so SIGTERM from systemd reaches Python directly (clean graceful stop).
cd "$INSTALL_DIR"
echo "abmeter_wrapper: starting monitor (count=${POLL_COUNT}, interval=${POLL_INTERVAL}s)"
exec "$PYTHON" "${INSTALL_DIR}/ab_power_meter_monitor.py" \
     --headless --count "$POLL_COUNT"
