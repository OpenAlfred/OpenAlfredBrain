# TICKET-05: Verkehrslage, Pendeln & proaktive Aufbruchswarnung

- **Priorität:** Mittel
- **Bereich:** Werkzeuge / Kalender & Mobilität (`src/alfred/werkzeuge/verkehr.py`, `src/alfred/verkehr.py`)
- **Status:** Geplant
- **Aufwand:** Mittel

---

## 1. Kontext & Motivation
Kommerzielle Sprachassistenten können feste Pendelstrecken („Wie ist der Verkehr zur Arbeit?“) abfragen. Ein echter persönlicher Assistent geht jedoch einen Schritt weiter: Er verknüpft den **Kalender** (Termine mit Ort) mit der **Echtzeit-Verkehrslage** und warnt proaktiv vor dem Aufbruch.

---

## 2. Kernfunktionen

### 2.1 Reaktiv: Fahrzeit- & Verkehrsabfragen
* Werkzeug: `fahrzeit_abfragen(ziel, verkehrsmittel="auto")`:
  * *Beispiele:* „Wie lange brauche ich nach Stuttgart?“, „Gibt es Stau auf dem Weg zur Arbeit?“.
  * *Antwort:* „Aktuell 42 Minuten über die A8. Es gibt 8 Minuten Verzögerung wegen Baustellen.“

### 2.2 Proaktiv: Der smarte Aufbruch-Warner
* Wenn ein Kalendertermin in den nächsten 2 Stunden einen Ort enthält:
  * Alfred berechnet 45 Minuten vor Terminbeginn die aktuelle Fahrtzeit (inkl. Puffer).
  * 10–15 Minuten vor der nötigen Abfahrt meldet Alfred proaktiv:
    * *„Alexander, wenn du deinen Termin beim Zahnarzt um 15:00 Uhr schaffen willst, solltest du in 10 Minuten losgehen – auf der B10 ist stockender Verkehr.“*
  * Bei Nicht-Anwesenheit im Raum geht die Meldung als Push-Nachricht aufs Smartphone.

---

## 3. Technische Umsetzung
* **Routing-API:** Unterstützung von HERE API, Google Routes API oder Open-Source-Routing (OSRM / OpenTripPlanner).
* **Standort:** Heimatort aus `profil.yaml` oder aktueller Standort via GPS (aus der PWA).
* **Verknüpfung mit `kalender.py` und `ereignisse.py`:** Hintergrund-Check prüft fällige Anfahrten in der Event-Schleife.

---

## 4. Akzeptanzkriterien
1. [ ] Fahrzeitabfragen per Sprache liefern verlässliche Angaben mit Verkehrsverzögerungen.
2. [ ] Kalendertermine mit hinterlegter Adresse lösen zur passenden Zeit eine Vorwarnung aus.
3. [ ] Ruhezeiten und Modus-Einstellungen (`proaktiv` in `config/alfred.yaml`) werden respektiert.
