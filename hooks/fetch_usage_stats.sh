#!/bin/bash
# stop hook wrapper: load credentials from macOS Keychain, then run fetch_usage_stats.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

read_keychain() {
  security find-generic-password -a "$USER" -s "$1" -w 2>/dev/null
}

export WorkosCursorSessionToken="$(read_keychain cursor-session)"
export CursorTeamId="$(read_keychain cursor-team-id)"
export CursorUserId="$(read_keychain cursor-user-id)"

exec python3 "$SCRIPT_DIR/fetch_usage_stats.py"
