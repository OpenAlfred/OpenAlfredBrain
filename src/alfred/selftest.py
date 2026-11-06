"""Selbsttest ohne Mikrofon und Lautsprecher.

Spielt gesprochene Testsätze in den Sprach-Loop und misst, wie lange Alfred bis zum ersten
Ton braucht. Prüft damit Schlüssel, Modelle, Stimme und Netzwerk in einem Durchgang, auch auf
einem Rechner ohne Audiogeräte.

Start:  python -m alfred.selftest [aufnahme.wav ...] [--nachhall SEK] [--raum] [--bereitschaft] [--leerlauf SEK]

Mehrere Aufnahmen sind ein Gespräch: Jede folgende beginnt eine Sekunde, nachdem Alfred die
vorige Antwort beendet hat. So lassen sich Rückfragen prüfen, etwa Auftrag, Nachfrage, Ja.

--nachhall  Nach der letzten Antwort so lange offen bleiben, um zu hören, was Alfred von sich
            aus sagt (fällige Erinnerung, Ergebnis des Denkers).
--raum      Die Kette des Raum-Modus: Der erste Satz muss die Wachwort-Schleuse öffnen.
--bereitschaft  Mit --raum: die Schleuse in Bereitschaft (Standby); sie muss kurz nach der Antwort schließen.
--leerlauf  So viele Sekunden Stille vor dem ersten Satz. Prüft, ob Alfred nach langer Ruhe
            noch antwortet, etwa weil die Transkription ihre Verbindung neu aufbauen muss.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
import wave
from datetime import datetime
from array import array
from pathlib import Path

from dotenv import load_dotenv
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.runner import WorkerRunner
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from alfred.config import CONFIG_DIR, load_settings
from alfred.persona import load_persona
from alfred.pipeline import build_voice_loop, require_api_key, verbinde_schleuse

TESTSATZ = CONFIG_DIR.parent / "tests" / "fixtures" / "wach_frage.wav"
FRAME_SECS = 0.02
STILLE_SCHWELLE = 500  # Amplitude, unter der ein Frame als Stille zählt
ZEITLIMIT_SECS = 20.0
ANTWORT_RUHE_SECS = 1.0  # so lange nichts vom Sprecher, dann gilt die Antwort als beendet


class _Ausgang(FrameProcessor):
    """Steht anstelle des Lautsprechers und merkt sich, wann Ton ankommt.

    Ohne echten Lautsprecher gibt es keine BotStoppedSpeakingFrames. Das Ende einer Antwort
    ergibt sich deshalb aus dem Modell und der Sprachausgabe: keine laufende Antwort, kein
    laufendes Werkzeug (eine Websuche dauert Sekunden, in denen Alfred nur "Moment" sagt),
    kein laufender Ton, und eine Sekunde lang nichts Neues. TTSStoppedFrame allein reicht
    nicht, denn der kommt nach jedem Satz, und bei Werkzeugaufrufen antwortet das Modell in
    Etappen.
    """

    def __init__(self):
        super().__init__()
        self.erster_ton: float | None = None
        self.audio_secs = 0.0
        self._antwort_laeuft = False
        self._ton_laeuft = False
        self._werkzeuge_laufen = 0
        self._zuletzt = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        jetzt = time.perf_counter()
        if isinstance(frame, TTSAudioRawFrame):
            if self.erster_ton is None:
                self.erster_ton = jetzt
            self.audio_secs += frame.num_frames / frame.sample_rate
            self._zuletzt = jetzt
        elif isinstance(frame, (TTSStartedFrame, TTSStoppedFrame)):
            self._ton_laeuft = isinstance(frame, TTSStartedFrame)
            self._zuletzt = jetzt
            # Wie ein echter Lautsprecher melden, wann Alfred spricht: Schleuse, Bereitschaft und
            # Unterbrechungsregeln richten sich danach.
            gemeldet = BotStartedSpeakingFrame if self._ton_laeuft else BotStoppedSpeakingFrame
            for richtung in (FrameDirection.UPSTREAM, FrameDirection.DOWNSTREAM):
                await self.push_frame(gemeldet(), richtung)
        elif isinstance(frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame)):
            self._antwort_laeuft = isinstance(frame, LLMFullResponseStartFrame)
            self._zuletzt = jetzt
        elif isinstance(frame, (FunctionCallInProgressFrame, FunctionCallResultFrame)):
            self._werkzeuge_laufen += 1 if isinstance(frame, FunctionCallInProgressFrame) else -1
            self._zuletzt = jetzt
        await self.push_frame(frame, direction)

    @property
    def zuletzt(self) -> float:
        """Zeitpunkt der letzten Regung des Sprechers (Ton, Modell, Werkzeug)."""
        return self._zuletzt

    def antwort_fertig(self) -> bool:
        return (
            self.erster_ton is not None
            and not self._antwort_laeuft
            and not self._ton_laeuft
            and self._werkzeuge_laufen <= 0
            and time.perf_counter() - self._zuletzt > ANTWORT_RUHE_SECS
        )


class _Durchreicher(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _StummerTransport:
    """Transport ohne Geräte: Audio kommt aus einer Datei, die Antwort wird nur vermessen."""

    def __init__(self):
        self._eingang = _Durchreicher()
        self.ausgang = _Ausgang()

    def input(self) -> FrameProcessor:
        return self._eingang

    def output(self) -> FrameProcessor:
        return self.ausgang


def _lade_testsatz(path: Path) -> tuple[list[bytes], int]:
    """Die Aufnahme in 20-ms-Stücken und der Index des letzten Stücks mit Sprache."""
    with wave.open(str(path), "rb") as wf:
        if (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{path} muss 16 kHz, mono, 16 Bit sein")
        pcm = wf.readframes(wf.getnframes())
    step = int(16000 * FRAME_SECS) * 2
    stuecke = [pcm[i : i + step] for i in range(0, len(pcm) - step + 1, step)]
    laut = [i for i, s in enumerate(stuecke) if max(abs(x) for x in array("h", s)) > STILLE_SCHWELLE]
    return stuecke, laut[-1]


async def run_selftest(
    paths: list[Path] | None = None,
    nachhall_secs: float = 0.0,
    *,
    raum: bool = False,
    leerlauf_secs: float = 0.0,
    bereitschaft: bool = False,
) -> list[float]:
    """Führt ein Gespräch aus einer Aufnahme je Gesprächszug und gibt die Antwortzeiten zurück.

    Mit nachhall_secs bleibt die Sitzung danach so lange offen, damit auch zu hören ist, was
    Alfred von sich aus sagt, etwa eine fällige Erinnerung oder ein Ergebnis des Denkers.
    Mit raum sitzt die Wachwort-Schleuse vor der Transkription wie im Raum-Modus; die Sitzung
    bleibt dann offen, bis die Schleuse wieder geschlossen hat. leerlauf_secs Stille gehen
    dem ersten Satz voraus.
    """
    settings, persona = load_settings(), load_persona()
    # Eigener, bei jedem Lauf frischer Gedächtnisordner: Testsätze landen nicht im echten
    # Gedächtnis, und das Protokoll des vorigen Laufs färbt nicht auf die Antworten ab.
    testordner = settings.pfad(settings.gedaechtnis.ordner) / "selbsttest"
    shutil.rmtree(testordner, ignore_errors=True)
    settings.gedaechtnis.ordner = str(testordner)
    saetze = [_lade_testsatz(p) for p in (paths or [TESTSATZ])]
    transport = _StummerTransport()

    gate = None
    schleuse: dict[str, float] = {}
    if raum:
        from alfred.wakeword import VoskWakeWordDetector, WakeWordGate

        gate = WakeWordGate(
            VoskWakeWordDetector(settings.wachwort.phrasen, settings.wachwort.modell),
            open_secs=settings.wachwort.offen_sekunden,
            bereitschaft=bereitschaft,
            bereitschaft_secs=settings.wachwort.bereitschaft_nachlauf_sekunden,
            rueckfrage_secs=settings.wachwort.rueckfrage_sekunden,
        )
        # Die Schleuse schließt erst nach offen_sekunden Ruhe; so lange zuhören, damit auch
        # das Einschlafen samt Protokoll im Test vorkommt.
        nachhall_secs = max(nachhall_secs, settings.wachwort.offen_sekunden + 3.0)

    sitzung = await build_voice_loop(
        transport, settings, persona, gate=gate, always_on=raum  # type: ignore[arg-type]
    )
    worker, context = sitzung.worker, sitzung.context

    if gate:
        verbinde_schleuse(sitzung, gate)

        @gate.event_handler("on_wake")
        async def on_wake(gate, grund):
            schleuse["offen"] = time.perf_counter()

        @gate.event_handler("on_sleep")
        async def on_sleep(gate):
            schleuse["zu"] = time.perf_counter()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    lauf = asyncio.create_task(runner.run())
    await asyncio.sleep(2.0)  # Verbindungsaufbau zur Transkription abwarten

    ausgang = transport.ausgang
    stille = b"\x00" * len(saetze[0][0][0])
    offen = list(saetze)  # noch nicht gesendete Sätze
    aktuell: list[bytes] | None = None  # der Satz, der gerade läuft
    pos = letztes_wort = 0
    sprechende = 0.0
    antwort_erwartet = False
    antwortzeiten: list[float] = []
    start = time.perf_counter()
    i = 0
    # Wie ein Mikrofon: in Echtzeit senden. Erst Stille, dann ein Satz, dann Stille, bis die
    # Antwort fertig ist; nach einer Sekunde Luft der nächste Satz.
    naechster_ab = leerlauf_secs  # Sekunden ab Start, ab denen der nächste Satz beginnen darf
    limit = leerlauf_secs + ZEITLIMIT_SECS
    while (jetzt := time.perf_counter() - start) < limit:
        # Eine Aufnahme läuft immer bis zum Ende, auch wenn Alfred schon in einer Denkpause
        # geantwortet hat; sonst fehlte der Rest des Satzes (Zuhören, 6.10.2026).
        if aktuell is None and ausgang.antwort_fertig():
            if antwort_erwartet:
                # Kam die Antwort schon vor dem letzten Wort (Satz zu früh als beendet
                # erkannt), wird die Antwortzeit negativ; ohne Sprachende gibt es keine.
                antwortzeiten.append(ausgang.erster_ton - sprechende if sprechende else float("nan"))
                antwort_erwartet = False
            ausgang.erster_ton = None
            if offen:
                naechster_ab, limit = jetzt + 1.0, jetzt + 1.0 + ZEITLIMIT_SECS
            elif nachhall_secs > 0:
                limit, nachhall_secs = jetzt + nachhall_secs, 0.0
            else:
                break
        if gate and "zu" in schleuse:
            break
        # Solange der Sprecher arbeitet (Werkzeuge, Modell, Ton), läuft die Frist mit; sie
        # endet erst nach ZEITLIMIT_SECS ohne jede Regung.
        if ausgang.zuletzt:
            limit = max(limit, ausgang.zuletzt - start + ZEITLIMIT_SECS)
        if aktuell is None and offen and jetzt >= naechster_ab:
            aktuell, letztes_wort = offen.pop(0)
            pos, antwort_erwartet = 0, True
            # Der nächste Satz erst, wenn die Antwort auf diesen fertig ist.
            naechster_ab, limit = float("inf"), jetzt + ZEITLIMIT_SECS
        if aktuell is not None:
            audio = aktuell[pos]
            if pos == letztes_wort:
                sprechende = time.perf_counter()
            pos += 1
            if pos == len(aktuell):
                aktuell = None
        else:
            audio = stille
        await worker.queue_frame(InputAudioRawFrame(audio=audio, sample_rate=16000, num_channels=1))
        i += 1
        await asyncio.sleep(max(0.0, start + i * FRAME_SECS - time.perf_counter()))

    await runner.cancel()
    await lauf
    await sitzung.abschliessen()

    if gate and "offen" not in schleuse:
        raise SystemExit("Selbsttest fehlgeschlagen: Die Wachwort-Schleuse hat nicht geöffnet.")
    if not antwortzeiten:
        grund = "Die Antwort wurde nicht fertig" if ausgang.erster_ton else "Es kam keine Sprachausgabe"
        raise SystemExit(f"Selbsttest fehlgeschlagen: {grund}. Details im Log oben.")

    werkzeuge = [t["function"]["name"] for m in context.messages for t in (m.get("tool_calls") or []) if isinstance(t, dict)]
    for m in context.messages:
        if m.get("role") in ("user", "assistant", "developer") and isinstance(m.get("content"), str):
            print(f"{m['role']:11}: {m['content']}")
    print(f"Werkzeuge:   {', '.join(werkzeuge) or 'keine'}")
    if gate:
        erster_satz = start + leerlauf_secs
        zu = f", geschlossen nach {schleuse['zu'] - erster_satz:.1f} s" if "zu" in schleuse else ", nicht geschlossen"
        print(f"Schleuse:    offen {schleuse['offen'] - erster_satz:.2f} s nach Satzbeginn{zu}")
    if len(antwortzeiten) < len(saetze):
        print(f"Antworten:   nur {len(antwortzeiten)} von {len(saetze)} Sätzen beantwortet")
    print(f"Antwortzeit: {', '.join(f'{t:.2f}' for t in antwortzeiten)} s vom letzten Wort bis zum ersten Ton")
    protokoll = sitzung.ressourcen.gedaechtnis.protokoll_heute(datetime.now())
    print(f"Protokoll:   {protokoll.splitlines()[-1] if protokoll else 'nichts geschrieben'}")
    print(f"Ton:         {transport.ausgang.audio_secs:.1f} s Sprachausgabe erhalten")
    return antwortzeiten


def main() -> None:
    parser = argparse.ArgumentParser(description="Selbsttest des Sprach-Loops ohne Audiogeräte")
    parser.add_argument(
        "aufnahmen", nargs="*", type=Path, default=[TESTSATZ],
        help="WAV-Dateien, 16 kHz mono, ein Gesprächszug je Datei",
    )
    parser.add_argument("--nachhall", type=float, default=0.0, metavar="SEK", help="nach der letzten Antwort offen bleiben")
    parser.add_argument("--raum", action="store_true", help="mit Wachwort-Schleuse wie im Raum-Modus")
    parser.add_argument("--leerlauf", type=float, default=0.0, metavar="SEK", help="Stille vor dem ersten Satz")
    parser.add_argument("--bereitschaft", action="store_true", help="mit --raum: Schleuse in Bereitschaft (Standby)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    load_dotenv(override=True)
    require_api_key()
    asyncio.run(
        run_selftest(args.aufnahmen, args.nachhall, raum=args.raum, leerlauf_secs=args.leerlauf, bereitschaft=args.bereitschaft)
    )


if __name__ == "__main__":
    main()
