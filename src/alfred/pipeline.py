"""Der Sprach-Loop, den Browser- und Raum-Modus gemeinsam nutzen."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from mistralai.client import Mistral
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMMessagesAppendFrame, LLMUpdateSettingsFrame, TTSSpeakFrame
from pipecat.observers.user_bot_latency_observer import MeasuredFrom, UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.mistral.llm import MistralLLMService
from pipecat.services.mistral.tts import MistralTTSService
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.empty_user_turn import EmptyUserTurnConfig
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

from alfred.aufgaben import Aufgaben
from alfred.auftraege import Auftraege, Auftragsbearbeiter
from alfred.behauptungen import hinweis as behauptung_hinweis
from alfred.behauptungen import fragt_nach_diktat, unbelegte_behauptung, zuhoer_versprechen
from alfred.benachrichtigung import Benachrichtiger
from alfred.config import Settings
from alfred.denker import Denker
from alfred.einkauf import Einkaufsliste
from alfred.ereignisse import (
    QUITTUNG_SECS,
    Ansagewaechter,
    Gespraechszustand,
    Tageszaehler,
    Zusteller,
    Zustellregeln,
    ist_gesperrt,
)
from alfred.codewort import Codewort, Codewortschutz
from alfred.erinnerungen import Erinnerungen
from alfred.freigaben import Freigabedatei
from alfred.ernaehrung import Ernaehrung, Schaetzer
from alfred.geburtstage import Geburtstagserinnerer
from alfred.gedaechtnis import Gedaechtnis, zusammenfassen
from alfred.haushalt import Ausgaben, Vorrat, Wochenplan
from alfred.jabra_led import JabraLedController
from alfred.kalender import Kalender, Terminerinnerer
from alfred.kosten import Kostenzaehler
from alfred.mail import Mail
from alfred.mitschrift import Mitschrift
from alfred.musik import aus_umgebung
from alfred.persona import Persona, build_system_prompt
from alfred.resources import Gesprochen, Ressourcen
from alfred.routinen import Routinen
from alfred.sprachfilter import Sprachfilter
from alfred.stimme import Stimmfilter, Stimmpruefer, aktives_profil, stimmerkenner
from alfred.stt import FinalizingMistralSTTService
from alfred.suche import Suchmaschine
from alfred.tts_cache import CachingMistralTTSService, SprechtextFilter
from alfred.turns import Zuhoeren, build_turn_strategies
from alfred.werkzeuge import Rueckfragewaechter, build_tools, faehigkeiten
from alfred.wetter import Wetter
from alfred.whatsapp import Kontakte, Meldeliste, WhatsApp, bruecke_starter, nachrichten_melden


# Werkzeuge, die mehrere Sekunden brauchen, und was Alfred solange sagt.
WARTE_ANSAGEN = {"websuche": "Moment, ich sehe nach."}

_UNTERBROCHEN_OHNE_TEXT = (
    "Hinweis vom System, nicht vom Gegenüber: Du wurdest unterbrochen, aber es wurde nichts "
    "Verständliches erkannt. Frag in einem kurzen Satz, was gemeint war. Wiederhole deine "
    "Antwort nicht; was im Verlauf steht, hat das Gegenüber gehört. Kein Werkzeugaufruf nötig."
)


def require_api_key() -> str:
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise SystemExit(
            "MISTRAL_API_KEY fehlt. Kopiere .env.example nach .env und trag den Schlüssel ein."
        )
    return api_key


def system_prompt(res: Ressourcen) -> str:
    jetzt = datetime.now().astimezone()
    wissen = "" if res.persona.gaeste_da else res.gedaechtnis.fuer_prompt(jetzt)
    return build_system_prompt(
        res.persona,
        faehigkeiten=faehigkeiten(res),
        jetzt=jetzt,
        wissen=wissen,
        zusatzregeln=res.gedaechtnis.regeln(),
    )


@dataclass
class Sitzung:
    """Ein laufender Sprach-Loop samt allem, was im Hintergrund dazugehört."""

    worker: PipelineWorker
    context: LLMContext
    ressourcen: Ressourcen
    hintergrund: list[asyncio.Task] = field(default_factory=list)
    _protokolliert_bis: int = 0
    # Schließt die Verbindung der Spracherkennung, solange die Wachwort-Schleuse zu ist.
    ruhen: Callable[[], Awaitable[None]] | None = None
    # Ob Alfred auf eine Antwort wartet oder noch arbeitet; die Schleuse bleibt in Bereitschaft dann offen.
    wartet_auf_antwort: Callable[[], bool] = lambda: False
    _protokoll_sperre: asyncio.Lock = field(default_factory=asyncio.Lock)
    # LED am Jabra, folgt dem Zustand der Mitschrift; nur im Raum-Modus (room.py setzt sie), damit
    # Selbsttest, Browser und Tests das Gerät nicht anfassen. Hier nur, damit abschliessen sie ausschaltet.
    jabra: JabraLedController | None = None
    # Spricht die Ereignisse; abschliessen legt Ungesprochenes zurück in die Warteschlange.
    zusteller: Zusteller | None = None

    async def protokollieren(self) -> None:
        """Schreibt das Gespräch seit dem letzten Mal ins Tagesprotokoll.

        Der Zähler rückt erst nach der Zusammenfassung vor: Bricht der Runner beim Beenden das
        Einschlafen ab, während sie noch läuft, holt der nächste Aufruf (abschliessen) dieselben
        Nachrichten nach. Die Sperre verhindert, dass zwei Aufrufe sie doppelt schreiben.
        """
        async with self._protokoll_sperre:
            bis = len(self.context.messages)
            neue = list(self.context.messages[self._protokolliert_bis : bis])
            if not neue:
                return
            res = self.ressourcen
            try:
                # Mit dem kleinen Modell ohne Nachdenken: GLM 5.3 als Sprecher verbrauchte die 200 Token
                # fürs Denken und lieferte keinen Text, das Tagesprotokoll blieb vom 7. bis 9.10.2026 leer.
                text = await zusammenfassen(res.client, res.settings.modelle.werkzeuge, neue)
            except Exception as e:  # Protokoll ist Komfort, kein Grund abzubrechen
                self._protokolliert_bis = bis  # nicht noch einmal an denselben Nachrichten scheitern
                logger.warning(f"Protokoll nicht geschrieben: {e}")
                return
            self._protokolliert_bis = bis
            if text:
                res.gedaechtnis.protokollieren(text, datetime.now().astimezone())
                logger.info(f"Protokoll: {text}")

    def neues_gespraech(self) -> None:
        """Verwirft den bisherigen Verlauf; das Protokoll zählt von vorn."""
        self.context.set_messages([])
        self._protokolliert_bis = 0
        # Mit dem Verlauf geht auch der fremde Text; schreibende Werkzeuge laufen wieder frei.
        self.ressourcen.fremden_inhalt_vergessen()

    async def abschliessen(self) -> None:
        for task in self.hintergrund:
            task.cancel()
        await asyncio.gather(*self.hintergrund, return_exceptions=True)
        if self.zusteller is not None:
            # Erst nach dem Abbruch, dann ist nichts mehr unterwegs; im Raum holt es der nächste Anlauf.
            self.zusteller.zurueckgeben()
        if self.jabra is not None:
            await self.jabra.close()
        await self.protokollieren()


def build_ressourcen(settings: Settings, persona: Persona) -> Ressourcen:
    """Alles, was Werkzeuge brauchen: Mistral-Client, Gedächtnis, Bestände, Anbindungen.

    Gemeinsam für Stimme (Raum, Browser) und App. Jede Anbindung ist nur aktiv, wenn sie
    eingerichtet ist; die Bestände laden nach, wenn ein anderer Prozess ihre Datei ändert.
    """
    api_key = require_api_key()
    client = Mistral(api_key=api_key)
    gedaechtnis = Gedaechtnis(settings.pfad(settings.gedaechtnis.ordner))
    kalender = None
    if settings.pfad(settings.kalender.token).exists():
        kalender = Kalender(settings.pfad(settings.kalender.token))
    # WhatsApp nur, wenn die Brücke eingerichtet ist (Token-Datei vorhanden); Push nur mit Server und Thema.
    whatsapp = WhatsApp(
        settings.whatsapp.url,
        settings.pfad(settings.whatsapp.token),
        starter=bruecke_starter(settings.pfad(".")) if settings.whatsapp.bruecke_starten else None,
    )
    benachrichtiger = Benachrichtiger(
        settings.benachrichtigung.url, settings.benachrichtigung.thema, settings.benachrichtigung.token
    )
    kosten = Kostenzaehler(gedaechtnis.ordner / "kosten.json", settings.preise)
    # Eigene Suchmaschine: kostenlose Suche für Sprecher und Denker; ohne sucht beides bei Mistral.
    suche = Suchmaschine(settings.suche.url) if settings.suche.url else None
    res = Ressourcen(
        settings=settings,
        persona=persona,
        client=client,
        gedaechtnis=gedaechtnis,
        erinnerungen=Erinnerungen(gedaechtnis.ordner / "erinnerungen.json"),
        denker=Denker(client, settings.modelle.denker, settings.modelle.werkzeuge, zaehler=kosten.websuche, suche=suche),
        suche=suche,
        kosten=kosten,
        kalender=kalender,
        routinen=Routinen(gedaechtnis.ordner / "routinen.yaml"),
        # Der gemerkte Wohnort aus dem Profil geht vor der Konfiguration.
        wetter=Wetter(settings.wetter.ort, wohnort=lambda: gedaechtnis.profil().get("wohnort", "")),
        whatsapp=whatsapp if whatsapp.eingerichtet else None,
        kontakte=Kontakte(gedaechtnis.ordner / "kontakte.yaml", settings.whatsapp.landesvorwahl),
        meldeliste=Meldeliste(gedaechtnis.ordner / "whatsapp_melden.yaml"),
        aufgaben=Aufgaben(gedaechtnis.ordner / "aufgaben.yaml"),
        auftraege=Auftraege(gedaechtnis.ordner / "auftraege.yaml", gedaechtnis.ordner / "berichte"),
        einkauf=Einkaufsliste(gedaechtnis.ordner / "einkaufsliste.yaml"),
        ernaehrung=Ernaehrung(gedaechtnis.ordner / "ernaehrung"),
        schaetzer=Schaetzer(client, settings.modelle.werkzeuge),
        ausgaben=Ausgaben(gedaechtnis.ordner / "ausgaben"),
        vorrat=Vorrat(gedaechtnis.ordner / "vorrat.yaml"),
        wochenplan=Wochenplan(gedaechtnis.ordner / "wochenplan.yaml", client, settings.modelle.werkzeuge),
        benachrichtiger=benachrichtiger if benachrichtiger.eingerichtet else None,
        # Mail nur mit Zugangsdaten in .env; das App-Passwort bleibt dort.
        mail=Mail(os.environ["MAIL_ADRESSE"], os.environ["MAIL_PASSWORT"])
        if os.getenv("MAIL_ADRESSE") and os.getenv("MAIL_PASSWORT")
        else None,
    )
    res.freigabedatei = Freigabedatei(gedaechtnis.ordner / "freigabe.json")
    # Werkzeuge wie modus_wechseln und regel_merken frischen damit den Systemprompt sofort auf.
    res.systemprompt = lambda: system_prompt(res)
    musik = aus_umgebung(settings.pfad(settings.spotify.token), settings.spotify.geraet)
    if musik and musik.eingerichtet:
        res.musik = musik
    logger.info(
        f"{persona.name} im Modus '{persona.aktiver_modus}' mit Stimme "
        f"'{settings.stimme}', Kalender {'an' if kalender else 'aus'}, "
        f"WhatsApp {'an' if res.whatsapp else 'aus'}, Mail {'an' if res.mail else 'aus'}, "
        f"Push {'an' if res.benachrichtiger else 'aus'}"
    )
    return res



def _extrahiere_sprechtext(inhalt: Any) -> str:
    """Extrahiert reinen Sprachtext und verwirft Thinking-Tokens (z. B. von zai-glm-5-3)."""
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        teile = []
        for stueck in inhalt:
            if isinstance(stueck, str):
                teile.append(stueck)
            elif isinstance(stueck, dict):
                if stueck.get("type") == "text" or ("text" in stueck and stueck.get("type") != "thinking"):
                    teile.append(str(stueck.get("text", "")))
            else:
                if getattr(stueck, "type", None) == "text" or (hasattr(stueck, "text") and getattr(stueck, "type", None) != "thinking"):
                    teile.append(str(getattr(stueck, "text", "")))
        return "".join(teile)
    if isinstance(inhalt, dict):
        if inhalt.get("type") == "text" or ("text" in inhalt and inhalt.get("type") != "thinking"):
            return str(inhalt.get("text", ""))
    return ""


class AlfredMistralLLMService(MistralLLMService):
    """Mistral-LLM-Dienst mit Filterung von Thinking-Tokens für Reasoning-Modelle (z. B. GLM 5.3)."""

    async def _push_llm_text(self, text: Any):
        bereinigt = _extrahiere_sprechtext(text)
        if bereinigt:
            await super()._push_llm_text(bereinigt)


async def build_voice_loop(
    transport: BaseTransport,
    settings: Settings,
    persona: Persona,
    *,
    gate: FrameProcessor | None = None,
    always_on: bool = False,
    quelle: str = "Browser",
    ereignisse: asyncio.Queue | None = None,
) -> Sitzung:
    """Baut Transkription, Sprachmodell, Werkzeuge und Sprachausgabe um einen Transport.

    Args:
        transport: Woher das Mikrofon-Audio kommt und wohin die Antwort geht.
        settings: Technische Einstellungen aus config/alfred.yaml.
        persona: Persönlichkeit aus config/persoenlichkeit.yaml.
        gate: Optionaler Processor direkt hinter dem Eingang, etwa die Wachwort-Schleuse.
        always_on: Dauerbetrieb. Der Worker beendet sich dann nicht nach fünf Minuten Stille,
            bricht aber ab, sobald ein Baustein dauerhaft ausfällt. Ein Dienst, der zuhört und
            nicht antworten kann, oder eine defekte Wachwort-Schleuse soll nicht weiterlaufen.
        quelle: Wie die App den Sprach-Prozess nennt, etwa "Raum buero".
        ereignisse: Warteschlange für Ereignisse, die länger lebt als dieser Sprach-Loop (im Raum
            hält room.run_room sie über Neustarts); ohne entsteht eine je Sprach-Loop.
    """
    api_key = require_api_key()
    res = build_ressourcen(settings, persona)
    if ereignisse is not None:
        res.ereignisse = ereignisse
    gedaechtnis, kalender = res.gedaechtnis, res.kalender
    res.mitschrift = Mitschrift(gedaechtnis.ordner / "mitschrift.json", quelle)
    # Vor build_tools: gespraech_beenden und bereitschaft gibt es nur mit Schleuse, zuhoeren nur hier.
    res.schleuse = gate
    res.zuhoeren = Zuhoeren(settings.gespraech.zuhoeren_pause_sekunden)
    # Heikle Aktionen (WhatsApp, Mail, Ende des Gästemodus) verlangen hier das Codewort statt eines Ja.
    res.codewort = Codewort.aus_umgebung(settings.wachwort.phrasen)

    stt = FinalizingMistralSTTService(
        api_key=api_key,
        settings=FinalizingMistralSTTService.Settings(model=settings.modelle.transkription),
    )
    llm = AlfredMistralLLMService(
        api_key=api_key,
        settings=MistralLLMService.Settings(
            model=settings.modelle.sprecher,
            system_instruction=system_prompt(res),
        ),
    )

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        # Langsame Werkzeuge ansagen, statt sekundenlang zu schweigen. Das Modell selbst
        # sagt vor einem Werkzeugaufruf meist nichts, auch wenn der Prompt darum bittet.
        namen = {f.function_name for f in function_calls}
        if ansage := next((WARTE_ANSAGEN[n] for n in WARTE_ANSAGEN if n in namen), None):
            await service.push_frame(TTSSpeakFrame(ansage))
    tts = CachingMistralTTSService(
        cache_ordner=settings.pfad("cache/phrasen"),
        api_key=api_key,
        settings=MistralTTSService.Settings(
            model=settings.modelle.sprachausgabe, voice=settings.stimme
        ),
        # Trotz Sprachregel setzt das Modell Titel gern in *Sternchen*; die sollen nicht in
        # die Sprachausgabe, einzelne Reste wie „_diskret“ und Emojis aus WhatsApp-Texten auch nicht.
        text_filters=[MarkdownTextFilter(), SprechtextFilter()],
        # Mistral brauchte gelegentlich bis zu fünfzehn Sekunden bis zum ersten Ton (dagegen
        # fragt CachingMistralTTSService nach zwei und nach weiteren drei Sekunden neu).
        # Pipecat gäbe den Satz nach dieser Frist auf; sie ist nur ein Sicherheitsnetz, ein
        # sauber beendeter Satz schließt seinen Kontext sofort.
        stop_frame_timeout_s=20.0,
    )

    context = LLMContext(tools=build_tools(res))
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=build_turn_strategies(
                settings.gespraech, antwort_erwartet=lambda: res.offene_freigabe is not None, zuhoeren=res.zuhoeren
            ),
            # Pipecats Wächter beendet einen Zug nach so viel Stille ohne jede Meldung; im Zuhör-Modus
            # muss die Denkpause darunter bleiben.
            user_turn_stop_timeout=max(5.0, settings.gespraech.zuhoeren_pause_sekunden + 1.5),
            # Unterbrechung ohne verständlichen Text (Geräusch, verworfene Fremdsprache):
            # Pipecats englische Vorgabe ließ das Modell die ganze Antwort von vorn beginnen.
            empty_user_turn=EmptyUserTurnConfig(interrupted_prompt=_UNTERBROCHEN_OHNE_TEXT),
        ),
    )

    zustand = Gespraechszustand()

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        if isinstance(message.content, str):
            res.mitschrift.gesagt(message.content, message.interrupted)
        # Im Verlauf steht nach einer Unterbrechung nur der angefangene Satz. Ohne Markierung
        # hält das Modell ihn für seine ganze Antwort und wiederholt sie beim nächsten Mal.
        if message.interrupted and message.content and context.messages:
            letzte = context.messages[-1]
            if letzte.get("role") == "assistant" and isinstance(letzte.get("content"), str):
                letzte["content"] = letzte["content"].rstrip() + " [hier unterbrochen]"
        # Behauptet das Modell eine Aktion ohne Werkzeug ("dann richte ich die Erinnerung ein"),
        # muss es nachbessern: Werkzeug aufrufen oder ehrlich sagen, dass es das nicht kann.
        antwort = zustand.modellantwort_abholen()
        if antwort and res.zuhoeren is not None and not res.zuhoeren.aktiv:
            # "Ich halte mich zurück" ohne zuhoeren: Das Versprechen hält der Code ein. Und fragt
            # Alfred nach dem Text einer Nachricht, darf das Diktat Pausen haben.
            if zuhoer_versprechen(antwort):
                res.zuhoeren.an("Versprechen im Gespräch")
            elif fragt_nach_diktat(antwort):
                res.zuhoeren.an("Diktat")
        if antwort and not message.interrupted and (satz := unbelegte_behauptung(antwort, context.messages)):
            logger.warning(f"Behauptung ohne Werkzeug, Alfred bessert nach: {satz!r}")
            await worker.queue_frames(
                [LLMMessagesAppendFrame(messages=[{"role": "developer", "content": behauptung_hinweis(satz)}], run_llm=True)]
            )

    # Stimmprüfung nur mit stimmerkennung.aktiv und angelerntem Profil; sie hängt vor der
    # Transkription, weil sie das rohe Audio des Gesprächszugs braucht.
    if (stimmprofil := aktives_profil(settings)) is not None:
        res.stimmpruefer = Stimmpruefer(
            stimmerkenner(settings.stimmerkennung.modell, settings.wachwort.modell),
            stimmprofil,
            settings.stimmerkennung.schwelle,
        )

    def erwartet_antwort() -> bool:
        """Alfred hat gefragt oder hört zu: Die Antwort zählt in Bereitschaft auch als ganzer Satz ohne Wachwort."""
        return (
            res.offene_freigabe is not None
            or (res.zuhoeren is not None and res.zuhoeren.aktiv)
            or letzte_antwort_ist_frage(context.messages)
        )

    # Nach einer Gesprächspause lässt er nur Sätze in Alexanders Stimme oder mit Wachwort durch,
    # ohne Stimmerkennung nur in Bereitschaft (kurze Sätze, Wachwort, Antworten auf Alfreds Frage);
    # ohne Schleuse (Browser) oder in der freien Zeit geht alles durch.
    stimmfilter = Stimmfilter(
        gate,
        res.stimmpruefer,
        settings.stimmerkennung.schwelle_wachwort,
        settings.wachwort.phrasen,
        bei_verworfen=res.mitschrift.ueberhoert,
        erwartet_antwort=erwartet_antwort,
    )

    # Erkennt „Nicht jetzt" und „Lass das" nach einer ungefragten Ansage. Sitzt vor dem Aggregator,
    # damit die Regel greift, egal was das Modell aus dem Satz macht, und hinter dem
    # Rückfragewächter: Sonst verwürfe der bei „Nein, lass das" gleich die Regel-Rückfrage.
    waechter = Ansagewaechter(res, context, aufschieben=timedelta(minutes=settings.proaktiv.aufschieben_minuten))

    eingang, ausgang = transport.input(), transport.output()
    pipeline = Pipeline(
        [
            eingang,
            *([gate] if gate else []),
            *([res.stimmpruefer] if res.stimmpruefer else []),
            stt,
            # Direkt hinter der Transkription: Dahinter sieht niemand das Codewort, nur codewort.MARKE.
            *([Codewortschutz(res.codewort)] if res.codewort else []),
            Sprachfilter(settings.gespraech.sprachen),
            stimmfilter,
            Rueckfragewaechter(res),  # ein Nein verwirft die offene Rückfrage, auch ohne abbrechen
            waechter,
            user_aggregator,
            llm,
            tts,
            ausgang,
            assistant_aggregator,
        ]
    )

    # Schlüsselt pro Antwort auf, wo die Zeit zwischen dem letzten Wort des Nutzers und
    # Alfreds erstem Ton geblieben ist (Pausenerkennung, Transkription, LLM, Sprachausgabe).
    latency = UserBotLatencyObserver()

    @latency.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(observer, breakdown):
        for line in breakdown.turn_contribution_lines():
            logger.info(f"Antwortzeit {line}")
        # Die erste Begrüßung misst Pipecat ab Verbindungsaufbau; das ist keine Antwortzeit.
        if breakdown.measured_from is not MeasuredFrom.CLIENT_CONNECTED:
            res.mitschrift.antwortzeit(breakdown.total_secs)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[latency, zustand, res.kosten, res.mitschrift],
        app_resources=res,
        idle_timeout_secs=None if always_on else 300,
        processor_unusable_policy=(
            ProcessorUnusablePolicy.CANCEL if always_on else ProcessorUnusablePolicy.CONTINUE
        ),
    )

    # Ungefragtes (Routinen, später Vorschläge) nach den Regeln aus proaktiv: Tagesgrenze, Ruhezeit,
    # Modus und „nie wieder"-Regeln aus regeln.yaml. Angefordertes kommt immer durch, außer im
    # Gästemodus: Dann wartet alles bis auf Timer und für den Besuch Angefordertes.
    zustellregeln = Zustellregeln(
        settings.proaktiv,
        Tageszaehler(gedaechtnis.ordner / "proaktiv.json"),
        modus=lambda: persona.aktiver_modus,
        gesperrt=lambda quelle: ist_gesperrt(gedaechtnis.regeln(), quelle),
    )

    async def ungefragtes_aufs_telefon(text: str) -> None:
        await res.benachrichtiger.senden(text, titel="Alfred")

    # Gesprochen wird erst, wenn die Pipeline läuft, und zugestellt ist erst, was Alfred hörbar
    # spricht: Am 16.10.2026 nahm ein Anlauf, dessen Mikrofon nicht aufging, vier Ereignisse mit.
    zusteller = Zusteller(
        res.ereignisse,
        context,
        worker,
        zustand,
        gate=gate,
        regeln=zustellregeln,
        waechter=waechter,
        benachrichtigen=ungefragtes_aufs_telefon if res.benachrichtiger else None,
        fremder_inhalt=res.fremden_inhalt_merken,
        bereit=pipeline_bereit(worker, eingang, ausgang),
        quittung_secs=QUITTUNG_SECS,
    )

    def wartet_auf_antwort() -> bool:
        return erwartet_antwort() or zustand.antwort_laeuft() or assistant_aggregator.has_function_calls_in_progress

    async def aufs_telefon(text: str) -> None:
        await res.benachrichtiger.senden(text, titel="Erinnerung", prioritaet="hoch")

    geburtstage = Geburtstagserinnerer(res.kontakte, res.erinnerungen, gedaechtnis.ordner / "geburtstage_erinnert.json")
    aufgaben: dict[str, Callable[[], Awaitable[None]]] = {
        "Erinnerungen": lambda: res.erinnerungen.warten(
            res.ereignisse, benachrichtigen=aufs_telefon if res.benachrichtiger else None
        ),
        "Freigaben": lambda: freigaben_beobachten(res),
        "Routinen": lambda: res.routinen.warten(res.ereignisse, persona.nutzer),
        "Zusteller": lambda: zusteller.laufen(),
        "Geburtstage": lambda: geburtstage.laufen(),
        "Mitschrift": lambda: res.mitschrift.lebenszeichen(),
    }
    if kalender:
        erinnerer = Terminerinnerer(
            kalender,
            res.erinnerungen,
            timedelta(minutes=settings.kalender.vorlauf_minuten),
            gedaechtnis.ordner / "termine_erinnert.json",
        )
        aufgaben["Termine"] = lambda: erinnerer.laufen()
    if res.whatsapp and res.meldeliste:
        # Nur hier: Ansagen kann nur der Sprach-Prozess. Startet unter Windows nebenbei die Brücke.
        aufgaben["WhatsApp"] = lambda: nachrichten_melden(res.whatsapp, res.meldeliste, res.ereignisse)
    if res.auftraege:
        # Nur hier, nicht in der App: Ergebnisse sagt Alfred an, und das kann nur der Sprach-Prozess.
        bearbeiter = Auftragsbearbeiter(
            res.auftraege, res.denker, res.ereignisse, res.benachrichtiger, settings.benachrichtigung.app_url
        )
        aufgaben["Aufträge"] = lambda: bearbeiter.laufen()
    hintergrund =[asyncio.create_task(beaufsichtigt(name, start), name=name) for name, start in aufgaben.items()]

    return Sitzung(
        worker, context, res, hintergrund,
        ruhen=stt.ruhen, wartet_auf_antwort=wartet_auf_antwort, zusteller=zusteller,
    )


def pipeline_bereit(worker: PipelineWorker, *transporte: FrameProcessor) -> asyncio.Event:
    """Wird gesetzt, sobald die Pipeline läuft und Mikrofon und Lautsprecher offen sind.

    on_pipeline_started allein reicht nicht: Scheitert ein Transport beim Einrichten (am 16.10.2026
    das Mikrofon mit PortAudio -9999), kommt der StartFrame trotzdem durch, und erst danach bricht
    der Worker ab. Den gescheiterten Transport hat Pipecat dann schon als unbrauchbar markiert.
    """
    bereit = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(worker, frame):
        if all(t.is_usable for t in transporte):
            bereit.set()
        else:
            logger.warning("Pipeline gestartet, aber Mikrofon oder Lautsprecher unbrauchbar; Ereignisse warten")

    return bereit


def letzte_antwort_ist_frage(messages) -> bool:
    """Ob Alfreds letzter Satz eine Frage war ("Welche Nachricht soll ich schicken?")."""
    for m in reversed(list(messages)):
        if isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("content"), str):
            text = m["content"].replace("[hier unterbrochen]", "").strip(" ●◐○\n")
            if text:
                return text.endswith("?")
        if isinstance(m, dict) and m.get("role") == "user":
            return False
    return False


async def beaufsichtigt(name: str, start: Callable[[], Awaitable[None]], pause: float = 10.0) -> None:
    """Startet eine Hintergrundaufgabe nach einem Fehler neu, statt sie still sterben zu lassen.

    Eine kaputte Datei oder ein Netzfehler darf Erinnerungen, Routinen oder den Zusteller nicht
    für den Rest des Tages lahmlegen. Der Fehler steht im Log, die Aufgabe läuft weiter.
    """
    while True:
        try:
            await start()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - genau dafür ist die Aufsicht da
            logger.exception(f"Hintergrundaufgabe '{name}' abgebrochen: {e}; Neustart in {pause:.0f} s")
            await asyncio.sleep(pause)


async def freigaben_beobachten(res: Ressourcen, intervall: float = 1.0) -> None:
    """Führt Freigaben aus, die Alexander per Push-Knopf vom Handy gegeben hat."""
    while True:
        await asyncio.sleep(intervall)
        if not res.freigabedatei:
            continue
        entscheidung = res.freigabedatei.entscheidung_abholen(datetime.now().astimezone())
        if not entscheidung:
            continue
        freigabe, res.offene_freigabe = res.offene_freigabe, None
        if not freigabe or freigabe.beschreibung != entscheidung["beschreibung"]:
            continue
        if entscheidung["status"] == "nein":
            res.gedaechtnis.aktion_protokollieren(freigabe.protokolltext, "ABGELEHNT vom Handy", datetime.now().astimezone())
            text = f"Hinweis vom System, nicht vom Gegenüber: Alexander hat vom Handy aus abgelehnt: {freigabe.beschreibung}. Sag das in einem Satz, kein Werkzeugaufruf nötig."
        else:
            try:
                ergebnis = await freigabe.starten()
                res.gedaechtnis.aktion_protokollieren(freigabe.protokolltext, "ausgeführt (Handy)", datetime.now().astimezone())
                if isinstance(ergebnis, Gesprochen):
                    # Die Aktion hat ihre Bestätigung schon gesprochen; ein Hinweis ans Modell
                    # ließe es dieselbe Bestätigung noch einmal sagen.
                    continue
                text = f"Hinweis vom System, nicht vom Gegenüber: Alexander hat vom Handy aus freigegeben: {freigabe.beschreibung}. Ergebnis: {ergebnis}. Sag das in einem Satz, kein Werkzeugaufruf nötig."
            except Exception as e:  # noqa: BLE001
                res.gedaechtnis.aktion_protokollieren(freigabe.protokolltext, f"FEHLER {e}", datetime.now().astimezone())
                text = f"Hinweis vom System, nicht vom Gegenüber: Die vom Handy freigegebene Aktion '{freigabe.beschreibung}' ist fehlgeschlagen: {e}. Sag das in einem Satz."
        await res.ereignisse.put(text)


def verbinde_schleuse(
    sitzung: Sitzung, gate: FrameProcessor, clock: Callable[[], float] = time.monotonic
) -> None:
    """Gesprächslogik an der Wachwort-Schleuse im Raum-Modus.

    Beim Einschlafen wandert das Gespräch ins Tagesprotokoll. Beim Aufwachen bekommt der
    Sprecher Datum, Uhrzeit und Gedächtnis frisch in den Systemprompt, denn der Prozess läuft
    über Tage; und nach einer langen Pause beginnt ein neues Gespräch ohne den alten Verlauf,
    sonst wächst der Kontext endlos und jede Antwort wird teurer.
    """
    settings = sitzung.ressourcen.settings
    worker, context = sitzung.worker, sitzung.context
    mitschrift = sitzung.ressourcen.mitschrift
    eingeschlafen = clock()
    if mitschrift:
        mitschrift.schlafen()  # die Schleuse ist beim Start zu
    if hasattr(gate, "wartet_auf_antwort_setzen"):
        gate.wartet_auf_antwort_setzen(sitzung.wartet_auf_antwort)

    @gate.event_handler("on_sleep")
    async def on_sleep(gate):
        nonlocal eingeschlafen
        eingeschlafen = clock()
        if mitschrift:
            mitschrift.schlafen()
        # Die Schleuse lässt jetzt kein Audio mehr durch; ohne Audio bräche Mistral die
        # Verbindung nach dreißig Sekunden mit einem Fehler ab.
        if sitzung.ruhen:
            await sitzung.ruhen()
        await sitzung.protokollieren()

    @gate.event_handler("on_wake")
    async def on_wake(gate, grund):
        if mitschrift:
            mitschrift.aufwachen()
        pause = clock() - eingeschlafen
        if context.messages and pause > settings.gespraech.vergessen_nach_minuten * 60:
            logger.info(f"Neues Gespräch nach {pause / 60:.0f} Minuten Pause")
            sitzung.neues_gespraech()
        await worker.queue_frame(
            LLMUpdateSettingsFrame(
                delta=MistralLLMService.Settings(system_instruction=system_prompt(sitzung.ressourcen))
            )
        )
