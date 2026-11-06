# TICKET-03: Paket- & Sendungsverfolgung via E-Mail

- **Priorität:** Mittel
- **Bereich:** Werkzeuge / E-Mail (`src/alfred/mail.py`, `src/alfred/werkzeuge/kommunikation.py`, `src/alfred/pakete.py`)
- **Status:** Geplant
- **Aufwand:** Mittel

---

## 1. Kontext & Motivation
Ein oft genutztes Feature bei Amazon Alexa ist die automatische Paketankündigung („Dein Amazon-Paket kommt heute“). Diese ist jedoch strikt auf das Amazon-Ökosystem beschränkt.

Da Alfred über das bestehende `mail.py`-Modul bereits per IMAP auf E-Mails zugreifen kann, soll ein universeller Sendungsverfolger implementiert werden. Dieser scannt automatisiert Versand- und Zustellbenachrichtigungen aller großen Versanddienstleister (DHL, Amazon, DPD, UPS, GLS, Hermes) und macht den Status per Sprache abfragbar.

---

## 2. Kernfunktionen

### 2.1 Erkennung von Versand- & Zustell-Mails
* Parser für typische Versandbestätigungen:
  * **DHL:** Tracking-Links, Liefertag und Zeitfenster („Zustellung voraussichtlich heute zwischen 11:30 und 14:00 Uhr“).
  * **Amazon:** Versandmitteilungen und Zustellbestätigungen.
  * **DPD / UPS / Hermes / GLS:** Paketnummer und Status.
* Speicherung aktiver Sendungen in `gedaechtnis/pakete.json` mit Status (`unterwegs`, `in_zustellung`, `zugestellt`).

### 2.2 Sprachwerkzeuge
* `pakete_abfragen()`:
  * *Beispiele:* „Kommt heute ein Paket für mich?“, „Wo ist meine Bestellung?“, „Was ist mit dem Paket von Mindfactory?“.
  * *Antwort:* „Heute wird ein DHL-Paket von Mindfactory erwartet. Zwei weitere Sendungen sind noch unterwegs.“

### 2.3 Proaktive Benachrichtigung
* Sobald eine Sendung den Status `in_zustellung` erreicht, kann Alfred dies morgens im Briefing oder als Push-Nachricht aufs Smartphone melden.

---

## 3. Technische Umsetzung
* **Hintergrund-Job:** Erweiterung in `src/alfred/pipeline.py` (analog zu Geburtstagen oder WhatsApp-Meldungen), der alle 30–60 Minuten neue Mails auf bekannte Versandmuster prüft.
* **Regex / Heuristik statt teurer LLM-Aufrufe:** Erkennung von Absender-Domains (z. B. `paket@dhl.de`, `auto-confirm@amazon.de`) und Tracking-Mustern im Code, um API-Kosten zu minimieren.
* **Datenspeicherung:** `gedaechtnis/pakete.json` (automatische Archivierung/Löschung 7 Tage nach erfolgreicher Zustellung).

---

## 4. Akzeptanzkriterien
1. [ ] Sendungsbestätigungen von DHL und Amazon werden zuverlässig erkannt und in `gedaechtnis/pakete.json` hinterlegt.
2. [ ] Die Frage „Kommt heute Post/Pakete für mich?“ fasst alle fälligen Lieferungen korrekt zusammen.
3. [ ] Bereits zugestellte Pakete werden nicht mehr als aktiv gemeldet.
4. [ ] Keine unnötigen LLM-Token für Standard-E-Mail-Checks (rein lokale Textanalyse).
