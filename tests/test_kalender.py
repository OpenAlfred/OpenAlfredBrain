from datetime import datetime, timedelta, timezone

from alfred.erinnerungen import Erinnerungen
from alfred.kalender import Termin, Terminerinnerer, termin_aus_google

TZ = timezone(timedelta(hours=2))
JETZT = datetime(2026, 10, 3, 9, 0, tzinfo=TZ)


def test_termin_aus_google_mit_uhrzeit_und_ganztags():
    mit_zeit = termin_aus_google(
        {"id": "a", "summary": "Zahnarzt", "start": {"dateTime": "2026-10-03T10:00:00+02:00"}, "end": {"dateTime": "2026-10-03T11:00:00+02:00"}},
        TZ,
    )
    ganztags = termin_aus_google({"id": "b", "start": {"date": "2026-10-04"}, "end": {"date": "2026-10-05"}}, TZ)

    assert mit_zeit.titel == "Zahnarzt" and mit_zeit.beginn.hour == 10 and not mit_zeit.ganztags
    # Deutscher Wochentag, auch ohne Locale: Python würde sonst "Saturday" liefern.
    assert mit_zeit.ansage() == "Samstag, 03.10., zehn Uhr bis elf Uhr: Zahnarzt"
    assert ganztags.ganztags and ganztags.titel == "(ohne Titel)"
    assert ganztags.ansage() == "Sonntag, 04.10.: (ohne Titel) (ganztägig)"


class FakeKalender:
    def __init__(self, termine):
        self._termine = termine

    def termine(self, von, bis):
        return [t for t in self._termine if von <= t.beginn <= bis]


def test_terminerinnerer_erinnert_jeden_termin_einmal(tmp_path):
    termine = [
        Termin("bald", "Standup", JETZT + timedelta(minutes=5), JETZT + timedelta(minutes=20)),
        Termin("spaeter", "Zahnarzt", JETZT + timedelta(hours=1), JETZT + timedelta(hours=2)),
        Termin("tag", "Urlaub", JETZT + timedelta(hours=1), JETZT + timedelta(days=1), ganztags=True),
        Termin("fern", "Konzert", JETZT + timedelta(hours=5), JETZT + timedelta(hours=7)),
    ]
    erinnerungen = Erinnerungen(tmp_path / "e.json", lambda: JETZT)
    merkdatei = tmp_path / "erinnert.json"
    erinnerer = Terminerinnerer(FakeKalender(termine), erinnerungen, timedelta(minutes=10), merkdatei, lambda: JETZT)

    neue = erinnerer.pruefen()
    assert [t.id for t in neue] == ["bald", "spaeter"]
    faellig = {e.text: e.faellig_um for e in erinnerungen.offene()}
    assert faellig["Termin um 09:05 Uhr: Standup"] == JETZT  # Vorlauf schon vorbei: sofort
    assert faellig["Termin um 10:00 Uhr: Zahnarzt"] == JETZT + timedelta(minutes=50)

    assert erinnerer.pruefen() == []
    neu = Terminerinnerer(FakeKalender(termine), erinnerungen, timedelta(minutes=10), merkdatei, lambda: JETZT)
    assert neu.pruefen() == [], "gemerkte Termine überleben einen Neustart"
