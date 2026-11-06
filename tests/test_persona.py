from datetime import datetime

import pytest
from pydantic import ValidationError

from alfred.persona import GRUPPEN, STUFEN, Persona, build_system_prompt, dauer_gesprochen, load_persona


def persona(**overrides) -> Persona:
    daten = {
        "name": "Alfred",
        "nutzer": "Alexander",
        "charakter": "Du bist ein Assistent.",
        "regler": {"widerspruch": 7, "ausfuehrlichkeit": 3},
        "modi": {
            "standard": {},
            "fokus": {"beschreibung": "Konzentriertes Arbeiten.", "regler": {"ausfuehrlichkeit": 1}},
        },
    }
    return Persona.model_validate({**daten, **overrides})


def stufe(regler: str, index: int) -> str:
    return STUFEN[regler][index][1]


def test_mitgelieferte_konfiguration_ist_gueltig():
    p = load_persona()
    for modus in p.modi:
        assert p.name in build_system_prompt(p, modus)


@pytest.mark.parametrize(
    ("wert", "index"), [(0, 0), (2, 0), (3, 1), (5, 1), (6, 2), (8, 2), (9, 3), (10, 3)]
)
def test_regler_waehlt_stufe(wert, index):
    prompt = build_system_prompt(persona(regler={"humor": wert}))
    assert stufe("humor", index) in prompt
    assert all(stufe("humor", i) not in prompt for i in range(4) if i != index)


def test_modus_ueberschreibt_nur_seine_regler():
    p = persona()
    standard = build_system_prompt(p)
    fokus = build_system_prompt(p, "fokus")

    assert stufe("ausfuehrlichkeit", 1) in standard
    assert stufe("ausfuehrlichkeit", 0) in fokus
    assert stufe("widerspruch", 2) in standard and stufe("widerspruch", 2) in fokus
    assert "Konzentriertes Arbeiten." in fokus and "Konzentriertes Arbeiten." not in standard


def test_aktiver_modus_gilt_ohne_angabe():
    assert stufe("ausfuehrlichkeit", 0) in build_system_prompt(persona(aktiver_modus="fokus"))


def test_anrede():
    assert "Ihr duzt euch." in build_system_prompt(persona())
    assert "Du siezt Alexander." in build_system_prompt(persona(anrede="sie"))


def test_datum_und_uhrzeit_stehen_im_prompt():
    prompt = build_system_prompt(persona(), jetzt=datetime(2026, 10, 2, 23, 14))

    assert "Es ist Freitag, der 2. Oktober 2026, 23:14 Uhr." in prompt
    assert "Gesprochen: Freitag, der zweite Oktober zweitausendsechsundzwanzig, dreiundzwanzig Uhr vierzehn." in prompt
    assert "Das Jahr heißt gesprochen zweitausendsechsundzwanzig." in prompt
    # Die Tabelle der nächsten Tage: Das Modell machte sonst aus "Mittwoch" den Donnerstag.
    assert "Die nächsten Tage: Samstag 3.10., Sonntag 4.10., Montag 5.10., Dienstag 6.10., Mittwoch 7.10., Donnerstag 8.10., Freitag 9.10." in prompt
    # Und die letzten: Aus "gestern" wurde sonst der Montag der nächsten Woche.
    assert "Zuletzt: vorgestern Mittwoch 30.9., gestern Donnerstag 1.10." in prompt
    assert "Es ist " not in build_system_prompt(persona())


@pytest.mark.parametrize(
    ("stunde", "minute", "gesprochen"),
    [
        (0, 0, "null Uhr"),
        (1, 1, "ein Uhr eins"),
        (9, 30, "neun Uhr dreißig"),
        (12, 5, "zwölf Uhr fünf"),
        (17, 45, "siebzehn Uhr fünfundvierzig"),
        (21, 21, "einundzwanzig Uhr einundzwanzig"),
        (23, 59, "dreiundzwanzig Uhr neunundfünfzig"),
    ],
)
def test_uhrzeit_wird_ausgeschrieben(stunde, minute, gesprochen):
    prompt = build_system_prompt(persona(), jetzt=datetime(2026, 10, 2, stunde, minute))

    assert f"zweitausendsechsundzwanzig, {gesprochen}." in prompt


@pytest.mark.parametrize(
    ("sekunden", "gesprochen"),
    [
        (0, "null Sekunden"),
        (1, "eine Sekunde"),
        (30, "dreißig Sekunden"),
        (60, "eine Minute"),
        (70, "eine Minute und zehn Sekunden"),
        (240, "vier Minuten"),
        (3600, "eine Stunde"),
        (3600 + 300 + 7, "eine Stunde und fünf Minuten"),  # Sekunden ab einer Stunde egal
        (7200, "zwei Stunden"),
        (-5, "null Sekunden"),
        (89.6, "eine Minute und dreißig Sekunden"),
    ],
)
def test_dauer_wird_ausgeschrieben(sekunden, gesprochen):
    assert dauer_gesprochen(sekunden) == gesprochen


def test_kein_rollenspiel_auch_mit_werkzeugen():
    for faehigkeiten in ([], ["Du kannst den Kalender lesen."]):
        assert "ohne Körper" in build_system_prompt(persona(), faehigkeiten=faehigkeiten)


def test_werkzeuge_werden_genannt_oder_ausgeschlossen():
    ohne = build_system_prompt(persona())
    mit = build_system_prompt(persona(), faehigkeiten=["Du kannst den Kalender lesen."])

    assert "noch keine Werkzeuge" in ohne
    assert "Du kannst den Kalender lesen." in mit and "noch keine Werkzeuge" not in mit


@pytest.mark.parametrize(
    "overrides",
    [
        {"aktiver_modus": "gibt_es_nicht"},
        {"regler": {"humor": 11}},
        {"regler": {"unbekannt": 5}},
        {"modi": {"standard": {}, "kaputt": {"regler": {"humor": -1}}}},
        {"modi": {"standard": {}, "kaputt": {"regler": {"unbekannt": 5}}}},
        {"modi": {"standard": {}, "kaputt": {"werkzeuge": ["kalendar"]}}},
        {"modi": {"standard": {"werkzeuge": "keine"}}},
        {"anrede": "ihr"},
    ],
)
def test_ungueltige_konfiguration_wird_abgelehnt(overrides):
    with pytest.raises(ValidationError):
        persona(**overrides)


def test_zusatzregeln_und_modi_im_prompt():
    prompt = build_system_prompt(persona(), zusatzregeln=["Bei Timern nicht nachfragen."])
    assert "- Bei Timern nicht nachfragen." in prompt


def test_werkzeuggruppen_je_modus():
    p = persona(modi={"standard": {}, "fokus": {"werkzeuge": ["kalender"]}})
    assert p.aktive_gruppen() == frozenset(GRUPPEN), "ohne Angabe gelten alle Gruppen"
    assert p.aktive_gruppen("fokus") == {"kern", "kalender"}, "kern ist immer dabei"
    assert p.abgeschaltete_gruppen("fokus") == [g for g in GRUPPEN if g not in ("kern", "kalender")]
    assert p.abgeschaltete_gruppen() == []

    fokus = build_system_prompt(p, "fokus")
    assert "Im Modus 'fokus' sind abgeschaltet: aufgaben (To-do-Liste), kommunikation (" in fokus
    assert "sag in einem Satz, dass du das in diesem Modus nicht machst" in fokus
    assert "abgeschaltet" not in build_system_prompt(p)


def test_mitgelieferte_modi_schalten_gruppen_ab():
    p = load_persona()
    assert p.abgeschaltete_gruppen("feierabend") == ["haushalt", "finanzen", "auftraege"]
    assert p.abgeschaltete_gruppen("fokus") == ["haushalt", "medien"]
    assert p.abgeschaltete_gruppen("standard") == [] and p.abgeschaltete_gruppen("sparring") == []
    assert p.abgeschaltete_gruppen("gaeste") == [
        "kalender", "aufgaben", "kommunikation", "haushalt", "ernaehrung", "finanzen", "auftraege", "system"
    ]


def test_gaeste_modus_unterdrueckt_wissen_im_prompt():
    p = load_persona()
    prompt = build_system_prompt(p, "gaeste", wissen="Geheimes Privatwissen über Alexander.")
    assert "Geheimes Privatwissen über Alexander." not in prompt
    assert "Absolute Diskretion" in prompt
    assert "Sind die Gäste gegangen" in prompt
    # 16.10.2026: Auf „Welcher Modus?“ wich Alfred aus; nur die Begrüßung nennt den Modus nicht.
    assert "sag offen, dass du im Gästemodus bist" in prompt and "keinesfalls technisch" not in prompt
    assert f"Nachrichten für {p.nutzer} sagst du erst an, wenn der Besuch weg ist" in prompt


def test_prompt_enthaelt_wechselhinweis_fuer_gaeste():
    p = load_persona()
    prompt = build_system_prompt(p, "standard")
    assert "Sagt das Gegenüber 'wir haben Gäste'" in prompt

