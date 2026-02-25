#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TARGET_DIR="${HOME}/.local/bin"
TARGET_BIN="${TARGET_DIR}/initiative-tracker"

mkdir -p "$TARGET_DIR"

"$PYTHON_BIN" -m pip install --user --break-system-packages --disable-pip-version-check --no-warn-script-location -e "$ROOT_DIR"

cat > "$TARGET_BIN" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
PYTHON_BIN="\${PYTHON_BIN:-python3}"
export INITIATIVE_TRACKER_HOME="$ROOT_DIR"
exec "\$PYTHON_BIN" -m initiative_tracker.cli "\$@"
WRAPPER
chmod +x "$TARGET_BIN"

echo "Installed initiative-tracker."
echo "Binary: $TARGET_BIN"

if [[ ":$PATH:" != *":$TARGET_DIR:"* ]]; then
  echo "PATH update required:"
  echo "  export PATH=\"$TARGET_DIR:\$PATH\""
fi

echo "Try:"
echo "  initiative-tracker --help"
