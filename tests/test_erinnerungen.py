from datetime import datetime, timedelta, timezone

import pytest

from alfred.ereignisse import Ansage
from alfred.erinnerungen import Erinnerungen, zeitpunkt

TZ = timezone(timedelta(hours=2))
JETZT = datetime(2026, 10, 3, 9, 0, tzinfo=TZ)


class Uhr:
    def __init__(self, jetzt=JETZT):
        self.jetzt = jetzt

    def __call__(self):
        return self.jetzt


def test_anlegen_persistiert_und_sortiert(tmp_path):
    datei = tmp_path / "erinnerungen.json"
    e = Erinnerungen(datei, Uhr())
    spaet = e.anlegen("Wäsche", JETZT + timedelta(hours=2))
    frueh = e.anlegen("Kaffee", JETZT + timedelta(minutes=5))

    neu = Erinnerungen(datei, Uhr())
    assert [x.text for x in neu.offene()] == ["Kaffee", "Wäsche"]
    assert (spaet.nummer, frueh.nummer) == (1, 2)


def test_faellige_und_ansage(tmp_path):
    uhr = Uhr()
    e = Erinnerungen(tmp_path / "e.json", uhr)
    e.anlegen("Wäsche", JETZT + timedelta(minutes=10))
    assert e.faellige() == []

    uhr.jetzt = JETZT + timedelta(minutes=10)
    (faellig,) = e.faellige()
    ansage = e.ansage(faellig)
    assert "'Wäsche' ist jetzt fällig" in ansage and "kein Werkzeugaufruf" in ansage
    assert e.faellige() == [] and e.offene() == []


def test_verspaetete_erinnerung_nennt_die_uhrzeit(tmp_path):
    uhr = Uhr()
    e = Erinnerungen(tmp_path / "e.json", uhr)
    e.anlegen("Wäsche", JETZT)
    uhr.jetzt = JETZT + timedelta(minutes=30)

    (faellig,) = e.faellige()
    assert "um 09:00 Uhr fällig" in e.ansage(faellig)


def test_loeschen(tmp_path):
    e = Erinnerungen(tmp_path / "e.json", Uhr())
    eintrag = e.anlegen("Wäsche", JETZT + timedelta(minutes=10))

    assert e.loeschen(eintrag.nummer) and not e.loeschen(eintrag.nummer)
    assert e.offene() == []


def test_timer_klingelt_woertlich_statt_ueber_das_modell(tmp_path):
    uhr = Uhr()
    datei = tmp_path / "e.json"
    e = Erinnerungen(datei, uhr)
    e.anlegen("Wäsche", JETZT + timedelta(minutes=5))
    nudeln = e.anlegen("Nudeln", JETZT + timedelta(minutes=8), art="timer")
    e.anlegen("Timer", JETZT + timedelta(minutes=9), art="timer")

    assert [t.text for t in e.timer()] == ["Nudeln", "Timer"]
    assert nudeln.kurztext() == "Timer abgelaufen: Nudeln"
    assert Erinnerungen(datei, uhr).timer()[0].timer, "die Art überlebt das Speichern"

    uhr.jetzt = JETZT + timedelta(minutes=10)
    ansagen = [e.ansage(x) for x in e.faellige()]
    assert isinstance(ansagen[0], str) and "Wäsche" in ansagen[0]
    # Der Timer klingelt auch vor Gästen; die Erinnerung wartet im Gästemodus (Zustellregeln).
    timer = ansagen[1]
    assert isinstance(timer.inhalt, Ansage) and timer.text == "Die Zeit ist um: Nudeln." and timer.vor_gaesten
    assert timer.inhalt.klingeln
    assert ansagen[2].text == "Die Zeit ist um."
    assert e.offene() == []


def test_alte_dateien_ohne_art_sind_erinnerungen(tmp_path):
    datei = tmp_path / "e.json"
    datei.write_text('[{"nummer": 1, "faellig": "2026-10-03T09:30:00+02:00", "text": "Wäsche", "status": "offen"}]', encoding="utf-8")
    (eintrag,) = Erinnerungen(datei, Uhr()).offene()
    assert not eintrag.timer and eintrag.kurztext() == "Wäsche"


@pytest.mark.parametrize(
    ("kwargs", "erwartet"),
    [
        ({"in_minuten": 20}, datetime(2026, 10, 3, 9, 20, tzinfo=TZ)),
        ({"uhrzeit": "10:30"}, datetime(2026, 10, 3, 10, 30, tzinfo=TZ)),
        ({"uhrzeit": "08:00"}, datetime(2026, 10, 4, 8, 0, tzinfo=TZ)),
        ({"uhrzeit": "08:00", "datum": "2026-10-10"}, datetime(2026, 10, 10, 8, 0, tzinfo=TZ)),
    ],
)
def test_zeitpunkt(kwargs, erwartet):
    assert zeitpunkt(JETZT, **kwargs) == erwartet


def test_zeitpunkt_braucht_eine_angabe():
    with pytest.raises(ValueError):
        zeitpunkt(JETZT)


def test_wiederkehrende_erinnerung_werktags(tmp_path):
    # So wollte Alexander es am 6.10.2026: werktags um acht an die Aufgaben für GdP 2 erinnert werden.
    uhr = Uhr(datetime(2026, 10, 6, 14, 30, tzinfo=TZ))  # Dienstag, nach acht
    e = Erinnerungen(tmp_path / "e.json", uhr)
    eintrag = e.anlegen("Aufgaben für Grundlagen der Programmierung 2", datetime(2026, 10, 7, 8, 0, tzinfo=TZ), wochentage=[0, 1, 2, 3, 4])
    assert eintrag.wiederkehrend and eintrag.wiederholung() == "werktags"
    assert e.faellige() == [], "heute ist acht Uhr schon vorbei"

    uhr.jetzt = datetime(2026, 10, 7, 8, 0, 30, tzinfo=TZ)  # Mittwoch
    (faellig,) = e.faellige()
    assert "jetzt fällig" in e.ansage(faellig)
    assert e.faellige() == [], "einmal am Tag"
    neu = Erinnerungen(tmp_path / "e.json", uhr)
    (offen,) = neu.offene()
    assert offen.faellig_um == datetime(2026, 10, 8, 8, 0, tzinfo=TZ), "bleibt offen, nächstes Mal Donnerstag"

    uhr.jetzt = datetime(2026, 10, 10, 8, 0, tzinfo=TZ)  # Samstag
    assert neu.faellige() == []
    uhr.jetzt = datetime(2026, 10, 12, 9, 30, tzinfo=TZ)  # Montag, verspätet nachgeholt
    (faellig,) = neu.faellige()
    assert "war um 08:00 Uhr fällig" in neu.ansage(faellig)
    assert neu.loeschen(offen.nummer) and neu.offene() == []


def test_wiederkehrende_erinnerung_haelt_die_ortszeit_ueber_die_zeitumstellung(tmp_path):
    sommer, winter = timezone(timedelta(hours=2)), timezone(timedelta(hours=1))
    uhr = Uhr(datetime(2026, 10, 24, 12, 0, tzinfo=sommer))
    e = Erinnerungen(tmp_path / "e.json", uhr)
    e.anlegen("Medikament", datetime(2026, 10, 25, 8, 0, tzinfo=sommer), wochentage=list(range(7)))
    uhr.jetzt = datetime(2026, 10, 25, 7, 59, tzinfo=winter)
    assert e.faellige() == []
    uhr.jetzt = datetime(2026, 10, 25, 8, 0, tzinfo=winter)
    assert len(e.faellige()) == 1, "acht Uhr bleibt acht Uhr"
