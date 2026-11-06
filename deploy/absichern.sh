#!/usr/bin/env bash
# Sichert den Rechner ab, auf dem Alfred läuft. Mehrfach ausführbar; fragt, bevor es Regeln entfernt.
#
#   bash deploy/absichern.sh --offen 25565/tcp --offen 5520/udp --heimnetz 80/tcp --heimnetz 443/tcp
#
# Was es tut:
#   - Firewall (ufw): eingehend nur SSH aus dem Heimnetz, alles aus deinem Tailnet und die Ports
#     aus --offen und --heimnetz. Regeln, die SSH oder einen --heimnetz-Port für jeden Absender
#     öffnen, entfernt es nach Rückfrage.
#   - Sicherheitsupdates täglich, und nachts ein Neustart, wenn eins ihn braucht (Kernel, libc).
#   - SSH: nur mit Schlüssel, nie als root, ohne X11; nur, wenn ein Schlüssel hinterlegt ist.
#   - Tailscale aktualisiert sich selbst.
#
# Optionen:
#   --offen PORT       für alle erreichbar, etwa Spiele-Server: 25565/tcp, 25565:25566/tcp, 5520/udp
#   --heimnetz PORT    nur aus dem Heimnetz und dem Tailnet, etwa die Pi-hole-Oberfläche: 80/tcp
#   --neustart HH:MM   Uhrzeit für den Neustart nach Updates (Vorgabe 04:00)
#   --ohne-neustart    nie von selbst neu starten
#
# Docker-Container mit veröffentlichten Ports (-p 8443:8443) umgehen ufw bei IPv4: Für sie
# entscheidet der Router, was aus dem Internet ankommt. ufw wirkt bei ihnen nur auf IPv6.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/gemeinsam.sh"

FUER_ALLE=()
NUR_HEIMNETZ=()
NEUSTART="04:00"
fehlt() { echo "$1 braucht einen Wert (--help zeigt alle Optionen)" >&2; exit 2; }
while [ $# -gt 0 ]; do
    case "$1" in
        --offen) [ $# -ge 2 ] || fehlt "$1"; FUER_ALLE+=("$2"); shift 2 ;;
        --heimnetz) [ $# -ge 2 ] || fehlt "$1"; NUR_HEIMNETZ+=("$2"); shift 2 ;;
        --neustart) [ $# -ge 2 ] || fehlt "$1"; NEUSTART="$2"; shift 2 ;;
        --ohne-neustart) NEUSTART=""; shift ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "Unbekannte Option: $1 (--help zeigt alle)" >&2; exit 2 ;;
    esac
done
INTERAKTIV=0
[ -t 0 ] && INTERAKTIV=1
OFFEN=()  # was am Ende noch zu tun bleibt

# Die Auswertung der Regeln braucht nur die Standardbibliothek; ohne venv tut es das Python des Systems.
helfer() {
    if [ -x "$PY" ]; then
        "$PY" -m alfred.absichern "$@"
    else
        PYTHONPATH="$PROJEKT/src" python3 -m alfred.absichern "$@"
    fi
}

if [ ${#FUER_ALLE[@]} -gt 0 ] || [ ${#NUR_HEIMNETZ[@]} -gt 0 ]; then
    helfer port-pruefen "${FUER_ALLE[@]}" "${NUR_HEIMNETZ[@]}"
fi
if [ -n "$NEUSTART" ] && ! [[ "$NEUSTART" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
    echo "--neustart braucht eine Uhrzeit wie 04:00, nicht $NEUSTART" >&2
    exit 2
fi

echo "== Rechner absichern ($(hostname))"
if ! sudo -n true 2>/dev/null; then
    echo "Firewall, Updates und SSH brauchen sudo; bitte das Passwort eingeben."
    sudo -v
fi

# Heimnetz: das IPv4-Netz der Schnittstelle mit der Standardroute und ihre privaten IPv6-Netze
# (fd00::/8, vom Router vergeben). Das öffentliche IPv6-Präfix wechselt beim Anbieter und gilt
# deshalb nicht als Heimnetz.
SCHNITTSTELLE="$(ip -4 route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')"
if [ -z "$SCHNITTSTELLE" ]; then
    echo "Keine Standardroute gefunden; ohne sie erkennt das Skript das Heimnetz nicht." >&2
    exit 1
fi
NETZ4="$(ip -4 route show dev "$SCHNITTSTELLE" scope link proto kernel | awk 'NR == 1 {print $1}')"
mapfile -t NETZE6 < <(ip -6 route show dev "$SCHNITTSTELLE" | awk '$1 ~ /^fd/ {print $1}' | sort -u)
if [ -z "$NETZ4" ]; then
    echo "Kein IPv4-Netz an $SCHNITTSTELLE gefunden." >&2
    exit 1
fi
HEIMNETZE=("$NETZ4" "${NETZE6[@]}")
SSH_PORT="$(sudo sshd -T 2>/dev/null | awk '$1 == "port" {print $2; exit}' || true)"
SSH_PORT="${SSH_PORT:-22}"
echo "Heimnetz an $SCHNITTSTELLE: ${HEIMNETZE[*]}; SSH-Port $SSH_PORT"

# ufw allow from NETZ to any port P proto T für jedes Heimnetz; ohne /tcp oder /udp gilt beides.
heimnetz_erlauben() {
    local port="${1%%/*}" proto=() netz
    if [[ "$1" == */* ]]; then
        proto=(proto "${1#*/}")
    fi
    for netz in "${HEIMNETZE[@]}"; do
        sudo ufw allow from "$netz" to any port "$port" "${proto[@]}" comment "$2" >/dev/null
    done
}

ufw_an() { sudo env LC_ALL=C ufw status | grep -q '^Status: active'; }

if ! command -v ufw >/dev/null; then
    schritt "Firewall installieren"
    sudo apt-get install -y -qq ufw
fi

schritt "Firewall vorher"
sudo env LC_ALL=C ufw status numbered

schritt "Firewall: Regeln für Heimnetz, Tailnet und offene Ports"
sudo ufw default deny incoming >/dev/null
heimnetz_erlauben "$SSH_PORT/tcp" "SSH aus dem Heimnetz"
sudo ufw allow in on tailscale0 comment 'Tailscale: eigene Geräte' >/dev/null
sudo ufw allow 41641/udp comment 'Tailscale: direkte Verbindungen' >/dev/null
for port in "${NUR_HEIMNETZ[@]}"; do
    heimnetz_erlauben "$port" "nur Heimnetz (absichern.sh)"
done
for port in "${FUER_ALLE[@]}"; do
    sudo ufw allow "$port" comment 'für alle (absichern.sh)' >/dev/null
done
echo "SSH ($SSH_PORT) aus dem Heimnetz, alles über tailscale0${NUR_HEIMNETZ[*]:+, nur Heimnetz: ${NUR_HEIMNETZ[*]}}${FUER_ALLE[*]:+, für alle: ${FUER_ALLE[*]}}"

if ! ufw_an; then
    if [ "$INTERAKTIV" = 1 ] && ja "ufw ist aus. Jetzt einschalten? Eingehend gilt dann nur noch, was oben steht."; then
        sudo ufw --force enable
    else
        OFFEN+=("Firewall einschalten: sudo ufw enable")
    fi
fi

# Wer gerade per SSH von außerhalb von Heimnetz und Tailnet verbunden ist, sperrte sich mit dem
# Entfernen der SSH-Regeln aus; dann bleiben sie.
SUCHE=("${NUR_HEIMNETZ[@]}")
ABSENDER="${SSH_CLIENT:-}"
ABSENDER="${ABSENDER%% *}"
if [ -z "$ABSENDER" ] || helfer im-netz "$ABSENDER" "${HEIMNETZE[@]}"; then
    SUCHE=("$SSH_PORT/tcp" "${SUCHE[@]}")
else
    echo "Du bist von $ABSENDER verbunden, außerhalb von Heimnetz und Tailnet: Die SSH-Regeln bleiben." >&2
    OFFEN+=("SSH auf Heimnetz und Tailnet beschränken: das Skript von dort aus erneut starten")
fi

schritt "Regeln, die SSH oder Heimnetz-Ports für jeden Absender öffnen"
BREIT=()
if ufw_an && [ ${#SUCHE[@]} -gt 0 ]; then
    STATUS="$(sudo env LC_ALL=C ufw status numbered)"
    GEFUNDEN="$(printf '%s\n' "$STATUS" | helfer breite-regeln "${SUCHE[@]}")"
    [ -n "$GEFUNDEN" ] && mapfile -t BREIT <<<"$GEFUNDEN"
fi
if [ ${#BREIT[@]} -eq 0 ]; then
    echo "keine"
else
    printf '  %s\n' "${BREIT[@]#*$'\t'}"
    if [ "$INTERAKTIV" = 1 ] && ja "Diese Regeln entfernen? Heimnetz und Tailnet bleiben erlaubt."; then
        # Höchste Nummer zuerst (so liefert sie der Helfer), damit die übrigen Nummern stimmen.
        for zeile in "${BREIT[@]}"; do
            sudo ufw --force delete "${zeile%%$'\t'*}" >/dev/null
        done
        echo "entfernt"
    else
        OFFEN+=("Regeln für jeden Absender entfernen: das Skript im Terminal erneut starten und mit j antworten")
    fi
fi

# Ubuntu installiert Sicherheitsupdates täglich von selbst; Kernel und libc wirken aber erst nach
# einem Neustart. Die Datei steht nach 50unattended-upgrades und gilt deshalb zuletzt.
UPDATES=/etc/apt/apt.conf.d/52absichern
if [ -n "$NEUSTART" ]; then
    schritt "Sicherheitsupdates täglich, Neustart um $NEUSTART, wenn eins ihn braucht"
else
    schritt "Sicherheitsupdates täglich, ohne Neustart"
fi
dpkg -s unattended-upgrades >/dev/null 2>&1 || sudo apt-get install -y -qq unattended-upgrades
{
    echo "// deploy/absichern.sh: Sicherheitsupdates täglich; nach Kernel- und libc-Updates nachts neu starten."
    echo 'APT::Periodic::Update-Package-Lists "1";'
    echo 'APT::Periodic::Unattended-Upgrade "1";'
    if [ -n "$NEUSTART" ]; then
        echo 'Unattended-Upgrade::Automatic-Reboot "true";'
        echo "Unattended-Upgrade::Automatic-Reboot-Time \"$NEUSTART\";"
    fi
} | sudo tee "$UPDATES" >/dev/null
echo "eingestellt in $UPDATES"

# sshd nimmt den ersten Wert, den es liest, und liest sshd_config.d/ vor sshd_config; 10- steht
# vor 50-cloud-init.conf, das bei manchen Installationen Passwörter wieder erlaubt.
SSH_DATEI=/etc/ssh/sshd_config.d/10-absichern.conf
schritt "SSH: nur mit Schlüssel, nie als root, ohne X11"
if [ -s "$HOME/.ssh/authorized_keys" ]; then
    {
        echo "# deploy/absichern.sh"
        echo "PasswordAuthentication no"
        echo "KbdInteractiveAuthentication no"
        echo "PermitRootLogin no"
        echo "X11Forwarding no"
    } | sudo tee "$SSH_DATEI" >/dev/null
    sudo mkdir -p /run/sshd  # sshd -t braucht das Verzeichnis, auch wenn noch keine Anmeldung lief
    if sudo sshd -t; then
        sudo systemctl try-reload-or-restart ssh
        echo "eingestellt in $SSH_DATEI"
    else
        sudo rm -f "$SSH_DATEI"
        echo "sshd lehnt die Einstellung ab; es bleibt, wie es war." >&2
    fi
else
    echo "Kein Schlüssel in ~/.ssh/authorized_keys: Passwörter bleiben erlaubt, sonst sperrst du dich aus." >&2
    OFFEN+=("SSH-Schlüssel hinterlegen (ssh-copy-id vom eigenen Rechner), dann das Skript erneut starten")
fi

if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
    schritt "Tailscale aktualisiert sich selbst"
    # Ubuntus automatische Updates kennen die Paketquelle von Tailscale nicht.
    sudo tailscale set --auto-update || echo "Diese Tailscale-Version aktualisiert sich nicht selbst." >&2
    if [ -x "$PY" ]; then
        ABLAUF="$(tailscale status --json | "$PY" -m alfred.einrichtung tailscale-ablauf || true)"
        if [ -n "$ABLAUF" ]; then
            OFFEN+=("Tailscale-Verwaltung (https://login.tailscale.com/admin/machines): beim Server „Disable key expiry“, sonst meldet er sich am $ABLAUF ab")
        fi
    fi
fi

schritt "Firewall jetzt"
sudo env LC_ALL=C ufw status verbose

if command -v docker >/dev/null && [ -n "$($DOCKER ps -q 2>/dev/null)" ]; then
    schritt "Docker-Ports: im ganzen Heimnetz erreichbar (IPv4 an ufw vorbei); aus dem Internet nur mit Freigabe im Router"
    $DOCKER ps --format '{{.Names}}|{{.Ports}}' | awk -F'|' '{
        n = split($2, teile, ", "); liste = ""
        for (i = 1; i <= n; i++) if (teile[i] ~ /^0\.0\.0\.0:/) {
            sub(/^0\.0\.0\.0:/, "", teile[i]); split(teile[i], t, "->")
            proto = t[2]; sub(/^[^\/]*/, "", proto)
            liste = liste (liste ? ", " : "") t[1] proto
        }
        if (liste) print "  " $1 ": " liste
    }'
fi

if [ -f /var/run/reboot-required ]; then
    OFFEN+=("Neustart für schon installierte Updates: sudo reboot (Dienste und Container starten von selbst wieder)")
fi
FREIGABE="${FUER_ALLE[*]:-keine}"
OFFEN+=("Router (FRITZ!Box: Internet → Freigaben): für diesen Rechner nur freigeben, was von außen erreichbar sein soll (hier: $FREIGABE); nie 22, 53, 80 oder 443, kein „Exposed Host“, und „Selbstständige Portfreigaben“ (UPnP) aus")

echo
echo "== Fertig. Noch zu tun:"
for punkt in "${OFFEN[@]}"; do
    echo "  - $punkt"
done
