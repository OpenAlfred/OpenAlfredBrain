// Kleine Diagramme ohne Bibliothek: Ring, Linie mit Verlaufsfläche und Säulen. Alle liefern DOM-Elemente.
// Hier steht nur die Geometrie. Farben und Schrift kommen aus css/ernaehrung.css (Klassen ch-…), damit beide
// Themes stimmen. Beschriftungen gehen nur als Text in die Seite, nie als HTML.
import { h, s, zahl } from "./core.js";

let zaehler = 0;
const neueId = (praefix) => `${praefix}-${++zaehler}`;
const zahlOder0 = (x) => { const n = Number(x); return Number.isFinite(n) ? n : 0; };
const positiv = (x) => { const n = Number(x); return Number.isFinite(n) && n > 0 ? n : null; };
const klemmen = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
// Zwei Bilder warten, damit der Anfangszustand wirklich gezeichnet ist und die Animation startet.
const nachBild = (f) => requestAnimationFrame(() => requestAnimationFrame(f));

/**
 * Ring mit Füllstand. Ohne Ziel zeichnet er nur die Summe (kein Füllstand). Die Mitte ist HTML über dem SVG.
 * farbe: accent | ok | info | violet | warn. ueberWarnt=false, wenn mehr als das Ziel gut ist (Eiweiß): Der Ring
 * wird dann beim Erreichen grün statt bernsteinfarben.
 * groesse ist die Größe in Pixeln; ohne Angabe gilt 148 und CSS darf sie über --ring-s ändern (die Striche skalieren mit).
 */
export function ring({ wert = 0, ziel = null, groesse = null, dicke = 12, farbe = "accent", titel = "", einheit = "", ueberWarnt = true } = {}) {
  const w = Math.max(0, zahlOder0(wert));
  const z = positiv(ziel);
  const mass = groesse || 148;
  const radius = (mass - dicke) / 2;
  const umfang = 2 * Math.PI * radius;
  const anteil = z ? Math.min(w / z, 1) : 0;
  const ueber = z !== null && ueberWarnt && w > z;
  const erreicht = z !== null && !ueber && w >= z;
  const id = neueId("ch-ring");

  const bogen = s("circle", { class: "ch-ring-bogen", cx: mass / 2, cy: mass / 2, r: radius, "stroke-width": dicke, stroke: `url(#${id})` });
  // Per Stil statt Attribut, sonst läuft die CSS-Animation nicht.
  bogen.style.strokeDasharray = String(umfang);
  bogen.style.strokeDashoffset = String(umfang);
  if (anteil <= 0) bogen.style.display = "none";
  else nachBild(() => { bogen.style.strokeDashoffset = String(umfang * (1 - anteil)); });

  const svg = s("svg", { class: "ch-ring-svg", viewBox: `0 0 ${mass} ${mass}`, "aria-hidden": "true", focusable: "false" },
    s("defs", null, s("linearGradient", { id, x1: 0, y1: 0, x2: 1, y2: 1 },
      s("stop", { offset: "0%", class: "ch-stop-a" }), s("stop", { offset: "100%", class: "ch-stop-b" }))),
    s("circle", { class: "ch-ring-spur", cx: mass / 2, cy: mass / 2, r: radius, "stroke-width": dicke }),
    bogen);

  const unten = z ? `von ${zahl(Math.round(z))}${einheit ? ` ${einheit}` : ""}` : einheit;
  const beschreibung = `${titel ? `${titel}: ` : ""}${zahl(Math.round(w))}${z ? ` von ${zahl(Math.round(z))}` : ""}${einheit ? ` ${einheit}` : ""}`;
  const klassen = ["ch-ring", `ch-f-${farbe}`, ueber ? "ch-ueber" : "", erreicht ? "ch-erreicht" : "", z ? "" : "ch-ohne-ziel"].filter(Boolean).join(" ");
  return h("div", { class: klassen, role: "img", "aria-label": beschreibung, style: groesse ? { "--ring-s": `${groesse}px` } : null },
    svg,
    h("div", { class: "ch-ring-mitte" },
      h("span", { class: "ch-ring-zahl" }, zahl(Math.round(w))),
      unten ? h("span", { class: "ch-ring-text" }, unten) : null));
}

/**
 * Liniendiagramm mit Verlaufsfläche, gestrichelter Ziel-Linie, Beschriftung von Höchst- und Tiefstwert sowie
 * erstem und letztem Punkt, Hervorhebung des letzten Werts und Hinweis beim Darüberfahren (Maus oder Finger).
 * punkte: [{ x: Beschriftung, y: Zahl, t: optional Zeitpunkt als Zahl }]. Mit t auf allen Punkten liegen sie im
 * richtigen zeitlichen Abstand, sonst gleichmäßig verteilt. Das Diagramm zeichnet sich neu, wenn sich die Breite ändert.
 */
export function linie({ punkte = [], ziel = null, hoehe = 180, einheit = "", stellen = 1, titel = "" } = {}) {
  const daten = (punkte || [])
    .filter((p) => p && typeof p.y === "number" && Number.isFinite(p.y))
    .map((p) => ({ x: String(p.x ?? ""), y: p.y, t: typeof p.t === "number" && Number.isFinite(p.t) ? p.t : null }));
  const z = positiv(ziel);
  const fmt = (v) => zahl(v, stellen);
  const mitEinheit = (v) => (einheit ? `${fmt(v)} ${einheit}` : fmt(v));
  const letzterWert = daten.length ? daten[daten.length - 1] : null;
  const wurzel = h("div", {
    class: "ch-linie", style: { height: `${hoehe}px` }, role: "img",
    "aria-label": letzterWert ? `${titel ? `${titel}: ` : ""}${daten.length} Werte, zuletzt ${mitEinheit(letzterWert.y)}${letzterWert.x ? ` (${letzterWert.x})` : ""}` : titel,
  });
  if (!daten.length) return wurzel;

  const tip = h("div", { class: "ch-tip", hidden: true });
  let aktuell = null;
  let letzteBreite = 0;

  function zeichnen(breite) {
    letzteBreite = breite;
    const b = Math.max(160, Math.round(breite));
    const oben = 12, unten = 26, links = 6, rechts = 6;
    const innenB = b - links - rechts;
    const innenH = hoehe - oben - unten;
    const werte = daten.map((p) => p.y);
    const dMin = Math.min(...werte);
    const dMax = Math.max(...werte);
    const alle = z === null ? werte : [...werte, z];
    let lo = Math.min(...alle);
    let hi = Math.max(...alle);
    const rand = Math.max((hi - lo) * 0.18, Math.abs(hi) * 0.006, 1e-6);
    lo -= rand;
    hi += rand;
    const y = (v) => oben + (1 - (v - lo) / (hi - lo)) * innenH;
    const zeiten = daten.every((p) => p.t !== null) ? daten.map((p) => p.t) : null;
    const t0 = zeiten ? Math.min(...zeiten) : 0;
    const t1 = zeiten ? Math.max(...zeiten) : daten.length - 1;
    const x = (i) => (daten.length === 1 || t1 === t0 ? links + innenB / 2 : links + (((zeiten ? zeiten[i] : i) - t0) / (t1 - t0)) * innenB);
    const xy = daten.map((p, i) => [x(i), y(p.y)]);
    const f = (n) => n.toFixed(1);
    const pfad = xy.map(([px, py], i) => `${i ? "L" : "M"}${f(px)} ${f(py)}`).join(" ");
    const grund = f(hoehe - unten);
    const erster = xy[0];
    const letzter = xy[xy.length - 1];
    const gid = neueId("ch-fl");
    const gespreizt = dMax - dMin > 1e-9;

    const kinder = [
      s("defs", null, s("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
        s("stop", { offset: "0%", class: "ch-fl-oben" }), s("stop", { offset: "100%", class: "ch-fl-unten" }))),
      s("line", { class: "ch-gitter", x1: links, x2: b - rechts, y1: f(y(dMax)), y2: f(y(dMax)) }),
      gespreizt ? s("line", { class: "ch-gitter", x1: links, x2: b - rechts, y1: f(y(dMin)), y2: f(y(dMin)) }) : null,
      s("text", { class: "ch-text", x: links, y: f(y(dMax) - 5) }, fmt(dMax)),
      gespreizt ? s("text", { class: "ch-text", x: links, y: f(y(dMin) + 14) }, fmt(dMin)) : null,
    ];
    if (z !== null) {
      const yz = y(z);
      const yText = yz - 5 < 11 ? yz + 14 : yz - 5;
      kinder.push(
        s("line", { class: "ch-ziel-pfad", x1: links, x2: b - rechts, y1: f(yz), y2: f(yz) }),
        s("text", { class: "ch-text ch-text-ziel", x: b - rechts, y: f(yText), "text-anchor": "end" }, `Ziel ${mitEinheit(z)}`));
    }
    if (daten.length > 1) kinder.push(s("path", { class: "ch-flaeche", d: `${pfad} L${f(letzter[0])} ${grund} L${f(erster[0])} ${grund} Z`, fill: `url(#${gid})` }));
    kinder.push(s("path", { class: "ch-pfad", d: pfad }));
    if (daten.length <= 14) for (const [px, py] of xy.slice(0, -1)) kinder.push(s("circle", { class: "ch-punkt", cx: f(px), cy: f(py), r: 3 }));
    kinder.push(
      s("circle", { class: "ch-halo", cx: f(letzter[0]), cy: f(letzter[1]), r: 9 }),
      s("circle", { class: "ch-punkt ch-letzter", cx: f(letzter[0]), cy: f(letzter[1]), r: 4.5 }),
      s("text", { class: "ch-text", x: links, y: hoehe - 7 }, daten[0].x));
    if (daten.length > 1) kinder.push(s("text", { class: "ch-text", x: b - rechts, y: hoehe - 7, "text-anchor": "end" }, daten[daten.length - 1].x));

    const fuehrung = s("line", { class: "ch-fuehrung", x1: 0, x2: 0, y1: oben, y2: f(hoehe - unten) });
    const marke = s("circle", { class: "ch-marke", cx: 0, cy: 0, r: 5.5 });
    fuehrung.style.display = "none";
    marke.style.display = "none";
    kinder.push(fuehrung, marke);

    const svg = s("svg", { width: b, height: hoehe, viewBox: `0 0 ${b} ${hoehe}`, "aria-hidden": "true", focusable: "false" }, ...kinder);
    wurzel.replaceChildren(svg, tip);
    tip.hidden = true;
    aktuell = { xy, b, fuehrung, marke };
  }

  function zeigen(ev) {
    if (!aktuell) return;
    const px = ev.clientX - wurzel.getBoundingClientRect().left;
    let nah = 0;
    for (let i = 1; i < aktuell.xy.length; i++) {
      if (Math.abs(aktuell.xy[i][0] - px) < Math.abs(aktuell.xy[nah][0] - px)) nah = i;
    }
    const [cx, cy] = aktuell.xy[nah];
    aktuell.fuehrung.setAttribute("x1", cx.toFixed(1));
    aktuell.fuehrung.setAttribute("x2", cx.toFixed(1));
    aktuell.fuehrung.style.display = "";
    aktuell.marke.setAttribute("cx", cx.toFixed(1));
    aktuell.marke.setAttribute("cy", cy.toFixed(1));
    aktuell.marke.style.display = "";
    tip.replaceChildren(h("b", null, mitEinheit(daten[nah].y)), h("span", null, daten[nah].x));
    tip.hidden = false;
    tip.classList.toggle("unten", cy < 56);
    tip.style.left = `${klemmen(cx, 56, aktuell.b - 56)}px`;
    tip.style.top = `${cy}px`;
  }

  function verbergen() {
    if (!aktuell) return;
    aktuell.fuehrung.style.display = "none";
    aktuell.marke.style.display = "none";
    tip.hidden = true;
  }

  wurzel.addEventListener("pointermove", zeigen);
  wurzel.addEventListener("pointerdown", zeigen);
  wurzel.addEventListener("pointerleave", verbergen);
  wurzel.addEventListener("pointercancel", verbergen);

  zeichnen(560);
  if (typeof ResizeObserver === "function") {
    let warImDokument = false;
    const beobachter = new ResizeObserver(() => {
      if (!wurzel.isConnected) {
        if (warImDokument) beobachter.disconnect();  // aus der Seite genommen: nichts mehr beobachten
        return;
      }
      warImDokument = true;
      const b = wurzel.clientWidth;
      if (b > 0 && Math.abs(b - letzteBreite) > 0.5) zeichnen(b);
    });
    beobachter.observe(wurzel);
  }
  return wurzel;
}

/**
 * Säulendiagramm aus HTML (Text bleibt bei jeder Breite scharf). werte: [{ label, wert, hervor }], ziel: gestrichelte Linie.
 * Säulen über dem Ziel bekommen die Klasse ch-ueber. Die höchste Säule füllt 84 Prozent der Fläche, der Rest ist für den Wert darüber.
 */
export function saeulen({ werte = [], ziel = null, hoehe = 170, titel = "", format = (v) => zahl(Math.round(v)) } = {}) {
  const liste = (werte || []).map((w) => ({ label: String(w?.label ?? ""), wert: Math.max(0, zahlOder0(w?.wert)), hervor: !!w?.hervor }));
  const z = positiv(ziel);
  const hoechst = Math.max(1, z ?? 0, ...liste.map((w) => w.wert));
  const prozent = (v) => ((v / hoechst) * 84).toFixed(2);
  const balken = [];

  const spalten = liste.map((w) => {
    const ueber = z !== null && w.wert > z;
    const b = h("div", { class: "ch-balken" }, h("span", { class: "ch-wert" }, w.wert > 0 ? format(w.wert) : "–"));
    b.style.setProperty("--h", "0");
    balken.push([b, prozent(w.wert)]);
    return h("div", { class: ["ch-spalte", w.hervor ? "hervor" : "", ueber ? "ch-ueber" : "", w.wert > 0 ? "" : "leer"].filter(Boolean).join(" "), title: `${w.label}: ${w.wert > 0 ? format(w.wert) : "nichts eingetragen"}` }, b);
  });

  nachBild(() => { for (const [b, p] of balken) b.style.setProperty("--h", p); });

  const beschreibung = `${titel ? `${titel}: ` : ""}${liste.map((w) => `${w.label} ${w.wert > 0 ? format(w.wert) : "nichts"}`).join(", ")}`;
  return h("div", { class: "ch-saeulen", style: { "--n": liste.length || 1, "--hoehe": `${hoehe}px` }, role: "img", "aria-label": beschreibung },
    h("div", { class: "ch-feld" },
      z !== null ? h("div", { class: "ch-ziel", style: { "--z": prozent(z) } }) : null,
      h("div", { class: "ch-reihe" }, ...spalten)),
    h("div", { class: "ch-achse" }, ...liste.map((w) => h("span", { class: w.hervor ? "hervor" : "" }, w.label))));
}
