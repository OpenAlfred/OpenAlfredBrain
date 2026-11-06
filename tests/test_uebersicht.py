"""Die Fähigkeiten-Übersicht der App: kommt aus dem Werkzeug-Register, ohne Mistral."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from alfred import web
from alfred.aufgaben import Aufgaben
from alfred.auftraege import Auftraege
from alfred.config import load_settings
from alfred.einkauf import Einkaufsliste
from alfred.erinnerungen import Erinnerungen
from alfred.ernaehrung import Ernaehrung
from alfred.freigaben import Freigabedatei
from alfred.gedaechtnis import Gedaechtnis
from alfred.haushalt import Ausgaben, Vorrat, Wochenplan
from alfred.persona import GRUPPEN, Modus, Persona, load_persona
from alfred.resources import Ressourcen
from alfred.routinen import Routinen
from alfred.werkzeuge import REGISTER, STUFEN, Werkzeug
from alfred.werkzeuge.uebersicht import ANBINDUNGEN, ANZEIGE, BESCHREIBUNG, beschreibung, uebersicht


def ressourcen(tmp_path, persona=None, **anbindungen) -> Ressourcen:
    g = Gedaechtnis(tmp_path / "g")
    return Ressourcen(
        settings=load_settings(),
        persona=persona or load_persona(),
        client=None,
        gedaechtnis=g,
        erinnerungen=Erinnerungen(g.ordner / "e.json"),
        denker=None,
        routinen=Routinen(g.ordner / "r.yaml"),
        aufgaben=Aufgaben(g.ordner / "a.yaml"),
        auftraege=Auftraege(g.ordner / "auftraege.yaml", g.ordner / "berichte"),
        einkauf=Einkaufsliste(g.ordner / "einkauf.yaml"),
        ernaehrung=Ernaehrung(g.ordner / "ernaehrung"),
        schaetzer=SimpleNamespace(),
        ausgaben=Ausgaben(g.ordner / "ausgaben"),
        vorrat=Vorrat(g.ordner / "vorrat.yaml"),
        wochenplan=Wochenplan(g.ordner / "plan.yaml", None, "m"),
        freigabedatei=Freigabedatei(g.ordner / "freigabe.json"),
        **anbindungen,
    )


def werkzeug(u: dict, name: str) -> dict:
    return next(w for g in u["gruppen"] for w in g["werkzeuge"] if w["name"] == name)


def test_jedes_werkzeug_und_jede_gruppe_hat_einen_anzeigetext():
    assert set(BESCHREIBUNG) == set(REGISTER), "neues Werkzeug: Satz in BESCHREIBUNG (werkzeuge/uebersicht.py) nachtragen"
    assert set(ANZEIGE) == set(GRUPPEN), "neue Gruppe: Titel, Symbol und Beispiele in ANZEIGE nachtragen"
    assert all(a.beispiele for a in ANZEIGE.values()), "jede Gruppe zeigt mindestens einen Beispielsatz"
    assert len({a.titel for a in ANZEIGE.values()}) == len(ANZEIGE)
    assert all(not s.endswith(".") for s in BESCHREIBUNG.values()), "Beschreibungen stehen ohne Schlusspunkt"


def test_ohne_eintrag_zeigt_die_app_die_erste_zeile_der_modellbeschreibung():
    async def neues_werkzeug(params):
        """Tut etwas Neues, das noch keinen Anzeigetext hat.

        Weitere Zeilen sind für das Modell.
        """

    assert beschreibung(Werkzeug(neues_werkzeug, "kern", "lesend")) == "Tut etwas Neues, das noch keinen Anzeigetext hat."
    assert beschreibung(REGISTER["aktuelle_zeit"]) == BESCHREIBUNG["aktuelle_zeit"]


def test_jede_anbindung_im_register_hat_einen_namen(tmp_path):
    """Jedes Feld, an dem ein Werkzeug scheitern kann, hat in ANBINDUNGEN einen lesbaren Namen."""
    u = uebersicht(ressourcen(tmp_path))
    unbekannt = {w["braucht"] for g in u["gruppen"] for w in g["werkzeuge"] if w["braucht"]} - set(ANBINDUNGEN.values())
    assert not unbekannt


def test_uebersicht_zaehlt_das_register(tmp_path):
    u = uebersicht(ressourcen(tmp_path))
    assert [g["id"] for g in u["gruppen"]] == list(GRUPPEN)
    summe = u["summe"]
    assert summe["gesamt"] == len(REGISTER)
    assert summe["bereit"] + summe["nur_stimme"] + summe["fehlt"] == summe["gesamt"]
    assert sum(summe["stufen"].values()) == summe["gesamt"] and set(summe["stufen"]) == set(STUFEN)
    for g in u["gruppen"]:
        assert len(g["werkzeuge"]) == g["bereit"] + g["nur_stimme"] + g["fehlt"] == sum(g["stufen"].values())


def test_fehlende_anbindung_wird_benannt(tmp_path):
    u = uebersicht(ressourcen(tmp_path))
    kalender = next(g for g in u["gruppen"] if g["id"] == "kalender")
    assert kalender["bereit"] == 0 and kalender["fehlt"] == 4 and kalender["braucht"] == ["Google Kalender"]
    assert werkzeug(u, "termin_anlegen")["status"] == "fehlt" and werkzeug(u, "termin_anlegen")["braucht"] == "Google Kalender"
    assert werkzeug(u, "einkauf_hinzufuegen")["status"] == "bereit" and werkzeug(u, "einkauf_hinzufuegen")["braucht"] is None
    assert werkzeug(u, "musik")["braucht"] == "Spotify"

    # WhatsApp braucht Brücke und Kontakte: Der Späher nennt das erste leere Feld.
    assert werkzeug(u, "nachricht_senden")["braucht"] == "WhatsApp-Brücke"
    u = uebersicht(ressourcen(tmp_path, whatsapp=SimpleNamespace()))
    assert werkzeug(u, "nachricht_senden")["braucht"] == "Kontakte"
    u = uebersicht(ressourcen(tmp_path, whatsapp=SimpleNamespace(), kontakte=SimpleNamespace(), meldeliste=SimpleNamespace()))
    assert werkzeug(u, "nachricht_senden")["status"] == "bereit" and werkzeug(u, "whatsapp_melden")["status"] == "bereit"


def test_was_nur_der_sprach_prozess_hat_ist_nicht_nicht_eingerichtet(tmp_path):
    u = uebersicht(ressourcen(tmp_path))
    for name in ("zuhoeren", "bereitschaft", "gespraech_beenden"):
        assert werkzeug(u, name)["status"] == "nur_stimme", name
    assert u["summe"]["nur_stimme"] == 3
    # Mit Schleuse und Zuhör-Schalter (wie im Raum) sind sie bereit.
    u = uebersicht(ressourcen(tmp_path, schleuse=SimpleNamespace(), zuhoeren=SimpleNamespace()))
    assert werkzeug(u, "zuhoeren")["status"] == "bereit" and u["summe"]["nur_stimme"] == 0


def test_modi_und_gruppen_im_aktiven_modus(tmp_path):
    persona = Persona(
        name="Alfred",
        nutzer="Alexander",
        charakter="x",
        aktiver_modus="fokus",
        modi={
            "standard": Modus(beschreibung="Alltag"),
            "fokus": Modus(beschreibung="Konzentriert", regler={"humor": 0}, werkzeuge=["aufgaben"]),
        },
    )
    u = uebersicht(ressourcen(tmp_path, persona=persona))
    assert u["persona"]["modus"] == "fokus"
    assert [m["name"] for m in u["modi"]] == ["standard", "fokus"] and [m["aktiv"] for m in u["modi"]] == [False, True]
    fokus = u["modi"][1]
    assert fokus["gruppen"] == ["kern", "aufgaben"] and fokus["abweichend"] == ["humor"] and fokus["regler"]["humor"] == 0
    assert u["modi"][0]["gruppen"] == list(GRUPPEN) and u["modi"][0]["abweichend"] == []
    assert fokus["werkzeuge"] < u["modi"][0]["werkzeuge"]
    imm = {g["id"]: g["im_modus"] for g in u["gruppen"]}
    assert imm["kern"] and imm["aufgaben"] and not imm["haushalt"] and not imm["kommunikation"]
    assert next(g for g in u["gruppen"] if g["id"] == "aufgaben")["modi"] == ["standard", "fokus"]
    assert next(g for g in u["gruppen"] if g["id"] == "haushalt")["modi"] == ["standard"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    res = ressourcen(tmp_path)
    monkeypatch.setattr(web, "build_ressourcen", lambda settings, persona: res)
    monkeypatch.setattr(web, "require_api_key", lambda: "x")
    with TestClient(web.create_app(load_settings(), load_persona(), logordner=tmp_path / "logs")) as c:
        yield c


def test_api_zaehler_zaehlt_offenes(client):
    from datetime import date, timedelta

    res = client.app.state.res
    assert client.get("/api/zaehler").json() == {"einkauf": 0, "aufgaben": 0, "ueberfaellig": 0, "auftraege": 0, "timer": 0}
    res.einkauf.hinzufuegen("Milch, Brot")
    gestern = (date.today() - timedelta(days=1)).isoformat()
    res.aufgaben.anlegen("Alt", gestern)
    res.aufgaben.anlegen("Ohne Datum")
    res.auftraege.anlegen("Recherche")
    assert client.get("/api/zaehler").json() == {"einkauf": 2, "aufgaben": 2, "ueberfaellig": 1, "auftraege": 1, "timer": 0}


def test_api_faehigkeiten(client):
    u = client.get("/api/faehigkeiten").json()
    assert u["summe"]["gesamt"] == len(REGISTER)
    assert u["modelle"]["sprecher"] and u["wachwort"] and u["persona"]["name"]
    kern = u["gruppen"][0]
    assert kern["id"] == "kern" and kern["titel"] == "Grundlagen" and kern["beispiele"]
