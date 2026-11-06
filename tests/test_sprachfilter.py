import asyncio

from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

from alfred.sprachfilter import Sprachfilter, sprachcode


def test_sprachcode():
    assert sprachcode(Language.DE_DE) == "de" and sprachcode("es") == "es" and sprachcode(None) == ""


def test_fremde_sprachen_werden_verworfen():
    filt = Sprachfilter(["de", "en"])
    frames = [
        TranscriptionFrame("Wie spät ist es?", "u", "t", language=Language.DE),
        InterimTranscriptionFrame("Ik is", "u", "t", language=Language.NL),
        TranscriptionFrame("Ik is gisteren goed", "u", "t", language=Language.NL),
        TranscriptionFrame("¿Ves tú?", "u", "t", language="es"),
        TranscriptionFrame("What time is it?", "u", "t", language=Language.EN_US),
        TranscriptionFrame("Ohne Sprache", "u", "t"),
    ]
    down, _ = asyncio.run(run_test(filt, frames_to_send=frames))

    texte = [f.text for f in down if isinstance(f, (TranscriptionFrame, InterimTranscriptionFrame))]
    assert texte == ["Wie spät ist es?", "What time is it?", "Ohne Sprache"]
    assert filt.verworfen == 2
