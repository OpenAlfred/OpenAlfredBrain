"""Was Alfred gerade tut und zuletzt gehört und gesagt hat, für die App.

Der Sprach-Prozess schreibt den Stand nach gedaechtnis/mitschrift.json: bei jeder Änderung und
alle fünf Sekunden als Lebenszeichen. Die App liest die Datei. So sieht Alexander vom Handy
aus, ob Alfred schläft, zuhört, nachdenkt oder spricht, was die Spracherkennung verstanden hat
und wie lange die Antwort gedauert hat, ohne ins Terminal zu schauen.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from alfred.stimme import Stimmfilter

ZUEGE_MAX = 20
LEBENSZEICHEN_SEKUNDEN = 5.0
ERREICHBAR_SEKUNDEN = 15.0  # älter als das, und der Sprach-Prozess gilt als aus
ZUSTAENDE = {
    "schlaeft": "schläft, wartet aufs Wachwort",
    "bereit": "hört zu",
    "hoert": "du sprichst",
    "denkt": "denkt nach",
    "spricht": "spricht",
}


class Mitschrift(BaseObserver):
    """Beobachtet die Pipeline und hält den Stand in einer Datei fest."""

    def __init__(self, pfad: Path, quelle: str, clock: Callable[[], datetime] | None = None):
        super().__init__()
        self._pfad = pfad
        self._quelle = quelle
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._zustand = "bereit"
        self._schlaeft = False
        self._verstanden: dict | None = None
        self._ueberhoert: dict | None = None
        self._gesagt: dict | None = None
        self._antwortzeit: float | None = None
        self._zuege: deque[dict] = deque(maxlen=ZUEGE_MAX)
        # Wer den Zustand außerhalb der App zeigt (LED am Jabra); bekommt jeden neuen Zustand.
        self.bei_zustand: list[Callable[[str], None]] = []

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, UserStartedSpeakingFrame):
            self._setzen("hoert")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._setzen("denkt")
        elif isinstance(frame, FunctionCallInProgressFrame):
            self._setzen(f"arbeitet: {frame.function_name}")
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._setzen("spricht")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._setzen("schlaeft" if self._schlaeft else "bereit")
        elif isinstance(frame, TranscriptionFrame) and isinstance(data.source, Stimmfilter):
            # Erst hinter dem letzten Filter zählt es als verstanden; jedes Transkript genau einmal.
            self.verstanden(frame.text)

    def _setzen(self, zustand: str) -> bool:
        """Setzt den Zustand, meldet und schreibt ihn, wenn er neu ist; sagt, ob er neu war."""
        if zustand == self._zustand:
            return False
        self._zustand = zustand
        for melden in self.bei_zustand:
            try:
                melden(zustand)
            except Exception as e:  # noqa: BLE001 - die Anzeige ist Zugabe, das Gespräch geht vor
                logger.warning(f"Zustand nicht gemeldet: {e}")
        self.schreiben()
        return True

    def schlafen(self) -> None:
        self._schlaeft = True
        self._setzen("schlaeft")

    def aufwachen(self) -> None:
        self._schlaeft = False
        if self._zustand == "schlaeft":
            self._setzen("bereit")

    def verstanden(self, text: str) -> None:
        self._verstanden = self._zug("Alexander", text)
        self.schreiben()

    def ueberhoert(self, text: str) -> None:
        """Ein Satz nach der Gesprächspause, nicht in Alexanders Stimme: verworfen, nicht beantwortet."""
        self._ueberhoert = {"text": text, "zeit": self._clock().isoformat(timespec="seconds")}
        # Sonst stünde es auf "denkt nach", und die LED blinkte weiter.
        if not self._setzen("schlaeft" if self._schlaeft else "bereit"):
            self.schreiben()

    def gesagt(self, text: str, unterbrochen: bool = False) -> None:
        if not text.strip():
            return
        self._gesagt = self._zug("Alfred", text.strip() + (" …" if unterbrochen else ""))
        self.schreiben()

    def antwortzeit(self, sekunden: float) -> None:
        self._antwortzeit = round(sekunden, 2)
        self.schreiben()

    def _zug(self, wer: str, text: str) -> dict:
        zug = {"wer": wer, "text": text, "zeit": self._clock().isoformat(timespec="seconds")}
        self._zuege.append(zug)
        return zug

    def stand(self) -> dict:
        return {
            "quelle": self._quelle,
            "zustand": self._zustand,
            "beschreibung": ZUSTAENDE.get(self._zustand, self._zustand.replace("arbeitet: ", "arbeitet mit ")),
            "aktualisiert": self._clock().isoformat(timespec="seconds"),
            "verstanden": self._verstanden,
            "ueberhoert": self._ueberhoert,
            "gesagt": self._gesagt,
            "antwortzeit": self._antwortzeit,
            "zuege": list(self._zuege),
        }

    def schreiben(self) -> None:
        """Erst in eine Nebendatei, dann umbenennen: Die App liest nie eine halbe Datei."""
        try:
            self._pfad.parent.mkdir(parents=True, exist_ok=True)
            vorlaeufig = self._pfad.with_suffix(".tmp")
            vorlaeufig.write_text(json.dumps(self.stand(), ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(vorlaeufig, self._pfad)
        except OSError as e:  # die Anzeige ist Zugabe, das Gespräch geht vor
            logger.warning(f"Mitschrift nicht geschrieben: {e}")

    async def lebenszeichen(self, intervall: float = LEBENSZEICHEN_SEKUNDEN) -> None:
        while True:
            self.schreiben()
            await asyncio.sleep(intervall)


def lesen(pfad: Path, jetzt: datetime | None = None) -> dict:
    """Der Stand für die App; `erreichbar` sagt, ob der Sprach-Prozess sich noch meldet."""
    jetzt = jetzt or datetime.now().astimezone()
    if not pfad.exists():
        return {"erreichbar": False, "zustand": "aus", "beschreibung": "kein Sprach-Prozess", "zuege": []}
    try:
        stand = json.loads(pfad.read_text(encoding="utf-8"))
        alter = (jetzt - datetime.fromisoformat(stand["aktualisiert"])).total_seconds()
    except (OSError, ValueError, KeyError):
        return {"erreichbar": False, "zustand": "aus", "beschreibung": "Mitschrift unlesbar", "zuege": []}
    stand["erreichbar"] = alter < ERREICHBAR_SEKUNDEN
    if not stand["erreichbar"]:
        stand["beschreibung"] = f"kein Lebenszeichen seit {int(alter // 60)} Minuten" if alter >= 60 else "kein Lebenszeichen"
    return stand
