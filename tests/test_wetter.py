import asyncio

import pytest

from alfred.wetter import GEOCODING_URL, VORHERSAGE_URL, Wetter, wetterlage, zusammenfassung

VORHERSAGE = {
    "daily": {
        "weather_code": [3, 61],
        "temperature_2m_max": [17.4, 12.0],
        "temperature_2m_min": [8.1, 6.6],
        "precipitation_probability_max": [20, 85],
        "wind_speed_10m_max": [12.0, 41.5],
    }
}


class FakeAntwort:
    def __init__(self, daten):
        self._daten = daten

    def raise_for_status(self):
        pass

    def json(self):
        return self._daten


class FakeClient:
    def __init__(self):
        self.aufrufe = []

    async def get(self, url, params):
        self.aufrufe.append((url, params))
        if url == GEOCODING_URL:
            if params["name"] == "Nirgendwo":
                return FakeAntwort({"results": []})
            return FakeAntwort({"results": [{"latitude": 50.0, "longitude": 10.0, "name": "Musterstadt"}]})
        assert url == VORHERSAGE_URL
        return FakeAntwort(VORHERSAGE)


def test_wetterlage_aus_wmo_code():
    assert wetterlage(0) == "klar" and wetterlage(95) == "Gewitter"
    assert wetterlage(123) == "unbestimmtes Wetter"


def test_zusammenfassung_heute_und_morgen():
    assert zusammenfassung(VORHERSAGE, "Musterstadt", "heute") == (
        "Heute in Musterstadt: bedeckt, zwischen 8 und 17 Grad, Regenwahrscheinlichkeit 20 Prozent."
    )
    assert zusammenfassung(VORHERSAGE, "Musterstadt", "morgen") == (
        "Morgen in Musterstadt: leichter Regen, zwischen 7 und 12 Grad, Regenwahrscheinlichkeit 85 Prozent, "
        "Wind bis 42 Kilometer pro Stunde."
    )


def test_vorhersage_mit_standardort_und_koordinaten_cache():
    client = FakeClient()
    wetter = Wetter("Musterstadt", client)

    text = asyncio.run(wetter.vorhersage())
    assert text.startswith("Heute in Musterstadt: bedeckt")
    asyncio.run(wetter.vorhersage(tag="morgen"))

    geocoding = [p for url, p in client.aufrufe if url == GEOCODING_URL]
    vorhersagen = [p for url, p in client.aufrufe if url == VORHERSAGE_URL]
    assert len(geocoding) == 1 and len(vorhersagen) == 2
    assert vorhersagen[0]["latitude"] == 50.0 and vorhersagen[0]["forecast_days"] == 2


def test_gemerkter_wohnort_geht_vor_konfiguration():
    client = FakeClient()
    profil = {"wohnort": ""}
    wetter = Wetter("Berlin", client, wohnort=lambda: profil["wohnort"])
    assert wetter.ort_standard == "Berlin"

    profil["wohnort"] = "Musterstadt"
    assert wetter.ort_standard == "Musterstadt"
    asyncio.run(wetter.vorhersage())
    assert client.aufrufe[0][1]["name"] == "Musterstadt"


def test_vorhersage_ohne_ort_und_mit_unbekanntem_ort():
    client = FakeClient()
    with pytest.raises(ValueError, match="Kein Wohnort bekannt"):
        asyncio.run(Wetter("", client).vorhersage())
    with pytest.raises(ValueError, match="nicht gefunden"):
        asyncio.run(Wetter("", client).vorhersage("Nirgendwo"))
    with pytest.raises(ValueError, match="heute oder morgen"):
        asyncio.run(Wetter("Musterstadt", client).vorhersage(tag="übermorgen"))
