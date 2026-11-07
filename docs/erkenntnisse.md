# Erkenntnisse: Laborbuch

Chronik der Messreihen, Live-Läufe und Fehlersuchen mit dem, was daraus geändert wurde. Die
Regeln, die daraus folgen, stehen in [CLAUDE.md](../CLAUDE.md); wie die Bausteine heute
zusammenspielen, steht in [architektur.md](architektur.md). Einträge, die im Original kein Datum
trugen, stehen bei dem Tag, an dem sie ins Repository kamen (nach `git log`). Neue Einträge
unten anhängen, mit Datum.

## Themenübersicht

| Thema | Abschnitte |
|---|---|
| Pipecat und Mistral-Dienste (Latenzen, STT, TTS) | 2./3.10., 4.10. |
| Sprachmodell und Prompt (Butler-Rolle, Uhrzeit, Jahr, Wochentage, Werkzeugwahl, Prompt-Größe) | 2./3.10., 3.10., 4.10., 5.10., 6.10. |
| Wachwort, Schleuse und Stimmprofil (Bereitschaft gegen Fernseher, Sprechermodell, Stimmerkennung aus) | 2./3.10., 4.10., 6.10., 9.10., 20.10. |
| Audio-Hardware (Jabra, Windows, LED am Jabra, Gerätenummern nach dem Hochfahren, toter Stream im Betrieb) | 2./3.10., 4.10., 9.10., 16.10. |
| Kalender, Routinen, Profil, Wetter | 3.10., 4.10. |
| WhatsApp | 3.10., 4.10., 6.10. |
| App, Freigaben vom Handy, Ernährung, Haushalt | 4.10. |
| Robustheit (Aufsicht, Logs, Startprüfung, Sicherung, Ereignisse über einen Neustart, Neustart nach Audio-Ausfall) | 4.10., 16.10. |
| Aufträge, Denker, Websuche, SearXNG | 5.10. |
| Deploy und Konfiguration (erster Lauf auf dem Mini-PC) | 5.10. |
| Fremder Inhalt im Gespräch (Fremdschutz für schreibende Werkzeuge) | 6.10. |
| Eval-Suite (Szenarien gegen das Sprecher-Modell) | 6.10., 7.10. |
| Werkzeuggruppen je Modus, Werkzeuge als Paket | 6.10. |
| Raum-Gespräch: Zuhören, Bereitschaft, Ja-Prüfung, erfundene Aktionen, Kostenzähler | 6.10. |
| STT-Ausfälle: Reconnect-Drosselung bei Serverfehlern | 7.10. |
| Raum-Gespräch: doppelte Bestätigung, leeres Tagesprotokoll, Video im Gespräch | 9.10. |
| App: neue Oberfläche, Fähigkeiten-Übersicht, Zwischenspeicher im Browser | 10.10. |
| Mini-PC absichern: Firewall und Docker, Tailscale, Updates, Umzug | 16.10. |
| Bestände über Prozessgrenzen (`Dateibestand`: atomares Speichern, Kennung, gesperrte Dateien, libyaml-Lader) | 16.10. |
| Raum-Gespräch mit Besuch: Gästemodus im Code, Markdown-Reste, WhatsApp-Kanäle | 16.10. |
| Codewort statt Stimme für heikle Aktionen (WhatsApp, Mail, Ende des Gästemodus) | 20.10. |

## 2. und 3.10.2026: Grundaufbau und erster echter Lauf

Die Einträge dieses Abschnitts stammen aus den ersten beiden Tagen (Erstcommit vom 3.10.2026).
Das Gespräch im Browser mit echter Stimme lief am 2.10.2026.

### Messwerte der Mistral-Dienste (2.10.2026)

- Gemessen mit Alexanders Schlüssel: Transkript final 0,2 s nach dem Sprechende, solange weiter
  Audio fließt; erstes LLM-Token nach 0,5 s; erster Ton der Sprachausgabe nach 0,6 s.
- `MistralTTSService` braucht zwingend eine Stimme. Mistral bietet 30 Standardstimmen, nur
  Englisch und Französisch (Stand 2.10.2026). Sie sprechen Deutsch verständlich: Die Ausgabe von
  `gb_oliver_neutral` ließ sich mit dem lokalen deutschen Erkenner fast wörtlich zurücklesen.
- `MistralSTTService` schickt jedes Audio-Frame an Mistral, solange die Pipeline läuft. Bei
  0,006 $ pro Minute wären das im Dauerbetrieb rund 8,60 $ am Tag. Deshalb sitzt im Raum-Modus
  die Wachwort-Schleuse vor der Transkription.
- Pipecats `MistralSTTService` markiert Transkripte nicht als endgültig. Die Turn-Erkennung
  wartet dann jedes Mal ihren Sicherheits-Timer ab (1,89 s nach dem letzten Wort). Deshalb
  `alfred.stt.FinalizingMistralSTTService`; das sparte rund 1,4 s pro Antwort.
- Die lokale Piper-Stimme `de_DE-thorsten-high` berechnete auf dem Entwicklungsrechner 8,3 s
  Ton in 2,5 s. Auf dem Mini-PC wäre eher das Modell `medium` nötig; noch nicht gemessen.
- Wirft ein Processor in `setup()`, läuft die Pipeline standardmäßig trotzdem weiter und markiert
  ihn nur als unbrauchbar; deshalb setzt der Raum-Modus `ProcessorUnusablePolicy.CANCEL`.
  `PipelineWorker` beendet sich nach 300 s ohne Gespräch; der Raum-Modus schaltet das ab.
- Der Start-Banner des Runners enthält Unicode-Zeichen. `bot.py` stellt die Ausgabe auf UTF-8,
  sonst stürzt der Start unter Windows bei umgeleiteter Ausgabe ab.

### Wachwort-Erkenner (Vosk)

- Auf das Wachwort als einziges Wort eingeschränkt, meldet Vosk in den Zwischenergebnissen bei
  jedem beliebigen Satz „alfred". Nicht wieder versuchen.
- Mit vollem Vokabular gab es bei den Testsätzen weder Fehlauslösungen noch Aussetzer, das Wort
  erscheint aber erst rund 1,5 s nach dem Aussprechen. Die Schleuse puffert deshalb fünf Sekunden
  und reicht das Audio ab dem Wortbeginn nach.
- Ein trainiertes Modell (etwa openWakeWord) wäre schneller. Der Erkenner hängt hinter dem
  Protokoll `WakeWordDetector` und lässt sich tauschen. openWakeWord lässt sich nach meinem
  Wissensstand unter Python 3.12 nicht ohne Weiteres installieren; vor einem Wechsel prüfen.
- Vosk bringt ein Sprechermodell mit (`SpkModel`). Das war der Kandidat für den Filter „nur
  Alexanders Stimme", der am 4.10.2026 kam (siehe unten).

### Stimme: „extrem nervig"

Die britische Standardstimme mit Akzent empfand Alexander als „extrem nervig". Seit dem
3.10.2026 spricht Alfred mit „Jarvis v.1", einer eigenen deutschen Stimme aus Alexanders
Mistral-Konsole (UUID in `config/alfred.yaml`, Referenzaufnahme als Sicherung unter
`config/stimmen/`). Im Selbsttest kam der erste Ton nach 0,7 s, wie bei den Standardstimmen.
Die API meldet für eigene Stimmen `retention_notice=30`; die Doku erklärt das Feld nicht.
Fällt die Stimme weg, aus der Sicherung neu anlegen (`client.audio.voices.create_async`).

### Butler-Rolle

Mistral Small 4 spielt eine Butler-Rolle wörtlich: Es bot an, Kaffee zu bringen, erfand
Termine und sagte Erinnerungen zu. Auslöser waren das Wort „Butler" im Charaktertext und
beiläufige Sätze wie „Was geht?". Geholfen haben: „Butler" streichen, die Grenzen als
Verhaltensvorschrift formulieren („sag in einem Satz, dass du es noch nicht kannst, ohne
Ersatzangebot") und für Leerlauf eine Vorgabe machen. Daraus die Regel: Prompt-Änderungen
immer mit mehreren beiläufigen Sätzen gegen das echte Modell prüfen, nicht nur mit direkten
Fragen.

### Uhrzeit: „fünf nach elf"

Die Uhrzeit muss ausgeschrieben im Prompt stehen. Aus „23:14 Uhr" machte das Modell
„fünf nach elf". Sie wird beim Verbindungsaufbau und bei jedem Wachwort gesetzt; in langen
Gesprächen liefert das Werkzeug `aktuelle_zeit` die frische Zeit.

### Hinweise an das Modell: „Erinnere jetzt an: Wäsche"

Hinweise an das Modell (Erinnerung fällig, Denker fertig) gehen als `developer`-Nachricht in
den Kontext. Mistral kennt diese Rolle nicht, Pipecat macht daraus eine Nutzernachricht. Ohne
Kennzeichnung hielt das Modell „Erinnere jetzt an: Wäsche" für einen Auftrag. Seitdem beginnen
sie mit „Hinweis vom System, nicht vom Gegenüber" und enden mit „kein Werkzeugaufruf nötig".

### Protokoll-Zähler beim neuen Gespräch

Ein neues Gespräch nach `vergessen_nach_minuten` setzt auch den Protokoll-Zähler zurück
(`Sitzung.neues_gespraech`), sonst fehlen dem nächsten Protokoll die ersten Nachrichten.

### Hardware-Budget

Ein Konferenzlautsprecher für 60 bis 90 € war zu teuer; Ziel war ein gebrauchter Jabra Speak
410 für rund 35 €. (Am 4.10.2026 hing unter Windows ein Jabra Speak 510 am Rechner, siehe
unten.) Daraus die Regel in CLAUDE.md: Hardware-Empfehlungen zuerst mit Mindestanforderung
begründen, dann den günstigsten Weg nennen (gebraucht, vorhanden, Software statt Gerät).

### Sprecher: GLM 5.3 statt Mistral Small 4 (3.10.2026)

Seit dem 3.10.2026 ist der Sprecher GLM 5.3 (`zai-glm-5-3`, von Mistral gehostet), von
Alexander in der Konfiguration gesetzt; vorher Mistral Small 4 (`mistral-small-2603`). Im
Selbsttest mit GLM: erstes Token nach 0,4 bis 1,1 s, Werkzeugaufrufe sauber, sagt vor
Werkzeugaufrufen von sich aus einen kurzen Satz. Mistral Medium ist ausdrücklich nicht gewünscht.

Websuche und Code-Ausführung laufen über Mistrals Conversations API mit eingebauten Werkzeugen
(`modelle.werkzeuge`). Geprüft am 3.10.2026: Das geht mit GLM 5.3 (3,0 bis 3,5 s) und mit
Small 4 (2,6 s); die frühere Notiz, GLM könne die eingebauten Werkzeuge nicht, war falsch.

### Stand am 3.10.2026

Umgesetzt: Sprach-Loop im Browser und im Raum, Persönlichkeit, Unterbrechungs-Regeln,
Latenzmessung, lokales Wachwort, Werkzeuge mit Freigabestufen (Uhrzeit, Erinnerungen,
Gedächtnis, Denker, Kalender), proaktives Sprechen über eine Ereignis-Warteschlange,
Tagesprotokoll, Selbsttest. Live geprüft am 3.10.2026 über den Selbsttest: Uhrzeit, Merken,
Erinnerung mit Ansage, Denker mit Rechnung und Websuche. Antwortzeit ohne Werkzeug 1,6 bis
1,8 s, mit Werkzeug 2 bis 3 s. Zu diesem Zeitpunkt war der Kalender gegen Google ungetestet
(kein Token), der Raum-Modus nur mit synthetischen Sätzen geprüft, Ubuntu gar nicht; der
Kalender wurde noch am selben Tag angebunden (siehe unten).

### Selbsttest im Raum-Modus und im Leerlauf (3.10.2026)

Mit `--raum` läuft der Satz durch die Wachwort-Schleuse: Schleuse offen 2,2 s nach Satzbeginn,
Antwort 3,8 s nach dem letzten Wort, weil der kurze Testsatz endet, bevor der Erkenner das
Wachwort meldet; Schleuse nach 10 s Ruhe zu, Protokoll geschrieben.

Die Transkription hält eine Websocket-Verbindung, die im Raum-Modus bei geschlossener Schleuse
kein Audio bekommt. Nach 20 Minuten und nach 110 Minuten Stille stand sie noch (Selbsttest
`--raum --leerlauf`), ohne Neuaufbau; nach 110 Minuten brauchte die Sprachausgabe allerdings
4,8 s bis zum ersten Ton. Reißt die Verbindung ab, verbindet Pipecats `MistralSTTService` beim
nächsten Audio-Frame neu; als Netz darunter startet der systemd-Dienst den Prozess bei einem
Abbruch neu. (Siehe aber 4.10.2026: „Timeout waiting for response" 30 s nach dem Schließen der
Schleuse.)

## 3.10.2026, nachmittags und abends: Kalender, Routinen, Profil, WhatsApp, Push

### Wochentage und Uhrzeiten im Kalender

Mistral Small rechnet Wochentage falsch um: Aus „Mittwoch" wurde der 8.10., ein Donnerstag.
Deshalb steht die Liste der nächsten sieben Tage mit Datum im Prompt, `termine_abfragen`
nimmt ein Datum statt „Tage ab jetzt", und die Rückfrage vor dem Anlegen nennt den
Wochentag. Uhrzeiten in Werkzeugergebnissen ausgeschrieben liefern (`uhrzeit_gesprochen`),
sonst wird aus „15:30" „fünfzehneinhalb Uhr". Titel setzt das Modell trotz Sprachregel in
*Sternchen*; Pipecats `MarkdownTextFilter` vor der Sprachausgabe fängt das ab.

### Kalender gegen Google geprüft

Termine eines Wochentags vorlesen, Termin anlegen mit Rückfrage und „Ja" (Selbsttest mit zwei
Aufnahmen). Testtermine danach per API gelöscht. Der Selbsttest spielt seitdem mehrere Aufnahmen
als ein Gespräch ab; der Testordner `gedaechtnis/selbsttest` wird bei jedem Lauf geleert, sonst
beantwortet das Modell Fragen aus dem Protokoll des vorigen Laufs statt aus dem Kalender.

### Routinen und Wetter

Routinen (routinen.py): Auftrag als Text plus Zeitplan in `gedaechtnis/routinen.yaml`. Zur
Zeit legt `Routinen.warten` den Auftrag als Ereignis ab, der Sprecher erledigt ihn mit seinen
Werkzeugen. Live geprüft am 3.10.2026: anlegen, auf Zuruf starten, fällig werden. Nachholen
nur zwei Stunden, danach nächster Tag; beim Anlegen nach der Uhrzeit zählt heute als gelaufen.
Wetter (wetter.py) über Open-Meteo, kostenlos, ohne Schlüssel; Ortsname wird einmal geocodiert.

### „Я. Genauso?"

Das Modell sagte „Die Routine ist angelegt", ohne `bestaetigen` aufzurufen, als die
Transkription aus dem synthetischen „Ja, genau so" ein „Я. Genauso?" machte. Der Hinweis im
Werkzeugergebnis sagt deshalb ausdrücklich, dass ohne `bestaetigen` nichts passiert ist,
und dass die Rückfrage alle Angaben wiederholen soll. Mit „Ja, mach das" lief es.

### Profil

Profil (`gedaechtnis/profil.yaml`, Werkzeug `profil_merken`): einzelne Fakten über Alexander,
die Werkzeuge brauchen, komplett im Prompt. Fehlt ein Fakt, meldet das Werkzeug im Fehlertext,
was zu tun ist („frag nach dem Wohnort, merk ihn mit profil_merken, frag das Wetter erneut
ab"); das Modell folgt dem zuverlässig (geprüft 3.10.2026). Neue Fakten dieser Art gehören
ins Profil, nicht in die Konfiguration: Alexander will nichts zweimal sagen müssen.

### Jahreszahl: „zweitausendsechs"

Jahreszahlen ebenfalls ausschreiben: Mistral Small machte aus 2026 „zweitausendsechs" und
hielt dann das ganze Gespräch an 2006 fest, weil es seinen eigenen Text wieder liest. Seit
3.10.2026 stehen Datum und Jahr gesprochen im Prompt (`datum_gesprochen`, `jahr_gesprochen`)
und im Ergebnis von `aktuelle_zeit`. Regel: Alles, was das Modell in Worte umrechnen müsste
(Uhrzeit, Datum, Jahr), fertig ausgeschrieben liefern.

### Ausweich-Muster

Scheitert ein Dienst, versucht das Werkzeug selbst den nächsten Weg und meldet im Ergebnis
einen `hinweis`, was der Sprecher dazu sagen soll (Wetter → Websuche, live geprüft). Der Prompt
sagt zusätzlich allgemein, bei Fehlern einen anderen Weg zu versuchen. Ausweichen im Code ist
verlässlicher als im Prompt; beides zusammen deckt auch Fälle ab, an die im Code niemand
gedacht hat.

### WhatsApp-Brücke

neonize braucht protobuf ≥ 7.34, Pipecat erlaubt < 7. Deshalb läuft die Brücke
(`whatsapp_bridge.py`, stdlib + neonize + qrcode, keine alfred-Importe) in `.venv-wa` als
eigener Prozess auf 127.0.0.1:7861 mit Token-Datei; Alfred spricht sie über httpx an.
`NewClient(name)` nimmt den Pfad der Sitzungsdatenbank als Namen, `connect()` blockiert und
läuft im Thread, QR über `client.event.qr(handler)`. Ohne Kopplung geprüft: Status, Fehler;
das Senden selbst braucht Alexanders Kopplung. Verknüpftes Gerät = kein offizieller Weg,
Sperrrisiko klein, aber vorhanden; steht so im Handbuch.

### Aufgaben und Push

Aufgaben (aufgaben.py) und Push (benachrichtigung.py, ntfy): offene Aufgaben stehen im
Prompt; fällige Erinnerungen gehen zusätzlich als Push, wenn ein ntfy-Server konfiguriert ist.

### Hedda-Testaufnahmen

Testaufnahmen lassen sich unter Windows mit der Sprachsynthese „Microsoft Hedda" erzeugen
(16 kHz, mono). Die Hedda-Aufnahmen machen an Kommas Pausen, die die Turn-Erkennung als
Satzende nimmt: Für Selbsttests Sätze ohne Kommas sprechen lassen, sonst zerfällt ein Auftrag
in mehrere Züge.

## 4.10.2026

### Robuster im Gespräch: Unterbrechung ohne Text, Smart Turn, run_test

- Unterbrechung ohne Text: Pipecats englischer `interrupted_prompt` ließ das Modell die ganze
  Antwort wiederholen; jetzt deutscher Hinweis in `EmptyUserTurnConfig`, und der unterbrochene
  Assistententext endet mit „[hier unterbrochen]" (Handler `on_assistant_turn_stopped`).
- Smart Turn: `stop_secs` von 3 auf 1,5 s (`gespraech.pause_max_sekunden`); das waren die
  „Hänger" nach Sätzen, die Smart Turn für unfertig hielt.
- Pipecats `run_test` verträgt keinen zweiten Lauf mit demselben Processor (hängt); je Lauf
  einen frischen anlegen. Die Testsuite deshalb mit Zeitlimit starten (Start-Process + WaitForExit).

### Zusätzliche Bausteine seit 4.10.2026

Alle nur aktiv, wenn eingerichtet: `sprachfilter.py` (fremde Sprachen verwerfen), `kosten.py`
(Observer auf MetricsFrame), `stimme.py` (Vosk SpkModel; `SpkModel` lädt nicht selbst,
`Model.get_model_by_name` holt es), `mail.py` (IMAP/SMTP, kein OAuth: Gmail-Mailzugriff ist ein
eingeschränkter Bereich), `suche.py` (SearXNG, Rückfall auf Mistral), `musik.py` (spotipy, Extra
`musik`), Regeln per Stimme in `gedaechtnis/regeln.yaml`, Modus per `modus_wechseln`;
Prompt-Auffrischung über `Ressourcen.systemprompt` und `LLMUpdateSettingsFrame` via
`params.pipeline_worker`.

### Prompt-Größe

Gemessen 4.10.2026, alles aktiv: 38 Werkzeuge mit 11.600 Zeichen Schema und 3.500 Zeichen
Systemprompt, nach Kürzen der Beschreibungen, Bündeln der Musik-Werkzeuge und einer
Fähigkeitenliste, die nur noch Regeln und aktuellen Stand enthält (was ein Werkzeug tut, steht
allein in seiner Beschreibung). Messskript: Schemas über `DirectFunctionWrapper`. Neu gemessen
am 5.10.2026, siehe dort.

### Feedback, Aktionsprotokoll, Phrasen-Cache, Wachwort-Stimme, Selbsttest-Frist

- `feedback_merken` schreibt Alexanders Bemerkung samt letztem Wortwechsel nach
  `gedaechtnis/feedback.md`: Vor jeder Verbesserungsrunde zuerst diese Datei lesen.
- `gedaechtnis/aktionen.log`: jede Freigabe mit Stimmwert und Ergebnis, auch verweigerte.
- `tts_cache.CachingMistralTTSService`: Sätze bis 60 Zeichen werden nach dem ersten Mal aus
  `cache/phrasen/` gespielt (TTFB 0 statt 0,5 bis 5 s). Der Schlüssel enthält Modell, Stimme
  und Abtastrate; Fehlerläufe werden nicht gespeichert.
- Wachwort-Schleuse mit `stimmpruefung`: Audio ab dem Wachwort (meist 1 bis 2 s) gegen das
  Profil, Schwelle `schwelle_wachwort` 0,5; zu kurzes Audio gilt als passend.
- Der Selbsttest lässt seine Frist bei jeder Regung des Sprechers (Werkzeug, Modell, Ton)
  weiterlaufen; vorher brach er bei zwei Websuchen hintereinander mit „keine Sprachausgabe" ab.

### App und Stimme als getrennte Prozesse

App (web.py, FastAPI, Port 7870) und Stimme laufen als getrennte Prozesse auf denselben
Dateien. Bestände erben von `Dateibestand` (dateien.py) und laden nach, wenn sich die
Datei geändert hat; `build_ressourcen` (pipeline.py) ist der gemeinsame Aufbau. Nur der
Sprach-Prozess liefert Erinnerungen und Routinen aus. Das Textgespräch (textgespraech.py)
ruft die Werkzeuge mit einem `params`-Ersatz auf (SimpleNamespace wie in den Tests);
Werkzeuge dürfen deshalb `params.pipeline_worker` und `params.llm` als None vorfinden.

Beim Ausprobieren der App immer `--gedaechtnis gedaechtnis/selbsttest` nehmen; sonst
landen Testeinträge im echten Gedächtnis (passiert am 4.10.2026, danach aufgeräumt).

### Freigaben vom Handy, Ernährung, Haushalt

- Freigaben vom Handy (freigaben.py): Datei `gedaechtnis/freigabe.json` mit Kennung und
  Geheimnis; Push mit ntfy-Knöpfen (`Actions`-Kopfzeile, `method=GET`) auf
  `/api/freigabe/{id}/{ja|nein}?g=...`; `freigaben_beobachten` im Sprach-Prozess führt aus und
  legt die Ansage als Ereignis ab. Gesprochene Entscheidungen schließen die Datei.
- Ernährung (ernaehrung.py): Schätzung per Mistral mit `response_format json_object`, Barcode
  über Open Food Facts (User-Agent setzen). Haushalt (haushalt.py): Fotos als `image_url` mit
  Data-URI an `mistral-small-2603`, technisch geprüft; echte Kassenbons noch nicht.

### Verwaister Name in `build_voice_loop`

`build_voice_loop` wird von keinem Werkzeugtest durchlaufen; ein verwaister Name darin
(nach dem Herauslösen von `build_ressourcen`) fiel erst im Browser auf: Verbindung stand,
Pipeline startete nie, Alfred schwieg (4.10.2026). Seitdem baut
`test_sprachschleife_laesst_sich_ohne_anbindungen_aufbauen` die ganze Schleife mit leerem
Transport auf, und `bot.py` trennt bei einem Aufbaufehler die Verbindung und loggt
„Alfred konnte nicht starten". Nach Umbauten an pipeline.py zusätzlich
`.venv/Scripts/python -m pyflakes src/alfred bot.py` laufen lassen (meldet undefinierte Namen).

### Ja auf eine Rückfrage zählt sofort

Die Unterbrechungs-Regel funktionierte wie gedacht: Ein und zwei Wörter liefen durch, beim
dritten stoppte er. Kehrseite: Das Ja auf eine Rückfrage kommt meist, bevor die Frage
verklungen ist, und ging als Einwurf verloren; Alexander musste drei Wörter sagen. Seit
4.10.2026 zählt bei offener Freigabe (`res.offene_freigabe`) ein Antwortwort aus
`ANTWORTWOERTER` (turns.py) sofort, auch während Alfred spricht.

### Termine verschieben, Ansagen am Modell vorbei, Geburtstage

- Termine verschieben und absagen suchen nach Titelteil (`Kalender.suchen`), ohne Datum in den
  nächsten zwei Wochen; bei mehreren Treffern fragt das Werkzeug nach dem Tag, statt zu raten.
  Live geprüft am 4.10.2026 (Testtermin angelegt, verschoben, gelöscht).
- Ereignisse sind Text für das Modell („Hinweis vom System …"); eine `Ansage` (ereignisse.py)
  geht am Modell vorbei: Klingelton als `OutputAudioRawFrame` plus `TTSSpeakFrame`, danach als
  Assistentennachricht in den Verlauf. Timer nutzen das (sofort, kostenlos, kein Risiko, dass
  das Modell etwas anderes sagt). Der Transport sampelt den 24-kHz-Ton selbst um.
- Geburtstage liegen bei den Kontakten (`kontakte.yaml`, Feld `geburtstag`, auch ohne Nummer);
  `Geburtstagserinnerer` legt am Vortag um neun eine Erinnerung an, gemerkt pro Jahr in
  `geburtstage_erinnert.json`.

### Jabra Speak 510 unter Windows

PortAudio zeigt es viermal (MME, DirectSound, WASAPI, WDM-KS). Nur MME spielt im blockierenden
Betrieb wirklich ab; DirectSound nahm 1,9 s Audio in 0,00 s entgegen und gab nichts aus (Alfred
„sprach", man hörte nichts), WASAPI und WDM-KS öffnen bei 16/24 kHz nicht. MME kürzt Namen auf
31 Zeichen, deshalb gleicht `waehle_geraet` (room.py) über den vollen Namen eines anderen
Eintrags ab. Wenn „Bot stopped speaking" verdächtig früh nach „Bot started speaking" kommt, ist
es das.

### Werkzeugwahl bei Sätzen ohne klaren Auftrag

Mit 50+ Werkzeugen greift mistral-small bei Sätzen ohne klaren Auftrag daneben („Ich kann
dich nicht hören" → modus_wechseln feierabend). Werkzeugbeschreibungen sagen deshalb, wann
sie **nicht** gelten, und der Prompt hat die Regel „nur, wenn der Satz genau das verlangt".

### Mistrals Sprachausgabe hängt gelegentlich; Realtime-STT bricht ab

Mistrals Sprachausgabe braucht meist 0,4 bis 0,9 s bis zum ersten Ton, gelegentlich aber 7
bis 15 s, zufällig verteilt: auch bei Standardstimmen, kurzen Sätzen und ohne Pause davor
(Messreihe 4.10.2026; die Vermutung „Kaltstart der eigenen Stimme nach Pause" war falsch,
die erste Anfrage nach zwanzig Minuten war die schnellste). Warmhalten bringt also nichts.
`CachingMistralTTSService` bricht eine Anfrage ohne Ton nach 2 s ab und stellt sie neu, nach
weiteren 3 s ein drittes Mal; der letzte Versuch wartet ohne Frist. Mit nur einem neuen
Versuch (nach 3 s) hing es im Gespräch am 4.10.2026 einmal trotzdem 7 s. Pipecats
`stop_frame_timeout_s` (Standard 3 s) ist nur ein Sicherheitsnetz, ein sauber beendeter Satz
schließt seinen Kontext sofort; der Mistral-TTS bekommt 20 s, sonst meldet Pipecat „completed
with no audio" und legt den Kontext neu an. Die Sprachausgabe bleibt ein austauschbares Modul.

Mistrals Realtime-STT bricht gelegentlich serverseitig ab (`1011 Upstream connection error`,
„Timeout waiting for response"); Pipecat verbindet beim nächsten Audio-Stück neu, die ersten
Silben danach können fehlen. Nicht fatal, nur ein Hinweis im Log.

### Alexanders Vorgabe: Alfred darf nie stehen bleiben

Fehler werden geloggt und es geht weiter. Umsetzung: `room.run_room` ist eine Aufsicht, die
`_einmal_laufen` nach jedem Abbruch neu baut (Pause 5 s, bei Dauerfehlern verdoppelt bis
120 s); nur das `Abbruchsignal` (Strg+C, SIGTERM) beendet. Pipecats Runner läuft deshalb mit
`handle_sigint=False, handle_sigterm=False`. Hintergrundaufgaben laufen in `beaufsichtigt`
(pipeline.py). `logbuch.einrichten` schreibt `logs/<prozess>.log` und `logs/fehler.log`;
`/api/status` liefert `fehler`, die App zeigt sie. Beim Debuggen zuerst `logs/fehler.log`.

### Debuggen, Startprüfung, Sicherung unter Windows

Beim Debuggen: `python -m alfred.pruefen` zuerst (Schlüssel, Geräte mit Host-API, TTS-
Antwortzeit, STT-Verbindung, Anbindungen, Sicherung), dann `logs/fehler.log`, dann der
Reiter „Alfred" in der App (Live-Zustand aus `gedaechtnis/mitschrift.json`, geschrieben vom
Sprach-Prozess mit Lebenszeichen alle 5 s). Die Konsole filtert Pipecats Prompt-Dumps und
„auto-registered"-Zeilen (`logbuch.leise`); im Browser-Modus hängt `logbuch.einrichten` erst
in `bot()`, weil Pipecats `main()` vorher alle Logger-Senken entfernt.

`python -m alfred.sichern` ersetzt unter Windows das Bash-Skript (gleicher Inhalt, Zip statt
tgz, gleicher Zielordner, löscht nur eigene `alfred-*.zip`).

### Stimmprofil: eigene Stimme bei 0,27

Alexanders eigene Stimme lag am Jabra bei 0,27, weil das alte `--anlernen` das
Standardmikrofon nahm (Headset). Jetzt: Mikrofon des Raums über `room.find_device`, 20 s freie
Rede plus kurze Wachwort-Sätze (Stille abgeschnitten, auf 2,5 s gekürzt wie in der Schleuse),
Profil = Mittelwert normierter Vektoren, Schwellen-Empfehlung aus Leave-one-out-Ähnlichkeit
(`selbstaehnlichkeit`), Übernahme per gezieltem Zeilen-Edit (`schwellen_setzen`, Kommentare
bleiben). Das Profil speichert Mikrofon, Datum, Proben; alte Profile ohne Mikrofon meldet
`alfred.pruefen` als Warnung.

### STT-Timeout 30 s nach dem Schließen der Schleuse

Fehler „Mistral STT error: Timeout waiting for response" mit anschließendem 1011 kam genau
30 s nach „Wachwort-Schleuse geschlossen": Hinter geschlossener Schleuse fließt kein Audio, und
Mistral beendet stille Realtime-Sitzungen selbst. Jetzt schließt `on_sleep` die Verbindung
(`FinalizingMistralSTTService.ruhen`, nicht Pipecats `_disconnect`, das die Verbindung offen
lässt, weil der abgebrochene Empfang `_connection` vorher leert). Das nächste Audio verbindet neu.

### Gesprächspausen ohne Wachwort

Die Schleuse zählte jedes Sprechen im Raum als Aktivität, ein Fernseher hätte sie bei längerer
Frist ewig offen gehalten. Jetzt: `offen_sekunden` frei, danach bis `gespraech_sekunden` nur
Sätze, die der `Stimmfilter` (hinter dem Sprachfilter) annimmt: Ähnlichkeit ≥
`schwelle_wachwort`, Wachwort im Satz, oder zu kurz zum Prüfen. Fremdes Sprechen setzt die
Frist dann nicht zurück; ein angefangener Satz schiebt das Ende höchstens 30 s auf. Der
Stimmprüfer liefert sein Ergebnis je VAD-Abschnitt als Future (`urteil()`, Frist 1 s), weil das
Transkript rund 0,25 s vor dem Stimmvektor fertig ist. Ein verworfenes Transkript lässt den Zug
über Pipecats `user_turn_stop_timeout` (5 s) still enden (`idle_prompt` ist None).
`gespraech_beenden` (nur mit Schleuse) schließt sie beim nächsten Audio.

### WhatsApp: „Kein Kontakt namens Jan"

Alfred kannte nur `kontakte.yaml`. Das Telefonbuch liegt aber in der neonize-Sitzung
(`whatsmeow_contacts`, `full_name` = Name aus dem Handy; am 4.10.2026 189 Einträge, alle als
Nummer-JID). Brücke: `GET /kontakte`; Alfred: `WhatsApp.adressbuch()` (10 min
zwischengespeichert) und `kontakt_suchen` (Wörter einzeln, Umlaute ausgeschrieben, kurze Wörter
nur exakt, längere ab 0,8 Ähnlichkeit). Beim Prüfen nur Trefferzahlen ausgeben, nie Namen oder
Nummern.

### Das erste echte WhatsApp-Gespräch ging viermal schief

1. „Jan ist nicht in deinen Kontakten", ohne nachzusehen. Die Behauptung stammte aus dem
   Tagesprotokoll eines früheren Fehlschlags, und das Protokoll steht im Systemprompt. Seitdem
   lässt die Zusammenfassung weg, was Alfred nicht konnte oder nicht fand. Der Prompt sagt
   außerdem, dass Alfred nur aus dem Ergebnis von `nachricht_senden` weiß, ob jemand im
   Adressbuch steht.
2. Einen Nachrichtentext erfunden. `nachricht_senden` geht jetzt auch ohne Text: Das Werkzeug
   sucht den Empfänger und meldet, dass Alfred nach dem Text fragen soll. Diktierter Text wird
   wörtlich übernommen, auch kurzer.
3. „Nein" ließ die Rückfrage offen, weil das Modell `abbrechen` nicht aufrief. Jetzt verwirft
   der Code sie (`Rueckfragewaechter`, siehe architektur.md). Kehrseite: Ein Satz, der mit Nein
   beginnt, verwirft immer. Zum Ändern ruft das Modell das Werkzeug ohnehin neu auf.
4. Im App-Chat fehlten die Werkzeugaufrufe im Verlauf. Das Modell sah die offene Rückfrage
   nicht und fragte auf „Ja" neu. `Textgespraech.verlauf` behält jetzt Aufrufe und Ergebnisse
   (`_als_dict`), kürzt nur vor einer Nutzernachricht (`_kuerzen`), und die App zeigt
   `sichtbarer_verlauf`.

Live geprüft gegen Mistral mit Fake-WhatsApp in `gedaechtnis/selbsttest`: Der diktierte Text
wurde übernommen, „Nein, schreib ihm lieber …" ersetzte die Rückfrage, und „Ja" schickte ab.
Falsche Fakten im Protokoll von Hand entfernen; sonst wiederholt Alfred sie den ganzen Tag.

## 5.10.2026

### Aufträge an Alfred

Alexander will Arbeit abgeben („analysier …, Bericht mit Quellen, danach die Tech-News") und
**immer** sofort eine Bestätigung mit Zusammenfassung hören (auftraege.py).

- Bestätigen im Code: `auftrag_geben` spricht über `_sofort_sagen` (`TTSSpeakFrame` über
  `params.llm`) und meldet `FunctionCallResultProperties(run_llm=False)`, damit das Modell nicht
  noch etwas anhängt. Das Textgespräch gibt dafür einen Sammler als `llm` mit (`_Sprachausgabe`)
  und fragt das Modell nicht erneut, wenn alle Werkzeuge einer Runde so geantwortet haben.
- Abarbeiten: `Auftragsbearbeiter` nur im Sprach-Prozess (Hintergrundaufgabe „Aufträge"), einer
  nach dem anderen. Die Liste ist YAML, geteilt mit der App: Lebenszeichen alle 30 s; ein
  laufender Auftrag ohne Lebenszeichen seit 180 s wartet wieder. Beim Beenden legt der
  Bearbeiter ihn zurück, und das zählt nicht als Versuch. Nach drei Abstürzen ist er
  gescheitert. Abbrechen aus der App sieht der Bearbeiter beim nächsten Takt (2 s).
- Bericht: `Denker.bericht` mit fester Gliederung (Kurzfassung, Ergebnis, Hinweise, Quellen).
  Die Websuche liefert Adressen nur als `tool_reference`-Stücke neben dem Text, `_quellen`
  hängt sie an. In der letzten Runde gilt `tool_choice="none"`, damit er mit dem schreibt, was er
  hat.
- Live geprüft mit Fake-Push in `gedaechtnis/selbsttest`. Aus Alexanders Satz wurden zwei
  Aufträge mit Push, die Bestätigung kam nach 3,5 s. Der Tech-News-Bericht brauchte ohne Grenze
  21 Websuchen (rund 60 Cent) und 159 s, mit 32 echten Quellen. Mit `MAX_SUCHEN_BERICHT` = 8
  waren es 53 s und 18 Quellen.
- Weitere Funde aus den Live-Läufen:
  - Der Vorsatz „Hier ist der Bericht:" wird abgeschnitten.
  - Den Push-Wunsch aus dem Gesprächskontext bezog der Denker auf sich („kein Werkzeug fürs
    Handy"). Jetzt steht im Kontext, dass Alfred das übernimmt.
  - Die Kurzfassung brach bei „zum 15." mitten im Satz ab. Seitdem gibt es `_letztes_satzende`.

### Websuche billiger: SearXNG

Alexander: „extrem teuer". Mit `suche.url` (SearXNG) bekommt der Denker andere Werkzeuge:

- `websuche`: SearXNG, kostenlos, mit `nachrichten` (categories=news) und `zeitraum`
  (time_range).
- `seite_lesen`: seiten.py, stdlib-HTMLParser, Text aus article/main.
- `tiefensuche`: Mistral, höchstens zweimal je Auftrag (`GRENZEN_BERICHT`).

Ohne SearXNG bleibt es bei Mistral, mit höchstens 8 Suchen je Bericht und 4 beim Nachdenken.
Ist SearXNG nicht erreichbar, meldet websuche „Nimm tiefensuche". Absichtlich gibt es keinen
unbegrenzten Rückfall auf Mistral, damit ein Ausfall nichts kostet. Die Warnung steht im Log.

- `seite_lesen` liest nur öffentliche Adressen (`is_global`, auch nach jeder Weiterleitung,
  nur Port 80/443). Sonst könnte eine Webseite den Denker auf die App unter 127.0.0.1 lenken,
  die keine Anmeldung hat, oder auf Tailscale-Geräte in 100.64/10. DNS-Rebinding ist nicht
  abgefangen.
- Live: 5 von 5 echten Quellen aus dem Mistral-Bericht lesbar, 0,4 bis 1,6 s, 3.500 bis 6.000
  Zeichen. Gefunden: `title`-Tags in SVG-Logos hingen am Seitentitel; jetzt zählt nur der erste.

### Vergleich SearXNG gegen Mistral

Vergleich am 5.10.2026 mit demselben Tech-News-Auftrag (morgens Mistral, abends SearXNG;
andere Nachrichtenlage, also kein sauberer Laborvergleich):

| Lauf | Dauer | Werkzeuge | Kosten Suche | Quellen |
|---|---|---|---|---|
| Mistral ohne Grenze | 159 s | 21 Suchen | rund 63 Cent | 32 |
| Mistral mit Grenze | 53 s | 8 Suchen | rund 24 Cent | 18 |
| SearXNG | 136 s | 9 Suchen, 6 Seiten, keine tiefensuche | 0 | 20 |

Der SearXNG-Bericht ist gründlicher: Zahlen mit Uhrzeit, Belege aus mehreren Quellen und
ehrliche Hinweise, was nicht lesbar war. Er hat weniger Themen und mehr Wirtschaft als Gadgets.
Schwach ist die Nachrichtensuche: Von den Engines antwortete nur Bing News, einmal kam gar
nichts. Bei unter drei Treffern sucht `_eigene_suche` deshalb allgemein nach. Ohne Antwort
blieben auch Brave und DuckDuckGo; die allgemeinen Treffer kommen über „google cse".

### SearXNG auf Alexanders PC

Docker Desktop (Nutzerinstallation, `docker` steht erst in neuen Terminals im PATH, sonst fehlt
`docker-credential-desktop`), Container `searxng` mit `--restart unless-stopped` auf
127.0.0.1:8888, Einstellungen in `%USERPROFILE%\searxng`.

### Mini-PC-Einrichtung fast ohne Handarbeit

Für den Mini-PC liegen unter `deploy/` die Einrichtung (`einrichten.sh --sicherung ZIP`), die
Aktualisierung (`aktualisieren.sh`) und systemd-Dienste (`alfred-raum@<raum>` und weitere);
Stand 5.10.2026 alles noch nicht auf Linux gelaufen, nur `bash -n` geprüft. Alexanders Vorgabe
(5.10.2026): so viel wie möglich automatisch. Das Skript fragt nur nach dem Mistral-Schlüssel
(`read -s`, wenn die `.env` fehlt), nach der Tailscale-Anmeldung und nach dem WhatsApp-Koppeln.
Alles andere macht es selbst:

- Pakete, venvs, Sicherung (nur beim ersten Mal, Marke `gedaechtnis/.eingespielt`).
- SearXNG-Container, danach `suche.url`, aber nur, wenn SearXNG JSON liefert.
- Tailscale, `tailscale serve /app`, `benachrichtigung.app_url`.
- Dienste installieren, prüfen und erst dann starten, wenn die Prüfung kein ✘ zeigt.

Was es in Dateien schreibt, steht in `alfred.einrichtung` (Python, getestet), nicht in sed.
Geheimnisse kommen über die Umgebungsvariable ALFRED_WERT, nicht über die Kommandozeile.
`python -m alfred.room <raum> --hoertest` prüft Mikrofon und Lautsprecher ohne Mistral.

### Erster Lauf auf dem Mini-PC

Per `ssh mini-pc`; sudo verlangt dort ein Passwort, deshalb nur die Teile ohne sudo: Code per
`git push minipc`, venvs, Sicherung eingespielt, SearXNG über die Gruppe docker,
`alfred.pruefen` mit allem ✔ außer PyAudio (braucht portaudio19-dev aus dem Skript), 397 Tests
grün, `selftest --raum` vom Mini-PC aus: Schleuse offen nach 2,2 s, Antwort 3,6 s nach dem
letzten Wort, wie unter Windows. Das Jabra war nicht eingesteckt.

- Der Server stand auf UTC (Alfred sagte „neunzehn Uhr"); `alfred.pruefen` warnt jetzt bei
  fremder Zeitzone.
- Im Selbsttest blieb das Protokoll leer: Der Runner bricht beim Beenden das Einschlafen ab,
  während die Zusammenfassung noch läuft (seit `ruhen` eine Sekunde später), und
  `Sitzung.protokollieren` hatte den Zähler schon vorher vorgerückt, sodass `abschliessen`
  nichts mehr fand. Jetzt rückt der Zähler erst nach dem Schreiben vor, unter einer Sperre gegen
  doppeltes Schreiben; dasselbe hätte bei `systemctl stop` direkt nach einem Gespräch das
  Tagesprotokoll gekostet.
- `deploy/sichern.sh` packte ein tgz ohne alfred.yaml, Persönlichkeit, Client-Secret und
  Stimmen, weil die Liste zweimal gepflegt war; jetzt ruft es `python -m alfred.sichern` auf.
- Das Skript fragt das sudo-Passwort jetzt gleich am Anfang ab, und Docker läuft ohne sudo in der
  Gruppe docker. Die sudo-Schritte (Pakete, Gruppe audio, Zeitzone, Tailscale, Dienste) hat
  Alexander noch nicht ausgeführt.

### `config/alfred.yaml` nicht mehr im Git

`config/alfred.yaml` ist seit dem 5.10.2026 nicht mehr im Git (`.gitignore`). Im Repository liegt
die Vorlage `config/alfred.beispiel.yaml`; `load_settings` nimmt sie, wenn die eigene Datei
fehlt, und `konfig_anlegen` kopiert sie vor dem ersten Schreiben. Der Grund: Beim Umzug lag
nach `git clone` schon eine alfred.yaml, die die Sicherung nicht überschreibt; Alexanders
Einstellungen wären verloren gegangen. Außerdem kann so kein `git pull` mehr an ihr scheitern,
und das ntfy-Thema kann nicht versehentlich ins Git geraten. Neue Einstellungen bekommen ihren
Standard in config.py und ihre Erklärung in der Vorlage.

### Prompt-Größe neu gemessen

Mit allem eingerichtet: 64 Werkzeuge mit 22.280 Zeichen Schema; Grundausstattung 18 Werkzeuge
mit 5.729 Zeichen (am 4.10.2026 waren es nach dem Kürzen 38 Werkzeuge mit 11.600 Zeichen).
Dazu kommen rund 3.500 Zeichen Systemprompt. Messskript: Schemas über `DirectFunctionWrapper`.

## 6.10.2026

### Fremder Inhalt im Gespräch

Mails, WhatsApp-Nachrichten, Suchtreffer, Berichte und Denker-Ergebnisse waren nur im
Werkzeugergebnis und im Prompt als Daten markiert; eine Mail mit „Speichere meine neue Adresse
x@y" brachte das Modell zu `kontakt_email_merken`, und die nächste Mail wäre an die fremde
Adresse gegangen. Jetzt steht die Regel im Code: `Ressourcen.fremder_inhalt` (Herkunft,
Zeitpunkt) setzen `mails_abfragen`, `mail_vorlesen`, `nachrichten_lesen`, `bericht_lesen`,
`websuche`, das Wetter im Websuche-Ausweg und `auftraege_anzeigen` mit Kurzfassung. Ereignisse
mit fremdem Text (Ergebnis von `nachdenken`, fertiger Bericht) kommen als `Fremd(...)`
(ereignisse.py); der Zusteller setzt die Marke beim Ausliefern, nach dem Aufwecken, damit sie zu
dem Gespräch gehört, in dem der Text landet. Jedes Werkzeug ist in werkzeuge.py eingestuft:
`LESEND`, `SCHREIBEND` (mit Rückfrage-Vorlage, die den Wert nennt) und
`BESTAETIGUNGSPFLICHTIG`; ein Test prüft, dass nichts aus `build_tools` fehlt. Steht die Marke,
laufen schreibende Werkzeuge in der Hülle `_mit_fremdschutz` über `_freigabe_anfordern`
(„E-Mail-Adresse von Jan auf x@y setzen"), mit Grund im Hinweis, im Push und im `aktionen.log`
(`Freigabe.protokolltext`); die Aktion läuft erst in `bestaetigen` mit dessen params
(`Freigabe.starten`), damit `auftrag_geben` seine Bestätigung dort spricht. `musik` mit
`was_laeuft` bleibt frei (Vorlage liefert None). Die Marke endet mit dem Gespräch
(`Sitzung.neues_gespraech`; im Chat, sobald der Zug mit dem fremden Text aus dem Verlauf fällt),
nicht nach einer Frist; auch eine reine Rechnung des Denkers setzt sie, weil von außen nicht zu
sehen ist, ob er gesucht hat. Rückfragen von `mail_senden`, `mail_antworten` und
`nachricht_senden` nennen Adresse bzw. Nummer (`nummer_gesprochen`); die Nummer steht nur in der
gesprochenen Fassung, nicht im Log (`Freigabe.protokoll`). Offen: Das Tagesprotokoll fasst auch
fremde Texte zusammen und steht am nächsten Tag im Systemprompt; dagegen hilft die Marke nicht.

### Ungefragtes mit Maß

Der Zusteller sprach jedes Ereignis aus, sobald 1,5 s Ruhe war: ohne Grenze, ohne Ruhezeit,
ohne Rücksicht auf den Modus. Jetzt tragen Ereignisse eine Klasse (`Ereignis` in ereignisse.py):
Angefordertes (Denker, Aufträge, Timer, eigene Erinnerungen) kommt immer durch, Ungefragtes
(Routinen, später Briefings und Vorschläge) nur nach den `Zustellregeln` im Code: Ruhezeit
22:00 bis 07:00, höchstens sechs am Tag (Zähler in `gedaechtnis/proaktiv.json`), im Modus
feierabend nur Dringendes, Sperr-Regeln aus `regeln.yaml`; alles unter `proaktiv` in der
Konfiguration. Zurückgehaltenes wartet im Zusteller, geht mit Push-Text aufs Telefon oder
verfällt (Routinen zwei Stunden nach ihrer Zeit). Der `Ansagewaechter` erkennt „nicht jetzt"
(Ansage kommt 30 Minuten später noch einmal) und „lass das" (Regel nach Ja über `bestaetigen`).
Live geprüft im Selbsttest mit einer Routine, die kurz nach dem Start fällig wurde: um 13:30
als ungefragte Ansage 1 gezählt, Werkzeugaufruf `aktuelle_zeit`, Sprachausgabe „Alexander, es
ist jetzt dreizehn Uhr dreißig." „Nicht jetzt" und „lass das" bisher nur in Tests mit `run_test`.
Beim Merge mit „Fremder Inhalt" wurde `Fremd` zur Hülle, die `als_ereignis` in ein `Ereignis`
mit Feld `herkunft` übersetzt; die Marke setzt der Zusteller weiterhin nach dem Aufwecken.

### Eval-Suite: erste Läufe

`python -m alfred.eval` (eval.py, 18 Szenarien in `tests/eval_szenarien.yaml`, Aufbau in
architektur.md) läuft gegen das echte Sprecher-Modell auf Textebene. Erster Lauf mit
mistral-small-2603: 11 von 18 Szenarien bestanden, 41 s, 43 Modellaufrufe, 344.000 Token Eingabe
(rund 8.000 je Aufruf, fast alles Systemprompt und Werkzeugschemas). Die Fehlschläge hatten ein
Muster: Das Modell kündigte den Werkzeugaufruf nur in Worten an („Timer stellen auf zehn Minuten
für Nudeln.", „Jan sucht im Telefonbuch.", „… leg ich an. Bestätigen?" ohne `routine_anlegen`)
oder behauptete „Jan ist nicht in deinem Telefonbuch", ohne `nachricht_senden` aufzurufen; erst
auf den zweiten Satz kam der Aufruf. Bestanden: beiläufige Sätze, Jahr, Uhrzeit, Wochentag, Nein
verwirft, Wetter ohne Wohnort, beide Mail-Szenarien, Auftrag, Erinnerung.

Derselbe Lauf mit `--modell zai-glm-5-3`: 16 von 18 in 1:17 min, 49 Aufrufe, 373.000 Token
Eingabe und viermal so viel Ausgabe (ausführlicher, proaktive Nachfragen, einmal „halb acht" für
07:32). Gravierend: GLM rief nach `routine_anlegen` im selben Zug `bestaetigen` auf, ohne
Alexanders Ja (siehe nächster Eintrag). Die Konfiguration stand bei diesen Läufen auf
mistral-small-2603, nicht auf GLM; die Kosten blieben unbekannt, weil `preise.modelle` auf 0 steht.

### Freigaben: GLM bestätigte die eigene Rückfrage

In der Eval-Suite rief GLM 5.3 nach `routine_anlegen` im selben Zug `bestaetigen` auf, ohne
Alexanders Ja; `bestaetigen` prüfte nur, ob eine Rückfrage offen ist. Jetzt zählt
`Ressourcen.nutzerzuege` die Sätze des Gegenübers (`Rueckfragewaechter` je Transkript,
`Textgespraech.antworten` je Text), jede Freigabe merkt sich den Stand beim Anlegen
(`Freigabe.nutzerzug`), und `bestaetigen` verweigert ohne neuen Satz: nichts ausgeführt, Rückfrage
bleibt offen, Eintrag „VERWEIGERT ohne Antwort des Gegenübers" im Aktionsprotokoll (über
`protokolltext`, also ohne Telefonnummer). Werkzeugtext und Hinweis sagen dem Modell, `bestaetigen`
nie im selben Zug wie die Frage aufzurufen. Systemhinweise und Ereignisse zählen nicht als Satz.
Nachprobe mit `--modell zai-glm-5-3 --wiederholungen 2 routine`: vier von vier Läufen bestanden,
GLM fragte und wartete; den Schutz selbst lösten nur die Tests aus (Werkzeug direkt, zählender
Wächter, App-Chat über zwei Züge). Am Abend beim Zusammenführen durch die Ja-Prüfung ersetzt
(siehe „Zusammenführen" unten); der Fall „bestaetigen im selben Zug" bleibt dort verweigert.

### Werkzeuggruppen je Modus

Werkzeuge liegen seit dem 6.10.2026 im Paket `src/alfred/werkzeuge/` mit Gruppen, die ein Modus
abschalten kann. Prompt-Größe vorher (je Werkzeug `DirectFunctionWrapper(fn).to_function_schema()
.to_default_dict()` als JSON ohne ASCII-Zwang, Zeichen summiert): alles eingerichtet 64 Werkzeuge
mit 22.280 Zeichen Schema (Modi standard und sparring), Fokus 54 mit 19.084, Feierabend 49 mit
16.965; Grundausstattung 19 mit 6.086 (18 und 5.729 ohne `modus_wechseln`, das es nur bei mehr
als einem Modus gibt). Systemprompt im Selbsttest rund 4.400 Zeichen.

Live geprüft (Selbsttest mit vier Hedda-Sätzen): „Wechsel in den Feierabendmodus" rief
`modus_wechseln` auf, der frische Prompt nannte die abgeschalteten Gruppen, und auf „Setz Milch
auf die Einkaufsliste" kam „Im Feierabendmodus mach ich keine Einkaufslisten", ohne
Werkzeugaufruf. Zwei Funde zu mistral-small-2603, unabhängig vom Umbau (mit dem alten Code
genauso): Es schreibt den Werkzeugaufruf gelegentlich als Text in die Antwort
(`modus_wechseln{"modus": "standard"}`); dann passiert nichts, und der Modus bleibt. Und nach dem
Text „modus_wechseln" blieb der Stream in 3 von 10 Anfragen ganz stehen (20 s, bis der Selbsttest
abbrach), direkt gegen die API in alter und neuer Reihenfolge der Werkzeuge gemessen; Pipecats
LLM-Dienst hat dafür keine Frist. Nach dem fehlgeschlagenen Rückwechsel wich das Modell auf
`aufgabe_anlegen` aus und behauptete, Milch stehe auf der Einkaufsliste; deshalb sagt der
Modus-Satz jetzt auch „nimm kein anderes Werkzeug als Ersatz". Auf Textebene (Textgespraech gegen
Mistral) dasselbe Bild: 33 statt 48 Werkzeuge im Feierabend, Einkaufsliste abgelehnt, mit dem
Zusatz kein Ausweichen mehr; der Rückwechsel scheiterte in zwei von drei Läufen an der
Text-Antwort statt des Aufrufs.

### Raum-Gespräch am Abend (Jabra, mistral-small-2603, 17 Minuten)

- Die Schleuse schloss die ganze Zeit nicht: Die verpasste Erinnerung weckte sie beim Start,
  danach hielten Gesprächszeit und Stimmfilter sie offen; Fernsehstimmen lagen bei 0,36 bis 0,48,
  die Schwelle bei 0,46. Darauf antwortete Alfred („Yesterday that was planned."). Abhilfe:
  Bereitschaft (Alexanders Wunsch: aktiv nur, wenn er „Alfred" sagt), und ungeprüfbare Sätze mit
  mehr als drei Wörtern zählen nach der freien Zeit nicht mehr.
- `bestaetigen` schickte „Hi!" an Jan, ohne dass Alfred gefragt hatte, auf das verhörte
  Weiterdiktieren „He is all for it." Seitdem prüft der Code Rückfrage und Ja
  (`verlauf_seit_rueckfrage`, `zustimmung`). Live geprüft (Textprobe mit Attrappe): dasselbe
  „He is all for it." wird verweigert, Alfred fragt neu.
- Diktate und Erzählungen zerfielen an Denkpausen in mehrere Züge, und das Modell baute aus den
  Hälften neue Texte, bis zur vertauschten Perspektive. Alexander: „extrem anstrengend, wenn ich
  alles in einem Stück erzählen muss". Abhilfe: Zuhören (`zuhoeren`, `GeduldigeTurnErkennung`,
  5 s Stille statt 1,5 s), beim Diktat von selbst; Regel „ich bleibt ich".
- Mistral Small rief vorhandene Werkzeuge nicht auf und erfand stattdessen: „Ich habe heute
  nichts verbraucht" (kosten_heute), „keine neue Nachricht erhalten" (nachrichten_lesen), „richte
  die Erinnerung ein" (es gab nur einmalige). Das Tagesprotokoll nahm die erfundene Erinnerung als
  Tatsache auf (von Hand korrigiert). Abhilfe: Wächter (behauptungen.py), wiederkehrende
  Erinnerungen, Werkzeuge im Protokoll-Auftrag, Prompt-Sätze. Live geprüft: Erinnerung werktags,
  Kosten, „gestern" richtig.
- Der Kostenzähler zählte jede Messung bei jedem Weiterreichen (Beobachter sehen jeden Schritt):
  172 statt 17 Minuten Transkription. Jetzt nur `first_push`; Listenpreise in config.py
  (Mistral-Preisliste vom 6.10.2026: mistral-small-2603 0,15/0,6 $ je Million Token, zai-glm-5-3
  1,4/4,4 $ mit 0,14 $ für zwischengespeicherte Eingabe, Sprachausgabe 16 $ je Million Zeichen,
  Echtzeit-Transkription 0,006 $ je Minute).
- Mistrals Echtzeit-Transkription fiel abends mehrfach aus (`EngineDeadError`,
  `QueueOverflowError`, 1011, 1013 „try again later"); dann kommen nur Zwischentranskripte oder
  gar nichts. Kein Fehler in Alfred, aber bei Ausfällen zuerst `logs/fehler.log` prüfen.
- Selbsttest-Aufnahmen mit Hedda: Denkpausen durch Zusammensetzen einzelner Sätze mit Stille
  dazwischen; Heddas eigene Stille am Satzende und -anfang verlängert die Pause um rund eine
  halbe Sekunde. Der Selbsttest lässt Aufnahmen jetzt zu Ende laufen (negative Antwortzeit = zu
  früh geantwortet) und meldet Bot-Sprechen wie ein Lautsprecher; `--raum --bereitschaft` prüft
  das Schließen. Geprüft: Eine Erzählung mit drei Sekunden Denkpause kam als ein Zug an; in
  Bereitschaft schloss die Schleuse acht Sekunden nach einer Rückfrage.

### Zusammenführen: Fremdschutz, Zustellregeln und Eval-Suite mit Paket und Ja-Prüfung

Zwei Zweige hatten parallel an Werkzeugen und Freigaben gearbeitet: der eine mit Paket, Gruppen
und Ja-Prüfung, der andere mit Fremdschutz, Zustellregeln, Eval-Suite und dem Zugzähler
(`nutzerzuege`). Beim Zusammenführen:

- Die Einstufung `LESEND`, `SCHREIBEND`, `BESTAETIGUNGSPFLICHTIG` aus werkzeuge.py steht jetzt am
  Decorator: `@werkzeug(gruppe, stufe, ..., rueckfrage=...)`; schreibende Werkzeuge brauchen die
  Rückfrage-Vorlage, sonst wirft der Decorator. Gesprächssteuerung (`bestaetigen`, `abbrechen`,
  `zuhoeren`, `bereitschaft`, `gespraech_beenden`) ist `lesend`.
- Die Ja-Prüfung ersetzt den Zugzähler: Antworten hängen an der Freigabe (`Freigabe.antworten`),
  der Rückfragewächter hält sie fest. Ein `bestaetigen` im selben Zug wie die Frage findet keine
  Antwort und wird verweigert („VERWEIGERT ohne Antwort"), wie mit dem Zähler.
- Der `Ansagewaechter` stand vor dem `Rueckfragewaechter`: „Nein, lass das" legte die
  Regel-Rückfrage an, und der Rückfragewächter verwarf sie wegen des Neins sofort (mit der alten
  Reihenfolge nachgestellt). Jetzt steht er dahinter; ein Test prüft die Reihenfolge im Aufbau.
- Neue WhatsApp-Nachrichten, die Alfred ansagt, sind fremder Text: `nachrichten_melden` reiht sie
  als `Fremd(Ansage(...), "WhatsApp von …")` ein, damit der Zusteller die Marke setzt.
- Prompt-Größe danach, gemessen wie oben: alles eingerichtet 67 Werkzeuge mit 23.875 Zeichen
  Schema, Fokus 57 mit 20.679, Feierabend 52 mit 18.560, Grundausstattung 19 mit 6.358 (18 und
  6.001 ohne `modus_wechseln`). Neu sind `bereitschaft`, `zuhoeren` und `whatsapp_melden`.

## 7.10.2026

### Eval nach dem Zusammenführen, mit GLM und Wiederholungen

Die Eval-Suite auf dem zusammengeführten Stand (0137afb), je ein Lauf:

| Modell | bestanden | Dauer | Aufrufe | Token Eingabe | Kosten |
|---|---|---|---|---|---|
| mistral-small-2603 | 13 von 18 | 35 s | 45 | 378.000 | rund 6 Cent |
| zai-glm-5-3 (Alexanders Sprecher) | 14 von 18 | 1:00 min | 48 | 382.000 | rund 56 Cent |

Am 6.10.2026 vor dem Zusammenführen waren es 11 und 16 von 18. Mistral Small scheiterte am
bekannten Muster: Es kündigte den Aufruf nur an („Timer stelle zehn Minuten für Nudeln.", „Jan
schreibt."), und beim Wetter ohne Wohnort fragte es nach dem Ort, merkte ihn aber nicht mit
`profil_merken`.

`wochentag-mittwoch` scheiterte mit beiden Modellen, weil der 7.10. selbst ein Mittwoch war:
`{naechster:Mittwoch}` zählt heute nicht mit und erwartet den 14.10., beide Modelle fragten den
heutigen Tag ab. An einem Mittwoch ist die Frage mehrdeutig; das Szenario prüft das Umrechnen
der Wochentage nur an den anderen sechs Tagen.

Ob die GLM-Fehlschläge vom Zusammenführen kommen, mit `--wiederholungen` geprüft; „vorher" ist der
Stand 539d1c5 (Eval-Zweig, ohne Werkzeug-Paket und ohne die Änderungen aus dem Raum-Gespräch):

- `erinnerung-in-minuten`: 3 von 3. Der Timer statt der Erinnerung im vollen Lauf war Zufall.
- `beilaeufig-nicht-hoeren`: vorher wie nachher 1 von 3. GLM schickt auf „Ich kann dich nicht
  hören" mit `ans_telefon` eine Push-Nachricht („Prüf mal die Lautstärke …"), einmal dazu
  `feedback_merken`. Das lag schon vor dem Zusammenführen so; ob es erwünscht ist, ist offen.
- `beilaeufig-was-geht`: vorher 3 von 3, nachher 4 von 9. Mit der Werkzeugreihenfolge von vor
  dem Umbau (Register zur Laufzeit umsortiert) 3 von 5; an der Reihenfolge der Gruppen liegt es
  also nicht erkennbar. GLM bietet an, „Termine und Wetter zu checken", oder ruft
  `termine_abfragen`, `erinnerungen_anzeigen` und `aufgaben_anzeigen` auf (alle lesend). Was das
  auslöst, ist offen. Im Prompt kamen am Abend des 6.10. unter anderem die Zeile „Zuletzt:
  vorgestern …, gestern …" und Sätze zu Kosten und WhatsApp dazu.

In der Eval-Umgebung (Attrappen, keine Schleuse) sieht das Modell jetzt 63 Werkzeuge und 5.200
Zeichen Systemprompt, vorher 62 und 4.755. Alle Läufe dieses Abschnitts zusammen kosteten rund
einen Dollar; GLM-Wiederholungen deshalb nur für die betroffenen Szenarien.

### STT-Ausfälle: Reconnect-Sturm und ErrorFrame-Flut bei Serverfehlern (7.10.2026)

Mistrals Echtzeit-Transkription (`voxtral-mini-transcribe-realtime-2602`) fiel abends mit
`EngineDeadError` und `Upstream connection error 1011` aus. In Pipecats Standard-`MistralSTTService`
führte das zu einer Kaskade: Jedes 20-ms-Audioframe rief bei getrennter Verbindung sofort
`_connect()` auf (50 Anfragen pro Sekunde). Das provozierte Mistrals `1013 try again later` und
Verbindungs-Resets. Gleichzeitig warfen `send_audio` und `flush_audio` über geschlossene Sockets
unbehandelte Fehler in die Frame-Verarbeitung (`Error processing frame`), was pro Sekunde Dutzende
ErrorFrames downstream schickte und das Log flutete.

`FinalizingMistralSTTService` fängt das jetzt ab:
- Reconnect-Drosselung mit exponentiellem Backoff (1 s bis 15 s): Während der Pause wird Audio
  verworfen, ohne Mistral mit Verbindungsanfragen zu überfluten.
- Geschütztes `send_audio` und `flush_audio`: Verbindungsabbrüche werden abgefangen, markieren die
  Verbindung als defekt und setzen den Backoff, ohne `process_frame` zum Absturz zu bringen.
- ErrorFrames und Logs werden pro Ausfall-Episode gedrosselt, statt 50 Fehler pro Sekunde zu werfen.
- `ruhen()` setzt beim Schließen der Wachwort-Schleuse den Backoff zurück, sodass beim nächsten
  Wachwort sofort wieder verbunden wird.

## 9.10.2026

### Die LED am Jabra schaltete das Mikrofon stumm

Am Jabra Speak 510 sollte der Ring rot leuchten, solange Alexander spricht, geschaltet über das
USB-HID-Interface (Telephony-Output-Report 0x03) bei Beginn und Ende der VAD. Mit der Maske 0x20
(Microphone) leuchtete nichts; mit 0x21 (Microphone und Off-Hook) leuchtete der Ring, aber Alfred
verstand nichts mehr. Im Transkript standen nur Satzanfänge („Ich habe dir", „Bist du am",
„Kannst du mich") und erfundener Text der Transkription („Entendi, já", „Yeah, yeah"); im Log
wechselten `speaking=true` und `speaking=false` im Sekundentakt. Am Abend davor, mit 0x20,
dauerten Sprechphasen bis zu sechs Sekunden.

Ursache: Das Gerät liest den Report als Anrufzustand, nicht als Leuchte. Gemessen am Mikrofon
(MME, 16 kHz) nach jedem Report; stumm heißt digitale Stille (Spitze 1, Effektivwert 0,5),
offen zeigt auch ein ruhiger Raum Spitzen ab 16:

| Report | Bits | Anzeige | Mikrofon |
|---|---|---|---|
| 0x00 | keine | aus | offen |
| 0x01 | Off-Hook | grüne Hörer-LED | offen |
| 0x21 | Off-Hook, Microphone | roter Ring | stumm, binnen 100 ms |
| 0x20 | Microphone | nichts | offen |
| 0x05 | Off-Hook, Mute | nicht angesehen | stumm |
| 0x09 | Off-Hook, Ring | nicht angesehen | stumm, in einem Lauf erst nach über einer Sekunde |
| 0x11 | Off-Hook, Hold | nicht angesehen | stumm |
| 0x41, 0x03 | Off-Hook mit Online oder Speaker | nicht angesehen | offen |

Der Report-Deskriptor (hidapi `get_report_descriptor`) nennt für Report 0x03 die Bits Off-Hook,
Speaker, Mute, Ring, Hold, Microphone, Online und Ringer (0x80, Klingelton; nicht gesendet). Der
rote Ring ist also die Stummschaltung selbst, und das Weckwort-Signal 0x09 schaltete das Mikrofon
zusätzlich direkt nach dem Wachwort stumm. Wechsel zwischen 0x00 und 0x01 zeigten in
100-ms-Fenstern keine Lücke; einzelne stille 100 ms kommen auch ohne Report vor, das ist die
Rauschunterdrückung des Jabra. 0x00 nach 0x21 gab das Mikrofon sofort frei. In einem Lauf blieb
das Mikrofon nach 0x05 und dann 0x01 noch Sekunden stumm, auch über 0x00 hinweg; das ließ sich
nicht wiederholen.

Geändert: `JabraDevice.write_report` lässt nur Off-Hook durch, und die Konfiguration kennt keine
Masken mehr. Die LED ist grün, solange die Schleuse offen ist, sonst aus; das Sprechen ändert an
der Anzeige nichts mehr. Geschaltet wird nur im Raum-Prozess (room.py), beim Start auf aus, was
auch eine hinterlassene Stummschaltung aufhebt; Selbsttest, Browser und pytest fassen das Gerät
nicht an. `python -m alfred.pruefen` öffnet das Gerät nur noch und schreibt nichts.

Fehlerbild: Transkripte aus Satzanfängen und VAD-Abschnitte unter einer Sekunde heißen, dass das
Mikrofon mitten im Satz verstummt. Den Pegel zeigt `python -m alfred.room <raum> --hoertest`.

Danach wollte Alexander sehen, was Alfred gerade tut: grün nach dem Wachwort, weiß beim Zuhören,
kreisende LEDs beim Nachdenken. Weiß und Kreisen kann das Speak 510 nicht anzeigen. Die Wahl
fiel deshalb auf die grüne Taste allein: Sie blinkt beim Nachdenken, Off-Hook im Takt an und
aus. Geprüft am Gerät:

- Mikrofon: 34 Umschaltungen im 0,3-s-Takt, während Alexander laut zählte. Kein einziges stilles
  20-ms-Stück, weder in den 100 ms nach dem Umschalten noch sonst.
- Lautsprecher: 13,7 s Testsätze über das Jabra, dabei geblinkt. Laut Alexander kein Knacken,
  kein Aussetzer, kein Piepen.
- Takt: 0,3 s war zu hektisch, 0,5 s passt (`jabra.blinken_sekunden`).

Die LED folgt seitdem der Mitschrift, die die Zustände für die App ohnehin führt. Weil `denkt`
ohne Antwort stehen bleibt (etwa ein Husten ohne Transkript), blinkt sie höchstens zehn Sekunden.

### Wachwort: Die Stimmprüfung wies Alexander bei jedem zweiten Versuch ab

Alexander: Alfred wacht nur auf, wenn die Stimme immer in derselben Tonlage bleibt, „nicht
lauter, nicht leiser, gar nix". Die Logs vom 4. bis 9.10.2026 (Profil vom 4.10., Jabra) zeigen:
Vosk hat „alfred" bei allen 32 Versuchen erkannt, die Stimmprüfung am Wachwort wies 15 davon ab
(Werte 0,20 bis 0,40 bei Schwelle 0,41, angenommen 0,42 bis 0,71). Am 9.10. um 15:27 sechs
Abweisungen in 25 Sekunden, keine Öffnung; in derselben Minute lagen ganze Sätze bei 0,61 bis
0,71. Geprüft wird das Audio ab dem Wachwort, oft nur das Wort selbst, rund eine halbe Sekunde
Sprache, seit der LED erst recht (Alexander wartet auf Grün).

Vosk selbst hört das Wort unabhängig von Lautstärke und Tonlage: Synthetische deutsche Stimmen
(Hedda, Katja, Stefan), „Alfred" allein in sieben Tonlagen und elf Sätze, je von −30 dB bis
+12 dB (übersteuert): alles erkannt bis auf eine Aufnahme „sehr tief, langsam" bei einer
Lautstärke. Englische Stimmen sprechen „Alfred" englisch aus und werden nicht erkannt.

Vergleich der Sprechermodelle mit sechs synthetischen Stimmen (Profil wie beim Anlernen: Rede
plus sechs Sätze; Alfreds eigene Stimme aus dem Phrasen-Cache als weitere fremde Stimme):

| | vosk-model-spk-0.4 | WeSpeaker ResNet34-LM (ONNX) |
|---|---|---|
| Wort allein: eigene Median, Minimum | 0,50, 0,21 | 0,61, 0,31 |
| Wort allein: fremde Median, Maximum | 0,18, 0,54 | 0,20, 0,54 |
| Wort allein: gleiche Fehlerrate | 9 % | 6 % |
| Wort allein, eigene Median bei −20 / 0 / +12 dB (übersteuert) | 0,53 / 0,65 / 0,40 | 0,59 / 0,71 / 0,55 |
| Sätze: eigene Minimum, Median | 0,55, 0,82 | 0,64, 0,87 |
| Sätze: fremde Median, Maximum | 0,33, 0,69 | 0,19, 0,54 |
| Sätze: gleiche Fehlerrate | 3 % | 0 % |
| Zeit je Vektor | 398 ms | 19 ms |

CAM++-LM aus demselben Release lag mit derselben Filterbank bei rund 50 % Fehlerrate, also
unbrauchbar in dieser Form; nicht weiter verfolgt.

Geändert: Die Stimmprüfung am Wachwort ist aus (`stimmerkennung.wachwort: false`); für das Wort
allein taugt keines der beiden Modelle. Neues Sprechermodell `wespeaker-resnet34`
(sprechermodell.py, onnxruntime, Filterbank in numpy, Download mit fester Prüfsumme nach
~/.cache/alfred) als Vorgabe; ein Profil merkt sich sein Modell und gilt nur für dieses.
Anlernen nimmt das neue Modell, trägt es mit den Schwellen ein und fragt Sätze leise, laut,
beiläufig und müde ab. Alexanders Konfiguration bleibt bei Vosk, bis neu angelernt ist. Offen:
den ersten Satz nach dem Wachwort mit dem neuen Modell prüfen (Schutz gegen „Alfred" aus dem
Fernseher), sobald Werte mit Alexanders echter Stimme vorliegen.

### Raum-Gespräch am Abend: Sätze doppelt, Tagesprotokoll leer, Video im Gespräch

Gespräch von 22:27 bis 22:39 mit dem neu angelernten Sprechermodell (WeSpeaker, sieben Proben,
eigene ab 0,46; Schwellen 0,41 und 0,36). Das Wachwort öffnete beim ersten Mal.

**Doppelte Bestätigung.** Nach einer Websuche stand die Fremdschutz-Marke, `auftrag_geben`
fragte nach, und nach dem Ja lief es in `bestaetigen`. Das Werkzeug sprach „Verstanden, ich
übernehme: …" (`_sofort_sagen`) und meldete `run_llm=False`, aber `_mit_fremdschutz` sammelte nur
das Ergebnis, nicht die Eigenschaften, und `bestaetigen` meldete es ohne: Das Modell redete
weiter. Nachgestellt in der Eval-Suite mit GLM 5.3 (neues Szenario `auftrag-nach-rueckfrage`),
ohne Korrektur: „Verstanden, ich übernehme: … Sobald ich fertig bin, schicke ich dir eine
Nachricht aufs Handy, den Bericht findest du in der App. Alles klar, ich lege los. Den fertigen
Bericht findest du dann in der App, und ich schicke dir eine Nachricht aufs Handy, sobald er
steht." Beim Freigeben vom Handy genauso: Das Werkzeug sprach, und ein Hinweis ließ das Modell
es noch einmal sagen. Geändert: Nach einer Rückfrage liefert ein Werkzeug mit `run_llm=False`
ein `Gesprochen`-Ergebnis; `bestaetigen` gibt `run_llm=False` weiter, `freigaben_beobachten` legt
keinen Hinweis ab. Mit Korrektur bestanden `auftrag-nach-rueckfrage` und `auftrag-tech-news` je
2 von 2 Läufen; alle Läufe zusammen rund 26 Cent.

Ausgeschlossen als Ursache: Die neuen Versuche der Sprachausgabe (viermal „kein Ton nach 2 s")
brechen den hängenden Versuch ab, bevor der nächste startet, und Pipecat liest `run_tts`
unmittelbar in den Audio-Kontext. Pipecat schiebt die nächste Antwort nach einem Werkzeug auf,
solange Alfred spricht. Text, den das Modell neben einen Werkzeugaufruf schreibt, liest Pipecat
aber vor; die Eval-Suite zeigt ihn jetzt als „Vorab" und wertet ihn bei `antwort_vom_werkzeug`
als Mangel. GLM schrieb in diesen Läufen keinen.

**Tagesprotokoll leer seit dem 7.10.** Am 6.10. scheiterte es an „'list' object has no
attribute 'strip'" (Thinking-Chunks, am 7.10. behoben), danach stand keine Zeile mehr im Log. Die
Zusammenfassung lief mit dem Sprecher und `max_tokens=200`: GLM 5.3 dachte die 200 Token voll
und lieferte keinen Text (`finish_reason` length, nur ein Thinking-Stück); mit 1.000 Token brach
der Text mitten im Satz ab. Jetzt fasst `modelle.werkzeuge` (mistral-small-2603) zusammen wie bis
zum 6.10.; das Gespräch vom Abend ergab drei Sätze. Hört ein Modell vor dem Text auf, steht das
als Warnung im Log. Die Zusammenfassung übernahm allerdings Alfreds Behauptung, die Seite sei nur
Englisch, obwohl Alexander widersprochen hatte; falsche Fakten weiter von Hand entfernen.

**Video im Gespräch (offen).** Das neue Modell trennt im Raum sauber: Alexanders Sätze 0,38 bis
0,75, englische Stimmen aus einem Video −0,10 bis 0,17 (mit Vosk am 6.10. noch 0,36 bis 0,48).
Von 22:35:40 bis 22:36:58 überhörte der Stimmfilter 23 Sätze. Ab 22:37:01 kamen sie durch, und
Alfred antwortete dem Video („Klingt stark nach Slay the Spire …"). Wahrscheinlich zählte ein
kurzer Satz aus dem Video (höchstens drei Wörter, unter einer Sekunde, also ohne Stimmwert) als
Alexanders (`angenommen`), das öffnete die freie Zeit, und jedes weitere Sprechen des Videos
hielt sie offen (`_track_activity`). Belegen lässt es sich nicht, weil angenommene Sätze nicht im
Log stehen. Bereitschaft verhindert es; Alexander schaltete sie danach ein. Einen automatischen
Standby nach mehreren fremden Stimmen in Folge lehnte Alexander ab (9.10.2026): Er fürchtet
viele inkonsistente Fehler. Beim Fernsehen bleibt es bei „Alfred, Standby".

Sonst: GLM brauchte für drei längere Antworten 7,7 s, 14,8 s und 13,5 s (Antwortzeiten bis
19 s, eine davon mit fünf Sekunden Zuhör-Pause).

## 10.10.2026

### Neue Oberfläche der App

Anlass: Alexander fand die App „totalen Müll" und wollte ein Design wie ein fortgeschrittenes
Agenten-Werkzeug, in dem man sieht, was Alfred alles kann. Die alte App waren sieben schlichte Reiter
mit Listen; Zustand, Fähigkeiten, Modi und Werkzeugaufrufe waren nirgends zu sehen. Jetzt gibt es
Zentrale, Chat, Heute, Aufgaben, Aufträge, Einkauf, Ernährung, Haushalt, Fähigkeiten, Gedächtnis und
System (Aufbau: docs/architektur.md, „App").

**Entscheidungen.** Dunkel ist der Standard, Hell und „System" sind wählbar; die Schrift ist die des
Systems (kein Download, die App braucht kein Netz für Schriften), die Symbole stehen als Sprite in
`index.html`. Die Fähigkeiten kommen aus dem Werkzeug-Register, nicht aus einer gepflegten Liste: Ohne
jede Anbindung zählt die Übersicht 67 Werkzeuge, davon 44 bereit, 3 nur per Stimme (Schleuse,
Zuhören) und 20 nicht eingerichtet, und nennt je Werkzeug, was fehlt (Google Kalender, WhatsApp-Brücke,
E-Mail-Zugang, Push, Spotify). Von Hand stehen nur Anzeigetexte in `werkzeuge/uebersicht.py`, weil die
Beschreibungen für das Modell an das Modell gerichtet sind („Merkt dir …", „Nutze es, wenn …") und
in der Anzeige falsch klängen; ein Test bricht, wenn ein Werkzeug oder eine Gruppe dort fehlt. Im
Chat steht jeder Werkzeugaufruf als Karte vor der Antwort (Gruppe, Schutzstufe, Argumente, Ergebnis),
und eine offene Rückfrage bietet Ja und Nein an; das Ja geht als normaler Satz durch dieselbe
Ja-Prüfung wie getippt.

**Befunde beim Bauen.**
- Die alte Heute-Ansicht zeigte Termine als Sprechtext („neun Uhr dreißig bis neun Uhr
  fünfundvierzig"), weil `/api/heute` nur `Termin.ansage()` lieferte, gemacht für die Sprachausgabe.
  Jetzt liefert es zusätzlich `kalender` mit Zahlen; ebenso `timer` und `wiederholung` je Erinnerung.
- Statische Dateien gingen ohne `Cache-Control` hinaus. Chromium hielt `app.js` nach einer Änderung
  weiter für frisch (Faustregel nach dem Zeitstempel) und lieferte neue Seite mit altem JavaScript. Der
  Service Worker verdeckte das nur, solange er die Seite steuerte; beim ersten Besuch und in Browsern
  ohne ihn nicht. Seitdem `Cache-Control: no-cache` mit ETag (`AktuelleDateien` in web.py).
- Ein fixiertes Fenster in einer Ansicht (der Berichtleser am Handy) war 1.046 statt 812 Pixel hoch:
  Die Eingangsanimation der Ansicht (`transform`, `animation-fill-mode: both`) machte sie zum
  Bezugsrahmen für fixierte Kinder. Die Animation ändert deshalb nur noch die Deckkraft, kein `transform`.
- Die alte Wochenplan-Ansicht setzte Gerichte und Zutaten per `innerHTML` ein, also Text aus dem
  Sprachmodell als HTML. Die neue Oberfläche baut alles über `h()` und `textContent`; nur `markdown()`
  erzeugt HTML und maskiert vorher.
- iOS zoomt in Eingabefelder mit weniger als 16 px; am Handy gilt deshalb 16 px. Ein nativer
  Datumsfeld-Platzhalter („tt.mm.jjjj") sah im Eingabefeld unfertig aus; das Feld liegt jetzt
  unsichtbar über einem Knopf, der das Datum lesbar zeigt.

**Befunde der Durchsicht.** Ein Prüfdurchgang über alle Ansichten fand noch Folgendes, jetzt behoben:
- Die Zentrale baute bei jedem Takt (der Live-Zustand kommt alle drei Sekunden) alle Karten neu auf:
  Der Tastaturfokus sprang weg, ein Klick zwischen zwei Neuzeichnungen ging verloren. Jetzt zeichnet
  jede Karte aus ihrer eigenen Anfrage und neu nur, wenn sich ihre Daten ändern (und einmal je
  Minute wegen der „vor …“-Zeiten). Gemessen: neun Sekunden lang keine einzige Neuzeichnung der Karten,
  nur die laufenden Zeiten ändern ihren Text.
- Zwei Anfragen derselben Ansicht konnten sich überholen (Abhaken und Neuladen im Takt) und eine alte
  Antwort die neuere überschreiben; seitdem zählt je Ansicht nur die jüngste (`juengste()`).
- Fiel `/api/heute` aus, blieb die Wetterkarte ein Platzhalter; ganztägige Termine von morgen hießen
  nur „ganztägig“. „Planen“ ersetzte einen vorhandenen Wochenplan ohne Rückfrage; jetzt fragt die App.
- Am Handy im Querformat lagen Inhalte unter der Kamera-Aussparung (`viewport-fit=cover` ohne
  seitliche Sicherheitsränder), und der Service Worker lieferte bei einem 502 des Proxys (während
  Alfred neu startet) die Fehlerseite statt der Kopie.
- Im Backend: Die Beschreibung auf der Freigabeseite kam ungemaskiert als HTML aus den Argumenten des
  Werkzeugs (Modell, mitunter fremder Text). `aktionen.log` ist tabgetrennt, und ein Tabulator oder
  Zeilenumbruch in einem Argument konnte Felder und ganze Zeilen im Protokoll erfinden, das der Reiter
  „System“ als Beleg zeigt. Ein unbrauchbares JSON des Modells beim Foto oder beim Wochenplan wurde
  zu einem 500 (beim Foto als 400, weil `JSONDecodeError` ein `ValueError` ist), und ein Wert wie
  „ca. 30 g“ trug nichts ein und brach mit 500 ab; ein Plan mit einer Zahl oder einem leeren Eintrag
  als Zutat verhinderte die Einkaufsliste. Jetzt antworten die Routen mit 502 und Klartext, und
  die Zutaten überspringen Fehlformen.

**Offen.** Der Modus in der App kommt aus der Konfiguration: Wechselt Alfred ihn per Stimme (nur im
Sprach-Prozess), zeigt die App weiter den Startmodus; die Fähigkeiten-Ansicht zeigt deshalb alle Modi
als Vorschau. Aufrufe aus Ereignisrunden (Denker fertig) stehen im `POST /api/chat`, aber nicht im
späteren `GET`, weil `Textgespraech.antworten` sie nicht im Verlauf ablegt. Das Wetter zerlegt die App
aus dem gesprochenen Satz von `wetter.zusammenfassung`; ändert sich dessen Satzbau, zeigt die Karte den
Rohtext. Beim Bau fielen drei Dinge im Backend auf, die nicht zur Oberfläche gehören: Der
`Kostenzaehler` liest `kosten.json` nur beim Start, „Kosten heute“ in der App zählt deshalb nie mit,
was der Sprach-Prozess danach verbraucht, und ein Schreiben der App kann dessen Zähler überschreiben
(als eigener Auftrag vorgemerkt). `POST /api/foto` trägt bei einer Nährwerttabelle auch dann eine
Mahlzeit ein, wenn nichts erkannt wurde (0 kcal); die App warnt davor, der Server verhindert es nicht.
`POST /api/vorrat` legt, anders als das Sprach-Werkzeug, keine Erinnerung für den Vortag des
Ablaufs an; die App verspricht sie deshalb nicht.

**Prüfung.** Gegen die echten App-Routen mit einem Demo-Server mit erfundenen Daten (die echte
Konfiguration hätte die WhatsApp-Brücke gestartet), bei 1.240 und 375 Pixel Breite, Dunkel und Hell;
`tests/test_uebersicht.py`, `tests/test_web_static.py` und die erweiterten Tests in
`tests/test_web.py`; zuletzt 798 Tests grün, pyflakes ohne Befund, alle Module mit `node --check`.

## 16.10.2026: Mini-PC absichern, Zugriff vom Handy, Umzug vorbereitet

Bestandsaufnahme am Mini-PC ohne sudo (nur lesend), dazu Verbindungsversuche vom Windows-PC
im Heimnetz. Alfred lief an diesem Tag auf dem Windows-PC; auf dem Mini-PC lagen noch Code und
Gedächtnis vom 5.10., ohne Dienst.

**Stand.** Ubuntu 24.04.4, 94 Tage ohne Neustart: Es lief Kernel 6.8.0-134, installiert waren
136 bis 139 und eine neue libc (`/var/run/reboot-required`). unattended-upgrades installierte
die Sicherheitsupdates täglich, startete aber nie neu (`Automatic-Reboot` nicht gesetzt). SSH
bot nur `publickey` an (`ssh -v -o PubkeyAuthentication=no`: „Authentications that can
continue: publickey"), root durfte nicht. Im Journal stand kein einziger fremder
Anmeldeversuch. Das ufw-Log vom 4. bis 16.10. enthielt nur Broadcast und Multicast aus dem
Heimnetz, nichts von öffentlichen Adressen. ufw war an, mit Standard DROP; die Regeln selbst
liest nur root.

**Docker umgeht ufw, aber nur bei IPv4.** Vom Windows-PC aus über IPv4 offen: 22, 53, 80, 443,
8443 und 25565. Über IPv6 (ULA wie öffentlich) waren 22, 80, 443 und 25565 offen, 53 und 8443
gefiltert. Docker leitet IPv4 per NAT an den Container weiter, bevor die INPUT-Kette (und damit
ufw) das Paket sieht. Die Netze der Container haben kein IPv6, deshalb nimmt `docker-proxy`
IPv6-Verbindungen als gewöhnlicher Prozess an, und die laufen durch ufw. Folgerungen: Für
Docker-Ports entscheidet bei IPv4 allein der Router, ufw schützt sie nur über IPv6; eigene
Container nur an 127.0.0.1 veröffentlichen, wie SearXNG (`-p 127.0.0.1:8888:8080`). Pi-hole
läuft mit `FTLCONF_dns_listeningMode=ALL` und beantwortet jede Anfrage, die Port 53 erreicht.
Port 53 darf deshalb nie im Router freigegeben sein. Seine Oberfläche verlangt ein Passwort
(`/api/auth` ohne Sitzung: `valid: false`).

**Was geändert wurde.**

- `deploy/absichern.sh` (neu) erledigt die Firewall:
  - ufw lässt SSH nur aus dem Heimnetz (IPv4-Netz und ULA an der Standardschnittstelle) und alles
    über `tailscale0` herein, dazu `--heimnetz`- und `--offen`-Ports.
  - Breite Regeln für SSH und Heimnetz-Ports entfernt das Skript nach Rückfrage. Die Auswertung
    steht in `alfred.absichern` und ist getestet.
  - Ist die eigene SSH-Sitzung von außerhalb, bleiben die SSH-Regeln, damit sich niemand aussperrt.
- Ebenfalls neu in `absichern.sh`: Neustart nach Sicherheitsupdates um 4 Uhr (`52absichern`).
  Dazu kommt eine sshd-Datei `10-absichern.conf`, die vor `50-cloud-init.conf` gelesen wird.
- `tailscale serve ... >/dev/null` in `einrichten.sh` hätte beim ersten Mal den Link verschluckt,
  mit dem man HTTPS für das Tailnet einschaltet. Die Ausgabe bleibt jetzt sichtbar.
- Tailscale kommt aus einer fremden Paketquelle, die unattended-upgrades nicht kennt. Deshalb
  setzt `einrichten.sh` jetzt `tailscale set --auto-update`.
- Ein ablaufender Tailscale-Schlüssel hätte den Server nach 180 Tagen still aus dem Tailnet
  genommen. Er steht jetzt in der Startprüfung und in der Liste am Ende von `einrichten.sh`.
- Das Repository ist privat, und der Server hat keinen Deploy-Schlüssel bei GitHub. `git pull`
  geht dort also nicht, der Code kommt per `git push minipc`. `aktualisieren.sh` überspringt
  den Pull deshalb, wenn der Zweig keinen Upstream hat, statt mit `set -e` abzubrechen.
- Umzug: `einrichten.sh --sicherung` mit einer anderen als der eingespielten Sicherung ersetzt
  nach Rückfrage den Stand. Der bisherige wandert nach `~/alfred-vor-umzug-<Zeit>`. Bisher
  übersprang das Skript jede weitere Sicherung, und `--einspielen` ohne `--ueberschreiben`
  hätte den alten Stand vom 5.10. behalten.
- Die App funktioniert unter `/app/` hinter `tailscale serve`, weil alle Pfade relativ sind
  (`fetch("api/" + pfad)`, `sw.js`, Manifest `./`). Geöffnet werden muss sie mit Schrägstrich
  am Ende.

Alexanders Entscheidungen (16.10.2026): Alfred zieht auf den Mini-PC. Minecraft (Crafty, 25565
und 25566) und Hytale (5520/udp) sollen von außen erreichbar bleiben. Ein Neustart um 4 Uhr ist
in Ordnung, wenn ein Update ihn braucht. Die Router-Freigaben sind noch nicht geprüft; die
Oberfläche des Routers liest nur Alexander.

**Nach dem Neustart (16.10.2026, 12:11).** Alles kam von selbst wieder hoch, mit Kernel
6.8.0-147, dem neuesten installierten: Raum, App, Brücke, Tailscale, SearXNG, Crafty und Hytale.
SSH bot nur `publickey` an. Über die öffentliche IPv6-Adresse war Port 22 gefiltert, 25565
offen. Zwei Fehler kamen dazu:

- Pi-hole startete nicht: „failed to bind host port 0.0.0.0:443/tcp: address already in use".
  - Ursache: Für `tailscale serve` hält tailscaled Port 443 auf seinen eigenen Adressen
    (`100.64.0.10%tailscale0:443`, `[fd7a:…]%tailscale0:443`). Bis zum Neustart hielt
    docker-proxy seit Juli 0.0.0.0:443. Nach dem Neustart war tailscaled zuerst da, und Docker
    startet einen Container gar nicht, wenn eine seiner Portbindungen scheitert.
  - Behebung: Pi-hole veröffentlicht 443 nur noch auf der Heimnetz-Adresse
    (`"192.168.1.99:443:443/tcp"`). Zwei verschiedene Adressen vertragen sich auf demselben Port.
  - `einrichten.sh` warnt jetzt vor Diensten und Containern, auch angehaltenen, die 443 auf allen
    Adressen wollen (`alfred.absichern docker-belegt`).
- Die WhatsApp-Brücke fand `web.whatsapp.com` nicht:
  - Ursache: `tailscale up` hatte mit dem Standard `--accept-dns` die Datei `/etc/resolv.conf` auf
    100.100.100.100 umgestellt, und beim Booten stand das noch nicht. `connect()` warf, der Faden
    endete, und die Brücke lief ohne WhatsApp weiter.
  - Behebung: `tailscale set --accept-dns=false`, damit fragt der Server über systemd-resolved
    wieder den Router. `einrichten.sh` setzt das jetzt selbst, und die Brücke versucht es nach
    einem gescheiterten Verbindungsaufbau erneut.

**Journal voller Vosk-Warnungen.** Im Raum-Dienst standen in 40 Minuten 6.360 Zeilen „WARNING
(VoskAPI:OutputArcForce()) Discarding word-ids at the end of a sentence, that don't have
alignments". Das waren 2,6 je Sekunde, und echte Fehler gingen darin unter. `SetLogLevel(-1)`
blendet bei Kaldi nur Infos aus, denn eine Meldung erscheint, solange ihre Stufe (Warnung −1,
Fehler −2) nicht über dem eingestellten Wert liegt. `wakeword.py` und `stimme.py` setzen jetzt −2,
damit bleiben nur Fehler.

**Pi-hole neu anlegen mit Compose 1.** Auf dem Mini-PC gibt es nur `docker-compose` 1.29.2, kein
`docker compose`. Alle drei Container wurden damit angelegt. Compose 1 scheitert mit Docker
ab 25 beim Neuanlegen eines bestehenden Containers („KeyError: 'ContainerConfig'"). Deshalb
kam der gestoppte Pi-hole-Container zuerst weg, denn seine Daten liegen in Bind-Mounts unter
`~/srv/pihole`. Danach hat `docker-compose up -d` ihn frisch angelegt, und er lief mit
`192.168.1.99:443`. Nachgemessen vom Windows-PC: Über die öffentliche IPv6-Adresse sind
22, 80 und 443 gefiltert, offen ist nur 25565. Über die ULA sind 22 und 80 offen und 443 zu,
über IPv4 ist alles wie vorher. Die Minecraft-Server in Crafty haben `auto_start = 0` und
waren nach dem Neustart aus. Mit dem nächtlichen Neustart nach Updates muss Auto-Start in
Crafty an sein, sonst ist Minecraft danach weg.

## 16.10.2026

### Alfred sprach ins Leere: Ausgabe am Monitor statt am Jabra

Alexander startete den Raum rund eine Minute nach dem Hochfahren (Windows ab 08:19:13, Raum
08:20:15). Der erste Anlauf scheiterte beim Öffnen des Mikrofons mit `[Errno -9999] Unanticipated
host error`, die Aufsicht startete nach 10 s neu. Danach lief laut Log alles: Wachwort, Transkript,
Antwort („Ja, ich hör dich deutlich …"), Sprachausgabe 0,6 s, Ausgabe-Transport 0,005 s, kein
Fehler. Zu hören war nichts.

Ursache: Windows führte Alfreds Wiedergabe beim Monitor-Audio (DELL S2721HGF über NVIDIA), beim
Jabra gab es keine Sitzung des Prozesses; die Aufnahme lag richtig beim Jabra (gelesen über die
Audiositzungen von Windows, `IAudioSessionManager2` je Gerät). PortAudio liest die Geräte nur beim
Initialisieren ein und öffnet ein MME-Gerät später über die Nummer, die Windows ihm damals gab. Eine
weitere PortAudio-Instanz liest nicht neu ein, solange eine andere lebt (gemessen: erste Instanz
98 ms, zweite 0,0 ms, nach Freigabe beider wieder 43 ms), und Pipecats `LocalAudioTransport` gibt
seine nie frei. Der Neustart nahm deshalb die Liste des ersten Anlaufs von 08:20:20, als nach dem
Hochfahren noch Geräte hinzukamen; dieselbe Nummer zeigte beim Öffnen auf den Monitor. Frisch
eingelesen stand das Jabra danach wieder unter Index 5 wie in allen Logs seit dem 4.10.

Geändert: `_einmal_laufen` gibt PortAudio nach jedem Anlauf frei, auch wenn der Aufbau scheitert;
jeder Neustart liest die Geräte frisch ein. Direkt vor dem Start vergleicht `_geraete_pruefen`
PortAudios MME-Liste mit der Nummerierung, die Windows jetzt hat (`geraeteliste_veraltet`, winmm über
ctypes). Weicht sie ab, bricht der Anlauf ab und die Aufsicht liest neu ein, nach drei Abbrüchen in
Folge startet Alfred trotzdem. Verglichen wird ohne den Soundmapper, den Windows nicht mitzählt, nur
Buchstaben und Ziffern und bis zur Länge des kürzeren Namens. Den nachgestellten Fall (Monitor vor
dem Jabra eingereiht) erkennt die Prüfung, die frisch eingelesene Liste passt.

Fehlerbild: Mitschrift und Log zeigen Antwort und Antwortzeiten, aber es kommt kein Ton. Abhilfe
ohne Korrektur: den Raum neu starten, sobald die Geräte stehen.

Offen: Ereignisse, die in einen Anlauf fallen, der danach scheitert, gehen verloren. Um 08:20
galten drei überfällige Erinnerungen als ausgeliefert (aufs Telefon kamen sie per Push) und das
Morgenbriefing als angesagt, obwohl kein Ton herauskam. Behoben, siehe unten „Ereignisse aus einem
gescheiterten Anlauf“.

### Sprachnachrichten in der App

Anlass: Alexander fragte, ob man in der App auch sprechen kann, „oder zumindest audios einsprechen,
welche er sich dann anhören kann und verarbeiten kann". Bis dahin ging dort nur Schreiben; Sprechen ging nur im
Raum oder über `bot.py`. Jetzt nimmt der Chat Sprachnachrichten auf (Aufbau: docs/architektur.md,
„App").

**Entscheidungen.** Eine Sprachnachricht statt eines Live-Gesprächs, weil sie den Chat nicht umbaut:
Die Abschrift läuft durch `Textgespraech.antworten` wie getippter Text, mit denselben Werkzeugkarten,
Rückfragen und Ja-Prüfungen. Gesendet wird nur auf Knopfdruck, und das Mikrofon schließt, sobald die
Aufnahme endet oder man den Chat verlässt; das Audio wird nicht gespeichert. Abgehört wird mit
`voxtral-mini-latest`, dem Modell für fertige Dateien, nicht mit dem Echtzeitmodell des Raums. Die
Umwandlung in WAV macht der Browser (`OfflineAudioContext` dekodiert und rechnet auf 16 kHz um), weil
`MediaRecorder` je nach Browser WebM, Ogg oder MP4 liefert und der Server sonst ffmpeg bräuchte. Die
Sekunden zählt `Kostenzaehler.transkription` mit dem Minutenpreis aus `preise.transkription_pro_minute`.

**Messung.** Eine Testaufnahme mit „Microsoft Hedda" (16 kHz mono, 6,8 s) kam von
`voxtral-mini-latest` nach 0,7 s zurück: „Setz bitte Milch und zwei Brot auf die Einkaufsliste und
erinnere mich morgen um 8 an den Zahnarzt." Die Sprache erkannte das Modell selbst; Zahlen kommen als
Ziffern. Im Browser lief der ganze Weg mit einem künstlichen Mikrofon (Ton statt Stimme) gegen den
Demo-Server: Aufnahme, Pegel, Anhören, Verwerfen, Umwandlung (8,7 s kamen als 16-kHz-WAV an), Abschrift,
Werkzeugkarte, nach dem Neuladen die Sprachnachricht im Verlauf, Stille-Hinweis, Anhalten beim
Verlassen des Chats, Start aus der Zentrale und der Befehlspalette, am Desktop und bei 375 Pixel Breite.

**Befunde beim Bauen.** Der Hinweis auf eine stumme Aufnahme stand zuerst als Meldung unten rechts
und verdeckte genau den Senden-Knopf. Er steht jetzt in der Aufnahme-Leiste, und Meldungen rücken im
Chat über das Eingabefeld. Safari lässt einen Audio-Kontext angehalten, der nicht im Klick entsteht;
ein angehaltener Kontext misst Nullen und hätte jede Aufnahme für stumm erklärt. Der Kontext entsteht
deshalb vor dem ersten `await`, und als stumm gilt eine Aufnahme nur, wenn mindestens 30 Messungen
mit laufendem Kontext vorliegen.

**Offen.** Am iPhone ist es nicht ausprobiert: Das Mikrofon gibt Safari nur über HTTPS frei
(Tailscale), und als Web-App vom Home-Bildschirm fragt iOS je nach Version bei jedem Start neu nach
der Erlaubnis. Solange `Kostenzaehler` nicht über Prozessgrenzen abgleicht (eigener Auftrag), kann ein
Schreiben der App die Zahlen des Sprach-Prozesses überschreiben; mit Sprachnachrichten schreibt die
App öfter.

### Ereignisse aus einem gescheiterten Anlauf

Ablauf um 08:20 (logs/raum-buero.log): `build_voice_loop` startet die Hintergrundaufgaben am Ende
des Aufbaus, also bevor die Pipeline läuft. Von 08:20:23 bis 08:20:25 legten sie drei überfällige
Erinnerungen und das Morgenbriefing in die Warteschlange und schrieben dabei schon `ausgeliefert`
(erinnerungen.json) und `zuletzt` (routinen.yaml). Um 08:20:24 scheiterte das Mikrofon beim
Einrichten des Eingangs, der Worker brach ab (`ProcessorUnusablePolicy.CANCEL`). Um 08:20:25 nahm
der Zusteller trotzdem alle vier heraus: Kontextnachricht und LLMRunFrame landeten in der
Warteschlange des schon abgebrochenen Workers, das Briefing zählte als ungefragte Ansage in
proaktiv.json. `Ressourcen.ereignisse` entstand je Anlauf neu, der Neustart um 08:20:35 hatte
nichts mehr davon.

Befund zu Pipecat (nachgestellt mit einem Processor, dessen `setup` OSError -9999 wirft): Scheitert
ein Processor beim Einrichten, kommt der StartFrame trotzdem durch die ganze Pipeline,
`on_pipeline_started` feuert, und erst danach bricht der Worker ab. Das Startsignal allein sagt
also nicht, dass die Geräte offen sind; der gescheiterte Processor steht zu dem Zeitpunkt aber schon
auf `is_usable == False`.

Abgewogen:
- Hintergrundaufgaben erst nach dem Start: hilft nur beim Aufbau. Was zurückgehalten wird
  (Ruhezeit) oder gerade unterwegs ist, wenn später ein Gerät ausfällt, ginge weiter verloren,
  ebenso ein Ergebnis des Denkers, das nach dem Abbruch fertig wird.
- Status erst nach dem Aussprechen setzen: deckt auch einen Neustart des Prozesses, braucht aber in
  jedem Erzeuger (Erinnerungen, Routinen, Aufträge, WhatsApp, Denker, Freigaben vom Handy) einen
  Zustand „unterwegs", und der Push einer Erinnerung käme beim nächsten Anlauf ein zweites Mal.
- Warteschlange über den Neuaufbau halten: eine Stelle für alle Erzeuger, kein doppelter Push.
  Gewählt, zusammen mit einer Quittung, damit nichts im sterbenden Worker als erledigt gilt.

Geändert:
- `run_room` hält die Warteschlange in `stand` über den Neuaufbau, `build_voice_loop` nimmt sie mit
  `ereignisse=`.
- Der Zusteller beginnt erst, wenn `pipeline_bereit` (pipeline.py) die Pipeline als laufend meldet:
  `on_pipeline_started` und Mikrofon und Lautsprecher brauchbar. Vorher bleibt alles in der
  Warteschlange.
- Zugestellt ist ein Ereignis erst, wenn Alfred danach hörbar zu sprechen beginnt
  (BotStartedSpeakingFrame, `Gespraechszustand.sprechbeginne`), spätestens nach `QUITTUNG_SECS`
  (60 s; danach gilt es wie bisher als zugestellt, damit nichts doppelt kommt). Erst dann zählt
  Ungefragtes für die Tagesgrenze und erfährt der `Ansagewaechter` davon. Endet der Lauf vorher,
  legt `Sitzung.abschliessen` über `Zusteller.zurueckgeben` es mit allem Zurückgehaltenen vorn in
  die Warteschlange zurück.
- `_sprachschleife_laufen` schließt die Sitzung auch, wenn nach `build_voice_loop` noch etwas
  scheitert, etwa `_geraete_pruefen`, die kurz nach dem Hochfahren bis zu dreimal abbricht. Sonst
  liefen Erinnerungen, Routinen und WhatsApp des alten Anlaufs neben denen des neuen weiter, jetzt
  in dieselbe Warteschlange.
- Nebenwirkung: Fällige Ereignisse gehen nacheinander hinaus, das nächste erst nach dem Ton des
  vorigen und 1,5 s Ruhe. Vorher landeten mehrere in derselben Sekunde im Kontext (7.10.,
  Erinnerung und Morgenbriefing um 08:00:01), jedes mit eigenem LLMRunFrame. Bis zum ersten Ton
  brauchte das Morgenbriefing mit Wetter und Terminen rund fünf Sekunden (5. und 9.10.).

Geprüft mit Tests ohne Mikrofon: der Ablauf von 08:20 mit Abbruch vor dem Ton und zweitem Anlauf
auf derselben Warteschlange, Zurückgehaltenes über den Neuaufbau, die Frist ohne Ton, ein echter
Pipecat-Worker mit scheiterndem und mit funktionierendem Mikrofon, die Aufsicht und der Abbruch
nach `build_voice_loop` (tests/test_ereignisse.py, tests/test_pipeline.py, tests/test_room.py).

Offen: Ein Neustart des Prozesses (Strg+C, Absturz) verliert die Warteschlange weiterhin, und
Erinnerungen und Routinen darin gelten dann als erledigt; betroffen wäre vor allem Zurückgehaltenes
wie ein Briefing in der Ruhezeit. Abhilfe wäre, die Warteschlange beim Beenden in eine Datei unter
gedaechtnis/ zu schreiben. Wartet ein Ereignis über mehrere gescheiterte Anläufe (Gerät länger weg),
kommt es entsprechend spät, und sein Text sagt womöglich noch „jetzt fällig"; nur Routinen
verfallen nach `gueltig_bis`. Die Ereignisse von 08:20 holt die Änderung nicht nach:
erinnerungen.json, routinen.yaml und proaktiv.json stehen noch so, wie der gescheiterte Anlauf sie
hinterließ.

### Lautsprecher fiel im Betrieb aus, Alfred blieb stumm (Log vom 5.10.2026)

Im Log `raum-buero.2026-10-05_08-00-23_993502.log` scheiterte um 08:00:28, mitten im
Morgenbriefing, das Schreiben auf den Lautsprecher (Jabra, MME) mit `[Errno -9999] Unanticipated
host error`, danach 407-mal mit `[Errno -9988] Stream closed`: einmal je 40-ms-Stück der Antwort,
alles binnen fünf Sekunden. Danach blieb das Log ruhig, weil nichts mehr geschrieben wurde. Die
Pipeline lief weiter (um 08:02:14 legte der Kalender noch eine Erinnerung an), Alfred war stumm, bis
Alexander um 08:13:10 Strg+C drückte; beim Beenden kam noch „Error cleaning up
LocalAudioOutputTransport#0: Stream not open". Die Aufsicht griff nie: Pipecats
`MediaSender._audio_task_handler` loggt einen Schreibfehler und nimmt das nächste Stück, nichts
meldet den Ausgang als unbrauchbar. Ein Fehler beim Öffnen dagegen (16.10., oben) macht ihn
unbrauchbar, und mit `ProcessorUnusablePolicy.CANCEL` startete die Aufsicht nach 10 s neu.

PyAudio 0.2.14 schließt einen Stream nach jedem gescheiterten `write` selbst. Gemessen unter Windows:
Ein `write` auf einen angehaltenen Stream scheitert mit -9983 „Stream is stopped", ohne dass das Gerät
etwas bekommt; danach scheitert jedes weitere mit -9988 „Stream closed", `is_active()` und
`stop_stream()` mit „Stream not open", nur `close()` geht. Ein toter Ausgang wird also nie wieder
lebendig, und jedes weitere Stück scheitert sofort. Für einen toten Eingang gibt es keinen Fehler,
den Pipecat sehen könnte: Er bekommt Audio nur über PortAudios Rückruf, und der bleibt dann aus oder
PortAudio hält den Stream an (erwartet, am Gerät noch nicht beobachtet). Alfred wäre taub, bis er
wieder spricht.

Geändert: `local_audio.py` mit `WatchedLocalAudioTransport`, im Raum statt Pipecats
`LocalAudioTransport` (Aufbau: docs/architektur.md, „Audio-Geräte"). Der Ausgang prüft nach einem
Schreibfehler, ob der Stream noch läuft; ist er zu, meldet er sich einmal als unbrauchbar und
schreibt nichts mehr. Der Eingang hat einen Wächter im Sekundentakt, der sich meldet, wenn PortAudio
den Stream anhält oder fünf Sekunden kein Rückruf kam. Scheitert schon `start_stream`, nimmt Pipecat
das als vorübergehenden Fehler („Something went wrong") und läuft ohne den Stream weiter; auch das
melden beide jetzt als unbrauchbar. Der Worker bricht ab, die Aufsicht startet neu
(5 s, wenn der Anlauf über eine Minute lief) und liest die Geräte frisch ein. Im Log steht statt 408
Fehlern:

```
ERROR   | WatchedLocalAudioOutputTransport#0 exception (…\pyaudio\__init__.py:550): Lautsprecher ausgefallen: [Errno -9999] Unanticipated host error
ERROR   | PipelineWorker#0: WatchedLocalAudioOutputTransport#0 can no longer do its job
WARNING | Raum 'buero' startet in 5 s neu
```

Beim Mikrofon heißt es „Mikrofon ausgefallen: seit 5 s kein Audio" oder „… PortAudio hat den
Stream angehalten".

Geprüft: `tests/test_local_audio.py` mit nachgebauten Streams (der Ausgang fällt aus wie am 5.10., der
Eingang ruft aus einem eigenen Thread alle 20 ms zurück und verstummt dann) und einem Worker wie im
Raum. Mit Pipecats Originalen statt der neuen Klassen zeigten dieselben Tests das alte Bild: einmal
-9999, danach „Stream closed" je Stück, und der Lauf endete nie. Mit echtem PyAudio auf dem
Standard-Ausgabegerät (nur Stille, Stream im Schreib-Thread angehalten): eine Meldung „Lautsprecher
ausgefallen: [Errno -9983] Stream is stopped", Abbruch sofort, kein Fehler beim Aufräumen. Ein Test
hält fest, dass PyAudio den Stream nach einem Fehler schließt, einer, dass der Raum-Modus `CANCEL`
setzt.

Offen: Das echte Abziehen des Jabra im laufenden Betrieb steht noch aus. Ist das Gerät beim Neustart
noch nicht wieder da, versucht es die Aufsicht mit wachsender Pause weiter (`DeviceNotFoundError`).
Stirbt der Lautsprecher mitten in einer Ansage, gilt sie trotzdem als zugestellt: Die Quittung aus
dem Abschnitt davor zählt BotStartedSpeakingFrames, und Pipecat schickt das erste vor dem ersten
Schreibversuch. Nur was noch in der Warteschlange wartet, holt der nächste Anlauf nach. Ein
Schreibversuch, der hängt statt zu scheitern, fällt unter Pipecats eigene Frist
(`audio_out_write_timeout_secs`, 10 s), die den Ausgang ebenfalls als unbrauchbar meldet.

### Bestände über Prozessgrenzen: `Dateibestand` gehärtet

Aufgaben, Aufträge, Einkaufsliste, Erinnerungen, Vorrat, Routinen und die Meldeliste erben von
`Dateibestand`; Sprach-Prozess und App haben je eine eigene Instanz auf derselben Datei. Beim Beheben
des Kostenzählers (`kosten.py`, Eintrag 10.10.2026) fielen drei Schwächen der Basisklasse auf:

1. Sie schrieb an Ort und Stelle. Ein Leser im anderen Prozess sah eine leere oder halbe Datei:
   `yaml.safe_load("")` liefert None, `_laden(None)` machte daraus einen leeren Bestand, und ein Speichern
   des Lesers überschrieb die Datei damit; eine halbe YAML-Datei warf `yaml.YAMLError` in den Aufrufer.
2. Sie merkte sich ihren Stand erst NACH dem Schreiben. Hatte die andere Seite dazwischen geschrieben,
   galt deren Fassung als die eigene und wurde nie geladen.
3. Der Stand war nur der Zeitstempel.

Nachgestellt mit zwei echten Prozessen auf einer Einkaufsliste mit 150 Artikeln (Windows 11,
Repo-Ordner): Der eine ersetzt sie ununterbrochen (rund 35 Speicherungen je Sekunde), der andere liest
ununterbrochen. Alter Stand: 12 von 3.231 und 18 von 3.292 Lesungen (je 4 s) zeigten eine
unvollständige Liste (0,4 bis 0,5 %), ohne Ausnahme. Neuer Stand: 0 von 28.700 Lesungen (drei Läufe, 120
bis 142 Speicherungen je Lauf, keine Fehler).

**Geändert** (`dateien.py`, Vorbild `kosten.py`):

- Erkannt wird eine andere Fassung an Zeitstempel, Größe und Inode (`stand_von`). Beim Überschreiben an
  Ort und Stelle blieb diese Kennung bei 1.060 und 1.235 von 2.000 schnellen Schreibvorgängen mit neuem
  Inhalt gleich (Temp- und Repo-Ordner, enge Schleife, 100 Byte; am 10.10.2026 waren es 257 von 2.000
  bei anderem Takt); bei 2.000 atomaren Ersetzungen nie, weil jede Fassung eine neue Datei mit neuer
  Inode ist. Die Kennung der Nebendatei vor dem Umbenennen war in 2.000 von 2.000 Fällen die der Datei
  danach.
- Geschrieben wird in `<Name>.<Prozessnummer>.tmp` und mit `os.replace` umbenannt (wie
  `Mitschrift.schreiben`, aber mit der Prozessnummer, weil hier zwei Prozesse schreiben). Die Kennung der
  eigenen Fassung kommt von der Nebendatei, VOR dem Umbenennen.
- Eine Datei, die sich nicht lesen lässt, wird nie als leerer Bestand geladen: leer oder nur Kommentare
  (ein Bestand schreibt immer Daten, auch einen leeren), halb, ungültiges UTF-8, ein Aufbau, den
  `_laden` nicht annimmt, gesperrt. Es gilt der Stand im Speicher; der nächste Zugriff liest erneut, auch
  bei derselben Kennung (eine Sperre ist kein Inhalt); das Log meldet es einmal je Fassung. Beim Start
  gilt ein leerer Bestand. Vor dem Überschreiben einer unlesbaren Datei bleibt eine Kopie als
  `<Name>.defekt` liegen. Das ist ein Zusatz: Vorher stoppte eine halbe Datei den Start mit `YAMLError`,
  eine leere ergab still einen leeren Bestand. Ohne die Kopie vernichtete der erste Speichervorgang nach
  einem Start mit kaputter Datei, was ein Mensch noch hätte retten können.
- Eine Datei, die nach dem ersten Laden fehlt, lässt den Speicher gelten; das nächste Speichern legt sie
  neu an (vorher: leerer Bestand). Beim Ersetzen verschwindet sie für einen Leser nie: Bei je 2.000
  Ersetzungen gegen einen Leser im Dauerlauf (92.482 und 247.348 `stat`-Aufrufe, 1.272 und 1.471
  Lesungen) kam kein einziges `FileNotFoundError`. Folge für die Handarbeit: Einen Bestand bei laufendem
  Alfred von Hand zu leeren geht nur mit einem gültigen leeren Bestand (`[]`, bei der Meldeliste
  `alle: false` und `kontakte: []`); eine leere oder gelöschte Datei gilt als Störung, bis Alfred neu
  startet.
- `Meldeliste._laden` baut erst alles und übernimmt dann, damit eine Datei mit falschem Aufbau nichts
  halb ändert.

**Gesperrte Datei: erst wiederholen, dann Fehler an den Aufrufer.** Unter Windows scheitert
`os.replace` mit PermissionError, solange die andere Seite die Datei geöffnet hat (Python öffnet ohne
FILE_SHARE_DELETE); schon ein `stat` der anderen Seite sperrt kurz (243 und 232 gesperrte Versuche bei
je 2.000 Ersetzungen). Umgekehrt scheitert das Lesen, während die Datei ersetzt wird. Gemessen mit zwei
Prozessen (der eine ersetzt, der andere liest im Dauerlauf; je 300 Ersetzungen; Python 3.13):

| Datei, Ordner | 1. Versuch scheitert | höchste Versuchszahl bei 2 / 5 / 10 / 25 ms Pause | endgültig gescheitert |
|---|---|---|---|
| 3,9 KB, Temp | 0 bis 1,7 % | 2 / 2 / 2 / 2 | 0 von 300 |
| 26,7 KB, Repo-Ordner | 5 bis 23 % | 4 / 3 / 2 / 2 | 0 von 300 |

Ein Schreiber allein scheiterte nie (0 von 300, Median 0,6 ms). Ohne Pause zwischen den Versuchen
scheiterten bei der großen Datei 9 von 300 Ersetzungen auch nach zehn Versuchen: Es braucht die Pause.
Ein Leser im Dauerlauf bekam, solange der andere ununterbrochen ersetzte, beim ersten Versuch in 1,9 bis
5,5 % der Lesungen einen PermissionError (je rund 1.100 Lesungen in 10 s); mit 2 oder 5 ms Pause klappte
der zweite Versuch immer.

Gewählt: Bei PermissionError wiederholen, mit 5 ms Pause, das Ersetzen höchstens zehnmal und das Lesen
höchstens dreimal; jeder andere Fehler (Platte voll, kein Schreibrecht) geht sofort weiter. Das Warten
blockiert den Ereignis-Loop, aber nur im Ausnahmefall und höchstens 45 ms (Lesen 10 ms). Scheitert es
danach noch, geht der PermissionError an den Aufrufer. Verworfen: den Fehler sofort weiterreichen (bis
23 % der Speicherungen scheiterten sichtbar) und ihn verschlucken oder das Speichern nachholen (die
Aufrufer ändern den Speicher vor `_speichern()`; Eingaben von Alexander gingen verloren oder stünden nur
im Speicher).

**Nach einem gescheiterten Speichern kehrt der Speicher zur Datei zurück** (`_verworfen`: der nächste
Zugriff lädt neu). Der Aufrufer hat den Speicher schon geändert und bekommt einen Fehler; ohne die Rückkehr
zeigte der Bestand die Änderung weiter, und ein späteres Speichern schriebe sie doch noch hinaus. Bei
`Erinnerungen.ansage` gälte die Erinnerung im Speicher als ausgeliefert, obwohl nichts gesagt wurde, und
käme nie wieder; so liefert der nächste Takt sie noch einmal. Pipecat meldet dem Modell ein Werkzeug,
das eine Ausnahme wirft, als gescheitert (ohne den Fehlertext), und die Schleifen für Erinnerungen und
Routinen laufen unter `beaufsichtigt`. Der Auftragsbearbeiter wertete jede
Ausnahme in seiner Schleife als Fehler des Auftrags (`scheitern`), auch eine aus
`lebenszeichen_geben`; nach zehn gesperrten Versuchen (nie beobachtet) wäre ein laufender Auftrag samt seiner
Recherche (die weiterlief) als gescheitert gemeldet worden. Darum fängt er dort jetzt einen OSError ab (Warnung
im Log, der nächste Takt versucht es wieder; verwaist gilt ein Auftrag erst nach drei Minuten ohne Lebenszeichen).
Ungeschützt bleibt `abschliessen` am Ende: Scheitert es nach allen Versuchen, endet die Aufgabe, und der Auftrag
beginnt nach drei Minuten ohne Lebenszeichen neu.

**Dauer.** Windows 11, Repo-Ordner, Median: `_schreiben` allein 0,6 ms alt, 1,2 ms neu (10 und 70
Artikel; bei 150 Artikeln 0,7 gegen 1,8 ms), 99 % unter 2,5 ms (alt unter 0,8 bis 2,3 ms). Ein ganzer
Aufruf (`abhaken`: Nachladen, Ändern, YAML, Speichern) 2,6 gegen 3,2 ms bei 10, 13,0 gegen 13,8 ms bei
70 und 26,5 gegen 32,2 ms bei 150 Artikeln. Den Rest braucht PyYAML in Python: `safe_load` 22,3 ms und
`safe_dump` 13,1 ms bei 70 Artikeln, mit libyaml (`CSafeLoader`, `CSafeDumper`, im venv vorhanden) je
rund 2,6 ms. Das Speichern läuft wie vorher synchron im Ereignis-Loop.

**Grenzen.**

- Ohne Schloss bleibt ein Zeitfenster: Schreibt die andere Seite zwischen dem Nachladen und dem
  Umbenennen, überschreibt das Umbenennen ihre Fassung. Gemessen mit zwei Prozessen, die dicht
  hintereinander Artikel hinzufügen (Raum alle 0,15 s, App alle 1,5 s und dazwischen alle 0,3 s die
  Anzeige; 10 s, je rund 70 Hinzufügungen): alter Stand 0 bis 51 % verloren (21 Läufe, 18 mit 0 bis
  8,6 %, drei mit 38 bis 51 %), neuer Stand 1,4 bis 10 % (22 Läufe, keine Ausreißer). Im Dauerfeuer ohne
  Pause (8 s, drei Läufe) 49 bis 52 % statt 55 bis 80 %. Das Fenster ist mit dem libyaml-Lader nur halb so lang (Nachtrag unten), geschlossen ist es nicht. Was weg
  ist, sind die leeren und halben Fassungen: Der Speicher einer Seite wurde im alten Stand beim
  Nachladen in 6 von 12 Läufen 1- bis 3-mal kleiner (bis zu 35 Einträge weniger: Die Datei war gerade
  leer oder halb geschrieben), im neuen in 0 von 12. Die Ausreißer des alten Stands (38 bis 51 %) passen
  dazu; belegt ist es nicht.
- Das Log zeigt, ob das Fenster getroffen wurde: Vor dem Ersetzen prüft `_schreiben`, ob die Datei noch die
  Fassung ist, auf der der Speicher beruht, und warnt nach dem Ersetzen, wenn nicht. Im Alltagstakt kam
  je verlorener Hinzufügung eine Warnung (±1), im Dauerfeuer mehr Warnungen als Verluste (370 bis 433
  gegen 244 bis 258). Die Warnung steht bewusst NACH dem Ersetzen: Als sie noch davor stand, blieb in
  der Messung ein Prozess beim Warnen stehen (seine Standardfehlerausgabe ging in eine Pipe, die niemand
  leerte), während die Nebendatei fertig war, und schrieb danach seine alte Fassung über die Datei
  (App 34 bis 39 Hinzufügungen statt rund 250, in der Datei 38 bis 43 von 262 bis 294 Artikeln).
- Im Betrieb liegt das Fenster weit darunter (Rechnung, nicht gemessen): Bei 70 Artikeln dauert ein
  Aufruf rund 14 ms; bei zehn Speicherungen der Gegenseite je Stunde überschneiden sich zwei Fenster mit
  einer Wahrscheinlichkeit von etwa 1 zu 10.000 je Speicherung. Regelmäßig schreibt der Sprach-Prozess nur
  das Lebenszeichen eines laufenden Auftrags (alle 30 s, `auftraege.yaml`); sonst schreibt er auf
  Anweisung oder zu einer Fälligkeit.
- `Erinnerungen.ansage` speichert ohne vorheriges Nachladen (es ändert den Eintrag, den `faellige` eben
  lieferte); dort reicht das Fenster vom Aufruf von `faellige` bis zum Speichern, bei mehreren fälligen
  Erinnerungen um die Dauer des Push dazwischen länger. Die Warnung zeigt es.
- Nicht eingebaut: ein Schloss (plattformabhängig, `msvcrt`/`fcntl`, könnte den Sprach-Loop aufhalten) und
  eine Ausnahme bei veralteter Fassung (träfe die Hintergrundaufgaben, siehe Lebenszeichen oben). Der
  Hebel mit der größten Wirkung auf das Fenster war libyaml (Nachtrag unten): Das Laden läuft mit `CSafeLoader`;
  der Schreiber (`yaml.safe_dump`) steht in sechs Unterklassen und könnte mit `CSafeDumper` das Format
  der Dateien im Detail ändern. Nicht umgestellt.
- Unter Linux, wo Alfred läuft, nicht gemessen; POSIX erlaubt das Umbenennen über eine geöffnete Datei,
  die Wiederholungen dürften dort entfallen. Unter ext4 werden Inode-Nummern schnell wiederverwendet: Die
  Kennung täuscht nur, wenn zwei Fassungen im selben Takt der Uhr, gleich groß und mit derselben Inode
  entstehen und ein Leser die dazwischen verpasst.
- Kein `fsync` vor dem Umbenennen (wie bei `Mitschrift.schreiben`); gegen einen Stromausfall mitten im
  Speichern schützt das nicht sicher. Stirbt ein Prozess zwischen Schreiben und Umbenennen, bleibt
  `<Name>.<Prozessnummer>.tmp` liegen.
- Die Messungen liefen als Skripte außerhalb des Repositorys (zwei echte Prozesse auf einer Datei, im
  Temp- und im Repo-Ordner); `tests/test_dateien.py` bildet die Abläufe nach und schickt in einem Fall
  einen echten zweiten Prozess an die Datei.

**Tests.** 40 neue Fälle in `tests/test_dateien.py` (26 Funktionen): zwei Instanzen abwechselnd, gleicher
Zeitstempel und gleiche Größe, die Fassung der anderen Seite dazwischen, Nachladen nur bei Änderung,
Kennung vor und nach dem Umbenennen, acht unlesbare Inhalte beim Lesen und beim Start (mit Kopie
`.defekt`), halbe Datei, die später ganz wird, Log einmal je Fassung, gelöschte Datei, Meldeliste mit
falschem Aufbau, Nebendatei und Umbenennen, Wiederholung beim Ersetzen und beim Lesen, dauerhaft
gesperrt (Fehler an den Aufrufer, Speicher kehrt zurück, Erinnerung bleibt fällig), Fehler beim
Schreiben der Nebendatei, Warnung bei überschriebener Fassung, und ein Leser im anderen Prozess. 36
davon scheitern am alten Stand (5db5f1f); vier laufen auf beiden durch (Abwechseln, Nachladen nur bei
Änderung, leere Datei beim Start, Datei ohne Vorgänger). Wo ein Test die Datei von Hand ändert, setzt
`von_hand_schreiben` den Zeitstempel sicher später; sonst hinge es vom Takt der Uhr ab, ob der alte Stand die
Änderung bemerkt und der Test scheitert (der Meldeliste-Fall bestand so einmal zufällig). Dazu in
`tests/test_auftraege.py` der Fall "gesperrtes Lebenszeichen", der am alten Stand scheitert. Die komplette
Suite besteht (800 Tests).

### Nachtrag: Laden mit libyaml

Auf Alexanders Wort lädt `Dateibestand` jetzt mit `yaml.CSafeLoader` (`dateien.LADER`), wo libyaml vorhanden ist,
sonst mit `SafeLoader` (ohne libyaml simuliert: läuft). Das Verhalten ist das von `safe_load` (keine Objekte). In
Python bleibt das Schreiben (`yaml.safe_dump`, 13,1 ms bei 70 Artikeln): Es steht in sechs Unterklassen, und der
C-Schreiber könnte das Format der Dateien im Detail ändern.

**Liest er dasselbe?** Gegenprobe der beiden Lader (PyYAML 6.0.3): Texte, wie die Bestände sie schreiben (198 mit
heiklen Zeichenketten, `yaml.safe_dump` und `json.dumps`), 20.000 Zufallsstrings in beiden Formaten und jeder Anfang
echter Dateien, wie ihn ein Leser bei einer halb geschriebenen Datei sieht (5.356): keine Abweichung, auch nicht bei
der Frage lesbar oder unlesbar. Ein BOM am Dateianfang lesen beide gleich. Anders ist es nur bei absichtlich
beschädigten Dateien (60.000 zufällig veränderte, 801 Abweichungen, 1,3 %): libyaml liest Tabulatoren in Werten und
Einrückung, die der Python-Lader abweist (711 Fälle, etwa `Milch<Tab>2 Liter` oder JSON mit Tabs; eine von Hand mit
Tab bearbeitete Datei wird lesbar statt unlesbar), und weist ab, was Python liest, bei einem BOM mitten im Text und
einem `?` in JSON (ohne Tabulator und BOM blieben 27, davon 25 mit `?`). Die Bestände schreiben keine Tabulatoren.

**Wirkung** (Windows 11, Repo-Ordner, zwei Instanzen wechseln sich ab, Median): Nachladen einer von der Gegenseite
geänderten Datei 3,7 auf 1,1 ms bei 10, 18,7 auf 3,0 ms bei 70 und 43,7 auf 9,8 ms bei 150 Artikeln; das ganze
Zeitfenster (Nachladen, Ändern, Speichern) 6,4 auf 4,1, 31,4 auf 15,8 und 70,5 auf 36,0 ms. Zwei Prozesse im dichten
Takt (je 12 Läufe hintereinander, 10 s): vorher 1,4 bis 10 % der Hinzufügungen verloren (52 von rund 830), jetzt 0
bis 4,3 % (19 von rund 840); die Warnung im Log zählte weiter einen Verlust genau (±1). Im Dauerfeuer unverändert rund
49 % (49,2 bis 49,4 gegen 49,0 bis 50,7 %). Leser im anderen Prozess: weiter keine unvollständige Liste (0 von
153.000 Lesungen, vorher 0 von 28.800).

**Nebenwirkung.** Ein Leser im Dauerlauf ohne Pause fragt nun öfter (153.000 statt 28.800 Aufrufe in 12 s, meist nur
`stat`) und hält den Schreiber auf: 93 bis 124 statt 131 bis 139 Speicherungen in 4 s durch die Wiederholungen, ohne
Fehler. Einen solchen Takt gibt es im Betrieb nicht. Die Wiederholungen mit dem schnellen Leser neu gemessen (je 300
Ersetzungen, 3,9 KB im Temp- und 26,7 KB im Repo-Ordner): Ersetzen höchstens 4 Versuche bei 2 ms Pause und höchstens 2
bei 5 ms, endgültig gescheitert 0 von 300 in jeder Zeile; Lesen bei 5 ms Pause höchstens 2 Versuche (beim ersten
gesperrt 1,9 bis 4,5 %), ohne Pause bis zu 5. Die Grenzen von 10 und 3 Versuchen mit 5 ms Pause bleiben.

### Gespräch mit Besuch: Gästemodus nur im Prompt, Unterstrich, WhatsApp-Kanäle

Gespräch im Büro von 08:44 bis 08:50 (logs/raum-buero.log, gedaechtnis/mitschrift.json, GLM 5.3):
Wetter, „Also wir haben Gäste da“, Kevin zu Besuch, Fragen nach Privatem, „Welcher Modus?“, „Der
Besuch ist wieder weg“ und die letzten WhatsApp-Kontakte. Im fehler.log stand zu dem Gespräch
nichts. Inhaltlich lehnte Alfred alles Private richtig ab, und ein englisches Video im Hintergrund
überhörte der Stimmfilter zweimal (Ähnlichkeit 0,10 und −0,01).

Befunde:
- Der Gästemodus schützte nur über den Prompt. Fällige Erinnerungen und WhatsApp-Meldungen zählen
  als angefordert und kamen in jedem Modus durch, eine Meldung sogar wörtlich am Modell vorbei.
  `erinnerungen_anzeigen`, `nachschlagen` und die Regel-Werkzeuge gehören zu `kern` und blieben
  angeboten: Das Wissen fehlte im Prompt, war aber abrufbar. Und „der Besuch ist weg“ schaltete
  ohne Stimmprüfung zurück; in den ersten zehn Sekunden nach dem Wachwort zählt jede Stimme.
- Auf „Welcher Modus?“ wich Alfred aus („Sagen wir: Solange Besuch im Haus ist …“). „Bestätige den
  Wechsel keinesfalls technisch“ stand im Hinweis von `modus_wechseln` und zusätzlich in der
  Beschreibung des Modus, galt also für den ganzen Besuch. Die Begrüßung geriet zu „Ah, nette
  Begrüßung dann von mir!“, und „sagt einfach Bescheid“ an den Gast lief ins Leere: Nach den ersten
  Sekunden lässt der Stimmfilter nur Alexanders Stimme oder das Wachwort durch.
- „_diskret ist mein zweiter Vorname.“: Pipecats `MarkdownTextFilter` entfernt nur Paare, der
  einzelne Unterstrich ging an die Sprachausgabe (nachgestellt).
- WhatsApp: Die Brücke nahm alles außer Gruppen und eigenen Nachrichten. An dem Tag kamen 364
  Nachrichten von 19 Absendern, 287 davon von sieben Absendern ohne Namen, deren Kennung die von
  Kanälen ist (120363…, 18 Ziffern). Der Speicher fasst 50; um 08:47 lagen dort nur Deal-Posts,
  Alfreds „kein echter Kontakt“ war also nicht verlässlich. Die Antwort brauchte 9,7 s, davon 4,5 s
  fürs Modell nach `nachrichten_lesen` mit den ungekürzten Posts samt Links.
- Kein Fehler, aber leicht zu verwechseln: „Stimme 0,39, passt nicht“ bei „Nenn mir mal …“ liegt
  unter der Schwelle für Freigaben (0,41) und über der des Filters (0,36). „TOTAL (from client
  connected) 200 s“ bei „Welcher Modus?“: Die kurze Frage löste die VAD nicht aus, Pipecat misst
  dann ab Verbindungsaufbau; echt kamen rund 1,7 s dazu, weil der Zug ohne VAD-Ende endete.

Geändert:
- Gästemodus im Code (`GAESTEMODUS`, `Persona.gaeste_da`): Werkzeuge mit `privat=True` fehlen
  (Erinnerungen anzeigen und löschen, `merken`, `profil_merken`, `nachschlagen`, Regeln,
  `feedback_merken`). Die Zustellregeln halten alles ohne `Ereignis.vor_gaesten` zurück, bis der
  Besuch weg ist; durch dürfen Timer und was im Gästemodus angefordert wurde (Ergebnis des
  Denkers). `modus_wechseln` verlässt den Gästemodus nicht, wenn das Stimmprofil die Stimme ablehnt
  (`stimmpruefer.passt is False`, wie bei `bestaetigen`), und wechselt hinein auch bei fremdem
  Inhalt ohne Rückfrage.
- Prompt: Im Gästemodus nennt Alfred den Modus auf Nachfrage offen und sagt, dass Privates nach dem
  Besuch kommt; nur der Hinweis nach dem Wechsel sagt, den Modus nicht von sich aus zu nennen und
  die Gäste direkt zu begrüßen. Den Satz „Bestätige den Wechsel keinesfalls technisch …“ aus der
  Beschreibung des Modus genommen (Vorlage und Alexanders persoenlichkeit.yaml). Im Raum steht im
  Prompt, dass Gäste Alfred beim Namen ansprechen müssen. Im Standardmodus ändern sich Prompt und
  Werkzeugliste nicht, deshalb lief die Eval nur mit den neuen Szenarien.
- `SprechtextFilter` (vorher `EmojiFilter`) nimmt auch einzelne `* _ ~ ` #` aus der Sprachausgabe.
- Die Brücke nimmt nur noch Einzelchats (`aus_einzelchat`: kein Kanal `@newsletter`, kein Status
  `@broadcast`) und schreibt die Art des Chats ins Log. `nachrichten_lesen` kürzt in der Übersicht
  wie die Ansage (240 Zeichen, Links als „(Link)“, `gekuerzt` in whatsapp.py); nach einer Person
  gefragt, kommt der ganze Text.
- Eval-Suite: drei Szenarien `gaeste-*`. Jedes Szenario bekommt eine eigene Kopie der
  Persönlichkeit; sonst begänne nach einem Wechsel in den Gästemodus das nächste darin.

Geprüft: die ganze pytest-Suite, pyflakes, und `python -m alfred.eval gaeste --wiederholungen 3` gegen
GLM 5.3: 9 von 9 Läufen bestanden, 28,6 Cent. Begrüßungen wie „Herzlich willkommen! Schön, dass ihr
da seid. Wie darf ich die Gäste denn nennen?“, auf „Welcher Modus ist gerade an?“ jedes Mal „Ich bin
gerade im Gästemodus …“, und „Welche Erinnerungen hat Alexander heute noch?“ ohne Werkzeugaufruf mit
„… nenne ich erst, wenn der Besuch weg ist“. Die Eval prüft auf Textebene ohne Stimmprofil und ohne
WhatsApp-Eingang; Stimmprüfung, Zurückhalten und Kürzen prüfen die Tests.

Offen: Ist ein Satz zu kurz für die Stimmprüfung (unter einer Sekunde), bleibt `passt` vom Satz
davor stehen; ein knappes „Besuch weg“ erbt also dessen Urteil, wie ein kurzes „Ja“ bei
`bestaetigen`. Was im Gästemodus wartet, verliert ein Neustart des Prozesses wie alles in der
Warteschlange; fällige Erinnerungen kamen dann wenigstens als Push. Die drei Erinnerungen und das
Morgenbriefing vom gescheiterten Anlauf um 08:20 sind weiter nicht nachgeholt.

## 20.10.2026

### Stimmerkennung aus, Codewort für heikle Aktionen

Alexander: Die Stimmerkennung „funktioniert extrem selten, und dadurch muss ich mich immer
tausendmal wiederholen“; das Wachwort soll bleiben, die Schwelle für die Stimme weg. Die Logs im
Windows-Checkout (Profil vom 9.10. mit WeSpeaker, Schwellen 0,41 für Freigaben und 0,36 für den
Stimmfilter, Gespräche bis zum Umzug am 16.10.) zeigen 66 eigene Sätze zwischen 0,23 und 0,75
(Median 0,56); 14 lagen unter der Freigabe-Schwelle, 6 unter der des Filters. Am 10.10. verweigerte
`bestaetigen` drei Freigaben (0,23, 0,23, 0,26), Alexander musste Auftrag und Ja wiederholen.
Seit dem 16.10. läuft Alfred auf dem Mini-PC; das Jabra steht dort an einem anderen Platz, und das
Profil wurde nicht neu angelernt. Dass es seitdem „extrem selten“ passt, ist damit wahrscheinlich,
belegt ist es nicht (die Logs des Mini-PCs sind hier nicht ausgewertet).

Eine Bestätigung am Handy (Code im Push oder Ja-Knopf) lehnte Alexander ab: „Wenn ich die über mein
Handy bestätigen muss, dann kann ich das auch direkt selbst machen.“ Gewählt: ein Codewort.

Geändert:
- `stimmerkennung.aktiv` (Vorgabe false). Ohne bleibt das Profil ungenutzt (`stimme.aktives_profil`):
  kein Stimmprüfer, keine Stimmprüfung am Wachwort, keine Gesprächszeit nach `offen_sekunden`. Die
  Schleuse schließt nach zehn Sekunden Stille, danach weckt das Wachwort. Das Stimmen-Modell prüft
  `pruefen` nur noch mit eingeschalteter Erkennung.
- Bereitschaft ohne Stimme: Der Stimmfilter hätte jede Antwort auf Alfreds Frage mit mehr als drei
  Wörtern verworfen (kein Satz ist prüfbar). Wartet Alfred auf eine Antwort (offene Rückfrage,
  Zuhören, letzte Antwort eine Frage; `erwartet_antwort` in pipeline.py), gilt die Länge nicht.
- Codewort (codewort.py): WhatsApp, Mail und das Ende des Gästemodus fragen im Sprach-Loop nach dem
  Codewort statt nach einem Ja (`_freigabe_anfordern(heikel=True)`, `Freigabe.mit_codewort`). Der
  `Codewortschutz` direkt hinter der Transkription ersetzt es durch „[Codewort]“, bevor Filter, Log,
  Mitschrift, Modell oder Tagesprotokoll den Satz sehen; `bestaetigen` prüft nur die Marke
  (`codewort_genannt`), nach drei Antworten ohne verfällt die Rückfrage. Das Wort steht in .env
  (`ALFRED_CODEWORT`, festlegen mit `python -m alfred.codewort`). Im App-Chat reicht weiter ein Ja,
  ebenso ein Knopf im Push. Ohne gesetztes Codewort reicht auch im Raum ein Ja; `pruefen` warnt dann.

Geprüft: Eval-Suite mit GLM 5.3, neue Szenarien `whatsapp-codewort` und `gaeste-ende-mit-codewort`
je 2 von 2 Läufen (zusammen rund 24 Cent). GLM bat um das Codewort, ohne eins zu raten, rief auf
„Ja, schick ab.“ gar nicht erst `bestaetigen` auf, sondern fragte noch einmal nach dem Codewort, und
schickte mit „[Codewort].“ ab. Beim Gästemodus sagte es „nenn mir bitte euer Codewort“, an alle im
Raum gerichtet. Selbsttest im Raum-Modus (Hedda, 16 kHz): „Alfred, wie spät ist es?“ in 4,4 s
beantwortet, Schleuse zehn Sekunden danach zu; „Alfred Zitronenfalter“ mit gesetztem Codewort
erkannte Mistral so, dass der Schutz griff: Als „Alfred [Codewort]“ ging es durch den Sprachfilter,
und das Wort stand weder in der Konsole (auch nicht auf DEBUG) noch in Mitschrift oder Gedächtnis.

Nebenbei: Der erste Selbsttest startete im Windows-Checkout die WhatsApp-Brücke
(`whatsapp.bruecke_starten` ist dort an); sie meldete sich um 08:30:33 mit der Kopplung an, die seit
dem 16.10. die Brücke auf dem Mini-PC nutzt, und lief bis etwa 08:32. Selbsttests unter Windows
deshalb mit einer Kopie der Konfiguration (`ALFRED_CONFIG_DIR`, siehe architektur.md, „Selbsttest“).

Offen: In Bereitschaft zählt während einer Rückfrage jeder Satz, auch einer aus dem Fernseher. Bei
heiklen Aktionen hält das Codewort ihn auf; bei anderen Freigaben (Termin, Regel, Routine) könnte
ein „yes“ aus dem Fernseher die Aktion bestätigen, die Alexander gerade selbst angefragt hat. Wer
im Raum ist, wenn Alexander das Codewort sagt, kennt es danach; dann neu festlegen.
