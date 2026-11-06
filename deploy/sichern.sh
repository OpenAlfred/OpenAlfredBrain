#!/usr/bin/env bash
# Sichert Alfreds Gedächtnis, Einstellungen und Zugänge als Zip, behält 14 Tage. Was hinein
# gehört, steht an einer Stelle: in src/alfred/sichern.py (gleiche Sicherung wie unter Windows).
#
#   bash deploy/sichern.sh                 -> ~/alfred-sicherung/alfred-JJJJ-MM-TT-HHMM.zip
#   SICHERUNG_ZIEL=/mnt/usb bash deploy/sichern.sh   -> anderer Ordner, z. B. eine USB-Platte
#   SICHERUNG_ZIEL=nutzer@rechner:alfred  -> per scp auf einen anderen Rechner
#
# Täglich um drei Uhr nachts erledigt das der systemd-Timer alfred-sicherung (deploy/einrichten.sh).
# Das Archiv enthält persönliche Daten und Zugangsdaten: entsprechend ablegen.
set -euo pipefail

PROJEKT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZIEL="${SICHERUNG_ZIEL:-$HOME/alfred-sicherung}"
PY="$PROJEKT/.venv/bin/python"

if [[ "$ZIEL" == *:* ]]; then
    TMP="$(mktemp -d)"
    "$PY" -m alfred.sichern --ziel "$TMP" >/dev/null
    scp -q "$TMP"/alfred-*.zip "$ZIEL/"
    echo "Gesichert nach $ZIEL/$(basename "$TMP"/alfred-*.zip)"
    rm -rf "$TMP"
else
    "$PY" -m alfred.sichern --ziel "$ZIEL"
fi
