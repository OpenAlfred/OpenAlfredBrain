# Gemeinsame Teile von einrichten.sh und aktualisieren.sh; wird eingebunden, nicht ausgeführt.

PROJEKT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NUTZER="$(id -un)"
PY="$PROJEKT/.venv/bin/python"
DIENSTE=(alfred-raum@.service alfred-whatsapp.service alfred-browser.service alfred-web.service alfred-sicherung.service alfred-sicherung.timer)
SEARXNG_ORDNER="$HOME/searxng"
SEARXNG_ADRESSE="http://127.0.0.1:8888"
# Docker ohne sudo, wenn der Nutzer in der Gruppe docker ist; sonst mit.
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

schritt() { echo; echo "== $*"; }

ja() {
    local antwort
    read -rp "$1 [j/N] " antwort
    [[ "$antwort" =~ ^[jJyY] ]]
}

# systemd-Dienste mit eingesetztem Nutzer und Pfad installieren (nicht starten).
dienste_installieren() {
    local unit
    for unit in "${DIENSTE[@]}"; do
        sed -e "s|__USER__|$NUTZER|g" -e "s|__DIR__|$PROJEKT|g" "$PROJEKT/deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
    done
    sudo systemctl daemon-reload
}

# SearXNG im Container, nur auf 127.0.0.1 erreichbar; startet mit Docker von selbst wieder.
searxng_container() {
    $DOCKER run -d --name searxng --restart unless-stopped -p 127.0.0.1:8888:8080 \
        -v "$SEARXNG_ORDNER:/etc/searxng" searxng/searxng >/dev/null
}

searxng_starten() {
    mkdir -p "$SEARXNG_ORDNER"
    if [ ! -f "$SEARXNG_ORDNER/settings.yml" ]; then
        # json muss erlaubt sein, sonst antwortet SearXNG Alfred mit 403.
        printf 'use_default_settings: true\nserver:\n  secret_key: "%s"\nsearch:\n  formats: [html, json]\n' \
            "$(openssl rand -hex 32)" >"$SEARXNG_ORDNER/settings.yml"
    fi
    if $DOCKER ps -a --format '{{.Names}}' | grep -qx searxng; then
        $DOCKER start searxng >/dev/null
    else
        searxng_container
    fi
}

# Wartet bis zu einer Minute, bis SearXNG Treffer als JSON liefert; Rückgabewert 0, wenn ja.
searxng_antwortet() {
    local versuch
    for versuch in $(seq 30); do
        if curl -fsS "$SEARXNG_ADRESSE/search?q=wetter&format=json" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}
