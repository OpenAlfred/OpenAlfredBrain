import asyncio
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from pipecat.frames.frames import (
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import run_test

from alfred.codewort import MARKE
from alfred.pruefen import stimmprofil_befund
from alfred.stimme import (
    SAMPLE_RATE,
    Stimmfilter,
    Stimmprofil,
    Stimmpruefer,
    aehnlichkeit,
    aktives_profil,
    mittelvektor,
    schwellen_empfehlen,
    schwellen_setzen,
    selbstaehnlichkeit,
    sprache_zuschneiden,
)


class FakeErkenner:
    """Der Vektor hängt vom ersten Byte des Audios ab: 1 = Alexander, 2 = jemand anderes."""

    def vektor(self, audio: bytes):
        return [1.0, 0.0] if audio[0] == 1 else [0.0, 1.0]


def test_aehnlichkeit():
    assert aehnlichkeit([1, 0], [1, 0]) == 1.0
    assert aehnlichkeit([1, 0], [0, 1]) == 0.0
    assert aehnlichkeit([], [1, 0]) == 0.0


def test_profil_speichern_und_laden(tmp_path):
    pfad = tmp_path / "stimme.json"
    profil = Stimmprofil(pfad)
    assert not profil.vorhanden
    profil.speichern([0.5, 0.5])
    assert Stimmprofil(pfad).vektor == [0.5, 0.5] and json.loads(pfad.read_text())["vektor"] == [0.5, 0.5]
    assert Stimmprofil(pfad).info == {}


def test_profil_merkt_sich_wie_es_angelernt_wurde(tmp_path):
    pfad = tmp_path / "stimme.json"
    Stimmprofil(pfad).speichern([1.0, 0.0], mikrofon="Raum buero (Jabra)", proben=6, angelernt="2026-10-04T20:40:00+02:00")
    geladen = Stimmprofil(pfad)
    assert geladen.vektor == [1.0, 0.0] and geladen.info["mikrofon"] == "Raum buero (Jabra)" and geladen.info["proben"] == 6

    befund = stimmprofil_befund(geladen)
    assert befund.stufe == "ok" and "04.10." in befund.text and "Jabra" in befund.text
    alt = tmp_path / "alt.json"
    alt.write_text('{"vektor": [1.0, 0.0]}', encoding="utf-8")
    assert stimmprofil_befund(Stimmprofil(alt)).stufe == "warnung", "alte Profile ohne Mikrofon fallen auf"
    assert stimmprofil_befund(Stimmprofil(tmp_path / "fehlt.json")).stufe == "warnung"


def test_mittelvektor_gewichtet_jede_probe_gleich():
    # Die zweite Probe ist zehnmal so lang wie die erste, zählt aber nicht mehr.
    assert mittelvektor([[1.0, 0.0], [0.0, 10.0]]) == pytest.approx([0.5, 0.5])
    assert mittelvektor([[0.0, 0.0], [3.0, 4.0]]) == pytest.approx([0.6, 0.8]), "leere Proben fallen weg"
    with pytest.raises(ValueError):
        mittelvektor([[0.0, 0.0]])


def test_selbstaehnlichkeit_laesst_die_probe_selbst_aussen_vor():
    rede = [[1.0, 0.0]]
    kurze = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]  # die dritte klingt anders
    werte = selbstaehnlichkeit(kurze, rede)
    assert werte[0] == pytest.approx(aehnlichkeit([1.0, 0.0], mittelvektor([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])))
    assert werte[2] == pytest.approx(0.0), "gegen sich selbst gemessen läge sie höher"


@pytest.mark.parametrize(
    ("werte", "erwartet"),
    [
        ([0.71, 0.64, 0.58], {"schwelle_wachwort": 0.48, "schwelle": 0.53}),
        ([0.9, 0.95], {"schwelle_wachwort": 0.6, "schwelle": 0.7}),  # nach oben gedeckelt
        ([0.2, 0.3], {"schwelle_wachwort": 0.25, "schwelle": 0.3}),  # nach unten nicht beliebig
    ],
)
def test_schwellen_empfehlung(werte, erwartet):
    assert schwellen_empfehlen(werte) == erwartet


def test_sprache_zuschneiden():
    stille = np.zeros(SAMPLE_RATE, dtype=np.int16)
    t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
    sprache = (8000 * np.sin(2 * np.pi * 200 * t)).astype(np.int16)
    audio = np.concatenate([stille, sprache, stille]).tobytes()

    geschnitten = np.frombuffer(sprache_zuschneiden(audio), dtype=np.int16)
    assert 2.0 <= len(geschnitten) / SAMPLE_RATE <= 2.25, "Stille ab, 100 ms Rand bleiben"
    kurz = sprache_zuschneiden(audio, max_secs=1.0)
    assert len(kurz) / 2 / SAMPLE_RATE == pytest.approx(1.0)
    assert sprache_zuschneiden(stille.tobytes()) == b""
    assert sprache_zuschneiden(b"") == b""


YAML = (
    "# Kopf\r\n"
    "stimmerkennung:\r\n"
    "  profil: config/stimme.json\r\n"
    "  schwelle: 0.65 # Freigaben\r\n"
    "\r\n"
    "# Preise\r\n"
    "preise:\r\n"
    "  schwelle: 9\r\n"
)


def test_schwellen_setzen_aendert_nur_den_abschnitt():
    neu = schwellen_setzen(YAML, {"schwelle_wachwort": 0.42, "schwelle": 0.5})
    assert neu == (
        "# Kopf\r\n"
        "stimmerkennung:\r\n"
        "  profil: config/stimme.json\r\n"
        "  schwelle: 0.5 # Freigaben\r\n"
        "  schwelle_wachwort: 0.42\r\n"
        "\r\n"
        "# Preise\r\n"
        "preise:\r\n"
        "  schwelle: 9\r\n"
    )
    assert schwellen_setzen(neu, {"schwelle_wachwort": 0.4}).count("schwelle_wachwort") == 1


def test_schwellen_setzen_ohne_abschnitt():
    assert schwellen_setzen("a: 1", {"schwelle": 0.5}) == "a: 1\nstimmerkennung:\n  schwelle: 0.5\n"


def zug(sprecher: int, sekunden: float):
    audio = bytes([sprecher]) + b"\x00" * (int(SAMPLE_RATE * 2 * sekunden) - 1)
    return [
        VADUserStartedSpeakingFrame(),
        InputAudioRawFrame(audio=audio, sample_rate=SAMPLE_RATE, num_channels=1),
        VADUserStoppedSpeakingFrame(),
    ]


def pruefen(tmp_path, *zuege):
    """Ein frischer Prüfer je Lauf: Pipecats run_test verträgt keinen zweiten Lauf mit demselben Processor."""
    profil = Stimmprofil(tmp_path / "s.json")
    profil.speichern([1.0, 0.0])
    pruefer = Stimmpruefer(FakeErkenner(), profil, schwelle=0.5)
    asyncio.run(run_test(pruefer, frames_to_send=[f for z in zuege for f in z]))
    return pruefer


def test_pruefer_erkennt_alexander_und_fremde(tmp_path):
    pruefer = pruefen(tmp_path, zug(1, 1.5))
    assert pruefer.passt is True and pruefer.wert == 1.0

    pruefer = pruefen(tmp_path, zug(2, 1.5))
    assert pruefer.passt is False and pruefer.wert == 0.0

    # Zu kurze Züge werden nicht geprüft; das Ergebnis des letzten geprüften Zugs bleibt.
    pruefer = pruefen(tmp_path, zug(2, 1.5), zug(1, 0.3))
    assert pruefer.passt is False


def test_urteil_je_sprechabschnitt():
    async def lauf():
        profil = Stimmprofil(Path(tempfile.mkdtemp()) / "s.json")
        profil.speichern([1.0, 0.0])
        pruefer = Stimmpruefer(FakeErkenner(), profil, 0.5)
        schleife = asyncio.get_running_loop()

        assert await pruefer.urteil() is None, "noch nichts gesprochen"
        pruefer._urteil = schleife.create_future()
        await pruefer._pruefen(b"\x01" * SAMPLE_RATE * 2, pruefer._urteil)
        assert await pruefer.urteil() == pytest.approx(1.0) and pruefer.passt is True

        pruefer._urteil = schleife.create_future()
        await pruefer._pruefen(b"\x02" * SAMPLE_RATE * 2, pruefer._urteil)
        assert await pruefer.urteil() == pytest.approx(0.0) and pruefer.passt is False

        pruefer._urteil = schleife.create_future()
        assert await pruefer.urteil(frist=0.05) is None, "kommt das Ergebnis nicht rechtzeitig, entscheidet es nicht"

    asyncio.run(lauf())


class FakeSchleuse:
    def __init__(self, nur_eigene: bool, erstes_transkript_offen: bool = False):
        self.nur_eigene_stimme = nur_eigene
        self.erstes_transkript_offen = erstes_transkript_offen
        self.angenommen_zahl = 0

    def angenommen(self):
        self.angenommen_zahl += 1


class FakeUrteile:
    def __init__(self, werte):
        self._werte = list(werte)

    async def urteil(self, frist: float = 1.0):
        return self._werte.pop(0)


def filtern(texte, werte, nur_eigene=True, schleuse=True, gerade_geweckt=False, erwartet_antwort=None, pruefer=True):
    s = FakeSchleuse(nur_eigene, gerade_geweckt) if schleuse else None
    verworfen = []
    filt = Stimmfilter(
        s, FakeUrteile(werte) if pruefer else None, 0.4, ["Alfred"], bei_verworfen=verworfen.append,
        erwartet_antwort=erwartet_antwort,
    )  # fmt: skip
    frames = [TranscriptionFrame(t, "nutzer", "2026-10-04T21:00:00") for t in texte]
    down, _ = asyncio.run(run_test(filt, frames_to_send=frames))
    return [f.text for f in down if isinstance(f, TranscriptionFrame)], verworfen, (s.angenommen_zahl if s else 0)


def test_stimmfilter_nach_der_pause_nur_alexander_oder_wachwort():
    durch, verworfen, angenommen = filtern(
        ["I don't care if the check happens.", "Wie spät ist es?", "Alfred, hörst du?", "Ja."],
        [0.2, 0.7, 0.1, None],
    )
    assert durch == ["Wie spät ist es?", "Alfred, hörst du?", "Ja."]
    assert verworfen == ["I don't care if the check happens."]
    assert angenommen == 3, "jeder angenommene Satz hält das Gespräch offen"


def test_stimmfilter_laesst_in_der_freien_zeit_und_ohne_schleuse_alles_durch():
    assert filtern(["Hallo"], [], nur_eigene=False) == (["Hallo"], [], 0)
    assert filtern(["Hallo"], [], schleuse=False) == (["Hallo"], [], 0)


def test_stimmfilter_laesst_ungepruefte_lange_saetze_nicht_durch():
    # Am 6.10.2026 kam so ein Satz aus einem Video an, ohne Stimmwert.
    durch, verworfen, _ = filtern(
        ["Yesterday that was planned.", "Ja, mach das.", "Alfred, wie spät ist es jetzt?"], [None, None, None]
    )
    assert durch == ["Ja, mach das.", "Alfred, wie spät ist es jetzt?"] and verworfen == ["Yesterday that was planned."]


def test_wartet_alfred_auf_eine_antwort_zaehlt_auch_ein_langer_satz():
    # Ohne Stimmerkennung (seit dem 20.10.2026) ist kein Satz prüfbar. In Bereitschaft verwürfe der
    # Filter sonst jede Antwort auf Alfreds Frage, die länger als drei Wörter ist.
    saetze = ["Ich komme heute etwas später nach Hause.", "Wie wird das Wetter morgen?"]
    durch, verworfen, angenommen = filtern(saetze, [], pruefer=False, erwartet_antwort=lambda: True)
    assert durch == saetze and verworfen == [] and angenommen == 2
    durch, verworfen, _ = filtern(saetze, [], pruefer=False, erwartet_antwort=lambda: False)
    assert durch == [] and verworfen == saetze
    # Eine fremde Stimme bleibt fremd, auch wenn Alfred wartet.
    assert filtern(["Yesterday that was planned."], [0.1], erwartet_antwort=lambda: True)[0] == []


def test_verhoertes_wachwort_wird_zum_wachwort():
    durch, _, _ = filtern(["Eidrith.", "Eidrith."], [], nur_eigene=False, gerade_geweckt=True)
    assert durch == ["Alfred.", "Eidrith."], "nur das erste Transkript nach dem Wachwort"
    assert filtern(["Wie spät?"], [], nur_eigene=False, gerade_geweckt=True)[0] == ["Wie spät?"], "mehr als ein Wort bleibt"
    assert filtern([MARKE], [], nur_eigene=False, gerade_geweckt=True)[0] == [MARKE], "das Codewort bleibt das Codewort"


def test_aktives_profil_nur_mit_eingeschalteter_stimmerkennung(tmp_path):
    from types import SimpleNamespace

    profilpfad = tmp_path / "stimme.json"
    Stimmprofil(profilpfad).speichern([1.0, 0.0], modell="wespeaker-resnet34")
    erkennung = SimpleNamespace(aktiv=False, profil=str(profilpfad), modell="wespeaker-resnet34")
    settings = SimpleNamespace(stimmerkennung=erkennung, pfad=lambda p: tmp_path / "stimme.json")
    assert aktives_profil(settings) is None, "ein angelerntes Profil allein schaltet nichts ein"
    erkennung.aktiv = True
    assert aktives_profil(settings).vektor == [1.0, 0.0]
    erkennung.modell = "vosk-model-spk-0.4"
    assert aktives_profil(settings) is None, "für ein anderes Modell gilt das Profil nicht"


def test_anlernen_traegt_neues_modell_mit_den_schwellen_ein(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import alfred.stimme as stimme

    pfad = tmp_path / "alfred.yaml"
    pfad.write_text("stimmerkennung:\n  modell: vosk-model-spk-0.4 # alt\n  schwelle: 0.46\n  schwelle_wachwort: 0.41\n", encoding="utf-8")
    monkeypatch.setattr(stimme, "konfig_anlegen", lambda: pfad)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("beim Modellwechsel keine Rückfrage"))
    settings = SimpleNamespace(stimmerkennung=SimpleNamespace(modell="vosk-model-spk-0.4", schwelle=0.46, schwelle_wachwort=0.41))

    stimme._uebernehmen(settings, "wespeaker-resnet34", {"schwelle_wachwort": 0.6, "schwelle": 0.7})
    text = pfad.read_text(encoding="utf-8")
    assert "  modell: wespeaker-resnet34 # alt\n" in text, "ein Profil zu einem anderen Modell bliebe ungenutzt"
    assert "  schwelle: 0.7\n" in text and "  schwelle_wachwort: 0.6\n" in text, "die alten Schwellen gehören zum alten Modell"

    # Gleiches Modell: Schwellen wie bisher nur nach einem Ja
    settings.stimmerkennung.modell = "wespeaker-resnet34"
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    stimme._uebernehmen(settings, "wespeaker-resnet34", {"schwelle_wachwort": 0.5, "schwelle": 0.55})
    assert pfad.read_text(encoding="utf-8") == text
    monkeypatch.setattr("builtins.input", lambda *_: "")
    stimme._uebernehmen(settings, "wespeaker-resnet34", {"schwelle_wachwort": 0.5, "schwelle": 0.55})
    assert "  schwelle: 0.55\n" in pfad.read_text(encoding="utf-8")
