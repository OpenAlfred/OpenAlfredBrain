"""Google Kalender: Termine lesen, anlegen und rechtzeitig daran erinnern.

Einrichten (einmalig, am Rechner mit Browser; die Schritte im Detail stehen in der README):
  1. In der Google Cloud Console ein Projekt anlegen, die Google Calendar API aktivieren,
     unter "Google Auth Platform" die App als "Extern" anlegen und veröffentlichen, dann einen
     OAuth-Client vom Typ "Desktop-App" erstellen. Dessen JSON-Datei als
     config/google_client_secret.json ablegen.
  2. python -m alfred.kalender --einrichten   (öffnet den Browser, speichert config/google_token.json)
  3. Für den Mini-PC beide Dateien nach config/ kopieren; der Server braucht keinen Browser.

Die App muss veröffentlicht sein ("In Produktion"): Im Testmodus läuft der Zugang nach sieben
Tagen ab, und die Terminerinnerungen hören kommentarlos auf.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from alfred.persona import WOCHENTAGE, uhrzeit_gesprochen

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


@dataclass
class Termin:
    id: str
    titel: str
    beginn: datetime
    ende: datetime
    ganztags: bool = False

    def ansage(self) -> str:
        """Der Termin, wie der Sprecher ihn vorlesen soll.

        Kein %A: Python formatiert Wochentage ohne Locale englisch, und Alfred läse "Monday"
        vor. Die Uhrzeiten ausgeschrieben, weil das Modell aus "15:30" sonst "fünfzehneinhalb
        Uhr" machte.
        """
        tag = f"{WOCHENTAGE[self.beginn.weekday()]}, {self.beginn:%d.%m.}"
        if self.ganztags:
            return f"{tag}: {self.titel} (ganztägig)"
        return f"{tag}, {uhrzeit_gesprochen(self.beginn)} bis {uhrzeit_gesprochen(self.ende)}: {self.titel}"


class Kalender:
    """Dünne Hülle um die Google-Calendar-API. Alle Methoden sind blockierend."""

    def __init__(self, token: Path, kalender_id: str = "primary"):
        self._token = token
        self._kalender_id = kalender_id
        self._dienst = None

    def _service(self):
        if self._dienst is None:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(self._token), SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._token.write_text(creds.to_json(), encoding="utf-8")
            self._dienst = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._dienst

    def termine(self, von: datetime, bis: datetime) -> list[Termin]:
        antwort = (
            self._service()
            .events()
            .list(
                calendarId=self._kalender_id,
                timeMin=von.isoformat(),
                timeMax=bis.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )
        return [termin_aus_google(e, von.tzinfo) for e in antwort.get("items", [])]

    def anlegen(self, titel: str, beginn: datetime, ende: datetime) -> Termin:
        ereignis = (
            self._service()
            .events()
            .insert(
                calendarId=self._kalender_id,
                body={
                    "summary": titel,
                    "start": {"dateTime": beginn.isoformat()},
                    "end": {"dateTime": ende.isoformat()},
                },
            )
            .execute()
        )
        return termin_aus_google(ereignis, beginn.tzinfo)

    def suchen(self, titel: str, von: datetime, bis: datetime) -> list[Termin]:
        """Termine im Zeitraum, deren Titel den Text enthält; Groß- und Kleinschreibung egal."""
        gesucht = titel.strip().casefold()
        return [t for t in self.termine(von, bis) if gesucht and gesucht in t.titel.casefold()]

    def verschieben(self, termin: Termin, beginn: datetime, ende: datetime) -> Termin:
        """Setzt Beginn und Ende neu; ein ganztägiger Termin bleibt ganztägig."""
        if termin.ganztags:
            zeiten = {"start": {"date": f"{beginn:%Y-%m-%d}"}, "end": {"date": f"{ende:%Y-%m-%d}"}}
        else:
            zeiten = {"start": {"dateTime": beginn.isoformat()}, "end": {"dateTime": ende.isoformat()}}
        ereignis = (
            self._service()
            .events()
            .patch(calendarId=self._kalender_id, eventId=termin.id, body=zeiten)
            .execute()
        )
        return termin_aus_google(ereignis, beginn.tzinfo)

    def loeschen(self, termin: Termin) -> None:
        self._service().events().delete(calendarId=self._kalender_id, eventId=termin.id).execute()


def termin_aus_google(ereignis: dict, tz) -> Termin:
    """Ein Google-Ereignis als Termin, ganztägige Einträge eingeschlossen."""
    start, ende = ereignis.get("start", {}), ereignis.get("end", {})
    if "dateTime" in start:
        beginn = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
        schluss = datetime.fromisoformat(ende["dateTime"]).astimezone(tz)
        return Termin(ereignis["id"], ereignis.get("summary", "(ohne Titel)"), beginn, schluss)
    beginn = datetime.strptime(start["date"], "%Y-%m-%d").replace(tzinfo=tz)
    schluss = datetime.strptime(ende["date"], "%Y-%m-%d").replace(tzinfo=tz)
    return Termin(ereignis["id"], ereignis.get("summary", "(ohne Titel)"), beginn, schluss, ganztags=True)


class Terminerinnerer:
    """Legt für anstehende Termine Erinnerungen an, jeden Termin nur einmal."""

    def __init__(
        self,
        kalender: Kalender,
        erinnerungen,
        vorlauf: timedelta,
        merkdatei: Path,
        clock: Callable[[], datetime] | None = None,
    ):
        self._kalender = kalender
        self._erinnerungen = erinnerungen
        self._vorlauf = vorlauf
        self._merkdatei = merkdatei
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._erledigt: set[str] = set(
            json.loads(merkdatei.read_text(encoding="utf-8")) if merkdatei.exists() else []
        )

    def pruefen(self) -> list[Termin]:
        """Erinnerungen für Termine der nächsten zwei Stunden anlegen; gibt die neuen zurück."""
        jetzt = self._clock()
        neue = []
        for termin in self._kalender.termine(jetzt, jetzt + timedelta(hours=2)):
            if termin.id in self._erledigt or termin.ganztags:
                continue
            faellig = max(termin.beginn - self._vorlauf, jetzt)
            self._erinnerungen.anlegen(
                f"Termin um {termin.beginn:%H:%M} Uhr: {termin.titel}", faellig
            )
            self._erledigt.add(termin.id)
            neue.append(termin)
        if neue:
            self._merkdatei.parent.mkdir(parents=True, exist_ok=True)
            self._merkdatei.write_text(json.dumps(sorted(self._erledigt)), encoding="utf-8")
        return neue

    async def laufen(self, intervall: float = 300.0) -> None:
        while True:
            try:
                for termin in await asyncio.to_thread(self.pruefen):
                    logger.info(f"Erinnerung für Termin angelegt: {termin.ansage()}")
            except Exception as e:  # Kalender darf den Sprach-Loop nie mitreißen
                logger.warning(f"Kalender nicht erreichbar: {e}")
            await asyncio.sleep(intervall)


def einrichten(client_secret: Path, token: Path) -> None:
    """Holt den Zugang per Browser und speichert ihn. Läuft am Rechner mit Browser, nicht am Server."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret.exists():
        raise SystemExit(
            f"{client_secret} fehlt: die JSON-Datei des OAuth-Clients aus der Google Cloud Console. "
            "Die Schritte stehen in der README unter Kalender."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    # prompt=consent: Google gibt das Refresh-Token sonst nur bei der allerersten Zustimmung
    # heraus. Ohne Refresh-Token wäre der Zugang nach einer Stunde zu Ende.
    creds = flow.run_local_server(port=0, prompt="consent")
    if not creds.refresh_token:
        raise SystemExit("Google hat kein Refresh-Token geliefert; die Einrichtung noch einmal starten.")
    token.write_text(creds.to_json(), encoding="utf-8")
    print(f"Zugang gespeichert in {token}")


def zeige_termine(token: Path, tage: int = 7) -> None:
    """Prüfung nach der Einrichtung: die Termine der nächsten Tage, wie Alfred sie vorlesen würde."""
    kalender = Kalender(token)
    jetzt = datetime.now().astimezone()
    termine = kalender.termine(jetzt, jetzt + timedelta(days=tage))
    if not termine:
        print(f"Zugang funktioniert; keine Termine in den nächsten {tage} Tagen.")
    for termin in termine:
        print(termin.ansage())


def main() -> None:
    from alfred.config import CONFIG_DIR, load_settings

    parser = argparse.ArgumentParser(
        description="Google Kalender für Alfred. Ohne Option: Termine der nächsten Tage anzeigen."
    )
    parser.add_argument("--einrichten", action="store_true", help="Zugang per Browser freischalten")
    parser.add_argument("--tage", type=int, default=7, help="Zeitraum der Anzeige in Tagen")
    args = parser.parse_args()
    settings = load_settings()
    basis = CONFIG_DIR.parent
    token = basis / settings.kalender.token
    if args.einrichten:
        einrichten(basis / settings.kalender.client_secret, token)
        print("Probe:")
    elif not token.exists():
        raise SystemExit(f"{token} fehlt. Erst einrichten: python -m alfred.kalender --einrichten")
    zeige_termine(token, args.tage)


if __name__ == "__main__":
    main()
