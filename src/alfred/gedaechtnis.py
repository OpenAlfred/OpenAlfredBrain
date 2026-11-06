"""Gedächtnis in drei Ebenen, als Markdown-Dateien, plus ein Profil.

- kontext.md: Was Alfred immer wissen soll. Steht komplett im Systemprompt, von Hand gepflegt.
- protokoll/JJJJ-MM-TT.md: Was an einem Tag besprochen wurde. Alfred schreibt es selbst,
  das heutige Protokoll steht im Systemprompt.
- wissen/<bereich>.md: Dauerhafte Notizen nach Bereichen und Projekten. Alfred legt sie mit
  dem Werkzeug `merken` an und liest sie mit `nachschlagen`.
- profil.yaml: Einzelne Fakten über den Nutzer, die Werkzeuge brauchen (Wohnort für das
  Wetter). Alfred trägt sie mit `profil_merken` ein, sobald sie im Gespräch fallen, und muss
  sie dann nie wieder erfragen. Sie stehen komplett im Systemprompt.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import yaml


def verlauf_als_text(messages, zuege: int = 10, max_zeichen: int = 400) -> str:
    """Die letzten Nachrichten eines Gesprächs lesbar: Sprecher, Werkzeugaufrufe, Ergebnisse."""
    zeilen = []
    for m in list(messages)[-zuege:]:
        rolle = m.get("role")
        if rolle == "assistant" and m.get("tool_calls"):
            for t in m["tool_calls"]:
                if isinstance(t, dict):
                    fn = t.get("function", {})
                    zeilen.append(f"Alfred ruft {fn.get('name')} auf: {fn.get('arguments')}")
        if rolle == "tool":
            inhalt = m.get("content")
            text = inhalt if isinstance(inhalt, str) else json.dumps(inhalt, ensure_ascii=False)
            zeilen.append(f"Werkzeug: {text[:max_zeichen]}")
        elif isinstance(m.get("content"), str) and m["content"].strip():
            wer = {"user": "Alexander", "assistant": "Alfred"}.get(rolle, rolle)
            zeilen.append(f"{wer}: {m['content'].strip()[:max_zeichen]}")
    return "\n".join(zeilen)

_KONTEXT_VORLAGE = """\
# Was Alfred immer wissen soll

Diese Datei steht komplett in Alfreds Systemprompt. Kurz halten.

## Über Alexander

- (Beruf, Arbeitszeiten, Vorlieben, was nervt, was hilft)

## Laufende Projekte

- Alfred: sprachgesteuerter Assistent, eine Woche Zeit
"""

MAX_PROTOKOLL_ZEICHEN = 1500
MAX_TREFFER = 15


def slug(bereich: str) -> str:
    text = bereich.strip().casefold()
    for von, nach in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(von, nach)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        raise ValueError(f"Kein brauchbarer Bereichsname: {bereich!r}")
    return text


class Gedaechtnis:
    def __init__(self, ordner: Path):
        self.ordner = ordner
        self.wissen = ordner / "wissen"
        self.protokoll = ordner / "protokoll"
        for pfad in (self.wissen, self.protokoll):
            pfad.mkdir(parents=True, exist_ok=True)
        self.kontext_datei = ordner / "kontext.md"
        if not self.kontext_datei.exists():
            self.kontext_datei.write_text(_KONTEXT_VORLAGE, encoding="utf-8")
        self.profil_datei = ordner / "profil.yaml"

    def kontext(self) -> str:
        return self.kontext_datei.read_text(encoding="utf-8").strip()

    def profil(self) -> dict[str, str]:
        """Die gemerkten Fakten über den Nutzer, Feld zu Wert."""
        if not self.profil_datei.exists():
            return {}
        daten = yaml.safe_load(self.profil_datei.read_text(encoding="utf-8")) or {}
        return {str(k): str(v) for k, v in daten.items()}

    def regeln(self) -> list[str]:
        """Verhaltensregeln, die Alexander per Stimme gesetzt hat; ergänzen die aus der Persönlichkeit."""
        datei = self.ordner / "regeln.yaml"
        if not datei.exists():
            return []
        return [str(r) for r in (yaml.safe_load(datei.read_text(encoding="utf-8")) or [])]

    def _regeln_speichern(self, regeln: list[str]) -> None:
        (self.ordner / "regeln.yaml").write_text(
            "# Regeln, die Alexander Alfred per Stimme gegeben hat. Von Hand änderbar.\n"
            + yaml.safe_dump(regeln, allow_unicode=True),
            encoding="utf-8",
        )

    def regel_merken(self, text: str) -> list[str]:
        if not text.strip():
            raise ValueError("Die Regel darf nicht leer sein.")
        regeln = self.regeln() + [text.strip()]
        self._regeln_speichern(regeln)
        return regeln

    def regel_loeschen(self, nummer: int) -> str | None:
        regeln = self.regeln()
        if not 1 <= nummer <= len(regeln):
            return None
        entfernt = regeln.pop(nummer - 1)
        self._regeln_speichern(regeln)
        return entfernt

    def feedback(self, bemerkung: str, verlauf: str, wann: datetime) -> Path:
        """Hält fest, was schiefging: Alexanders Bemerkung und der letzte Wortwechsel, für die Entwicklung."""
        datei = self.ordner / "feedback.md"
        if not datei.exists():
            datei.write_text("# Feedback aus Gesprächen\n\nJeder Eintrag: Bemerkung und der Wortwechsel davor.\n", encoding="utf-8")
        with datei.open("a", encoding="utf-8") as f:
            f.write(f"\n## {wann:%Y-%m-%d %H:%M}\n\n**Bemerkung:** {bemerkung.strip()}\n\n```\n{verlauf.strip()}\n```\n")
        return datei

    def aktion_protokollieren(self, beschreibung: str, ergebnis: str, wann: datetime, stimme: float | None = None) -> None:
        """Eine Zeile je freigegebener Aktion: wann, was, mit welchem Stimmwert, mit welchem Ergebnis."""
        datei = self.ordner / "aktionen.log"
        stimmwert = f"stimme={stimme:.2f}" if stimme is not None else "stimme=ungeprüft"
        # Tabulatoren und Zeilenumbrüche aus Werkzeugargumenten (Modell, fremder Text) würden aus einer Zeile Felder oder
        # Zeilen machen, die es nie gab: Das Protokoll ist der Beleg dafür, was Alfred getan hat.
        beschreibung, ergebnis = " ".join(str(beschreibung).split()), " ".join(str(ergebnis).split())
        with datei.open("a", encoding="utf-8") as f:
            f.write(f"{wann:%Y-%m-%d %H:%M:%S}\t{stimmwert}\t{beschreibung}\t{ergebnis}\n")

    def profil_merken(self, feld: str, wert: str) -> dict[str, str]:
        """Setzt ein Feld im Profil; ein leerer Wert löscht es. Gibt das ganze Profil zurück."""
        name = slug(feld)
        profil = self.profil()
        if wert.strip():
            profil[name] = wert.strip()
        else:
            profil.pop(name, None)
        self.profil_datei.write_text(
            "# Was Alfred über dich gemerkt hat. Von Hand änderbar.\n"
            + yaml.safe_dump(profil, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        return profil

    def bereiche(self) -> list[str]:
        return sorted(p.stem for p in self.wissen.glob("*.md"))

    def merken(self, bereich: str, notiz: str, wann: datetime) -> str:
        """Hängt eine Notiz an den Bereich an und gibt dessen Namen zurück."""
        name = slug(bereich)
        datei = self.wissen / f"{name}.md"
        if not datei.exists():
            datei.write_text(f"# {bereich.strip()}\n\n", encoding="utf-8")
        with datei.open("a", encoding="utf-8") as f:
            f.write(f"- {wann:%Y-%m-%d}: {notiz.strip()}\n")
        return name

    def nachschlagen(self, begriff: str) -> list[str]:
        """Zeilen aus allen Ebenen, die den Begriff enthalten, neueste Protokolle zuerst."""
        begriff = begriff.strip().casefold()
        if not begriff:
            return []
        dateien = [self.kontext_datei, *sorted(self.wissen.glob("*.md"))]
        dateien += sorted(self.protokoll.glob("*.md"), reverse=True)
        treffer = []
        for datei in dateien:
            for zeile in datei.read_text(encoding="utf-8").splitlines():
                if begriff in zeile.casefold() and not zeile.startswith("#"):
                    treffer.append(f"{datei.relative_to(self.ordner).as_posix()}: {zeile.strip()}")
                    if len(treffer) >= MAX_TREFFER:
                        return treffer
        return treffer

    def protokoll_heute(self, heute: datetime) -> str:
        datei = self.protokoll / f"{heute:%Y-%m-%d}.md"
        if not datei.exists():
            return ""
        text = datei.read_text(encoding="utf-8").strip()
        return text[-MAX_PROTOKOLL_ZEICHEN:]

    def protokollieren(self, zusammenfassung: str, wann: datetime) -> None:
        datei = self.protokoll / f"{wann:%Y-%m-%d}.md"
        if not datei.exists():
            datei.write_text(f"# {wann:%d.%m.%Y}\n\n", encoding="utf-8")
        with datei.open("a", encoding="utf-8") as f:
            f.write(f"## {wann:%H:%M}\n\n{zusammenfassung.strip()}\n\n")

    def fuer_prompt(self, heute: datetime) -> str:
        """Der Gedächtnis-Abschnitt des Systemprompts."""
        teile = [f"Was du dauerhaft weißt:\n{self.kontext()}"]
        if profil := self.profil():
            teile.append(
                "Was du dir über dein Gegenüber gemerkt hast (profil_merken):\n"
                + "\n".join(f"- {feld}: {wert}" for feld, wert in profil.items())
            )
        if protokoll := self.protokoll_heute(heute):
            teile.append(f"Heute bereits besprochen:\n{protokoll}")
        if bereiche := self.bereiche():
            teile.append(
                "Bereiche mit Notizen, die du mit nachschlagen durchsuchen kannst: "
                + ", ".join(bereiche)
            )
        return "\n\n".join(teile)


_ZUSAMMENFASSUNG_AUFTRAG = (
    "Fasse das folgende Gespräch in höchstens drei Sätzen zusammen: nur was besprochen, "
    "entschieden oder vereinbart wurde, keine Floskeln. Lass weg, was Alfred nicht konnte, "
    "nicht fand oder verweigerte (etwa einen Kontakt, einen Termin, einen Dienst) und wie er "
    "das begründet hat: Das galt nur für den Moment, und im Protokoll hielte Alfred es später "
    "für eine Tatsache. Eine Aktion (Erinnerung, Nachricht, Termin, Routine) ist nur erledigt, "
    "wenn ein Werkzeugergebnis sie bestätigt; sagt Alfred bloß, er tue es, ist nichts passiert, "
    "und das schreibst du so oder gar nicht. War nichts Bleibendes dabei, antworte genau mit: nichts"
)


async def zusammenfassen(client, modell: str, messages: list[dict]) -> str:
    """Kurzfassung eines Gesprächs für das Tagesprotokoll, oder "" wenn es nichts zu sagen gibt.

    Mit Werkzeugaufrufen und Ergebnissen: Am 6.10.2026 stand eine Erinnerung als eingerichtet im
    Protokoll, die Alfred nur angekündigt hatte, und das Protokoll steht im Systemprompt.
    """
    zeilen = []
    for m in messages:
        rolle, inhalt = m.get("role"), m.get("content")
        if rolle == "assistant" and m.get("tool_calls"):
            for aufruf in m["tool_calls"]:
                funktion = aufruf.get("function", {}) if isinstance(aufruf, dict) else {}
                zeilen.append(f"(Alfred ruft {funktion.get('name')} auf: {funktion.get('arguments')})")
        if rolle == "tool":
            text = inhalt if isinstance(inhalt, str) else json.dumps(inhalt, ensure_ascii=False)
            zeilen.append(f"(Ergebnis: {text[:300]})")
        elif rolle in ("user", "assistant") and isinstance(inhalt, str) and inhalt.strip():
            zeilen.append(f"{'Alexander' if rolle == 'user' else 'Alfred'}: {inhalt}")
    gespraech = "\n".join(zeilen) if any(not z.startswith("(") for z in zeilen) else ""
    if not gespraech.strip():
        return ""
    antwort = await client.chat.complete_async(
        model=modell,
        messages=[
            {"role": "system", "content": _ZUSAMMENFASSUNG_AUFTRAG},
            {"role": "user", "content": gespraech},
        ],
        max_tokens=200,
    )
    wahl = antwort.choices[0]
    text = _inhalt_text(wahl.message.content)
    if not text and getattr(wahl, "finish_reason", None) == "length":
        # Ein Denk-Modell kann das ganze Budget fürs Nachdenken verbrauchen; das soll im Log stehen.
        raise RuntimeError(f"{modell} hörte vor dem Text auf (max_tokens erreicht)")
    return "" if text.casefold().rstrip(".") == "nichts" else text


def _inhalt_text(inhalt: object) -> str:
    """Nachrichteninhalt als Text; SDK liefert String oder Liste von Stücken (auch Thinking-Chunks)."""
    if isinstance(inhalt, str):
        return inhalt.strip()
    if isinstance(inhalt, list):
        teile = []
        for stueck in inhalt:
            if isinstance(stueck, str):
                teile.append(stueck)
            elif isinstance(stueck, dict):
                if stueck.get("type") == "text" or ("text" in stueck and stueck.get("type") != "thinking"):
                    teile.append(str(stueck.get("text", "")))
            else:
                if getattr(stueck, "type", None) == "text" or (hasattr(stueck, "text") and getattr(stueck, "type", None) != "thinking"):
                    teile.append(str(getattr(stueck, "text", "")))
        return "\n".join(t for t in teile if t).strip()
    return ""
