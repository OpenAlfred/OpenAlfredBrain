"""Gruppe finanzen: Ausgaben."""

from __future__ import annotations

from pipecat.services.llm_service import FunctionCallParams

from alfred.werkzeuge.basis import _res
from alfred.werkzeuge.registrierung import werkzeug


def _ausgaben(res) -> object:
    return res.ausgaben


@werkzeug(
    "finanzen",
    "schreibend",
    braucht=_ausgaben,
    rueckfrage=lambda betrag, kategorie, text: f"Ausgabe von {betrag} Euro eintragen, {kategorie}" + (f": {text}" if text else ""),
)
async def ausgabe_merken(params: FunctionCallParams, betrag: float, kategorie: str = "Sonstiges", text: str = ""):
    """Trägt eine Ausgabe ein ("12 Euro fürs Mittagessen").

    Args:
        betrag: Betrag in Euro.
        kategorie: Lebensmittel, Essen gehen, Haushalt, Mobilität, Freizeit, Kleidung, Gesundheit oder Sonstiges.
        text: Wofür, kurz.
    """
    res = _res(params)
    try:
        ausgabe = res.ausgaben.merken(betrag, kategorie, text)
    except ValueError as e:
        await params.result_callback({"fehler": str(e)})
        return
    await params.result_callback({"eingetragen": f"{ausgabe.betrag:.2f} Euro, {ausgabe.kategorie}", "monat": res.ausgaben.uebersicht()})


@werkzeug("finanzen", "lesend", braucht=_ausgaben)
async def ausgaben_monat(params: FunctionCallParams):
    """Nennt die Ausgaben dieses Monats, gesamt und je Kategorie."""
    await params.result_callback(_res(params).ausgaben.uebersicht())
