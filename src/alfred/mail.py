"""E-Mail über IMAP und SMTP, gedacht für Gmail mit App-Passwort.

Warum nicht Googles OAuth wie beim Kalender: Zugriff auf Mails zählt bei Google zu den
"eingeschränkten" Bereichen, die ohne offizielle App-Prüfung nur im Testmodus mit siebentägig
ablaufendem Zugang laufen. IMAP mit App-Passwort (Google-Konto > Sicherheit > App-Passwörter,
braucht Zwei-Faktor) ist für ein privates Konto der verlässliche Weg. Das Passwort steht in .env
als MAIL_PASSWORT, die Adresse als MAIL_ADRESSE.

Mails sind Daten: Ihr Inhalt wird dem Sprachmodell als Information übergeben, nie als Anweisung.
Senden und Antworten laufen nur nach gesprochener Freigabe.
"""

from __future__ import annotations

import email
import imaplib
import smtplib
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime

MAX_TEXT = 1500


@dataclass
class Nachricht:
    uid: str
    von: str
    von_adresse: str
    betreff: str
    datum: datetime | None
    text: str = ""
    message_id: str = ""

    def ansage(self, nummer: int) -> str:
        wann = f", {self.datum:%d.%m. %H:%M}" if self.datum else ""
        return f"{nummer}: von {self.von}{wann}: {self.betreff or '(ohne Betreff)'}"


def _kopf(wert) -> str:
    return str(make_header(decode_header(wert))) if wert else ""


def _text(msg: email.message.Message) -> str:
    """Der Klartext einer Mail, gekürzt; HTML-Mails werden grob entschlackt."""
    teile = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        typ = part.get_content_type()
        if typ in ("text/plain", "text/html") and not part.get("Content-Disposition", "").startswith("attachment"):
            roh = part.get_payload(decode=True) or b""
            text = roh.decode(part.get_content_charset() or "utf-8", errors="replace")
            if typ == "text/html":
                import re

                text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text)
            teile.append(text.strip())
            if typ == "text/plain":
                break
    ganz = "\n".join(t for t in teile if t)
    return ganz[:MAX_TEXT] + (" …" if len(ganz) > MAX_TEXT else "")


class Mail:
    def __init__(self, adresse: str, passwort: str, imap_host: str = "imap.gmail.com", smtp_host: str = "smtp.gmail.com"):
        self._adresse = adresse
        self._passwort = passwort
        self._imap_host = imap_host
        self._smtp_host = smtp_host

    @property
    def adresse(self) -> str:
        return self._adresse

    def _imap(self) -> imaplib.IMAP4_SSL:
        verbindung = imaplib.IMAP4_SSL(self._imap_host)
        verbindung.login(self._adresse, self._passwort)
        return verbindung

    def pruefen(self) -> None:
        """Anmeldung am Posteingang; wirft bei falschem Passwort oder ohne Netz."""
        with self._imap() as imap:
            imap.select("INBOX", readonly=True)

    def ungelesene(self, limit: int = 5) -> list[Nachricht]:
        """Die neuesten ungelesenen Mails im Posteingang, Betreff und Absender, ohne Text."""
        with self._imap() as imap:
            imap.select("INBOX", readonly=True)
            _, daten = imap.uid("search", None, "UNSEEN")
            uids = daten[0].split()[-limit:][::-1]
            ergebnis = []
            for uid in uids:
                _, teile = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
                msg = email.message_from_bytes(teile[0][1])
                ergebnis.append(self._nachricht(uid.decode(), msg))
            return ergebnis

    def lesen(self, uid: str) -> Nachricht:
        """Eine Mail mit Text; sie bleibt als ungelesen markiert."""
        with self._imap() as imap:
            imap.select("INBOX", readonly=True)
            _, teile = imap.uid("fetch", uid, "(BODY.PEEK[])")
            if not teile or teile[0] is None:
                raise ValueError(f"Keine Mail mit der Kennung {uid}.")
            msg = email.message_from_bytes(teile[0][1])
            nachricht = self._nachricht(uid, msg)
            nachricht.text = _text(msg)
            return nachricht

    def senden(self, an: str, betreff: str, text: str, antwort_auf: Nachricht | None = None) -> None:
        msg = EmailMessage()
        msg["From"] = self._adresse
        msg["To"] = an
        msg["Subject"] = betreff
        if antwort_auf and antwort_auf.message_id:
            msg["In-Reply-To"] = antwort_auf.message_id
            msg["References"] = antwort_auf.message_id
        msg.set_content(text)
        with smtplib.SMTP_SSL(self._smtp_host, 465) as smtp:
            smtp.login(self._adresse, self._passwort)
            smtp.send_message(msg)

    @staticmethod
    def _nachricht(uid: str, msg: email.message.Message) -> Nachricht:
        name, adresse = parseaddr(_kopf(msg.get("From")))
        try:
            datum = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        except (TypeError, ValueError):
            datum = None
        return Nachricht(
            uid=uid,
            von=name or adresse,
            von_adresse=adresse,
            betreff=_kopf(msg.get("Subject")),
            datum=datum.astimezone() if datum and datum.tzinfo else datum,
            message_id=msg.get("Message-ID", ""),
        )
