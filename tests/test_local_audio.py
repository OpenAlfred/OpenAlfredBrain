"""Toter Audio-Stream im Raum-Modus: einmal melden, Anlauf beenden, damit die Aufsicht neu startet."""

import asyncio
import threading
import time

import pytest
from loguru import logger

pyaudio = pytest.importorskip("pyaudio", reason="Raum-Modus braucht das Extra raum (PyAudio)")
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.runner import WorkerRunner  # noqa: E402
from pipecat.pipeline.worker import PipelineWorker, ProcessorUnusablePolicy  # noqa: E402
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402
from pipecat.transports.local.audio import LocalAudioTransportParams  # noqa: E402

from alfred.local_audio import (  # noqa: E402
    WatchedLocalAudioInputTransport,
    WatchedLocalAudioOutputTransport,
    WatchedLocalAudioTransport,
    stream_lebt,
)

AUSGANG = LocalAudioTransportParams(audio_out_enabled=True)
EINGANG = LocalAudioTransportParams(audio_in_enabled=True)


def tts_audio(anzahl: int) -> list[TTSAudioRawFrame]:
    """40 ms Sprachausgabe je Frame, so groß, wie Pipecat sie an den Lautsprecher schreibt."""
    return [TTSAudioRawFrame(audio=bytes(1920), sample_rate=24000, num_channels=1) for _ in range(anzahl)]


class FakeOutStream:
    """PyAudio-Ausgang. Fällt er aus, dann wie am 5.10.2026: ein Host-Fehler, danach geschlossen."""

    def __init__(
        self, *, ausfall_nach: int | None = None, einzelfehler_bei: int | None = None, startfehler: bool = False
    ):
        self.ausfall_nach = ausfall_nach  # so viele Schreibversuche gelingen, dann ist das Gerät weg
        self.einzelfehler_bei = einzelfehler_bei  # dieser Versuch scheitert, der Stream läuft weiter
        self.startfehler = startfehler  # weg zwischen Öffnen und Start
        self.versuche = 0
        self.offen = True
        self.aktiv = False
        self.gestoppt = False
        self.geschlossen = False

    def start_stream(self):
        if self.startfehler:
            self.offen = False
            raise OSError(-9999, "Unanticipated host error")
        self.aktiv = True

    def is_active(self):
        if not self.offen:
            raise OSError("Stream not open")
        return self.aktiv

    def write(self, audio):
        self.versuche += 1
        if not self.offen:
            raise OSError(-9988, "Stream closed")
        if self.versuche == self.einzelfehler_bei:
            raise OSError(-9980, "Output underflowed")
        if self.ausfall_nach is not None and self.versuche > self.ausfall_nach:
            self.offen = False  # PyAudio schließt den Stream nach jedem gescheiterten write selbst
            raise OSError(-9999, "Unanticipated host error")

    def stop_stream(self):
        if not self.offen:
            raise OSError("Stream not open")
        self.aktiv = False
        self.gestoppt = True

    def close(self):
        self.offen = self.aktiv = False
        self.geschlossen = True


class FakeInStream:
    """PyAudio-Eingang im Rückrufbetrieb: Ein eigener Thread ruft alle 20 ms, wie PortAudio.

    Nach `liefert_secs` ist das Gerät weg: Die Rückrufe bleiben aus, ohne Fehler. Mit `haelt_an`
    hält PortAudio den Stream dabei an, wie nach einem Gerätefehler.
    """

    def __init__(self, *, liefert_secs: float | None = None, haelt_an: bool = False, startfehler: bool = False):
        self.callback = None
        self.liefert_secs = liefert_secs
        self.haelt_an = haelt_an
        self.startfehler = startfehler
        self.rueckrufe = 0
        self.aktiv = False
        self.geschlossen = False
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    def start_stream(self):
        if self.startfehler:
            raise OSError(-9999, "Unanticipated host error")
        self.aktiv = True
        self._thread = threading.Thread(target=self._liefern, daemon=True)
        self._thread.start()

    def _liefern(self):
        ende = None if self.liefert_secs is None else time.monotonic() + self.liefert_secs
        while not self._halt.wait(0.02):
            if ende is not None and time.monotonic() > ende:
                self.aktiv = not self.haelt_an
                return
            self.rueckrufe += 1
            self.callback(bytes(640), 320, {}, 0)

    def is_active(self):
        if self.geschlossen:
            raise OSError("Stream not open")
        return self.aktiv

    def stop_stream(self):
        self._halt.set()
        if self._thread:
            self._thread.join()
        self.aktiv = False

    def close(self):
        self.stop_stream()
        self.geschlossen = True


class FakePyAudio:
    """Gibt den vorbereiteten Stream heraus; ein Eingang bekommt dabei seinen Rückruf."""

    def __init__(self, stream):
        self.stream = stream

    def get_format_from_width(self, width):
        return pyaudio.paInt16

    def open(self, **kwargs):
        self.stream.callback = kwargs.get("stream_callback")
        return self.stream


@pytest.fixture
def fehler():
    """Was Alfred als Fehler loggt, als Liste von Texten."""
    texte = []
    senke = logger.add(lambda m: texte.append(m.record["message"]), level="ERROR")
    yield texte
    logger.remove(senke)


async def laufen(prozessor, schicken=None, *, timeout: float = 5.0) -> None:
    """Ein Worker wie im Raum-Modus: Ein unbrauchbarer Baustein bricht ihn ab.

    Endet der Lauf ohne EndFrame, hat ihn ein Fehler abgebrochen; sonst schlägt das Zeitlimit zu.
    """
    worker = PipelineWorker(
        Pipeline([prozessor]), idle_timeout_secs=None, processor_unusable_policy=ProcessorUnusablePolicy.CANCEL
    )
    gestartet = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(worker, frame):
        gestartet.set()

    async def frames_schicken():
        await gestartet.wait()
        if schicken:
            await schicken(worker)

    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), frames_schicken()), timeout)


def test_toter_lautsprecher_beendet_den_anlauf_mit_einer_meldung(fehler):
    stream = FakeOutStream(ausfall_nach=3)
    ausgang = WatchedLocalAudioOutputTransport(FakePyAudio(stream), AUSGANG)

    async def sprechen(worker):
        await worker.queue_frames(tts_audio(30))

    asyncio.run(laufen(ausgang, sprechen))  # kein EndFrame: Der Lauf endet nur durch den Abbruch

    assert stream.versuche == 4, "nach dem Ausfall kein weiterer Schreibversuch"
    assert not ausgang.is_usable
    assert len([t for t in fehler if "Lautsprecher ausgefallen" in t]) == 1
    assert any("[Errno -9999] Unanticipated host error" in t for t in fehler)
    assert any("can no longer do its job" in t for t in fehler), "der Worker bricht ab"
    assert not any("Error writing" in t for t in fehler), "kein Fehler je Audio-Stück wie am 5.10."
    # Den von PyAudio geschlossenen Stream nur noch freigeben: stop_stream scheiterte mit „Stream not open".
    assert stream.geschlossen and not stream.gestoppt
    assert not any("Error cleaning up" in t for t in fehler)


def test_einzelner_schreibfehler_bei_lebendem_stream_bleibt_ein_einzelfall(fehler):
    stream = FakeOutStream(einzelfehler_bei=2)
    ausgang = WatchedLocalAudioOutputTransport(FakePyAudio(stream), AUSGANG)

    _, hoch = asyncio.run(run_test(ausgang, frames_to_send=[*tts_audio(5), SleepFrame(sleep=0.2)]))

    assert not any(isinstance(f, ErrorFrame) for f in hoch)
    assert ausgang.is_usable
    assert stream.versuche > 2, "nach dem Fehler im zweiten Versuch geht es weiter"
    assert len([t for t in fehler if "Error writing" in t]) == 1, "Pipecat loggt den Einzelfall wie bisher"
    assert stream.gestoppt and stream.geschlossen


@pytest.mark.parametrize(
    ("haelt_an", "grund"),
    [(False, "kein Audio"), (True, "PortAudio hat den Stream angehalten")],
    ids=["rueckrufe_bleiben_aus", "portaudio_haelt_an"],
)
def test_totes_mikrofon_beendet_den_anlauf(fehler, haelt_an, grund):
    stream = FakeInStream(liefert_secs=0.2, haelt_an=haelt_an)
    eingang = WatchedLocalAudioInputTransport(FakePyAudio(stream), EINGANG, ohne_audio_secs=0.5, takt_secs=0.05)

    asyncio.run(laufen(eingang))  # kein EndFrame: Der Lauf endet nur durch den Abbruch

    assert stream.rueckrufe > 0
    assert not eingang.is_usable
    meldungen = [t for t in fehler if "Mikrofon ausgefallen" in t]
    assert len(meldungen) == 1 and grund in meldungen[0]
    assert any("can no longer do its job" in t for t in fehler), "der Worker bricht ab"
    assert stream.geschlossen


@pytest.mark.parametrize("richtung", ["Lautsprecher", "Mikrofon"])
def test_scheitert_schon_der_start_endet_der_anlauf(fehler, richtung):
    # Sonst hielte Pipecat das für einen vorübergehenden Fehler und liefe stumm oder taub weiter.
    if richtung == "Lautsprecher":
        prozessor = WatchedLocalAudioOutputTransport(FakePyAudio(FakeOutStream(startfehler=True)), AUSGANG)
    else:
        prozessor = WatchedLocalAudioInputTransport(FakePyAudio(FakeInStream(startfehler=True)), EINGANG)

    asyncio.run(laufen(prozessor))  # kein EndFrame: Der Lauf endet nur durch den Abbruch

    assert len([t for t in fehler if f"{richtung} ausgefallen: [Errno -9999]" in t]) == 1
    assert any("can no longer do its job" in t for t in fehler), "der Worker bricht ab"
    assert not any("Error processing frame" in t or "Error cleaning up" in t for t in fehler)


def test_lebendes_mikrofon_gilt_nicht_als_tot(fehler):
    # Dreimal so lange wie die Grenze: Zählte der Wächter die Rückrufe nicht, schlüge er an.
    stream = FakeInStream()
    eingang = WatchedLocalAudioInputTransport(FakePyAudio(stream), EINGANG, ohne_audio_secs=0.5, takt_secs=0.05)

    _, hoch = asyncio.run(run_test(eingang, frames_to_send=[SleepFrame(sleep=1.5)]))

    assert stream.rueckrufe > 30
    assert eingang.is_usable
    assert not any(isinstance(f, ErrorFrame) for f in hoch)
    assert not any("Mikrofon ausgefallen" in t for t in fehler)
    assert stream.geschlossen


def test_transport_liefert_die_ueberwachten_richtungen(monkeypatch):
    attrappe = FakePyAudio(FakeOutStream())
    monkeypatch.setattr(pyaudio, "PyAudio", lambda: attrappe)

    transport = WatchedLocalAudioTransport(LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True))

    assert isinstance(transport.input(), WatchedLocalAudioInputTransport)
    assert isinstance(transport.output(), WatchedLocalAudioOutputTransport)
    assert transport.input() is transport.input() and transport.output() is transport.output()
    # room._einmal_laufen gibt diese Instanz nach jedem Anlauf frei.
    assert transport._pyaudio is attrappe


def test_pyaudio_schliesst_den_stream_nach_einem_gescheiterten_write():
    """Darauf baut WatchedLocalAudioOutputTransport: Nach einem Fehler ist der Stream zu.

    Schreiben auf einen angehaltenen Stream lehnt PortAudio ab, bevor das Gerät etwas bekommt; es
    ist also nichts zu hören. Ohne Ausgabegerät (Server ohne Audio) entfällt der Test.
    """
    audio = pyaudio.PyAudio()
    try:
        try:
            stream = audio.open(format=pyaudio.paInt16, channels=1, rate=16000, output=True)
        except OSError as e:
            pytest.skip(f"kein Ausgabegerät: {e}")
        assert stream_lebt(stream)
        stream.stop_stream()
        with pytest.raises(OSError):
            stream.write(bytes(320))
        with pytest.raises(OSError, match="Stream closed"):
            stream.write(bytes(320))
        assert not stream_lebt(stream)
        stream.close()  # wie WatchedLocalAudioOutputTransport.cleanup: harmlos, gibt ihn nur frei
    finally:
        audio.terminate()
