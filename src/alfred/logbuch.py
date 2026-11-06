"""Logdateien unter logs/ und eine lesbare Konsole.

Im Terminal scrollt ein Fehler vorbei und ist weg; die Frage „warum antwortet er nicht?"
braucht ihn aber Stunden später noch. Deshalb schreibt jeder Prozess (Raum, Browser, App) sein
Log nach logs/<name>.log, täglich neu, zwei Wochen aufgehoben, und alles ab WARNING zusätzlich
nach logs/fehler.log, das die App im Reiter „Alfred" zeigt.

Die Konsole bekommt weiter alles ab DEBUG, bis auf Pipecats Lärm: Den kompletten Systemprompt
(über hundert Zeilen) druckt es bei jedem Gesprächszug zweimal, dazu je Zug fünfzig Zeilen
„auto-registered handler". Dazwischen ging Alexander ein Traceback verloren.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from loguru import logger

from alfred.config import PROJEKT_DIR

FEHLERDATEI = "fehler.log"
_ZEITSTEMPEL = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} - {message}"
LAERM = ("System instruction composed", "Using system instruction", "auto-registered handler")

_sinks: list[int] = []
_ordner: Path | None = None


def leise(record) -> bool:
    """Filter für die Konsole: Pipecats Prompt-Dumps und Werkzeuglisten bleiben draußen."""
    return not any(muster in record["message"] for muster in LAERM)


def einrichten(name: str, ordner: Path | None = None, konsole: bool = True) -> Path:
    """Hängt die Dateien an loguru und tauscht die Konsole gegen eine gefilterte. Gibt den Ordner zurück.

    Einmal je Prozess; ein zweiter Aufruf tut nichts. Im Browser-Modus muss das nach Pipecats
    eigenem Logger-Aufbau geschehen, der alle Senken entfernt.
    """
    global _ordner
    if _ordner is not None:
        return _ordner
    ordner = ordner or PROJEKT_DIR / "logs"
    ordner.mkdir(parents=True, exist_ok=True)
    _ordner = ordner
    if konsole:
        logger.remove()
        _sinks.append(logger.add(sys.stderr, level="DEBUG", filter=leise))
    gemeinsam = dict(format=_FORMAT, encoding="utf-8", enqueue=True, backtrace=False, diagnose=False)
    _sinks.append(logger.add(ordner / f"{name}.log", level="INFO", rotation="00:00", retention="14 days", **gemeinsam))
    _sinks.append(logger.add(ordner / FEHLERDATEI, level="WARNING", rotation="2 MB", retention=3, **gemeinsam))
    return ordner


def abhaengen() -> None:
    """Schließt die Dateien wieder; für Tests."""
    global _ordner
    logger.complete()
    while _sinks:
        logger.remove(_sinks.pop())
    _ordner = None


def letzte_fehler(ordner: Path, anzahl: int = 20) -> list[str]:
    """Die jüngsten Einträge aus fehler.log, neueste zuerst; ein Traceback bleibt bei seinem Eintrag."""
    datei = ordner / FEHLERDATEI
    if not datei.exists():
        return []
    eintraege: list[list[str]] = []
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        if _ZEITSTEMPEL.match(zeile) or not eintraege:
            eintraege.append([zeile])
        else:
            eintraege[-1].append(zeile)
    return ["\n".join(e).strip() for e in eintraege[-anzahl:][::-1] if "".join(e).strip()]
