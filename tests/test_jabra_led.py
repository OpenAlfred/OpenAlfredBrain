"""Grüne Hörer-LED am Jabra Speak 510: schaltet nie das Mikrofon stumm und hält Alfred nie auf."""

import asyncio
import threading
import time

import pytest
from pydantic import ValidationError

from alfred.config import Jabra
from alfred.jabra_led import AUS, GRUEN, REPORT_ID, JabraDevice, JabraLedController


class FakeHid:
    """Steht für hid.device und merkt sich, was ans Gerät ging."""

    def __init__(self, fehler: bool = False):
        self.geschrieben: list[bytes] = []
        self.pfad = None
        self.offen = False
        self.fehler = fehler

    def open_path(self, pfad):
        self.pfad = pfad
        self.offen = True

    def set_nonblocking(self, modus):
        pass

    def write(self, daten):
        if self.fehler:
            raise OSError("USB weg")
        self.geschrieben.append(bytes(daten))
        return len(daten)

    def close(self):
        self.offen = False


def verbunden(hid_geraet: FakeHid) -> JabraDevice:
    geraet = JabraDevice()
    geraet._dev = hid_geraet
    geraet._is_connected = True
    return geraet


@pytest.fixture
def hid(monkeypatch):
    """hidapi mit einem eingesteckten Jabra; der Linux-Weg über /dev/hidraw bleibt aus dem Spiel."""
    hid = pytest.importorskip("hid")
    monkeypatch.setattr(JabraDevice, "_connect_linux_hidraw", lambda self: False)
    stand = {
        "sammlungen": [{"path": b"consumer", "usage_page": 0x0C}, {"path": b"telephony", "usage_page": 0x0B}],
        "geraet": FakeHid(),
        "gefragt": [],
    }

    def enumerate_(vid, pid):
        stand["gefragt"].append((vid, pid))
        return stand["sammlungen"]

    monkeypatch.setattr(hid, "enumerate", enumerate_)
    monkeypatch.setattr(hid, "device", lambda: stand["geraet"])
    return stand


def test_kein_report_schaltet_das_mikrofon_stumm():
    """Mute, Ring, Hold und Microphone (der rote Ring) schalten das Mikrofon stumm (9.10.2026)."""
    for maske in range(256):
        hid_geraet = FakeHid()
        verbunden(hid_geraet).write_report(maske)
        assert hid_geraet.geschrieben == [bytes([REPORT_ID, maske & 0x01, 0x00])]


def test_geraet_wird_ueber_die_usb_kennung_gefunden(hid):
    geraet = JabraDevice()
    assert geraet.connect() and geraet.is_connected
    assert hid["gefragt"] == [(0x0B0E, 0x0422)]
    # Unter Windows ist jede Sammlung ein eigenes Gerät; Report 0x03 liegt in der Telephony-Sammlung.
    assert hid["geraet"].pfad == b"telephony"


def test_ohne_geraet_laeuft_alles_weiter(hid):
    hid["sammlungen"] = []
    geraet = JabraDevice()
    assert not geraet.connect()
    assert not geraet.write_report(GRUEN)


def test_nach_vergeblicher_suche_erst_nach_der_wartezeit_neu(hid):
    hid["sammlungen"] = []
    geraet = JabraDevice(cooldown_s=60)
    geraet.connect()
    geraet.write_report(GRUEN)
    assert len(hid["gefragt"]) == 1


def test_nach_dem_wiedereinstecken_geht_es_weiter(hid):
    geraet = JabraDevice(cooldown_s=0)
    ausgesteckt = FakeHid(fehler=True)
    geraet._dev, geraet._is_connected = ausgesteckt, True
    assert not geraet.write_report(GRUEN)
    assert not geraet.is_connected and not ausgesteckt.offen
    assert geraet.write_report(GRUEN)
    assert hid["geraet"].geschrieben == [bytes([REPORT_ID, GRUEN, 0x00])]


def test_schliessen_schreibt_nichts():
    """Sonst schaltete die Startprüfung einem wachen Alfred die LED aus."""
    hid_geraet = FakeHid()
    geraet = verbunden(hid_geraet)
    geraet.close()
    assert hid_geraet.geschrieben == [] and not hid_geraet.offen and not geraet.is_connected


class FakeGeraet:
    """Steht für JabraDevice; der Thread der LED schreibt hier hinein."""

    def __init__(self, fehler: bool = False, haengt: threading.Event | None = None):
        self.zustaende: list[int] = []
        self.versuche = 0
        self.geschlossen = False
        self.fehler = fehler
        self.haengt = haengt

    def write_report(self, zustand: int) -> bool:
        self.versuche += 1
        if self.haengt is not None:
            self.haengt.wait(5)
        if self.fehler:
            raise OSError("USB weg")
        self.zustaende.append(zustand)
        return True

    def close(self) -> None:
        self.geschlossen = True


def warten_bis(bedingung, sekunden: float = 2.0) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.005)
    return bedingung()


def test_zustaende_der_mitschrift():
    led = JabraLedController(FakeGeraet())
    try:
        for zustand, modus in [
            ("bereit", "grün"), ("hoert", "grün"), ("denkt", "blinkt"),
            ("arbeitet: wetter_abfragen", "blinkt"), ("spricht", "grün"), ("schlaeft", "aus"),
        ]:
            led.zeigen(zustand)
            assert led.modus == modus, zustand
    finally:
        asyncio.run(led.close())


def test_gruen_solange_alfred_wach_ist():
    geraet = FakeGeraet()
    led = JabraLedController(geraet)
    assert warten_bis(lambda: geraet.zustaende == [AUS]), "beim Start aus"
    led.zeigen("bereit")
    assert warten_bis(lambda: geraet.zustaende == [AUS, GRUEN])
    led.zeigen("hoert")  # bleibt grün: kein neuer Report
    led.zeigen("spricht")
    led.zeigen("schlaeft")
    assert warten_bis(lambda: geraet.zustaende == [AUS, GRUEN, AUS])
    asyncio.run(led.close())
    assert geraet.zustaende[-1] == AUS and geraet.geschlossen
    led.zeigen("bereit")  # nach dem Beenden passiert nichts mehr
    time.sleep(0.05)
    assert geraet.zustaende[-1] == AUS


def test_blinkt_beim_nachdenken_und_hoert_beim_sprechen_auf():
    geraet = FakeGeraet()
    led = JabraLedController(geraet, blinken_s=0.02)
    led.zeigen("bereit")
    assert warten_bis(lambda: geraet.zustaende[-1:] == [GRUEN])
    vorher = len(geraet.zustaende)
    led.zeigen("denkt")
    assert warten_bis(lambda: len(geraet.zustaende) >= vorher + 5)
    blinken = geraet.zustaende[vorher:]
    assert blinken[0] == AUS, "sofort sichtbar: Wer zu Ende gesprochen hat, sieht gleich, dass es weitergeht"
    assert all(a != b for a, b in zip(blinken, blinken[1:]))
    led.zeigen("spricht")
    assert warten_bis(lambda: geraet.zustaende[-1] == GRUEN)
    anzahl = len(geraet.zustaende)
    time.sleep(0.1)
    assert len(geraet.zustaende) == anzahl, "grün bleibt stehen"
    asyncio.run(led.close())


def test_blinkt_nicht_ewig(monkeypatch):
    """Ohne Antwort (Husten ohne Transkript) steht die Mitschrift bis zum Einschlafen auf denkt."""
    monkeypatch.setattr(JabraLedController, "BLINKEN_HOECHSTENS_S", 0.1)
    geraet = FakeGeraet()
    led = JabraLedController(geraet, blinken_s=0.02)
    led.zeigen("denkt")
    time.sleep(0.3)
    anzahl = len(geraet.zustaende)
    assert geraet.zustaende[-1] == GRUEN and anzahl > 3
    time.sleep(0.1)
    assert len(geraet.zustaende) == anzahl
    asyncio.run(led.close())


def test_nach_einem_fehler_versucht_sie_es_wieder(monkeypatch):
    monkeypatch.setattr(JabraLedController, "NEUER_VERSUCH_S", 0.02)
    geraet = FakeGeraet(fehler=True)
    led = JabraLedController(geraet)
    led.zeigen("bereit")
    assert warten_bis(lambda: geraet.versuche >= 3)
    geraet.fehler = False  # wieder eingesteckt
    assert warten_bis(lambda: geraet.zustaende == [GRUEN])
    asyncio.run(led.close())
    assert geraet.geschlossen


def test_haengendes_usb_haelt_das_beenden_nicht_auf(monkeypatch):
    """Sonst käme die Aufsicht in room.run_room nach einem Fehler nie zum Neustart."""
    monkeypatch.setattr(JabraLedController, "SCHLIESSEN_TIMEOUT_S", 0.1)
    haengt = threading.Event()
    led = JabraLedController(FakeGeraet(haengt=haengt))
    try:
        led.zeigen("bereit")
        start = time.monotonic()
        asyncio.run(led.close())
        assert time.monotonic() - start < 1
    finally:
        haengt.set()


def test_konfiguration_kennt_keine_masken():
    """Mit den Masken ließ sich das Mikrofon stumm schalten (9.10.2026); jetzt gibt es nur an, aus und Blinken."""
    with pytest.raises(ValidationError):
        Jabra(speaking_mask=0x21)
    jabra = Jabra(vendor_id="0x0b0e", product_id="0x0422")
    assert (jabra.led_aktiv, jabra.blinken_sekunden, jabra.vendor_id, jabra.product_id) == (True, 0.5, 0x0B0E, 0x0422)
