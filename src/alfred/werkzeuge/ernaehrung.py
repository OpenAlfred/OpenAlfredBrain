"""Gruppe ernaehrung: Ernährungstagebuch und Gewicht."""

from __future__ import annotations

from pipecat.services.llm_service import FunctionCallParams

from alfred.ernaehrung import bilanz
from alfred.resources import Ressourcen
from alfred.werkzeuge.basis import _res
from alfred.werkzeuge.registrierung import werkzeug


def _ernaehrung(res) -> object:
    return res.ernaehrung and res.schaetzer


def _ziele(res: Ressourcen) -> dict:
    profil = res.gedaechtnis.profil()

    def zahl(feld):
        try:
            return float(str(profil.get(feld, "")).replace(",", ".").split()[0])
        except (ValueError, IndexError):
            return None

    return {"kalorienziel": zahl("kalorienziel"), "eiweissziel": zahl("eiweissziel"), "zielgewicht": zahl("zielgewicht")}


@werkzeug("ernaehrung", "schreibend", braucht=_ernaehrung, rueckfrage="Mahlzeit ins Ernährungstagebuch eintragen: {mahlzeit}")
async def essen_protokollieren(params: FunctionCallParams, mahlzeit: str):
    """Trägt eine Mahlzeit ins Ernährungstagebuch ein und schätzt Kalorien und Nährwerte.

    Args:
        mahlzeit: Was gegessen wurde, mit Mengen, wenn genannt ("zwei Brötchen mit Käse, ein Apfel").
    """
    res = _res(params)
    try:
        werte = await res.schaetzer.schaetzen(mahlzeit)
    except Exception as e:
        await params.result_callback({"fehler": f"Schätzung fehlgeschlagen: {e}"})
        return
    eintrag = res.ernaehrung.eintragen(mahlzeit, werte, "schaetzung", werte.get("annahme", ""))
    heute = bilanz(res.ernaehrung.tag())
    await params.result_callback(
        {
            "eingetragen": eintrag.ansage(),
            "annahme": eintrag.annahme,
            "heute_gesamt": heute,
            "ziele": _ziele(res),
            "hinweis": "Nenne Kalorien und Eiweiß der Mahlzeit und den Tagesstand; es sind Schätzungen.",
        }
    )


@werkzeug("ernaehrung", "lesend", braucht=_ernaehrung)
async def ernaehrung_heute(params: FunctionCallParams):
    """Sagt, was heute gegessen wurde und wo der Tag gegenüber den Zielen steht."""
    res = _res(params)
    mahlzeiten = res.ernaehrung.tag()
    await params.result_callback(
        {"mahlzeiten": [m.ansage() for m in mahlzeiten] or ["noch nichts eingetragen"], "gesamt": bilanz(mahlzeiten), "ziele": _ziele(res)}
    )


@werkzeug("ernaehrung", "schreibend", braucht=_ernaehrung, rueckfrage="Gewicht {kilogramm} Kilogramm eintragen")
async def gewicht_merken(params: FunctionCallParams, kilogramm: float):
    """Trägt das heutige Körpergewicht ein.

    Args:
        kilogramm: Das Gewicht in Kilogramm.
    """
    res = _res(params)
    try:
        gewichte = res.ernaehrung.gewicht_merken(kilogramm)
    except ValueError as e:
        await params.result_callback({"fehler": str(e)})
        return
    verlauf = sorted(gewichte.items())[-8:]
    await params.result_callback({"gemerkt": kilogramm, "verlauf": dict(verlauf), "zielgewicht": _ziele(res)["zielgewicht"]})
