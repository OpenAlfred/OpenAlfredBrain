"""Wächter gegen erfundene Aktionen: Behauptet Alfred, etwas getan zu haben, ohne Werkzeug, bessert er nach.

Im Gespräch vom 6.10.2026 sagte Alfred "Alles klar, dann richte ich die Erinnerung ein",
"Ich sage dir sofort Bescheid, sobald Jan schreibt" und "Ich habe heute nichts verbraucht",
ohne ein einziges Werkzeug aufzurufen; im Tagesprotokoll stand die Erinnerung danach als
erledigt. Der Prompt verbietet das längst. Deshalb prüft der Code jede Antwort des Modells:
Steht darin eine solche Behauptung und lief seit Alexanders letztem Satz kein Werkzeug, bekommt
das Modell einen Hinweis und muss das Werkzeug aufrufen oder ehrlich sagen, dass es das nicht kann.

Die Muster sind absichtlich eng: lieber eine Behauptung übersehen als jede Plauderei stören.
Fragen ("Soll ich das einrichten?"), Möglichkeiten ("Ich kann dir …") und Verneinungen zählen nicht.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

def _ich(verb: str, rest: str = "") -> re.Pattern:
    """Erste Person, auch umgestellt ("ich richte … ein", "dann richte ich … ein")."""
    return re.compile(rf"(\bich {verb}\b|\b{verb} ich\b){rest}", re.IGNORECASE)


_ZUKUNFT = (
    r"(einrichten|anlegen|eintragen|speichern|notieren|merken|schicken|senden|erinnern|informieren|"
    r"benachrichtigen|bescheid sagen|bescheid geben)"
)
_GETAN = (
    r"(eingerichtet|angelegt|eingetragen|gespeichert|notiert|gemerkt|gesetzt|gestellt|verschickt|geschickt|"
    r"gesendet|abgeschickt|erstellt|hinzugefügt|gelöscht|aktiviert|nachgesehen|nachgeschaut|geprüft|"
    r"gelesen|erhalten|bekommen|verbraucht)"
)

MUSTER = [
    _ich("(richte|trage|stelle)", r"[^.!]{0,60}\bein\b"),  # "dann richte ich die Erinnerung ein"
    _ich("lege", r"[^.!]{0,60}\ban\b"),  # "ich lege dir den Termin an"
    _ich("setze", r"[^.!]{0,60}\b(auf|ein)\b"),  # "ich setze Milch auf die Liste"
    _ich("(speichere|notiere|schicke|sende)"),  # "dann schicke ich ihm das"
    _ich("merke", r" (mir|es|das)\b"),  # "Das merke ich mir"
    _ich("(erinnere|informiere|benachrichtige)", r" dich\b"),  # "ich erinnere dich morgen daran"
    re.compile(rf"\bich werde\b[^.!]{{0,60}}\b{_ZUKUNFT}\b", re.IGNORECASE),  # "ich werde dich informieren"
    re.compile(r"\b(sage|sag|gebe|geb) (ich )?dir\b[^.!]{0,40}\bbescheid\b", re.IGNORECASE),
    re.compile(rf"\b(ist|sind|wurde|wurden|habe ich|hab ich|ich habe|ich hab)\b[^.!]{{0,60}}\b{_GETAN}\b", re.IGNORECASE),
    re.compile(r"^\W*(erledigt|gemacht|ist erledigt|alles erledigt)\b", re.IGNORECASE),
    re.compile(r"\bhalte dich auf dem laufenden\b", re.IGNORECASE),
]
# Ein Satz mit diesen Wörtern spricht über Möglichkeiten oder Verneintes, nicht über Getanes.
_KEINE_BEHAUPTUNG = re.compile(
    r"\b(kann|könnte|kannst|würde|möchtest|willst|soll ich|sollen wir|nicht|wenn du willst|falls)\b", re.IGNORECASE
)


# Verspricht Alfred, zuzuhören oder zu warten, erfüllt der Code das (turns.Zuhoeren), statt zu
# ermahnen: "Alles klar, ich halte mich zurück", "Ich warte jetzt einfach, bis du fertig bist"
# (6.10.2026, beide Male ohne Werkzeug, und beim nächsten Satz fiel er wieder ins Wort).
ZUHOER_VERSPRECHEN = re.compile(
    r"\bich (halte mich\b[^.!?]{0,15}\bzurück|höre (dir )?(jetzt |einfach |nur |ab jetzt )*zu\b|warte\b[^.!?]{0,25}\bbis\b|"
    r"unterbreche dich nicht|unterbreche nicht mehr|falle dir nicht (mehr )?ins wort|lasse? dich (erst |jetzt )?ausreden|"
    r"bin ganz ohr)",
    re.IGNORECASE,
)


def zuhoer_versprechen(text: str) -> bool:
    """Ob Alfred verspricht, zuzuhören oder zu warten, bis das Gegenüber fertig ist."""
    return bool(text and ZUHOER_VERSPRECHEN.search(text))


# Fragt Alfred nach dem Text einer Nachricht, kommt gleich ein Diktat, oft mit Pausen. Das Modell
# fragte live, ohne vorher nachricht_senden aufzurufen ("Worauf soll die Nachricht lauten?",
# 6.10.2026); dann schaltet eben diese Frage aufs Zuhören.
DIKTAT_FRAGE = re.compile(
    r"\b(was|wie|worauf|welche[nrs]?)\b[^.!?]{0,50}\b(schreiben|lauten|schicken|senden|ausrichten)\b[^.!]*\?",
    re.IGNORECASE,
)


def fragt_nach_diktat(text: str) -> bool:
    """Ob Alfred nach dem Wortlaut einer Nachricht fragt."""
    return bool(text and DIKTAT_FRAGE.search(text))


def behauptung(text: str) -> str | None:
    """Der Satz, in dem Alfred eine Aktion behauptet, oder None.

    Eine Antwort mit Frage zählt nicht: Dann fragt Alfred nach Angaben oder nach dem Ja.
    """
    if not text or "?" in text:
        return None
    for satz in re.split(r"(?<=[.!])\s+|\n+", text.strip()):
        if _KEINE_BEHAUPTUNG.search(satz):
            continue
        if any(m.search(satz) for m in MUSTER):
            return satz.strip()
    return None


def werkzeug_seit_dem_letzten_satz(nachrichten: Iterable[dict]) -> bool | None:
    """Ob seit Alexanders letztem Satz ein Werkzeug lief. None: Die Antwort galt einem Hinweis vom System.

    Hinweise (fällige Erinnerung, Ergebnis des Denkers, dieser Wächter selbst) stehen als
    developer-Nachricht im Verlauf; darauf antwortet Alfred ohne Werkzeug, und das ist richtig.
    """
    for m in reversed([n for n in nachrichten if isinstance(n, dict)]):
        rolle = m.get("role")
        if rolle == "tool" or (rolle == "assistant" and m.get("tool_calls")):
            return True
        if rolle == "user":
            return False
        if rolle in ("developer", "system"):
            return None
    return None


def hinweis(satz: str) -> str:
    """Der Hinweis ans Modell, wenn es eine Aktion behauptet hat, ohne ein Werkzeug aufzurufen."""
    return (
        f"Hinweis vom System, nicht vom Gegenüber: Du hast eben gesagt: „{satz}“ Dabei hast du kein Werkzeug "
        "aufgerufen; es ist also nichts passiert und nichts nachgesehen. Ruf jetzt das passende Werkzeug auf. "
        "Gibt es keins, sag in einem Satz ehrlich, dass du das doch nicht kannst oder nicht weißt."
    )


def unbelegte_behauptung(antwort: str, nachrichten: Iterable[dict]) -> str | None:
    """Der behauptende Satz aus Alfreds Antwort, wenn kein Werkzeug ihn deckt; sonst None."""
    if werkzeug_seit_dem_letzten_satz(nachrichten) is not False:
        return None
    return behauptung(antwort)
