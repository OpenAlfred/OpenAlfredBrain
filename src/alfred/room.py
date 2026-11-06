"""Alfred im Raum: Mikrofon und Lautsprecher am Rechner, aktiv erst nach dem Wachwort.

Start:    python -m alfred.room buero
Geräte:   python -m alfred.room --geraete
Hörtest:  python -m alfred.room buero --hoertest   (nimmt 3 s auf und spielt sie ab, ohne Mistral)

Jeder Raum ist ein eigener Prozess mit eigenem Mikrofon und Lautsprecher. Die Antwort
kommt damit immer in dem Raum, in dem das Wachwort gefallen ist.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import sys
import time
from array import array
from dataclasses import dataclass

import pyaudio
from dotenv import load_dotenv
from loguru import logger
from pipecat.pipeline.runner import WorkerRunner
from pipecat.transports.local.audio import LocalAudioTransportParams

from alfred import logbuch
from alfred.config import Raum, load_settings
from alfred.jabra_led import JabraLedController
from alfred.local_audio import WatchedLocalAudioTransport
from alfred.persona import load_persona
from alfred.pipeline import build_voice_loop, require_api_key, verbinde_schleuse
from alfred.stimme import aktives_profil, stimmerkenner, wachwort_pruefung
from alfred.wakeword import VoskWakeWordDetector, WakeWordGate

HOERTEST_SECS = 3.0
HOERTEST_RATE = 16000


@dataclass(frozen=True)
class Geraet:
    index: int
    name: str
    eingaenge: int
    ausgaenge: int
    api: str  # Host-API von PortAudio: ALSA, MME, Windows DirectSound, Windows WASAPI …

    def kann(self, output: bool) -> bool:
        return (self.ausgaenge if output else self.eingaenge) > 0


def list_devices() -> list[Geraet]:
    """Alle Audiogeräte, wie PortAudio sie sieht."""
    audio = pyaudio.PyAudio()
    try:
        infos = [audio.get_device_info_by_index(i) for i in range(audio.get_device_count())]
        apis = {i: str(audio.get_host_api_info_by_index(i)["name"]) for i in range(audio.get_host_api_count())}
    finally:
        audio.terminate()
    return [
        Geraet(int(d["index"]), str(d["name"]), int(d["maxInputChannels"]), int(d["maxOutputChannels"]), apis[int(d["hostApi"])])
        for d in infos
    ]


def waehle_geraet(name: str, geraete: list[Geraet], *, output: bool) -> Geraet | None:
    """Das Gerät, dessen Name `name` enthält; unter Windows bevorzugt der MME-Eintrag.

    Windows zeigt jedes Gerät mehrfach, einmal je Host-API. Verlässlich abspielen und aufnehmen
    tut in PortAudios blockierendem Betrieb nur MME: DirectSound nahm beim Jabra Speak 510 das
    Audio entgegen, ohne es auszugeben, WASAPI und WDM-KS öffnen nicht bei 16 oder 24 kHz. MME
    kürzt Namen aber auf 31 Zeichen ("Freisprechtelefon mit Echoaussc"), sodass "Jabra" dort
    nicht mehr vorkommt; deshalb wird der volle Name eines anderen Eintrags zum Abgleich genommen.
    """
    passend = [g for g in geraete if name.casefold() in g.name.casefold() and g.kann(output)]
    if not passend:
        return None
    mme = [
        g
        for g in geraete
        if g.api == "MME" and g.kann(output) and any(p.name.startswith(g.name) for p in passend)
    ]
    return (mme or passend)[0]


class DeviceNotFoundError(Exception):
    """Kein Audiogerät passt zum Namen aus der Konfiguration.

    Ein gewöhnlicher Fehler, kein Programmende: Kurz nach dem Hochfahren ist das Gerät oft noch
    nicht da, bei einem USB-Aussetzer kurz weg. Die Aufsicht (`run_room`) versucht es deshalb
    weiter; nur der Hörtest und `alfred.stimme` enden mit der Meldung.
    """


def find_device(name: str | None, *, output: bool) -> int | None:
    """Index des Geräts zu `name` aus der Konfiguration. None heißt Standardgerät."""
    art = "Lautsprecher" if output else "Mikrofon"
    if not name:
        # Ohne Namen nimmt PortAudio das Standardgerät; auf einem Server ist das oft der
        # verwaiste Klinkenanschluss. Deshalb ins Log, was gewählt wurde.
        audio = pyaudio.PyAudio()
        try:
            info = audio.get_default_output_device_info() if output else audio.get_default_input_device_info()
            logger.info(f"{art} (Standardgerät): {info['name']}")
        except OSError:
            logger.warning(f"{art}: kein Standardgerät gefunden; Namen in config/alfred.yaml eintragen")
        finally:
            audio.terminate()
        return None
    geraet = waehle_geraet(name, list_devices(), output=output)
    if geraet is None:
        raise DeviceNotFoundError(
            f"Kein {art} mit '{name}' im Namen gefunden. "
            "Verfügbare Geräte zeigt: python -m alfred.room --geraete"
        )
    logger.info(f"{art}: [{geraet.index}] {geraet.name} ({geraet.api})")
    return geraet.index


def mme_liste_veraltet(damals: list[str], jetzt: list[str]) -> bool:
    """Passt PortAudios MME-Liste (`damals`) nicht mehr zur Nummerierung, die Windows jetzt hat?

    PortAudio liest die Geräte nur beim Initialisieren und öffnet ein MME-Gerät später über die
    Nummer, die Windows ihm damals gab. Kommt danach ein Gerät hinzu oder fällt eines weg, öffnet
    dieselbe Nummer ein anderes Gerät, ohne Fehlermeldung. Vorn steht bei PortAudio der Soundmapper,
    den Windows nicht mitzählt. Verglichen werden nur Buchstaben und Ziffern und nur bis zur Länge
    des kürzeren Namens: Umlaute in anderer Kodierung oder ungekürzte Namen sind kein Unterschied.
    """

    def gleich(a: str, b: str) -> bool:
        a, b = re.sub(r"[^0-9A-Za-z]", "", a), re.sub(r"[^0-9A-Za-z]", "", b)
        return a.startswith(b) or b.startswith(a)

    vorn = len(damals) - len(jetzt)
    return vorn not in (0, 1) or not all(gleich(a, b) for a, b in zip(damals[vorn:], jetzt))


def _windows_mme_namen(ausgabe: bool) -> list[str]:
    """Die MME-Geräte einer Richtung in der Reihenfolge, in der Windows sie gerade nummeriert."""
    import ctypes
    from ctypes import wintypes

    class Faehigkeiten(ctypes.Structure):  # WAVEOUTCAPSW; WAVEINCAPSW endet vor dwSupport
        _fields_ = [
            ("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
            ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD),
            ("wChannels", wintypes.WORD), ("wReserved1", wintypes.WORD), ("dwSupport", wintypes.DWORD),
        ]

    winmm = ctypes.WinDLL("winmm")
    if ausgabe:
        anzahl, lesen, groesse = winmm.waveOutGetNumDevs, winmm.waveOutGetDevCapsW, ctypes.sizeof(Faehigkeiten)
    else:
        anzahl, lesen, groesse = winmm.waveInGetNumDevs, winmm.waveInGetDevCapsW, ctypes.sizeof(Faehigkeiten) - 4
    lesen.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint]
    namen = []
    for nummer in range(anzahl()):
        f = Faehigkeiten()
        if lesen(nummer, ctypes.byref(f), groesse) == 0:  # wie PortAudio: Geräte mit Fehler auslassen
            namen.append(f.szPname)
    return namen


def geraeteliste_veraltet(audio: pyaudio.PyAudio) -> bool:
    """Unter Windows: Öffnet ein MME-Index von `audio` inzwischen womöglich ein anderes Gerät?"""
    if sys.platform != "win32":
        return False
    try:
        api = audio.get_host_api_info_by_type(pyaudio.paMME)
    except OSError:  # PortAudio ohne MME
        return False
    infos = [
        audio.get_device_info_by_host_api_device_index(api["index"], i) for i in range(int(api["deviceCount"]))
    ]
    return any(
        mme_liste_veraltet([str(d["name"]) for d in infos if int(d[kanaele]) > 0], _windows_mme_namen(ausgabe))
        for kanaele, ausgabe in (("maxInputChannels", False), ("maxOutputChannels", True))
    )


def hoertest(raum: Raum) -> None:
    """Nimmt einige Sekunden vom Mikrofon des Raums auf und spielt sie über den Lautsprecher ab.

    Prüft Geräte, Treiber und Pegel, ohne dass ein Byte zu Mistral geht. Nach dem Einstecken
    eines neuen Lautsprechers oder auf einem frisch eingerichteten Rechner zuerst ausführen.
    """
    try:
        mikrofon = find_device(raum.mikrofon, output=False)
        lautsprecher = find_device(raum.lautsprecher, output=True)
    except DeviceNotFoundError as e:
        raise SystemExit(str(e)) from e
    audio = pyaudio.PyAudio()
    try:
        stueck = int(HOERTEST_RATE * 0.02)
        eingang = audio.open(
            format=pyaudio.paInt16, channels=1, rate=HOERTEST_RATE, input=True,
            input_device_index=mikrofon, frames_per_buffer=stueck,
        )
        print(f"Aufnahme, {HOERTEST_SECS:.0f} Sekunden: Sag etwas.")
        aufnahme = b"".join(
            eingang.read(stueck, exception_on_overflow=False)
            for _ in range(int(HOERTEST_SECS / 0.02))
        )
        eingang.close()

        pegel = max(abs(x) for x in array("h", aufnahme))
        print(f"Pegel: {pegel / 32768:.0%} vom Maximum", end=" ")
        if pegel < 1000:
            print("(sehr leise: falsches Mikrofon, stumm geschaltet oder zu weit weg)")
        elif pegel > 30000:
            print("(übersteuert: Mikrofon leiser stellen)")
        else:
            print("(gut)")

        print("Wiedergabe.")
        ausgang = audio.open(
            format=pyaudio.paInt16, channels=1, rate=HOERTEST_RATE, output=True,
            output_device_index=lautsprecher,
        )
        ausgang.write(aufnahme)
        ausgang.stop_stream()
        ausgang.close()
    finally:
        audio.terminate()


class Abbruchsignal:
    """Strg+C und SIGTERM: einmal gesetzt, startet die Aufsicht nicht mehr neu."""

    def __init__(self) -> None:
        self.gesetzt = asyncio.Event()
        self.runner: WorkerRunner | None = None

    def einhaengen(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._ausloesen)
            except NotImplementedError:  # Windows kennt keine Signal-Handler in der Schleife
                signal.signal(sig, lambda s, f: loop.call_soon_threadsafe(self._ausloesen))

    def _ausloesen(self) -> None:
        if self.gesetzt.is_set():
            return
        logger.info("Beenden angefordert")
        self.gesetzt.set()
        if self.runner is not None:
            asyncio.ensure_future(self.runner.cancel("Beenden angefordert"))


async def run_room(raum_name: str, raum: Raum):
    """Hält den Raum am Laufen: Nach jedem Abbruch wird der Sprach-Loop neu gebaut.

    Alfred soll immer antworten, auch wenn Mistral, das Audiogerät oder ein Baustein einmal
    ausfällt. Der Fehler steht im Log (logs/fehler.log), danach geht es weiter: nach einem
    Einzelfall binnen Sekunden, bei einem Dauerfehler mit wachsender Pause, damit das Log nicht
    überläuft. Nur Strg+C und SIGTERM (systemd stop) beenden den Prozess.
    """
    settings = load_settings()
    persona = load_persona()
    abbruch = Abbruchsignal()
    abbruch.einhaengen()
    # Was Alexander per Stimme umgeschaltet hat, überlebt den Neuaufbau nach einem Fehler. Ebenso die
    # Ereignisse, die noch nicht gesprochen sind: Erinnerungen und Routinen gelten beim Einreihen als
    # erledigt, und ein Anlauf, der danach scheitert, darf sie nicht mitnehmen (16.10.2026).
    stand = {"bereitschaft": settings.wachwort.bereitschaft, "ereignisse": asyncio.Queue()}
    pause = 5.0
    while not abbruch.gesetzt.is_set():
        gestartet = time.monotonic()
        try:
            await _einmal_laufen(raum_name, raum, settings, persona, abbruch, stand)
        except asyncio.CancelledError:
            raise
        except DeviceNotFoundError as e:
            # Kein Programmfehler, sondern ein Gerät, das (noch) fehlt: Die Meldung reicht, ohne Traceback.
            logger.error(f"Raum '{raum_name}': {e}")
        except Exception as e:  # noqa: BLE001 - genau dafür ist die Aufsicht da
            logger.exception(f"Raum '{raum_name}' abgebrochen: {e}")
        if abbruch.gesetzt.is_set():
            break
        # Lief es über eine Minute, war es ein Einzelfall; sonst langsamer, der Fehler bleibt wohl.
        pause = 5.0 if time.monotonic() - gestartet > 60 else min(pause * 2, 120.0)
        logger.warning(f"Raum '{raum_name}' startet in {pause:.0f} s neu")
        try:
            await asyncio.wait_for(abbruch.gesetzt.wait(), timeout=pause)
        except TimeoutError:
            pass
    logger.info(f"Raum '{raum_name}' beendet")


async def _einmal_laufen(
    raum_name: str, raum: Raum, settings, persona, abbruch: Abbruchsignal, stand: dict | None = None
) -> None:
    stand = stand if stand is not None else {"bereitschaft": settings.wachwort.bereitschaft}
    # Stirbt ein Stream im Betrieb, meldet der Transport das; der Worker bricht ab, die Aufsicht
    # startet neu (local_audio.py). Pipecats Original lief am 5.10.2026 stumm weiter.
    transport = WatchedLocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            input_device_index=find_device(raum.mikrofon, output=False),
            output_device_index=find_device(raum.lautsprecher, output=True),
        )
    )
    try:
        await _sprachschleife_laufen(transport, raum_name, settings, persona, abbruch, stand)
    finally:
        # PortAudio liest die Geräte nur ein, solange keine Instanz lebt, und Pipecat gibt seine nie
        # frei. Ohne das sah jeder Neustart die Geräteliste des ersten Anlaufs (16.10.2026).
        transport._pyaudio.terminate()


def _geraete_pruefen(transport: WatchedLocalAudioTransport, stand: dict) -> None:
    """Bricht den Anlauf ab, wenn sich die Audiogeräte seit dem Einlesen geändert haben.

    Kurz nach dem Hochfahren kommen noch Geräte hinzu; die Aufsicht liest sie beim Neustart frisch
    ein. Nach drei Abbrüchen in Folge geht es trotzdem los: Ein Fehler in dieser Prüfung darf
    Alfred nicht dauerhaft stumm halten.
    """
    try:
        veraltet = geraeteliste_veraltet(transport._pyaudio)
    except Exception as e:  # noqa: BLE001 - die Prüfung ist Vorsicht, kein Grund zu schweigen
        logger.warning(f"Audiogeräte nicht geprüft: {e}")
        veraltet = False
    stand["veraltet"] = stand.get("veraltet", 0) + 1 if veraltet else 0
    if not veraltet:
        return
    if stand["veraltet"] > 3:
        logger.warning("Audiogeräte weichen weiter von der Liste in Windows ab; Alfred startet trotzdem")
        return
    raise RuntimeError("Audiogeräte haben sich seit dem Einlesen geändert, etwa kurz nach dem Hochfahren")


async def _sprachschleife_laufen(
    transport: WatchedLocalAudioTransport, raum_name: str, settings, persona, abbruch: Abbruchsignal, stand: dict
) -> None:
    # Mit stimmerkennung.wachwort weckt nur Alexanders Stimme. Standard ist aus: Für das Wort
    # allein ist das Sprechermodell zu unzuverlässig, Alexander musste oft mehrmals rufen. Seit dem
    # 20.10.2026 ist die Stimmerkennung ganz aus (stimmerkennung.aktiv); das Wachwort bleibt.
    stimmprofil = aktives_profil(settings)
    pruefung = None
    if settings.stimmerkennung.wachwort and stimmprofil is not None:
        pruefung = wachwort_pruefung(
            stimmerkenner(settings.stimmerkennung.modell, settings.wachwort.modell),
            stimmprofil,
            settings.stimmerkennung.schwelle_wachwort,
        )
        logger.info("Wachwort nur in Alexanders Stimme")
    # Längere Gesprächspausen nur mit Stimmerkennung: Ohne könnte der Stimmfilter fremde Stimmen
    # nicht aussortieren, und Alfred antwortete minutenlang dem Fernseher.
    gespraech = settings.wachwort.gespraech_sekunden if stimmprofil is not None else 0.0
    if settings.wachwort.gespraech_sekunden > settings.wachwort.offen_sekunden and stimmprofil is None:
        logger.info(
            f"Ohne Stimmerkennung schließt die Schleuse nach {settings.wachwort.offen_sekunden:g} s Stille; "
            "danach weckt das Wachwort"
        )
    gate = WakeWordGate(
        VoskWakeWordDetector(settings.wachwort.phrasen, settings.wachwort.modell),
        open_secs=settings.wachwort.offen_sekunden,
        gespraech_secs=gespraech,
        stimmpruefung=pruefung,
        bereitschaft=stand["bereitschaft"],
        bereitschaft_secs=settings.wachwort.bereitschaft_nachlauf_sekunden,
        rueckfrage_secs=settings.wachwort.rueckfrage_sekunden,
    )
    if gate.bereitschaft:
        logger.info("Bereitschaft: Alfred reagiert nur auf sein Wachwort und hört nach jeder Antwort weg")
    sitzung = await build_voice_loop(
        transport, settings, persona, gate=gate, always_on=True, quelle=f"Raum {raum_name}",
        ereignisse=stand.get("ereignisse"),
    )
    # Ab hier laufen die Hintergrundaufgaben. abschliessen beendet sie, auch wenn der Aufbau noch
    # scheitert; sonst liefen sie neben denen des nächsten Anlaufs weiter, in dieselbe Warteschlange.
    try:
        if settings.jabra.led_aktiv:
            # Die LED folgt dem Zustand der Mitschrift: aus im Schlaf, blinkend beim Nachdenken, sonst
            # grün. Sie startet aus; verbinde_schleuse legt die Mitschrift gleich schlafen.
            sitzung.jabra = JabraLedController(
                vendor_id=settings.jabra.vendor_id,
                product_id=settings.jabra.product_id,
                blinken_s=settings.jabra.blinken_sekunden,
            )
            sitzung.ressourcen.mitschrift.bei_zustand.append(sitzung.jabra.zeigen)
        verbinde_schleuse(sitzung, gate)

        logger.info(
            f"Raum '{raum_name}' bereit. Wachwort: {', '.join(settings.wachwort.phrasen)}"
        )
        # Signale fängt die Aufsicht selbst ab; der Runner soll beim Beenden nicht neu gestartet werden.
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        abbruch.runner = runner
        await runner.add_workers(sitzung.worker)
        if not abbruch.gesetzt.is_set():  # kam das Signal während des Aufbaus, gar nicht erst starten
            # Direkt vor dem Öffnen: Der Aufbau dauert Sekunden, in denen sich die Liste ändern kann.
            _geraete_pruefen(transport, stand)
            await runner.run()
    finally:
        abbruch.runner = None
        stand["bereitschaft"] = gate.bereitschaft
        await sitzung.abschliessen()


def main() -> None:
    parser = argparse.ArgumentParser(description="Alfred im Raum mit Wachwort")
    parser.add_argument("raum", nargs="?", help="Name eines Raums aus config/alfred.yaml")
    parser.add_argument("--geraete", action="store_true", help="Audiogeräte auflisten")
    parser.add_argument(
        "--hoertest", action="store_true",
        help="Aufnahme und Wiedergabe mit den Geräten des Raums prüfen, ohne Mistral",
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # Linux: PortAudio öffnet Hardware-Geräte sonst ohne Umrechnung der Abtastrate. Ein
    # USB-Lautsprecher, der 16 oder 24 kHz nicht direkt kann, ließe sich dann nicht öffnen.
    os.environ.setdefault("PA_ALSA_PLUGHW", "1")

    if args.geraete:
        for g in list_devices():
            print(f"{g.index:3}  {g.name:50}  Eingänge: {g.eingaenge}  Ausgänge: {g.ausgaenge}  {g.api}")
        return
    if not args.raum:
        parser.error("Raum angeben oder --geraete")

    settings = load_settings()
    if args.raum not in settings.raeume:
        raise SystemExit(
            f"Raum '{args.raum}' steht nicht unter raeume in config/alfred.yaml. "
            f"Bekannt: {', '.join(settings.raeume) or 'keiner'}"
        )
    raum = settings.raeume[args.raum]

    if args.hoertest:
        hoertest(raum)
        return

    load_dotenv(override=True)
    require_api_key()
    logbuch.einrichten(f"raum-{args.raum}")
    asyncio.run(run_room(args.raum, raum))


if __name__ == "__main__":
    main()
