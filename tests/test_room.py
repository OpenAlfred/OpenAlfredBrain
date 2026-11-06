"""Geräteauswahl und Aufsicht im Raum-Modus."""

import asyncio
import sys
from types import SimpleNamespace

import pytest
from loguru import logger

pyaudio = pytest.importorskip("pyaudio", reason="Raum-Modus braucht das Extra raum (PyAudio)")
from alfred import room  # noqa: E402
from alfred.room import Abbruchsignal, Geraet, mme_liste_veraltet, waehle_geraet

JABRA = "Freisprechtelefon mit Echoausschaltung (Jabra SPEAK 510 USB)"

# So zeigt PortAudio das Jabra unter Windows: viermal, MME mit gekürztem Namen.
WINDOWS = [
    Geraet(1, "Mikrofon (Razer BlackShark V2 H", 1, 0, "MME"),
    Geraet(2, "Freisprechtelefon mit Echoaussc", 1, 0, "MME"),
    Geraet(3, "Lautsprecher (Razer BlackShark", 0, 2, "MME"),
    Geraet(5, "Freisprechtelefon mit Echoaussc", 0, 2, "MME"),
    Geraet(10, "Mikrofon (Razer BlackShark V2 HS 2.4)", 1, 0, "Windows DirectSound"),
    Geraet(11, JABRA, 1, 0, "Windows DirectSound"),
    Geraet(14, JABRA, 0, 2, "Windows DirectSound"),
    Geraet(18, JABRA, 0, 2, "Windows WASAPI"),
    Geraet(24, JABRA, 2, 0, "Windows WASAPI"),
]

LINUX = [
    Geraet(0, "HDA Intel PCH: ALC892 Analog (hw:0,0)", 2, 2, "ALSA"),
    Geraet(1, "Jabra SPEAK 510 USB: Audio (hw:1,0)", 1, 2, "ALSA"),
    Geraet(2, "default", 32, 32, "ALSA"),
]


def test_windows_nimmt_den_mme_eintrag_trotz_gekuerztem_namen():
    assert waehle_geraet("Jabra", WINDOWS, output=True).index == 5
    assert waehle_geraet("Jabra", WINDOWS, output=False).index == 2


def test_windows_ohne_mme_eintrag_faellt_auf_den_vollen_namen_zurueck():
    ohne_mme = [g for g in WINDOWS if g.api != "MME"]
    assert waehle_geraet("Jabra", ohne_mme, output=True).index == 14


def test_richtung_zaehlt():
    assert waehle_geraet("Razer", WINDOWS, output=True).index == 3
    assert waehle_geraet("Razer", WINDOWS, output=False).index == 1


def test_linux_nimmt_das_erste_passende_geraet():
    assert waehle_geraet("Jabra", LINUX, output=True).index == 1
    assert waehle_geraet("jabra", LINUX, output=False).index == 1


def test_unbekannter_name():
    assert waehle_geraet("Sonos", WINDOWS, output=True) is None
    assert waehle_geraet("Jabra", [], output=True) is None


def test_aufsicht_startet_nach_abbruch_neu_und_hoert_auf_das_signal(monkeypatch):
    laeufe = []

    bereitschaft = []
    wartend = []

    async def einmal_laufen(raum_name, raum, settings, persona, abbruch, stand):
        laeufe.append(len(laeufe))
        bereitschaft.append(stand["bereitschaft"])
        wartend.append(stand["ereignisse"].qsize())
        if len(laeufe) == 1:
            stand["bereitschaft"] = True  # per Stimme umgeschaltet, kurz vor dem Absturz
            # Eingereiht, aber nicht gesprochen: Die Erinnerung gilt in ihrer Datei schon als ausgeliefert.
            stand["ereignisse"].put_nowait("Hinweis vom System: Erinnerung fällig.")
            raise RuntimeError("Audiogerät weg")
        if len(laeufe) == 2:
            return  # Worker hat sich beendet, etwa weil ein Baustein unbrauchbar wurde
        abbruch._ausloesen()  # dritter Lauf: Strg+C
        await asyncio.sleep(0.05)

    pausen = []

    async def ohne_zu_warten(warten, timeout):
        pausen.append(timeout)
        warten.close()  # die Coroutine abbruch.gesetzt.wait() nie gestartet
        raise TimeoutError

    monkeypatch.setattr(room, "_einmal_laufen", einmal_laufen)
    monkeypatch.setattr(room, "load_settings", lambda: SimpleNamespace(wachwort=SimpleNamespace(bereitschaft=False)))
    monkeypatch.setattr(room, "load_persona", lambda: None)
    monkeypatch.setattr(Abbruchsignal, "einhaengen", lambda self: None)
    monkeypatch.setattr(room.asyncio, "wait_for", ohne_zu_warten)

    asyncio.run(room.run_room("buero", None))

    assert laeufe == [0, 1, 2]
    assert bereitschaft == [False, True, True], "die Bereitschaft überlebt den Neuaufbau"
    assert wartend == [0, 1, 1], "die Warteschlange auch, samt dem Ungesprochenen"
    # Beide Abbrüche kamen sofort nach dem Start: die Pause wächst.
    assert pausen == [10.0, 20.0]


def test_abgebrochener_aufbau_beendet_die_hintergrundaufgaben(monkeypatch):
    # Ab build_voice_loop laufen Erinnerungen, Routinen und Zusteller. Scheitert danach noch etwas
    # (hier die Schleuse, am 16.10.2026 die Geräteprüfung), müssen sie enden: Sonst liefen sie neben
    # denen des nächsten Anlaufs weiter, in dieselbe Warteschlange.
    from alfred.config import load_settings

    uebergeben, abgeschlossen = [], []

    async def build_voice_loop(transport, settings, persona, **optionen):
        uebergeben.append(optionen["ereignisse"])

        async def abschliessen():
            abgeschlossen.append(True)

        return SimpleNamespace(abschliessen=abschliessen)

    def verbinde_schleuse(sitzung, gate):
        raise OSError("mitschrift.json nicht schreibbar")

    monkeypatch.setattr(room, "build_voice_loop", build_voice_loop)
    monkeypatch.setattr(room, "verbinde_schleuse", verbinde_schleuse)
    monkeypatch.setattr(room, "aktives_profil", lambda settings: None)
    monkeypatch.setattr(room, "VoskWakeWordDetector", lambda phrasen, modell: None)
    monkeypatch.setattr(room, "WakeWordGate", lambda detector, **optionen: SimpleNamespace(bereitschaft=False))
    settings = load_settings()
    settings.jabra.led_aktiv = False
    warteschlange = asyncio.Queue()
    stand = {"bereitschaft": False, "ereignisse": warteschlange}

    with pytest.raises(OSError):
        asyncio.run(room._sprachschleife_laufen(None, "buero", settings, None, Abbruchsignal(), stand))
    assert uebergeben == [warteschlange], "der Sprach-Loop bekommt die Warteschlange der Aufsicht"
    assert abgeschlossen == [True]


# MME-Ausgänge am 16.10.2026: PortAudio mit dem Soundmapper vorn, Windows ohne.
PORTAUDIO_AUS = [
    "Microsoft Soundmapper - Output",
    "Lautsprecher (High Definition A",
    "Freisprechtelefon mit Echoaussc",
    "DELL S2721HGF (NVIDIA High Defi",
    "Lautsprecher (Razer BlackShark ",
]
WINDOWS_AUS = PORTAUDIO_AUS[1:]


def test_mme_liste_passt_mit_soundmapper_vorn():
    assert not mme_liste_veraltet(PORTAUDIO_AUS, WINDOWS_AUS)
    assert not mme_liste_veraltet(WINDOWS_AUS, WINDOWS_AUS), "ohne Soundmapper ebenso"
    assert not mme_liste_veraltet(["Microsoft Soundmapper - Input"], [])


def test_mme_liste_nach_dem_hochfahren_veraltet():
    # Eingelesen, bevor der Monitor da war: Dieselbe Nummer öffnete danach den Monitor statt das Jabra.
    ohne_monitor = [n for n in PORTAUDIO_AUS if not n.startswith("DELL")]
    assert mme_liste_veraltet(ohne_monitor, WINDOWS_AUS)
    assert mme_liste_veraltet(PORTAUDIO_AUS, [n for n in WINDOWS_AUS if not n.startswith("DELL")])
    vertauscht = [WINDOWS_AUS[0], WINDOWS_AUS[2], WINDOWS_AUS[1], WINDOWS_AUS[3]]
    assert mme_liste_veraltet(PORTAUDIO_AUS, vertauscht)


def test_mme_namen_vertragen_kodierung_und_volle_namen():
    portaudio = ["Microsoft Soundmapper - Output", "PrimÃ¤rer Soundtreiber", "Freisprechtelefon mit Echoausschaltung (Jabra)"]
    assert not mme_liste_veraltet(portaudio, ["Primärer Soundtreiber", "Freisprechtelefon mit Echoaussc"])


@pytest.mark.skipif(sys.platform != "win32", reason="MME gibt es nur unter Windows")
def test_frisch_eingelesene_geraeteliste_passt_zu_windows():
    # Schützt vor Fehlalarmen: Sonst begänne jeder Start mit drei Abbrüchen.
    audio = pyaudio.PyAudio()
    try:
        assert not room.geraeteliste_veraltet(audio)
    finally:
        audio.terminate()


def test_veraltete_geraeteliste_bricht_ab_aber_nicht_endlos(monkeypatch):
    transport = SimpleNamespace(_pyaudio=None)
    stand = {}
    monkeypatch.setattr(room, "geraeteliste_veraltet", lambda audio: True)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            room._geraete_pruefen(transport, stand)
    room._geraete_pruefen(transport, stand)  # vierter Anlauf startet trotzdem

    monkeypatch.setattr(room, "geraeteliste_veraltet", lambda audio: False)
    room._geraete_pruefen(transport, stand)
    assert stand["veraltet"] == 0

    def kaputt(audio):
        raise OSError("winmm fehlt")

    monkeypatch.setattr(room, "geraeteliste_veraltet", kaputt)
    room._geraete_pruefen(transport, stand)  # eine scheiternde Prüfung hält Alfred nicht auf


@pytest.mark.parametrize("scheitert", [False, True])
def test_portaudio_wird_nach_jedem_anlauf_freigegeben(monkeypatch, scheitert):
    freigegeben = []

    class Transport:
        def __init__(self, params):
            self._pyaudio = SimpleNamespace(terminate=lambda: freigegeben.append(True))

    async def sprachschleife(transport, *args):
        if scheitert:
            raise RuntimeError("Aufbau gescheitert")

    monkeypatch.setattr(room, "WatchedLocalAudioTransport", Transport)
    monkeypatch.setattr(room, "find_device", lambda name, output: None)
    monkeypatch.setattr(room, "_sprachschleife_laufen", sprachschleife)
    raum = SimpleNamespace(mikrofon=None, lautsprecher=None)

    lauf = room._einmal_laufen("buero", raum, None, None, Abbruchsignal(), {"bereitschaft": False})
    if scheitert:
        with pytest.raises(RuntimeError):
            asyncio.run(lauf)
    else:
        asyncio.run(lauf)
    # Sonst läse PortAudio die Geräte beim Neustart nicht neu ein.
    assert freigegeben == [True]


def test_aufsicht_versucht_es_weiter_wenn_das_geraet_fehlt(monkeypatch):
    # Kurz nach dem Hochfahren oder bei einem USB-Aussetzer fehlt das Jabra; nach der zweiten
    # Pause ist es wieder da.
    ohne_jabra = [g for g in WINDOWS if "Razer" in g.name]
    pausen, geoeffnet, signale, meldungen = [], [], [], []

    class Transport:
        def __init__(self, params):
            geoeffnet.append((params.input_device_index, params.output_device_index))
            signale[0]._ausloesen()  # Strg+C: Den Sprach-Loop braucht der Test nicht mehr
            raise RuntimeError("Sprach-Loop im Test nicht aufgebaut")

    async def ohne_zu_warten(warten, timeout):
        pausen.append(timeout)
        warten.close()
        raise TimeoutError

    monkeypatch.setattr(room, "list_devices", lambda: WINDOWS if len(pausen) >= 2 else ohne_jabra)
    monkeypatch.setattr(room, "WatchedLocalAudioTransport", Transport)
    monkeypatch.setattr(room, "load_settings", lambda: SimpleNamespace(wachwort=SimpleNamespace(bereitschaft=False)))
    monkeypatch.setattr(room, "load_persona", lambda: None)
    monkeypatch.setattr(Abbruchsignal, "einhaengen", lambda self: signale.append(self))
    monkeypatch.setattr(room.asyncio, "wait_for", ohne_zu_warten)
    senke = logger.add(lambda m: meldungen.append(m.record), level="ERROR")
    try:
        asyncio.run(room.run_room("buero", SimpleNamespace(mikrofon="Jabra", lautsprecher="Jabra")))
    finally:
        logger.remove(senke)

    assert pausen == [10.0, 20.0], "der Raum lief weiter, mit wachsender Pause"
    assert geoeffnet == [(2, 5)], "danach das Jabra über MME"
    fehlt = [r for r in meldungen if "Kein Mikrofon mit 'Jabra' im Namen gefunden" in r["message"]]
    assert len(fehlt) == 2, "ein falsch geschriebener Name steht im Log und auf der Konsole"
    assert all(r["exception"] is None for r in fehlt), "die Meldung, ohne Traceback"


def test_hoertest_und_stimme_enden_mit_meldung_wenn_das_geraet_fehlt(monkeypatch):
    import alfred.stimme as stimme

    monkeypatch.setattr(room, "list_devices", lambda: [])
    raum = SimpleNamespace(mikrofon="Jabra", lautsprecher="Jabra")
    with pytest.raises(SystemExit, match="Kein Mikrofon mit 'Jabra' im Namen gefunden"):
        room.hoertest(raum)
    with pytest.raises(SystemExit, match="Kein Mikrofon mit 'Jabra' im Namen gefunden"):
        stimme._mikrofon(SimpleNamespace(raeume={"buero": raum}), "buero")
