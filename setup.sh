#!/usr/bin/env bash
# Bootstraps a Python virtual environment with pip and installs ILD-RS.
# Works even on systems where python3-venv/ensurepip is not installed.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV_DIR=".venv"

echo "[setup] using python: $($PYTHON --version 2>&1)"

if [ ! -d "$VENV_DIR" ]; then
  echo "[setup] creating virtualenv at $VENV_DIR"
  $PYTHON -m venv --without-pip "$VENV_DIR"
  curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/ildrs-get-pip.py
  "$VENV_DIR/bin/python" /tmp/ildrs-get-pip.py
fi

echo "[setup] installing dependencies"
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo "[setup] installing package (editable)"
"$VENV_DIR/bin/python" -m pip install -e .

if [ ! -f .env ]; then
  echo "[setup] creating .env from .env.example (safe defaults)"
  cp .env.example .env
fi

echo
echo "[setup] done."
echo "[setup] activate with:  source .venv/bin/activate"
echo "[setup] run:            ildrs --help"
