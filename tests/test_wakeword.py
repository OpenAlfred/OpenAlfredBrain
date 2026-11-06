import asyncio
import importlib.util
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.worker import PipelineParams
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test

from alfred.wakeword import VoskWakeWordDetector, WakeWordGate, WakeWordHit

FRAME_SECS = 0.02
SAMPLES_PER_FRAME = 320  # 20 ms bei 16 kHz


def frames(start: int, stop: int) -> list[InputAudioRawFrame]:
    """Audio-Frames, die ihre Nummer im Inhalt tragen."""
    return [
        InputAudioRawFrame(
            audio=i.to_bytes(2, "little") * SAMPLES_PER_FRAME, sample_rate=16000, num_channels=1
        )
        for i in range(start, stop)
    ]


class FakeDetector:
    """Meldet genau einmal ein Wachwort: beim n-ten Audio-Frame."""

    def __init__(self, hit_at: int | None, start_secs: float):
        self.hit_at = hit_at
        self.start_secs = start_secs
        self.calls = 0
        self.resets = 0

    def process(self, audio: bytes) -> WakeWordHit | None:
        self.calls += 1
        if self.calls == self.hit_at:
            self.hit_at = None
            return WakeWordHit("alfred", self.start_secs)
        return None

    def reset(self) -> None:
        self.resets += 1


class GateUnderTest(WakeWordGate):
    """Schleuse, deren Uhr mit dem eingehenden Audio läuft: 20 ms pro Frame."""

    def __init__(self, detector, uhr, **kwargs):
        super().__init__(detector, clock=lambda: uhr.now, **kwargs)
        self._uhr = uhr
        self.forwarded: list[int] = []

    async def process_frame(self, frame, direction):
        if isinstance(frame, InputAudioRawFrame):
            self._uhr.now += FRAME_SECS
        await super().process_frame(frame, direction)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, InputAudioRawFrame):
            self.forwarded.append(int.from_bytes(frame.audio[:2], "little"))
        await super().push_frame(frame, direction)


def run_gate(to_send, *, hit_at=None, start_secs=0.0, open_secs=10.0, **kwargs):
    detector = FakeDetector(hit_at, start_secs)
    gate = GateUnderTest(detector, SimpleNamespace(now=0.0), open_secs=open_secs, **kwargs)
    events = []

    @gate.event_handler("on_wake")
    async def on_wake(gate, phrase):
        events.append(("wake", phrase, len(gate.forwarded)))

    @gate.event_handler("on_sleep")
    async def on_sleep(gate):
        events.append(("sleep", len(gate.forwarded)))

    down, _ = asyncio.run(run_test(gate, frames_to_send=to_send))
    angekommen = [int.from_bytes(f.audio[:2], "little") for f in down if isinstance(f, InputAudioRawFrame)]
    assert angekommen == gate.forwarded
    return gate.forwarded, events, detector


def test_im_ruhezustand_geht_kein_audio_durch():
    forwarded, events, detector = run_gate(frames(0, 10))

    assert forwarded == []
    assert events == []
    assert detector.calls == 10


def test_wachwort_reicht_audio_ab_dem_wort_nach():
    # Erkannt beim 10. Frame; das Wort begann bei 0,11 s, mit 0,04 s Vorlauf also ab 0,07 s.
    # Frame i endet bei (i + 1) * 0,02 s, Frame 3 ist damit der erste nach 0,07 s.
    forwarded, events, detector = run_gate(
        frames(0, 20), hit_at=10, start_secs=0.11, lead_secs=0.04
    )

    assert forwarded == list(range(3, 20))
    assert detector.resets == 1
    assert detector.calls == 10, "nach dem Aufwachen hört der lokale Erkenner nicht mehr mit"


def test_on_wake_laeuft_bevor_audio_weitergeht():
    _, events, _ = run_gate(frames(0, 10), hit_at=5)

    assert events == [("wake", "alfred", 0)]


def test_puffer_ist_begrenzt():
    # Das Wort liegt 0,4 s zurück, der Puffer hält nur 0,11 s: Frames, die nach 0,29 s enden.
    forwarded, _, _ = run_gate(frames(0, 20), hit_at=20, start_secs=0.0, buffer_secs=0.11)

    assert forwarded == list(range(14, 20))


def test_schleuse_schliesst_nach_stille():
    # Wach nach Frame 4 (Uhr 0,10 s). Bei 0,09 s Offenzeit ist Frame 9 (Uhr 0,20 s) der erste zu späte.
    forwarded, events, detector = run_gate(frames(0, 15), hit_at=5, open_secs=0.09)

    assert forwarded == list(range(0, 9))
    assert events == [("wake", "alfred", 0), ("sleep", 9)]
    assert detector.calls == 5 + 6, "nach dem Schließen hört der lokale Erkenner wieder mit"


@pytest.mark.parametrize(
    ("started", "stopped"),
    [
        (UserStartedSpeakingFrame, UserStoppedSpeakingFrame),
        (BotStartedSpeakingFrame, BotStoppedSpeakingFrame),
    ],
)
def test_sprechen_haelt_die_schleuse_offen(started, stopped):
    to_send = [
        *frames(0, 5),  # Wachwort beim 5. Frame
        started(),
        *frames(5, 25),  # 0,4 s Sprechen, weit über der Offenzeit
        stopped(),
        *frames(25, 35),  # ab hier läuft die Offenzeit von 0,09 s: vier Frames passen
    ]
    forwarded, events, _ = run_gate(to_send, hit_at=5, open_secs=0.09)

    assert forwarded == list(range(0, 29))
    assert [e[0] for e in events] == ["wake", "sleep"]


def test_falsche_abtastrate_macht_die_schleuse_unbrauchbar():
    # Pipecat fängt Fehler aus setup() ab und markiert den Processor als unbrauchbar.
    # Der Raum-Modus bricht dann ab (ProcessorUnusablePolicy.CANCEL in pipeline.py).
    gate = GateUnderTest(FakeDetector(None, 0.0), SimpleNamespace(now=0.0), open_secs=1.0)
    asyncio.run(
        run_test(
            gate,
            frames_to_send=frames(0, 3),
            pipeline_params=PipelineParams(audio_in_sample_rate=8000),
        )
    )

    assert not gate.is_usable
    assert gate.forwarded == []


# --- Echter Erkenner gegen synthetisch gesprochene Sätze ---------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
MODEL = "vosk-model-small-de-0.15"
needs_vosk = pytest.mark.skipif(
    importlib.util.find_spec("vosk") is None or not (Path.home() / ".cache" / "vosk" / MODEL).exists(),
    reason="Vosk oder das Sprachmodell fehlt; der erste Start im Raum-Modus lädt es herunter",
)


FRAME_BYTES = SAMPLES_PER_FRAME * 2


def recording(name: str) -> bytes:
    """Eine Aufnahme als PCM, gefolgt von 1,5 s Stille."""
    with wave.open(str(FIXTURES / f"{name}.wav"), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (16000, 1, 2)
        return wf.readframes(wf.getnframes()) + b"\x00\x00" * 24000


def speak(detector: VoskWakeWordDetector, name: str) -> WakeWordHit | None:
    """Spielt eine Aufnahme in 20-ms-Stücken ein."""
    pcm = recording(name)
    for i in range(0, len(pcm), FRAME_BYTES):
        hit = detector.process(pcm[i : i + FRAME_BYTES])
        if hit:
            return hit
    return None


@pytest.fixture(scope="module")
def detector() -> VoskWakeWordDetector:
    return VoskWakeWordDetector(["alfred"], MODEL)


@needs_vosk
@pytest.mark.parametrize("name", ["wach_frage", "wach_mitte"])
def test_vosk_erkennt_das_wachwort(detector, name):
    detector.reset()
    hit = speak(detector, name)

    assert hit is not None and hit.phrase == "alfred"
    assert 0 <= hit.start_secs < 3


@needs_vosk
@pytest.mark.parametrize("name", ["neg_wetter", "neg_aehnlich"])
def test_vosk_loest_bei_anderen_saetzen_nicht_aus(detector, name):
    detector.reset()
    assert speak(detector, name) is None


@needs_vosk
def test_vosk_zeiten_beginnen_nach_reset_bei_null(detector):
    detector.reset()
    assert speak(detector, "neg_wetter") is None
    detector.reset()
    hit = speak(detector, "wach_frage")

    assert hit is not None and hit.start_secs < 1.5


@needs_vosk
def test_vosk_lehnt_unbekannte_woerter_ab():
    with pytest.raises(ValueError, match="gibtesnichtwort"):
        VoskWakeWordDetector(["alfred", "gibtesnichtwort"], MODEL)


@needs_vosk
def test_schleuse_mit_echtem_erkenner_laesst_nur_den_satz_mit_wachwort_durch(detector):
    detector.reset()
    davor = recording("neg_wetter")
    strom = davor + recording("wach_frage")
    to_send = [
        InputAudioRawFrame(audio=strom[i : i + FRAME_BYTES], sample_rate=16000, num_channels=1)
        for i in range(0, len(strom) - FRAME_BYTES + 1, FRAME_BYTES)
    ]

    down, _ = asyncio.run(run_test(WakeWordGate(detector, open_secs=60), frames_to_send=to_send))
    durchgelassen = [f for f in down if isinstance(f, InputAudioRawFrame)]

    erster = next(i for i, f in enumerate(to_send) if f is durchgelassen[0])
    satzanfang = len(davor) // FRAME_BYTES
    # Höchstens 0,5 s vor dem Satz mit dem Wachwort, und nichts vom Satz davor.
    assert satzanfang - 25 <= erster <= satzanfang + 25
    assert all(a is b for a, b in zip(durchgelassen, to_send[erster:], strict=True))


def test_wachwort_in_fremder_stimme_oeffnet_nicht():
    gepruefte = []

    def pruefung(audio: bytes) -> bool:
        gepruefte.append(len(audio))
        return False

    forwarded, events, detector = run_gate(frames(0, 60), hit_at=20, start_secs=0.2, stimmpruefung=pruefung)
    assert forwarded == [] and events == [] and gepruefte and detector.resets >= 1


def test_wachwort_in_eigener_stimme_oeffnet():
    forwarded, events, detector = run_gate(frames(0, 60), hit_at=20, start_secs=0.2, stimmpruefung=lambda audio: True)
    assert forwarded and events and events[0][0] == "wake"


def offene_schleuse(gespraech_secs=120.0):
    """Eine geweckte Schleuse mit Uhr zum Vorstellen: zehn Sekunden frei, dann nur Alexander."""
    uhr = SimpleNamespace(now=0.0)
    gate = WakeWordGate(FakeDetector(None, 0.0), open_secs=10.0, gespraech_secs=gespraech_secs, clock=lambda: uhr.now)
    asyncio.run(gate.aufwecken("alfred"))
    return gate, uhr


def test_nach_der_freien_zeit_zaehlt_nur_noch_alexander():
    gate, uhr = offene_schleuse()
    uhr.now = 9.0
    assert not gate.nur_eigene_stimme and not gate._idle_expired()
    uhr.now = 11.0
    assert gate.nur_eigene_stimme and not gate._idle_expired(), "offen, aber nur für Alexander"

    # Fremdes Sprechen (Fernseher) hält die Schleuse nicht offen.
    gate._track_activity(UserStartedSpeakingFrame())
    uhr.now = 12.0
    gate._track_activity(UserStoppedSpeakingFrame())
    uhr.now = 119.0
    assert gate.nur_eigene_stimme and not gate._idle_expired()
    uhr.now = 121.0
    assert gate._idle_expired()


def test_angenommener_satz_und_alfreds_antwort_setzen_die_zeit_zurueck():
    gate, uhr = offene_schleuse()
    uhr.now = 50.0
    gate.angenommen()
    uhr.now = 55.0
    assert not gate.nur_eigene_stimme, "nach einem Satz in Alexanders Stimme zählt wieder jede"

    uhr.now = 70.0
    gate._track_activity(BotStartedSpeakingFrame())
    assert not gate.nur_eigene_stimme and not gate._idle_expired()
    uhr.now = 75.0
    gate._track_activity(BotStoppedSpeakingFrame())
    uhr.now = 84.0
    assert not gate.nur_eigene_stimme
    uhr.now = 86.0
    assert gate.nur_eigene_stimme


def test_ein_angefangener_satz_schiebt_das_ende_nur_kurz_auf():
    gate, uhr = offene_schleuse()
    uhr.now = 100.0
    gate._track_activity(UserStartedSpeakingFrame())
    uhr.now = 125.0
    assert not gate._idle_expired(), "wer gerade spricht, wird nicht abgeschnitten"
    uhr.now = 151.0
    assert gate._idle_expired(), "ein Fernseher hält sie nicht ewig offen"


def test_ohne_gespraechszeit_wie_bisher():
    gate, uhr = offene_schleuse(gespraech_secs=0.0)
    uhr.now = 5.0
    gate._track_activity(UserStartedSpeakingFrame())
    uhr.now = 30.0
    assert not gate.nur_eigene_stimme and not gate._idle_expired(), "wer spricht, hält sie offen"
    gate._track_activity(UserStoppedSpeakingFrame())
    uhr.now = 41.0
    assert gate._idle_expired()


def test_gespraech_beenden_schliesst_beim_naechsten_audio():
    gate, uhr = offene_schleuse()
    gate._track_activity(BotStartedSpeakingFrame())
    gate.nach_antwort_schlafen()
    assert gate._idle_expired(), "auch während Alfred sich verabschiedet; seine Antwort läuft weiter"
    asyncio.run(gate.aufwecken("alfred"))
    assert not gate._idle_expired()


def schleuse_in_bereitschaft(wartet=lambda: False):
    """Eine Schleuse in Bereitschaft, gerade durch das Wachwort geöffnet."""
    uhr = SimpleNamespace(now=0.0)
    gate = WakeWordGate(
        FakeDetector(None, 0.0), open_secs=10.0, gespraech_secs=120.0, clock=lambda: uhr.now,
        bereitschaft=True, bereitschaft_secs=3.0, rueckfrage_secs=8.0,
    )
    gate.wartet_auf_antwort_setzen(wartet)
    asyncio.run(gate.aufwecken("alfred"))
    return gate, uhr


def alfred_antwortet(gate, uhr, von: float, bis: float):
    uhr.now = von
    gate._track_activity(BotStartedSpeakingFrame())
    uhr.now = bis
    gate._track_activity(BotStoppedSpeakingFrame())


def test_bereitschaft_schliesst_kurz_nach_der_antwort():
    gate, uhr = schleuse_in_bereitschaft()
    # Die Frage nach dem Wachwort zählt frei, auch nach einer kurzen Pause.
    assert not gate.nur_eigene_stimme
    alfred_antwortet(gate, uhr, 4.0, 6.0)
    assert gate.nur_eigene_stimme, "nach der Antwort kein Gespräch ohne Wachwort"
    uhr.now = 8.5
    assert not gate._idle_expired(), "kurz Zeit für ein Danke"
    uhr.now = 9.5
    assert gate._idle_expired()


def test_bereitschaft_wartet_auf_die_antwort_einer_rueckfrage():
    wartet = SimpleNamespace(ja=True)
    gate, uhr = schleuse_in_bereitschaft(lambda: wartet.ja)
    alfred_antwortet(gate, uhr, 2.0, 4.0)  # "Welche Nachricht soll ich schicken?"
    uhr.now = 11.0
    assert not gate._idle_expired()
    uhr.now = 12.5
    assert gate._idle_expired()
    wartet.ja = False


def test_bereitschaft_fernseher_haelt_die_schleuse_nicht_offen():
    gate, uhr = schleuse_in_bereitschaft()
    alfred_antwortet(gate, uhr, 2.0, 4.0)
    uhr.now = 5.0
    gate._track_activity(UserStartedSpeakingFrame())  # der Fernseher redet
    uhr.now = 15.0
    assert not gate._idle_expired(), "ein angefangener Satz wird nicht abgeschnitten"
    uhr.now = 17.5
    assert gate._idle_expired(), "aber nicht lange gehört"


def test_bereitschaft_angenommener_satz_verlaengert():
    gate, uhr = schleuse_in_bereitschaft()
    alfred_antwortet(gate, uhr, 2.0, 4.0)
    uhr.now = 6.0
    gate.angenommen()  # Alexanders "Und morgen?"
    uhr.now = 8.5
    assert not gate._idle_expired()


def test_bereitschaft_laesst_sich_umschalten():
    gate, uhr = schleuse_in_bereitschaft()
    gate.bereitschaft = False
    alfred_antwortet(gate, uhr, 2.0, 4.0)
    uhr.now = 13.0
    assert not gate.nur_eigene_stimme and not gate._idle_expired(), "wieder zehn Sekunden frei"


def test_erstes_transkript_nur_nach_dem_wachwort():
    gate = GateUnderTest(FakeDetector(5, 0.0), SimpleNamespace(now=0.0), open_secs=10.0)
    asyncio.run(run_test(gate, frames_to_send=frames(0, 10)))
    assert gate.erstes_transkript_offen, "der Stimmfilter prüft das erste Transkript nach dem Wachwort"
    asyncio.run(gate.aufwecken("Ereignis"))
    assert not gate.erstes_transkript_offen, "aufwecken durch ein Ereignis zählt nicht"
