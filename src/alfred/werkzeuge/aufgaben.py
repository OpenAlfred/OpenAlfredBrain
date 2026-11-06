"""Gruppe aufgaben: die To-do-Liste."""

from __future__ import annotations

from pipecat.services.llm_service import FunctionCallParams

from alfred.werkzeuge.basis import _res
from alfred.werkzeuge.registrierung import werkzeug


def _aufgaben(res) -> object:
    return res.aufgaben


@werkzeug(
    "aufgaben",
    "schreibend",
    braucht=_aufgaben,
    rueckfrage=lambda text, faellig: f"Aufgabe anlegen: {text}" + (f", fällig am {faellig}" if faellig else ""),
)
async def aufgabe_anlegen(params: FunctionCallParams, text: str, faellig: str = ""):
    """Setzt eine Aufgabe auf die To-do-Liste.

    Args:
        text: Die Aufgabe in einem kurzen Satz, zum Beispiel "Steuererklärung abschicken".
        faellig: Optionales Datum im Format JJJJ-MM-TT.
    """
    try:
        aufgabe = _res(params).aufgaben.anlegen(text, faellig)
    except ValueError as e:
        await params.result_callback({"fehler": str(e)})
        return
    await params.result_callback({"angelegt": aufgabe.ansage()})


@werkzeug("aufgaben", "lesend", braucht=_aufgaben)
async def aufgaben_anzeigen(params: FunctionCallParams):
    """Listet die offenen Aufgaben mit Nummer, nächste Fälligkeit zuerst."""
    offene = _res(params).aufgaben.offene()
    await params.result_callback({"aufgaben": [a.ansage() for a in offene] or ["keine offenen Aufgaben"]})


@werkzeug(
    "aufgaben",
    "schreibend",
    braucht=_aufgaben,
    rueckfrage=lambda nummer, text: f"Aufgabe Nummer {nummer} abhaken" if nummer else f"Aufgabe '{text}' abhaken",
)
async def aufgabe_erledigt(params: FunctionCallParams, nummer: int = 0, text: str = ""):
    """Hakt eine Aufgabe ab, über ihre Nummer oder über Wörter aus ihrem Text.

    Args:
        nummer: Die Nummer aus der Liste, wenn bekannt.
        text: Sonst Wörter aus der Aufgabe, zum Beispiel "Steuererklärung".
    """
    aufgaben = _res(params).aufgaben
    if not nummer:
        treffer = aufgaben.finden(text)
        if len(treffer) != 1:
            await params.result_callback(
                {"fehler": "Keine eindeutige Aufgabe gefunden. Frag nach, welche gemeint ist.", "treffer": [a.ansage() for a in treffer]}
            )
            return
        nummer = treffer[0].nummer
    aufgabe = aufgaben.erledigen(nummer)
    await params.result_callback({"erledigt": aufgabe.text if aufgabe else None})
