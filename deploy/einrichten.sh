#!/usr/bin/env bash
# Richtet Alfred auf Ubuntu Server ein, so weit es ohne dich geht. Mehrfach ausführbar.
#
#   bash deploy/einrichten.sh --sicherung ~/alfred-2026-10-05_0300.zip
#
# Optionen:
#   --sicherung DATEI   Zip vom alten Rechner (python -m alfred.sichern): Gedächtnis,
#                       Einstellungen, Google-Zugang, WhatsApp-Sitzung, Stimmprofil. Eine
#                       andere als die schon eingespielte ersetzt nach Rückfrage den Stand hier.
#   --raum NAME         Raum, dessen Dienst startet (Vorgabe: buero)
#   --ohne-searxng      keine eigene Suchmaschine (Websuche dann bei Mistral, rund 3 Cent je Suche)
#   --ohne-tailscale    kein Tailscale (dann keine App und kein Zugriff vom iPhone)
#   --nicht-starten     alles vorbereiten, aber keine Dienste starten
#
# Fragt nur, was nur du weißt oder tun kannst: den Mistral-Schlüssel (fehlt er in .env), die
# Anmeldung bei Tailscale (ein Link) und, ohne WhatsApp-Sitzung in der Sicherung, das Koppeln.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/gemeinsam.sh"

RAUM=buero
SICHERUNG=""
SEARXNG=1
TAILSCALE=1
STARTEN=1
while [ $# -gt 0 ]; do
    case "$1" in
        --sicherung) SICHERUNG="$(realpath "$2")"; shift 2 ;;
        --raum) RAUM="$2"; shift 2 ;;
        --ohne-searxng) SEARXNG=0; shift ;;
        --ohne-tailscale) TAILSCALE=0; shift ;;
        --nicht-starten) STARTEN=0; shift ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "Unbekannte Option: $1 (--help zeigt alle)" >&2; exit 2 ;;
    esac
done
INTERAKTIV=0
[ -t 0 ] && INTERAKTIV=1
OFFEN=()  # was am Ende noch zu tun bleibt
APP_ADRESSE=""

echo "== Alfred einrichten für $NUTZER in $PROJEKT"
cd "$PROJEKT"

# Das Passwort für sudo gleich am Anfang, nicht erst nach dem ersten langen Schritt; ohne
# Terminal (etwa per ssh ohne -t) bricht das Skript hier mit sudos Meldung ab, statt mittendrin.
if ! sudo -n true 2>/dev/null; then
    echo "Pakete, Dienste und Tailscale brauchen sudo; bitte das Passwort eingeben."
    sudo -v
fi

# Python 3.11 oder neuer (Ubuntu 24.04 bringt 3.12 mit; 22.04 nur 3.10).
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "python3 ist $(python3 --version), Alfred braucht 3.11 oder neuer." >&2
    echo "Ubuntu 24.04 bringt Python 3.12 mit; auf 22.04 hilft das PPA deadsnakes (python3.12-venv)." >&2
    exit 1
fi
if [ -n "$SICHERUNG" ] && [ ! -f "$SICHERUNG" ]; then
    echo "Sicherung nicht gefunden: $SICHERUNG" >&2
    exit 1
fi

# Systempakete: venv, Compiler und PortAudio-Header für PyAudio, ALSA-Werkzeuge zum Prüfen,
# Docker für die eigene Suchmaschine.
schritt "Systempakete"
PAKETE=(python3-venv python3-dev build-essential portaudio19-dev alsa-utils curl openssl)
[ "$SEARXNG" = 1 ] && PAKETE+=(docker.io)
sudo apt-get update -qq
sudo apt-get install -y -qq "${PAKETE[@]}"

# Audiogeräte dürfen nur Mitglieder der Gruppe audio öffnen; Dienste bekommen die Gruppe beim Start.
if ! id -nG "$NUTZER" | grep -qw audio; then
    schritt "$NUTZER zur Gruppe audio hinzufügen"
    sudo usermod -aG audio "$NUTZER"
    # Die Dienste bekommen die Gruppe von systemd; die eigene Shell erst nach neuer Anmeldung.
    OFFEN+=("Neu anmelden (exit, dann wieder ssh): Erst dann darf deine Shell Hörtest und Anlernen am Jabra ausführen")
fi

# udev-Regel für Jabra Speak 510: Gruppe audio darf das HID-Interface für LED-Steuerung ansprechen.
if [ -f "$PROJEKT/deploy/99-jabra.rules" ]; then
    schritt "udev-Regel für Jabra Speak 510 (LED-Steuerung) einrichten"
    sudo cp "$PROJEKT/deploy/99-jabra.rules" /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger
fi

# Zeitzone: Erinnerungen und der Systemprompt rechnen mit der Systemzeit.
if [ "$(timedatectl show -p Timezone --value)" != "Europe/Berlin" ]; then
    schritt "Zeitzone auf Europe/Berlin stellen"
    sudo timedatectl set-timezone Europe/Berlin
fi

schritt "Python-Umgebung"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[raum]"

# WhatsApp-Brücke: eigene Umgebung, weil neonize eine neuere protobuf-Version braucht als Pipecat.
schritt "Umgebung der WhatsApp-Brücke"
[ -d .venv-wa ] || python3 -m venv .venv-wa
.venv-wa/bin/pip install -q --upgrade pip
.venv-wa/bin/pip install -q neonize qrcode

# Dieselbe Sicherung nur einmal: Später würde sie neuere Daten nicht überschreiben, aber
# Gelöschtes zurückbringen. Eine andere (Umzug mit frischem Stand) ersetzt nach Rückfrage
# Gedächtnis und Einstellungen; der bisherige Stand wandert vorher nach ~/alfred-vor-umzug-<Zeit>.
if [ -n "$SICHERUNG" ]; then
    NAME="$(basename "$SICHERUNG")"
    BISHER="$(cat gedaechtnis/.eingespielt 2>/dev/null || true)"
    if [ "$BISHER" = "$NAME" ]; then
        schritt "Sicherung: $NAME schon eingespielt, übersprungen"
    elif [ -n "$BISHER" ] && ! { [ "$INTERAKTIV" = 1 ] && ja "Hier ist $BISHER eingespielt. Durch $NAME ersetzen (Umzug)?"; }; then
        schritt "Sicherung: $NAME nicht eingespielt, es bleibt $BISHER"
        OFFEN+=("Sicherung $NAME einspielen: das Skript im Terminal erneut starten und mit j antworten")
    else
        UEBERSCHREIBEN=()
        if [ -n "$BISHER" ]; then
            ALT="$HOME/alfred-vor-umzug-$(date +%Y-%m-%d-%H%M)"
            schritt "Bisherigen Stand nach $ALT verschieben"
            # Laufende Dienste schrieben sonst in das Gedächtnis, das gerade umzieht.
            sudo systemctl stop 'alfred-raum@*' alfred-web alfred-whatsapp alfred-browser 2>/dev/null || true
            mkdir -p "$ALT"
            mv gedaechtnis "$ALT/"
            cp -a config "$ALT/"
            UEBERSCHREIBEN=(--ueberschreiben)
        fi
        schritt "Sicherung einspielen"
        "$PY" -m alfred.sichern --einspielen "$SICHERUNG" "${UEBERSCHREIBEN[@]}"
        mkdir -p gedaechtnis
        echo "$NAME" >gedaechtnis/.eingespielt
    fi
fi
# Ohne Sicherung gilt die Vorlage; Werte, die das Skript einträgt, brauchen die eigene Datei.
"$PY" -m alfred.einrichtung konfig-anlegen

schritt "Schlüssel (.env)"
if [ ! -f .env ]; then
    cp .env.example .env
fi
chmod 600 .env
if "$PY" -m alfred.einrichtung env-gesetzt MISTRAL_API_KEY; then
    echo "MISTRAL_API_KEY ist eingetragen."
elif [ "$INTERAKTIV" = 1 ]; then
    echo "Den Schlüssel gibt es unter https://console.mistral.ai → API Keys. Die Eingabe bleibt unsichtbar."
    read -rsp "Mistral-API-Schlüssel: " SCHLUESSEL
    echo
    ALFRED_WERT="$SCHLUESSEL" "$PY" -m alfred.einrichtung env-setzen MISTRAL_API_KEY
    unset SCHLUESSEL
else
    OFFEN+=("Mistral-Schlüssel eintragen: nano $PROJEKT/.env, dann das Skript erneut starten")
fi

if [ "$SEARXNG" = 1 ]; then
    schritt "Eigene Suchmaschine (SearXNG): Websuche kostenlos statt rund 3 Cent je Suche"
    systemctl is-active --quiet docker || sudo systemctl enable --now docker
    searxng_starten
    if searxng_antwortet; then
        "$PY" -m alfred.einrichtung konfig suche.url "$SEARXNG_ADRESSE" --nur-wenn-leer
    else
        # suche.url bleibt leer: Ohne erreichbares SearXNG soll Alfred bei Mistral suchen.
        echo "SearXNG antwortet nicht; Alfred sucht vorerst bei Mistral." >&2
        OFFEN+=("SearXNG prüfen: sudo docker logs searxng, dann das Skript erneut starten")
    fi
fi

if [ "$TAILSCALE" = 1 ]; then
    schritt "Tailscale: App und Zugriff vom iPhone, nur in deinem privaten Netz, kein offener Port"
    if ! command -v tailscale >/dev/null; then
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
    if ! tailscale status >/dev/null 2>&1; then
        if [ "$INTERAKTIV" = 1 ]; then
            echo "Gleich erscheint ein Link: im Browser öffnen und mit deinem Tailscale-Konto anmelden."
            sudo tailscale up --accept-dns=false
        else
            OFFEN+=("Tailscale anmelden: sudo tailscale up, dann das Skript erneut starten")
        fi
    fi
    if tailscale status >/dev/null 2>&1; then
        # Ein Server braucht Tailscales DNS nicht. Mit ihm hing die Namensauflösung beim Booten an
        # tailscaled, und die WhatsApp-Brücke fand WhatsApp nicht (16.10.2026).
        sudo tailscale set --accept-dns=false
        # Ubuntus automatische Updates kennen die Paketquelle von Tailscale nicht; so bleibt es aktuell.
        sudo tailscale set --auto-update || echo "Diese Tailscale-Version aktualisiert sich nicht selbst." >&2
        # Alles aus dem eigenen Tailnet darf herein, auch die Sprachverbindung des Browser-Modus (UDP).
        if command -v ufw >/dev/null && sudo env LC_ALL=C ufw status | grep -q '^Status: active'; then
            sudo ufw allow in on tailscale0 comment 'Tailscale: eigene Geräte' >/dev/null
        fi
        # tailscale serve belegt 443 auf den Tailscale-Adressen. Will ein anderer Dienst 443 auf allen
        # Adressen (Pi-hole im Container), startet nach einem Neustart der spätere von beiden nicht;
        # am 16.10.2026 war das Pi-hole. Auch angehaltene Container zählen.
        BELEGT="$(ss -tlnH '( sport = :443 )' | awk '$4 ~ /^(0\.0\.0\.0|\[::\]|\*):443$/ {print $4}' | sort -u | tr '\n' ' ')"
        if command -v docker >/dev/null && [ -n "$($DOCKER ps -aq 2>/dev/null)" ]; then
            BELEGT+="$($DOCKER ps -aq | xargs $DOCKER inspect | "$PY" -m alfred.absichern docker-belegt 443/tcp | tr '\n' ' ')"
        fi
        if [ -n "${BELEGT// /}" ]; then
            echo "Achtung: Port 443 ist schon auf allen Adressen vergeben ($BELEGT)." >&2
            OFFEN+=("Port 443 an die Heimnetz-Adresse binden für ${BELEGT}(im Container etwa \"<Heimnetz-Adresse>:443:443/tcp\"); sonst startet nach einem Neustart er oder Alfreds App nicht")
        fi
        # Beim ersten Mal nennt Tailscale hier einen Link, um HTTPS für das Tailnet einzuschalten, und
        # wartet darauf; die Ausgabe bleibt deshalb sichtbar.
        echo "Erscheint gleich ein Link: im Browser öffnen und HTTPS einschalten, danach geht es weiter."
        sudo tailscale serve --bg --set-path /app 7870
        APP_ADRESSE="$(tailscale status --json | "$PY" -m alfred.einrichtung app-adresse)"
        # Damit ein Tippen auf den Push den Bericht öffnet und Freigaben vom Handy gehen.
        "$PY" -m alfred.einrichtung konfig benachrichtigung.app_url "$APP_ADRESSE" --nur-wenn-leer
        ABLAUF="$(tailscale status --json | "$PY" -m alfred.einrichtung tailscale-ablauf)"
        if [ -n "$ABLAUF" ]; then
            OFFEN+=("Tailscale-Verwaltung (https://login.tailscale.com/admin/machines): beim Server „Disable key expiry“, sonst meldet er sich am $ABLAUF ab")
        fi
    fi
fi

schritt "Dienste installieren"
dienste_installieren
# Die tägliche Sicherung des Gedächtnisses läuft sofort mit.
sudo systemctl enable --now alfred-sicherung.timer

schritt "Prüfen (eine Zeile je Baustein)"
PRUEFUNG=ok
# Die Gruppe audio wirkt in dieser Shell erst nach einer neuen Anmeldung; ohne sie fände die
# Prüfung das Mikrofon nicht, und Alfred startete nicht. sg gibt sie der Prüfung schon jetzt.
if ! id -nG | grep -qw audio && getent group audio | cut -d: -f4 | tr ',' '\n' | grep -qx "$NUTZER"; then
    sg audio -c "$(printf '%q ' "$PY" -m alfred.pruefen)" || PRUEFUNG=fehler
else
    "$PY" -m alfred.pruefen || PRUEFUNG=fehler
fi

if [ "$STARTEN" = 1 ] && [ "$PRUEFUNG" = ok ]; then
    schritt "Alfred starten"
    # Die Brücke zuerst; Raum und App erkennen WhatsApp an ihrer Token-Datei.
    if [ ! -f config/whatsapp.db ] && [ "$INTERAKTIV" = 1 ] && ja "WhatsApp jetzt koppeln (QR-Code mit dem Handy scannen)?"; then
        .venv-wa/bin/python src/alfred/whatsapp_bridge.py --koppeln || true
    fi
    if [ -f config/whatsapp.db ]; then
        sudo systemctl enable --now alfred-whatsapp
    else
        OFFEN+=("WhatsApp (optional): .venv-wa/bin/python src/alfred/whatsapp_bridge.py --koppeln, dann das Skript erneut starten")
    fi
    sudo systemctl enable --now "alfred-raum@$RAUM" alfred-web
    sudo systemctl restart "alfred-raum@$RAUM" alfred-web  # liest neue Einstellungen, falls sie schon liefen
elif [ "$PRUEFUNG" != ok ]; then
    OFFEN+=("Die ✘-Zeilen der Prüfung beheben und das Skript erneut starten; dann startet es Alfred")
    OFFEN+=("Mikrofon und Lautsprecher testen: $PY -m alfred.room $RAUM --hoertest")
fi

echo
echo "== Fertig"
if [ "$PRUEFUNG" = ok ] && [ "$STARTEN" = 1 ]; then
    echo "Alfred läuft. Log: journalctl -u alfred-raum@$RAUM -f   Fehler: $PROJEKT/logs/fehler.log"
fi
if [ -n "$APP_ADRESSE" ]; then
    echo "iPhone: Tailscale-App installieren und anmelden, dann $APP_ADRESSE/ öffnen und zum Home-Bildschirm."
fi
if [ ${#OFFEN[@]} -gt 0 ]; then
    echo "Noch zu tun:"
    for punkt in "${OFFEN[@]}"; do
        echo "  - $punkt"
    done
fi
echo "Später aktualisieren: bash deploy/aktualisieren.sh"
echo "Absichern (Firewall, Neustart nach Updates, SSH): bash deploy/absichern.sh --help"
