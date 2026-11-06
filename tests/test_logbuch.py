import asyncio

from loguru import logger

from alfred import logbuch
from alfred.pipeline import beaufsichtigt


def test_konsolenfilter_laesst_den_laerm_weg():
    assert not logbuch.leise({"message": "MistralLLMService#0: System instruction composed: Du bist …"})
    assert not logbuch.leise({"message": "MistralLLMService#0: Using system instruction: Du bist …"})
    assert not logbuch.leise({"message": "MistralLLMService#0: auto-registered handler for advertised direct function 'wetter'"})
    assert logbuch.leise({"message": "MistralLLMService#0: updated settings fields: {'system_instruction'}"})
    assert logbuch.leise({"message": "Antwortzeit  2.069s  TOTAL"})


def test_logdateien_und_letzte_fehler(tmp_path):
    ordner = logbuch.einrichten("probe", tmp_path / "logs", konsole=False)
    assert logbuch.einrichten("nochmal", tmp_path / "anders", konsole=False) == tmp_path / "logs", "einmal je Prozess"
    try:
        logger.info("läuft")
        logger.warning("erste Warnung")
        try:
            raise RuntimeError("kaputt")
        except RuntimeError:
            logger.exception("mit Traceback")
        logger.error("zuletzt")
    finally:
        logbuch.abhaengen()  # schreibt die Warteschlange und schließt die Dateien

    assert "läuft" in (ordner / "probe.log").read_text(encoding="utf-8")
    assert "läuft" not in (ordner / "fehler.log").read_text(encoding="utf-8")

    fehler = logbuch.letzte_fehler(ordner, 2)
    assert len(fehler) == 2
    assert "zuletzt" in fehler[0] and "mit Traceback" in fehler[1]
    assert "RuntimeError: kaputt" in fehler[1], "der Traceback bleibt bei seinem Eintrag"
    assert all(e.startswith("20") for e in fehler)
    assert len(logbuch.letzte_fehler(ordner)) == 3


def test_letzte_fehler_ohne_datei(tmp_path):
    assert logbuch.letzte_fehler(tmp_path) == []


def test_aufsicht_startet_eine_kaputte_aufgabe_neu():
    laeufe = []

    async def wackelig():
        laeufe.append(len(laeufe))
        if len(laeufe) < 3:
            raise ValueError("Datei kaputt")
        await asyncio.sleep(10)

    async def ablauf():
        task = asyncio.create_task(beaufsichtigt("Probe", wackelig, pause=0.05))
        await asyncio.sleep(0.4)
        assert laeufe == [0, 1, 2], "zwei Abstürze, dann läuft sie"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(ablauf())


def test_aufsicht_laesst_ein_regulaeres_ende_durch():
    async def fertig():
        return None

    asyncio.run(asyncio.wait_for(beaufsichtigt("Probe", fertig, pause=0.05), 1.0))
