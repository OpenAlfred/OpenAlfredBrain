"""Zählt, was Alfred verbraucht, und rechnet es mit den Preisen aus der Konfiguration in Geld um.

Pipecat meldet nach jedem Schritt Nutzungsdaten (Token je Modell, Transkriptionssekunden,
Zeichen der Sprachausgabe); Websuchen zählt der Denker. Alles liegt je Tag in
gedaechtnis/kosten.json. Preise stehen in config/alfred.yaml; wo dort 0 steht, gilt der
Listenpreis aus config.py, und was weder dort noch da steht, bleibt ungepreist: Alfred sagt das
dazu, statt eine Zahl zu erfinden.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMUsageMetricsData, STTUsageMetricsData, TTSUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed


def modellkosten(preise, modell: str, eingabe: int, ausgabe: int, eingabe_cache: int = 0) -> float | None:
    """Dollar für so viele Token eines Modells; None, wenn es weder in der Konfiguration noch als
    Listenpreis (config.STANDARD_MODELLPREISE) einen Preis hat. eingabe zählt die
    zwischengespeicherten Token mit; die kosten den Cachepreis, wo es einen gibt."""
    preis = preise.modell(modell)
    if preis is None:
        return None
    gespeichert = min(eingabe_cache, eingabe)
    return (
        (eingabe - gespeichert) / 1_000_000 * preis.eingabe
        + gespeichert / 1_000_000 * (preis.eingabe_cache or preis.eingabe)
        + ausgabe / 1_000_000 * preis.ausgabe
    )


class Kostenzaehler(BaseObserver):
    def __init__(self, pfad: Path, preise, clock: Callable[[], datetime] | None = None):
        super().__init__()
        self._pfad = pfad
        self._preise = preise
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._tage: dict[str, dict] = {}
        if pfad.exists():
            self._tage = json.loads(pfad.read_text(encoding="utf-8"))

    def _heute(self) -> dict:
        tag = self._clock().date().isoformat()
        return self._tage.setdefault(
            tag, {"modelle": {}, "transkription_sekunden": 0.0, "sprachausgabe_zeichen": 0, "websuchen": 0}
        )

    def _speichern(self) -> None:
        self._pfad.parent.mkdir(parents=True, exist_ok=True)
        self._pfad.write_text(json.dumps(self._tage, ensure_ascii=False, indent=1), encoding="utf-8")

    async def on_push_frame(self, data: FramePushed):
        # Ein Beobachter sieht jeden Frame bei jedem Weiterreichen, von Processor zu Processor. Bis
        # 6.10.2026 zählte das jede Messung mehrfach: 172 statt 17 Minuten Transkription am Tag.
        if not isinstance(data.frame, MetricsFrame) or not getattr(data, "first_push", True):
            return
        heute = self._heute()
        geaendert = False
        for eintrag in data.frame.data:
            if isinstance(eintrag, LLMUsageMetricsData):
                modell = heute["modelle"].setdefault(eintrag.model or "?", {"eingabe": 0, "ausgabe": 0})
                modell["eingabe"] += eintrag.value.prompt_tokens
                modell["ausgabe"] += eintrag.value.completion_tokens
                # Der Systemprompt kommt bei jedem Zug aus Mistrals Zwischenspeicher (13.008 von 13.082
                # Token am 6.10.2026); das ist billiger, soweit das Modell einen eigenen Preis dafür hat.
                if gespeichert := getattr(eintrag.value, "cache_read_input_tokens", None):
                    modell["eingabe_cache"] = modell.get("eingabe_cache", 0) + gespeichert
                geaendert = True
            elif isinstance(eintrag, STTUsageMetricsData):
                heute["transkription_sekunden"] += eintrag.value.audio_seconds
                geaendert = True
            elif isinstance(eintrag, TTSUsageMetricsData):
                heute["sprachausgabe_zeichen"] += eintrag.value
                geaendert = True
        if geaendert:
            self._speichern()

    def websuche(self) -> None:
        self._heute()["websuchen"] += 1
        self._speichern()

    def transkription(self, sekunden: float) -> None:
        """Abgehörte Sprachnachrichten der App; was der Sprach-Prozess transkribiert, meldet Pipecat."""
        self._heute()["transkription_sekunden"] += sekunden
        self._speichern()

    def bericht(self, tag: str | None = None) -> dict:
        """Verbrauch und geschätzte Kosten eines Tages (Standard: heute) in Dollar."""
        tag = tag or self._clock().date().isoformat()
        daten = self._tage.get(tag)
        if not daten or not (daten["modelle"] or daten["transkription_sekunden"] or daten["websuchen"]):
            return {"tag": tag, "verbrauch": "nichts", "kosten_dollar": 0.0, "unbepreist": []}
        p = self._preise
        kosten = daten["transkription_sekunden"] / 60 * p.transkription_pro_minute
        kosten += daten["websuchen"] * p.websuche_pro_aufruf
        kosten += daten["sprachausgabe_zeichen"] / 1_000_000 * p.sprachausgabe()
        unbepreist = []
        for name, token in daten["modelle"].items():
            betrag = modellkosten(p, name, token["eingabe"], token["ausgabe"], token.get("eingabe_cache", 0))
            if betrag is None:
                unbepreist.append(name)
            else:
                kosten += betrag
        return {
            "tag": tag,
            "transkription_minuten": round(daten["transkription_sekunden"] / 60, 1),
            "sprachausgabe_zeichen": daten["sprachausgabe_zeichen"],
            "websuchen": daten["websuchen"],
            "token": {name: dict(t) for name, t in daten["modelle"].items()},
            "kosten_dollar": round(kosten, 3),
            "unbepreist": unbepreist,
        }
