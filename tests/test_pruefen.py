"""Startprüfung: Teile, die ohne Netz und Geräte laufen."""

import json
import subprocess
from datetime import datetime, timedelta, timezone

from alfred import pruefen
from alfred.pruefen import pruefe_tailscale, pruefe_zeitzone


def test_zeitzone_berlin_ist_in_ordnung_utc_eine_warnung():
    berlin = pruefe_zeitzone(datetime(2026, 10, 5, 21, 31, tzinfo=timezone(timedelta(hours=2), "CEST")))
    assert berlin.stufe == "ok" and berlin.text == "CEST, 21:31 Uhr"
    # Der Mini-PC lief nach der Installation auf UTC; Alfred sagte "neunzehn Uhr" statt einundzwanzig.
    utc = pruefe_zeitzone(datetime(2026, 10, 5, 19, 31, tzinfo=timezone.utc))
    assert utc.stufe == "warnung" and "timedatectl" in utc.text and utc.text.startswith("UTC, 19:31 Uhr")


def test_tailscale_warnt_vor_dem_schluesselablauf(monkeypatch):
    # Mit Ablauf meldet sich der Server nach 180 Tagen ab; vom Handy ist Alfred dann weg.
    selbst = {"DNSName": "mini-pc.tail1234.ts.net.", "KeyExpiry": "2027-04-14T06:30:15Z"}
    antwort = {"BackendState": "Running", "Self": selbst}
    monkeypatch.setattr(pruefen.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(pruefen.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(antwort), ""))
    befund = pruefe_tailscale()
    assert befund.stufe == "warnung" and "14.04.2027" in befund.text and "Disable key expiry" in befund.text

    del selbst["KeyExpiry"]  # in der Verwaltung abgeschaltet: Tailscale lässt das Feld weg
    befund = pruefe_tailscale()
    assert befund.stufe == "ok" and befund.text == "verbunden als mini-pc.tail1234.ts.net"
