# TICKET-04: Freies Webradio & ARD Audiothek (Streams)

- **Priorität:** Mittel
- **Bereich:** Werkzeuge / Medien (`src/alfred/werkzeuge/medien.py`, `src/alfred/radio.py`)
- **Status:** Geplant
- **Aufwand:** Gering

---

## 1. Kontext & Motivation
Bisher unterstützt Alfred für Audio die Anbindung an Spotify Connect. Für Nachrichten am Morgen oder Hintergrundbeschallung sind viele Nutzer jedoch auf freie Radiosender (Öffentlich-Rechtliche, Lokalsender) oder Nachrichten-Podcasts angewiesen.

Alexa nutzt hierfür meist TuneIn, was zunehmend durch vorgeschaltete Zwangswerbung („TuneIn Preroll-Ads“) frustriert. Alfred soll direkte, werbefreie Web-Streams (HTTP/HTTPS MP3/AAC) und freie Podcasts (z. B. ARD Audiothek, Tagesschau in 100 Sekunden) direkt auf dem Raum-Lautsprecher abspielen können.

---

## 2. Kernfunktionen

### 2.1 Senderliste & Stream-Wiedergabe
* Konfigurierbare Senderliste in `config/alfred.yaml` (z. B. Deutschlandfunk, SWR3, Radio Eins, Rock Antenne).
* Werkzeug: `radio_spielen(sender=None)`:
  * *Beispiele:* „Spiel Deutschlandfunk“, „Schalt das Radio ein“, „Mach Radio Eins an“.
* Werkzeug: `nachrichten_spielen()`:
  * Ruft den neuesten Kurznachrichten-Podcast ab (z. B. Deutschlandfunk-Nachrichten oder Tagesschau in 100 Sekunden) und spielt ihn ab.

### 2.2 Wiedergabesteuerung
* Werkzeuge: `radio_stoppen()`, `lautstaerke_aendern(wert)`.
* Sofortige Unterbrechung bei Nennung des Wachworts „Alfred“ (Audio mutet oder stoppt während des Gesprächs).

---

## 3. Technische Umsetzung
* **Player-Engine:** Nutzung von `mpv` (headless als Hintergrundprozess) oder eines leichtgewichtigen Python-Stream-Players, der das Audio direkt an das Zielgerät des Raums leitet.
* **Stream-Mapping (`config/alfred.yaml`):**
  ```yaml
  radio:
    sender:
      dlf: "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3"
      radioeins: "https://rbb-radioeins-live.sslcast.addradio.de/rbb/radioeins/live/mp3/128/stream.mp3"
      swr3: "https://liveradio.swr.de/sw282p3/swr3/play.mp3"
  ```
* **Routine-Integration:** Eignet sich optimal für den Morgen („Jeden Morgen um 7:30 Uhr: Wetter, Termine und danach Deutschlandfunk“).

---

## 4. Akzeptanzkriterien
1. [ ] Sprachbefehl „Spiel [Sender]“ startet den Stream innerhalb von < 1,5 Sekunden.
2. [ ] Wachwort „Alfred“ dämpft oder stoppt die Wiedergabe zuverlässig, damit das Mikrofon nicht übersteuert.
3. [ ] Keine Unterbrechung durch Drittanbieter-Werbung.
4. [ ] In Routinen integrierbar.
