"""Hilfen für deploy/absichern.sh: Firewall-Regeln finden, die Ports für jeden Absender öffnen.

ufw lässt seine Regeldateien nur root lesen; das Skript reicht deshalb die Ausgabe von
`ufw status numbered` herein (mit LC_ALL=C, sonst übersetzt ufw sie). Hier steht nur die
Auswertung, damit sie sich testen lässt; Regeln ändert allein das Skript, und erst nach Rückfrage.
Nur Standardbibliothek: Das Skript kann das Modul auch mit dem Python des Systems starten.

    sudo env LC_ALL=C ufw status numbered | python -m alfred.absichern breite-regeln 22/tcp 80/tcp
    python -m alfred.absichern im-netz 192.168.1.25 192.168.1.0/24 100.64.0.0/10
    python -m alfred.absichern port-pruefen 25565:25566/tcp 5520/udp
    docker ps -aq | xargs docker inspect | python -m alfred.absichern docker-belegt 443/tcp
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from dataclasses import dataclass

# Anwendungsprofile aus /etc/ufw/applications.d, die ufw in der Statusliste mit Namen zeigt.
# Ein Profil, das hier fehlt, fasst das Skript nicht an.
PROFILE = {
    "OpenSSH": "22/tcp",
    "DNS": "53",
    "WWW": "80/tcp",
    "WWW Secure": "443/tcp",
    "WWW Full": "80,443/tcp",
    "Apache": "80/tcp",
    "Apache Secure": "443/tcp",
    "Apache Full": "80,443/tcp",
    "Nginx HTTP": "80/tcp",
    "Nginx HTTPS": "443/tcp",
    "Nginx Full": "80,443/tcp",
}

# Adressbereiche von Tailscale: Wer von dort kommt, ist ein eigenes Gerät im Tailnet.
TAILSCALE_NETZE = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")

# [ 3] 22/tcp (v6)                ALLOW IN    Anywhere (v6)              # Kommentar
# Die Spalten sind nicht verlässlich durch zwei Leerzeichen getrennt (lange Ziele), deshalb
# dient die Aktion als Anker.
ZEILE = re.compile(
    r"^\[\s*(?P<nummer>\d+)\]\s+(?P<ziel>.+?)\s+(?P<aktion>ALLOW|LIMIT|DENY|REJECT)"
    r"(?:\s+(?P<richtung>IN|OUT|FWD))?\s+(?P<quelle>.+?)\s*(?:#.*)?$"
)
PORTANGABE = re.compile(r"[\d,:]+(?:/(?:tcp|udp))?")


@dataclass(frozen=True)
class Ports:
    """Ports wie ufw sie schreibt: 22, 22/tcp, 80,443/tcp, 25565:25566/tcp."""

    bereiche: tuple[tuple[int, int], ...]
    protokoll: str | None = None  # tcp oder udp; None gilt für beide

    @classmethod
    def lesen(cls, text: str) -> Ports:
        teil, schraegstrich, protokoll = text.strip().lower().partition("/")
        if schraegstrich and protokoll not in ("tcp", "udp"):
            raise ValueError(f"Protokoll {protokoll!r} in {text!r}: nur tcp oder udp")
        bereiche = []
        for stueck in teil.split(","):
            von, _, bis = stueck.partition(":")
            if not von.isdigit() or (bis and not bis.isdigit()):
                raise ValueError(f"Keine Portangabe: {text!r}")
            bereich = (int(von), int(bis or von))
            if not 1 <= bereich[0] <= bereich[1] <= 65535:
                raise ValueError(f"Port außerhalb von 1 bis 65535 oder Bereich verkehrt: {text!r}")
            bereiche.append(bereich)
        return cls(tuple(bereiche), protokoll or None)

    def ueberschneidet(self, andere: Ports) -> bool:
        if self.protokoll and andere.protokoll and self.protokoll != andere.protokoll:
            return False
        return any(a <= d and c <= b for a, b in self.bereiche for c, d in andere.bereiche)


ALLE_PORTS = Ports(((1, 65535),))


@dataclass(frozen=True)
class Regel:
    nummer: int
    zeile: str
    ports: Ports | None  # None: unbekanntes Anwendungsprofil, nicht anfassen
    fuer_alle: bool  # jeder Absender, nicht nur das Heimnetz oder Tailscale


def port_pruefen(text: str) -> Ports:
    """Prüft eine Portangabe für das Skript; mehrere Ports oder Bereiche brauchen ein Protokoll (wie bei ufw)."""
    ports = Ports.lesen(text)
    if ports.protokoll is None and (len(ports.bereiche) > 1 or ports.bereiche[0][0] != ports.bereiche[0][1]):
        raise ValueError(f"Mehrere Ports oder ein Bereich brauchen /tcp oder /udp: {text!r}")
    return ports


def _ports_des_ziels(ziel: str) -> Ports | None:
    if ziel in PROFILE:
        return Ports.lesen(PROFILE[ziel])
    letztes = ziel.split()[-1]
    if PORTANGABE.fullmatch(letztes):
        return Ports.lesen(letztes)
    if letztes == "Anywhere" or _ist_adresse(letztes):
        return ALLE_PORTS  # ohne Port: alle Ports (zu dieser Adresse)
    return None


def _ist_adresse(text: str) -> bool:
    try:
        ipaddress.ip_network(text, strict=False)
    except ValueError:
        return False
    return True


def regeln(status: str) -> list[Regel]:
    """Die eingehenden Erlaubnis-Regeln aus `ufw status numbered` (LC_ALL=C)."""
    gefunden = []
    for zeile in status.splitlines():
        treffer = ZEILE.match(zeile.strip())
        if not treffer or treffer["aktion"] not in ("ALLOW", "LIMIT") or treffer["richtung"] not in (None, "IN"):
            continue
        ziel, _, schnittstelle = treffer["ziel"].replace(" (v6)", "").partition(" on ")
        quelle = treffer["quelle"].replace(" (v6)", "").strip()
        # Über tailscale0 kommen nur eigene Geräte; jede andere Schnittstelle sieht auch das Internet.
        fuer_alle = quelle == "Anywhere" and schnittstelle.strip() != "tailscale0"
        gefunden.append(Regel(int(treffer["nummer"]), zeile.strip(), _ports_des_ziels(ziel.strip()), fuer_alle))
    return gefunden


def breite_regeln(status: str, ports: list[str]) -> list[Regel]:
    """Regeln, die einen der Ports für jeden Absender öffnen; höchste Nummer zuerst, so löscht ufw sie der Reihe nach."""
    gesucht = [Ports.lesen(p) for p in ports]
    treffer = [
        regel for regel in regeln(status)
        if regel.fuer_alle and regel.ports is not None and any(regel.ports.ueberschneidet(g) for g in gesucht)
    ]
    return sorted(treffer, key=lambda regel: regel.nummer, reverse=True)


def docker_belegt(inspect_json: str, port: str) -> list[str]:
    """Container, die PORT auf allen Adressen veröffentlichen, auch angehaltene (`docker inspect` als JSON).

    tailscale serve belegt 443 auf den Tailscale-Adressen des Rechners. Will ein Container 443 auf
    allen Adressen (Pi-hole), startet nach einem Neustart der nicht, der später kommt; am 16.10.2026
    war das Pi-hole. An eine bestimmte Adresse gebunden (192.168.1.99:443) vertragen sich beide.
    """
    gesucht = Ports.lesen(port)
    namen = set()
    for container in json.loads(inspect_json or "[]"):
        bindungen = (container.get("HostConfig") or {}).get("PortBindings") or {}
        for ziel, liste in bindungen.items():
            protokoll = ziel.partition("/")[2] or "tcp"
            if protokoll not in ("tcp", "udp"):
                continue
            for bindung in liste or []:
                host_port = (bindung.get("HostPort") or "").replace("-", ":")
                if host_port and bindung.get("HostIp", "") in ("", "0.0.0.0", "::"):
                    if Ports.lesen(f"{host_port}/{protokoll}").ueberschneidet(gesucht):
                        namen.add(str(container.get("Name", "?")).lstrip("/"))
    return sorted(namen)


def im_netz(adresse: str, netze: list[str]) -> bool:
    """True, wenn die Adresse (etwa der SSH-Absender) in einem der Netze liegt."""
    ip = ipaddress.ip_address(adresse.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in ipaddress.ip_network(netz, strict=False) for netz in netze if netz)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hilfen für deploy/absichern.sh")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    breit = befehle.add_parser("breite-regeln", help="Regeln aus `ufw status numbered` (Standardeingabe), die PORT für alle öffnen")
    breit.add_argument("ports", nargs="+", metavar="PORT")
    netz = befehle.add_parser("im-netz", help="Rückgabewert 0, wenn ADRESSE in einem der Netze liegt (Tailscale zählt immer)")
    netz.add_argument("adresse")
    netz.add_argument("netze", nargs="*")
    pruefen = befehle.add_parser("port-pruefen", help="Portangaben prüfen, etwa 25565/tcp oder 25565:25566/tcp")
    pruefen.add_argument("ports", nargs="+", metavar="PORT")
    docker = befehle.add_parser("docker-belegt", help="Container aus `docker inspect` (Standardeingabe), die PORT auf allen Adressen wollen")
    docker.add_argument("port", metavar="PORT")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.befehl == "breite-regeln":
        for regel in breite_regeln(sys.stdin.read(), args.ports):
            print(f"{regel.nummer}\t{regel.zeile}")
    elif args.befehl == "im-netz":
        try:
            drin = im_netz(args.adresse, [*args.netze, *TAILSCALE_NETZE])
        except ValueError:
            drin = False  # keine lesbare Adresse: lieber als fremd behandeln
        sys.exit(0 if drin else 1)
    elif args.befehl == "port-pruefen":
        for text in args.ports:
            try:
                port_pruefen(text)
            except ValueError as fehler:
                sys.exit(str(fehler))
    elif args.befehl == "docker-belegt":
        for name in docker_belegt(sys.stdin.read(), args.port):
            print(name)


if __name__ == "__main__":
    main()
