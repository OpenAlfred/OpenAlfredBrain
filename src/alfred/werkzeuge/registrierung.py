"""Registrierung der Werkzeuge: Gruppe, Freigabestufe und Rückfrage stehen am Werkzeug selbst.

Ein Werkzeug meldet sich mit ``@werkzeug(gruppe, stufe, braucht=..., rueckfrage=...)`` an. Die
Gruppe entscheidet, in welchen Modi es dem Modell angeboten wird (Persönlichkeit, ``werkzeuge:``
je Modus; ``kern`` gilt immer), die Stufe sagt, was es anrichten kann, und ``braucht`` prüft an den
Ressourcen, ob die Anbindung eingerichtet ist. ``privat`` nimmt es im Gästemodus heraus, auch
aus ``kern``. ``build_tools`` (auswahl.py) baut die Liste aus diesem Register, in der Reihenfolge
der Registrierung.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from alfred.persona import GRUPPEN

if TYPE_CHECKING:
    from alfred.resources import Ressourcen

Stufe = Literal["lesend", "schreibend", "bestaetigen"]

# lesend: liest oder rechnet, oder steuert nur das Gespräch (bestaetigen, abbrechen, Schleuse,
# Zuhören); läuft immer frei. schreibend: ändert Alfreds eigene Daten oder löst etwas aus, das nur
# Alexander betrifft (Push, Musik, Modus); läuft frei, solange nur Alexander spricht, und fragt mit
# seiner `rueckfrage` nach, sobald fremder Text im Gespräch steht (Ressourcen.fremder_inhalt,
# basis._mit_fremdschutz). bestaetigen: wirkt nach außen (Kalender, Nachrichten, Mails) oder
# dauerhaft auf Alfreds Verhalten (Regeln, Routinen) und legt die Aktion nur ab; erst das
# Werkzeug ``bestaetigen`` führt sie aus (basis.py).
STUFEN: tuple[Stufe, ...] = ("lesend", "schreibend", "bestaetigen")


@dataclass(frozen=True)
class Rueckfrage:
    """Wie ein schreibendes Werkzeug bei fremdem Inhalt nachfragt.

    beschreibung nennt alle Werte des Aufrufs, als Vorlage mit {argument} oder als Funktion der
    Argumente, damit ein untergeschobener Wert auffällt. Liefert die Funktion None, läuft der
    Aufruf frei (musik mit was_laeuft ändert nichts). protokoll ist die Fassung fürs Log, ohne
    Telefonnummer; leer heißt wie beschreibung.
    """

    beschreibung: str | Callable[..., str | None]
    protokoll: str | Callable[..., str] = ""


@dataclass(frozen=True)
class Werkzeug:
    """Ein registriertes Werkzeug samt Gruppe, Stufe, Voraussetzung und Rückfrage."""

    funktion: Callable
    gruppe: str
    stufe: Stufe
    braucht: Callable[[Ressourcen], object] | None = None
    rueckfrage: Rueckfrage | None = None  # nur bei Stufe schreibend
    privat: bool = False  # fehlt im Gästemodus (auswahl.werkzeuge_fuer)

    @property
    def name(self) -> str:
        return self.funktion.__name__

    def verfuegbar(self, res: Ressourcen) -> bool:
        """Ob die Anbindung, die das Werkzeug braucht, in dieser Sitzung eingerichtet ist."""
        return self.braucht is None or bool(self.braucht(res))


REGISTER: dict[str, Werkzeug] = {}


def werkzeug(
    gruppe: str,
    stufe: Stufe,
    braucht: Callable[[Ressourcen], object] | None = None,
    rueckfrage: Rueckfrage | str | Callable[..., str | None] | None = None,
    *,
    privat: bool = False,
) -> Callable[[Callable], Callable]:
    """Registriert eine Pipecat-Direktfunktion als Werkzeug.

    Args:
        gruppe: Eine Gruppe aus ``alfred.persona.GRUPPEN``; ``kern`` ist in jedem Modus dabei.
        stufe: lesend, schreibend oder bestaetigen (siehe STUFEN).
        braucht: Prüft an den Ressourcen, ob das Werkzeug angeboten wird, etwa
            ``lambda res: res.kalender``; None heißt immer.
        rueckfrage: Pflicht bei Stufe schreibend, sonst verboten: die Rückfrage bei fremdem Inhalt,
            als Rueckfrage, Vorlage mit {argument} oder Funktion der Argumente (siehe Rueckfrage).
        privat: Liest oder ändert, was nur Alexander angeht (Erinnerungen, Gedächtnis, Regeln,
            Profil). Im Gästemodus bekommt das Modell es nicht zu sehen. Nötig nur in ``kern``;
            die anderen Gruppen schaltet der Gästemodus über ``werkzeuge:`` ganz ab.
    """
    if gruppe not in GRUPPEN:
        raise ValueError(f"Unbekannte Werkzeuggruppe '{gruppe}'. Es gibt: {', '.join(GRUPPEN)}")
    if stufe not in STUFEN:
        raise ValueError(f"Unbekannte Stufe '{stufe}'. Es gibt: {', '.join(STUFEN)}")
    if (stufe == "schreibend") != (rueckfrage is not None):
        raise ValueError("Ein schreibendes Werkzeug braucht eine rueckfrage, alle anderen haben keine.")
    if rueckfrage is not None and not isinstance(rueckfrage, Rueckfrage):
        rueckfrage = Rueckfrage(rueckfrage)

    def einhaengen(funktion: Callable) -> Callable:
        erster = next(iter(inspect.signature(funktion).parameters), None)
        if not inspect.iscoroutinefunction(funktion) or erster != "params":
            raise TypeError(f"Werkzeug {funktion.__name__} muss async sein und mit 'params' beginnen")
        if funktion.__name__ in REGISTER:
            raise ValueError(f"Werkzeug '{funktion.__name__}' ist doppelt registriert")
        eintrag = Werkzeug(funktion, gruppe, stufe, braucht, rueckfrage, privat)
        REGISTER[funktion.__name__] = eintrag
        funktion.werkzeug = eintrag  # type: ignore[attr-defined]
        return funktion

    return einhaengen
