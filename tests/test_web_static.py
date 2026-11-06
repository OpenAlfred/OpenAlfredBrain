"""Die Dateien der App (web_static/) hängen sauber zusammen: nichts fehlt, kein Symbol ist erfunden, nichts bleibt alt im Browser."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alfred import web
from alfred.config import load_settings
from alfred.persona import load_persona
from alfred.werkzeuge.uebersicht import ANZEIGE
from test_uebersicht import ressourcen

STATIC = Path(web.__file__).parent / "web_static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
SW = (STATIC / "sw.js").read_text(encoding="utf-8")
JS = sorted(STATIC.glob("js/**/*.js"))
ANSICHTEN = re.findall(r'\{ id: "([a-z]+)", titel:', (STATIC / "js" / "app.js").read_text(encoding="utf-8"))


def test_index_verweist_nur_auf_vorhandene_dateien():
    verweise = re.findall(r'(?:href|src)="(static/[^"]+)"', HTML)
    assert verweise, "index.html bindet Stile und Skripte ein"
    fehlend = [v for v in verweise if not (STATIC / v.removeprefix("static/")).exists()]
    assert not fehlend


def test_service_worker_kennt_jede_datei_der_oberflaeche():
    """Sonst fehlt sie offline: jede CSS- und JS-Datei steht im SHELL des Service Workers, und nichts steht dort ohne Datei."""
    shell = re.findall(r'"((?:static/[^"]+)|\./|manifest\.webmanifest)"', SW)
    for eintrag in shell:
        if eintrag.startswith("static/"):
            assert (STATIC / eintrag.removeprefix("static/")).exists(), f"{eintrag} steht im SHELL, existiert aber nicht"
    dateien = {f"static/{p.relative_to(STATIC).as_posix()}" for p in [*STATIC.glob("css/*.css"), *STATIC.glob("js/**/*.js")]}
    assert dateien <= set(shell), f"nicht im SHELL: {sorted(dateien - set(shell))}"


def test_module_importieren_nur_vorhandenes():
    for datei in JS:
        for ziel in re.findall(r'from\s+"(\.[^"]+)"', datei.read_text(encoding="utf-8")):
            assert (datei.parent / ziel).resolve().exists(), f"{datei.name} importiert {ziel}"


def test_jede_ansicht_hat_ihr_modul_mit_mount():
    assert len(ANSICHTEN) >= 10
    for ansicht in ANSICHTEN:
        modul = STATIC / "js" / "views" / f"{ansicht}.js"
        assert modul.exists(), f"Ansicht {ansicht} ohne Datei"
        assert "export function mount(" in modul.read_text(encoding="utf-8"), f"{ansicht}.js exportiert kein mount"


def test_jedes_symbol_im_code_steht_im_sprite():
    """icon("name") und symbol: "name" in den Skripten, dazu die Symbole der Fähigkeiten-Gruppen, müssen im Sprite stehen."""
    sprite = set(re.findall(r'id="i-([a-z0-9-]+)"', HTML))
    verwendet = set(ANZEIGE[g].symbol for g in ANZEIGE)
    for datei in JS:
        text = datei.read_text(encoding="utf-8")
        verwendet |= set(re.findall(r'icon\(\s*"([a-z0-9-]+)"', text))
        verwendet |= set(re.findall(r'symbol:\s*"([a-z0-9-]+)"', text))
    unbekannt = verwendet - sprite
    assert not unbekannt, f"Symbole ohne Eintrag in index.html: {sorted(unbekannt)}"


def test_manifest_ist_gueltig_und_nennt_vorhandene_symbole():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["display"] == "standalone" and manifest["lang"] == "de"
    for symbol in manifest["icons"]:
        assert (STATIC / symbol["src"].removeprefix("static/")).exists()


@pytest.fixture
def client(tmp_path, monkeypatch):
    res = ressourcen(tmp_path)
    monkeypatch.setattr(web, "build_ressourcen", lambda settings, persona: res)
    monkeypatch.setattr(web, "require_api_key", lambda: "x")
    with TestClient(web.create_app(load_settings(), load_persona(), logordner=tmp_path / "logs")) as c:
        yield c


def test_der_browser_fragt_vor_jeder_datei_nach(client):
    """Ohne no-cache hält der Browser Skripte nach Zeitstempel-Faustregel stundenlang für frisch (Update-Mischmasch)."""
    for pfad in ("/", "/manifest.webmanifest", "/sw.js", "/static/js/app.js", "/static/css/tokens.css"):
        antwort = client.get(pfad)
        assert antwort.status_code == 200, pfad
        assert antwort.headers["cache-control"] == "no-cache", pfad
    assert "etag" in client.get("/static/js/app.js").headers, "mit ETag kostet das Nachfragen nur eine 304-Antwort"
