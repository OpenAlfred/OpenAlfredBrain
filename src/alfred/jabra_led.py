"""Grüne Hörer-LED am Jabra Speak 510: aus im Schlaf, grün, wenn Alfred wach ist, blinkend, wenn er nachdenkt.

Das Jabra hat neben dem Audio ein USB-HID-Interface (VID 0x0B0E, PID 0x0422); der
Telephony-Output-Report 0x03 setzt seine Anzeigen. Das Gerät liest den Report aber als
Anrufzustand, nicht als Leuchte, und mehrere Bits schalten das Mikrofon stumm. Gemessen am
9.10.2026 mit dem Pegel am Mikrofon nach jedem Report (docs/erkenntnisse.md):

    0x01  Off-Hook     grüne Hörer-LED, Mikrofon offen
    0x04  Mute         Mikrofon stumm
    0x08  Ring         Mikrofon stumm, nicht immer sofort
    0x10  Hold         Mikrofon stumm
    0x20  Microphone   mit Off-Hook roter Ring und Mikrofon stumm, ohne Off-Hook wirkungslos

Der rote Ring ist also die Stummschaltung selbst, und mehr als die grüne Taste lässt sich nicht
schalten, ohne das Mikrofon zu gefährden. Deshalb sendet `JabraDevice` nur Off-Hook und filtert
alles andere heraus. Blinken heißt Off-Hook im Takt an und aus; das stört weder Mikrofon noch
Lautsprecher (gemessen am selben Tag).

Die LED ist Komfort. Fehlt das Gerät, fehlen die Rechte oder reißt die Verbindung ab, steht es
im Log, und Alfred läuft weiter. Geschrieben wird in einem eigenen Thread; der Sprach-Loop
wartet nie auf USB.

Am Gerät:  python -m alfred.jabra_led --an | --aus | --info
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_VENDOR_ID = 0x0B0E
DEFAULT_PRODUCT_ID = 0x0422
REPORT_ID = 0x03

BIT_OFF_HOOK = 0x01
# Alle anderen Bits des Reports können das Mikrofon stumm schalten (siehe oben).
ERLAUBTE_BITS = BIT_OFF_HOOK

GRUEN = BIT_OFF_HOOK
AUS = 0x00


class JabraDevice:
    """Das HID-Interface des Jabra Speak 510, gefunden über die USB-Kennung statt über einen Pfad."""

    def __init__(
        self,
        vendor_id: int = DEFAULT_VENDOR_ID,
        product_id: int = DEFAULT_PRODUCT_ID,
        cooldown_s: float = 2.0,
    ) -> None:
        self.vendor_id = vendor_id
        self.product_id = product_id
        # Nach einem vergeblichen Versuch so lange nicht neu suchen; das Gerät kann ausgesteckt sein.
        self.cooldown_s = cooldown_s
        self._dev: Any = None
        self._linux_fd: int | None = None
        self._is_connected: bool = False
        self._last_connect_attempt: float | None = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def connect(self) -> bool:
        """Sucht das Gerät und öffnet es; schreibt nichts."""
        if self._is_connected:
            return True
        now = time.monotonic()
        if self._last_connect_attempt is not None and now - self._last_connect_attempt < self.cooldown_s:
            return False
        self._last_connect_attempt = now

        # hidapi läuft unter Windows und Linux; unter Linux notfalls direkt über /sys/class/hidraw.
        if self._connect_hidapi() or (platform.system() == "Linux" and self._connect_linux_hidraw()):
            self._is_connected = True
            logger.info("[JabraLED] Jabra Speak 510 connected")
            return True
        return False

    def _connect_hidapi(self) -> bool:
        try:
            import hid
        except ImportError:
            return False

        try:
            devices = hid.enumerate(self.vendor_id, self.product_id)
            if not devices:
                return False
            # Windows führt jede Sammlung des Interfaces als eigenes Gerät; Report 0x03 liegt in
            # der Telephony-Sammlung (Usage-Page 0x0B).
            best_device = next((d for d in devices if d.get("usage_page") == 0x0B), devices[0])
            path = best_device.get("path")
            if not path:
                return False

            dev = hid.device()
            dev.open_path(path)
            try:
                dev.set_nonblocking(1)
            except Exception:
                pass
            self._dev = dev
            return True
        except Exception as e:
            logger.debug(f"[JabraLED] hidapi Verbindung fehlgeschlagen: {e}")
            self._trennen()
            return False

    def _connect_linux_hidraw(self) -> bool:
        try:
            hidraw_dir = Path("/sys/class/hidraw")
            if not hidraw_dir.exists():
                return False

            target_vid = f"{self.vendor_id:04X}"
            target_pid = f"{self.product_id:04X}"

            for entry in sorted(hidraw_dir.iterdir()):
                uevent_path = entry / "device" / "uevent"
                if not uevent_path.exists():
                    continue
                content = uevent_path.read_text(encoding="utf-8", errors="ignore").upper()
                if f":0000{target_vid}:0000{target_pid}" in content or f"{target_vid}:{target_pid}" in content:
                    dev_node = Path("/dev") / entry.name
                    if dev_node.exists():
                        self._linux_fd = os.open(str(dev_node), os.O_RDWR | os.O_NONBLOCK)
                        return True
        except Exception as e:
            logger.debug(f"[JabraLED] Linux hidraw Fallback fehlgeschlagen: {e}")
            self._trennen()
        return False

    def write_report(self, state: int) -> bool:
        """Schreibt den Report; Bits, die das Mikrofon stumm schalten, kommen nie beim Gerät an."""
        if state & ~ERLAUBTE_BITS:
            logger.warning(
                f"[JabraLED] 0x{state:02x} würde das Mikrofon stumm schalten; gesendet wird 0x{state & ERLAUBTE_BITS:02x}"
            )
            state &= ERLAUBTE_BITS
        if not self._is_connected and not self.connect():
            return False

        report_data = bytes([REPORT_ID, state, 0x00])
        try:
            if self._dev is not None:
                if self._dev.write(report_data) < 0:
                    raise OSError("hid.write meldete einen Fehler")
            elif self._linux_fd is not None:
                os.write(self._linux_fd, report_data)
            else:
                return False
            return True
        except Exception as e:
            logger.warning(f"[JabraLED] HID write failed: {e}")
            logger.warning("[JabraLED] device disconnected")
            self._trennen()
            return False

    def _trennen(self) -> None:
        self._is_connected = False
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        if self._linux_fd is not None:
            try:
                os.close(self._linux_fd)
            except Exception:
                pass
            self._linux_fd = None

    def close(self) -> None:
        """Gibt das Gerät frei, ohne etwas zu schreiben.

        Sonst schaltete die Startprüfung einem wachen Alfred die LED aus.
        """
        self._trennen()


class JabraLedController:
    """Zeigt an der grünen Taste, was Alfred tut, und folgt dabei dem Zustand der Mitschrift.

    Ein eigener Thread schreibt: im Takt, solange die LED blinkt, sonst nur bei einer Änderung
    und nach einem Fehler alle paar Sekunden neu. Wer `zeigen` ruft, wartet nie auf USB.
    """

    # So lange darf das Ausschalten beim Beenden dauern; sonst käme die Aufsicht nie zum Neustart.
    SCHLIESSEN_TIMEOUT_S = 2.0
    # Kommt keine Antwort (Husten ohne Transkript), stünde die Mitschrift bis zum Einschlafen auf
    # "denkt"; länger blinkt die LED deshalb nicht, danach ist sie wieder grün.
    BLINKEN_HOECHSTENS_S = 10.0
    # Nach einem Schreibfehler (ausgesteckt, keine Rechte) so lange bis zum nächsten Versuch.
    NEUER_VERSUCH_S = 5.0

    def __init__(
        self,
        device: JabraDevice | None = None,
        *,
        vendor_id: int = DEFAULT_VENDOR_ID,
        product_id: int = DEFAULT_PRODUCT_ID,
        blinken_s: float = 0.5,
    ) -> None:
        self.device = device or JabraDevice(vendor_id=vendor_id, product_id=product_id)
        self.blinken_s = blinken_s
        # Beim Start aus: Die Schleuse ist zu, und es hebt eine Stummschaltung auf, die ein
        # früherer Lauf am Gerät hinterlassen hat (Auflegen gibt das Mikrofon frei).
        self._modus = "aus"
        self._seit = time.monotonic()
        self._ende = False
        self._bedingung = threading.Condition()
        self._thread = threading.Thread(target=self._laufen, name="JabraLED", daemon=True)
        self._thread.start()

    @property
    def modus(self) -> str:
        """aus, grün oder blinkt."""
        return self._modus

    def zeigen(self, zustand: str) -> None:
        """Nimmt einen Zustand der Mitschrift: schlaeft, bereit, hoert, denkt, arbeitet: …, spricht."""
        if zustand == "schlaeft":
            modus = "aus"
        elif zustand == "denkt" or zustand.startswith("arbeitet"):
            modus = "blinkt"
        else:
            modus = "grün"
        with self._bedingung:
            if self._ende or modus == self._modus:
                return
            self._modus, self._seit = modus, time.monotonic()
            self._bedingung.notify()
        logger.debug(f"[JabraLED] {modus}")

    def _laufen(self) -> None:
        geschrieben: int | None = None
        an = True
        while True:
            with self._bedingung:
                if self._ende:
                    break
                modus, seit = self._modus, self._seit
            warten: float | None = None
            if modus == "blinkt" and time.monotonic() - seit < self.BLINKEN_HOECHSTENS_S:
                an = not an  # beginnt mit aus: Wer zu Ende gesprochen hat, sieht sofort, dass es weitergeht
                ziel, warten = (GRUEN if an else AUS), self.blinken_s
            else:
                an = True
                ziel = AUS if modus == "aus" else GRUEN
            if ziel != geschrieben:
                if self._schreiben(ziel):
                    geschrieben = ziel
                elif warten is None:
                    warten = self.NEUER_VERSUCH_S
            with self._bedingung:
                if not self._ende and (self._modus, self._seit) == (modus, seit):
                    self._bedingung.wait(warten)
        try:
            self._schreiben(AUS)
        finally:
            self.device.close()

    def _schreiben(self, zustand: int) -> bool:
        try:
            return self.device.write_report(zustand)
        except Exception as e:  # noqa: BLE001 - die LED darf Alfred nie aufhalten
            logger.warning(f"[JabraLED] HID write failed: {e}")
            return False

    async def close(self) -> None:
        """Schaltet aus und gibt das Gerät frei; wartet höchstens SCHLIESSEN_TIMEOUT_S auf USB."""
        with self._bedingung:
            if self._ende:
                return
            self._ende = True
            self._bedingung.notify()
        await asyncio.to_thread(self._thread.join, self.SCHLIESSEN_TIMEOUT_S)


def zeige_info() -> None:
    """Listet die HID-Sammlungen aller Jabra-Geräte; zeigt die Produktkennung anderer Revisionen."""
    try:
        import hid
    except ImportError:
        print("hidapi ist nicht installiert: pip install -e .[raum]")
        return
    geraete = hid.enumerate(DEFAULT_VENDOR_ID)
    if not geraete:
        print("Kein Jabra-Gerät gefunden.")
    for d in geraete:
        print(
            f"{d.get('product_string') or '?'}: product_id 0x{d.get('product_id', 0):04x}, "
            f"Interface {d.get('interface_number')}, Usage-Page 0x{d.get('usage_page', 0):04x}"
        )
    if platform.system() == "Linux" and Path("/sys/class/hidraw").exists():
        for entry in sorted(Path("/sys/class/hidraw").iterdir()):
            uevent = entry / "device" / "uevent"
            if uevent.exists() and f"{DEFAULT_VENDOR_ID:08X}" in uevent.read_text(errors="ignore").upper():
                print(f"/dev/{entry.name}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Grüne Hörer-LED am Jabra Speak 510")
    gruppe = parser.add_mutually_exclusive_group(required=True)
    gruppe.add_argument("--an", action="store_true", help="grüne LED einschalten; das Mikrofon bleibt offen")
    gruppe.add_argument("--aus", action="store_true", help="LED ausschalten; hebt auch eine Stummschaltung auf")
    gruppe.add_argument("--info", action="store_true", help="HID-Sammlungen der Jabra-Geräte auflisten")
    args = parser.parse_args()

    if args.info:
        zeige_info()
        return
    geraet = JabraDevice()
    if not geraet.connect():
        raise SystemExit("Kein Jabra Speak 510 gefunden: eingesteckt? Unter Linux: deploy/99-jabra.rules")
    try:
        if not geraet.write_report(GRUEN if args.an else AUS):
            raise SystemExit("Schreiben fehlgeschlagen, siehe Log")
    finally:
        geraet.close()
    print("Grün." if args.an else "Aus.")


if __name__ == "__main__":
    main()
