// Zentrale: Alfreds Zustand, der Tag, laufende Aufträge, Fähigkeiten und Anbindungen auf einen Blick.
import {
  h, icon, api, store, orb, orbSetzen, faehigkeitenLaden, imChatVorbereiten, imChatAufnehmen, uhrzeit, relativ, vergangen, datumLang, tageszeitGruss,
  faelligkeit, countdown, sekundentakt, zahl, plural, leeren, zaehlerLaden, fehler, lokalesDatum, tageBis, juengste,
} from "../core.js";
import {
  kartenKopf, leer, skelett, auftragBadge, AUFTRAG, auftragOffen, checkKnopf, DIENSTE, dienstZustand, aktion, fehlerEintrag,
  zugZeile, zustandBadge, STUFEN,
} from "../teile.js";

const alleLink = (ziel, text = "Alle") => h("a", { class: "btn ghost sm", href: `#/${ziel}` }, text, icon("chevron-right"));

export function mount(wurzel) {
  let heute = null;
  let faehig = null;
  const marke = juengste();
  // Jeder Bereich zeichnet sich nur neu, wenn sich seine Daten ändern (und einmal je Minute wegen der „vor …“-Zeiten):
  // Sonst springt der Tastaturfokus beim Takt weg, und ein Klick zwischen zwei Neuzeichnungen geht verloren.
  const gesehen = {};
  const minute = () => Math.floor(Date.now() / 60000);
  function einmal(name, stand, zeichnen) {
    const merke = JSON.stringify(stand);
    if (gesehen[name] === merke) return;
    gesehen[name] = merke;
    zeichnen();
  }

  // --- Kopf: Orb, Gruß, Zustand, Eingabe --------------------------------------------------------------------
  const heroOrb = orb("lg");
  const titel = h("h2", { class: "hero-title" }, tageszeitGruss());
  const datum = h("div", { class: "overline" }, datumLang());
  const zustand = h("div", { class: "hero-state" });
  const specs = h("div", { class: "hero-specs" });
  const zeilen = h("div", { class: "hero-lines" });
  const eingabe = h("input", { type: "text", placeholder: "Frag Alfred oder gib ihm einen Auftrag …", autocomplete: "off", enterkeyhint: "send", "aria-label": "Nachricht an Alfred" });
  const vorschlaege = h("div", { class: "row wrap", style: { "--gap": "8px" } });
  const hero = h("section", { class: "card hero z-8" },
    h("div", { class: "hero-orb" }, heroOrb),
    h("div", { class: "hero-body" }, datum, titel, zustand, specs, zeilen,
      h("form", { class: "composer", onsubmit: (ev) => {
        ev.preventDefault();
        const text = eingabe.value.trim();
        if (text) { eingabe.value = ""; imChatVorbereiten(text, true); }
      } }, icon("sparkles"), eingabe,
        h("button", { class: "btn ghost icon", type: "button", "aria-label": "Sprachnachricht aufnehmen", title: "Sprachnachricht aufnehmen", onclick: () => imChatAufnehmen() }, icon("mic")),
        h("button", { class: "btn primary icon", type: "submit", "aria-label": "Senden" }, icon("arrow-up"))),
      vorschlaege));

  // --- Kennzahlen ---------------------------------------------------------------------------------------------
  const kennzahlen = h("div", { class: "kpi-grid z-4" });

  // --- Karten ---------------------------------------------------------------------------------------------------
  const planKarte = h("section", { class: "card z-4" }, skelett(3));
  const auftraegeKarte = h("section", { class: "card z-4 breit-m" }, skelett(3));
  const aufgabenKarte = h("section", { class: "card z-4" }, skelett(3));
  const faehigKarte = h("section", { class: "card z-8" }, skelett(3, 56));
  const dienstKarte = h("section", { class: "card z-4" }, skelett(4, 34));
  const aktivitaetKarte = h("section", { class: "card z-6" }, skelett(4, 40));
  const mitschriftKarte = h("section", { class: "card z-6 breit-m" }, skelett(3));

  wurzel.append(
    h("div", { class: "z-grid" }, hero, kennzahlen, planKarte, aufgabenKarte, auftraegeKarte, faehigKarte, dienstKarte, aktivitaetKarte, mitschriftKarte));

  // --- Live-Zustand -----------------------------------------------------------------------------------------------
  function liveZeichnen(l) {
    const zeitAntwort = l?.antwortzeit ? `letzte Antwort nach ${String(l.antwortzeit).replace(".", ",")} s` : "";
    leeren(zustand, zustandBadge(l), l?.erreichbar && l.quelle ? h("span", { class: "muted" }, [l.quelle, zeitAntwort].filter(Boolean).join(" · ")) : null);
    const stand = [];
    if (!l || !l.erreichbar) {
      stand.push(h("p", { class: "muted", style: { fontSize: "var(--fs-sm)" } },
        "Alfreds Sprach-Prozess läuft gerade nicht, deshalb hört er nicht zu. Schreiben und alle Listen der App gehen trotzdem."));
    } else {
      if (l.verstanden) stand.push(h("div", { class: "hero-line" }, h("b", null, "Du"), h("span", null, l.verstanden.text), h("time", null, vergangen(l.verstanden.zeit))));
      if (l.gesagt) stand.push(h("div", { class: "hero-line alfred" }, h("b", null, "Alfred"), h("span", null, l.gesagt.text), h("time", null, vergangen(l.gesagt.zeit))));
      if (!stand.length) stand.push(h("p", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, "Noch nichts gesprochen."));
    }
    leeren(zeilen, ...stand);
  }

  function live(l) {
    orbSetzen(heroOrb, l);
    if (!l) { leeren(zustand, h("span", { class: "badge" }, "…")); return; }  // vor der ersten Antwort nichts behaupten
    einmal("live", [l.erreichbar, l.beschreibung, l.quelle, l.antwortzeit, l.verstanden, l.gesagt, minute()], () => liveZeichnen(l));
    einmal("mitschrift", [(l.zuege || []).slice(-6), minute()], () => mitschrift(l));
  }

  function mitschrift(l) {
    const zuege = (l?.zuege || []).slice(-6);
    leeren(mitschriftKarte, kartenKopf("Mitschrift", "mic", h("span", { class: "card-sub" }, "was Alfred gehört und gesagt hat"), alleLink("system", "System")),
      zuege.length ? h("div", { class: "zuege" }, ...zuege.map(zugZeile)) : leer("mic", "Noch nichts gesprochen", "Sobald Alfred etwas hört, steht es hier."));
  }

  // --- Heute: Kennzahlen, Plan -------------------------------------------------------------------------------------
  function kachel(symbol, label, wert, sub, ziel, art = "") {
    return h("a", { class: "stat interactive", href: `#/${ziel}` },
      h("div", { class: "stat-label" }, icon(symbol), label), h("div", { class: `stat-value ${art}` }, wert), h("div", { class: "stat-sub truncate" }, sub));
  }

  function kennzahlenZeigen() {
    const z = store.get("zaehler") || {};
    const jetzt = Date.now();
    const termine = (heute?.kalender || []).filter((t) => t.beginn.slice(0, 10) === lokalesDatum() && new Date(t.ende) > jetzt);
    const naechster = termine[0];
    leeren(kennzahlen,
      kachel("calendar", "Termine heute", heute ? termine.length : "–", naechster ? `${naechster.ganztags ? "ganztägig" : uhrzeit(naechster.beginn)} ${naechster.titel}` : "nichts mehr offen", "heute"),
      kachel("check-square", "Aufgaben", z.aufgaben ?? "–", z.ueberfaellig ? `${z.ueberfaellig} überfällig` : "nichts überfällig", "aufgaben", z.ueberfaellig ? "warn" : ""),
      kachel("shopping-cart", "Einkauf", z.einkauf ?? "–", z.einkauf ? "Artikel offen" : "Liste ist leer", "einkauf"),
      kachel("layers", "Aufträge", z.auftraege ?? "–", z.auftraege ? "arbeitet Alfred ab" : "keine offen", "auftraege"));
  }

  const kennzahlenAktualisieren = () => einmal("kpi", [store.get("zaehler"), heute?.kalender, minute()], kennzahlenZeigen);

  function timerPille(e) {
    return h("span", { class: "timer-pill", "data-faellig": e.faellig, title: e.text }, icon("timer"), h("b", { class: "num" }, countdown(e.faellig)), h("span", null, e.text.replace(/^Timer: /, "")));
  }

  function planZeigen() {
    if (!heute) return;
    const jetzt = Date.now();
    const termine = heute.kalender.filter((t) => new Date(t.ende) > jetzt).slice(0, 4);
    const timer = heute.erinnerungen.filter((e) => e.timer);
    const erinnerungen = heute.erinnerungen.filter((e) => !e.timer).slice(0, 3);
    const zeile = (symbol, text, meta, hervor) => h("div", { class: `item${hervor ? " now" : ""}` }, h("span", { class: `lead${hervor ? " accent" : ""}` }, icon(symbol)),
      h("div", { class: "item-main" }, h("div", { class: "item-title truncate" }, text)), h("span", { class: "item-meta" }, meta));
    const eintraege = [
      ...termine.map((t, i) => zeile("calendar", t.titel, t.ganztags ? (tageBis(t.beginn) === 0 ? "ganztägig" : "morgen ganztägig") : new Date(t.beginn) <= jetzt ? "läuft" : `${uhrzeit(t.beginn)} · ${relativ(t.beginn)}`, i === 0)),
      ...erinnerungen.map((e) => zeile("bell", e.text, relativ(e.faellig), false)),
    ];
    leeren(planKarte, kartenKopf("Als Nächstes", "clock", alleLink("heute")),
      timer.length ? h("div", { class: "row wrap", style: { "--gap": "8px", marginBottom: "10px" } }, ...timer.map(timerPille)) : null,
      eintraege.length ? h("div", { class: "list inset" }, ...eintraege) : leer("sun", "Nichts mehr heute", "Keine Termine und Erinnerungen in Sicht."));
  }

  function aufgabenZeigen() {
    if (!heute) return;
    const offene = [...heute.aufgaben].sort((a, b) => (a.faellig || "9999").localeCompare(b.faellig || "9999")).slice(0, 5);
    leeren(aufgabenKarte, kartenKopf("Aufgaben", "check-square", alleLink("aufgaben")),
      offene.length ? h("div", { class: "list inset" }, ...offene.map((a) => {
        const f = faelligkeit(a.faellig);
        const zeile = h("div", { class: "item" },
          checkKnopf(false, async () => {
            zeile.classList.add("weg");
            try { await api("aufgaben", "PATCH", { nummer: a.nummer }); } catch (e) { fehler(e); }
            laden();
            zaehlerLaden();
          }, `„${a.text}“ erledigt`),
          h("div", { class: "item-main" }, h("div", { class: "item-title" }, a.text)),
          f.text ? h("span", { class: `badge ${f.art}` }, f.text) : null);
        return zeile;
      })) : leer("check-square", "Alles erledigt", "Keine offenen Aufgaben."));
  }

  function auftraegeZeigen(daten) {
    const liste = [...daten.auftraege].sort((a, b) => Number(auftragOffen(b)) - Number(auftragOffen(a)) || b.nummer - a.nummer).slice(0, 4);
    leeren(auftraegeKarte, kartenKopf("Aufträge", "layers", alleLink("auftraege")),
      liste.length ? h("div", { class: "list inset" }, ...liste.map((a) => {
        const s = AUFTRAG[a.status] || AUFTRAG.wartet;
        return h("a", { class: "item", href: `#/auftraege/${a.nummer}`, style: { color: "inherit", textDecoration: "none" } },
          h("span", { class: `lead ${s.art}` }, icon(s.symbol, a.status === "laeuft" ? "spin" : "")),
          h("div", { class: "item-main" }, h("div", { class: "item-title truncate" }, a.titel), h("div", { class: "item-sub" }, `${s.text} · ${vergangen(a.beendet || a.begonnen || a.angelegt)}`)),
          icon("chevron-right", "dim"));
      })) : leer("layers", "Keine Aufträge", "Sag: „Analysier …, mach mir einen Bericht mit Quellen.“"));
  }

  // --- Fähigkeiten --------------------------------------------------------------------------------------------------
  function faehigkeitenZeigen() {
    if (!faehig) return;
    const s = faehig.summe;
    const kann = s.bereit + s.nur_stimme;
    const modus = faehig.modi.find((m) => m.aktiv) || faehig.modi[0];
    const chip = (symbol, text, titel, mono = false) => h("a", { class: "badge outline", href: "#/faehigkeiten", title: titel }, icon(symbol), h("span", { class: mono ? "mono" : "" }, text));
    leeren(specs, chip("sliders", `Modus ${modus.name[0].toUpperCase()}${modus.name.slice(1)}`, "Aktiver Modus laut Konfiguration"),
      chip("cpu", faehig.modelle.sprecher, "Sprecher-Modell: führt das Gespräch", true), chip("tool", `${modus.werkzeuge} Werkzeuge`, "So viele Werkzeuge sieht Alfred in diesem Modus"));
    leeren(faehigKarte, kartenKopf("Was Alfred kann", "sparkles", alleLink("faehigkeiten", "Alle Fähigkeiten")),
      h("div", { class: "cap-head" },
        h("div", { class: "cap-big" }, h("b", { class: "num" }, kann), h("span", null, `von ${s.gesamt} Werkzeugen einsatzbereit`)),
        h("div", { class: "cap-levels" }, ...["lesend", "schreibend", "bestaetigen"].map((st) =>
          h("span", { class: "badge lvl", "data-lvl": st, title: STUFEN[st].erklaerung }, icon(STUFEN[st].symbol), `${s.stufen[st]} ${STUFEN[st].text}`)))),
      h("div", { class: "meter", style: { "--v": (100 * kann) / s.gesamt, margin: "12px 0 16px" } }, h("i")),
      h("div", { class: "cap-grid" }, ...faehig.gruppen.map((g) => {
        const k = g.bereit + g.nur_stimme;
        const art = k === 0 ? "off" : g.fehlt ? "warn" : "ok";
        return h("a", { class: `cap-tile${g.im_modus ? "" : " aus"}`, href: `#/faehigkeiten/${g.id}`, title: g.beschreibung },
          h("span", { class: "cap-icon" }, icon(g.symbol)),
          h("span", { class: "cap-name" }, g.titel),
          h("span", { class: "cap-count num" }, `${k}/${g.werkzeuge.length}`),
          h("span", { class: `cap-dot ${art}` }));
      })),
      s.fehlt ? h("p", { class: "hint", style: { marginTop: "14px" } }, icon("info"), ` ${plural(s.fehlt, "Werkzeug braucht", "Werkzeuge brauchen")} noch eine Anbindung (grau oder gelb markiert).`) : null);
  }

  // --- Anbindungen, Kosten, Aktivität --------------------------------------------------------------------------------------
  function statusZeigen(st) {
    const k = st.kosten;
    const kosten = k && k.verbrauch !== "nichts"
      ? h("div", { class: "kosten" }, h("div", null, h("div", { class: "overline" }, "Kosten heute"), h("div", { class: "kosten-wert num" }, `${zahl(Math.round(k.kosten_dollar * 100))} Cent`)),
        h("div", { class: "hint", style: { textAlign: "right" } }, `${zahl(k.transkription_minuten, 1)} Min. Sprache`, h("br"), plural(k.websuchen, "Suche", "Suchen")))
      : h("div", { class: "hint" }, "Heute noch kein Verbrauch.");
    leeren(dienstKarte, kartenKopf("Anbindungen", "link", alleLink("system", "System")),
      h("div", { class: "svc-list" }, ...DIENSTE.map((d) => {
        const z = dienstZustand(d.id, st.dienste);
        return h("div", { class: "svc" }, h("span", { class: "svc-icon" }, icon(d.symbol)), h("span", { class: "svc-name" }, d.name),
          h("span", { class: `badge ${z.art === "off" ? "" : z.art}` }, h("span", { class: "dot" }), z.text));
      })), kosten);

    const eintraege = [
      ...st.aktionen.map(aktion).map((a) => ({ zeit: a.zeit, art: a.art || "info", symbol: a.art === "ok" ? "shield-check" : a.art === "danger" ? "shield" : "alert-circle", titel: a.was, sub: a.ergebnis })),
      ...(st.fehler || []).map(fehlerEintrag).map((f) => ({ zeit: f.zeit, art: f.art, symbol: "alert-triangle", titel: f.text, sub: f.stufe.toLowerCase() })),
    ].sort((a, b) => b.zeit.localeCompare(a.zeit)).slice(0, 6);
    leeren(aktivitaetKarte, kartenKopf("Letzte Aktivität", "history", alleLink("system", "Protokoll")),
      eintraege.length ? h("div", { class: "feed" }, ...eintraege.map((e) => h("div", { class: "feed-item" },
        h("span", { class: `feed-dot ${e.art}` }, icon(e.symbol)),
        h("div", { class: "item-main" }, h("div", { class: "item-title clamp-2" }, e.titel), h("div", { class: "item-sub" }, `${e.sub} · ${vergangen(e.zeit)}`)))))
        : leer("history", "Noch keine Aktivität", "Freigaben und Fehler erscheinen hier."));
  }

  function vorschlaegeZeigen() {
    if (!faehig || vorschlaege.childElementCount) return;
    const gruppen = faehig.gruppen.filter((g) => g.im_modus && g.bereit + g.nur_stimme > 0 && g.beispiele.length);
    const auswahl = [...gruppen].sort(() => Math.random() - 0.5).slice(0, 3);
    leeren(vorschlaege, ...auswahl.map((g) => h("button", { class: "chip suggest", type: "button", onclick: () => { eingabe.value = g.beispiele[0]; eingabe.focus(); } },
      icon(g.symbol), g.beispiele[0])));
  }

  // --- Laden ---------------------------------------------------------------------------------------------------------------
  async function laden() {
    const gilt = marke();
    // Jede Karte zeichnet sich, sobald ihre Antwort da ist: Ein langsamer Kalender oder Wetterdienst hält die anderen nicht auf.
    const teile = [
      api("heute").then((d) => {
        if (!gilt()) return;
        heute = d;
        einmal("plan", [d.kalender, d.erinnerungen, minute()], planZeigen);
        einmal("aufgaben", [d.aufgaben, minute()], aufgabenZeigen);
        kennzahlenAktualisieren();
        titel.textContent = `${tageszeitGruss(faehig?.persona?.nutzer)}.`;
      }, (e) => {
        if (!gilt()) return;
        leeren(planKarte, kartenKopf("Als Nächstes", "clock"), leer("alert-circle", "Nicht erreichbar", e.message));
        kennzahlenZeigen();
      }),
      api("auftraege").then((d) => { if (gilt()) einmal("auftraege", [d.auftraege.map((x) => [x.nummer, x.status, x.titel, x.begonnen, x.beendet]), minute()], () => auftraegeZeigen(d)); }, () => {}),
      api("status").then((d) => { if (gilt()) einmal("status", [d, minute()], () => statusZeigen(d)); }, () => {}),
      // Frisch holen: Nach einem Moduswechsel im Chat stimmen sonst Modus und Werkzeugzahl im Kopf nicht mehr.
      faehigkeitenLaden(true).then((d) => {
        if (!gilt()) return;
        faehig = d;
        einmal("faehig", d, faehigkeitenZeigen);
        vorschlaegeZeigen();
        if (heute) titel.textContent = `${tageszeitGruss(d.persona.nutzer)}.`;
      }, () => { /* ohne die Übersicht bleibt die Karte beim Platzhalter */ }),
    ];
    datum.textContent = datumLang();
    zaehlerLaden();
    await Promise.allSettled(teile);
  }

  // Countdown der Timer und „vor …“-Angaben im Sekundentakt.
  sekundentakt(wurzel, () => {
    for (const pille of wurzel.querySelectorAll(".timer-pill")) pille.querySelector("b").textContent = countdown(pille.dataset.faellig);
  });
  store.on("live", live);
  store.on("zaehler", kennzahlenAktualisieren);
  live(store.get("live"));

  return { laden, takt: 30000 };
}
