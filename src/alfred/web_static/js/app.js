// Alfreds App: Gerüst aus Seitenleiste, Kopfzeile und Tab-Leiste, Adressleisten-Router, Takt für den Live-Zustand.
// Jede Ansicht liegt in js/views/<id>.js und liefert mount(wurzel, kontext) → { zeigen(params), laden(), verlassen(), takt }.
// Eine Ansicht wird beim ersten Besuch gebaut und bleibt danach im Dokument (Eingaben und Scrollstand bleiben).
import { $, h, icon, store, orb, orbSetzen, zustandText, panelOeffnen, faehigkeitenLaden, liveLaden, zaehlerLaden } from "./core.js";
import { DESIGNS, designAnwenden, designLesen, designSetzen, designWeiter } from "./design.js";

const ANSICHTEN = [
  { id: "zentrale", titel: "Zentrale", symbol: "layout-dashboard", gruppe: "" },
  { id: "chat", titel: "Chat", symbol: "message-square", gruppe: "" },
  { id: "heute", titel: "Heute", symbol: "sun", gruppe: "Planen" },
  { id: "aufgaben", titel: "Aufgaben", symbol: "check-square", gruppe: "Planen", zaehler: "aufgaben" },
  { id: "auftraege", titel: "Aufträge", symbol: "layers", gruppe: "Planen", zaehler: "auftraege" },
  { id: "einkauf", titel: "Einkauf", symbol: "shopping-cart", gruppe: "Zuhause", zaehler: "einkauf" },
  { id: "ernaehrung", titel: "Ernährung", symbol: "apple", gruppe: "Zuhause" },
  { id: "haushalt", titel: "Haushalt", symbol: "home", gruppe: "Zuhause" },
  { id: "faehigkeiten", titel: "Fähigkeiten", symbol: "sparkles", gruppe: "Alfred" },
  { id: "gedaechtnis", titel: "Gedächtnis", symbol: "book-open", gruppe: "Alfred" },
  { id: "system", titel: "System", symbol: "activity", gruppe: "Alfred" },
];
const GRUPPEN = ["", "Planen", "Zuhause", "Alfred"];
const TAB_IDS = ["zentrale", "heute", "chat", "einkauf"];  // dazu "Mehr" als fünfter Platz am Handy
const ansichten = new Map(ANSICHTEN.map((a) => [a.id, { ...a, el: null, ctrl: null, timer: null }]));

const istMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

// --- Gerüst -------------------------------------------------------------------------------------------------

const orbs = [];  // alle Orbs der Oberfläche, vom Live-Zustand gespeist
function lebenderOrb(groesse) {
  const o = orb(groesse);
  orbs.push(o);
  return o;
}

function navEintrag(a, art) {
  const el = h("a", { class: art, href: `#/${a.id}`, "data-id": a.id, "aria-label": a.titel },
    icon(a.symbol), art === "nav-item" ? h("span", null, a.titel) : h("span", null, a.titel));
  if (a.zaehler) el.append(h("span", { class: art === "nav-item" ? "nav-badge" : "tab-badge", "data-zaehler": a.zaehler, hidden: true }));
  return el;
}

function designKnopf(klasse = "btn icon ghost") {
  const knopf = h("button", { class: klasse, type: "button", onclick: designWeiter });
  const aktualisieren = () => {
    const [, text, symbol] = DESIGNS.find(([id]) => id === designLesen());
    knopf.replaceChildren(icon(symbol));
    knopf.title = `Design: ${text} (klicken zum Wechseln)`;
    knopf.setAttribute("aria-label", knopf.title);
  };
  store.on("design", aktualisieren);
  aktualisieren();
  return knopf;
}

function seitenleiste() {
  const el = $("#sidebar");
  el.append(h("a", { class: "brand", href: "#/zentrale", style: { color: "inherit", textDecoration: "none" } },
    lebenderOrb("sm"),
    h("div", { class: "brand-text" }, h("div", { class: "brand-name" }, "Alfred"), h("div", { class: "brand-sub" }, "Persönlicher Agent"))));
  for (const gruppe of GRUPPEN) {
    if (gruppe) el.append(h("div", { class: "nav-label" }, h("span", null, gruppe)));
    for (const a of ANSICHTEN.filter((x) => x.gruppe === gruppe)) el.append(navEintrag(a, "nav-item"));
  }
  const status = h("a", { class: "side-card hide-rail", href: "#/system", style: { color: "inherit", textDecoration: "none" } },
    lebenderOrb("sm"), h("div", { style: { minWidth: 0 } }, h("b", { id: "side-zustand" }, "…"), h("span", { id: "side-quelle" }, "")));
  el.append(h("div", { class: "side-foot" }, status, h("div", { class: "row" }, designKnopf(), h("span", { class: "dim hide-rail", style: { fontSize: "var(--fs-xs)" } }, "Design"))));
}

function kopfzeile() {
  const el = $("#topbar");
  const chip = h("a", { class: "live-chip", href: "#/system", title: "Zustand von Alfreds Sprach-Prozess" },
    lebenderOrb("xs"), h("span", { id: "chip-zustand" }, "…"), h("span", { class: "live-sub hide-m", id: "chip-quelle" }));
  el.append(
    h("a", { class: "brand-m", href: "#/zentrale", style: { color: "inherit", textDecoration: "none" } }, lebenderOrb("xs"), "Alfred"),
    h("button", { class: "search-trigger", type: "button", onclick: paletteOeffnen, "aria-label": "Suchen oder Alfred etwas fragen" },
      icon("search"), h("span", { class: "hide-m" }, "Suchen oder Alfred etwas fragen …"), h("span", { class: "spacer" }),
      h("span", { class: "kbd hide-m" }, istMac ? "⌘ K" : "Strg K")),
    h("span", { class: "spacer" }),
    chip,
    h("span", { class: "hide-m" }, designKnopf()));
}

function tableiste() {
  const el = $("#tabbar");
  for (const id of TAB_IDS) el.append(navEintrag(ansichten.get(id), "tab"));
  el.append(h("button", { class: "tab", type: "button", "data-id": "mehr", onclick: mehrOeffnen }, icon("more-horizontal"), h("span", null, "Mehr")));
}

function mehrOeffnen() {
  // Die Fähigkeiten stehen vorn und hervorgehoben: Am Handy gibt es für sie keinen eigenen Platz in der Tab-Leiste.
  const rest = ANSICHTEN.filter((a) => !TAB_IDS.includes(a.id)).sort((a, b) => (b.id === "faehigkeiten") - (a.id === "faehigkeiten"));
  const gitter = h("div", { class: "more-grid" }, ...rest.map((a) =>
    h("a", { class: `more-item${a.id === "faehigkeiten" ? " hervor" : ""}`, href: `#/${a.id}`, "data-id": a.id, onclick: () => fenster.schliessen() },
      h("span", { class: "lead" }, icon(a.symbol)), h("span", null, a.titel))));
  const wahl = h("div", { class: "seg", role: "tablist" }, ...DESIGNS.map(([id, text, symbol]) =>
    h("button", { type: "button", role: "tab", "aria-selected": String(designLesen() === id), onclick: (ev) => {
      designSetzen(id);
      for (const b of wahl.children) b.setAttribute("aria-selected", String(b === ev.currentTarget));
    } }, icon(symbol), text)));
  const fenster = panelOeffnen({ titel: "Mehr", inhalt: h("div", { class: "stack", style: { "--gap": "22px" } },
    gitter, h("div", { class: "stack", style: { "--gap": "8px" } }, h("div", { class: "overline" }, "Design"), wahl)) });
}

function paletteOeffnen() {
  import("./palette.js").then((m) => m.paletteOeffnen({ ansichten: ANSICHTEN })).catch(console.error);
}

// --- Router und Ansichten ---------------------------------------------------------------------------------------

let aktiv = null;
let zaehlerNr = 0;

function fehlerAnsicht(e, erneut) {
  return h("div", { class: "card" }, h("div", { class: "empty" }, icon("alert-triangle"), h("b", null, "Diese Ansicht lässt sich nicht laden"),
    h("span", null, e.message || String(e)), h("button", { class: "btn", onclick: erneut }, icon("refresh-cw"), "Erneut versuchen")));
}

/** Eine Ansicht lädt nie zweimal gleichzeitig: Kommt der Takt oder ein Klick dazwischen, läuft danach genau eine weitere Runde.
 * So überholt keine ältere Antwort eine neuere, und bei hängendem Server stapeln sich keine Anfragen. */
function ohneUeberlappung(ctrl) {
  const laden = ctrl.laden;
  if (!laden) return;
  let laeuft = null;
  let nochmal = false;
  ctrl.laden = () => {
    if (laeuft) { nochmal = true; return laeuft; }
    laeuft = Promise.resolve().then(laden).catch(() => {}).finally(() => {
      laeuft = null;
      if (nochmal) { nochmal = false; ctrl.laden(); }
    });
    return laeuft;
  };
}

async function zeigen(id, params) {
  const nr = ++zaehlerNr;
  const a = ansichten.get(id);
  if (!a.el) {
    a.el = h("section", { class: "view", id: `view-${id}`, "aria-label": a.titel });
    $("#views").append(a.el);
  }
  const frisch = !a.ctrl;  // eben erst gebaut (oder nach einem Fehler neu versucht): muss laden, auch ohne Wechsel
  if (!a.ctrl) {
    try {
      const modul = await import(`./views/${id}.js`);
      if (!a.ctrl) {
        a.el.replaceChildren();
        a.ctrl = modul.mount(a.el, { id, params });
        ohneUeberlappung(a.ctrl);
      }
    } catch (e) {
      // Ohne Netz oder bei einem Fehler im Modul bleibt die Ansicht leer; der nächste Versuch lädt sie neu.
      console.error(e);
      a.el.replaceChildren(fehlerAnsicht(e, () => zeigen(id, params)));
    }
  }
  if (nr !== zaehlerNr) return;  // inzwischen woanders hin gewechselt
  const wechsel = aktiv !== a;
  if (wechsel) {
    if (aktiv) {
      clearInterval(aktiv.timer);
      aktiv.timer = null;
      aktiv.ctrl?.verlassen?.();
      aktiv.el.classList.remove("active");
    }
    aktiv = a;
    a.el.classList.add("active");
    window.scrollTo(0, 0);
  }
  for (const el of document.querySelectorAll("[data-id]")) {
    const hier = el.dataset.id === id || (el.dataset.id === "mehr" && !TAB_IDS.includes(id));
    if (hier) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
  }
  document.title = `${a.titel} · Alfred`;
  if (!a.ctrl) return;
  a.ctrl.zeigen?.(params);
  if (wechsel || frisch) {
    clearInterval(a.timer);
    a.ctrl.laden?.();
    if (a.ctrl.takt) a.timer = setInterval(() => { if (!document.hidden) a.ctrl.laden?.(); }, a.ctrl.takt);
  }
  try { localStorage.setItem("reiter", id); } catch { /* ohne Speicher startet die App in der Zentrale */ }
}

function route() {
  const alt = location.hash.match(/^#auftrag-(\d+)$/);  // Links aus Push-Nachrichten zu fertigen Aufträgen
  if (alt) { history.replaceState(null, "", `#/auftraege/${alt[1]}`); return route(); }
  const teile = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map((t) => { try { return decodeURIComponent(t); } catch { return t; } });
  let id = teile[0];
  if (!ansichten.has(id)) {
    try { id = localStorage.getItem("reiter"); } catch { id = null; }
    if (!ansichten.has(id)) id = "zentrale";
    history.replaceState(null, "", `#/${id}`);
    return zeigen(id, []);
  }
  return zeigen(id, teile.slice(1));
}

// --- Live-Zustand und Marken --------------------------------------------------------------------------------------

function liveAnzeigen(live) {
  for (const o of orbs) orbSetzen(o, live);
  const text = zustandText(live);
  const quelle = live?.erreichbar ? (live.quelle || "") : "";
  for (const id of ["side-zustand", "chip-zustand"]) { const el = document.getElementById(id); if (el) el.textContent = text; }
  const q = document.getElementById("side-quelle");
  if (q) q.textContent = quelle ? `${quelle}${live.antwortzeit ? ` · Antwort ${String(live.antwortzeit).replace(".", ",")} s` : ""}` : "";
  const cq = document.getElementById("chip-quelle");
  if (cq) cq.textContent = quelle;
}

function zaehlerAnzeigen(z) {
  for (const el of document.querySelectorAll("[data-zaehler]")) {
    const n = z[el.dataset.zaehler] || 0;
    el.hidden = !n;
    el.textContent = n;
    el.classList.toggle("warn", el.dataset.zaehler === "aufgaben" && z.ueberfaellig > 0);
    el.classList.toggle("hot", el.dataset.zaehler === "auftraege");
  }
}

// --- Start --------------------------------------------------------------------------------------------------------------

designAnwenden();
seitenleiste();
kopfzeile();
tableiste();
store.on("live", liveAnzeigen);
store.on("zaehler", zaehlerAnzeigen);
store.on("online", (ok) => { $("#offline").hidden = ok; });

window.addEventListener("hashchange", route);
document.addEventListener("keydown", (ev) => {
  const tippt = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); paletteOeffnen(); }
  else if (ev.key === "/" && !tippt && !ev.ctrlKey && !ev.metaKey) { ev.preventDefault(); paletteOeffnen(); }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  liveLaden();
  zaehlerLaden();
  aktiv?.ctrl?.laden?.();
});
setInterval(() => { if (!document.hidden) liveLaden(); }, 3000);
setInterval(() => { if (!document.hidden) zaehlerLaden(); }, 20000);

route();
liveLaden();
zaehlerLaden();
faehigkeitenLaden().catch(() => {});
setTimeout(() => import("./palette.js").catch(() => {}), 1500);  // vorladen: Die erste Befehlspalette öffnet sofort
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
