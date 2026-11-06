"""Was alle Werkzeuge teilen: Ressourcen, Zeit, Freigaben, Fremdschutz, Rückfragewächter, Prompt-Auffrischung."""

from __future__ import annotations

import copy
import inspect
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import cache, wraps
from typing import Any

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams

from alfred.codewort import MARKE
from alfred.resources import Freigabe, Gesprochen, Ressourcen


def _res(params: FunctionCallParams) -> Ressourcen:
    return params.app_resources


def _fremd(params: FunctionCallParams, herkunft: str) -> None:
    """Vermerkt, dass ein Werkzeug fremden Text ins Gespräch bringt; ab jetzt fragen schreibende nach."""
    _res(params).fremden_inhalt_merken(herkunft)


def gespraechskontext(context, zuege: int = 6) -> str:
    """Die letzten Gesprächszüge als Text, für Werkzeuge, die den Zusammenhang brauchen."""
    if context is None:
        return ""
    zeilen = []
    for m in getattr(context, "messages", []):
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            wer = "Alexander" if m["role"] == "user" else "Alfred"
            zeilen.append(f"{wer}: {m['content'].strip()}")
    zeilen = zeilen[-zuege:]
    return "Bisheriges Gespräch:\n" + "\n".join(zeilen) if zeilen else ""


def _jetzt() -> datetime:
    return datetime.now().astimezone()


async def _sofort_sagen(params: FunctionCallParams, text: str) -> None:
    """Spricht text sofort und wörtlich, am Modell vorbei.

    Pipecat: über den LLM-Dienst zur Sprachausgabe; der Text landet auch im Verlauf. Das
    Textgespräch reicht als llm einen Sammler, der den Text als Antwort zeigt.
    """
    llm = getattr(params, "llm", None)
    if llm is not None:
        await llm.push_frame(TTSSpeakFrame(text))


async def _freigabe_aufs_handy(res: Ressourcen, beschreibung: str, grund: str = "") -> None:
    """Push mit Ja/Nein-Knöpfen, wenn Push und App-Adresse eingerichtet sind."""
    app_url = res.settings.benachrichtigung.app_url.rstrip("/") if hasattr(res.settings, "benachrichtigung") else ""
    if not (res.freigabedatei and res.benachrichtiger and app_url):
        return
    eintrag = res.freigabedatei.anlegen(beschreibung, _jetzt())
    knoepfe = [
        (name, f"{app_url}/api/freigabe/{eintrag['id']}/{wahl}?g={eintrag['geheimnis']}")
        for name, wahl in (("Ja", "ja"), ("Nein", "nein"))
    ]
    try:
        text = f"{beschreibung}\n\nGrund der Rückfrage: {grund}" if grund else beschreibung
        await res.benachrichtiger.senden(text, titel="Alfred fragt", prioritaet="hoch", aktionen=knoepfe)
    except Exception as e:  # Push ist Zugabe; die Rückfrage per Stimme läuft trotzdem
        logger.warning(f"Freigabe-Push fehlgeschlagen: {e}")


async def _freigabe_anfordern(
    params: FunctionCallParams,
    beschreibung: str,
    aktion: Callable[..., Awaitable[Any]],
    *,
    protokoll: str = "",
    grund: str = "",
    mit_params: bool = False,
    heikel: bool = False,
) -> None:
    """Stufe bestaetigen: Aktion merken und den Sprecher nachfragen lassen; mit Push auch aufs Handy.

    Args:
        protokoll: Fassung für Log und Aktionsprotokoll, etwa ohne Telefonnummer; leer = beschreibung.
        grund: Warum ein sonst freies Werkzeug fragt; steht in Hinweis, Push und Aktionsprotokoll.
        mit_params: Die Aktion nimmt den bestätigenden Aufruf entgegen (Freigabe.starten).
        heikel: Nachricht in Alexanders Namen oder Ende des Gästemodus; im Sprach-Loop mit Codewort
            zählt dann nur das Codewort, kein Ja.
    """
    res = _res(params)
    mit_codewort = heikel and getattr(res, "codewort", None) is not None
    res.offene_freigabe = Freigabe(
        beschreibung, aktion, protokoll=protokoll, grund=grund, mit_params=mit_params,
        tool_call_id=getattr(params, "tool_call_id", None), mit_codewort=mit_codewort,
    )  # fmt: skip
    await _freigabe_aufs_handy(res, beschreibung, grund)
    if mit_codewort:
        zustimmen = (
            "Bitte dabei um das Codewort, ein Ja reicht hier nicht. Du kennst das Codewort nicht und "
            f"nennst nie eins; sagt das Gegenüber es, steht dafür {MARKE} im Text. Dann rufst du "
            "bestaetigen auf"
        )
    else:
        zustimmen = "Antwortet das Gegenüber darauf klar mit Ja, rufst du bestaetigen auf"
    hinweis = (
        "Noch ist nichts passiert. Frag in einem Satz, ob du das tun sollst, und wiederhole "
        "dabei genau die Angaben aus aktion (bei Terminen Tag und Uhrzeit, bei Nachrichten "
        f"Empfänger und Text), damit Fehler auffallen. {zustimmen}, nie im selben Zug wie "
        "die Frage; erst dessen Ergebnis bestätigt die Aktion. Bei Nein ruf abbrechen auf. "
        "Will das Gegenüber etwas ändern, ruf das Werkzeug mit den neuen Angaben neu auf; die "
        "alte Rückfrage verfällt. Sag nie, dass du es tust oder getan hast, ohne bestaetigen "
        "aufgerufen zu haben."
    )
    if grund:
        hinweis += f" Grund dieser Rückfrage: {grund}; sonst liefe das Werkzeug frei. Nenn den Grund in einem Halbsatz."
    await params.result_callback({"status": "bestaetigung_noetig", "aktion": beschreibung, "hinweis": hinweis})


def freigabe_verwerfen(res: Ressourcen, grund: str) -> str | None:
    """Verwirft die offene Rückfrage samt Push-Knöpfen; gibt ihre Beschreibung zurück."""
    freigabe, res.offene_freigabe = res.offene_freigabe, None
    if res.freigabedatei:
        res.freigabedatei.schliessen()
    if freigabe is None:
        return None
    res.gedaechtnis.aktion_protokollieren(freigabe.protokolltext, grund, _jetzt())
    logger.info(f"Rückfrage verworfen ({grund}): {freigabe.protokolltext}")
    return freigabe.beschreibung


NEIN_WOERTER = frozenset({"nein", "nee", "nö", "noe", "abbrechen"})

# Ein Ja auf eine Rückfrage braucht eines dieser Wörter ("Ja, mach das", "Passt", "Schick's ab").
JA_WOERTER = frozenset(
    "ja jaa joa jo jop jap jep jup yo yes yeah yep okay ok oke okey klar genau passt stimmt korrekt "
    "richtig gerne gern bitte mach mache machs machen tu tus schick schicke schicks schicken sende "
    "senden raus los go einverstanden abgemacht gebongt sicher perfekt super prima toll top gut fein "
    "jawohl jawoll sollst logisch natürlich absolut unbedingt definitiv selbstverständlich".split()
)
# Und keines von diesen: Dann will das Gegenüber etwas anderes oder zögert ("Ja, aber erst morgen").
VORBEHALT_WOERTER = frozenset(
    "nein nee nö noe nicht abbrechen stopp stop warte warten moment falsch aber sondern statt "
    "stattdessen lieber anders ändern ändere änder korrigier korrigiere".split()
)


def zustimmung(antwort: str) -> bool:
    """Ob ein Satz ein klares Ja auf eine Rückfrage ist.

    Am 6.10.2026 schickte Alfred eine WhatsApp, weil die Spracherkennung aus Alexanders
    Weiterdiktieren "He is all for it." machte und das Modell darin ein Ja sah. Ob die Antwort
    zustimmt, entscheidet deshalb der Code: ein Ja-Wort, kein Vorbehalt, keine Gegenfrage.
    """
    woerter = re.findall(r"\w+", antwort.casefold())
    if not woerter or VORBEHALT_WOERTER.intersection(woerter):
        return False
    if antwort.rstrip().endswith("?") and woerter[0] not in JA_WOERTER:
        return False  # "Bist du sicher?", "Welche Nachricht?"
    return bool(JA_WOERTER.intersection(woerter))


def codewort_genannt(antwort: str) -> bool:
    """Ob eine Antwort das Codewort enthält (der Codewortschutz hat es durch MARKE ersetzt) und keinen Vorbehalt.

    „[Codewort], aber schreib noch dazu …“ zählt nicht; das Gegenüber will dann etwas ändern.
    """
    if MARKE not in antwort:
        return False
    return not VORBEHALT_WOERTER.intersection(re.findall(r"\w+", antwort.replace(MARKE, " ").casefold()))


def verlauf_seit_rueckfrage(context, tool_call_id: str | None) -> tuple[bool, str] | None:
    """Was seit dem Ergebnis des Werkzeugaufrufs im Verlauf steht: (Rückfrage gestellt, letzte Antwort).

    Gestellt heißt: Alfred hat danach etwas gesagt. Die letzte Antwort ist Alexanders letzter
    Gesprächszug danach, so wie das Modell ihn bekommen hat. None, wenn es sich nicht prüfen
    lässt (kein Verlauf wie im App-Chat, keine Kennung, Ergebnis nicht im Verlauf).
    """
    nachrichten = getattr(context, "messages", None) if context is not None else None
    if not nachrichten or not tool_call_id:
        return None
    nachrichten = [m for m in nachrichten if isinstance(m, dict)]
    for i, m in enumerate(nachrichten):
        if m.get("role") == "tool" and m.get("tool_call_id") == tool_call_id:
            danach = nachrichten[i + 1 :]
            gefragt = any(
                n.get("role") == "assistant" and isinstance(n.get("content"), str) and n["content"].strip(" ●◐○")
                for n in danach
            )
            antworten = [n["content"] for n in danach if n.get("role") == "user" and isinstance(n.get("content"), str)]
            return gefragt, antworten[-1] if antworten else ""
    return None


def antwort_merken(res: Ressourcen, text: str) -> None:
    """Hält einen Satz des Gegenübers bei der offenen Rückfrage fest; bestaetigen prüft ihn."""
    if res.offene_freigabe is not None:
        res.offene_freigabe.antwort(text)


class Rueckfragewaechter(FrameProcessor):
    """Verwirft eine offene Rückfrage, sobald Alexanders Antwort mit Nein beginnt, und merkt sich die Antworten.

    Das Modell sagte auf ein Nein "Alles klar, dann breche ich das ab", ohne abbrechen
    aufzurufen (4.10.2026). Die Rückfrage mit einem selbst erfundenen Text blieb offen, und
    es bot sie später wieder an. Ob etwas abgelehnt ist, entscheidet deshalb der Code; das
    Transkript geht trotzdem weiter, damit Alfred antwortet. Ebenso merkt er sich, dass
    Alexander nach der Rückfrage etwas gesagt hat: Ohne Antwort führt bestaetigen nichts aus.
    """

    def __init__(self, res: Ressourcen, **kwargs):
        super().__init__(**kwargs)
        self._res = res

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and self._res.offene_freigabe is not None:
            woerter = re.findall(r"\w+", frame.text.casefold())
            if woerter and woerter[0] in NEIN_WOERTER:
                freigabe_verwerfen(self._res, "ABGELEHNT")
            else:
                antwort_merken(self._res, frame.text)
        await self.push_frame(frame, direction)


async def _prompt_auffrischen(params: FunctionCallParams) -> None:
    """Schickt den neu gebauten Systemprompt an das Sprachmodell; wirkt ab der nächsten Antwort."""
    res = _res(params)
    if res.systemprompt and params.pipeline_worker is not None:
        from pipecat.frames.frames import LLMUpdateSettingsFrame
        from pipecat.services.mistral.llm import MistralLLMService

        await params.pipeline_worker.queue_frame(
            LLMUpdateSettingsFrame(delta=MistralLLMService.Settings(system_instruction=res.systemprompt()))
        )


def _vorlage(spec, argumente: dict) -> str | None:
    """Füllt eine Rückfrage-Vorlage: Text mit {argument} oder Funktion der Argumente, die sie braucht."""
    if isinstance(spec, str):
        return spec.format(**argumente)
    erwartet = inspect.signature(spec).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in erwartet.values()):
        return spec(**argumente)
    return spec(**{name: wert for name, wert in argumente.items() if name in erwartet})


@cache
def _mit_fremdschutz(werkzeug: Callable, rueckfrage) -> Callable:
    """Hülle für ein schreibendes Werkzeug: frei ohne fremden Inhalt, sonst Rückfrage wie bei bestaetigen.

    Steht fremder Text im Gespräch (Ressourcen.fremder_inhalt: Mail, Nachricht, Suchtreffer,
    Bericht), hält das Modell Anweisungen darin gelegentlich für Aufträge; eine Mail mit "Speichere
    meine neue Adresse" brachte es zu kontakt_email_merken. Dann fragt auch ein schreibendes
    Werkzeug nach, mit allen Werten (rueckfrage, registrierung.Rueckfrage), damit ein
    untergeschobener auffällt. Name und Schema bleiben die des Werkzeugs (functools.wraps); Pipecat
    und das Textgespräch merken nichts davon. Bei gesetzter Marke läuft das Werkzeug erst in
    bestaetigen, mit einem Sammler als result_callback; ein fehler im Ergebnis wird dort zur Ausnahme
    und steht so im Aktionsprotokoll.
    """
    signatur = inspect.signature(werkzeug)

    @wraps(werkzeug)
    async def geschuetzt(params, **argumente):
        marke = _res(params).fremder_inhalt
        if marke is None:
            await werkzeug(params, **argumente)
            return
        try:
            gebunden = signatur.bind(params, **argumente)
        except TypeError:
            await werkzeug(params, **argumente)  # meldet die falschen Argumente selbst
            return
        gebunden.apply_defaults()
        werte = {name: wert for name, wert in gebunden.arguments.items() if name != "params"}
        beschreibung = _vorlage(rueckfrage.beschreibung, werte)
        if not beschreibung:
            await werkzeug(params, **argumente)
            return

        async def ausfuehren(bestaetigung=None):
            ergebnisse: list[Any] = []
            gesprochen = False

            async def sammeln(ergebnis, *, properties=None):
                nonlocal gesprochen
                ergebnisse.append(ergebnis)
                gesprochen = properties is not None and properties.run_llm is False

            # Mit den params des bestätigenden Aufrufs, wenn es einen gibt: Dessen Sprachausgabe
            # und Kontext sind die aktuellen; vom Handy aus bleibt es beim ursprünglichen Aufruf.
            eigene = copy.copy(bestaetigung if bestaetigung is not None else params)
            eigene.result_callback = sammeln
            await werkzeug(eigene, **argumente)
            ergebnis = ergebnisse[-1] if ergebnisse else {}
            if isinstance(ergebnis, dict) and ergebnis.get("fehler"):
                raise RuntimeError(str(ergebnis["fehler"]))
            # Hat das Werkzeug selbst gesprochen (auftrag_geben), darf das Modell danach nicht weiterreden.
            return Gesprochen(ergebnis) if gesprochen and isinstance(ergebnis, dict) else ergebnis

        protokoll = _vorlage(rueckfrage.protokoll, werte) if rueckfrage.protokoll else ""
        await _freigabe_anfordern(params, beschreibung, ausfuehren, protokoll=protokoll or "", grund=marke.grund, mit_params=True)

    return geschuetzt
