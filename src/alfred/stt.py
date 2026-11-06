"""Transkription über Mistral, mit Meldung an Pipecat, wann ein Transkript endgültig ist.

Behebt Schwächen von Pipecats Standard-MistralSTTService:
- Transkripte nach Sprechpause sofort als endgültig bestätigen (spart 1,4 s Wartezeit).
- Verbindung beim Ruhen der Wachwort-Schleuse schließen (verhindert 30s-Timeout von Mistral).
- Wiederverbindungs-Drosselung (Backoff): Bei Verbindungsfehlern oder Server-Ausfällen
  (z.B. EngineDeadError, 1011, 1013 "try again later") nicht 50-mal pro Sekunde neu verbinden,
  sondern mit wachsender Pause (1 s bis 15 s) abwarten und Audio verwerfen.
- Schutz vor unbehandelten Fehlern beim Audio-Senden und Flush über geschlossene Sockets.
- Keine Flut an doppelten ErrorFrames bei einem Ausfall.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from typing import cast

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.mistral.stt import MistralSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.types import assert_given

try:
    from mistralai.client.models import (
        AudioFormat,
        RealtimeTranscriptionError,
        RealtimeTranscriptionSessionCreated,
        TranscriptionStreamDone,
        TranscriptionStreamLanguage,
        TranscriptionStreamTextDelta,
    )
    from mistralai.extra.realtime import UnknownRealtimeEvent
except ImportError:
    pass


class FinalizingMistralSTTService(MistralSTTService):
    """Markiert das Transkript nach jeder Sprechpause als endgültig und drosselt Reconnects.

    Pipecats Mistral-Dienst schickt bei einer Sprechpause ein flush an Mistral, sagt dem
    Rest der Pipeline aber nicht, dass die Antwort darauf das fertige Transkript ist. Die
    Turn-Erkennung wartet dann jedes Mal einen Sicherheits-Timer ab (1,89 s nach dem letzten
    Wort), obwohl das Transkript nach rund 0,3 s da ist. Im ersten echten Lauf kostete das
    jede Antwort etwa 1,4 s.

    Mistral beantwortet genau jedes flush mit einem abschließenden Transkript. Ohne flush
    kommt keines. Das abschließende Transkript ist damit die Bestätigung des flush.

    Fällt Mistrals Backend aus (z.B. EngineDeadError, gRPC-Abbrüche oder 1013 "try again later"),
    verhinderte Pipecats Standard-Code jede Erholung: Jedes 20-ms-Audiochunk löste sofort einen
    neuen Verbindungsversuch aus (50/s), was zu Verbindungs-Resets, unkontrollierten Fehlern und
    einer ErrorFrame-Flut führte. Dieser Dienst fängt Verbindungsabbrüche sicher ab und wartet
    mit exponentiellem Backoff ab.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, **kwargs):
        super().__init__(**kwargs)
        self._clock = clock
        self._naechster_verbindungsversuch: float = 0.0
        self._wiederholungs_pause: float = 1.0
        self._verbindet: bool = False
        self._letzter_fehler_text: str | None = None
        self._fehler_gemeldet: bool = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self.request_finalize()
            if self._connection and not self._connection.is_closed:
                try:
                    await self._connection.flush_audio()
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"{self}: flush_audio fehlgeschlagen ({e})")
                    self._verbindung_verwerfen()
            await super(MistralSTTService, self).process_frame(frame, direction)
            return

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._accumulated_text = ""
            await super(MistralSTTService, self).process_frame(frame, direction)
            return

        await super().process_frame(frame, direction)

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        if isinstance(frame, TranscriptionFrame):
            self.confirm_finalize()
        await super().push_frame(frame, direction)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Sendet Audio an Mistral mit Reconnect-Drosselung und Fehlerschutz."""
        if not self._connection or self._connection.is_closed:
            jetzt = self._clock()
            if jetzt < self._naechster_verbindungsversuch or self._verbindet:
                # Während der Abkühlpause Audio verwerfen, kein Verbindungs-Sturm
                yield None
                return
            await self._connect()

        if self._connection is None or self._connection.is_closed:
            yield None
            return

        try:
            await self._connection.send_audio(audio)
        except Exception as e:  # noqa: BLE001
            fehler_text = str(e).splitlines()[0]
            logger.warning(f"{self}: Audio senden fehlgeschlagen ({fehler_text})")
            self._verbindung_verwerfen()

        yield None

    async def _connect(self):
        """Baut Verbindung zu Mistrals Realtime-API mit Backoff auf."""
        if self._verbindet:
            return
        self._verbindet = True
        try:
            logger.debug(f"{self}: Verbinde mit Mistral STT...")
            audio_format = AudioFormat(
                encoding="pcm_s16le",
                sample_rate=self.sample_rate,
            )
            model = assert_given(self._settings.model)
            if model is None:
                raise ValueError("Mistral STT model must be specified")
            self._connection = await self._client.audio.realtime.connect(
                model=model,
                audio_format=audio_format,
                target_streaming_delay_ms=self._target_streaming_delay_ms,
            )
            self._receive_task = self.create_task(
                self._receive_events(), name="mistral_stt_receive"
            )
        except Exception as e:
            fehler_text = str(e).splitlines()[0]
            self._connection = None
            jetzt = self._clock()
            self._naechster_verbindungsversuch = jetzt + self._wiederholungs_pause
            pause = self._wiederholungs_pause
            self._wiederholungs_pause = min(self._wiederholungs_pause * 2.0, 15.0)
            if not self._fehler_gemeldet or fehler_text != self._letzter_fehler_text:
                logger.warning(
                    f"{self}: Verbindung zu Mistral STT fehlgeschlagen ({fehler_text}); "
                    f"nächster Versuch in {pause:.0f} s"
                )
                self._letzter_fehler_text = fehler_text
                self._fehler_gemeldet = True
                await self.push_error(error_msg=f"Mistral STT nicht erreichbar: {fehler_text}", exception=e)
        finally:
            self._verbindet = False

    async def _receive_events(self):
        """Hintergrund-Empfang mit Ausfall-Behandlung und Backoff."""
        connection = self._connection
        if connection is None:
            return
        try:
            async for event in connection.events():
                if isinstance(event, RealtimeTranscriptionSessionCreated):
                    logger.debug(f"{self}: Session created: {event.session}")
                    self._wiederholungs_pause = 1.0  # Verbindung steht -> Backoff zurücksetzen
                    self._fehler_gemeldet = False
                    self._letzter_fehler_text = None
                    await self._call_event_handler("on_connected")

                elif isinstance(event, TranscriptionStreamTextDelta):
                    self._accumulated_text += event.text
                    await self.push_frame(
                        InterimTranscriptionFrame(
                            self._accumulated_text,
                            self._user_id,
                            time_now_iso8601(),
                        )
                    )

                elif isinstance(event, TranscriptionStreamDone):
                    if event.text:
                        await self.emit_stt_usage_metrics()
                        await self.push_frame(
                            TranscriptionFrame(
                                event.text,
                                self._user_id,
                                time_now_iso8601(),
                                language=self._detected_language,
                            )
                        )
                        await self._handle_transcription(event.text, True, self._detected_language)
                    self._accumulated_text = ""

                elif isinstance(event, TranscriptionStreamLanguage):
                    self._detected_language = cast("Language | None", event.audio_language)

                elif isinstance(event, RealtimeTranscriptionError):
                    error_msg = event.error.message if event.error else "Unknown error"
                    kurz = error_msg.splitlines()[0][:140]
                    self._naechster_verbindungsversuch = self._clock() + self._wiederholungs_pause
                    pause = self._wiederholungs_pause
                    self._wiederholungs_pause = min(self._wiederholungs_pause * 2.0, 15.0)
                    if not self._fehler_gemeldet or kurz != self._letzter_fehler_text:
                        logger.warning(f"{self}: Mistral STT Fehler: {kurz}; nächster Versuch in {pause:.0f} s")
                        self._letzter_fehler_text = kurz
                        self._fehler_gemeldet = True
                        await self.push_error(error_msg=f"Mistral STT error: {kurz}")
                    await self._call_event_handler("on_connection_error", error_msg)
                    break

                elif isinstance(event, UnknownRealtimeEvent):
                    logger.warning(f"{self}: Unknown realtime event: {event}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            fehler_text = str(e).splitlines()[0]
            self._naechster_verbindungsversuch = self._clock() + self._wiederholungs_pause
            pause = self._wiederholungs_pause
            self._wiederholungs_pause = min(self._wiederholungs_pause * 2.0, 15.0)
            if not self._fehler_gemeldet or fehler_text != self._letzter_fehler_text:
                logger.warning(f"{self}: Mistral STT Empfangsfehler: {fehler_text}; nächster Versuch in {pause:.0f} s")
                self._letzter_fehler_text = fehler_text
                self._fehler_gemeldet = True
                await self.push_error(error_msg=f"Mistral STT receive error: {fehler_text}", exception=e)
            await self._call_event_handler("on_connection_error", str(e))
        finally:
            self._connection = None

    def _verbindung_verwerfen(self) -> None:
        """Trennt eine fehlerhafte Verbindung und plant die Wiederholung."""
        verbindung = self._connection
        self._connection = None
        if verbindung is not None and not verbindung.is_closed:
            asyncio.create_task(self._schliesse_verbindung_sicher(verbindung))
        if self._receive_task and not self._receive_task.done():
            self.cancel_task(self._receive_task)
            self._receive_task = None
        self._naechster_verbindungsversuch = self._clock() + self._wiederholungs_pause
        self._wiederholungs_pause = min(self._wiederholungs_pause * 2.0, 15.0)

    @staticmethod
    async def _schliesse_verbindung_sicher(verbindung) -> None:
        try:
            await asyncio.wait_for(verbindung.close(), timeout=1.0)
        except Exception:  # noqa: BLE001
            pass

    async def ruhen(self) -> None:
        """Schließt die Verbindung zu Mistral, solange die Wachwort-Schleuse zu ist.

        Hinter geschlossener Schleuse kommt kein Audio an. Mistral bricht eine stille Sitzung
        nach dreißig Sekunden selbst ab ("Timeout waiting for response", danach 1011), was im
        Log nach jedem Gespräch als Fehler stand. Das erste Audio nach dem Wachwort baut die
        Verbindung neu auf, rund 0,4 s, während das Wachwort noch nachgereicht wird.

        Nur aufrufen, solange kein Audio fließt: Pipecats eigenes _disconnect ließe die
        Verbindung offen, weil der abgebrochene Empfang _connection schon geleert hat.
        """
        verbindung, empfang = self._connection, self._receive_task
        self._connection = None
        self._receive_task = None
        self._naechster_verbindungsversuch = 0.0  # Beim nächsten Wachwort sofort verbinden
        self._wiederholungs_pause = 1.0
        self._letzter_fehler_text = None
        self._fehler_gemeldet = False
        if verbindung is None:
            return
        if empfang is not None and not empfang.done():
            await self.cancel_task(empfang)
        if not verbindung.is_closed:
            try:
                await asyncio.wait_for(verbindung.close(), timeout=1.0)
            except Exception as e:  # noqa: BLE001 - die Verbindung ist ohnehin aufgegeben
                logger.debug(f"{self}: Verbindung beim Ruhen nicht sauber geschlossen: {e}")
        logger.debug(f"{self}: Spracherkennung ruht bis zum nächsten Wachwort")
