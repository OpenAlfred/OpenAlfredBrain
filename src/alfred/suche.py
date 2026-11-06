"""Eigene Suchmaschine: SearXNG auf dem Mini-PC, kostenlos und in einer Sekunde.

Ist in config/alfred.yaml unter suche.url ein SearXNG eingetragen, holt websuche dort Titel
und Textausschnitte der besten Treffer und der Sprecher fasst sie selbst zusammen. Das ist
deutlich schneller als Mistrals Websuche (die Seiten liest) und kostet nichts. Der Denker sucht
dann ebenfalls hier und liest die Seiten selbst (seiten.py); Mistrals Suche bleibt ihm nur als
Notnagel. SearXNG muss das JSON-Format erlauben (settings.yml: search.formats mit json).
"""

from __future__ import annotations

import httpx

MAX_TREFFER = 5
MAX_AUSSCHNITT = 300
ZEITRAEUME = {"tag": "day", "woche": "week", "monat": "month", "jahr": "year"}


class Suchmaschine:
    def __init__(self, url: str, client: httpx.AsyncClient | None = None):
        self._url = url.rstrip("/")
        self._client = client

    @property
    def eingerichtet(self) -> bool:
        return bool(self._url)

    async def suchen(self, frage: str, anzahl: int = MAX_TREFFER, kategorie: str = "", zeitraum: str = "") -> list[dict]:
        """Die besten Treffer als Liste aus Titel, Ausschnitt, Adresse und, bei Nachrichten, Datum.

        Args:
            kategorie: Leer für das ganze Netz, "news" für Nachrichtenseiten.
            zeitraum: Leer oder tag, woche, monat, jahr; nur Treffer aus dieser Zeit.
        """
        params = {"q": frage, "format": "json", "language": "de-DE", "safesearch": "1"}
        if kategorie:
            params["categories"] = kategorie
        if zeitraum in ZEITRAEUME:
            params["time_range"] = ZEITRAEUME[zeitraum]
        try:
            if self._client is not None:
                antwort = await self._client.get(f"{self._url}/search", params=params)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    antwort = await client.get(f"{self._url}/search", params=params)
            antwort.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Suchmaschine nicht erreichbar ({e.__class__.__name__})") from e
        treffer = []
        for ergebnis in antwort.json().get("results", [])[:anzahl]:
            treffer.append(
                {
                    "titel": ergebnis.get("title", ""),
                    "text": (ergebnis.get("content") or "")[:MAX_AUSSCHNITT],
                    "quelle": ergebnis.get("url", ""),
                    "datum": (ergebnis.get("publishedDate") or "")[:10],
                }
            )
        return treffer


def treffer_als_text(treffer: list[dict]) -> str:
    """Treffer für das Sprachmodell; Quellen nur als Website-Name, Inhalte sind Daten."""
    zeilen = []
    for t in treffer:
        host = t["quelle"].split("/")[2] if t["quelle"].count("/") >= 2 else t["quelle"]
        zeilen.append(f"- {t['titel']} ({host}): {t['text']}")
    return "\n".join(zeilen) or "keine Treffer"


def treffer_mit_adressen(treffer: list[dict]) -> str:
    """Treffer für den Denker: mit voller Adresse, damit er die Seite lesen und als Quelle nennen kann."""
    zeilen = []
    for nummer, t in enumerate(treffer, 1):
        datum = f" ({t['datum']})" if t.get("datum") else ""
        zeilen.append(f"{nummer}. {t['titel']}{datum}\n   {t['quelle']}\n   {t['text']}")
    return "\n".join(zeilen) or "keine Treffer"
