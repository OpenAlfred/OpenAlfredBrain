# TICKET-07: Lokaler Notfall- & Offline-Modus

- **Priorität:** Niedrig (Langfristiges Alleinstellungsmerkmal)
- **Bereich:** Pipeline / Fallback-Architektur (`src/alfred/pipeline.py`, `src/alfred/offline.py`)
- **Status:** Geplant
- **Aufwand:** Hoch

---

## 1. Kontext & Motivation
Das größte Ärgernis kommerzieller Assistenten (Amazon Alexa, Google Home) ist der vollständige Funktionsausfall bei Internetstörungen („Ich habe leider Schwierigkeiten, mich mit dem Internet zu verbinden...“). Selbst simple Aktionen wie „Schalte das Licht aus“ oder „Stell einen Timer auf 5 Minuten“ schlagen fehl.

Alfred verfügt bereits über ein **vollständig lokales Wachwort (Vosk)**. Wenn das Internet ausfällt, soll Alfred in einen lokalen Notfallmodus schalten und Basisfunktionen autark ausführen.

---

## 2. Kernfunktionen

### 2.1 Autarke Kernfunktionen ohne Internet
Auch bei getrennter DSL-/Glasfaserleitung funktionieren weiterhin:
* **Timer & Wecker:** Stellen, abfragen, stoppen.
* **Uhrzeit & Datum.**
* **Smart Home via Home Assistant:** Schalten von Lichtern und Heizung im lokalen Heimnetz (LAN).
* **Lokale Notizen & Einkaufsliste.**

### 2.2 Transparenter Offline-Hinweis
* Alfred antwortet mit lokaler Stimme: *„Keine Internetverbindung – ich arbeite im lokalen Notbetrieb.“*
* Komplexe Fragen (Websuche, Denker, Mails) werden verständlich vertagt, bis das Netz wieder steht.

---

## 3. Technische Umsetzung

### 3.1 Lokale Sprachausgabe (Piper TTS)
* Vorbereitung ist in der Architektur bereits als Kandidat vermerkt (`de_DE-thorsten-medium`).
* Piper läuft extrem schnell und ressourcensparend auf der CPU des Mini-PCs (Intel i7-6700T).

### 3.2 Lokales Mini-Sprachmodell (SLM via Ollama oder llama.cpp)
* Ein kompaktes, quantisiertes Modell (z. B. **Qwen 2.5 1.5B Instruct** oder **Llama 3.2 1B/3B**) läuft lokal auf der CPU.
* Es beherrscht Function Calling für die Grundausstattung (`kern`, `smarthome`).

### 3.3 Failover-Logik in `pipeline.py`
* Wenn der Verbindungsaufbau zu Mistral scheitert (Socket-Timeout / HTTP 5xx / No Route to Host):
  * Automatische Weiterleitung des Transkripts an das lokale Modell und Piper TTS.
  * Sobald das Netz zurückkehrt, nahtlose Rückkehr zur Cloud-Pipeline.

---

## 4. Akzeptanzkriterien
1. [ ] Wenn das Ethernet-Kabel gezogen wird, funktionieren Timer, Uhrzeit und lokales Smart Home weiterhin.
2. [ ] Die Reaktionszeit des lokalen Modells auf dem i7-6700T liegt unter 1,5 Sekunden.
3. [ ] Bei Netzrückkehr schaltet das System ohne Neustart wieder auf Mistral / GLM um.
