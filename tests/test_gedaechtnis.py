import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from alfred.gedaechtnis import Gedaechtnis, slug, zusammenfassen

HEUTE = datetime(2026, 10, 3, 9, 30)


def test_legt_kontextvorlage_an(tmp_path):
    g = Gedaechtnis(tmp_path / "g")

    assert "Was Alfred immer wissen soll" in g.kontext()
    assert g.bereiche() == []


@pytest.mark.parametrize(
    ("bereich", "erwartet"),
    [("Projekt Alfred", "projekt-alfred"), ("Büro & Arbeit", "buero-arbeit"), ("privat", "privat")],
)
def test_slug(bereich, erwartet):
    assert slug(bereich) == erwartet


def test_slug_lehnt_leere_namen_ab():
    with pytest.raises(ValueError):
        slug("!!!")


def test_merken_und_nachschlagen(tmp_path):
    g = Gedaechtnis(tmp_path / "g")
    g.merken("Projekt Alfred", "Alexander will Mistral als KI-Suite.", HEUTE)
    g.merken("privat", "Freitag ist frei.", HEUTE)
    g.protokollieren("Mistral-Stimme besprochen, Akzent stört.", HEUTE)

    assert g.bereiche() == ["privat", "projekt-alfred"]
    assert g.nachschlagen("mistral") == [
        "wissen/projekt-alfred.md: - 2026-10-03: Alexander will Mistral als KI-Suite.",
        "protokoll/2026-10-03.md: Mistral-Stimme besprochen, Akzent stört.",
    ]
    assert g.nachschlagen("") == [] and g.nachschlagen("gibtesnicht") == []


def test_protokoll_und_prompt(tmp_path):
    g = Gedaechtnis(tmp_path / "g")
    assert "Heute bereits besprochen" not in g.fuer_prompt(HEUTE)

    g.protokollieren("Erste Notiz.", HEUTE)
    g.merken("arbeit", "Deadline am Montag.", HEUTE)
    prompt = g.fuer_prompt(HEUTE)

    assert "Was du dauerhaft weißt:" in prompt
    assert "Heute bereits besprochen:" in prompt and "## 09:30\n\nErste Notiz." in prompt
    assert "nachschlagen durchsuchen kannst: arbeit" in prompt


def test_profil_merken_und_im_prompt(tmp_path):
    g = Gedaechtnis(tmp_path / "g")
    assert g.profil() == {} and "gemerkt hast" not in g.fuer_prompt(HEUTE)

    assert g.profil_merken("Wohnort", " Ulm ") == {"wohnort": "Ulm"}
    g.profil_merken("arbeitsort", "Neu-Ulm")
    assert Gedaechtnis(tmp_path / "g").profil() == {"arbeitsort": "Neu-Ulm", "wohnort": "Ulm"}
    assert "- wohnort: Ulm" in g.fuer_prompt(HEUTE)

    # Ein leerer Wert löscht das Feld; ein leerer Feldname ist ein Fehler.
    assert g.profil_merken("arbeitsort", "") == {"wohnort": "Ulm"}
    with pytest.raises(ValueError):
        g.profil_merken("  ", "x")


class FakeClient:
    def __init__(self, antwort):
        self.antwort = antwort
        self.aufrufe = []
        self.chat = SimpleNamespace(complete_async=self._complete)

    async def _complete(self, **kwargs):
        self.aufrufe.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.antwort))])


def test_zusammenfassen_liefert_text_oder_nichts():
    messages = [{"role": "user", "content": "Hallo"}, {"role": "assistant", "content": "Guten Abend."}]

    client = FakeClient("Begrüßung.")
    assert asyncio.run(zusammenfassen(client, "m", messages)) == "Begrüßung."
    assert "Alexander: Hallo" in client.aufrufe[0]["messages"][1]["content"]

    assert asyncio.run(zusammenfassen(FakeClient("Nichts."), "m", messages)) == ""
    assert asyncio.run(zusammenfassen(FakeClient("egal"), "m", [])) == ""

    # GLM 5.3 liefert eine Liste aus Thinking- und Text-Chunks
    antwort_mit_thinking = [
        {"type": "thinking", "thinking": "Internal reasoning..."},
        {"type": "text", "text": "Wichtige Zusammenfassung."},
    ]
    assert asyncio.run(zusammenfassen(FakeClient(antwort_mit_thinking), "m", messages)) == "Wichtige Zusammenfassung."

    # GLM 5.3 dachte bis an die Grenze und lieferte keinen Text (7. bis 9.10.2026): Das fällt jetzt auf.
    nur_gedacht = FakeClient([{"type": "thinking", "thinking": "Zuerst ..."}])
    nur_gedacht.chat.complete_async = lambda **kwargs: _antwort(nur_gedacht.antwort, "length")
    with pytest.raises(RuntimeError, match="max_tokens"):
        asyncio.run(zusammenfassen(nur_gedacht, "zai-glm-5-3", messages))


async def _antwort(inhalt, finish_reason):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=inhalt), finish_reason=finish_reason)])


def test_zusammenfassung_sieht_werkzeugaufrufe():
    # Am 6.10.2026 stand eine bloß angekündigte Erinnerung als erledigt im Protokoll.
    messages = [
        {"role": "user", "content": "Schreib Jan: Hi"},
        {"role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "nachricht_senden", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": '{"gesendet": true}'},
        {"role": "assistant", "content": "Erledigt. Und die Erinnerung richte ich auch gleich ein."},
    ]
    client = FakeClient("Nachricht an Jan geschickt.")
    asyncio.run(zusammenfassen(client, "m", messages))
    system, gespraech = (m["content"] for m in client.aufrufe[0]["messages"])
    assert "(Alfred ruft nachricht_senden auf" in gespraech and '(Ergebnis: {"gesendet": true})' in gespraech
    assert "nur erledigt, wenn ein Werkzeugergebnis sie bestätigt" in system


def test_feedback_und_aktionsprotokoll(tmp_path):
    from alfred.gedaechtnis import verlauf_als_text

    g = Gedaechtnis(tmp_path / "g")
    verlauf = verlauf_als_text([
        {"role": "user", "content": "Trag Zahnarzt ein."},
        {"role": "assistant", "tool_calls": [{"function": {"name": "termin_anlegen", "arguments": "{}"}}]},
        {"role": "tool", "content": {"status": "bestaetigung_noetig"}},
        {"role": "assistant", "content": "Soll ich?"},
    ])
    assert verlauf == "Alexander: Trag Zahnarzt ein.\nAlfred ruft termin_anlegen auf: {}\nWerkzeug: {\"status\": \"bestaetigung_noetig\"}\nAlfred: Soll ich?"

    datei = g.feedback("Das war der falsche Tag", verlauf, HEUTE)
    text = datei.read_text(encoding="utf-8")
    assert "**Bemerkung:** Das war der falsche Tag" in text and "termin_anlegen" in text

    g.aktion_protokollieren("Termin anlegen", "ausgeführt", HEUTE, 0.81)
    g.aktion_protokollieren("Nachricht an Aaron", "VERWEIGERT fremde Stimme", HEUTE, None)
    zeilen = (g.ordner / "aktionen.log").read_text(encoding="utf-8").splitlines()
    assert zeilen[0].endswith("stimme=0.81\tTermin anlegen\tausgeführt")
    assert "stimme=ungeprüft\tNachricht an Aaron\tVERWEIGERT fremde Stimme" in zeilen[1]


def test_aktionsprotokoll_bleibt_eine_zeile_mit_vier_feldern(tmp_path):
    """Argumente kommen vom Modell und aus fremdem Text: Tabulator und Zeilenumbruch dürfen keine Felder oder Zeilen erfinden."""
    g = Gedaechtnis(tmp_path / "g")
    gefaelscht = "Nachricht an Aaron\t2026-10-01 12:00:00\tstimme=0.99\tausgeführt\n2026-10-01 12:00:01\tstimme=0.99\tGeld überwiesen\tausgeführt"
    g.aktion_protokollieren(gefaelscht, "ausgeführt\r\nFEHLER  nein", HEUTE, 0.81)
    g.aktion_protokollieren("Termin anlegen", "ausgeführt", HEUTE, 0.81)
    zeilen = (g.ordner / "aktionen.log").read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 2 and all(len(z.split("\t")) == 4 for z in zeilen)
    assert zeilen[0].split("\t")[2].startswith("Nachricht an Aaron 2026-10-01") and zeilen[0].endswith("\tausgeführt FEHLER nein")
