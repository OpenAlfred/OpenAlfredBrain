import asyncio

from pipecat.frames.frames import TranscriptionFrame, VADUserStoppedSpeakingFrame
from pipecat.tests.utils import run_test

from alfred.stt import FinalizingMistralSTTService


class OfflineSTT(FinalizingMistralSTTService):
    """Der Dienst ohne Verbindung zu Mistral."""

    async def _connect(self):
        pass


def transcripts(*to_send) -> list[TranscriptionFrame]:
    down, _ = asyncio.run(run_test(OfflineSTT(api_key="unbenutzt"), frames_to_send=list(to_send)))
    return [f for f in down if isinstance(f, TranscriptionFrame)]


def transcript(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text, "nutzer", "2026-10-02T23:00:00")


def test_transkript_nach_sprechpause_ist_endgueltig():
    (frame,) = transcripts(VADUserStoppedSpeakingFrame(), transcript("Wie spät ist es?"))

    assert frame.finalized


def test_transkript_ohne_sprechpause_ist_nicht_endgueltig():
    (frame,) = transcripts(transcript("Wie spät ist es?"))

    assert not frame.finalized


def test_jede_sprechpause_gilt_nur_fuer_ein_transkript():
    erstes, zweites = transcripts(
        VADUserStoppedSpeakingFrame(), transcript("Erster Satz."), transcript("Zweiter Satz.")
    )

    assert erstes.finalized and not zweites.finalized


class FakeVerbindung:
    def __init__(self):
        self.is_closed = False
        self.geschlossen = 0

    async def close(self):
        self.is_closed = True
        self.geschlossen += 1


def test_ruhen_schliesst_die_verbindung_bis_zum_naechsten_wachwort():
    async def lauf():
        stt = OfflineSTT(api_key="unbenutzt")
        verbindung = FakeVerbindung()
        empfang = asyncio.create_task(asyncio.sleep(60))
        stt._connection, stt._receive_task = verbindung, empfang

        async def abbrechen(task):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        stt.cancel_task = abbrechen
        await stt.ruhen()
        assert empfang.cancelled() and verbindung.geschlossen == 1
        assert stt._connection is None and stt._receive_task is None, "das nächste Audio verbindet neu"

        await stt.ruhen()
        assert verbindung.geschlossen == 1, "ohne Verbindung tut es nichts"

    asyncio.run(lauf())


def test_reconnect_drosselung_verwirft_audio_waehrend_cooldown():
    """Während der Backoff-Pause darf kein neuer Verbindungsversuch gestartet werden."""
    jetzt = 100.0

    def clock():
        return jetzt

    stt = OfflineSTT(api_key="unbenutzt", clock=clock)
    verbindungsversuche = 0

    async def fehlerhafter_connect():
        nonlocal verbindungsversuche
        verbindungsversuche += 1
        stt._connection = None
        stt._naechster_verbindungsversuch = stt._clock() + stt._wiederholungs_pause
        stt._wiederholungs_pause = min(stt._wiederholungs_pause * 2.0, 15.0)

    stt._connect = fehlerhafter_connect

    async def lauf():
        nonlocal jetzt
        # Erster Chunk: Reconnect wird versucht und schlägt fehl (Pause = 1 s)
        async for _ in stt.run_stt(b"\x00" * 320):
            pass
        assert verbindungsversuche == 1
        assert stt._naechster_verbindungsversuch == 101.0
        assert stt._wiederholungs_pause == 2.0

        # Nächste Chunks 20 ms später: Noch im Cooldown (jetzt = 100.02)
        jetzt = 100.02
        for _ in range(10):
            async for _ in stt.run_stt(b"\x00" * 320):
                pass
        # Kein weiterer Verbindungsversuch!
        assert verbindungsversuche == 1

        # Erst nach Ablauf von 1 s (jetzt = 101.1): Neuer Versuch
        jetzt = 101.1
        async for _ in stt.run_stt(b"\x00" * 320):
            pass
        assert verbindungsversuche == 2
        assert stt._naechster_verbindungsversuch == 101.1 + 2.0
        assert stt._wiederholungs_pause == 4.0

    asyncio.run(lauf())


class VerbindungMitFehler:
    def __init__(self):
        self.is_closed = False
        self.gesendet = 0
        self.geflusht = 0

    async def send_audio(self, audio: bytes):
        raise RuntimeError("Connection closed (1011)")

    async def flush_audio(self):
        raise RuntimeError("Connection closed (1013)")

    async def close(self):
        self.is_closed = True


def test_send_audio_fehler_wird_abgefangen_und_setzt_backoff():
    """Ein Fehler beim Senden von Audio darf den Prozess nicht abbrechen."""
    jetzt = 200.0
    stt = OfflineSTT(api_key="unbenutzt", clock=lambda: jetzt)
    verbindung = VerbindungMitFehler()
    stt._connection = verbindung

    async def lauf():
        async for _ in stt.run_stt(b"\x00" * 320):
            pass
        # Verbindung wurde abgeräumt und Backoff gesetzt
        assert stt._connection is None
        assert stt._naechster_verbindungsversuch == 201.0
        assert stt._wiederholungs_pause == 2.0

    asyncio.run(lauf())


def test_flush_audio_fehler_bringt_process_frame_nicht_zum_absturz():
    """Ein Fehler bei flush_audio während VADUserStoppedSpeakingFrame wird sicher abgefangen."""
    from pipecat.processors.frame_processor import FrameDirection

    stt = OfflineSTT(api_key="unbenutzt")
    verbindung = VerbindungMitFehler()
    stt._connection = verbindung

    async def lauf():
        # Darf keine Exception werfen
        await stt.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert stt._connection is None

    asyncio.run(lauf())


def test_ruhen_setzt_backoff_zurueck():
    """Beim Schließen der Schleuse wird der Backoff für das nächste Aufwachen zurückgesetzt."""
    stt = OfflineSTT(api_key="unbenutzt")
    stt._naechster_verbindungsversuch = 999.0
    stt._wiederholungs_pause = 16.0
    stt._fehler_gemeldet = True

    async def lauf():
        await stt.ruhen()
        assert stt._naechster_verbindungsversuch == 0.0
        assert stt._wiederholungs_pause == 1.0
        assert not stt._fehler_gemeldet

    asyncio.run(lauf())

