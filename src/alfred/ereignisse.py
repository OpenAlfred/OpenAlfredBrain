"""Proaktives Sprechen: Ereignisse werden ausgesprochen, sobald im Gespräch Ruhe ist.

Erinnerungen, Ergebnisse des Denkers, Routinen und später Kalender und Präsenz legen Texte in
die Ereignis-Warteschlange. Der Zusteller wartet, bis weder Nutzer noch Alfred sprechen, und
lässt den Sprecher den Text dann in eigenen Worten ausgeben. Nur der Sprecher spricht.

Eine `Ansage` geht am Sprachmodell vorbei: Sie wird wörtlich gesprochen, auf Wunsch mit
Klingel davor. So klingelt ein Timer sofort und kostet nichts.

Zwei Klassen von Ereignissen, als Regel im Code, nicht im Prompt:

- angefordert: Antwort auf etwas, das Alexander ausgelöst hat (Denker fertig, Auftrag fertig,
  Timer, selbst gesetzte Erinnerung). Kommt immer durch, außer im Gästemodus. Ein nackter Text
  oder eine Ansage in der Warteschlange zählt als angefordert.
- ungefragt: Vorschläge, Nachfragen, Briefings, Hinweise aus Routinen. Dafür gelten die
  `Zustellregeln`: höchstens `proaktiv.max_pro_tag` am Tag, Ruhezeit, je Modus abschaltbar,
  „nie wieder" als Regel in gedaechtnis/regeln.yaml. Was nicht durchkommt, geht als Push aufs
  Telefon oder wartet bis zur nächsten erlaubten Zeit; Veraltetes (`gueltig_bis`) verfällt.
  Der `Ansagewaechter` erkennt Alexanders Antwort darauf: „Nicht jetzt" schiebt die Ansage
  auf, „Lass das" schlägt eine Regel vor.

Im Gästemodus wartet beides, bis der Besuch weg ist, außer was ausdrücklich vor Gästen gesagt
werden darf (`vor_gaesten`: Timer, was im Gästemodus angefordert wurde). Sonst läse Alfred eine
WhatsApp wörtlich vor oder eine Erinnerung samt Arzttermin, während ein Gast zuhört (16.10.2026).

`Fremd` umhüllt ein Ereignis, das fremden Text enthält (Ergebnis des Denkers aus dem Internet,
Kurzfassung eines Berichts): Beim Ausliefern setzt der Zusteller die Marke "fremder Inhalt im
Gespräch" in den Ressourcen, ab dann fragen schreibende Werkzeuge nach.

Zugestellt ist ein Ereignis erst, wenn Alfred danach hörbar zu sprechen beginnt. Endet der Lauf
vorher, etwa weil das Mikrofon nicht aufgeht, bleibt es in der Warteschlange für den nächsten.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from datetime import time as tageszeit
from functools import cache
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from alfred.config import Proaktiv
from alfred.persona import GAESTEMODUS, uhrzeit_gesprochen
from alfred.resources import Freigabe

KLINGEL_RATE = 24000
# So lange wartet der Zusteller darauf, dass Alfred zu einem Ereignis hörbar spricht. Das
# Morgenbriefing mit zwei Werkzeugen klang nach rund fünf Sekunden (5. und 9.10.2026), GLM braucht
# je Antwort bis zu 19 s. Danach gilt es trotzdem als zugestellt, wie vor dem 16.10.2026.
QUITTUNG_SECS = 60.0
ANGEFORDERT = "angefordert"
UNGEFRAGT = "ungefragt"
# Vorsatz der Regel in gedaechtnis/regeln.yaml, die eine Quelle ungefragter Ansagen abschaltet.
ANSAGE_SPERRE = "Nicht ungefragt ansagen: "


@dataclass
class Ansage:
    """Wird wörtlich gesprochen, ohne Umweg über das Sprachmodell."""

    text: str
    klingeln: bool = True
    # So steht sie im Verlauf, wenn das anders sein soll als gesprochen (etwa als fremder Inhalt markiert).
    verlauf: str | None = None


@dataclass
class Ereignis:
    """Etwas, das Alfred von sich aus sagen soll, mit Klasse und Regeln für die Zustellung.

    inhalt: Text für das Sprachmodell („Hinweis vom System …") oder eine wörtliche Ansage.
    klasse: angefordert (kommt immer durch) oder ungefragt (Zustellregeln).
    quelle: Woher es kommt, etwa "Routine 'Morgenbriefing'"; Schlüssel für „nie wieder".
    dringend: Ungefragtes, das Tagesgrenze und Modus „nur Dringendes" übergeht; die Ruhezeit nicht.
    push: Text fürs Telefon, wenn die Ansage nicht durchkommt; leer = warten statt Push.
    gueltig_bis: Danach verfällt es ungesprochen.
    nicht_vor: Aufgeschoben („nicht jetzt"): vorher wird es nicht gesprochen.
    herkunft: Fremder Text im Inhalt (Denker aus dem Internet, Bericht): Der Zusteller setzt beim
        Ausliefern die Marke, ab der schreibende Werkzeuge nachfragen.
    vor_gaesten: Darf auch gesprochen werden, solange Gäste da sind (Timer, im Gästemodus
        Angefordertes); alles andere wartet im Gästemodus.
    """

    inhalt: str | Ansage
    klasse: str = ANGEFORDERT
    quelle: str = ""
    dringend: bool = False
    push: str = ""
    gueltig_bis: datetime | None = None
    nicht_vor: datetime | None = None
    herkunft: str = ""
    vor_gaesten: bool = False
    # Buchführung des Zustellers: Push schon versucht, Warten schon im Log.
    push_versucht: bool = field(default=False, compare=False)
    gemeldet: bool = field(default=False, compare=False)

    def __post_init__(self):
        if self.klasse not in (ANGEFORDERT, UNGEFRAGT):
            raise ValueError(f"Unbekannte Ereignisklasse: {self.klasse}")

    @property
    def text(self) -> str:
        return self.inhalt.text if isinstance(self.inhalt, Ansage) else self.inhalt

    @property
    def ungefragt(self) -> bool:
        return self.klasse == UNGEFRAGT

    def beschreibung(self) -> str:
        """Für das Log: die Quelle, sonst der Textanfang."""
        return self.quelle or self.text[:60]


@dataclass
class Fremd:
    """Ein Ereignis mit fremdem Inhalt, samt Herkunft für Rückfrage und Aktionsprotokoll.

    Die Marke setzt der Zusteller erst beim Ausliefern, nicht beim Einreihen: Dazwischen kann ein
    neues Gespräch beginnen, und die Marke gehört zu dem Gespräch, in dem der Text landet.
    """

    ereignis: str | Ansage
    herkunft: str


Zustellbar = str | Ansage | Fremd | Ereignis


def als_ereignis(eintrag: Zustellbar) -> Ereignis:
    """Nackter Text, Ansage und Fremd zählen als angefordert; Fremd bringt seine Herkunft mit."""
    if isinstance(eintrag, Ereignis):
        return eintrag
    if isinstance(eintrag, Fremd):
        return Ereignis(eintrag.ereignis, herkunft=eintrag.herkunft)
    return Ereignis(eintrag)


def ereignis_text(eintrag: Zustellbar) -> str:
    return als_ereignis(eintrag).text


@cache
def klingelton() -> bytes:
    """Zwei Töne wie eine Tischglocke, 16 Bit mono mit KLINGEL_RATE; der Transport sampelt um."""
    teile = []
    for frequenz, dauer in ((880.0, 0.22), (659.25, 0.45)):
        t = np.arange(int(KLINGEL_RATE * dauer)) / KLINGEL_RATE
        huelle = np.exp(-4.0 * t / dauer)
        huelle[: KLINGEL_RATE // 100] *= np.linspace(0.0, 1.0, KLINGEL_RATE // 100)  # kein Knacken
        teile.append(0.4 * huelle * np.sin(2 * np.pi * frequenz * t))
    ton = np.concatenate(teile + [np.zeros(KLINGEL_RATE // 5)])
    return (ton * 32767).astype(np.int16).tobytes()


class Gespraechszustand(BaseObserver):
    """Weiß, ob gerade jemand spricht oder das Modell antwortet, und was es zuletzt geantwortet hat."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self._clock = clock
        self._nutzer_spricht = False
        self._bot_spricht = False
        self._antwort_laeuft = False
        self._ruhig_seit = clock()
        self._antwort_teile: list[str] = []
        self._letzte_antwort: str | None = None
        self._sprechbeginne = 0

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        # Text nur beim ersten Weiterreichen sammeln: Ein Beobachter sieht jeden Frame bei jedem Schritt.
        erstes = getattr(data, "first_push", True)
        if isinstance(frame, UserStartedSpeakingFrame):
            self._nutzer_spricht = True
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._nutzer_spricht = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_spricht = True
            if erstes:
                self._sprechbeginne += 1
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_spricht = False
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._antwort_laeuft = True
            if erstes:
                self._antwort_teile = []
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._antwort_laeuft = False
            if erstes:
                self._letzte_antwort = "".join(self._antwort_teile).strip()
        elif isinstance(frame, LLMTextFrame):
            if erstes:
                self._antwort_teile.append(frame.text)
            return
        else:
            return
        self._ruhig_seit = self._clock()

    def modellantwort_abholen(self) -> str | None:
        """Der Text der letzten Antwort des Sprachmodells, einmal; None, wenn keine neue da ist.

        Ansagen und sofort Gesagtes (TTSSpeakFrame) kommen nicht vom Modell und stehen hier nie.
        """
        antwort, self._letzte_antwort = self._letzte_antwort, None
        return antwort

    def ruhig_seit(self) -> float:
        """Sekunden seit dem letzten Gesprächsbeitrag, 0 solange jemand spricht."""
        if self._nutzer_spricht or self._bot_spricht or self._antwort_laeuft:
            return 0.0
        return self._clock() - self._ruhig_seit

    def antwort_laeuft(self) -> bool:
        """Ob das Sprachmodell gerade antwortet."""
        return self._antwort_laeuft

    @property
    def sprechbeginne(self) -> int:
        """Wie oft Alfred hörbar zu sprechen begann; daran sieht der Zusteller, dass ein Ereignis ankam."""
        return self._sprechbeginne


def in_ruhezeit(jetzt: tageszeit, von: tageszeit, bis: tageszeit) -> bool:
    """Liegt die Uhrzeit in der Ruhezeit? Geht sie über Mitternacht (22:00 bis 07:00), zählt der
    Abend wie der frühe Morgen. Gleiche Zeiten heißen keine Ruhezeit."""
    if von == bis:
        return False
    if von < bis:
        return von <= jetzt < bis
    return jetzt >= von or jetzt < bis


def ist_gesperrt(regeln: Iterable[str], quelle: str) -> bool:
    """Steht in den Regeln, dass diese Quelle nicht mehr ungefragt ansagen darf?"""
    gesucht = f"{ANSAGE_SPERRE}{quelle}".casefold()
    return any(regel.strip().casefold() == gesucht for regel in regeln)


def _mit_zone(zeit: datetime, wie: datetime) -> datetime:
    """Eine Zeit ohne Zeitzone bekommt die der Uhr; sonst ließe sie sich nicht vergleichen."""
    return zeit if zeit.tzinfo is not None else zeit.replace(tzinfo=wie.tzinfo)


class Tageszaehler:
    """Zählt die ungefragten Ansagen des Tages in einer JSON-Datei, damit ein Neustart nicht zurücksetzt."""

    def __init__(self, pfad: Path):
        self._pfad = pfad
        self._tag = ""
        self._ansagen: list[str] = []
        if pfad.exists():
            try:
                daten = json.loads(pfad.read_text(encoding="utf-8"))
                self._tag = str(daten["tag"])
                self._ansagen = [str(a) for a in daten["ansagen"]]
            except (ValueError, KeyError, TypeError) as e:  # kaputte Datei: von vorn zählen statt stehen bleiben
                logger.warning(f"Zähler {pfad.name} unlesbar, zählt von vorn: {e}")
                self._tag, self._ansagen = "", []

    def stand(self, heute: date) -> int:
        return len(self._ansagen) if self._tag == heute.isoformat() else 0

    def zaehlen(self, jetzt: datetime, was: str) -> int:
        if self._tag != jetzt.date().isoformat():
            self._tag, self._ansagen = jetzt.date().isoformat(), []
        self._ansagen.append(f"{jetzt:%H:%M} {was}")
        self._pfad.parent.mkdir(parents=True, exist_ok=True)
        self._pfad.write_text(
            json.dumps({"tag": self._tag, "ansagen": self._ansagen}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(self._ansagen)


@dataclass(frozen=True)
class Entscheidung:
    erlaubt: bool
    grund: str = ""  # warum nicht, für das Log
    endgueltig: bool = False  # verfallen oder abgeschaltet: nicht mehr warten


class Zustellregeln:
    """Entscheidet, ob ein Ereignis jetzt gesprochen werden darf.

    Angefordertes immer, außer im Gästemodus. Ungefragtes nur außerhalb der Ruhezeit, unter der
    Tagesgrenze, wenn der Modus es erlaubt und keine Regel die Quelle abschaltet. Dringendes
    übergeht Tagesgrenze und „nur Dringendes", nicht die Ruhezeit: Nachts gehört Dringendes aufs
    Telefon, das auch stumm klingeln kann. Im Gästemodus wartet alles ohne `vor_gaesten`; mit
    Push-Text geht es gleich aufs Telefon.
    """

    def __init__(
        self,
        einstellungen: Proaktiv,
        zaehler: Tageszaehler,
        *,
        modus: Callable[[], str] = lambda: "standard",
        gesperrt: Callable[[str], bool] = lambda quelle: False,
        clock: Callable[[], datetime] | None = None,
    ):
        self._e = einstellungen
        self._zaehler = zaehler
        self._modus = modus
        self._gesperrt = gesperrt
        self._clock = clock or (lambda: datetime.now().astimezone())

    def pruefen(self, ereignis: Ereignis) -> Entscheidung:
        jetzt = self._clock()
        if ereignis.gueltig_bis is not None and jetzt >= _mit_zone(ereignis.gueltig_bis, jetzt):
            return Entscheidung(False, "verfallen", endgueltig=True)
        if ereignis.nicht_vor is not None and jetzt < _mit_zone(ereignis.nicht_vor, jetzt):
            return Entscheidung(False, f"aufgeschoben bis {ereignis.nicht_vor:%H:%M}")
        if not ereignis.vor_gaesten and self._modus() == GAESTEMODUS:
            return Entscheidung(False, "Gäste da")
        if not ereignis.ungefragt:
            return Entscheidung(True)
        if ereignis.quelle and self._gesperrt(ereignis.quelle):
            return Entscheidung(False, "per Regel abgeschaltet", endgueltig=True)
        modus = self._modus()
        erlaubnis = self._e.modi.get(modus, "alles")
        if erlaubnis == "nichts":
            return Entscheidung(False, f"Modus {modus} erlaubt nichts Ungefragtes")
        if in_ruhezeit(jetzt.time(), self._e.ruhe_von_zeit, self._e.ruhe_bis_zeit):
            return Entscheidung(False, f"Ruhezeit bis {self._e.ruhe_bis}")
        if ereignis.dringend:
            return Entscheidung(True)
        if erlaubnis == "nur_dringendes":
            return Entscheidung(False, f"Modus {modus} erlaubt nur Dringendes")
        if (stand := self._zaehler.stand(jetzt.date())) >= self._e.max_pro_tag:
            return Entscheidung(False, f"schon {stand} ungefragte Ansagen heute")
        return Entscheidung(True)

    def gesprochen(self, ereignis: Ereignis) -> int | None:
        """Nach der Zustellung: Ungefragtes zählt für die Tagesgrenze. Gibt den Stand zurück."""
        if ereignis.ungefragt:
            return self._zaehler.zaehlen(self._clock(), ereignis.beschreibung())
        return None


# Antworten auf eine ungefragte Ansage, im Code erkannt wie NEIN_WOERTER in werkzeuge/basis.py. Ein Satz
# zählt, wenn er nach Füllwörtern mit einer Wendung beginnt; ein einzelnes Wort nur als kurzer Satz.
NICHT_JETZT = ("nicht jetzt", "jetzt nicht", "gerade nicht", "keine zeit", "später", "spaeter", "nachher")
NIE_WIEDER = (
    "nie wieder",
    "lass das",
    "hör auf damit",
    "hoer auf damit",
    "hör damit auf",
    "hoer damit auf",
    "das will ich nicht mehr",
    "das möchte ich nicht mehr",
)
FUELLWOERTER = frozenset(
    "alfred nein nee nö noe ne äh ähm ach ok okay stopp stop halt warte moment bitte danke also".split()
)
KURZ = 3  # so viele Wörter darf ein Satz haben, damit ein einzelnes Wort wie „später" als Antwort zählt


def antwort_art(text: str) -> tuple[str | None, list[str]]:
    """„nicht_jetzt", „nie_wieder" oder None, dazu die Wörter des Satzes ohne Füllwörter am Anfang."""
    woerter = re.findall(r"\w+", text.casefold())
    while woerter and woerter[0] in FUELLWOERTER:
        woerter.pop(0)
    for art, wendungen in (("nicht_jetzt", NICHT_JETZT), ("nie_wieder", NIE_WIEDER)):
        for wendung in wendungen:
            teile = wendung.split()
            if woerter[: len(teile)] == teile and (len(teile) > 1 or len(woerter) <= KURZ):
                return art, woerter
    return None, woerter


class Ansagewaechter(FrameProcessor):
    """Erkennt Alexanders Antwort auf eine ungefragte Ansage im Code.

    „Nicht jetzt" legt die Ansage um `aufschieben` später noch einmal in die Warteschlange.
    „Lass das" oder „Nie wieder" schlägt eine Regel vor, die die Quelle abschaltet; angelegt
    wird sie erst durch ein Ja über `bestaetigen` (Mechanismus von regel_merken). Das
    Transkript geht in beiden Fällen weiter an den Sprecher, mit einem Hinweis, was er sagen
    soll. Der Zusteller meldet jede ungefragte Ansage mit `ansage_gesprochen`; die Antwort
    darauf zählt zwei Minuten lang, bis ein ganzer Satz zu etwas anderem fällt.
    """

    ANTWORT_SECS = 120.0

    def __init__(
        self,
        res,
        context,
        *,
        aufschieben: timedelta,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._res = res
        self._context = context
        self._aufschieben = aufschieben
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._monotonic = monotonic
        self.offen: Ereignis | None = None
        self._offen_seit = 0.0

    @property
    def _nutzer(self) -> str:
        return getattr(getattr(self._res, "persona", None), "nutzer", "Dein Gegenüber")

    def ansage_gesprochen(self, ereignis: Ereignis) -> None:
        """Der Zusteller meldet eine ungefragte Ansage; die nächsten Worte können sich darauf beziehen."""
        self.offen = ereignis
        self._offen_seit = self._monotonic()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and self.offen is not None:
            self._antworten(frame.text)
        await self.push_frame(frame, direction)

    def _antworten(self, text: str) -> None:
        ereignis, self.offen = self.offen, None
        if self._monotonic() - self._offen_seit > self.ANTWORT_SECS:
            return
        art, woerter = antwort_art(text)
        if art == "nicht_jetzt":
            self._aufschieben_(ereignis)
        elif art == "nie_wieder":
            self._regel_vorschlagen(ereignis)
        elif len(woerter) <= 2:
            self.offen = ereignis  # ein Einwurf wie „mhm": die Antwort kann noch kommen

    def _aufschieben_(self, ereignis: Ereignis) -> None:
        bis = self._clock() + self._aufschieben
        gueltig_bis = ereignis.gueltig_bis
        if gueltig_bis is not None:
            # Aufgeschoben heißt gewollt: Die Ansage darf nicht vorher verfallen.
            gueltig_bis = max(_mit_zone(gueltig_bis, bis), bis + self._aufschieben)
        self._res.ereignisse.put_nowait(
            replace(ereignis, nicht_vor=bis, gueltig_bis=gueltig_bis, gemeldet=False, push_versucht=False)
        )
        logger.info(f"Ansage aufgeschoben bis {bis:%H:%M}: {ereignis.beschreibung()}")
        self._context.add_message(
            {
                "role": "developer",
                "content": (
                    f"Hinweis vom System, nicht vom Gegenüber: {self._nutzer} will das jetzt nicht hören. "
                    f"Du meldest dich damit um {uhrzeit_gesprochen(bis)} noch einmal. Sag das in einem "
                    "kurzen Satz, kein Werkzeugaufruf nötig."
                ),
            }
        )

    def _regel_vorschlagen(self, ereignis: Ereignis) -> None:
        if not ereignis.quelle:
            logger.info(f"„Nie wieder\" ohne Quelle, keine Regel möglich: {ereignis.beschreibung()}")
            return
        regel = f"{ANSAGE_SPERRE}{ereignis.quelle}"
        res = self._res

        async def anlegen():
            res.gedaechtnis.regel_merken(regel)
            if res.systemprompt is not None:
                from pipecat.frames.frames import LLMUpdateSettingsFrame
                from pipecat.services.mistral.llm import MistralLLMService

                await self.push_frame(
                    LLMUpdateSettingsFrame(delta=MistralLLMService.Settings(system_instruction=res.systemprompt()))
                )
            return {"gemerkt": regel, "hinweis": "Sag in einem Satz, dass du das nicht mehr von dir aus ansagst."}

        res.offene_freigabe = Freigabe(f"Neue Regel: {regel}", anlegen)
        logger.info(f"Regel vorgeschlagen, wartet auf ein Ja: {regel}")
        self._context.add_message(
            {
                "role": "developer",
                "content": (
                    f"Hinweis vom System, nicht vom Gegenüber: {self._nutzer} will {ereignis.quelle} nicht "
                    "mehr ungefragt hören. Frag in einem Satz, ob du dir das dauerhaft als Regel merken "
                    "sollst. Bei Ja rufst du bestaetigen auf, bei Nein abbrechen; sonst kein Werkzeugaufruf."
                ),
            }
        )


class Zusteller:
    """Spricht Ereignisse aus, sobald Ruhe ist, und hält Ungefragtes (im Gästemodus fast alles)
    nach den Zustellregeln zurück.

    Zurückgehaltenes bleibt in `_wartend` und wird jeden Takt neu geprüft, bis es erlaubt ist,
    als Push zugestellt wurde oder verfallen ist. Die Liste gehört dem Zusteller, nicht dem
    Lauf: Startet die Aufsicht `laufen` nach einem Fehler neu, geht nichts verloren.

    Endet der ganze Sprach-Loop, legt `zurueckgeben` Zurückgehaltenes und noch nicht hörbar
    Gesprochenes zurück in die Warteschlange. Im Raum überlebt sie den Neuaufbau (room.run_room):
    Erinnerungen und Routinen gelten schon beim Einreihen als erledigt, ein Anlauf, der danach
    scheitert, darf sie nicht mitnehmen (16.10.2026).
    """

    def __init__(
        self,
        ereignisse: asyncio.Queue,
        context,
        worker,
        zustand: Gespraechszustand,
        *,
        gate=None,
        ruhe_secs: float = 1.5,
        regeln: Zustellregeln | None = None,
        waechter: Ansagewaechter | None = None,
        benachrichtigen: Callable[[str], Awaitable[None]] | None = None,
        takt: float = 30.0,
        fremder_inhalt: Callable[[str], None] | None = None,
        bereit: asyncio.Event | None = None,
        quittung_secs: float | None = None,
    ):
        """
        Args:
            regeln: Zustellregeln für Ungefragtes; ohne sie kommt alles sofort durch.
            waechter: Erfährt jede ungefragte Ansage, um „nicht jetzt" und „lass das" zuzuordnen.
            benachrichtigen: Schickt einen Text aufs Telefon, für Zurückgehaltenes mit Push-Text.
            takt: So oft wird Zurückgehaltenes neu geprüft.
            fremder_inhalt: Wird mit der Herkunft gerufen, wenn ein Ereignis fremden Text ins
                Gespräch bringt (Ressourcen.fremden_inhalt_merken).
            bereit: Gesetzt, sobald die Pipeline läuft und Mikrofon und Lautsprecher offen sind.
                Vorher nimmt der Zusteller nichts aus der Warteschlange.
            quittung_secs: Ein Ereignis gilt erst als zugestellt, wenn Alfred danach hörbar zu
                sprechen beginnt, spätestens nach so vielen Sekunden; erst dann zählt Ungefragtes
                für die Tagesgrenze und erfährt der Wächter davon. None: sofort (ohne Lautsprecher).
        """
        self._ereignisse = ereignisse
        self._context = context
        self._worker = worker
        self._zustand = zustand
        self._gate = gate
        self._ruhe_secs = ruhe_secs
        self._regeln = regeln
        self._waechter = waechter
        self._benachrichtigen = benachrichtigen
        self._takt = takt
        self._fremder_inhalt = fremder_inhalt
        self._bereit = bereit
        self._quittung_secs = quittung_secs
        self._wartend: list[Ereignis] = []

    @property
    def wartend(self) -> list[Ereignis]:
        return list(self._wartend)

    async def laufen(self) -> None:
        if self._bereit is not None:
            # Was der Worker vor dem Start annimmt, verschwindet mit einem Anlauf, der beim Öffnen
            # der Geräte scheitert; in der Warteschlange wartet es auf den nächsten.
            await self._bereit.wait()
        while True:
            if (neu := await self._abholen()) is not None:
                self._wartend.append(als_ereignis(neu))
            await self._zustellen()

    def zurueckgeben(self) -> list[Ereignis]:
        """Legt Zurückgehaltenes und Ungesprochenes zurück in die Warteschlange, vor das Neuere.

        Für das Ende des Sprach-Loops, nachdem `laufen` abgebrochen ist. Gibt zurück, was der
        Zusteller schon in der Hand hatte.
        """
        zurueck, self._wartend = self._wartend, []
        neuer = []
        while not self._ereignisse.empty():
            neuer.append(self._ereignisse.get_nowait())
        for ereignis in zurueck:
            ereignis.gemeldet = False  # wartet es im nächsten Lauf wieder, steht das erneut im Log
        for eintrag in [*zurueck, *neuer]:
            self._ereignisse.put_nowait(eintrag)
        if zurueck or neuer:
            liste = "; ".join(als_ereignis(e).beschreibung() for e in [*zurueck, *neuer])
            logger.info(f"Noch nicht gesprochen, bleibt in der Warteschlange: {liste}")
        return zurueck

    async def _abholen(self) -> Zustellbar | None:
        """Das nächste Ereignis; wartet höchstens einen Takt, solange etwas zurückgehalten ist."""
        if not self._wartend:
            return await self._ereignisse.get()
        try:
            return await asyncio.wait_for(self._ereignisse.get(), timeout=self._takt)
        except asyncio.TimeoutError:
            return None

    async def _zustellen(self) -> None:
        for ereignis in list(self._wartend):
            entscheidung = self._regeln.pruefen(ereignis) if self._regeln else Entscheidung(True)
            if entscheidung.erlaubt:
                self._wartend.remove(ereignis)
                try:
                    await self._aussprechen(ereignis)
                except asyncio.CancelledError:
                    # Der Lauf endet, bevor Alfred es hörbar gesprochen hat: Es gehört dem nächsten.
                    self._wartend.insert(0, ereignis)
                    raise
            elif entscheidung.endgueltig:
                self._wartend.remove(ereignis)
                logger.info(f"Ereignis {entscheidung.grund}: {ereignis.beschreibung()}")
            elif await self._pushen(ereignis, entscheidung.grund):
                self._wartend.remove(ereignis)
            elif not ereignis.gemeldet:
                ereignis.gemeldet = True
                logger.info(f"Ereignis wartet ({entscheidung.grund}): {ereignis.beschreibung()}")

    async def _pushen(self, ereignis: Ereignis, grund: str) -> bool:
        """Was nicht gesprochen werden darf, geht aufs Telefon, wenn es einen Push-Text hat."""
        if not ereignis.push or self._benachrichtigen is None or ereignis.push_versucht:
            return False
        ereignis.push_versucht = True
        try:
            await self._benachrichtigen(ereignis.push)
        except Exception as e:  # noqa: BLE001 - dann wartet die Ansage eben
            logger.warning(f"Push statt Ansage fehlgeschlagen ({grund}), Ansage wartet: {e}")
            return False
        logger.info(f"Ereignis als Push zugestellt ({grund}): {ereignis.beschreibung()}")
        return True

    async def _aussprechen(self, ereignis: Ereignis) -> None:
        while self._zustand.ruhig_seit() < self._ruhe_secs:
            await asyncio.sleep(0.2)
        if self._gate is not None:
            # Damit eine Antwort auf Alfreds Ansage kein Wachwort braucht.
            await self._gate.aufwecken("Ereignis")
        if ereignis.herkunft and self._fremder_inhalt is not None:
            # Nach dem Aufwecken: Beginnt dort ein neues Gespräch, gehört die Marke zum neuen.
            self._fremder_inhalt(ereignis.herkunft)
        vorher = self._zustand.sprechbeginne
        if isinstance(ereignis.inhalt, Ansage):
            await self._ansagen(ereignis.inhalt)
        else:
            logger.info(f"Ereignis wird ausgesprochen: {ereignis.inhalt[:80]}")
            self._context.add_message({"role": "developer", "content": ereignis.inhalt})
            await self._worker.queue_frames([LLMRunFrame()])
        if self._quittung_secs is not None and not await self._quittung_abwarten(vorher):
            logger.warning(
                f"Nach {self._quittung_secs:.0f} s kein Ton zum Ereignis, es gilt trotzdem als "
                f"zugestellt: {ereignis.beschreibung()}"
            )
        if self._regeln and (stand := self._regeln.gesprochen(ereignis)) is not None:
            logger.info(f"Ungefragte Ansage {stand} heute: {ereignis.beschreibung()}")
        if ereignis.ungefragt and self._waechter is not None:
            self._waechter.ansage_gesprochen(ereignis)

    async def _quittung_abwarten(self, vorher: int) -> bool:
        """Wartet, bis Alfred hörbar zu sprechen beginnt; False, wenn das binnen der Frist ausbleibt.

        Mit dem Ereignis im Kontext ist es noch nicht gesagt: Bricht der Worker ab (Gerätefehler),
        bleiben LLMRunFrame und Ansage in seiner Warteschlange liegen, und kein Ton kommt.
        """
        frist = time.monotonic() + self._quittung_secs
        while self._zustand.sprechbeginne == vorher:
            if time.monotonic() >= frist:
                return False
            await asyncio.sleep(0.1)
        return True

    async def _ansagen(self, ansage: Ansage) -> None:
        logger.info(f"Ansage: {ansage.text}")
        frames = []
        if ansage.klingeln:
            frames.append(OutputAudioRawFrame(audio=klingelton(), sample_rate=KLINGEL_RATE, num_channels=1))
        frames.append(TTSSpeakFrame(ansage.text))
        await self._worker.queue_frames(frames)
        # Im Verlauf, damit das Modell weiß, was gesagt wurde ("Danke", "noch mal fünf Minuten").
        self._context.add_message({"role": "assistant", "content": ansage.verlauf or ansage.text})
