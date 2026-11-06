"""Gesprächsverhalten: wann Alfred sich unterbrechen lässt und wann Alexander fertig ist."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Iterable

from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from alfred.config import Gespraech

# Antworten auf eine Rückfrage ("Soll ich den Termin anlegen?"). Solange eine offen ist,
# zählt jedes davon sofort, auch einzeln und während Alfred noch spricht.
ANTWORTWOERTER = frozenset(
    "ja jo jep jup okay ok genau klar passt mach nein nee nö nicht lass abbrechen".split()
)

# Damit sagt Alexander, dass er noch nicht fertig ist; ab da hört Alfred zu. Im Gespräch vom
# 6.10.2026: "Lasst mich bitte kurz noch fertig sprechen", "Bruder, ich war mitten am Reden".
AUSREDEN = re.compile(
    r"\b(lass|lasst|lässt) mich (doch |bitte |mal |kurz |einfach |noch |erst |jetzt )*"
    r"(aus ?reden|aus ?sprechen|fertig (sprechen|reden|erzählen)|zu ende (sprechen|reden|erzählen))"
    r"|\bich (war|bin) (noch |gerade )*(mitten (am|beim|im) \w+|nicht fertig)"
    r"|\bhör (mir )?(doch |mal |einfach |kurz |bitte |jetzt )*zu\b"
    r"|\b(unterbrich|unterbrech) mich nicht"
    r"|\bfall mir nicht (dauernd |ständig |immer )?ins wort",
    re.IGNORECASE,
)

# Damit endet das Zuhören sofort, wenn es am Ende eines Satzes steht.
ENDE = re.compile(
    r"\b(fertig|ende|das war'?s|das war es|so,? jetzt du|was (meinst|sagst|denkst|hältst) du( dazu| davon)?|"
    r"deine meinung)\W*$",
    re.IGNORECASE,
)


class Zuhoeren:
    """Zuhör-Modus: Alfred antwortet erst nach einer langen Pause oder auf "fertig".

    Für Erzählungen mit Denkpausen und für Diktate. Smart Turn beendet einen Zug sonst nach
    höchstens `pause_max_sekunden` Stille, und ein Satz, der fertig klingt, endet sofort; wer
    nachdenkt, wird unterbrochen. Der Modus gilt für den laufenden oder nächsten Gesprächszug;
    endet der, hört Alfred wieder wie sonst. Eingeschaltet vom Werkzeug zuhoeren, beim Diktieren
    einer Nachricht und von Sätzen wie "Lass mich ausreden" (AUSREDEN).
    """

    def __init__(self, pause_secs: float):
        self.pause_secs = pause_secs
        self.aktiv = False

    def an(self, grund: str) -> None:
        if not self.aktiv:
            logger.info(f"Zuhören an ({grund}): Antwort erst nach {self.pause_secs:.0f} s Stille")
        self.aktiv = True

    def aus(self) -> None:
        if self.aktiv:
            logger.info("Zuhören aus")
        self.aktiv = False


class StopWordUserTurnStartStrategy(MinWordsUserTurnStartStrategy):
    """Unterbricht ab einer Mindestwortzahl, bei einem Stoppwort aber sofort.

    Solange Alfred spricht, zählen kurze Einwürfe wie "mhm" oder "ja okay" nicht als
    Unterbrechung. "Stopp" oder "Warte" müssen trotzdem beim ersten Wort wirken, und wartet
    Alfred gerade auf ein Ja oder Nein, auch die Antwort: Sie kommt meist, bevor seine Frage
    ganz verklungen ist, und ging sonst mit der Mindestwortzahl verloren.
    """

    def __init__(
        self,
        *,
        min_words: int,
        stop_words: Iterable[str],
        antwort_erwartet: Callable[[], bool] | None = None,
        antwortwoerter: Iterable[str] = ANTWORTWOERTER,
        **kwargs,
    ):
        super().__init__(min_words=min_words, **kwargs)
        self._stop_words = {wort.casefold() for wort in stop_words}
        self._antwort_erwartet = antwort_erwartet or (lambda: False)
        self._antwortwoerter = {wort.casefold() for wort in antwortwoerter}

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if self._bot_speaking and isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            woerter = set(re.findall(r"\w+", frame.text.casefold()))
            if self._stop_words & woerter or (
                self._antwort_erwartet() and self._antwortwoerter & woerter
            ):
                await self.trigger_user_turn_started()
                return ProcessFrameResult.STOP
        return await super().process_frame(frame)


class GeduldigeTurnErkennung(TurnAnalyzerUserTurnStopStrategy):
    """Smart Turn wie gehabt; im Zuhör-Modus endet der Zug erst nach langer Stille.

    Meldet Smart Turn das Ende, während Alfred zuhört, wartet die Strategie, bis Alexander
    `Zuhoeren.pause_secs` still ist; spricht er vorher weiter, verfällt die Meldung, und der
    Zug läuft weiter. "Fertig" oder "Was meinst du?" am Satzende beenden ihn sofort. Der
    Zug bleibt dabei offen: Für Pipeline, Schleuse und Zusteller spricht Alexander noch, und
    niemand fällt ihm in die Denkpause.
    """

    # Hat Alexander seit dem Einschalten weniger Wörter als das gesagt (außer "Lass mich ausreden"
    # selbst), hat er nur angekündigt; dann bleibt das Zuhören für den nächsten Zug an. Live am
    # 6.10.2026: Nach "Lass mich bitte ausreden." und vier Sekunden Stille antwortete das Modell
    # "Ich halte mich zurück" und fiel ihm beim nächsten Satz doch wieder in die Denkpause.
    MIN_INHALT_WOERTER = 3

    def __init__(self, *, zuhoeren: Zuhoeren | None = None, clock: Callable[[], float] = time.monotonic, **kwargs):
        super().__init__(**kwargs)
        self._zuhoeren = zuhoeren
        self._clock = clock
        self._still_seit: float | None = None  # wann Alexander zuletzt aufgehört hat zu sprechen
        self._frist: asyncio.Task | None = None
        self._ende_gehoert = False
        self._inhalt = 0  # Wörter seit dem Einschalten, ohne die Ankündigung selbst

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._still_seit = None
            await self._frist_abbrechen()  # er spricht weiter: kein Ende
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._still_seit = self._clock() - frame.stop_secs
        elif isinstance(frame, InterimTranscriptionFrame) and frame.text.strip():
            # Neue Wörter, während die Frist läuft: Er spricht wieder, auch wenn die VAD es noch
            # nicht gemeldet hat (live knapp verpasst, 6.10.2026). Nach dem nächsten endgültigen
            # Transkript meldet Smart Turn erneut, und die Frist beginnt wieder ab der Stille.
            await self._frist_abbrechen()
        elif isinstance(frame, TranscriptionFrame) and self._zuhoeren is not None:
            # Vor Smart Turn auswerten: Das Transkript kann den Zug gleich beenden.
            woerter = len(re.findall(r"\w+", frame.text))
            if treffer := AUSREDEN.search(frame.text):
                self._zuhoeren.an(f"„{frame.text.strip()}“")
                self._inhalt += max(0, woerter - len(re.findall(r"\w+", treffer.group(0))) - 2)
            elif self._zuhoeren.aktiv:
                self._inhalt += woerter
                if ENDE.search(frame.text):
                    self._ende_gehoert = True
        return await super().process_frame(frame)

    async def trigger_user_turn_stopped(self, **kwargs):
        if self._zuhoeren is None or not self._zuhoeren.aktiv or self._ende_gehoert:
            await self._beenden(**kwargs)
            return
        still_seit = self._still_seit if self._still_seit is not None else self._clock()
        rest = self._zuhoeren.pause_secs - (self._clock() - still_seit)
        if rest <= 0:
            await self._beenden(**kwargs)
            return
        await self._frist_abbrechen()
        self._frist = self.task_manager.create_task(self._nach_frist(rest, kwargs), f"{self}::zuhoeren")

    async def _nach_frist(self, rest: float, kwargs: dict) -> None:
        await asyncio.sleep(rest)
        self._frist = None  # vor dem Beenden: das Zugende räumt die Frist sonst mitten in sich selbst ab
        await self._beenden(**kwargs)

    async def _beenden(self, **kwargs) -> None:
        self._zuhoeren_beenden()
        await super().trigger_user_turn_stopped(**kwargs)

    def _zuhoeren_beenden(self) -> None:
        """Am Zugende: Zuhören aus, außer Alexander hat bisher nur angekündigt, dass er erzählen will."""
        self._ende_gehoert = False
        if self._zuhoeren is not None and self._zuhoeren.aktiv and self._inhalt >= self.MIN_INHALT_WOERTER:
            self._zuhoeren.aus()
        self._inhalt = 0

    async def _frist_abbrechen(self) -> None:
        if self._frist is not None:
            frist, self._frist = self._frist, None
            await self.task_manager.cancel_task(frist)

    async def handle_user_turn_started(self):
        await self._frist_abbrechen()
        self._ende_gehoert = False
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self):
        # Auch ein Zugende von anderswo (Pipecats Wächter nach Stille) beendet das Zuhören.
        await self._frist_abbrechen()
        self._zuhoeren_beenden()
        await super().handle_user_turn_stopped()

    async def cleanup(self):
        await self._frist_abbrechen()
        await super().cleanup()


def build_turn_strategies(
    gespraech: Gespraech,
    antwort_erwartet: Callable[[], bool] | None = None,
    zuhoeren: Zuhoeren | None = None,
) -> UserTurnStrategies:
    """Start-Strategie aus der Konfiguration; das Turn-Ende erkennt Pipecats Smart Turn.

    Smart Turn hört, ob ein Satz fertig klingt. Klingt er unfertig, wartet es bis zu
    `pause_max_sekunden` Stille, bevor es den Zug trotzdem beendet. Pipecats Vorgabe sind drei
    Sekunden; das war die Pause, die Alexander im Gespräch als Hänger erlebt hat. Im
    Zuhör-Modus wartet die Strategie länger (GeduldigeTurnErkennung).

    Args:
        gespraech: Abschnitt `gespraech` der Konfiguration.
        antwort_erwartet: Sagt, ob gerade eine Rückfrage auf Ja oder Nein wartet.
        zuhoeren: Der Zuhör-Modus der Sitzung; ohne ihn endet jeder Zug wie gewohnt.
    """
    return UserTurnStrategies(
        start=[
            StopWordUserTurnStartStrategy(
                min_words=gespraech.unterbrechen_ab_woertern,
                stop_words=gespraech.stoppwoerter,
                antwort_erwartet=antwort_erwartet,
            )
        ],
        stop=[
            GeduldigeTurnErkennung(
                turn_analyzer=LocalSmartTurnAnalyzerV3(
                    params=SmartTurnParams(stop_secs=gespraech.pause_max_sekunden)
                ),
                zuhoeren=zuhoeren,
            )
        ],
    )
