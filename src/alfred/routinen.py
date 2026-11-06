"""Routinen: Aufträge, die Alfred sich merkt und zu festen Zeiten von selbst ausführt.

Eine Routine ist ein gesprochener Auftrag ("Nenn mir das Wetter und meine Termine") mit
Zeitplan. Zur fälligen Zeit bekommt der Sprecher den Auftrag als Ereignis und erledigt ihn mit
seinen Werkzeugen; was Alfred kann, kann damit auch eine Routine. Ohne Zeitplan ist eine
Routine ein benannter Auftrag, den Alfred auf Zuruf ausführt ("Mach mein Morgenbriefing").

Gespeichert als YAML im Gedächtnisordner, von Hand änderbar.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml
from loguru import logger

from alfred.dateien import Dateibestand
from alfred.ereignisse import UNGEFRAGT, Ereignis
from alfred.persona import WOCHENTAGE, uhrzeit_gesprochen

# So lange nach der fälligen Zeit holt Alfred eine Routine noch nach, etwa nach einem Neustart.
# Danach wartet sie auf den nächsten Tag; ein Morgenbriefing am Nachmittag hilft niemandem.
NACHHOLEN = timedelta(hours=2)

_KUERZEL = {tag[:2].casefold(): i for i, tag in enumerate(WOCHENTAGE)}
_GRUPPEN = {
    "täglich": list(range(7)),
    "taeglich": list(range(7)),
    "jeden tag": list(range(7)),
    "werktags": list(range(5)),
    "wochentags": list(range(5)),
    "wochenende": [5, 6],
    "wochenends": [5, 6],
}


def parse_wochentage(text: str) -> list[int]:
    """Wochentage aus der Angabe des Sprachmodells: leer oder "täglich", "werktags",
    "wochenende" oder eine Aufzählung wie "Mo, Mi, Fr" oder "montags und freitags"."""
    text = text.strip().casefold()
    if not text or text in _GRUPPEN:
        return _GRUPPEN.get(text, list(range(7)))
    tage = []
    for wort in re.findall(r"[a-zäöü]+", text):
        if wort in ("und", "am", "jeden"):
            continue
        if wort[:2] not in _KUERZEL:
            raise ValueError(f"Wochentag unverständlich: {wort}")
        if _KUERZEL[wort[:2]] not in tage:
            tage.append(_KUERZEL[wort[:2]])
    if not tage:
        raise ValueError("Keine Wochentage erkannt.")
    return sorted(tage)


def parse_uhrzeit(text: str) -> time:
    try:
        stunde, minute = (int(teil) for teil in text.strip().split(":"))
        return time(stunde, minute)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Uhrzeit unverständlich, erwartet HH:MM: {text}") from e


def wochentage_gesprochen(tage: list[int]) -> str:
    if sorted(tage) == list(range(7)):
        return "täglich"
    if sorted(tage) == list(range(5)):
        return "werktags"
    if sorted(tage) == [5, 6]:
        return "am Wochenende"
    return "am " + ", ".join(WOCHENTAGE[t] for t in sorted(tage))


@dataclass
class Routine:
    name: str
    auftrag: str
    uhrzeit: str | None = None  # HH:MM; ohne Uhrzeit nur auf Zuruf
    wochentage: list[int] = field(default_factory=lambda: list(range(7)))  # 0 = Montag
    zuletzt: str | None = None  # ISO-Datum des letzten Laufs
    aktiv: bool = True

    def zeitplan(self) -> str:
        if not self.uhrzeit:
            return "nur auf Zuruf"
        return f"{wochentage_gesprochen(self.wochentage)} um {uhrzeit_gesprochen(datetime.combine(date.today(), parse_uhrzeit(self.uhrzeit)))}"

    def ansage(self) -> str:
        return f"{self.name}, {self.zeitplan()}: {self.auftrag}"

    def faellig(self, jetzt: datetime) -> bool:
        """Fällig, wenn heute ein passender Tag ist, die Zeit erreicht und noch nicht nachgeholt
        verstrichen ist, und die Routine heute noch nicht lief."""
        if not self.aktiv or not self.uhrzeit or jetzt.weekday() not in self.wochentage:
            return False
        if self.zuletzt == jetzt.date().isoformat():
            return False
        soll = datetime.combine(jetzt.date(), parse_uhrzeit(self.uhrzeit), tzinfo=jetzt.tzinfo)
        return soll <= jetzt <= soll + NACHHOLEN


class Routinen(Dateibestand):
    def __init__(self, pfad: Path, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._eintraege: list[Routine] = []
        super().__init__(pfad)

    def _laden(self, daten) -> None:
        self._eintraege = [Routine(**eintrag) for eintrag in (daten or [])]

    def _speichern(self) -> None:
        self._schreiben(
            "# Alfreds Routinen. Jede ist ein Auftrag mit Zeitplan; 0 = Montag. Änderungen wirken sofort.\n"
            + yaml.safe_dump([asdict(r) for r in self._eintraege], allow_unicode=True, sort_keys=False)
        )

    def alle(self) -> list[Routine]:
        self._auffrischen()
        return list(self._eintraege)

    def finden(self, name: str) -> Routine | None:
        """Die Routine, deren Name den gesuchten enthält oder umgekehrt, unabhängig von Groß- und Kleinschreibung."""
        self._auffrischen()
        gesucht = name.strip().casefold()
        for r in self._eintraege:
            if r.name.casefold() == gesucht:
                return r
        for r in self._eintraege:
            if gesucht in r.name.casefold() or r.name.casefold() in gesucht:
                return r
        return None

    def anlegen(self, name: str, auftrag: str, uhrzeit: str = "", wochentage: str = "") -> Routine:
        """Legt eine Routine an oder ersetzt die gleichnamige. Fehlerhafte Angaben lösen ValueError aus."""
        self._auffrischen()
        if not name.strip() or not auftrag.strip():
            raise ValueError("Name und Auftrag dürfen nicht leer sein.")
        routine = Routine(
            name.strip(),
            auftrag.strip(),
            parse_uhrzeit(uhrzeit).strftime("%H:%M") if uhrzeit else None,
            parse_wochentage(wochentage),
        )
        jetzt = self._clock()
        # Liegt die Uhrzeit heute schon zurück, soll die neue Routine nicht sofort losgehen.
        if routine.uhrzeit and parse_uhrzeit(routine.uhrzeit) <= jetzt.time():
            routine.zuletzt = jetzt.date().isoformat()
        self._eintraege = [r for r in self._eintraege if r.name.casefold() != routine.name.casefold()]
        self._eintraege.append(routine)
        self._speichern()
        return routine

    def loeschen(self, name: str) -> Routine | None:
        routine = self.finden(name)
        if routine:
            self._eintraege.remove(routine)
            self._speichern()
        return routine

    def faellige(self) -> list[Routine]:
        self._auffrischen()
        jetzt = self._clock()
        faellige = [r for r in self._eintraege if r.faellig(jetzt)]
        for r in faellige:
            r.zuletzt = jetzt.date().isoformat()
        if faellige:
            self._speichern()
        return faellige

    @staticmethod
    def auftrag_hinweis(routine: Routine, nutzer: str) -> str:
        """Die Anweisung an das Sprachmodell, eine fällige Routine jetzt auszuführen."""
        return (
            f"Hinweis vom System, nicht vom Gegenüber: Die Routine '{routine.name}' ist jetzt fällig. "
            f"Ihr Auftrag: {routine.auftrag} Führe den Auftrag jetzt mit deinen Werkzeugen aus und "
            f"sprich das Ergebnis zusammenhängend aus; sprich {nutzer} dabei kurz an. Scheitert ein "
            "Werkzeug, sag das in einem Satz."
        )

    def ereignis(self, routine: Routine, nutzer: str) -> Ereignis:
        """Die fällige Routine als ungefragtes Ereignis: Die Zustellregeln (Ruhezeit, Modus, Tagesgrenze)
        gelten, und wie beim Nachholen verfällt sie NACHHOLEN nach ihrer Zeit."""
        jetzt = self._clock()
        soll = datetime.combine(jetzt.date(), parse_uhrzeit(routine.uhrzeit), tzinfo=jetzt.tzinfo) if routine.uhrzeit else jetzt
        return Ereignis(
            self.auftrag_hinweis(routine, nutzer),
            klasse=UNGEFRAGT,
            quelle=f"Routine '{routine.name}'",
            gueltig_bis=soll + NACHHOLEN,
        )

    async def warten(self, ereignisse: asyncio.Queue, nutzer: str, intervall: float = 30.0) -> None:
        """Legt fällige Routinen als ungefragtes Ereignis ab, bis die Aufgabe abgebrochen wird."""
        while True:
            for routine in self.faellige():
                logger.info(f"Routine fällig: {routine.name}")
                await ereignisse.put(self.ereignis(routine, nutzer))
            await asyncio.sleep(intervall)
