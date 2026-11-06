"""Codewort für heikle Aktionen: Erkennen, Verdecken, Tauglichkeit, Festlegen."""

import asyncio

import pytest
from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

from alfred import codewort as cw
from alfred.codewort import MARKE, Codewort, Codewortschutz, untauglich
from alfred.pruefen import pruefe_codewort
from alfred.sprachfilter import Sprachfilter
from alfred.werkzeuge import codewort_genannt


@pytest.mark.parametrize(
    "text, erwartet",
    [
        ("Zitronenfalter", MARKE),
        ("Zitronenfalter.", f"{MARKE}."),
        ("Ja, Zitronenfalter!", f"Ja, {MARKE}!"),
        # Die Spracherkennung trennt oder schreibt anders; das Wort zählt trotzdem.
        ("Das Codewort ist Zitronen-Falter.", f"Das Codewort ist {MARKE}."),
        ("zitronen falter", MARKE),
        ("ZITRONENFALTER und noch mal Zitronenfalter", f"{MARKE} und noch mal {MARKE}"),
    ],
)
def test_codewort_wird_verdeckt(text, erwartet):
    assert Codewort("Zitronenfalter").maskieren(text) == erwartet


def test_codewort_zaehlt_nur_in_ganzen_woertern():
    adler = Codewort("Adler")
    assert adler.maskieren("Der Seeadler fliegt.") == "Der Seeadler fliegt."
    assert adler.maskieren("Adlerhorst") == "Adlerhorst"
    assert adler.maskieren("Ad ler") == MARKE


def test_umlaute_und_zwei_woerter():
    assert Codewort("Grüner Bär").maskieren("Grüner Baer, bitte.") == f"{MARKE}, bitte."
    assert Codewort("roter Elefant").maskieren("Roter Elefant") == MARKE


def test_eine_marke_im_text_zaehlt_nicht_als_codewort():
    # Nur der Codewortschutz darf die Marke setzen; ein Transkript, das sie schon enthält, wird entschärft.
    assert MARKE not in Codewort("Zitronenfalter").maskieren(f"Ja, {MARKE}")


@pytest.mark.parametrize(
    "wort, grund",
    [
        ("Ja bitte", "„ja“"),
        ("Okay", "mindestens 5 Buchstaben"),
        ("Hallo Alfred", "„alfred“"),
        ("Codewort", "„codewort“"),
        ("Adler 7", "Ziffern"),
        ("", "mindestens 5 Buchstaben"),
    ],
)
def test_untaugliche_codewoerter(wort, grund):
    assert grund in untauglich(wort)


def test_taugliche_codewoerter():
    assert untauglich("Zitronenfalter") is None
    assert untauglich("roter Elefant") is None
    assert "„computer“" in untauglich("Hallo Computer", wachwoerter=["computer"])


def test_codewort_aus_der_umgebung(monkeypatch):
    monkeypatch.delenv(cw.UMGEBUNG, raising=False)
    assert Codewort.aus_umgebung() is None
    monkeypatch.setenv(cw.UMGEBUNG, "  ")
    assert Codewort.aus_umgebung() is None
    # Ein Wort, das jedes „Ja, bitte“ zum Codewort machte, gilt nicht.
    monkeypatch.setenv(cw.UMGEBUNG, "ja bitte")
    assert Codewort.aus_umgebung() is None
    monkeypatch.setenv(cw.UMGEBUNG, "Zitronenfalter")
    assert Codewort.aus_umgebung().maskieren("Zitronenfalter") == MARKE


def test_codewort_genannt():
    assert codewort_genannt(MARKE) and codewort_genannt(f"Ja, {MARKE}.")
    assert not codewort_genannt("Ja, schick ab.")
    assert not codewort_genannt("Codewort"), "das Wort „Codewort“ ist nicht das Codewort"
    assert not codewort_genannt(f"{MARKE}, aber schreib noch dazu, dass ich später komme.")


def test_schutz_verdeckt_transkripte_vor_allen_folgenden_bausteinen():
    schutz = Codewortschutz(Codewort("Zitronenfalter"))
    frames = [
        TranscriptionFrame("Zitronenfalter", "nutzer", "2026-10-20T08:00:00", language=Language.NL),
        InterimTranscriptionFrame("Das ist Zitronenfalter", "nutzer", "2026-10-20T08:00:01"),
        TranscriptionFrame("Wie spät ist es?", "nutzer", "2026-10-20T08:00:02", language=Language.DE),
    ]
    down, _ = asyncio.run(run_test(schutz, frames_to_send=frames))
    texte = [(f.text, f.language) for f in down if isinstance(f, (TranscriptionFrame, InterimTranscriptionFrame))]
    # Ohne Sprache: Der Sprachfilter verwirft das einzelne Wort sonst als Niederländisch.
    assert texte == [(MARKE, None), (f"Das ist {MARKE}", None), ("Wie spät ist es?", Language.DE)]


def test_ein_codewort_in_fremder_sprache_kommt_durch_den_sprachfilter():
    schutz = Codewortschutz(Codewort("Zitronenfalter"))
    frames = [TranscriptionFrame("Zitronenfalter", "nutzer", "2026-10-20T08:00:00", language=Language.NL)]
    down, _ = asyncio.run(run_test(schutz, frames_to_send=frames))
    durch, _ = asyncio.run(run_test(Sprachfilter(["de", "en"]), frames_to_send=[f for f in down if isinstance(f, TranscriptionFrame)]))
    assert [f.text for f in durch if isinstance(f, TranscriptionFrame)] == [MARKE]


def test_pruefung_meldet_ob_ein_codewort_gesetzt_ist(monkeypatch):
    from alfred.config import load_settings

    settings = load_settings()
    monkeypatch.delenv(cw.UMGEBUNG, raising=False)
    befund = pruefe_codewort(settings)
    assert befund.stufe == "warnung" and "python -m alfred.codewort" in befund.text
    monkeypatch.setenv(cw.UMGEBUNG, "ja bitte")
    assert pruefe_codewort(settings).stufe == "warnung"
    monkeypatch.setenv(cw.UMGEBUNG, "Zitronenfalter")
    befund = pruefe_codewort(settings)
    assert befund.stufe == "ok" and "Zitronenfalter" not in befund.text


def test_festlegen_schreibt_in_env_und_entfernen_leert(tmp_path, monkeypatch):
    import alfred.config as config

    monkeypatch.setattr(config, "PROJEKT_DIR", tmp_path)
    (tmp_path / ".env").write_text("MISTRAL_API_KEY=abc\n", encoding="utf-8")
    eingaben = iter(["Zitronenfalter", "Zitronenfalter"])
    monkeypatch.setattr(cw.getpass, "getpass", lambda aufforderung: next(eingaben))
    monkeypatch.setattr(cw.sys, "argv", ["alfred.codewort"])
    cw.main()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "MISTRAL_API_KEY=abc\nALFRED_CODEWORT=Zitronenfalter\n"

    monkeypatch.setattr(cw.sys, "argv", ["alfred.codewort", "--aus"])
    cw.main()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "MISTRAL_API_KEY=abc\nALFRED_CODEWORT=\n"


def test_festlegen_lehnt_ab_ohne_etwas_zu_aendern(tmp_path, monkeypatch):
    import alfred.config as config

    monkeypatch.setattr(config, "PROJEKT_DIR", tmp_path)
    monkeypatch.setattr(cw.sys, "argv", ["alfred.codewort"])
    for eingaben, meldung in ((["Ja bitte"], "„ja“"), (["Zitronenfalter", "Zitronenfalten"], "unterscheiden sich")):
        folge = iter(eingaben)
        monkeypatch.setattr(cw.getpass, "getpass", lambda aufforderung, folge=folge: next(folge))
        with pytest.raises(SystemExit, match=meldung):
            cw.main()
    assert not (tmp_path / ".env").exists()
