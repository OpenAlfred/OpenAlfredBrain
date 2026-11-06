"""Hilfen für die Einrichtung des Mini-PCs: Konfiguration, .env, App-Adresse."""

import json
import os
import stat

import pytest
import yaml

from alfred import config
from alfred.einrichtung import app_adresse, env_setzen, env_wert, konfig_setzen, schluessel_ablauf

KONFIG = """# Alfreds Einstellungen
suche:
  url: "" # leer = Mistrals Websuche
benachrichtigung:
  url: https://ntfy.sh
  thema: geheim-123
  app_url: ""
"""


def test_konfig_setzen_traegt_ein_und_laesst_kommentare_stehen(tmp_path):
    pfad = tmp_path / "alfred.yaml"
    pfad.write_text(KONFIG, encoding="utf-8")
    assert konfig_setzen("suche.url", "http://127.0.0.1:8888", nur_wenn_leer=True, pfad=pfad)
    text = pfad.read_text(encoding="utf-8")
    assert '  url: "http://127.0.0.1:8888" # leer = Mistrals Websuche\n' in text
    assert yaml.safe_load(text)["benachrichtigung"]["thema"] == "geheim-123", "der Rest bleibt"

    # Ein gesetzter Wert bleibt mit --nur-wenn-leer stehen; ohne wird er ersetzt.
    assert not konfig_setzen("suche.url", "http://anders:8888", nur_wenn_leer=True, pfad=pfad)
    assert konfig_setzen("benachrichtigung.app_url", "https://server.tail.ts.net/app", pfad=pfad)
    assert not konfig_setzen("benachrichtigung.app_url", "https://server.tail.ts.net/app", pfad=pfad), "unverändert"
    assert yaml.safe_load(pfad.read_text(encoding="utf-8"))["benachrichtigung"]["app_url"] == "https://server.tail.ts.net/app"
    with pytest.raises(ValueError):
        konfig_setzen("ohne_punkt", "x", pfad=pfad)


def test_ohne_eigene_konfiguration_gilt_die_vorlage(tmp_path, monkeypatch):
    (tmp_path / "alfred.beispiel.yaml").write_text("stimme: vorlage\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert config.konfig_pfad() == tmp_path / "alfred.beispiel.yaml"
    assert config.load_settings().stimme == "vorlage"
    # Schreiben braucht die eigene Datei: aus der Vorlage angelegt, die Vorlage bleibt.
    assert config.konfig_anlegen() == tmp_path / "alfred.yaml"
    (tmp_path / "alfred.yaml").write_text("stimme: eigene\n", encoding="utf-8")
    assert config.load_settings().stimme == "eigene"
    assert config.konfig_anlegen().read_text(encoding="utf-8") == "stimme: eigene\n", "eine vorhandene bleibt"


def test_vorlage_liegt_im_projekt_und_enthaelt_kein_geheimnis():
    vorlage = yaml.safe_load((config.CONFIG_DIR / config.VORLAGE_DATEI).read_text(encoding="utf-8"))
    assert config.Settings.model_validate(vorlage)
    assert vorlage["benachrichtigung"]["thema"] in ("", None), "das ntfy-Thema wirkt wie ein Passwort"


def test_env_setzen_aus_der_vorlage_und_ersetzen(tmp_path):
    env, vorlage = tmp_path / ".env", tmp_path / ".env.example"
    vorlage.write_text("# Schlüssel\nMISTRAL_API_KEY=\nMAIL_ADRESSE=\n", encoding="utf-8")
    assert env_wert(env, "MISTRAL_API_KEY") == ""
    env_setzen(env, vorlage, "MISTRAL_API_KEY", "abc&123|x")
    assert env.read_text(encoding="utf-8") == "# Schlüssel\nMISTRAL_API_KEY=abc&123|x\nMAIL_ADRESSE=\n"
    assert env_wert(env, "MISTRAL_API_KEY") == "abc&123|x"
    env_setzen(env, vorlage, "MISTRAL_API_KEY", "neu")
    env_setzen(env, vorlage, "SPOTIFY_CLIENT_ID", "id")
    assert env.read_text(encoding="utf-8").count("MISTRAL_API_KEY=") == 1 and env_wert(env, "SPOTIFY_CLIENT_ID") == "id"
    if os.name == "posix":
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        env_setzen(env, vorlage, "X", "zwei\nzeilen")


def test_app_adresse_aus_tailscale():
    status = json.dumps({"Self": {"DNSName": "mini-pc.tail1234.ts.net.", "HostName": "mini-pc"}})
    assert app_adresse(status) == "https://mini-pc.tail1234.ts.net/app"
    with pytest.raises(ValueError):
        app_adresse(json.dumps({"BackendState": "NeedsLogin"}))


def test_schluesselablauf_aus_tailscale():
    # Tailscale schreibt Nanosekunden; gelesen wird nur das Datum.
    status = json.dumps({"Self": {"DNSName": "server.tail.ts.net.", "KeyExpiry": "2027-04-14T06:30:15.123456789Z"}})
    assert schluessel_ablauf(status) == "14.04.2027"
    assert schluessel_ablauf(json.dumps({"Self": {"DNSName": "server.tail.ts.net."}})) == "", "Ablauf abgeschaltet"
    assert schluessel_ablauf(json.dumps({"BackendState": "NeedsLogin"})) == ""
