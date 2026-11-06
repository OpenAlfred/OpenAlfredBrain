import asyncio
from types import SimpleNamespace

import pytest
from pipecat.audio.turn.base_turn_analyzer import BaseTurnAnalyzer, BaseTurnParams, EndOfTurnState
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.utils.asyncio.task_manager import TaskManager

from alfred.config import load_settings
from alfred.turns import (
    AUSREDEN,
    ENDE,
    GeduldigeTurnErkennung,
    StopWordUserTurnStartStrategy,
    Zuhoeren,
    build_turn_strategies,
)


def run(
    bot_spricht: bool, *texte: str, interim: bool = False, rueckfrage_offen: bool = False
) -> list[bool]:
    """Füttert die Strategie mit Transkripten und meldet je Text, ob er einen Turn startet."""

    async def ablauf() -> list[bool]:
        strategie = StopWordUserTurnStartStrategy(
            min_words=3, stop_words=["Stopp", "warte"], antwort_erwartet=lambda: rueckfrage_offen
        )
        gestartet = []

        @strategie.event_handler("on_user_turn_started")
        async def on_user_turn_started(strategie, params):
            gestartet.append(params.enable_interruptions)

        frame_cls = InterimTranscriptionFrame if interim else TranscriptionFrame
        ergebnisse = []
        for text in texte:
            # Ein gestarteter Turn setzt den Zustand zurück, deshalb vor jedem Text neu setzen.
            await strategie.process_frame(
                BotStartedSpeakingFrame() if bot_spricht else BotStoppedSpeakingFrame()
            )
            vorher = len(gestartet)
            ergebnis = await strategie.process_frame(frame_cls(text, "nutzer", "2026-10-02T10:00:00"))
            ergebnisse.append(len(gestartet) > vorher)
            assert (ergebnis is ProcessFrameResult.STOP) == ergebnisse[-1]
            if ergebnisse[-1]:
                await strategie.handle_user_turn_started()
        assert all(gestartet), "eine Unterbrechung muss den Bot auch wirklich stoppen"
        return ergebnisse

    return asyncio.run(ablauf())


def test_kurze_einwuerfe_unterbrechen_nicht():
    assert run(True, "mhm", "ja okay", "Genau.") == [False, False, False]


def test_ab_drei_woertern_wird_unterbrochen():
    assert run(True, "das sehe ich anders") == [True]


def test_stoppwort_unterbricht_sofort():
    assert run(True, "Stopp!", "ähm, warte", "STOPP") == [True, True, True]


def test_stoppwort_wirkt_schon_im_zwischentranskript():
    assert run(True, "warte", interim=True) == [True]


def test_stoppwort_muss_als_ganzes_wort_fallen():
    assert run(True, "erwartet", "Wartezeit") == [False, False]


def test_wenn_alfred_schweigt_startet_jedes_wort_einen_turn():
    assert run(False, "ja") == [True]


def test_antwort_auf_offene_rueckfrage_zaehlt_sofort():
    # "Soll ich den Termin anlegen?" – das Ja kommt, bevor die Frage verklungen ist.
    assert run(True, "Ja.", "Nein", "nee lass", rueckfrage_offen=True) == [True, True, True]


def test_antwort_wirkt_schon_im_zwischentranskript():
    assert run(True, "ja", interim=True, rueckfrage_offen=True) == [True]


def test_ohne_offene_rueckfrage_bleibt_ein_ja_ein_einwurf():
    assert run(True, "ja", "nein", rueckfrage_offen=False) == [False, False]


def test_waehrend_rueckfrage_zaehlt_nur_eine_antwort_als_einwurf():
    # Ein "mhm" während der Rückfrage ist keine Antwort.
    assert run(True, "mhm", "aha", rueckfrage_offen=True) == [False, False]


def test_mitgelieferte_konfiguration_ist_gueltig():
    strategien = build_turn_strategies(load_settings().gespraech)
    assert [type(s) for s in strategien.start] == [StopWordUserTurnStartStrategy]
    assert [type(s) for s in strategien.stop] == [GeduldigeTurnErkennung]


# Sätze aus dem Gespräch vom 6.10.2026 und naheliegende Varianten.
@pytest.mark.parametrize(
    "satz",
    ["Ich nutze einmal Lasst mich bitte kurz noch fertig sprechen.", "Bruder, ich war Mitten am Reden.",
     "Lass mich ausreden!", "Warte, ich bin noch nicht fertig.", "Hör mir einfach mal zu.", "Unterbrich mich nicht."],
)  # fmt: skip
def test_ausreden_saetze_schalten_das_zuhoeren_an(satz):
    assert AUSREDEN.search(satz)


@pytest.mark.parametrize("satz", ["Hörst du mir zu?", "Du kannst nicht warten, bis ich fertig bin.", "Lass mich überlegen, was ich esse."])
def test_andere_saetze_schalten_es_nicht_an(satz):
    assert not AUSREDEN.search(satz)


@pytest.mark.parametrize("satz", ["Fertig.", "So, das war's.", "Und das war es.", "Was meinst du dazu?", "So, jetzt du!"])
def test_ende_saetze_beenden_das_zuhoeren(satz):
    assert ENDE.search(satz)


@pytest.mark.parametrize("satz", ["Ich muss das heute fertig machen und", "Was meinst du, soll ich das morgen machen oder"])
def test_ende_nur_am_satzende(satz):
    assert not ENDE.search(satz)


class FakeAnalyzer(BaseTurnAnalyzer):
    """Steht für Smart Turn: Die Tests sagen selbst, wann es das Ende meldet."""

    @property
    def speech_triggered(self) -> bool:
        return False

    @property
    def params(self):
        return BaseTurnParams()

    def append_audio(self, buffer: bytes, is_speech: bool) -> EndOfTurnState:
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self):
        return EndOfTurnState.INCOMPLETE, None

    def update_vad_start_secs(self, vad_start_secs: float):
        pass

    def clear(self):
        pass


class Zugende:
    """Die Strategie mit Ersatz-Analyzer, dazu die Zugenden, die sie meldet."""

    def __init__(self, zuhoeren: Zuhoeren):
        self.strategie = GeduldigeTurnErkennung(turn_analyzer=FakeAnalyzer(), zuhoeren=zuhoeren)
        self.enden = 0

        @self.strategie.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(strategie, params):
            self.enden += 1

    async def starten(self):
        manager = TaskManager()
        await self.strategie.setup(SimpleNamespace(task_manager=manager, audio_in_sample_rate=16000))
        await self.strategie.handle_user_turn_started()

    async def satz(self, text: str, smart_turn_meldet_ende: bool = True):
        """Alexander sagt einen Satz und hört auf; Smart Turn meldet danach das Ende."""
        await self.strategie.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        await self.strategie.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.0))
        await self.strategie.process_frame(TranscriptionFrame(text, "nutzer", "2026-10-06T14:23:00", finalized=True))
        if smart_turn_meldet_ende:
            await self.strategie.trigger_user_turn_stopped()


def test_ohne_zuhoeren_endet_der_zug_sofort():
    async def ablauf():
        zug = Zugende(Zuhoeren(pause_secs=5.0))
        await zug.starten()
        await zug.satz("Also das Ding ist, ich habe gerade mies viel zu tun, okay?")
        assert zug.enden == 1

    asyncio.run(ablauf())


def test_beim_zuhoeren_endet_der_zug_erst_nach_der_pause():
    async def ablauf():
        zuhoeren = Zuhoeren(pause_secs=0.3)
        zug = Zugende(zuhoeren)
        await zug.starten()
        zuhoeren.an("Test")
        await zug.satz("Also ich werde heute für die Uni einiges machen müssen.")
        await asyncio.sleep(0.15)
        assert zug.enden == 0, "Denkpause: noch nicht antworten"
        # Er spricht weiter, bevor die Pause um ist: Die Meldung verfällt.
        await zug.satz("Einmal Programmieren zwei, die Aufgaben kann ich schon abarbeiten.")
        await asyncio.sleep(0.15)
        assert zug.enden == 0
        await asyncio.sleep(0.3)
        assert zug.enden == 1 and not zuhoeren.aktiv, "nach der Pause endet der Zug, und das Zuhören auch"

    asyncio.run(ablauf())


def test_sprechen_vor_ablauf_der_frist_haelt_den_zug_offen():
    async def ablauf():
        zuhoeren = Zuhoeren(pause_secs=0.3)
        zug = Zugende(zuhoeren)
        await zug.starten()
        zuhoeren.an("Test")
        await zug.satz("Ich muss jetzt einmal")
        await zug.strategie.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        await asyncio.sleep(0.5)
        assert zug.enden == 0, "solange er spricht, endet nichts"

    asyncio.run(ablauf())


def test_fertig_beendet_das_zuhoeren_sofort():
    async def ablauf():
        zuhoeren = Zuhoeren(pause_secs=10.0)
        zug = Zugende(zuhoeren)
        await zug.starten()
        zuhoeren.an("Test")
        await zug.satz("Das war mein Tag. Was meinst du dazu?")
        assert zug.enden == 1 and not zuhoeren.aktiv

    asyncio.run(ablauf())


def test_lass_mich_ausreden_schaltet_mitten_im_zug_auf_zuhoeren():
    async def ablauf():
        zuhoeren = Zuhoeren(pause_secs=0.3)
        zug = Zugende(zuhoeren)
        await zug.starten()
        await zug.satz("Bruder, ich war mitten am Reden.")
        assert zuhoeren.aktiv and zug.enden == 0
        await asyncio.sleep(0.45)
        assert zug.enden == 1

    asyncio.run(ablauf())


def test_zugende_von_anderswo_beendet_das_zuhoeren():
    async def ablauf():
        zuhoeren = Zuhoeren(pause_secs=10.0)
        zug = Zugende(zuhoeren)
        await zug.starten()
        zuhoeren.an("Test")
        await zug.satz("Ich erzähl dir was.")
        await zug.strategie.handle_user_turn_stopped()  # etwa Pipecats Wächter nach langer Stille
        assert not zuhoeren.aktiv
        await zug.strategie.cleanup()

    asyncio.run(ablauf())


def test_nur_angekuendigt_bleibt_das_zuhoeren_fuer_den_naechsten_zug():
    # Live am 6.10.2026: "Lass mich bitte ausreden.", dann vier Sekunden Stille, dann die Erzählung.
    async def ablauf():
        zuhoeren = Zuhoeren(pause_secs=0.2)
        zug = Zugende(zuhoeren)
        await zug.starten()
        await zug.satz("Lass mich bitte ausreden.")
        await asyncio.sleep(0.35)
        assert zug.enden == 1 and zuhoeren.aktiv, "nur angekündigt: weiter zuhören"

        await zug.strategie.handle_user_turn_started()
        await zug.satz("Ich muss heute für die Uni Programmieren zwei machen.")
        await asyncio.sleep(0.1)
        assert zug.enden == 1, "die Denkpause unterbricht nicht"
        await asyncio.sleep(0.25)
        assert zug.enden == 2 and not zuhoeren.aktiv, "nach der Erzählung wieder wie sonst"

    asyncio.run(ablauf())


def test_neue_woerter_halten_die_frist_an_auch_ohne_vad():
    async def ablauf():
        zuhoeren = Zuhoeren(pause_secs=0.3)
        zug = Zugende(zuhoeren)
        await zug.starten()
        zuhoeren.an("Test")
        await zug.satz("Ich muss heute für die Uni Programmieren zwei machen.")
        await asyncio.sleep(0.15)
        await zug.strategie.process_frame(InterimTranscriptionFrame("und danach", "nutzer", "2026-10-06T19:24:37"))
        await asyncio.sleep(0.3)
        assert zug.enden == 0, "er spricht wieder: kein Ende, auch wenn die VAD es noch nicht gemeldet hat"

    asyncio.run(ablauf())
