"""Sicherung von Gedächtnis und Zugängen, auf Windows wie Linux: python -m alfred.sichern

Packt gedaechtnis/ (ohne den Testordner) und die Zugänge aus config/ in ein Zip mit Datum,
standardmäßig nach ~/alfred-sicherung, und löscht dort Sicherungen, die älter als 14 Tage
sind. Das Archiv enthält persönliche Daten und Zugangsdaten: entsprechend ablegen.

    python -m alfred.sichern                     -> ~/alfred-sicherung/alfred-JJJJ-MM-TT-HHMM.zip
    python -m alfred.sichern --ziel E:\\Alfred   -> anderer Ordner, etwa ein USB-Stick
    python -m alfred.sichern --behalten 30       -> 30 Tage aufheben

Täglich: unter Linux der systemd-Timer alfred-sicherung (deploy/einrichten.sh), unter Windows
die Aufgabenplanung (Befehl im Handbuch).
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from alfred.config import PROJEKT_DIR

ZUGAENGE = (
    "config/alfred.yaml",
    "config/persoenlichkeit.yaml",
    "config/google_client_secret.json",
    "config/google_token.json",
    "config/whatsapp.db",
    "config/whatsapp_bridge.token",
    "config/stimme.json",
    "config/spotify_token.json",
)
ORDNER = ("gedaechtnis", "config/stimmen")
AUSGENOMMEN = ("gedaechtnis/selbsttest",)
PRAEFIX = "alfred-"


def standardziel() -> Path:
    return Path.home() / "alfred-sicherung"


def _dateien(projekt: Path):
    for name in ZUGAENGE:
        pfad = projekt / name
        if pfad.exists():
            yield pfad
    for name in ORDNER:
        ordner = projekt / name
        if not ordner.exists():
            continue
        for pfad in sorted(ordner.rglob("*")):
            relativ = pfad.relative_to(projekt).as_posix()
            if pfad.is_file() and not any(relativ.startswith(aus) for aus in AUSGENOMMEN):
                yield pfad


def sichern(ziel: Path | None = None, behalten_tage: int = 14, projekt: Path = PROJEKT_DIR, jetzt: datetime | None = None) -> Path:
    """Schreibt das Archiv und räumt alte auf; gibt den Pfad des neuen Archivs zurück."""
    ziel = ziel or standardziel()
    jetzt = jetzt or datetime.now().astimezone()
    ziel.mkdir(parents=True, exist_ok=True)
    archiv = ziel / f"{PRAEFIX}{jetzt:%Y-%m-%d-%H%M}.zip"
    vorlaeufig = archiv.with_suffix(".teil")
    with zipfile.ZipFile(vorlaeufig, "w", zipfile.ZIP_DEFLATED) as zip_:
        for pfad in _dateien(projekt):
            zip_.write(pfad, pfad.relative_to(projekt).as_posix())
    vorlaeufig.replace(archiv)
    grenze = jetzt - timedelta(days=behalten_tage)
    for alt in ziel.glob(f"{PRAEFIX}*.zip"):
        if alt != archiv and _zeitpunkt(alt) is not None and _zeitpunkt(alt) < grenze:
            alt.unlink()
    return archiv


def _zeitpunkt(archiv: Path) -> datetime | None:
    """Der Zeitpunkt aus dem Dateinamen; alte tgz-Namen ohne Uhrzeit zählen als Mitternacht."""
    rest = archiv.stem.removeprefix(PRAEFIX)
    for muster in ("%Y-%m-%d-%H%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(rest, muster).astimezone()
        except ValueError:
            continue
    return None


def letzte_sicherung(ziel: Path | None = None) -> datetime | None:
    ziel = ziel or standardziel()
    if not ziel.exists():
        return None
    zeiten = [z for z in (_zeitpunkt(p) for p in ziel.glob(f"{PRAEFIX}*.*")) if z is not None]
    return max(zeiten) if zeiten else None


def einspielen(archiv: Path, projekt: Path = PROJEKT_DIR, ueberschreiben: bool = False) -> list[str]:
    """Entpackt eine Sicherung ins Projekt, etwa beim Umzug auf den Mini-PC; gibt die Dateien zurück.

    Vorhandene Dateien bleiben stehen, außer mit `ueberschreiben`. Pfade, die aus dem Projekt
    hinausführen, werden abgewiesen; das Archiv ist zwar das eigene, aber sicher ist sicher.
    """
    eingespielt = []
    with zipfile.ZipFile(archiv) as zip_:
        for eintrag in zip_.infolist():
            if eintrag.is_dir():
                continue
            ziel = (projekt / eintrag.filename).resolve()
            if projekt.resolve() not in ziel.parents:
                raise ValueError(f"Pfad im Archiv führt aus dem Projekt hinaus: {eintrag.filename}")
            if ziel.exists() and not ueberschreiben:
                continue
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_bytes(zip_.read(eintrag))
            eingespielt.append(eintrag.filename)
    return eingespielt


def main() -> None:
    parser = argparse.ArgumentParser(description="Sichert Alfreds Gedächtnis und Zugänge als Zip, oder spielt eine Sicherung ein.")
    parser.add_argument("--ziel", type=Path, default=None, help=f"Zielordner, Vorgabe {standardziel()}")
    parser.add_argument("--behalten", type=int, default=14, help="so viele Tage aufheben (Vorgabe 14)")
    parser.add_argument("--einspielen", type=Path, metavar="ARCHIV", help="diese Sicherung ins Projekt entpacken (Umzug)")
    parser.add_argument("--ueberschreiben", action="store_true", help="beim Einspielen vorhandene Dateien ersetzen")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.einspielen:
        dateien = einspielen(args.einspielen, ueberschreiben=args.ueberschreiben)
        print(f"{len(dateien)} Dateien eingespielt" + (":" if dateien else "."))
        for name in dateien:
            print(f"  {name}")
        if not dateien:
            print("Nichts zu tun; vorhandene Dateien bleiben stehen (--ueberschreiben ersetzt sie).")
        return
    archiv = sichern(args.ziel, args.behalten)
    print(f"Gesichert nach {archiv} ({archiv.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
