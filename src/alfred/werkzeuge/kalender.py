"""Gruppe kalender: Termine lesen, anlegen, verschieben, absagen."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from pipecat.services.llm_service import FunctionCallParams

from alfred.kalender import Termin
from alfred.persona import WOCHENTAGE
from alfred.werkzeuge.basis import _freigabe_anfordern, _jetzt, _res
from alfred.werkzeuge.registrierung import werkzeug


def _kalender(res) -> object:
    return res.kalender


@werkzeug("kalender", "lesend", braucht=_kalender)
async def termine_abfragen(params: FunctionCallParams, datum: str = "", tage: int = 1):
    """Liest Termine aus dem Kalender.

    Args:
        datum: Erster Tag JJJJ-MM-TT, leer = heute; Wochentage über die Liste der nächsten Tage.
        tage: Anzahl Tage ab datum, 1 = der Tag, 7 = die Woche.
    """
    res = _res(params)
    jetzt = _jetzt()
    try:
        erster = datetime.strptime(datum, "%Y-%m-%d").replace(tzinfo=jetzt.tzinfo) if datum else jetzt
    except ValueError as e:
        await params.result_callback({"fehler": f"Datum unverständlich: {e}"})
        return
    # Heute ab jetzt, sonst ab Tagesbeginn; Ende immer Mitternacht nach dem letzten Tag.
    von = erster if erster.date() == jetzt.date() else erster.replace(hour=0, minute=0, second=0, microsecond=0)
    bis = erster.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=max(1, tage))
    try:
        termine = await asyncio.to_thread(res.kalender.termine, von, bis)
    except Exception as e:
        await params.result_callback({"fehler": f"Kalender nicht erreichbar: {e}"})
        return
    zeitraum = f"{WOCHENTAGE[von.weekday()]} {von:%d.%m.}" + (f" bis {bis - timedelta(days=1):%d.%m.}" if tage > 1 else "")
    await params.result_callback({"zeitraum": zeitraum, "termine": [t.ansage() for t in termine] or ["keine Termine"]})


@werkzeug("kalender", "bestaetigen", braucht=_kalender)
async def termin_anlegen(params: FunctionCallParams, titel: str, beginn: str, ende: str):
    """Legt einen Termin im Kalender an. Braucht eine Bestätigung des Gegenübers.

    Args:
        titel: Bezeichnung des Termins.
        beginn: Beginn im Format JJJJ-MM-TT HH:MM.
        ende: Ende im Format JJJJ-MM-TT HH:MM.
    """
    res = _res(params)
    try:
        tz = _jetzt().tzinfo
        von = datetime.strptime(beginn, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        bis = datetime.strptime(ende, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    except ValueError as e:
        await params.result_callback({"fehler": f"Zeitangabe unverständlich: {e}"})
        return

    async def anlegen():
        termin = await asyncio.to_thread(res.kalender.anlegen, titel, von, bis)
        return {"angelegt": termin.ansage()}

    # Mit Wochentag, damit ein falsch umgerechnetes Datum in der Rückfrage auffällt.
    await _freigabe_anfordern(
        params,
        f"Termin '{titel}' am {WOCHENTAGE[von.weekday()]}, {von:%d.%m. um %H:%M} Uhr anlegen",
        anlegen,
    )


def _termin_wann(termin: Termin, zeitpunkt: datetime | None = None) -> str:
    """Wochentag und Datum eines Termins für die Rückfrage, mit Uhrzeit, wenn er eine hat."""
    wann = zeitpunkt or termin.beginn
    tag = f"{WOCHENTAGE[wann.weekday()]}, {wann:%d.%m.}"
    return tag if termin.ganztags else f"{tag} um {wann:%H:%M} Uhr"


async def _termin_finden(params: FunctionCallParams, titel: str, datum: str) -> Termin | None:
    """Der gemeinte Termin; meldet selbst, wenn keiner oder mehrere passen."""
    res = _res(params)
    jetzt = _jetzt()
    if datum:
        try:
            tag = datetime.strptime(datum, "%Y-%m-%d").replace(tzinfo=jetzt.tzinfo)
        except ValueError as e:
            await params.result_callback({"fehler": f"Datum unverständlich: {e}"})
            return None
        von = tag.replace(hour=0, minute=0, second=0, microsecond=0)
        bis, wo = von + timedelta(days=1), f"am {WOCHENTAGE[von.weekday()]}, {von:%d.%m.}"
    else:
        von, bis, wo = jetzt, jetzt + timedelta(days=14), "in den nächsten zwei Wochen"
    try:
        treffer = await asyncio.to_thread(res.kalender.suchen, titel, von, bis)
    except Exception as e:
        await params.result_callback({"fehler": f"Kalender nicht erreichbar: {e}"})
        return None
    if not treffer:
        await params.result_callback(
            {"fehler": f"Kein Termin mit '{titel}' {wo} gefunden. Frag nach, welcher gemeint ist, oder lies mit termine_abfragen nach."}
        )
        return None
    if len(treffer) > 1:
        await params.result_callback(
            {
                "fehler": "Mehrere Termine passen. Frag, welcher gemeint ist, und gib dann dessen Datum an.",
                "termine": [t.ansage() for t in treffer],
            }
        )
        return None
    return treffer[0]


@werkzeug("kalender", "bestaetigen", braucht=_kalender)
async def termin_verschieben(
    params: FunctionCallParams, titel: str, neuer_beginn: str, datum: str = "", neues_ende: str = ""
):
    """Verschiebt einen Termin im Kalender. Braucht eine Bestätigung des Gegenübers.

    Args:
        titel: Der Termin, wie das Gegenüber ihn nennt, z. B. Zahnarzt.
        neuer_beginn: Neuer Beginn im Format JJJJ-MM-TT HH:MM.
        datum: Bisheriger Tag des Termins JJJJ-MM-TT, falls genannt; leer = die nächsten zwei Wochen.
        neues_ende: Neues Ende JJJJ-MM-TT HH:MM; leer = die Dauer bleibt gleich.
    """
    res = _res(params)
    try:
        tz = _jetzt().tzinfo
        von = datetime.strptime(neuer_beginn, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        bis = datetime.strptime(neues_ende, "%Y-%m-%d %H:%M").replace(tzinfo=tz) if neues_ende else None
    except ValueError as e:
        await params.result_callback({"fehler": f"Zeitangabe unverständlich: {e}"})
        return
    termin = await _termin_finden(params, titel, datum)
    if termin is None:
        return
    if bis is None:
        bis = von + (termin.ende - termin.beginn)
    if bis <= von:
        await params.result_callback({"fehler": "Das Ende liegt vor dem Beginn."})
        return

    async def verschieben():
        neu = await asyncio.to_thread(res.kalender.verschieben, termin, von, bis)
        return {"verschoben": neu.ansage()}

    await _freigabe_anfordern(
        params,
        f"Termin '{termin.titel}' von {_termin_wann(termin)} auf {_termin_wann(termin, von)} verschieben",
        verschieben,
    )


@werkzeug("kalender", "bestaetigen", braucht=_kalender)
async def termin_absagen(params: FunctionCallParams, titel: str, datum: str = ""):
    """Sagt einen Termin ab und löscht ihn aus dem Kalender. Braucht eine Bestätigung des Gegenübers.

    Args:
        titel: Der Termin, wie das Gegenüber ihn nennt, z. B. Zahnarzt.
        datum: Tag des Termins JJJJ-MM-TT, falls genannt; leer = die nächsten zwei Wochen.
    """
    res = _res(params)
    termin = await _termin_finden(params, titel, datum)
    if termin is None:
        return

    async def loeschen():
        await asyncio.to_thread(res.kalender.loeschen, termin)
        return {"abgesagt": termin.ansage()}

    await _freigabe_anfordern(
        params, f"Termin '{termin.titel}' {_termin_wann(termin)} absagen und aus dem Kalender löschen", loeschen
    )
