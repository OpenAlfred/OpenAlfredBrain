# Roadmap & Tickets: „Die bessere Alexa"

Übersicht über die geplanten Erweiterungen für Alfred, um die verbleibenden Lücken gegenüber kommerziellen Sprachassistenten (wie Amazon Alexa) zu schließen und Alfreds Stärken als echter, autonomer KI-Agent voll auszuspielen.

---

## Ticket-Übersicht

| ID | Ticket | Bereich | Priorität | Aufwand | Status |
|---|---|---|---|---|---|
| [TICKET-01](01-wand-dashboard-kiosk.md) | **Wand-Dashboard & Kiosk-Modus (Smart Display)** | UI / Web | **Hoch** | Mittel | Geplant |
| [TICKET-02](02-smart-home-home-assistant.md) | **Smart-Home-Steuerung via Home Assistant** | Werkzeuge / IoT | **Hoch** | Mittel | Geplant |
| [TICKET-03](03-paketverfolgung-mail.md) | **Paket- & Sendungsverfolgung via E-Mail** | Werkzeuge / Mail | Mittel | Mittel | Geplant |
| [TICKET-04](04-webradio-podcasts.md) | **Freies Webradio & ARD Audiothek (Streams)** | Werkzeuge / Medien | Mittel | Gering | Geplant |
| [TICKET-05](05-verkehr-pendeln.md) | **Verkehrslage, Pendeln & proaktive Aufbruchswarnung** | Werkzeuge / Kalender | Mittel | Mittel | Geplant |
| [TICKET-06](06-raum-intercom.md) | **Multi-Raum Intercom & gezielte Durchsagen** | Audio / Multiroom | Niedrig | Mittel | Geplant |
| [TICKET-07](07-offline-fallback.md) | **Lokaler Notfall- & Offline-Modus** | Pipeline / Fallback | Niedrig | Hoch | Geplant |

---

## Leitplanken & Architekturprinzipien

Alle Tickets unterliegen den in `CLAUDE.md` und `docs/architektur.md` festgelegten Grundsätzen:
1. **Sicherheit:** Kritisches Handeln nur nach explizitem Ja; Fremdschutz vor Prompt Injections aus externen Inhalten (Mails, Web).
2. **Niedrige Kosten:** Bevorzugung lokaler Lösungen (SearXNG, lokale Streams, gecachte Sprachausgabe); LLM-Tokens nur dort einsetzen, wo semantische Intelligenz nötig ist.
3. **Modularität:** Werkzeuge modular unter `src/alfred/werkzeuge/` mit sauberer Gruppierung und Freigabestufen; Ausfallsicherheit (Alfred bleibt nie stehen).
4. **Gespräch in Echtzeit:** Das Audio-Erlebnis bleibt flüssig, Unterbrechungen und Zuhören werden respektiert.
