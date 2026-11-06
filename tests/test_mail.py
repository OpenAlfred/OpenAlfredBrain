import email
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from alfred.mail import Mail, _text

ROH_TEXT = b"""From: =?utf-8?q?Aaron_M=C3=BCller?= <aaron@example.org>
Subject: =?utf-8?q?Treffen_n=C3=A4chste_Woche?=
Date: Sat, 03 Oct 2026 10:15:00 +0200
Message-ID: <abc@example.org>
Content-Type: text/plain; charset=utf-8

Hallo Alexander, passt dir Dienstag?
"""


def test_kopfzeilen_werden_dekodiert():
    msg = email.message_from_bytes(ROH_TEXT)
    n = Mail._nachricht("17", msg)
    assert n.von == "Aaron Müller" and n.von_adresse == "aaron@example.org"
    assert n.betreff == "Treffen nächste Woche" and n.message_id == "<abc@example.org>"
    # In Ortszeit des Rechners; der Test darf nicht von dessen Zeitzone abhängen (Mini-PC lief auf UTC).
    erwartet = datetime(2026, 10, 3, 10, 15, tzinfo=timezone(timedelta(hours=2))).astimezone()
    assert n.datum == erwartet
    assert n.ansage(1) == f"1: von Aaron Müller, {erwartet:%d.%m. %H:%M}: Treffen nächste Woche"
    assert _text(msg) == "Hallo Alexander, passt dir Dienstag?"


def test_html_wird_entschlackt_und_gekuerzt():
    msg = EmailMessage()
    msg.set_content("x" * 2000)
    assert _text(msg).endswith(" …") and len(_text(msg)) < 1600

    html = EmailMessage()
    html.add_alternative("<html><style>p{}</style><body><p>Hallo <b>Welt</b></p><script>bad()</script></body></html>", subtype="html")
    assert _text(html) == "Hallo Welt"
