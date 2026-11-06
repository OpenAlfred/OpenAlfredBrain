# TICKET-06: Multi-Raum Intercom & gezielte Durchsagen

- **Priorität:** Niedrig bis Mittel
- **Bereich:** Audio / Multi-Raum (`src/alfred/room.py`, `src/alfred/werkzeuge/kommunikation.py`)
- **Status:** Geplant
- **Aufwand:** Mittel

---

## 1. Kontext & Motivation
Alexas „Drop-In“- und „Ankündigungs“-Funktion („Alexa, sag allen: Essen ist fertig“) ist in größeren Wohnungen oder Häusern eines der meistgenutzten Features. 

Alfred verfügt architektonisch bereits über Multi-Raum-Fähigkeit (`alfred-raum@<name>` je Raum mit eigener Hardware). Was noch fehlt, ist die Vernetzung der Räume untereinander für gezielte Durchsagen und Intercom-Gespräche.

---

## 2. Kernfunktionen

### 2.1 Gezielte Durchsagen
* Werkzeug: `durchsage_senden(ziel_raum, text)`:
  * *Beispiele:* „Alfred, sag der Küche, dass das Essen fertig ist“, „Sag im Wohnzimmer Bescheid: Ich bin am Telefon“.
  * *Verhalten:* Im Zielraum ertönt ein kurzer Signalton, gefolgt von Alfreds Stimme mit dem Durchsage-Text.

### 2.2 Haus-Durchsage (Broadcast)
* *Beispiel:* „Durchsage an alle: Wir fahren in fünf Minuten los!“
* Spielt die Durchsage auf allen aktiven Raum-Prozessen synchron ab.

---

## 3. Technische Umsetzung
* **IPC (Inter-Process-Communication):** Da jeder Raum als eigener systemd-Dienst läuft, können Ereignisse über einen Unix-Domain-Socket, MQTT oder einen einfachen Redis-/Dateibasierten Event-Bus an den Ziel-Prozess weitergegeben werden.
* **Audio-Ausgabe:** Der Ziel-Prozess empfängt das Event und gibt den `TTSSpeakFrame` über seine lokale Soundkarte aus.

---

## 4. Akzeptanzkriterien
1. [ ] Sprachbefehl im Raum A löst innerhalb von < 1 s eine Ansage im Raum B aus.
2. [ ] Laufende Gespräche im Zielraum werden nicht abrupt übertönt, sondern respektvoll eingereiht.
