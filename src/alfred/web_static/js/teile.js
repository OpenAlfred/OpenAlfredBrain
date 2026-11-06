// Bausteine, die mehrere Ansichten teilen: Seitenkopf, Leerzustände, Schutzstufen, Auftragsstatus, Wetter, Anbindungen, Protokolle.
import { h, icon, vergangen, zustandSchluessel, zustandText } from "./core.js";

/** Abzeichen mit dem Zustand des Sprach-Prozesses: Punkt (pulsiert, solange etwas passiert) und Beschreibung. */
const ZUSTAND_ART = { schlaeft: "info", bereit: "ok", hoert: "ok", denkt: "warn", arbeitet: "violet", spricht: "accent", aus: "danger" };
export function zustandBadge(live) {
  const z = zustandSchluessel(live);
  const bewegt = ["hoert", "denkt", "arbeitet", "spricht"].includes(z);
  return h("span", { class: `badge ${ZUSTAND_ART[z]}` }, h("span", { class: bewegt ? "dot pulse" : "dot" }), zustandText(live));
}

export function seitenKopf(titel, untertitel = "", ...aktionen) {
  return h("header", { class: "page-head" },
    h("div", null, h("h1", null, titel), untertitel && h("p", null, untertitel)),
    aktionen.length ? h("div", { class: "page-actions" }, ...aktionen) : null);
}

/** Kopfzeile einer Karte: Symbol und Titel links, danach Beliebiges (Knöpfe, Zähler) rechts. */
export function kartenKopf(titel, symbol, ...rechts) {
  return h("div", { class: "card-h" }, h("div", { class: "card-title" }, symbol && icon(symbol), titel), h("span", { class: "spacer" }), ...rechts);
}

export function leer(symbol, titel, text = "", aktion = null) {
  return h("div", { class: "empty" }, icon(symbol), h("b", null, titel), text && h("span", null, text), aktion);
}

export function skelett(zeilen = 3, hoehe = 44) {
  return h("div", { class: "stack", style: { "--gap": "8px" } }, ...Array.from({ length: zeilen }, () => h("div", { class: "skel", style: { height: `${hoehe}px` } })));
}

export function fehlerKarte(e, erneut) {
  return h("div", { class: "empty" }, icon("alert-circle"), h("b", null, "Das hat nicht geklappt"), h("span", null, e.message || String(e)),
    erneut && h("button", { class: "btn sm", type: "button", onclick: erneut }, icon("refresh-cw"), "Erneut versuchen"));
}

export function checkKnopf(gesetzt, beiKlick, label = "Erledigt") {
  const knopf = h("button", { class: "check", type: "button", role: "checkbox", "aria-checked": String(!!gesetzt), "aria-label": label, onclick: (ev) => {
    ev.stopPropagation();
    knopf.setAttribute("aria-checked", String(knopf.getAttribute("aria-checked") !== "true"));
    beiKlick(knopf.getAttribute("aria-checked") === "true", knopf);
  } }, icon("check"));
  return knopf;
}

// --- Schutzstufen der Werkzeuge ------------------------------------------------------------------------------

export const STUFEN = {
  lesend: { text: "Lesen", kurz: "frei", symbol: "eye", erklaerung: "Liest oder rechnet nur. Läuft sofort." },
  schreibend: { text: "Schreiben", kurz: "eigene Daten", symbol: "edit-3", erklaerung: "Ändert Alfreds eigene Daten. Läuft sofort, fragt aber nach, sobald fremder Text (Mail, Suchtreffer) im Gespräch ist." },
  bestaetigen: { text: "Mit Ja", kurz: "nur nach Ja", symbol: "shield-check", erklaerung: "Wirkt nach außen oder dauerhaft. Alfred legt es nur ab und führt es erst nach deinem Ja aus." },
};

export function stufenBadge(stufe, mitText = true) {
  const s = STUFEN[stufe];
  if (!s) return null;
  return h("span", { class: "badge lvl", "data-lvl": stufe, title: `${s.text}: ${s.erklaerung}` }, icon(s.symbol), mitText ? s.text : null);
}

// --- Aufträge --------------------------------------------------------------------------------------------------------

export const AUFTRAG = {
  wartet: { text: "wartet", symbol: "clock", art: "" },
  laeuft: { text: "in Arbeit", symbol: "loader", art: "violet" },
  fertig: { text: "fertig", symbol: "check-circle", art: "ok" },
  fehler: { text: "gescheitert", symbol: "alert-triangle", art: "danger" },
  abgebrochen: { text: "abgebrochen", symbol: "x-circle", art: "" },
};
export const auftragOffen = (a) => a.status === "wartet" || a.status === "laeuft";

export function auftragBadge(status) {
  const s = AUFTRAG[status] || { text: status, symbol: "info", art: "" };
  return h("span", { class: `badge ${s.art}` }, icon(s.symbol, status === "laeuft" ? "spin" : ""), s.text);
}

// --- Wetter -----------------------------------------------------------------------------------------------------------

/** Zerlegt den gesprochenen Wettersatz ("Heute in Ulm: Regen, zwischen 9 und 17 Grad, …"); null, wenn er anders aussieht. */
export function wetterTeile(text) {
  const m = text && text.match(/^(Heute|Morgen) in (.+?): ([^,]+?)(?:,|\.|$)/);
  if (!m) return null;
  const temperatur = text.match(/zwischen (-?\d+) und (-?\d+) Grad/);
  const regen = text.match(/Regenwahrscheinlichkeit (\d+) Prozent/);
  const wind = text.match(/Wind bis (\d+) Kilometer/);
  const lage = m[3];
  const l = lage.toLowerCase();
  const symbol = l.includes("gewitter") ? "cloud-lightning" : l.includes("schnee") ? "cloud-snow" : l.includes("niesel") ? "cloud-drizzle"
    : l.includes("regen") || l.includes("schauer") ? "cloud-rain" : l.includes("nebel") || l.includes("bedeckt") || l.includes("bewölkt") ? "cloud" : "sun";
  return { tag: m[1], ort: m[2], lage, symbol, tief: temperatur && +temperatur[1], hoch: temperatur && +temperatur[2], regen: regen && +regen[1], wind: wind && +wind[1] };
}

// --- Anbindungen und Protokolle --------------------------------------------------------------------------------------

export const DIENSTE = [
  { id: "kalender", name: "Google Kalender", symbol: "calendar", text: "Termine lesen, anlegen und verschieben" },
  { id: "whatsapp", name: "WhatsApp", symbol: "message-circle", text: "Nachrichten senden, vorlesen und ansagen" },
  { id: "mail", name: "E-Mail", symbol: "mail", text: "Mails lesen und beantworten" },
  { id: "push", name: "Push aufs Handy", symbol: "smartphone", text: "Erinnerungen, Berichte und Freigaben per ntfy" },
  { id: "suche", name: "Websuche", symbol: "search", text: "Aktuelle Fakten und Recherchen" },
  { id: "musik", name: "Spotify", symbol: "music", text: "Musik abspielen und steuern" },
  { id: "stimmprofil", name: "Stimmprofil", symbol: "mic", text: "Erkennt deine Stimme bei Freigaben" },
];

/** Zustand einer Anbindung aus /api/status → { art: ok | warn | danger | off, text }. */
export function dienstZustand(id, dienste) {
  const d = dienste[id];
  switch (id) {
    case "whatsapp":
      if (!d) return { art: "off", text: "nicht eingerichtet" };
      if (d.angemeldet) return { art: "ok", text: "verbunden" };
      return d.fehler ? { art: "danger", text: "Brücke aus" } : { art: "warn", text: "nicht gekoppelt" };
    case "suche":
      return d ? { art: "ok", text: "eigene Suche" } : { art: "warn", text: "über Mistral (kostet)" };
    case "push":
      return d ? { art: "ok", text: "aktiv" } : { art: "off", text: "nicht eingerichtet" };
    case "stimmprofil":
      return d ? { art: "ok", text: "angelernt" } : { art: "off", text: "kein Profil" };
    default:
      return d ? { art: "ok", text: "verbunden" } : { art: "off", text: "nicht eingerichtet" };
  }
}

/** Eine Zeile aus gedaechtnis/aktionen.log: "Zeit \t stimme=… \t Was \t Ergebnis". */
export function aktion(zeile) {
  const [zeit = "", stimme = "", was = "", ergebnis = ""] = zeile.split("\t");
  const e = ergebnis.toLowerCase();
  const art = e.startsWith("ausgef") ? "ok" : e.startsWith("abgelehnt") ? "warn" : e.startsWith("verweigert") ? "danger" : "";
  const wert = stimme.replace("stimme=", "");
  return { zeit: zeit.replace(" ", "T"), stimme: wert, was, ergebnis, art };
}

/** Ein Eintrag aus logs/fehler.log: "2026-10-04 16:52:10 | WARNING  | modul:funktion:zeile - Text", Traceback in weiteren Zeilen. */
export function fehlerEintrag(eintrag) {
  const [kopf, ...rest] = eintrag.split("\n");
  const teile = kopf.split(" | ");
  const text = teile.length >= 3 ? teile.slice(2).join(" | ").replace(/^[^ ]+ - /, "") : kopf;
  const stufe = (teile[1] || "").trim();
  return { zeit: (teile[0] || "").replace(" ", "T"), stufe, text, rest: rest.join("\n"), art: stufe === "ERROR" || stufe === "CRITICAL" ? "danger" : "warn" };
}

export function zugZeile(z) {
  const alfred = z.wer === "Alfred";
  return h("div", { class: `zug ${alfred ? "alfred" : "du"}` },
    h("div", { class: "zug-kopf" }, h("b", null, alfred ? "Alfred" : "Du"), h("time", { title: z.zeit }, vergangen(z.zeit))),
    h("div", { class: "zug-text" }, z.text));
}
