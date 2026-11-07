// System: der Sprach-Prozess im Detail, Anbindungen, Kosten, Freigaben, Fehler und die Darstellung.
// Der Live-Zustand kommt aus dem Store (die Mitschrift, von app.js alle 3 Sekunden geholt), der Rest aus GET status alle 15 Sekunden.
import {
  h, icon, api, store, orb, orbSetzen, sekundentakt, leeren, zahl, plural, uhrzeit, vergangen, dauer, kopieren, liveLaden, juengste,
} from "../core.js";
import {
  seitenKopf, kartenKopf, leer, skelett, fehlerKarte, zustandBadge, DIENSTE, dienstZustand, aktion, fehlerEintrag, zugZeile,
} from "../teile.js";
import { DESIGNS, designLesen, designSetzen } from "../design.js";

const FEHLER_SICHTBAR = 6;
const LOGSTUFEN = { WARNING: ["Warnung", "warn"], ERROR: ["Fehler", "danger"], CRITICAL: ["Kritisch", "danger"] };

// Wie eine Zeile aus aktionen.log zu lesen ist; das Ergebnis beginnt mit einem dieser Wörter, der Rest ist Beiwerk.
const ERGEBNISSE = [
  ["ausgef", "ok", "Ausgeführt", "shield-check"],
  ["abgelehnt", "warn", "Abgelehnt", "x-circle"],
  ["verweigert", "danger", "Verweigert", "shield"],
  ["fehler", "danger", "Fehlgeschlagen", "alert-triangle"],
  ["abgelaufen", "", "Abgelaufen", "clock"],
];

/** Eine Zeit, die sich im Sekundentakt selbst nachführt ("vor 5 Min."); der Zeitstempel steht im Tooltip. */
function zeitEl(iso) {
  return h("time", { datetime: iso, "data-vergangen": iso, title: iso.replace("T", " ") }, vergangen(iso));
}

function aktionDeuten(zeile) {
  const a = aktion(String(zeile));
  const e = a.ergebnis.trim();
  const treffer = ERGEBNISSE.find(([anfang]) => e.toLowerCase().startsWith(anfang));
  const [, art, text, symbol] = treffer || [null, a.art, e || "–", "info"];
  const detail = treffer ? e.replace(/^\S+\s*/, "").replace(/^\((.*)\)$/, "$1") : "";  // "ausgeführt (Handy)" → "Handy"
  const zahlWert = Number(a.stimme);
  const stimme = a.stimme && Number.isFinite(zahlWert) ? zahl(zahlWert, 2) : a.stimme || "–";
  return { ...a, art, text, symbol, detail, stimme };
}

function kv(...zeilen) {
  return h("dl", { class: "kv" }, ...zeilen.flatMap(([name, wert, klasse]) => [h("dt", { class: klasse || null }, name), h("dd", null, wert)]));
}

export function mount(wurzel) {
  wurzel.classList.add("sy");

  let status = null;         // letzte Antwort von GET status
  let alleFehler = false;
  // Je Karte die zuletzt gezeichnete Fassung: Der 15-Sekunden-Takt zeichnet nur neu, was sich geändert hat
  // (sonst klappten aufgeklappte Tracebacks zu und eine Textauswahl ginge verloren).
  let sigMitschrift = "";
  let sigDienste = "";
  let sigKosten = "";
  let sigAktionen = "";
  let sigFehler = "";
  const offeneSpuren = new Set();
  const aktiv = () => wurzel.classList.contains("active");

  // --- Sprach-Prozess ----------------------------------------------------------------------------------------------
  const sprachOrb = orb("lg");
  const zustandZeile = h("div", { class: "sy-zustand" });
  const zeitAntwort = h("span", { class: "sy-zeit", hidden: true });
  const zeitLebenszeichen = h("span", { class: "sy-zeit", hidden: true });
  const stillstand = h("div", { class: "callout danger", role: "alert", hidden: true }, icon("alert-triangle"), h("div"));
  const gesprochen = h("div", { class: "sy-zeilen" });
  const mitschriftZahl = h("span", { class: "card-sub" });
  const box = h("div", { class: "zuege sy-zuege", tabindex: "0", role: "region", "aria-label": "Mitschrift, die neuesten Beiträge stehen unten" });

  const prozessKarte = h("section", { class: "card sy-prozess" },
    h("div", { class: "sy-links" },
      kartenKopf("Sprach-Prozess", "mic"),
      h("div", { class: "sy-kopf" },
        h("div", { class: "sy-orb" }, sprachOrb),
        h("div", { class: "sy-info" }, zustandZeile, h("div", { class: "sy-zeiten" }, zeitAntwort, zeitLebenszeichen))),
      stillstand, gesprochen),
    h("div", { class: "sy-rechts" }, kartenKopf("Mitschrift", "message-square", mitschriftZahl), box));

  function zeile(klasse, name, eintrag, leerText) {
    return h("div", { class: `sy-zeile ${klasse}${eintrag ? "" : " leer"}` },
      h("div", { class: "sy-zeile-kopf" }, h("span", null, name),
        eintrag ? h("time", { title: vergangen(eintrag.zeit) }, uhrzeit(eintrag.zeit)) : null),
      h("div", { class: "sy-zeile-text" }, eintrag ? eintrag.text : leerText));
  }

  function liveZeigen(l) {
    if (!aktiv()) return;  // zeigen() holt es nach, sobald die Ansicht wieder offen ist
    const lebt = !!l?.erreichbar;
    orbSetzen(sprachOrb, l);
    leeren(zustandZeile, l ? zustandBadge(l) : h("span", { class: "badge" }, "…"),  // noch nichts geladen: nicht gleich "aus" melden
      l?.quelle ? h("span", { class: "badge outline", title: "So nennt die App den Sprach-Prozess" }, icon("mic"), l.quelle) : null);
    stillstand.hidden = !l || lebt;
    if (l && !lebt) {
      stillstand.lastChild.textContent = store.get("online") === false
        ? "Die App erreicht Alfred gerade nicht. Hier steht der letzte bekannte Stand."
        : "Alfreds Sprach-Prozess meldet sich nicht. Läuft er (Raum oder Browser)? Die App selbst funktioniert trotzdem.";
    }
    gesprochen.classList.toggle("alt", !!l && !lebt);  // ohne Lebenszeichen ist es der Stand von früher
    leeren(gesprochen,
      zeile("verstanden", "Verstanden", l?.verstanden, "Noch nichts verstanden."),
      zeile("alfred", "Gesagt", l?.gesagt, "Noch nichts gesagt."),
      zeile("ueberhoert", "Überhört, nicht deine Stimme", l?.ueberhoert, "Nichts überhört."));
    zeitenZeigen();
    mitschriftZeigen(l);
  }

  /** "letzte Antwort nach 1,4 s" und "Lebenszeichen vor 3 s"; Letzteres läuft im Sekundentakt weiter. */
  function zeitenZeigen() {
    const l = store.get("live");
    zeitAntwort.hidden = !l?.antwortzeit;
    if (l?.antwortzeit) zeitAntwort.textContent = `letzte Antwort nach ${zahl(l.antwortzeit, 1)} s`;
    const wann = l?.aktualisiert ? Date.parse(l.aktualisiert) : NaN;
    zeitLebenszeichen.hidden = Number.isNaN(wann);
    if (!Number.isNaN(wann)) {
      const alter = Math.max(0, Math.round((Date.now() - wann) / 1000));
      zeitLebenszeichen.textContent = `Lebenszeichen vor ${dauer(alter)}`;
      zeitLebenszeichen.className = `sy-zeit${!l.erreichbar ? " danger" : alter > 10 ? " warn" : ""}`;
    }
  }

  function mitschriftZeigen(l) {
    const zuege = l?.zuege || [];
    const sig = `${JSON.stringify(zuege)}|${Math.floor(Date.now() / 60000)}`;  // alle Minute neu, damit "vor 5 Min." stimmt
    if (sig === sigMitschrift) return;
    sigMitschrift = sig;
    const amEnde = box.scrollHeight - box.scrollTop - box.clientHeight < 28;
    const stand = box.scrollTop;
    mitschriftZahl.textContent = zuege.length ? plural(zuege.length, "Beitrag", "Beiträge") : "";
    leeren(box, ...(zuege.length ? zuege.map(zugZeile) : [leer("mic", "Noch nichts gesprochen", "Sobald Alfred etwas hört, steht es hier.")]));
    box.scrollTop = amEnde ? box.scrollHeight : stand;  // wer weiter oben liest, bleibt dort
  }

  // --- Anbindungen ---------------------------------------------------------------------------------------------------
  const dienstKopf = h("div", { class: "section-h" }, h("h2", null, "Anbindungen"), h("p", null, "Womit Alfred verbunden ist. Gestrichelt heißt: nicht eingerichtet."));
  const dienstGitter = h("div", { class: "auto-grid sy-dienste", style: { "--min": "250px" } },
    ...DIENSTE.map(() => h("div", { class: "card" }, skelett(2, 22))));

  function dienstHinweis(d, z, dienste) {
    if (z.art === "off") return "Einrichten: siehe Handbuch, Abschnitt Anbindungen einrichten.";
    if (d.id === "suche" && z.art === "warn") return "Mit einer eigenen Suche (SearXNG) kostet die Websuche nichts. Einrichten: siehe Handbuch, Abschnitt Anbindungen einrichten.";
    if (d.id === "whatsapp" && z.art === "warn") return "Koppeln: siehe Handbuch, Abschnitt Anbindungen einrichten.";
    if (d.id === "whatsapp" && z.art === "danger") return dienste.whatsapp?.fehler || "Die Brücke läuft nicht.";
    return null;
  }

  function dienstZeigen() {
    const dienste = status.dienste || {};
    const sig = JSON.stringify(dienste);
    if (sig === sigDienste) return;
    sigDienste = sig;
    leeren(dienstGitter, ...DIENSTE.map((d) => {
      const z = dienstZustand(d.id, dienste);
      const hinweis = dienstHinweis(d, z, dienste);
      return h("div", { class: `card sy-dienst${z.art === "off" ? " aus" : ""}` },
        h("div", { class: "sy-dienst-kopf" },
          h("span", { class: `sy-dienst-icon ${z.art === "off" ? "" : z.art}` }, icon(d.symbol)),
          h("div", { class: "sy-dienst-titel" }, h("div", { class: "sy-dienst-name" }, d.name), h("div", { class: "sy-dienst-text" }, d.text)),
          h("span", { class: `badge sy-dienst-zustand ${z.art === "off" ? "" : z.art}` }, h("span", { class: "dot" }), z.text)),
        hinweis ? h("p", { class: `hint sy-dienst-hinweis${z.art === "danger" ? " danger" : ""}` }, hinweis) : null);
    }));
  }

  // --- Kosten heute ----------------------------------------------------------------------------------------------------
  const kostenKarte = h("section", { class: "card sy-kosten" }, skelett(3, 28));

  function kostenZeigen() {
    const k = status.kosten;
    const sig = JSON.stringify([k, !!status.dienste?.suche]);
    if (sig === sigKosten) return;
    sigKosten = sig;
    const kopf = kartenKopf("Kosten heute", "wallet");
    if (!k || k.verbrauch === "nichts") {
      leeren(kostenKarte, kopf, leer("wallet", "Heute noch kein Verbrauch", "Sobald Alfred zuhört oder antwortet, erscheinen hier die Kosten."));
      return;
    }
    const cent = (k.kosten_dollar || 0) * 100;
    const modelle = Object.entries(k.token || {});
    const hinweise = [
      k.unbepreist?.length ? h("div", { class: "callout warn" }, icon("alert-triangle"),
        h("div", null, `Für ${k.unbepreist.join(", ")} gibt es keinen Preis, das Modell fehlt in der Summe. Preise stehen in config/alfred.yaml.`)) : null,
      k.websuchen > 0 && !status.dienste?.suche
        ? h("p", { class: "hint" }, icon("info"), " Mistrals Websuche kostet pro Aufruf. Mit einer eigenen Suche (SearXNG) entfällt das.") : null,
    ].filter(Boolean);
    leeren(kostenKarte, kopf,
      h("div", { class: "sy-kosten-wert" }, h("b", { class: "num" }, cent >= 10 ? zahl(Math.round(cent)) : zahl(cent, 1)), h("span", null, "Cent")),
      h("p", { class: "hint" }, "Geschätzt, nach den Preisen aus der Konfiguration."),
      h("div", { class: "sy-kv" }, kv(
        ["Transkription", `${zahl(k.transkription_minuten || 0, 1)} Min.`],
        ["Sprachausgabe", `${zahl(k.sprachausgabe_zeichen || 0)} Zeichen`],
        ["Websuchen", zahl(k.websuchen || 0)])),
      modelle.length ? h("div", { class: "sy-kv" }, h("div", { class: "overline" }, "Token je Modell"),
        kv(...modelle.map(([name, t]) => [name, [`${zahl(t.eingabe || 0)} Eingabe · ${zahl(t.ausgabe || 0)} Ausgabe`,
          t.eingabe_cache ? h("div", { class: "hint" }, `davon ${zahl(t.eingabe_cache)} aus dem Zwischenspeicher`) : null], "mono"]))) : null,
      hinweise.length ? h("div", { class: "sy-extra" }, ...hinweise) : null);
  }

  // --- Aktionsprotokoll ---------------------------------------------------------------------------------------------------
  const aktionenKarte = h("section", { class: "card sy-aktionen" }, skelett(4, 36));

  function aktionenZeigen() {
    const roh = (status.aktionen || []).map(String);
    const sig = JSON.stringify(roh);
    if (sig === sigAktionen) return;
    sigAktionen = sig;
    const eintraege = roh.map(aktionDeuten);
    const kopf = [kartenKopf("Aktionsprotokoll", "shield-check", h("span", { class: "card-sub" }, "neueste zuerst")),
      h("p", { class: "hint", style: { marginTop: "-6px", marginBottom: "12px" } }, "Jede Freigabe steht hier, auch die verweigerten.")];
    if (!eintraege.length) {
      leeren(aktionenKarte, ...kopf, leer("shield-check", "Noch keine Freigaben", "Sobald Alfred nach deinem Ja etwas ausführt, steht es hier."));
      return;
    }
    leeren(aktionenKarte, ...kopf,
      h("div", { class: "sy-scroll hide-m" }, h("table", { class: "table sy-tabelle" },
        h("caption", { class: "sr-only" }, "Letzte Freigaben, neueste zuerst"),
        h("thead", null, h("tr", null, ...["Zeit", "Aktion", "Ergebnis", "Stimme"].map((t) => h("th", { scope: "col" }, t)))),
        h("tbody", null, ...eintraege.map((a) => h("tr", null,
          h("td", { class: "nowrap dim" }, zeitEl(a.zeit)),
          h("td", { class: "sy-was" }, a.was),
          h("td", null, h("span", { class: `badge ${a.art}` }, a.text), a.detail ? h("div", { class: "hint sy-detail" }, a.detail) : null),
          h("td", { class: "num", title: "Wie sicher die Stimme zu deinem Stimmprofil passt" }, a.stimme)))))),
      h("div", { class: "only-m" }, h("div", { class: "list inset" }, ...eintraege.map((a) => h("div", { class: "item" },
        h("span", { class: `lead ${a.art}` }, icon(a.symbol)),
        h("div", { class: "item-main" }, h("div", { class: "item-title" }, a.was),
          h("div", { class: "item-sub" }, zeitEl(a.zeit), ` · Stimme ${a.stimme}`, a.detail ? ` · ${a.detail}` : "")),
        h("span", { class: `badge ${a.art}` }, a.text))))));
  }

  // --- Fehler ------------------------------------------------------------------------------------------------------------------
  const fehlerKartenEl = h("section", { class: "card sy-fehlerkarte" }, skelett(3, 40));

  function fehlerZeile(f) {
    const [stufeText, stufeArt] = LOGSTUFEN[f.stufe] || [f.stufe || "Meldung", f.art];
    const kopfzeile = f.roh.split("\n")[0];
    const quelle = kopfzeile.split(" | ")[2]?.match(/^(\S+) - /)?.[1];  // "alfred.room:run_room:190"
    const spur = f.rest.trim();
    return h("div", { class: "sy-fehler" },
      h("div", { class: "sy-fehler-kopf" },
        h("span", { class: `badge ${stufeArt}`, title: f.stufe }, stufeText),
        f.zeit ? zeitEl(f.zeit) : null,
        quelle ? h("span", { class: "mono dim sy-quelle" }, quelle) : null),
      h("div", { class: "sy-fehler-text" }, f.text),
      spur ? h("details", {
        class: "sy-spur", open: offeneSpuren.has(kopfzeile),
        ontoggle: (ev) => { if (ev.currentTarget.open) offeneSpuren.add(kopfzeile); else offeneSpuren.delete(kopfzeile); },
      }, h("summary", null, `Traceback · ${plural(spur.split("\n").length, "Zeile", "Zeilen")}`), h("pre", { class: "mono" }, spur)) : null);
  }

  function fehlerZeigen() {
    const roh = (status.fehler || []).map(String);
    const sig = JSON.stringify([roh, alleFehler]);
    if (sig === sigFehler) return;
    sigFehler = sig;
    const eintraege = roh.map((r) => ({ ...fehlerEintrag(r), roh: r }));
    const fehlerZahl = eintraege.filter((e) => e.art === "danger").length;
    const warnZahl = eintraege.length - fehlerZahl;
    const gezeigt = alleFehler ? eintraege : eintraege.slice(0, FEHLER_SICHTBAR);
    leeren(fehlerKartenEl,
      kartenKopf("Fehler und Warnungen", "alert-triangle",
        fehlerZahl ? h("span", { class: "badge danger" }, plural(fehlerZahl, "Fehler", "Fehler")) : null,
        warnZahl ? h("span", { class: "badge warn" }, plural(warnZahl, "Warnung", "Warnungen")) : null,
        eintraege.length ? h("button", { class: "btn ghost sm", type: "button", onclick: () => kopieren(roh.join("\n")) }, icon("copy"), "Kopieren") : null),
      eintraege.length
        ? [h("div", { class: "sy-fehlerliste" }, ...gezeigt.map(fehlerZeile)),
          eintraege.length > FEHLER_SICHTBAR
            ? h("button", { class: "btn sm sy-mehr", type: "button", onclick: () => { alleFehler = !alleFehler; fehlerZeigen(); } },
              icon(alleFehler ? "chevron-up" : "chevron-down"), alleFehler ? "Weniger anzeigen" : `Alle ${eintraege.length} anzeigen`)
            : null]
        : h("div", { class: "sy-ruhe" }, leer("check-circle", "Keine Fehler", "Alles ruhig.")));
  }

  // --- Darstellung --------------------------------------------------------------------------------------------------------------
  const designWahl = h("div", { class: "seg", role: "tablist", "aria-label": "Design" }, ...DESIGNS.map(([id, text, symbol]) =>
    h("button", { type: "button", role: "tab", "data-design": id, "aria-selected": String(designLesen() === id), onclick: () => designSetzen(id) }, icon(symbol), text)));
  store.on("design", (wahl) => {  // der Knopf in der Kopfzeile wechselt das Design ebenfalls
    for (const knopf of designWahl.children) knopf.setAttribute("aria-selected", String(knopf.dataset.design === wahl));
  });
  const designKarte = h("section", { class: "card sy-design" },
    kartenKopf("Darstellung", "monitor"), designWahl,
    h("p", { class: "hint", style: { marginTop: "12px" } }, "„System“ folgt der Einstellung deines Geräts. Die Wahl merkt sich dein Browser."));

  // --- Gerüst -------------------------------------------------------------------------------------------------------------------
  const statusHinweis = h("div", { class: "callout warn", role: "status", hidden: true }, icon("wifi"), h("div"));
  const statusFehlerKarte = h("section", { class: "card", hidden: true });
  const duo = h("div", { class: "sy-duo" }, kostenKarte, aktionenKarte);
  const statusSektionen = [dienstKopf, dienstGitter, duo, fehlerKartenEl];

  const aktualisieren = h("button", { class: "btn ghost", type: "button", "aria-label": "Aktualisieren", title: "Stand neu holen", onclick: async () => {
    aktualisieren.disabled = true;
    await Promise.allSettled([laden(), liveLaden()]);
    aktualisieren.disabled = false;
  } }, icon("refresh-cw"), h("span", { class: "hide-m" }, "Aktualisieren"));

  wurzel.append(
    seitenKopf("System", "Was gerade läuft, was angebunden ist und was es kostet.", aktualisieren),
    h("div", { class: "sy-stack" }, statusHinweis, prozessKarte, statusFehlerKarte, dienstKopf, dienstGitter, duo, fehlerKartenEl, designKarte));

  // --- Daten ----------------------------------------------------------------------------------------------------------------------
  function statusZeigen() {
    dienstZeigen();
    kostenZeigen();
    aktionenZeigen();
    fehlerZeigen();
  }

  const marke = juengste();
  async function laden() {
    const gilt = marke();  // nur die jüngste Antwort zählt, sonst überschreibt eine späte alte den neueren Stand
    try {
      const neu = await api("status");
      if (!gilt()) return;
      status = neu;
      statusHinweis.hidden = true;
      statusFehlerKarte.hidden = true;
      for (const s of statusSektionen) s.hidden = false;
      statusZeigen();
    } catch (e) {
      if (!gilt()) return;
      if (status) {  // der letzte Stand bleibt stehen
        statusHinweis.hidden = false;
        statusHinweis.lastChild.textContent = `Der Status ließ sich nicht aktualisieren: ${e.message}. Du siehst den letzten Stand.`;
        return;
      }
      for (const s of statusSektionen) s.hidden = true;
      statusFehlerKarte.hidden = false;
      leeren(statusFehlerKarte, fehlerKarte(e, laden));
    }
  }

  store.on("live", liveZeigen);
  sekundentakt(wurzel, () => {
    if (!aktiv()) return;
    zeitenZeigen();
    for (const el of wurzel.querySelectorAll("time[data-vergangen]")) {
      const text = vergangen(el.dataset.vergangen);
      if (el.textContent !== text) el.textContent = text;
    }
  });

  let sichtbar = false;
  return {
    laden,
    zeigen() {
      const neuSichtbar = !sichtbar;
      sichtbar = true;
      liveZeigen(store.get("live"));
      // Eine ausgeblendete Ansicht verliert den Scrollstand ihrer Rahmen: Die Mitschrift öffnet unten, bei den neuesten Beiträgen.
      if (neuSichtbar) box.scrollTop = box.scrollHeight;
    },
    verlassen() { sichtbar = false; },
    takt: 15000,
  };
}
