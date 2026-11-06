// Kern der App: DOM-Helfer, Datenzugriff, Formatierung, Hinweise, Dialoge, Seitenfenster, gemeinsamer Zustand.
// Alle Adressen sind relativ, damit die App auch unter einem Unterpfad läuft (tailscale serve --set-path /app).
// Inhalte von Alfred, aus Mails oder aus dem Netz kommen nie als HTML in die Seite, nur als Text (h) oder
// über markdown(), das zuerst alles maskiert.

const SVG_NS = "http://www.w3.org/2000/svg";

export const $ = (sel, wurzel = document) => wurzel.querySelector(sel);
export const $$ = (sel, wurzel = document) => [...wurzel.querySelectorAll(sel)];

function anhaengen(el, kinder) {
  for (const k of kinder.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

function eigenschaften(el, props) {
  if (!props) return;
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") {
      for (const [p, w] of Object.entries(v)) (p.startsWith("--") ? el.style.setProperty(p, w) : (el.style[p] = w));
    } else if (k === "style") el.style.cssText = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "for") el.htmlFor = v;
    else if (k in el && k !== "list" && !k.includes("-")) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
}

/** Baut ein Element: h("div", { class: "card", onclick }, "Text", andereElemente). */
export function h(tag, props, ...kinder) {
  const el = document.createElement(tag);
  eigenschaften(el, props);
  anhaengen(el, kinder);
  return el;
}

/** Dasselbe für SVG-Elemente (Diagramme). */
export function s(tag, attribute, ...kinder) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attribute || {})) if (v != null && v !== false) el.setAttribute(k, v);
  anhaengen(el, kinder);
  return el;
}

/** Ein Symbol aus dem Sprite in index.html. */
export function icon(name, klasse = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", klasse ? `icon ${klasse}` : "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export const leeren = (el, ...kinder) => { el.replaceChildren(); anhaengen(el, kinder); return el; };

// --- Gemeinsamer Zustand ------------------------------------------------------------------------------

export const store = {
  daten: {},
  hoerer: {},
  get(schluessel) { return this.daten[schluessel]; },
  set(schluessel, wert) {
    this.daten[schluessel] = wert;
    for (const f of this.hoerer[schluessel] || []) f(wert);
  },
  /** Meldet jede Änderung; gibt eine Funktion zum Abmelden zurück. */
  on(schluessel, f) {
    (this.hoerer[schluessel] ||= []).push(f);
    return () => { this.hoerer[schluessel] = this.hoerer[schluessel].filter((x) => x !== f); };
  },
};

// --- Datenzugriff -------------------------------------------------------------------------------------

export async function api(pfad, methode = "GET", daten = null) {
  let antwort;
  try {
    antwort = await fetch("api/" + pfad, {
      method: methode,
      headers: daten ? { "Content-Type": "application/json" } : {},
      body: daten ? JSON.stringify(daten) : null,
    });
  } catch {
    store.set("online", false);
    const fehler = new Error("Alfred ist nicht erreichbar");
    fehler.netz = true;
    throw fehler;
  }
  store.set("online", true);
  if (!antwort.ok) {
    let text = antwort.statusText || `Fehler ${antwort.status}`;
    try {
      const d = (await antwort.json()).detail;
      if (typeof d === "string") text = d;
      else if (Array.isArray(d)) text = d.map((x) => x.msg).join("; ");
    } catch { /* keine JSON-Antwort */ }
    throw new Error(text);
  }
  return antwort.json();
}

/** Die Fähigkeiten-Übersicht, einmal geholt und für alle Ansichten geteilt. */
export async function faehigkeitenLaden(neu = false) {
  if (!neu && store.get("faehigkeiten")) return store.get("faehigkeiten");
  const daten = await api("faehigkeiten");
  store.set("faehigkeiten", daten);
  return daten;
}

/** Offenes in Zahlen für die Marken der Navigation; Ansichten rufen es nach jeder Änderung. */
export async function zaehlerLaden() {
  try { store.set("zaehler", await api("zaehler")); } catch { /* die Marken bleiben, wie sie waren */ }
}

/** Was der Sprach-Prozess gerade tut (aus der Mitschrift); fehlt die App selbst, steht dort "aus". */
let liveLaeuft = false;
export async function liveLaden() {
  if (liveLaeuft) return;  // hängt der Server, stapeln sich sonst alle drei Sekunden neue Anfragen
  liveLaeuft = true;
  try {
    store.set("live", await api("mitschrift"));
  } catch {
    store.set("live", { erreichbar: false, zustand: "aus", beschreibung: "App nicht erreichbar", zuege: [] });
  } finally {
    liveLaeuft = false;
  }
}

/** Werkzeugname → Eintrag der Übersicht (Gruppe, Stufe, Beschreibung), sobald sie geladen ist. */
export function werkzeugInfo(name) {
  const f = store.get("faehigkeiten");
  if (!f) return null;
  for (const g of f.gruppen) {
    const w = g.werkzeuge.find((x) => x.name === name);
    if (w) return { ...w, gruppe: g };
  }
  return null;
}

// --- Navigation ---------------------------------------------------------------------------------------

export const gehe = (pfad) => { location.hash = "#/" + pfad; };

/** Legt einen Satz in den Chat und öffnet ihn; abgeschickt wird er dort von Hand. */
export function imChatVorbereiten(text, senden = false) {
  store.set("chatEntwurf", { text, senden });
  gehe("chat");
}

/** Öffnet den Chat und beginnt dort eine Sprachnachricht. */
export function imChatAufnehmen() {
  store.set("chatEntwurf", { aufnehmen: true });
  gehe("chat");
}

// --- Formatierung -------------------------------------------------------------------------------------

const WOCHENTAGE_KURZ = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const WOCHENTAGE = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
export const wochentagKurz = (d) => WOCHENTAGE_KURZ[d.getDay()];
export const wochentag = (d) => WOCHENTAGE[d.getDay()];
export const pad = (n) => String(n).padStart(2, "0");

// Die Uhrzeit aus dem ISO-Text des Servers, ohne Umrechnung: So steht dort, was der Server meint.
export const uhrzeit = (iso) => (iso ? iso.slice(11, 16) : "");
export const datumKurz = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.` : "");

export function datumLang(d = new Date()) {
  return new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(d);
}

export function tageBis(isoDatum) {
  const [j, m, t] = isoDatum.slice(0, 10).split("-").map(Number);
  const heute = new Date();
  heute.setHours(0, 0, 0, 0);
  return Math.round((new Date(j, m - 1, t) - heute) / 864e5);
}

/** Fälligkeit eines Datums in Worten, dazu eine Tönung für die Anzeige. */
export function faelligkeit(isoDatum) {
  if (!isoDatum) return { text: "", art: "", tage: null };
  const tage = tageBis(isoDatum);
  const [j, m, t] = isoDatum.slice(0, 10).split("-").map(Number);
  const tag = new Date(j, m - 1, t);
  if (tage < 0) return { text: tage === -1 ? "gestern fällig" : `seit ${-tage} Tagen fällig`, art: "danger", tage };
  if (tage === 0) return { text: "heute", art: "warn", tage };
  if (tage === 1) return { text: "morgen", art: "info", tage };
  if (tage < 7) return { text: `${wochentag(tag)}`, art: "", tage };
  return { text: datumKurz(isoDatum), art: "", tage };
}

/** "in 25 Min.", "in 2 Std.", "morgen 09:00", "Mo 09:00" für einen Zeitpunkt mit Zeitzone. */
export function relativ(iso) {
  const ziel = new Date(iso);
  const minuten = Math.round((ziel - Date.now()) / 60000);
  if (minuten < -1) return minuten > -60 ? `vor ${-minuten} Min.` : minuten > -1440 ? `vor ${Math.round(-minuten / 60)} Std.` : `vor ${Math.round(-minuten / 1440)} Tagen`;
  if (minuten <= 0) return "jetzt";
  if (minuten < 60) return `in ${minuten} Min.`;
  if (minuten < 360) return `in ${Math.round(minuten / 60)} Std.`;
  const tage = tageBis(iso);
  if (tage === 0) return `heute ${uhrzeit(iso)}`;
  if (tage === 1) return `morgen ${uhrzeit(iso)}`;
  return `${wochentagKurz(ziel)} ${uhrzeit(iso)}`;
}

/** "vor 5 Min.", "gestern 20:14", "Mo 07:55" für eine vergangene Zeit; ohne Uhrzeit bei weit zurück. */
export function vergangen(iso) {
  if (!iso) return "";
  const minuten = Math.round((Date.now() - new Date(iso)) / 60000);
  if (minuten < 1) return "gerade eben";
  if (minuten < 60) return `vor ${minuten} Min.`;
  const tage = -tageBis(iso);
  if (tage === 0) return `heute ${uhrzeit(iso)}`;
  if (tage === 1) return `gestern ${uhrzeit(iso)}`;
  if (tage < 7) return `${wochentagKurz(new Date(iso))} ${uhrzeit(iso)}`;
  return `${datumKurz(iso)} ${uhrzeit(iso)}`;
}

export function dauer(sekunden) {
  const s = Math.max(0, Math.round(sekunden));
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} Min.`;
  return `${Math.round(s / 360) / 10} Std.`.replace(".", ",");
}

export function countdown(iso) {
  const rest = Math.max(0, Math.round((new Date(iso) - Date.now()) / 1000));
  return `${Math.floor(rest / 60)}:${pad(rest % 60)}`;
}

const zahlenformat = {};
export function zahl(n, stellen = 0) {
  zahlenformat[stellen] ||= new Intl.NumberFormat("de-DE", { maximumFractionDigits: stellen, minimumFractionDigits: stellen });
  return zahlenformat[stellen].format(n);
}
export const euro = (n) => new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(n);
export const plural = (n, eins, viele) => `${zahl(n)} ${n === 1 ? eins : viele}`;

export function tageszeitGruss(name = "") {
  const std = new Date().getHours();
  const gruss = std < 5 ? "Gute Nacht" : std < 11 ? "Guten Morgen" : std < 14 ? "Guten Tag" : std < 18 ? "Guten Nachmittag" : "Guten Abend";
  return name ? `${gruss}, ${name}` : gruss;
}

/** Nur die jüngste Antwort zählt: Jeder Aufruf holt eine Marke, die nur gilt, solange kein neuerer Aufruf begonnen hat.
 * `const gilt = marke(); const d = await api(...); if (!gilt()) return;` */
export function juengste() {
  let nr = 0;
  return () => {
    const mein = ++nr;
    return () => mein === nr;
  };
}

export function entprellen(f, ms = 200) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => f(...args), ms); };
}

export async function kopieren(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Kopiert", "ok", 1800);
  } catch {
    toast("Kopieren nicht möglich", "err");
  }
}

// --- Markdown (Berichte) -------------------------------------------------------------------------------
// Berichte sind Markdown mit Inhalten aus dem Internet: erst alles maskieren, dann nur Überschriften, Listen,
// Zitate, Tabellen, Code, Fettdruck, Kursives und Links auf http(s) zulassen.

const MASKE = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const maske = (t) => t.replace(/[&<>"']/g, (z) => MASKE[z]);

function inline(roh) {
  return maske(roh)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

const zellen = (zeile) => zeile.trim().replace(/^\||\|$/g, "").split("|").map((z) => z.trim());

export function markdown(text) {
  let html = "";
  let liste = null;
  let code = null;
  const zeilen = text.split("\n");
  const listeZu = () => { if (liste) html += `</${liste}>`; liste = null; };
  for (let i = 0; i < zeilen.length; i++) {
    const roh = zeilen[i];
    const z = roh.trim();
    let m;
    if (code !== null) {
      if (z.startsWith("```")) { html += `<pre><code>${maske(code)}</code></pre>`; code = null; } else code += (code ? "\n" : "") + roh;
      continue;
    }
    if (z.startsWith("```")) { listeZu(); code = ""; continue; }
    if (!z) { listeZu(); continue; }
    if ((m = z.match(/^(#{1,4})\s+(.*)$/))) { listeZu(); const n = m[1].length + 2; html += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if (/^(-{3,}|\*{3,})$/.test(z)) { listeZu(); html += "<hr>"; }
    else if (z.startsWith("|") && zeilen[i + 1] && /^\|?[\s:|-]+\|[\s:|-]*$/.test(zeilen[i + 1].trim())) {
      listeZu();
      const kopf = zellen(z);
      i += 2;
      let tabelle = `<table><thead><tr>${kopf.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>`;
      for (; i < zeilen.length && zeilen[i].trim().startsWith("|"); i++) tabelle += `<tr>${zellen(zeilen[i]).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`;
      i--;
      html += tabelle + "</tbody></table>";
    }
    else if ((m = z.match(/^>\s?(.*)$/))) { listeZu(); html += `<blockquote>${inline(m[1])}</blockquote>`; }
    else if ((m = z.match(/^[-*•]\s+(.*)$/))) { if (liste !== "ul") { listeZu(); html += "<ul>"; liste = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = z.match(/^\d+[.)]\s+(.*)$/))) { if (liste !== "ol") { listeZu(); html += "<ol>"; liste = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else { listeZu(); html += `<p>${inline(z)}</p>`; }
  }
  if (code !== null) html += `<pre><code>${maske(code)}</code></pre>`;
  listeZu();
  return html;
}

/** Füllt ein Element mit dem Markdown-Text. markdown() maskiert alles Fremde zuerst; hier und nirgends sonst kommt HTML in die Seite. */
export function markdownEinsetzen(el, text) {
  el.innerHTML = markdown(text);
  return el;
}

/** Ein Textblock (Klasse prose) aus Markdown, etwa für Berichte, Notizen und Protokolle. */
export const prosa = (text, klasse = "prose") => markdownEinsetzen(h("div", { class: klasse }), text);

// --- Hinweise und Dialoge ------------------------------------------------------------------------------

export function toast(text, art = "info", ms = 3200) {
  const symbol = art === "err" ? "alert-circle" : art === "ok" ? "check-circle" : "info";
  const el = h("div", { class: `toast ${art}`, role: "status" }, icon(symbol), h("span", null, text));
  document.getElementById("toasts").append(el);
  setTimeout(() => { el.classList.add("leaving"); setTimeout(() => el.remove(), 220); }, ms);
}
export const fehler = (e) => toast(e.message || String(e), "err", 5200);

/** Ja/Nein-Frage; löst mit true oder false auf. */
export function bestaetigen({ titel, text = "", ok = "OK", abbrechen = "Abbrechen", gefahr = false }) {
  return new Promise((aufloesen) => {
    const dlg = h("dialog", { class: "modal", "aria-label": titel },
      h("form", { method: "dialog" },
        h("div", { class: "modal-body" }, h("div", { class: "modal-title" }, titel), text && h("p", { class: "modal-text" }, text)),
        h("div", { class: "modal-foot" },
          // Bei Unumkehrbarem hat der sichere Knopf den Fokus: Ein Enter aus Versehen löscht nichts.
          h("button", { class: "btn ghost", type: "submit", value: "nein", autofocus: gefahr }, abbrechen),
          h("button", { class: `btn ${gefahr ? "danger" : "primary"}`, type: "submit", value: "ja", autofocus: !gefahr }, ok))));
    dlg.addEventListener("close", () => { aufloesen(dlg.returnValue === "ja"); dlg.remove(); });
    document.body.append(dlg);
    dlg.showModal();
  });
}

/** Ein Eingabefeld im Dialog; löst mit dem Text auf, bei Abbruch mit null. */
export function abfragen({ titel, text = "", label = "", wert = "", platzhalter = "", ok = "OK", modus = "text" }) {
  return new Promise((aufloesen) => {
    const feld = h("input", { class: "input", type: "text", value: wert, placeholder: platzhalter, inputmode: modus, name: "wert", autocomplete: "off", autofocus: true });
    const dlg = h("dialog", { class: "modal", "aria-label": titel },
      h("form", { method: "dialog" },
        h("div", { class: "modal-body" }, h("div", { class: "modal-title" }, titel), text && h("p", { class: "modal-text" }, text),
          h("div", { class: "field" }, label && h("label", null, label), feld)),
        h("div", { class: "modal-foot" },
          // Abbrechen ist kein Submit-Knopf: Enter im Feld löst sonst den ersten Submit-Knopf aus und bräche ab.
          h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close("nein") }, "Abbrechen"),
          h("button", { class: "btn primary", type: "submit", value: "ja" }, ok))));
    dlg.addEventListener("close", () => { aufloesen(dlg.returnValue === "ja" ? feld.value.trim() : null); dlg.remove(); });
    document.body.append(dlg);
    dlg.showModal();
  });
}

let offeneFenster = 0;       // mehrere Seitenfenster können übereinander stehen; die Sperre endet erst mit dem letzten
let gemerkterRand = "";

/** Seitenfenster: rechts am Rechner, als Blatt von unten am Handy. */
export function panelOeffnen({ titel, untertitel = "", symbol = null, inhalt, beiZu = null, breit = false }) {
  const davor = document.activeElement;
  const scrim = h("div", { class: "scrim", onclick: () => zu() });
  const titelzeile = h("div", { class: "stack", style: { gap: "3px", flex: "1", minWidth: "0" } },
    h("h2", { class: "panel-title" }, titel), untertitel && h("p", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, untertitel));
  const body = h("div", { class: "panel-body" }, inhalt);
  const panel = h("aside", { class: "panel", role: "dialog", "aria-modal": "true", "aria-label": titel, tabindex: "-1", style: breit ? { width: "min(760px, 100vw)" } : null },
    h("div", { class: "panel-h" },
      symbol && h("div", { class: "lead-lg" }, icon(symbol)),
      titelzeile,
      h("button", { class: "btn icon ghost sm", "aria-label": "Schließen", onclick: () => zu() }, icon("x"))),
    body);
  const taste = (e) => { if (e.key === "Escape" && panel.isConnected) { e.stopPropagation(); zu(); } };
  function zu() {
    if (!panel.isConnected) return;
    document.removeEventListener("keydown", taste, true);
    scrim.remove();
    panel.remove();
    if (--offeneFenster === 0) {
      document.body.style.overflow = gemerkterRand;
      document.getElementById("app").inert = false;
    }
    if (davor?.isConnected) davor.focus?.({ preventScroll: true });
    beiZu?.();
  }
  document.addEventListener("keydown", taste, true);
  if (offeneFenster++ === 0) {
    gemerkterRand = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.getElementById("app").inert = true;  // dahinter weder Tab noch Vorlesen
  }
  document.getElementById("overlays").append(scrim, panel);
  panel.focus({ preventScroll: true });
  return { schliessen: zu, body, panel };
}

// --- Orb: Alfreds Zustand ---------------------------------------------------------------------------------

export function orb(groesse = "md") {
  return h("div", { class: `orb ${groesse}`, "data-z": "aus", role: "img", "aria-label": "Alfreds Zustand" },
    h("span", { class: "orb-ring" }), h("span", { class: "orb-arc" }), h("span", { class: "orb-core" }),
    h("span", { class: "orb-bars" }, ...[0, 1, 2, 3, 4].map((i) => h("i", { style: { "--i": i } }))));
}

/** schlaeft, bereit, hoert, denkt, arbeitet, spricht oder aus (der Sprach-Prozess meldet sich nicht). */
export function zustandSchluessel(live) {
  if (!live || !live.erreichbar) return "aus";
  return String(live.zustand || "bereit").split(":")[0];
}

export function zustandText(live) {
  if (!live) return "…";
  return live.beschreibung || (live.erreichbar ? "bereit" : "kein Sprach-Prozess");
}

export function orbSetzen(el, live) {
  const z = zustandSchluessel(live);
  if (el.dataset.z !== z) el.dataset.z = z;
  el.setAttribute("aria-label", `Alfred: ${zustandText(live)}`);
}

// --- Eingaben und Fotos ------------------------------------------------------------------------------------

/** Das Datum (Standard heute) als JJJJ-MM-TT in der Ortszeit des Geräts. */
export const lokalesDatum = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** Zahl aus einer Eingabe mit deutschem Komma ("9,5" → 9.5); NaN, wenn es keine ist. */
export const kommaZahl = (text) => Number(String(text ?? "").trim().replace(",", "."));

/** Foto auf höchstens maxKante Pixel verkleinern und als Base64 (JPEG) liefern. */
export async function fotoVerkleinern(datei, maxKante = 1280) {
  let bild;
  try {
    bild = await createImageBitmap(datei);
  } catch {
    throw new Error("Das Foto ließ sich nicht öffnen. Versuch es mit einem anderen Bild.");
  }
  try {
    const faktor = Math.min(1, maxKante / Math.max(bild.width, bild.height));
    const flaeche = document.createElement("canvas");
    flaeche.width = Math.max(1, Math.round(bild.width * faktor));
    flaeche.height = Math.max(1, Math.round(bild.height * faktor));
    flaeche.getContext("2d").drawImage(bild, 0, 0, flaeche.width, flaeche.height);
    return flaeche.toDataURL("image/jpeg", 0.85).split(",")[1];
  } finally {
    if (bild.close) bild.close();
  }
}

/** Knopf während einer Arbeit sperren und mit drehendem Symbol beschriften; danach wie vorher. */
export async function beschaeftigt(knopf, text, arbeit) {
  const vorher = [...knopf.childNodes];
  knopf.disabled = true;
  knopf.setAttribute("aria-busy", "true");
  knopf.replaceChildren(icon("loader", "spin"), text);
  try {
    return await arbeit();
  } finally {
    knopf.replaceChildren(...vorher);
    knopf.disabled = false;
    knopf.removeAttribute("aria-busy");
  }
}

/** Die Zeitleiste, die jede Ansicht mit Zeitangaben aktualisiert: ruft f jede Sekunde, solange das Element in der Seite steht. */
export function sekundentakt(el, f) {
  const timer = setInterval(() => { if (!el.isConnected) clearInterval(timer); else if (!document.hidden) f(); }, 1000);
  f();
  return () => clearInterval(timer);
}
