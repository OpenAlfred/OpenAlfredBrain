"""Erinnerungen und Timer. Dateigestützt, damit sie einen Neustart überleben."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from loguru import logger

from alfred.dateien import Dateibestand
from alfred.ereignisse import Ansage, Ereignis
from alfred.routinen import NACHHOLEN, wochentage_gesprochen

# Ab so viel Verspätung sagt Alfred dazu, wann die Erinnerung eigentlich fällig war.
VERSPAETET_AB = timedelta(minutes=2)


@dataclass
class Erinnerung:
    nummer: int
    faellig: str  # ISO 8601 mit Zeitzone; bei wiederkehrenden die nächste Ausgabe
    text: str
    status: str = "offen"  # offen | ausgeliefert
    art: str = "erinnerung"  # erinnerung | timer; ein Timer klingelt und wird wörtlich angesagt
    # Wiederkehrend an diesen Wochentagen (0 = Montag) zur Uhrzeit von faellig; None = einmalig.
    wochentage: list[int] | None = None
    zuletzt: str | None = None  # wiederkehrend: Datum der letzten Ausgabe

    @property
    def faellig_um(self) -> datetime:
        return datetime.fromisoformat(self.faellig)

    @property
    def timer(self) -> bool:
        return self.art == "timer"

    @property
    def wiederkehrend(self) -> bool:
        return bool(self.wochentage)

    def faellig_jetzt(self, jetzt: datetime) -> bool:
        """Einmalige ab ihrer Zeit; wiederkehrende wie Routinen: am passenden Tag zur Uhrzeit, bis zu
        NACHHOLEN später, einmal am Tag. Die Uhrzeit gilt als Ortszeit, auch über die Zeitumstellung."""
        if not self.wiederkehrend:
            return self.faellig_um <= jetzt
        if jetzt.weekday() not in self.wochentage or self.zuletzt == jetzt.date().isoformat():
            return False
        soll = datetime.combine(jetzt.date(), self.faellig_um.time(), tzinfo=jetzt.tzinfo)
        return soll <= jetzt <= soll + NACHHOLEN

    def kurztext(self) -> str:
        """Für Push und Anzeige."""
        return f"Timer abgelaufen: {self.text}" if self.timer else self.text

    def wiederholung(self) -> str:
        return wochentage_gesprochen(self.wochentage) if self.wochentage else ""


def naechster_termin(nach: datetime, um: time, wochentage: list[int]) -> datetime:
    """Der nächste Zeitpunkt nach `nach` an einem der Wochentage zur Uhrzeit `um`."""
    for tage in range(8):
        tag = (nach + timedelta(days=tage)).date()
        kandidat = datetime.combine(tag, um, tzinfo=nach.tzinfo)
        if tag.weekday() in wochentage and kandidat > nach:
            return kandidat
    raise ValueError("Keine Wochentage angegeben.")


class Erinnerungen(Dateibestand):
    def __init__(self, pfad: Path, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._eintraege: list[Erinnerung] = []
        super().__init__(pfad)

    def _laden(self, daten) -> None:
        self._eintraege = [Erinnerung(**e) for e in (daten or [])]

    def _speichern(self) -> None:
        self._schreiben(json.dumps([asdict(e) for e in self._eintraege], ensure_ascii=False, indent=2))

    def anlegen(
        self, text: str, faellig: datetime, art: str = "erinnerung", wochentage: list[int] | None = None
    ) -> Erinnerung:
        """Legt eine Erinnerung an; mit `wochentage` wiederkehrend zur Uhrzeit von `faellig`."""
        self._auffrischen()
        nummer = max((e.nummer for e in self._eintraege), default=0) + 1
        eintrag = Erinnerung(nummer, faellig.isoformat(timespec="seconds"), text, art=art, wochentage=wochentage or None)
        if eintrag.wiederkehrend:
            jetzt = self._clock()
            # Liegt die Uhrzeit heute schon zurück, zählt heute als erledigt, wie bei Routinen.
            if datetime.combine(jetzt.date(), faellig.time(), tzinfo=jetzt.tzinfo) <= jetzt:
                eintrag.zuletzt = jetzt.date().isoformat()
        self._eintraege.append(eintrag)
        self._speichern()
        return eintrag

    def offene(self) -> list[Erinnerung]:
        self._auffrischen()
        return sorted((e for e in self._eintraege if e.status == "offen"), key=lambda e: e.faellig)

    def timer(self) -> list[Erinnerung]:
        """Die laufenden Timer, der nächste zuerst."""
        return [e for e in self.offene() if e.timer]

    def loeschen(self, nummer: int) -> bool:
        self._auffrischen()
        vorher = len(self._eintraege)
        self._eintraege = [e for e in self._eintraege if e.nummer != nummer or e.status != "offen"]
        self._speichern()
        return len(self._eintraege) < vorher

    def faellige(self) -> list[Erinnerung]:
        jetzt = self._clock()
        return [e for e in self.offene() if e.faellig_jetzt(jetzt)]

    def ansage(self, eintrag: Erinnerung) -> str | Ereignis:
        """Was der Zusteller ausgibt: ein Timer wörtlich mit Klingel, sonst die Anweisung ans Modell.

        Nur der Timer klingelt auch vor Gästen; eine Erinnerung wartet im Gästemodus (Zustellregeln).
        """
        jetzt = self._clock()
        if eintrag.wiederkehrend:
            # Bleibt offen; faellig zeigt die nächste Ausgabe, damit Anzeige und Reihenfolge stimmen.
            soll = datetime.combine(jetzt.date(), eintrag.faellig_um.time(), tzinfo=jetzt.tzinfo)
            eintrag.zuletzt = jetzt.date().isoformat()
            eintrag.faellig = naechster_termin(jetzt, soll.time(), eintrag.wochentage).isoformat(timespec="seconds")
        else:
            soll = eintrag.faellig_um
            eintrag.status = "ausgeliefert"
        self._speichern()
        if eintrag.timer:
            text = "Die Zeit ist um." if eintrag.text == "Timer" else f"Die Zeit ist um: {eintrag.text}."
            return Ereignis(Ansage(text), vor_gaesten=True)
        # Der Hinweis erreicht das Modell als Nutzernachricht. Ohne klare Einordnung hielt es
        # "Erinnere jetzt an: Wäsche" für den Auftrag, eine Erinnerung anzulegen.
        if jetzt - soll > VERSPAETET_AB:
            return (
                f"Hinweis vom System, nicht vom Gegenüber: Die Erinnerung '{eintrag.text}' war um "
                f"{soll:%H:%M} Uhr fällig und konnte nicht ausgesprochen werden. Hol "
                "das jetzt in einem Satz nach und nenne die ursprüngliche Uhrzeit. Sie ist damit "
                "erledigt, kein Werkzeugaufruf nötig."
            )
        return (
            f"Hinweis vom System, nicht vom Gegenüber: Die Erinnerung '{eintrag.text}' ist jetzt "
            "fällig. Sprich sie jetzt in einem Satz aus. Sie ist damit erledigt, kein "
            "Werkzeugaufruf nötig."
        )

    async def warten(
        self,
        ereignisse: asyncio.Queue,
        intervall: float = 1.0,
        benachrichtigen: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Legt fällige Erinnerungen als Ereignis ab, bis die Aufgabe abgebrochen wird.

        Mit `benachrichtigen` geht jede fällige Erinnerung zusätzlich aufs Telefon, damit sie
        auch ankommt, wenn niemand im Raum ist. Scheitert das, bleibt die Ansage trotzdem.
        """
        while True:
            for eintrag in self.faellige():
                logger.info(f"Erinnerung fällig: {eintrag.kurztext()}")
                await ereignisse.put(self.ansage(eintrag))
                if benachrichtigen:
                    try:
                        await benachrichtigen(eintrag.kurztext())
                    except Exception as e:  # Push ist Zugabe, kein Grund abzubrechen
                        logger.warning(f"Push aufs Telefon fehlgeschlagen: {e}")
            await asyncio.sleep(intervall)


def zeitpunkt(
    jetzt: datetime, in_minuten: int = 0, uhrzeit: str = "", datum: str = ""
) -> datetime:
    """Fälligkeit aus den Angaben des Sprachmodells. Eine vergangene Uhrzeit meint morgen."""
    if in_minuten > 0:
        return jetzt + timedelta(minutes=in_minuten)
    if not uhrzeit:
        raise ValueError("Entweder in_minuten oder uhrzeit angeben.")
    stunde, minute = (int(teil) for teil in uhrzeit.split(":"))
    tag = datetime.strptime(datum, "%Y-%m-%d").date() if datum else jetzt.date()
    faellig = jetzt.replace(year=tag.year, month=tag.month, day=tag.day, hour=stunde, minute=minute, second=0, microsecond=0)
    if faellig <= jetzt and not datum:
        faellig += timedelta(days=1)
    return faellig
