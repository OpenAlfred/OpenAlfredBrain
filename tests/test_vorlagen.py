"""Die Vorlagen im Repository sind der Standard für alle, die Alfred herunterladen.

Die eigenen Dateien (config/alfred.yaml, config/persoenlichkeit.yaml) stehen in .gitignore. Fehlen
sie, gelten die Vorlagen. Deshalb dürfen die Vorlagen keine persönlichen Angaben enthalten.
"""

import pytest
import yaml

from alfred import config
from alfred.persona import Persona, load_persona


@pytest.mark.parametrize("datei", [config.VORLAGE_DATEI, config.PERSONA_VORLAGE])
def test_vorlagen_enthalten_keine_persoenlichen_angaben(datei):
    text = (config.CONFIG_DIR / datei).read_text(encoding="utf-8").lower()
    assert "alexander" not in text and "jarvis" not in text


def test_standardvorlage_nennt_die_standardstimme():
    vorlage = config.load_settings(config.CONFIG_DIR / config.VORLAGE_DATEI)
    assert vorlage.stimme == "gb_oliver_neutral"


def test_persoenlichkeitsvorlage_ist_gueltig():
    daten = yaml.safe_load((config.CONFIG_DIR / config.PERSONA_VORLAGE).read_text(encoding="utf-8"))
    assert Persona.model_validate(daten).name == "Alfred"


def test_ohne_eigene_persoenlichkeit_gilt_die_vorlage(tmp_path, monkeypatch):
    vorlage = (config.CONFIG_DIR / config.PERSONA_VORLAGE).read_text(encoding="utf-8")
    (tmp_path / config.PERSONA_VORLAGE).write_text(vorlage, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert config.persona_pfad() == tmp_path / config.PERSONA_VORLAGE
    assert load_persona().name == "Alfred"


def test_eigene_persoenlichkeit_hat_vorrang(tmp_path, monkeypatch):
    (tmp_path / config.PERSONA_VORLAGE).write_text("name: Vorlage\nnutzer: Nutzer\ncharakter: x\n", encoding="utf-8")
    (tmp_path / config.PERSONA_DATEI).write_text("name: Eigene\nnutzer: Test\ncharakter: x\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert config.persona_pfad() == tmp_path / config.PERSONA_DATEI
    assert load_persona().name == "Eigene"
