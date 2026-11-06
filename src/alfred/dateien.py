"""Gemeinsame Grundlage für Bestände, die als YAML-Datei liegen und von mehreren Prozessen
(Stimme und App) benutzt werden: Vor jedem Zugriff wird neu geladen, wenn sich die Datei
geändert hat, und jedes Speichern merkt sich den eigenen Stand.

Jede Seite hat ihre eigene Instanz auf derselben Datei. Darum gilt:

- Geschrieben wird über eine Nebendatei und `os.replace`: Wer die Datei liest, sieht nie eine leere
  oder halbe, sondern die alte oder die neue Fassung ganz.
- Die Kennung der eigenen Fassung (Zeitstempel, Größe, Inode) wird an der Nebendatei VOR dem
  Umbenennen genommen. Schreibt die andere Seite gleich danach, gilt ihre Fassung nicht als die
  eigene, und der nächste Zugriff lädt sie.
- Eine Datei, die sich nicht lesen lässt (leer, halb, kein gültiger Bestand, gesperrt), wird nie als
  leerer Bestand geladen: Es gilt der Stand im Speicher, und der nächste Zugriff liest erneut.
- Scheitert das Speichern, geht der Fehler an den Aufrufer, und der Speicher kehrt beim nächsten
  Zugriff zur Datei zurück, damit er nichts zeigt, was nicht gespeichert ist.

Messungen und Grenzen: docs/erkenntnisse.md, 16.10.2026.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from pathlib import Path

import yaml
from loguru import logger

# Unter Windows scheitert `os.replace` mit PermissionError, solange die andere Seite die Datei gerade
# geöffnet hat (Python öffnet ohne FILE_SHARE_DELETE), und ihr Lesen scheitert ebenso, während die
# Datei ersetzt wird. Beides dauert Bruchteile einer Millisekunde, nach einer Pause klappt es. Das
# Warten blockiert den Ereignis-Loop; die Obergrenzen halten es unter 50 ms.
PAUSE_SECS = 0.005
SCHREIB_VERSUCHE = 10
LESE_VERSUCHE = 3

# Mit libyaml, wo es das gibt: Verhalten wie SafeLoader (baut nur einfache Typen, keine Objekte), aber das
# Nachladen ist rund sechsmal schneller (70 Artikel: 3,0 statt 18,7 ms). Das halbiert das Zeitfenster zwischen
# Nachladen und Speichern und verkürzt die Zeit, die der Ereignis-Loop beim Nachladen steht (docs/erkenntnisse.md,
# 16.10.2026). Ohne libyaml bleibt der Lader in Python.
LADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

Stand = tuple[int, int, int]


def stand_von(pfad: Path) -> Stand:
    """Woran man eine andere Fassung der Datei erkennt. Der Zeitstempel allein täuscht: Zwei
    Schreibvorgänge im selben Takt der Uhr haben denselben (unter Windows blieben beim Überschreiben
    an Ort und Stelle bei 13 bis 62 % schneller Schreibvorgänge mit neuem Inhalt Zeitstempel, Größe
    und Inode gleich). Jede Fassung, die atomar ersetzt wird, kommt als neue Datei und hat darum eine
    andere Inode."""
    st = pfad.stat()
    return st.st_mtime_ns, st.st_size, st.st_ino


def _wiederholt(aktion, versuche: int):
    """Führt `aktion` aus; bei einem PermissionError folgt nach einer kurzen Pause der nächste Versuch."""
    for versuch in range(1, versuche + 1):
        try:
            return aktion()
        except PermissionError:
            if versuch == versuche:
                raise
            time.sleep(PAUSE_SECS)


class Dateibestand:
    def __init__(self, pfad: Path):
        self._pfad = pfad
        # Welche Fassung der Datei der Speicher abbildet (None: die Datei fehlte). `_geladen` sagt, ob der
        # Speicher schon aus der Datei oder als leerer Bestand aufgebaut ist.
        self._stand: Stand | None = None
        self._geladen = False
        # Bei welcher Fassung das Lesen zuletzt scheiterte (damit das Log nicht überläuft).
        self._unlesbar: Stand | str | None = None
        # Ein Speichern scheiterte: Der Speicher ist der Datei voraus und wird nicht mehr gezeigt.
        self._verworfen = False
        self._auffrischen(erzwingen=True)

    def _laden(self, daten) -> None:
        """Übernimmt den Inhalt der Datei (None, wenn sie fehlt oder beim Start nicht zu lesen ist)."""
        raise NotImplementedError

    def _auffrischen(self, erzwingen: bool = False) -> None:
        """Lädt neu, wenn die Datei sich seit dem letzten Lesen oder Speichern geändert hat. Eine Datei,
        die sich nicht lesen lässt oder nach dem ersten Laden fehlt, ersetzt den Speicher nicht; der
        nächste Aufruf versucht es wieder. Nur beim Start gilt sie als leerer Bestand."""
        try:
            # Vor dem Lesen: Schreibt die andere Seite dazwischen, lädt der nächste Aufruf einmal zu oft.
            stand = stand_von(self._pfad)
        except FileNotFoundError:
            stand = None
        except OSError as e:
            self._nicht_gelesen("stat", e)
            return
        if not (erzwingen or self._verworfen or not self._geladen) and stand == self._stand:
            return
        if stand is None:
            if self._stand is not None and not self._verworfen:
                return  # Verschwunden, etwa mitten in einer Wiederherstellung: Der Speicher gilt.
            self._laden(None)
        else:
            try:
                daten = yaml.load(_wiederholt(lambda: self._pfad.read_text(encoding="utf-8"), LESE_VERSUCHE), Loader=LADER)
                if daten is None:
                    raise ValueError("leer")  # Ein Bestand schreibt immer Daten, auch einen leeren.
                self._laden(daten)
            except Exception as e:  # noqa: BLE001 - jede Datei ohne gültigen Bestand gilt als unlesbar
                self._nicht_gelesen(stand, e)
                return
        self._stand, self._geladen, self._unlesbar, self._verworfen = stand, True, None, False

    def _nicht_gelesen(self, stand: Stand | str, fehler: Exception) -> None:
        """Merkt, dass diese Fassung der Datei nicht zu lesen war; beim Start beginnt ein leerer Bestand."""
        if stand != self._unlesbar:
            grund = " ".join(str(fehler).split())
            logger.warning(f"{self._pfad.name} nicht gelesen, es gilt der Stand im Speicher: {type(fehler).__name__} {grund}")
            self._unlesbar = stand
        if not self._geladen:
            self._laden(None)
            self._geladen = True

    def _schreiben(self, text: str) -> None:
        """Ersetzt die Datei ganz oder gar nicht. Scheitert das, bleibt die Datei, wie sie war, es bleibt
        keine Nebendatei zurück, und der Fehler geht an den Aufrufer. Der hat den Speicher schon geändert;
        der nächste Zugriff lädt ihn deshalb aus der Datei neu."""
        # Die Nebendatei trägt die Prozessnummer, weil die App und der Sprach-Prozess zugleich speichern können.
        vorlaeufig = self._pfad.with_name(f"{self._pfad.name}.{os.getpid()}.tmp")
        try:
            self._pfad.parent.mkdir(parents=True, exist_ok=True)
            vorlaeufig.write_text(text, encoding="utf-8")
            # Das Umbenennen ändert die Kennung nicht. So gehört sie sicher uns, auch wenn die andere Seite
            # gleich danach schreibt; ein Blick auf die fertige Datei könnte schon ihre Fassung treffen.
            stand = stand_von(vorlaeufig)
            ungesehen = self._ersetztes_pruefen()
            _wiederholt(lambda: os.replace(vorlaeufig, self._pfad), SCHREIB_VERSUCHE)
        except BaseException:
            with contextlib.suppress(OSError):
                vorlaeufig.unlink(missing_ok=True)
            self._verworfen = True
            raise
        self._stand, self._geladen, self._unlesbar, self._verworfen = stand, True, None, False
        if ungesehen:  # Erst jetzt: Zwischen der Prüfung und dem Ersetzen soll nichts warten, auch kein Log.
            logger.warning(f"{self._pfad.name}: Die andere Seite hat gespeichert, seit hier zuletzt gelesen wurde; ihre Fassung ist ersetzt")

    def _ersetztes_pruefen(self) -> bool:
        """Kurz vor dem Ersetzen: Was jetzt in der Datei liegt, geht verloren. War es unlesbar, bleibt eine
        Kopie daneben liegen (<Name>.defekt): Der Speicher gilt, aber was in der Datei stand, soll nicht ganz
        verschwinden. Gibt True zurück, wenn es eine Fassung war, die diese Seite nie gelesen hat (die andere
        Seite hat zwischen Laden und Speichern gespeichert): Ohne Schloss lässt sich das Zeitfenster
        verkleinern, aber nicht schließen, und das Log soll zeigen, ob es getroffen wurde."""
        try:
            aktuell = stand_von(self._pfad)
        except OSError:
            return False  # Es gibt nichts zu ersetzen.
        if aktuell == self._stand:
            return False
        if aktuell != self._unlesbar:
            return True
        if aktuell[1]:
            with contextlib.suppress(OSError):
                shutil.copyfile(self._pfad, self._pfad.with_name(self._pfad.name + ".defekt"))
        return False
