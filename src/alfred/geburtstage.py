"""Geburtstage aus den Kontakten: am Vortag erinnern, jeden nur einmal im Jahr."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path

from loguru import logger

from alfred.persona import WOCHENTAGE
from alfred.whatsapp import Geburtstag, Kontakte


def geburtstag_ansage(geburtstag: Geburtstag, wann, heute) -> str:
    """"Max: morgen, wird 30" oder "Anna: Freitag, 10.10."; heute heißt heute."""
    abstand = (wann - heute).days
    tag = {0: "heute", 1: "morgen"}.get(abstand, f"{WOCHENTAGE[wann.weekday()]}, {wann:%d.%m.}")
    alter = geburtstag.alter_am(wann)
    return f"{geburtstag.name}: {tag}" + (f", wird {alter}" if alter else "")


class Geburtstagserinnerer:
    """Legt am Vortag eines Geburtstags eine Erinnerung an, pro Person und Jahr einmal."""

    def __init__(
        self,
        kontakte: Kontakte,
        erinnerungen,
        merkdatei: Path,
        clock: Callable[[], datetime] | None = None,
        uhrzeit: time = time(9, 0),
    ):
        self._kontakte = kontakte
        self._erinnerungen = erinnerungen
        self._merkdatei = merkdatei
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._uhrzeit = uhrzeit
        self._erledigt: set[str] = set(
            json.loads(merkdatei.read_text(encoding="utf-8")) if merkdatei.exists() else []
        )

    def pruefen(self) -> list[str]:
        """Erinnerungen für die Geburtstage von morgen anlegen; gibt die Namen zurück."""
        jetzt = self._clock()
        morgen = jetzt.date() + timedelta(days=1)
        neue = []
        for geburtstag, wann in self._kontakte.anstehende_geburtstage(jetzt.date(), tage=1):
            schluessel = f"{geburtstag.name}:{wann.isoformat()}"
            if wann != morgen or schluessel in self._erledigt:
                continue
            # Zur Erinnerungszeit; ist die heute schon vorbei, sofort.
            faellig = max(jetzt.replace(hour=self._uhrzeit.hour, minute=self._uhrzeit.minute, second=0, microsecond=0), jetzt)
            alter = geburtstag.alter_am(wann)
            text = f"Morgen hat {geburtstag.name} Geburtstag" + (f" und wird {alter}" if alter else "")
            self._erinnerungen.anlegen(text, faellig)
            self._erledigt.add(schluessel)
            neue.append(geburtstag.name)
        if neue:
            self._merkdatei.parent.mkdir(parents=True, exist_ok=True)
            self._merkdatei.write_text(json.dumps(sorted(self._erledigt)), encoding="utf-8")
        return neue

    async def laufen(self, intervall: float = 3600.0) -> None:
        while True:
            try:
                for name in self.pruefen():
                    logger.info(f"Erinnerung an den Geburtstag von {name} angelegt")
            except Exception as e:  # Geburtstage dürfen den Sprach-Loop nie mitreißen
                logger.warning(f"Geburtstage nicht geprüft: {e}")
            await asyncio.sleep(intervall)
