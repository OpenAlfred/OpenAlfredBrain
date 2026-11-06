import asyncio
from types import SimpleNamespace

from alfred.denker import MAX_RUNDEN_BERICHT, MAX_SUCHEN_BERICHT, MAX_SUCHEN_DENKEN, Denker, _text_aus_ausgaben


def nachricht(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


class FakeClient:
    def __init__(self):
        self.chat_aufrufe = []
        self.agent_aufrufe = []
        self.chat = SimpleNamespace(complete_async=self._chat)
        self.beta = SimpleNamespace(conversations=SimpleNamespace(start_async=self._agent))

    async def _chat(self, **kwargs):
        self.chat_aufrufe.append(kwargs)
        if len(self.chat_aufrufe) == 1:
            aufruf = SimpleNamespace(
                id="c1", function=SimpleNamespace(name="websuche", arguments='{"frage": "Wetter Berlin"}')
            )
            return nachricht(tool_calls=[aufruf])
        return nachricht(content="Morgen wird es in Berlin sonnig, sagt der Wetterdienst.")

    async def _agent(self, **kwargs):
        self.agent_aufrufe.append(kwargs)
        return SimpleNamespace(outputs=[SimpleNamespace(content="Sonnig, 18 Grad (wetter.de)")])


def test_denker_ruft_werkzeug_auf_und_antwortet():
    client = FakeClient()
    denker = Denker(client, "glm", "small")

    ergebnis = asyncio.run(denker.bearbeiten("Wie wird das Wetter morgen in Berlin?"))

    assert ergebnis.startswith("Morgen wird es in Berlin sonnig")
    assert client.agent_aufrufe[0]["tools"] == [{"type": "web_search"}]
    assert client.agent_aufrufe[0]["inputs"] == "Wetter Berlin"
    zweiter = client.chat_aufrufe[1]["messages"]
    assert zweiter[-1] == {"role": "tool", "tool_call_id": "c1", "name": "websuche", "content": "Sonnig, 18 Grad (wetter.de)"}
    assert client.chat_aufrufe[0]["model"] == "glm"


def test_text_aus_ausgaben_versteht_text_und_stuecke():
    ausgaben = [
        SimpleNamespace(type="tool.execution"),
        SimpleNamespace(content="Erster Teil"),
        SimpleNamespace(content=[SimpleNamespace(text="Zweiter"), SimpleNamespace(url="x"), SimpleNamespace(text="Teil")]),
    ]
    assert _text_aus_ausgaben(ausgaben) == "Erster Teil\nZweiter\nTeil"
    assert _text_aus_ausgaben([]) == "Keine Antwort erhalten."


def referenz(titel, url):
    return SimpleNamespace(type="tool_reference", title=titel, url=url)


def test_text_aus_ausgaben_haengt_auf_wunsch_die_quellen_an():
    ausgaben = [
        SimpleNamespace(
            content=[
                SimpleNamespace(text="Apple stellt ein neues iPhone vor."),
                referenz("Heise", "https://www.heise.de/news/1"),
                referenz("Heise", "https://www.heise.de/news/1"),  # doppelt
                referenz("ohne Adresse", object()),  # die SDK setzt dann einen Platzhalter
                referenz("Golem", "https://www.golem.de/2"),
            ]
        )
    ]
    assert _text_aus_ausgaben(ausgaben) == "Apple stellt ein neues iPhone vor."
    assert _text_aus_ausgaben(ausgaben, mit_quellen=True) == (
        "Apple stellt ein neues iPhone vor.\n\nQuellen:\n- Heise: https://www.heise.de/news/1\n- Golem: https://www.golem.de/2"
    )


class SuchenderClient(FakeClient):
    """Sucht, bis er nicht mehr darf; dann schreibt er den Bericht."""

    async def _chat(self, **kwargs):
        self.chat_aufrufe.append(kwargs)
        if kwargs["tool_choice"] == "none":
            return nachricht(content="# Tech-News\n## Kurzfassung\nKI überall.")
        aufruf = SimpleNamespace(id=f"c{len(self.chat_aufrufe)}", function=SimpleNamespace(name="websuche", arguments={"frage": "Tech-News"}))
        return nachricht(tool_calls=[aufruf])

    async def _agent(self, **kwargs):
        self.agent_aufrufe.append(kwargs)
        return SimpleNamespace(outputs=[SimpleNamespace(content=[SimpleNamespace(text="Neues iPhone."), referenz("Heise", "https://heise.de/1")])])


def test_bericht_mit_datum_quellen_und_letzter_runde_ohne_werkzeuge():
    client = SuchenderClient()
    zaehler = []
    denker = Denker(client, "glm", "small", zaehler=lambda: zaehler.append(1))

    bericht = asyncio.run(denker.bericht("Die Tech-News von heute", kontext="Bisheriges Gespräch: …", heute="Montag, der 05.10.2026"))

    assert bericht == "# Tech-News\n## Kurzfassung\nKI überall."
    eingabe = client.chat_aufrufe[0]["messages"][1]["content"]
    assert eingabe.startswith("Heute ist Montag, der 05.10.2026.") and eingabe.endswith("Auftrag: Die Tech-News von heute")
    assert "Nachricht aufs Handy erledigt Alfred" in eingabe, "den Push-Wunsch aus dem Gespräch soll er nicht auf sich beziehen"
    assert "## Quellen" in client.chat_aufrufe[0]["messages"][0]["content"]
    werkzeugergebnisse = [m["content"] for m in client.chat_aufrufe[-1]["messages"] if isinstance(m, dict) and m["role"] == "tool"]
    assert werkzeugergebnisse[0].endswith("Quellen:\n- Heise: https://heise.de/1")
    # Gehen die Runden aus, muss er mit dem schreiben, was er hat, statt zu scheitern.
    assert len(client.chat_aufrufe) == MAX_RUNDEN_BERICHT and client.chat_aufrufe[-1]["tool_choice"] == "none"
    # Mehr Suchen als erlaubt kosten nichts: Sie kommen gar nicht bei Mistral an.
    assert len(zaehler) == len(client.agent_aufrufe) == MAX_SUCHEN_BERICHT
    assert werkzeugergebnisse[-1].startswith("Keine Suche mehr erlaubt")


def test_nachdenken_ohne_eigene_suche_nimmt_hoechstens_vier_mistral_suchen():
    client = SuchenderClient()
    asyncio.run(Denker(client, "glm", "small").bearbeiten("Recherchier mal was"))
    assert len(client.agent_aufrufe) == MAX_SUCHEN_DENKEN


class FakeSuche:
    def __init__(self, kaputt=False):
        self.anfragen = []
        self.kaputt = kaputt

    async def suchen(self, frage, anzahl=5, kategorie="", zeitraum=""):
        self.anfragen.append((frage, anzahl, kategorie, zeitraum))
        if self.kaputt:
            raise RuntimeError("Suchmaschine nicht erreichbar (ConnectError)")
        return [{"titel": "iPhone 18", "text": "Apple zeigt …", "quelle": "https://www.heise.de/news/1", "datum": "2026-10-05"}]


class FakeLeser:
    def __init__(self):
        self.gelesen = []

    async def lesen(self, adresse, max_zeichen=6000):
        self.gelesen.append(adresse)
        return f"Titel: iPhone 18\nAdresse: {adresse}\n\nApple hat das iPhone 18 vorgestellt."


class Rechercheur(FakeClient):
    """Sucht kostenlos, liest eine Seite, will dreimal teuer suchen und schreibt dann."""

    def __init__(self):
        super().__init__()
        self.schritte = [
            [("websuche", {"frage": "Tech-News", "nachrichten": True, "zeitraum": "tag"})],
            [("seite_lesen", {"adresse": "https://www.heise.de/news/1"})],
            [("tiefensuche", {"frage": "a"}), ("tiefensuche", {"frage": "b"}), ("tiefensuche", {"frage": "c"})],
        ]

    async def _chat(self, **kwargs):
        self.chat_aufrufe.append(kwargs)
        if self.schritte:
            schritt = self.schritte.pop(0)
            aufrufe = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a)) for i, (n, a) in enumerate(schritt)]
            return nachricht(tool_calls=aufrufe)
        return nachricht(content="# Tech-News\n## Kurzfassung\nKI überall.")

    async def _agent(self, **kwargs):
        self.agent_aufrufe.append(kwargs)
        return SimpleNamespace(outputs=[SimpleNamespace(content=[SimpleNamespace(text="Gefunden."), referenz("Golem", "https://golem.de/1")])])


def werkzeugergebnisse(client):
    return [m["content"] for m in client.chat_aufrufe[-1]["messages"] if isinstance(m, dict) and m["role"] == "tool"]


def test_mit_eigener_suchmaschine_sucht_und_liest_er_kostenlos():
    client, suche, leser = Rechercheur(), FakeSuche(), FakeLeser()
    zaehler = []
    denker = Denker(client, "glm", "small", zaehler=lambda: zaehler.append(1), suche=suche, leser=leser)

    bericht = asyncio.run(denker.bericht("Die Tech-News von heute"))

    assert bericht.startswith("# Tech-News")
    assert [w["function"]["name"] for w in client.chat_aufrufe[0]["tools"]] == ["websuche", "seite_lesen", "tiefensuche", "code_ausfuehren"]
    # Ein Nachrichtentreffer ist zu wenig: Die allgemeine Suche kommt dazu, doppelte Adressen nicht.
    assert suche.anfragen == [("Tech-News", 8, "news", "tag"), ("Tech-News", 8, "", "")]
    assert leser.gelesen == ["https://www.heise.de/news/1"]
    ergebnisse = werkzeugergebnisse(client)
    assert ergebnisse[0].startswith("1. iPhone 18 (2026-10-05)\n   https://www.heise.de/news/1")
    # Mistral nur als Notnagel: Von drei teuren Suchen laufen zwei, und nur die kosten.
    assert len(client.agent_aufrufe) == len(zaehler) == 2
    assert "Quellen:\n- Golem: https://golem.de/1" in ergebnisse[2]
    assert ergebnisse[-1].startswith("Keine tiefensuche mehr erlaubt")


def test_bringt_die_nachrichtensuche_wenig_kommt_die_allgemeine_dazu():
    class Duenn(FakeSuche):
        async def suchen(self, frage, anzahl=5, kategorie="", zeitraum=""):
            self.anfragen.append((frage, anzahl, kategorie, zeitraum))
            if kategorie == "news":
                return [{"titel": "Eine Meldung", "text": "…", "quelle": "https://a.de/1", "datum": "2026-10-05"}]
            return [
                {"titel": "Eine Meldung", "text": "…", "quelle": "https://a.de/1", "datum": ""},
                {"titel": "heise online", "text": "IT-News", "quelle": "https://www.heise.de/", "datum": ""},
            ]

    client, suche = Rechercheur(), Duenn()
    client.schritte = [[("websuche", {"frage": "Tech-News", "nachrichten": True, "zeitraum": "tag"})]]
    asyncio.run(Denker(client, "glm", "small", suche=suche, leser=FakeLeser()).bericht("x"))
    assert [a[2] for a in suche.anfragen] == ["news", ""]
    ergebnis = werkzeugergebnisse(client)[0]
    assert ergebnis.count("https://a.de/1") == 1 and "https://www.heise.de/" in ergebnis


def test_faellt_die_eigene_suchmaschine_aus_bleibt_die_tiefensuche():
    client = Rechercheur()
    client.schritte = [[("websuche", {"frage": "Tech-News"})]]
    asyncio.run(Denker(client, "glm", "small", suche=FakeSuche(kaputt=True), leser=FakeLeser()).bericht("x"))
    ergebnis = werkzeugergebnisse(client)[0]
    assert "antwortet nicht" in ergebnis and "Nimm tiefensuche" in ergebnis


def test_bericht_ohne_vorrede():
    class Vorredner(FakeClient):
        async def _chat(self, **kwargs):
            return nachricht(content="Alle Recherchen sind abgeschlossen. Hier ist der Bericht:\n\n# Titel\n## Kurzfassung\nKurz.")

    assert asyncio.run(Denker(Vorredner(), "glm", "small").bericht("x")) == "# Titel\n## Kurzfassung\nKurz."


def test_bericht_ohne_text_ist_ein_fehler():
    class Stumm(FakeClient):
        async def _chat(self, **kwargs):
            return nachricht(content="")

    try:
        asyncio.run(Denker(Stumm(), "glm", "small").bericht("x"))
    except RuntimeError as e:
        assert "keinen Bericht" in str(e)
    else:
        raise AssertionError("ohne Bericht kein Erfolg")
