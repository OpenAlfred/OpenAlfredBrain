"""Codewort: der zweite Faktor für heikle Aktionen im Gespräch, seit die Stimme nicht mehr zählt.

Die Stimmerkennung wies Alexander zu oft ab (docs/erkenntnisse.md, 20.10.2026), und eine
Bestätigung am Handy lehnte Alexander ab: „dann kann ich das auch direkt selbst machen“. Deshalb
verlangen heikle Aktionen (WhatsApp, Mail, das Ende des Gästemodus) im Sprach-Loop statt eines Ja
ein Codewort, das Alexander einmal festlegt. Fernseher und Videos kennen es nicht, Gäste nur, wenn
sie es mithören. Im App-Chat reicht weiter ein Ja: Die App ist nur im Tailnet erreichbar, also
von Alexanders eigenen Geräten.

Das Wort steht in .env (ALFRED_CODEWORT), nie in Konfiguration, Prompt oder Gedächtnis. Der
`Codewortschutz` sitzt direkt hinter der Transkription und ersetzt es in jedem Transkript durch
MARKE, bevor Filter, Log, Mitschrift oder Modell den Satz sehen; bestaetigen prüft nur die Marke.

    python -m alfred.codewort         Codewort festlegen oder ändern (fragt verdeckt, zweimal)
    python -m alfred.codewort --aus   Codewort entfernen; heikle Aktionen brauchen dann nur ein Ja
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import unicodedata
from collections.abc import Iterable

from loguru import logger
from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

UMGEBUNG = "ALFRED_CODEWORT"
MARKE = "[Codewort]"  # steht im Transkript an der Stelle des Codeworts
MIN_BUCHSTABEN = 5
_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _normiert(wort: str) -> str:
    """Kleingeschrieben, Umlaute ausgeschrieben, ohne Akzente und Satzzeichen: „Zitronen-Falter!“ → zitronenfalter."""
    text = unicodedata.normalize("NFKD", wort.casefold().translate(_UMLAUTE))
    return "".join(z for z in text if z.isascii() and z.isalnum())


class Codewort:
    """Erkennt das Codewort in Transkripten, auch getrennt geschrieben („Zitronen Falter“).

    Gezählt wird nur in ganzen Wörtern: Ein Codewort „Adler“ steckt nicht in „Seeadler“.
    """

    def __init__(self, wort: str):
        self._ziel = _normiert(wort)
        if not self._ziel:
            raise ValueError("Das Codewort braucht mindestens einen Buchstaben.")

    @classmethod
    def aus_umgebung(cls, wachwoerter: Iterable[str] = ("alfred",)) -> Codewort | None:
        """Das Codewort aus ALFRED_CODEWORT (.env); None, wenn keines gesetzt ist oder es nichts taugt."""
        wort = os.environ.get(UMGEBUNG, "").strip()
        if not wort:
            return None
        if fehler := untauglich(wort, wachwoerter):
            # Ein unbrauchbares Wort (etwa „ja bitte“) ließe jedes Ja als Codewort durch: lieber ohne.
            logger.warning(f"Codewort in .env nicht verwendet: {fehler}")
            return None
        return cls(wort)

    def maskieren(self, text: str) -> str:
        """Ersetzt das Codewort durch MARKE. Eine MARKE, die schon im Text steht, zählt nicht."""
        text = text.replace(MARKE, "Codewort")
        woerter = [(m.start(), m.end(), w) for m in re.finditer(r"\w+", text) if (w := _normiert(m.group()))]
        treffer: list[tuple[int, int]] = []
        i = 0
        while i < len(woerter):
            stueck, j = "", i
            while j < len(woerter) and len(stueck) < len(self._ziel):
                stueck += woerter[j][2]
                j += 1
            if stueck == self._ziel:
                treffer.append((woerter[i][0], woerter[j - 1][1]))
                i = j
            else:
                i += 1
        for anfang, ende in reversed(treffer):
            text = text[:anfang] + MARKE + text[ende:]
        return text


def untauglich(wort: str, wachwoerter: Iterable[str] = ("alfred",)) -> str | None:
    """Warum ein Wort als Codewort nichts taugt; None, wenn es taugt.

    Ein Ja-, Nein- oder Vorbehaltswort darin machte aus jedem „Ja, bitte“ ein Codewort, Ziffern
    schreibt die Spracherkennung mal als Zahl, mal als Wort.
    """
    from alfred.werkzeuge.basis import JA_WOERTER, NEIN_WOERTER, VORBEHALT_WOERTER

    if any(z.isdigit() for z in wort):
        return "Das Codewort darf keine Ziffern enthalten; die Spracherkennung schreibt sie mal als Zahl, mal als Wort."
    teile = [_normiert(t) for t in re.findall(r"\w+", wort)]
    if len("".join(teile)) < MIN_BUCHSTABEN:
        return f"Das Codewort braucht mindestens {MIN_BUCHSTABEN} Buchstaben."
    verboten = {_normiert(w) for w in (*JA_WOERTER, *NEIN_WOERTER, *VORBEHALT_WOERTER, "codewort")}
    verboten |= {_normiert(w) for phrase in wachwoerter for w in phrase.split()}
    if belegt := [t for t in teile if t in verboten]:
        return f"„{belegt[0]}“ kommt in gewöhnlichen Antworten vor oder ist das Wachwort; nimm ein anderes Wort."
    return None


class Codewortschutz(FrameProcessor):
    """Ersetzt das Codewort in jedem Transkript durch MARKE; sitzt direkt hinter der Transkription.

    Hinter ihm sieht niemand das Wort: weder Sprach- und Stimmfilter mit ihren Logzeilen noch
    Mitschrift, Modell und Tagesprotokoll. Ein Satz mit dem Codewort verliert seine Sprache, damit
    der Sprachfilter ein einzelnes Wort nicht als Niederländisch verwirft.
    """

    def __init__(self, codewort: Codewort, **kwargs):
        super().__init__(**kwargs)
        self._codewort = codewort

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)) and frame.text:
            maskiert = self._codewort.maskieren(frame.text)
            if maskiert != frame.text:
                frame.text, frame.language, frame.result = maskiert, None, None
                if isinstance(frame, TranscriptionFrame):
                    logger.info("Codewort im Transkript erkannt")
        await self.push_frame(frame, direction)


def main() -> None:
    from alfred.config import PROJEKT_DIR, load_settings
    from alfred.einrichtung import env_setzen

    parser = argparse.ArgumentParser(description="Codewort für heikle Aktionen im Gespräch festlegen")
    parser.add_argument("--aus", action="store_true", help="Codewort entfernen; heikle Aktionen brauchen dann nur ein Ja")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    env, vorlage = PROJEKT_DIR / ".env", PROJEKT_DIR / ".env.example"
    if args.aus:
        env_setzen(env, vorlage, UMGEBUNG, "")
        print("Codewort entfernt: WhatsApp, Mail und das Ende des Gästemodus brauchen jetzt nur ein Ja.")
        print("Alfred neu starten, damit es gilt.")
        return
    settings = load_settings()
    print("Das Codewort sagst du statt „Ja“, wenn Alfred eine WhatsApp oder Mail abschicken oder den")
    print("Gästemodus beenden soll. Nimm ein Wort, das du sonst nie zu Alfred sagst, gern zwei Wörter")
    print("wie „roter Elefant“; Alfred selbst bekommt es nie zu sehen. Die Eingabe bleibt verdeckt.\n")
    wort = getpass.getpass("Codewort: ").strip()
    if fehler := untauglich(wort, settings.wachwort.phrasen):
        sys.exit(f"{fehler} Nichts geändert.")
    if getpass.getpass("Noch einmal: ").strip() != wort:
        sys.exit("Die beiden Eingaben unterscheiden sich; nichts geändert.")
    env_setzen(env, vorlage, UMGEBUNG, wort)
    print(f"Gespeichert in {env}. Alfred neu starten, damit es gilt; prüfen mit python -m alfred.pruefen.")


if __name__ == "__main__":
    main()
