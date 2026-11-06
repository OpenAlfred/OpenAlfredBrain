// Einkauf: Liste nach Abteilungen in der Reihenfolge des Supermarkts, schnell füllen, im Laden abhaken.
import { h, icon, api, leeren, zaehlerLaden, fehler, toast, bestaetigen, plural, juengste } from "../core.js";
import { seitenKopf, leer, skelett, fehlerKarte, checkKnopf } from "../teile.js";

// Symbol und Tönung je Abteilung; unbekannte bekommen das Etikett.
const ABTEILUNG = {
  "Obst & Gemüse": { symbol: "apple", art: "ok" },
  "Brot & Backwaren": { symbol: "flame", art: "warn" },
  "Kühlregal": { symbol: "thermometer", art: "info" },
  "Fleisch & Fisch": { symbol: "utensils", art: "danger" },
  "Trocken & Konserven": { symbol: "package", art: "violet" },
  "Tiefkühl": { symbol: "cloud-snow", art: "info" },
  "Getränke": { symbol: "droplet", art: "accent" },
  "Haushalt & Drogerie": { symbol: "home", art: "accent" },
};
const STANDARD = { symbol: "tag", art: "" };

export function mount(wurzel) {
  const unter = h("span", null);
  const kopf = seitenKopf("Einkauf", "");
  kopf.querySelector("div").append(h("p", null, unter));
  const aktionen = h("div", { class: "page-actions" });
  kopf.append(aktionen);

  const text = h("input", { type: "text", placeholder: "2 Brot, Milch und Äpfel …", autocomplete: "off", enterkeyhint: "done", "aria-label": "Artikel hinzufügen" });
  const formular = h("form", { class: "composer", onsubmit: async (ev) => {
    ev.preventDefault();
    const t = text.value.trim();
    if (!t) return;
    text.value = "";
    try {
      const r = await api("einkauf", "POST", { text: t });
      toast(r.hinzugefuegt.length ? `Dazu: ${r.hinzugefuegt.join(", ")}` : "Steht schon drauf", "ok", 2200);
      laden();
      zaehlerLaden();
    } catch (e) {
      if (!text.value) text.value = t;  // schlechter Empfang im Laden: der Satz bleibt stehen
      fehler(e);
    }
  } }, icon("plus"), text, h("button", { class: "btn primary", type: "submit" }, "Hinzufügen"));

  const abteilungen = h("div", { class: "einkauf-raster" }, skelett(3, 60));
  const erledigt = h("section", { class: "card flush", hidden: true });
  wurzel.append(kopf, h("div", { class: "stack", style: { "--gap": "18px" } }, formular, abteilungen, erledigt));

  async function haken(name, an) {
    try { await api("einkauf", "PATCH", { name, erledigt: an }); } catch (e) { fehler(e); }
    laden();
    zaehlerLaden();
  }

  const artikelZeile = (a, fertig) => {
    const zeile = h("div", { class: `item${fertig ? " done" : ""}` },
      checkKnopf(fertig, () => { zeile.classList.add("weg"); setTimeout(() => haken(a.name, !fertig), 220); }, `${a.name} ${fertig ? "wieder auf die Liste" : "abhaken"}`),
      h("div", { class: "item-main" }, h("div", { class: "item-title" }, a.name)),
      a.menge ? h("span", { class: "badge outline" }, a.menge) : null);
    return zeile;
  };

  const marke = juengste();
  async function laden() {
    const gilt = marke();  // Im Laden wird schnell abgehakt: nur die jüngste Antwort darf zeichnen
    let d;
    try { d = await api("einkauf"); } catch (e) { if (gilt()) leeren(abteilungen, h("div", { class: "card" }, fehlerKarte(e, laden))); return; }
    if (!gilt()) return;
    const namen = Object.keys(d.abteilungen);
    const offen = namen.reduce((n, abt) => n + d.abteilungen[abt].length, 0);
    unter.textContent = offen ? `${plural(offen, "Artikel", "Artikel")} offen, nach Abteilungen wie im Laden sortiert` : "Die Liste ist leer.";

    leeren(aktionen, offen || d.erledigte.length ? h("button", { class: "btn", type: "button", onclick: async () => {
      if (!(await bestaetigen({ titel: "Ganze Liste leeren?", text: "Alle Artikel verschwinden, auch die noch offenen.", ok: "Alles entfernen", gefahr: true }))) return;
      try { await api("einkauf?alles=true", "DELETE"); } catch (e) { fehler(e); }
      laden();
      zaehlerLaden();
    } }, icon("trash-2"), h("span", { class: "hide-m" }, "Liste leeren")) : null);

    leeren(abteilungen, ...(namen.length ? namen.map((abt) => {
      const a = ABTEILUNG[abt] || STANDARD;
      return h("section", { class: "card flush" },
        h("div", { class: "card-h" }, h("div", { class: "card-title" }, h("span", { class: `lead ${a.art}`, style: { width: "28px", height: "28px", fontSize: ".95rem" } }, icon(a.symbol)), abt),
          h("span", { class: "spacer" }), h("span", { class: "badge" }, d.abteilungen[abt].length)),
        h("div", { class: "list" }, ...d.abteilungen[abt].map((x) => artikelZeile(x, false))));
    }) : [h("div", { class: "card einkauf-leer" }, leer("shopping-cart", "Die Liste ist leer", "Oben eintippen oder per Stimme sagen: „Setz Milch und 2 Brot auf die Einkaufsliste.“"))]));

    erledigt.hidden = !d.erledigte.length;
    leeren(erledigt,
      h("div", { class: "card-h" }, h("div", { class: "card-title" }, icon("check-circle"), "Erledigt"), h("span", { class: "spacer" }), h("span", { class: "badge" }, d.erledigte.length),
        h("button", { class: "btn sm", type: "button", onclick: async () => { try { await api("einkauf", "DELETE"); } catch (e) { fehler(e); } laden(); zaehlerLaden(); } }, icon("trash-2"), "Erledigte entfernen")),
      h("div", { class: "list" }, ...d.erledigte.map((x) => artikelZeile(x, true))));
  }

  return { laden, takt: 30000 };
}
