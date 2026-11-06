"""Ernährung: Mahlzeiten protokollieren, Nährwerte schätzen, Ziele und Bilanz, Gewicht.

Schätzungen macht das Sprachmodell aus der Beschreibung ("Brötchen mit Käse und ein Apfel");
sie liegen erfahrungsgemäß zwanzig bis dreißig Prozent daneben und ersetzen keine Waage und
keine Beratung. Verpackte Produkte kommen genauer über Open Food Facts (kostenlos, per
Barcode). Ziele stehen im Profil: kalorienziel, eiweissziel, zielgewicht.

Ablage: gedaechtnis/ernaehrung/JJJJ-MM.yaml (Mahlzeiten) und gedaechtnis/gewicht.yaml.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import yaml

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

_SCHAETZ_AUFTRAG = (
    "Du schätzt Nährwerte von Mahlzeiten für ein privates Ernährungstagebuch. Nimm übliche "
    "Portionsgrößen in Deutschland an, wenn keine Menge genannt ist. Antworte nur mit JSON: "
    '{"kcal": Zahl, "eiweiss": Gramm, "fett": Gramm, "kohlenhydrate": Gramm, '
    '"annahme": "ein kurzer Satz, welche Mengen du angenommen hast"}'
)


@dataclass
class Mahlzeit:
    nummer: int
    zeit: str  # ISO ohne Sekunden
    text: str
    kcal: float
    eiweiss: float
    fett: float
    kohlenhydrate: float
    quelle: str = "schaetzung"  # schaetzung | produkt | foto
    annahme: str = ""

    def ansage(self) -> str:
        return f"{self.nummer}: {self.text}, etwa {round(self.kcal)} Kilokalorien, {round(self.eiweiss)} Gramm Eiweiß"


def bilanz(mahlzeiten: list[Mahlzeit]) -> dict[str, float]:
    return {
        "kcal": round(sum(m.kcal for m in mahlzeiten)),
        "eiweiss": round(sum(m.eiweiss for m in mahlzeiten)),
        "fett": round(sum(m.fett for m in mahlzeiten)),
        "kohlenhydrate": round(sum(m.kohlenhydrate for m in mahlzeiten)),
    }


class Ernaehrung:
    def __init__(self, ordner: Path, clock: Callable[[], datetime] | None = None):
        self._ordner = ordner
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._ordner.mkdir(parents=True, exist_ok=True)

    def _datei(self, tag: date) -> Path:
        return self._ordner / f"{tag:%Y-%m}.yaml"

    def _lesen(self, tag: date) -> list[Mahlzeit]:
        datei = self._datei(tag)
        if not datei.exists():
            return []
        return [Mahlzeit(**m) for m in (yaml.safe_load(datei.read_text(encoding="utf-8")) or [])]

    def _schreiben(self, tag: date, mahlzeiten: list[Mahlzeit]) -> None:
        self._datei(tag).write_text(
            "# Ernährungstagebuch, ein Monat. Nährwerte sind Schätzungen, sofern nicht als produkt markiert.\n"
            + yaml.safe_dump([asdict(m) for m in mahlzeiten], allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def eintragen(self, text: str, naehrwerte: dict, quelle: str = "schaetzung", annahme: str = "") -> Mahlzeit:
        jetzt = self._clock()
        monat = self._lesen(jetzt.date())
        mahlzeit = Mahlzeit(
            max((m.nummer for m in monat), default=0) + 1,
            jetzt.isoformat(timespec="minutes"),
            text.strip(),
            float(naehrwerte.get("kcal", 0) or 0),
            float(naehrwerte.get("eiweiss", 0) or 0),
            float(naehrwerte.get("fett", 0) or 0),
            float(naehrwerte.get("kohlenhydrate", 0) or 0),
            quelle,
            annahme,
        )
        monat.append(mahlzeit)
        self._schreiben(jetzt.date(), monat)
        return mahlzeit

    def tag(self, tag: date | None = None) -> list[Mahlzeit]:
        tag = tag or self._clock().date()
        return [m for m in self._lesen(tag) if m.zeit.startswith(tag.isoformat())]

    def loeschen(self, nummer: int, tag: date | None = None) -> Mahlzeit | None:
        tag = tag or self._clock().date()
        monat = self._lesen(tag)
        treffer = next((m for m in monat if m.nummer == nummer and m.zeit.startswith(tag.isoformat())), None)
        if treffer:
            monat.remove(treffer)
            self._schreiben(tag, monat)
        return treffer

    def woche(self) -> list[dict]:
        """Bilanz je Tag der letzten sieben Tage, älteste zuerst."""
        heute = self._clock().date()
        return [
            {"tag": (heute - timedelta(days=i)).isoformat(), **bilanz(self.tag(heute - timedelta(days=i)))}
            for i in range(6, -1, -1)
        ]

    # Gewicht
    def gewichte(self) -> dict[str, float]:
        datei = self._ordner.parent / "gewicht.yaml"
        if not datei.exists():
            return {}
        return {str(k): float(v) for k, v in (yaml.safe_load(datei.read_text(encoding="utf-8")) or {}).items()}

    def gewicht_merken(self, kg: float, tag: date | None = None) -> dict[str, float]:
        if not 20 < kg < 400:
            raise ValueError("Das kann kein Körpergewicht in Kilogramm sein.")
        gewichte = self.gewichte()
        gewichte[(tag or self._clock().date()).isoformat()] = round(kg, 1)
        (self._ordner.parent / "gewicht.yaml").write_text(
            "# Gewicht in Kilogramm je Tag.\n" + yaml.safe_dump(dict(sorted(gewichte.items())), allow_unicode=True),
            encoding="utf-8",
        )
        return gewichte


class Schaetzer:
    """Nährwerte aus Text (Sprachmodell) oder Barcode (Open Food Facts)."""

    def __init__(self, client, modell: str, http: httpx.AsyncClient | None = None):
        self._client = client
        self._modell = modell
        self._http = http

    async def schaetzen(self, text: str) -> dict:
        antwort = await self._client.chat.complete_async(
            model=self._modell,
            messages=[{"role": "system", "content": _SCHAETZ_AUFTRAG}, {"role": "user", "content": text}],
            response_format={"type": "json_object"},
            max_tokens=200,
        )
        inhalt = antwort.choices[0].message.content
        if isinstance(inhalt, list):
            inhalt = "".join(getattr(t, "text", "") for t in inhalt)
        daten = json.loads(inhalt)
        return {
            "kcal": float(daten.get("kcal", 0) or 0),
            "eiweiss": float(daten.get("eiweiss", 0) or 0),
            "fett": float(daten.get("fett", 0) or 0),
            "kohlenhydrate": float(daten.get("kohlenhydrate", 0) or 0),
            "annahme": str(daten.get("annahme", "")),
        }

    async def produkt(self, barcode: str) -> dict:
        """Name und Nährwerte je 100 g aus Open Food Facts."""
        barcode = "".join(ch for ch in barcode if ch.isdigit())
        url = OFF_URL.format(barcode=barcode)
        params = {"fields": "product_name,product_name_de,brands,nutriments,serving_size"}
        try:
            if self._http is not None:
                antwort = await self._http.get(url, params=params)
            else:
                async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Alfred/1.0 (privat)"}) as http:
                    antwort = await http.get(url, params=params)
        except httpx.HTTPError as e:
            raise RuntimeError(f"Open Food Facts nicht erreichbar ({e.__class__.__name__})") from e
        daten = antwort.json() if antwort.status_code == 200 else {}
        produkt = daten.get("product") or {}
        if not produkt:
            raise ValueError(f"Kein Produkt mit dem Barcode {barcode} bekannt.")
        n = produkt.get("nutriments", {})
        name = produkt.get("product_name_de") or produkt.get("product_name") or "Unbekanntes Produkt"
        if produkt.get("brands"):
            name = f"{name} ({produkt['brands']})"
        return {
            "name": name,
            "je_100g": {
                "kcal": float(n.get("energy-kcal_100g", 0) or 0),
                "eiweiss": float(n.get("proteins_100g", 0) or 0),
                "fett": float(n.get("fat_100g", 0) or 0),
                "kohlenhydrate": float(n.get("carbohydrates_100g", 0) or 0),
            },
            "portion": produkt.get("serving_size", ""),
        }


def anteilig(je_100g: dict, gramm: float) -> dict:
    faktor = gramm / 100.0
    return {k: round(v * faktor, 1) for k, v in je_100g.items()}
