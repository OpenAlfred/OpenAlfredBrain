"""Eval-Suite auf Textebene: Szenarien gegen das echte Sprecher-Modell, ohne Audio.

Start:  python -m alfred.eval [NAME ...] [--wiederholungen N] [--modell MODELL] [--szenarien DATEI] [--liste]

Jedes Szenario ist ein kurzes Gespräch aus einem oder mehreren Sätzen. Je Satz steht dabei, welche
Werkzeuge das Modell aufrufen soll (mit Argumenten als regulären Ausdrücken) oder dass es keins
aufruft, und was in der Antwort stehen muss oder nicht stehen darf. Die Szenarien liegen in
tests/eval_szenarien.yaml (das Format steht dort im Kopf); die meisten stammen aus Fehlern echter
Gespräche (CLAUDE.md, „Erkenntnisse aus dem ersten echten Lauf"), damit sie nicht wiederkommen.

Die Kette ist dieselbe wie in der App (textgespraech.py): gleicher Systemprompt, gleiche Werkzeuge,
gleicher Code für Freigaben. Nur Dienste mit Nebenwirkungen oder Kosten (WhatsApp, Mail, Push,
Kalender, Wetter, Denker, Schätzer) sind Attrappen: Nichts wird verschickt, und nur der Sprecher
kostet. Gedächtnis ist gedaechtnis/selbsttest, vor jedem Szenario geleert; sonst beantwortet das
Modell Fragen aus dem Protokoll des vorigen Laufs statt aus dem Szenario.

Ein Lauf kostet Cent-Beträge und gehört deshalb nicht in die pytest-Suite; die Auswertung ohne
Modell prüft tests/test_eval.py. Sprachmodelle sind nicht deterministisch: Mit --wiederholungen N
läuft jedes Szenario N-mal und gilt nur als bestanden, wenn alle Läufe bestehen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger

from alfred.codewort import Codewort, untauglich
from alfred.config import CONFIG_DIR, Settings, load_settings
from alfred.denker import _inhalt_text
from alfred.kalender import Termin
from alfred.kosten import modellkosten
from alfred.mail import Nachricht
from alfred.persona import WOCHENTAGE, Persona, jahr_gesprochen, load_persona, uhrzeit_gesprochen
from alfred.pipeline import build_ressourcen, require_api_key, system_prompt
from alfred.resources import Ressourcen
from alfred.textgespraech import Textgespraech
from alfred.werkzeuge import REGISTER
from alfred.wetter import GEOCODING_URL, Wetter

SZENARIEN_DATEI = CONFIG_DIR.parent / "tests" / "eval_szenarien.yaml"
TESTORDNER = "selbsttest"  # nur einen Gedächtnisordner mit diesem Namen leert die Suite
ZEICHEN = {True: "✔", False: "✘"}
MAX_ERGEBNIS_ZEICHEN = 160  # so viel eines Werkzeugergebnisses zeigt die Ausgabe

# Alexanders Telefonbuch aus Sicht der WhatsApp-Attrappe: genau ein Jan, damit "Schreib Jan" eindeutig ist.
ADRESSBUCH = [
    {"name": "Jan Müller", "nummer": "491701111111"},
    {"name": "Mama", "nummer": "491607654321"},
    {"name": "Lea Schulz", "nummer": "491703333333"},
]


# --- Szenarien -----------------------------------------------------------------------------------


@dataclass
class Werkzeugerwartung:
    name: str
    # Argumentname zu Muster: ein Text ist ein regulärer Ausdruck, eine Zahl wird verglichen.
    argumente: dict[str, Any] = field(default_factory=dict)


@dataclass
class Erwartung:
    werkzeuge: list[Werkzeugerwartung] = field(default_factory=list)  # müssen aufgerufen werden
    erlaubt: list[Werkzeugerwartung] = field(default_factory=list)  # dürfen zusätzlich aufgerufen werden
    verboten: list[str] = field(default_factory=list)  # ihr Aufruf ist ein eigens benannter Mangel
    antwort_enthaelt: list[str] = field(default_factory=list)
    antwort_nicht: list[str] = field(default_factory=list)
    antwort_vom_werkzeug: bool = False  # die Antwort kam vom Werkzeug (run_llm=False), das Modell hängte nichts an
    freigabe_offen: bool | None = None  # ob nach dem Schritt eine Rückfrage offen ist
    freigabe_enthaelt: list[str] = field(default_factory=list)


@dataclass
class Schritt:
    sage: str
    erwartung: Erwartung


@dataclass
class Szenario:
    name: str
    grund: str = ""
    schritte: list[Schritt] = field(default_factory=list)
    profil: dict[str, str] = field(default_factory=dict)  # Fakten im Profil vor dem Gespräch
    kontakte: dict[str, str] = field(default_factory=dict)  # eigene Kontakte: Name zu Nummer
    kalender: list[dict] = field(default_factory=list)  # Termine der Kalender-Attrappe
    mails: list[dict] = field(default_factory=list)  # ungelesene Mails der Mail-Attrappe
    # Wie im Sprach-Loop mit ALFRED_CODEWORT: Heikle Aktionen verlangen es, und in jedem Satz steht
    # es nur als codewort.MARKE, so wie der Codewortschutz es dem Modell zeigt.
    codewort: str = ""


_SZENARIO_SCHLUESSEL = {"name", "grund", "schritte", "profil", "kontakte", "kalender", "mails", "codewort"}
_SCHRITT_SCHLUESSEL = {
    "sage", "werkzeuge", "kein_werkzeug", "erlaubt", "verboten", "antwort_enthaelt", "antwort_nicht",
    "antwort_vom_werkzeug", "freigabe_offen", "freigabe_enthaelt",
}  # fmt: skip
_TERMIN_SCHLUESSEL = {"titel", "tag", "von", "bis"}
_MAIL_SCHLUESSEL = {"von", "adresse", "betreff", "text"}


def werkzeugnamen() -> set[str]:
    """Die Namen aller Werkzeuge im Vollausbau, um Tippfehler in Szenarien sofort zu finden."""
    return set(REGISTER)


def _texte(wert: Any, was: str) -> list[str]:
    if wert is None:
        return []
    if isinstance(wert, str):
        return [wert]
    if isinstance(wert, list) and all(isinstance(w, str) for w in wert):
        return list(wert)
    raise ValueError(f"{was} muss ein Text oder eine Liste von Texten sein")


def _werkzeugerwartungen(eintraege: Any, wo: str, namen: set[str]) -> list[Werkzeugerwartung]:
    if eintraege is None:
        return []
    if not isinstance(eintraege, list):
        raise ValueError(f"{wo}: eine Liste von Werkzeugen erwartet")
    erwartungen = []
    for eintrag in eintraege:
        if isinstance(eintrag, str):
            eintrag = {"name": eintrag}
        if not isinstance(eintrag, dict) or not eintrag.get("name"):
            raise ValueError(f"{wo}: ein Werkzeug braucht einen Namen: {eintrag!r}")
        if unbekannt := set(eintrag) - {"name", "argumente"}:
            raise ValueError(f"{wo}: unbekannte Angaben beim Werkzeug {eintrag['name']}: {sorted(unbekannt)}")
        if eintrag["name"] not in namen:
            raise ValueError(f"{wo}: unbekanntes Werkzeug '{eintrag['name']}'")
        argumente = eintrag.get("argumente") or {}
        if not isinstance(argumente, dict):
            raise ValueError(f"{wo}: argumente von {eintrag['name']} müssen Name zu Muster sein")
        erwartungen.append(Werkzeugerwartung(str(eintrag["name"]), dict(argumente)))
    return erwartungen


def _muster_pruefen(muster: list[Any], wo: str, jetzt: datetime) -> None:
    for m in muster:
        if not isinstance(m, str):
            continue
        try:
            re.compile(platzhalter(m, jetzt))
        except re.error as e:
            raise ValueError(f"{wo}: ungültiger regulärer Ausdruck {m!r}: {e}") from e


def _erwartung(daten: dict, wo: str, namen: set[str], jetzt: datetime) -> Erwartung:
    if unbekannt := set(daten) - _SCHRITT_SCHLUESSEL:
        raise ValueError(f"{wo}: unbekannte Angaben {sorted(unbekannt)}")
    if "werkzeuge" not in daten and not daten.get("kein_werkzeug"):
        raise ValueError(f"{wo}: 'werkzeuge' (auch leer) oder 'kein_werkzeug: true' fehlt")
    if daten.get("kein_werkzeug") and daten.get("werkzeuge"):
        raise ValueError(f"{wo}: 'kein_werkzeug' und 'werkzeuge' zugleich")
    offen = daten.get("freigabe_offen")
    if offen is not None and not isinstance(offen, bool):
        raise ValueError(f"{wo}: freigabe_offen muss true oder false sein")
    erwartung = Erwartung(
        werkzeuge=_werkzeugerwartungen(daten.get("werkzeuge"), wo, namen),
        erlaubt=_werkzeugerwartungen(daten.get("erlaubt"), wo, namen),
        verboten=_texte(daten.get("verboten"), f"{wo}: verboten"),
        antwort_enthaelt=_texte(daten.get("antwort_enthaelt"), f"{wo}: antwort_enthaelt"),
        antwort_nicht=_texte(daten.get("antwort_nicht"), f"{wo}: antwort_nicht"),
        antwort_vom_werkzeug=bool(daten.get("antwort_vom_werkzeug", False)),
        freigabe_offen=offen,
        freigabe_enthaelt=_texte(daten.get("freigabe_enthaelt"), f"{wo}: freigabe_enthaelt"),
    )
    for name in erwartung.verboten:
        if name not in namen:
            raise ValueError(f"{wo}: unbekanntes Werkzeug '{name}' unter verboten")
    _muster_pruefen(erwartung.antwort_enthaelt + erwartung.antwort_nicht + erwartung.freigabe_enthaelt, wo, jetzt)
    for w in erwartung.werkzeuge + erwartung.erlaubt:
        _muster_pruefen(list(w.argumente.values()), f"{wo}, {w.name}", jetzt)
    return erwartung


def lade_szenarien(pfad: Path = SZENARIEN_DATEI) -> list[Szenario]:
    """Liest die Szenario-Datei und prüft sie streng: Ein Tippfehler soll hier auffallen, nicht erst
    nach einem bezahlten Lauf als „Werkzeug nicht aufgerufen"."""
    daten = yaml.safe_load(pfad.read_text(encoding="utf-8")) or []
    if not isinstance(daten, list):
        raise ValueError(f"{pfad}: eine Liste von Szenarien erwartet")
    namen = werkzeugnamen()
    jetzt = datetime.now().astimezone()
    szenarien: list[Szenario] = []
    for i, eintrag in enumerate(daten, start=1):
        if not isinstance(eintrag, dict) or not eintrag.get("name"):
            raise ValueError(f"{pfad}: Szenario {i} hat keinen Namen")
        name = str(eintrag["name"])
        if any(s.name == name for s in szenarien):
            raise ValueError(f"{pfad}: Szenario '{name}' kommt doppelt vor")
        if unbekannt := set(eintrag) - _SZENARIO_SCHLUESSEL:
            raise ValueError(f"Szenario '{name}': unbekannte Angaben {sorted(unbekannt)}")
        schritte = eintrag.get("schritte") or []
        if not isinstance(schritte, list) or not schritte:
            raise ValueError(f"Szenario '{name}': keine Schritte")
        szenario = Szenario(
            name=name,
            grund=" ".join(str(eintrag.get("grund") or "").split()),
            profil={str(k): str(v) for k, v in (eintrag.get("profil") or {}).items()},
            kontakte={str(k): str(v) for k, v in (eintrag.get("kontakte") or {}).items()},
            kalender=list(eintrag.get("kalender") or []),
            mails=list(eintrag.get("mails") or []),
            codewort=str(eintrag.get("codewort") or ""),
        )
        if szenario.codewort and (fehler := untauglich(szenario.codewort)):
            raise ValueError(f"Szenario '{name}': {fehler}")
        for j, schritt in enumerate(schritte, start=1):
            wo = f"Szenario '{name}', Schritt {j}"
            if not isinstance(schritt, dict) or not str(schritt.get("sage") or "").strip():
                raise ValueError(f"{wo}: 'sage' fehlt")
            sage = " ".join(str(schritt["sage"]).split())
            platzhalter(sage, jetzt)  # unbekannte Platzhalter melden
            szenario.schritte.append(Schritt(sage, _erwartung(schritt, wo, namen, jetzt)))
        for termin in szenario.kalender:
            if not isinstance(termin, dict) or set(termin) != _TERMIN_SCHLUESSEL:
                raise ValueError(f"Szenario '{name}': ein Termin braucht genau {sorted(_TERMIN_SCHLUESSEL)}")
            _zeitpunkt(str(termin["tag"]), str(termin["von"]), jetzt)
            _zeitpunkt(str(termin["tag"]), str(termin["bis"]), jetzt)
        for mail in szenario.mails:
            if not isinstance(mail, dict) or set(mail) != _MAIL_SCHLUESSEL:
                raise ValueError(f"Szenario '{name}': eine Mail braucht genau {sorted(_MAIL_SCHLUESSEL)}")
        szenarien.append(szenario)
    return szenarien


# --- Platzhalter ---------------------------------------------------------------------------------

_PLATZHALTER = re.compile(
    r"\{(heute|morgen|jahr|jahr_gesprochen|jahr_muster|stunde|stunde_gesprochen|naechster:[^}]+)\}"
)


def jahr_muster(jahr: int) -> str:
    """Regulärer Ausdruck für die Jahreszahl: als Ziffern oder ausgeschrieben, auch mit Leerzeichen
    zwischen den Teilen („zweitausend sechsundzwanzig")."""
    wort = re.sub(r"und(?!ert)", r"\\s*und\\s*", jahr_gesprochen(jahr))  # nicht das "und" in "hundert"
    wort = re.sub(r"(tausend|hundert)", r"\1\\s*", wort)
    return f"{wort}|\\b{jahr}\\b"


def platzhalter(text: str, jetzt: datetime) -> str:
    """Setzt Zeitangaben ein, die beim Schreiben eines Szenarios noch nicht feststehen.

    {heute}, {morgen}: JJJJ-MM-TT. {naechster:Mittwoch}: der nächste Mittwoch wie in der
    Wochentagsliste des Systemprompts (morgen bis in sieben Tagen; heute zählt nicht). {jahr},
    {jahr_gesprochen}, {jahr_muster} (regulärer Ausdruck für Ziffern oder Wort), {stunde} (0 bis 23),
    {stunde_gesprochen} („dreizehn Uhr").
    """

    def ersetzen(treffer: re.Match) -> str:
        name = treffer.group(1)
        if name == "heute":
            return jetzt.strftime("%Y-%m-%d")
        if name == "morgen":
            return (jetzt + timedelta(days=1)).strftime("%Y-%m-%d")
        if name == "jahr":
            return str(jetzt.year)
        if name == "jahr_gesprochen":
            return jahr_gesprochen(jetzt.year)
        if name == "jahr_muster":
            return jahr_muster(jetzt.year)
        if name == "stunde":
            return str(jetzt.hour)
        if name == "stunde_gesprochen":
            return uhrzeit_gesprochen(jetzt.replace(minute=0))
        wochentag = name.split(":", 1)[1].strip().capitalize()
        if wochentag not in WOCHENTAGE:
            raise ValueError(f"Unbekannter Wochentag im Platzhalter {{{name}}}")
        for i in range(1, 8):
            tag = jetzt + timedelta(days=i)
            if WOCHENTAGE[tag.weekday()] == wochentag:
                return tag.strftime("%Y-%m-%d")
        raise AssertionError("unerreichbar")

    return _PLATZHALTER.sub(ersetzen, text)


def aufgeloest(erwartung: Erwartung, jetzt: datetime) -> Erwartung:
    """Die Erwartung mit eingesetzten Platzhaltern in allen Mustern."""

    def argumente(w: Werkzeugerwartung) -> Werkzeugerwartung:
        return Werkzeugerwartung(
            w.name, {k: platzhalter(v, jetzt) if isinstance(v, str) else v for k, v in w.argumente.items()}
        )

    return replace(
        erwartung,
        werkzeuge=[argumente(w) for w in erwartung.werkzeuge],
        erlaubt=[argumente(w) for w in erwartung.erlaubt],
        antwort_enthaelt=[platzhalter(m, jetzt) for m in erwartung.antwort_enthaelt],
        antwort_nicht=[platzhalter(m, jetzt) for m in erwartung.antwort_nicht],
        freigabe_enthaelt=[platzhalter(m, jetzt) for m in erwartung.freigabe_enthaelt],
    )


def _zeitpunkt(tag: str, uhrzeit: str, jetzt: datetime) -> datetime:
    try:
        return datetime.strptime(f"{platzhalter(tag, jetzt)} {uhrzeit}", "%Y-%m-%d %H:%M").replace(tzinfo=jetzt.tzinfo)
    except ValueError as e:
        raise ValueError(f"Termin unverständlich (tag JJJJ-MM-TT, von und bis HH:MM): {tag} {uhrzeit}") from e


# --- Attrappen -----------------------------------------------------------------------------------


class Kalenderattrappe:
    """Ein Kalender mit festen Terminen; Anlegen, Verschieben und Löschen schreibt er nur mit."""

    def __init__(self, termine: list[Termin] | None = None):
        self._termine = list(termine or [])
        self.angelegt: list[Termin] = []
        self.verschoben: list[Termin] = []
        self.geloescht: list[Termin] = []

    def termine(self, von: datetime, bis: datetime) -> list[Termin]:
        return sorted((t for t in self._termine if von <= t.beginn < bis), key=lambda t: t.beginn)

    def suchen(self, titel: str, von: datetime, bis: datetime) -> list[Termin]:
        gesucht = titel.strip().casefold()
        return [t for t in self.termine(von, bis) if gesucht and gesucht in t.titel.casefold()]

    def anlegen(self, titel: str, beginn: datetime, ende: datetime) -> Termin:
        termin = Termin(f"neu-{len(self.angelegt) + 1}", titel, beginn, ende)
        self._termine.append(termin)
        self.angelegt.append(termin)
        return termin

    def verschieben(self, termin: Termin, beginn: datetime, ende: datetime) -> Termin:
        neu = Termin(termin.id, termin.titel, beginn, ende, termin.ganztags)
        self._termine = [neu if t.id == termin.id else t for t in self._termine]
        self.verschoben.append(neu)
        return neu

    def loeschen(self, termin: Termin) -> None:
        self._termine = [t for t in self._termine if t.id != termin.id]
        self.geloescht.append(termin)


class Wetterattrappe(Wetter):
    """Der echte Wetter-Code (Ortsregel, Fehlertext, Ansage); nur die Netzabfragen sind fest."""

    async def _get(self, url: str, params: dict) -> dict:
        if url == GEOCODING_URL:
            return {"results": [{"latitude": 48.4, "longitude": 10.0, "name": str(params.get("name", "")).strip().title()}]}
        return {
            "daily": {
                "weather_code": [2, 61],
                "temperature_2m_max": [17, 14],
                "temperature_2m_min": [8, 7],
                "precipitation_probability_max": [20, 70],
                "wind_speed_10m_max": [12, 25],
            }
        }


class WhatsAppattrappe:
    eingerichtet = True

    def __init__(self, adressbuch: list[dict] | None = None):
        self.buch = list(adressbuch if adressbuch is not None else ADRESSBUCH)
        self.gesendet: list[tuple[str, str]] = []

    async def status(self) -> dict:
        return {"verbunden": True, "angemeldet": True}

    async def adressbuch(self) -> list[dict]:
        return self.buch

    async def nachrichten(self) -> list[dict]:
        return []

    async def senden(self, nummer: str, text: str) -> dict:
        self.gesendet.append((nummer, text))
        return {"gesendet": True}


class Mailattrappe:
    """Ein Postfach mit vorgegebenen ungelesenen Mails; Senden schreibt es nur mit."""

    def __init__(self, mails: list[Nachricht] | None = None):
        self._mails = list(mails or [])
        self.gesendet: list[tuple[str, str, str, str | None]] = []

    def ungelesene(self, limit: int = 5) -> list[Nachricht]:
        return [replace(m, text="") for m in self._mails[:limit]]

    def lesen(self, uid: str) -> Nachricht:
        nachricht = next((m for m in self._mails if m.uid == uid), None)
        if nachricht is None:
            raise ValueError(f"Keine Mail mit der Kennung {uid}.")
        return replace(nachricht)

    def senden(self, an: str, betreff: str, text: str, antwort_auf: Nachricht | None = None) -> None:
        self.gesendet.append((an, betreff, text, antwort_auf.message_id if antwort_auf else None))


class Pushattrappe:
    eingerichtet = True

    def __init__(self):
        self.gesendet: list[tuple[str, str]] = []

    async def senden(self, text: str, titel: str = "Alfred", prioritaet: str = "normal", aktionen=None, klick: str = "") -> None:
        self.gesendet.append((titel, text))


class Denkerattrappe:
    """Antwortet sofort und kostenlos; der echte Denker bezahlte Websuchen bei Mistral."""

    def __init__(self):
        self.aufgaben: list[str] = []

    async def bearbeiten(self, aufgabe: str, kontext: str = "") -> str:
        self.aufgaben.append(aufgabe)
        return f"Attrappe: Ergebnis zu '{aufgabe}'."

    async def suchen(self, frage: str) -> str:
        self.aufgaben.append(frage)
        return f"Attrappe: Suchergebnis zu '{frage}' (Quelle: Beispielseite)."

    async def bericht(self, auftrag: str, kontext: str = "", heute: str = "") -> str:
        self.aufgaben.append(auftrag)
        return "# Bericht\n\n## Kurzfassung\n\nAttrappe.\n"


class Schaetzerattrappe:
    async def schaetzen(self, text: str) -> dict:
        return {"kcal": 400.0, "eiweiss": 20.0, "fett": 15.0, "kohlenhydrate": 45.0, "annahme": "eine Portion"}

    async def produkt(self, barcode: str) -> dict:
        raise ValueError("Die Attrappe kennt keine Produkte.")


@dataclass
class Attrappen:
    kalender: Kalenderattrappe
    wetter: Wetterattrappe
    whatsapp: WhatsAppattrappe
    mail: Mailattrappe
    push: Pushattrappe
    denker: Denkerattrappe
    schaetzer: Schaetzerattrappe


def attrappen_einsetzen(res: Ressourcen, szenario: Szenario, jetzt: datetime) -> Attrappen:
    """Ersetzt in den Ressourcen alles, was nach außen wirkt oder Geld kostet, und richtet das Szenario ein.

    Die Werkzeugliste ist danach die des Vollausbaus (Kalender, WhatsApp, Mail und Push gelten als
    eingerichtet), damit das Modell dieselbe Auswahl sieht wie im echten Betrieb.
    """
    termine = [
        Termin(f"t{i}", str(t["titel"]), _zeitpunkt(str(t["tag"]), str(t["von"]), jetzt), _zeitpunkt(str(t["tag"]), str(t["bis"]), jetzt))
        for i, t in enumerate(szenario.kalender, start=1)
    ]
    mails = [
        Nachricht(str(i), str(m["von"]), str(m["adresse"]), str(m["betreff"]), None, text=str(m["text"]), message_id=f"<eval-{i}@beispiel>")
        for i, m in enumerate(szenario.mails, start=1)
    ]
    attrappen = Attrappen(
        kalender=Kalenderattrappe(termine),
        wetter=Wetterattrappe("", wohnort=lambda: res.gedaechtnis.profil().get("wohnort", "")),
        whatsapp=WhatsAppattrappe(),
        mail=Mailattrappe(mails),
        push=Pushattrappe(),
        denker=Denkerattrappe(),
        schaetzer=Schaetzerattrappe(),
    )
    res.kalender = attrappen.kalender
    res.wetter = attrappen.wetter
    res.whatsapp = attrappen.whatsapp
    res.mail = attrappen.mail
    res.benachrichtiger = attrappen.push
    res.denker = attrappen.denker
    res.schaetzer = attrappen.schaetzer
    res.suche = None  # websuche ginge sonst an SearXNG; die Attrappe des Denkers antwortet stattdessen
    for feld, wert in szenario.profil.items():
        res.gedaechtnis.profil_merken(feld, wert)
    for name, nummer in szenario.kontakte.items():
        res.kontakte.merken(name, nummer)
    return attrappen


# --- Mitschrift des Modells ----------------------------------------------------------------------


@dataclass
class Modellaufruf:
    modell: str
    eingabe: int
    ausgabe: int
    werkzeuge: list[str]  # Werkzeugaufrufe in der Antwort; leer heißt, das Modell hat Text geantwortet
    # Text der Antwort. Steht er neben Werkzeugaufrufen, liest Pipecat ihn beim Sprechen vor dem
    # Werkzeug vor; das Textgespräch zeigt ihn nicht.
    text: str = ""


class Mitschreiber:
    """Steht vor dem Mistral-Client: zählt Token je Aufruf und merkt sich, womit jede Antwort endete."""

    def __init__(self, client):
        self._client = client
        self.aufrufe: list[Modellaufruf] = []
        self.chat = SimpleNamespace(complete_async=self._complete)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._client, name)  # audio, beta, models: unverändert der echte Client

    async def _complete(self, *args, **kwargs):
        antwort = await self._client.chat.complete_async(*args, **kwargs)
        nachricht = antwort.choices[0].message
        verbrauch = getattr(antwort, "usage", None)
        self.aufrufe.append(
            Modellaufruf(
                modell=str(kwargs.get("model") or (args[0] if args else "?")),
                eingabe=int(getattr(verbrauch, "prompt_tokens", 0) or 0),
                ausgabe=int(getattr(verbrauch, "completion_tokens", 0) or 0),
                werkzeuge=[a.function.name for a in (getattr(nachricht, "tool_calls", None) or [])],
                text=_inhalt_text(getattr(nachricht, "content", None)),
            )
        )
        return antwort


# --- Ergebnisse und Bewertung --------------------------------------------------------------------


@dataclass
class Werkzeugaufruf:
    name: str
    argumente: dict[str, Any]
    ergebnis: Any = None

    def ansage(self) -> str:
        return f"{self.name}({json.dumps(self.argumente, ensure_ascii=False)})"


@dataclass
class Schrittergebnis:
    sage: str
    antwort: str
    werkzeuge: list[Werkzeugaufruf]
    endete_mit_werkzeug: bool  # die letzte Modellantwort war ein Werkzeugaufruf; die Antwort kam vom Werkzeug
    freigabe: str | None  # Beschreibung der offenen Rückfrage nach dem Schritt
    dauer: float
    modellaufrufe: int = 0
    eingabe: int = 0
    ausgabe: int = 0
    maengel: list[str] = field(default_factory=list)
    # Was das Modell neben Werkzeugaufrufen schrieb: Beim Sprechen hört Alexander es vor dem Werkzeug.
    vorab: list[str] = field(default_factory=list)


@dataclass
class Szenarioergebnis:
    szenario: Szenario
    modell: str
    schritte: list[Schrittergebnis] = field(default_factory=list)
    fehler: str | None = None  # Ausnahme im Lauf, etwa Netz weg

    @property
    def bestanden(self) -> bool:
        return self.fehler is None and bool(self.schritte) and all(not s.maengel for s in self.schritte)

    @property
    def dauer(self) -> float:
        return sum(s.dauer for s in self.schritte)

    @property
    def eingabe(self) -> int:
        return sum(s.eingabe for s in self.schritte)

    @property
    def ausgabe(self) -> int:
        return sum(s.ausgabe for s in self.schritte)

    @property
    def modellaufrufe(self) -> int:
        return sum(s.modellaufrufe for s in self.schritte)


def _argument_passt(muster: Any, wert: Any) -> bool:
    """Ein Text ist ein regulärer Ausdruck (Suche im Wert, Groß- und Kleinschreibung egal); ein
    fehlendes Argument zählt als leer. Eine Zahl wird als Zahl verglichen, auch wenn sie als Text kam."""
    if isinstance(muster, str):
        if wert is None:
            text = ""
        elif isinstance(wert, (dict, list)):
            text = json.dumps(wert, ensure_ascii=False)
        else:
            text = str(wert)
        return re.search(muster, text, re.IGNORECASE | re.DOTALL) is not None
    if isinstance(muster, (int, float)) and not isinstance(muster, bool) and isinstance(wert, (int, float, str)):
        try:
            return float(wert) == float(muster)
        except ValueError:
            return False
    return wert == muster


def argumente_passen(soll: dict[str, Any], ist: dict[str, Any]) -> bool:
    return all(_argument_passt(muster, ist.get(name)) for name, muster in soll.items())


def _muster_text(argumente: dict[str, Any]) -> str:
    return ", ".join(f"{k}~{v!r}" if isinstance(v, str) else f"{k}={v!r}" for k, v in argumente.items())


def bewerten(erwartung: Erwartung, ergebnis: Schrittergebnis) -> list[str]:
    """Die Mängel eines Schritts; eine leere Liste heißt bestanden."""
    maengel: list[str] = []
    for soll in erwartung.werkzeuge:
        passende = [w for w in ergebnis.werkzeuge if w.name == soll.name]
        if not passende:
            maengel.append(f"{soll.name} nicht aufgerufen")
        elif soll.argumente and not any(argumente_passen(soll.argumente, w.argumente) for w in passende):
            maengel.append(
                f"{soll.name} mit anderen Argumenten; erwartet {_muster_text(soll.argumente)}, "
                "bekommen " + "; ".join(w.ansage() for w in passende)
            )
    pflicht = {w.name for w in erwartung.werkzeuge}
    erlaubt = {w.name: w for w in erwartung.erlaubt}
    for aufruf in ergebnis.werkzeuge:
        if aufruf.name in erwartung.verboten:
            maengel.append(f"verbotenes Werkzeug aufgerufen: {aufruf.ansage()}")
        elif aufruf.name in pflicht:
            continue
        elif aufruf.name not in erlaubt:
            maengel.append(f"unerwartetes Werkzeug aufgerufen: {aufruf.ansage()}")
        elif erlaubt[aufruf.name].argumente and not argumente_passen(erlaubt[aufruf.name].argumente, aufruf.argumente):
            maengel.append(
                f"{aufruf.name} nur erlaubt mit {_muster_text(erlaubt[aufruf.name].argumente)}, bekommen {aufruf.ansage()}"
            )
    for muster in erwartung.antwort_enthaelt:
        if not re.search(muster, ergebnis.antwort, re.IGNORECASE | re.DOTALL):
            maengel.append(f"Antwort ohne »{muster}«")
    for muster in erwartung.antwort_nicht:
        if treffer := re.search(muster, ergebnis.antwort, re.IGNORECASE | re.DOTALL):
            maengel.append(f"Antwort enthält »{treffer.group()}«")
    if erwartung.antwort_vom_werkzeug and not ergebnis.endete_mit_werkzeug:
        maengel.append("die Antwort kam nicht vom Werkzeug; das Modell hat selbst weitergeredet")
    if erwartung.antwort_vom_werkzeug and ergebnis.vorab:
        # Beim Sprechen kommt die Bestätigung dann doppelt: erst vom Modell, dann vom Werkzeug.
        maengel.append(f"das Modell hat vor dem Werkzeug selbst gesprochen: »{_kurz(' '.join(ergebnis.vorab))}«")
    if erwartung.freigabe_offen is True and ergebnis.freigabe is None:
        maengel.append("keine Rückfrage offen")
    if erwartung.freigabe_offen is False and ergebnis.freigabe is not None:
        maengel.append(f"Rückfrage noch offen: {ergebnis.freigabe}")
    for muster in erwartung.freigabe_enthaelt:
        if ergebnis.freigabe is None or not re.search(muster, ergebnis.freigabe, re.IGNORECASE | re.DOTALL):
            maengel.append(f"offene Rückfrage ohne »{muster}«: {ergebnis.freigabe or 'keine offen'}")
    return maengel


def _als_daten(roh: Any) -> Any:
    if isinstance(roh, str):
        try:
            return json.loads(roh)
        except ValueError:
            return roh
    return roh


def werkzeuge_im_verlauf(verlauf: list[dict], sage: str) -> list[Werkzeugaufruf]:
    """Die Werkzeugaufrufe samt Ergebnissen, die im Verlauf auf den letzten Satz mit diesem Text folgen."""
    ab = 0
    for i in range(len(verlauf) - 1, -1, -1):
        if verlauf[i].get("role") == "user" and verlauf[i].get("content") == sage:
            ab = i + 1
            break
    aufrufe: list[tuple[Any, Werkzeugaufruf]] = []
    ergebnisse: dict[Any, Any] = {}
    for m in verlauf[ab:]:
        if m.get("role") == "assistant":
            for t in m.get("tool_calls") or []:
                fn = t.get("function", {})
                argumente = _als_daten(fn.get("arguments"))
                if not isinstance(argumente, dict):
                    argumente = {} if argumente in (None, "") else {"_roh": argumente}
                aufrufe.append((t.get("id"), Werkzeugaufruf(str(fn.get("name", "?")), argumente)))
        elif m.get("role") == "tool":
            ergebnisse[m.get("tool_call_id")] = _als_daten(m.get("content"))
    for kennung, aufruf in aufrufe:
        aufruf.ergebnis = ergebnisse.get(kennung)
    return [aufruf for _, aufruf in aufrufe]


# --- Lauf ----------------------------------------------------------------------------------------


async def szenario_ausfuehren(
    szenario: Szenario, settings: Settings, persona: Persona, *, jetzt: datetime | None = None, client=None
) -> Szenarioergebnis:
    """Ein Szenario in frischer Umgebung: geleerter Testordner, neue Ressourcen, neues Gespräch.

    Args:
        client: Ersatz für den Mistral-Client, für Tests ohne Netz.
    """
    jetzt = jetzt or datetime.now().astimezone()
    ordner = settings.pfad(settings.gedaechtnis.ordner)
    if ordner.name != TESTORDNER:
        raise ValueError(f"Die Eval-Suite leert nur einen Gedächtnisordner namens {TESTORDNER}, nicht {ordner}.")
    shutil.rmtree(ordner, ignore_errors=True)
    # Eine eigene Persönlichkeit je Szenario: Wechselt eines den Modus (Gästemodus), begänne sonst
    # das nächste darin.
    res = build_ressourcen(settings, persona.model_copy(deep=True))
    attrappen_einsetzen(res, szenario, jetzt)
    res.codewort = Codewort(szenario.codewort) if szenario.codewort else None
    mitschreiber = Mitschreiber(client or res.client)
    res.client = mitschreiber
    gespraech = Textgespraech(res, lambda: system_prompt(res))
    ergebnis = Szenarioergebnis(szenario, settings.modelle.sprecher)
    for schritt in szenario.schritte:
        sage = platzhalter(schritt.sage, jetzt)
        if res.codewort is not None:
            sage = res.codewort.maskieren(sage)
        ab = len(mitschreiber.aufrufe)
        start = time.perf_counter()
        try:
            antworten = await gespraech.antworten(sage)
        except Exception as e:  # noqa: BLE001 - der Lauf soll den Fehler zeigen, nicht abstürzen
            logger.exception(f"Szenario '{szenario.name}' abgebrochen")
            ergebnis.fehler = f"{type(e).__name__}: {e}"
            break
        aufrufe = mitschreiber.aufrufe[ab:]
        schrittergebnis = Schrittergebnis(
            sage=sage,
            antwort=" ".join(a for a in antworten if a).strip(),
            werkzeuge=werkzeuge_im_verlauf(gespraech.verlauf, sage),
            endete_mit_werkzeug=bool(aufrufe and aufrufe[-1].werkzeuge),
            freigabe=res.offene_freigabe.beschreibung if res.offene_freigabe else None,
            dauer=time.perf_counter() - start,
            modellaufrufe=len(aufrufe),
            eingabe=sum(a.eingabe for a in aufrufe),
            ausgabe=sum(a.ausgabe for a in aufrufe),
            vorab=[a.text for a in aufrufe if a.werkzeuge and a.text],
        )
        schrittergebnis.maengel = bewerten(aufgeloest(schritt.erwartung, jetzt), schrittergebnis)
        ergebnis.schritte.append(schrittergebnis)
    return ergebnis


@dataclass
class Bericht:
    laeufe: dict[str, list[Szenarioergebnis]] = field(default_factory=dict)  # je Szenario alle Läufe
    dauer: float = 0.0

    def bestanden(self, name: str) -> bool:
        return bool(self.laeufe.get(name)) and all(e.bestanden for e in self.laeufe[name])

    @property
    def alle_bestanden(self) -> bool:
        return bool(self.laeufe) and all(self.bestanden(name) for name in self.laeufe)

    def token(self) -> dict[str, list[int]]:
        """Je Modell: Eingabe-Token, Ausgabe-Token, Modellaufrufe."""
        summen: dict[str, list[int]] = {}
        for laeufe in self.laeufe.values():
            for e in laeufe:
                eintrag = summen.setdefault(e.modell, [0, 0, 0])
                eintrag[0] += e.eingabe
                eintrag[1] += e.ausgabe
                eintrag[2] += e.modellaufrufe
        return summen

    def zusammenfassung(self, preise) -> list[str]:
        namen = list(self.laeufe)
        bestanden = [n for n in namen if self.bestanden(n)]
        quote = round(100 * len(bestanden) / len(namen)) if namen else 0
        zeilen = [f"Ergebnis: {len(bestanden)} von {len(namen)} Szenarien bestanden ({quote} %), Dauer {_dauer(self.dauer)}."]
        if gescheitert := [n for n in namen if n not in bestanden]:
            zeilen.append("Gescheitert: " + ", ".join(gescheitert))
        teile, ohne_preis, kosten = [], [], 0.0
        for modell, (eingabe, ausgabe, aufrufe) in self.token().items():
            teile.append(f"{aufrufe} Modellaufrufe mit {modell}, {_zahl(eingabe)} Token Eingabe und {_zahl(ausgabe)} Ausgabe")
            betrag = modellkosten(preise, modell, eingabe, ausgabe)
            if betrag is None:
                ohne_preis.append(modell)
            else:
                kosten += betrag
        if teile:
            satz = "Verbrauch: " + "; ".join(teile) + "."
            if kosten or not ohne_preis:
                satz += f" Kosten etwa {_komma(kosten * 100)} Cent."
            if ohne_preis:
                satz += f" Kein Preis für {', '.join(ohne_preis)} in config/alfred.yaml (preise.modelle), diese Kosten sind unbekannt."
            zeilen.append(satz)
        return zeilen


def _dauer(sekunden: float) -> str:
    if sekunden >= 60:
        return f"{int(sekunden // 60)}:{int(sekunden % 60):02d} min"
    return f"{_komma(sekunden)} s"


def _komma(zahl: float) -> str:
    return f"{zahl:.1f}".replace(".", ",")


def _zahl(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _kurz(text: str, max_zeichen: int = MAX_ERGEBNIS_ZEICHEN) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_zeichen else text[: max_zeichen - 1] + "…"


def drucken(zeile: str) -> None:
    """print mit sofortigem Leeren: Umgeleitet in eine Datei käme der Bericht sonst erst am Ende an."""
    print(zeile, flush=True)


def ergebnis_zeilen(ergebnis: Szenarioergebnis, lauf: str = "") -> list[str]:
    """Ein Szenario-Ergebnis für die Konsole: Kopfzeile, je Schritt Satz, Werkzeuge, Antwort, Mängel."""
    zeilen = [
        f"{ZEICHEN[ergebnis.bestanden]} {ergebnis.szenario.name}{lauf} "
        f"({_dauer(ergebnis.dauer)}, {_zahl(ergebnis.eingabe)} Token Eingabe, {_zahl(ergebnis.ausgabe)} Ausgabe)"
    ]
    for s in ergebnis.schritte:
        zeilen.append(f"    Satz:     {s.sage}")
        for w in s.werkzeuge:
            daten = w.ergebnis if isinstance(w.ergebnis, str) else json.dumps(w.ergebnis, ensure_ascii=False, default=str)
            zeilen.append(f"    Werkzeug: {w.ansage()} → {_kurz(daten)}")
        if not s.werkzeuge:
            zeilen.append("    Werkzeug: keins")
        if s.vorab:
            zeilen.append(f"    Vorab:    {' | '.join(s.vorab)}")
        zeilen.append(f"    Antwort:  {s.antwort or '(keine)'}")
        zeilen.extend(f"    Mangel:   {m}" for m in s.maengel)
    if ergebnis.fehler:
        zeilen.append(f"    Fehler:   {ergebnis.fehler}")
    if not ergebnis.bestanden and ergebnis.szenario.grund:
        zeilen.append(f"    Grund des Szenarios: {ergebnis.szenario.grund}")
    return zeilen


async def suite_ausfuehren(
    szenarien: list[Szenario],
    settings: Settings,
    persona: Persona,
    wiederholungen: int = 1,
    *,
    ausgabe=drucken,
    client=None,
    jetzt: datetime | None = None,
) -> Bericht:
    """Lässt alle Szenarien laufen, gibt jedes Ergebnis sofort aus und zum Schluss die Bilanz."""
    bericht = Bericht()
    start = time.perf_counter()
    laeufe = "ein Lauf" if wiederholungen == 1 else f"{wiederholungen} Läufe"
    ausgabe(f"Eval-Suite: {len(szenarien)} Szenarien gegen {settings.modelle.sprecher}, {laeufe} je Szenario.\n")
    for szenario in szenarien:
        ergebnisse = []
        for i in range(1, wiederholungen + 1):
            ergebnis = await szenario_ausfuehren(szenario, settings, persona, jetzt=jetzt, client=client)
            ergebnisse.append(ergebnis)
            for zeile in ergebnis_zeilen(ergebnis, f", Lauf {i} von {wiederholungen}" if wiederholungen > 1 else ""):
                ausgabe(zeile)
        bericht.laeufe[szenario.name] = ergebnisse
        if wiederholungen > 1:
            n = sum(e.bestanden for e in ergebnisse)
            ausgabe(f"{ZEICHEN[n == len(ergebnisse)]} {szenario.name}: {n} von {len(ergebnisse)} Läufen bestanden")
        ausgabe("")
    bericht.dauer = time.perf_counter() - start
    for zeile in bericht.zusammenfassung(settings.preise):
        ausgabe(zeile)
    return bericht


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval-Suite: Szenarien auf Textebene gegen das echte Sprecher-Modell")
    parser.add_argument("namen", nargs="*", metavar="NAME", help="nur Szenarien, deren Name eines dieser Wörter enthält")
    parser.add_argument(
        "--wiederholungen", type=int, default=1, metavar="N",
        help="jedes Szenario so oft laufen lassen; bestanden nur, wenn alle Läufe bestehen",
    )  # fmt: skip
    parser.add_argument("--modell", metavar="MODELL", help="anderes Sprecher-Modell als in der Konfiguration, zum Vergleich")
    parser.add_argument("--szenarien", type=Path, default=SZENARIEN_DATEI, metavar="DATEI", help="andere Szenario-Datei")
    parser.add_argument("--liste", action="store_true", help="die Szenarien nur anzeigen")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if args.wiederholungen < 1:
        parser.error("--wiederholungen muss mindestens 1 sein")
    try:
        alle = lade_szenarien(args.szenarien)
    except ValueError as e:
        raise SystemExit(f"Szenarien ungültig: {e}") from e
    szenarien = alle
    if args.namen:
        gesucht = [n.casefold() for n in args.namen]
        szenarien = [s for s in alle if any(g in s.name.casefold() for g in gesucht)]
        if not szenarien:
            raise SystemExit("Kein Szenario passt. Vorhanden: " + ", ".join(s.name for s in alle))
    if args.liste:
        for s in szenarien:
            print(f"{s.name}: " + " | ".join(schritt.sage for schritt in s.schritte))
            if s.grund:
                print(f"    {s.grund}")
        return

    load_dotenv(override=True)
    require_api_key()
    settings, persona = load_settings(), load_persona()
    if args.modell:
        settings.modelle.sprecher = args.modell
    # Eigener, bei jedem Szenario frischer Gedächtnisordner wie im Selbsttest; das echte Gedächtnis bleibt unberührt.
    settings.gedaechtnis.ordner = str(settings.pfad(settings.gedaechtnis.ordner) / TESTORDNER)
    # Nur Warnungen und Fehler, ohne loguru-Variablendumps: Ein Netzfehler soll eine Zeile sein, kein Bildschirm.
    logger.remove()
    logger.add(sys.stderr, level="WARNING", backtrace=False, diagnose=False)
    bericht = asyncio.run(suite_ausfuehren(szenarien, settings, persona, args.wiederholungen))
    raise SystemExit(0 if bericht.alle_bestanden else 1)


if __name__ == "__main__":
    main()
