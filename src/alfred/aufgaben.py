"""Aufgaben: eine schlichte To-do-Liste per Stimme, als YAML im Gedächtnisordner.

Offene Aufgaben stehen im Systemprompt, damit "Was steht an?" ohne Werkzeugaufruf geht.
Erledigte bleiben mit Datum in der Datei, damit "Was habe ich diese Woche geschafft?" später
möglich ist, und werden nach einiger Zeit ausgedünnt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from alfred.dateien import Dateibestand

ERLEDIGTE_BEHALTEN = timedelta(days=30)
MAX_IM_PROMPT = 12


@dataclass
class Aufgabe:
    nummer: int
    text: str
    angelegt: str  # ISO-Datum
    faellig: str | None = None  # ISO-Datum, optional
    erledigt: str | None = None  # ISO-Datum, sobald abgehakt

    def ansage(self) -> str:
        zusatz = f", fällig am {datetime.fromisoformat(self.faellig):%d.%m.}" if self.faellig else ""
        return f"{self.nummer}: {self.text}{zusatz}"


class Aufgaben(Dateibestand):
    def __init__(self, pfad: Path, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._eintraege: list[Aufgabe] = []
        super().__init__(pfad)

    def _laden(self, daten) -> None:
        self._eintraege = [Aufgabe(**e) for e in (daten or [])]

    def _speichern(self) -> None:
        grenze = (self._clock() - ERLEDIGTE_BEHALTEN).date().isoformat()
        self._eintraege = [a for a in self._eintraege if not a.erledigt or a.erledigt >= grenze]
        self._schreiben(
            "# Alfreds Aufgabenliste. Von Hand änderbar; erledigte verschwinden nach 30 Tagen.\n"
            + yaml.safe_dump([asdict(a) for a in self._eintraege], allow_unicode=True, sort_keys=False)
        )

    def anlegen(self, text: str, faellig: str = "") -> Aufgabe:
        self._auffrischen()
        if not text.strip():
            raise ValueError("Die Aufgabe darf nicht leer sein.")
        if faellig:
            datetime.strptime(faellig, "%Y-%m-%d")  # prüft das Format
        nummer = max((a.nummer for a in self._eintraege), default=0) + 1
        aufgabe = Aufgabe(nummer, text.strip(), self._clock().date().isoformat(), faellig or None)
        self._eintraege.append(aufgabe)
        self._speichern()
        return aufgabe

    def offene(self) -> list[Aufgabe]:
        self._auffrischen()
        offene = [a for a in self._eintraege if not a.erledigt]
        # Mit Fälligkeit zuerst, die nächste zuoberst; dann der Rest in Reihenfolge der Anlage.
        return sorted(offene, key=lambda a: (a.faellig is None, a.faellig or "", a.nummer))

    def erledigen(self, nummer: int) -> Aufgabe | None:
        self._auffrischen()
        for a in self._eintraege:
            if a.nummer == nummer and not a.erledigt:
                a.erledigt = self._clock().date().isoformat()
                self._speichern()
                return a
        return None

    def finden(self, text: str) -> list[Aufgabe]:
        """Offene Aufgaben, deren Text die Wörter enthält; für "hak Wäsche ab" ohne Nummer."""
        woerter = [w for w in text.casefold().split() if len(w) > 2]
        return [a for a in self.offene() if woerter and all(w in a.text.casefold() for w in woerter)]

    def fuer_prompt(self) -> str:
        offene = self.offene()
        if not offene:
            return ""
        zeilen = [a.ansage() for a in offene[:MAX_IM_PROMPT]]
        rest = len(offene) - len(zeilen)
        if rest > 0:
            zeilen.append(f"und {rest} weitere")
        return "Offene Aufgaben (Nummer: Text):\n" + "\n".join(f"- {z}" for z in zeilen)
