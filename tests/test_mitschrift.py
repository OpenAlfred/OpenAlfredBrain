import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)

from alfred.mitschrift import Mitschrift, lesen
from alfred.stimme import Stimmfilter

TZ = timezone(timedelta(hours=2))
JETZT = datetime(2026, 10, 4, 18, 0, tzinfo=TZ)


class Uhr:
    def __init__(self):
        self.jetzt = JETZT

    def __call__(self):
        return self.jetzt


def gepusht(m, frame, source=None):
    asyncio.run(m.on_push_frame(SimpleNamespace(frame=frame, source=source)))


def test_zustand_verstanden_und_gesagt_landen_in_der_datei(tmp_path):
    uhr = Uhr()
    pfad = tmp_path / "mitschrift.json"
    m = Mitschrift(pfad, "Raum buero", clock=uhr)
    filt = Stimmfilter()

    m.schlafen()
    assert lesen(pfad, uhr())["zustand"] == "schlaeft"
    m.aufwachen()
    gepusht(m, UserStartedSpeakingFrame())
    assert json.loads(pfad.read_text(encoding="utf-8"))["zustand"] == "hoert"

    transkript = TranscriptionFrame("Wie spät ist es?", "nutzer", "2026-10-04T18:00:01")
    gepusht(m, transkript, source=SimpleNamespace())  # von der Transkription selbst: zählt nicht
    gepusht(m, transkript, source=filt)  # hinter dem letzten Filter: zählt genau einmal
    gepusht(m, UserStoppedSpeakingFrame())
    gepusht(m, FunctionCallInProgressFrame(function_name="aktuelle_zeit", tool_call_id="1", arguments={}))
    stand = lesen(pfad, uhr())
    assert stand["zustand"] == "arbeitet: aktuelle_zeit" and stand["beschreibung"] == "arbeitet mit aktuelle_zeit"
    assert stand["verstanden"]["text"] == "Wie spät ist es?" and len(stand["zuege"]) == 1

    gepusht(m, BotStartedSpeakingFrame())
    m.gesagt("Es ist achtzehn Uhr.")
    m.antwortzeit(1.234)
    gepusht(m, BotStoppedSpeakingFrame())
    stand = lesen(pfad, uhr())
    assert stand["zustand"] == "bereit" and stand["erreichbar"] is True
    assert stand["gesagt"]["text"] == "Es ist achtzehn Uhr." and stand["antwortzeit"] == 1.23
    assert [z["wer"] for z in stand["zuege"]] == ["Alexander", "Alfred"]

    # Nach dem Einschlafen endet das Sprechen im Schlaf, nicht im Bereit.
    m.schlafen()
    gepusht(m, BotStartedSpeakingFrame())
    gepusht(m, BotStoppedSpeakingFrame())
    assert lesen(pfad, uhr())["zustand"] == "schlaeft"


def test_ueberhoerter_satz_steht_da_und_der_zustand_springt_zurueck(tmp_path):
    m = Mitschrift(tmp_path / "m.json", "Raum buero", clock=Uhr())
    gepusht(m, UserStoppedSpeakingFrame())
    assert m.stand()["zustand"] == "denkt"
    m.ueberhoert("I don't care if the check happens.")
    stand = m.stand()
    assert stand["zustand"] == "bereit" and stand["ueberhoert"]["text"] == "I don't care if the check happens."
    assert stand["zuege"] == [], "überhörte Sätze gehören nicht ins Gespräch"


def test_neue_zustaende_gehen_an_die_led(tmp_path):
    m = Mitschrift(tmp_path / "m.json", "Raum buero", clock=Uhr())
    gemeldet = []
    m.bei_zustand.append(lambda zustand: 1 / 0)  # eine kaputte Anzeige hält das Gespräch nicht auf
    m.bei_zustand.append(gemeldet.append)
    m.schlafen()
    m.schlafen()  # nichts Neues, keine Meldung
    m.aufwachen()
    gepusht(m, UserStartedSpeakingFrame())
    gepusht(m, UserStoppedSpeakingFrame())
    m.ueberhoert("Fernseher im Hintergrund")  # keine Antwort: zurück auf bereit, sonst blinkte die LED weiter
    assert gemeldet == ["schlaeft", "bereit", "hoert", "denkt", "bereit"]
    assert m.stand()["ueberhoert"]["text"] == "Fernseher im Hintergrund"


def test_unterbrochene_antwort_und_leere_texte(tmp_path):
    m = Mitschrift(tmp_path / "m.json", "Browser", clock=Uhr())
    m.gesagt("Ich wollte sagen", unterbrochen=True)
    m.gesagt("   ")
    stand = m.stand()
    assert stand["gesagt"]["text"] == "Ich wollte sagen …" and len(stand["zuege"]) == 1
    assert stand["quelle"] == "Browser"


def test_ohne_lebenszeichen_gilt_der_prozess_als_aus(tmp_path):
    uhr = Uhr()
    pfad = tmp_path / "m.json"
    assert lesen(pfad, uhr())["erreichbar"] is False
    m = Mitschrift(pfad, "Raum buero", clock=uhr)
    m.schreiben()
    assert lesen(pfad, uhr() + timedelta(seconds=10))["erreichbar"] is True
    spaeter = lesen(pfad, uhr() + timedelta(minutes=3))
    assert spaeter["erreichbar"] is False and "3 Minuten" in spaeter["beschreibung"]
    pfad.write_text("kaputt", encoding="utf-8")
    assert lesen(pfad, uhr())["beschreibung"] == "Mitschrift unlesbar"


def test_lebenszeichen_schreibt_regelmaessig(tmp_path):
    pfad = tmp_path / "m.json"
    m = Mitschrift(pfad, "Raum buero", clock=Uhr())

    async def lauf():
        task = asyncio.create_task(m.lebenszeichen(intervall=0.05))
        await asyncio.sleep(0.12)
        task.cancel()

    asyncio.run(lauf())
    assert pfad.exists() and not pfad.with_suffix(".tmp").exists()
