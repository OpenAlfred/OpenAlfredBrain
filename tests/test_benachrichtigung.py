import asyncio

import httpx
import pytest

from alfred.benachrichtigung import Benachrichtiger
from alfred.erinnerungen import Erinnerungen


def server(status=200):
    anfragen = []

    def handler(request: httpx.Request) -> httpx.Response:
        anfragen.append((str(request.url), dict(request.headers), request.content.decode("utf-8")))
        return httpx.Response(status)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), anfragen


def test_senden_mit_titel_prioritaet_und_token():
    client, anfragen = server()
    b = Benachrichtiger("https://ntfy.beispiel.de/", "alfred-geheim", "tk_123", client)
    assert b.eingerichtet

    asyncio.run(b.senden("Wäsche rausholen", titel="Erinnerung", prioritaet="hoch"))
    url, kopf, rumpf = anfragen[0]
    assert url == "https://ntfy.beispiel.de/alfred-geheim" and rumpf == "Wäsche rausholen"
    assert kopf["title"] == "Erinnerung" and kopf["priority"] == "4" and kopf["authorization"] == "Bearer tk_123"

    asyncio.run(b.senden("Aufstehen!", titel="Wecker ☀", prioritaet="max"))
    assert anfragen[1][1]["priority"] == "5" and anfragen[1][1]["title"] == "Wecker "
    assert "click" not in anfragen[1][1]

    asyncio.run(b.senden("Bericht fertig", klick="https://alfred.example/app/#auftrag-3"))
    assert anfragen[2][1]["click"] == "https://alfred.example/app/#auftrag-3"


def test_nicht_eingerichtet_und_fehler():
    client, _ = server()
    with pytest.raises(RuntimeError, match="nicht eingerichtet"):
        asyncio.run(Benachrichtiger("", "", client=client).senden("x"))
    with pytest.raises(ValueError):
        asyncio.run(Benachrichtiger("https://ntfy.sh", "t", client=client).senden("x", prioritaet="laut"))
    client, _ = server(status=403)
    with pytest.raises(RuntimeError, match="403"):
        asyncio.run(Benachrichtiger("https://ntfy.sh", "t", client=client).senden("x"))


def test_faellige_erinnerung_geht_auch_aufs_telefon(tmp_path):
    from datetime import datetime, timedelta

    erinnerungen = Erinnerungen(tmp_path / "e.json")
    erinnerungen.anlegen("Tee", datetime.now().astimezone() - timedelta(seconds=1))
    gesendet = []

    async def benachrichtigen(text):
        gesendet.append(text)
        raise RuntimeError("Server weg")  # darf die Ansage nicht verhindern

    async def lauf():
        ereignisse: asyncio.Queue[str] = asyncio.Queue()
        aufgabe = asyncio.create_task(erinnerungen.warten(ereignisse, intervall=0.01, benachrichtigen=benachrichtigen))
        text = await asyncio.wait_for(ereignisse.get(), 2)
        aufgabe.cancel()
        return text

    assert "Tee" in asyncio.run(lauf()) and gesendet == ["Tee"]
