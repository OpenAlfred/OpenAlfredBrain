"""Hilfen für deploy/absichern.sh: offene Firewall-Regeln finden, Portangaben und Absender prüfen."""

import json

import pytest

from alfred.absichern import TAILSCALE_NETZE, Ports, breite_regeln, docker_belegt, im_netz, port_pruefen, regeln

# So zeigt `LC_ALL=C ufw status numbered` (ufw 0.36) die Regeln; lange Ziele schieben die Spalten.
STATUS = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere
[ 2] 80/tcp                     ALLOW IN    Anywhere                   # Pi-hole
[ 3] 443                        ALLOW IN    Anywhere
[ 4] 25565                      ALLOW IN    Anywhere
[ 5] 5520/udp                   ALLOW IN    Anywhere
[ 6] Anywhere on tailscale0     ALLOW IN    Anywhere                   # Tailscale
[ 7] 22/tcp                     ALLOW IN    192.168.1.0/24           # SSH aus dem Heimnetz
[ 8] OpenSSH                    LIMIT IN    Anywhere
[ 9] Crafty                     ALLOW IN    Anywhere
[10] 8443/tcp                   DENY IN     Anywhere
[11] 53                         ALLOW OUT   Anywhere
[12] 22/tcp on enp0s31f6        ALLOW IN    Anywhere
[13] 22/tcp (v6)                ALLOW IN    Anywhere (v6)
[14] 80/tcp (v6)                ALLOW IN    Anywhere (v6)              # Pi-hole
[15] 25565 (v6)                 ALLOW IN    Anywhere (v6)
[16] Anywhere (v6) on tailscale0 ALLOW IN    Anywhere (v6)             # Tailscale
[17] 22/tcp (v6)                ALLOW IN    fd6f:ecdc:1d64::/64
[18] 25500:25600/tcp            ALLOW IN    Anywhere
"""


def nummern(gefunden):
    return [regel.nummer for regel in gefunden]


def test_breite_regeln_fuer_ssh_und_pihole():
    # Gefunden wird, was 22, 80 oder 443 für jeden Absender öffnet, auch über das Profil OpenSSH
    # oder nur an der Netzwerkkarte; Heimnetz, Tailscale, Sperren, ausgehende und unbekannte
    # Profile bleiben. Die höchste Nummer kommt zuerst, damit ufw der Reihe nach löschen kann.
    gefunden = breite_regeln(STATUS, ["22/tcp", "80/tcp", "443/tcp"])
    assert nummern(gefunden) == [14, 13, 12, 8, 3, 2, 1]
    assert gefunden[-1].zeile.startswith("[ 1] 22/tcp")


def test_spieleports_auch_im_bereich():
    assert nummern(breite_regeln(STATUS, ["25565/tcp"])) == [18, 15, 4]
    assert nummern(breite_regeln(STATUS, ["5520/tcp"])) == [], "die Regel gilt nur für udp"


def test_alles_erlaubt_zaehlt_fuer_jeden_port():
    status = "[ 1] Anywhere                   ALLOW IN    Anywhere\n[ 2] 192.168.1.99             ALLOW IN    Anywhere\n"
    assert nummern(breite_regeln(status, ["22/tcp"])) == [2, 1]


def test_regeln_lesen_tailscale_und_unbekanntes_profil():
    nach_nummer = {regel.nummer: regel for regel in regeln(STATUS)}
    assert not nach_nummer[6].fuer_alle and not nach_nummer[16].fuer_alle, "tailscale0 sieht nur eigene Geräte"
    assert not nach_nummer[17].fuer_alle
    assert nach_nummer[9].ports is None, "ein unbekanntes Profil fasst das Skript nicht an"
    assert 10 not in nach_nummer and 11 not in nach_nummer, "Sperren und ausgehende Regeln zählen nicht"


def test_ports_ueberschneiden_sich():
    assert Ports.lesen("53").ueberschneidet(Ports.lesen("53/udp"))
    assert not Ports.lesen("22/tcp").ueberschneidet(Ports.lesen("22/udp"))
    assert Ports.lesen("25500:25600/tcp").ueberschneidet(Ports.lesen("25565/tcp"))
    assert Ports.lesen("80,443/tcp").ueberschneidet(Ports.lesen("443"))
    assert not Ports.lesen("80/tcp").ueberschneidet(Ports.lesen("8080/tcp"))


@pytest.mark.parametrize("text", ["22", "22/tcp", "25565:25566/tcp", "80,443/tcp", "5520/udp", "53/UDP"])
def test_port_pruefen_nimmt_was_ufw_nimmt(text):
    port_pruefen(text)


@pytest.mark.parametrize("text", ["0", "70000", "abc", "80,443", "25565:25566", "22/icmp", "30:20/tcp", "", "22/"])
def test_port_pruefen_weist_ab(text):
    with pytest.raises(ValueError):
        port_pruefen(text)


def test_docker_belegt_findet_443_auf_allen_adressen():
    # Am 16.10.2026 hielt tailscale serve nach dem Neustart 443 auf den Tailscale-Adressen, und
    # Pi-hole ("443:443/tcp", also alle Adressen) startete nicht mehr. Auch angehaltene zählen.
    def container(name, bindungen):
        return {"Name": f"/{name}", "HostConfig": {"PortBindings": bindungen}}

    inspect = json.dumps([
        container("pihole", {"53/udp": [{"HostIp": "", "HostPort": "53"}], "443/tcp": [{"HostIp": "", "HostPort": "443"}]}),
        container("pihole-heimnetz", {"443/tcp": [{"HostIp": "192.168.1.99", "HostPort": "443"}]}),
        container("nur-ipv6", {"443/tcp": [{"HostIp": "::", "HostPort": "443"}]}),
        container("searxng", {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8888"}]}),
        container("crafty", {"25500/tcp": [{"HostIp": "0.0.0.0", "HostPort": "25500-25600"}], "443/udp": [{"HostIp": "", "HostPort": "443"}]}),
        container("ohne-ports", None),
    ])
    assert docker_belegt(inspect, "443/tcp") == ["nur-ipv6", "pihole"]
    assert docker_belegt(inspect, "25565/tcp") == ["crafty"]
    assert docker_belegt("", "443/tcp") == []


def test_im_netz():
    lan = ["192.168.1.0/24", "fd6f:ecdc:1d64::/64"]
    assert im_netz("192.168.1.25", lan)
    assert not im_netz("192.168.2.25", lan)
    assert im_netz("fd6f:ecdc:1d64:0:1::5", lan)
    assert im_netz("::ffff:192.168.1.25", lan), "IPv4 in IPv6-Schreibweise"
    assert im_netz("fe80::1%enp0s31f6", ["fe80::/10"])
    assert not im_netz("100.101.102.103", lan)
    assert im_netz("100.101.102.103", list(TAILSCALE_NETZE))
    assert im_netz("192.168.1.25", ["", "192.168.1.0/24"]), "leere Netze (nicht erkannt) stören nicht"
