"""Sprachnachrichten aus der App: WAV prüfen und abhören, ohne Mistral."""

import asyncio
import io
import struct
import wave
from types import SimpleNamespace

import pytest

from alfred.sprachnachricht import abhoeren, wav_dauer


def wav(sekunden: float, rate: int = 16000, kanaele: int = 1) -> bytes:
    """Stille als WAV, so wie der Browser sie schickt (16 Bit PCM)."""
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as w:
        w.setnchannels(kanaele)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * kanaele * int(sekunden * rate))
    return puffer.getvalue()


def teil(kennung: bytes, inhalt: bytes) -> bytes:
    return kennung + struct.pack("<I", len(inhalt)) + inhalt + (b"\x00" if len(inhalt) % 2 else b"")


def riff(*teile: bytes) -> bytes:
    koerper = b"WAVE" + b"".join(teile)
    return b"RIFF" + struct.pack("<I", len(koerper)) + koerper


def fmt(format_code=1, kanaele=1, rate=16000, byte_rate=32000) -> bytes:
    return teil(b"fmt ", struct.pack("<HHIIHH", format_code, kanaele, rate, byte_rate, 2 * kanaele, 16))


def test_wav_dauer_aus_kopf_und_daten():
    assert wav_dauer(wav(2.5)) == pytest.approx(2.5)
    assert wav_dauer(wav(1.0, rate=48000, kanaele=2)) == pytest.approx(1.0)


def test_wav_dauer_ueberspringt_fremde_teile_und_liest_auch_stroeme():
    # Ein LIST-Teil mit ungerader Länge vor den Daten, wie ihn manche Aufnahmeprogramme schreiben
    assert wav_dauer(riff(fmt(), teil(b"LIST", b"abcde"), teil(b"data", bytes(32000)))) == pytest.approx(1.0)
    roh = wav(1.0)
    # Wer in einen Datenstrom schreibt, kennt die Länge vorher nicht: Dann zählt, was da ist.
    assert wav_dauer(roh[:40] + struct.pack("<I", 0xFFFFFFFF) + roh[44:]) == pytest.approx(1.0)
    assert wav_dauer(roh[:40] + struct.pack("<I", 0) + roh[44:]) == pytest.approx(1.0)
    assert wav_dauer(roh[: 44 + 16000]) == pytest.approx(0.5), "abgeschnitten: die halbe Sekunde, die da ist"


@pytest.mark.parametrize(
    "daten",
    [
        b"",
        b"ID3\x04" + bytes(60),  # MP3
        riff(),  # ohne Teile
        riff(teil(b"data", bytes(32))),  # Daten vor dem Format
        riff(fmt(format_code=2), teil(b"data", bytes(32))),  # ADPCM
        riff(teil(b"fmt ", bytes(10)), teil(b"data", bytes(32))),  # Formatteil zu kurz
        riff(fmt(byte_rate=0), teil(b"data", bytes(32))),
        riff(fmt(kanaele=0), teil(b"data", bytes(32))),
    ],
)
def test_unbrauchbares_audio_ist_ein_valueerror_mit_einem_satz_fuer_die_app(daten):
    with pytest.raises(ValueError) as fehler:
        wav_dauer(daten)
    assert str(fehler.value).endswith(".")


class Transkription:
    def __init__(self, text):
        self.text, self.aufrufe = text, []

    async def complete_async(self, **kwargs):
        self.aufrufe.append(kwargs)
        return SimpleNamespace(text=self.text, usage=SimpleNamespace(prompt_audio_seconds=1))


def test_abhoeren_schickt_wav_an_das_dateimodell_und_glaettet_die_abschrift():
    transkription = Transkription("  Setz Milch\n auf die   Einkaufsliste. ")
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transkription))
    daten = wav(1.0)
    assert asyncio.run(abhoeren(client, "voxtral-mini-latest", daten)) == "Setz Milch auf die Einkaufsliste."
    aufruf = transkription.aufrufe[0]
    assert aufruf["model"] == "voxtral-mini-latest"
    assert aufruf["file"] == {"file_name": "sprachnachricht.wav", "content": daten, "content_type": "audio/wav"}
    assert "language" not in aufruf, "die Sprache erkennt Mistral selbst, wie beim Sprechen im Raum"


def test_abhoeren_ohne_text_ist_leer():
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=Transkription(None)))
    assert asyncio.run(abhoeren(client, "m", wav(1.0))) == ""
