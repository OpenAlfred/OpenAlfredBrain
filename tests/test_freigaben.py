from datetime import datetime, timedelta, timezone

from alfred.freigaben import Freigabedatei

TZ = timezone(timedelta(hours=2))
JETZT = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)


def test_anlegen_entscheiden_abholen(tmp_path):
    f = Freigabedatei(tmp_path / "freigabe.json")
    assert f.entscheidung_abholen(JETZT) is None
    eintrag = f.anlegen("Termin anlegen", JETZT)
    assert f.entscheidung_abholen(JETZT) is None  # noch offen

    assert f.entscheiden("falsch", eintrag["geheimnis"], "ja") is None
    assert f.entscheiden(eintrag["id"], "falsch", "ja") is None
    assert f.entscheiden(eintrag["id"], eintrag["geheimnis"], "vielleicht") is None
    assert f.entscheiden(eintrag["id"], eintrag["geheimnis"], "ja")["status"] == "ja"
    assert f.entscheiden(eintrag["id"], eintrag["geheimnis"], "nein") is None  # schon entschieden

    abgeholt = f.entscheidung_abholen(JETZT)
    assert abgeholt["status"] == "ja" and abgeholt["beschreibung"] == "Termin anlegen"
    assert f.lesen() is None


def test_offene_freigabe_laeuft_ab(tmp_path):
    f = Freigabedatei(tmp_path / "freigabe.json")
    f.anlegen("x", JETZT)
    assert f.entscheidung_abholen(JETZT + timedelta(minutes=11)) is None and f.lesen() is None
