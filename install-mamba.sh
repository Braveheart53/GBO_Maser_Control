#!/usr/bin/env bash
# ===========================================================================
# install-mamba.sh — create the AB Power Meter environment PURELY from mamba
#                    (conda-forge only; NO pip is ever invoked)
# ---------------------------------------------------------------------------
# Usage:
#   ./install-mamba.sh                 # env "abmeter", python 3.11, full stack
#   ./install-mamba.sh myenv           # custom env name
#   ABMETER_MINIMAL=1 ./install-mamba.sh   # exporter/headless deps only
#   PYTHON_VERSION=3.10 ./install-mamba.sh # pin a different interpreter
#
# Requires: mamba (or micromamba/conda) already on PATH.
#
# Why pure-conda is 3.8+ only:
#   conda-forge no longer ships Python 3.7 builds for most of these packages,
#   so a no-pip install cannot target 3.7.  For 3.7.11 use the pip route
#   (requirements-py37.txt) inside a mamba-created 3.7 interpreter instead.
#
# @Author: W. Wallace — NRAO / Green Bank Observatory
# Version: 1.0.0
# ===========================================================================
set -euo pipefail

ENV_NAME="${1:-abmeter}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

# Pick an available conda-family front-end (prefer mamba for speed).
if command -v mamba >/dev/null 2>&1;  then CONDA=mamba
elif command -v micromamba >/dev/null 2>&1; then CONDA=micromamba
elif command -v conda >/dev/null 2>&1; then CONDA=conda
else
  echo "ERROR: none of mamba / micromamba / conda found on PATH." >&2
  echo "Install Miniforge (bundles mamba): https://github.com/conda-forge/miniforge" >&2
  exit 1
fi
echo "Using front-end: $CONDA"

# ── Core packages every mode needs ─────────────────────────────────────────
CORE=(requests beautifulsoup4 lxml psutil prometheus_client)

# ── Optional packages (FITS / XLSX / GUI / Veusz) ──────────────────────────
# Skip them for a lean exporter/headless install with ABMETER_MINIMAL=1.
OPTIONAL=(numpy astropy openpyxl pyside6 matplotlib-base veusz)

PKGS=("python=${PYTHON_VERSION}" "${CORE[@]}")
if [ "${ABMETER_MINIMAL:-0}" != "1" ]; then
  PKGS+=("${OPTIONAL[@]}")
  echo "Installing FULL stack (core + FITS + XLSX + GUI + Veusz)."
else
  echo "Installing MINIMAL stack (core only — exporter / headless-file modes)."
fi

echo "Creating env '${ENV_NAME}' (python ${PYTHON_VERSION}) from conda-forge…"
"$CONDA" create -y -n "$ENV_NAME" -c conda-forge "${PKGS[@]}"

echo
echo "Done.  Activate with:"
echo "    mamba activate ${ENV_NAME}     # (or: conda activate ${ENV_NAME})"
echo
echo "The interpreter is now at:"
echo "    \$(mamba run -n ${ENV_NAME} which python)"
echo "Pass that path to install.sh --python <…> to wire up the systemd service."
