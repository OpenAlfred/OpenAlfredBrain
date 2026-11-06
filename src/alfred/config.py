"""Lädt config/alfred.yaml: Modelle, Stimme, Gesprächsverhalten, Wachwort, Räume, Gedächtnis, Kalender.

config/alfred.yaml ist die eigene Datei jedes Rechners und nicht im Git: Sie enthält das
ntfy-Thema (wirkt wie ein Passwort), Gerätenamen und Schwellen. Im Repository liegt die Vorlage
config/alfred.beispiel.yaml; fehlt die eigene Datei (frisch geklont), gilt die Vorlage. So
überschreibt weder ein Klonen noch ein git pull die eigenen Einstellungen, und die Sicherung
bringt sie beim Umzug mit.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import time
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Liegt neben src/. Für eine andere Ablage ALFRED_CONFIG_DIR setzen.
CONFIG_DIR = Path(os.environ.get("ALFRED_CONFIG_DIR") or Path(__file__).resolve().parents[2] / "config")
PROJEKT_DIR = CONFIG_DIR.parent
KONFIG_DATEI = "alfred.yaml"
VORLAGE_DATEI = "alfred.beispiel.yaml"
PERSONA_DATEI = "persoenlichkeit.yaml"
PERSONA_VORLAGE = "persoenlichkeit.beispiel.yaml"


class Modelle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sprecher: str = "mistral-small-2603"
    denker: str = "zai-glm-5-3"
    werkzeuge: str = "mistral-small-2603"
    transkription: str = "voxtral-mini-transcribe-realtime-2602"
    sprachausgabe: str = "voxtral-mini-tts-2603"
    sprachnachricht: str = "voxtral-mini-latest"  # hört Sprachnachrichten aus der App ab (fertige Dateien)


class Gespraech(BaseModel):
    model_config = ConfigDict(extra="forbid")

    begruessung: bool = True
    unterbrechen_ab_woertern: int = Field(3, ge=1)
    stoppwoerter: list[str] = Field(
        default_factory=lambda: ["stopp", "stop", "halt", "warte", "moment", "alfred"]
    )
    vergessen_nach_minuten: float = Field(15, ge=0)
    # Klingt ein Satz unfertig, wartet Alfred höchstens so lange Stille ab, bevor er antwortet.
    pause_max_sekunden: float = Field(1.5, gt=0)
    # Im Zuhör-Modus ("Lass mich ausreden", "Hör mal zu", beim Diktieren) antwortet er erst nach so
    # viel Stille; Denkpausen darunter unterbrechen ihn nicht.
    zuhoeren_pause_sekunden: float = Field(5.0, gt=0, le=15)
    # Transkripte in anderen Sprachen werden verworfen; die Erkennung kippt bei Undeutlichem sonst
    # ins Spanische oder Niederländische, und das Modell nimmt das ernst.
    sprachen: list[str] = Field(default_factory=lambda: ["de", "en"], min_length=1)


class Wachwort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrasen: list[str] = Field(default_factory=lambda: ["alfred"], min_length=1)
    offen_sekunden: float = Field(10.0, gt=0)  # so lange zählt nach dem Gesprochenen jede Stimme
    # So lange bleibt Alfred insgesamt ansprechbar; nach offen_sekunden nur noch für Alexanders
    # Stimme (nur mit stimmerkennung.aktiv). 0 oder kleiner als offen_sekunden schaltet es ab.
    gespraech_sekunden: float = Field(120.0, ge=0)
    modell: str = "vosk-model-small-de-0.15"
    # Bereitschaft (Standby) beim Start: Alfred reagiert nur, wenn sein Name fällt, und hört nach
    # jeder Antwort weg. Per Stimme umschaltbar ("Alfred, Standby" / "Standby aus").
    bereitschaft: bool = False
    # In Bereitschaft: so lange nach Alfreds Antwort offen, für ein schnelles "Danke" oder "Und morgen?".
    bereitschaft_nachlauf_sekunden: float = Field(3.0, ge=0)
    # In Bereitschaft: so lange nach einer Frage Alfreds offen, damit die Antwort kein Wachwort braucht.
    rueckfrage_sekunden: float = Field(8.0, ge=0)


class Raum(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mikrofon: str | None = None
    lautsprecher: str | None = None


class Jabra(BaseModel):
    """Grüne Hörer-LED am Jabra Speak 510 im Raum-Modus: an, wenn Alfred wach ist, blinkend beim Nachdenken.

    Masken gibt es bewusst nicht: Außer Off-Hook schalten die Bits des Geräts das Mikrofon stumm
    (docs/erkenntnisse.md, 9.10.2026).
    """

    model_config = ConfigDict(extra="forbid")

    led_aktiv: bool = True
    # Beim Nachdenken jeweils so lange an und aus; 0,3 s war Alexander zu hektisch (9.10.2026).
    blinken_sekunden: float = Field(0.5, ge=0.1, le=5)
    # USB-Kennung; ein Speak 510 anderer Revision zeigt python -m alfred.jabra_led --info.
    vendor_id: int = 0x0B0E
    product_id: int = 0x0422

    @field_validator("vendor_id", "product_id", mode="before")
    @classmethod
    def _parse_hex(cls, wert: Any) -> Any:
        if isinstance(wert, str):
            wert = wert.strip()
            return int(wert, 16) if wert.lower().startswith("0x") else int(wert)
        return wert


class Gedaechtnis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordner: str = "gedaechtnis"


class Kalender(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vorlauf_minuten: int = Field(10, ge=0)
    client_secret: str = "config/google_client_secret.json"
    token: str = "config/google_token.json"


class Wetter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ort: str = ""  # Standardort für die Vorhersage; leer = nur mit Ort im Satz


class WhatsApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://127.0.0.1:7861"
    token: str = "config/whatsapp_bridge.token"  # existiert erst, wenn die Brücke eingerichtet ist
    landesvorwahl: str = "49"  # für Nummern, die mit 0 beginnen
    # Nur Windows: Läuft die Brücke nicht, startet Alfred sie selbst (unter Linux macht das systemd).
    # Auf false, sobald die Brücke auf einem anderen Rechner mit derselben Kopplung läuft.
    bruecke_starten: bool = True


class Benachrichtigung(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = ""  # ntfy-Server, z. B. https://ntfy.sh oder http://mini-pc:8080; leer = aus
    thema: str = ""  # Themenname, den die App abonniert; bei ntfy.sh lang und zufällig wählen
    token: str = ""  # Zugangstoken, falls der Server eines verlangt
    # Adresse der App vom Handy aus (über Tailscale), für Freigabe-Knöpfe im Push; leer = keine Knöpfe
    app_url: str = ""


class Spotify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = "config/spotify_token.json"  # entsteht bei python -m alfred.musik --einrichten
    geraet: str = ""  # Namensteil des Spotify-Connect-Geräts, z. B. "mini-pc"; leer = das aktive


class Suche(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = ""  # eigene SearXNG-Instanz, z. B. http://127.0.0.1:8888; leer = Mistrals Websuche


class Stimmerkennung(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Aus, weil sie Alexander zu oft abwies: eigene Sätze ab 0,23 bei Schwelle 0,36, Freigaben
    # verweigert (docs/erkenntnisse.md, 20.10.2026). Dann schließt die Schleuse nach offen_sekunden,
    # und heikle Aktionen sichert das Codewort (codewort.py). Ein Profil allein schaltet nichts ein.
    aktiv: bool = False
    profil: str = "config/stimme.json"  # Alexanders Stimmvektor; fehlt er, wird nicht geprüft
    # Sprechermodell: wespeaker-resnet34 (ONNX, sprechermodell.py) oder vosk-model-spk-0.4, das
    # Stimmen schlechter trennt und rund 20-mal langsamer ist. Ein Profil gilt nur für sein Modell.
    modell: str = "wespeaker-resnet34"
    schwelle: float = Field(0.65, ge=0, le=1)  # Ähnlichkeit, ab der die Stimme als Alexanders gilt
    # Auch das Wachwort nur in Alexanders Stimme annehmen (Raum-Modus). Aus, weil „Alfred" allein
    # für eine Stimmprüfung zu kurz ist: Vosk wies Alexander vom 4. bis 9.10.2026 bei 15 von 32
    # Weckversuchen ab, je nach Lautstärke und Tonlage, und WeSpeaker ist beim Wort allein kaum
    # verlässlicher (docs/erkenntnisse.md, 9.10.2026).
    wachwort: bool = False
    # Mildere Schwelle für kurzes Audio: Sätze in Gesprächspausen (Stimmfilter), mit `wachwort`
    # auch das Wachwort.
    schwelle_wachwort: float = Field(0.5, ge=0, le=1)


class Modellpreis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eingabe: float = 0.0  # Dollar je Million Token; 0 = Standardpreis unten, sonst unbekannt
    ausgabe: float = 0.0
    eingabe_cache: float = 0.0  # zwischengespeicherte Eingabe; 0 = wie eingabe


# Listenpreise von docs.mistral.ai, Stand 6.10.2026. Gelten, wo config/alfred.yaml 0 oder nichts sagt.
STANDARD_MODELLPREISE = {
    "mistral-small-2603": Modellpreis(eingabe=0.15, ausgabe=0.6),
    "zai-glm-5-3": Modellpreis(eingabe=1.4, ausgabe=4.4, eingabe_cache=0.14),
}
STANDARD_SPRACHAUSGABE_PRO_MILLION_ZEICHEN = 16.0  # voxtral-mini-tts-2603


class Preise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transkription_pro_minute: float = 0.006
    websuche_pro_aufruf: float = 0.03
    sprachausgabe_pro_million_zeichen: float = 0.0  # 0 = Listenpreis
    modelle: dict[str, Modellpreis] = Field(default_factory=dict)

    def modell(self, name: str) -> Modellpreis | None:
        """Der Preis eines Modells: aus der Konfiguration, sonst der Listenpreis; None = unbekannt."""
        preis = self.modelle.get(name)
        if preis and (preis.eingabe or preis.ausgabe):
            return preis
        return STANDARD_MODELLPREISE.get(name)

    def sprachausgabe(self) -> float:
        """Dollar je Million Zeichen der Sprachausgabe: aus der Konfiguration, sonst der Listenpreis."""
        return self.sprachausgabe_pro_million_zeichen or STANDARD_SPRACHAUSGABE_PRO_MILLION_ZEICHEN


class Proaktiv(BaseModel):
    """Grenzen für Ungefragtes (Routinen, Briefings, Vorschläge). Angefordertes wie Timer, eigene
    Erinnerungen, Denker- und Auftragsergebnisse kommt immer durch. Die Regeln stehen in ereignisse.py."""

    model_config = ConfigDict(extra="forbid")

    max_pro_tag: int = Field(6, ge=0)  # ungefragte Ansagen am Tag; Dringendes darf darüber hinaus
    ruhe_von: str = "22:00"  # in der Ruhezeit nichts Ungefragtes, auch nichts Dringendes
    ruhe_bis: str = "07:00"  # gleiche Zeiten = keine Ruhezeit
    aufschieben_minuten: int = Field(30, ge=1)  # "Nicht jetzt": so viel später noch einmal
    # Je Modus: alles, nur_dringendes oder nichts. Nicht genannte Modi erlauben alles.
    modi: dict[str, Literal["alles", "nur_dringendes", "nichts"]] = Field(
        default_factory=lambda: {"feierabend": "nur_dringendes"}
    )

    @field_validator("ruhe_von", "ruhe_bis", mode="before")
    @classmethod
    def _uhrzeit(cls, wert):
        # YAML liest 22:00 ohne Anführungszeichen als Zahl (22 mal 60 Minuten); das fangen wir ab.
        if isinstance(wert, int) and not isinstance(wert, bool):
            wert = f"{wert // 60:02d}:{wert % 60:02d}"
        try:
            stunde, minute = (int(teil) for teil in str(wert).strip().split(":"))
            time(stunde, minute)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Uhrzeit erwartet HH:MM, nicht {wert!r}") from e
        return f"{stunde:02d}:{minute:02d}"

    @property
    def ruhe_von_zeit(self) -> time:
        return time(*(int(teil) for teil in self.ruhe_von.split(":")))

    @property
    def ruhe_bis_zeit(self) -> time:
        return time(*(int(teil) for teil in self.ruhe_bis.split(":")))


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stimme: str = Field("gb_oliver_neutral", min_length=1)
    modelle: Modelle = Field(default_factory=Modelle)
    gespraech: Gespraech = Field(default_factory=Gespraech)
    proaktiv: Proaktiv = Field(default_factory=Proaktiv)
    wachwort: Wachwort = Field(default_factory=Wachwort)
    raeume: dict[str, Raum] = Field(default_factory=dict)
    jabra: Jabra = Field(default_factory=Jabra)
    gedaechtnis: Gedaechtnis = Field(default_factory=Gedaechtnis)
    kalender: Kalender = Field(default_factory=Kalender)
    wetter: Wetter = Field(default_factory=Wetter)
    whatsapp: WhatsApp = Field(default_factory=WhatsApp)
    benachrichtigung: Benachrichtigung = Field(default_factory=Benachrichtigung)
    preise: Preise = Field(default_factory=Preise)
    stimmerkennung: Stimmerkennung = Field(default_factory=Stimmerkennung)
    suche: Suche = Field(default_factory=Suche)
    spotify: Spotify = Field(default_factory=Spotify)

    def pfad(self, relativ: str) -> Path:
        """Ein Pfad aus der Konfiguration, relativ zum Projektordner."""
        return PROJEKT_DIR / relativ


def konfig_pfad() -> Path:
    """Die eigene config/alfred.yaml; fehlt sie, die Vorlage."""
    eigene = CONFIG_DIR / KONFIG_DATEI
    return eigene if eigene.exists() else CONFIG_DIR / VORLAGE_DATEI


def persona_pfad() -> Path:
    """Die eigene config/persoenlichkeit.yaml; fehlt sie, die Vorlage."""
    eigene = CONFIG_DIR / PERSONA_DATEI
    return eigene if eigene.exists() else CONFIG_DIR / PERSONA_VORLAGE


def konfig_anlegen() -> Path:
    """Legt config/alfred.yaml aus der Vorlage an, wenn sie fehlt, und gibt den Pfad zurück."""
    eigene = CONFIG_DIR / KONFIG_DATEI
    if not eigene.exists():
        shutil.copyfile(CONFIG_DIR / VORLAGE_DATEI, eigene)
    return eigene


def load_settings(path: Path | None = None) -> Settings:
    path = path or konfig_pfad()
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def werte_setzen(text: str, abschnitt: str, werte: dict[str, object]) -> str:
    """Trägt Werte im Abschnitt einer YAML-Datei ein, ohne Kommentare und Rest anzufassen.

    Die Werte stehen danach so in der Datei, wie sie übergeben werden; Zeichenketten also mit
    Anführungszeichen übergeben, wo YAML sie braucht.
    """
    zeilenende = "\r\n" if "\r\n" in text else "\n"
    zeilen = text.splitlines(keepends=True)
    kopf = next((i for i, z in enumerate(zeilen) if z.rstrip("\r\n") == f"{abschnitt}:"), None)
    if kopf is None:
        if zeilen and not zeilen[-1].endswith(("\n", "\r")):
            zeilen[-1] += zeilenende
        zeilen.append(f"{abschnitt}:{zeilenende}")
        kopf = len(zeilen) - 1
    ende = kopf + 1
    while ende < len(zeilen) and (not zeilen[ende].strip() or zeilen[ende][0] in " \t"):
        ende += 1
    while ende > kopf + 1 and not zeilen[ende - 1].strip():
        ende -= 1
    for schluessel, wert in werte.items():
        muster = re.compile(rf"^(\s+{re.escape(schluessel)}:[ \t]*)([^#\r\n]*?)([ \t]*#[^\r\n]*)?(\r?\n)?$")
        for i in range(kopf + 1, ende):
            if treffer := muster.match(zeilen[i]):
                zeilen[i] = f"{treffer.group(1)}{wert}{treffer.group(3) or ''}{treffer.group(4) or ''}"
                break
        else:
            if not zeilen[ende - 1].endswith(("\n", "\r")):
                zeilen[ende - 1] += zeilenende
            zeilen.insert(ende, f"  {schluessel}: {wert}{zeilenende}")
            ende += 1
    return "".join(zeilen)
