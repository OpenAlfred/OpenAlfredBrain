"""Aufträge an Alfred: Recherchen, Analysen und Berichte, im Hintergrund nacheinander abgearbeitet.

Alexander sagt etwa "Analysiere …, mach mir einen Bericht mit Quellen, und danach such die
Tech-News von heute raus". Der Sprecher nimmt beides mit `auftrag_geben` an. Das Werkzeug
bestätigt sofort und am Modell vorbei mit einer Zusammenfassung, damit Alexander hört, was
angekommen ist. Der `Auftragsbearbeiter` im Sprach-Prozess arbeitet die Liste der Reihe nach ab:
Der Denker recherchiert und schreibt einen Bericht in Markdown (Kurzfassung, Ergebnis, Hinweise,
Quellen). Der Bericht liegt unter `gedaechtnis/berichte/` und in der App. Ist ein Auftrag fertig
oder gescheitert, sagt Alfred es an und schickt auf Wunsch einen Push aufs Handy.

Die Liste liegt als YAML im Gedächtnisordner, weil App und Sprach-Prozess sie teilen und ein
Neustart nichts verlieren darf. Ein Auftrag, der beim Beenden lief, wartet danach wieder; einer,
dessen Prozess abgestürzt ist, beginnt neu, sobald sein Lebenszeichen veraltet ist.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from loguru import logger

from alfred.dateien import Dateibestand
from alfred.ereignisse import Ansage, Fremd
from alfred.persona import WOCHENTAGE

ERLEDIGTE_BEHALTEN = timedelta(days=30)  # die Berichte selbst bleiben liegen
MAX_VERSUCHE = 3  # so oft darf ein Auftrag durch Absturz abbrechen, dann gilt er als gescheitert
TAKT_SECS = 2.0  # so oft schaut der Bearbeiter nach neuen Aufträgen und nach Abbrüchen
LEBENSZEICHEN_SECS = 30.0
VERWAIST_SECS = 180.0  # ohne Lebenszeichen so lange gilt ein laufender Auftrag als verwaist

STATUS_TEXT = {
    "wartet": "wartet",
    "laeuft": "in Arbeit",
    "fertig": "fertig",
    "fehler": "gescheitert",
    "abgebrochen": "abgebrochen",
}
_REIHE = ("Erstens", "Zweitens", "Drittens", "Viertens", "Fünftens", "Sechstens", "Siebtens", "Achtens")
_ANZAHL = ("", "einen", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht")


@dataclass
class Auftrag:
    nummer: int
    titel: str  # worum es geht, in wenigen Wörtern; Alfred liest ihn zur Bestätigung vor
    auftrag: str  # vollständig, mit allem Zusammenhang
    angelegt: str  # ISO-Zeit
    status: str = "wartet"
    aufs_handy: bool = True
    kontext: str = ""  # die letzten Gesprächszüge beim Anlegen, damit "das" etwas meint
    begonnen: str | None = None
    lebenszeichen: str | None = None
    beendet: str | None = None
    versuche: int = 0
    bericht: str | None = None  # Dateiname im Berichtsordner
    kurzfassung: str | None = None
    fehler: str | None = None

    @property
    def offen(self) -> bool:
        return self.status in ("wartet", "laeuft")

    def ansage(self) -> str:
        return f"{self.nummer}: {self.titel} ({STATUS_TEXT.get(self.status, self.status)})"


class Auftraege(Dateibestand):
    def __init__(self, pfad: Path, berichte: Path, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.berichte = berichte
        self._eintraege: list[Auftrag] = []
        super().__init__(pfad)

    def _laden(self, daten) -> None:
        self._eintraege = [Auftrag(**e) for e in (daten or [])]

    def _speichern(self) -> None:
        grenze = self._clock() - ERLEDIGTE_BEHALTEN
        self._eintraege = [
            a for a in self._eintraege if a.offen or datetime.fromisoformat(a.beendet or a.angelegt) >= grenze
        ]
        self._schreiben(
            "# Alfreds Aufträge. Von Hand änderbar; beendete verschwinden nach 30 Tagen, die Berichte bleiben.\n"
            + yaml.safe_dump([asdict(a) for a in self._eintraege], allow_unicode=True, sort_keys=False)
        )

    def _jetzt(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    def _finden(self, nummer: int, *status: str) -> Auftrag | None:
        self._auffrischen()
        return next((a for a in self._eintraege if a.nummer == nummer and (not status or a.status in status)), None)

    def anlegen(self, titel: str, auftrag: str = "", aufs_handy: bool = True, kontext: str = "") -> Auftrag:
        self._auffrischen()
        titel = " ".join(titel.split()).rstrip(".!?;: ")
        if not titel:
            raise ValueError("Der Auftrag braucht einen Titel.")
        nummer = max((a.nummer for a in self._eintraege), default=0) + 1
        neu = Auftrag(nummer, titel, auftrag.strip() or titel, self._jetzt(), aufs_handy=aufs_handy, kontext=kontext)
        self._eintraege.append(neu)
        self._speichern()
        return neu

    def alle(self) -> list[Auftrag]:
        """Alle Aufträge der letzten 30 Tage, ältester zuerst."""
        self._auffrischen()
        return list(self._eintraege)

    def offene(self) -> list[Auftrag]:
        return [a for a in self.alle() if a.offen]

    def holen(self, nummer: int) -> Auftrag | None:
        return self._finden(nummer)

    def naechster(self) -> Auftrag | None:
        return next((a for a in self.alle() if a.status == "wartet"), None)

    def beginnen(self, nummer: int) -> Auftrag | None:
        """Übernimmt einen wartenden Auftrag; None, wenn ihn inzwischen jemand anders hat."""
        auftrag = self._finden(nummer, "wartet")
        if auftrag is None:
            return None
        auftrag.status, auftrag.versuche = "laeuft", auftrag.versuche + 1
        auftrag.begonnen = auftrag.lebenszeichen = self._jetzt()
        self._speichern()
        return auftrag

    def laeuft_noch(self, nummer: int) -> bool:
        return self._finden(nummer, "laeuft") is not None

    def lebenszeichen_geben(self, nummer: int) -> bool:
        auftrag = self._finden(nummer, "laeuft")
        if auftrag is None:
            return False
        auftrag.lebenszeichen = self._jetzt()
        self._speichern()
        return True

    def zuruecklegen(self, nummer: int) -> Auftrag | None:
        """Ein laufender Auftrag wartet wieder, etwa weil Alfred beendet wird; zählt nicht als Versuch."""
        auftrag = self._finden(nummer, "laeuft")
        if auftrag is None:
            return None
        auftrag.status, auftrag.versuche = "wartet", max(0, auftrag.versuche - 1)
        self._speichern()
        return auftrag

    def abschliessen(self, nummer: int, bericht: str, kurzfassung: str) -> Auftrag | None:
        auftrag = self._finden(nummer, "laeuft")
        if auftrag is None:
            return None
        auftrag.status, auftrag.beendet = "fertig", self._jetzt()
        auftrag.bericht, auftrag.kurzfassung, auftrag.fehler = bericht, kurzfassung, None
        self._speichern()
        return auftrag

    def scheitern(self, nummer: int, fehler: str) -> Auftrag | None:
        auftrag = self._finden(nummer, "wartet", "laeuft")
        if auftrag is None:
            return None
        auftrag.status, auftrag.beendet, auftrag.fehler = "fehler", self._jetzt(), fehler
        self._speichern()
        return auftrag

    def abbrechen(self, nummer: int) -> Auftrag | None:
        auftrag = self._finden(nummer, "wartet", "laeuft")
        if auftrag is None:
            return None
        auftrag.status, auftrag.beendet = "abgebrochen", self._jetzt()
        self._speichern()
        return auftrag

    def verwaiste_zuruecksetzen(self, max_alter: timedelta) -> list[Auftrag]:
        """Laufende Aufträge ohne frisches Lebenszeichen warten wieder; ihr Prozess ist weg."""
        self._auffrischen()
        grenze = self._clock() - max_alter
        verwaist = [
            a
            for a in self._eintraege
            if a.status == "laeuft" and datetime.fromisoformat(a.lebenszeichen or a.begonnen or a.angelegt) < grenze
        ]
        for auftrag in verwaist:
            auftrag.status = "wartet"
        if verwaist:
            self._speichern()
        return verwaist

    def bericht_speichern(self, auftrag: Auftrag, text: str) -> str:
        self.berichte.mkdir(parents=True, exist_ok=True)
        name = f"{self._clock():%Y-%m-%d}-{auftrag.nummer}-{_kennung(auftrag.titel)}.md"
        (self.berichte / name).write_text(text.strip() + "\n", encoding="utf-8")
        return name

    def bericht_lesen(self, auftrag: Auftrag) -> str | None:
        if not auftrag.bericht:
            return None
        pfad = self.berichte / auftrag.bericht
        # Die Datei ist von Hand änderbar; ein Name wie "../profil.yaml" soll nichts anderes lesen.
        if pfad.resolve().parent != self.berichte.resolve() or not pfad.is_file():
            return None
        return pfad.read_text(encoding="utf-8")

    def fuer_prompt(self) -> str:
        offene = self.offene()
        if not offene:
            return ""
        return (
            "Deine offenen Aufträge (Stand beim Aufwachen, den aktuellen nennt auftraege_anzeigen): "
            + "; ".join(a.ansage() for a in offene)
            + "."
        )


def _kennung(titel: str) -> str:
    """Titel als Dateinamen-Teil: klein, Umlaute ausgeschrieben, nur Buchstaben, Ziffern und Striche."""
    text = titel.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:50].rstrip("-") or "bericht"


def kurztitel(text: str, max_zeichen: int = 80) -> str:
    """Ein Auftrag aus der App hat keinen Titel: der Anfang des Textes, an einer Wortgrenze gekürzt."""
    text = " ".join(text.split())
    if len(text) <= max_zeichen:
        return text
    return text[:max_zeichen].rsplit(" ", 1)[0].rstrip(",;:") + " …"


def kurzfassung(bericht: str, max_zeichen: int = 450) -> str:
    """Der Abschnitt "Kurzfassung" des Berichts als vorlesbarer Text; ohne ihn der erste Absatz."""
    treffer = re.search(r"^#+\s*Kurzfassung\s*$(.*?)(?=^#|\Z)", bericht, re.M | re.S | re.I)
    if treffer:
        text = treffer.group(1)
    else:
        absaetze = [a for a in re.split(r"\n\s*\n", bericht) if a.strip() and not a.lstrip().startswith("#")]
        text = absaetze[0] if absaetze else ""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # Links: nur der Text
    text = re.sub(r"\s*\[\d+(?:[,–-]\s*\d+)*\]", "", text)  # Quellenverweise wie [1] oder [1, 2]
    text = " ".join(re.sub(r"[*_`#>]", "", text).split())
    if len(text) > max_zeichen:
        gekuerzt = text[:max_zeichen]
        ende = _letztes_satzende(gekuerzt)
        text = gekuerzt[:ende] if ende else gekuerzt.rsplit(" ", 1)[0] + " …"
    return text


def _letztes_satzende(text: str) -> int:
    """Die Stelle hinter dem letzten Satzende, 0 ohne.

    Ein Punkt hinter einer Zahl ("zum 15. Mal") oder einem Kürzel ("z. B.") beendet keinen Satz;
    die Kurzfassung brach dort einmal mitten im Satz ab (5.10.2026).
    """
    ende = 0
    for treffer in re.finditer(r"[.!?](?=\s|$)", text):
        wort = text[: treffer.start()].split()[-1:]
        if treffer.group() != "." or (wort and len(wort[0]) >= 3 and not wort[0][-1].isdigit()):
            ende = treffer.end()
    return ende


def bestaetigung(neue: list[Auftrag], vorher: int, push: bool | None) -> str:
    """Was Alfred sofort sagt, wenn er Aufträge annimmt: worum es geht und wie er sich meldet.

    Args:
        vorher: So viele Aufträge waren schon offen; die kommen zuerst dran.
        push: True heißt Nachricht aufs Handy, False keine gewünscht, None gewünscht, aber Push
            ist nicht eingerichtet.
    """
    if len(neue) == 1:
        saetze = [f"Verstanden, ich übernehme: {neue[0].titel}."]
    else:
        anzahl = _ANZAHL[len(neue)] if len(neue) < len(_ANZAHL) else str(len(neue))
        saetze = [f"Verstanden, {anzahl} Aufträge."]
        saetze += [f"{_REIHE[i] if i < len(_REIHE) else 'Dann'}: {a.titel}." for i, a in enumerate(neue)]
        saetze.append("Ich arbeite sie nacheinander ab.")
    if vorher:
        andere = "einen anderen Auftrag" if vorher == 1 else f"{_ANZAHL[vorher] if vorher < len(_ANZAHL) else vorher} andere"
        saetze.append(f"Vorher erledige ich noch {andere}.")
    einer = "ich fertig bin" if len(neue) == 1 else "einer fertig ist"
    if push:
        saetze.append(f"Sobald {einer}, schicke ich dir eine Nachricht aufs Handy, den Bericht findest du in der App.")
    elif push is None:
        saetze.append(f"Aufs Handy kann ich nichts schicken, Push ist nicht eingerichtet. Sobald {einer}, sage ich Bescheid.")
    else:
        saetze.append(f"Sobald {einer}, sage ich Bescheid, den Bericht findest du in der App.")
    return " ".join(saetze)


class Auftragsbearbeiter:
    """Arbeitet die Aufträge im Sprach-Prozess der Reihe nach ab, einen zur Zeit.

    Läuft als Hintergrundaufgabe (pipeline.beaufsichtigt). Ein Auftrag, den Alexander in der App
    oder per Stimme abbricht, wird beim nächsten Takt beendet.
    """

    def __init__(
        self,
        auftraege: Auftraege,
        denker,
        ereignisse: asyncio.Queue,
        benachrichtiger=None,
        app_url: str = "",
        *,
        takt: float = TAKT_SECS,
        lebenszeichen_secs: float = LEBENSZEICHEN_SECS,
        verwaist_nach: timedelta = timedelta(seconds=VERWAIST_SECS),
        clock: Callable[[], datetime] | None = None,
    ):
        self._auftraege = auftraege
        self._denker = denker
        self._ereignisse = ereignisse
        self._benachrichtiger = benachrichtiger
        self._app_url = app_url.rstrip("/")
        self._takt = takt
        self._lebenszeichen_secs = lebenszeichen_secs
        self._verwaist_nach = verwaist_nach
        self._clock = clock or (lambda: datetime.now().astimezone())

    async def laufen(self) -> None:
        while True:
            for auftrag in self._auftraege.verwaiste_zuruecksetzen(self._verwaist_nach):
                logger.warning(f"Auftrag {auftrag.nummer} lief ohne Lebenszeichen; er beginnt neu: {auftrag.titel}")
            auftrag = self._auftraege.naechster()
            if auftrag is None:
                await asyncio.sleep(self._takt)
                continue
            await self.bearbeiten(auftrag.nummer)

    async def bearbeiten(self, nummer: int) -> None:
        auftrag = self._auftraege.beginnen(nummer)
        if auftrag is None:
            return
        if auftrag.versuche > MAX_VERSUCHE:
            fehler = f"Nach {MAX_VERSUCHE} abgebrochenen Versuchen aufgegeben"
            if gescheitert := self._auftraege.scheitern(nummer, fehler):
                await self._melden(gescheitert)
            return
        logger.info(f"Auftrag {nummer} beginnt: {auftrag.titel}")
        jetzt = self._clock()
        heute = f"{WOCHENTAGE[jetzt.weekday()]}, der {jetzt:%d.%m.%Y}, {jetzt:%H:%M} Uhr"
        arbeit = asyncio.create_task(self._denker.bericht(auftrag.auftrag, auftrag.kontext, heute=heute))
        gemeldet = time.monotonic()
        try:
            while not arbeit.done():
                await asyncio.wait({arbeit}, timeout=self._takt)
                if arbeit.done():
                    break
                if not self._auftraege.laeuft_noch(nummer):
                    arbeit.cancel()
                    logger.info(f"Auftrag {nummer} abgebrochen: {auftrag.titel}")
                    return
                if time.monotonic() - gemeldet >= self._lebenszeichen_secs:
                    try:
                        self._auftraege.lebenszeichen_geben(nummer)
                    except OSError as e:
                        # Eine gesperrte Datei (unter Windows, solange die App sie liest) beendet weder den Auftrag
                        # noch seine Recherche: Der nächste Takt versucht es wieder, verwaist gilt er erst nach drei Minuten.
                        logger.warning(f"Lebenszeichen zu Auftrag {nummer} nicht gespeichert: {e}")
                        continue
                    gemeldet = time.monotonic()
            text = arbeit.result()
        except asyncio.CancelledError:
            # Alfred wird beendet: Der Auftrag wartet wieder und beginnt beim nächsten Start neu.
            arbeit.cancel()
            self._auftraege.zuruecklegen(nummer)
            raise
        except Exception as e:  # noqa: BLE001 - ein gescheiterter Auftrag darf die Liste nicht anhalten
            logger.exception(f"Auftrag {nummer} gescheitert: {auftrag.titel}")
            if gescheitert := self._auftraege.scheitern(nummer, str(e) or e.__class__.__name__):
                await self._melden(gescheitert)
            return
        name = self._auftraege.bericht_speichern(auftrag, text)
        fertig = self._auftraege.abschliessen(nummer, name, kurzfassung(text))
        if fertig is None:  # während des Schreibens abgebrochen
            return
        logger.info(f"Auftrag {nummer} fertig, Bericht {name}")
        await self._melden(fertig)

    async def _melden(self, auftrag: Auftrag) -> None:
        """Ansage im Raum und, wenn gewünscht, Push aufs Handy; ein Push-Fehler hält nichts auf."""
        if auftrag.status == "fertig":
            teile = [f"Auftrag erledigt: {auftrag.titel}.", auftrag.kurzfassung, "Den ganzen Bericht findest du in der App."]
            ansage = " ".join(t for t in teile if t)
            titel, text = "Alfred: Auftrag erledigt", f"{auftrag.titel}\n\n{auftrag.kurzfassung or 'Bericht in der App.'}"
        else:
            grund = auftrag.fehler or "unbekannter Fehler"
            ansage = f"Den Auftrag {auftrag.titel} konnte ich nicht erledigen: {grund[:150]}."
            titel, text = "Alfred: Auftrag gescheitert", f"{auftrag.titel}\n\n{grund}"
        # Die Kurzfassung stammt aus dem Internet: Mit ihr im Gespräch fragen schreibende Werkzeuge nach.
        ereignis = Fremd(Ansage(ansage), f"Kurzfassung des Berichts '{auftrag.titel}'") if auftrag.status == "fertig" else Ansage(ansage)
        await self._ereignisse.put(ereignis)
        if not (auftrag.aufs_handy and self._benachrichtiger):
            return
        klick = f"{self._app_url}/#auftrag-{auftrag.nummer}" if self._app_url else ""
        try:
            await self._benachrichtiger.senden(text, titel=titel, klick=klick)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Push zu Auftrag {auftrag.nummer} fehlgeschlagen: {e}")
