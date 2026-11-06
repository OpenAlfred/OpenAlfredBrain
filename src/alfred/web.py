"""Alfreds App: eine PWA vom Mini-PC, erreichbar über Tailscale, auf dem iPhone als Icon.

Dieselben Daten wie die Stimme (Einkaufsliste, Aufgaben, Erinnerungen, Routinen, Kalender,
Gedächtnis), dazu Schreiben mit Alfred. Dieser Prozess liefert keine Erinnerungen aus und
startet keine Routinen; das tut der Sprach-Prozess, sonst käme alles doppelt.

Start:  python -m alfred.web            (http://127.0.0.1:7870)
Auf dem Mini-PC als Dienst alfred-web, mit HTTPS über: sudo tailscale serve --bg --set-path /app 7870
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from alfred import logbuch, mitschrift
from alfred.auftraege import bestaetigung, kurztitel
from alfred.config import load_settings
from alfred.ernaehrung import anteilig, bilanz
from alfred.geburtstage import geburtstag_ansage
from alfred.haushalt import foto_auswerten
from alfred.persona import datum_gesprochen, load_persona
from alfred.pipeline import build_ressourcen, require_api_key, system_prompt
from alfred.sprachnachricht import MAX_BYTES, MAX_SEKUNDEN, MIN_SEKUNDEN, abhoeren, wav_dauer
from alfred.textgespraech import Textgespraech, ansicht_verlauf, schritt_ansicht
from alfred.werkzeuge.ernaehrung import _ziele
from alfred.werkzeuge.uebersicht import uebersicht

STATIC = Path(__file__).parent / "web_static"
# Der Browser fragt bei jeder Datei der App nach (ETag, 304 bei Unverändertem). Ohne diese Kopfzeile hält er
# Skripte nach Zeitstempel-Faustregel noch Stunden für frisch, und nach einem Update liefen neue Seite und
# altes JavaScript gemischt.
KEIN_CACHE = {"Cache-Control": "no-cache"}


class AktuelleDateien(StaticFiles):
    def file_response(self, *args, **kwargs):
        antwort = super().file_response(*args, **kwargs)
        antwort.headers.update(KEIN_CACHE)
        return antwort


async def _koerper_lesen(request: Request, grenze: int) -> bytes:
    """Der Körper einer Anfrage mit höchstens grenze Bytes; darüber 413, ohne den Rest zu lesen."""
    angabe = request.headers.get("content-length", "")
    if angabe.isdigit() and int(angabe) > grenze:
        raise HTTPException(413, "Die Aufnahme ist zu groß.")
    teile, summe = [], 0
    async for teil in request.stream():
        summe += len(teil)
        if summe > grenze:
            raise HTTPException(413, "Die Aufnahme ist zu groß.")
        teile.append(teil)
    return b"".join(teile)


class Text(BaseModel):
    text: str
    faellig: str = ""


class Haken(BaseModel):
    name: str = ""
    nummer: int = 0
    erledigt: bool = True


class Auftragseingabe(BaseModel):
    text: str
    aufs_handy: bool = True


class Mahlzeiteingabe(BaseModel):
    text: str = ""
    barcode: str = ""
    gramm: float = 0.0


class Gewichtseingabe(BaseModel):
    kilogramm: float


class Ausgabeneingabe(BaseModel):
    betrag: float
    kategorie: str = "Sonstiges"
    text: str = ""


class Vorratseingabe(BaseModel):
    name: str
    menge: str = ""
    ablauf: str = ""


class Planeingabe(BaseModel):
    wuensche: str = ""
    tage: int = 5


class Profileingabe(BaseModel):
    feld: str
    wert: str


class Fotoeingabe(BaseModel):
    art: str  # naehrwerte | kassenbon
    bild: str  # Base64
    mime: str = "image/jpeg"
    gramm: float = 0.0
    eintragen: bool = True


def create_app(settings=None, persona=None, logordner: Path | None = None) -> FastAPI:
    settings = settings or load_settings()
    persona = persona or load_persona()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.res = build_ressourcen(settings, persona)
        app.state.gespraech = Textgespraech(app.state.res, lambda: system_prompt(app.state.res))
        app.state.logordner = logordner or settings.pfad("logs")
        logger.info("Alfreds App bereit")
        yield

    app = FastAPI(title="Alfred", lifespan=lifespan)
    app.mount("/static", AktuelleDateien(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers=KEIN_CACHE)

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json", headers=KEIN_CACHE)

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers=KEIN_CACHE)

    @app.get("/api/heute")
    async def heute():
        res = app.state.res
        jetzt = datetime.now().astimezone()
        termine, kalender, wetter = [], [], None
        if res.kalender:
            try:
                eintraege = await asyncio.to_thread(res.kalender.termine, jetzt, jetzt + timedelta(days=1))
                termine = [t.ansage() for t in eintraege]
                # Dieselben Termine für die Zeitleiste der App: Uhrzeiten als Zahlen, nicht ausgeschrieben.
                kalender = [
                    {
                        "titel": t.titel,
                        "beginn": t.beginn.isoformat(timespec="minutes"),
                        "ende": t.ende.isoformat(timespec="minutes"),
                        "ganztags": t.ganztags,
                    }
                    for t in eintraege
                ]
            except Exception as e:  # noqa: BLE001
                termine = [f"Kalender nicht erreichbar: {e}"]
        if res.wetter:
            try:
                wetter = await res.wetter.vorhersage()
            except Exception:  # noqa: BLE001 - ohne Wohnort oder ohne Netz einfach kein Wetter
                wetter = None
        heute = jetzt.date()
        return {
            "datum": datum_gesprochen(jetzt),
            "termine": termine,
            "kalender": kalender,
            "wetter": wetter,
            "aufgaben": [{"nummer": a.nummer, "text": a.text, "faellig": a.faellig} for a in res.aufgaben.offene()],
            "erinnerungen": [
                {
                    "nummer": e.nummer,
                    "faellig": e.faellig,
                    "text": f"Timer: {e.text}" if e.timer else e.text,
                    "timer": e.timer,
                    "wiederholung": e.wiederholung(),
                }
                for e in res.erinnerungen.offene()
            ],
            "geburtstage": [geburtstag_ansage(g, wann, heute) for g, wann in res.kontakte.anstehende_geburtstage(heute, 14)]
            if res.kontakte
            else [],
            "routinen": [{"name": r.name, "zeitplan": r.zeitplan()} for r in res.routinen.alle()],
            "einkauf_offen": len(res.einkauf.offene()),
        }

    @app.get("/api/einkauf")
    async def einkauf():
        res = app.state.res
        return {
            "abteilungen": {abt: [{"name": a.name, "menge": a.menge} for a in artikel] for abt, artikel in res.einkauf.nach_abteilung().items()},
            "erledigte": [{"name": a.name, "menge": a.menge} for a in res.einkauf.alle() if a.erledigt],
        }

    @app.post("/api/einkauf")
    async def einkauf_hinzufuegen(eingabe: Text):
        neue = app.state.res.einkauf.hinzufuegen(eingabe.text)
        return {"hinzugefuegt": [a.ansage() for a in neue]}

    @app.patch("/api/einkauf")
    async def einkauf_abhaken(haken: Haken):
        artikel = app.state.res.einkauf.abhaken(haken.name, haken.erledigt)
        if not artikel:
            raise HTTPException(404, "Artikel nicht gefunden")
        return {"name": artikel.name, "erledigt": artikel.erledigt}

    @app.delete("/api/einkauf")
    async def einkauf_leeren(alles: bool = False):
        return {"entfernt": app.state.res.einkauf.leeren(nur_erledigte=not alles)}

    @app.get("/api/aufgaben")
    async def aufgaben():
        return {"offene": [{"nummer": a.nummer, "text": a.text, "faellig": a.faellig} for a in app.state.res.aufgaben.offene()]}

    @app.post("/api/aufgaben")
    async def aufgabe_anlegen(eingabe: Text):
        try:
            aufgabe = app.state.res.aufgaben.anlegen(eingabe.text, eingabe.faellig)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"nummer": aufgabe.nummer, "text": aufgabe.text}

    @app.patch("/api/aufgaben")
    async def aufgabe_erledigt(haken: Haken):
        aufgabe = app.state.res.aufgaben.erledigen(haken.nummer)
        if not aufgabe:
            raise HTTPException(404, "Aufgabe nicht gefunden")
        return {"erledigt": aufgabe.text}

    @app.get("/api/auftraege")
    async def auftraege():
        res = app.state.res
        felder = ("nummer", "titel", "auftrag", "status", "angelegt", "begonnen", "beendet", "kurzfassung", "fehler")
        return {
            "auftraege": [
                {**{f: getattr(a, f) for f in felder}, "bericht": bool(a.bericht)} for a in reversed(res.auftraege.alle())
            ],
            # Abgearbeitet wird im Sprach-Prozess; läuft der nicht, warten die Aufträge.
            "sprachprozess": mitschrift.lesen(res.gedaechtnis.ordner / "mitschrift.json")["erreichbar"],
        }

    @app.post("/api/auftraege")
    async def auftrag_anlegen(eingabe: Auftragseingabe):
        res = app.state.res
        text = eingabe.text.strip()
        if not text:
            raise HTTPException(400, "Was soll Alfred ausarbeiten?")
        vorher = len(res.auftraege.offene())
        auftrag = res.auftraege.anlegen(kurztitel(text), text, aufs_handy=eingabe.aufs_handy)
        push = (True if res.benachrichtiger else None) if eingabe.aufs_handy else False
        return {"nummer": auftrag.nummer, "bestaetigung": bestaetigung([auftrag], vorher, push)}

    @app.delete("/api/auftraege")
    async def auftrag_abbrechen(nummer: int):
        auftrag = app.state.res.auftraege.abbrechen(nummer)
        if not auftrag:
            raise HTTPException(404, "Kein offener Auftrag mit dieser Nummer")
        return {"abgebrochen": auftrag.titel}

    @app.get("/api/auftraege/{nummer}/bericht")
    async def bericht(nummer: int):
        auftraege = app.state.res.auftraege
        auftrag = auftraege.holen(nummer)
        text = auftraege.bericht_lesen(auftrag) if auftrag else None
        if text is None:
            raise HTTPException(404, "Zu diesem Auftrag gibt es keinen Bericht")
        return {"titel": auftrag.titel, "text": text}

    @app.get("/api/ernaehrung")
    async def ernaehrung():
        res = app.state.res
        mahlzeiten = res.ernaehrung.tag()
        return {
            "heute": [asdict(m) for m in mahlzeiten],
            "gesamt": bilanz(mahlzeiten),
            "ziele": _ziele(res),
            "woche": res.ernaehrung.woche(),
            "gewichte": res.ernaehrung.gewichte(),
        }

    @app.post("/api/ernaehrung")
    async def essen(eingabe: Mahlzeiteingabe):
        res = app.state.res
        if eingabe.barcode:
            produkt = await res.schaetzer.produkt(eingabe.barcode)
            gramm = eingabe.gramm or 100.0
            werte = anteilig(produkt["je_100g"], gramm)
            text = f"{round(gramm)} g {produkt['name']}"
            eintrag = res.ernaehrung.eintragen(text, werte, "produkt", "aus Open Food Facts")
        else:
            if not eingabe.text.strip():
                raise HTTPException(400, "leer")
            werte = await res.schaetzer.schaetzen(eingabe.text)
            eintrag = res.ernaehrung.eintragen(eingabe.text, werte, "schaetzung", werte.get("annahme", ""))
        return {"eintrag": asdict(eintrag), "gesamt": bilanz(res.ernaehrung.tag())}

    @app.get("/api/ernaehrung/produkt")
    async def produkt(barcode: str):
        try:
            return await app.state.res.schaetzer.produkt(barcode)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from e

    @app.delete("/api/ernaehrung")
    async def essen_loeschen(nummer: int):
        eintrag = app.state.res.ernaehrung.loeschen(nummer)
        if not eintrag:
            raise HTTPException(404, "Eintrag nicht gefunden")
        return {"geloescht": eintrag.text}

    @app.post("/api/ernaehrung/gewicht")
    async def gewicht(eingabe: Gewichtseingabe):
        try:
            return {"gewichte": app.state.res.ernaehrung.gewicht_merken(eingabe.kilogramm)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/haushalt")
    async def haushalt():
        res = app.state.res
        heute = datetime.now().astimezone().date()
        return {
            "ausgaben": res.ausgaben.uebersicht(),
            "ausgaben_liste": [asdict(a) for a in res.ausgaben.monat()][-30:][::-1],
            "vorrat": [asdict(v) for v in res.vorrat.alle()],
            "ablaufend": [v.name for v in res.vorrat.ablaufende(3, heute)],
            "wochenplan": res.wochenplan.lesen(),
        }

    @app.post("/api/ausgaben")
    async def ausgabe(eingabe: Ausgabeneingabe):
        try:
            a = app.state.res.ausgaben.merken(eingabe.betrag, eingabe.kategorie, eingabe.text)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return asdict(a)

    @app.post("/api/vorrat")
    async def vorrat(eingabe: Vorratseingabe):
        try:
            return asdict(app.state.res.vorrat.merken(eingabe.name, eingabe.menge, eingabe.ablauf))
        except ValueError as e:
            raise HTTPException(400, f"Datum unverständlich: {e}") from e

    @app.delete("/api/vorrat")
    async def vorrat_weg(name: str):
        eintrag = app.state.res.vorrat.verbraucht(name)
        if not eintrag:
            raise HTTPException(404, "nicht eindeutig gefunden")
        return {"entfernt": eintrag.name}

    @app.post("/api/wochenplan")
    async def wochenplan(eingabe: Planeingabe):
        try:
            plan = await app.state.res.wochenplan.erstellen(eingabe.wuensche, max(1, min(eingabe.tage, 7)))
        except (ValueError, TypeError, AttributeError) as e:  # keine brauchbare JSON-Antwort des Modells (JSONDecodeError ist ein ValueError)
            raise HTTPException(502, "Alfred konnte keinen Plan erstellen: Die Antwort des Modells war unbrauchbar. Versuch es noch einmal.") from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Alfred konnte keinen Plan erstellen: {e}") from e
        return {"plan": plan}

    @app.post("/api/wochenplan/einkaufen")
    async def wochenplan_einkaufen():
        res = app.state.res
        zutaten = res.wochenplan.zutaten(res.vorrat)
        neue = res.einkauf.hinzufuegen(", ".join(zutaten)) if zutaten else []
        return {"hinzugefuegt": [a.ansage() for a in neue]}

    @app.post("/api/foto")
    async def foto(eingabe: Fotoeingabe):
        res = app.state.res
        try:
            daten = await foto_auswerten(res.client, res.settings.modelle.werkzeuge, eingabe.art, eingabe.bild, eingabe.mime)
        except json.JSONDecodeError as e:  # ein Unterfall von ValueError: Die Antwort des Modells war kein JSON, die Eingabe ist in Ordnung
            raise HTTPException(502, "Das Foto ließ sich nicht lesen: Die Antwort des Modells war unbrauchbar. Versuch es mit einem schärferen Foto.") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Foto nicht lesbar: {e}") from e
        if not isinstance(daten, dict):
            raise HTTPException(502, "Das Foto ließ sich nicht lesen: Die Antwort des Modells war unbrauchbar.")
        try:
            if eingabe.art == "naehrwerte" and eingabe.eintragen:
                gramm = eingabe.gramm or float(daten.get("portion_g") or 100)
                werte = anteilig(daten.get("je_100g", {}), gramm)
                eintrag = res.ernaehrung.eintragen(f"{round(gramm)} g {daten.get('name') or 'Produkt vom Foto'}", werte, "foto", "aus Nährwerttabelle")
                daten["eintrag"] = asdict(eintrag)
            if eingabe.art == "kassenbon" and eingabe.eintragen and daten.get("summe"):
                a = res.ausgaben.merken(float(daten["summe"]), str(daten.get("kategorie", "Lebensmittel")), f"{daten.get('laden', 'Kassenbon')}")
                daten["eintrag"] = asdict(a)
        except (ValueError, TypeError, AttributeError, KeyError) as e:  # Werte wie „ca. 30 g“ oder Text statt Zahlen im JSON des Modells
            raise HTTPException(502, f"Die Werte auf dem Foto ließen sich nicht übernehmen ({e}). Lies sie ab und trag sie von Hand ein.") from e
        return daten

    @app.get("/api/freigabe/{kennung}/{entscheidung}", response_class=HTMLResponse)
    async def freigabe(kennung: str, entscheidung: str, g: str = ""):
        res = app.state.res
        daten = res.freigabedatei.entscheiden(kennung, g, entscheidung) if res.freigabedatei else None
        if not daten:
            raise HTTPException(404, "Keine offene Freigabe mit dieser Kennung; vielleicht schon entschieden oder abgelaufen.")
        # Die Beschreibung stammt aus den Argumenten des Werkzeugs (vom Modell, mitunter aus fremdem Text): nie als HTML ausgeben.
        return f"<meta charset='utf-8'><p>{'Freigegeben' if entscheidung == 'ja' else 'Abgelehnt'}: {escape(str(daten['beschreibung']))}</p>"

    @app.get("/api/gedaechtnis")
    async def gedaechtnis():
        g = app.state.res.gedaechtnis
        wissen = {p.stem: p.read_text(encoding="utf-8") for p in sorted(g.wissen.glob("*.md"))}
        protokolle = {p.stem: p.read_text(encoding="utf-8") for p in sorted(g.protokoll.glob("*.md"))[-7:]}
        feedback = (g.ordner / "feedback.md").read_text(encoding="utf-8") if (g.ordner / "feedback.md").exists() else ""
        return {"kontext": g.kontext(), "profil": g.profil(), "regeln": g.regeln(), "wissen": wissen, "protokolle": protokolle, "feedback": feedback}

    @app.put("/api/gedaechtnis/kontext")
    async def kontext_schreiben(eingabe: Text):
        app.state.res.gedaechtnis.kontext_datei.write_text(eingabe.text, encoding="utf-8")
        return {"gespeichert": True}

    @app.patch("/api/gedaechtnis/profil")
    async def profil_setzen(eingabe: Profileingabe):
        try:
            return {"profil": app.state.res.gedaechtnis.profil_merken(eingabe.feld, eingabe.wert)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/gedaechtnis/regel")
    async def regel_loeschen(nummer: int):
        entfernt = app.state.res.gedaechtnis.regel_loeschen(nummer)
        if not entfernt:
            raise HTTPException(404, "Regel nicht gefunden")
        return {"geloescht": entfernt}

    @app.get("/api/status")
    async def status():
        res = app.state.res
        whatsapp = None
        if res.whatsapp:
            try:
                whatsapp = await res.whatsapp.status()
            except RuntimeError as e:
                whatsapp = {"fehler": str(e)}
        aktionen = res.gedaechtnis.ordner / "aktionen.log"
        letzte = aktionen.read_text(encoding="utf-8").splitlines()[-10:][::-1] if aktionen.exists() else []
        return {
            "dienste": {
                "kalender": bool(res.kalender), "whatsapp": whatsapp, "mail": bool(res.mail), "push": bool(res.benachrichtiger),
                "suche": bool(res.suche), "musik": bool(res.musik), "stimmprofil": res.stimmpruefer is not None,
            },
            "kosten": res.kosten.bericht() if res.kosten else None,
            "aktionen": letzte,
            # Warnungen und Fehler aller Prozesse (Raum, Browser, App), neueste zuerst.
            "fehler": logbuch.letzte_fehler(app.state.logordner, 20),
        }

    @app.get("/api/zaehler")
    async def zaehler():
        """Offenes in Zahlen für die Marken der Navigation; liest nur Dateien, ruft keinen Dienst auf."""
        res = app.state.res
        offene = res.aufgaben.offene()
        heute = datetime.now().astimezone().date().isoformat()
        return {
            "einkauf": len(res.einkauf.offene()),
            "aufgaben": len(offene),
            "ueberfaellig": sum(1 for a in offene if a.faellig and a.faellig < heute),
            "auftraege": len(res.auftraege.offene()),
            "timer": len(res.erinnerungen.timer()),
        }

    @app.get("/api/faehigkeiten")
    async def faehigkeiten():
        """Was Alfred kann: Werkzeuge nach Gruppen mit Stufe und Anbindung, dazu Modi und Modelle."""
        return uebersicht(app.state.res)

    @app.get("/api/mitschrift")
    async def mitschrift_stand():
        """Was der Sprach-Prozess gerade tut; er schreibt die Datei, die App liest sie nur."""
        return mitschrift.lesen(app.state.res.gedaechtnis.ordner / "mitschrift.json")

    @app.get("/api/chat")
    async def chat_verlauf():
        gespraech = app.state.gespraech
        return {"verlauf": ansicht_verlauf(gespraech.verlauf, gespraech.sprachnachrichten)}

    @app.post("/api/chat")
    async def chat(eingabe: Text):
        if not eingabe.text.strip():
            raise HTTPException(400, "leer")
        antworten = await app.state.gespraech.antworten(eingabe.text)
        return {"antworten": antworten, "schritte": [schritt_ansicht(**s) for s in app.state.gespraech.schritte]}

    @app.post("/api/sprachnachricht")
    async def sprachnachricht(request: Request):
        """Eine Sprachnachricht aus der App (WAV im Körper): abhören, dann beantworten wie getippten Text.

        Das Audio wird nicht gespeichert. Scheitert die Antwort nach dem Abhören, steht die Abschrift im
        Fehler (detail.transkript), damit die App sie ins Eingabefeld legen kann, statt sie zu verlieren.
        """
        res = app.state.res
        audio = await _koerper_lesen(request, MAX_BYTES)
        try:
            sekunden = wav_dauer(audio)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if sekunden < MIN_SEKUNDEN:
            raise HTTPException(400, "Die Aufnahme ist zu kurz.")
        if sekunden > MAX_SEKUNDEN:
            raise HTTPException(413, f"Höchstens {MAX_SEKUNDEN // 60} Minuten am Stück.")
        try:
            text = await abhoeren(res.client, res.settings.modelle.sprachnachricht, audio)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Sprachnachricht nicht abgehört: {e}")
            raise HTTPException(502, f"Die Aufnahme ließ sich nicht abhören: {e}") from e
        if res.kosten:  # Mistral rechnet die Minuten ab, auch wenn nichts zu verstehen war
            res.kosten.transkription(sekunden)
        if not text:
            raise HTTPException(422, "Ich habe nichts verstanden. Sprich etwas lauter oder näher am Mikrofon.")
        try:
            antworten = await app.state.gespraech.antworten(text, gesprochen=True)
        except Exception as e:  # noqa: BLE001
            logger.exception("Antwort auf eine Sprachnachricht gescheitert")
            raise HTTPException(502, {"text": f"Alfred konnte nicht antworten: {e}", "transkript": text}) from e
        return {"transkript": text, "antworten": antworten, "schritte": [schritt_ansicht(**s) for s in app.state.gespraech.schritte]}

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Alfreds App")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7870)
    parser.add_argument("--gedaechtnis", help="anderer Gedächtnisordner, etwa zum Ausprobieren")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(override=True)
    require_api_key()
    settings = load_settings()
    if args.gedaechtnis:
        settings.gedaechtnis.ordner = args.gedaechtnis
    logbuch.einrichten("app")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
