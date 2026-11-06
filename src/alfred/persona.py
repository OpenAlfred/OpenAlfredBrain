"""Baut aus config/persoenlichkeit.yaml den Systemprompt für das Sprecher-Modell."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alfred.config import persona_pfad

# Jeder Regler hat vier Stufen. Eine Stufe gilt bis einschließlich ihrer Obergrenze.
# Das Modell bekommt den Satz der Stufe, nie die Zahl: "Humor 7" versteht es schlechter
# als eine Verhaltensbeschreibung.
STUFEN: dict[str, tuple[tuple[int, str], ...]] = {
    "widerspruch": (
        (2, "Du widersprichst nur, wenn etwas sachlich falsch ist oder ein klares Risiko besteht."),
        (5, "Wenn du Einwände hast, nennst du sie kurz und begründet, bestehst aber nicht darauf."),
        (
            8,
            "Du prüfst Ideen auf Schwächen und sagst deutlich, wenn du anderer Meinung bist, "
            "auch ungefragt. Wenn du zustimmst, sagst du warum.",
        ),
        (
            10,
            "Du nimmst zunächst die Gegenposition ein und lässt eine Idee erst gelten, "
            "wenn sie deinen Einwänden standhält.",
        ),
    ),
    "humor": (
        (2, "Du bleibst sachlich und machst keine Scherze."),
        (5, "Ein trockener Kommentar ist gelegentlich erlaubt, wenn er passt."),
        (8, "Du hast trockenen Humor und zeigst ihn regelmäßig, ohne vom Thema abzulenken."),
        (10, "Du bist schlagfertig und lässt kaum eine Gelegenheit für eine trockene Bemerkung aus."),
    ),
    "foermlichkeit": (
        (2, "Du sprichst locker und umgangssprachlich, wie mit einem guten Freund."),
        (5, "Du sprichst entspannt, aber gepflegt."),
        (8, "Du sprichst höflich und gewählt."),
        (10, "Du sprichst sehr förmlich und zurückhaltend, wie ein Butler der alten Schule."),
    ),
    "ausfuehrlichkeit": (
        (2, "Du antwortest so knapp wie möglich, meist mit einem einzigen Satz."),
        (5, "Du antwortest in ein bis drei Sätzen und gehst nur auf Nachfrage ins Detail."),
        (8, "Du erklärst deine Antwort in einigen Sätzen und nennst die wichtigsten Gründe."),
        (10, "Du antwortest ausführlich und gehst von dir aus auf Hintergründe und Alternativen ein."),
    ),
    "proaktivitaet": (
        (2, "Du beantwortest nur, was gefragt wurde."),
        (5, "Du weist auf etwas hin, wenn es offensichtlich wichtig ist."),
        (8, "Du denkst einen Schritt voraus und schlägst von dir aus den nächsten Schritt vor."),
        (10, "Du bringst aktiv eigene Vorschläge ein und sprichst an, was übersehen wurde."),
    ),
    "waerme": (
        (2, "Dein Ton ist nüchtern und distanziert."),
        (5, "Dein Ton ist freundlich und ruhig."),
        (8, "Dein Ton ist warm und zugewandt. Du merkst, wenn etwas belastet, und gehst kurz darauf ein."),
        (10, "Dein Ton ist herzlich und fürsorglich."),
    ),
}

_SPRACHREGELN = """\
Das Gespräch läuft über Mikrofon und Lautsprecher: Was du hörst, stammt aus einer \
Spracherkennung, und deine Antworten werden vorgelesen.
- Schreib so, wie man spricht: ganze Sätze, keine Listen, keine Überschriften, kein Markdown, keine Emojis.
- Schreib Zahlen, Uhrzeiten und Abkürzungen so, wie man sie ausspricht.
- Die Spracherkennung macht Fehler. Ergibt ein Satz keinen Sinn, frag kurz nach, statt zu raten.
- Antworte auf Deutsch, außer {nutzer} wechselt die Sprache."""

# Im ersten echten Lauf bot Alfred an, Kaffee zu bringen, und beharrte darauf. Auslöser sind
# vor allem beiläufige Sätze wie "Was geht?" oder "Okay", deshalb der letzte Satz.
_GRENZEN = (
    "Du bist ein Sprachprogramm ohne Körper und spielst keine Rolle. Du kannst nichts bringen, "
    "holen, vorbereiten oder vor Ort erledigen und bietest so etwas nie an, auch nicht im Scherz. "
    "Behaupte nie, etwas getan zu haben oder gleich zu tun, wofür du kein Werkzeug hast. Steht "
    "nichts Konkretes an, antworte kurz oder frag, woran dein Gegenüber gerade arbeitet."
)

_KEINE_WERKZEUGE = (
    "Du hast noch keine Werkzeuge. Du kannst zuhören, mitdenken und antworten, sonst nichts. "
    "Erinnerungen, Timer, Kalendereinträge, Suchen im Internet, Nachschlagen, Aufschreiben und "
    "Merken über dieses Gespräch hinaus kannst du noch nicht. Wirst du um so etwas gebeten, sagst "
    "du in einem Satz, dass du es noch nicht kannst, und hängst kein Ersatzangebot an. Du sagst "
    "nie zu, dich später zu melden, etwas zu notieren oder dir etwas zu merken. Du kennst weder "
    "Termine noch Aufgaben, Nachrichten oder Unterlagen deines Gegenübers und erfindest keine."
)

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_MONATE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip

# Gruppen der Werkzeuge, die ein Modus abschalten kann (persoenlichkeit.yaml, `werkzeuge:` je
# Modus). Zu welcher Gruppe ein Werkzeug gehört, steht an seinem @werkzeug in
# src/alfred/werkzeuge/; hier stehen nur die Namen, die ein Modus nennen darf, und was das
# Modell darunter verstehen soll. kern gilt in jedem Modus.
GRUPPEN: dict[str, str] = {
    "kern": "Zeit, Erinnerungen, Timer, Gedächtnis, Wetter, Websuche, Denker, Regeln, Feedback, Modus",
    "kalender": "Termine lesen, anlegen, verschieben, absagen",
    "aufgaben": "To-do-Liste",
    "kommunikation": "WhatsApp, E-Mail, Kontakte, Geburtstage, Push aufs Handy",
    "haushalt": "Einkaufsliste, Vorrat, Wochenplan",
    "ernaehrung": "Ernährungstagebuch, Gewicht",
    "finanzen": "Ausgaben",
    "medien": "Musik",
    "auftraege": "Aufträge im Hintergrund, Berichte",
    "system": "Routinen, Kosten",
}
KERN = "kern"
# Der Modus für Besuch. Was dann Alexanders Privates schützt, steht im Code, nicht in der
# Beschreibung des Modus: Private Werkzeuge fehlen (@werkzeug(privat=True)), Erinnerungen und
# Nachrichten warten bis zum Ende des Besuchs (ereignisse.Zustellregeln), und zurückschalten kann
# im Raum nur das Codewort (modus_wechseln; bis zum 20.10.2026 Alexanders Stimme). Anlass:
# Gespräch mit Besuch am 16.10.2026.
GAESTEMODUS = "gaeste"


class Regler(BaseModel):
    model_config = ConfigDict(extra="forbid")

    widerspruch: int = Field(5, ge=0, le=10)
    humor: int = Field(5, ge=0, le=10)
    foermlichkeit: int = Field(5, ge=0, le=10)
    ausfuehrlichkeit: int = Field(5, ge=0, le=10)
    proaktivitaet: int = Field(5, ge=0, le=10)
    waerme: int = Field(5, ge=0, le=10)


class Modus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beschreibung: str = ""
    regler: dict[str, int] = Field(default_factory=dict)
    # Welche Werkzeuggruppen in diesem Modus gelten: "alle" oder eine Liste aus GRUPPEN; kern immer.
    werkzeuge: Literal["alle"] | list[str] = "alle"

    @field_validator("werkzeuge")
    @classmethod
    def _gruppen_pruefen(cls, wert: Literal["alle"] | list[str]) -> Literal["alle"] | list[str]:
        if wert == "alle":
            return wert
        if unbekannt := [g for g in wert if g not in GRUPPEN]:
            raise ValueError(f"unbekannte Werkzeuggruppe(n) {', '.join(unbekannt)}; es gibt: {', '.join(GRUPPEN)}")
        return wert

    def aktive_gruppen(self) -> frozenset[str]:
        if self.werkzeuge == "alle":
            return frozenset(GRUPPEN)
        return frozenset([KERN, *self.werkzeuge])


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    nutzer: str
    anrede: Literal["du", "sie"] = "du"
    charakter: str
    regler: Regler = Field(default_factory=Regler)
    regeln: list[str] = Field(default_factory=list)
    aktiver_modus: str = "standard"
    modi: dict[str, Modus] = Field(default_factory=lambda: {"standard": Modus()})

    @model_validator(mode="after")
    def _modi_pruefen(self) -> Persona:
        if self.aktiver_modus not in self.modi:
            raise ValueError(f"aktiver_modus '{self.aktiver_modus}' ist unter modi nicht definiert")
        for name in self.modi:
            try:
                self.regler_fuer(name)
            except ValueError as e:
                raise ValueError(f"Modus '{name}': {e}") from e
        return self

    @property
    def gaeste_da(self) -> bool:
        """Ob der Gästemodus läuft (GAESTEMODUS)."""
        return self.aktiver_modus == GAESTEMODUS

    def regler_fuer(self, modus: str | None = None) -> Regler:
        """Grundregler, überschrieben durch die Regler des Modus."""
        return Regler.model_validate({**self.regler.model_dump(), **self._modus(modus).regler})

    def aktive_gruppen(self, modus: str | None = None) -> frozenset[str]:
        """Werkzeuggruppen, die im Modus gelten (Standard: der aktive); kern immer."""
        return self._modus(modus).aktive_gruppen()

    def abgeschaltete_gruppen(self, modus: str | None = None) -> list[str]:
        """Gruppen, die der Modus abschaltet, in der Reihenfolge von GRUPPEN."""
        aktiv = self.aktive_gruppen(modus)
        return [g for g in GRUPPEN if g not in aktiv]

    def _modus(self, modus: str | None) -> Modus:
        modus = modus or self.aktiver_modus
        if modus not in self.modi:
            raise ValueError(f"Modus '{modus}' ist nicht definiert")
        return self.modi[modus]


def load_persona(path: Path | None = None) -> Persona:
    path = path or persona_pfad()
    return Persona.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def build_system_prompt(
    persona: Persona,
    modus: str | None = None,
    faehigkeiten: Sequence[str] = (),
    jetzt: datetime | None = None,
    wissen: str = "",
    zusatzregeln: Sequence[str] = (),
) -> str:
    """Systemprompt für den Sprecher.

    Args:
        persona: Die geladene Persönlichkeit.
        modus: Modus, dessen Regler gelten sollen. Standard ist persona.aktiver_modus.
        faehigkeiten: Je ein Satz pro Werkzeug, das dem Modell zur Verfügung steht.
        jetzt: Zeitpunkt des Gesprächs. Ohne ihn kennt das Modell weder Datum noch Tageszeit
            und wünscht auch um 23 Uhr einen guten Morgen.
        wissen: Der Gedächtnis-Abschnitt aus alfred.gedaechtnis.Gedaechtnis.fuer_prompt.
        zusatzregeln: Regeln, die Alexander per Stimme gesetzt hat; gelten wie die aus der Datei.
    """
    modus = modus or persona.aktiver_modus
    regler = persona.regler_fuer(modus)
    anrede = "Ihr duzt euch." if persona.anrede == "du" else f"Du siezt {persona.nutzer}."

    teile = [
        persona.charakter.strip(),
        f"Du heißt {persona.name}. Dein Gegenüber ist {persona.nutzer}. {anrede}",
        "So trittst du auf:\n"
        + _liste(_stufe(name, wert) for name, wert in regler.model_dump().items()),
    ]
    if persona.modi[modus].beschreibung:
        teile.append(f"Aktuelle Situation: {persona.modi[modus].beschreibung}")
    if jetzt:
        # Die Tabelle der nächsten Tage, weil das Modell Wochentage falsch umrechnet: Aus
        # "Mittwoch" wurde im Test der Donnerstag.
        tage = ", ".join(
            f"{WOCHENTAGE[t.weekday()]} {t.day}.{t.month}." for t in (jetzt + timedelta(days=i) for i in range(1, 8))
        )
        # Auch die letzten Tage: Ohne sie machte das Modell aus "gestern" den Montag der nächsten Woche
        # (6.10.2026: "gestern, am Montag, den zwölften Oktober").
        vorher = ", ".join(
            f"{name} {WOCHENTAGE[t.weekday()]} {t.day}.{t.month}."
            for name, t in (("vorgestern", jetzt - timedelta(days=2)), ("gestern", jetzt - timedelta(days=1)))
        )
        teile.append(
            f"Es ist {WOCHENTAGE[jetzt.weekday()]}, der {jetzt.day}. {_MONATE[jetzt.month - 1]} "
            f"{jetzt.year}, {jetzt:%H:%M} Uhr. Gesprochen: {datum_gesprochen(jetzt)}, "
            f"{uhrzeit_gesprochen(jetzt)}. Das Jahr heißt gesprochen {jahr_gesprochen(jetzt.year)}. "
            f"Zuletzt: {vorher}. Die nächsten Tage: {tage}."
        )
    if wissen and modus != GAESTEMODUS:
        teile.append(wissen)
    regeln = [*persona.regeln, *zusatzregeln]
    if regeln:
        teile.append("Diese Regeln gelten immer:\n" + _liste(regeln))
    if len(persona.modi) > 1:
        saetze = [
            f"Dein Modus ist '{modus}'. Weitere Modi: "
            + ", ".join(m for m in persona.modi if m != modus)
            + ". Wechseln auf Zuruf mit modus_wechseln."
        ]
        if GAESTEMODUS in persona.modi and modus != GAESTEMODUS:
            saetze.append(
                "Sagt das Gegenüber 'wir haben Gäste', 'wir haben einen Gast', 'Besuch ist da' oder Ähnliches, "
                f"wechsle sofort mit modus_wechseln in den Modus '{GAESTEMODUS}'."
            )
        elif modus == GAESTEMODUS:
            # Auf „Welcher Modus?“ wich Alfred aus (16.10.2026): Die Beschreibung des Modus verbot
            # jede technische Bestätigung, und das galt dem Modell auch für eine direkte Frage.
            saetze.append(
                "Sind die Gäste gegangen ('wir sind wieder allein', 'die Gäste sind weg'), "
                "wechsle mit modus_wechseln zurück in 'standard'. Fragt jemand, in welchem Modus du bist, "
                f"sag offen, dass du im Gästemodus bist. Erinnerungen, Notizen und Nachrichten für "
                f"{persona.nutzer} sagst du erst an, wenn der Besuch weg ist."
            )
        teile.append(" ".join(saetze))
    if aus := persona.abgeschaltete_gruppen(modus):
        # Die Werkzeuge dieser Gruppen fehlen dem Modell ganz (werkzeuge.build_tools); ohne den
        # Satz sagte es nur "das kann ich nicht", statt den Modus als Grund zu nennen; ohne den
        # Zusatz wich es auf die To-do-Liste aus und nannte sie Einkaufsliste (Selbsttest 6.10.2026).
        teile.append(
            f"Im Modus '{modus}' sind abgeschaltet: "
            + ", ".join(f"{g} ({GRUPPEN[g]})" for g in aus)
            + ". Wirst du um so etwas gebeten, sag in einem Satz, dass du das in diesem Modus nicht machst, "
            "und nimm kein anderes Werkzeug als Ersatz."
        )
    teile.append(_SPRACHREGELN.format(nutzer=persona.nutzer))
    teile.append(_GRENZEN)
    if faehigkeiten:
        teile.append("Das kannst du:\n" + _liste(faehigkeiten))
    else:
        teile.append(_KEINE_WERKZEUGE)
    return "\n\n".join(teile)


_EINER = (
    "null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn",
    "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn",
)  # fmt: skip
_ZEHNER = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig", 7: "siebzig", 8: "achtzig", 9: "neunzig"}
_ORDINAL_SONDER = {1: "erste", 3: "dritte", 7: "siebte", 8: "achte"}


def _zahl(n: int) -> str:
    """Zahlwort für 0 bis 99."""
    if n < 20:
        return _EINER[n]
    zehner, einer = divmod(n, 10)
    if einer == 0:
        return _ZEHNER[zehner]
    return ("ein" if einer == 1 else _EINER[einer]) + "und" + _ZEHNER[zehner]


def _ordinal(n: int) -> str:
    """Ordnungszahl für 1 bis 31 im Nominativ, etwa "dritte" oder "einundzwanzigste"."""
    if n in _ORDINAL_SONDER:
        return _ORDINAL_SONDER[n]
    return _zahl(n) + ("te" if n < 20 else "ste")


def jahr_gesprochen(jahr: int) -> str:
    """Die Jahreszahl ausgeschrieben, etwa "zweitausendsechsundzwanzig" oder "neunzehnhundertneunzig"."""
    if 2000 <= jahr < 2100:
        rest = jahr - 2000
        return "zweitausend" + (_zahl(rest) if rest else "")
    if 1100 <= jahr < 2000:
        jahrhundert, rest = divmod(jahr, 100)
        return _zahl(jahrhundert) + "hundert" + (_zahl(rest) if rest else "")
    return str(jahr)


def datum_gesprochen(tag: datetime) -> str:
    """Das Datum ausgeschrieben, etwa "Samstag, der dritte Oktober zweitausendsechsundzwanzig".

    Das Sprachmodell schreibt die Jahreszahl sonst selbst in Worte und verliert dabei Teile:
    Aus 2026 wurde "zweitausendsechs", im Gespräch bis hin zu "Wir haben das Jahr 2006".
    """
    return f"{WOCHENTAGE[tag.weekday()]}, der {_ordinal(tag.day)} {_MONATE[tag.month - 1]} {jahr_gesprochen(tag.year)}"


def uhrzeit_gesprochen(jetzt: datetime) -> str:
    """Die Uhrzeit ausgeschrieben, etwa "dreiundzwanzig Uhr vierzehn".

    Das Sprachmodell rechnet "23:14" sonst selbst in Worte um und liegt dabei daneben: Im
    Test wurde daraus "fünf nach elf".
    """
    stunde = "ein" if jetzt.hour == 1 else _zahl(jetzt.hour)
    return f"{stunde} Uhr" + (f" {_zahl(jetzt.minute)}" if jetzt.minute else "")


def dauer_gesprochen(sekunden: float) -> str:
    """Eine Dauer ausgeschrieben, etwa "vier Minuten" oder "eine Minute und zehn Sekunden".

    Sekunden nennt sie nur unterhalb einer Stunde; bei längeren Dauern zählen Minuten.
    """
    rest = max(0, round(sekunden))
    stunden, rest = divmod(rest, 3600)
    minuten, sekunden = divmod(rest, 60)

    def wort(n: int) -> str:
        return _zahl(n) if n < 100 else str(n)

    teile = []
    if stunden:
        teile.append("eine Stunde" if stunden == 1 else f"{wort(stunden)} Stunden")
    if minuten:
        teile.append("eine Minute" if minuten == 1 else f"{wort(minuten)} Minuten")
    if sekunden and not stunden:
        teile.append("eine Sekunde" if sekunden == 1 else f"{wort(sekunden)} Sekunden")
    return " und ".join(teile) if teile else "null Sekunden"


def _stufe(regler: str, wert: int) -> str:
    return next(text for grenze, text in STUFEN[regler] if wert <= grenze)


def _liste(zeilen) -> str:
    return "\n".join(f"- {zeile}" for zeile in zeilen)
