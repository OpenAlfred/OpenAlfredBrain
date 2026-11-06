"""Freigaben vom Handy: Alfred fragt per Push, du tippst Ja oder Nein.

Die wartende Aktion lebt im Sprach-Prozess. Die App kann sie nicht aufrufen, deshalb läuft die
Entscheidung über eine Datei im Gedächtnisordner: Der Sprach-Prozess schreibt die offene
Freigabe mit Kennung und Geheimnis hinein und schickt einen Push mit zwei Knöpfen, deren
Adressen auf die App zeigen. Die App trägt die Entscheidung in die Datei ein, der
Sprach-Prozess sieht sie beim nächsten Blick (jede Sekunde), führt aus oder verwirft, und
sagt das Ergebnis an. Ein gesprochenes Ja im Raum geht weiter wie bisher.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

GUELTIG_MINUTEN = 10


class Freigabedatei:
    def __init__(self, pfad: Path):
        self._pfad = pfad

    def lesen(self) -> dict | None:
        if not self._pfad.exists():
            return None
        try:
            return json.loads(self._pfad.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def _schreiben(self, daten: dict | None) -> None:
        if daten is None:
            self._pfad.unlink(missing_ok=True)
            return
        self._pfad.parent.mkdir(parents=True, exist_ok=True)
        self._pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")

    def anlegen(self, beschreibung: str, jetzt: datetime) -> dict:
        """Neue offene Freigabe; eine ältere wird ersetzt."""
        daten = {
            "id": secrets.token_hex(8),
            "geheimnis": secrets.token_hex(16),
            "beschreibung": beschreibung,
            "zeit": jetzt.isoformat(timespec="seconds"),
            "status": "offen",
        }
        self._schreiben(daten)
        return daten

    def entscheiden(self, kennung: str, geheimnis: str, entscheidung: str) -> dict | None:
        """Die App trägt ja oder nein ein; nur mit passender Kennung und Geheimnis."""
        daten = self.lesen()
        if not daten or daten["id"] != kennung or not secrets.compare_digest(daten["geheimnis"], geheimnis):
            return None
        if daten["status"] != "offen" or entscheidung not in ("ja", "nein"):
            return None
        daten["status"] = entscheidung
        self._schreiben(daten)
        return daten

    def entscheidung_abholen(self, jetzt: datetime) -> dict | None:
        """Für den Sprach-Prozess: eine getroffene Entscheidung, danach ist die Datei weg."""
        daten = self.lesen()
        if not daten:
            return None
        alter = (jetzt - datetime.fromisoformat(daten["zeit"])).total_seconds() / 60
        if daten["status"] == "offen":
            if alter > GUELTIG_MINUTEN:
                self._schreiben(None)
            return None
        self._schreiben(None)
        return daten

    def schliessen(self) -> None:
        self._schreiben(None)
