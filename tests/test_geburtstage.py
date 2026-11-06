from datetime import date, datetime, timedelta, timezone

from alfred.erinnerungen import Erinnerungen
from alfred.geburtstage import Geburtstagserinnerer, geburtstag_ansage
from alfred.whatsapp import Geburtstag, Kontakte

TZ = timezone(timedelta(hours=2))
JETZT = datetime(2026, 10, 3, 8, 0, tzinfo=TZ)


def test_ansage_nennt_heute_morgen_oder_den_wochentag():
    heute = date(2026, 10, 3)
    max_ = Geburtstag("Max", 4, 10, 1996)
    assert geburtstag_ansage(max_, date(2026, 10, 3), heute) == "Max: heute, wird 30"
    assert geburtstag_ansage(max_, date(2026, 10, 4), heute) == "Max: morgen, wird 30"
    assert geburtstag_ansage(Geburtstag("Anna", 9, 10, None), date(2026, 10, 9), heute) == "Anna: Freitag, 09.10."


def test_erinnert_am_vortag_und_nur_einmal(tmp_path):
    kontakte = Kontakte(tmp_path / "kontakte.yaml")
    kontakte.merken_geburtstag("Max", "04.10.1996")
    kontakte.merken_geburtstag("Anna", "09.10.")
    erinnerungen = Erinnerungen(tmp_path / "e.json", lambda: JETZT)
    merkdatei = tmp_path / "geburtstage_erinnert.json"
    erinnerer = Geburtstagserinnerer(kontakte, erinnerungen, merkdatei, clock=lambda: JETZT)

    assert erinnerer.pruefen() == ["Max"]
    (erinnerung,) = erinnerungen.offene()
    assert erinnerung.text == "Morgen hat Max Geburtstag und wird 30"
    assert erinnerung.faellig_um == JETZT.replace(hour=9)  # zur Erinnerungszeit, nicht sofort um acht

    assert erinnerer.pruefen() == []
    neu = Geburtstagserinnerer(kontakte, erinnerungen, merkdatei, clock=lambda: JETZT)
    assert neu.pruefen() == [], "gemerkte Geburtstage überleben einen Neustart"
    # Nächstes Jahr wieder.
    naechstes_jahr = Geburtstagserinnerer(kontakte, erinnerungen, merkdatei, clock=lambda: JETZT.replace(year=2027))
    assert naechstes_jahr.pruefen() == ["Max"]


def test_nach_der_erinnerungszeit_sofort_und_ohne_jahr_ohne_alter(tmp_path):
    kontakte = Kontakte(tmp_path / "kontakte.yaml")
    kontakte.merken_geburtstag("Anna", "04.10.")
    spaet = JETZT.replace(hour=20)
    erinnerungen = Erinnerungen(tmp_path / "e.json", lambda: spaet)
    erinnerer = Geburtstagserinnerer(kontakte, erinnerungen, tmp_path / "m.json", clock=lambda: spaet)

    assert erinnerer.pruefen() == ["Anna"]
    (erinnerung,) = erinnerungen.offene()
    assert erinnerung.text == "Morgen hat Anna Geburtstag" and erinnerung.faellig_um == spaet
