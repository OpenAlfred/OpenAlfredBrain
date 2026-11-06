import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import (
    LLMTokenUsage,
    LLMUsageMetricsData,
    STTUsage,
    STTUsageMetricsData,
    TTSUsageMetricsData,
)

from alfred.config import Modellpreis, Preise
from alfred.kosten import Kostenzaehler

TZ = timezone(timedelta(hours=2))
HEUTE = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)


def frame(*daten):
    return SimpleNamespace(frame=MetricsFrame(data=list(daten)))


def test_zaehlt_und_rechnet(tmp_path):
    preise = Preise(
        transkription_pro_minute=0.006,
        websuche_pro_aufruf=0.03,
        sprachausgabe_pro_million_zeichen=10.0,
        modelle={"zai-glm-5-3": Modellpreis(eingabe=1.0, ausgabe=4.0)},
    )
    zaehler = Kostenzaehler(tmp_path / "kosten.json", preise, lambda: HEUTE)

    async def lauf():
        await zaehler.on_push_frame(frame(
            LLMUsageMetricsData(processor="llm", model="zai-glm-5-3", value=LLMTokenUsage(prompt_tokens=400_000, completion_tokens=50_000, total_tokens=450_000)),
            STTUsageMetricsData(processor="stt", value=STTUsage(audio_seconds=600)),
            TTSUsageMetricsData(processor="tts", value=100_000),
        ))
        await zaehler.on_push_frame(frame(
            LLMUsageMetricsData(processor="llm", model="mistral-neu-2701", value=LLMTokenUsage(prompt_tokens=1000, completion_tokens=100, total_tokens=1100)),
        ))

    asyncio.run(lauf())
    zaehler.websuche()
    zaehler.websuche()

    b = zaehler.bericht()
    assert b["transkription_minuten"] == 10.0 and b["websuchen"] == 2 and b["sprachausgabe_zeichen"] == 100_000
    assert b["token"]["zai-glm-5-3"] == {"eingabe": 400_000, "ausgabe": 50_000}
    # 10 min * 0.006 + 2 * 0.03 + 0.1 M Zeichen * 10 + 0.4 M * 1 + 0.05 M * 4 = 0.06 + 0.06 + 1.0 + 0.4 + 0.2
    assert b["kosten_dollar"] == 1.72
    assert b["unbepreist"] == ["mistral-neu-2701"]

    # Gespeichert und je Tag getrennt.
    neu = Kostenzaehler(tmp_path / "kosten.json", preise, lambda: HEUTE + timedelta(days=1))
    assert neu.bericht()["verbrauch"] == "nichts"
    assert neu.bericht("2026-10-04")["kosten_dollar"] == 1.72


def test_leerer_tag(tmp_path):
    zaehler = Kostenzaehler(tmp_path / "k.json", Preise(), lambda: HEUTE)
    assert zaehler.bericht("2020-01-01") == {"tag": "2020-01-01", "verbrauch": "nichts", "kosten_dollar": 0.0, "unbepreist": []}


def test_jede_messung_zaehlt_nur_einmal(tmp_path):
    # Pipecat meldet einem Beobachter jeden Weiterreich-Schritt; nur der erste zählt.
    zaehler = Kostenzaehler(tmp_path / "k.json", Preise(), lambda: HEUTE)
    messung = MetricsFrame(data=[STTUsageMetricsData(processor="stt", value=STTUsage(audio_seconds=60))])

    async def lauf():
        for erstes in (True, False, False, False):
            await zaehler.on_push_frame(SimpleNamespace(frame=messung, first_push=erstes))

    asyncio.run(lauf())
    assert zaehler.bericht()["transkription_minuten"] == 1.0


def test_listenpreise_und_zwischengespeicherte_eingabe(tmp_path):
    # So steht es in Alexanders config/alfred.yaml: Nullen heißen Listenpreis.
    preise = Preise(modelle={"mistral-small-2603": Modellpreis(eingabe=0, ausgabe=0)})
    zaehler = Kostenzaehler(tmp_path / "k.json", preise, lambda: HEUTE)
    nutzung = LLMTokenUsage(prompt_tokens=1_000_000, completion_tokens=100_000, total_tokens=1_100_000, cache_read_input_tokens=900_000)

    async def lauf():
        await zaehler.on_push_frame(frame(LLMUsageMetricsData(processor="llm", model="mistral-small-2603", value=nutzung)))
        await zaehler.on_push_frame(frame(LLMUsageMetricsData(processor="llm", model="zai-glm-5-3", value=nutzung)))

    asyncio.run(lauf())
    b = zaehler.bericht()
    assert b["unbepreist"] == [] and b["token"]["zai-glm-5-3"]["eingabe_cache"] == 900_000
    # Small ohne eigenen Cachepreis: 1 M * 0,15 + 0,1 M * 0,6 = 0,21; GLM: 0,1 M * 1,4 + 0,9 M * 0,14 + 0,1 M * 4,4 = 0,706
    assert b["kosten_dollar"] == round(0.21 + 0.706, 3)
