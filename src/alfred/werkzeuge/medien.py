"""Gruppe medien: Musik über Spotify."""

from __future__ import annotations

import asyncio

from pipecat.services.llm_service import FunctionCallParams

from alfred.werkzeuge.basis import _res
from alfred.werkzeuge.registrierung import werkzeug


async def _musik(params: FunctionCallParams, aktion, *args) -> None:
    """Führt eine blockierende Spotify-Aktion im Thread aus und meldet Fehler lesbar."""
    try:
        ergebnis = await asyncio.to_thread(aktion, *args)
    except ValueError as e:
        await params.result_callback({"fehler": str(e)})
        return
    except Exception as e:
        await params.result_callback({"fehler": f"Spotify: {str(e)[:160]}"})
        return
    await params.result_callback({"ok": True, "ergebnis": ergebnis} if ergebnis else {"ok": True})


@werkzeug(
    "medien",
    "schreibend",
    braucht=lambda res: res.musik,
    # was_laeuft ändert nichts und läuft auch bei fremdem Inhalt frei.
    rueckfrage=lambda aktion, suche, art, prozent: (
        None
        if aktion == "was_laeuft"
        else f"Spotify: {art} '{suche}' abspielen"
        if aktion == "abspielen"
        else f"Spotify: Lautstärke auf {prozent} Prozent"
        if aktion == "lautstaerke"
        else f"Spotify: {aktion}"
    ),
)
async def musik(params: FunctionCallParams, aktion: str, suche: str = "", art: str = "titel", prozent: int = 50):
    """Steuert Spotify.

    Args:
        aktion: abspielen, pause, weiter, naechstes, lautstaerke oder was_laeuft.
        suche: Bei abspielen: Titel, Album, Playlist oder Künstler, zum Beispiel "Deep Focus".
        art: Bei abspielen: titel, album, playlist oder kuenstler.
        prozent: Bei lautstaerke: 0 bis 100.
    """
    aktionen = {
        "abspielen": (suche, art),
        "pause": (),
        "weiter": (),
        "naechstes": (),
        "lautstaerke": (prozent,),
        "was_laeuft": (),
    }
    if aktion not in aktionen:
        await params.result_callback({"fehler": f"Unbekannte Aktion '{aktion}'. Erlaubt: {', '.join(aktionen)}."})
        return
    await _musik(params, getattr(_res(params).musik, aktion), *aktionen[aktion])
