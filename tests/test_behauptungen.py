import pytest

from alfred.behauptungen import (
    behauptung,
    fragt_nach_diktat,
    hinweis,
    unbelegte_behauptung,
    werkzeug_seit_dem_letzten_satz,
    zuhoer_versprechen,
)


# Was Alfred am 6.10.2026 behauptete, ohne ein Werkzeug aufzurufen, und naheliegende Varianten.
@pytest.mark.parametrize(
    "antwort",
    [
        "Alles klar, dann richte ich die Erinnerung ein: werktags um acht Uhr für die Grundlagen der Programmierung 2. "
        "Ich halte dich auf dem Laufenden.",
        "Ich sage dir sofort Bescheid, sobald eine neue Nachricht von Jan Mustermann eingeht.",
        "Alles klar, ich werde dich sofort informieren, sobald eine neue Nachricht von Jan Mustermann eingeht.",
        "Ich habe heute nichts verbraucht – keine Rechenleistung, keine Cloud-Dienste, keine externen Kosten.",
        "Ich habe keine neue Nachricht von ihm erhalten, seitdem ich dir das letzte Mal Bescheid gegeben habe.",
        "Die Routine ist angelegt.",
        "Erledigt.",
        "Gut, das merke ich mir.",
        "Ich erinnere dich morgen um acht daran.",
        "Ich setze Milch auf die Einkaufsliste.",
    ],
)
def test_behauptungen_werden_erkannt(antwort):
    assert behauptung(antwort)


# Ebenfalls aus dem Gespräch: Fragen, Möglichkeiten und Plauderei sind keine Behauptung.
@pytest.mark.parametrize(
    "antwort",
    [
        "Ich kann dir eine Push-Nachricht auf dein Telefon schicken, sobald eine neue Nachricht von Jan Mustermann "
        "eingeht. Soll ich das einrichten?",
        "Ich richte dir eine tägliche Erinnerung für die Grundlagen der Programmierung 2 ein. Ab wann soll sie starten?",
        "Spotify ist nicht erreichbar – entweder ist kein Gerät verbunden oder die App läuft nicht.",
        "Gut, dann viel Erfolg mit den Uni-Aufgaben. Wenn du noch etwas brauchst, sag einfach Bescheid.",
        "Danke der Nachfrage – ich bin einsatzbereit.",
        "Das habe ich noch nicht eingerichtet.",
        "Ich merke, dass du gerade viel zu tun hast.",
        "Ich erinnere mich an unser Gespräch von gestern.",
        "",
    ],
)
def test_anderes_ist_keine_behauptung(antwort):
    assert behauptung(antwort) is None


def test_nur_ohne_werkzeug_seit_dem_letzten_satz():
    satz = "Alles klar, dann richte ich die Erinnerung ein."
    ohne = [{"role": "user", "content": "Passt."}, {"role": "assistant", "content": satz}]
    assert unbelegte_behauptung(satz, ohne) == satz

    mit = [
        {"role": "user", "content": "Erinner mich werktags um acht."},
        {"role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "erinnerung_setzen"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "{}"},
        {"role": "assistant", "content": "Die Erinnerung ist eingerichtet."},
    ]
    assert werkzeug_seit_dem_letzten_satz(mit) is True
    assert unbelegte_behauptung("Die Erinnerung ist eingerichtet.", mit) is None

    # Antworten auf Hinweise vom System (auch auf diesen Wächter selbst) prüft er nicht: keine Schleife.
    hinweis_zuletzt = [*ohne, {"role": "developer", "content": hinweis(satz)}, {"role": "assistant", "content": "Erledigt."}]
    assert werkzeug_seit_dem_letzten_satz(hinweis_zuletzt) is None
    assert unbelegte_behauptung("Erledigt.", hinweis_zuletzt) is None


def test_hinweis_zitiert_den_satz():
    text = hinweis("Ich merke mir das.")
    assert text.startswith("Hinweis vom System") and "„Ich merke mir das.“" in text and "kein Werkzeug" in text


@pytest.mark.parametrize(
    "antwort",
    ["Alles klar, ich halte mich zurück. Wenn du fertig bist, sag einfach Bescheid.",
     "Du hast recht. Ich warte jetzt einfach, bis du deinen Satz beendet hast.",
     "Aber ich höre jetzt zu und unterbreche nicht mehr.", "Verstanden, ich halte mich zurück.", "Ich bin ganz Ohr."],
)  # fmt: skip
def test_zuhoer_versprechen_wird_erkannt(antwort):
    assert zuhoer_versprechen(antwort)


@pytest.mark.parametrize("antwort", ["Hörst du mir zu?", "Ich warte auf den Bericht.", "Stimmt, das ist ein Systemlimit.", ""])
def test_anderes_ist_kein_zuhoer_versprechen(antwort):
    assert not zuhoer_versprechen(antwort)


@pytest.mark.parametrize(
    "antwort",
    ["Worauf soll die Nachricht lauten?", "Was soll ich Jan Mustermann schreiben?", "Welche Nachricht soll ich Jan schicken?",
     "Alles klar. Wie soll die Nachricht an Mama lauten?", "Was soll ich ihm ausrichten?"],
)  # fmt: skip
def test_frage_nach_dem_wortlaut_heisst_diktat(antwort):
    assert fragt_nach_diktat(antwort)


@pytest.mark.parametrize("antwort", ["Soll ich die Nachricht Hi an Jan schicken?", "Was steht heute an?", "Ich schreibe Jan."])
def test_andere_fragen_sind_kein_diktat(antwort):
    assert not fragt_nach_diktat(antwort)
