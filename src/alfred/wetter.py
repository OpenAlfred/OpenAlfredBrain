"""Wettervorhersage über Open-Meteo: kostenlos, ohne Schlüssel und ohne Konto.

Der Ort kommt aus dem Satz ("Wetter in Berlin"), sonst aus dem Profil (Wohnort, den Alfred
sich gemerkt hat), sonst aus config/alfred.yaml (wetter.ort). Ortsnamen werden einmal über
die Geocoding-API von Open-Meteo in Koordinaten übersetzt und dann für die Laufzeit des
Prozesses behalten.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
VORHERSAGE_URL = "https://api.open-meteo.com/v1/forecast"

# WMO-Wettercodes, wie Open-Meteo sie liefert.
_WETTERLAGEN = {
    0: "klar",
    1: "überwiegend klar",
    2: "teils bewölkt",
    3: "bedeckt",
    45: "Nebel",
    48: "Nebel mit Reif",
    51: "leichter Nieselregen",
    53: "Nieselregen",
    55: "starker Nieselregen",
    56: "gefrierender Nieselregen",
    57: "starker gefrierender Nieselregen",
    61: "leichter Regen",
    63: "Regen",
    65: "starker Regen",
    66: "gefrierender Regen",
    67: "starker gefrierender Regen",
    71: "leichter Schneefall",
    73: "Schneefall",
    75: "starker Schneefall",
    77: "Schneegriesel",
    80: "leichte Regenschauer",
    81: "Regenschauer",
    82: "heftige Regenschauer",
    85: "leichte Schneeschauer",
    86: "starke Schneeschauer",
    95: "Gewitter",
    96: "Gewitter mit leichtem Hagel",
    99: "Gewitter mit Hagel",
}


def wetterlage(code: int | None) -> str:
    return _WETTERLAGEN.get(code, "unbestimmtes Wetter")


def zusammenfassung(daten: dict, ort: str, tag: str) -> str:
    """Ein vorlesbarer Satz aus der Tagesvorhersage von Open-Meteo.

    Args:
        daten: Die Antwort der Vorhersage-API (Feld "daily" mit Listen je Tag).
        ort: Name des Orts für die Ansage.
        tag: "heute" oder "morgen"; wählt den Eintrag aus den Listen.
    """
    daily = daten["daily"]
    i = 1 if tag == "morgen" else 0
    teile = [f"{tag.capitalize()} in {ort}: {wetterlage(daily['weather_code'][i])}"]
    tief, hoch = daily["temperature_2m_min"][i], daily["temperature_2m_max"][i]
    if tief is not None and hoch is not None:
        teile.append(f"zwischen {round(tief)} und {round(hoch)} Grad")
    regen = daily.get("precipitation_probability_max", [None, None])[i]
    if regen is not None:
        teile.append(f"Regenwahrscheinlichkeit {round(regen)} Prozent")
    wind = daily.get("wind_speed_10m_max", [None, None])[i]
    if wind is not None and wind >= 30:
        teile.append(f"Wind bis {round(wind)} Kilometer pro Stunde")
    return ", ".join(teile) + "."


class Wetter:
    def __init__(
        self,
        ort_standard: str = "",
        client: httpx.AsyncClient | None = None,
        wohnort: Callable[[], str] | None = None,
    ):
        """Args:
            ort_standard: Ort aus der Konfiguration, gilt ohne Angabe im Satz und ohne Profil.
            client: HTTP-Client, für Tests austauschbar.
            wohnort: Liefert den gemerkten Wohnort aus dem Profil, oder "" wenn unbekannt.
        """
        self._ort_standard = ort_standard.strip()
        self._client = client
        self._wohnort = wohnort or (lambda: "")
        self._koordinaten: dict[str, tuple[float, float, str]] = {}

    @property
    def ort_standard(self) -> str:
        """Der Ort, der ohne Angabe im Satz gilt: gemerkter Wohnort vor Konfiguration."""
        return self._wohnort().strip() or self._ort_standard

    async def _get(self, url: str, params: dict) -> dict:
        if self._client is not None:
            antwort = await self._client.get(url, params=params)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                antwort = await client.get(url, params=params)
        antwort.raise_for_status()
        return antwort.json()

    async def koordinaten(self, ort: str) -> tuple[float, float, str]:
        """Breite, Länge und amtlicher Name eines Orts."""
        schluessel = ort.strip().casefold()
        if schluessel not in self._koordinaten:
            daten = await self._get(GEOCODING_URL, {"name": ort, "count": 1, "language": "de", "format": "json"})
            treffer = (daten.get("results") or [None])[0]
            if not treffer:
                raise ValueError(f"Ort nicht gefunden: {ort}")
            self._koordinaten[schluessel] = (treffer["latitude"], treffer["longitude"], treffer["name"])
        return self._koordinaten[schluessel]

    async def vorhersage(self, ort: str = "", tag: str = "heute") -> str:
        """Die Vorhersage für heute oder morgen als vorlesbarer Satz."""
        ort = ort.strip() or self.ort_standard
        if not ort:
            raise ValueError(
                "Kein Wohnort bekannt. Frag das Gegenüber, wo es wohnt, merk dir die Antwort mit "
                "profil_merken (Feld wohnort) und frag das Wetter dann noch einmal ab."
            )
        if tag not in ("heute", "morgen"):
            raise ValueError("tag muss heute oder morgen sein.")
        breite, laenge, name = await self.koordinaten(ort)
        daten = await self._get(
            VORHERSAGE_URL,
            {
                "latitude": breite,
                "longitude": laenge,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
                "timezone": "auto",
                "forecast_days": 2,
            },
        )
        return zusammenfassung(daten, name, tag)
