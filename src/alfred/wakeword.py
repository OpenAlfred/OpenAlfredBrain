"""Lokales Wachwort: Solange es nicht gefallen ist, verlässt kein Audio den Rechner."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from loguru import logger
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    UserSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup


@dataclass
class WakeWordHit:
    """Ein erkanntes Wachwort.

    Parameters:
        phrase: Die erkannte Phrase.
        start_secs: Beginn der Phrase, gemessen ab dem ersten Audio seit dem letzten reset().
    """

    phrase: str
    start_secs: float


class WakeWordDetector(Protocol):
    """Schnittstelle, hinter der sich der Erkenner austauschen lässt."""

    def process(self, audio: bytes) -> WakeWordHit | None:
        """Nimmt 16-kHz-Mono-PCM entgegen und meldet ein Wachwort, sobald es erkannt ist."""
        ...

    def reset(self) -> None:
        """Verwirft den bisherigen Audiostrom; Zeitangaben beginnen wieder bei null."""
        ...


class VoskWakeWordDetector:
    """Sucht das Wachwort im laufenden Transkript eines lokalen Vosk-Erkenners.

    Vosk läuft mit vollem Vokabular. Die naheliegende Abkürzung, den Erkenner auf das
    Wachwort als einziges Wort einzuschränken, löst bei beliebigen Sätzen aus. Der Preis
    für die Zuverlässigkeit: Das Wort erscheint erst rund 1,5 s nach dem Aussprechen.
    """

    SAMPLE_RATE = 16000

    def __init__(self, phrases: Sequence[str], model_name: str = "vosk-model-small-de-0.15"):
        # Erst hier importieren: vosk gehört zum Extra "raum" und fehlt im Browser-Modus.
        from vosk import KaldiRecognizer, Model, SetLogLevel

        # Nur Fehler: Bei -1 zeigt Kaldi noch Warnungen, und mit SetWords schreibt es zu fast jedem
        # Satzende "Discarding word-ids ...", am Mini-PC 2,6 Zeilen je Sekunde im Journal (16.10.2026).
        SetLogLevel(-2)
        # Lädt das Modell beim ersten Mal nach ~/.cache/vosk herunter (rund 45 MB).
        self._model = Model(model_name=model_name)
        self._recognizer_cls = KaldiRecognizer
        self._phrases = [phrase.casefold().split() for phrase in phrases]

        unbekannt = sorted(
            {w for phrase in self._phrases for w in phrase if self._model.vosk_model_find_word(w) < 0}
        )
        if not self._phrases or not all(self._phrases) or unbekannt:
            raise ValueError(
                f"Wachwort nicht nutzbar: {unbekannt or 'leere Phrase'}. Jedes Wort muss im "
                f"Vokabular von {model_name} stehen."
            )
        self.reset()

    def reset(self) -> None:
        # Ein neuer Erkenner statt Reset(): Nur so beginnen die Wortzeiten sicher bei null.
        self._recognizer = self._recognizer_cls(self._model, self.SAMPLE_RATE)
        self._recognizer.SetWords(True)
        self._recognizer.SetPartialWords(True)

    def process(self, audio: bytes) -> WakeWordHit | None:
        if self._recognizer.AcceptWaveform(audio):
            words = json.loads(self._recognizer.Result()).get("result", [])
        else:
            words = json.loads(self._recognizer.PartialResult()).get("partial_result", [])
        return self._find(words)

    def _find(self, words: list[dict]) -> WakeWordHit | None:
        texts = [w["word"] for w in words]
        for phrase in self._phrases:
            for i in range(len(texts) - len(phrase) + 1):
                if texts[i : i + len(phrase)] == phrase:
                    return WakeWordHit(" ".join(phrase), words[i]["start"])
        return None


class WakeWordGate(FrameProcessor):
    """Lässt Mikrofon-Audio erst durch, nachdem das Wachwort gefallen ist.

    Gehört direkt hinter den Eingang des Transports. Im Ruhezustand hört nur der lokale
    Erkenner mit, alle Audio-Frames werden verworfen. Nach dem Wachwort reicht die Schleuse
    das gepufferte Audio ab dem Wachwort nach und lässt alles Weitere durch. Sie schließt
    wieder, wenn `open_secs` lang weder der Nutzer noch der Bot gesprochen hat.

    Mit `gespraech_secs` bleibt sie danach weiter offen, damit Alexander nach einer Pause ohne
    Wachwort weiterreden kann. In dieser Zeit (`nur_eigene_stimme`) hält fremdes Sprechen die
    Schleuse nicht mehr offen; ob ein Satz zählt, entscheidet der Stimmfilter hinter der
    Transkription und meldet ihn mit `angenommen()`. Ohne diese Trennung hielte ein laufender
    Fernseher die Schleuse für immer offen.

    In Bereitschaft (`bereitschaft`, "Standby") gibt es kein Gespräch ohne Wachwort: Nach Alfreds
    Antwort schließt die Schleuse nach `bereitschaft_secs`, und schon in dieser Zeit zählt nur
    noch, was der Stimmfilter annimmt. Wartet Alfred auf eine Antwort (Rückfrage, Zuhören, eine
    laufende Antwort oder ein laufendes Werkzeug; `wartet_auf_antwort`), bleibt sie
    `rueckfrage_secs` offen. Am 6.10.2026 hielt ein Fernseher die Schleuse siebzehn Minuten
    offen, und Alfred antwortete auf "Yesterday that was planned".

    Event-Handler:

    - on_wake(gate, phrase): Das Wachwort ist gefallen; läuft, bevor Audio weitergeht.
    - on_sleep(gate): Die Schleuse hat wieder geschlossen.
    """

    NACHLAUF_SECS = 30.0  # so lange darf ein angefangener Satz das Ende der Gesprächszeit aufschieben
    NACHLAUF_BEREITSCHAFT_SECS = 10.0  # dasselbe in Bereitschaft

    def __init__(
        self,
        detector: WakeWordDetector,
        *,
        open_secs: float,
        gespraech_secs: float = 0.0,
        buffer_secs: float = 5.0,
        lead_secs: float = 0.3,
        clock: Callable[[], float] = time.monotonic,
        stimmpruefung: Callable[[bytes], bool] | None = None,
        bereitschaft: bool = False,
        bereitschaft_secs: float = 3.0,
        rueckfrage_secs: float = 8.0,
        **kwargs,
    ):
        """Legt die Schleuse an.

        Args:
            detector: Der lokale Wachwort-Erkenner.
            open_secs: So lange zählt nach dem letzten Gesprochenen jede Stimme.
            gespraech_secs: So lange bleibt die Schleuse insgesamt offen; nach `open_secs`
                zählt nur noch, was der Stimmfilter annimmt. Kleiner als `open_secs` heißt aus.
            buffer_secs: Länge des Audiopuffers im Ruhezustand. Muss die Dauer des Wachworts
                plus den Verzug des Erkenners abdecken.
            lead_secs: So viel Audio vor dem Wachwort wird mitgeschickt.
            clock: Zeitquelle, für Tests austauschbar.
            stimmpruefung: Bekommt das Audio ab dem Wachwort und sagt, ob es Alexanders Stimme
                ist; bei False bleibt die Schleuse zu. Läuft blockierend in einem Thread.
            bereitschaft: Mit Bereitschaft starten (siehe Klassenbeschreibung); umschaltbar.
            bereitschaft_secs: In Bereitschaft so lange nach Alfreds Antwort offen.
            rueckfrage_secs: In Bereitschaft so lange offen, wenn Alfred auf eine Antwort wartet.
            **kwargs: Weitere Argumente für FrameProcessor.
        """
        super().__init__(**kwargs)
        self._detector = detector
        self._open_secs = open_secs
        self._gespraech_secs = max(open_secs, gespraech_secs)
        self._schluss = False  # gespraech_beenden: beim nächsten Audio schließen
        self._buffer_secs = buffer_secs
        self._lead_secs = lead_secs
        self._clock = clock
        self._stimmpruefung = stimmpruefung
        self.abgewiesen = 0  # Wachwörter in fremder Stimme
        self.bereitschaft = bereitschaft
        self._bereitschaft_secs = bereitschaft_secs
        self._rueckfrage_secs = rueckfrage_secs
        self._wartet_auf_antwort: Callable[[], bool] = lambda: False
        # Gerade durch das Wachwort geöffnet und noch kein Transkript gesehen (Stimmfilter).
        self.erstes_transkript_offen = False

        self._awake = False
        self._user_speaking = False
        self._bot_speaking = False
        self._hat_gesprochen = False  # Alfred hat seit dem Aufwachen etwas gesagt
        self._idle_since = 0.0

        # Audio seit dem letzten reset() des Erkenners: (Endzeit im Strom, Frame).
        self._buffer: deque[tuple[float, InputAudioRawFrame]] = deque()
        self._stream_secs = 0.0

        self._register_event_handler("on_wake", sync=True)
        self._register_event_handler("on_sleep", sync=True)

    @property
    def awake(self) -> bool:
        return self._awake

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        if setup.audio_in_sample_rate != VoskWakeWordDetector.SAMPLE_RATE:
            raise ValueError(
                f"Die Wachwort-Erkennung braucht {VoskWakeWordDetector.SAMPLE_RATE} Hz am Eingang, "
                f"die Pipeline läuft mit {setup.audio_in_sample_rate} Hz."
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            await self._handle_audio(frame)
            return

        self._track_activity(frame)
        await self.push_frame(frame, direction)

    async def _handle_audio(self, frame: InputAudioRawFrame):
        if self._awake:
            if not self._idle_expired():
                await self.push_frame(frame)
                return
            self._awake = False
            logger.info("Wachwort-Schleuse geschlossen")
            await self._call_event_handler("on_sleep")

        self._stream_secs += frame.num_frames / frame.sample_rate
        self._buffer.append((self._stream_secs, frame))
        while self._buffer[0][0] < self._stream_secs - self._buffer_secs:
            self._buffer.popleft()

        hit = await asyncio.to_thread(self._detector.process, frame.audio)
        if hit:
            await self._wake(hit)

    async def _wake(self, hit: WakeWordHit):
        nachzureichen = [f for end, f in self._buffer if end > hit.start_secs - self._lead_secs]
        if self._stimmpruefung is not None:
            audio = b"".join(f.audio for f in nachzureichen)
            if not await asyncio.to_thread(self._stimmpruefung, audio):
                self.abgewiesen += 1
                logger.info(f"Wachwort '{hit.phrase}' in fremder Stimme, Schleuse bleibt zu")
                self._buffer.clear()
                self._stream_secs = 0.0
                self._detector.reset()
                return
        logger.info(f"Wachwort '{hit.phrase}' erkannt, Schleuse offen")
        await self.aufwecken(hit.phrase)
        self.erstes_transkript_offen = True
        for frame in nachzureichen:
            await self.push_frame(frame)

    async def aufwecken(self, grund: str):
        """Öffnet die Schleuse ohne Wachwort, etwa wenn Alfred von sich aus spricht."""
        self._buffer.clear()
        self._stream_secs = 0.0
        self._detector.reset()

        self._awake = True
        self._schluss = False
        self._user_speaking = self._bot_speaking = self._hat_gesprochen = False
        self.erstes_transkript_offen = False
        self._idle_since = self._clock()

        await self._call_event_handler("on_wake", grund)

    def wartet_auf_antwort_setzen(self, pruefen: Callable[[], bool]) -> None:
        """Sagt der Schleuse, wann Alfred auf eine Antwort wartet; in Bereitschaft bleibt sie dann länger offen."""
        self._wartet_auf_antwort = pruefen

    @property
    def nur_eigene_stimme(self) -> bool:
        """Offen, aber nach der freien Zeit: Jetzt zählt nur, was der Stimmfilter annimmt.

        In Bereitschaft gibt es keine freie Zeit nach Alfreds erster Antwort.
        """
        if not self._awake or self._bot_speaking:
            return False
        if self.bereitschaft:
            return self._hat_gesprochen
        return self._gespraech_secs > self._open_secs and self._clock() - self._idle_since > self._open_secs

    def angenommen(self) -> None:
        """Ein Satz nach der Pause zählt: Das Gespräch läuft weiter, jede Stimme zählt wieder."""
        if self._awake:
            self._idle_since = self._clock()

    def nach_antwort_schlafen(self) -> None:
        """Gespräch beendet: Beim nächsten Audio schließt die Schleuse; Alfreds Antwort läuft zu Ende."""
        if self._awake:
            self._schluss = True

    def _track_activity(self, frame: Frame):
        nutzer = isinstance(frame, (UserStartedSpeakingFrame, UserStoppedSpeakingFrame, UserSpeakingFrame))
        # Vor der Änderung fragen: Nach der Pause hält fremdes Sprechen die Schleuse nicht offen.
        nur_eigene = nutzer and self.nur_eigene_stimme
        if isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._hat_gesprochen = True
        elif not isinstance(frame, (UserSpeakingFrame, BotSpeakingFrame)):
            return
        if not nur_eigene:
            self._idle_since = self._clock()

    def _idle_expired(self) -> bool:
        if self._schluss:
            return True
        if self._bot_speaking:
            return False
        stille = self._clock() - self._idle_since
        if self.bereitschaft:
            frist = self._rueckfrage_secs if self._wartet_auf_antwort() else self._bereitschaft_secs
            # Wer gerade spricht, wird nicht mitten im Satz abgeschnitten, ein Fernseher aber nicht
            # lange gehört: Jeder angenommene Satz verlängert ohnehin (angenommen()).
            if self._user_speaking and stille < frist + self.NACHLAUF_BEREITSCHAFT_SECS:
                return False
            return stille > frist
        if self._gespraech_secs > self._open_secs:
            # Ein angefangener Satz darf das Ende kurz aufschieben, ein Fernseher nicht ewig.
            if self._user_speaking and stille < self._gespraech_secs + self.NACHLAUF_SECS:
                return False
            return stille > self._gespraech_secs
        if self._user_speaking:
            return False
        return stille > self._open_secs
