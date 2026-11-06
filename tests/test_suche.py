import asyncio

import httpx
import pytest

from alfred.suche import Suchmaschine, treffer_als_text, treffer_mit_adressen

ANTWORT = {
    "results": [
        {"title": "Friedrich Merz", "content": "Bundeskanzler seit Mai 2025. " + "x" * 400, "url": "https://de.wikipedia.org/wiki/Friedrich_Merz"},
        {"title": "Tagesschau", "content": "Regierung", "url": "https://www.tagesschau.de/x"},
    ]
}


def client(status=200, daten=ANTWORT):
    anfragen = []

    def handler(request: httpx.Request) -> httpx.Response:
        anfragen.append(dict(request.url.params))
        return httpx.Response(status, json=daten)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), anfragen


def test_suchen_liefert_gekuerzte_treffer():
    c, anfragen = client()
    treffer = asyncio.run(Suchmaschine("http://127.0.0.1:8888/", c).suchen("Wer ist Bundeskanzler?"))
    assert anfragen[0]["q"] == "Wer ist Bundeskanzler?" and anfragen[0]["format"] == "json"
    assert treffer[0]["titel"] == "Friedrich Merz" and len(treffer[0]["text"]) == 300
    assert treffer[1]["quelle"] == "https://www.tagesschau.de/x"

    text = treffer_als_text(treffer)
    assert text.startswith("- Friedrich Merz (de.wikipedia.org): Bundeskanzler seit Mai 2025.")
    assert "(www.tagesschau.de)" in text
    assert treffer_als_text([]) == "keine Treffer"


def test_nachrichten_von_heute_mit_datum_und_adresse_fuer_den_denker():
    daten = {"results": [{"title": "iPhone 18", "content": "Apple zeigt …", "url": "https://www.heise.de/news/1", "publishedDate": "2026-10-05T07:12:00"}]}
    c, anfragen = client(daten=daten)
    treffer = asyncio.run(Suchmaschine("http://127.0.0.1:8888", c).suchen("Tech-News", kategorie="news", zeitraum="tag"))
    assert anfragen[0]["categories"] == "news" and anfragen[0]["time_range"] == "day"
    assert treffer_mit_adressen(treffer) == "1. iPhone 18 (2026-10-05)\n   https://www.heise.de/news/1\n   Apple zeigt …"
    assert treffer_mit_adressen([]) == "keine Treffer"

    c, anfragen = client()
    asyncio.run(Suchmaschine("http://127.0.0.1:8888", c).suchen("x", zeitraum="irgendwann"))
    assert "categories" not in anfragen[0] and "time_range" not in anfragen[0]


def test_fehler_und_nicht_eingerichtet():
    c, _ = client(status=500, daten={})
    with pytest.raises(RuntimeError, match="nicht erreichbar"):
        asyncio.run(Suchmaschine("http://127.0.0.1:8888", c).suchen("x"))
    assert not Suchmaschine("").eingerichtet and Suchmaschine("http://x").eingerichtet
