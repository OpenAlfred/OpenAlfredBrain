"""Mikrofon und Lautsprecher am Rechner, mit Meldung, wenn ein Audio-Stream im Betrieb stirbt.

Pipecats `LocalAudioTransport` bemerkt nicht, wenn ein Gerät im laufenden Betrieb wegfällt
(USB-Aussetzer, Treiber, Ruhezustand):
- Der Ausgang loggt jeden gescheiterten Schreibversuch und macht weiter. Am 5.10.2026 kam um
  08:00:28 einmal „Unanticipated host error", danach 407-mal „Stream closed", und die Pipeline lief
  stumm weiter, bis Alexander um 08:13 Strg+C drückte. Die Aufsicht (`room.run_room`) griff nie.
- Der Eingang kennt keinen Fehler: Er bekommt Audio nur über PortAudios Rückruf, und der bleibt
  dann einfach aus (oder PortAudio hält den Stream an). Alfred wäre taub, bis er wieder spricht.

Die Unterklassen hier melden einen toten Stream, auch einen, der schon beim Start scheitert, einmal
als dauerhaften Fehler (`push_error(..., force_treat_as_permanent=True)`). Der Raum-Modus setzt
`ProcessorUnusablePolicy.CANCEL`; der `PipelineWorker` bricht den Anlauf ab, und die Aufsicht baut
ihn mit frisch eingelesenen Geräten neu, wie nach einem Fehler beim Öffnen.
"""

from __future__ import annotations

import asyncio
import time

from pipecat.frames.frames import CancelFrame, EndFrame, OutputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.local.audio import (
    LocalAudioInputTransport,
    LocalAudioOutputTransport,
    LocalAudioTransport,
)

# PortAudio ruft den Eingang alle 20 ms auf. Bleibt das so lange aus, ist das Gerät weg; ein
# Fehlalarm kostet nur einen Neustart der Aufsicht, ein verpasster einen tauben Alfred.
OHNE_AUDIO_SECS = 5.0
PRUEFTAKT_SECS = 1.0


def stream_lebt(stream) -> bool:
    """Ob ein PyAudio-Stream noch läuft.

    PyAudio schließt einen Stream nach jedem gescheiterten Schreibversuch selbst: Danach meldet
    `is_active` „Stream not open", und jedes weitere `write` scheitert mit „Stream closed" (PyAudio
    0.2.14, docs/erkenntnisse.md, 16.10.2026). Hält PortAudio einen Stream nach einem Gerätefehler an,
    ist er nicht mehr aktiv.
    """
    try:
        return bool(stream.is_active())
    except OSError:
        return False


class WatchedLocalAudioOutputTransport(LocalAudioOutputTransport):
    """Lautsprecher, der einen toten Stream einmal meldet, statt jeden Schreibversuch zu loggen."""

    def __init__(self, py_audio, params):
        super().__init__(py_audio, params)
        self._weg = False

    async def start(self, frame: StartFrame):
        try:
            await super().start(frame)
        except OSError as e:  # Pipecat nähme das als vorübergehend und liefe ohne Ausgang weiter
            await self._aufgeben(e)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        if self._weg:
            return False
        try:
            return await super().write_audio_frame(frame)
        except OSError as e:
            if stream_lebt(self._out_stream):
                raise  # ein einzelner Fehler, der Stream läuft weiter: Pipecat loggt ihn wie bisher
            await self._aufgeben(e)
            return False

    async def cleanup(self):
        if self._weg and self._out_stream:
            # PyAudio hat den Stream schon geschlossen; Pipecats stop_stream scheiterte mit
            # „Stream not open", close dagegen gibt ihn nur noch frei.
            self._out_stream.close()
            self._out_stream = None
        await super().cleanup()

    async def _aufgeben(self, e: OSError) -> None:
        # Unbrauchbar gemeldet, schreibt Pipecat nichts mehr hinein: kein Fehler je Audio-Stück.
        self._weg = True
        await self.push_error(f"Lautsprecher ausgefallen: {e}", exception=e, force_treat_as_permanent=True)


class WatchedLocalAudioInputTransport(LocalAudioInputTransport):
    """Mikrofon mit Wächter: meldet sich, wenn PortAudio kein Audio mehr liefert.

    Ein toter Eingang wirft keinen Fehler, der Rückruf bleibt nur aus. Der Wächter prüft deshalb im
    Takt, ob der Stream noch läuft und wann zuletzt Audio kam.
    """

    def __init__(
        self,
        py_audio,
        params,
        *,
        ohne_audio_secs: float = OHNE_AUDIO_SECS,
        takt_secs: float = PRUEFTAKT_SECS,
    ):
        super().__init__(py_audio, params)
        self._ohne_audio_secs = ohne_audio_secs
        self._takt_secs = takt_secs
        self._letztes_audio = 0.0
        self._waechter: asyncio.Task | None = None

    def _audio_in_callback(self, in_data, frame_count, time_info, status):
        # Läuft in PortAudios Thread, nicht in der Ereignisschleife: Steht die einmal ein paar
        # Sekunden, gilt das Mikrofon trotzdem nicht als tot.
        self._letztes_audio = time.monotonic()
        return super()._audio_in_callback(in_data, frame_count, time_info, status)

    async def start(self, frame: StartFrame):
        try:
            await super().start(frame)
        except OSError as e:  # Pipecat nähme das als vorübergehend und liefe taub weiter
            await self._melden(str(e), e)
            return
        if self._in_stream and not self._waechter:
            self._letztes_audio = time.monotonic()
            self._waechter = self.create_task(self._wachen())

    async def stop(self, frame: EndFrame):
        await self._waechter_beenden()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        await self._waechter_beenden()
        await super().cancel(frame)

    async def cleanup(self):
        await self._waechter_beenden()
        await super().cleanup()

    async def _waechter_beenden(self) -> None:
        if self._waechter:
            await self.cancel_task(self._waechter)
            self._waechter = None

    async def _wachen(self) -> None:
        while True:
            await asyncio.sleep(self._takt_secs)
            still = time.monotonic() - self._letztes_audio
            if not stream_lebt(self._in_stream):
                grund = "PortAudio hat den Stream angehalten"
            elif still > self._ohne_audio_secs:
                grund = f"seit {still:.0f} s kein Audio"
            else:
                continue
            await self._melden(grund)
            return

    async def _melden(self, grund: str, e: OSError | None = None) -> None:
        await self.push_error(f"Mikrofon ausgefallen: {grund}", exception=e, force_treat_as_permanent=True)


class WatchedLocalAudioTransport(LocalAudioTransport):
    """Pipecats `LocalAudioTransport` mit überwachtem Ein- und Ausgang (Raum-Modus)."""

    def input(self) -> FrameProcessor:
        if not self._input:
            self._input = WatchedLocalAudioInputTransport(self._pyaudio, self._params)
        return self._input

    def output(self) -> FrameProcessor:
        if not self._output:
            self._output = WatchedLocalAudioOutputTransport(self._pyaudio, self._params)
        return self._output
