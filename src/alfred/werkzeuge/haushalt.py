"""Gruppe haushalt: Einkaufsliste, Vorrat, Wochenplan."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from pipecat.services.llm_service import FunctionCallParams

from alfred.werkzeuge.basis import _jetzt, _res
from alfred.werkzeuge.registrierung import werkzeug


def _einkauf(res) -> object:
    return res.einkauf


def _vorrat(res) -> object:
    return res.vorrat


def _wochenplan(res) -> object:
    return res.wochenplan and res.einkauf


@werkzeug("haushalt", "schreibend", braucht=_einkauf, rueckfrage="Auf die Einkaufsliste setzen: {artikel}")
async def einkauf_hinzufuegen(params: FunctionCallParams, artikel: str):
    """Setzt Artikel auf die Einkaufsliste; mehrere mit Komma oder "und", Mengen davor ("2 Brot, 500 g Hack").

    Args:
        artikel: Die Artikel, so wie genannt.
    """
    neue = _res(params).einkauf.hinzufuegen(artikel)
    await params.result_callback({"hinzugefuegt": [a.ansage() for a in neue], "offen": len(_res(params).einkauf.offene())})


@werkzeug("haushalt", "lesend", braucht=_einkauf)
async def einkaufsliste_anzeigen(params: FunctionCallParams):
    """Liest die offene Einkaufsliste vor, nach Abteilung geordnet."""
    gruppen = _res(params).einkauf.nach_abteilung()
    await params.result_callback(
        {"liste": {abt: [a.ansage() for a in artikel] for abt, artikel in gruppen.items()} or "leer"}
    )


@werkzeug(
    "haushalt",
    "schreibend",
    braucht=_einkauf,
    rueckfrage=lambda artikel, entfernen: f"{artikel} von der Einkaufsliste {'entfernen' if entfernen else 'abhaken'}",
)
async def einkauf_abhaken(params: FunctionCallParams, artikel: str, entfernen: bool = False):
    """Hakt einen Artikel ab ("Milch haben wir") oder nimmt ihn ganz von der Liste.

    Args:
        artikel: Der Artikel, wie er auf der Liste steht.
        entfernen: True, wenn er von der Liste verschwinden soll statt nur abgehakt zu werden.
    """
    einkauf = _res(params).einkauf
    getroffen = einkauf.entfernen(artikel) if entfernen else einkauf.abhaken(artikel)
    await params.result_callback({"artikel": getroffen.name if getroffen else None, "hinweis": None if getroffen else "Nicht eindeutig gefunden; frag nach."})


@werkzeug(
    "haushalt",
    "schreibend",
    braucht=_einkauf,
    rueckfrage=lambda alles: "die ganze Einkaufsliste leeren" if alles else "abgehakte Artikel von der Einkaufsliste entfernen",
)
async def einkaufsliste_leeren(params: FunctionCallParams, alles: bool = False):
    """Räumt abgehakte Artikel von der Liste; mit alles=True die ganze Liste.

    Args:
        alles: Auch offene Artikel löschen.
    """
    anzahl = _res(params).einkauf.leeren(nur_erledigte=not alles)
    await params.result_callback({"entfernt": anzahl})


@werkzeug(
    "haushalt",
    "schreibend",
    braucht=_vorrat,
    rueckfrage=lambda artikel, menge, ablauf: "In den Vorrat aufnehmen: "
    + f"{menge} {artikel}".strip()
    + (f", haltbar bis {ablauf}" if ablauf else ""),
)
async def vorrat_merken(params: FunctionCallParams, artikel: str, menge: str = "", ablauf: str = ""):
    """Merkt sich, was zu Hause da ist, mit Haltbarkeit; vor dem Ablauf erinnert Alfred von selbst.

    Args:
        artikel: Was, zum Beispiel Milch.
        menge: Optional, zum Beispiel "2 Liter".
        ablauf: Optional das Ablaufdatum als JJJJ-MM-TT.
    """
    res = _res(params)
    try:
        eintrag = res.vorrat.merken(artikel, menge, ablauf)
    except ValueError as e:
        await params.result_callback({"fehler": f"Datum unverständlich: {e}"})
        return
    if eintrag.ablauf:
        wann = datetime.fromisoformat(eintrag.ablauf).replace(hour=18, tzinfo=_jetzt().tzinfo) - timedelta(days=1)
        if wann > _jetzt():
            res.erinnerungen.anlegen(f"{eintrag.name} läuft morgen ab", wann)
    await params.result_callback({"gemerkt": asdict(eintrag)})


@werkzeug("haushalt", "lesend", braucht=_vorrat)
async def vorrat_anzeigen(params: FunctionCallParams):
    """Listet den Vorrat, bald ablaufendes zuerst."""
    vorrat = _res(params).vorrat.alle()
    await params.result_callback({"vorrat": [f"{v.menge} {v.name}".strip() + (f" (bis {v.ablauf})" if v.ablauf else "") for v in vorrat] or ["leer"]})


@werkzeug("haushalt", "schreibend", braucht=_vorrat, rueckfrage="{artikel} aus dem Vorrat nehmen")
async def vorrat_verbraucht(params: FunctionCallParams, artikel: str):
    """Nimmt etwas aus dem Vorrat ("die Milch ist alle").

    Args:
        artikel: Der Artikel.
    """
    eintrag = _res(params).vorrat.verbraucht(artikel)
    await params.result_callback({"entfernt": eintrag.name if eintrag else None})


@werkzeug(
    "haushalt",
    "schreibend",
    braucht=_wochenplan,
    rueckfrage=lambda wuensche, tage: f"Wochenplan für {tage} Abende erstellen" + (f" ({wuensche})" if wuensche else ""),
)
async def wochenplan_erstellen(params: FunctionCallParams, wuensche: str = "", tage: int = 5):
    """Plant Abendessen für mehrere Tage mit Rezepten; danach lassen sich die Zutaten auf die Einkaufsliste setzen.

    Args:
        wuensche: Zum Beispiel "eiweißreich, unter 30 Minuten, einmal vegetarisch".
        tage: Wie viele Abende, Standard fünf.
    """
    res = _res(params)
    try:
        plan = await res.wochenplan.erstellen(wuensche, max(1, min(tage, 7)))
    except Exception as e:
        await params.result_callback({"fehler": f"Plan fehlgeschlagen: {e}"})
        return
    await params.result_callback(
        {
            "plan": [f"{t.get('tag')}: {t.get('gericht')} ({t.get('dauer_minuten', '?')} Minuten)" for t in plan],
            "hinweis": "Nenne die Gerichte knapp; Details stehen in der App. Frag, ob die Zutaten auf die Einkaufsliste sollen (wochenplan_einkaufen).",
        }
    )


@werkzeug("haushalt", "schreibend", braucht=_wochenplan, rueckfrage="Zutaten des Wochenplans auf die Einkaufsliste setzen")
async def wochenplan_einkaufen(params: FunctionCallParams):
    """Setzt die Zutaten des Wochenplans auf die Einkaufsliste, ohne das, was im Vorrat ist."""
    res = _res(params)
    zutaten = res.wochenplan.zutaten(res.vorrat)
    neue = res.einkauf.hinzufuegen(", ".join(zutaten)) if zutaten else []
    await params.result_callback({"hinzugefuegt": len(neue), "offen": len(res.einkauf.offene())})
