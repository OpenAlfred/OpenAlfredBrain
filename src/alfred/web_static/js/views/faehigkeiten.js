// Fähigkeiten: was Alfred kann, was er dafür darf und was ihm noch fehlt. Alles kommt aus dem Werkzeug-Register (/api/faehigkeiten).
import { h, icon, store, faehigkeitenLaden, leeren, panelOeffnen, imChatVorbereiten, plural } from "../core.js";
import { seitenKopf, kartenKopf, leer, skelett, fehlerKarte, STUFEN, stufenBadge } from "../teile.js";

const norm = (t) => t.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
const STATUS = {
  bereit: { text: "bereit", art: "ok" },
  nur_stimme: { text: "nur per Stimme", art: "info" },
  fehlt: { text: "nicht eingerichtet", art: "" },
};
const REGLER = {
  widerspruch: ["Widerspruch", "stimmt zu", "widerspricht"],
  humor: ["Humor", "sachlich", "trocken-witzig"],
  foermlichkeit: ["Förmlichkeit", "kumpelhaft", "förmlich"],
  ausfuehrlichkeit: ["Ausführlichkeit", "knapp", "ausführlich"],
  proaktivitaet: ["Proaktivität", "antwortet nur", "schlägt vor"],
  waerme: ["Wärme", "nüchtern", "herzlich"],
};
const MODELLE = [
  { schluessel: "sprecher", name: "Sprecher", symbol: "mic", text: "Führt das Gespräch und ruft die Werkzeuge auf" },
  { schluessel: "denker", name: "Denker", symbol: "sparkles", text: "Denkt im Hintergrund nach, recherchiert und schreibt Berichte" },
  { schluessel: "werkzeuge", name: "Werkzeug-Modell", symbol: "tool", text: "Websuche, Code, Fotos und Zusammenfassungen" },
  { schluessel: "transkription", name: "Spracherkennung", symbol: "mic", text: "Macht aus Gesprochenem Text" },
  { schluessel: "sprachausgabe", name: "Sprachausgabe", symbol: "message-circle", text: "Liest Alfreds Antworten vor" },
  { schluessel: "sprachnachricht", name: "Sprachnachrichten", symbol: "mic", text: "Hört Sprachnachrichten aus dem Chat der App ab" },
];

export function mount(wurzel) {
  let f = null;
  let vorschau = null;     // Modus, dessen Fähigkeiten gerade gezeigt werden
  let stufe = null;        // Filter nach Schutzstufe
  let zustand = "alle";    // alle | bereit | fehlt
  let suche = "";
  let offeneGruppe = null; // Gruppe, die das Seitenfenster zeigen soll (Adresse #/faehigkeiten/<gruppe>)
  let fenster = null;
  let stand = "";          // JSON der zuletzt gezeichneten Übersicht

  const kopf = seitenKopf("Fähigkeiten", "Was Alfred kann, was er dafür darf und was ihm noch fehlt. Die Liste kommt direkt aus seinen Werkzeugen und kann deshalb nicht veralten.");
  const kennzahlen = h("div", { class: "kennzahlen-grid" }, ...[0, 1, 2, 3].map(() => h("div", { class: "stat" }, skelett(2, 22))));
  const stufenZeile = h("div", { class: "auto-grid", style: { "--min": "250px", "--gap": "12px" } });
  const werkzeugleiste = h("div", { class: "toolbar" });
  const raster = h("div", { class: "faehig-raster" }, skelett(3, 120));
  const matrix = h("section", { class: "card flush" }, skelett(4, 30));
  const persona = h("section", { class: "card" }, skelett(4, 24));
  const system = h("section", { class: "card" }, skelett(4, 24));

  wurzel.append(kopf, kennzahlen,
    h("div", { class: "section-h" }, h("h2", null, "Schutzstufen"), h("p", null, "Wie viel Alfred allein darf: ein Klick filtert die Werkzeuge")), stufenZeile,
    h("div", { class: "section-h" }, h("h2", null, "Bereiche"), h("p", null, "Ein Klick zeigt alle Werkzeuge, Beispielsätze und was noch fehlt")), werkzeugleiste, raster,
    h("div", { class: "section-h" }, h("h2", null, "Modi und Persönlichkeit"), h("p", null, "Ein Modus schaltet Bereiche ab und verändert Alfreds Ton")),
    h("div", { class: "faehig-unten" }, matrix, h("div", { class: "stack", style: { "--gap": "16px" } }, persona, system)));

  // --- Filter ----------------------------------------------------------------------------------------------------------
  const modus = () => f.modi.find((m) => m.name === vorschau) || f.modi.find((m) => m.aktiv) || f.modi[0];

  function passtWerkzeug(w) {
    if (stufe && w.stufe !== stufe) return false;
    if (zustand === "bereit" && w.status === "fehlt") return false;
    if (zustand === "fehlt" && w.status !== "fehlt") return false;
    return !suche || norm(`${w.name} ${w.beschreibung}`).includes(norm(suche));
  }
  function passtGruppe(g) {
    if (!suche && !stufe && zustand === "alle") return true;
    const gruppenTreffer = suche && norm(`${g.titel} ${g.beschreibung}`).includes(norm(suche));
    return g.werkzeuge.some((w) => passtWerkzeug(w)) || (gruppenTreffer && !stufe && zustand === "alle");
  }

  // --- Kennzahlen und Stufen ------------------------------------------------------------------------------------------
  function kennzahlenZeigen() {
    const s = f.summe;
    const kann = s.bereit + s.nur_stimme;
    const fehlend = [...new Set(f.gruppen.flatMap((g) => g.braucht))];
    const m = f.modi.find((x) => x.aktiv) || f.modi[0];
    const kachel = (label, symbol, wert, sub, extra) => h("div", { class: "stat" }, h("div", { class: "stat-label" }, icon(symbol), label), h("div", { class: "stat-value" }, wert), h("div", { class: "stat-sub" }, sub), extra);
    leeren(kennzahlen,
      kachel("Werkzeuge", "tool", s.gesamt, `in ${f.gruppen.length} Bereichen`),
      kachel("Einsatzbereit", "check-circle", kann, s.nur_stimme ? `davon ${s.nur_stimme} nur per Stimme (Raum)` : "alle sofort nutzbar", h("div", { class: "meter ok", style: { "--v": (100 * kann) / s.gesamt, marginTop: "6px" } }, h("i"))),
      kachel("Brauchen Anbindung", "link", s.fehlt, s.fehlt ? fehlend.join(", ") : "alles angebunden"),
      kachel("Aktiver Modus", "sliders", m.name[0].toUpperCase() + m.name.slice(1), `${m.werkzeuge} Werkzeuge sieht Alfred darin`));
  }

  function stufenZeigen() {
    leeren(stufenZeile, ...["lesend", "schreibend", "bestaetigen"].map((st) => h("button", { class: "stufe-karte", type: "button", "data-lvl": st, "aria-pressed": String(stufe === st), onclick: () => {
      stufe = stufe === st ? null : st;
      stufenZeigen();
      rasterZeigen();
    } },
      h("span", { class: "stufe-icon" }, icon(STUFEN[st].symbol)),
      h("div", { class: "stufe-text" }, h("b", null, STUFEN[st].text), h("p", null, STUFEN[st].erklaerung)),
      h("span", { class: "stufe-zahl num" }, f.summe.stufen[st]))));
  }

  // --- Werkzeugleiste und Raster ----------------------------------------------------------------------------------------
  function leisteZeigen() {
    const sucheFeld = h("input", { class: "input", type: "search", placeholder: "Fähigkeit suchen: Timer, WhatsApp, Einkauf …", value: suche, "aria-label": "Fähigkeiten suchen", oninput: (ev) => { suche = ev.target.value; rasterZeigen(); } });
    const zustandWahl = h("div", { class: "seg", role: "tablist" }, ...[["alle", "Alle"], ["bereit", "Einsatzbereit"], ["fehlt", "Nicht eingerichtet"]].map(([id, text]) =>
      h("button", { type: "button", role: "tab", "aria-selected": String(zustand === id), onclick: () => { zustand = id; leisteZeigen(); rasterZeigen(); } }, text)));
    const modusWahl = h("div", { class: "row", style: { "--gap": "8px" } }, h("span", { class: "hint hide-m" }, "Vorschau Modus"),
      h("div", { class: "seg", role: "tablist" }, ...f.modi.map((m) =>
        h("button", { type: "button", role: "tab", "aria-selected": String(modus().name === m.name), title: m.beschreibung, onclick: () => { vorschau = m.name; leisteZeigen(); rasterZeigen(); matrixZeigen(); personaZeigen(); } },
          m.name[0].toUpperCase() + m.name.slice(1), m.aktiv ? h("span", { class: "dot", style: { color: "var(--ok)" }, title: "aktiver Modus" }) : null))));
    leeren(werkzeugleiste, h("div", { class: "suchfeld" }, icon("search"), sucheFeld), zustandWahl, modusWahl);
  }

  function gruppeKarte(g) {
    const m = modus();
    const imModus = m.gruppen.includes(g.id);
    const gesamt = g.werkzeuge.length;
    const kann = g.bereit + g.nur_stimme;
    const treffer = g.werkzeuge.filter(passtWerkzeug).length;
    const filterAktiv = suche || stufe || zustand !== "alle";
    const statusText = !imModus ? `im Modus „${m.name}“ aus`
      : g.fehlt === 0 ? (g.nur_stimme ? "bereit, teils nur per Stimme" : "alles bereit")
        : kann === 0 ? `braucht ${g.braucht.join(", ") || "eine Anbindung"}` : `${kann} von ${gesamt} bereit · braucht ${g.braucht.join(", ")}`;
    const art = !imModus ? "off" : g.fehlt === 0 ? "ok" : kann === 0 ? "off" : "warn";
    const oeffnen = () => gruppeOeffnen(g.id);
    return h("article", { class: `gruppe-karte${imModus ? "" : " aus"}${kann === 0 ? " leer" : ""}`, role: "button", tabindex: 0, onclick: oeffnen,
      onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); oeffnen(); } }, "aria-label": `${g.titel}: ${statusText}` },
      h("header", { class: "gruppe-kopf" },
        h("span", { class: "gruppe-icon" }, icon(g.symbol)),
        h("div", { class: "gruppe-titel" }, h("h3", null, g.titel), h("p", null, g.beschreibung)),
        h("span", { class: "badge" }, filterAktiv ? `${treffer}/${gesamt}` : plural(gesamt, "Werkzeug", "Werkzeuge"))),
      h("div", { class: "stufenbalken", title: `${g.stufen.lesend} Lesen · ${g.stufen.schreibend} Schreiben · ${g.stufen.bestaetigen} mit Ja` },
        ...["lesend", "schreibend", "bestaetigen"].filter((st) => g.stufen[st]).map((st) => h("i", { "data-lvl": st, style: { "--w": g.stufen[st] } }))),
      h("div", { class: "stufen-legende" }, ...["lesend", "schreibend", "bestaetigen"].filter((st) => g.stufen[st]).map((st) =>
        h("span", { "data-lvl": st }, h("i"), `${g.stufen[st]} ${STUFEN[st].text}`))),
      h("footer", { class: "gruppe-fuss" },
        h("span", { class: `status-zeile ${art}` }, h("span", { class: "dot" }), statusText),
        g.beispiele[0] ? h("span", { class: "beispiel" }, `„${g.beispiele[0]}“`) : null));
  }

  function rasterZeigen() {
    const sichtbar = f.gruppen.filter(passtGruppe);
    leeren(raster, ...(sichtbar.length ? sichtbar.map(gruppeKarte)
      : [h("div", { class: "card", style: { gridColumn: "1 / -1" } }, leer("search", "Nichts gefunden", "Versuch einen anderen Begriff oder nimm die Filter heraus."))]));
  }

  // --- Seitenfenster einer Gruppe ------------------------------------------------------------------------------------------
  function gruppeOeffnen(id) {
    const g = f.gruppen.find((x) => x.id === id);
    if (!g) return;
    fenster?.schliessen();
    history.replaceState(null, "", `#/faehigkeiten/${id}`);
    const m = f.modi.find((x) => x.aktiv) || f.modi[0];
    const inhalt = h("div", { class: "stack", style: { "--gap": "22px" } });
    if (g.fehlt) {
      inhalt.append(h("div", { class: "callout warn" }, icon("link"), h("div", null,
        h("div", { class: "callout-title" }, `Nicht eingerichtet: ${g.braucht.join(", ") || "eine Anbindung"}`),
        h("div", null, `${plural(g.fehlt, "Werkzeug ist", "Werkzeuge sind")} bis dahin nicht verfügbar; Alfred sagt dann ehrlich, dass er das nicht kann. Einrichten: Handbuch (docs/handbuch.md), Abschnitt „Anbindungen einrichten“.`))));
    }
    if (!g.im_modus) {
      inhalt.append(h("div", { class: "callout" }, icon("sliders"), h("div", null, h("div", { class: "callout-title" }, `Im Modus „${m.name}“ aus`),
        h("div", null, `Dieser Bereich gilt in: ${g.modi.map((n) => n[0].toUpperCase() + n.slice(1)).join(", ") || "keinem Modus"}.`))));
    }
    if (g.beispiele.length) {
      inhalt.append(h("section", { class: "stack", style: { "--gap": "10px" } }, h("div", { class: "overline" }, "So sagst du es Alfred"),
        ...g.beispiele.map((b) => h("div", { class: "beispiel-zeile" }, h("span", null, `„${b}“`),
          h("button", { class: "btn sm", type: "button", onclick: () => { fenster?.schliessen(); imChatVorbereiten(b); } }, icon("message-square"), "Ausprobieren")))));
    }
    inhalt.append(h("section", { class: "stack", style: { "--gap": "10px" } }, h("div", { class: "overline" }, `Werkzeuge (${g.werkzeuge.length})`),
      h("div", { class: "card flush" }, h("div", { class: "list" }, ...g.werkzeuge.map((w) => {
        const st = STATUS[w.status];
        return h("div", { class: `item werkzeug${w.status === "fehlt" ? " fehlt" : ""}`, "data-lvl": w.stufe },
          h("span", { class: "lead", style: { background: "var(--lvl-soft)", color: "var(--lvl)" }, title: `${STUFEN[w.stufe].text}: ${STUFEN[w.stufe].erklaerung}` }, icon(STUFEN[w.stufe].symbol)),
          h("div", { class: "item-main" }, h("div", { class: "item-title mono" }, w.name), h("div", { class: "item-sub" }, w.beschreibung)),
          h("span", { class: `badge ${st.art}`, title: w.braucht ? `braucht ${w.braucht}` : "" }, w.status === "fehlt" && w.braucht ? w.braucht : st.text));
      })))));
    inhalt.append(h("section", { class: "stack", style: { "--gap": "10px" } }, h("div", { class: "overline" }, "Gilt in diesen Modi"),
      h("div", { class: "row wrap", style: { "--gap": "8px" } }, ...f.modi.map((mm) => {
        const an = mm.gruppen.includes(g.id);
        return h("span", { class: `badge ${an ? "ok" : ""}`, style: an ? null : { opacity: ".55" } }, icon(an ? "check" : "x"), mm.name[0].toUpperCase() + mm.name.slice(1));
      }))));
    fenster = panelOeffnen({ titel: g.titel, untertitel: g.beschreibung, symbol: g.symbol, inhalt, beiZu: () => {
      fenster = null;
      if (location.hash.startsWith("#/faehigkeiten/")) history.replaceState(null, "", "#/faehigkeiten");
    } });
  }

  // --- Modi, Persönlichkeit, Systemkarte --------------------------------------------------------------------------------------
  function matrixZeigen() {
    const aktiv = modus();
    leeren(matrix, kartenKopf("Welcher Modus kann was", "sliders", h("span", { class: "card-sub" }, "Spaltenkopf anklicken zeigt die Vorschau")),
      h("div", { class: "tabelle-rahmen" }, h("table", { class: "table matrix" },
        h("thead", null, h("tr", null, h("th", null, "Bereich"), ...f.modi.map((m) => h("th", { class: `mod${m.name === aktiv.name ? " aktiv" : ""}` },
          h("button", { type: "button", onclick: () => { vorschau = m.name; leisteZeigen(); rasterZeigen(); matrixZeigen(); personaZeigen(); } },
            m.name[0].toUpperCase() + m.name.slice(1), h("small", null, `${m.werkzeuge} Werkzeuge`), m.aktiv ? h("span", { class: "badge ok" }, "aktiv") : null))))),
        h("tbody", null, ...f.gruppen.map((g) => h("tr", null,
          h("td", null, h("span", { class: "row", style: { "--gap": "10px" } }, h("span", { class: "lead", style: { width: "28px", height: "28px", fontSize: ".95rem" } }, icon(g.symbol)), h("span", { class: "item-title" }, g.titel))),
          ...f.modi.map((m) => h("td", { class: `zelle${m.name === aktiv.name ? " aktiv" : ""}` }, m.gruppen.includes(g.id) ? icon("check", "ja") : h("span", { class: "nein" }, "–")))))))),
      h("p", { class: "hint", style: { padding: "12px 18px 16px" } }, "Der Kern (Zeit, Erinnerungen, Gedächtnis, Websuche, Denker) gilt in jedem Modus. „Alfred, Fokus-Modus“ per Stimme tauscht die Werkzeuge im laufenden Gespräch."));
  }

  function personaZeigen() {
    const m = modus();
    leeren(persona, kartenKopf(`Persönlichkeit im Modus „${m.name}“`, "user"),
      h("p", { class: "muted", style: { fontSize: "var(--fs-sm)", marginBottom: "14px" } }, m.beschreibung || "Normaler Alltag."),
      h("div", { class: "stack", style: { "--gap": "14px" } }, ...Object.entries(REGLER).map(([schluessel, [name, links, rechts]]) => {
        const wert = m.regler[schluessel];
        const grund = f.persona.regler[schluessel];
        const geaendert = m.abweichend.includes(schluessel);
        return h("div", { class: "regler" },
          h("div", { class: "regler-kopf" }, h("b", null, name), h("span", { class: "num" }, wert, geaendert ? h("small", { class: "dim" }, ` (sonst ${grund})`) : null)),
          h("div", { class: `regler-spur${geaendert ? " geaendert" : ""}`, role: "img", "aria-label": `${name}: ${wert} von 10` },
            h("i", { style: { "--v": wert * 10 } }), geaendert ? h("b", { style: { "--v": grund * 10 }, title: "Grundwert" }) : null),
          h("div", { class: "regler-enden" }, h("span", null, links), h("span", null, rechts)));
      })),
      f.persona.regeln.length ? h("div", { style: { marginTop: "18px" } }, h("div", { class: "overline", style: { marginBottom: "8px" } }, "Regeln in jedem Modus"),
        h("ul", { class: "regel-liste" }, ...f.persona.regeln.map((r) => h("li", null, r)))) : null);
  }

  function systemZeigen() {
    leeren(system, kartenKopf("Gehirn und Sinne", "cpu", h("span", { class: "card-sub" }, "welche Modelle was tun")),
      h("div", { class: "list inset" }, ...MODELLE.map(({ schluessel, name, symbol, text }) => h("div", { class: "item" },
        h("span", { class: "lead" }, icon(symbol)), h("div", { class: "item-main" }, h("div", { class: "item-title" }, name), h("div", { class: "item-sub" }, text)),
        h("code", { class: "modell" }, f.modelle[schluessel])))),
      h("div", { class: "wachwort" }, icon("mic"), h("span", null, "Wachwort im Raum:"), ...f.wachwort.map((w) => h("b", null, `„${w}“`))));
  }

  function alleZeigen() {
    kennzahlenZeigen();
    stufenZeigen();
    leisteZeigen();
    rasterZeigen();
    matrixZeigen();
    personaZeigen();
    systemZeigen();
  }

  async function laden() {
    try {
      f = await faehigkeitenLaden(true);
    } catch (e) {
      if (!f) leeren(raster, h("div", { class: "card", style: { gridColumn: "1 / -1" } }, fehlerKarte(e, laden)));
      return;
    }
    // Neu zeichnen nur bei Änderung: Sonst verlöre das Suchfeld beim Tippen jede Minute den Fokus.
    const neu = JSON.stringify(f);
    if (neu !== stand) {
      stand = neu;
      vorschau ||= f.persona.modus;
      alleZeigen();
    }
    if (offeneGruppe) { const id = offeneGruppe; offeneGruppe = null; gruppeOeffnen(id); }
  }

  return {
    laden,
    zeigen(params) {
      if (params?.[0]) { if (f) gruppeOeffnen(params[0]); else offeneGruppe = params[0]; }
    },
    verlassen() { fenster?.schliessen(); },
    takt: 60000,
  };
}
