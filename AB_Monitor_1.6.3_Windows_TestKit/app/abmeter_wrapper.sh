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
# Version: 1.1.0   Modified: 2026-10-26
#   1.1.0  POLL_INTERVAL is now passed to the monitor (--interval; 1.0.0 read it
#          but never passed it, so the file default always applied); optional
#          RAM_PCT_LIMIT -> --ram-pct; ABMETER_OUTPUT_DIR is inherited.
#   1.0.0  First version.
# ===========================================================================
set -euo pipefail

# ── Configuration (overridable via the service Environment= lines) ─────────
INSTALL_DIR="${INSTALL_DIR:-/opt/abmeter}"
PYTHON="${PYTHON:-${INSTALL_DIR}/venv/bin/python}"
RUN_DIR="${RUN_DIR:-/run/abmeter}"          # user mode overrides to $XDG_RUNTIME_DIR
POLL_COUNT="${POLL_COUNT:-0}"               # 0 = infinite
POLL_INTERVAL="${POLL_INTERVAL:-30}"        # seconds between polls
RAM_PCT_LIMIT="${RAM_PCT_LIMIT:-}"          # optional system-RAM % flush threshold
# ABMETER_OUTPUT_DIR (optional) is read by the monitor itself.

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
EXTRA_ARGS=()
[ -n "$RAM_PCT_LIMIT" ] && EXTRA_ARGS+=(--ram-pct "$RAM_PCT_LIMIT")
exec "$PYTHON" "${INSTALL_DIR}/ab_power_meter_monitor.py" \
     --headless --count "$POLL_COUNT" --interval "$POLL_INTERVAL" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
