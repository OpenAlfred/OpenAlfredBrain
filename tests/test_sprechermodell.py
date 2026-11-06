import asyncio
import hashlib
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from alfred import sprechermodell
from alfred.pruefen import stimmprofil_befund
from alfred.sprechermodell import MODELLE, filterbank, modelldatei, sprache_eingrenzen
from alfred.stimme import VOSK_SPRECHERMODELL, Stimmprofil, Stimmpruefer, aehnlichkeit, stimmerkenner

SR = 16000
FIXTURES = Path(__file__).parent / "fixtures"
MODELL_DA = (sprechermodell.CACHE / MODELLE["wespeaker-resnet34"][0]).exists()


def _lies(name: str) -> bytes:
    with wave.open(str(FIXTURES / name)) as wf:
        return wf.readframes(wf.getnframes())


def _gestreckt(audio: bytes, faktor: float) -> bytes:
    """Tonhöhe und Formanten verschoben, wie eine andere Stimme."""
    x = np.frombuffer(audio, dtype=np.int16).astype(np.float64)
    neu = np.linspace(0, len(x) - 1, int(len(x) * faktor))
    return np.interp(neu, np.arange(len(x)), x).astype(np.int16).tobytes()


def test_filterbank_form_und_mittelwert():
    merkmale = filterbank(np.random.default_rng(0).normal(0, 1000, SR))
    assert merkmale.shape == (1 + (SR - 400) // 160, 80)
    assert np.abs(merkmale.mean(axis=0)).max() < 1e-4, "über die Zeit mittelwertbereinigt"
    assert filterbank(np.zeros(399)).shape == (0, 80), "kürzer als ein Fenster"


def test_filterbank_ton_landet_im_band_um_1_khz():
    t = np.arange(SR) / SR
    ruhe = np.random.default_rng(0).normal(0, 10, SR)
    merkmale = filterbank(np.concatenate([ruhe, 8000 * np.sin(2 * np.pi * 1000 * t) + ruhe]))
    # Kaldi-Mel-Skala von 20 Hz bis 8 kHz mit 80 Bändern: Band 27 hat seine Mitte bei 1 kHz.
    assert merkmale[150:].mean(axis=0).argmax() == 27


def test_sprache_eingrenzen_misst_am_lautesten_stueck():
    stille = np.zeros(SR)
    t = np.arange(SR) / SR
    for lautstaerke in (100, 10000):  # leise wie laut bleibt gleich viel übrig
        audio = np.concatenate([stille, lautstaerke * np.sin(2 * np.pi * 200 * t), stille]).astype(np.int16)
        assert 1.0 <= len(sprache_eingrenzen(audio)) / SR <= 1.25, lautstaerke
    assert len(sprache_eingrenzen(np.zeros(SR, dtype=np.int16))) == 0
    assert len(sprache_eingrenzen(np.zeros(5, dtype=np.int16))) == 0


def test_modelldatei_laedt_einmal_und_prueft_die_pruefsumme(tmp_path, monkeypatch):
    release = tmp_path / "release"
    release.mkdir()
    (release / "test.onnx").write_bytes(b"modell")
    monkeypatch.setitem(MODELLE, "test", ("test.onnx", hashlib.sha256(b"modell").hexdigest()))
    monkeypatch.setitem(MODELLE, "falsch", ("test.onnx", "0" * 64))
    cache, quelle = tmp_path / "cache", release.as_uri() + "/"

    with pytest.raises(RuntimeError, match="Prüfsumme"):
        modelldatei("falsch", cache, quelle)
    assert list(cache.iterdir()) == [], "ein verworfener Download hinterlässt nichts"

    pfad = modelldatei("test", cache, quelle)
    assert pfad.read_bytes() == b"modell"
    (release / "test.onnx").unlink()
    assert modelldatei("test", cache, quelle) == pfad, "liegt es schon da, lädt nichts mehr"


def test_stimmerkenner_nach_modellname(monkeypatch):
    import alfred.stimme as stimme

    monkeypatch.setattr(stimme, "VoskStimmerkenner", lambda modell, sprachmodell: ("vosk", modell, sprachmodell))
    monkeypatch.setattr(sprechermodell, "OnnxStimmerkenner", lambda modell: ("onnx", modell))
    assert stimmerkenner("vosk-model-spk-0.4", "vosk-model-de-0.21") == ("vosk", "vosk-model-spk-0.4", "vosk-model-de-0.21")
    assert stimmerkenner("wespeaker-resnet34") == ("onnx", "wespeaker-resnet34")
    with pytest.raises(ValueError, match="Unbekanntes Sprechermodell"):
        stimmerkenner("ecapa")


def test_profil_gilt_nur_fuer_sein_modell(tmp_path):
    pfad = tmp_path / "stimme.json"
    Stimmprofil(pfad).speichern(
        [1.0, 0.0], angelernt="2026-10-09T16:00:00+02:00", mikrofon="Raum buero (Jabra)", modell="wespeaker-resnet34", proben=7
    )
    passend = Stimmprofil(pfad, "wespeaker-resnet34")
    assert passend.vorhanden and "Modell wespeaker-resnet34" in stimmprofil_befund(passend).text

    anders = Stimmprofil(pfad, VOSK_SPRECHERMODELL)
    assert not anders.vorhanden and anders.anderes_modell == "wespeaker-resnet34"
    befund = stimmprofil_befund(anders)
    assert befund.stufe == "warnung" and "--anlernen" in befund.text

    alt = tmp_path / "alt.json"
    alt.write_text('{"vektor": [1.0, 0.0]}', encoding="utf-8")
    assert Stimmprofil(alt, VOSK_SPRECHERMODELL).vorhanden, "Profile ohne Modell stammen von Vosk"
    assert not Stimmprofil(alt, "wespeaker-resnet34").vorhanden
    assert Stimmprofil(alt).vorhanden, "ohne Angabe des Modells wird nichts geprüft"


@pytest.mark.skipif(not MODELL_DA, reason="Sprechermodell nicht im Cache; lädt beim ersten Anlernen")
def test_wespeaker_trennt_stimmen_und_ueberhoert_die_lautstaerke():
    erkenner = sprechermodell.OnnxStimmerkenner()
    frage, mitte = _lies("wach_frage.wav"), _lies("wach_mitte.wav")
    a, b = erkenner.vektor(frage), erkenner.vektor(mitte)
    assert len(a) == 256
    assert aehnlichkeit(a, b) > 0.6, "dieselbe Stimme, anderer Satz"
    assert aehnlichkeit(erkenner.vektor(_gestreckt(frage, 1.25)), b) < 0.5, "andere Stimmlage und Formanten"
    leise = (np.frombuffer(frage, dtype=np.int16) * 0.1).astype(np.int16).tobytes()
    assert aehnlichkeit(a, erkenner.vektor(leise)) > 0.95, "20 dB leiser ist dieselbe Stimme"
    assert erkenner.vektor(b"\x00\x00" * 4000) is None, "zu wenig Sprache"


@pytest.mark.skipif(not MODELL_DA, reason="Sprechermodell nicht im Cache; lädt beim ersten Anlernen")
def test_stimmpruefer_mit_wespeaker_urteilt_rechtzeitig(tmp_path):
    erkenner = sprechermodell.OnnxStimmerkenner()
    profil = Stimmprofil(tmp_path / "s.json", "wespeaker-resnet34")
    profil.speichern(erkenner.vektor(_lies("wach_mitte.wav")), modell="wespeaker-resnet34")

    async def urteil(audio: bytes):
        pruefer = Stimmpruefer(erkenner, profil, schwelle=0.5)
        pruefer._urteil = asyncio.get_running_loop().create_future()
        start = time.perf_counter()
        await pruefer._pruefen(audio, pruefer._urteil)
        return await pruefer.urteil(), time.perf_counter() - start

    wert, dauer = asyncio.run(urteil(_lies("wach_frage.wav")))
    assert wert > 0.5 and dauer < 1.0, "der Stimmfilter wartet höchstens eine Sekunde auf das Urteil"
    assert asyncio.run(urteil(_gestreckt(_lies("wach_frage.wav"), 1.25)))[0] < 0.5
