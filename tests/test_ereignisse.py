import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from datetime import time as tageszeit
from types import SimpleNamespace

import numpy as np
import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import run_test

from alfred.config import Proaktiv
from alfred.ereignisse import (
    ANSAGE_SPERRE,
    KLINGEL_RATE,
    UNGEFRAGT,
    Ansage,
    Ansagewaechter,
    Ereignis,
    Fremd,
    Gespraechszustand,
    Tageszaehler,
    Zusteller,
    Zustellregeln,
    antwort_art,
    ereignis_text,
    in_ruhezeit,
    ist_gesperrt,
    klingelton,
)
from alfred.gedaechtnis import Gedaechtnis

TZ = timezone(timedelta(hours=2))
ABENDS = datetime(2026, 10, 5, 23, 0, tzinfo=TZ)
MORGENS = datetime(2026, 10, 6, 8, 0, tzinfo=TZ)


class Uhr:
    now = 0.0


class Wanduhr:
    def __init__(self, jetzt: datetime):
        self.jetzt = jetzt

    def __call__(self) -> datetime:
        return self.jetzt


def gepusht(zustand, frame):
    asyncio.run(zustand.on_push_frame(SimpleNamespace(frame=frame)))


def test_ruhig_seit_folgt_dem_gespraech():
    uhr = Uhr()
    zustand = Gespraechszustand(clock=lambda: uhr.now)

    uhr.now = 5.0
    assert zustand.ruhig_seit() == 5.0
    gepusht(zustand, UserStartedSpeakingFrame())
    uhr.now = 9.0
    assert zustand.ruhig_seit() == 0.0
    gepusht(zustand, UserStoppedSpeakingFrame())
    gepusht(zustand, BotStartedSpeakingFrame())
    assert zustand.ruhig_seit() == 0.0
    gepusht(zustand, BotStoppedSpeakingFrame())
    uhr.now = 11.0
    assert zustand.ruhig_seit() == 2.0


def test_zusteller_spricht_erst_bei_ruhe():
    async def lauf():
        uhr = Uhr()
        zustand = Gespraechszustand(clock=lambda: uhr.now)
        context = SimpleNamespace(messages=[], add_message=lambda m: context.messages.append(m))
        frames = []
        gate = SimpleNamespace(geweckt=[])

        async def queue_frames(fs):
            frames.extend(fs)

        async def aufwecken(grund):
            gate.geweckt.append(grund)

        gate.aufwecken = aufwecken
        worker = SimpleNamespace(queue_frames=queue_frames)
        ereignisse = asyncio.Queue()
        aufgabe = asyncio.create_task(Zusteller(ereignisse, context, worker, zustand, gate=gate).laufen())

        await zustand.on_push_frame(SimpleNamespace(frame=UserStartedSpeakingFrame()))
        await ereignisse.put("Erinnere jetzt an: Wäsche")
        await asyncio.sleep(0.5)
        assert context.messages == [], "solange gesprochen wird, bleibt das Ereignis liegen"

        await zustand.on_push_frame(SimpleNamespace(frame=UserStoppedSpeakingFrame()))
        uhr.now = 10.0
        await asyncio.sleep(0.5)
        aufgabe.cancel()

        assert context.messages == [{"role": "developer", "content": "Erinnere jetzt an: Wäsche"}]
        assert len(frames) == 1 and isinstance(frames[0], LLMRunFrame)
        assert gate.geweckt == ["Ereignis"]

    asyncio.run(lauf())


def test_ansage_klingelt_und_spricht_am_modell_vorbei():
    async def lauf():
        uhr = Uhr()
        zustand = Gespraechszustand(clock=lambda: uhr.now)
        uhr.now = 10.0  # lange Ruhe, der Zusteller darf sofort
        context = SimpleNamespace(messages=[], add_message=lambda m: context.messages.append(m))
        frames = []

        async def queue_frames(fs):
            frames.extend(fs)

        ereignisse = asyncio.Queue()
        aufgabe = asyncio.create_task(
            Zusteller(ereignisse, context, SimpleNamespace(queue_frames=queue_frames), zustand).laufen()
        )
        await ereignisse.put(Ansage("Die Zeit ist um: Nudeln."))
        await ereignisse.put(Ansage("Ohne Klingel.", klingeln=False))
        await asyncio.sleep(0.3)
        aufgabe.cancel()

        klingel, gesprochen, leise = frames
        assert isinstance(klingel, OutputAudioRawFrame) and klingel.sample_rate == KLINGEL_RATE
        assert isinstance(gesprochen, TTSSpeakFrame) and gesprochen.text == "Die Zeit ist um: Nudeln."
        assert isinstance(leise, TTSSpeakFrame) and leise.text == "Ohne Klingel."
        # Kein Umweg über das Modell, aber im Verlauf, damit ein "Danke" danach einen Bezug hat.
        assert [m["role"] for m in context.messages] == ["assistant", "assistant"]

    asyncio.run(lauf())


def test_klingelton_ist_kurz_und_hoerbar():
    ton = np.frombuffer(klingelton(), dtype=np.int16)
    assert 0.5 < len(ton) / KLINGEL_RATE < 1.5
    assert 5000 < np.abs(ton).max() < 32767
    assert klingelton() is klingelton(), "einmal berechnet, dann aus dem Speicher"


def test_zustand_merkt_sich_die_letzte_modellantwort_einmal():
    from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame

    zustand = Gespraechszustand()

    def hop(frame, erstes=True):
        asyncio.run(zustand.on_push_frame(SimpleNamespace(frame=frame, first_push=erstes)))

    hop(LLMFullResponseStartFrame())
    for teil in ("Alles klar, ", "dann richte ich ", "die Erinnerung ein."):
        text = LLMTextFrame(teil)
        hop(text)
        hop(text, erstes=False)  # weitergereicht: nicht doppelt
    hop(LLMFullResponseEndFrame())
    assert zustand.modellantwort_abholen() == "Alles klar, dann richte ich die Erinnerung ein."
    assert zustand.modellantwort_abholen() is None, "nur einmal"


def test_ansage_steht_als_fremder_inhalt_im_verlauf():
    async def lauf():
        ereignisse: asyncio.Queue = asyncio.Queue()
        context = SimpleNamespace(messages=[], add_message=lambda m: context.messages.append(m))

        async def queue_frames(frames):
            pass

        uhr = Uhr()
        zustand = Gespraechszustand(clock=lambda: uhr.now)
        uhr.now = 10.0  # lange Ruhe, der Zusteller darf sofort
        await ereignisse.put(Ansage("Neue WhatsApp von Jan: Hi", verlauf="[Vorgelesen …] Hi"))
        zusteller = asyncio.create_task(Zusteller(ereignisse, context, SimpleNamespace(queue_frames=queue_frames), zustand).laufen())
        await asyncio.sleep(0.1)
        zusteller.cancel()
        return context.messages

    assert asyncio.run(lauf()) == [{"role": "assistant", "content": "[Vorgelesen …] Hi"}]


# --- Zustellregeln für Ungefragtes -------------------------------------------------------------


def regeln(tmp_path, uhr, modus="standard", gesperrt=(), **einstellungen) -> Zustellregeln:
    """modus: Name des Modus oder eine Funktion, die ihn liefert (für einen Wechsel mitten im Test)."""
    return Zustellregeln(
        Proaktiv(**einstellungen),
        Tageszaehler(tmp_path / "proaktiv.json"),
        modus=modus if callable(modus) else lambda: modus,
        gesperrt=lambda quelle: quelle in gesperrt,
        clock=uhr,
    )


def ungefragt(**felder) -> Ereignis:
    return Ereignis("Hinweis vom System: Routine fällig.", klasse=UNGEFRAGT, quelle="Routine 'Briefing'", **felder)


@pytest.mark.parametrize(
    ("jetzt", "von", "bis", "erwartet"),
    [
        ("23:00", "22:00", "07:00", True),
        ("06:59", "22:00", "07:00", True),
        ("07:00", "22:00", "07:00", False),
        ("12:00", "22:00", "07:00", False),
        ("13:00", "12:00", "14:00", True),
        ("14:00", "12:00", "14:00", False),
        ("13:00", "13:00", "13:00", False),
    ],
)
def test_ruhezeit_auch_ueber_mitternacht(jetzt, von, bis, erwartet):
    def zeit(text):
        return tageszeit(*(int(teil) for teil in text.split(":")))

    assert in_ruhezeit(zeit(jetzt), zeit(von), zeit(bis)) is erwartet


def test_proaktiv_einstellungen_pruefen_die_uhrzeiten():
    assert Proaktiv(ruhe_von="22:00").ruhe_von_zeit == tageszeit(22, 0)
    # YAML liest 22:00 ohne Anführungszeichen als 22 mal 60 Minuten.
    assert Proaktiv(ruhe_von=1320).ruhe_von == "22:00"
    assert Proaktiv(ruhe_bis="7:5").ruhe_bis_zeit == tageszeit(7, 5)
    assert Proaktiv().modi == {"feierabend": "nur_dringendes"}
    with pytest.raises(ValueError):
        Proaktiv(ruhe_von="25:00")
    with pytest.raises(ValueError):
        Proaktiv(modi={"fokus": "manchmal"})


def test_angefordertes_kommt_immer_durch(tmp_path):
    r = regeln(tmp_path, Wanduhr(ABENDS), modus="feierabend", max_pro_tag=0)
    assert r.pruefen(Ereignis("Der Denker ist fertig.")).erlaubt
    assert r.pruefen(Ereignis(Ansage("Die Zeit ist um."))).erlaubt
    assert not r.pruefen(ungefragt()).erlaubt


def test_ungefragtes_wartet_in_der_ruhezeit(tmp_path):
    uhr = Wanduhr(ABENDS)
    r = regeln(tmp_path, uhr)
    entscheidung = r.pruefen(ungefragt())
    assert not entscheidung.erlaubt and not entscheidung.endgueltig
    assert "Ruhezeit bis 07:00" in entscheidung.grund
    assert not r.pruefen(ungefragt(dringend=True)).erlaubt, "nachts gehört auch Dringendes aufs Telefon"
    uhr.jetzt = MORGENS.replace(hour=6, minute=30)
    assert not r.pruefen(ungefragt()).erlaubt, "der frühe Morgen gehört zur Ruhezeit"
    uhr.jetzt = MORGENS
    assert r.pruefen(ungefragt()).erlaubt


def test_tagesgrenze_zaehlt_in_der_datei_und_ueberlebt_den_neustart(tmp_path):
    uhr = Wanduhr(MORGENS)
    r = regeln(tmp_path, uhr, max_pro_tag=2)
    for _ in range(2):
        assert r.pruefen(ungefragt()).erlaubt
        r.gesprochen(ungefragt())
    gestoppt = r.pruefen(ungefragt())
    assert not gestoppt.erlaubt and "2 ungefragte Ansagen" in gestoppt.grund
    assert r.pruefen(ungefragt(dringend=True)).erlaubt, "Dringendes darf über die Grenze"
    assert r.gesprochen(Ereignis("Angefordertes zählt nicht")) is None

    daten = json.loads((tmp_path / "proaktiv.json").read_text(encoding="utf-8"))
    assert daten == {"tag": "2026-10-06", "ansagen": ["08:00 Routine 'Briefing'"] * 2}

    neu = regeln(tmp_path, uhr, max_pro_tag=2)
    assert not neu.pruefen(ungefragt()).erlaubt, "nach einem Neustart zählt der Tag weiter"
    uhr.jetzt = MORGENS + timedelta(days=1)
    assert neu.pruefen(ungefragt()).erlaubt, "am nächsten Tag von vorn"


def test_kaputte_zaehlerdatei_haelt_alfred_nicht_auf(tmp_path):
    (tmp_path / "proaktiv.json").write_text("{kaputt", encoding="utf-8")
    assert Tageszaehler(tmp_path / "proaktiv.json").stand(MORGENS.date()) == 0


def test_modus_feierabend_laesst_nur_dringendes_durch(tmp_path):
    uhr = Wanduhr(MORGENS)
    feierabend = regeln(tmp_path, uhr, modus="feierabend")
    assert "nur Dringendes" in feierabend.pruefen(ungefragt()).grund
    assert feierabend.pruefen(ungefragt(dringend=True)).erlaubt
    nichts = regeln(tmp_path, uhr, modus="fokus", modi={"fokus": "nichts"})
    assert not nichts.pruefen(ungefragt(dringend=True)).erlaubt
    assert regeln(tmp_path, uhr, modus="sparring").pruefen(ungefragt()).erlaubt, "nicht genannte Modi erlauben alles"


def test_modus_gaeste_laesst_nichts_durch(tmp_path):
    uhr = Wanduhr(MORGENS)
    gaeste = regeln(tmp_path, uhr, modus="gaeste", modi={"gaeste": "nichts"})
    assert not gaeste.pruefen(ungefragt()).erlaubt
    assert not gaeste.pruefen(ungefragt(dringend=True)).erlaubt


def test_gaestemodus_haelt_auch_angefordertes_zurueck(tmp_path):
    """Erinnerung und WhatsApp gehören Alexander, nicht dem Besuch; nur der Timer klingelt für alle."""
    gaeste = regeln(tmp_path, Wanduhr(MORGENS), modus="gaeste")
    erinnerung = Ereignis("Hinweis vom System: Die Erinnerung 'Chiropraktiker' ist jetzt fällig.")
    entscheidung = gaeste.pruefen(erinnerung)
    assert not entscheidung.erlaubt and not entscheidung.endgueltig and entscheidung.grund == "Gäste da"
    whatsapp = Ereignis(Ansage("Neue WhatsApp von Jan: Hallo"), herkunft="WhatsApp von Jan")
    assert not gaeste.pruefen(whatsapp).erlaubt
    assert gaeste.pruefen(Ereignis(Ansage("Die Zeit ist um."), vor_gaesten=True)).erlaubt
    assert regeln(tmp_path, Wanduhr(MORGENS)).pruefen(erinnerung).erlaubt


def test_veraltetes_verfaellt_und_aufgeschobenes_wartet(tmp_path):
    uhr = Wanduhr(MORGENS)
    r = regeln(tmp_path, uhr)
    verfallen = r.pruefen(ungefragt(gueltig_bis=MORGENS - timedelta(minutes=1)))
    assert not verfallen.erlaubt and verfallen.endgueltig
    spaeter = r.pruefen(ungefragt(nicht_vor=MORGENS + timedelta(minutes=30)))
    assert not spaeter.erlaubt and not spaeter.endgueltig and "08:30" in spaeter.grund
    uhr.jetzt = MORGENS + timedelta(minutes=30)
    assert r.pruefen(ungefragt(nicht_vor=MORGENS + timedelta(minutes=30))).erlaubt
    # Zeiten ohne Zeitzone bekommen die der Uhr, statt den Vergleich scheitern zu lassen.
    assert r.pruefen(ungefragt(gueltig_bis=datetime(2026, 10, 6, 9, 0))).erlaubt


def test_regel_schaltet_eine_quelle_ab(tmp_path):
    regel = f"{ANSAGE_SPERRE}Routine 'Briefing'"
    assert ist_gesperrt(["Schmeichle nicht.", regel.upper()], "Routine 'Briefing'")
    assert not ist_gesperrt([regel], "Routine 'Abendbriefing'")
    entscheidung = regeln(tmp_path, Wanduhr(MORGENS), gesperrt={"Routine 'Briefing'"}).pruefen(ungefragt())
    assert not entscheidung.erlaubt and entscheidung.endgueltig


# --- Zusteller mit Regeln ---------------------------------------------------------------------


class Aufbau:
    """Zusteller mit Fakes: Wanduhr, Kontext, Worker, Wächter-Attrappe und Push-Sammler."""

    def __init__(
        self,
        tmp_path,
        jetzt: datetime,
        *,
        push: bool = False,
        bereit: asyncio.Event | None = None,
        quittung_secs: float | None = None,
        ereignisse: asyncio.Queue | None = None,
        **einstellungen,
    ):
        self.uhr = Wanduhr(jetzt)
        uhr = self.ruhe_uhr = Uhr()
        self.zustand = Gespraechszustand(clock=lambda: uhr.now)
        uhr.now = 10.0  # lange Ruhe, der Zusteller darf sofort
        self.context = SimpleNamespace(messages=[])
        self.context.add_message = self.context.messages.append
        self.frames: list = []
        self.gepusht: list[str] = []
        self.gemeldet: list[Ereignis] = []
        # Im Raum hält die Aufsicht die Warteschlange über den Neuaufbau: der nächste Anlauf bekommt dieselbe.
        self.ereignisse: asyncio.Queue = ereignisse if ereignisse is not None else asyncio.Queue()

        async def queue_frames(fs):
            self.frames.extend(fs)

        async def benachrichtigen(text):
            if text == "kaputt":
                raise RuntimeError("Server weg")
            self.gepusht.append(text)

        self.zusteller = Zusteller(
            self.ereignisse,
            self.context,
            SimpleNamespace(queue_frames=queue_frames),
            self.zustand,
            regeln=regeln(tmp_path, self.uhr, **einstellungen),
            waechter=SimpleNamespace(ansage_gesprochen=self.gemeldet.append),
            benachrichtigen=benachrichtigen if push else None,
            takt=0.05,
            bereit=bereit,
            quittung_secs=quittung_secs,
        )

    def gesprochen(self) -> list[str]:
        return [m["content"] for m in self.context.messages if m["role"] == "developer"]

    async def spricht(self) -> None:
        """Der Lautsprecher spielt Alfreds Antwort ab; danach ist wieder lange Ruhe."""
        for frame in (BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()):
            await self.zustand.on_push_frame(SimpleNamespace(frame=frame))
        self.ruhe_uhr.now += 10.0


async def bis(bedingung, frist: float = 3.0) -> None:
    """Wartet, bis die Bedingung gilt; der Zusteller läuft mit echten Pausen."""
    ende = time.monotonic() + frist
    while not bedingung():
        assert time.monotonic() < ende, "Bedingung nicht rechtzeitig erfüllt"
        await asyncio.sleep(0.02)


def test_zusteller_haelt_ungefragtes_in_der_ruhezeit_zurueck_und_spricht_es_danach(tmp_path):
    async def lauf():
        a = Aufbau(tmp_path, ABENDS)
        aufgabe = asyncio.create_task(a.zusteller.laufen())
        await a.ereignisse.put(ungefragt())
        await a.ereignisse.put("Hinweis vom System: Der Denker ist fertig.")
        await asyncio.sleep(0.3)
        assert a.gesprochen() == ["Hinweis vom System: Der Denker ist fertig."], "Angefordertes geht vor"
        assert [e.quelle for e in a.zusteller.wartend] == ["Routine 'Briefing'"]
        assert a.gemeldet == []

        a.uhr.jetzt = MORGENS
        await asyncio.sleep(0.3)
        aufgabe.cancel()
        assert a.gesprochen()[-1] == "Hinweis vom System: Routine fällig."
        assert a.zusteller.wartend == [] and a.gemeldet == [ungefragt()]
        daten = json.loads((tmp_path / "proaktiv.json").read_text(encoding="utf-8"))
        assert daten["ansagen"] == ["08:00 Routine 'Briefing'"]

    asyncio.run(lauf())


def test_zusteller_spricht_privates_erst_wenn_der_besuch_weg_ist(tmp_path):
    """Gespräch vom 16.10.2026: Vor dem Besuch hätte Alfred eine WhatsApp wörtlich vorgelesen."""

    async def lauf():
        modus = ["gaeste"]
        a = Aufbau(tmp_path, MORGENS, modus=lambda: modus[0])
        aufgabe = asyncio.create_task(a.zusteller.laufen())
        await a.ereignisse.put("Hinweis vom System: Die Erinnerung 'Chiropraktiker' ist jetzt fällig.")
        await a.ereignisse.put(Fremd(Ansage("Neue WhatsApp von Jan: Hallo"), "WhatsApp von Jan"))
        await a.ereignisse.put(Ereignis(Ansage("Die Zeit ist um."), vor_gaesten=True))
        await asyncio.sleep(0.3)

        def gesagt():
            return [f.text for f in a.frames if isinstance(f, TTSSpeakFrame)]

        assert gesagt() == ["Die Zeit ist um."] and a.gesprochen() == [], "nur der Timer klingelt"
        assert len(a.zusteller.wartend) == 2

        modus[0] = "standard"
        await bis(lambda: not a.zusteller.wartend)
        aufgabe.cancel()
        assert a.gesprochen() == ["Hinweis vom System: Die Erinnerung 'Chiropraktiker' ist jetzt fällig."]
        assert gesagt()[-1] == "Neue WhatsApp von Jan: Hallo"

    asyncio.run(lauf())


def test_zusteller_schickt_zurueckgehaltenes_als_push(tmp_path):
    async def lauf():
        a = Aufbau(tmp_path, MORGENS, push=True, modi={"standard": "nur_dringendes"})
        aufgabe = asyncio.create_task(a.zusteller.laufen())
        await a.ereignisse.put(ungefragt(push="Alfred hätte etwas zu sagen: Briefing"))
        await a.ereignisse.put(ungefragt(push="kaputt"))
        await a.ereignisse.put(ungefragt())
        await asyncio.sleep(0.3)
        aufgabe.cancel()
        assert a.gepusht == ["Alfred hätte etwas zu sagen: Briefing"]
        assert a.gesprochen() == [] and a.gemeldet == []
        # Gepushtes ist erledigt; ohne Push-Text oder mit kaputtem Server wartet die Ansage weiter.
        assert [e.push for e in a.zusteller.wartend] == ["kaputt", ""]

    asyncio.run(lauf())


def test_zusteller_laesst_veraltetes_verfallen(tmp_path):
    async def lauf():
        a = Aufbau(tmp_path, ABENDS)
        aufgabe = asyncio.create_task(a.zusteller.laufen())
        await a.ereignisse.put(ungefragt(gueltig_bis=ABENDS + timedelta(hours=2)))
        await asyncio.sleep(0.2)
        assert len(a.zusteller.wartend) == 1
        a.uhr.jetzt = MORGENS  # Ruhezeit vorbei, aber die Ansage ist veraltet
        await asyncio.sleep(0.2)
        aufgabe.cancel()
        assert a.zusteller.wartend == [] and a.gesprochen() == []

    asyncio.run(lauf())


# --- Zustellung über einen gescheiterten Anlauf hinweg (16.10.2026) ------------------------------


def test_zustand_zaehlt_jeden_hoerbaren_sprechbeginn_einmal():
    zustand = Gespraechszustand()
    frame = BotStartedSpeakingFrame()
    for erstes in (True, False):  # weitergereicht: nicht doppelt
        asyncio.run(zustand.on_push_frame(SimpleNamespace(frame=frame, first_push=erstes)))
    assert zustand.sprechbeginne == 1


def test_zusteller_nimmt_erst_etwas_wenn_die_pipeline_laeuft(tmp_path):
    async def lauf():
        bereit = asyncio.Event()
        a = Aufbau(tmp_path, MORGENS, bereit=bereit)
        aufgabe = asyncio.create_task(a.zusteller.laufen())
        await a.ereignisse.put("Hinweis vom System: Erinnerung fällig.")
        await asyncio.sleep(0.2)
        assert a.gesprochen() == [] and a.frames == [], "vor dem Start nichts in den Worker"
        assert a.ereignisse.qsize() == 1, "es wartet in der Warteschlange, die den Anlauf überlebt"
        bereit.set()
        await bis(lambda: a.gesprochen())
        aufgabe.cancel()
        assert a.gesprochen() == ["Hinweis vom System: Erinnerung fällig."]

    asyncio.run(lauf())


def test_ungefragtes_zaehlt_erst_wenn_alfred_hoerbar_spricht(tmp_path):
    async def lauf():
        a = Aufbau(tmp_path, MORGENS, quittung_secs=30)
        aufgabe = asyncio.create_task(a.zusteller.laufen())
        await a.ereignisse.put(ungefragt())
        await bis(lambda: a.gesprochen())
        await asyncio.sleep(0.2)
        assert a.gemeldet == [] and not (tmp_path / "proaktiv.json").exists(), "übergeben, aber noch kein Ton"
        await a.spricht()
        await bis(lambda: a.gemeldet)
        aufgabe.cancel()
        assert a.gemeldet == [ungefragt()]
        daten = json.loads((tmp_path / "proaktiv.json").read_text(encoding="utf-8"))
        assert daten["ansagen"] == ["08:00 Routine 'Briefing'"]

    asyncio.run(lauf())


def test_gescheiterter_anlauf_gibt_ungesprochenes_an_den_naechsten(tmp_path):
    # 16.10.2026: Der Worker brach beim Öffnen des Mikrofons ab, der Zusteller sprach trotzdem drei
    # Erinnerungen und das Morgenbriefing hinein; der Neustart hatte nichts mehr davon.
    async def lauf():
        erster = Aufbau(tmp_path, MORGENS, quittung_secs=30)
        aufgabe = asyncio.create_task(erster.zusteller.laufen())
        for eintrag in ("Hinweis vom System: Erinnerung eins.", ungefragt(), Ansage("Die Zeit ist um.")):
            await erster.ereignisse.put(eintrag)
        await bis(lambda: erster.gesprochen())
        aufgabe.cancel()  # der Worker ist weg, abschliessen bricht die Hintergrundaufgaben ab
        await asyncio.gather(aufgabe, return_exceptions=True)
        zurueck = erster.zusteller.zurueckgeben()
        assert [e.text for e in zurueck] == ["Hinweis vom System: Erinnerung eins."]
        assert erster.gemeldet == [] and not (tmp_path / "proaktiv.json").exists()

        zweiter = Aufbau(tmp_path, MORGENS, quittung_secs=30, ereignisse=erster.ereignisse)
        aufgabe = asyncio.create_task(zweiter.zusteller.laufen())
        for anzahl in (1, 2):
            await bis(lambda: len(zweiter.gesprochen()) == anzahl)
            await zweiter.spricht()
        await bis(lambda: zweiter.frames[-1:] and isinstance(zweiter.frames[-1], TTSSpeakFrame))
        await zweiter.spricht()
        await bis(lambda: zweiter.ereignisse.empty() and not zweiter.zusteller.wartend)
        aufgabe.cancel()
        # In der alten Reihenfolge: zuerst das unterbrochene Ereignis, dann was dahinter wartete.
        assert zweiter.gesprochen() == ["Hinweis vom System: Erinnerung eins.", "Hinweis vom System: Routine fällig."]
        assert zweiter.frames[-1].text == "Die Zeit ist um."
        assert zweiter.gemeldet == [ungefragt()]
        daten = json.loads((tmp_path / "proaktiv.json").read_text(encoding="utf-8"))
        assert daten["ansagen"] == ["08:00 Routine 'Briefing'"], "einmal gezählt, im Anlauf, der es sprach"

    asyncio.run(lauf())


def test_zurueckgehaltenes_ueberlebt_den_neuaufbau(tmp_path):
    async def lauf():
        erster = Aufbau(tmp_path, ABENDS)
        aufgabe = asyncio.create_task(erster.zusteller.laufen())
        await erster.ereignisse.put(ungefragt())
        await bis(lambda: erster.zusteller.wartend)
        aufgabe.cancel()
        await asyncio.gather(aufgabe, return_exceptions=True)
        (zurueck,) = erster.zusteller.zurueckgeben()
        assert not zurueck.gemeldet, "wartet es im nächsten Anlauf wieder, steht das erneut im Log"

        zweiter = Aufbau(tmp_path, MORGENS, ereignisse=erster.ereignisse)  # Ruhezeit vorbei
        aufgabe = asyncio.create_task(zweiter.zusteller.laufen())
        await bis(lambda: zweiter.gesprochen())
        aufgabe.cancel()
        assert zweiter.gesprochen() == ["Hinweis vom System: Routine fällig."]

    asyncio.run(lauf())


def test_ohne_ton_gilt_ein_ereignis_nach_der_frist_als_zugestellt(tmp_path):
    # Läuft die Pipeline, antwortet das Modell aber nicht hörbar (Fehler bei Mistral), hält ein
    # einzelnes Ereignis nicht alle anderen auf und kommt auch nicht doppelt.
    async def lauf():
        a = Aufbau(tmp_path, MORGENS, quittung_secs=0.2)
        aufgabe = asyncio.create_task(a.zusteller.laufen())
        await a.ereignisse.put(ungefragt())
        await a.ereignisse.put("Hinweis vom System: Der Denker ist fertig.")
        await bis(lambda: len(a.gesprochen()) == 2)
        assert a.gemeldet == [ungefragt()]
        await asyncio.sleep(0.4)  # auch die Frist des zweiten läuft ab
        aufgabe.cancel()
        await asyncio.gather(aufgabe, return_exceptions=True)
        assert a.zusteller.zurueckgeben() == [], "nichts bleibt für einen nächsten Anlauf"

    asyncio.run(lauf())


# --- Antworten auf eine ungefragte Ansage ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("Alfred, nicht jetzt.", "nicht_jetzt"),
        ("Nein, jetzt nicht, ich telefoniere gerade.", "nicht_jetzt"),
        ("Später.", "nicht_jetzt"),
        ("Später habe ich einen Termin beim Zahnarzt.", None),
        ("Keine Zeit", "nicht_jetzt"),
        ("Lass das.", "nie_wieder"),
        ("Nein, lass das bitte", "nie_wieder"),
        ("Hör auf damit!", "nie_wieder"),
        ("Das will ich nicht mehr hören.", "nie_wieder"),
        ("Danke.", None),
        ("Wie wird das Wetter morgen?", None),
    ],
)
def test_antworten_auf_eine_ansage_erkennen(text, erwartet):
    assert antwort_art(text)[0] == erwartet


def ressourcen(tmp_path):
    return SimpleNamespace(
        persona=SimpleNamespace(nutzer="Alexander"),
        gedaechtnis=Gedaechtnis(tmp_path / "g"),
        ereignisse=asyncio.Queue(),
        offene_freigabe=None,
        systemprompt=None,
    )


def kontext():
    context = SimpleNamespace(messages=[])
    context.add_message = context.messages.append
    return context


def antwort(res, context, ereignis, text, jetzt=MORGENS) -> Ansagewaechter:
    """Alexanders Satz durch den Wächter, nachdem der Zusteller die Ansage gemeldet hat."""
    waechter = Ansagewaechter(res, context, aufschieben=timedelta(minutes=30), clock=lambda: jetzt)
    if ereignis is not None:
        waechter.ansage_gesprochen(ereignis)
    frames = [TranscriptionFrame(text, "nutzer", "2026-10-06T08:00:00")]
    down, _ = asyncio.run(run_test(waechter, frames_to_send=frames))
    assert [f.text for f in down if isinstance(f, TranscriptionFrame)] == [text], "das Transkript geht weiter"
    return waechter


def test_nicht_jetzt_schiebt_die_ansage_auf(tmp_path):
    res, context = ressourcen(tmp_path), kontext()
    ereignis = ungefragt(gueltig_bis=MORGENS + timedelta(minutes=10))
    waechter = antwort(res, context, ereignis, "Alfred, nicht jetzt.")

    neu = res.ereignisse.get_nowait()
    assert neu.nicht_vor == MORGENS + timedelta(minutes=30) and neu.quelle == ereignis.quelle
    assert neu.gueltig_bis == MORGENS + timedelta(minutes=60), "aufgeschoben heißt gewollt: verfällt nicht vorher"
    assert waechter.offen is None and res.offene_freigabe is None
    (hinweis,) = context.messages
    assert hinweis["role"] == "developer" and "acht Uhr dreißig" in hinweis["content"]


def test_lass_das_schlaegt_eine_regel_vor_die_erst_ein_ja_anlegt(tmp_path):
    res, context = ressourcen(tmp_path), kontext()
    antwort(res, context, ungefragt(), "Nein, lass das.")

    assert res.ereignisse.empty()
    assert res.offene_freigabe.beschreibung == f"Neue Regel: {ANSAGE_SPERRE}Routine 'Briefing'"
    (hinweis,) = context.messages
    assert "Routine 'Briefing'" in hinweis["content"] and "bestaetigen" in hinweis["content"]
    assert not ist_gesperrt(res.gedaechtnis.regeln(), "Routine 'Briefing'"), "ohne Ja keine Regel"
    # Das Ja läuft über das Werkzeug bestaetigen; danach greift die Regel im Code.
    ergebnis = asyncio.run(res.offene_freigabe.ausfuehren())
    assert ergebnis["gemerkt"] == f"{ANSAGE_SPERRE}Routine 'Briefing'"
    assert ist_gesperrt(res.gedaechtnis.regeln(), "Routine 'Briefing'")


def test_nein_lass_das_hinter_dem_rueckfragewaechter_laesst_die_regel_offen(tmp_path):
    # Reihenfolge wie in pipeline.py: Stünde der Ansagewächter vorn, verwürfe der Rückfragewächter
    # die eben angelegte Regel-Rückfrage wegen des Neins, und der Satz zählte als Antwort darauf.
    from pipecat.pipeline.pipeline import Pipeline

    from alfred.werkzeuge import Rueckfragewaechter

    res, context = ressourcen(tmp_path), kontext()
    waechter = Ansagewaechter(res, context, aufschieben=timedelta(minutes=30), clock=lambda: MORGENS)
    waechter.ansage_gesprochen(ungefragt())
    frames = [TranscriptionFrame("Nein, lass das.", "nutzer", "2026-10-06T08:00:00")]
    asyncio.run(run_test(Pipeline([Rueckfragewaechter(res), waechter]), frames_to_send=frames))
    assert res.offene_freigabe is not None and res.offene_freigabe.antworten == []


def test_ohne_quelle_gibt_es_keine_regel(tmp_path):
    res, context = ressourcen(tmp_path), kontext()
    antwort(res, context, Ereignis("Vorschlag ohne Quelle", klasse=UNGEFRAGT), "Lass das.")
    assert res.offene_freigabe is None and context.messages == [] and res.ereignisse.empty()


def test_ein_anderer_satz_schliesst_das_antwortfenster(tmp_path):
    res, context = ressourcen(tmp_path), kontext()
    waechter = antwort(res, context, ungefragt(), "Wie wird das Wetter morgen?")
    assert waechter.offen is None and res.ereignisse.empty() and context.messages == []
    # Ein kurzer Einwurf lässt das Fenster offen; die Antwort kann noch kommen.
    waechter = antwort(res, context, ungefragt(), "Mhm.")
    assert waechter.offen is not None
    # Ohne gemeldete Ansage passiert nichts, auch bei „nicht jetzt".
    antwort(res, context, None, "Nicht jetzt.")
    assert res.ereignisse.empty() and context.messages == []


def test_ereignis_text_und_klassen():
    assert ereignis_text("Text") == "Text"
    assert ereignis_text(Ansage("Die Zeit ist um.")) == "Die Zeit ist um."
    assert ereignis_text(ungefragt()) == "Hinweis vom System: Routine fällig."
    assert not Ereignis("x").ungefragt and ungefragt().ungefragt
    with pytest.raises(ValueError):
        Ereignis("x", klasse="vielleicht")


def test_fremder_inhalt_setzt_die_marke_beim_ausliefern_nach_dem_aufwecken():
    async def lauf():
        uhr = Uhr()
        zustand = Gespraechszustand(clock=lambda: uhr.now)
        uhr.now = 10.0
        context = SimpleNamespace(messages=[], add_message=lambda m: context.messages.append(m))
        frames, ablauf = [], []

        async def queue_frames(fs):
            frames.extend(fs)

        async def aufwecken(grund):
            ablauf.append("aufgewacht")  # erst wecken (dort kann ein neues Gespräch beginnen), dann Marke

        ereignisse = asyncio.Queue()
        zusteller = Zusteller(
            ereignisse, context, SimpleNamespace(queue_frames=queue_frames), zustand,
            gate=SimpleNamespace(aufwecken=aufwecken), fremder_inhalt=ablauf.append,
        )
        aufgabe = asyncio.create_task(zusteller.laufen())
        await ereignisse.put(Fremd("Hinweis vom System: Der Denker hat ... Ergebnis: 42.", "Ergebnis des Denkers"))
        await ereignisse.put(Fremd(Ansage("Auftrag erledigt: Tech-News. KI überall."), "Kurzfassung des Berichts 'Tech-News'"))
        await asyncio.sleep(0.3)
        aufgabe.cancel()

        assert ablauf == ["aufgewacht", "Ergebnis des Denkers", "aufgewacht", "Kurzfassung des Berichts 'Tech-News'"]
        assert [m["role"] for m in context.messages] == ["developer", "assistant"]
        assert context.messages[0]["content"].startswith("Hinweis vom System")
        assert any(isinstance(f, TTSSpeakFrame) and f.text.startswith("Auftrag erledigt") for f in frames)

    asyncio.run(lauf())
