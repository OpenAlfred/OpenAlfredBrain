from datetime import datetime, timedelta, timezone

import pytest

from alfred.aufgaben import Aufgaben

TZ = timezone(timedelta(hours=2))
HEUTE = datetime(2026, 10, 3, 9, 0, tzinfo=TZ)


class Uhr:
    def __init__(self, jetzt):
        self.jetzt = jetzt

    def __call__(self):
        return self.jetzt


def test_anlegen_anzeigen_erledigen_und_wiederfinden(tmp_path):
    pfad = tmp_path / "aufgaben.yaml"
    aufgaben = Aufgaben(pfad, Uhr(HEUTE))
    a = aufgaben.anlegen("Steuererklärung abschicken", "2026-10-15")
    b = aufgaben.anlegen("Wäsche aufhängen")
    c = aufgaben.anlegen("Zahnarzt anrufen", "2026-10-06")

    assert [x.nummer for x in aufgaben.offene()] == [3, 1, 2]  # nächste Fälligkeit zuerst, dann Reihenfolge
    assert a.ansage() == "1: Steuererklärung abschicken, fällig am 15.10."
    assert b.ansage() == "2: Wäsche aufhängen"

    assert aufgaben.finden("Wäsche") == [b]
    assert aufgaben.finden("anrufen zahnarzt") == [c]
    assert aufgaben.finden("xyz") == []

    assert aufgaben.erledigen(2).text == "Wäsche aufhängen"
    assert aufgaben.erledigen(2) is None and aufgaben.erledigen(99) is None
    assert [x.nummer for x in Aufgaben(pfad).offene()] == [3, 1]

    prompt = aufgaben.fuer_prompt()
    assert prompt.startswith("Offene Aufgaben") and "- 3: Zahnarzt anrufen, fällig am 06.10." in prompt
    assert "Wäsche" not in prompt


def test_leere_liste_und_fehler(tmp_path):
    aufgaben = Aufgaben(tmp_path / "a.yaml")
    assert aufgaben.offene() == [] and aufgaben.fuer_prompt() == ""
    with pytest.raises(ValueError):
        aufgaben.anlegen("  ")
    with pytest.raises(ValueError):
        aufgaben.anlegen("x", "morgen")


def test_erledigte_verschwinden_nach_dreissig_tagen(tmp_path):
    uhr = Uhr(HEUTE)
    pfad = tmp_path / "a.yaml"
    aufgaben = Aufgaben(pfad, uhr)
    aufgaben.anlegen("alt")
    aufgaben.erledigen(1)
    aufgaben.anlegen("neu")

    uhr.jetzt = HEUTE + timedelta(days=31)
    aufgaben.anlegen("noch eine")  # speichert und dünnt aus
    texte = [a.text for a in Aufgaben(pfad)._eintraege]
    assert texte == ["neu", "noch eine"]
