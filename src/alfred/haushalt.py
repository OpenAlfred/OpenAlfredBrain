"""Haushalt: Ausgaben, Vorrat mit Haltbarkeit, Wochenplan mit Rezepten, Fotos auswerten.

- Ausgaben: gedaechtnis/ausgaben/JJJJ-MM.yaml, per Satz oder Kassenbon-Foto.
- Vorrat: gedaechtnis/vorrat.yaml; ein Ablaufdatum legt eine Erinnerung für den Vortag an.
- Wochenplan: gedaechtnis/wochenplan.yaml, vom Sprachmodell als JSON erzeugt; die Zutaten
  gehen auf Wunsch auf die Einkaufsliste, abzüglich dessen, was im Vorrat ist.
- Fotos: Mistral liest Nährwerttabellen und Kassenbons (Bild als Base64 im Chat-Aufruf).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from alfred.dateien import Dateibestand

KATEGORIEN = ("Lebensmittel", "Essen gehen", "Haushalt", "Mobilität", "Freizeit", "Kleidung", "Gesundheit", "Sonstiges")

_PLAN_AUFTRAG = (
    "Du planst Abendessen für eine Person in Deutschland. Antworte nur mit JSON: "
    '{"tage": [{"tag": "Montag", "gericht": "Name", "dauer_minuten": Zahl, "zutaten": ["500 g Hackfleisch", ...], '
    '"schritte": ["kurz", "..."], "kcal": Zahl}]}. Zutaten mit Mengen, Schritte knapp, höchstens fünf.'
)
_FOTO_AUFTRAEGE = {
    "naehrwerte": (
        "Lies die Nährwerttabelle auf dem Foto. Antworte nur mit JSON: "
        '{"name": "Produktname oder leer", "je_100g": {"kcal": Zahl, "eiweiss": g, "fett": g, "kohlenhydrate": g}, '
        '"portion_g": Zahl oder null}. Fehlt ein Wert, nimm 0.'
    ),
    "kassenbon": (
        "Lies den Kassenbon auf dem Foto. Antworte nur mit JSON: "
        '{"laden": "Name", "datum": "JJJJ-MM-TT oder leer", "summe": Zahl, "kategorie": "Lebensmittel|Haushalt|Sonstiges", '
        '"posten": [{"text": "Artikel", "betrag": Zahl}]}.'
    ),
}


@dataclass
class Ausgabe:
    nummer: int
    datum: str
    betrag: float
    kategorie: str
    text: str


class Ausgaben:
    def __init__(self, ordner: Path, clock: Callable[[], datetime] | None = None):
        self._ordner = ordner
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._ordner.mkdir(parents=True, exist_ok=True)

    def _datei(self, tag: date) -> Path:
        return self._ordner / f"{tag:%Y-%m}.yaml"

    def monat(self, tag: date | None = None) -> list[Ausgabe]:
        datei = self._datei(tag or self._clock().date())
        if not datei.exists():
            return []
        return [Ausgabe(**a) for a in (yaml.safe_load(datei.read_text(encoding="utf-8")) or [])]

    def merken(self, betrag: float, kategorie: str, text: str, tag: date | None = None) -> Ausgabe:
        if betrag <= 0:
            raise ValueError("Der Betrag muss größer als null sein.")
        kategorie = next((k for k in KATEGORIEN if k.casefold() == kategorie.strip().casefold()), "Sonstiges")
        tag = tag or self._clock().date()
        monat = self.monat(tag)
        ausgabe = Ausgabe(max((a.nummer for a in monat), default=0) + 1, tag.isoformat(), round(betrag, 2), kategorie, text.strip())
        monat.append(ausgabe)
        self._datei(tag).write_text(
            "# Ausgaben eines Monats in Euro.\n" + yaml.safe_dump([asdict(a) for a in monat], allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return ausgabe

    def uebersicht(self, tag: date | None = None) -> dict:
        monat = self.monat(tag)
        je_kategorie: dict[str, float] = {}
        for a in monat:
            je_kategorie[a.kategorie] = round(je_kategorie.get(a.kategorie, 0) + a.betrag, 2)
        return {"summe": round(sum(a.betrag for a in monat), 2), "je_kategorie": dict(sorted(je_kategorie.items(), key=lambda kv: -kv[1])), "anzahl": len(monat)}


@dataclass
class Vorratsartikel:
    name: str
    menge: str = ""
    ablauf: str | None = None  # ISO-Datum


class Vorrat(Dateibestand):
    def __init__(self, pfad: Path):
        self._eintraege: list[Vorratsartikel] = []
        super().__init__(pfad)

    def _laden(self, daten) -> None:
        self._eintraege = [Vorratsartikel(**e) for e in (daten or [])]

    def _speichern(self) -> None:
        self._schreiben("# Vorrat zu Hause, mit Ablaufdatum, wo bekannt.\n" + yaml.safe_dump([asdict(e) for e in self._eintraege], allow_unicode=True, sort_keys=False))

    def alle(self) -> list[Vorratsartikel]:
        self._auffrischen()
        return sorted(self._eintraege, key=lambda e: (e.ablauf is None, e.ablauf or "", e.name))

    def merken(self, name: str, menge: str = "", ablauf: str = "") -> Vorratsartikel:
        self._auffrischen()
        if ablauf:
            date.fromisoformat(ablauf)
        name = name.strip()
        artikel = next((e for e in self._eintraege if e.name.casefold() == name.casefold()), None)
        if artikel:
            artikel.menge, artikel.ablauf = menge or artikel.menge, ablauf or artikel.ablauf
        else:
            artikel = Vorratsartikel(name[:1].upper() + name[1:], menge, ablauf or None)
            self._eintraege.append(artikel)
        self._speichern()
        return artikel

    def verbraucht(self, name: str) -> Vorratsartikel | None:
        self._auffrischen()
        gesucht = name.strip().casefold()
        treffer = [e for e in self._eintraege if e.name.casefold() == gesucht] or [e for e in self._eintraege if gesucht in e.name.casefold()]
        if len(treffer) != 1:
            return None
        self._eintraege.remove(treffer[0])
        self._speichern()
        return treffer[0]

    def vorhanden(self, zutat: str) -> bool:
        woerter = [w for w in re.findall(r"[a-zäöüß]+", zutat.casefold()) if len(w) > 3]
        return any(w in e.name.casefold() for e in self.alle() for w in woerter)

    def ablaufende(self, tage: int, heute: date) -> list[Vorratsartikel]:
        return [e for e in self.alle() if e.ablauf and date.fromisoformat(e.ablauf) <= heute + timedelta(days=tage)]


class Wochenplan:
    def __init__(self, pfad: Path, client, modell: str):
        self._pfad = pfad
        self._client = client
        self._modell = modell

    def lesen(self) -> list[dict]:
        if not self._pfad.exists():
            return []
        plan = yaml.safe_load(self._pfad.read_text(encoding="utf-8"))
        return plan if isinstance(plan, list) else []

    async def erstellen(self, wuensche: str, tage: int = 5) -> list[dict]:
        antwort = await self._client.chat.complete_async(
            model=self._modell,
            messages=[{"role": "system", "content": _PLAN_AUFTRAG}, {"role": "user", "content": f"{tage} Abendessen. Wünsche: {wuensche or 'abwechslungsreich, alltagstauglich'}"}],
            response_format={"type": "json_object"},
            max_tokens=1800,
        )
        inhalt = antwort.choices[0].message.content
        if isinstance(inhalt, list):
            inhalt = "".join(getattr(t, "text", "") for t in inhalt)
        plan = json.loads(inhalt).get("tage", [])[:tage]
        self._pfad.write_text("# Wochenplan, vom Sprachmodell erstellt.\n" + yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return plan

    def zutaten(self, vorrat: Vorrat | None = None) -> list[str]:
        """Alle Zutaten des Plans, ohne das, was laut Vorrat da ist."""
        zutaten: list[str] = []
        for tag in self.lesen():
            # Der Plan ist Modellausgabe und von Hand änderbar: Ein Tag ohne Liste, eine Zahl als Zutat oder ein leerer Eintrag
            # darf die Einkaufsliste nicht mit einem Fehler verhindern.
            roh = tag.get("zutaten") if isinstance(tag, dict) else None
            for z in [roh] if isinstance(roh, str) else roh if isinstance(roh, list) else []:
                if isinstance(z, str) and z.strip():
                    zutaten.append(z.strip())
        return [z for z in zutaten if not (vorrat and vorrat.vorhanden(z))]


async def foto_auswerten(client, modell: str, art: str, bild_base64: str, mime: str = "image/jpeg") -> dict:
    """Lässt Mistral ein Foto lesen (Nährwerttabelle oder Kassenbon) und gibt das JSON zurück."""
    if art not in _FOTO_AUFTRAEGE:
        raise ValueError("art muss naehrwerte oder kassenbon sein.")
    antwort = await client.chat.complete_async(
        model=modell,
        messages=[
            {"role": "system", "content": _FOTO_AUFTRAEGE[art]},
            {"role": "user", "content": [{"type": "text", "text": "Hier das Foto."}, {"type": "image_url", "image_url": f"data:{mime};base64,{bild_base64}"}]},
        ],
        response_format={"type": "json_object"},
        max_tokens=600,
    )
    inhalt = antwort.choices[0].message.content
    if isinstance(inhalt, list):
        inhalt = "".join(getattr(t, "text", "") for t in inhalt)
    return json.loads(inhalt)
