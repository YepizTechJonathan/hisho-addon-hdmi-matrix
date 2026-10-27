#!/usr/bin/env bash
# Run by `hisho addon install hdmi-matrix` on the Core machine, in the add-on folder: puts matrixctl
# on PATH. Nothing else to install: the integration is Python's standard library talking HTTP.
set -euo pipefail
ADDON="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$HOME/.local/bin"
ln -sfn "$ADDON/bin/matrixctl" "$HOME/.local/bin/matrixctl"
echo "matrixctl -> $ADDON/bin/matrixctl"
