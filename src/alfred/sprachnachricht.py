"""Sprachnachrichten aus der App: im Browser aufgenommen, hier abgehört und dann beantwortet wie getippter Text.

Der Browser schickt WAV (16 kHz, mono, 16 Bit); den Umbau aus seinem eigenen Format macht er selbst, so
braucht der Server kein ffmpeg und Mistral bekommt immer dasselbe Format. Das Audio wird nirgends
gespeichert: Es geht einmal an Mistrals Transkription, was bleibt, ist die Abschrift im Verlauf des Chats.
Abgehört wird mit dem Modell für fertige Dateien (`modelle.sprachnachricht`), nicht mit dem Echtzeitmodell
des Sprach-Prozesses: Wenn die Aufnahme ankommt, ist sie schon zu Ende.
"""

from __future__ import annotations

import struct

MIN_SEKUNDEN = 0.5
MAX_SEKUNDEN = 10 * 60
# Zehn Minuten WAV mit 16 kHz mono sind 19,2 MB; der Rest ist Luft für Aufnahmen mit höherer Abtastrate.
MAX_BYTES = 25 * 1024 * 1024

# PCM, IEEE-Gleitkomma und WAVE_FORMAT_EXTENSIBLE; komprimiertes WAV (etwa ADPCM) gibt es aus dem Browser nicht.
_FORMATE = (1, 3, 0xFFFE)


def wav_dauer(daten: bytes) -> float:
    """Dauer einer WAV-Aufnahme in Sekunden; ValueError mit einem Satz für die App, wenn es keine brauchbare ist."""
    if len(daten) < 12 or daten[:4] != b"RIFF" or daten[8:12] != b"WAVE":
        raise ValueError("Das ist keine WAV-Aufnahme.")
    pos, byte_rate = 12, 0
    while pos + 8 <= len(daten):
        kennung = daten[pos : pos + 4]
        groesse = int.from_bytes(daten[pos + 4 : pos + 8], "little")
        inhalt = pos + 8
        if kennung == b"fmt ":
            if groesse < 16 or inhalt + 16 > len(daten):
                raise ValueError("Die WAV-Aufnahme hat keinen lesbaren Formatteil.")
            fmt, kanaele, rate, byte_rate, _block, _bits = struct.unpack("<HHIIHH", daten[inhalt : inhalt + 16])
            if fmt not in _FORMATE or not kanaele or not rate or not byte_rate:
                raise ValueError("Dieses WAV-Format kann Alfred nicht abhören.")
        elif kennung == b"data":
            if not byte_rate:
                raise ValueError("Die WAV-Aufnahme hat keinen Formatteil vor den Daten.")
            # Wer in einen Datenstrom schreibt, kennt die Länge vorher nicht und trägt 0 oder 0xFFFFFFFF ein;
            # dann zählt, was da ist. Ebenso bei einer abgeschnittenen Datei.
            vorhanden = len(daten) - inhalt
            laenge = vorhanden if groesse in (0, 0xFFFFFFFF) or groesse > vorhanden else groesse
            return laenge / byte_rate
        pos = inhalt + groesse + (groesse & 1)  # Teile sind auf gerade Längen aufgefüllt
    raise ValueError("Die Aufnahme enthält keine Audiodaten.")


async def abhoeren(client, modell: str, wav: bytes) -> str:
    """Die Abschrift einer WAV-Aufnahme in einer Zeile; leer, wenn nichts zu verstehen war.

    Ohne Sprachvorgabe, wie beim Sprechen im Raum: Mistral erkennt die Sprache selbst.
    """
    antwort = await client.audio.transcriptions.complete_async(
        model=modell,
        file={"file_name": "sprachnachricht.wav", "content": wav, "content_type": "audio/wav"},
    )
    return " ".join((antwort.text or "").split())
