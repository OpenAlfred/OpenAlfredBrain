"""Gruppe system: Routinen und Kosten."""

from __future__ import annotations

from pipecat.services.llm_service import FunctionCallParams

from alfred.ereignisse import in_ruhezeit
from alfred.persona import uhrzeit_gesprochen
from alfred.routinen import parse_uhrzeit, parse_wochentage, wochentage_gesprochen
from alfred.werkzeuge.basis import _freigabe_anfordern, _jetzt, _res
from alfred.werkzeuge.registrierung import werkzeug


def _routinen(res) -> object:
    return res.routinen


@werkzeug("system", "bestaetigen", braucht=_routinen)
async def routine_anlegen(
    params: FunctionCallParams, name: str, auftrag: str, uhrzeit: str = "", wochentage: str = ""
):
    """Legt eine Routine an: einen Auftrag, den du zur Zeit von selbst mit deinen Werkzeugen ausführst ("jeden Morgen Wetter und Termine"). Eine bloße Erinnerung, auch wiederkehrende, ist erinnerung_setzen. Braucht Bestätigung.

    Args:
        name: Kurzer Name, z. B. Morgenbriefing.
        auftrag: Anweisung an dich selbst, z. B. "Nenne das Wetter für heute und die Termine des Tages."
        uhrzeit: HH:MM; leer = nur auf Zuruf.
        wochentage: leer, täglich, werktags, wochenende oder "Mo, Mi, Fr".
    """
    res = _res(params)
    try:
        # Angaben prüfen, bevor nachgefragt wird: Eine Rückfrage zu einer kaputten Zeit ist sinnlos.
        tage = parse_wochentage(wochentage)
        zeit = parse_uhrzeit(uhrzeit) if uhrzeit else None
    except ValueError as e:
        await params.result_callback({"fehler": str(e)})
        return

    async def anlegen():
        routine = res.routinen.anlegen(name, auftrag, uhrzeit, wochentage)
        return {"angelegt": routine.ansage()}

    zeitplan = (
        f"{wochentage_gesprochen(tage)} um {uhrzeit_gesprochen(_jetzt().replace(hour=zeit.hour, minute=zeit.minute))}"
        if zeit
        else "nur auf Zuruf"
    )
    ruhe = ""
    proaktiv = getattr(res.settings, "proaktiv", None)
    if zeit and proaktiv and in_ruhezeit(zeit, proaktiv.ruhe_von_zeit, proaktiv.ruhe_bis_zeit):
        # Die Zustellregeln halten Ungefragtes in der Ruhezeit zurück; das soll vor dem Ja bekannt sein,
        # sonst wartet Alexander auf eine Routine, die verfällt.
        ruhe = (
            f" (Achtung: {uhrzeit_gesprochen(_jetzt().replace(hour=zeit.hour, minute=zeit.minute))} liegt in der "
            f"Ruhezeit {proaktiv.ruhe_von} bis {proaktiv.ruhe_bis}, in der Alfred nichts von sich aus sagt; die "
            "Routine verfiele ungesprochen, außer die Ruhezeit wird in der Konfiguration geändert)"
        )
    await _freigabe_anfordern(params, f"Routine '{name}' anlegen, {zeitplan}: {auftrag}{ruhe}", anlegen)


@werkzeug("system", "lesend", braucht=_routinen)
async def routinen_anzeigen(params: FunctionCallParams):
    """Listet alle Routinen mit Zeitplan und Auftrag."""
    routinen = _res(params).routinen.alle()
    await params.result_callback({"routinen": [r.ansage() for r in routinen] or ["keine Routinen"]})


@werkzeug("system", "schreibend", braucht=_routinen, rueckfrage="Routine '{name}' löschen")
async def routine_loeschen(params: FunctionCallParams, name: str):
    """Löscht eine Routine anhand ihres Namens.

    Args:
        name: Der Name der Routine.
    """
    routine = _res(params).routinen.loeschen(name)
    await params.result_callback({"geloescht": routine.name if routine else None})


@werkzeug("system", "lesend", braucht=_routinen)
async def routine_ausfuehren(params: FunctionCallParams, name: str):
    """Startet eine Routine sofort, etwa auf "Mach mein Morgenbriefing". Liefert den Auftrag, den du dann ausführst.

    Args:
        name: Der Name der Routine.
    """
    routine = _res(params).routinen.finden(name)
    if not routine:
        await params.result_callback({"fehler": f"Keine Routine mit dem Namen '{name}'."})
        return
    await params.result_callback(
        {"auftrag": routine.auftrag, "hinweis": "Führe den Auftrag jetzt mit deinen Werkzeugen aus."}
    )


@werkzeug("system", "lesend", braucht=lambda res: res.kosten)
async def kosten_heute(params: FunctionCallParams, tag: str = ""):
    """Sagt, was Alfred heute verbraucht hat und ungefähr gekostet hat.

    Args:
        tag: Optional ein anderer Tag im Format JJJJ-MM-TT.
    """
    bericht = _res(params).kosten.bericht(tag or None)
    cent = round(bericht.get("kosten_dollar", 0) * 100)
    bericht["gesprochen"] = (
        f"etwa {cent} Cent" + (f"; ohne Preis: {', '.join(bericht['unbepreist'])}" if bericht.get("unbepreist") else "")
    )
    await params.result_callback(bericht)
