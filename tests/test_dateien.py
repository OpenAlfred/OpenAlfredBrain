"""Der Sprach-Prozess und die App haben je eine eigene Instanz auf derselben Bestandsdatei."""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from loguru import logger

import alfred
from alfred import dateien
from alfred.dateien import Dateibestand
from alfred.einkauf import Einkaufsliste
from alfred.erinnerungen import Erinnerungen
from alfred.whatsapp import Meldeliste

TZ = timezone(timedelta(hours=2))
JETZT = datetime(2026, 10, 16, 9, 0, tzinfo=TZ)


class Liste(Dateibestand):
    """Der kleinste Bestand: Wörter, geschrieben wie die echten (Kommentar, dann YAML)."""

    def __init__(self, pfad):
        self.woerter: list[str] = []
        super().__init__(pfad)

    def _laden(self, daten) -> None:
        self.woerter = [str(eintrag["wort"]) for eintrag in (daten or [])]

    def _speichern(self) -> None:
        self._schreiben("# Testliste\n" + yaml.safe_dump([{"wort": w} for w in self.woerter]))

    def alle(self) -> list[str]:
        self._auffrischen()
        return list(self.woerter)

    def hinzufuegen(self, wort: str) -> None:
        self._auffrischen()
        self.woerter.append(wort)
        self._speichern()

    def setzen(self, woerter: list[str]) -> None:
        self._auffrischen()
        self.woerter = list(woerter)
        self._speichern()


@pytest.fixture(autouse=True)
def ohne_pausen(monkeypatch):
    monkeypatch.setattr(dateien, "PAUSE_SECS", 0, raising=False)


@contextmanager
def warnungen():
    meldungen: list[str] = []
    senke = logger.add(lambda m: meldungen.append(m.record["message"]), level="WARNING")
    try:
        yield meldungen
    finally:
        logger.remove(senke)


def nur_die_datei(ordner: Path, name: str = "liste.yaml") -> bool:
    """Nebendateien bleiben nie liegen."""
    return [p.name for p in ordner.iterdir()] == [name]


def gesperrt(*_):
    raise PermissionError("gesperrt")


def von_hand_schreiben(pfad: Path, inhalt: bytes) -> None:
    """Ein anderer Schreiber ersetzt den Inhalt, spürbar später als der letzte Stand: Der Zeitstempel unterscheidet sich
    sicher. Zwei Schreibvorgänge im selben Takt der Uhr haben denselben (dafür gibt es einen eigenen Test); ohne diesen
    Helfer hinge es vom Takt ab, ob der alte Stand die Änderung bemerkt und diese Tests scheitern."""
    spaeter = pfad.stat().st_mtime_ns + 5_000_000_000
    pfad.write_bytes(inhalt)
    os.utime(pfad, ns=(spaeter, spaeter))


def nach_dem_schreiben(monkeypatch, pfad: Path, aktion) -> None:
    """Führt `aktion` einmal aus, sobald die Fassung dieser Seite die Datei ist: nach dem Umbenennen (im
    alten Stand: nach dem Schreiben an Ort und Stelle), also bevor diese Seite sich den Stand merkt."""
    echt_ersetzen, echt_schreiben = os.replace, Path.write_text
    getan = []

    def einmal():
        if not getan:
            getan.append(1)
            aktion()

    def ersetzen(quelle, ziel):
        echt_ersetzen(quelle, ziel)
        if Path(ziel) == pfad:
            einmal()

    def schreiben(self, *args, **kwargs):
        ergebnis = echt_schreiben(self, *args, **kwargs)
        if self == pfad:
            einmal()
        return ergebnis

    monkeypatch.setattr(os, "replace", ersetzen)
    monkeypatch.setattr(Path, "write_text", schreiben)


# --- Zwei Instanzen auf derselben Datei ---


def test_zwei_instanzen_speichern_abwechselnd_und_sehen_alles(tmp_path):
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    with warnungen() as meldungen:
        for i in range(30):
            raum.hinzufuegen(f"R{i}")
            app.hinzufuegen(f"A{i}")
    erwartet = [wort for i in range(30) for wort in (f"R{i}", f"A{i}")]
    assert raum.alle() == app.alle() == Liste(pfad).alle() == erwartet
    assert meldungen == []  # Wer abwechselt, überschreibt nie eine Fassung, die er nicht kennt
    assert nur_die_datei(tmp_path)


def test_gleicher_zeitstempel_und_gleiche_groesse_taeuschen_nicht(tmp_path):
    # Zwei Schreibvorgänge im selben Takt der Uhr haben denselben Zeitstempel; unter Windows blieb beim
    # Überschreiben an Ort und Stelle bei einem großen Teil schneller Schreibvorgänge alles gleich.
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.setzen(["Milch"])
    zeitstempel, groesse = pfad.stat().st_mtime_ns, pfad.stat().st_size
    assert app.alle() == ["Milch"]

    app.setzen(["Brote"])  # gleich lang, anderer Inhalt
    os.utime(pfad, ns=(zeitstempel, zeitstempel))
    assert (pfad.stat().st_mtime_ns, pfad.stat().st_size) == (zeitstempel, groesse)
    assert raum.alle() == ["Brote"]


def test_die_fassung_der_anderen_seite_dazwischen_gilt_nicht_als_die_eigene(tmp_path, monkeypatch):
    # Der Stand der eigenen Fassung wurde erst NACH dem Schreiben genommen. Hatte die andere Seite
    # dazwischen gespeichert, galt ihre Fassung als die eigene und wurde nie geladen.
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    assert app.alle() == ["Milch"]

    with monkeypatch.context() as m:
        nach_dem_schreiben(m, pfad, lambda: app.hinzufuegen("Brot"))
        raum.hinzufuegen("Butter")

    assert Liste(pfad).alle() == ["Milch", "Butter", "Brot"]
    assert raum.alle() == ["Milch", "Butter", "Brot"]


def test_laedt_nur_neu_wenn_sich_die_datei_geaendert_hat(tmp_path, monkeypatch):
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    lesungen = []

    for liste in (raum, app):
        def zaehlend(daten, echt=liste._laden):
            lesungen.append(1)
            echt(daten)

        monkeypatch.setattr(liste, "_laden", zaehlend)
    app.alle()
    app.alle()
    assert len(lesungen) == 1  # beim ersten Mal hat sich etwas geändert, beim zweiten nicht
    app.hinzufuegen("Brot")
    app.alle()
    assert len(lesungen) == 1  # die eigene Fassung ist gemerkt, sie wird nicht noch einmal gelesen
    assert raum.alle() == ["Milch", "Brot"]
    assert len(lesungen) == 2


def test_umbenennen_aendert_die_kennung_nicht(tmp_path):
    # Darauf beruht, dass die eigene Fassung an der Nebendatei VOR dem Umbenennen erkannt wird.
    ziel, vorlaeufig = tmp_path / "datei", tmp_path / "datei.tmp"
    ziel.write_text("alt", encoding="utf-8")
    vorlaeufig.write_text("neu", encoding="utf-8")
    alt, neu = dateien.stand_von(ziel), dateien.stand_von(vorlaeufig)
    assert alt != neu
    os.replace(vorlaeufig, ziel)
    assert dateien.stand_von(ziel) == neu


# --- Der Lader ---


def test_geladen_wird_mit_libyaml_wo_es_das_gibt(tmp_path, monkeypatch):
    # Mit libyaml ist das Nachladen rund sechsmal schneller (70 Artikel: 3,0 statt 18,7 ms). Das halbiert das Zeitfenster
    # zwischen Nachladen und Speichern und verkürzt die Zeit, die der Ereignis-Loop beim Nachladen steht.
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    lader = []
    echt = yaml.load

    def merken(stream, Loader=None):
        lader.append(Loader)
        return echt(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", merken)
    assert app.alle() == ["Milch"]
    assert lader == [yaml.CSafeLoader if yaml.__with_libyaml__ else yaml.SafeLoader]


# Zeichenketten, die ein Bestand schreiben muss, ohne dass sie beim Lesen etwas anderes werden.
HEIKLE_ZEICHENKETTEN = [
    "Milch", "Äpfel & Birnen", "🥛 Milch", "ß", "yes", "no", "null", "~", "true", "off", "1e3", "0x10", "1_000", "2026-10-16", "12:30",
    ": ", "- ", "a: b", "# kein Kommentar", "x #y", "'einfach'", '"doppelt"', "mit\nZeilenumbruch", "mit\ttab", " führend", "folgend ",
    "", "x" * 200, " LS", "\x85NEL", "<<", "*alias", "&anker", "!tag", "%direktive", "|", ">", "?", "{", "[", ",", "---", "...",
]


def ergebnis(text: str, lader):
    try:
        return "gelesen", yaml.load(text, Loader=lader)
    except Exception:  # noqa: BLE001 - wie in _auffrischen gilt jeder Fehler als unlesbar
        return "fehler", None


def test_libyaml_liest_dasselbe_wie_pyyaml_in_python():
    # Texte, wie die Bestände sie schreiben: yaml.safe_dump (Aufgaben, Einkaufsliste, Routinen ...) und json.dumps (Erinnerungen).
    abweichungen = []
    for wort in HEIKLE_ZEICHENKETTEN:
        eintraege = [{"name": wort, "menge": wort, "erledigt": False}]
        for art, text in (
            ("yaml", "# Alfreds Einkaufsliste.\n" + yaml.safe_dump(eintraege, allow_unicode=True, sort_keys=False)),
            ("json", json.dumps(eintraege, ensure_ascii=False, indent=2)),
        ):
            if ergebnis(text, dateien.LADER) != ergebnis(text, yaml.SafeLoader):
                abweichungen.append((art, wort))
    assert abweichungen == []


def test_libyaml_und_pyyaml_beurteilen_halbe_dateien_gleich():
    # So sieht ein Leser, was ein anderer Schreiber gerade schreibt: jeden Anfang einer echten Datei. Beide Lader müssen
    # ihn gleich beurteilen (Fehler oder dieselben Daten), sonst gälte eine lesbare Datei als unlesbar oder umgekehrt.
    eintraege = [{"nummer": i, "text": f"Wäsche {i}: äöü # x", "faellig": "2026-10-16T09:30:00+02:00", "wochentage": [0, 1] if i else None}
                 for i in range(3)]
    for text in ("# Alfreds Routinen.\n" + yaml.safe_dump(eintraege, allow_unicode=True, sort_keys=False),
                 json.dumps(eintraege, ensure_ascii=False, indent=2)):
        abweichungen = [n for n in range(len(text) + 1) if ergebnis(text[:n], dateien.LADER) != ergebnis(text[:n], yaml.SafeLoader)]
        assert abweichungen == []


# --- Halbe und leere Dateien ---

# Was ein anderer Schreiber hinterlässt, der mittendrin war, oder ein Mensch, der die Datei von Hand kaputt macht.
UNLESBAR = [
    pytest.param(b"", id="leer"),
    pytest.param(b"\n\n", id="nur-leerzeilen"),
    pytest.param(b"# Alfreds Einkaufsliste. Von Hand aenderbar.\n", id="nur-kommentar"),
    pytest.param(b"# Alfreds Einkaufsliste\n- wort: 'Mil", id="offenes-anfuehrungszeichen"),
    pytest.param(b"[{", id="offene-klammern"),
    pytest.param(b"\xff\xfe\x00", id="kein-utf8"),
    pytest.param(b"geheim", id="kein-bestand"),
    pytest.param(b"wort: Milch\n", id="eintrag-statt-liste"),
]


@pytest.mark.parametrize("inhalt", UNLESBAR)
def test_unlesbare_datei_wirft_nicht_und_der_speicher_bleibt(tmp_path, inhalt):
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    assert app.alle() == ["Milch"]

    von_hand_schreiben(pfad, inhalt)
    assert app.alle() == ["Milch"]  # kein Wurf und kein leerer Bestand: es gilt der Stand im Speicher
    app.hinzufuegen("Brot")  # zählt darauf weiter und schreibt wieder eine ganze Datei
    assert Liste(pfad).alle() == raum.alle() == ["Milch", "Brot"]
    if inhalt:  # was in der Datei stand, bleibt daneben liegen
        assert (tmp_path / "liste.yaml.defekt").read_bytes() == inhalt
    assert sorted(p.name for p in tmp_path.iterdir()) == ["liste.yaml"] + (["liste.yaml.defekt"] if inhalt else [])


@pytest.mark.parametrize("inhalt", UNLESBAR)
def test_unlesbare_datei_beim_start_stoppt_nichts(tmp_path, inhalt):
    pfad = tmp_path / "liste.yaml"
    pfad.write_bytes(inhalt)
    liste = Liste(pfad)
    assert liste.alle() == []  # beim Start gibt es nichts anderes als den leeren Bestand
    liste.hinzufuegen("Milch")
    assert Liste(pfad).alle() == ["Milch"]
    if inhalt:
        assert (tmp_path / "liste.yaml.defekt").read_bytes() == inhalt


def test_halb_geschriebene_datei_wird_beim_naechsten_mal_wieder_gelesen(tmp_path):
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    app.alle()

    von_hand_schreiben(pfad, b"# Testliste\n- wort: 'Mil")  # ein alter Schreiber, mittendrin
    assert app.alle() == ["Milch"]
    raum.hinzufuegen("Butter")  # der Raum liest dasselbe Bruchstück, behält seinen Speicher und schreibt ganz
    assert app.alle() == ["Milch", "Butter"]  # die App versucht es erneut und findet die Datei heil


def test_unlesbare_datei_steht_nur_einmal_im_log(tmp_path):
    pfad = tmp_path / "liste.yaml"
    liste = Liste(pfad)
    liste.hinzufuegen("Milch")
    with warnungen() as meldungen:
        von_hand_schreiben(pfad, b"[{")
        for _ in range(5):
            liste.alle()
    assert len(meldungen) == 1 and "liste.yaml" in meldungen[0]


def test_geloeschte_datei_geht_nicht_verloren(tmp_path):
    pfad = tmp_path / "liste.yaml"
    liste = Liste(pfad)
    liste.hinzufuegen("Milch")
    pfad.unlink()
    assert liste.alle() == ["Milch"]  # etwa mitten in einer Wiederherstellung
    liste.hinzufuegen("Brot")
    assert Liste(pfad).alle() == ["Milch", "Brot"]


def test_datei_die_es_nie_gab_ist_ein_leerer_bestand(tmp_path):
    pfad = tmp_path / "neu" / "liste.yaml"
    liste = Liste(pfad)
    assert liste.alle() == [] and not pfad.parent.exists()
    liste.hinzufuegen("Milch")
    assert Liste(pfad).alle() == ["Milch"] and nur_die_datei(pfad.parent)


def test_meldeliste_mit_falschem_aufbau_aendert_nichts_halb(tmp_path):
    pfad = tmp_path / "melden.yaml"
    liste = Meldeliste(pfad)
    liste.setzen(None, None, True)
    von_hand_schreiben(pfad, b"alle: false\nkontakte: abc\n")  # "alle" ließe sich lesen, "kontakte" nicht
    assert liste.leer is False and liste.alle is True


# --- Gesperrte Datei und gescheitertes Umbenennen (unter Windows, solange die andere Seite die Datei liest) ---


def test_gespeichert_wird_ueber_eine_nebendatei(tmp_path, monkeypatch):
    pfad = tmp_path / "liste.yaml"
    liste = Liste(pfad)
    liste.hinzufuegen("Milch")
    assert nur_die_datei(tmp_path)

    # Beim Umbenennen ist die Nebendatei fertig, und die Datei hat noch ihren alten, ganzen Inhalt.
    beim_umbenennen = []
    echt = os.replace

    def pruefen(quelle, ziel):
        inhalt = [[w["wort"] for w in yaml.safe_load(Path(p).read_text(encoding="utf-8"))] for p in (quelle, ziel)]
        beim_umbenennen.append((Path(quelle).name, *inhalt))
        echt(quelle, ziel)

    monkeypatch.setattr(os, "replace", pruefen)
    liste.hinzufuegen("Brot")
    assert beim_umbenennen == [(f"liste.yaml.{os.getpid()}.tmp", ["Milch", "Brot"], ["Milch"])]
    assert nur_die_datei(tmp_path)


def test_umbenennen_wird_wiederholt_solange_die_datei_gesperrt_ist(tmp_path, monkeypatch):
    pfad = tmp_path / "liste.yaml"
    liste = Liste(pfad)
    liste.hinzufuegen("Milch")
    versuche = []
    echt = os.replace

    def zuerst_gesperrt(quelle, ziel):
        versuche.append(1)
        if len(versuche) < dateien.SCHREIB_VERSUCHE:
            raise PermissionError("gesperrt")
        echt(quelle, ziel)

    with monkeypatch.context() as m:
        m.setattr(os, "replace", zuerst_gesperrt)
        liste.hinzufuegen("Brot")  # kein Fehler, nichts geht verloren
    assert len(versuche) == dateien.SCHREIB_VERSUCHE
    assert Liste(pfad).alle() == liste.alle() == ["Milch", "Brot"] and nur_die_datei(tmp_path)


def test_dauerhaft_gesperrte_datei_geht_als_fehler_an_den_aufrufer(tmp_path, monkeypatch):
    # Der Aufrufer hat den Speicher schon geändert, bevor er speichert. Ein stilles Verschlucken würde
    # Eingaben verlieren, und der Speicher zeigte danach etwas, das in keiner Datei steht.
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    vorher = pfad.read_bytes()

    versuche = []
    with monkeypatch.context() as m:
        m.setattr(os, "replace", lambda *a: versuche.append(1) or gesperrt())
        with pytest.raises(PermissionError):
            raum.hinzufuegen("Brot")
    assert len(versuche) == dateien.SCHREIB_VERSUCHE
    assert pfad.read_bytes() == vorher and nur_die_datei(tmp_path)
    assert raum.alle() == ["Milch"]  # der Speicher kehrt zur Datei zurück, kein "Brot" ohne Datei dahinter

    raum.hinzufuegen("Butter")  # danach geht es wieder, ohne das gescheiterte Brot
    assert app.alle() == Liste(pfad).alle() == ["Milch", "Butter"]


def test_andere_fehler_beim_umbenennen_werden_nicht_wiederholt(tmp_path, monkeypatch):
    pfad = tmp_path / "liste.yaml"
    liste = Liste(pfad)
    liste.hinzufuegen("Milch")
    versuche = []

    def voll(*_):
        versuche.append(1)
        raise OSError(28, "Auf dem Gerät ist kein Speicherplatz mehr verfügbar")

    monkeypatch.setattr(os, "replace", voll)
    with pytest.raises(OSError, match="Speicherplatz"):
        liste.hinzufuegen("Brot")
    assert len(versuche) == 1 and nur_die_datei(tmp_path) and liste.alle() == ["Milch"]


def test_fehler_beim_schreiben_der_nebendatei_laesst_die_datei_ganz(tmp_path, monkeypatch):
    pfad = tmp_path / "liste.yaml"
    liste = Liste(pfad)
    liste.hinzufuegen("Milch")
    vorher = pfad.read_bytes()
    echt = Path.write_text

    def halb(self, text, *args, **kwargs):
        echt(self, text[: len(text) // 2], *args, **kwargs)
        raise OSError(28, "Auf dem Gerät ist kein Speicherplatz mehr verfügbar")

    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", halb)
        with pytest.raises(OSError, match="Speicherplatz"):
            liste.hinzufuegen("Brot")
    assert pfad.read_bytes() == vorher and nur_die_datei(tmp_path)  # die halbe Nebendatei ist weg
    assert liste.alle() == ["Milch"]


def test_lesen_wird_wiederholt_solange_die_andere_seite_die_datei_ersetzt(tmp_path, monkeypatch):
    # Auch das Lesen scheitert unter Windows, während die Datei gerade ersetzt wird (gemessen: bei 2 bis 6 %
    # der Lesungen im Dauerlauf); nach einer kurzen Pause klappt es.
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    versuche = []
    echt = Path.read_text

    def zuerst_gesperrt(self, *args, **kwargs):
        if self == pfad:
            versuche.append(1)
            if len(versuche) < dateien.LESE_VERSUCHE:
                raise PermissionError("gesperrt")
        return echt(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", zuerst_gesperrt)
    with warnungen() as meldungen:
        assert app.alle() == ["Milch"]
    assert len(versuche) == dateien.LESE_VERSUCHE and meldungen == []


def test_dauerhaft_gesperrte_datei_beim_lesen_laesst_den_speicher_gelten(tmp_path, monkeypatch):
    pfad = tmp_path / "liste.yaml"
    raum, app = Liste(pfad), Liste(pfad)
    raum.hinzufuegen("Milch")
    assert app.alle() == ["Milch"]
    raum.hinzufuegen("Brot")

    echt = Path.read_text

    def gesperrt_lesen(self, *args, **kwargs):
        if self == pfad:
            raise PermissionError("gesperrt")
        return echt(self, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(Path, "read_text", gesperrt_lesen)
        assert app.alle() == ["Milch"]  # kein Wurf
    assert app.alle() == ["Milch", "Brot"]  # beim nächsten Mal liest sie die Datei, obwohl sie dieselbe ist


# --- Ersetzen, ohne die Fassung der anderen Seite zu kennen ---


def test_ueberschriebene_fassung_der_anderen_seite_steht_im_log(tmp_path, monkeypatch):
    # Erinnerungen.ansage ändert den Eintrag, den faellige() eben geliefert hat, und speichert ohne
    # Nachladen. Schreibt die App dazwischen, geht ihre Fassung verloren: Ohne Schloss lässt sich das
    # Zeitfenster nicht schließen, aber das Log zeigt, dass es getroffen wurde.
    pfad = tmp_path / "erinnerungen.json"
    raum, app = Erinnerungen(pfad, lambda: JETZT), Erinnerungen(pfad, lambda: JETZT)
    raum.anlegen("Wäsche", JETZT - timedelta(minutes=1))
    (faellig,) = raum.faellige()
    app.anlegen("Müll", JETZT + timedelta(hours=1))  # die App lädt den Stand der Datei und speichert

    beim_umbenennen = []
    echt = os.replace

    def merken(quelle, ziel):
        beim_umbenennen.append(len(meldungen))
        echt(quelle, ziel)

    with warnungen() as meldungen, monkeypatch.context() as m:
        m.setattr(os, "replace", merken)
        raum.ansage(faellig)
    assert len(meldungen) == 1 and "erinnerungen.json" in meldungen[0]
    assert beim_umbenennen == [0]  # erst nach dem Ersetzen: Zwischen Prüfung und Ersetzen wartet nichts
    assert "Wäsche" not in [e.text for e in Erinnerungen(pfad, lambda: JETZT).offene()]  # die Datei ist ganz


def test_erinnerungen_gescheitertes_speichern_laesst_die_erinnerung_faellig(tmp_path, monkeypatch):
    # Die Auslieferung markiert die Erinnerung im Speicher, bevor sie speichert. Scheitert das Speichern,
    # darf sie nicht als ausgeliefert gelten: Der nächste Takt liefert sie noch einmal.
    pfad = tmp_path / "erinnerungen.json"
    erinnerungen = Erinnerungen(pfad, lambda: JETZT)
    erinnerungen.anlegen("Wäsche", JETZT - timedelta(minutes=1))
    (faellig,) = erinnerungen.faellige()
    with monkeypatch.context() as m:
        m.setattr(os, "replace", gesperrt)
        with pytest.raises(PermissionError):
            erinnerungen.ansage(faellig)
    assert [e.text for e in erinnerungen.faellige()] == ["Wäsche"]
    assert "ausgeliefert" not in pfad.read_text(encoding="utf-8")


def test_einkaufsliste_zwei_instanzen_und_gesperrtes_umbenennen(tmp_path, monkeypatch):
    pfad = tmp_path / "einkaufsliste.yaml"
    raum, app = Einkaufsliste(pfad), Einkaufsliste(pfad)
    raum.hinzufuegen("Milch")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", gesperrt)
        with pytest.raises(PermissionError):
            app.hinzufuegen("Brot")
    assert [a.name for a in app.alle()] == ["Milch"]
    app.hinzufuegen("Brot")
    assert [a.name for a in raum.alle()] == ["Milch", "Brot"]


# --- Zwei echte Prozesse ---

SCHREIBER = """
import sys, time
from pathlib import Path
from alfred.einkauf import Einkaufsliste

liste = Einkaufsliste(Path(sys.argv[1]))
ende = time.perf_counter() + float(sys.argv[2])
gespeichert = gescheitert = 0
while time.perf_counter() < ende:
    try:
        liste.abhaken(f"Artikel{gespeichert % 150}", erledigt=(gespeichert // 150) % 2 == 0)
        gespeichert += 1
    except PermissionError:  # nach allen Versuchen noch gesperrt: Die Zählung gehört nicht hierher
        gescheitert += 1
print(gespeichert, gescheitert)
"""


def test_ein_leser_im_anderen_prozess_sieht_nie_eine_halbe_liste(tmp_path):
    # Der Schreiber ersetzt eine Liste mit 150 Artikeln ununterbrochen, dieser Prozess liest ununterbrochen.
    # Beim Überschreiben an Ort und Stelle sah der Leser in einem von rund 200 Lesungen eine leere oder
    # halbe Liste (gemessen am 16.10.2026, Windows 11).
    pfad = tmp_path / "einkaufsliste.yaml"
    leser = Einkaufsliste(pfad)
    leser.hinzufuegen(", ".join(f"Artikel{i}" for i in range(150)))
    skript = tmp_path / "schreiber.py"
    skript.write_text(SCHREIBER, encoding="utf-8")
    umgebung = {**os.environ, "PYTHONPATH": str(Path(alfred.__file__).resolve().parents[1])}
    schreiber = subprocess.Popen(
        [sys.executable, str(skript), str(pfad), "2"],
        env=umgebung, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    lesungen, unvollstaendig = 0, []
    try:
        while schreiber.poll() is None:
            lesungen += 1
            try:
                if (anzahl := len(leser.alle())) != 150:
                    unvollstaendig.append(anzahl)
            except Exception as e:  # noqa: BLE001 - jede Ausnahme beim Lesen ist ein Befund
                unvollstaendig.append(repr(e))
        ausgabe = schreiber.communicate(timeout=30)[0]
    finally:
        if schreiber.poll() is None:
            schreiber.kill()
    assert schreiber.returncode == 0 and int(ausgabe.split()[0]) > 10, ausgabe  # der Schreiber hat gearbeitet
    assert lesungen > 50
    assert unvollstaendig == [], f"{len(unvollstaendig)} von {lesungen} Lesungen unvollständig"
