# TICKET-01: Wand-Dashboard & Kiosk-Modus (Smart Display)

- **Priorität:** Hoch
- **Bereich:** Frontend / Web-App (`src/alfred/web.py`, `src/alfred/web_static/`)
- **Status:** Geplant
- **Aufwand:** Mittel

---

## 1. Kontext & Motivation
Bisher verfügt Alfred über eine mobile-optimierte Web-App (PWA) für das Smartphone und ein Textgespräch. Um Alfred im Wohnraum oder Büro eine sichtbare, lebendige Präsenz zu geben (ähnlich einem Echo Show oder Home Assistant WallPanel), soll die bestehende Web-App um einen dedizierten **Wand-Dashboard- / Kiosk-Modus** erweitert werden. 

Das Dashboard läuft im Vollbild auf einem günstigen Tablet (z. B. altes iPad oder Android-Tablet an der Wand) oder einem Raspberry Pi mit Touchscreen. Es soll auf einen Blick alle Alltagsdaten zeigen und optisch lebendig reagieren, wenn Alfred im Raum hört, denkt oder spricht.

---

## 2. Kernfunktionen

### 2.1 Lebendige Audio- & Status-Visualisierung („Dem System Leben geben“)
* **Visueller Kern (Orb / Waveform / Puls):**
  * **Schläft (Standby):** Ruhiges, subtiles Atmen / minimaler Leuchtpunkt.
  * **Wach / Hört zu:** Reaktiver Glow / dynamische Wellenform in Echtzeit.
  * **Denkt nach:** Rotierender / pulsierender Akzent (zeigt an, dass der Denker oder ein Werkzeug arbeitet).
  * **Spricht:** Rhythmisches Aufleuchten im Takt der Sprachausgabe.
* **Live-Transkription (Untertitel):** Optionales Einblenden der erkannten Wörter und Alfreds Antworttext unten im Display (aus `gedaechtnis/mitschrift.json`).

### 2.2 Glanceable Informations-Kacheln (Auf einen Blick)
* **Große Uhrzeit & Datum:** Gut lesbar aus mehreren Metern Entfernung.
* **Wetter-Widget:** Aktuelle Temperatur, Vorhersage für den Tag, Regenrisiko (Open-Meteo).
* **Kalender-Kachel:** Die nächsten Termine des Tages mit Startzeit und Ort.
* **Aktive Timer:** Große, gut erkennbare Countdown-Ringe für laufende Küchen- oder Arbeitstimer.
* **Aufgaben & Einkaufsliste:** Kompakte Ansicht der offenen Aufgaben; Umschaltbar zur Einkaufsliste.
* **Musik-Player:** Wenn Spotify läuft: Anzeige von Cover-Art, Titel, Interpret und einfache Touch-Steuerung (Play/Pause, Skip).

### 2.3 Kontextuelle Popups & Aktionskarten
* **Alarm / Timer abgelaufen:** Vollbild-Warnung mit Klingel-Animation und großem Touch-Button zum Stoppen.
* **Rezept- / Berichts-Ansicht:** Wenn Alfred einen Denker-Bericht fertigstellt oder ein Rezept/Wochenplan besprochen wird, öffnet sich automatisch eine lesbare Seitenkarte.
* **Freigabe-Karten:** Wenn eine heikle Aktion ansteht (z. B. Mail senden oder WhatsApp abschicken), erscheint auf dem Display eine Karte mit großen Touch-Buttons für „Ja / Ausführen“ und „Abbrechen“.

### 2.4 Kiosk- & Hardware-Tauglichkeit
* **Touch-Optimierung:** Große Buttons für Alltags-Shortcuts (z. B. „Standby“, „Timer stoppen“, „Licht aus“, Einkaufsartikel abhaken).
* **Nacht- & Ruhezustand:** 
  * Automatisches Abdunkeln des Displays während der Ruhezeit (`proaktiv.ruhe_von` bis `ruhe_bis`, z. B. 22:00 bis 07:00 Uhr).
  * Reaktivierung durch Berührung oder sobald das Wachwort im Raum erkannt wird.
* **Vollbild & Wake-Lock:** Nutzung der `Screen Wake Lock API`, damit das Display an der Wand nicht unerwünscht in den Bildschirmschoner fällt.

---

## 3. Technische Umsetzung

### 3.1 Backend (`src/alfred/web.py`)
* Neuer URL-Pfad: `/kiosk` (oder `/` mit `?mode=kiosk`).
* **Live-Event-Stream (SSE):** 
  * Neuer Endpunkt `/api/live/stream` mit Server-Sent Events (SSE).
  * Pusht sofort Zustandsänderungen aus `mitschrift.json`, neu gestartete Timer (`erinnerungen.json`), Medien-Updates und fällige Freigaben an den Client, anstatt dass der Browser im Sekundentakt pollt.

### 3.2 Frontend (`src/alfred/web_static/`)
* **`kiosk.html` / Modus in `index.html`:** Eigenes, responsives CSS-Grid-Layout mit Fokus auf 16:9- und 4:3-Querformat-Displays.
* **Visualizer-Komponente:** Leichtgewichtiges Canvas- oder CSS-Glow-Element (ohne schwere Three.js-Abhängigkeiten, um alte Tablets nicht zu überlasten).
* **Theme:** Dunkles OLED-freundliches Design mit klaren Akzentfarben.

---

## 4. Akzeptanzkriterien
1. [ ] Die Route `/kiosk` öffnet eine vollflächige Dashboard-Ansicht ohne störende Browser-Scrollbalken.
2. [ ] Bei Sprachinteraktion im Raum wechselt der visuelle Indikator innerhalb von < 300 ms flüssig zwischen den Zuständen *Inaktiv*, *Hört zu*, *Denkt* und *Spricht*.
3. [ ] Aktive Timer werden mit verbleibender Restzeit in Sekunden angezeigt und können per Touch gestoppt werden.
4. [ ] Artikel auf der Einkaufsliste können direkt per Fingertipp abgehakt werden.
5. [ ] Spotify-Wiedergabe wird bei Aktivität mit Cover und Steuerungselementen eingeblendet.
6. [ ] Der Kiosk-Modus bleibt dank Wake-Lock aktiv und dunkelt sich in der konfigurierten Ruhezeit selbstständig ab.
