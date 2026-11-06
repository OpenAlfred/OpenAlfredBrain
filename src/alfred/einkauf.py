"""Einkaufsliste: per Stimme oder App füllen, im Laden abhaken, nach Abteilung sortiert.

Die Liste liegt als YAML im Gedächtnisordner. Stimme (Raum- oder Browser-Prozess) und App
(Web-Prozess) schreiben dieselbe Datei; vor jedem Zugriff wird sie neu gelesen, wenn sie sich
geändert hat.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml

from alfred.dateien import Dateibestand

# Abteilungen in der Reihenfolge, wie man durch einen Supermarkt läuft; Schlüsselwörter als Wortanfänge.
ABTEILUNGEN: dict[str, tuple[str, ...]] = {
    "Obst & Gemüse": ("apfel", "äpfel", "banane", "birne", "beere", "erdbeer", "himbeer", "trauben", "zitrone", "limette", "orange", "mandarine", "kiwi", "mango", "avocado", "tomate", "gurke", "paprika", "zwiebel", "knoblauch", "kartoffel", "karotte", "möhre", "salat", "spinat", "brokkoli", "blumenkohl", "zucchini", "aubergine", "pilz", "champignon", "lauch", "porree", "ingwer", "kräuter", "petersilie", "basilikum", "obst", "gemüse", "melone", "pfirsich", "pflaume", "kirsche"),
    "Brot & Backwaren": ("brot", "brötchen", "semmel", "toast", "baguette", "croissant", "kuchen", "knäcke", "zwieback", "wrap", "tortilla"),
    "Kühlregal": ("milch", "käse", "joghurt", "quark", "butter", "sahne", "schmand", "frischkäse", "ei ", "eier", "wurst", "schinken", "salami", "aufschnitt", "tofu", "hummus", "margarine", "pudding", "skyr", "mozzarella", "feta", "parmesan", "hafermilch", "saft"),
    "Fleisch & Fisch": ("hack", "hähnchen", "huhn", "pute", "rind", "schwein", "steak", "lachs", "fisch", "thunfisch", "garnele", "bratwurst", "filet"),
    "Trocken & Konserven": ("reis", "nudel", "pasta", "spaghetti", "mehl", "zucker", "salz", "pfeffer", "öl", "essig", "gewürz", "haferflocken", "müsli", "cornflakes", "linsen", "bohnen", "kichererbsen", "mais", "tomatenmark", "passierte", "dose", "honig", "marmelade", "nuss", "nüsse", "mandel", "schokolade", "keks", "chips", "kaffee", "tee", "kakao", "brühe", "senf", "ketchup", "soße", "sauce", "couscous", "quinoa"),
    "Tiefkühl": ("tiefkühl", "tk ", "pizza", "eis", "pommes", "gefroren"),
    "Getränke": ("wasser", "sprudel", "cola", "limo", "bier", "wein", "sekt", "apfelschorle", "energy", "mate"),
    "Haushalt & Drogerie": ("spülmittel", "spültab", "waschmittel", "müllbeutel", "klopapier", "toilettenpapier", "küchenrolle", "zahnpasta", "zahnbürste", "shampoo", "duschgel", "seife", "deo", "rasier", "taschentücher", "batterie", "alufolie", "frischhalte", "schwamm", "putz", "windeln"),
}
SONSTIGES = "Sonstiges"

_MENGE = re.compile(r"^(?P<menge>\d+[.,]?\d*\s*(?:x|g|kg|l|ml|stück|st\.?|packung(?:en)?|pack|dosen?|flaschen?|liter|gramm|kilo)?)\s+(?P<name>.+)$", re.I)


def abteilung_fuer(name: str) -> str:
    text = name.casefold() + " "
    for abteilung, woerter in ABTEILUNGEN.items():
        if any(w in text for w in woerter):
            return abteilung
    return SONSTIGES


def artikel_trennen(text: str) -> list[tuple[str, str]]:
    """"2 Brot, Milch und 500 g Hack" -> [("2", "Brot"), ("", "Milch"), ("500 g", "Hack")]."""
    teile = re.split(r",(?!\d)|;|\bund\b|\n", text)  # Komma, aber nicht in "1,5 l"
    ergebnis = []
    for teil in teile:
        teil = teil.strip(" .")
        if not teil:
            continue
        m = _MENGE.match(teil)
        if m:
            ergebnis.append((m.group("menge").strip(), m.group("name").strip()))
        else:
            ergebnis.append(("", teil))
    return ergebnis


@dataclass
class Artikel:
    name: str
    menge: str = ""
    abteilung: str = SONSTIGES
    erledigt: bool = False
    angelegt: str = ""  # ISO-Zeit

    def ansage(self) -> str:
        return f"{self.menge} {self.name}".strip()


class Einkaufsliste(Dateibestand):
    def __init__(self, pfad: Path, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._eintraege: list[Artikel] = []
        super().__init__(pfad)

    def _laden(self, daten) -> None:
        self._eintraege = [Artikel(**e) for e in (daten or [])]

    def _speichern(self) -> None:
        self._schreiben(
            "# Alfreds Einkaufsliste. Erledigte bleiben, bis die Liste geleert wird.\n"
            + yaml.safe_dump([asdict(a) for a in self._eintraege], allow_unicode=True, sort_keys=False)
        )

    def alle(self) -> list[Artikel]:
        self._auffrischen()
        return list(self._eintraege)

    def offene(self) -> list[Artikel]:
        return [a for a in self.alle() if not a.erledigt]

    def nach_abteilung(self) -> dict[str, list[Artikel]]:
        """Offene Artikel in Supermarkt-Reihenfolge der Abteilungen."""
        gruppen: dict[str, list[Artikel]] = {}
        for a in self.offene():
            gruppen.setdefault(a.abteilung, []).append(a)
        reihenfolge = [*ABTEILUNGEN, SONSTIGES]
        return {k: gruppen[k] for k in reihenfolge if k in gruppen}

    def hinzufuegen(self, text: str) -> list[Artikel]:
        """Nimmt einen Satz mit mehreren Artikeln; schon vorhandene offene werden nicht verdoppelt."""
        self._auffrischen()
        neue = []
        for menge, name in artikel_trennen(text):
            vorhanden = next((a for a in self._eintraege if not a.erledigt and a.name.casefold() == name.casefold()), None)
            if vorhanden:
                if menge:
                    vorhanden.menge = menge
                continue
            artikel = Artikel(name[:1].upper() + name[1:], menge, abteilung_fuer(name), False, self._clock().isoformat(timespec="minutes"))
            self._eintraege.append(artikel)
            neue.append(artikel)
        if neue or text:
            self._speichern()
        return neue

    def _finden(self, name: str) -> Artikel | None:
        gesucht = name.strip().casefold()
        offene = [a for a in self._eintraege if not a.erledigt]
        for a in offene:
            if a.name.casefold() == gesucht:
                return a
        treffer = [a for a in offene if gesucht in a.name.casefold() or a.name.casefold() in gesucht]
        return treffer[0] if len(treffer) == 1 else None

    def abhaken(self, name: str, erledigt: bool = True) -> Artikel | None:
        self._auffrischen()
        artikel = self._finden(name) if erledigt else next((a for a in self._eintraege if a.erledigt and a.name.casefold() == name.strip().casefold()), None)
        if artikel:
            artikel.erledigt = erledigt
            self._speichern()
        return artikel

    def entfernen(self, name: str) -> Artikel | None:
        self._auffrischen()
        artikel = self._finden(name)
        if artikel:
            self._eintraege.remove(artikel)
            self._speichern()
        return artikel

    def leeren(self, nur_erledigte: bool = True) -> int:
        self._auffrischen()
        vorher = len(self._eintraege)
        self._eintraege = [a for a in self._eintraege if nur_erledigte and not a.erledigt]
        self._speichern()
        return vorher - len(self._eintraege)
