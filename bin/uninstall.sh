#!/usr/bin/env bash
# Undo bin/install.sh. The last-seen state (for journaling changes) stays in ~/.local/state/hisho.
set -euo pipefail
ADDON="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(readlink -f "$HOME/.local/bin/matrixctl" 2>/dev/null)" = "$ADDON/bin/matrixctl" ] && rm -f "$HOME/.local/bin/matrixctl"
echo "matrixctl removed"
