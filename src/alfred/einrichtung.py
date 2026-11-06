"""Hilfen für deploy/einrichten.sh: Werte in config/alfred.yaml und .env setzen, App-Adresse finden.

Der Mini-PC soll sich möglichst ohne Handarbeit einrichten lassen. Was das Skript in Dateien
schreiben muss, steht hier statt in sed-Zeilen: Es lässt sich testen, fasst Kommentare nicht an
und bekommt Geheimnisse über eine Umgebungsvariable statt über die Kommandozeile, damit sie in
keiner Prozessliste auftauchen.

    python -m alfred.einrichtung konfig suche.url http://127.0.0.1:8888 --nur-wenn-leer
    python -m alfred.einrichtung env-gesetzt MISTRAL_API_KEY        # Rückgabewert 0 oder 1
    ALFRED_WERT=... python -m alfred.einrichtung env-setzen MISTRAL_API_KEY
    tailscale status --json | python -m alfred.einrichtung app-adresse
    tailscale status --json | python -m alfred.einrichtung tailscale-ablauf  # leer ohne Ablauf
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

from alfred.config import PROJEKT_DIR, konfig_anlegen, werte_setzen


def konfig_setzen(name: str, wert: str, nur_wenn_leer: bool = False, pfad: Path | None = None) -> bool:
    """Setzt abschnitt.schluessel in config/alfred.yaml (legt sie aus der Vorlage an); True, wenn geändert."""
    abschnitt, _, schluessel = name.partition(".")
    if not abschnitt or not schluessel:
        raise ValueError(f"Name als abschnitt.schluessel angeben, nicht {name!r}")
    pfad = pfad or konfig_anlegen()
    text = pfad.read_text(encoding="utf-8")
    jetzt = ((yaml.safe_load(text) or {}).get(abschnitt) or {}).get(schluessel)
    if nur_wenn_leer and jetzt not in (None, ""):
        return False
    if jetzt == wert:
        return False
    pfad.write_text(werte_setzen(text, abschnitt, {schluessel: json.dumps(wert, ensure_ascii=False)}), encoding="utf-8")
    return True


def env_wert(pfad: Path, schluessel: str) -> str:
    if not pfad.exists():
        return ""
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if zeile.startswith(f"{schluessel}="):
            return zeile.split("=", 1)[1].strip().strip("\"'")
    return ""


def env_setzen(pfad: Path, vorlage: Path, schluessel: str, wert: str) -> None:
    """Trägt schluessel=wert in .env ein; legt sie aus der Vorlage an. Lesbar nur für den Besitzer."""
    if "\n" in wert or "\r" in wert:
        raise ValueError("Der Wert darf keinen Zeilenumbruch enthalten.")
    text = pfad.read_text(encoding="utf-8") if pfad.exists() else (vorlage.read_text(encoding="utf-8") if vorlage.exists() else "")
    zeile = f"{schluessel}={wert}"
    if re.search(rf"^{re.escape(schluessel)}=.*$", text, re.M):
        text = re.sub(rf"^{re.escape(schluessel)}=.*$", lambda _: zeile, text, count=1, flags=re.M)
    else:
        text = text + ("" if not text or text.endswith("\n") else "\n") + zeile + "\n"
    pfad.write_text(text, encoding="utf-8")
    pfad.chmod(0o600)


def app_adresse(status_json: str, pfad: str = "/app") -> str:
    """Die Adresse der App im Tailnet aus `tailscale status --json`, etwa https://server.tailnet.ts.net/app."""
    name = (json.loads(status_json).get("Self") or {}).get("DNSName", "").rstrip(".")
    if not name:
        raise ValueError("Tailscale nennt keinen Rechnernamen; ist der Rechner angemeldet?")
    return f"https://{name}{pfad}"


def schluessel_ablauf(status_json: str) -> str:
    """Wann der Tailscale-Schlüssel dieses Rechners abläuft (TT.MM.JJJJ); leer, wenn der Ablauf aus ist.

    Mit Ablauf meldet sich der Server nach 180 Tagen aus dem Tailnet ab, und vom Handy ist Alfred
    nicht mehr erreichbar. In der Tailscale-Verwaltung lässt sich das je Gerät abschalten.
    """
    ablauf = (json.loads(status_json).get("Self") or {}).get("KeyExpiry") or ""
    return datetime.strptime(ablauf[:10], "%Y-%m-%d").strftime("%d.%m.%Y") if ablauf else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Hilfen für deploy/einrichten.sh")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    konfig = befehle.add_parser("konfig", help="Wert in config/alfred.yaml setzen")
    konfig.add_argument("name", help="abschnitt.schluessel, etwa suche.url")
    konfig.add_argument("wert")
    konfig.add_argument("--nur-wenn-leer", action="store_true", help="einen vorhandenen Wert stehen lassen")
    befehle.add_parser("konfig-anlegen", help="config/alfred.yaml aus der Vorlage anlegen, falls sie fehlt")
    gesetzt = befehle.add_parser("env-gesetzt", help="Rückgabewert 0, wenn der Schlüssel in .env einen Wert hat")
    gesetzt.add_argument("schluessel")
    setzen = befehle.add_parser("env-setzen", help="Wert aus der Umgebungsvariable ALFRED_WERT in .env eintragen")
    setzen.add_argument("schluessel")
    befehle.add_parser("app-adresse", help="App-Adresse aus `tailscale status --json` (Standardeingabe)")
    befehle.add_parser("tailscale-ablauf", help="Ablauf des Tailscale-Schlüssels aus `tailscale status --json`; leer ohne Ablauf")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    env = PROJEKT_DIR / ".env"

    if args.befehl == "konfig":
        geaendert = konfig_setzen(args.name, args.wert, args.nur_wenn_leer)
        print(f"{args.name}: {args.wert}" if geaendert else f"{args.name}: bleibt, wie es ist")
    elif args.befehl == "konfig-anlegen":
        konfig_anlegen()
    elif args.befehl == "env-gesetzt":
        sys.exit(0 if env_wert(env, args.schluessel) else 1)
    elif args.befehl == "env-setzen":
        wert = os.environ.get("ALFRED_WERT", "").strip()
        if not wert:
            sys.exit("ALFRED_WERT ist leer; nichts eingetragen.")
        env_setzen(env, PROJEKT_DIR / ".env.example", args.schluessel, wert)
        print(f"{args.schluessel} in .env eingetragen.")
    elif args.befehl == "app-adresse":
        print(app_adresse(sys.stdin.read()))
    elif args.befehl == "tailscale-ablauf":
        print(schluessel_ablauf(sys.stdin.read()))


if __name__ == "__main__":
    main()
