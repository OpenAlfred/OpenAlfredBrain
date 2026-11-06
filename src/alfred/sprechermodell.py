"""Stimmvektoren mit WeSpeaker (ONNX): genauer und schneller als Vosks Sprechermodell.

Vosks Sprechermodell (`vosk-model-spk-0.4`) trennte auf synthetischen Stimmen eigene und fremde
Sätze nicht sauber (fremde bis 0,69, eigene ab 0,55) und braucht für einen Vektor eine ganze
Spracherkennung, rund 0,4 s. WeSpeaker ResNet34 (auf VoxCeleb trainiert, mit Large-Margin-
Feinschliff) trennte dieselben Sätze vollständig (fremde bis 0,54, eigene ab 0,64), in rund
20 ms je Vektor; Messung in docs/erkenntnisse.md, 9.10.2026.

Das Modell kommt aus dem sherpa-onnx-Release (26 MB), lädt beim ersten Gebrauch nach
~/.cache/alfred und muss die feste Prüfsumme haben. Gerechnet wird mit onnxruntime, das Pipecat
für die Sprechpausen-Erkennung ohnehin mitbringt; die Filterbank steht hier in numpy.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
from loguru import logger

SAMPLE_RATE = 16000
QUELLE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
# Name in stimmerkennung.modell: (Datei im Release, SHA-256)
MODELLE = {
    "wespeaker-resnet34": (
        "wespeaker_en_voxceleb_resnet34_LM.onnx",
        "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012",
    ),
}
CACHE = Path.home() / ".cache" / "alfred"
MIN_RAHMEN = 50  # unter einer halben Sekunde Sprache gibt es keinen Vektor

# Filterbank wie torchaudio.compliance.kaldi.fbank, womit WeSpeaker trainiert ist
_FENSTER = 400  # 25 ms
_SCHRITT = 160  # 10 ms
_FFT = 512
_BAENDER = 80


def modelldatei(name: str, cache: Path = CACHE, quelle: str = QUELLE) -> Path:
    """Der Pfad des Modells; beim ersten Mal lädt es herunter und prüft die Prüfsumme."""
    datei, pruefsumme = MODELLE[name]
    pfad = cache / datei
    if pfad.exists():
        return pfad
    cache.mkdir(parents=True, exist_ok=True)
    logger.info(f"Lade das Sprechermodell {datei} nach {cache} (einmalig, rund 26 MB)")
    fd, teil = tempfile.mkstemp(dir=cache, suffix=".part")
    os.close(fd)
    try:
        urllib.request.urlretrieve(quelle + datei, teil)
        with open(teil, "rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != pruefsumme:
                raise RuntimeError(f"Prüfsumme von {datei} stimmt nicht, der Download ist verworfen.")
        os.replace(teil, pfad)
    finally:
        if os.path.exists(teil):
            os.remove(teil)
    return pfad


@lru_cache(maxsize=1)
def _mel_baender() -> np.ndarray:
    """Dreiecksfilter auf der Kaldi-Mel-Skala, 20 Hz bis 8 kHz, ohne den Nyquist-Wert."""

    def mel(f):
        return 1127.0 * np.log(1.0 + f / 700.0)

    unten, oben = mel(20.0), mel(SAMPLE_RATE / 2)
    abstand = (oben - unten) / (_BAENDER + 1)
    m = mel(SAMPLE_RATE / _FFT * np.arange(_FFT // 2))[None, :]
    links = unten + np.arange(_BAENDER)[:, None] * abstand
    mitte, rechts = links + abstand, links + 2 * abstand
    return np.maximum(0.0, np.minimum((m - links) / (mitte - links), (rechts - m) / (rechts - mitte)))


def filterbank(werte: np.ndarray) -> np.ndarray:
    """80 logarithmierte Mel-Energien je 10 ms, über die Zeit mittelwertbereinigt.

    Wie WeSpeaker beim Training: Abtastwerte im int16-Bereich, je Rahmen Gleichanteil weg,
    Preemphase 0,97, Hamming-Fenster, Leistungsspektrum mit 512 Punkten.
    """
    x = np.asarray(werte, dtype=np.float64)
    if len(x) < _FENSTER:
        return np.zeros((0, _BAENDER), dtype=np.float32)
    anzahl = 1 + (len(x) - _FENSTER) // _SCHRITT
    rahmen = x[np.arange(_FENSTER)[None, :] + _SCHRITT * np.arange(anzahl)[:, None]]
    rahmen = rahmen - rahmen.mean(axis=1, keepdims=True)
    rahmen = rahmen - 0.97 * np.concatenate([rahmen[:, :1], rahmen[:, :-1]], axis=1)
    fenster = 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(_FENSTER) / (_FENSTER - 1))
    leistung = np.abs(np.fft.rfft(rahmen * fenster, n=_FFT)) ** 2
    energie = np.log(np.maximum(leistung[:, : _FFT // 2] @ _mel_baender().T, np.finfo(np.float32).eps))
    return (energie - energie.mean(axis=0)).astype(np.float32)


def sprache_eingrenzen(werte: np.ndarray) -> np.ndarray:
    """Schneidet Stille vor und nach der Sprache ab, gemessen am lautesten Stück.

    Ohne feste Untergrenze: Leise gesprochen bleibt so viel übrig wie laut gesprochen.
    """
    stueck = SAMPLE_RATE // 100
    anzahl = len(werte) // stueck
    if anzahl == 0:
        return werte[:0]
    pegel = np.sqrt(np.mean(np.asarray(werte[: anzahl * stueck], dtype=np.float64).reshape(anzahl, stueck) ** 2, axis=1))
    laut = np.nonzero(pegel >= 0.03 * pegel.max())[0] if pegel.max() > 0 else []
    if len(laut) == 0:
        return werte[:0]
    return werte[max(0, laut[0] - 10) * stueck : min(anzahl, laut[-1] + 11) * stueck]


class OnnxStimmerkenner:
    """Rechnet 16-kHz-Mono-PCM in einen Stimmvektor um, wie der Vosk-Erkenner in stimme.py."""

    def __init__(self, name: str = "wespeaker-resnet34", cache: Path = CACHE):
        import onnxruntime as ort

        optionen = ort.SessionOptions()
        optionen.intra_op_num_threads = 2  # der Sprach-Loop braucht den Rest der Kerne
        self._sitzung = ort.InferenceSession(str(modelldatei(name, cache)), optionen, providers=["CPUExecutionProvider"])
        self._eingang = self._sitzung.get_inputs()[0].name

    def vektor(self, audio: bytes) -> list[float] | None:
        merkmale = filterbank(sprache_eingrenzen(np.frombuffer(audio, dtype=np.int16)))
        if len(merkmale) < MIN_RAHMEN:
            return None
        return self._sitzung.run(None, {self._eingang: merkmale[None]})[0][0].tolist()
