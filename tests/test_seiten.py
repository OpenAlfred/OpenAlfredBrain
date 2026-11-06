"""Seiten lesen für den Denker: Text herauslösen, und nie ins eigene Netz."""

import asyncio

import httpx

from alfred.seiten import Seitenleser, text_aus_html

ARTIKEL = """<!doctype html><html><head><meta charset="utf-8"><title>Neues iPhone vorgestellt | heise</title>
<script>var tracking = "nicht lesen";</script><style>.x { color: red }</style></head>
<body><header><a href="/">heise online</a> Abo Login</header><svg><title>heise Logo</title></svg><title>Zweiter Titel</title>
<nav><a>News</a><a>Tests</a></nav>
<main><article><header><h1>Apple zeigt das iPhone 18</h1></header>
<p>Apple hat am Montag das iPhone 18 vorgestellt. Es kostet ab 999 Euro und kommt am 17. Oktober in den Handel.</p>
<p>Neu sind ein größerer Akku, ein schnellerer Chip und eine Kamera mit fünffachem Zoom. Die Vorbestellung beginnt am Freitag.</p>
<p>Analysten erwarten, dass sich das Modell vor allem in Europa gut verkauft, weil der Preis gleich bleibt.</p>
</article></main>
<aside>Werbung: Jetzt abonnieren!</aside><footer>Impressum Datenschutz</footer></body></html>"""

OEFFENTLICH = {"www.heise.de": ["193.99.144.85"], "umleitung.de": ["93.184.216.34"], "localhost": ["127.0.0.1"]}


async def aufloesen(host):
    return OEFFENTLICH.get(host, [host])


def leser(antworten: dict[str, httpx.Response]):
    abgerufen = []

    def handler(request: httpx.Request) -> httpx.Response:
        abgerufen.append(str(request.url))
        return antworten.get(str(request.url), httpx.Response(404))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Seitenleser(client, aufloesen=aufloesen), abgerufen


def test_text_aus_dem_artikel_ohne_navigation_skripte_und_werbung():
    titel, text = text_aus_html(ARTIKEL)
    assert titel == "Neues iPhone vorgestellt | heise"
    assert text.startswith("Apple zeigt das iPhone 18\nApple hat am Montag")
    assert "999 Euro" in text and "fünffachem Zoom" in text
    for rauschen in ("tracking", "color", "Abo Login", "Tests", "Werbung", "Impressum"):
        assert rauschen not in text


def test_seite_lesen_liefert_titel_adresse_und_text():
    html = httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=ARTIKEL.encode())
    l, _ = leser({"https://www.heise.de/news/1": html})
    ergebnis = asyncio.run(l.lesen("https://www.heise.de/news/1"))
    assert ergebnis.startswith("Titel: Neues iPhone vorgestellt | heise\nAdresse: https://www.heise.de/news/1\n\nApple zeigt")
    assert "Kaum Text" not in ergebnis


def test_nie_ins_eigene_netz():
    l, abgerufen = leser({})
    for adresse in (
        "http://127.0.0.1:7870/api/gedaechtnis",  # die App, ohne Anmeldung
        "http://localhost/api/gedaechtnis",
        "http://192.168.1.1/",  # der Router
        "http://100.101.102.103/",  # ein Gerät im Tailscale-Netz
        "http://[::1]/",
        "file:///C:/Users/nutzer/.env",
        "ftp://www.heise.de/",
        "https://www.heise.de:8443/",
        "kein link",
    ):
        assert asyncio.run(l.lesen(adresse)).startswith(f"Seite nicht lesbar ({adresse})"), adresse
    assert abgerufen == [], "keine einzige Anfrage darf rausgehen"


def test_weiterleitung_ins_eigene_netz_wird_nicht_verfolgt():
    umleitung = httpx.Response(302, headers={"location": "http://localhost/api/gedaechtnis"})
    l, abgerufen = leser({"https://umleitung.de/": umleitung})
    assert "keine öffentliche Adresse" in asyncio.run(l.lesen("https://umleitung.de/"))
    assert abgerufen == ["https://umleitung.de/"]


def test_weiterleitung_nach_draussen_wird_verfolgt():
    html = httpx.Response(200, headers={"content-type": "text/html"}, content=ARTIKEL.encode())
    l, abgerufen = leser(
        {"https://umleitung.de/": httpx.Response(301, headers={"location": "https://www.heise.de/news/1"}), "https://www.heise.de/news/1": html}
    )
    assert "Adresse: https://www.heise.de/news/1" in asyncio.run(l.lesen("https://umleitung.de/"))
    assert abgerufen == ["https://umleitung.de/", "https://www.heise.de/news/1"]


def test_kein_text_und_wenig_text():
    pdf = httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    wand = httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html><body><p>Bitte Cookies akzeptieren.</p></body></html>")
    l, _ = leser({"https://www.heise.de/a.pdf": pdf, "https://www.heise.de/wand": wand})
    assert "kein Text, sondern application/pdf" in asyncio.run(l.lesen("https://www.heise.de/a.pdf"))
    assert "Kaum Text" in asyncio.run(l.lesen("https://www.heise.de/wand"))
    assert "Antwort 404" in asyncio.run(l.lesen("https://www.heise.de/fehlt"))


def test_zeichensatz_aus_der_seite_und_kuerzen():
    alt = "<html><head><meta charset='iso-8859-1'></head><body><p>" + "Größe und Fußweg. " * 600 + "</p></body></html>"
    seite = httpx.Response(200, headers={"content-type": "text/html"}, content=alt.encode("latin-1"))
    l, _ = leser({"https://www.heise.de/alt": seite})
    ergebnis = asyncio.run(l.lesen("https://www.heise.de/alt", max_zeichen=500))
    assert "Größe und Fußweg." in ergebnis and ergebnis.endswith("[… gekürzt]")
