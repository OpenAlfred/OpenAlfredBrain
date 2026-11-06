"""Mit Alfred schreiben statt sprechen, für die App.

Gleicher Systemprompt, gleiche Werkzeuge, gleiches Gedächtnis wie bei der Stimme; nur die
Schleife läuft direkt über Mistrals Chat-API statt durch die Pipecat-Pipeline. Werkzeuge
bekommen dasselbe `params`-Objekt wie dort (app_resources, result_callback, context), deshalb
funktionieren Freigaben, Profil, Routinen und alles andere unverändert.

Ereignisse (fällige Erinnerungen, Ergebnisse des Denkers) liefert der Sprach-Prozess aus;
hier werden nur die abgeholt, die eigene Werkzeugaufrufe dieses Gesprächs erzeugt haben.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from types import SimpleNamespace
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.direct_function import DirectFunctionWrapper
from pipecat.frames.frames import TTSSpeakFrame

from alfred.denker import _inhalt_text
from alfred.ereignisse import als_ereignis
from alfred.resources import Ressourcen
from alfred.werkzeuge import NEIN_WOERTER, REGISTER, antwort_merken, build_tools, freigabe_verwerfen

MAX_RUNDEN = 8
MAX_VERLAUF = 30
MAX_ARGUMENT_ZEICHEN = 300  # längere Texte in den Argumenten kürzt die Anzeige der Werkzeugaufrufe
MAX_KURZFASSUNG_ZEICHEN = 160  # Länge der Zeile zum Ergebnis eines Werkzeugs in der Anzeige


def werkzeug_schemas(werkzeuge) -> tuple[list[dict], dict[str, Any]]:
    """Mistral-Werkzeugbeschreibungen aus Pipecat-Direktfunktionen, plus Name zu Funktion."""
    schemas, funktionen = [], {}
    for fn in werkzeuge:
        schema = DirectFunctionWrapper(fn).to_function_schema()
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": schema.name,
                    "description": schema.description,
                    "parameters": {"type": "object", "properties": schema.properties, "required": schema.required},
                },
            }
        )
        funktionen[schema.name] = fn
    return schemas, funktionen


def _als_dict(nachricht) -> dict:
    """Eine Nachricht für den Verlauf; Werkzeugaufrufe des Modells kommen als SDK-Objekt."""
    if isinstance(nachricht, dict):
        return nachricht
    return {
        "role": "assistant",
        "content": _inhalt_text(nachricht.content) or "",
        "tool_calls": [
            {
                "id": aufruf.id,
                "type": "function",
                "function": {
                    "name": aufruf.function.name,
                    "arguments": aufruf.function.arguments
                    if isinstance(aufruf.function.arguments, str)
                    else json.dumps(aufruf.function.arguments, ensure_ascii=False),
                },
            }
            for aufruf in nachricht.tool_calls or []
        ],
    }


def _kuerzen(verlauf: list[dict]) -> list[dict]:
    """Die letzten MAX_VERLAUF Nachrichten, beginnend bei einer Nachricht von Alexander.

    Ein Werkzeugergebnis ohne den Aufruf davor lehnt Mistral ab; deshalb nie mitten in einem
    Werkzeugaufruf abschneiden.
    """
    verlauf = verlauf[-MAX_VERLAUF:]
    while verlauf and verlauf[0].get("role") != "user":
        verlauf = verlauf[1:]
    return verlauf


def sichtbarer_verlauf(verlauf: list[dict]) -> list[dict]:
    """Nur was Alexander und Alfred geschrieben haben, für die Anzeige in der App."""
    return [
        {"role": m["role"], "content": m["content"]}
        for m in verlauf
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip()
        and not m.get("tool_calls")
    ]


def _gekuerzt(text: str, grenze: int) -> str:
    return text if len(text) <= grenze else text[: grenze - 1] + "…"


def _json_tauglich(wert: Any) -> Any:
    """Der Wert als JSON-Daten für die Anzeige; Texte über MAX_ARGUMENT_ZEICHEN gekürzt, auch verschachtelt."""
    if isinstance(wert, str):
        return _gekuerzt(wert, MAX_ARGUMENT_ZEICHEN)
    if wert is None or isinstance(wert, (bool, int)):
        return wert
    if isinstance(wert, float):
        return wert if math.isfinite(wert) else str(wert)  # NaN und unendlich kennt JSON nicht
    if isinstance(wert, dict):
        return {str(k): _json_tauglich(v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_json_tauglich(v) for v in wert]
    return _gekuerzt(str(wert), MAX_ARGUMENT_ZEICHEN)


def _json_oder_text(roh: Any) -> Any:
    """Aufrufe und Ergebnisse stehen im Verlauf als JSON-Text; was sich nicht lesen lässt, bleibt Text."""
    if isinstance(roh, str):
        try:
            return json.loads(roh)
        except ValueError:
            return roh
    return roh


def _wert_text(wert: Any) -> str:
    """Ein Wert aus einem Werkzeugergebnis als kurzer Text; Verschachteltes als kompaktes JSON."""
    if wert is None:
        return "–"
    if isinstance(wert, bool):
        return "ja" if wert else "nein"
    if isinstance(wert, float) and wert.is_integer():
        return str(int(wert))
    if isinstance(wert, (list, tuple)) and not any(isinstance(w, (dict, list, tuple)) for w in wert):
        return ", ".join(_wert_text(w) for w in wert) or "–"
    if isinstance(wert, (dict, list, tuple)):
        try:
            return json.dumps(wert, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # etwa Schlüssel, die JSON nicht kennt
            pass
    return str(wert)


def schritt_ansicht(name: str, argumente: Any, ergebnis: Any) -> dict:
    """Ein Werkzeugaufruf für die Anzeige in der App: Name, Gruppe, Stufe, Argumente, Status und eine Zeile Text.

    status: fehler (das Ergebnis meldet einen fehler), bestaetigung (das Werkzeug hat nur eine Rückfrage
    angelegt, text ist die Rückfrage) oder ok (text nennt die Werte des Ergebnisses). Die Argumente sind
    JSON-tauglich und gekürzt; was sich nicht als Dict lesen ließ, steht unter _roh (wie in eval.py).
    """
    eintrag = REGISTER.get(name)
    status = "ok"
    if isinstance(ergebnis, dict) and "fehler" in ergebnis:
        status, text = "fehler", str(ergebnis["fehler"])
    elif isinstance(ergebnis, dict) and ergebnis.get("status") == "bestaetigung_noetig":
        status, text = "bestaetigung", str(ergebnis.get("aktion", ""))
    elif isinstance(ergebnis, dict):
        # Der hinweis richtet sich an das Modell, nicht an Alexander.
        text = " · ".join(f"{k}: {_wert_text(v)}" for k, v in ergebnis.items() if k != "hinweis")
    elif ergebnis is None or (isinstance(ergebnis, (str, list, tuple)) and not ergebnis):
        text = ""
    else:
        text = str(ergebnis)
    if argumente is None or (isinstance(argumente, str) and not argumente):
        argumente = {}
    elif not isinstance(argumente, dict):
        argumente = {"_roh": argumente}
    return {
        "name": name,
        "gruppe": eintrag.gruppe if eintrag else None,
        "stufe": eintrag.stufe if eintrag else None,
        "argumente": _json_tauglich(argumente),
        "status": status,
        "text": _gekuerzt(" ".join(text.split()), MAX_KURZFASSUNG_ZEICHEN),
    }


def ansicht_verlauf(verlauf: list[dict], gesprochen: list[dict] | tuple = ()) -> list[dict]:
    """Wie sichtbarer_verlauf, aber Alfreds Antworten tragen die Werkzeugaufrufe, die ihnen vorausgingen.

    Die Aufrufe werden aus dem Verlauf zusammengesetzt: Assistenten-Nachrichten mit tool_calls und die
    Werkzeugnachrichten mit dem Ergebnis, verknüpft über tool_call_id. Jeder Aufruf gehört zur nächsten
    sichtbaren Antwort in seinem Zug; Aufrufe ohne sichtbare Antwort fallen weg. Eine Nachricht von
    Alexander beginnt einen neuen Zug. Aufrufe aus Runden für Ereignisse (Denker fertig) legt antworten
    nicht im Verlauf ab; nur Textgespraech.schritte hat sie, bis der nächste Zug beginnt. Nachrichten aus
    `gesprochen` (Sprachnachrichten der App, dieselben Objekte wie im Verlauf) tragen "gesprochen": True.
    """
    ansicht: list[dict] = []
    schritte: list[dict] = []  # Aufrufe des laufenden Zugs, die noch auf ihre sichtbare Antwort warten
    nach_kennung: dict[Any, dict] = {}
    for m in verlauf:
        rolle = m.get("role")
        if rolle == "user":
            schritte, nach_kennung = [], {}
        elif rolle == "assistant":
            for aufruf in m.get("tool_calls") or []:
                fn = aufruf.get("function") or {}
                schritt = {
                    "name": str(fn.get("name", "?")),
                    "argumente": _json_oder_text(fn.get("arguments")),
                    "ergebnis": None,
                }
                schritte.append(schritt)
                if aufruf.get("id") is not None:
                    nach_kennung[aufruf["id"]] = schritt
        elif rolle == "tool" and m.get("tool_call_id") in nach_kennung:
            nach_kennung[m["tool_call_id"]]["ergebnis"] = _json_oder_text(m.get("content"))
        for element in sichtbarer_verlauf([m]):  # keine oder eine Nachricht; die Regel bleibt an einer Stelle
            if element["role"] == "assistant":
                element["schritte"] = [schritt_ansicht(**s) for s in schritte]
                schritte, nach_kennung = [], {}
            elif any(m is g for g in gesprochen):
                element["gesprochen"] = True
            ansicht.append(element)
    return ansicht


class _Sprachausgabe:
    """Steht im Textgespräch für Pipecats LLM-Dienst: sammelt, was Werkzeuge sofort sagen."""

    def __init__(self):
        self.gesagt: list[str] = []

    async def push_frame(self, frame, direction=None):
        if isinstance(frame, TTSSpeakFrame):
            self.gesagt.append(frame.text)


class Textgespraech:
    def __init__(self, res: Ressourcen, systemprompt):
        self._res = res
        self._systemprompt = systemprompt
        self.verlauf: list[dict] = []  # Texte samt Werkzeugaufrufen; die App zeigt ansicht_verlauf
        # Die Werkzeugaufrufe des letzten antworten(), auch aus den Runden für Ereignisse; das Ergebnis steht
        # so da, wie das Werkzeug es lieferte. schritt_ansicht macht daraus die Anzeige der App.
        self.schritte: list[dict] = []  # je Aufruf {"name", "argumente", "ergebnis"}
        # Nutzernachrichten, die als Sprachnachricht kamen (dieselben Objekte wie im Verlauf), nur für die Anzeige:
        # Das Modell sieht die Abschrift wie getippten Text, so wie beim Sprechen im Raum.
        self.sprachnachrichten: list[dict] = []
        self._sperre = asyncio.Lock()
        self._denker_gestartet = False
        self._fremd_ab: dict | None = None  # Nutzernachricht des Zugs, der zuletzt fremden Text brachte

    async def _werkzeug(self, name: str, argumente: dict, funktionen: dict, sprachausgabe: _Sprachausgabe) -> tuple[Any, bool]:
        """Ruft ein Werkzeug auf; gibt sein Ergebnis zurück und ob das Modell danach weiterreden soll."""
        ergebnisse: list[Any] = []
        weiter = True

        async def result_callback(ergebnis, *, properties=None):
            nonlocal weiter
            ergebnisse.append(ergebnis)
            # Wie in Pipecat: run_llm=False heißt, das Werkzeug hat selbst geantwortet.
            weiter = not (properties is not None and properties.run_llm is False)

        params = SimpleNamespace(
            app_resources=self._res,
            result_callback=result_callback,
            context=SimpleNamespace(messages=self.verlauf),
            pipeline_worker=None,
            llm=sprachausgabe,
            function_name=name,
            arguments=argumente,
        )
        fn = funktionen.get(name)
        if fn is None:
            return {"fehler": f"Unbekanntes Werkzeug {name}"}, True
        if name == "nachdenken":
            self._denker_gestartet = True
        try:
            await fn(params, **argumente)
        except TypeError as e:
            return {"fehler": f"Falsche Argumente für {name}: {e}"}, True
        except Exception as e:  # noqa: BLE001 - der Fehler soll beim Modell ankommen
            logger.exception(f"Werkzeug {name} gescheitert")
            return {"fehler": f"{name} gescheitert: {e}"}, True
        return (ergebnisse[-1] if ergebnisse else {}), weiter

    async def _runde(self, nachrichten: list) -> str:
        """Eine Modellantwort samt Werkzeugaufrufen; gibt den Text zurück."""
        sprachausgabe = _Sprachausgabe()
        # Je Runde neu: Anbindungen und Modus können sich seit der letzten geändert haben.
        schemas, funktionen = werkzeug_schemas(build_tools(self._res))
        for _ in range(MAX_RUNDEN):
            antwort = await self._res.client.chat.complete_async(
                model=self._res.settings.modelle.sprecher,
                messages=nachrichten,
                tools=schemas or None,
                tool_choice="auto" if schemas else None,
            )
            nachricht = antwort.choices[0].message
            if not nachricht.tool_calls:
                return " ".join([*sprachausgabe.gesagt, _inhalt_text(nachricht.content)]).strip()
            nachrichten.append(nachricht)
            modell_weiter = False
            for aufruf in nachricht.tool_calls:
                argumente = aufruf.function.arguments
                if isinstance(argumente, str):
                    argumente = json.loads(argumente or "{}")
                ergebnis, weiter = await self._werkzeug(aufruf.function.name, argumente or {}, funktionen, sprachausgabe)
                self.schritte.append({"name": aufruf.function.name, "argumente": argumente or {}, "ergebnis": ergebnis})
                modell_weiter = modell_weiter or weiter
                nachrichten.append(
                    {
                        "role": "tool",
                        "tool_call_id": aufruf.id,
                        "name": aufruf.function.name,
                        "content": json.dumps(ergebnis, ensure_ascii=False, default=str),
                    }
                )
            if any(aufruf.function.name == "modus_wechseln" for aufruf in nachricht.tool_calls):
                # Wie beim Sprechen gilt der neue Modus ab der nächsten Antwort: andere Gruppen, andere Liste.
                schemas, funktionen = werkzeug_schemas(build_tools(self._res))
            if not modell_weiter:
                # Alle Werkzeuge haben selbst geantwortet (etwa die Bestätigung von auftrag_geben).
                return " ".join(sprachausgabe.gesagt).strip() or "Erledigt."
        return "Ich bin mit der Aufgabe nicht fertig geworden."

    def _marke_pruefen(self) -> None:
        """Fiel der Zug, der zuletzt fremden Text brachte, beim Kürzen aus dem Verlauf, endet die Marke.

        Im Chat gibt es kein Gesprächsende, nur den Verlauf mit den letzten MAX_VERLAUF Nachrichten;
        solange der Zug mit dem fremden Text darin steht, fragen schreibende Werkzeuge nach.
        """
        if self._fremd_ab is not None and not any(m is self._fremd_ab for m in self.verlauf):
            self._fremd_ab = None
            self._res.fremden_inhalt_vergessen()

    async def antworten(self, text: str, *, gesprochen: bool = False) -> list[str]:
        """Alfreds Antworten auf einen Text: die eigentliche plus alles, was Werkzeuge nachliefern.

        gesprochen: Der Text ist die Abschrift einer Sprachnachricht aus der App. Er zählt wie getippt
        (auch ein Ja auf eine Rückfrage); die App zeigt ihn nur anders an.
        """
        async with self._sperre:
            self.schritte = []  # erst hier, in der Sperre: Ein laufender Zug soll seine Aufrufe behalten
            # Wie beim Sprechen: Ein Nein verwirft die offene Rückfrage, egal was das Modell tut;
            # jede andere Antwort hält bestaetigen fest und prüft sie dort auf ein Ja.
            woerter = re.findall(r"\w+", text.casefold())
            if self._res.offene_freigabe is not None and woerter and woerter[0] in NEIN_WOERTER:
                freigabe_verwerfen(self._res, "ABGELEHNT")
            else:
                antwort_merken(self._res, text)
            nutzer = {"role": "user", "content": text.strip()}
            if gesprochen:
                self.sprachnachrichten.append(nutzer)
            self.verlauf.append(nutzer)
            self.verlauf = _kuerzen(self.verlauf)
            self.sprachnachrichten = [m for m in self.sprachnachrichten if any(m is v for v in self.verlauf)]
            self._marke_pruefen()
            marke_vorher = self._res.fremder_inhalt
            nachrichten: list = [{"role": "system", "content": self._systemprompt()}, *self.verlauf]
            neu_ab = len(nachrichten)
            antworten = [await self._runde(nachrichten)]
            nachrichten.append({"role": "assistant", "content": antworten[0]})
            # Werkzeugaufrufe und Ergebnisse gehören in den Verlauf: Sonst sieht das Modell im
            # nächsten Zug nicht, dass eine Rückfrage offen ist, und fragt auf "Ja" einfach neu.
            self.verlauf.extend(_als_dict(m) for m in nachrichten[neu_ab:])

            # Ergebnisse des Denkers aus diesem Gespräch: Wurde er gestartet, bis zu 20 Sekunden
            # warten; sonst nur abholen, was schon da ist.
            wartezeit = 20.0 if self._denker_gestartet else 0.0
            self._denker_gestartet = False
            for _ in range(3):
                try:
                    if wartezeit:
                        ereignis = await asyncio.wait_for(self._res.ereignisse.get(), timeout=wartezeit)
                    else:
                        ereignis = self._res.ereignisse.get_nowait()
                except (asyncio.TimeoutError, asyncio.QueueEmpty):
                    break
                wartezeit = 0.0
                ereignis = als_ereignis(ereignis)
                if ereignis.herkunft:  # etwa das Ergebnis des Denkers aus dem Internet
                    self._res.fremden_inhalt_merken(ereignis.herkunft)
                hinweis = ereignis.text
                nachrichten.append({"role": "user", "content": hinweis})
                zusatz = await self._runde(nachrichten)
                antworten.append(zusatz)
                nachrichten.append({"role": "assistant", "content": zusatz})
                self.verlauf.append({"role": "assistant", "content": zusatz})
            if self._res.fremder_inhalt is not None and self._res.fremder_inhalt is not marke_vorher:
                self._fremd_ab = nutzer  # dieser Zug brachte den fremden Text in den Verlauf
            return antworten
