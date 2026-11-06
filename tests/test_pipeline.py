"""Gesprächslogik an der Wachwort-Schleuse: Protokoll, frischer Systemprompt, neues Gespräch."""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import LLMUpdateSettingsFrame
from pipecat.pipeline.worker import ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.base_transport import BaseTransport

from alfred.config import load_settings
from alfred.erinnerungen import Erinnerungen
from alfred.gedaechtnis import Gedaechtnis
from alfred.mitschrift import Mitschrift
from alfred.persona import load_persona
from alfred import pipeline
from alfred.pipeline import Sitzung, build_voice_loop, letzte_antwort_ist_frage, verbinde_schleuse
from alfred.resources import Ressourcen
from alfred.wakeword import WakeWordGate, WakeWordHit


class StummerDetector:
    def process(self, audio: bytes) -> WakeWordHit | None:
        return None

    def reset(self) -> None:
        pass


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


def szenario(tmp_path, *, pause_minuten: float, fremder_inhalt: bool = False, mitschrift=None):
    """Ein Gespräch, Einschlafen, Pause, Aufwachen. Gibt Kontext und Worker zurück."""
    settings = load_settings()
    settings.gespraech.vergessen_nach_minuten = 15
    res = Ressourcen(
        settings=settings,
        persona=load_persona(),
        client=None,
        gedaechtnis=Gedaechtnis(tmp_path),
        erinnerungen=Erinnerungen(tmp_path / "erinnerungen.json"),
        denker=None,
    )
    if fremder_inhalt:
        res.fremden_inhalt_merken("Websuche")
    res.mitschrift = mitschrift
    context = LLMContext()
    context.add_message({"role": "user", "content": "Hallo Alfred"})
    context.add_message({"role": "assistant", "content": "Hallo."})
    worker = FakeWorker()
    geruht = []

    async def ruhen():
        geruht.append(True)

    # Beide Nachrichten gelten als protokolliert; so braucht das Einschlafen kein Mistral.
    sitzung = Sitzung(worker, context, res, [], _protokolliert_bis=2, ruhen=ruhen)
    sitzung.geruht = geruht
    gate = WakeWordGate(StummerDetector(), open_secs=10)
    uhr = SimpleNamespace(now=1000.0)
    verbinde_schleuse(sitzung, gate, clock=lambda: uhr.now)

    async def ablauf():
        await gate._call_event_handler("on_sleep")
        uhr.now += pause_minuten * 60
        await gate.aufwecken("alfred")

    asyncio.run(ablauf())
    return sitzung, worker


def test_kurze_pause_behaelt_das_gespraech(tmp_path):
    sitzung, worker = szenario(tmp_path, pause_minuten=5)
    assert len(sitzung.context.messages) == 2
    assert sitzung._protokolliert_bis == 2


def test_die_jabra_led_erfaehrt_schlaf_und_aufwachen(tmp_path):
    """room.py hängt die LED an die Mitschrift; die Schleuse legt sie schlafen und weckt sie."""
    mitschrift = Mitschrift(tmp_path / "mitschrift.json", "Raum buero")
    gemeldet = []
    mitschrift.bei_zustand.append(gemeldet.append)
    szenario(tmp_path, pause_minuten=5, mitschrift=mitschrift)
    assert gemeldet == ["schlaeft", "bereit"]


def test_einschlafen_legt_die_spracherkennung_still(tmp_path):
    # Sonst bricht Mistral die stille Verbindung nach dreißig Sekunden mit einem Fehler ab.
    sitzung, worker = szenario(tmp_path, pause_minuten=5)
    assert sitzung.geruht == [True]


def test_lange_pause_beginnt_neues_gespraech(tmp_path):
    sitzung, worker = szenario(tmp_path, pause_minuten=16)
    assert sitzung.context.messages == []
    # Sonst übersähe das nächste Protokoll die ersten Nachrichten des neuen Gesprächs.
    assert sitzung._protokolliert_bis == 0


def sitzung_mit_gespraech(tmp_path):
    gedaechtnis = Gedaechtnis(tmp_path)
    res = Ressourcen(
        settings=load_settings(), persona=load_persona(), client=None, gedaechtnis=gedaechtnis,
        erinnerungen=Erinnerungen(tmp_path / "erinnerungen.json"), denker=None,
    )
    context = LLMContext()
    context.add_message({"role": "user", "content": "Hallo Alfred"})
    context.add_message({"role": "assistant", "content": "Hallo."})
    return Sitzung(FakeWorker(), context, res, []), gedaechtnis


def test_abgebrochene_zusammenfassung_wird_beim_abschliessen_nachgeholt(tmp_path, monkeypatch):
    """Beim Beenden bricht der Runner das Einschlafen ab, während die Zusammenfassung noch läuft.

    Vorher rückte der Zähler schon vor dem Aufruf vor, und abschliessen() fand nichts mehr: Das
    letzte Gespräch fehlte im Tagesprotokoll (Selbsttest --raum auf dem Mini-PC, 5.10.2026).
    """
    sitzung, gedaechtnis = sitzung_mit_gespraech(tmp_path)
    aufrufe = []

    async def zusammenfassen(client, modell, messages):
        aufrufe.append(len(messages))
        if len(aufrufe) == 1:
            await asyncio.sleep(10)  # hier kommt der Abbruch
        return "Begrüßung."

    monkeypatch.setattr(pipeline, "zusammenfassen", zusammenfassen)

    async def ablauf():
        einschlafen = asyncio.create_task(sitzung.protokollieren())
        await asyncio.sleep(0.01)
        einschlafen.cancel()
        with pytest.raises(asyncio.CancelledError):
            await einschlafen
        await sitzung.abschliessen()

    asyncio.run(ablauf())
    assert aufrufe == [2, 2] and sitzung._protokolliert_bis == 2
    assert "Begrüßung." in gedaechtnis.protokoll_heute(datetime.now())


def test_tagesprotokoll_schreibt_das_kleine_modell(tmp_path, monkeypatch):
    """GLM 5.3 als Sprecher dachte die 200 Token des Protokolls voll und schrieb nichts (7. bis 9.10.2026)."""
    sitzung, gedaechtnis = sitzung_mit_gespraech(tmp_path)
    sitzung.ressourcen.settings.modelle.sprecher = "zai-glm-5-3"
    modelle = []

    async def zusammenfassen(client, modell, messages):
        modelle.append(modell)
        return "Begrüßung."

    monkeypatch.setattr(pipeline, "zusammenfassen", zusammenfassen)
    asyncio.run(sitzung.protokollieren())
    assert modelle == [sitzung.ressourcen.settings.modelle.werkzeuge] and modelle != ["zai-glm-5-3"]


def test_gescheiterte_zusammenfassung_wird_nicht_wiederholt(tmp_path, monkeypatch):
    sitzung, gedaechtnis = sitzung_mit_gespraech(tmp_path)
    aufrufe = []

    async def zusammenfassen(client, modell, messages):
        aufrufe.append(len(messages))
        raise RuntimeError("Mistral nicht erreichbar")

    monkeypatch.setattr(pipeline, "zusammenfassen", zusammenfassen)

    async def ablauf():
        await sitzung.protokollieren()
        await sitzung.abschliessen()

    asyncio.run(ablauf())
    assert aufrufe == [2] and sitzung._protokolliert_bis == 2, "ein Fehler kostet das Protokoll, nicht den Betrieb"
    assert gedaechtnis.protokoll_heute(datetime.now()) == ""


def test_aufwachen_frischt_den_systemprompt_auf(tmp_path):
    sitzung, worker = szenario(tmp_path, pause_minuten=5)
    frames = [f for f in worker.frames if isinstance(f, LLMUpdateSettingsFrame)]
    assert len(frames) == 1
    assert "Alfred" in frames[0].delta.system_instruction
    assert "Uhr" in frames[0].delta.system_instruction


class LeererTransport(BaseTransport):
    """Transport ohne Gerät: Eingang und Ausgang sind leere Processors."""

    def __init__(self):
        super().__init__()
        self._eingang = FrameProcessor(name="Eingang")
        self._ausgang = FrameProcessor(name="Ausgang")

    def input(self) -> FrameProcessor:
        return self._eingang

    def output(self) -> FrameProcessor:
        return self._ausgang


def bausteine(pipeline):
    """Alle Processors einer Pipeline in Reihenfolge, auch in verschachtelten."""
    for p in pipeline.processors:
        yield p
        yield from bausteine(p)


@pytest.mark.parametrize("always_on", [False, True], ids=["browser", "raum"])
def test_sprachschleife_laesst_sich_ohne_anbindungen_aufbauen(tmp_path, monkeypatch, always_on):
    """Alle Bausteine von build_voice_loop entstehen ohne Netz und ohne Anbindungen.

    Ein verwaister Name darin fiel bislang erst im Browser auf: Die WebRTC-Verbindung
    stand, aber die Pipeline startete nie, und Alfred antwortete nicht.
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "nur-fuer-den-aufbau")
    for name in ("MAIL_ADRESSE", "MAIL_PASSWORT"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    settings.gedaechtnis.ordner = str(tmp_path / "gedaechtnis")
    # Token-Dateien zeigen ins Leere: kein Kalender, kein WhatsApp, kein Spotify, keine Stimmprüfung.
    settings.kalender.token = str(tmp_path / "google_token.json")
    settings.whatsapp.token = str(tmp_path / "whatsapp.token")
    settings.spotify.token = str(tmp_path / "spotify_token.json")
    # Ein angelerntes Profil allein schaltet die Stimmprüfung nicht ein (stimmerkennung.aktiv, 20.10.2026).
    settings.stimmerkennung.profil = str(tmp_path / "stimme.json")
    (tmp_path / "stimme.json").write_text(json.dumps({"vektor": [1.0, 0.0], "modell": settings.stimmerkennung.modell}))
    settings.stimmerkennung.aktiv = False
    # Im Raum mit Codewort, im Browser ohne: Der Codewortschutz gehört direkt hinter die Transkription.
    if always_on:
        monkeypatch.setenv("ALFRED_CODEWORT", "Zitronenfalter")
    else:
        monkeypatch.delenv("ALFRED_CODEWORT", raising=False)

    async def ablauf():
        warteschlange = asyncio.Queue()
        await warteschlange.put("Hinweis vom System, nicht vom Gegenüber: aus dem letzten Anlauf.")
        sitzung = await build_voice_loop(
            LeererTransport(), settings, load_persona(), always_on=always_on, ereignisse=warteschlange
        )
        try:
            # Im Raum bricht ein unbrauchbarer Baustein den Anlauf ab, etwa ein toter Audio-Stream
            # (local_audio.py); die Aufsicht startet dann neu. Im Browser läuft es weiter.
            erwartet = ProcessorUnusablePolicy.CANCEL if always_on else ProcessorUnusablePolicy.CONTINUE
            assert sitzung.worker._processor_unusable_policy is erwartet
            res = sitzung.ressourcen
            assert res.ereignisse is warteschlange, "im Raum hält die Aufsicht sie über den Neuaufbau"
            assert res.kalender is None and res.whatsapp is None and res.stimmpruefer is None
            # Ohne Kalender kein Terminerinnerer.
            assert {task.get_name() for task in sitzung.hintergrund} == {
                "Erinnerungen", "Freigaben", "Routinen", "Zusteller", "Geburtstage", "Mitschrift", "Aufträge"
            }
            assert res.mitschrift is not None
            assert res.schleuse is None and sitzung.ruhen is not None, "Browser: keine Schleuse, kein gespraech_beenden"
            # Die Jabra-LED gehört dem Raum-Modus; Tests und Selbsttest schalten das Gerät nicht.
            assert sitzung.jabra is None
            assert all(not task.done() for task in sitzung.hintergrund)
            assert sitzung.context.tools
            # Zuhören gibt es im Sprach-Loop, und die Schleuse fragt, ob Alfred auf eine Antwort wartet.
            assert res.zuhoeren is not None and not res.zuhoeren.aktiv
            assert sitzung.wartet_auf_antwort() is False
            res.zuhoeren.an("Test")
            assert sitzung.wartet_auf_antwort() is True
            # Erst verwirft ein Nein die offene Rückfrage, dann legt „Nein, lass das" die Regel-Rückfrage an.
            namen = [type(p).__name__ for p in bausteine(sitzung.worker.pipeline)]
            assert namen.index("Rueckfragewaechter") < namen.index("Ansagewaechter") < namen.index("LLMUserAggregator")
            if always_on:
                assert res.codewort is not None
                assert namen.index("Codewortschutz") == namen.index("FinalizingMistralSTTService") + 1
                assert namen.index("Codewortschutz") < namen.index("Sprachfilter") < namen.index("Stimmfilter")
            else:
                assert res.codewort is None and "Codewortschutz" not in namen
            await asyncio.sleep(0.2)
        finally:
            await sitzung.abschliessen()
        # Die Pipeline lief nie: Der Zusteller hat nichts herausgenommen, der nächste Anlauf spricht es.
        assert warteschlange.qsize() == 1

    asyncio.run(ablauf())


class Durchreicher(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class Mikrofon(Durchreicher):
    """Öffnet das Gerät beim Einrichten wie Pipecats LocalAudioInputTransport."""

    def __init__(self, fehler: Exception | None):
        super().__init__(name="Mikrofon")
        self._fehler = fehler

    async def setup(self, setup):
        await super().setup(setup)
        if self._fehler is not None:
            raise self._fehler


@pytest.mark.parametrize("scheitert", [False, True])
def test_bereit_erst_wenn_mikrofon_und_lautsprecher_offen_sind(scheitert):
    """16.10.2026: Das Mikrofon ging nach dem Hochfahren mit PortAudio -9999 nicht auf.

    Der StartFrame kam trotzdem durch die Pipeline, erst danach brach der Worker ab; der Zusteller
    sprach vier Ereignisse hinein. Bereit ist die Pipeline deshalb nur mit brauchbaren Transporten.
    """
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import WorkerRunner
    from pipecat.pipeline.worker import PipelineWorker, ProcessorUnusablePolicy

    async def ablauf():
        mikrofon = Mikrofon(OSError(-9999, "Unanticipated host error") if scheitert else None)
        lautsprecher = Durchreicher(name="Lautsprecher")
        worker = PipelineWorker(
            Pipeline([mikrofon, Durchreicher(), lautsprecher]),
            idle_timeout_secs=None,
            processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
        )
        gestartet = []

        @worker.event_handler("on_pipeline_started")
        async def on_pipeline_started(worker, frame):
            gestartet.append(True)

        bereit = pipeline.pipeline_bereit(worker, mikrofon, lautsprecher)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(worker)
        lauf = asyncio.create_task(runner.run())
        try:
            if scheitert:
                await asyncio.wait_for(asyncio.shield(lauf), 10)  # der Worker bricht von selbst ab
            else:
                await asyncio.wait_for(bereit.wait(), 10)
        finally:
            await worker.cancel()
            await asyncio.wait_for(lauf, 10)
        await asyncio.sleep(0)  # Pipecat ruft Ereignis-Handler als eigene Aufgabe
        return gestartet, bereit.is_set()

    gestartet, bereit = asyncio.run(ablauf())
    assert gestartet == [True], "on_pipeline_started allein unterscheidet die Fälle nicht"
    assert bereit is not scheitert


def test_letzte_antwort_ist_frage():
    frage = [{"role": "user", "content": "Schreib Jan."}, {"role": "assistant", "content": "Was soll ich Jan schreiben?"}]
    assert letzte_antwort_ist_frage(frage)
    assert letzte_antwort_ist_frage([*frage[:1], {"role": "assistant", "content": "Was soll ich [hier unterbrochen]"}]) is False
    assert not letzte_antwort_ist_frage([*frage, {"role": "user", "content": "Hi"}]), "schon beantwortet"
    assert not letzte_antwort_ist_frage([{"role": "assistant", "content": "Erledigt."}])
    assert not letzte_antwort_ist_frage([])


def test_lange_pause_vergisst_den_fremden_inhalt(tmp_path):
    # Mit dem Verlauf geht auch der fremde Text; schreibende Werkzeuge laufen wieder frei.
    sitzung, _ = szenario(tmp_path, pause_minuten=16, fremder_inhalt=True)
    assert sitzung.ressourcen.fremder_inhalt is None


def test_kurze_pause_behaelt_den_fremden_inhalt(tmp_path):
    sitzung, _ = szenario(tmp_path, pause_minuten=5, fremder_inhalt=True)
    assert sitzung.ressourcen.fremder_inhalt is not None and sitzung.ressourcen.fremder_inhalt.herkunft == "Websuche"


def test_extrahiere_sprechtext_filtert_thinking_chunks():
    from alfred.pipeline import _extrahiere_sprechtext

    # Reine Strings bleiben erhalten
    assert _extrahiere_sprechtext("Hallo Welt") == "Hallo Welt"

    # Thinking-Chunks von Reasoning-Modellen wie zai-glm-5-3 werden verworfen
    thinking_chunk = [{"type": "thinking", "thinking": [{"type": "text", "text": "Reasoning..."}], "closed": True}]
    assert _extrahiere_sprechtext(thinking_chunk) == ""

    # Text-Chunks werden sauber extrahiert
    text_chunk = [{"type": "text", "text": "Hallo Alexander!"}]
    assert _extrahiere_sprechtext(text_chunk) == "Hallo Alexander!"

    # Gemischte Chunks behalten nur den Text
    gemischt = [
        {"type": "thinking", "thinking": "Internal CoT"},
        {"type": "text", "text": "Guten Morgen."},
    ]
    assert _extrahiere_sprechtext(gemischt) == "Guten Morgen."


def test_alfred_mistral_llm_service_schluckt_thinking_frames():
    from alfred.pipeline import AlfredMistralLLMService

    gepusht = []

    class DummyService(AlfredMistralLLMService):
        def __init__(self):
            # Nicht super().__init__ aufrufen, da kein API-Key benötigt wird
            self._filter_incomplete_user_turns = False

        @property
        def reports_ttfat(self):
            return False

        async def push_frame(self, frame, direction=None):
            gepusht.append(frame)

    service = DummyService()

    # Thinking-Token wird nicht als Frame gepusht
    thinking = [{"type": "thinking", "thinking": "Denke nach..."}]
    asyncio.run(service._push_llm_text(thinking))
    assert len(gepusht) == 0

    # Text-Token wird gepusht
    text = [{"type": "text", "text": "Antwort"}]
    asyncio.run(service._push_llm_text(text))
    assert len(gepusht) == 1
    assert gepusht[0].text == "Antwort"

