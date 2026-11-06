"""Push-Nachrichten aufs Telefon über ntfy (https://ntfy.sh): kostenlos, offen, selbst hostbar.

Alfred schickt damit fällige Erinnerungen auch dann, wenn niemand im Raum ist, und auf Zuruf
Texte "aufs Handy". Die ntfy-App auf dem iPhone abonniert ein Thema; der Server ist entweder
ein eigener auf dem Mini-PC (empfohlen, über Tailscale erreichbar) oder ntfy.sh mit einem
langen, zufälligen Themennamen. Dringende Nachrichten (prioritaet "max") klingeln auch bei
stummem Telefon, wenn die App so eingestellt ist; das ersetzt einen Wecker.
"""

from __future__ import annotations

import httpx

PRIORITAETEN = {"min": "1", "niedrig": "2", "normal": "3", "hoch": "4", "max": "5"}


class Benachrichtiger:
    def __init__(self, url: str, thema: str, token: str = "", client: httpx.AsyncClient | None = None):
        self._url = url.rstrip("/")
        self._thema = thema.strip()
        self._token = token.strip()
        self._client = client

    @property
    def eingerichtet(self) -> bool:
        return bool(self._url and self._thema)

    async def senden(
        self,
        text: str,
        titel: str = "Alfred",
        prioritaet: str = "normal",
        aktionen: list[tuple[str, str]] | None = None,
        klick: str = "",
    ) -> None:
        """Schickt eine Nachricht; Fehler kommen als RuntimeError mit lesbarem Text.

        aktionen: Knöpfe als (Beschriftung, Adresse); ntfy ruft die Adresse beim Tippen auf.
        klick: Adresse, die sich beim Tippen auf die Nachricht öffnet, etwa ein Bericht in der App.
        """
        if not self.eingerichtet:
            raise RuntimeError("Push ist nicht eingerichtet: benachrichtigung.url und .thema in config/alfred.yaml fehlen.")
        if prioritaet not in PRIORITAETEN:
            raise ValueError(f"Priorität unbekannt: {prioritaet}. Erlaubt: {', '.join(PRIORITAETEN)}")
        kopf = {
            # HTTP-Kopfzeilen vertragen nur ASCII; der Text selbst geht als UTF-8 im Rumpf.
            "Title": titel.encode("ascii", "ignore").decode() or "Alfred",
            "Priority": PRIORITAETEN[prioritaet],
            "Tags": "robot",
        }
        if self._token:
            kopf["Authorization"] = f"Bearer {self._token}"
        if klick:
            kopf["Click"] = klick
        if aktionen:
            kopf["Actions"] = "; ".join(
                f"http, {name.encode('ascii', 'ignore').decode()}, {url}, method=GET, clear=true" for name, url in aktionen
            )
        try:
            if self._client is not None:
                antwort = await self._client.post(f"{self._url}/{self._thema}", content=text.encode("utf-8"), headers=kopf)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    antwort = await client.post(f"{self._url}/{self._thema}", content=text.encode("utf-8"), headers=kopf)
        except httpx.HTTPError as e:
            raise RuntimeError(f"Push-Server nicht erreichbar ({e.__class__.__name__})") from e
        if antwort.status_code >= 400:
            raise RuntimeError(f"Push-Server antwortet mit {antwort.status_code}")
