import zipfile
from datetime import datetime

from alfred import sichern

# Ortszeit des Rechners, wie im Dateinamen; der Test lief auf dem Mini-PC mit UTC sonst fehl.
JETZT = datetime(2026, 10, 4, 3, 0).astimezone()


def projekt(tmp_path):
    p = tmp_path / "projekt"
    (p / "gedaechtnis" / "wissen").mkdir(parents=True)
    (p / "gedaechtnis" / "selbsttest").mkdir()
    (p / "config" / "stimmen").mkdir(parents=True)
    (p / "gedaechtnis" / "kontext.md").write_text("# Kontext", encoding="utf-8")
    (p / "gedaechtnis" / "wissen" / "privat.md").write_text("- Notiz", encoding="utf-8")
    (p / "gedaechtnis" / "selbsttest" / "kontext.md").write_text("Test", encoding="utf-8")
    (p / "config" / "alfred.yaml").write_text("stimme: x", encoding="utf-8")
    (p / "config" / "google_token.json").write_text("{}", encoding="utf-8")
    (p / "config" / "stimmen" / "jarvis.wav").write_bytes(b"RIFF")
    (p / ".env").write_text("MISTRAL_API_KEY=geheim", encoding="utf-8")
    return p


def test_sichern_packt_gedaechtnis_und_zugaenge_ohne_testordner(tmp_path):
    ziel = tmp_path / "sicherung"
    archiv = sichern.sichern(ziel, projekt=projekt(tmp_path), jetzt=JETZT)

    assert archiv == ziel / "alfred-2026-10-04-0300.zip"
    with zipfile.ZipFile(archiv) as zip_:
        namen = set(zip_.namelist())
    assert namen == {
        "gedaechtnis/kontext.md",
        "gedaechtnis/wissen/privat.md",
        "config/alfred.yaml",
        "config/google_token.json",
        "config/stimmen/jarvis.wav",
    }
    assert ".env" not in namen and not list(ziel.glob("*.teil"))
    assert sichern.letzte_sicherung(ziel) == JETZT


def test_alte_sicherungen_werden_geloescht(tmp_path):
    ziel = tmp_path / "sicherung"
    ziel.mkdir()
    (ziel / "alfred-2026-09-01-0300.zip").write_bytes(b"alt")
    (ziel / "alfred-2026-10-01.tgz").write_bytes(b"linux")  # Format des Bash-Skripts, bleibt stehen
    (ziel / "alfred-2026-10-03-0300.zip").write_bytes(b"neu")
    (ziel / "fremd.zip").write_bytes(b"x")

    sichern.sichern(ziel, behalten_tage=14, projekt=projekt(tmp_path), jetzt=JETZT)

    namen = sorted(p.name for p in ziel.iterdir())
    assert namen == ["alfred-2026-10-01.tgz", "alfred-2026-10-03-0300.zip", "alfred-2026-10-04-0300.zip", "fremd.zip"]


def test_letzte_sicherung_ohne_ordner(tmp_path):
    assert sichern.letzte_sicherung(tmp_path / "nix") is None
    assert sichern.letzte_sicherung(tmp_path) is None


def test_einspielen_fuellt_ein_frisches_projekt_und_laesst_vorhandenes_stehen(tmp_path):
    archiv = sichern.sichern(tmp_path / "sicherung", projekt=projekt(tmp_path), jetzt=JETZT)
    neu = tmp_path / "miniserver"
    (neu / "config").mkdir(parents=True)
    (neu / "config" / "alfred.yaml").write_text("stimme: server", encoding="utf-8")

    dateien = sichern.einspielen(archiv, projekt=neu)

    assert sorted(dateien) == ["config/google_token.json", "config/stimmen/jarvis.wav", "gedaechtnis/kontext.md", "gedaechtnis/wissen/privat.md"]
    assert (neu / "gedaechtnis" / "kontext.md").read_text(encoding="utf-8") == "# Kontext"
    assert (neu / "config" / "alfred.yaml").read_text(encoding="utf-8") == "stimme: server", "vorhandene Datei bleibt"
    assert sichern.einspielen(archiv, projekt=neu) == []
    assert "config/alfred.yaml" in sichern.einspielen(archiv, projekt=neu, ueberschreiben=True)
    assert (neu / "config" / "alfred.yaml").read_text(encoding="utf-8") == "stimme: x"


def test_einspielen_weist_pfade_ausserhalb_ab(tmp_path):
    import pytest

    boese = tmp_path / "boese.zip"
    with zipfile.ZipFile(boese, "w") as zip_:
        zip_.writestr("../../etc/passwd", "x")
    with pytest.raises(ValueError):
        sichern.einspielen(boese, projekt=tmp_path / "p")
