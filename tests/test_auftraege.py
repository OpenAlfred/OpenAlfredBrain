"""Aufträge an Alfred: Liste, Bestätigung und der Bearbeiter, mit falschem Denker statt Mistral."""

import asyncio
from datetime import datetime, timedelta

from alfred.auftraege import Auftraege, Auftragsbearbeiter, bestaetigung, kurzfassung, kurztitel
from alfred.ereignisse import Ansage, Fremd

BERICHT = """# Wärmepumpe oder Gasheizung
## Kurzfassung
Für dein Haus lohnt sich die **Wärmepumpe** [1], wenn die Vorlauftemperatur passt. Mehr dazu bei [Verbraucherzentrale](https://www.verbraucherzentrale.de) [2].
## Ergebnis
- Betriebskosten im Vergleich [1]
## Hinweise
- Vorher einen Energieberater fragen.
## Quellen
1. [Verbraucherzentrale](https://www.verbraucherzentrale.de)
"""


class Uhr:
    def __init__(self):
        self.jetzt = datetime(2026, 10, 5, 9, 0).astimezone()

    def __call__(self):
        return self.jetzt


class FakeDenker:
    def __init__(self, fehler_bei=(), dauer=0.0):
        self.auftraege = []
        self.fehler_bei = set(fehler_bei)
        self.dauer = dauer

    async def bericht(self, auftrag, kontext="", heute=""):
        self.auftraege.append((auftrag, heute))
        await asyncio.sleep(self.dauer)
        if auftrag in self.fehler_bei:
            raise RuntimeError("Mistral antwortet nicht")
        return BERICHT


class FakePush:
    def __init__(self):
        self.gesendet = []

    async def senden(self, text, titel="Alfred", prioritaet="normal", aktionen=None, klick=""):
        self.gesendet.append((titel, text, klick))


def liste(tmp_path, clock=None) -> Auftraege:
    return Auftraege(tmp_path / "auftraege.yaml", tmp_path / "berichte", clock=clock)


def test_kurzfassung_ist_vorlesbar():
    assert kurzfassung(BERICHT) == (
        "Für dein Haus lohnt sich die Wärmepumpe, wenn die Vorlauftemperatur passt. Mehr dazu bei Verbraucherzentrale."
    )
    assert kurzfassung("Nur ein Absatz ohne Gliederung.\n\nZweiter.") == "Nur ein Absatz ohne Gliederung."
    lang = "## Kurzfassung\n" + "Ein Satz, der lang ist. " * 40
    assert kurzfassung(lang).endswith("ist.") and len(kurzfassung(lang)) <= 450
    # Punkte hinter Zahlen und Kürzeln sind kein Satzende (so brach es einmal mitten im Satz ab).
    zahl = (
        "## Kurzfassung\n" + "Erster Satz mit Inhalt. " * 16
        + "Heute jährt sich, z. B. für Apple, sein Tod zum 15. Mal und noch viel mehr Text, der weitergeht und weitergeht."
    )
    assert len(zahl) > 480, "der Schnitt muss hinter 'zum 15.' liegen"
    assert kurzfassung(zahl).endswith("mit Inhalt.") and "zum 15" not in kurzfassung(zahl)


def test_kurztitel_kuerzt_an_einer_wortgrenze():
    assert kurztitel("  Vergleiche   Wärmepumpe und Gasheizung ") == "Vergleiche Wärmepumpe und Gasheizung"
    titel = kurztitel("Recherchiere bitte ausführlich " * 10)
    assert len(titel) <= 82 and titel.endswith(" …") and "  " not in titel


def test_bestaetigung_nennt_jeden_auftrag_und_wie_alfred_sich_meldet(tmp_path):
    auftraege = liste(tmp_path)
    eins = auftraege.anlegen("Wärmepumpe oder Gasheizung, Bericht mit Quellen.", "Vergleiche …")
    zwei = auftraege.anlegen("Tech-News von heute", "Such die Tech-News von heute")
    assert bestaetigung([eins], 0, True) == (
        "Verstanden, ich übernehme: Wärmepumpe oder Gasheizung, Bericht mit Quellen. Sobald ich fertig bin, "
        "schicke ich dir eine Nachricht aufs Handy, den Bericht findest du in der App."
    )
    beide = bestaetigung([eins, zwei], 1, None)
    assert beide.startswith(
        "Verstanden, zwei Aufträge. Erstens: Wärmepumpe oder Gasheizung, Bericht mit Quellen. Zweitens: Tech-News "
        "von heute. Ich arbeite sie nacheinander ab. Vorher erledige ich noch einen anderen Auftrag."
    )
    assert "Push ist nicht eingerichtet" in beide and "Sobald einer fertig ist" in beide
    assert "Handy" not in bestaetigung([eins], 0, False)


def test_liste_teilen_sich_prozesse_und_verwaiste_auftraege_beginnen_neu(tmp_path):
    uhr = Uhr()
    auftraege = liste(tmp_path, uhr)
    auftraege.anlegen("Tech-News", "Tech-News von heute")
    assert auftraege.beginnen(1).versuche == 1
    assert auftraege.beginnen(1) is None, "schon in Arbeit"
    # Ein Neustart (oder die App) sieht denselben Stand.
    neu = liste(tmp_path, uhr)
    assert neu.holen(1).status == "laeuft" and neu.naechster() is None
    uhr.jetzt += timedelta(minutes=2)
    assert neu.verwaiste_zuruecksetzen(timedelta(minutes=3)) == []
    uhr.jetzt += timedelta(minutes=2)
    assert [a.nummer for a in neu.verwaiste_zuruecksetzen(timedelta(minutes=3))] == [1]
    assert liste(tmp_path, uhr).naechster().nummer == 1


def test_beendete_verschwinden_nach_30_tagen_die_berichte_bleiben(tmp_path):
    uhr = Uhr()
    auftraege = liste(tmp_path, uhr)
    auftrag = auftraege.beginnen(auftraege.anlegen("Alt").nummer)
    auftraege.abschliessen(1, auftraege.bericht_speichern(auftrag, BERICHT), "kurz")
    uhr.jetzt += timedelta(days=31)
    auftraege.anlegen("Neu")
    assert [a.titel for a in auftraege.alle()] == ["Neu"] and auftraege.alle()[0].nummer == 2
    assert len(list((tmp_path / "berichte").glob("*.md"))) == 1


def test_bericht_lesen_bleibt_im_berichtsordner(tmp_path):
    auftraege = liste(tmp_path)
    auftrag = auftraege.anlegen("Neugierig")
    (tmp_path / "geheim.txt").write_text("nicht lesen", encoding="utf-8")
    auftrag.bericht = "../geheim.txt"
    assert auftraege.bericht_lesen(auftrag) is None


def test_bearbeiter_arbeitet_der_reihe_nach_und_meldet_sich(tmp_path):
    auftraege = liste(tmp_path)
    auftraege.anlegen("Wärmepumpe oder Gasheizung", "Vergleiche Wärmepumpe und Gasheizung")
    auftraege.anlegen("Kaputt", "kaputt")
    auftraege.anlegen("Tech-News von heute", "Tech-News", aufs_handy=False)
    denker, push = FakeDenker(fehler_bei={"kaputt"}), FakePush()

    async def lauf():
        ereignisse = asyncio.Queue()
        bearbeiter = Auftragsbearbeiter(auftraege, denker, ereignisse, push, "https://alfred.example/app/", takt=0.01)
        aufgabe = asyncio.create_task(bearbeiter.laufen())
        ansagen = [await asyncio.wait_for(ereignisse.get(), 2) for _ in range(3)]
        aufgabe.cancel()
        await asyncio.gather(aufgabe, return_exceptions=True)
        return ansagen

    ereignisse = asyncio.run(lauf())
    # Die Kurzfassung eines fertigen Berichts ist fremder Text: Der Zusteller setzt dafür die Marke.
    assert [type(e) for e in ereignisse] == [Fremd, Ansage, Fremd]
    assert ereignisse[0].herkunft == "Kurzfassung des Berichts 'Wärmepumpe oder Gasheizung'"
    ansagen = [e.ereignis if isinstance(e, Fremd) else e for e in ereignisse]
    assert all(isinstance(a, Ansage) and a.klingeln for a in ansagen)
    assert ansagen[0].text.startswith("Auftrag erledigt: Wärmepumpe oder Gasheizung. Für dein Haus lohnt sich die Wärmepumpe")
    assert ansagen[0].text.endswith("Den ganzen Bericht findest du in der App.")
    assert ansagen[1].text == "Den Auftrag Kaputt konnte ich nicht erledigen: Mistral antwortet nicht."
    assert ansagen[2].text.startswith("Auftrag erledigt: Tech-News von heute.")
    assert [a.status for a in auftraege.alle()] == ["fertig", "fehler", "fertig"]
    eins = auftraege.holen(1)
    assert eins.bericht.endswith("-1-waermepumpe-oder-gasheizung.md") and auftraege.bericht_lesen(eins).startswith("# Wärmepumpe")
    assert eins.kurzfassung.startswith("Für dein Haus")
    # Push für die ersten beiden; der dritte wollte keinen. Ein Tippen öffnet den Auftrag in der App.
    assert [(titel, klick) for titel, _, klick in push.gesendet] == [
        ("Alfred: Auftrag erledigt", "https://alfred.example/app/#auftrag-1"),
        ("Alfred: Auftrag gescheitert", "https://alfred.example/app/#auftrag-2"),
    ]
    assert push.gesendet[0][1].startswith("Wärmepumpe oder Gasheizung\n\nFür dein Haus")
    assert [a for a, _ in denker.auftraege] == ["Vergleiche Wärmepumpe und Gasheizung", "kaputt", "Tech-News"]
    assert denker.auftraege[0][1].count(".") >= 2, "der Denker bekommt das heutige Datum"


def test_abgebrochener_auftrag_endet_ohne_bericht_und_ohne_ansage(tmp_path):
    auftraege = liste(tmp_path)
    auftraege.anlegen("Lange Recherche", "lang")

    async def lauf():
        ereignisse = asyncio.Queue()
        bearbeiter = Auftragsbearbeiter(auftraege, FakeDenker(dauer=5), ereignisse, takt=0.01)
        arbeit = asyncio.create_task(bearbeiter.bearbeiten(1))
        await asyncio.sleep(0.05)
        auftraege.abbrechen(1)
        await asyncio.wait_for(arbeit, 1)
        return ereignisse.qsize()

    assert asyncio.run(lauf()) == 0
    assert auftraege.holen(1).status == "abgebrochen" and not list(tmp_path.glob("berichte/*.md"))


def test_beim_beenden_wartet_der_laufende_auftrag_wieder(tmp_path):
    auftraege = liste(tmp_path)
    auftraege.anlegen("Lange Recherche", "lang")

    async def lauf():
        bearbeiter = Auftragsbearbeiter(auftraege, FakeDenker(dauer=5), asyncio.Queue(), takt=0.01)
        aufgabe = asyncio.create_task(bearbeiter.laufen())
        await asyncio.sleep(0.05)
        aufgabe.cancel()
        await asyncio.gather(aufgabe, return_exceptions=True)

    asyncio.run(lauf())
    auftrag = auftraege.holen(1)
    assert auftrag.status == "wartet" and auftrag.versuche == 0, "ein sauberes Ende zählt nicht als Versuch"


def test_nach_drei_abstuerzen_gibt_alfred_auf(tmp_path):
    uhr = Uhr()
    auftraege = liste(tmp_path, uhr)
    auftraege.anlegen("Absturz", "x")
    for _ in range(3):  # dreimal begonnen, der Prozess starb jedes Mal
        auftraege.beginnen(1)
        uhr.jetzt += timedelta(minutes=10)
        auftraege.verwaiste_zuruecksetzen(timedelta(minutes=3))

    async def lauf():
        ereignisse = asyncio.Queue()
        await Auftragsbearbeiter(auftraege, FakeDenker(), ereignisse, takt=0.01).bearbeiten(1)
        return ereignisse.get_nowait()

    ansage = asyncio.run(lauf())
    assert "nach 3 abgebrochenen versuchen" in ansage.text.casefold() and auftraege.holen(1).status == "fehler"


def test_gesperrtes_lebenszeichen_beendet_den_auftrag_nicht(tmp_path, monkeypatch):
    # Unter Windows scheitert das Speichern, solange die App die Liste liest (docs/erkenntnisse.md, 16.10.2026). Der
    # Auftrag und seine teure Recherche laufen weiter; das Lebenszeichen holt der nächste Takt nach.
    auftraege = liste(tmp_path)
    auftraege.anlegen("Lange Recherche", "lang")
    echt = auftraege.lebenszeichen_geben
    versuche = []

    def gesperrt(nummer):
        versuche.append(nummer)
        if len(versuche) < 3:
            raise PermissionError("gesperrt")
        return echt(nummer)

    monkeypatch.setattr(auftraege, "lebenszeichen_geben", gesperrt)

    async def lauf():
        ereignisse = asyncio.Queue()
        bearbeiter = Auftragsbearbeiter(auftraege, FakeDenker(dauer=0.4), ereignisse, takt=0.01, lebenszeichen_secs=0.02)
        await bearbeiter.bearbeiten(1)
        return ereignisse

    ereignisse = asyncio.run(lauf())
    assert auftraege.holen(1).status == "fertig" and ereignisse.qsize() == 1
    assert len(versuche) >= 3, "nach dem Fehlschlag wurde das Lebenszeichen erneut versucht"
