import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from alfred.routinen import NACHHOLEN, Routine, Routinen, parse_uhrzeit, parse_wochentage, wochentage_gesprochen

TZ = timezone(timedelta(hours=2))
SAMSTAG_0700 = datetime(2026, 10, 3, 7, 0, tzinfo=TZ)


class Uhr:
    def __init__(self, jetzt):
        self.jetzt = jetzt

    def __call__(self):
        return self.jetzt


@pytest.mark.parametrize(
    "text, erwartet",
    [
        ("", list(range(7))),
        ("täglich", list(range(7))),
        ("werktags", [0, 1, 2, 3, 4]),
        ("Wochenende", [5, 6]),
        ("Mo, Mi, Fr", [0, 2, 4]),
        ("montags und freitags", [0, 4]),
        ("Dienstag", [1]),
    ],
)
def test_wochentage_verstehen(text, erwartet):
    assert parse_wochentage(text) == erwartet


def test_unverstaendliche_angaben():
    with pytest.raises(ValueError):
        parse_wochentage("irgendwann")
    with pytest.raises(ValueError):
        parse_uhrzeit("halb acht")


def test_zeitplan_gesprochen():
    assert wochentage_gesprochen(list(range(7))) == "täglich"
    assert wochentage_gesprochen([0, 1, 2, 3, 4]) == "werktags"
    assert wochentage_gesprochen([5, 6]) == "am Wochenende"
    assert wochentage_gesprochen([0, 4]) == "am Montag, Freitag"
    assert Routine("Briefing", "Wetter", "07:30").zeitplan() == "täglich um sieben Uhr dreißig"
    assert Routine("Check", "Termine").zeitplan() == "nur auf Zuruf"


def test_anlegen_speichern_und_wiederfinden(tmp_path):
    pfad = tmp_path / "routinen.yaml"
    routinen = Routinen(pfad, Uhr(SAMSTAG_0700))
    routine = routinen.anlegen("Morgenbriefing", "Nenne das Wetter und die Termine.", "07:30", "werktags")

    assert routine.uhrzeit == "07:30" and routine.wochentage == [0, 1, 2, 3, 4]
    assert "Morgenbriefing, werktags um sieben Uhr dreißig: Nenne das Wetter" in routine.ansage()

    neu = Routinen(pfad)
    assert [r.name for r in neu.alle()] == ["Morgenbriefing"]
    assert neu.finden("morgenbriefing").auftrag == routine.auftrag
    assert neu.finden("Briefing") is not None  # Teil des Namens reicht
    assert neu.finden("Abendbriefing") is None

    # Gleicher Name ersetzt die alte Routine statt sie zu verdoppeln.
    neu.anlegen("Morgenbriefing", "Nur das Wetter.", "08:00")
    assert len(neu.alle()) == 1 and neu.alle()[0].auftrag == "Nur das Wetter."

    assert neu.loeschen("Morgenbriefing").name == "Morgenbriefing"
    assert neu.alle() == [] and neu.loeschen("Morgenbriefing") is None


def test_faellig_zur_zeit_und_nur_einmal_am_tag(tmp_path):
    uhr = Uhr(SAMSTAG_0700)
    routinen = Routinen(tmp_path / "r.yaml", uhr)
    routinen.anlegen("Briefing", "Wetter", "07:30")

    assert routinen.faellige() == []
    uhr.jetzt = SAMSTAG_0700.replace(hour=7, minute=30)
    assert [r.name for r in routinen.faellige()] == ["Briefing"]
    uhr.jetzt = SAMSTAG_0700.replace(hour=7, minute=31)
    assert routinen.faellige() == []  # heute schon gelaufen

    # Der Lauf ist gespeichert und zählt auch nach einem Neustart.
    assert Routinen(tmp_path / "r.yaml", uhr).faellige() == []
    uhr.jetzt = SAMSTAG_0700 + timedelta(days=1, minutes=30)
    assert [r.name for r in Routinen(tmp_path / "r.yaml", uhr).faellige()] == ["Briefing"]


def test_nachholen_nur_innerhalb_von_zwei_stunden(tmp_path):
    uhr = Uhr(SAMSTAG_0700)
    routinen = Routinen(tmp_path / "r.yaml", uhr)
    routinen.anlegen("Briefing", "Wetter", "07:30")

    uhr.jetzt = SAMSTAG_0700.replace(hour=9, minute=29)
    assert [r.name for r in routinen.faellige()] == ["Briefing"]

    routinen.anlegen("Spät", "Termine", "07:30")
    # Beim Anlegen nach der Uhrzeit gilt der heutige Lauf als erledigt...
    assert routinen.faellige() == []
    # ... und außerhalb des Nachholfensters wartet eine Routine auf den nächsten Tag.
    routinen.alle()[-1].zuletzt = None
    uhr.jetzt = SAMSTAG_0700.replace(hour=9, minute=31)
    assert routinen.faellige() == []


def test_wochentage_und_zuruf(tmp_path):
    uhr = Uhr(SAMSTAG_0700)
    routinen = Routinen(tmp_path / "r.yaml", uhr)
    routinen.anlegen("Werktag", "Termine", "07:30", "werktags")
    routinen.anlegen("Zuruf", "Wetter")

    uhr.jetzt = SAMSTAG_0700.replace(hour=8)
    assert routinen.faellige() == []  # Samstag ist kein Werktag, Zuruf hat keine Zeit
    uhr.jetzt = SAMSTAG_0700 + timedelta(days=2, hours=1)  # Montag 8:00
    assert [r.name for r in routinen.faellige()] == ["Werktag"]


def test_warten_legt_den_auftrag_als_ereignis_ab(tmp_path):
    uhr = Uhr(SAMSTAG_0700)
    routinen = Routinen(tmp_path / "r.yaml", uhr)
    routinen.anlegen("Briefing", "Nenne das Wetter.", "07:30")
    uhr.jetzt = SAMSTAG_0700.replace(hour=7, minute=30)

    async def lauf():
        ereignisse: asyncio.Queue = asyncio.Queue()
        aufgabe = asyncio.create_task(routinen.warten(ereignisse, "Alexander", intervall=0.01))
        text = await asyncio.wait_for(ereignisse.get(), 2)
        aufgabe.cancel()
        return text

    ereignis = asyncio.run(lauf())
    # Ungefragt: Die Zustellregeln gelten, und wie beim Nachholen verfällt die Ansage zwei Stunden nach der Zeit.
    assert ereignis.klasse == "ungefragt" and ereignis.quelle == "Routine 'Briefing'"
    assert ereignis.gueltig_bis == uhr.jetzt + NACHHOLEN
    text = ereignis.text
    assert text.startswith("Hinweis vom System, nicht vom Gegenüber: Die Routine 'Briefing' ist jetzt fällig.")
    assert "Nenne das Wetter." in text and "Alexander" in text
