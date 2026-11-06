#!/usr/bin/env bash
# Holt den neuesten Stand und startet Alfred neu. Gedächtnis, config/alfred.yaml,
# config/persoenlichkeit.yaml und .env bleiben, wie sie sind; sie liegen nicht im Git.
#
#   bash deploy/aktualisieren.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/gemeinsam.sh"
cd "$PROJEKT"

schritt "Neuester Stand"
# config/persoenlichkeit.yaml liegt seit diesem Stand nicht mehr im Git. Verfolgt dieser Rechner die
# Datei noch, löscht der Pull sie; deshalb sichern wir sie vorher und stellen sie danach wieder her.
PERSONA_SICHER="$(mktemp)"
if [ -f config/persoenlichkeit.yaml ]; then
    cp config/persoenlichkeit.yaml "$PERSONA_SICHER"
fi
if git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then
    git pull --ff-only
else
    # Ohne Upstream kommt der Code per git push vom eigenen Rechner (Remote minipc) und liegt schon hier.
    echo "Kein Upstream für $(git branch --show-current): es gilt der Stand, der hier liegt."
fi
if [ -s "$PERSONA_SICHER" ] && [ ! -f config/persoenlichkeit.yaml ]; then
    mkdir -p config  # der Pull entfernt den Ordner, wenn er leer wird
    cp "$PERSONA_SICHER" config/persoenlichkeit.yaml
    echo "config/persoenlichkeit.yaml wiederhergestellt (sie liegt nicht mehr im Git)"
fi
rm -f "$PERSONA_SICHER"

schritt "Python-Pakete"
.venv/bin/pip install -q -e ".[raum]"

schritt "Dienste neu einlesen"
dienste_installieren

# Suchmaschinen ändern ihre Seiten, SearXNG zieht nach; ein neues Abbild heißt neuer Container.
if command -v docker >/dev/null && $DOCKER ps -a --format '{{.Names}}' | grep -qx searxng; then
    schritt "SearXNG"
    vorher="$($DOCKER inspect -f '{{.Image}}' searxng)"
    $DOCKER pull -q searxng/searxng >/dev/null
    if [ "$vorher" != "$($DOCKER image inspect -f '{{.Id}}' searxng/searxng)" ]; then
        $DOCKER rm -f searxng >/dev/null
        searxng_container
        echo "neue Version gestartet"
    else
        echo "aktuell"
    fi
fi

schritt "Alfred neu starten"
mapfile -t LAUFENDE < <(systemctl list-units --type=service --state=active --no-legend --plain 'alfred-*' | awk '{print $1}')
if [ ${#LAUFENDE[@]} -gt 0 ]; then
    sudo systemctl restart "${LAUFENDE[@]}"
    echo "neu gestartet: ${LAUFENDE[*]}"
else
    echo "Kein Alfred-Dienst lief; starten mit bash deploy/einrichten.sh"
fi

schritt "Prüfen"
"$PY" -m alfred.pruefen
