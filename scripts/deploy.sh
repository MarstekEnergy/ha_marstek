#!/usr/bin/env bash
# Deploy Marstek custom component to HA Green over the Samba share.
#
# Usage:
#   ./scripts/deploy.sh            # one-shot sync
#   ./scripts/deploy.sh --watch    # auto-sync on every file change
#
# After sync:
#   - panel.js / frontend  -> browser Ctrl+Shift+R on http://192.168.3.42:8123/marstek-panel
#   - *.py                 -> HA: Settings -> Devices & Services -> Marstek -> Reload
#   - manifest.json        -> restart Home Assistant

set -euo pipefail

HA_HOST="192.168.3.42"
SHARE="//${HA_HOST}/config"
MNT="/mnt/ha-config"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/custom_components/marstek"
DST="${MNT}/custom_components/marstek"

# If your Samba add-on requires auth, create a credentials file ~/.ha-smb-creds:
#   username=youruser
#   password=yourpass
# (chmod 600). Auto-used when present; otherwise falls back to guest.
CRED_FILE="${HOME}/.ha-smb-creds"
if [ -f "$CRED_FILE" ]; then
  SMB_OPTS="credentials=${CRED_FILE},uid=$(id -u),gid=$(id -g),iocharset=utf8,vers=3.0"
else
  SMB_OPTS="guest,uid=$(id -u),gid=$(id -g),iocharset=utf8,vers=3.0"
fi

# --inplace: the Samba share (HA add-on) rejects rsync's temp names for
# __init__.py — mkstemp(".__init__.py.XXXX") comes back ENOENT (the server
# vetoes ".__init__*"), which aborted the whole sync with code 23 before any
# other file was written. Writing in place skips the temp file entirely.
RSYNC_OPTS=(-av --inplace --delete --exclude='__pycache__' --exclude='*.pyc')

ensure_mount() {
  if mountpoint -q "$MNT"; then return; fi
  echo ">> mounting ${SHARE} -> ${MNT}"
  mkdir -p "$MNT"
  if ! mount -t cifs "$SHARE" "$MNT" -o "$SMB_OPTS"; then
    echo "!! mount failed. Try: apt install cifs-utils"
    echo "!! or if Samba needs auth, set SMB_OPTS to a credentials file (see comments)."
    exit 1
  fi
}

sync_once() {
  ensure_mount
  echo ">> rsync ${SRC} -> ${DST}"
  rsync "${RSYNC_OPTS[@]}" "${SRC}/" "${DST}/"
  echo ">> synced @ $(date +%T) — refresh browser or Reload integration."
}

watch_loop() {
  command -v inotifywait >/dev/null 2>&1 || {
    echo "!! inotifywait not found. Install: apt install inotify-tools"
    exit 1
  }
  ensure_mount
  sync_once
  echo ">> watching ${SRC} for changes (Ctrl+C to stop)"
  while true; do
    inotifywait -qr -e modify,create,delete,move "$SRC" >/dev/null 2>&1
    sleep 0.3
    rsync "${RSYNC_OPTS[@]}" "${SRC}/" "${DST}/" && echo ">> synced @ $(date +%T)"
  done
}

case "${1:-}" in
  --watch) watch_loop ;;
  *) sync_once ;;
esac
