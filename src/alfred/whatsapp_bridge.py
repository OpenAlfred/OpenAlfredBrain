"""WhatsApp-Brücke: ein eigener kleiner Prozess, der Alfreds Nachrichten verschickt.

Läuft in einer eigenen Python-Umgebung (.venv-wa), weil die Bibliothek neonize eine neuere
protobuf-Version braucht als Pipecat erlaubt. Die Trennung hat einen zweiten Vorteil: Die
WhatsApp-Zugangsdaten liegen nur in diesem Prozess. Alfred erreicht ihn ausschließlich über
127.0.0.1 mit einem Token aus config/whatsapp_bridge.token; nach außen ist kein Port offen.

Alfred erscheint bei WhatsApp als verknüpftes Gerät, wie WhatsApp Web. Das ist keine offizielle
Schnittstelle; WhatsApp duldet es, kann ein Konto aber sperren. Für wenige persönliche
Nachrichten am Tag ist das Risiko gering, aber nicht null.

Koppeln (einmalig, QR-Code mit dem Handy scannen: WhatsApp > Verknüpfte Geräte):
    .venv-wa/bin/python src/alfred/whatsapp_bridge.py --koppeln
Dienst:
    .venv-wa/bin/python src/alfred/whatsapp_bridge.py
Probe:
    .venv-wa/bin/python src/alfred/whatsapp_bridge.py --senden 4917xxxxxxx "Hallo von Alfred"

Schnittstelle (nur 127.0.0.1:7861):
    GET  /status                        -> {"verbunden": bool, "angemeldet": bool}
    GET  /nachrichten  X-Token: <token> -> {"nachrichten": [{"von", "nummer", "zeit", "text"}, ...]}
    GET  /kontakte     X-Token: <token> -> {"kontakte": [{"name", "nummer"}, ...]}  (Adressbuch des Handys)
    POST /senden       X-Token: <token> {"nummer": "4917...", "text": "..."} -> {"gesendet": true}

Eingegangene Textnachrichten aus Einzelchats (keine Gruppen, Kanäle, Status oder Medien) bleiben
nur im Speicher der Brücke, höchstens die letzten 50, und gehen beim Neustart verloren.
"""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJEKT = Path(__file__).resolve().parents[2]
DATENBANK = PROJEKT / "config" / "whatsapp.db"
TOKEN_DATEI = PROJEKT / "config" / "whatsapp_bridge.token"
ADRESSE = ("127.0.0.1", 7861)

log = logging.getLogger("whatsapp_bridge")


def token_laden() -> str:
    """Das gemeinsame Geheimnis von Brücke und Alfred; wird beim ersten Start erzeugt."""
    if not TOKEN_DATEI.exists():
        TOKEN_DATEI.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_DATEI.write_text(secrets.token_hex(32), encoding="utf-8")
        log.info(f"Neues Token erzeugt: {TOKEN_DATEI}")
    return TOKEN_DATEI.read_text(encoding="utf-8").strip()


def qr_anzeigen(_client, daten: bytes) -> None:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(daten.decode())
    qr.print_ascii(invert=True)
    print("\nQR-Code mit WhatsApp scannen: Einstellungen > Verknüpfte Geräte > Gerät hinzufügen.\n")


MAX_EINGANG = 50
# Pausen in Sekunden, bevor die Brücke nach einem gescheiterten Verbindungsaufbau neu versucht;
# danach bleibt es bei der letzten.
NEUVERSUCH_PAUSEN = (5, 10, 20, 40, 60)

# Chats, aus denen keine Nachricht an Alexander kommt: Kanäle (@newsletter) und Status oder
# Broadcast-Listen (@broadcast). Am 16.10.2026 kamen 287 von 364 Nachrichten aus sieben
# Deal-Kanälen, und im Speicher (MAX_EINGANG) blieb für echte Nachrichten kein Platz.
KEIN_EINZELCHAT = frozenset({"newsletter", "broadcast"})


def aus_einzelchat(quelle) -> bool:
    """Ob eine eingegangene Nachricht aus einem Einzelchat kommt: nicht von Alexander, keine Gruppe, kein Kanal."""
    return not (quelle.IsFromMe or quelle.IsGroup) and quelle.Chat.Server not in KEIN_EINZELCHAT


def _zeit_formatieren(ts: int | float | None) -> str:
    """Wandelt einen Unix-Zeitstempel (Sekunden, Millisekunden oder Nanosekunden) in Text um."""
    if not ts:
        t = time.time()
    else:
        try:
            t = float(ts)
            if t > 1e17:
                t /= 1e9
            elif t > 1e14:
                t /= 1e6
            elif t > 1e11:
                t /= 1e3
        except (ValueError, TypeError):
            t = time.time()
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except (OSError, ValueError):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime())


def adressbuch(kontakte) -> list[dict]:
    """Name und Nummer aus WhatsApps Kontakten, nur mit Namen aus dem Telefonbuch des Handys.

    WhatsApp kennt auch Leute aus Gruppen, mit selbst gewähltem Anzeigenamen; die bleiben außen
    vor, damit "schreib Jan" nicht bei einem Fremden landet.
    """
    eintraege = []
    for kontakt in kontakte:
        name = (kontakt.Info.FullName or kontakt.Info.FirstName).strip()
        if name and kontakt.JID.Server == "s.whatsapp.net" and kontakt.JID.User:
            eintraege.append({"name": name, "nummer": kontakt.JID.User})
    return sorted(eintraege, key=lambda e: e["name"].casefold())


class Bruecke:
    def __init__(self):
        from neonize.client import NewClient
        from neonize.events import ConnectedEv, LoggedOutEv, MessageEv, PairStatusEv

        DATENBANK.parent.mkdir(parents=True, exist_ok=True)
        self._client = NewClient(str(DATENBANK))
        self._client.event.qr(qr_anzeigen)
        self.angemeldet = False
        # Eingegangene Textnachrichten, nur im Speicher; Alfred liest sie auf Nachfrage.
        self.eingang: list[dict] = []

        @self._client.event(MessageEv)
        def on_message(_c, ev):
            try:
                quelle = ev.Info.MessageSource
                if not aus_einzelchat(quelle):
                    return
                msg = ev.Message
                text = (
                    getattr(msg, "conversation", "")
                    or getattr(getattr(msg, "extendedTextMessage", None), "text", "")
                    or getattr(getattr(msg, "imageMessage", None), "caption", "")
                    or getattr(getattr(msg, "videoMessage", None), "caption", "")
                    or getattr(getattr(msg, "documentMessage", None), "caption", "")
                )
                if not text:
                    return
                self.eingang.append(
                    {
                        "von": ev.Info.Pushname or quelle.Sender.User,
                        "nummer": quelle.Sender.User,
                        "zeit": _zeit_formatieren(ev.Info.Timestamp),
                        "text": text,
                    }
                )
                del self.eingang[:-MAX_EINGANG]
                # Mit der Art des Chats (s.whatsapp.net, lid), falls noch etwas anderes durchrutscht.
                log.info(f"Nachricht von {self.eingang[-1]['von']} ({quelle.Chat.Server})")
            except Exception as e:
                log.exception(f"Fehler beim Verarbeiten eingehender Nachricht: {e}")

        @self._client.event(PairStatusEv)
        def on_pair(_c, ev):
            self.angemeldet = True
            log.info(f"Gekoppelt als {ev.ID.User}")

        @self._client.event(ConnectedEv)
        def on_connected(_c, _ev):
            self.angemeldet = True
            log.info("Mit WhatsApp verbunden")

        @self._client.event(LoggedOutEv)
        def on_logged_out(_c, _ev):
            self.angemeldet = False
            log.warning("Von WhatsApp abgemeldet; neu koppeln mit --koppeln")

    def starten(self) -> None:
        """Verbindet im Hintergrund; connect() blockiert, bis die Verbindung endet."""
        threading.Thread(target=self._verbinden, name="whatsapp", daemon=True).start()

    def _verbinden(self, pausen: tuple[float, ...] = NEUVERSUCH_PAUSEN) -> None:
        """Scheitert schon der Verbindungsaufbau, versucht es die Brücke mit wachsender Pause erneut.

        Am 16.10.2026 startete sie beim Booten, bevor DNS stand; connect() warf, der Faden endete,
        und die Brücke lief ohne WhatsApp weiter, bis jemand sie neu startete. Eine bestehende
        Verbindung hält neonize selbst (es verbindet nach Abbrüchen von allein neu).
        """
        versuch = 0
        while True:
            try:
                self._client.connect()
                return
            except Exception as e:
                pause = pausen[min(versuch, len(pausen) - 1)]
                versuch += 1
                log.warning(f"Keine Verbindung zu WhatsApp ({str(e)[:160]}); neuer Versuch in {pause:g} s")
                time.sleep(pause)

    def status(self) -> dict:
        return {"verbunden": bool(self._client.is_connected), "angemeldet": bool(self._client.is_logged_in)}

    def kontakte(self) -> list[dict]:
        if not self._client.is_logged_in:
            raise RuntimeError("Nicht bei WhatsApp angemeldet; erst koppeln.")
        return adressbuch(self._client.contact.get_all_contacts())

    def senden(self, nummer: str, text: str) -> dict:
        from neonize.utils import build_jid

        nummer = "".join(ch for ch in nummer if ch.isdigit())
        if not nummer or not text.strip():
            raise ValueError("Nummer (nur Ziffern mit Landesvorwahl) und Text sind nötig.")
        if not self._client.is_logged_in:
            raise RuntimeError("Nicht bei WhatsApp angemeldet; erst koppeln.")
        antwort = self._client.send_message(build_jid(nummer), text)
        return {"gesendet": True, "id": getattr(antwort, "ID", "")}


def http_server(bruecke: Bruecke, token: str, adresse: tuple[str, int] = ADRESSE) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _antworten(self, status: int, daten: dict) -> None:
            body = json.dumps(daten, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/status":
                self._antworten(200, bruecke.status())
                return
            if self.path not in ("/nachrichten", "/kontakte"):
                self._antworten(404, {"fehler": "unbekannt"})
                return
            if not secrets.compare_digest(self.headers.get("X-Token", ""), token):
                self._antworten(401, {"fehler": "Token falsch"})
                return
            if self.path == "/nachrichten":
                self._antworten(200, {"nachrichten": bruecke.eingang[-20:]})
                return
            try:
                self._antworten(200, {"kontakte": bruecke.kontakte()})
            except RuntimeError as e:
                self._antworten(409, {"fehler": str(e)})
            except Exception as e:  # noqa: BLE001 - der Fehler soll beim Aufrufer ankommen
                log.exception("Kontakte nicht lesbar")
                self._antworten(500, {"fehler": str(e)})

        def do_POST(self):
            if self.path != "/senden":
                self._antworten(404, {"fehler": "unbekannt"})
                return
            if not secrets.compare_digest(self.headers.get("X-Token", ""), token):
                self._antworten(401, {"fehler": "Token falsch"})
                return
            try:
                laenge = int(self.headers.get("Content-Length", "0"))
                daten = json.loads(self.rfile.read(laenge) or b"{}")
                self._antworten(200, bruecke.senden(str(daten.get("nummer", "")), str(daten.get("text", ""))))
            except ValueError as e:
                self._antworten(400, {"fehler": str(e)})
            except RuntimeError as e:  # nicht angemeldet: erwartbar, kein Stacktrace nötig
                log.warning(str(e))
                self._antworten(409, {"fehler": str(e)})
            except Exception as e:  # noqa: BLE001 - der Fehler soll beim Aufrufer ankommen
                log.exception("Senden fehlgeschlagen")
                self._antworten(500, {"fehler": str(e)})

        def log_message(self, format, *args):  # noqa: A002
            log.debug("%s " + format, self.address_string(), *args)

    return ThreadingHTTPServer(adresse, Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="WhatsApp-Brücke für Alfred")
    parser.add_argument("--koppeln", action="store_true", help="QR-Code zeigen und auf die Kopplung warten")
    parser.add_argument("--senden", nargs=2, metavar=("NUMMER", "TEXT"), help="eine Nachricht schicken und beenden")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bruecke = Bruecke()
    bruecke.starten()

    if args.koppeln:
        frist = time.time() + 300
        while time.time() < frist and not bruecke.angemeldet:
            time.sleep(1)
        print("Gekoppelt. Die Sitzung liegt in config/whatsapp.db." if bruecke.angemeldet else "Keine Kopplung innerhalb von fünf Minuten.")
        return

    if args.senden:
        frist = time.time() + 30
        while time.time() < frist and not bruecke.status()["angemeldet"]:
            time.sleep(1)
        print(bruecke.senden(*args.senden))
        time.sleep(2)  # Zeit zum Ausliefern, bevor der Prozess endet
        return

    server = http_server(bruecke, token_laden())
    log.info(f"Brücke lauscht auf http://{ADRESSE[0]}:{ADRESSE[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
