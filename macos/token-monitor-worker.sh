#!/bin/bash -p
set -e
resources="$(cd "$(dirname "$0")" && pwd)"
export OCX_MAC_APP=1
export OCX_PLUGIN_PATH="$resources/token-monitor-worker.sh"
export OCX_STATE_DIR="${OCX_STATE_DIR:-$HOME/Library/Application Support/SwiftBarToken}"
# Keep simultaneous hosts out of each other's IPC files, even though the user's
# preferences and named keys are deliberately shared for easy migration.
export OCX_CACHE_DIR="${OCX_MAC_CACHE_DIR:-$HOME/Library/Caches/Token Monitor}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:$PATH"
if [ -f "$OCX_STATE_DIR/config.local.sh" ]; then source "$OCX_STATE_DIR/config.local.sh" >/dev/null; fi
export OCX_MAC_APP=1 OCX_PLUGIN_PATH="$resources/token-monitor-worker.sh"
export OCX_CACHE_DIR="${OCX_MAC_CACHE_DIR:-$HOME/Library/Caches/Token Monitor}"
export PYTHONDONTWRITEBYTECODE=1
exec "$resources/runtime/bin/python3" -I -B -c '
import sys
sys.path.insert(0,sys.argv.pop(1))
sys.path.insert(0,sys.argv.pop(1))
import worker
sys.exit(worker.main(sys.argv[1:]))
' "$resources/lib" "$resources/macos" "$@"
