#!/usr/bin/env bash
#
# Cloud Agent install script for fund-disclosures.
#
# Idempotent bootstrap that prepares a checked-out working tree so the Python
# pipeline (parsers, AMFI, matching, QC) and the Node fetch/sync CLIs run:
#
#   * Python virtualenv at .venv with requirements.txt installed. Every
#     npm "parse:*" / "amfi:*" / "holdings:*" python script shells out to
#     .venv/bin/python3, so this venv is required for local dev and for the
#     daily scripts/cloud-holdings-update.mjs automation.
#   * holdings-browser Node deps (@aws-sdk/client-s3) for the public API /
#     Vercel functions under holdings-browser/api.
#
# Safe to re-run: venv creation, pip install, and npm ci all converge without
# rewriting lockfiles or requiring network access to anything but the package
# registries.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> fund-disclosures cloud setup (root: $ROOT)"

# 0) Ensure the stdlib venv module is available -------------------------------
# The base image ships python3 without ensurepip/venv, so install the matching
# python3-venv package once. apt-get install is idempotent and (for builds) runs
# only while the baseline snapshot is created.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "==> installing python3-venv (ensurepip unavailable)"
  PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  sudo apt-get update -qq
  sudo apt-get install -y -qq "python${PYVER}-venv" python3-venv
fi

# 1) Python virtualenv + dependencies -----------------------------------------
if [ ! -x ".venv/bin/python3" ]; then
  echo "==> creating python virtualenv at .venv"
  python3 -m venv .venv
else
  echo "==> reusing existing .venv"
fi

echo "==> upgrading pip tooling"
.venv/bin/python3 -m pip install --upgrade pip setuptools wheel

echo "==> installing python requirements"
.venv/bin/python3 -m pip install -r requirements.txt

# 2) holdings-browser Node dependencies ---------------------------------------
if [ -f "holdings-browser/package-lock.json" ]; then
  echo "==> installing holdings-browser node deps (npm ci)"
  npm --prefix holdings-browser ci
fi

echo "==> setup complete"
.venv/bin/python3 --version
node --version
