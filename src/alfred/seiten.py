"""Webseiten lesen: der Text einer Seite für den Denker, kostenlos.

Mit der eigenen Suchmaschine bekommt der Denker nur Titel und kurze Ausschnitte; was auf der
Seite steht, liest er hiermit selbst. Statt drei Cent je Mistral-Suche kostet das nur die Tokens
für den gelesenen Text.

Nur öffentliche Adressen, auch nach Weiterleitungen: Welche Seite der Denker liest, bestimmt ein
Sprachmodell, und Inhalte aus dem Internet könnten es auf Dienste im eigenen Netz lenken (die
App auf 127.0.0.1 hat keine Anmeldung, Tailscale-Adressen liegen in 100.64.0.0/10). Lücke: Ein
Name, der nach der Prüfung auf eine andere Adresse zeigt (DNS-Rebinding), ist nicht abgefangen.
"""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import re
from html.parser import HTMLParser

import httpx

MAX_ZEICHEN = 6000
MAX_BYTES = 2_000_000
MAX_WEITERLEITUNGEN = 5
WENIG_TEXT = 300  # darunter ist es meist eine Cookie-Wand, eine Paywall oder eine JavaScript-Seite
KOPF = {
    # Viele Seiten liefern einfachen Programmen nichts aus; Alfred liest wie ein Browser.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
}

# Was zwischen diesen Tags steht, ist kein Inhalt. header nur außerhalb von article und main,
# dort steht oft die Überschrift.
_UEBERSPRINGEN = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "button", "iframe", "template", "select", "header"}
_BLOCK = {
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "main",
    "blockquote", "pre", "dd", "dt", "figcaption", "table", "ul", "ol",
}  # fmt: skip


class SeitenFehler(Exception):
    pass


class _Textsammler(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titel = ""
        self.alles: list[str] = []
        self.artikel: list[str] = []
        self._im_titel = False
        self._im_artikel = 0
        self._uebersprungen: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _UEBERSPRINGEN and not (tag == "header" and self._im_artikel):
            self._uebersprungen.append(tag)
        elif tag == "title" and not self._uebersprungen and not self.titel:
            # Nur der erste: Logos als SVG haben eigene title-Tags ("Electrek Logo9to5Mac Logo").
            self._im_titel = True
        elif tag in ("article", "main"):
            self._im_artikel += 1
        if tag in _BLOCK:
            self._umbruch()

    def handle_endtag(self, tag):
        if self._uebersprungen and self._uebersprungen[-1] == tag:
            self._uebersprungen.pop()
        elif tag == "title":
            self._im_titel = False
        elif tag in ("article", "main") and self._im_artikel:
            self._im_artikel -= 1
        if tag in _BLOCK:
            self._umbruch()

    def handle_data(self, data):
        if self._im_titel:
            self.titel += data
        elif not self._uebersprungen:
            self.alles.append(data)
            if self._im_artikel:
                self.artikel.append(data)

    def _umbruch(self):
        self.alles.append("\n")
        if self._im_artikel:
            self.artikel.append("\n")


def _aufraeumen(teile: list[str]) -> str:
    zeilen = (" ".join(zeile.split()) for zeile in "".join(teile).splitlines())
    return "\n".join(zeile for zeile in zeilen if zeile)


def text_aus_html(html: str) -> tuple[str, str]:
    """(Titel, Text) einer Seite; der Text aus article oder main, wenn dort genug steht."""
    sammler = _Textsammler()
    sammler.feed(html)
    sammler.close()
    artikel = _aufraeumen(sammler.artikel)
    text = artikel if len(artikel) >= WENIG_TEXT else _aufraeumen(sammler.alles)
    return " ".join(sammler.titel.split()), text


def _oeffentlich(ip: str) -> bool:
    adresse = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(adresse, ipaddress.IPv6Address) and adresse.ipv4_mapped:
        adresse = adresse.ipv4_mapped
    return adresse.is_global


async def _aufloesen(host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError as e:
        raise SeitenFehler(f"Name nicht auflösbar: {host}") from e
    return [info[4][0] for info in infos]


def _zeichensatz(antwort: httpx.Response, daten: bytes) -> str:
    treffer = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", daten[:4096], re.I)
    name = antwort.charset_encoding or (treffer.group(1).decode() if treffer else "utf-8")
    try:
        codecs.lookup(name)
    except LookupError:
        return "utf-8"
    return name


class Seitenleser:
    def __init__(self, client: httpx.AsyncClient | None = None, aufloesen=None):
        """Args:
        aufloesen: Name → IP-Adressen; austauschbar für Tests.
        """
        self._client = client
        self._aufloesen = aufloesen or _aufloesen

    async def lesen(self, adresse: str, max_zeichen: int = MAX_ZEICHEN) -> str:
        """Titel und Text der Seite für das Sprachmodell; Fehler als Text, nie als Ausnahme."""
        try:
            if self._client is not None:
                url, titel, text = await self._holen(self._client, adresse)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    url, titel, text = await self._holen(client, adresse)
        except SeitenFehler as e:
            return f"Seite nicht lesbar ({adresse}): {e}"
        except httpx.HTTPError as e:
            return f"Seite nicht lesbar ({adresse}): {e.__class__.__name__}"
        if len(text) > max_zeichen:
            text = text[:max_zeichen].rsplit("\n", 1)[0] + "\n[… gekürzt]"
        if len(text) < WENIG_TEXT:
            text += (
                "\n[Kaum Text: wahrscheinlich Cookie-Wand, Paywall oder eine Seite, die erst per JavaScript "
                "lädt. Nimm eine andere Seite oder den Ausschnitt aus der Trefferliste.]"
            )
        return f"Titel: {titel}\nAdresse: {url}\n\n{text}"

    async def _holen(self, client: httpx.AsyncClient, adresse: str) -> tuple[str, str, str]:
        try:
            url = httpx.URL(adresse.strip())
        except httpx.InvalidURL as e:
            raise SeitenFehler("keine gültige Adresse") from e
        for _ in range(MAX_WEITERLEITUNGEN + 1):
            if url.scheme not in ("http", "https") or not url.host:
                raise SeitenFehler("nur http- und https-Adressen")
            if url.port not in (None, 80, 443):
                raise SeitenFehler("nur die üblichen Ports 80 und 443")
            ips = await self._aufloesen(url.host)
            if not ips or not all(_oeffentlich(ip) for ip in ips):
                raise SeitenFehler("keine öffentliche Adresse")
            async with client.stream("GET", url, headers=KOPF, follow_redirects=False, timeout=10.0) as antwort:
                if antwort.is_redirect:
                    url = url.join(antwort.headers.get("location", ""))
                    continue
                if antwort.status_code >= 400:
                    raise SeitenFehler(f"Antwort {antwort.status_code}")
                art = antwort.headers.get("content-type", "").split(";")[0].strip().lower()
                if "html" not in art and art != "text/plain":
                    raise SeitenFehler(f"kein Text, sondern {art or 'unbekannt'}")
                daten = b""
                async for stueck in antwort.aiter_bytes():
                    daten += stueck
                    if len(daten) > MAX_BYTES:
                        break
            text = daten.decode(_zeichensatz(antwort, daten), errors="replace")
            if art == "text/plain":
                return str(url), "", _aufraeumen([text])
            titel, inhalt = text_aus_html(text)
            return str(url), titel, inhalt
        raise SeitenFehler("zu viele Weiterleitungen")
