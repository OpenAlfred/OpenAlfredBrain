from alfred.einkauf import Einkaufsliste, abteilung_fuer, artikel_trennen


def test_artikel_trennen_und_mengen():
    assert artikel_trennen("2 Brot, Milch und 500 g Hackfleisch") == [("2", "Brot"), ("", "Milch"), ("500 g", "Hackfleisch")]
    assert artikel_trennen("Äpfel; 1,5 l Wasser.") == [("", "Äpfel"), ("1,5 l", "Wasser")]
    assert artikel_trennen("  ") == []


def test_abteilungen():
    assert abteilung_fuer("Äpfel") == "Obst & Gemüse"
    assert abteilung_fuer("Hafermilch") == "Kühlregal"
    assert abteilung_fuer("Klopapier") == "Haushalt & Drogerie"
    assert abteilung_fuer("Gummibärchen") == "Sonstiges"


def test_liste_fuellen_abhaken_leeren_und_nachladen(tmp_path):
    pfad = tmp_path / "einkauf.yaml"
    liste = Einkaufsliste(pfad)
    neue = liste.hinzufuegen("2 Brot, Milch und Äpfel")
    assert [a.ansage() for a in neue] == ["2 Brot", "Milch", "Äpfel"]
    assert liste.hinzufuegen("milch") == []  # schon drauf, kein Doppel
    assert list(liste.nach_abteilung()) == ["Obst & Gemüse", "Brot & Backwaren", "Kühlregal"]

    assert liste.abhaken("Milch").erledigt is True
    assert [a.name for a in liste.offene()] == ["Brot", "Äpfel"]
    assert liste.abhaken("Milch", erledigt=False).erledigt is False
    assert liste.abhaken("Mi").name == "Milch"  # Namensteil reicht, wenn er eindeutig ist
    assert liste.abhaken("Kaviar") is None
    assert liste.entfernen("Äpfel").name == "Äpfel"

    # Ein zweiter Prozess ändert die Datei: Der erste sieht es beim nächsten Zugriff.
    andere = Einkaufsliste(pfad)
    andere.hinzufuegen("Butter")
    assert [a.name for a in liste.offene()] == ["Brot", "Butter"]

    liste.abhaken("Brot")
    assert liste.leeren() == 2 and [a.name for a in liste.alle()] == ["Butter"]
    assert liste.leeren(nur_erledigte=False) == 1 and liste.alle() == []
