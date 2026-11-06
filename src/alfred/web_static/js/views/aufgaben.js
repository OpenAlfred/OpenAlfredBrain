// Aufgaben: schnell anlegen, nach Fälligkeit gruppiert, abhaken.
import { h, icon, api, faelligkeit, tageBis, leeren, zaehlerLaden, fehler, toast, plural, pad, wochentagKurz, juengste } from "../core.js";
import { seitenKopf, kartenKopf, leer, skelett, fehlerKarte, checkKnopf } from "../teile.js";

const GRUPPEN = [
  { titel: "Überfällig", symbol: "alert-triangle", art: "danger", passt: (t) => t !== null && t < 0 },
  { titel: "Heute", symbol: "sun", art: "warn", passt: (t) => t === 0 },
  { titel: "Diese Woche", symbol: "calendar", art: "info", passt: (t) => t !== null && t > 0 && t <= 6 },
  { titel: "Später", symbol: "clock", art: "", passt: (t) => t !== null && t > 6 },
  { titel: "Ohne Datum", symbol: "list", art: "", passt: (t) => t === null },
];

const isoDatum = (tageAb) => {
  const d = new Date();
  d.setDate(d.getDate() + tageAb);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

export function mount(wurzel) {
  const unter = h("span", null);
  const kopf = seitenKopf("Aufgaben", "");
  kopf.querySelector("div").append(h("p", null, unter));

  const text = h("input", { type: "text", placeholder: "Neue Aufgabe …", autocomplete: "off", enterkeyhint: "done", "aria-label": "Neue Aufgabe", maxLength: 200 });
  // Das native Datumsfeld liegt unsichtbar über einem Knopf: Ein Tipp öffnet überall den Kalender, und der Knopf zeigt das Datum lesbar.
  const datum = h("input", { type: "date", "aria-label": "Fällig am" });
  datum.addEventListener("click", () => { try { datum.showPicker?.(); } catch { /* ältere Browser öffnen den Kalender selbst */ } });
  const datumText = h("span", null, "Fällig");
  const datumWeg = h("button", { class: "datum-weg", type: "button", hidden: true, "aria-label": "Datum entfernen", onclick: (ev) => {
    ev.preventDefault();
    datum.value = "";
    abgleichen();
  } }, icon("x"));
  const datumKnopf = h("label", { class: "datum-knopf", title: "Fälligkeit wählen" }, icon("calendar"), datumText, datum, datumWeg);
  const schnell = [["Heute", 0], ["Morgen", 1], ["In einer Woche", 7]].map(([name, tage]) =>
    h("button", { class: "chip", type: "button", "aria-pressed": "false", onclick: (ev) => {
      const an = ev.currentTarget.getAttribute("aria-pressed") !== "true";
      datum.value = an ? isoDatum(tage) : "";
      abgleichen();
      text.focus();
    }, "data-tage": tage }, name));
  function abgleichen() {
    for (const c of schnell) c.setAttribute("aria-pressed", String(datum.value && datum.value === isoDatum(Number(c.dataset.tage))));
    const gesetzt = !!datum.value;
    datumKnopf.classList.toggle("gesetzt", gesetzt);
    datumWeg.hidden = !gesetzt;
    if (gesetzt) {
      const [j, m, t] = datum.value.split("-").map(Number);
      datumText.textContent = `${wochentagKurz(new Date(j, m - 1, t))}, ${t}.${m}.`;
    } else datumText.textContent = "Fällig";
  }
  datum.addEventListener("input", abgleichen);

  let legtAn = false;  // ein zweiter Tipp oder Enter vor der Antwort legte die Aufgabe sonst doppelt an
  const formular = h("form", { class: "stack", style: { "--gap": "10px" }, onsubmit: async (ev) => {
    ev.preventDefault();
    const t = text.value.trim();
    if (!t || legtAn) return;
    legtAn = true;
    try {
      await api("aufgaben", "POST", { text: t, faellig: datum.value });
      text.value = "";
      datum.value = "";
      abgleichen();
      toast("Aufgabe angelegt", "ok", 1800);
      laden();
      zaehlerLaden();
    } catch (e) { fehler(e); } finally { legtAn = false; }
  } },
    h("div", { class: "composer" }, icon("plus"), text, datumKnopf, h("button", { class: "btn primary icon", type: "submit", "aria-label": "Aufgabe anlegen" }, icon("arrow-up"))),
    h("div", { class: "row wrap", style: { "--gap": "8px" } }, h("span", { class: "hint" }, "Schnell:"), ...schnell));

  const liste = h("div", { class: "spalten" }, h("div", { class: "card" }, skelett(3)));
  wurzel.append(kopf, h("div", { class: "aufgaben-spalte" }, formular, liste));

  const marke = juengste();
  async function laden() {
    const gilt = marke();  // Abhaken und Anlegen laden sofort neu: nur die jüngste Antwort darf zeichnen
    let d;
    try { d = await api("aufgaben"); } catch (e) { if (gilt()) leeren(liste, h("div", { class: "card" }, fehlerKarte(e, laden))); return; }
    if (!gilt()) return;
    const offene = d.offene;
    const ueber = offene.filter((a) => a.faellig && tageBis(a.faellig) < 0).length;
    unter.textContent = offene.length ? `${plural(offene.length, "offene Aufgabe", "offene Aufgaben")}${ueber ? ` · ${ueber} überfällig` : ""}` : "Nichts offen. Gut gemacht.";
    if (!offene.length) { leeren(liste, h("div", { class: "card" }, leer("check-square", "Alles erledigt", "Neue Aufgaben legst du oben an oder sagst sie Alfred."))); return; }
    const karten = [];
    for (const g of GRUPPEN) {
      const mit = offene.filter((a) => g.passt(a.faellig ? tageBis(a.faellig) : null)).sort((a, b) => (a.faellig || "9999").localeCompare(b.faellig || "9999"));
      if (!mit.length) continue;
      karten.push(h("section", { class: "card flush" },
        h("div", { class: "card-h" }, h("div", { class: "card-title" }, h("span", { class: `lead ${g.art}`, style: { width: "26px", height: "26px", fontSize: ".9rem" } }, icon(g.symbol)), g.titel), h("span", { class: "spacer" }), h("span", { class: `badge ${g.art}` }, mit.length)),
        h("div", { class: "list" }, ...mit.map((a) => {
          const f = faelligkeit(a.faellig);
          const zeile = h("div", { class: "item" },
            checkKnopf(false, async () => {
              zeile.classList.add("weg");
              try { await api("aufgaben", "PATCH", { nummer: a.nummer }); } catch (e) { fehler(e); }
              setTimeout(laden, 280);
              zaehlerLaden();
            }, `„${a.text}“ erledigt`),
            h("div", { class: "item-main" }, h("div", { class: "item-title" }, a.text), a.faellig ? h("div", { class: "item-sub" }, `fällig ${a.faellig.slice(8, 10)}.${a.faellig.slice(5, 7)}.${a.faellig.slice(0, 4)}`) : null),
            f.text && g.titel !== "Ohne Datum" ? h("span", { class: `badge ${f.art}` }, f.text) : null);
          return zeile;
        }))));
    }
    leeren(liste, ...karten);
  }

  return { laden, takt: 60000 };
}
