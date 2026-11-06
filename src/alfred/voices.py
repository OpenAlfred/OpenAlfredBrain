"""Listet die Stimmen der Mistral-Sprachausgabe auf: eigene zuerst, dann die Standardstimmen.

Alle:           python -m alfred.voices
Eine Sprache:   python -m alfred.voices de

Die erste Spalte ist der Wert für `stimme` in config/alfred.yaml.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from mistralai.client import Mistral
from mistralai.client.models import VoiceResponse

_SEITENGROESSE = 100


async def list_voices(api_key: str, sprache: str = "") -> list[VoiceResponse]:
    """Alle Stimmen, deren Sprachcode mit `sprache` beginnt (z. B. "en" für "en_gb")."""
    client = Mistral(api_key=api_key)
    stimmen: list[VoiceResponse] = []
    offset = 0
    while True:
        seite = await client.audio.voices.list_async(limit=_SEITENGROESSE, offset=offset)
        stimmen.extend(seite.items)
        offset += len(seite.items)
        if not seite.items or offset >= seite.total:
            break
    return [s for s in stimmen if any(code.lower().startswith(sprache) for code in s.languages or [""])]


def _main() -> None:
    load_dotenv()
    sprache = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    stimmen = asyncio.run(list_voices(os.environ["MISTRAL_API_KEY"], sprache))
    for s in sorted(stimmen, key=lambda s: (s.type != "custom", s.slug or s.id)):
        sprachen = ",".join(s.languages or [])
        # Eigene Stimmen haben keinen Slug, nur UUID und Namen.
        beschreibung = s.name if s.type == "custom" else ", ".join(s.tags or [])
        print(f"{s.slug or s.id:38} {sprachen:8} {s.gender or '':7} {beschreibung or ''}")


if __name__ == "__main__":
    _main()
