#!/usr/bin/env bash
# Verification script - runs all tests offline
set -euo pipefail
cd "$(dirname "$0")"
venv/bin/python -m unittest discover -s tests
