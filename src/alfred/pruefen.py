"""Startprüfung: python -m alfred.pruefen

Prüft in einer halben Minute, ob Alfred laufen kann, bevor man es im Gespräch merkt: Schlüssel,
Modelle, Sprachausgabe mit Antwortzeit, Spracherkennung, Mikrofon und Lautsprecher je Raum
(und ob Windows den richtigen Eintrag nimmt), Wachwort-Modell, Stimmprofil, Codewort, Kalender,
WhatsApp-Brücke, Mail, Push, Suche, Spotify, Tailscale, Sicherung. Jede Zeile OK, Warnung oder
Fehler; Rückgabewert 1, sobald ein Fehler dabei ist. Gedacht für den Mini-PC nach der
Einrichtung und für jeden Start, bei dem etwas nicht stimmt.

    python -m alfred.pruefen            alles
    python -m alfred.pruefen --offline  nur, was ohne Netz geht
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

from alfred import sichern
from alfred.config import PERSONA_VORLAGE, VORLAGE_DATEI, Settings, konfig_pfad, load_settings, persona_pfad
from alfred.einrichtung import schluessel_ablauf
from alfred.persona import load_persona

ZEICHEN = {"ok": "✔", "warnung": "!", "fehler": "✘"}
LANGSAM_AB = 3.0


@dataclass
class Befund:
    name: str
    stufe: str  # ok | warnung | fehler
    text: str

    def zeile(self) -> str:
        return f"{ZEICHEN[self.stufe]} {self.name}: {self.text}"


def _versuch(name: str, pruefung: Callable[[], Befund]) -> Befund:
    """Eine Prüfung darf die anderen nicht mitreißen."""
    try:
        return pruefung()
    except Exception as e:  # noqa: BLE001
        return Befund(name, "fehler", f"{type(e).__name__}: {e}")


def pruefe_konfiguration(settings: Settings) -> list[Befund]:
    befunde = []
    if konfig_pfad().name == VORLAGE_DATEI:
        befunde.append(
            Befund("Einstellungen", "warnung", "keine eigene config/alfred.yaml, es gilt die Vorlage (Sicherung einspielen?)")
        )
    else:
        befunde.append(Befund("Einstellungen", "ok", str(konfig_pfad())))

    def persona() -> Befund:
        p = load_persona()
        text = f"{p.name}, Modi: {', '.join(p.modi)}"
        if persona_pfad().name == PERSONA_VORLAGE:
            return Befund("Persönlichkeit", "warnung", f"{text}; es gilt die Vorlage (config/persoenlichkeit.yaml anlegen)")
        return Befund("Persönlichkeit", "ok", text)

    befunde.append(_versuch("Persönlichkeit", persona))
    for name, ordner in (("Gedächtnis", settings.pfad(settings.gedaechtnis.ordner)), ("Logs", settings.pfad("logs"))):

        def schreibbar(ordner=ordner, name=name) -> Befund:
            ordner.mkdir(parents=True, exist_ok=True)
            probe = ordner / ".schreibprobe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return Befund(name, "ok", f"{ordner} beschreibbar")

        befunde.append(_versuch(name, schreibbar))
    return befunde


def pruefe_zeitzone(jetzt: datetime | None = None) -> Befund:
    """Uhrzeiten im Prompt und Erinnerungen folgen der Systemzeit; ein frischer Server steht oft auf UTC."""
    jetzt = jetzt or datetime.now().astimezone()
    versatz = jetzt.utcoffset() or timedelta(0)
    zone = jetzt.tzname() or "?"
    if zone in ("CET", "CEST") or versatz in (timedelta(hours=1), timedelta(hours=2)):
        return Befund("Zeitzone", "ok", f"{zone}, {jetzt:%H:%M} Uhr")
    return Befund(
        "Zeitzone", "warnung",
        f"{zone}, {jetzt:%H:%M} Uhr; Alfred nennt Uhrzeiten in dieser Zone (sudo timedatectl set-timezone Europe/Berlin)",
    )


def pruefe_geraete(settings: Settings) -> list[Befund]:
    from alfred.room import list_devices, waehle_geraet

    befunde = []
    if not settings.raeume:
        return [Befund("Räume", "warnung", "keiner in config/alfred.yaml; nur Browser-Modus")]
    try:
        geraete = list_devices()
    except Exception as e:  # noqa: BLE001
        return [Befund("Audiogeräte", "fehler", f"PortAudio: {e}")]
    windows = platform.system() == "Windows"
    for raum_name, raum in settings.raeume.items():
        for art, name, output in (("Mikrofon", raum.mikrofon, False), ("Lautsprecher", raum.lautsprecher, True)):
            titel = f"Raum {raum_name}, {art}"
            if not name:
                befunde.append(Befund(titel, "warnung", "kein Name eingetragen, Alfred nimmt das Standardgerät"))
                continue
            geraet = waehle_geraet(name, geraete, output=output)
            if geraet is None:
                befunde.append(Befund(titel, "fehler", f"kein Gerät mit '{name}' im Namen; python -m alfred.room --geraete"))
            elif windows and geraet.api != "MME":
                befunde.append(Befund(titel, "warnung", f"[{geraet.index}] {geraet.name} über {geraet.api}; unter Windows spielt nur MME zuverlässig"))
            else:
                befunde.append(Befund(titel, "ok", f"[{geraet.index}] {geraet.name} ({geraet.api})"))

    if settings.jabra.led_aktiv:
        from alfred.jabra_led import JabraDevice

        # Nur öffnen, nichts schreiben: Sonst ginge einem wachen Alfred die LED aus.
        jabra = JabraDevice(vendor_id=settings.jabra.vendor_id, product_id=settings.jabra.product_id)
        if jabra.connect():
            jabra.close()
            befunde.append(Befund("Jabra-LED", "ok", "HID-Gerät gefunden; grün heißt: Alfred ist wach"))
        else:
            befunde.append(Befund(
                "Jabra-LED", "warnung",
                "kein HID-Gerät gefunden oder kein Zugriff (Linux: deploy/99-jabra.rules); Alfred läuft ohne LED",
            ))

    return befunde


def pruefe_lokale_modelle(settings: Settings) -> list[Befund]:
    from alfred.sprechermodell import CACHE, MODELLE
    from alfred.stimme import Stimmprofil

    befunde = []
    cache = Path.home() / ".cache" / "vosk"
    modelle = [("Wachwort-Modell", settings.wachwort.modell)]
    if settings.stimmerkennung.aktiv:
        modelle.append(("Stimmen-Modell", settings.stimmerkennung.modell))
    for name, modell in modelle:
        if modell in MODELLE:
            pfad = CACHE / MODELLE[modell][0]
        elif modell.startswith("vosk-"):
            pfad = cache / modell
        else:
            befunde.append(Befund(name, "fehler", f"unbekanntes Modell {modell}; möglich: {', '.join(MODELLE)} oder vosk-model-spk-0.4"))
            continue
        if pfad.exists():
            befunde.append(Befund(name, "ok", f"{modell} liegt bereit"))
        else:
            befunde.append(Befund(name, "warnung", f"{modell} fehlt noch, lädt beim ersten Start (braucht Netz)"))
    if settings.stimmerkennung.aktiv:
        profil = Stimmprofil(settings.pfad(settings.stimmerkennung.profil), settings.stimmerkennung.modell)
        befunde.append(stimmprofil_befund(profil))
    else:
        befunde.append(Befund("Stimmerkennung", "ok", "aus; heikle Aktionen sichert das Codewort"))
    return befunde


def pruefe_codewort(settings: Settings) -> Befund:
    """Ohne Codewort gehen WhatsApp und Mail im Raum mit einem Ja raus, und jede Stimme beendet den Gästemodus."""
    from alfred.codewort import UMGEBUNG, untauglich

    wort = os.environ.get(UMGEBUNG, "").strip()
    if not wort:
        return Befund(
            "Codewort", "warnung",
            "nicht gesetzt: WhatsApp und Mail gehen im Raum mit einem Ja raus, und jede Stimme beendet den "
            "Gästemodus (python -m alfred.codewort)",
        )  # fmt: skip
    if fehler := untauglich(wort, settings.wachwort.phrasen):
        return Befund("Codewort", "warnung", f"wird nicht verwendet: {fehler} Neu festlegen: python -m alfred.codewort")
    return Befund("Codewort", "ok", "gesetzt; WhatsApp, Mail und das Ende des Gästemodus brauchen es im Raum statt eines Ja")


def stimmprofil_befund(profil) -> Befund:
    if profil.anderes_modell:
        return Befund(
            "Stimmprofil", "warnung",
            f"mit {profil.anderes_modell} angelernt, eingestellt ist ein anderes Modell; bis zum neuen Anlernen "
            "(python -m alfred.stimme --anlernen) zählt jedes Ja im Raum",
        )
    if not profil.vorhanden:
        return Befund("Stimmprofil", "warnung", "nicht angelernt (python -m alfred.stimme --anlernen); jedes Ja im Raum zählt")
    info = profil.info
    if "mikrofon" not in info:
        # Alte Profile entstanden mit dem Standardmikrofon des Systems, nicht mit dem des Raums.
        return Befund("Stimmprofil", "warnung", "ohne Angabe des Mikrofons angelernt; bei schlechter Erkennung neu: python -m alfred.stimme --anlernen")
    text = f"angelernt am {datetime.fromisoformat(info['angelernt']):%d.%m.} mit {info['mikrofon']}, {info.get('proben', '?')} Proben"
    if "modell" in info:
        text += f", Modell {info['modell']}"
    if "eigene_min" in info:
        text += f", eigene Sätze ab {info['eigene_min']:.2f}"
    return Befund("Stimmprofil", "ok", text)


async def pruefe_mistral(settings: Settings) -> list[Befund]:
    from mistralai.client import Mistral
    from mistralai.client.models import AudioFormat, RealtimeTranscriptionSessionCreated

    schluessel = os.getenv("MISTRAL_API_KEY")
    if not schluessel:
        return [Befund("Mistral-Schlüssel", "fehler", "MISTRAL_API_KEY fehlt in .env")]
    client = Mistral(api_key=schluessel)
    befunde = []
    try:
        antwort = await client.models.list_async()
        # Auch die Aliasse („voxtral-mini-latest"), sonst warnt die Prüfung bei jedem *-latest grundlos.
        modelle = {m.id for m in (antwort.data or [])} | {a for m in (antwort.data or []) for a in (getattr(m, "aliases", None) or [])}
        befunde.append(Befund("Mistral-Schlüssel", "ok", f"gültig, {len(modelle)} Modelle sichtbar"))
    except Exception as e:  # noqa: BLE001
        return [Befund("Mistral-Schlüssel", "fehler", f"{e}".splitlines()[0][:160])]
    for rolle, modell in (
        ("Sprecher", settings.modelle.sprecher),
        ("Denker", settings.modelle.denker),
        ("Werkzeuge", settings.modelle.werkzeuge),
        ("Transkription", settings.modelle.transkription),
        ("Sprachausgabe", settings.modelle.sprachausgabe),
        ("Sprachnachrichten", settings.modelle.sprachnachricht),
    ):
        if modell in modelle:
            befunde.append(Befund(f"Modell {rolle}", "ok", modell))
        else:
            befunde.append(Befund(f"Modell {rolle}", "warnung", f"{modell} steht nicht in der Modellliste (kann trotzdem gehen)"))

    start = time.monotonic()
    try:
        erster = None
        async with await client.audio.speech.complete_async(
            input="Hm.", model=settings.modelle.sprachausgabe, voice_id=settings.stimme, response_format="pcm", stream=True
        ) as ereignisse:
            async for _ in ereignisse:
                if erster is None:
                    erster = time.monotonic() - start
        dauer = erster if erster is not None else time.monotonic() - start
        stufe = "ok" if dauer < LANGSAM_AB else "warnung"
        befunde.append(Befund("Sprachausgabe", stufe, f"Stimme {settings.stimme[:8]}…, erster Ton nach {dauer:.1f} s"))
    except Exception as e:  # noqa: BLE001
        befunde.append(Befund("Sprachausgabe", "fehler", f"{e}".splitlines()[0][:160]))

    try:
        verbindung = await asyncio.wait_for(
            client.audio.realtime.connect(
                model=settings.modelle.transkription,
                audio_format=AudioFormat(encoding="pcm_s16le", sample_rate=16000),
            ),
            timeout=15,
        )
        try:
            async def erste_sitzung():
                async for ereignis in verbindung.events():
                    if isinstance(ereignis, RealtimeTranscriptionSessionCreated):
                        return True
                return False

            if await asyncio.wait_for(erste_sitzung(), timeout=15):
                befunde.append(Befund("Spracherkennung", "ok", f"{settings.modelle.transkription} verbunden"))
            else:
                befunde.append(Befund("Spracherkennung", "fehler", "Verbindung ohne Sitzung beendet"))
        finally:
            await verbindung.close()
    except Exception as e:  # noqa: BLE001
        befunde.append(Befund("Spracherkennung", "fehler", f"{e}".splitlines()[0][:160]))
    return befunde


async def pruefe_anbindungen(settings: Settings) -> list[Befund]:
    from alfred.kalender import Kalender
    from alfred.mail import Mail
    from alfred.musik import aus_umgebung
    from alfred.suche import Suchmaschine
    from alfred.whatsapp import WhatsApp

    befunde = []
    jetzt = datetime.now().astimezone()

    token = settings.pfad(settings.kalender.token)
    if token.exists():
        try:
            termine = await asyncio.to_thread(Kalender(token).termine, jetzt, jetzt + timedelta(days=1))
            befunde.append(Befund("Kalender", "ok", f"verbunden, {len(termine)} Termine bis morgen"))
        except Exception as e:  # noqa: BLE001
            befunde.append(Befund("Kalender", "fehler", f"{e}".splitlines()[0][:160]))
    else:
        befunde.append(Befund("Kalender", "warnung", "nicht angebunden (python -m alfred.kalender --einrichten)"))

    whatsapp = WhatsApp(settings.whatsapp.url, settings.pfad(settings.whatsapp.token))
    if whatsapp.eingerichtet:
        try:
            status = await whatsapp.status()
            if status.get("angemeldet"):
                befunde.append(Befund("WhatsApp", "ok", "Brücke läuft, angemeldet"))
            else:
                befunde.append(Befund("WhatsApp", "warnung", f"Brücke läuft, aber nicht angemeldet: {status}"))
        except Exception as e:  # noqa: BLE001
            befunde.append(Befund("WhatsApp", "warnung", f"Brücke nicht erreichbar ({e}); Nachrichten gehen nicht"))
    else:
        befunde.append(Befund("WhatsApp", "warnung", "nicht eingerichtet (Brücke koppeln, siehe README)"))

    if os.getenv("MAIL_ADRESSE") and os.getenv("MAIL_PASSWORT"):
        try:
            await asyncio.to_thread(Mail(os.environ["MAIL_ADRESSE"], os.environ["MAIL_PASSWORT"]).pruefen)
            befunde.append(Befund("Mail", "ok", f"Anmeldung für {os.environ['MAIL_ADRESSE']} klappt"))
        except Exception as e:  # noqa: BLE001
            befunde.append(Befund("Mail", "fehler", f"Anmeldung fehlgeschlagen: {e}"))
    else:
        befunde.append(Befund("Mail", "warnung", "aus (MAIL_ADRESSE und MAIL_PASSWORT fehlen in .env)"))

    push = settings.benachrichtigung
    if push.url and push.thema:
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                antwort = await http.get(push.url.rstrip("/") + "/v1/health")
            befunde.append(Befund("Push", "ok" if antwort.status_code < 500 else "warnung", f"ntfy unter {push.url} antwortet ({antwort.status_code})"))
        except Exception as e:  # noqa: BLE001
            befunde.append(Befund("Push", "warnung", f"ntfy unter {push.url} nicht erreichbar: {e}"))
        if not push.app_url:
            befunde.append(Befund("Freigabe-Knöpfe", "warnung", "benachrichtigung.app_url leer; Push ohne Ja/Nein-Knöpfe"))
    else:
        befunde.append(Befund("Push", "warnung", "aus (benachrichtigung.url und thema leer)"))

    if settings.suche.url:
        try:
            treffer = await Suchmaschine(settings.suche.url).suchen("Alfred", anzahl=1)
            befunde.append(Befund("Suche", "ok", f"SearXNG antwortet, {len(treffer)} Treffer"))
        except Exception as e:  # noqa: BLE001
            befunde.append(Befund("Suche", "fehler", f"SearXNG unter {settings.suche.url}: {e}"))
    else:
        befunde.append(
            Befund("Suche", "warnung", "Mistrals Websuche, rund drei Cent je Suche; mit SearXNG (suche.url) kostenlos")
        )

    musik = aus_umgebung(settings.pfad(settings.spotify.token), settings.spotify.geraet)
    if musik and musik.eingerichtet:
        befunde.append(Befund("Spotify", "ok", "eingerichtet"))
    else:
        befunde.append(Befund("Spotify", "warnung", "aus (Zugangsdaten in .env und python -m alfred.musik --einrichten)"))

    befunde.append(_versuch("Tailscale", pruefe_tailscale))
    return befunde


def pruefe_tailscale() -> Befund:
    if not shutil.which("tailscale"):
        return Befund("Tailscale", "warnung", "nicht installiert; nur nötig für die App vom Handy aus")
    lauf = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
    if lauf.returncode != 0:
        return Befund("Tailscale", "warnung", (lauf.stderr or lauf.stdout).strip().splitlines()[0][:120] if (lauf.stderr or lauf.stdout).strip() else "status fehlgeschlagen")
    daten = json.loads(lauf.stdout)
    zustand = daten.get("BackendState", "?")
    name = (daten.get("Self") or {}).get("DNSName", "").rstrip(".")
    if zustand == "Running":
        ablauf = schluessel_ablauf(lauf.stdout)
        if ablauf:
            return Befund("Tailscale", "warnung", f"verbunden als {name or 'unbekannt'}; der Schlüssel läuft am {ablauf} ab "
                          "(Tailscale-Verwaltung: Disable key expiry)")
        return Befund("Tailscale", "ok", f"verbunden als {name or 'unbekannt'}")
    return Befund("Tailscale", "warnung", f"Zustand {zustand}")


def pruefe_sicherung(settings: Settings) -> list[Befund]:
    letzte = sichern.letzte_sicherung(sichern.standardziel())
    if letzte is None:
        return [Befund("Sicherung", "warnung", f"noch keine unter {sichern.standardziel()} (python -m alfred.sichern)")]
    alter = datetime.now().astimezone() - letzte
    tage = alter.days
    text = f"zuletzt {letzte:%d.%m. %H:%M}" + (f", vor {tage} Tagen" if tage else "")
    return [Befund("Sicherung", "ok" if tage <= 2 else "warnung", text)]


async def alles(settings: Settings, *, netz: bool = True) -> list[Befund]:
    befunde = pruefe_konfiguration(settings)
    befunde.append(_versuch("Zeitzone", pruefe_zeitzone))
    befunde += _liste("Audiogeräte", lambda: pruefe_geraete(settings))
    befunde += _liste("Lokale Modelle", lambda: pruefe_lokale_modelle(settings))
    befunde.append(_versuch("Codewort", lambda: pruefe_codewort(settings)))
    if netz:
        befunde += await _liste_async("Mistral", lambda: pruefe_mistral(settings))
        befunde += await _liste_async("Anbindungen", lambda: pruefe_anbindungen(settings))
    befunde += _liste("Sicherung", lambda: pruefe_sicherung(settings))
    return befunde


def _liste(name: str, pruefung: Callable[[], list[Befund]]) -> list[Befund]:
    try:
        return pruefung()
    except Exception as e:  # noqa: BLE001
        return [Befund(name, "fehler", f"{type(e).__name__}: {e}")]


async def _liste_async(name: str, pruefung) -> list[Befund]:
    try:
        return await pruefung()
    except Exception as e:  # noqa: BLE001
        return [Befund(name, "fehler", f"{type(e).__name__}: {e}")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Prüft, ob Alfred laufen kann.")
    parser.add_argument("--offline", action="store_true", help="nur Prüfungen ohne Netz")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(override=True)
    settings = load_settings()
    befunde = asyncio.run(alles(settings, netz=not args.offline))
    for befund in befunde:
        print(befund.zeile())
    zaehler = {stufe: sum(1 for b in befunde if b.stufe == stufe) for stufe in ZEICHEN}
    print(f"\n{zaehler['ok']} in Ordnung, {zaehler['warnung']} Warnungen, {zaehler['fehler']} Fehler")
    raise SystemExit(1 if zaehler["fehler"] else 0)


if __name__ == "__main__":
    main()
