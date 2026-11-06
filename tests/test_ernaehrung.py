import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from alfred.ernaehrung import Ernaehrung, Schaetzer, anteilig, bilanz

TZ = timezone(timedelta(hours=2))
HEUTE = datetime(2026, 10, 4, 12, 30, tzinfo=TZ)


class Uhr:
    def __init__(self, jetzt):
        self.jetzt = jetzt

    def __call__(self):
        return self.jetzt


def test_tagebuch_eintragen_bilanz_woche_loeschen(tmp_path):
    uhr = Uhr(HEUTE)
    e = Ernaehrung(tmp_path / "ernaehrung", uhr)
    a = e.eintragen("Brötchen mit Käse", {"kcal": 320, "eiweiss": 14, "fett": 12, "kohlenhydrate": 38}, annahme="ein Brötchen")
    e.eintragen("Apfel", {"kcal": 80, "eiweiss": 0.4, "fett": 0.2, "kohlenhydrate": 19})
    assert a.ansage() == "1: Brötchen mit Käse, etwa 320 Kilokalorien, 14 Gramm Eiweiß"
    assert bilanz(e.tag()) == {"kcal": 400, "eiweiss": 14, "fett": 12, "kohlenhydrate": 57}

    uhr.jetzt = HEUTE + timedelta(days=1)
    e.eintragen("Müsli", {"kcal": 450, "eiweiss": 15, "fett": 10, "kohlenhydrate": 70})
    assert [m.text for m in e.tag()] == ["Müsli"] and [m.text for m in e.tag(HEUTE.date())] == ["Brötchen mit Käse", "Apfel"]
    woche = e.woche()
    assert len(woche) == 7 and woche[-1]["kcal"] == 450 and woche[-2]["kcal"] == 400 and woche[0]["kcal"] == 0

    assert e.loeschen(3).text == "Müsli" and e.tag() == [] and e.loeschen(3) is None
    # Monatswechsel: neue Datei.
    uhr.jetzt = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)
    e.eintragen("Toast", {"kcal": 100})
    assert (tmp_path / "ernaehrung" / "2026-11.yaml").exists() and e.tag()[0].kcal == 100


def test_gewicht(tmp_path):
    e = Ernaehrung(tmp_path / "ernaehrung", Uhr(HEUTE))
    assert e.gewicht_merken(80.26) == {"2026-10-04": 80.3}
    e.gewicht_merken(79.9, HEUTE.date() + timedelta(days=1))
    assert list(Ernaehrung(tmp_path / "ernaehrung").gewichte()) == ["2026-10-04", "2026-10-05"]
    with pytest.raises(ValueError):
        e.gewicht_merken(5)


class FakeModell:
    def __init__(self):
        self.chat = SimpleNamespace(complete_async=self._complete)

    async def _complete(self, **kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"kcal": 410, "eiweiss": 18, "fett": 15, "kohlenhydrate": 50, "annahme": "zwei Brötchen à 60 g"}'
        ))])


def test_schaetzer_text():
    werte = asyncio.run(Schaetzer(FakeModell(), "m").schaetzen("zwei Brötchen mit Käse"))
    assert werte == {"kcal": 410.0, "eiweiss": 18.0, "fett": 15.0, "kohlenhydrate": 50.0, "annahme": "zwei Brötchen à 60 g"}


def test_schaetzer_barcode_und_anteil():
    def handler(request: httpx.Request) -> httpx.Response:
        if "4000417025005" in str(request.url):
            return httpx.Response(200, json={"product": {"product_name_de": "Vollmilch", "brands": "Beispiel", "serving_size": "250 ml",
                                                         "nutriments": {"energy-kcal_100g": 64, "proteins_100g": 3.4, "fat_100g": 3.5, "carbohydrates_100g": 4.8}}})
        return httpx.Response(404, json={"status": 0})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    s = Schaetzer(None, "m", http)
    p = asyncio.run(s.produkt("4000417025005"))
    assert p["name"] == "Vollmilch (Beispiel)" and p["je_100g"]["kcal"] == 64 and p["portion"] == "250 ml"
    assert anteilig(p["je_100g"], 250) == {"kcal": 160.0, "eiweiss": 8.5, "fett": 8.8, "kohlenhydrate": 12.0}
    with pytest.raises(ValueError):
        asyncio.run(s.produkt("000"))
