import asyncio
import time

from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame

from alfred.tts_cache import CachingMistralTTSService


class ServiceUnderTest(CachingMistralTTSService):
    """Statt Mistral: liefert je Aufruf zwei Audio-Frames und zählt die Aufrufe."""

    def __init__(self, cache_ordner, fehler=False, erster_ton_frist=0.2):
        super().__init__(cache_ordner=cache_ordner, api_key="x", erster_ton_frist=erster_ton_frist)
        self.aufrufe = 0
        self.fehler = fehler
        self.haenger = 0  # so viele Aufrufe bleiben vor dem ersten Ton hängen
        self.fehlstarts = 0  # so viele Aufrufe beginnen mit einem Fehler
        self._sample_rate = 24000
        self._metriken = []

    @property
    def sample_rate(self):
        return self._sample_rate

    async def start_ttfb_metrics(self):
        self._metriken.append("start")

    async def stop_ttfb_metrics(self):
        self._metriken.append("stop")


async def _mistral_run_tts(self, text, context_id):
    self.aufrufe += 1
    if self.haenger:
        self.haenger -= 1
        await asyncio.sleep(5)
    if self.fehlstarts:
        self.fehlstarts -= 1
        yield ErrorFrame(error="Dienst nicht erreichbar")
        return
    yield TTSAudioRawFrame(b"\x01\x00" * 960, 24000, 1, context_id=context_id)
    if self.fehler:
        yield ErrorFrame(error="kaputt")
    yield TTSAudioRawFrame(b"\x02\x00" * 960, 24000, 1, context_id=context_id)


def sammeln(service, text):
    async def lauf():
        return [f async for f in service.run_tts(text, "ctx")]

    return asyncio.run(lauf())


def test_kurze_saetze_kommen_beim_zweiten_mal_aus_dem_speicher(tmp_path, monkeypatch):
    from pipecat.services.mistral import tts as mistral_tts

    monkeypatch.setattr(mistral_tts.MistralTTSService, "run_tts", _mistral_run_tts)
    service = ServiceUnderTest(tmp_path / "phrasen")

    erst = sammeln(service, "Moment, ich sehe nach.")
    assert service.aufrufe == 1 and len(erst) == 2
    assert len(list((tmp_path / "phrasen").glob("*.pcm"))) == 1

    zweit = sammeln(service, "Moment, ich sehe nach.")
    assert service.aufrufe == 1  # kein Aufruf bei Mistral
    assert b"".join(f.audio for f in zweit) == b"".join(f.audio for f in erst)
    assert all(isinstance(f, TTSAudioRawFrame) and f.context_id == "ctx" for f in zweit)
    assert service._metriken[-2:] == ["start", "stop"]

    # Lange Sätze gehen immer zu Mistral.
    lang = "Das ist ein sehr langer Satz, " * 4
    sammeln(service, lang)
    sammeln(service, lang)
    assert service.aufrufe == 3


def test_fehlerhafte_antworten_werden_nicht_gespeichert(tmp_path, monkeypatch):
    from pipecat.services.mistral import tts as mistral_tts

    monkeypatch.setattr(mistral_tts.MistralTTSService, "run_tts", _mistral_run_tts)
    service = ServiceUnderTest(tmp_path / "phrasen", fehler=True)
    frames = sammeln(service, "Gern.")
    assert any(isinstance(f, ErrorFrame) for f in frames)
    assert list((tmp_path / "phrasen").glob("*.pcm")) == []


def test_bleibt_der_erste_ton_aus_wird_neu_gefragt(tmp_path, monkeypatch):
    from pipecat.services.mistral import tts as mistral_tts

    monkeypatch.setattr(mistral_tts.MistralTTSService, "run_tts", _mistral_run_tts)
    service = ServiceUnderTest(tmp_path / "phrasen")
    service.haenger = 1

    start = time.monotonic()
    frames = sammeln(service, "Gern.")
    assert time.monotonic() - start < 2.0, "der hängende Versuch wird abgebrochen, nicht abgewartet"
    assert service.aufrufe == 2
    assert [type(f) for f in frames] == [TTSAudioRawFrame, TTSAudioRawFrame]
    assert len(list((tmp_path / "phrasen").glob("*.pcm"))) == 1, "der zweite Versuch landet im Speicher"


def test_fehler_beim_ersten_versuch_fuehrt_zum_zweiten(tmp_path, monkeypatch):
    from pipecat.services.mistral import tts as mistral_tts

    monkeypatch.setattr(mistral_tts.MistralTTSService, "run_tts", _mistral_run_tts)
    service = ServiceUnderTest(tmp_path / "phrasen")
    service.fehlstarts = 1

    frames = sammeln(service, "Gern.")
    assert service.aufrufe == 2 and not any(isinstance(f, ErrorFrame) for f in frames)


def test_der_letzte_versuch_bekommt_keine_frist(tmp_path, monkeypatch):
    from pipecat.services.mistral import tts as mistral_tts

    monkeypatch.setattr(mistral_tts.MistralTTSService, "run_tts", _mistral_run_tts)

    async def kurz_haengen(self, text, context_id):
        self.aufrufe += 1
        await asyncio.sleep(0.05 if self.aufrufe == 1 else 0.5)
        yield TTSAudioRawFrame(b"\x03\x00" * 960, 24000, 1, context_id=context_id)

    monkeypatch.setattr(mistral_tts.MistralTTSService, "run_tts", kurz_haengen)
    service = ServiceUnderTest(tmp_path / "phrasen", erster_ton_frist=0.1)
    service.haenger = 0

    # Erster Versuch schnell genug: kein zweiter.
    assert len(sammeln(service, "Gern.")) == 1 and service.aufrufe == 1
    # Zweiter Satz: die ersten beiden Versuche hängen (Fristen 0,1 und 0,15 s), der dritte dauert
    # 0,5 s und wird abgewartet.
    service.aufrufe = 0

    async def erst_langsam(self, text, context_id):
        self.aufrufe += 1
        await asyncio.sleep(5 if self.aufrufe == 1 else 0.5)
        yield TTSAudioRawFrame(b"\x04\x00" * 960, 24000, 1, context_id=context_id)

    monkeypatch.setattr(mistral_tts.MistralTTSService, "run_tts", erst_langsam)
    assert len(sammeln(service, "Danke.")) == 1 and service.aufrufe == 3


def test_emojis_kommen_nicht_in_die_sprachausgabe():
    from alfred.tts_cache import SprechtextFilter

    gefiltert = asyncio.run(SprechtextFilter().filter('Soll ich "Alex ist der Daddy 👑🔥" an Jan schicken? 👍🏽'))
    assert gefiltert == 'Soll ich "Alex ist der Daddy " an Jan schicken? '
    assert asyncio.run(SprechtextFilter().filter("Grüße aus Köln, 15 € und 3 × 4")) == "Grüße aus Köln, 15 € und 3 × 4"


def test_markdown_reste_kommen_nicht_in_die_sprachausgabe():
    """Wie in der Pipeline: erst Pipecats MarkdownTextFilter, der nur Paare entfernt, dann der Rest."""
    from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

    from alfred.tts_cache import SprechtextFilter

    async def kette(text):
        for filter in (MarkdownTextFilter(), SprechtextFilter()):
            text = await filter.filter(text)
        return text

    # Gespräch vom 16.10.2026: ein einzelner Unterstrich ohne Gegenstück.
    assert asyncio.run(kette("Notizen. _diskret ist mein zweiter Vorname.")) == "Notizen. diskret ist mein zweiter Vorname."
    assert asyncio.run(kette("Das ist *wichtig* und **fett**.")) == "Das ist wichtig und fett."
    assert asyncio.run(kette("Stern* und `Code")) == "Stern und Code"
