"""Nur Alexanders Stimme: ein Filter gegen Fernseher, Besuch und Zufall; seit dem 20.10.2026 aus.

Ein Sprechermodell rechnet aus einigen Sekunden Sprache einen Stimmvektor: WeSpeaker über ONNX
(sprechermodell.py, empfohlen) oder Vosks Sprechermodell. Beim Anlernen wird Alexanders Vektor
gespeichert; mit `stimmerkennung.aktiv` bekommt danach jeder Gesprächszug einen Ähnlichkeitswert.
Ein Ja zu einer Aktion mit Freigabe zählt dann nur, wenn die Stimme passt. Das ist ein Filter,
kein Schutz gegen gezielte Täuschung mit einer Aufnahme. Ein Profil gilt nur für das Modell, mit
dem es angelernt wurde. Ausgeschaltet, weil Alexanders eigene Sätze bis auf 0,23 fielen und er
alles mehrmals sagen musste (docs/erkenntnisse.md, 20.10.2026); heikle Aktionen sichert seitdem
das Codewort (codewort.py).

Der Vektor hängt am Mikrofon und am Abstand: Mit dem Windows-Standardmikrofon angelernt und am
Jabra quer durch den Raum geprüft, lag Alexanders eigene Stimme bei 0,27 (4.10.2026), so tief
wie eine fremde. Deshalb nimmt das Anlernen das Mikrofon des Raums aus config/alfred.yaml, und
zu zwanzig Sekunden freier Rede kommen kurze Wachwort-Sätze aus der üblichen Entfernung, normal,
leise, laut und beiläufig gesprochen; das Profil ist der Mittelwert aller Proben. Wie ähnlich
sich die Proben untereinander sind, ergibt die Empfehlung für die Schwellen.

Anlernen (Mikrofon des ersten Raums; --raum kueche für einen anderen):
    python -m alfred.stimme --anlernen
    python -m alfred.stimme --anlernen rede.wav probe1.wav probe2.wav   (16 kHz mono)
Prüfen, live mit drei Wachwort-Sätzen oder aus einer Datei:
    python -m alfred.stimme --pruefen
    python -m alfred.stimme --pruefen aufnahme.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
import wave
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from alfred.codewort import MARKE
from alfred.config import konfig_anlegen, werte_setzen

SAMPLE_RATE = 16000
MIN_SPRACHE_SECS = 1.0  # kürzere Züge liefern keinen brauchbaren Vektor
KURZ_SECS = 2.5  # etwa so viel prüft die Wachwort-Schleuse: das Wachwort und den Anfang danach
VOSK_SPRECHERMODELL = "vosk-model-spk-0.4"  # alle Profile von vor dem 9.10.2026
EMPFOHLENES_MODELL = "wespeaker-resnet34"  # trennt Stimmen besser, rund 20-mal schneller
# Kurze Sätze zum Anlernen, je mit Sprechweise: Mit Sätzen in nur einer Tonlage passte die Stimme
# nur in dieser Tonlage (Alexander, 9.10.2026: „nicht lauter, nicht leiser, gar nix").
PROBE_SAETZE = (
    ("Alfred, wie spät ist es?", "normal"),
    ("Alfred, wie wird das Wetter morgen?", "leise, wie spätabends"),
    ("Alfred, setz Milch auf die Einkaufsliste.", "laut, als wärst du weiter weg"),
    ("Alfred, was steht heute an?", "beiläufig, in anderer Tonlage"),
    ("Alfred, stell einen Timer auf zehn Minuten.", "normal, von einem anderen Platz im Raum"),
    ("Alfred, erinner mich nachher an die Wäsche.", "müde oder gelangweilt"),
)


def aehnlichkeit(a: Sequence[float], b: Sequence[float]) -> float:
    """Kosinus-Ähnlichkeit zweier Stimmvektoren, 1 = gleich."""
    skalar = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return skalar / norm if norm else 0.0


def mittelvektor(vektoren: Sequence[Sequence[float]]) -> list[float]:
    """Der Mittelwert mehrerer Proben, jede auf Länge eins gebracht, damit keine dominiert."""
    normiert = []
    for v in vektoren:
        norm = math.sqrt(sum(x * x for x in v))
        if norm:
            normiert.append([x / norm for x in v])
    if not normiert:
        raise ValueError("keine brauchbare Probe")
    return [sum(spalte) / len(normiert) for spalte in zip(*normiert)]


def selbstaehnlichkeit(kurze: Sequence[Sequence[float]], weitere: Sequence[Sequence[float]] = ()) -> list[float]:
    """Wie ähnlich jede kurze Probe dem Profil aus allen übrigen Proben ist.

    Die Probe selbst bleibt außen vor; so ist der Wert eine ehrliche Schätzung dafür, wie ein
    neuer Satz in Alexanders Stimme abschneidet.
    """
    werte = []
    for i, probe in enumerate(kurze):
        uebrige = [*weitere, *kurze[:i], *kurze[i + 1 :]]
        werte.append(aehnlichkeit(probe, mittelvektor(uebrige)))
    return werte


def schwellen_empfehlen(werte: Sequence[float]) -> dict[str, float]:
    """Schwellen knapp unter der schwächsten eigenen Probe.

    Beim Wachwort großzügiger, weil ein fälschlich geöffnetes Ohr nur zuhört; bei Freigaben
    strenger, weil dort etwas passiert.
    """
    untere = min(werte)
    return {
        "schwelle_wachwort": round(min(0.6, max(0.25, untere - 0.10)), 2),
        "schwelle": round(min(0.7, max(0.30, untere - 0.05)), 2),
    }


def sprache_zuschneiden(audio: bytes, max_secs: float | None = None) -> bytes:
    """Schneidet Stille vor und nach der Sprache ab (Lautstärke je 20 ms), auf Wunsch auch die Länge."""
    werte = np.frombuffer(audio, dtype=np.int16)
    stueck = SAMPLE_RATE // 50
    anzahl = len(werte) // stueck
    if anzahl == 0:
        return b""
    pegel = np.sqrt(np.mean(werte[: anzahl * stueck].astype(np.float64).reshape(anzahl, stueck) ** 2, axis=1))
    # Die Untergrenze liegt weit über einem ruhigen Raum (Effektivwert um 10), aber unter leiser
    # Sprache: Mit 300 galt ein leise gesprochener Anlern-Satz als „kaum Sprache".
    laut = np.nonzero(pegel >= max(100.0, 0.1 * pegel.max()))[0]
    if len(laut) == 0:
        return b""
    start = max(0, int(laut[0]) - 5)  # 100 ms Rand davor
    ende = min(anzahl, int(laut[-1]) + 6)
    if max_secs:
        ende = min(ende, start + int(max_secs * 50))
    return werte[start * stueck : ende * stueck].tobytes()


def schwellen_setzen(text: str, werte: dict[str, float], abschnitt: str = "stimmerkennung") -> str:
    """Trägt Werte im Abschnitt einer YAML-Datei ein, ohne Kommentare und Rest anzufassen."""
    return werte_setzen(text, abschnitt, werte)


class Stimmerkenner(Protocol):
    """Schnittstelle der Sprechermodelle: 16-kHz-Mono-PCM rein, Stimmvektor raus."""

    def vektor(self, audio: bytes) -> list[float] | None:
        """Der Stimmvektor, oder None, wenn das Audio zu wenig Sprache enthält."""
        ...


def stimmerkenner(modell: str, sprachmodell: str = "vosk-model-small-de-0.15") -> Stimmerkenner:
    """Der Erkenner zu `stimmerkennung.modell`: vosk-model-spk-… oder ein Name aus sprechermodell.MODELLE."""
    if modell.startswith("vosk-"):
        return VoskStimmerkenner(modell, sprachmodell)
    from alfred.sprechermodell import MODELLE, OnnxStimmerkenner

    if modell not in MODELLE:
        raise ValueError(
            f"Unbekanntes Sprechermodell '{modell}' unter stimmerkennung.modell; "
            f"möglich: {', '.join([*MODELLE, VOSK_SPRECHERMODELL])}"
        )
    return OnnxStimmerkenner(modell)


class VoskStimmerkenner:
    """Vosks Sprechermodell; das Modell lädt beim ersten Mal (rund 13 MB).

    Für einen Vektor läuft eine ganze Spracherkennung über das Audio, rund 0,4 s je Satz.
    """

    def __init__(self, modell_name: str = VOSK_SPRECHERMODELL, sprachmodell: str = "vosk-model-small-de-0.15"):
        from vosk import KaldiRecognizer, Model, SetLogLevel, SpkModel

        SetLogLevel(-2)  # gilt für den ganzen Prozess; -1 ließe Kaldis Warnungen durch (wakeword.py)
        self._modell = Model(model_name=sprachmodell)
        # SpkModel lädt nicht selbst herunter; Model kennt den Weg in den Cache und holt es bei Bedarf.
        self._spk = SpkModel(str(self._modell.get_model_by_name(modell_name)))
        self._recognizer_cls = KaldiRecognizer

    def vektor(self, audio: bytes) -> list[float] | None:
        rec = self._recognizer_cls(self._modell, SAMPLE_RATE, self._spk)
        rec.AcceptWaveform(audio)
        ergebnis = json.loads(rec.FinalResult())
        return ergebnis.get("spk")


class Stimmprofil:
    """Alexanders Stimmvektor mit den Angaben zum Anlernen (Mikrofon, Datum, Modell, Proben).

    Mit `modell` gilt das Profil nur, wenn es mit diesem Modell angelernt wurde: Vektoren
    verschiedener Modelle lassen sich nicht vergleichen. Sonst steht der Name in `anderes_modell`,
    und es wird geprüft wie ohne Profil, bis neu angelernt ist.
    """

    def __init__(self, pfad: Path, modell: str | None = None):
        self._pfad = pfad
        self.vektor: list[float] | None = None
        self.info: dict = {}
        self.anderes_modell: str | None = None
        if pfad.exists():
            daten = json.loads(pfad.read_text(encoding="utf-8"))
            self.info = {k: v for k, v in daten.items() if k != "vektor"}
            angelernt_mit = daten.get("modell", VOSK_SPRECHERMODELL)
            if modell is not None and angelernt_mit != modell:
                self.anderes_modell = angelernt_mit
                logger.warning(f"Stimmprofil mit {angelernt_mit} angelernt, eingestellt ist {modell}: neu anlernen")
            else:
                self.vektor = daten.get("vektor")

    @property
    def vorhanden(self) -> bool:
        return bool(self.vektor)

    def speichern(self, vektor: list[float], **info) -> None:
        """Speichert den Vektor; `info` hält fest, womit angelernt wurde (Mikrofon, Modell, Proben, Werte)."""
        self.vektor, self.info, self.anderes_modell = vektor, info, None
        self._pfad.parent.mkdir(parents=True, exist_ok=True)
        self._pfad.write_text(json.dumps({"vektor": vektor, **info}, ensure_ascii=False), encoding="utf-8")


def aktives_profil(settings) -> Stimmprofil | None:
    """Alexanders Stimmprofil, wenn die Stimmerkennung eingeschaltet und für das Modell angelernt ist."""
    if not settings.stimmerkennung.aktiv:
        return None
    profil = Stimmprofil(settings.pfad(settings.stimmerkennung.profil), settings.stimmerkennung.modell)
    return profil if profil.vorhanden else None


def wachwort_pruefung(erkenner: Stimmerkenner, profil: Stimmprofil, schwelle: float):
    """Prüffunktion für die Wachwort-Schleuse: True, wenn das Audio nach Alexander klingt.

    Das Audio ab dem Wachwort ist kurz, meist ein bis zwei Sekunden; reicht es nicht für einen
    Vektor, gilt die Stimme als passend, damit Alfred lieber einmal zu viel aufwacht.
    """

    def pruefen(audio: bytes) -> bool:
        if len(audio) < SAMPLE_RATE * 2 * MIN_SPRACHE_SECS or not profil.vektor:
            return True
        vektor = erkenner.vektor(audio)
        if not vektor:
            return True
        wert = aehnlichkeit(vektor, profil.vektor)
        logger.info(f"Wachwort-Stimme: Ähnlichkeit {wert:.2f} (Schwelle {schwelle:.2f})")
        return wert >= schwelle

    return pruefen


class Stimmpruefer(FrameProcessor):
    """Sammelt das Audio eines Gesprächszugs und vergleicht es nach dem Zug mit dem Profil.

    Ergebnis in `passt` (None, solange nichts geprüft ist) und `wert`. Die Prüfung läuft im
    Thread, das Audio geht ungebremst weiter.
    """

    def __init__(self, erkenner: Stimmerkenner, profil: Stimmprofil, schwelle: float, **kwargs):
        super().__init__(**kwargs)
        self._erkenner = erkenner
        self._profil = profil
        self._schwelle = schwelle
        self._puffer: list[bytes] = []
        self._sammeln = False
        self._urteil: asyncio.Future | None = None  # Ähnlichkeit des letzten Sprechabschnitts
        self.passt: bool | None = None
        self.wert: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._puffer, self._sammeln = [], True
            self._urteil = asyncio.get_running_loop().create_future()
        elif isinstance(frame, InputAudioRawFrame) and self._sammeln:
            self._puffer.append(frame.audio)
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self._sammeln:
            self._sammeln = False
            audio = b"".join(self._puffer)
            if len(audio) >= SAMPLE_RATE * 2 * MIN_SPRACHE_SECS:
                self.create_task(self._pruefen(audio, self._urteil))
            else:
                self._entscheiden(self._urteil, None)
        await self.push_frame(frame, direction)

    @staticmethod
    def _entscheiden(urteil: asyncio.Future | None, wert: float | None) -> None:
        if urteil is not None and not urteil.done():
            urteil.set_result(wert)

    async def _pruefen(self, audio: bytes, urteil: asyncio.Future | None = None) -> None:
        wert = None
        try:
            vektor = await asyncio.to_thread(self._erkenner.vektor, audio)
            if vektor and self._profil.vektor:
                wert = aehnlichkeit(vektor, self._profil.vektor)
                self.wert, self.passt = wert, wert >= self._schwelle
                logger.info(
                    f"Stimme: Ähnlichkeit {wert:.2f} (Schwelle {self._schwelle:.2f}), {'passt' if self.passt else 'passt nicht'}"
                )
        finally:
            # Auch bei einem Fehler entscheiden, sonst wartet der Stimmfilter bis zu seiner Frist.
            self._entscheiden(urteil, wert)

    async def urteil(self, frist: float = 1.0) -> float | None:
        """Die Ähnlichkeit des letzten Sprechabschnitts; None, wenn er zu kurz war oder das Ergebnis zu spät kommt."""
        if self._urteil is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(self._urteil), frist)
        except TimeoutError:
            return None


MAX_UNGEPRUEFT_WOERTER = 3  # so kurze Sätze ("Ja", "Mach das") gehen auch ohne Stimmprüfung durch


class Stimmfilter(FrameProcessor):
    """Lässt nach einer Gesprächspause nur noch Alexanders Stimme ins Gespräch.

    Sitzt hinter Transkription und Sprachfilter. Solange die Wachwort-Schleuse in ihrer freien
    Zeit ist, geht alles durch. Danach (`schleuse.nur_eigene_stimme`) kommt ein Satz nur noch an,
    wenn der Stimmprüfer Alexanders Stimme erkennt oder der Satz das Wachwort enthält; andere
    verwirft der Filter, wie der Sprachfilter fremde Sprachen. So kann Alexander nach einer
    längeren Pause einfach weiterreden, ohne dass Fernseher oder Besuch Antworten auslösen.
    Ein Satz, der sich nicht prüfen lässt (zu kurz, kein Stimmvektor), geht nur durch, wenn er
    höchstens MAX_UNGEPRUEFT_WOERTER Wörter hat ("Ja", "Danke"): Am 6.10.2026 kam so
    "Yesterday that was planned." aus einem Video durch. Wartet Alfred auf eine Antwort
    (`erwartet_antwort`: Rückfrage, Zuhören, eine Frage), gilt die Länge nicht.

    Ohne Stimmerkennung (Vorgabe seit dem 20.10.2026) ist kein Satz prüfbar. Dann filtert er nur
    in Bereitschaft, nach Alfreds Antwort: Durch kommt, was das Wachwort enthält, kurz ist oder
    auf Alfreds Frage antwortet. Sonst schließt die Schleuse ohnehin nach `offen_sekunden`.

    Gleich nach dem Wachwort ist das erste Transkript meist das Wachwort selbst; verhört die
    Erkennung es zu einem einzelnen fremden Wort ("Eidrith."), ersetzt der Filter es durch das
    Wachwort, statt Alfred rätseln zu lassen. Das Codewort (codewort.MARKE) bleibt, was es ist.
    """

    def __init__(
        self,
        schleuse=None,
        pruefer: Stimmpruefer | None = None,
        schwelle: float = 0.5,
        wachwoerter: Sequence[str] = ("alfred",),
        bei_verworfen=None,
        erwartet_antwort: Callable[[], bool] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._schleuse = schleuse
        self._pruefer = pruefer
        self._schwelle = schwelle
        self._wachwoerter = {w.casefold() for phrase in wachwoerter for w in re.findall(r"\w+", phrase)}
        self._wachwort_text = f"{next(iter(wachwoerter), 'alfred').strip().capitalize()}."
        self._bei_verworfen = bei_verworfen
        self._erwartet_antwort = erwartet_antwort or (lambda: False)
        self.verworfen = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and self._schleuse is not None:
            woerter = re.findall(r"\w+", frame.text.casefold())
            if getattr(self._schleuse, "erstes_transkript_offen", False):
                self._schleuse.erstes_transkript_offen = False
                if len(woerter) == 1 and not self._wachwoerter.intersection(woerter) and MARKE not in frame.text:
                    logger.info(f"Erstes Wort nach dem Wachwort {frame.text!r} gilt als Wachwort")
                    frame.text = self._wachwort_text
                    woerter = re.findall(r"\w+", frame.text.casefold())
            if self._schleuse.nur_eigene_stimme:
                wert = await self._pruefer.urteil() if self._pruefer is not None else None
                fremd = wert is not None and wert < self._schwelle
                ungeprueft = wert is None and len(woerter) > MAX_UNGEPRUEFT_WOERTER and not self._erwartet_antwort()
                if (fremd or ungeprueft) and not self._wachwoerter.intersection(woerter):
                    self.verworfen += 1
                    grund = f"Stimme {wert:.2f}, Schwelle {self._schwelle:.2f}" if fremd else "Stimme nicht prüfbar"
                    logger.info(f"Nach der Pause überhört ({grund}): {frame.text!r}")
                    if self._bei_verworfen is not None:
                        self._bei_verworfen(frame.text)
                    return
                self._schleuse.angenommen()
        await self.push_frame(frame, direction)


def _wav_lesen(pfad: Path) -> bytes:
    with wave.open(str(pfad), "rb") as wf:
        if (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise SystemExit(f"{pfad} muss 16 kHz, mono, 16 Bit sein.")
        return wf.readframes(wf.getnframes())


def _aufnehmen(sekunden: float, geraet: int | None = None) -> bytes:
    import pyaudio

    audio = pyaudio.PyAudio()
    try:
        stueck = int(SAMPLE_RATE * 0.02)
        strom = audio.open(
            format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, input=True,
            input_device_index=geraet, frames_per_buffer=stueck,
        )
        daten = b"".join(strom.read(stueck, exception_on_overflow=False) for _ in range(int(sekunden / 0.02)))
        strom.close()
        return daten
    finally:
        audio.terminate()


def _mikrofon(settings, raum: str | None) -> tuple[int | None, str]:
    """Das Mikrofon des Raums, wie Alfred es im Betrieb nimmt; ohne Räume das Standardgerät."""
    from alfred.room import DeviceNotFoundError, find_device

    if not settings.raeume:
        return None, "Standardmikrofon"
    name = raum or next(iter(settings.raeume))
    if name not in settings.raeume:
        raise SystemExit(f"Raum '{name}' steht nicht unter raeume in config/alfred.yaml.")
    eintrag = settings.raeume[name].mikrofon
    try:
        geraet = find_device(eintrag, output=False)
    except DeviceNotFoundError as e:
        raise SystemExit(str(e)) from e
    return geraet, f"Raum {name}" + (f" ({eintrag})" if eintrag else "")


def _probe(erkenner: Stimmerkenner, geraet: int | None, aufforderung: str, sekunden: float, max_secs: float | None):
    """Eine Aufnahme mit bis zu drei Versuchen; gibt den Vektor zurück oder None."""
    for _ in range(3):
        try:
            input(f"{aufforderung}  [Enter, dann sprechen]")
        except EOFError:
            return None
        print("  … Aufnahme läuft")
        audio = _aufnehmen(sekunden, geraet)
        spitze = int(np.abs(np.frombuffer(audio, dtype=np.int16)).max()) if audio else 0
        sprache = sprache_zuschneiden(audio, max_secs)
        if len(sprache) < SAMPLE_RATE * 2 * 0.6:
            print(f"  Kaum Sprache gehört (Pegel {spitze / 32768:.0%}); noch einmal, etwas lauter.")
            continue
        if spitze > 32000:
            print("  Übersteuert; etwas leiser oder weiter weg, das Profil nimmt es trotzdem.")
        if vektor := erkenner.vektor(sprache):
            return vektor
        print("  Kein Stimmvektor aus dieser Aufnahme; noch einmal.")
    return None


def _anlernen_live(erkenner, geraet, beschreibung: str, sekunden: float, anzahl: int):
    print(f"\nStimmprofil anlernen mit dem Mikrofon von {beschreibung}.")
    print("Setz oder stell dich dahin, wo du sonst mit Alfred sprichst, und erzähl in normaler Lautstärke.")
    print("Die kurzen Sätze sprich so, wie es dabeisteht: Dann erkennt Alfred dich auch leise, laut und nebenbei.\n")
    rede = _probe(
        erkenner, geraet,
        f"Teil 1: {sekunden:.0f} Sekunden frei erzählen, etwa was du heute vorhast.", sekunden, None,
    )
    if rede is None:
        raise SystemExit("Keine brauchbare Aufnahme der freien Rede; Mikrofon prüfen: python -m alfred.room buero --hoertest")
    kurze = []
    for i, (satz, art) in enumerate(PROBE_SAETZE[:anzahl], 1):
        if vektor := _probe(erkenner, geraet, f"Teil 2, Satz {i} von {anzahl}, {art}: „{satz}“", 4.0, KURZ_SECS):
            kurze.append(vektor)
    if len(kurze) < 2:
        raise SystemExit("Zu wenige brauchbare Wachwort-Sätze; noch einmal anlernen.")
    return [rede], kurze


def _uebernehmen(settings, modell: str, empfehlung: dict[str, float] | None) -> None:
    """Trägt die empfohlenen Schwellen und bei einem Wechsel das Modell in config/alfred.yaml ein.

    Ein neues Modell kommt ohne Rückfrage hinein, mit ihm die Schwellen: Ein Profil, das nicht zum
    eingestellten Modell passt, bliebe ungenutzt, und die alten Schwellen gehören zum alten Modell.
    """
    jetzt = {"schwelle_wachwort": settings.stimmerkennung.schwelle_wachwort, "schwelle": settings.stimmerkennung.schwelle}
    if empfehlung:
        print(
            "Empfehlung: Gesprächspausen ab {schwelle_wachwort:.2f} (jetzt {alt_w:.2f}), Freigaben ab {schwelle:.2f} (jetzt {alt_f:.2f}).".format(
                **empfehlung, alt_w=jetzt["schwelle_wachwort"], alt_f=jetzt["schwelle"]
            )
        )
    werte: dict[str, object] = {}
    if modell != settings.stimmerkennung.modell:
        print(f"Neues Sprechermodell {modell} (bisher {settings.stimmerkennung.modell}); es kommt mit den Schwellen in config/alfred.yaml.")
        if not empfehlung:
            print("Ohne Empfehlung (zu wenige kurze Sätze) bleiben die alten Schwellen; besser mit Mikrofon neu anlernen.")
        werte = {"modell": modell, **(empfehlung or {})}
    elif empfehlung and empfehlung != jetzt:
        try:
            antwort = input("In config/alfred.yaml übernehmen? [J/n] ").strip().casefold()
        except EOFError:
            antwort = "n"
        if antwort in ("", "j", "ja", "y", "yes"):
            werte = dict(empfehlung)
        else:
            print("Nicht übernommen; die Werte stehen unter stimmerkennung in config/alfred.yaml.")
    if werte:
        pfad = konfig_anlegen()
        pfad.write_text(werte_setzen(pfad.read_text(encoding="utf-8"), "stimmerkennung", werte), encoding="utf-8")
        print("Übernommen. Alfred neu starten, damit es gilt.")


def main() -> None:
    from alfred.config import load_settings

    parser = argparse.ArgumentParser(description="Alexanders Stimme anlernen und prüfen")
    parser.add_argument("--anlernen", nargs="*", metavar="WAV", help="Profil per Mikrofon anlegen, oder aus Dateien: freie Rede, dann kurze Sätze")
    parser.add_argument("--pruefen", nargs="?", const="", metavar="WAV", help="Ähnlichkeit zum Profil, live oder aus einer Datei")
    parser.add_argument("--raum", help="Mikrofon dieses Raums nehmen (Vorgabe: der erste in config/alfred.yaml)")
    parser.add_argument("--sekunden", type=float, default=20.0, help="Dauer der freien Rede beim Anlernen")
    parser.add_argument("--saetze", type=int, default=len(PROBE_SAETZE), help=f"so viele Wachwort-Sätze beim Anlernen (höchstens {len(PROBE_SAETZE)})")
    parser.add_argument(
        "--modell",
        help=f"Sprechermodell; beim Anlernen Vorgabe {EMPFOHLENES_MODELL} (kommt in config/alfred.yaml), beim Prüfen das eingestellte",
    )
    parser.add_argument("--profil", type=Path, help="anderer Speicherort des Profils, etwa zum Ausprobieren")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("PA_ALSA_PLUGHW", "1")  # Linux: USB-Mikrofone auch bei 16 kHz öffnen

    settings = load_settings()
    profilpfad = args.profil or settings.pfad(settings.stimmerkennung.profil)

    if args.anlernen is not None:
        modell = args.modell or EMPFOHLENES_MODELL
        profil = Stimmprofil(profilpfad)
        erkenner = stimmerkenner(modell, settings.wachwort.modell)
        if args.anlernen:
            dateien = [Path(d) for d in args.anlernen]
            rede = [erkenner.vektor(sprache_zuschneiden(_wav_lesen(dateien[0])))]
            kurze = [erkenner.vektor(sprache_zuschneiden(_wav_lesen(d), KURZ_SECS)) for d in dateien[1:]]
            rede, kurze = [v for v in rede if v], [v for v in kurze if v]
            if not rede and not kurze:
                raise SystemExit("Kein Stimmvektor; war genug Sprache in den Aufnahmen?")
            quelle = "Dateien: " + ", ".join(d.name for d in dateien)
        else:
            geraet, quelle = _mikrofon(settings, args.raum)
            rede, kurze = _anlernen_live(erkenner, geraet, quelle, args.sekunden, max(2, min(args.saetze, len(PROBE_SAETZE))))
        info: dict = {
            "angelernt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mikrofon": quelle,
            "modell": modell,
            "proben": len(rede) + len(kurze),
        }
        empfehlung = None
        if len(kurze) >= 2:
            werte = selbstaehnlichkeit(kurze, rede)
            empfehlung = schwellen_empfehlen(werte)
            info |= {"eigene_min": round(min(werte), 2), "eigene_median": round(statistics.median(werte), 2)}
            print("\nDeine kurzen Sätze gegen das Profil aus den übrigen Proben: " + "  ".join(f"{w:.2f}" for w in werte))
            if min(werte) < 0.35:
                print("Die Proben ähneln sich wenig. Raumhall oder Abstand zu groß? Näher ans Mikrofon und neu anlernen.")
        profil.speichern(mittelvektor([*rede, *kurze]), **info)
        print(f"Profil gespeichert: {profilpfad} ({info['proben']} Proben, {quelle}, Modell {modell})")
        if args.profil is None:
            _uebernehmen(settings, modell, empfehlung)
        if not settings.stimmerkennung.aktiv:
            print("Die Stimmerkennung ist aus; sie wirkt erst mit aktiv: true unter stimmerkennung in config/alfred.yaml.")
    elif args.pruefen is not None:
        modell = args.modell or settings.stimmerkennung.modell
        profil = Stimmprofil(profilpfad, modell)
        if not profil.vorhanden:
            anders = f" (das vorhandene ist mit {profil.anderes_modell} angelernt)" if profil.anderes_modell else ""
            raise SystemExit(f"Noch kein Profil für {modell}{anders}; erst --anlernen.")
        erkenner = stimmerkenner(modell, settings.wachwort.modell)
        schwelle_w, schwelle_f = settings.stimmerkennung.schwelle_wachwort, settings.stimmerkennung.schwelle
        # Die mildere Schwelle gilt für Sätze in Gesprächspausen, fürs Wachwort nur, wenn es eingeschaltet ist.
        mild = "Wachwort und Gesprächspause" if settings.stimmerkennung.wachwort else "Gesprächspause"
        if args.pruefen:
            proben = [erkenner.vektor(sprache_zuschneiden(_wav_lesen(Path(args.pruefen))))]
        else:
            geraet, quelle = _mikrofon(settings, args.raum)
            print(f"\nPrüfung mit dem Mikrofon von {quelle}, aus deiner üblichen Entfernung.")
            proben = [
                _probe(erkenner, geraet, f"Satz {i}, {art}: „{satz}“", 4.0, KURZ_SECS)
                for i, (satz, art) in enumerate(PROBE_SAETZE[:3], 1)
            ]
        for vektor in proben:
            if not vektor:
                print("Kein Stimmvektor; zu wenig Sprache.")
                continue
            wert = aehnlichkeit(vektor, profil.vektor)
            print(
                f"Ähnlichkeit {wert:.2f}: {mild} {'zählt' if wert >= schwelle_w else 'zählt nicht'} (ab {schwelle_w:.2f}), "
                f"Freigabe {'zählt' if wert >= schwelle_f else 'zählt nicht'} (ab {schwelle_f:.2f})"
            )
    else:
        parser.error("--anlernen oder --pruefen angeben")


if __name__ == "__main__":
    main()
