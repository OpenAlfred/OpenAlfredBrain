# Architektur

Wie Alfreds Bausteine zusammenspielen, Stand 6.10.2026. Die Regeln für Änderungen stehen in
[CLAUDE.md](../CLAUDE.md), Messwerte und die Chronik der Live-Läufe in
[erkenntnisse.md](erkenntnisse.md). Die Dateiübersicht mit einer Zeile je Modul steht im
[Handbuch](handbuch.md), Abschnitt „Projektaufbau"; die Anleitungen zum Einrichten der Anbindungen ebenfalls dort.

## Prozesse

Drei Prozesse arbeiten auf denselben Dateien unter `gedaechtnis/` und `config/`.

### Sprach-Prozess

- Im Browser: `bot.py` mit Pipecats Runner auf Port 7860. Die Verbindung steht, solange die
  Seite offen ist, ohne Wachwort. `bot.py` stellt die Ausgabe auf UTF-8 (der Start-Banner des
  Runners enthält Unicode-Zeichen) und trennt bei einem Aufbaufehler die Verbindung mit dem
  Log „Alfred konnte nicht starten".
- Im Raum: `python -m alfred.room <raum>` mit Mikrofon und Lautsprecher aus `raeume` in
  `config/alfred.yaml` und der Wachwort-Schleuse. `--geraete` listet die Geräte, `--hoertest`
  prüft Mikrofon und Lautsprecher ohne Mistral (drei Sekunden Aufnahme mit Pegelanzeige, dann
  Wiedergabe).
- Aufsicht: `room.run_room` baut `_einmal_laufen` nach jedem Abbruch neu (Pause 5 s, bei
  Dauerfehlern verdoppelt bis 120 s); nur das `Abbruchsignal` (Strg+C, SIGTERM) beendet.
  Pipecats Runner läuft deshalb mit `handle_sigint=False, handle_sigterm=False`. Der Raum-Modus
  setzt `ProcessorUnusablePolicy.CANCEL` und schaltet das Beenden des `PipelineWorker` nach
  300 s ohne Gespräch ab. Ein fehlendes Audiogerät ist ein Abbruch wie jeder andere
  (`DeviceNotFoundError` aus `find_device`, im Log ohne Traceback): Kurz nach dem Hochfahren
  oder nach einem USB-Aussetzer ist es bald wieder da. Mit der Meldung enden nur `--hoertest`
  und `alfred.stimme`. Über den Neuaufbau bleiben die per Stimme geschaltete Bereitschaft und
  die Ereignis-Warteschlange (`stand`); scheitert ein Anlauf nach `build_voice_loop`, beendet
  `Sitzung.abschliessen` dessen Hintergrundaufgaben trotzdem.
- Hintergrundaufgaben (Erinnerungen, Routinen, Aufträge, Freigaben vom Handy, Geburtstage)
  laufen nur hier, in `beaufsichtigt` (pipeline.py), das sie nach einem Fehler neu startet.
- Schreibt `gedaechtnis/mitschrift.json` (Zustand, Verstandenes, Gesagtes, Antwortzeit,
  Lebenszeichen alle 5 s) für Zentrale und System der App (Orb, Zustand, Mitschrift); meldet er
  sich fünfzehn Sekunden nicht, zeigt die App das rot.

### App

- `web.py` (FastAPI, Port 7870, PWA unter `web_static/`) liefert Oberfläche und API. Die
  Oberfläche braucht keinen Build-Schritt: `index.html` (mit dem Symbol-Sprite), `css/` (Tokens
  für Dunkel und Hell, Grundlagen, Komponenten, Ansichten) und ES-Module unter `js/`.
  `js/app.js` baut Seitenleiste (am Handy Tab-Leiste mit „Mehr"), Kopfzeile und den Router über
  die Adressleiste (`#/heute`, `#/auftraege/3`, `#/faehigkeiten/kalender`; alte Push-Links
  `#auftrag-3` leitet er um). Jede Ansicht liegt in `js/views/<id>.js`, wird beim ersten Besuch
  nachgeladen (`mount(wurzel)` liefert `{laden, zeigen, verlassen, takt}`) und bleibt danach im
  Dokument, damit Eingaben und Scrollstand bleiben. Neue Ansicht: Modul, Eintrag in `ANSICHTEN`
  (app.js) und in `SHELL` (sw.js); `tests/test_web_static.py` prüft, dass Dateien, Importe und
  Symbole des Sprites zusammenpassen.
- Gemeinsames: `js/core.js` (DOM-Helfer `h()`, Datenzugriff, Formatierung, Dialoge,
  Seitenfenster, Orb, gemeinsamer Zustand), `js/teile.js`, `js/charts.js`, `js/palette.js`
  (Befehlspalette mit Strg/⌘ K), `js/design.js` (Dunkel, Hell, System; Standard Dunkel). Inhalte,
  die aus Modellen, Mails oder dem Netz stammen, kommen nie als HTML in die Seite, nur über `h()`
  und `textContent` oder über `prosa()`, das Markdown zuerst vollständig maskiert; dessen
  `markdownEinsetzen` ist die einzige Stelle mit `innerHTML`.
- Ansichten: Zentrale (Orb mit Live-Zustand aus der Mitschrift, Tag, Aufträge, Fähigkeiten,
  Anbindungen, Aktivität), Chat, Heute, Aufgaben, Aufträge (Liste und Berichtleser), Einkauf,
  Ernährung, Haushalt, Fähigkeiten, Gedächtnis und System (Sprach-Prozess, Anbindungen, Kosten,
  Aktionsprotokoll, Fehler). Die Marken in der Navigation kommen aus `/api/zaehler`, das nur
  Dateien liest; `/api/heute` ruft Wetter und Kalender und deshalb nur die Ansichten, die es
  zeigen.
- Fähigkeiten: `/api/faehigkeiten` (`werkzeuge/uebersicht.py`) leitet Gruppen, Stufen, Modi und
  Anbindungen aus dem Werkzeug-Register ab. Ein Späher über den Ressourcen findet bei einem nicht
  eingerichteten Werkzeug das leere Feld (`braucht` hört beim ersten leeren Feld auf) und nennt es
  („Google Kalender"); was nur der Sprach-Prozess hat (Schleuse, Zuhör-Schalter), heißt
  `nur_stimme` und zählt als einsatzbereit. Von Hand stehen dort nur die Anzeigetexte: ein Satz
  je Werkzeug (`BESCHREIBUNG`; die Beschreibung für das Modell ist an das Modell gerichtet) und
  Titel, Symbol und Beispielsätze je Gruppe (`ANZEIGE`); fehlt ein Eintrag, scheitert
  `tests/test_uebersicht.py`.
- Das Textgespräch (textgespraech.py) nutzt dieselben Werkzeuge und ruft sie mit einem
  `params`-Ersatz auf (SimpleNamespace wie in den Tests); `params.pipeline_worker` und
  `params.llm` sind dort None. Für sofortige Ansagen gibt es einen Sammler als `llm` mit
  (`_Sprachausgabe`); haben alle Werkzeuge einer Runde mit `run_llm=False` geantwortet, fragt
  es das Modell nicht erneut. `Textgespraech.verlauf` behält Werkzeugaufrufe und Ergebnisse
  (`_als_dict`), kürzt nur vor einer Nutzernachricht (`_kuerzen`). Die App zeigt jeden
  Werkzeugaufruf als Karte vor der Antwort: `POST /api/chat` liefert neben `antworten` die
  `schritte` des Zugs (`Textgespraech.schritte`, aufbereitet von `schritt_ansicht` mit Gruppe,
  Stufe, Argumenten, Status ok, bestaetigung oder fehler und einer Kurzfassung), `GET /api/chat`
  setzt sie über `ansicht_verlauf` aus dem Verlauf wieder zusammen (`sichtbarer_verlauf` bleibt
  für Tests). Aufrufe aus Ereignisrunden (Denker fertig) stehen nur im POST: `antworten` legt sie
  nicht im Verlauf ab. `Textgespraech.antworten` verwirft eine offene Freigabe bei einem Nein.
- Sprachnachrichten (`js/aufnahme.js`, `sprachnachricht.py`, `POST /api/sprachnachricht`): Der Chat
  nimmt über `MediaRecorder` auf, zeigt Pegel und Länge und schickt erst auf Knopfdruck. Der Browser
  rechnet die Aufnahme (WebM, Ogg oder MP4, je nach Browser) selbst in WAV mit 16 kHz mono um; so
  braucht der Server kein ffmpeg. Der Server prüft Kopf und Länge (`wav_dauer`, 0,5 s bis 10 min,
  höchstens 25 MB), lässt sie von `modelle.sprachnachricht` abhören (Mistrals Transkription für
  fertige Dateien, ohne Sprachvorgabe), zählt die Sekunden im `Kostenzaehler` und gibt die Abschrift
  an `Textgespraech.antworten(text, gesprochen=True)`. Sie zählt wie getippter Text, auch als Ja auf
  eine Rückfrage; `Textgespraech.sprachnachrichten` merkt sich die Nachrichten nur für die Anzeige.
  Das Audio wird nirgends gespeichert. Verlässt man den Chat, hält die Aufnahme an und wartet;
  klingt sie stumm (Spitzenpegel unter 0,01), schickt erst ein zweiter Tipp sie ab. Scheitert die
  Antwort nach dem Abhören, steht die Abschrift im Fehler (`detail.transkript`) und landet im
  Eingabefeld.
- Statische Dateien und die Startseite gehen mit `Cache-Control: no-cache` hinaus (`KEIN_CACHE`,
  `AktuelleDateien` in web.py): Der Browser fragt je Datei nach (ETag, 304), sonst liefen nach
  einem Update neue Seite und altes JavaScript gemischt.
- Robustheit der Oberfläche: Jede Ansicht lädt mit `juengste()` (core.js), sodass nur die Antwort der
  jüngsten Anfrage zeichnet; sonst überholt eine späte alte Antwort eine neuere (Abhaken, Neuladen im
  Takt). Die Zentrale zeichnet jede Karte aus ihrer eigenen Anfrage und neu nur bei geänderten Daten
  (`einmal`), damit kein langsamer Dienst die anderen aufhält und der Tastaturfokus im Takt nicht
  springt. Der Service Worker (`sw.js`, Netz zuerst) fällt bei Netzausfall und bei Serverfehlern ab
  500 auf die Kopie zurück. Nicht lesbare Modellausgaben (Foto, Wochenplan) melden die Routen als
  502 mit Klartext, nie als 500. Die Freigabeseite (`/api/freigabe/...`) maskiert die Beschreibung,
  und `aktion_protokollieren` faltet Tabulator und Zeilenumbrüche in Leerzeichen, weil das Protokoll
  tabgetrennt ist und die Argumente vom Modell oder aus fremdem Text stammen.
- Die App liefert keine Erinnerungen aus und startet keine Routinen oder Aufträge; das bleibt
  beim Sprach-Prozess, sonst käme alles doppelt. Aufträge aus der App warten, bis er läuft.
- Fernzugriff nur über Tailscale: `tailscale serve --bg --set-path /app 7870` legt HTTPS vor
  die App, `tailscale serve --bg 7860` vor den Browser-Server (Safari verlangt HTTPS fürs
  Mikrofon). Kein offener Port.

### WhatsApp-Brücke

- `whatsapp_bridge.py` (stdlib + neonize + qrcode, keine alfred-Importe) läuft in `.venv-wa`
  als eigener Prozess auf 127.0.0.1:7861 mit Token-Datei (`config/whatsapp_bridge.token`),
  weil neonize protobuf ≥ 7.34 braucht und Pipecat < 7 erlaubt. Alfred (whatsapp.py) spricht
  sie über httpx an (`whatsapp.url`, `whatsapp.token`, `whatsapp.landesvorwahl`).
- `NewClient(name)` nimmt den Pfad der Sitzungsdatenbank (`config/whatsapp.db`) als Namen,
  `connect()` blockiert und läuft im Thread, QR über `client.event.qr(handler)`. Wirft schon
  der Verbindungsaufbau (beim Booten ohne DNS, 16.10.2026), versucht `Bruecke._verbinden` es nach
  `NEUVERSUCH_PAUSEN` erneut; vorher endete der Faden, und die Brücke lief ohne WhatsApp weiter.
- `GET /kontakte` liefert das Telefonbuch aus der neonize-Sitzung (`whatsmeow_contacts`,
  `full_name` = Name aus dem Handy). Alfred hält es 10 min in `WhatsApp.adressbuch()`;
  `kontakt_suchen` sucht Wörter einzeln, Umlaute ausgeschrieben, kurze Wörter nur exakt,
  längere ab 0,8 Ähnlichkeit. Eigene Kontakte aus `gedaechtnis/kontakte.yaml` gehen vor.
- Eingang: nur Textnachrichten aus Einzelchats (`aus_einzelchat`: nicht von Alexander, keine
  Gruppe, kein Kanal `@newsletter`, kein Status `@broadcast`), höchstens `MAX_EINGANG` (50) im
  Speicher; das Log nennt je Nachricht die Art des Chats. Bis zum 16.10.2026 kamen Kanäle mit
  durch und verdrängten mit hunderten Deal-Posts am Tag jede echte Nachricht.
- Verknüpftes Gerät = kein offizieller Weg, Sperrrisiko klein, aber vorhanden (steht im
  Handbuch). Die Sitzung darf nie auf zwei Rechnern gleichzeitig laufen.

### Gemeinsame Bausteine aller Prozesse

- `Ressourcen` (resources.py) teilen sich alle Werkzeuge einer Sitzung; Pipecat reicht sie als
  `app_resources` in jeden Werkzeugaufruf. `build_ressourcen` (pipeline.py) ist der gemeinsame
  Aufbau für Stimme und App.
- Bestände erben von `Dateibestand` (dateien.py) und laden nach, wenn sich die Datei geändert
  hat (Zeitstempel, Größe, Inode; geladen wird mit libyaml, wo es das gibt); gespeichert wird über eine Nebendatei mit `os.replace` (unter
  Windows mit kurzer Wiederholung), eine unlesbare Datei lässt den Stand im Speicher gelten, und ein
  gescheitertes Speichern geht als Fehler an den Aufrufer, der Speicher kehrt zur Datei zurück
  (Messungen und Grenzen: docs/erkenntnisse.md, 16.10.2026). Die Auftragsliste ist YAML, geteilt
  mit der App.
- `logbuch.einrichten` schreibt `logs/<prozess>.log` (täglich neu, zwei Wochen aufgehoben) und
  `logs/fehler.log` (Warnungen und Fehler aller Prozesse). Die Konsole filtert Pipecats
  Prompt-Dumps und „auto-registered"-Zeilen (`logbuch.leise`). Im Browser-Modus hängt
  `logbuch.einrichten` erst in `bot()`, weil Pipecats `main()` vorher alle Logger-Senken entfernt.
- `python -m alfred.pruefen`: Startprüfung in einer halben Minute (Schlüssel, Modelle,
  Sprachausgabe mit Antwortzeit, Spracherkennung, Geräte je Raum mit Host-API, Wachwort-Modell,
  Stimmprofil, Kalender, WhatsApp-Brücke, Mail, Push, Suche, Spotify, Tailscale, letzte
  Sicherung); jede Zeile ✔, ! oder ✘, Rückgabewert 1 bei einem Fehler, `--offline` ohne Netz.
  Alte Stimmprofile ohne Mikrofon meldet sie als Warnung, ebenso eine Zeitzone außer
  MEZ/MESZ (`pruefe_zeitzone`; ein frischer Server steht oft auf UTC, dann nennt Alfred falsche
  Uhrzeiten).
- Sicherung: `python -m alfred.sichern` (Windows und Linux, Zip, `--ziel`, `--behalten`,
  `--einspielen`); `deploy/sichern.sh` (über den systemd-Timer) ruft nur noch dieses Modul auf,
  damit die Liste der gesicherten Dateien nur an einer Stelle steht. Gelöscht werden nur eigene
  `alfred-*.zip`, alte tgz bleiben stehen. Die `.env` ist absichtlich in keiner Sicherung.

## Sprach-Loop (pipeline.py)

- `build_voice_loop` baut die Kette aus Transport, Transkription, Sprachmodell und
  Sprachausgabe. Kein Werkzeugtest durchläuft sie, nur
  `test_sprachschleife_laesst_sich_ohne_anbindungen_aufbauen` mit leerem Transport; deshalb
  nach Umbauten pyflakes laufen lassen.
- Eigene Bausteine in der Kette: Im Raum sitzt die Wachwort-Schleuse vor der Transkription.
  Hinter der Transkription verwirft der Sprachfilter (sprachfilter.py) Transkripte in Sprachen
  außerhalb `gespraech.sprachen`, dahinter prüft der `Stimmfilter` die Stimme (siehe Wachwort
  und Schleuse). Der `Rueckfragewaechter` steht vor dem Nutzer-Aggregator, verwirft eine
  offene Freigabe bei einem Satz, der mit einem Wort aus `NEIN_WOERTER` beginnt, und hält jeden
  anderen Satz als Antwort darauf fest (`Freigabe.antworten`). Dahinter erkennt der
  `Ansagewaechter` Antworten auf ungefragte Ansagen (siehe Ereignisse und Ansagen). Vor der
  Sprachausgabe fängt Pipecats `MarkdownTextFilter` *Sternchen* ab, die das Modell trotz
  Sprachregel um Titel setzt, und `SprechtextFilter` (tts_cache.py) nimmt Emojis heraus (in
  WhatsApp-Texten gehören sie hinein, vorgelesen werden sie zu Lauten) und einzelne Markdown-Zeichen,
  die ohne Gegenstück stehen bleiben („_diskret“, 16.10.2026). Der Verlauf behält den Text.
- Systemprompt: Persönlichkeit (persona.py: Charakter, Regler als Verhaltenssätze, Regeln,
  Modus), Uhrzeit, Datum und Jahr ausgeschrieben (`datum_gesprochen`, `jahr_gesprochen`), die
  nächsten sieben Tage mit Datum, `kontext.md`, das heutige Protokoll, offene Aufgaben,
  Geburtstage der nächsten Woche, das Profil und die Fähigkeitenliste (nur Regeln und
  aktueller Stand; was ein Werkzeug tut, steht allein in seiner Beschreibung). Gesetzt beim
  Verbindungsaufbau und bei jedem Wachwort; Auffrischung über `Ressourcen.systemprompt` und
  `LLMUpdateSettingsFrame` via `params.pipeline_worker`. In langen Gesprächen liefert das
  Werkzeug `aktuelle_zeit` die frische Zeit.
- Hinweise an das Modell (Erinnerung fällig, Denker fertig) gehen als `developer`-Nachricht in
  den Kontext. Mistral kennt die Rolle nicht, Pipecat macht eine Nutzernachricht daraus;
  deshalb die festen Anfangs- und Schlussworte aus CLAUDE.md.
- Turn-Erkennung: Smart Turn mit `stop_secs` 1,5 s (`gespraech.pause_max_sekunden`).
  `FinalizingMistralSTTService` (stt.py) markiert Transkripte als endgültig; sonst wartet die
  Turn-Erkennung jedes Mal ihren Sicherheits-Timer ab (1,89 s nach dem letzten Wort). Ein
  verworfenes Transkript lässt den Zug über Pipecats `user_turn_stop_timeout` (5 s) still
  enden (`idle_prompt` ist None).
- Zuhören (turns.py): `Zuhoeren` ist ein Schalter an den Ressourcen; `GeduldigeTurnErkennung`
  (Unterklasse von Pipecats `TurnAnalyzerUserTurnStopStrategy`) schiebt im Zuhör-Modus das
  Zugende auf, bis `gespraech.zuhoeren_pause_sekunden` (5) Stille sind; VAD-Start oder neue
  Zwischentranskripte brechen die Frist ab, ein Satzende aus `ENDE` („fertig", „was meinst du?")
  beendet den Zug sofort. Der Zug bleibt offen, also warten auch Schleuse und Zusteller. An:
  Werkzeug `zuhoeren`, Sätze aus `AUSREDEN` („lass mich ausreden"), `nachricht_senden` ohne
  Text (Diktat), und in pipeline.py ein Zuhör-Versprechen oder eine Frage nach dem Wortlaut
  einer Nachricht (behauptungen.py). Aus: am Zugende, außer es kamen seit dem Einschalten
  weniger als `MIN_INHALT_WOERTER` (3) Wörter, dann war es nur angekündigt.
- Wächter gegen erfundene Aktionen (behauptungen.py): In `on_assistant_turn_stopped` prüft
  pipeline.py die letzte Modellantwort (`Gespraechszustand.modellantwort_abholen`, nur echte
  LLM-Antworten, keine Ansagen). Behauptet sie mit `MUSTER` eine Aktion („ich richte die
  Erinnerung ein", „ich sage dir Bescheid") und lief seit Alexanders letztem Satz kein
  Werkzeug, geht ein `developer`-Hinweis mit `LLMMessagesAppendFrame(run_llm=True)` hinterher:
  Werkzeug aufrufen oder sagen, dass es das nicht kann. Antworten auf Hinweise prüft er nicht,
  also keine Schleife.
- Unterbrechungen (turns.py): Solange Alfred spricht, unterbricht ihn erst ein Einwurf mit
  `gespraech.unterbrechen_ab_woertern` Wörtern, ein Wort aus `gespraech.stoppwoerter` sofort,
  und bei offener Freigabe (`res.offene_freigabe`) ein Antwortwort aus `ANTWORTWOERTER`
  sofort. Eine Unterbrechung ohne Text bekommt einen deutschen Hinweis in
  `EmptyUserTurnConfig` (Pipecats englischer `interrupted_prompt` ließ das Modell die ganze
  Antwort wiederholen), und der unterbrochene Assistententext endet mit „[hier unterbrochen]"
  (Handler `on_assistant_turn_stopped`).
- Nach jeder Antwort steht im Log eine Aufschlüsselung der Antwortzeit (Pausenerkennung,
  Transkription, Sprachmodell, Sprachausgabe). `kosten.py` zählt als Observer auf MetricsFrame
  Token, Transkriptionsminuten, Sprachausgabe und Websuchen je Tag (`gedaechtnis/kosten.json`),
  nur beim ersten Weiterreichen (`FramePushed.first_push`; Beobachter sehen jeden Schritt der
  Kette), und rechnet mit `preise` aus der Konfiguration, sonst mit den Listenpreisen in
  config.py (`STANDARD_MODELLPREISE`, zwischengespeicherte Eingabe zum eigenen Preis).
- Modelle unter `modelle`: `sprecher` (Vorlage `mistral-small-2603`, bei Alexander
  `zai-glm-5-3`), `denker` (`zai-glm-5-3`), `werkzeuge` (`mistral-small-2603`, führt Websuche
  und Code für den Denker aus), `transkription`, `sprachausgabe`. Mistral Medium ist nicht
  gewünscht.

## Sprachausgabe und Transkription

- `MistralTTSService` braucht zwingend eine Stimme (`stimme` in der Konfiguration, Liste über
  `python -m alfred.voices`). Eingestellt ist „Jarvis v.1", eine eigene deutsche Stimme aus
  Alexanders Mistral-Konsole; die Referenzaufnahme liegt zur Sicherung unter `config/stimmen/`,
  neu anlegen mit `client.audio.voices.create_async`. Mistrals 30 Standardstimmen sind Englisch
  und Französisch, sprechen Deutsch aber verständlich.
- `tts_cache.CachingMistralTTSService` spielt Sätze bis 60 Zeichen nach dem ersten Mal aus
  `cache/phrasen/` (Schlüssel: Modell, Stimme, Abtastrate; Fehlerläufe werden nicht
  gespeichert). Eine Anfrage ohne Ton bricht sie nach 2 s ab und stellt sie neu, nach weiteren
  3 s ein drittes Mal; der letzte Versuch wartet ohne Frist. Pipecats `stop_frame_timeout_s`
  (Standard 3 s) ist nur ein Sicherheitsnetz, ein sauber beendeter Satz schließt seinen Kontext
  sofort; der Mistral-TTS bekommt 20 s, sonst meldet Pipecat „completed with no audio" und legt
  den Kontext neu an. Die Sprachausgabe bleibt ein austauschbares Modul (Kandidat: lokale
  Piper-Stimme `de_DE-thorsten-high`, auf dem Mini-PC eher das Modell `medium`).
- `FinalizingMistralSTTService.ruhen` schließt beim Schließen der Schleuse (`on_sleep`) die
  Realtime-Verbindung, weil Mistral stille Sitzungen selbst beendet; nicht Pipecats
  `_disconnect`, das die Verbindung offen lässt, weil der abgebrochene Empfang `_connection`
  vorher leert. Das nächste Audio verbindet neu. Bricht Mistral serverseitig ab (`1011`,
  „Timeout waiting for response"), verbindet Pipecat beim nächsten Audio-Stück neu; die ersten
  Silben danach können fehlen.

## Wachwort und Schleuse (wakeword.py)

- Erkenner: Vosk mit dem kleinen deutschen Modell (`wachwort.modell`, rund 45 MB beim ersten
  Start) und vollem Vokabular, hinter dem Protokoll `WakeWordDetector` austauschbar (ein
  trainiertes Modell wie openWakeWord wäre schneller; vor einem Wechsel die Installation unter
  Python 3.12 prüfen). Auf das Wachwort als einziges Wort einschränken funktioniert nicht
  (Fehlauslösungen bei jedem Satz). Für mehr Reichweite kommt das große Modell
  `vosk-model-de-0.21` in Frage.
- Die Schleuse (`WakeWordGate`) lässt kein Audio zu Mistral, bevor das Wachwort
  (`wachwort.phrasen`) lokal erkannt ist; im Fehlerfall bleibt sie geschlossen. Weil das Wort
  erst rund 1,5 s nach dem Aussprechen erscheint, puffert sie fünf Sekunden und reicht das Audio
  ab dem Wortbeginn nach.
- Mit `stimmerkennung.aktiv`, Stimmprofil und `stimmerkennung.wachwort` (beides Vorgabe aus)
  prüft `stimmpruefung` das Audio ab dem Wachwort gegen das Profil, Schwelle `schwelle_wachwort`;
  zu kurzes Audio gilt als passend. Aus, weil das Wort allein für kein Sprechermodell reicht: Vosk
  wies Alexander bei 15 von 32 Weckversuchen ab (Erkenntnisse, 9.10.2026).
- Nach dem Wachwort und nach jeder Antwort bleibt sie `wachwort.offen_sekunden` (Vorgabe 10)
  für jede Stimme offen. Ohne Stimmerkennung (Vorgabe seit dem 20.10.2026) schließt sie danach,
  und das Wachwort weckt wieder. Mit ihr bleibt sie bis `wachwort.gespraech_sekunden` (Vorgabe
  120) offen für Sätze, die der `Stimmfilter` (hinter dem Sprachfilter) annimmt: Ähnlichkeit ≥
  `schwelle_wachwort`, Wachwort im Satz, oder nicht prüfbar und höchstens
  `MAX_UNGEPRUEFT_WOERTER` (3) Wörter lang („Ja", „Danke"). Fremdes Sprechen setzt die Frist
  nicht zurück; ein angefangener Satz schiebt das Ende höchstens 30 s auf. Der Stimmprüfer
  liefert sein Ergebnis je VAD-Abschnitt als Future (`urteil()`, Frist 1 s), weil das Transkript
  rund 0,25 s vor dem Stimmvektor fertig ist. `gespraech_beenden` (nur mit Schleuse, „Danke,
  das war's") schließt sie beim nächsten Audio.
- Bereitschaft (Standby; `wachwort.bereitschaft` als Startwert, per Werkzeug `bereitschaft`
  umschaltbar, `room.run_room` trägt den Stand über einen Neuaufbau): kein Gespräch ohne
  Wachwort. Nach Alfreds Antwort gibt es keine freie Zeit für fremde Stimmen
  (`nur_eigene_stimme`), und die Schleuse schließt nach `bereitschaft_nachlauf_sekunden` (3),
  oder nach `rueckfrage_sekunden` (8), solange `Sitzung.wartet_auf_antwort` sagt, dass eine
  Rückfrage, das Zuhören, eine Antwort oder ein Werkzeug läuft. Ein angefangener Satz schiebt
  das Ende höchstens `NACHLAUF_BEREITSCHAFT_SECS` (10) auf. Gegen Fernseher und Videos, die am
  6.10.2026 Gesprächszeit und Stimmfilter siebzehn Minuten offen hielten. Ohne Stimmerkennung
  ist kein Satz prüfbar: Nach Alfreds Antwort kommt dann durch, was das Wachwort enthält, höchstens
  drei Wörter hat oder auf eine Frage Alfreds antwortet (`erwartet_antwort` in pipeline.py:
  offene Rückfrage, Zuhören, letzte Antwort eine Frage; dann gilt die Länge nicht).
- `verbinde_schleuse` (pipeline.py) hängt die Gesprächslogik an die Schleuse: Protokoll beim
  Einschlafen, Transkription ruhen lassen, frischer Systemprompt beim Aufwachen, neues Gespräch
  nach `gespraech.vergessen_nach_minuten`. Ein neues Gespräch setzt auch den Protokoll-Zähler
  zurück (`Sitzung.neues_gespraech`), sonst fehlen dem nächsten Protokoll die ersten Nachrichten.
  `Sitzung.protokollieren` rückt den Zähler erst nach der Zusammenfassung vor, unter einer
  Sperre: Bricht der Runner beim Beenden das Einschlafen ab, holt `abschliessen` dieselben
  Nachrichten nach, ohne sie doppelt zu schreiben.

## Stimmprofil (stimme.py)

- Seit dem 20.10.2026 aus (`stimmerkennung.aktiv`, Vorgabe false): Alexanders eigene Sätze fielen
  bis auf 0,23, Freigaben wurden verweigert, und er musste alles mehrmals sagen (Erkenntnisse,
  20.10.2026). `aktives_profil(settings)` liefert das Profil nur mit eingeschalteter Erkennung;
  Raum und Sprach-Loop holen es nur darüber. Heikle Aktionen sichert jetzt das Codewort (siehe
  Werkzeuge und Freigaben). Das Folgende gilt, wenn sie eingeschaltet ist.
- Sprechermodell nach `stimmerkennung.modell` über `stimmerkenner()`: `wespeaker-resnet34`
  (sprechermodell.py: onnxruntime, Kaldi-Filterbank in numpy, lädt beim ersten Gebrauch mit
  fester Prüfsumme nach ~/.cache/alfred, rund 20 ms je Vektor) oder `vosk-model-spk-0.4`
  (`SpkModel` lädt nicht selbst, `Model.get_model_by_name` holt es; rund 0,4 s je Vektor).
  Profil in `config/stimme.json` (`stimmerkennung.profil`) mit Mikrofon, Datum, Modell und
  Proben; es gilt nur für sein Modell (`Stimmprofil.anderes_modell`, Warnung in `pruefen`).
  Profile ohne Modellangabe stammen von Vosk.
- `python -m alfred.stimme --anlernen` nimmt das Mikrofon des Raums über `room.find_device`:
  20 s freie Rede plus kurze Wachwort-Sätze (Stille abgeschnitten, auf 2,5 s gekürzt wie in der
  Schleuse). Profil = Mittelwert normierter Vektoren; Schwellen-Empfehlung aus
  Leave-one-out-Ähnlichkeit (`selbstaehnlichkeit`), Übernahme per gezieltem Zeilen-Edit in
  `config/alfred.yaml` (`schwellen_setzen`, Kommentare bleiben). `--pruefen` prüft drei
  Wachwort-Sätze oder eine Datei.
- Schwellen unter `stimmerkennung`: `schwelle` für Freigaben, `schwelle_wachwort` fürs
  Wachwort; das Log nennt bei jedem Zug Wert und Schwelle. Der Stimmvektor hängt am Mikrofon
  und am Abstand: neues Mikrofon oder neuer Platz heißt neu anlernen. Sprechererkennung ist ein
  Filter, kein Schutz gegen Aufnahmen.

## Werkzeuge und Freigaben (werkzeuge/, resources.py)

- Werkzeuge sind Pipecat-Direktfunktionen: async, erster Parameter `params`, Schema aus
  Signatur und Docstring. Die Beschreibung sagt knapp, was das Werkzeug tut und wann es
  **nicht** gilt. Werkzeuge müssen `params.pipeline_worker` und `params.llm` als None
  vertragen (Textgespräch). Anbindungen sind nur aktiv, wenn sie eingerichtet sind; die
  Werkzeugzahl wächst damit von 19 (Grundausstattung) auf 67 (alles eingerichtet).
- Register: Werkzeuge liegen im Paket `src/alfred/werkzeuge/`, ein Modul je Gruppe, und melden
  sich mit `@werkzeug(gruppe, stufe, braucht=..., rueckfrage=...)` an (registrierung.py).
  `braucht` prüft die Anbindung an den Ressourcen. `rueckfrage` (Text mit `{argument}` oder
  Funktion der Argumente, mit eigener Fassung fürs Log als `Rueckfrage`) haben genau die
  schreibenden Werkzeuge, sonst wirft der Decorator. `build_tools` und `faehigkeiten`
  (auswahl.py) filtern das Register nach Anbindung und nach den Gruppen des aktiven Modus
  (`Persona.aktive_gruppen`; Namen in `GRUPPEN` in persona.py, `werkzeuge:` je Modus in
  persoenlichkeit.yaml, `kern` immer), im Gästemodus zusätzlich nach `privat` (siehe unten).
  Die Reihenfolge der Importe in `werkzeuge/__init__.py` ist die der Werkzeugliste.
- Moduswechsel im laufenden Gespräch: `modus_wechseln` schickt über `params.pipeline_worker`
  einen `LLMUpdateSettingsFrame` (neuer Systemprompt) und einen `LLMSetToolsFrame` mit
  `build_tools(res)`; Pipecats Nutzer-Aggregator übernimmt ihn per `context.set_tools`, der
  LLM-Dienst registriert die Handler beim nächsten Kontext-Frame nach. Beides wirkt ab der
  nächsten Antwort. Der Systemprompt nennt die abgeschalteten Gruppen, damit das Modell sagt,
  dass es das in diesem Modus nicht macht. Das Textgespräch baut die Liste je Runde neu.
- Gästemodus (`GAESTEMODUS`, `Persona.gaeste_da`; seit dem Gespräch mit Besuch am 16.10.2026):
  Was Alexanders Privates schützt, steht im Code, nicht in der Beschreibung des Modus. Werkzeuge
  mit `privat=True` fehlen, obwohl `kern` gilt (Erinnerungen anzeigen und löschen, `merken`,
  `profil_merken`, `nachschlagen`, Regeln, `feedback_merken`; `werkzeuge_fuer`), das Wissen
  fehlt im Prompt (`pipeline.system_prompt`), und Ereignisse warten, bis der Besuch weg ist
  (siehe Ereignisse und Ansagen). Zurück schaltet nur Alexander: Im Sprach-Loop mit Codewort legt
  `modus_wechseln` eine heikle Rückfrage an („Gästemodus beenden, zurück in den Modus …“), erst
  das Codewort schaltet um (`_modus_setzen` in `bestaetigen`). Mit Stimmerkennung bleibt der
  Modus außerdem, wenn das Profil die Stimme ablehnt (`stimmpruefer.passt is False`); im
  App-Chat und ohne Codewort zählt das Wort. Hinein geht es auch bei fremdem Inhalt ohne
  Rückfrage, er schränkt nur ein (`_modus_rueckfrage` liefert None); heraus fragt die Schutzhülle
  dann zuerst nach einem Ja und danach das Werkzeug nach dem Codewort. Nach dem Wechsel nennt
  Alfred den Modus nicht von sich aus (Hinweis im Ergebnis), auf Nachfrage offen (Prompt); im
  Raum steht im Prompt, dass Gäste ihn beim Namen ansprechen müssen, weil die Schleuse nach
  einer kurzen Pause nur noch auf das Wachwort hört.
- Freigabestufen: `lesend` (auch Gesprächssteuerung wie `bestaetigen`, `zuhoeren`,
  `bereitschaft`) und `schreibend` (Merken, Eintragen) laufen frei, `bestaetigen` (Schreiben in
  fremde Systeme) nur nach Bestätigung.
  Stufe `bestaetigen` legt die Aktion in `offene_freigabe` ab und meldet `bestaetigung_noetig`;
  erst das Werkzeug `bestaetigen` führt sie aus, und das Werkzeugergebnis sagt ausdrücklich,
  dass ohne `bestaetigen` nichts passiert ist und die Rückfrage alle Angaben wiederholen soll.
  Ein Satz, der mit einem Wort aus `NEIN_WOERTER` beginnt, verwirft die offene Freigabe
  (`Rueckfragewaechter`, im App-Chat `Textgespraech.antworten`); nach `Freigabe.GUELTIG_SECS`
  (300 s) zählt kein Ja mehr. Und `bestaetigen` führt nur aus, wenn seit der Rückfrage ein Satz
  des Gegenübers kam (`Freigabe.antworten`; der `Rueckfragewaechter` hält jedes Transkript fest,
  im App-Chat `Textgespraech.antworten` jeden Text), Alfred die Frage im Verlauf danach gestellt
  hat (`verlauf_seit_rueckfrage` über `Freigabe.tool_call_id`) und der letzte Zug ein klares Ja
  ist (`zustimmung`: ein Wort aus `JA_WOERTER`, keines aus `VORBEHALT_WOERTER`, keine
  Gegenfrage). Sonst bleibt die Rückfrage offen, das Aktionsprotokoll bekommt „VERWEIGERT ohne
  Antwort", „… ohne Rückfrage" oder „… kein klares Ja", und der Hinweis sagt dem Modell, was
  fehlt; Werkzeugtext und Hinweis sagen außerdem, `bestaetigen` nie im selben Zug wie die Frage
  aufzurufen. Mit Stimmerkennung (aus seit dem 20.10.2026) zählt ein Ja nur in Alexanders Stimme
  (`stimmerkennung.schwelle`). Jede Freigabe steht mit Uhrzeit, Wortlaut, Stimmwert und
  Ergebnis in `gedaechtnis/aktionen.log`, auch verweigerte.
- Codewort für heikle Aktionen (codewort.py; seit dem 20.10.2026, statt der Stimme): Was in
  Alexanders Namen an andere geht (`nachricht_senden`, `mail_senden`, `mail_antworten`) und das
  Ende des Gästemodus fragen über `_freigabe_anfordern(..., heikel=True)`. Ist `res.codewort`
  gesetzt (nur in `build_voice_loop`, aus `ALFRED_CODEWORT` in .env), bekommt die Freigabe
  `mit_codewort`: Der Hinweis lässt um das Codewort statt um ein Ja bitten, `bestaetigen` prüft
  statt `zustimmung` nur `codewort_genannt` (Marke im Satz, kein Vorbehaltswort), und nach
  `Freigabe.MAX_FEHLVERSUCHE` (3) Antworten ohne verfällt die Rückfrage („VERWEIGERT ohne
  Codewort, verworfen“); ausgeführt heißt im Protokoll „ausgeführt (Codewort)“. Der
  `Codewortschutz` sitzt direkt hinter der Transkription und ersetzt das Wort in jedem
  Transkript, auch im vorläufigen, durch `MARKE` („[Codewort]“); eine Marke, die schon im Text
  steht, wird entschärft. Dahinter sieht es niemand: weder Sprach- und Stimmfilter samt Log noch
  Mitschrift, Modell und Tagesprotokoll. Ein Satz mit Codewort verliert seine Sprache, sonst
  verwürfe der Sprachfilter ein einzelnes Wort als Niederländisch. Erkannt wird in ganzen Wörtern,
  normalisiert und zusammengezogen („Zitronen-Falter“ zählt, „Seeadler“ für „Adler“ nicht).
  `untauglich` lehnt Wörter unter fünf Buchstaben ab, Ziffern, Ja-, Nein- und Vorbehaltswörter,
  das Wachwort und „Codewort“; ein solches in .env bleibt ungenutzt (Warnung). Festlegen mit
  `python -m alfred.codewort` (verdeckt, zweimal, über `einrichtung.env_setzen`), `--aus` leert
  es. Im App-Chat gibt es kein `res.codewort`, dort reicht ein Ja, ebenso ein Knopf im Push;
  ohne gesetztes Codewort auch im Raum (`pruefen` warnt).
- Freigaben vom Handy (freigaben.py): `gedaechtnis/freigabe.json` mit Kennung und Geheimnis;
  Push mit ntfy-Knöpfen (`Actions`-Kopfzeile, `method=GET`) auf
  `/api/freigabe/{id}/{ja|nein}?g=...`; `freigaben_beobachten` im Sprach-Prozess führt aus und
  legt die Ansage als Ereignis ab, außer die Aktion hat schon selbst gesprochen (`Gesprochen`,
  siehe unten). Gesprochene Entscheidungen schließen die Datei. Der Knopf gilt zehn Minuten, ein
  gesprochenes Ja fünf.
- Fremdschutz: Fremder Text im Gespräch (Mail, Nachricht, Suchtreffer, Bericht, Denker) schaltet
  alle schreibenden Werkzeuge auf Rückfrage mit Wert, bis das Gespräch endet.
  `Ressourcen.fremder_inhalt` (Herkunft, Zeitpunkt) setzen `mails_abfragen`, `mail_vorlesen`,
  `nachrichten_lesen`, `bericht_lesen`, `websuche`, das Wetter im Websuche-Ausweg und
  `auftraege_anzeigen` mit Kurzfassung; Ereignisse mit fremdem Text (Denker, Bericht, neue
  WhatsApp) kommen als `Fremd(...)` (siehe Ereignisse und Ansagen). `build_tools` steckt genau
  die schreibenden Werkzeuge in die Hülle `_mit_fremdschutz` (basis.py); Tests prüfen Hülle,
  Vorlage und Signatur für jedes Werkzeug im Register. Steht die Marke, laufen sie
  über `_freigabe_anfordern` („E-Mail-Adresse von Jan auf x@y
  setzen"), mit Grund im Hinweis, im Push und im `aktionen.log` (`Freigabe.protokolltext`); die
  Aktion läuft erst in `bestaetigen` mit dessen params (`Freigabe.starten`), damit
  `auftrag_geben` seine Bestätigung dort spricht. `musik` mit `was_laeuft` bleibt frei (Vorlage
  liefert None). Die Marke endet mit dem Gespräch (`Sitzung.neues_gespraech`; im Chat, sobald
  der Zug mit dem fremden Text aus dem Verlauf fällt), nicht nach einer Frist; auch eine reine
  Rechnung des Denkers setzt sie, weil von außen nicht zu sehen ist, ob er gesucht hat.
  Rückfragen von `mail_senden`, `mail_antworten` und `nachricht_senden` nennen Adresse bzw.
  Nummer (`nummer_gesprochen`); die Nummer steht nur in der gesprochenen Fassung, nicht im Log
  (`Freigabe.protokoll`).
- Ausweich-Muster: Scheitert ein Dienst, versucht das Werkzeug selbst den nächsten Weg und
  meldet im Ergebnis einen `hinweis`, was der Sprecher dazu sagen soll (Wetter → Websuche). Der
  Prompt sagt zusätzlich allgemein, bei Fehlern einen anderen Weg zu versuchen.
- Fehlt einem Werkzeug ein Fakt über Alexander, meldet es im Fehlertext, was zu tun ist („frag
  nach dem Wohnort, merk ihn mit profil_merken, frag das Wetter erneut ab"); das Modell folgt
  dem zuverlässig.
- Sofortige Ansagen aus dem Code: `auftrag_geben` spricht über `_sofort_sagen`
  (`TTSSpeakFrame` über `params.llm`) und meldet `FunctionCallResultProperties(run_llm=False)`,
  damit das Modell nichts anhängt. Läuft so ein Werkzeug erst nach einer Rückfrage, merkt sich
  `_mit_fremdschutz` das `run_llm=False` im Ergebnis (`Gesprochen`, ein dict in resources.py);
  `bestaetigen` gibt es weiter, und `freigaben_beobachten` legt dann keinen Hinweis ans Modell
  ab. Ohne das sprach das Modell nach dem Ja eine zweite Bestätigung (9.10.2026).
- Werkzeugergebnisse liefern Uhrzeiten ausgeschrieben (`uhrzeit_gesprochen`) und nehmen
  Datumsangaben als Datum, nicht als „Tage ab jetzt" (`termine_abfragen`).

### Die Werkzeugfamilien

- Zeit: `aktuelle_zeit` mit Uhrzeit, Datum und Jahr ausgeschrieben.
- Erinnerungen und Timer (erinnerungen.py): `gedaechtnis/erinnerungen.json`, überleben einen
  Neustart; verpasste Ansagen werden beim nächsten Start nachgeholt und nennen die alte Uhrzeit.
  Wiederkehrende (`wiederholen`: täglich, werktags, Wochentage) bleiben stehen und kommen jedes
  Mal zur selben Ortszeit, auch über die Zeitumstellung; verpasste nur bis zwei Stunden danach.
  Timer klingeln über eine `Ansage` und nennen die Restzeit. Mit ntfy gehen fällige
  Erinnerungen auch als Push, „dringend" lässt das Telefon auch stumm klingeln.
- Gedächtnis (gedaechtnis.py): `merken` nach Bereichen, Nachschlagen; Tagesprotokoll.
- Profil: `profil_merken` nach `gedaechtnis/profil.yaml`; nebenbei genannte Fakten merkt
  Alfred von selbst.
- Denker und Aufträge: eigener Abschnitt unten.
- Kalender (kalender.py): Google Kalender über OAuth-Client und Token in `config/`, Bereich
  `calendar.events`; `termine_abfragen` nimmt ein Datum; anlegen, verschieben, absagen nach Ja,
  die Rückfrage nennt den Wochentag. `Kalender.suchen` findet Termine nach Titelteil, ohne
  Datum in den nächsten zwei Wochen; bei mehreren Treffern fragt das Werkzeug nach dem Tag,
  statt zu raten. Erinnerung `kalender.vorlauf_minuten` (10) vor jedem Termin. Ohne
  Freischaltung läuft Alfred ohne Kalender und sagt das auch.
- Geburtstage (geburtstage.py): bei den Kontakten (`kontakte.yaml`, Feld `geburtstag`, auch
  ohne Nummer); `Geburtstagserinnerer` legt am Vortag um neun eine Erinnerung an, gemerkt pro
  Jahr in `geburtstage_erinnert.json`.
- Wetter (wetter.py): Open-Meteo, kostenlos, ohne Schlüssel; Ortsname wird einmal geocodiert;
  Wohnort aus dem Profil, `wetter.ort` nur als Ersatz; fällt der Dienst aus, Websuche.
- Routinen (routinen.py): Auftrag als Text plus Zeitplan in `gedaechtnis/routinen.yaml`;
  `Routinen.warten` legt den fälligen Auftrag als Ereignis ab, der Sprecher erledigt ihn mit
  seinen Werkzeugen. Nachholen nur zwei Stunden, danach nächster Tag; beim Anlegen nach der
  Uhrzeit zählt heute als gelaufen; ohne Uhrzeit ein benannter Ablauf auf Zuruf.
- WhatsApp (whatsapp.py): `nachricht_senden` auch ohne Text (sucht den Empfänger und meldet,
  dass Alfred nach dem Text fragen soll); diktierter Text wird wörtlich übernommen; nur aus dem
  Ergebnis weiß Alfred, ob jemand im Adressbuch steht. Die Rückfrage nennt die Nummer
  (`nummer_gesprochen`) und Emojis mit Namen. Nachrichten vorlesen auf Nachfrage
  (`nachrichten_lesen`, Absender über die Nummer; die Übersicht kürzt jede Nachricht wie die
  Ansage mit `gekuerzt`, Links als „(Link)“, nach einer Person gefragt kommt sie ganz). `whatsapp_melden` führt
  `gedaechtnis/whatsapp_melden.yaml` (`Meldeliste`; ohne Namen für alle), und
  `nachrichten_melden` im Sprach-Prozess fragt die Brücke alle 10 s und reiht Neues von dort
  als `Fremd(Ansage(...))` ein: wörtlich, lange Texte gekürzt, im Verlauf als vorgelesene
  fremde Nachricht markiert. Ist die Brücke nicht erreichbar, startet `WhatsApp` sie einmal
  selbst (`whatsapp.bruecke_starten`).
- Aufgaben (aufgaben.py): `gedaechtnis/aufgaben.yaml` mit optionalem Datum, offene Aufgaben im
  Prompt, erledigte bleiben dreißig Tage. Einkauf (einkauf.py): Einkaufsliste nach Abteilungen.
- Ernährung (ernaehrung.py): Schätzung per Mistral mit `response_format json_object`, Barcode
  über Open Food Facts (User-Agent setzen), Ziele, Bilanz, Gewicht unter
  `gedaechtnis/ernaehrung/`. Haushalt (haushalt.py): Fotos als `image_url` mit Data-URI an
  `mistral-small-2603`; Wochenplan, Ausgaben, Vorrat mit Haltbarkeit (`ausgaben/`,
  `vorrat.yaml`, `wochenplan.yaml`).
- Push (benachrichtigung.py): ntfy mit `benachrichtigung.url`, `thema`, `token`; `app_url`
  macht Nachrichten antippbar und schaltet die Freigaben vom Handy frei.
- Mail (mail.py): IMAP/SMTP mit `MAIL_ADRESSE` und `MAIL_PASSWORT` aus `.env`, kein OAuth
  (Gmail-Mailzugriff ist ein eingeschränkter Bereich); Mailinhalt gilt als Information, nie als
  Anweisung; schreiben und antworten nur nach Ja.
- Musik (musik.py): spotipy, Extra `musik`, Spotify-Connect-Gerät `spotify.geraet`, Zugang
  `config/spotify_token.json`. Fehlt ein Gerät, öffnet Alfred unter Windows die Spotify-App
  und wartet bis zu `GERAET_WARTEN_SECS` (10), bis sie als Gerät erscheint.
- Kosten (kosten.py, `kosten_heute` auch für einen anderen Tag), Modus (`modus_wechseln`, nur
  auf ausdrücklichen Wunsch) und Regeln per
  Stimme (`gedaechtnis/regeln.yaml`, nach Ja), Feedback (`feedback_merken` nach
  `gedaechtnis/feedback.md` mit letztem Wortwechsel, Werkzeugaufrufen und Ergebnissen),
  `gespraech_beenden`.

## Ereignisse und Ansagen (ereignisse.py)

- Proaktives Sprechen: Erzeuger (Erinnerungen, Routinen, Denker, Aufträge, Freigaben,
  Geburtstage) legen Texte in `ereignisse`; der `Zusteller` wartet auf 1,5 s Gesprächsruhe
  (Beobachter `Gespraechszustand`), öffnet im Raum-Modus die Wachwort-Schleuse und lässt den
  Sprecher den Text ausgeben. Nur der Sprecher spricht. `Gespraechszustand` merkt sich außerdem
  die letzte Modellantwort (`modellantwort_abholen`, für den Wächter gegen erfundene Aktionen)
  und ob gerade eine läuft (`antwort_laeuft`, für die Bereitschaft).
- Ereignisse sind Text für das Modell („Hinweis vom System …"). Eine `Ansage` geht am Modell
  vorbei: Klingelton als `OutputAudioRawFrame` plus `TTSSpeakFrame`, danach als
  Assistentennachricht in den Verlauf. Timer nutzen das (sofort, kostenlos, kein Risiko, dass
  das Modell etwas anderes sagt); der Transport sampelt den 24-kHz-Ton selbst um. Mit `verlauf`
  steht im Verlauf ein anderer Text als der gesprochene (neue WhatsApp: als vorgelesene fremde
  Nachricht markiert).
- Ereignisse mit fremdem Text (Ergebnis von `nachdenken`, fertiger Bericht) kommen als
  `Fremd(...)`; `als_ereignis` macht daraus ein `Ereignis` mit `herkunft`, und der `Zusteller`
  setzt `Ressourcen.fremder_inhalt` beim Ausliefern, nach dem Aufwecken, damit die Marke zu dem
  Gespräch gehört, in dem der Text landet (siehe Werkzeuge und Freigaben, Fremdschutz).
- Im Gästemodus warten alle Ereignisse, bis der Besuch weg ist (Grund „Gäste da“), außer mit
  `Ereignis.vor_gaesten`: Timer (`Erinnerungen.ansage`) und was im Gästemodus angefordert wurde
  (Ergebnis von `nachdenken`). Mit Push-Text geht Wartendes gleich aufs Telefon, fällige
  Erinnerungen schickt `Erinnerungen.warten` ohnehin als Push. Sonst läse Alfred eine WhatsApp
  wörtlich oder eine Erinnerung samt Arzttermin vor, während ein Gast zuhört.
- Ereignisse haben seit dem 6.10.2026 eine Klasse (`Ereignis`): „angefordert" (Denker fertig,
  Auftrag fertig, Timer, selbst gesetzte Erinnerung; nackter Text, `Ansage` und `Fremd` zählen
  so) kommt außer im Gästemodus immer durch, „ungefragt" (Routinen, später Briefings und Vorschläge) nur nach den
  `Zustellregeln` im Code: Ruhezeit `proaktiv.ruhe_von`/`ruhe_bis` (hält auch Dringendes auf;
  nachts gehört es aufs Telefon), Tagesgrenze `proaktiv.max_pro_tag` (Zähler in
  `gedaechtnis/proaktiv.json`, überlebt Neustarts; Dringendes darf darüber hinaus), je Modus
  `proaktiv.modi` (alles, nur_dringendes, nichts; Standard: feierabend nur Dringendes) und
  Sperren aus `regeln.yaml` („Nicht ungefragt ansagen: <Quelle>", geprüft mit `ist_gesperrt`).
  Zurückgehaltenes bleibt im Zusteller (`_wartend`, überlebt den Neustart der Aufgabe durch
  `beaufsichtigt`) und wird alle 30 s neu geprüft; mit Push-Text geht es stattdessen als Push und
  gilt damit als erledigt; mit `gueltig_bis` verfällt es (Routinen: zwei Stunden nach ihrer Zeit,
  wie das Nachholen).
- Zugestellt ist ein Ereignis erst, wenn Alfred danach hörbar zu sprechen beginnt
  (`Gespraechszustand.sprechbeginne` zählt BotStartedSpeakingFrames), spätestens nach
  `QUITTUNG_SECS` (60 s, danach gilt es trotzdem als zugestellt); bis dahin liefert der Zusteller
  nichts weiter aus. Erst dann zählt Ungefragtes für die Tagesgrenze und erfährt der
  `Ansagewaechter` davon. Der Zusteller beginnt erst, wenn `pipeline_bereit` (pipeline.py) die
  Pipeline als laufend meldet: `on_pipeline_started` und Mikrofon und Lautsprecher brauchbar.
  Pipecat meldet den Start auch, wenn ein Transport beim Einrichten scheiterte, und bricht erst
  danach ab. Endet der Sprach-Loop, legt `Sitzung.abschliessen` über `Zusteller.zurueckgeben`
  Zurückgehaltenes und noch nicht Gesprochenes vorn in die Warteschlange zurück; im Raum hält
  `run_room` sie über den Neuaufbau, denn Erinnerungen (`ausgeliefert`) und Routinen (`zuletzt`)
  gelten schon beim Einreihen als erledigt. Ein Neustart des Prozesses verliert sie weiterhin
  (docs/erkenntnisse.md, 16.10.2026).
- Der `Ansagewaechter` (hinter dem `Rueckfragewaechter`: sonst verwürfe der bei „Nein, lass das"
  gleich die eben angelegte Regel-Rückfrage) erkennt die Antwort auf eine ungefragte
  Ansage im Code: „nicht jetzt" legt sie mit `nicht_vor` neu in die Warteschlange
  (`proaktiv.aufschieben_minuten`), „lass das" oder „nie wieder" legt eine Freigabe für die
  Sperr-Regel an, die erst `bestaetigen` schreibt; dazu ein developer-Hinweis, was der Sprecher
  sagen soll. Wortlisten `NICHT_JETZT`, `NIE_WIEDER`, `FUELLWOERTER`; ein einzelnes Wort wie
  „später" zählt nur in einem Satz bis drei Wörter, und das Antwortfenster schließt nach zwei
  Minuten oder mit dem ersten ganzen Satz zu etwas anderem. Eine Routine mit Uhrzeit in der
  Ruhezeit nennt die Rückfrage beim Anlegen ausdrücklich, sonst wartet Alexander auf eine
  Routine, die ungesprochen verfällt. Nur im Sprach-Prozess; das Textgespräch nimmt aus der
  Warteschlange nur Text und Herkunft (`als_ereignis`).
- Im Browser-Modus laufen Erinnerungen nur, solange eine Verbindung besteht; für den Betrieb
  rund um die Uhr ist der Raum-Modus auf dem Mini-PC gedacht.

## Denker und Aufträge (denker.py, auftraege.py)

- Der Denker läuft als asyncio-Task mit eigener Function-Calling-Schleife (GLM 5.3) und ruft
  für Websuche und Code die Mistral Conversations API mit `mistral-small-2603` und eingebauten
  Werkzeugen auf (`store=False`). Ergebnis und Fehler kommen als Ereignis zurück. Er bekommt
  den Gesprächskontext mit, samt dem Hinweis, dass Alfred den Push ans Handy übernimmt. Der
  Sprecher sagt sofort, dass er nachdenkt, und gibt das Ergebnis in der nächsten Gesprächspause
  in eigenen Worten wieder.
- Aufträge: `auftrag_geben` bestätigt sofort im Code (siehe oben) und legt den Auftrag in die
  YAML-Liste. Der `Auftragsbearbeiter` läuft nur im Sprach-Prozess (Hintergrundaufgabe
  „Aufträge"), einer nach dem anderen: Lebenszeichen alle 30 s; ein laufender Auftrag ohne
  Lebenszeichen seit 180 s wartet wieder; beim Beenden legt der Bearbeiter ihn zurück, und das
  zählt nicht als Versuch; nach drei Abstürzen ist er gescheitert; Abbrechen aus der App sieht
  er beim nächsten Takt (2 s).
- Bericht: `Denker.bericht` mit fester Gliederung (Kurzfassung, Ergebnis, Hinweise, Quellen)
  nach `gedaechtnis/berichte/`, sichtbar im Reiter „Aufgaben" der App. Mistrals Websuche
  liefert Adressen nur als `tool_reference`-Stücke neben dem Text, `_quellen` hängt sie an. In
  der letzten Runde gilt `tool_choice="none"`, damit er mit dem schreibt, was er hat. Die
  Kurzfassung endet an einem Satzende (`_letztes_satzende`); der Vorsatz „Hier ist der Bericht:"
  wird abgeschnitten. Fertige Berichte liest Alfred als Kurzfassung vor (mit Klingel) und
  schickt einen Push; „Was steht im Bericht über …?" beantwortet er aus dem Bericht.

## Websuche (suche.py, seiten.py)

- Der Sprecher sucht direkt im Gespräch mit Ansage („Moment, ich sehe nach"): mit SearXNG aus
  Titeln und Ausschnitten in einer Sekunde, sonst über Mistrals eingebaute Websuche (rund drei
  Cent, einige Sekunden). Fällt SearXNG aus, sucht der Sprecher von selbst wieder bei Mistral.
- Mit `suche.url` (SearXNG) bekommt der Denker: `websuche` (SearXNG, kostenlos, mit
  `nachrichten` = categories=news und `zeitraum` = time_range), `seite_lesen` (seiten.py,
  stdlib-HTMLParser, Text aus article/main, nur der erste `title`-Tag zählt) und `tiefensuche`
  (Mistral, höchstens zweimal je Auftrag, `GRENZEN_BERICHT`). Bei unter drei Treffern der
  Nachrichtensuche sucht `_eigene_suche` allgemein nach.
- Ohne SearXNG bleibt es bei Mistral mit höchstens 8 Suchen je Bericht (`MAX_SUCHEN_BERICHT`)
  und 4 beim Nachdenken. Ist SearXNG nicht erreichbar, meldet `websuche` „Nimm tiefensuche";
  absichtlich gibt es keinen unbegrenzten Rückfall auf Mistral, damit ein Ausfall nichts kostet.
  Die Warnung steht in `logs/fehler.log` und in der App.
- `seite_lesen` liest nur öffentliche Adressen (`is_global`, auch nach jeder Weiterleitung, nur
  Port 80/443). Sonst könnte eine Webseite den Denker auf die App unter 127.0.0.1 lenken, die
  keine Anmeldung hat, oder auf Tailscale-Geräte in 100.64/10. DNS-Rebinding ist nicht
  abgefangen. Webinhalte sind Daten, nie Anweisungen.

## Gedächtnis (gedaechtnis/)

Markdown und YAML in `gedaechtnis.ordner`; der Ordner steht in `.gitignore`, weil er
persönliche Daten enthält (Zielbild: eigene Versionierung mit Git). Beim Ausprobieren immer
`gedaechtnis/selbsttest`, sonst landen Testeinträge im echten Gedächtnis.

- Drei Ebenen: `kontext.md` steht komplett im Systemprompt und ist Alexanders Datei;
  `wissen/<bereich>.md` füllt Alfred mit `merken`; `protokoll/<tag>.md` schreibt er nach jedem
  Gespräch beziehungsweise beim Einschlafen der Schleuse, und das heutige Protokoll steht im
  Prompt. Zusammengefasst wird mit `modelle.werkzeuge` (Mistral Small, ohne Nachdenken), nicht
  mit dem Sprecher: GLM 5.3 dachte die 200 Token voll und schrieb nichts; hört ein Modell vor
  dem Text auf, steht das als Warnung im Log. Die Zusammenfassung lässt weg, was Alfred nicht konnte oder nicht fand; falsche Fakten
  darin von Hand entfernen, sonst wiederholt er sie den ganzen Tag. Offen (6.10.2026): Das
  Protokoll fasst auch fremde Texte zusammen und steht am nächsten Tag im Systemprompt; dagegen
  hilft die Fremdschutz-Marke nicht.
- Über Alexander und den Betrieb: `profil.yaml` (Fakten, komplett im Prompt), `regeln.yaml`,
  `feedback.md`, `aktionen.log`, `kosten.json`, `mitschrift.json`.
- Werkzeugdaten: `erinnerungen.json`, `routinen.yaml`, `aufgaben.yaml`, `kontakte.yaml`
  (Nummern, Mailadressen, Geburtstage), `geburtstage_erinnert.json`, `freigabe.json`, die
  Auftragsliste, `berichte/`, Einkaufsliste, `ernaehrung/`, `ausgaben/`, `vorrat.yaml`,
  `wochenplan.yaml`; `.eingespielt` markiert eine eingespielte Sicherung.
- Zugänge liegen unter `config/` außerhalb von Git: `google_client_secret.json`,
  `google_token.json`, `whatsapp.db`, `whatsapp_bridge.token`, `stimme.json`,
  `spotify_token.json`, `stimmen/`. Geheimnisse (`MISTRAL_API_KEY`, `MAIL_ADRESSE`,
  `MAIL_PASSWORT`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`) nur in `.env`.

## Audio-Geräte

- Raum-Geräte stehen unter `raeume` in `config/alfred.yaml`. Unter Windows führt PortAudio
  jedes Gerät mehrfach (MME, DirectSound, WASAPI, WDM-KS); `waehle_geraet` (room.py) nimmt den
  MME-Eintrag, den einzigen, der im blockierenden Betrieb zuverlässig abspielt (DirectSound
  nimmt Audio an und gibt nichts aus, WASAPI und WDM-KS öffnen bei 16/24 kHz nicht). MME kürzt
  Namen auf 31 Zeichen, deshalb gleicht `waehle_geraet` über den vollen Namen eines anderen
  Eintrags ab. Unter Linux läuft das Jabra ohne Treiber als USB-Audiogerät; Ubuntu Server ohne
  Desktop, weil PipeWire oder PulseAudio den Direktzugriff blockieren können (der Hörtest zeigt
  das).
- PortAudio liest die Geräte nur beim Initialisieren ein, eine weitere Instanz nicht neu, solange
  eine andere lebt; ein MME-Gerät öffnet es über die Nummer, die Windows ihm damals gab. Pipecats
  `LocalAudioTransport` gibt seine Instanz nie frei, deshalb ruft `_einmal_laufen` (room.py) am
  Ende `terminate`: Jeder Neustart der Aufsicht liest die Geräte frisch ein. Direkt vor dem Start
  vergleicht `_geraete_pruefen` PortAudios MME-Liste mit der Nummerierung, die Windows jetzt hat
  (`geraeteliste_veraltet`, winmm über ctypes). Weicht sie ab, etwa weil kurz nach dem Hochfahren
  noch Geräte hinzukommen, bricht der Anlauf ab und die Aufsicht liest neu ein; nach drei Abbrüchen
  in Folge startet Alfred trotzdem (docs/erkenntnisse.md, 16.10.2026).
- Fällt ein Gerät im Betrieb aus, merkt Pipecats `LocalAudioTransport` das nicht: Der Ausgang loggt
  jeden gescheiterten Schreibversuch und läuft weiter (PyAudio schließt den Stream nach dem ersten
  Fehler selbst, danach kommt nur noch „Stream closed"), der Eingang bekommt schlicht keine Rückrufe
  mehr. Der Raum-Modus nimmt deshalb `WatchedLocalAudioTransport` (local_audio.py). Der Ausgang
  meldet sich, sobald ein Schreibfehler den Stream geschlossen hat (`stream_lebt`); ein Fehler bei
  laufendem Stream bleibt Pipecats Einzelfall im Log. Der Eingang hat einen Wächter im
  Sekundentakt: Hält PortAudio den Stream an oder kommt fünf Sekunden kein Rückruf
  (`OHNE_AUDIO_SECS`; die Zeit misst der Rückruf in PortAudios Thread, eine hängende
  Ereignisschleife löst also nichts aus), meldet er sich. Scheitert schon `start_stream`, melden
  sich beide sofort; Pipecat nähme das sonst als vorübergehend. Gemeldet wird einmal, als unbrauchbar
  (`force_treat_as_permanent`); der Worker bricht ab (`ProcessorUnusablePolicy.CANCEL`), die
  Aufsicht startet neu und liest die Geräte frisch ein. Einen geschlossenen Ausgang gibt
  `cleanup` nur noch frei, ohne `stop_stream` (sonst „Error cleaning up … Stream not open").
- Anforderungen an das Gerät: Mikrofon mit 16 kHz mono, Lautsprecher, Echo-Unterdrückung (sonst
  hört Alfred sich selbst, hält das für einen Einwurf und unterbricht sich nach drei Wörtern).
  Konferenzlautsprecher machen das in Hardware.
- LED am Jabra Speak 510 (`jabra_led.py`, `jabra.led_aktiv`): Sie folgt dem Zustand der
  Mitschrift (`Mitschrift.bei_zustand`, `JabraLedController.zeigen`): aus bei `schlaeft`,
  blinkend bei `denkt` und `arbeitet: …` (`jabra.blinken_sekunden`, höchstens zehn Sekunden,
  denn ohne Antwort bliebe `denkt` bis zum Einschlafen stehen), sonst grün. Nur der Raum-Prozess
  schaltet sie (`room.py` setzt `Sitzung.jabra`, damit `abschliessen` sie ausschaltet); sie
  startet aus. Ein eigener Thread schreibt den HID-Telephony-Report 0x03, im Takt beim Blinken,
  sonst nur bei einer Änderung; Fehler stehen nur im Log, das Beenden wartet höchstens zwei
  Sekunden auf USB. Das Gerät liest den Report als Anrufzustand: Mute, Ring, Hold und Microphone
  (der rote Ring) schalten das Mikrofon stumm, deshalb lässt `JabraDevice.write_report` nur
  Off-Hook durch; Off-Hook im Takt stört weder Mikrofon noch Lautsprecher (docs/erkenntnisse.md,
  9.10.2026). Gefunden wird es über die USB-Kennung (hidapi, unter Linux notfalls
  `/sys/class/hidraw`); unter Linux gibt `deploy/99-jabra.rules` der Gruppe audio Zugriff.
- Mehrere Räume: je Raum ein Eintrag unter `raeume` und ein eigener Prozess
  (`alfred-raum@<name>`); die Antwort kommt dort, wo das Wachwort gefallen ist. Ein
  Gesprächsverlauf über Raumgrenzen hinweg ist noch nicht gebaut.

## Pipecat

Installiert ist Pipecat 1.12. Die API hat sich gegenüber älteren Versionen stark geändert
(`PipelineWorker` und `WorkerRunner` statt `PipelineTask` und `PipelineRunner`, Turn-Strategien
am User-Aggregator). Vor neuem Code die Quellen unter `.venv/Lib/site-packages/pipecat` lesen,
nicht aus dem Gedächtnis schreiben. Eigene Processors lassen sich mit
`pipecat.tests.utils.run_test` testen, je Lauf mit einem frischen Processor.

- `MistralSTTService` schickt jedes Audio-Frame an Mistral, solange die Pipeline läuft (0,006 $
  pro Minute, im Dauerbetrieb rund 8,60 $ am Tag); deshalb sitzt im Raum-Modus die Schleuse
  davor, und bei geschlossener Schleuse ruht die Verbindung.
- Wirft ein Processor in `setup()` oder meldet er einen Fehler mit
  `force_treat_as_permanent=True`, läuft die Pipeline standardmäßig weiter und markiert ihn nur
  als unbrauchbar: Raum-Modus mit `ProcessorUnusablePolicy.CANCEL`. Einen Fehler in
  `process_frame` meldet Pipecat als vorübergehend, einen beim Schreiben auf den Ausgang
  (`MediaSender._audio_task_handler`) loggt es nur; beide Male läuft die Pipeline weiter. Wer
  abbrechen will, muss sich als unbrauchbar melden (local_audio.py). `PipelineWorker`
  beendet sich nach 300 s ohne Gespräch: im Raum-Modus abgeschaltet. Der Runner läuft mit
  `handle_sigint=False, handle_sigterm=False`, weil die Aufsicht die Signale behandelt.
- Eigene Services statt der Pipecat-Originale: `FinalizingMistralSTTService` (Transkripte
  endgültig markieren, Verbindung mit `ruhen` schließen), `CachingMistralTTSService`
  (Phrasen-Cache, neue Versuche ohne Ton, `stop_frame_timeout_s` 20 s statt 3 s) und im Raum
  `WatchedLocalAudioTransport` (toter Audio-Stream beendet den Anlauf, Abschnitt „Audio-Geräte").
- `EmptyUserTurnConfig` mit deutschem `interrupted_prompt` und `idle_prompt` None;
  `user_turn_stop_timeout` 5 s beendet verworfene Züge still.
- `developer`-Nachrichten werden zu Nutzernachrichten, weil Mistral die Rolle nicht kennt.
- Der Start-Banner enthält Unicode (UTF-8 in `bot.py`); Pipecats `main()` entfernt alle
  Logger-Senken (`logbuch.einrichten` erst in `bot()`).
- `run_test` verträgt keinen zweiten Lauf mit demselben Processor und hängt dann; die
  Testsuite deshalb mit Zeitlimit starten.

## Deploy (deploy/)

- `einrichten.sh --sicherung ZIP` richtet den Mini-PC ein, `aktualisieren.sh` holt den
  neuesten Stand (Pakete, Dienste, SearXNG-Abbild, Neustart, Prüfung); `gemeinsam.sh` teilt
  Funktionen. systemd-Dienste: `alfred-raum@<raum>`, `alfred-browser`, `alfred-web`,
  `alfred-whatsapp`, `alfred-sicherung.service` mit `alfred-sicherung.timer`
  (`deploy/sichern.sh`, auf Wunsch per `scp` nach `SICHERUNG_ZIEL`). Am 5.10.2026 auf dem
  Mini-PC gelaufen, soweit kein sudo nötig ist; Dienste und Tailscale noch nicht.
  `absichern.sh` sichert den Rechner selbst ab (siehe unten).
- Das Skript fragt nur nach dem Mistral-Schlüssel (`read -s`, wenn die `.env` fehlt), nach der
  Tailscale-Anmeldung und nach dem WhatsApp-Koppeln. Alles andere macht es selbst: Pakete,
  venvs, Zeitzone, Gruppe `audio`, Sicherung einspielen (dieselbe nur einmal, Marke
  `gedaechtnis/.eingespielt`); SearXNG-Container, danach `suche.url`, aber nur, wenn SearXNG
  JSON liefert; Tailscale mit `--accept-dns=false` (sonst hängt DNS beim Booten an tailscaled)
  und `tailscale set --auto-update` (Ubuntus automatische Updates kennen
  Tailscales Paketquelle nicht), eine Warnung, wenn ein anderer Dienst oder Container 443 auf allen
  Adressen will (`alfred.absichern docker-belegt`; `tailscale serve` belegt 443 auf den
  Tailscale-Adressen, und wer nach einem Neustart später kommt, startet nicht),
  `ufw allow in on tailscale0` bei aktiver Firewall (auch für das
  UDP des Browser-Modus), `tailscale serve /app` mit sichtbarer Ausgabe (beim ersten Mal ein Link,
  um HTTPS für das Tailnet einzuschalten), `benachrichtigung.app_url`; ein ablaufender
  Tailscale-Schlüssel kommt in die Liste am Ende (`einrichtung tailscale-ablauf`; die Startprüfung
  warnt ebenso). Dienste
  installieren, prüfen und erst dann starten, wenn die Prüfung kein ✘ zeigt. Es lässt sich nach
  jedem Fehler neu starten; am Ende zeigt `alfred.pruefen`, was fehlt. Das sudo-Passwort fragt
  es gleich am Anfang ab (`sudo -v`), nicht erst nach dem ersten langen Schritt; Docker läuft
  ohne sudo, wenn der Nutzer in der Gruppe docker ist. Nach dem Aufnehmen in die Gruppe audio
  steht „neu anmelden" in der Liste am Ende: Die Dienste bekommen die Gruppe von systemd, die
  eigene Shell erst nach einer neuen Anmeldung. Die Startprüfung läuft bis dahin über
  `sg audio`, sonst fände sie das Mikrofon nicht, und Alfred startete beim ersten Lauf nicht.
- Was es in Dateien schreibt, steht in `alfred.einrichtung` (Python, getestet), nicht in sed.
  Geheimnisse kommen über die Umgebungsvariable ALFRED_WERT, nicht über die Kommandozeile.
- Der Dienst startet nach Absturz oder Netzausfall von selbst neu und beendet sich bei `stop`
  sauber samt Tagesprotokoll. Die `.env` ist in keiner Sicherung und wird getrennt kopiert.
- Umzug: Eine andere Sicherung als die in `gedaechtnis/.eingespielt` ersetzt nach Rückfrage den
  Stand. Das Skript hält die Alfred-Dienste an, verschiebt `gedaechtnis/` und kopiert `config/`
  nach `~/alfred-vor-umzug-<Zeit>`, dann spielt es mit `--ueberschreiben` ein. Verschieben statt
  Überschreiben, weil sonst im alten Gedächtnis Dateien blieben, die auf dem alten Rechner längst
  gelöscht sind. Ohne Terminal bleibt der alte Stand, und die Liste am Ende sagt, was zu tun ist.
- `aktualisieren.sh` holt nur mit Upstream (`git pull --ff-only`). Ohne Upstream kommt der Code per
  `git push minipc` vom Windows-Rechner (`receive.denyCurrentBranch=updateInstead`), denn das
  Repository ist privat und der Server hat keinen Deploy-Schlüssel bei GitHub (Stand 16.10.2026).
- `absichern.sh [--offen PORT] [--heimnetz PORT] [--neustart HH:MM | --ohne-neustart]` sichert
  den Rechner ab und lässt sich wiederholen:
  - Heimnetz heißt das IPv4-Netz an der Schnittstelle mit der Standardroute plus deren private
    IPv6-Netze (`fd00::/8`). Das öffentliche IPv6-Präfix wechselt beim Anbieter und zählt deshalb
    nicht dazu.
  - ufw: Eingehend ist nur noch erlaubt: SSH (Port aus `sshd -T`) aus dem Heimnetz, alles über
    `tailscale0`, 41641/udp für direkte Tailscale-Verbindungen, `--heimnetz`-Ports aus dem Heimnetz
    und `--offen`-Ports für alle.
  - Regeln, die SSH oder einen `--heimnetz`-Port für jeden Absender öffnen, findet
    `alfred.absichern breite-regeln` in `LC_ALL=C ufw status numbered`. Das Modul braucht nur die
    Standardbibliothek, getestet in `tests/test_absichern.py`. Die Regeln liefert es mit der höchsten
    Nummer zuerst, damit `ufw delete N` der Reihe nach stimmt. Gelöscht wird erst nach Rückfrage.
  - Ist die eigene SSH-Sitzung (`SSH_CLIENT`) weder aus dem Heimnetz noch aus dem Tailnet, bleiben
    die SSH-Regeln stehen; sonst sperrte sich der Aufrufer aus.
  - `/etc/apt/apt.conf.d/52absichern` schaltet die täglichen Sicherheitsupdates ein und den
    Neustart, wenn eins ihn braucht. Die Datei steht nach `50unattended-upgrades` und gilt deshalb
    zuletzt.
  - `/etc/ssh/sshd_config.d/10-absichern.conf` erlaubt nur Schlüssel, nie root und kein X11. sshd
    nimmt den ersten gelesenen Wert, und 10- steht vor `50-cloud-init.conf`, das bei manchen
    Installationen Passwörter erlaubt. Ohne `authorized_keys` und bei einem Fehler in `sshd -t`
    bleibt alles, wie es war.
  - Docker-Ports mit `-p` umgehen ufw bei IPv4 (NAT vor der INPUT-Kette). In Netzen ohne IPv6
    nimmt `docker-proxy` die IPv6-Verbindungen an, und die laufen durch ufw (am Mini-PC gemessen,
    docs/erkenntnisse.md, 16.10.2026). Das Skript listet deshalb die Docker-Ports, die im Heimnetz
    offen sind, und nennt am Ende die Router-Freigaben, auf die es ankommt.

## Selbsttest (selftest.py)

- `python -m alfred.selftest [aufnahme.wav ...] [--nachhall SEK] [--raum] [--leerlauf SEK]`
  spielt gesprochene Testsätze in den echten Sprach-Loop ohne Mikrofon und Lautsprecher, zeigt
  Transkript, Werkzeugaufrufe und Antwort und misst die Zeit vom letzten Wort bis zum ersten
  Ton. Er kostet einen Bruchteil eines Cents.
- Mehrere Aufnahmen (16 kHz, mono) sind ein Gespräch (Auftrag, Rückfrage, Ja); der nächste Satz
  startet eine Sekunde nach dem Ende der Antwort. Das Ende erkennt der Test an Modell und
  Sprachausgabe, nicht an `TTSStoppedFrame`, denn der kommt nach jedem Satz; seine Frist läuft
  bei jeder Regung des Sprechers (Werkzeug, Modell, Ton) weiter.
- `--raum` schickt den ersten Satz durch die Wachwort-Schleuse und wartet, bis sie wieder
  geschlossen hat; `--leerlauf SEK` schickt vorher Stille und prüft so den Dauerbetrieb;
  `--nachhall SEK` hält die Sitzung offen, um zu hören, was Alfred von sich aus sagt.
- Er benutzt `gedaechtnis/selbsttest` und leert den Ordner bei jedem Lauf, sonst beantwortet
  das Modell Fragen aus dem Protokoll des vorigen Laufs. Live-Tests von Anbindungen liefen mit
  Fake-WhatsApp und Fake-Push gegen denselben Ordner.
- Testaufnahmen unter Windows mit der Sprachsynthese „Microsoft Hedda" (16 kHz, mono), Sätze
  ohne Kommas, sonst zerfällt ein Auftrag in mehrere Züge.
- Unter Windows startet er wie der Raum die WhatsApp-Brücke, wenn `whatsapp.bruecke_starten` an
  ist, mit derselben Kopplung wie der Mini-PC (20.10.2026). Deshalb dort mit einer Kopie von
  `alfred.yaml` und `persoenlichkeit.yaml` laufen lassen: `ALFRED_CONFIG_DIR=<kopie>/config`
  macht deren Elternordner zum Projektordner, ohne Brücken-Token, mit eigenem Gedächtnis und
  eigenen Logs; die `.env` findet python-dotenv trotzdem.

## Eval-Suite (eval.py)

- `python -m alfred.eval [NAME ...] [--wiederholungen N] [--modell MODELL] [--szenarien DATEI]
  [--liste]` schickt kurze Gespräche auf Textebene durch `Textgespraech` an das echte
  Sprecher-Modell: gleicher Systemprompt, gleiche Werkzeuge, gleicher Code für Freigaben, kein
  Audio. Geprüft wird je Satz, welche Werkzeuge das Modell mit welchen Argumenten aufruft
  (reguläre Ausdrücke, Zahlen als Zahl), ob es keins aufruft, wo es keins soll, was in der Antwort
  steht, ob die Antwort vom Werkzeug kam (`run_llm=False`) und ob danach eine Rückfrage offen ist.
  Text, den das Modell neben Werkzeugaufrufe schreibt, zeigt der Bericht als „Vorab"; beim
  Sprechen liest Pipecat ihn vor dem Werkzeug vor, bei `antwort_vom_werkzeug` ist er ein Mangel.
- Szenarien in `tests/eval_szenarien.yaml`, Format im Kopf der Datei: je Szenario Profil,
  Kontakte, Termine und Mails der Attrappen, auf Wunsch ein `codewort` (dann verlangen heikle
  Aktionen es wie im Raum, und jeder Satz kommt maskiert an), dann die Sätze mit Erwartungen (`werkzeuge`,
  `kein_werkzeug`, `erlaubt`, `verboten`, `antwort_enthaelt`, `antwort_nicht`,
  `antwort_vom_werkzeug`, `freigabe_offen`, `freigabe_enthaelt`). Platzhalter wie
  `{naechster:Mittwoch}` folgen der Wochentagsliste im Prompt. Der Lader prüft streng
  (unbekannte Werkzeuge, kaputte Muster), bevor etwas kostet.
- Attrappen für WhatsApp (Adressbuch mit „Jan Müller", „Mama", „Lea Schulz"), Mail, Push,
  Kalender, Wetter (echter Wetter-Code mit festen Netzantworten), Denker und Schätzer: Nichts geht
  nach außen, nur der Sprecher kostet. Die Werkzeugliste ist damit die des Vollausbaus. Gedächtnis
  ist `gedaechtnis/selbsttest`, vor jedem Szenario geleert; ein Schutz leert nur Ordner dieses
  Namens.
- Ein Mitschreiber vor dem Mistral-Client zählt Token je Aufruf und merkt sich, ob die letzte
  Modellantwort ein Werkzeugaufruf war. Die Bilanz nennt Trefferquote, Dauer, Modellaufrufe, Token
  und Kosten (`modellkosten` in kosten.py mit `preise.modelle`; ohne Preis sagt sie das).
  Rückgabewert 1, wenn ein Szenario scheitert; mit `--wiederholungen N` gilt ein Szenario nur als
  bestanden, wenn alle Läufe bestehen. Die Auswertung ohne Modell testet `tests/test_eval.py`,
  auch ganze Läufe mit einem Modell nach Drehbuch.

## Konfiguration (config.py)

- `config/alfred.yaml` ist die eigene Datei jedes Rechners, nicht im Git; im Repository liegt
  die Vorlage `config/alfred.beispiel.yaml`. `load_settings` nimmt die Vorlage, wenn die eigene
  Datei fehlt, und `konfig_anlegen` kopiert sie vor dem ersten Schreiben. Neue Einstellungen
  bekommen ihren Standard in config.py und ihre Erklärung in der Vorlage.
- Dasselbe gilt für `config/persoenlichkeit.yaml` mit der Vorlage
  `config/persoenlichkeit.beispiel.yaml`: `persona_pfad` liefert die eigene Datei oder die
  Vorlage, `load_persona` liest den Pfad. Der Pull auf dem Mini-PC sichert die eigene Datei vorher
  (`deploy/aktualisieren.sh`), weil sie seit diesem Stand nicht mehr im Git liegt.
- Abschnitte: `stimme`, `modelle`, `gespraech` (`begruessung`, `unterbrechen_ab_woertern`,
  `stoppwoerter`, `vergessen_nach_minuten`, `pause_max_sekunden`, `sprachen`), `wachwort`
  (`phrasen`, `offen_sekunden`, `gespraech_sekunden`, `modell`), `raeume`, `gedaechtnis`,
  `kalender`, `wetter`, `whatsapp`, `benachrichtigung`, `suche`, `spotify`, `stimmerkennung`,
  `preise`, `proaktiv` (`max_pro_tag`, `ruhe_von`, `ruhe_bis`, `aufschieben_minuten`, `modi`).
- Persönlichkeit in `config/persoenlichkeit.yaml` (Charakter, sechs Regler von 0 bis 10,
  Regeln, Modi, aktiver Modus); jeder Regler hat vier Stufen in `STUFEN` (persona.py) und
  erreicht das Modell als Verhaltenssatz, nie als Zahl. Änderungen wirken beim nächsten Start.
