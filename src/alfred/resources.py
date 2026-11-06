"""Was alle Werkzeuge einer Sitzung teilen. Pipecat reicht es als app_resources durch."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from mistralai.client import Mistral

    from alfred.aufgaben import Aufgaben
    from alfred.auftraege import Auftraege
    from alfred.benachrichtigung import Benachrichtiger
    from alfred.codewort import Codewort
    from alfred.config import Settings
    from alfred.denker import Denker
    from alfred.einkauf import Einkaufsliste
    from alfred.erinnerungen import Erinnerungen
    from alfred.freigaben import Freigabedatei
    from alfred.ernaehrung import Ernaehrung, Schaetzer
    from alfred.gedaechtnis import Gedaechtnis
    from alfred.haushalt import Ausgaben, Vorrat, Wochenplan
    from alfred.kalender import Kalender
    from alfred.kosten import Kostenzaehler
    from alfred.mail import Mail, Nachricht
    from alfred.mitschrift import Mitschrift
    from alfred.musik import Musik
    from alfred.persona import Persona
    from alfred.routinen import Routinen
    from alfred.stimme import Stimmpruefer
    from alfred.suche import Suchmaschine
    from alfred.turns import Zuhoeren
    from alfred.wetter import Wetter
    from alfred.whatsapp import Kontakte, Meldeliste, WhatsApp


class Gesprochen(dict):
    """Ergebnis einer Aktion, die ihre Bestätigung selbst gesprochen hat (Werkzeug mit run_llm=False).

    Läuft sie erst nach einer Rückfrage (bestaetigen, Freigabe vom Handy), muss das mit: Sonst
    redete das Modell danach weiter, und Alexander hörte die Bestätigung doppelt (9.10.2026,
    „Verstanden, ich übernehme" nach einem Auftrag mit Rückfrage).
    """


@dataclass
class Freigabe:
    """Eine Aktion, die auf ein Ja des Nutzers wartet."""

    beschreibung: str  # für Rückfrage und Push, mit allen Werten, auch Adresse und Nummer
    ausfuehren: Callable[[], Awaitable[Any]]
    protokoll: str = ""  # Fassung für Log und Aktionsprotokoll, ohne Telefonnummer; leer = beschreibung
    grund: str = ""  # warum ein sonst freies Werkzeug nachfragt, etwa fremder Inhalt im Gespräch
    mit_params: bool = False  # die Aktion nimmt den bestätigenden Aufruf entgegen (Schutzhülle)
    erstellt: float = field(default_factory=time.monotonic)
    # Der Werkzeugaufruf, der die Rückfrage angelegt hat; an ihm findet bestaetigen im Verlauf,
    # ob Alfred die Frage danach gestellt hat und was das Gegenüber darauf gesagt hat.
    tool_call_id: str | None = None
    # Was das Gegenüber seit dem Anlegen gesagt hat (Transkripte, im App-Chat je Text). Ein Ja zählt
    # erst danach (sonst bestätigt sich das Modell im selben Zug selbst, GLM 5.3 in der Eval-Suite),
    # und ob es eins ist, entscheidet bestaetigen im Code (basis.zustimmung), nicht das Modell.
    antworten: list[str] = field(default_factory=list)
    # Heikle Aktion im Sprach-Loop mit gesetztem Codewort: Statt eines Ja zählt nur das Codewort
    # (codewort.py); nach MAX_FEHLVERSUCHE Antworten ohne verfällt die Rückfrage. Gezählt wird je
    # neuer Antwort, nicht je Aufruf: Ruft das Modell bestaetigen zweimal auf dieselbe Antwort auf,
    # ist das ein Versuch.
    mit_codewort: bool = False
    fehlversuche: int = 0
    gezaehlt_bis: int = 0  # so viele Einträge aus antworten sind schon als Versuch gezählt

    GUELTIG_SECS = 300.0  # danach gilt ein Ja nicht mehr; das Werkzeug muss neu fragen
    MAX_FEHLVERSUCHE = 3

    @property
    def abgelaufen(self) -> bool:
        return time.monotonic() - self.erstellt > self.GUELTIG_SECS

    async def starten(self, params: Any = None) -> Any:
        """Führt die Aktion aus.

        Ein schreibendes Werkzeug in der Schutzhülle läuft erst hier; es bekommt den bestätigenden
        Aufruf (Sprachausgabe, Kontext), damit etwa auftrag_geben seine Bestätigung dort spricht.
        Vom Handy (freigaben_beobachten) gibt es keinen Aufruf, dann nimmt es den ursprünglichen.
        """
        return await (self.ausfuehren(params) if self.mit_params else self.ausfuehren())

    @property
    def protokolltext(self) -> str:
        """Die Zeile fürs Aktionsprotokoll: was gefragt wurde und, falls es einen gab, der Grund."""
        text = self.protokoll or self.beschreibung
        return f"{text} [Rückfrage: {self.grund}]" if self.grund else text

    def antwort(self, text: str) -> None:
        """Merkt einen Satz des Gegenübers seit der Rückfrage."""
        if text.strip():
            self.antworten.append(text.strip())


@dataclass
class FremderInhalt:
    """Fremder Text ist im Gespräch: eine Mail, eine Nachricht, Suchtreffer, ein Bericht.

    Das Modell hält Anweisungen in solchen Texten gelegentlich für Aufträge. Solange die Marke
    steht, fragen deshalb auch die sonst freien schreibenden Werkzeuge nach (Stufe schreibend,
    werkzeuge/basis.py: _mit_fremdschutz), und zwar mit allen Werten, damit ein untergeschobener
    auffällt. Die Marke endet mit dem Gespräch (Sitzung.neues_gespraech, Textgespraech beim
    Kürzen), nicht nach einer Frist.
    """

    herkunft: str  # etwa "Mail von Aaron Müller" oder "Websuche"
    seit: datetime

    @property
    def grund(self) -> str:
        """Für Rückfrage, Push und Aktionsprotokoll."""
        return f"fremder Inhalt im Gespräch ({self.herkunft})"


@dataclass
class Ressourcen:
    settings: Settings
    persona: Persona
    client: Mistral
    gedaechtnis: Gedaechtnis
    erinnerungen: Erinnerungen
    denker: Denker
    kalender: Kalender | None = None
    routinen: Routinen | None = None
    wetter: Wetter | None = None
    whatsapp: WhatsApp | None = None  # nur wenn die Brücke eingerichtet ist
    kontakte: Kontakte | None = None
    meldeliste: Meldeliste | None = None  # von wem neue WhatsApp-Nachrichten angesagt werden
    aufgaben: Aufgaben | None = None
    auftraege: Auftraege | None = None  # Aufträge an Alfred; abgearbeitet im Sprach-Prozess
    einkauf: Einkaufsliste | None = None
    ernaehrung: Ernaehrung | None = None
    schaetzer: Schaetzer | None = None
    ausgaben: Ausgaben | None = None
    vorrat: Vorrat | None = None
    wochenplan: Wochenplan | None = None
    benachrichtiger: Benachrichtiger | None = None  # Push aufs Telefon, nur wenn konfiguriert
    kosten: Kostenzaehler | None = None
    mail: Mail | None = None  # nur mit MAIL_ADRESSE und MAIL_PASSWORT in .env
    letzte_mails: list[Nachricht] = field(default_factory=list)  # zuletzt aufgezählte Mails, für Nummern
    stimmpruefer: Stimmpruefer | None = None  # nur mit stimmerkennung.aktiv und angelerntem Profil
    # Nur im Sprach-Loop und nur mit ALFRED_CODEWORT in .env: Heikle Aktionen verlangen es dann statt
    # eines Ja. Im App-Chat fehlt es, dort reicht ein Ja.
    codewort: Codewort | None = None
    systemprompt: Callable[[], str] | None = None  # baut den aktuellen Systemprompt, zum Auffrischen
    suche: Suchmaschine | None = None  # eigene Suchmaschine (SearXNG), sonst Mistrals Websuche
    musik: Musik | None = None  # Spotify, nur mit Zugangsdaten in .env und eingerichtetem Zugang
    mitschrift: Mitschrift | None = None  # Stand des Gesprächs für die App, nur im Sprach-Prozess
    schleuse: Any = None  # Wachwort-Schleuse, nur im Raum; gespraech_beenden schließt sie
    zuhoeren: Zuhoeren | None = None  # Zuhör-Modus des Sprach-Loops; im App-Chat gibt es keinen
    # Was Alfred von sich aus ausspricht, sobald im Gespräch Ruhe ist: Texte, Ansagen und Ereignisse
    # (ereignisse.py); nackte Texte zählen als angefordert. Im Raum hält die Aufsicht sie über den
    # Neuaufbau nach einem Fehler hinweg (room.run_room, build_voice_loop).
    ereignisse: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    offene_freigabe: Freigabe | None = None
    freigabedatei: Freigabedatei | None = None  # Freigaben vom Handy, geteilt mit der App
    fremder_inhalt: FremderInhalt | None = None  # steht, sobald fremder Text im Gespräch ist

    def fremden_inhalt_merken(self, herkunft: str) -> None:
        """Vermerkt fremden Text im Gespräch; bei mehreren zählt der jüngste."""
        self.fremder_inhalt = FremderInhalt(herkunft, datetime.now().astimezone())
        logger.info(f"Fremder Inhalt im Gespräch ({herkunft}): schreibende Werkzeuge fragen ab jetzt nach")

    def fremden_inhalt_vergessen(self) -> None:
        """Mit dem Gespräch endet die Marke; schreibende Werkzeuge laufen wieder frei."""
        if self.fremder_inhalt is not None:
            logger.info("Fremder Inhalt vergessen: neues Gespräch, schreibende Werkzeuge laufen wieder frei")
        self.fremder_inhalt = None
