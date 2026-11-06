"""Was Alfred kann, für die App: die Werkzeuge nach Gruppen mit Stufe, Anbindung und Beispielen.

Alles, was sich aus dem Register ableiten lässt (Namen, Gruppen, Stufen, Voraussetzungen, Modi),
kommt von dort und kann nicht veralten. Von Hand stehen hier nur die Anzeigetexte: ein Satz je
Werkzeug (die Beschreibung für das Modell ist an das Modell gerichtet und taugt nicht zum Lesen),
Titel, Symbol und Beispielsätze je Gruppe (aus der README, „Was Alfred kann") und die Namen der
Anbindungen. Ein neues Werkzeug oder eine neue Gruppe braucht hier einen Eintrag; ohne ihn
scheitert tests/test_uebersicht.py.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from alfred.persona import GRUPPEN
from alfred.resources import Ressourcen
from alfred.werkzeuge.registrierung import REGISTER, STUFEN, Werkzeug


@dataclass(frozen=True)
class Anzeige:
    titel: str
    symbol: str  # Name des Symbols in web_static/index.html
    beispiele: tuple[str, ...]


ANZEIGE: dict[str, Anzeige] = {
    "kern": Anzeige(
        "Grundlagen",
        "zap",
        (
            "Stell einen Timer auf zehn Minuten für die Nudeln.",
            "Erinner mich werktags um acht an die Uni-Aufgaben.",
            "Wie wird das Wetter morgen?",
            "Merk dir, dass ich freitags frei habe.",
            "Denk mal nach: Was kostet ein Jahr Mistral bei einer Stunde Gespräch am Tag?",
        ),
    ),
    "kalender": Anzeige(
        "Kalender",
        "calendar",
        ("Was steht morgen an?", "Trag Zahnarzt am Montag um zehn ein.", "Verschieb den Zahnarzt auf Donnerstag."),
    ),
    "aufgaben": Anzeige(
        "Aufgaben",
        "check-square",
        ("Setz Steuererklärung auf meine Liste.", "Was steht an?", "Hak die Wäsche ab."),
    ),
    "kommunikation": Anzeige(
        "Kommunikation",
        "message-circle",
        (
            "Schreib Aaron, dass ich zehn Minuten später komme.",
            "Hab ich neue Mails?",
            "Sag mir Bescheid, wenn Jan schreibt.",
            "Schick mir das aufs Handy.",
        ),
    ),
    "haushalt": Anzeige(
        "Haushalt",
        "home",
        (
            "Setz Milch und 2 Brot auf die Einkaufsliste.",
            "Plan mir fünf Abendessen.",
            "Merk dir: zwei Liter Milch, haltbar bis Freitag.",
        ),
    ),
    "ernaehrung": Anzeige(
        "Ernährung",
        "apple",
        ("Ich hatte ein Müsli mit Banane.", "Wie steht's heute?", "Ich wiege 80 Kilo."),
    ),
    "finanzen": Anzeige(
        "Finanzen",
        "wallet",
        ("Zwölf Euro fürs Mittagessen.", "Was hab ich diesen Monat ausgegeben?"),
    ),
    "medien": Anzeige("Musik", "music", ("Spiel Bohemian Rhapsody.", "Pause.", "Was läuft gerade?")),
    "auftraege": Anzeige(
        "Aufträge",
        "layers",
        (
            "Analysier, ob sich ein E-Auto lohnt, und mach mir einen Bericht mit Quellen.",
            "Was machst du gerade?",
            "Brich den Auftrag ab.",
        ),
    ),
    "system": Anzeige(
        "Routinen & Kosten",
        "repeat",
        (
            "Jeden Morgen um halb acht: Wetter und meine Termine.",
            "Mach mein Morgenbriefing.",
            "Was hast du heute gekostet?",
        ),
    ),
}

# Ein Satz je Werkzeug, in der dritten Person und für den Menschen geschrieben.
BESCHREIBUNG: dict[str, str] = {
    # kern
    "aktuelle_zeit": "Nennt Uhrzeit, Datum und Jahr",
    "erinnerung_setzen": "Erinnert zur gewünschten Zeit, einmalig oder wiederkehrend",
    "erinnerungen_anzeigen": "Listet offene Erinnerungen und Timer",
    "erinnerung_loeschen": "Löscht eine Erinnerung",
    "timer_stellen": "Stellt einen Timer, der von selbst klingelt",
    "timer_abfragen": "Nennt laufende Timer und die Restzeit",
    "timer_stoppen": "Stoppt einen oder alle Timer",
    "merken": "Merkt sich Wissen dauerhaft, nach Bereichen geordnet",
    "profil_merken": "Merkt sich Fakten über dich, etwa den Wohnort",
    "nachschlagen": "Durchsucht Notizen und frühere Gesprächsprotokolle",
    "websuche": "Sucht im Internet und antwortet mit Quelle",
    "nachdenken": "Gibt schwere Fragen zum gründlichen Nachdenken an den Denker",
    "bestaetigen": "Führt eine vorgeschlagene Aktion nach deinem Ja aus",
    "abbrechen": "Verwirft eine vorgeschlagene Aktion",
    "wetter": "Nennt die Wettervorhersage für heute oder morgen",
    "regel_merken": "Merkt sich eine neue Verhaltensregel dauerhaft",
    "regeln_anzeigen": "Listet die per Stimme gesetzten Regeln",
    "regel_loeschen": "Löscht eine Regel",
    "feedback_merken": "Hält fest, wenn etwas falsch lief, samt Wortwechsel",
    "modus_wechseln": "Wechselt den Modus, etwa Fokus, Feierabend oder Gäste",
    "gespraech_beenden": "Beendet das Gespräch; danach hört Alfred nur aufs Wachwort",
    "bereitschaft": "Standby: Alfred reagiert nur noch, wenn sein Name fällt",
    "zuhoeren": "Hört geduldig zu und redet nicht dazwischen",
    # kalender
    "termine_abfragen": "Liest Termine für einen Tag oder Zeitraum",
    "termin_anlegen": "Legt einen Termin im Google Kalender an",
    "termin_verschieben": "Verschiebt einen Termin",
    "termin_absagen": "Sagt einen Termin ab und löscht ihn",
    # aufgaben
    "aufgabe_anlegen": "Setzt eine Aufgabe auf die To-do-Liste, wahlweise mit Datum",
    "aufgaben_anzeigen": "Listet offene Aufgaben, Fälligstes zuerst",
    "aufgabe_erledigt": "Hakt eine Aufgabe ab",
    # kommunikation
    "nachricht_senden": "Schickt eine WhatsApp-Nachricht in deinem Namen",
    "whatsapp_melden": "Sagt neue WhatsApp-Nachrichten von selbst an",
    "nachrichten_lesen": "Liest eingegangene WhatsApp-Nachrichten vor",
    "kontakt_merken": "Merkt sich die Telefonnummer eines Kontakts",
    "kontakte_anzeigen": "Listet die gemerkten Kontakte",
    "geburtstag_merken": "Merkt einen Geburtstag und erinnert am Vortag",
    "geburtstage_anzeigen": "Nennt die Geburtstage der nächsten Tage",
    "mails_abfragen": "Nennt neue, ungelesene E-Mails",
    "mail_vorlesen": "Liest eine E-Mail vor",
    "mail_senden": "Schickt eine E-Mail in deinem Namen",
    "mail_antworten": "Antwortet auf eine E-Mail",
    "kontakt_email_merken": "Merkt sich die E-Mail-Adresse eines Kontakts",
    "ans_telefon": "Schickt einen Text als Push aufs Handy",
    # haushalt
    "einkauf_hinzufuegen": "Setzt Artikel mit Mengen auf die Einkaufsliste",
    "einkaufsliste_anzeigen": "Liest die Einkaufsliste vor, nach Abteilung",
    "einkauf_abhaken": "Hakt Artikel ab oder nimmt sie von der Liste",
    "einkaufsliste_leeren": "Räumt Abgehaktes oder die ganze Liste weg",
    "vorrat_merken": "Merkt sich Vorräte mit Haltbarkeit und erinnert vor dem Ablauf",
    "vorrat_anzeigen": "Listet den Vorrat, Ablaufendes zuerst",
    "vorrat_verbraucht": "Nimmt Verbrauchtes aus dem Vorrat",
    "wochenplan_erstellen": "Plant Abendessen mit Rezepten für mehrere Tage",
    "wochenplan_einkaufen": "Setzt die Zutaten des Wochenplans auf die Einkaufsliste",
    # ernaehrung
    "essen_protokollieren": "Trägt Mahlzeiten ein und schätzt die Nährwerte",
    "ernaehrung_heute": "Nennt den Tagesstand gegenüber den Zielen",
    "gewicht_merken": "Trägt das Körpergewicht ein",
    # finanzen
    "ausgabe_merken": "Trägt eine Ausgabe mit Kategorie ein",
    "ausgaben_monat": "Nennt die Ausgaben des Monats je Kategorie",
    # medien
    "musik": "Steuert Spotify: abspielen, pausieren, Lautstärke",
    # auftraege
    "auftrag_geben": "Nimmt Recherchen und Berichte für den Hintergrund an",
    "auftraege_anzeigen": "Zeigt, was läuft, wartet oder fertig ist",
    "auftrag_abbrechen": "Bricht einen Auftrag ab",
    "bericht_lesen": "Liest einen fertigen Bericht, um Fragen dazu zu beantworten",
    # system
    "routine_anlegen": "Legt eine Routine an, die Alfred zur Uhrzeit selbst ausführt",
    "routinen_anzeigen": "Listet Routinen mit Zeitplan",
    "routine_loeschen": "Löscht eine Routine",
    "routine_ausfuehren": "Startet eine Routine sofort",
    "kosten_heute": "Nennt Verbrauch und ungefähre Kosten des Tages",
}

# Feld der Ressourcen, das ein Werkzeug braucht, wie die App die Anbindung nennt.
ANBINDUNGEN: dict[str, str] = {
    "kalender": "Google Kalender",
    "whatsapp": "WhatsApp-Brücke",
    "kontakte": "Kontakte",
    "meldeliste": "WhatsApp-Meldeliste",
    "mail": "E-Mail-Zugang",
    "benachrichtiger": "Push (ntfy)",
    "musik": "Spotify",
    "wetter": "Wetterdienst",
    "kosten": "Kostenzähler",
    "schaetzer": "Nährwert-Schätzer",
    "schleuse": "Raum-Modus",
    "zuhoeren": "Sprach-Modus",
}

# Diese Felder füllt nur der Sprach-Prozess. Fehlen sie in der App, kann Alfred das Werkzeug
# trotzdem; es gilt als „nur per Stimme", nicht als nicht eingerichtet.
NUR_STIMME = ("schleuse", "zuhoeren")


class _Spaeher:
    """Reicht die Ressourcen durch und merkt sich das Feld, das zuletzt leer war.

    So sagt die Prüfung eines Werkzeugs (``braucht``) nicht nur, dass es fehlt, sondern auch
    woran: ``res.whatsapp and res.kontakte`` hört beim ersten leeren Feld auf.
    """

    def __init__(self, res: Ressourcen):
        self._res = res
        self.leer: str | None = None

    def __getattr__(self, name: str):
        wert = getattr(self._res, name)
        if not wert:
            self.leer = name
        return wert


def _status(werkzeug: Werkzeug, res: Ressourcen) -> tuple[str, str | None]:
    """bereit, nur_stimme oder fehlt; bei den letzten beiden dazu die Anbindung, die nötig ist."""
    if werkzeug.braucht is None:
        return "bereit", None
    spaeher = _Spaeher(res)
    if werkzeug.braucht(spaeher):
        return "bereit", None
    anbindung = ANBINDUNGEN.get(spaeher.leer, spaeher.leer) if spaeher.leer else None
    return ("nur_stimme" if spaeher.leer in NUR_STIMME else "fehlt"), anbindung


def beschreibung(werkzeug: Werkzeug) -> str:
    """Der Satz aus BESCHREIBUNG; für ein Werkzeug ohne Eintrag die erste Zeile seiner Modellbeschreibung."""
    if werkzeug.name in BESCHREIBUNG:
        return BESCHREIBUNG[werkzeug.name]
    erste = (inspect.getdoc(werkzeug.funktion) or "").split("\n", 1)[0]
    return erste[:120]


def _werkzeug(werkzeug: Werkzeug, res: Ressourcen) -> dict:
    status, braucht = _status(werkzeug, res)
    return {
        "name": werkzeug.name,
        "stufe": werkzeug.stufe,
        "beschreibung": beschreibung(werkzeug),
        "status": status,
        "braucht": braucht,
    }


def _gruppe(gruppe: str, res: Ressourcen) -> dict:
    anzeige = ANZEIGE.get(gruppe, Anzeige(gruppe.capitalize(), "zap", ()))
    werkzeuge = [_werkzeug(w, res) for w in REGISTER.values() if w.gruppe == gruppe]
    fehlend = [w["braucht"] for w in werkzeuge if w["status"] == "fehlt" and w["braucht"]]
    return {
        "id": gruppe,
        "titel": anzeige.titel,
        "symbol": anzeige.symbol,
        "beschreibung": GRUPPEN[gruppe],
        "beispiele": list(anzeige.beispiele),
        "im_modus": gruppe in res.persona.aktive_gruppen(),
        "modi": [name for name in res.persona.modi if gruppe in res.persona.aktive_gruppen(name)],
        "werkzeuge": werkzeuge,
        "bereit": sum(w["status"] == "bereit" for w in werkzeuge),
        "nur_stimme": sum(w["status"] == "nur_stimme" for w in werkzeuge),
        "fehlt": sum(w["status"] == "fehlt" for w in werkzeuge),
        "stufen": {stufe: sum(w["stufe"] == stufe for w in werkzeuge) for stufe in STUFEN},
        "braucht": list(dict.fromkeys(fehlend)),  # ohne Doppelte, in der Reihenfolge des Auftretens
    }


def _modus(name: str, res: Ressourcen) -> dict:
    persona = res.persona
    modus = persona.modi[name]
    aktiv = persona.aktive_gruppen(name)
    verfuegbar = sum(
        1 for w in REGISTER.values() if w.gruppe in aktiv and _status(w, res)[0] != "fehlt"
    )
    return {
        "name": name,
        "beschreibung": modus.beschreibung,
        "aktiv": name == persona.aktiver_modus,
        "gruppen": [g for g in GRUPPEN if g in aktiv],
        "werkzeuge": verfuegbar,
        "regler": persona.regler_fuer(name).model_dump(),
        "abweichend": list(modus.regler),
    }


def uebersicht(res: Ressourcen) -> dict:
    """Alles, was die App im Bereich „Fähigkeiten" zeigt.

    ``bereit`` heißt: in diesem Prozess eingerichtet. ``nur_stimme``: nur der Sprach-Prozess hat es
    (Wachwort-Schleuse, Zuhören). ``fehlt``: die Anbindung ist nicht eingerichtet, ``braucht``
    nennt sie.
    """
    gruppen = [_gruppe(g, res) for g in GRUPPEN]
    werkzeuge = [w for g in gruppen for w in g["werkzeuge"]]
    modelle = res.settings.modelle
    return {
        "persona": {
            "name": res.persona.name,
            "nutzer": res.persona.nutzer,
            "modus": res.persona.aktiver_modus,
            "regeln": list(res.persona.regeln),
            "regler": res.persona.regler.model_dump(),
        },
        "modi": [_modus(name, res) for name in res.persona.modi],
        "gruppen": gruppen,
        "summe": {
            "gesamt": len(werkzeuge),
            "bereit": sum(w["status"] == "bereit" for w in werkzeuge),
            "nur_stimme": sum(w["status"] == "nur_stimme" for w in werkzeuge),
            "fehlt": sum(w["status"] == "fehlt" for w in werkzeuge),
            "stufen": {stufe: sum(w["stufe"] == stufe for w in werkzeuge) for stufe in STUFEN},
        },
        "modelle": {
            "sprecher": modelle.sprecher,
            "denker": modelle.denker,
            "werkzeuge": modelle.werkzeuge,
            "transkription": modelle.transkription,
            "sprachausgabe": modelle.sprachausgabe,
            "sprachnachricht": modelle.sprachnachricht,
        },
        "wachwort": list(res.settings.wachwort.phrasen),
    }
