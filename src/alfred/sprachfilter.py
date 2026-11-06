"""Verwirft Transkripte in fremden Sprachen.

Mistrals Echtzeit-Transkription erkennt die Sprache selbst und lässt sich nicht festlegen. Bei
undeutlichen Stellen kippte sie im echten Gespräch ins Spanische und Niederländische, und das
Modell nahm die Sätze ernst. Dieser Processor sitzt hinter der Transkription: Trägt ein
Transkript eine Sprache, die nicht erlaubt ist, kommt es nicht an; Alexander wiederholt den
Satz dann einfach. Ohne erkannte Sprache geht alles durch.
"""

from __future__ import annotations

from collections.abc import Sequence

from loguru import logger
from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def sprachcode(language) -> str:
    """"de-DE", Language.DE_DE oder "de" ergeben alle "de"."""
    return str(language or "").split("-")[0].split("_")[0].casefold()


class Sprachfilter(FrameProcessor):
    def __init__(self, erlaubt: Sequence[str], **kwargs):
        super().__init__(**kwargs)
        self._erlaubt = {sprachcode(s) for s in erlaubt}
        self.verworfen = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            code = sprachcode(frame.language)
            if code and code not in self._erlaubt:
                if isinstance(frame, TranscriptionFrame):
                    self.verworfen += 1
                    logger.info(f"Transkript in '{code}' verworfen: {frame.text!r}")
                return
        await self.push_frame(frame, direction)
