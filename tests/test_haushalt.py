import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from alfred.haushalt import Ausgaben, Vorrat, Wochenplan, foto_auswerten

TZ = timezone(timedelta(hours=2))
HEUTE = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)


def test_ausgaben_merken_und_uebersicht(tmp_path):
    a = Ausgaben(tmp_path / "ausgaben", lambda: HEUTE)
    a.merken(12.5, "essen gehen", "Mittag")
    a.merken(43.99, "Lebensmittel", "Rewe")
    a.merken(9, "Quatsch", "x")  # unbekannte Kategorie -> Sonstiges
    u = a.uebersicht()
    assert u == {"summe": 65.49, "je_kategorie": {"Lebensmittel": 43.99, "Essen gehen": 12.5, "Sonstiges": 9.0}, "anzahl": 3}
    assert a.monat(date(2026, 9, 1)) == []
    with pytest.raises(ValueError):
        a.merken(0, "x", "")


def test_vorrat(tmp_path):
    v = Vorrat(tmp_path / "vorrat.yaml")
    v.merken("milch", "2 Liter", "2026-10-06")
    v.merken("Reis")
    assert [e.name for e in v.alle()] == ["Milch", "Reis"]  # ablaufendes zuerst
    assert v.merken("Milch", ablauf="2026-10-08").ablauf == "2026-10-08" and len(v.alle()) == 2
    assert [e.name for e in v.ablaufende(3, date(2026, 10, 5))] == ["Milch"] and v.ablaufende(1, date(2026, 10, 5)) == []
    assert v.vorhanden("500 g Reis") and not v.vorhanden("Hackfleisch")
    assert v.verbraucht("reis").name == "Reis" and v.verbraucht("Reis") is None
    with pytest.raises(ValueError):
        v.merken("Käse", ablauf="morgen")


class FakeModell:
    def __init__(self, antwort):
        self.antwort = antwort
        self.aufrufe = []
        self.chat = SimpleNamespace(complete_async=self._complete)

    async def _complete(self, **kwargs):
        self.aufrufe.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.antwort))])


def test_wochenplan_und_zutaten_ohne_vorrat(tmp_path):
    plan = '{"tage": [{"tag": "Montag", "gericht": "Reispfanne", "dauer_minuten": 25, "zutaten": ["200 g Reis", "1 Paprika"], "schritte": ["Reis kochen"], "kcal": 600}, {"tag": "Dienstag", "gericht": "Linsensuppe", "zutaten": ["250 g Linsen"]}]}'
    w = Wochenplan(tmp_path / "plan.yaml", FakeModell(plan), "m")
    ergebnis = asyncio.run(w.erstellen("schnell", 2))
    assert [t["gericht"] for t in ergebnis] == ["Reispfanne", "Linsensuppe"] and w.lesen() == ergebnis
    vorrat = Vorrat(tmp_path / "vorrat.yaml")
    vorrat.merken("Reis")
    assert w.zutaten(vorrat) == ["1 Paprika", "250 g Linsen"]


def test_foto_auswerten_schickt_bild_und_liest_json():
    modell = FakeModell('{"laden": "Rewe", "datum": "2026-10-04", "summe": 23.45, "kategorie": "Lebensmittel", "posten": []}')
    daten = asyncio.run(foto_auswerten(modell, "m", "kassenbon", "QUJD", "image/png"))
    assert daten["summe"] == 23.45
    inhalt = modell.aufrufe[0]["messages"][1]["content"]
    assert inhalt[1] == {"type": "image_url", "image_url": "data:image/png;base64,QUJD"}
    with pytest.raises(ValueError):
        asyncio.run(foto_auswerten(modell, "m", "selfie", "QUJD"))


def test_zutaten_vertragen_einen_unsauberen_plan(tmp_path):
    """Der Plan ist Modellausgabe und von Hand änderbar: Fehlformen werden übersprungen, die Einkaufsliste kommt trotzdem."""
    pfad = tmp_path / "plan.yaml"
    pfad.write_text(
        "- tag: Montag\n  zutaten: [Reis, 3, '', null, '  Paprika ']\n"
        "- tag: Dienstag\n  zutaten: Linsen, Möhren\n"
        "- tag: Mittwoch\n"
        "- Donnerstag\n"
        "- tag: Freitag\n  zutaten: {a: b}\n",
        encoding="utf-8",
    )
    w = Wochenplan(pfad, None, "m")
    assert w.zutaten() == ["Reis", "Paprika", "Linsen, Möhren"]
    vorrat = Vorrat(tmp_path / "vorrat.yaml")
    vorrat.merken("Reis")
    assert w.zutaten(vorrat) == ["Paprika", "Linsen, Möhren"]


def test_wochenplan_lesen_liefert_immer_eine_liste(tmp_path):
    pfad = tmp_path / "plan.yaml"
    w = Wochenplan(pfad, None, "m")
    assert w.lesen() == []
    pfad.write_text("# nur ein Kommentar\n", encoding="utf-8")
    assert w.lesen() == []
    pfad.write_text("tage: kaputt\n", encoding="utf-8")
    assert w.lesen() == [] and w.zutaten() == []
