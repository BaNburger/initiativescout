#!/usr/bin/env bash
set -euo pipefail

TARGET_BIN="${HOME}/.local/bin/initiative-tracker"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ -f "$TARGET_BIN" ]; then
  rm -f "$TARGET_BIN"
  echo "Removed $TARGET_BIN"
else
  echo "No installed binary found at $TARGET_BIN"
fi

"$PYTHON_BIN" -m pip uninstall -y initiative-tracker >/dev/null 2>&1 || true
echo "Uninstalled Python package (if present): initiative-tracker"
