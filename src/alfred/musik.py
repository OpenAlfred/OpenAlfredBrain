"""Spotify: abspielen, pausieren, weiter, lauter, was läuft.

Gesteuert wird über die Web-API (spotipy); gespielt wird auf einem Spotify-Connect-Gerät, auf
dem Mini-PC etwa spotifyd am Konferenzlautsprecher, sonst das Handy oder der Rechner, auf dem
Spotify offen ist. Braucht Spotify Premium und eine eigene App bei developer.spotify.com
(Client-ID und Secret in .env als SPOTIFY_CLIENT_ID und SPOTIFY_CLIENT_SECRET, Redirect-URI
http://127.0.0.1:8890/callback).

Einrichten (einmalig, mit Browser):  python -m alfred.musik --einrichten
Geräte sehen:                        python -m alfred.musik --geraete
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"
REDIRECT_URI = "http://127.0.0.1:8890/callback"
ARTEN = {"titel": "track", "lied": "track", "song": "track", "album": "album", "playlist": "playlist", "künstler": "artist", "interpret": "artist"}


GERAET_WARTEN_SECS = 10  # so lange darf eine frisch geöffnete Spotify-App brauchen, bis sie als Gerät erscheint


def app_starter() -> Callable[[], bool] | None:
    """Unter Windows: die installierte Spotify-App öffnen, damit sie als Gerät erscheint.

    Am 6.10.2026 scheiterte "Spiel ein Lied" an "Kein Spotify-Gerät verfügbar", weil die App
    nicht offen war. Unter Linux spielt spotifyd als Dienst; dort gibt es nichts zu öffnen.
    """
    if os.name != "nt":
        return None

    def starten() -> bool:
        try:
            os.startfile("spotify:")  # das URL-Schema der App; ohne installierte App OSError
        except OSError:
            return False
        return True

    return starten


class Musik:
    """Dünne Hülle um spotipy; alle Methoden blockieren und laufen in Werkzeugen im Thread."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_pfad: Path,
        geraet: str = "",
        starter: Callable[[], bool] | None = None,
        warten: Callable[[float], None] = time.sleep,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_pfad = token_pfad
        self._geraet = geraet.strip().casefold()
        self._sp = None
        self._starter = starter
        self._warten = warten

    def _spotify(self, open_browser: bool = False):
        if self._sp is None:
            import spotipy
            from spotipy.cache_handler import CacheFileHandler
            from spotipy.oauth2 import SpotifyOAuth

            self._sp = spotipy.Spotify(
                auth_manager=SpotifyOAuth(
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    redirect_uri=REDIRECT_URI,
                    scope=SCOPES,
                    cache_handler=CacheFileHandler(cache_path=str(self._token_pfad)),
                    open_browser=open_browser,
                )
            )
        return self._sp

    @property
    def eingerichtet(self) -> bool:
        return self._token_pfad.exists()

    def einrichten(self) -> None:
        """Holt den Zugang per Browser; spotipy fängt die Weiterleitung auf 127.0.0.1:8890 ab."""
        self._spotify(open_browser=True).current_user()
        print(f"Zugang gespeichert in {self._token_pfad}")

    def geraete(self) -> list[dict]:
        return [
            {"id": g["id"], "name": g["name"], "aktiv": g["is_active"]}
            for g in self._spotify().devices().get("devices", [])
        ]

    def _geraet_id(self) -> str | None:
        """Das konfigurierte Gerät, sonst das aktive, sonst das erste; notfalls öffnet es die App."""
        geraete = self.geraete()
        if not geraete and self._starter is not None and self._starter():
            for _ in range(GERAET_WARTEN_SECS):
                self._warten(1.0)
                if geraete := self.geraete():
                    break
        if not geraete:
            raise RuntimeError("Kein Spotify-Gerät verfügbar; Spotify auf einem Gerät öffnen oder spotifyd starten.")
        for g in geraete:
            if self._geraet and self._geraet in g["name"].casefold():
                return g["id"]
        for g in geraete:
            if g["aktiv"]:
                return g["id"]
        return geraete[0]["id"]

    def abspielen(self, suche: str, art: str = "titel") -> str:
        typ = ARTEN.get(art.strip().casefold(), "track")
        treffer = self._spotify().search(q=suche, type=typ, limit=1).get(f"{typ}s", {}).get("items", [])
        if not treffer:
            raise ValueError(f"Nichts gefunden zu '{suche}'.")
        gefunden = treffer[0]
        geraet = self._geraet_id()
        if typ == "track":
            self._spotify().start_playback(device_id=geraet, uris=[gefunden["uri"]])
            return f"{gefunden['name']} von {', '.join(a['name'] for a in gefunden['artists'])}"
        self._spotify().start_playback(device_id=geraet, context_uri=gefunden["uri"])
        return gefunden["name"]

    def pause(self) -> None:
        self._spotify().pause_playback()

    def weiter(self) -> None:
        self._spotify().start_playback(device_id=self._geraet_id())

    def naechstes(self) -> None:
        self._spotify().next_track()

    def lautstaerke(self, prozent: int) -> None:
        self._spotify().volume(max(0, min(100, prozent)), device_id=self._geraet_id())

    def was_laeuft(self) -> str:
        zustand = self._spotify().current_playback()
        if not zustand or not zustand.get("item"):
            return "Gerade läuft nichts."
        titel = zustand["item"]
        kuenstler = ", ".join(a["name"] for a in titel.get("artists", []))
        return f"{titel['name']} von {kuenstler}" + ("" if zustand.get("is_playing") else " (pausiert)")


def aus_umgebung(token_pfad: Path, geraet: str = "") -> Musik | None:
    client_id, secret = os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")
    if not (client_id and secret):
        return None
    return Musik(client_id, secret, token_pfad, geraet, starter=app_starter())


def main() -> None:
    from dotenv import load_dotenv

    from alfred.config import load_settings

    parser = argparse.ArgumentParser(description="Spotify für Alfred")
    parser.add_argument("--einrichten", action="store_true", help="Zugang per Browser freischalten")
    parser.add_argument("--geraete", action="store_true", help="Spotify-Connect-Geräte anzeigen")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(override=True)
    settings = load_settings()
    musik = aus_umgebung(settings.pfad(settings.spotify.token), settings.spotify.geraet)
    if musik is None:
        raise SystemExit("SPOTIFY_CLIENT_ID und SPOTIFY_CLIENT_SECRET fehlen in .env; Anleitung oben in src/alfred/musik.py.")
    if args.einrichten:
        musik.einrichten()
    if args.geraete or args.einrichten:
        for g in musik.geraete():
            print(f"{'*' if g['aktiv'] else ' '} {g['name']}")
    if not (args.einrichten or args.geraete):
        parser.error("--einrichten oder --geraete angeben")


if __name__ == "__main__":
    main()
