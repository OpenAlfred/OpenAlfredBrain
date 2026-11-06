"""Sprachausgabe mit Zwischenspeicher für kurze Sätze und neuen Versuchen bei Hängern.

"Moment, ich sehe nach.", "Gern.", "Soll ich das anlegen?" braucht Alfred jedes Mal neu von
Mistral. Kurze Sätze werden deshalb beim ersten Mal mitgeschnitten und danach sofort aus dem
Speicher gespielt; der Text ist derselbe, also klingt es auch gleich. Lange Sätze wiederholen
sich selten und bleiben außen vor. Der Speicher liegt je Stimme und Modell unter cache/phrasen/.

Mistrals Sprachausgabe antwortet meist in einer halben Sekunde, gelegentlich aber erst nach
sieben bis fünfzehn Sekunden, zufällig verteilt, auch bei Standardstimmen und kurzen Sätzen
(gemessen am 4.10.2026). Alfred wirkte dann, als würde er ewig nachdenken. Kommt binnen zwei
Sekunden kein Ton, wird die Anfrage abgebrochen und neu gestellt, nach weiteren drei Sekunden
ein drittes Mal; der letzte Versuch wartet. Mit nur einem neuen Versuch hing es im Gespräch
einmal trotzdem sieben Sekunden.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import AsyncGenerator
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.mistral.tts import MistralTTSService
from pipecat.utils.text.base_text_filter import BaseTextFilter

MAX_ZEICHEN = 60
MAX_DATEIEN = 500
ERSTER_TON_FRIST = 2.0  # Sekunden bis zum ersten Ton; der zweite Versuch bekommt das Anderthalbfache


class CachingMistralTTSService(MistralTTSService):
    def __init__(self, *, cache_ordner: Path, erster_ton_frist: float = ERSTER_TON_FRIST, **kwargs):
        super().__init__(**kwargs)
        self._cache_ordner = cache_ordner
        self._cache_ordner.mkdir(parents=True, exist_ok=True)
        # Je Versuch die Frist bis zum ersten Ton; None: der letzte wartet, so lange es dauert.
        self._fristen = (erster_ton_frist, erster_ton_frist * 1.5, None)

    def _datei(self, text: str) -> Path:
        schluessel = f"{self._settings.model}|{self._settings.voice}|{self.sample_rate}|{text.strip()}"
        return self._cache_ordner / (hashlib.sha256(schluessel.encode("utf-8")).hexdigest()[:24] + ".pcm")

    async def _mit_neuen_versuchen(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Mistral fragen; bleibt der erste Ton aus oder kommt ein Fehler, noch einmal."""
        for nummer, frist in enumerate(self._fristen, 1):
            strom = super().run_tts(text, context_id)
            letzter = frist is None
            try:
                erster = await asyncio.wait_for(strom.__anext__(), frist)
            except TimeoutError:
                await strom.aclose()
                logger.warning(f"{self}: kein Ton nach {frist:.0f} s, Versuch {nummer + 1} für {text[:40]!r}")
                continue
            except StopAsyncIteration:
                return
            if isinstance(erster, ErrorFrame) and not letzter:
                await strom.aclose()
                logger.warning(f"{self}: Fehler bei Versuch {nummer} ({erster.error}), neuer Versuch")
                continue
            yield erster
            async for frame in strom:
                yield frame
            return

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        kurz = len(text.strip()) <= MAX_ZEICHEN
        datei = self._datei(text) if kurz else None
        if datei and datei.exists():
            await self.start_ttfb_metrics()
            await self.stop_ttfb_metrics()
            logger.debug(f"{self}: Satz aus dem Speicher: {text!r}")
            pcm = datei.read_bytes()
            stueck = self.sample_rate * 2 // 25  # 40 ms je Frame, wie beim Streaming
            for i in range(0, len(pcm), stueck):
                yield TTSAudioRawFrame(pcm[i : i + stueck], self.sample_rate, 1, context_id=context_id)
            return

        teile: list[bytes] = []
        sauber = True
        async for frame in self._mit_neuen_versuchen(text, context_id):
            if isinstance(frame, TTSAudioRawFrame):
                teile.append(frame.audio)
            elif isinstance(frame, ErrorFrame):
                sauber = False
            yield frame
        if datei and teile and sauber:
            self._aufraeumen()
            datei.write_bytes(b"".join(teile))

    def _aufraeumen(self) -> None:
        """Hält den Speicher klein: die ältesten Dateien gehen zuerst."""
        dateien = sorted(self._cache_ordner.glob("*.pcm"), key=lambda p: p.stat().st_mtime)
        for alt in dateien[: max(0, len(dateien) - MAX_DATEIEN + 1)]:
            alt.unlink(missing_ok=True)


# Emojis, Dingbats, Flaggen, Variantenwähler und Verbinder: alles, was die Stimme nicht sprechen kann.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE0F\U0000200D]+")
# Markdown-Zeichen, die Pipecats MarkdownTextFilter stehen lässt, weil ihnen das Gegenstück fehlt.
_MARKDOWN_REST = re.compile(r"[*_~`#]+")


class SprechtextFilter(BaseTextFilter):
    """Nimmt aus dem Text der Sprachausgabe, was die Stimme nicht sprechen soll.

    Emojis: In WhatsApp-Texten gehören sie hinein ("Alex ist der Daddy 👑🔥", 6.10.2026), vorgelesen
    werden sie zu Lauten oder englischen Wörtern. Alfred nennt sie in der Rückfrage mit Namen.
    Markdown-Reste: Hinter Pipecats MarkdownTextFilter, der nur Paare entfernt. Am 16.10.2026 ging
    "_diskret ist mein zweiter Vorname" so an die Stimme. Im Verlauf bleibt der Text, wie er war.
    """

    async def filter(self, text: str) -> str:
        return re.sub(r"[ \t]{2,}", " ", _MARKDOWN_REST.sub(" ", _EMOJI.sub("", text)))
