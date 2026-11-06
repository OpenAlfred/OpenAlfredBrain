"""Alfreds Werkzeuge für das Sprecher-Modell: ein Modul je Gruppe, mit Freigabestufen.

Jede Funktion ist ein Pipecat-Werkzeug: async, erster Parameter `params`, Beschreibung und
Parameter kommen aus Signatur und Docstring. Das Ergebnis geht über params.result_callback.
Registriert wird sie mit `@werkzeug(gruppe, stufe, braucht=...)` (registrierung.py).

Gruppen (alfred.persona.GRUPPEN): kern ist in jedem Modus dabei; die übrigen kann ein Modus der
Persönlichkeit abschalten (`werkzeuge:` je Modus). Das Modell sieht dann nur die Werkzeuge der
aktiven Gruppen; `modus_wechseln` tauscht die Liste im laufenden Gespräch.

Stufen:
- lesend und schreibend laufen sofort (lesen, Erinnerungen, Notizen, Nachdenken).
- bestaetigen: Das Werkzeug legt die Aktion nur ab und meldet bestaetigung_noetig. Der Sprecher
  fragt nach, und erst `bestaetigen` führt sie aus. Die Regel steht im Code, nicht im Prompt.

`build_tools` und `faehigkeiten` (auswahl.py) sind die Schnittstelle für Pipeline, Textgespräch
und Tests; die Werkzeuge selbst sind hier unter ihrem Namen erreichbar.
"""

# Die Reihenfolge der Gruppen-Importe ist die Reihenfolge der Werkzeugliste für das Modell: kern zuerst,
# dann die abschaltbaren Gruppen; das Register füllt sich beim Import (registrierung.py).
from alfred.werkzeuge.registrierung import REGISTER, STUFEN, Rueckfrage, Werkzeug, werkzeug
from alfred.werkzeuge.basis import (
    JA_WOERTER,
    NEIN_WOERTER,
    Rueckfragewaechter,
    _freigabe_anfordern,
    _fremd,
    _jetzt,
    _mit_fremdschutz,
    _prompt_auffrischen,
    _res,
    _sofort_sagen,
    _vorlage,
    antwort_merken,
    codewort_genannt,
    freigabe_verwerfen,
    gespraechskontext,
    verlauf_seit_rueckfrage,
    zustimmung,
)
from alfred.werkzeuge.auswahl import build_tools, faehigkeiten, werkzeuge_auffrischen, werkzeuge_fuer
from alfred.werkzeuge.kern import (
    abbrechen,
    aktuelle_zeit,
    bereitschaft,
    bestaetigen,
    erinnerung_loeschen,
    erinnerung_setzen,
    erinnerungen_anzeigen,
    feedback_merken,
    gespraech_beenden,
    merken,
    modus_wechseln,
    nachdenken,
    nachschlagen,
    profil_merken,
    regel_loeschen,
    regel_merken,
    regeln_anzeigen,
    timer_abfragen,
    timer_stellen,
    timer_stoppen,
    websuche,
    wetter,
    zuhoeren,
)
from alfred.werkzeuge.kalender import termin_absagen, termin_anlegen, termin_verschieben, termine_abfragen
from alfred.werkzeuge.aufgaben import aufgabe_anlegen, aufgabe_erledigt, aufgaben_anzeigen
from alfred.werkzeuge.kommunikation import (
    ans_telefon,
    geburtstag_merken,
    geburtstage_anzeigen,
    kontakt_email_merken,
    kontakt_merken,
    kontakte_anzeigen,
    mail_antworten,
    mail_senden,
    mail_vorlesen,
    mails_abfragen,
    nachricht_senden,
    nachrichten_lesen,
    whatsapp_melden,
)
from alfred.werkzeuge.haushalt import (
    einkauf_abhaken,
    einkauf_hinzufuegen,
    einkaufsliste_anzeigen,
    einkaufsliste_leeren,
    vorrat_anzeigen,
    vorrat_merken,
    vorrat_verbraucht,
    wochenplan_einkaufen,
    wochenplan_erstellen,
)
from alfred.werkzeuge.ernaehrung import _ziele, ernaehrung_heute, essen_protokollieren, gewicht_merken
from alfred.werkzeuge.finanzen import ausgabe_merken, ausgaben_monat
from alfred.werkzeuge.medien import musik
from alfred.werkzeuge.auftraege import (
    MAX_BERICHT_ZEICHEN,
    AuftragEingabe,
    auftraege_anzeigen,
    auftrag_abbrechen,
    auftrag_geben,
    bericht_lesen,
)
from alfred.werkzeuge.system import kosten_heute, routine_anlegen, routine_ausfuehren, routine_loeschen, routinen_anzeigen

__all__ = [
    "JA_WOERTER", "MAX_BERICHT_ZEICHEN", "NEIN_WOERTER", "REGISTER", "STUFEN", "AuftragEingabe", "Rueckfrage",
    "Rueckfragewaechter", "Werkzeug", "_freigabe_anfordern", "_fremd", "_jetzt", "_mit_fremdschutz", "_prompt_auffrischen",
    "_res", "_sofort_sagen", "_vorlage", "_ziele",
    "antwort_merken", "build_tools", "codewort_genannt", "faehigkeiten", "freigabe_verwerfen", "gespraechskontext", "verlauf_seit_rueckfrage",
    "werkzeug", "werkzeuge_auffrischen", "werkzeuge_fuer", "zustimmung",
    # kern
    "abbrechen", "aktuelle_zeit", "bereitschaft", "bestaetigen", "erinnerung_loeschen", "erinnerung_setzen", "erinnerungen_anzeigen",
    "feedback_merken", "gespraech_beenden", "merken", "modus_wechseln", "nachdenken", "nachschlagen", "profil_merken",
    "regel_loeschen", "regel_merken", "regeln_anzeigen", "timer_abfragen", "timer_stellen", "timer_stoppen",
    "websuche", "wetter", "zuhoeren",
    # kalender
    "termin_absagen", "termin_anlegen", "termin_verschieben", "termine_abfragen",
    # aufgaben
    "aufgabe_anlegen", "aufgabe_erledigt", "aufgaben_anzeigen",
    # kommunikation
    "ans_telefon", "geburtstag_merken", "geburtstage_anzeigen", "kontakt_email_merken", "kontakt_merken",
    "kontakte_anzeigen", "mail_antworten", "mail_senden", "mail_vorlesen", "mails_abfragen", "nachricht_senden",
    "nachrichten_lesen", "whatsapp_melden",
    # haushalt
    "einkauf_abhaken", "einkauf_hinzufuegen", "einkaufsliste_anzeigen", "einkaufsliste_leeren", "vorrat_anzeigen",
    "vorrat_merken", "vorrat_verbraucht", "wochenplan_einkaufen", "wochenplan_erstellen",
    # ernaehrung
    "ernaehrung_heute", "essen_protokollieren", "gewicht_merken",
    # finanzen
    "ausgabe_merken", "ausgaben_monat",
    # medien
    "musik",
    # auftraege
    "auftraege_anzeigen", "auftrag_abbrechen", "auftrag_geben", "bericht_lesen",
    # system
    "kosten_heute", "routine_anlegen", "routine_ausfuehren", "routine_loeschen", "routinen_anzeigen",
]  # fmt: skip
