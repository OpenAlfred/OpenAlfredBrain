// Haushalt: Wochenplan mit Rezepten, Ausgaben des Monats und Vorrat mit Ablaufdaten.
// Rezepte, Zutaten, Läden und Artikelnamen stammen aus Modellen und Fotos und kommen nur als Text in die Seite, nie als HTML.
import {
  h, icon, api, leeren, toast, fehler, zaehlerLaden, bestaetigen,
  datumKurz, euro, faelligkeit, plural, tageBis, zahl, lokalesDatum, kommaZahl, fotoVerkleinern, beschaeftigt,
} from "../core.js";
import { seitenKopf, kartenKopf, leer, skelett, fehlerKarte } from "../teile.js";

const KATEGORIEN = ["Lebensmittel", "Essen gehen", "Haushalt", "Mobilität", "Freizeit", "Kleidung", "Gesundheit", "Sonstiges"];
const REITER = [
  ["wochenplan", "Wochenplan", "calendar"],
  ["ausgaben", "Ausgaben", "wallet"],
  ["vorrat", "Vorrat", "package"],
];

const num = (x) => { const n = Number(x); return Number.isFinite(n) ? n : 0; };
const volleDatum = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
const feinerZeiger = () => !!(window.matchMedia && window.matchMedia("(pointer: fine)").matches);

/** Modelle liefern Zutaten und Schritte mal als Text, mal als Objekt oder Zahl: immer zu Text machen. */
function alsText(x) {
  if (x == null) return "";
  if (typeof x === "string") return x.trim();
  if (typeof x === "number") return String(x);
  if (typeof x === "object") return Object.values(x).map(alsText).filter(Boolean).join(" ");
  return "";
}
const alsListe = (x) => (Array.isArray(x) ? x : x ? [x] : []).map(alsText).filter(Boolean);

/** "Heute", "Gestern" oder "05.10." für ein Datum der Vergangenheit. */
function tagLabel(iso) {
  if (!iso) return "";
  const tage = tageBis(iso);
  return tage === 0 ? "Heute" : tage === -1 ? "Gestern" : datumKurz(iso);
}

/** Wann ein Artikel abläuft, in Worten; rot, wenn er bald abläuft oder schon abgelaufen ist. */
function ablaufInfo(ablauf, dringend) {
  const f = faelligkeit(ablauf);
  let text;
  if (f.tage < 0) text = f.tage === -1 ? "seit gestern abgelaufen" : `seit ${-f.tage} Tagen abgelaufen`;
  else if (f.tage === 0) text = "läuft heute ab";
  else if (f.tage === 1) text = "läuft morgen ab";
  else if (f.tage === 2) text = "läuft übermorgen ab";
  else text = `bis ${f.text}`;
  return { text, art: dringend || f.tage < 0 ? "danger" : "" };
}

/** Antwort von /api/haushalt bereinigen: fehlende Felder und falsche Typen verkraften. */
function normieren(d) {
  const a = d?.ausgaben || {};
  return {
    summe: num(a.summe),
    anzahl: num(a.anzahl),
    kategorien: Object.entries(a.je_kategorie && typeof a.je_kategorie === "object" ? a.je_kategorie : {})
      .map(([name, betrag]) => [name, num(betrag)])
      .filter(([, betrag]) => betrag > 0)
      .sort((x, y) => y[1] - x[1]),
    liste: (Array.isArray(d?.ausgaben_liste) ? d.ausgaben_liste : [])
      .filter((x) => x && typeof x === "object")
      .map((x) => ({ datum: String(x.datum ?? ""), betrag: num(x.betrag), kategorie: String(x.kategorie ?? "Sonstiges"), text: String(x.text ?? "").trim() })),
    vorrat: (Array.isArray(d?.vorrat) ? d.vorrat : [])
      .filter((v) => v && typeof v === "object" && v.name)
      .map((v) => ({ name: String(v.name), menge: String(v.menge ?? "").trim(), ablauf: v.ablauf ? String(v.ablauf).slice(0, 10) : "" })),
    dringend: (Array.isArray(d?.ablaufend) ? d.ablaufend : []).map(String),
    plan: (Array.isArray(d?.wochenplan) ? d.wochenplan : [])
      .filter((t) => t && typeof t === "object")
      .map((t) => ({ tag: alsText(t.tag), gericht: alsText(t.gericht), dauer: num(t.dauer_minuten), kcal: num(t.kcal), zutaten: alsListe(t.zutaten), schritte: alsListe(t.schritte) })),
  };
}

export function mount(wurzel) {
  wurzel.classList.add("v-haushalt");

  let daten = null;
  let ladeNr = 0;
  let reiter = "wochenplan";
  const offen = new Set();   // aufgeklappte Tage des Plans; bleiben beim Neuladen offen
  const gesehen = {};        // Stand je Abschnitt: Unverändertes wird nicht neu gezeichnet

  function einmal(name, stand, zeichnen) {
    const merke = JSON.stringify(stand);
    if (gesehen[name] === merke) return;
    gesehen[name] = merke;
    zeichnen();
  }

  // --- Reiter -------------------------------------------------------------------------------------------------------
  const reiterKnoepfe = {};
  const zaehlerAnzeigen = {};
  const leiste = h("div", { class: "seg hh-tabs", role: "tablist", "aria-label": "Bereich im Haushalt" },
    ...REITER.map(([id, text, symbol]) => {
      const zaehler = h("span", { class: "count", hidden: true });
      zaehlerAnzeigen[id] = zaehler;
      const knopf = h("button", {
        type: "button", role: "tab", id: `hh-reiter-${id}`, "aria-controls": `hh-panel-${id}`, "aria-selected": "false",
        onclick: () => reiterWaehlen(id, true),
      }, icon(symbol), text, zaehler);
      reiterKnoepfe[id] = knopf;
      return knopf;
    }));
  leiste.addEventListener("keydown", (ev) => {
    const schritt = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
    if (!schritt) return;
    ev.preventDefault();
    const i = REITER.findIndex(([id]) => id === reiter);
    const neu = REITER[(i + schritt + REITER.length) % REITER.length][0];
    reiterWaehlen(neu, true);
    reiterKnoepfe[neu].focus();
  });

  // --- Wochenplan: Formular und Plan ---------------------------------------------------------------------------------
  const wunschFeld = h("input", { type: "text", placeholder: "Wünsche: eiweißreich, unter 30 Minuten …", autocomplete: "off", enterkeyhint: "go", maxlength: 300, "aria-label": "Wünsche für den Wochenplan" });
  const planKnopf = h("button", { class: "btn primary", type: "submit" }, icon("sparkles"), "Planen");
  const planBereich = h("div", null, skelett(3, 96));
  const panelWochenplan = h("section", { class: "hh-panel", role: "tabpanel", id: "hh-panel-wochenplan", "aria-labelledby": "hh-reiter-wochenplan" },
    h("form", { class: "composer hh-composer", onsubmit: planen }, icon("calendar"), wunschFeld, planKnopf),
    h("p", { class: "hint" }, icon("info"), " Alfred plant fünf Abendessen für eine Person. Das dauert bis zu 20 Sekunden und ersetzt den bisherigen Plan."),
    planBereich);

  // --- Ausgaben: Summe, Formular, Liste -------------------------------------------------------------------------------
  const betragFeld = h("input", { class: "input", id: "hh-betrag", type: "text", inputmode: "decimal", autocomplete: "off", placeholder: "0,00" });
  const kategorieWahl = h("select", { class: "select", id: "hh-kategorie" }, ...KATEGORIEN.map((k) => h("option", { value: k }, k)));
  const zweckFeld = h("input", { class: "input", id: "hh-zweck", type: "text", autocomplete: "off", enterkeyhint: "done", maxlength: 120, placeholder: "zum Beispiel Mittagessen" });
  const ausgabeKnopf = h("button", { class: "btn primary", type: "submit" }, icon("plus"), "Eintragen");
  const bonDatei = h("input", { type: "file", accept: "image/*", capture: "environment", hidden: true, "aria-hidden": "true", tabindex: "-1" });
  const bonKnopf = h("button", { class: "btn", type: "button", onclick: () => bonDatei.click() }, icon("camera"), "Kassenbon fotografieren");
  const bonErgebnis = h("div", { role: "status", hidden: true });
  bonDatei.addEventListener("change", bonAbsenden);
  const feld = (beschriftung, id, steuerung, klasse = "") => h("div", { class: `field ${klasse}`.trim() }, h("label", { for: id }, beschriftung), steuerung);

  const summeKarte = h("section", { class: "card", "aria-label": "Ausgaben im Monat" }, skelett(2, 56));
  const ausgabenFormKarte = h("section", { class: "card", "aria-label": "Ausgabe eintragen" },
    kartenKopf("Ausgabe eintragen", "plus"),
    h("form", { class: "hh-felder", onsubmit: ausgabeAbsenden },
      feld("Betrag", "hh-betrag", h("div", { class: "hh-geld" }, betragFeld)),
      feld("Kategorie", "hh-kategorie", kategorieWahl),
      feld("Wofür", "hh-zweck", zweckFeld, "voll"),
      ausgabeKnopf),
    h("div", { class: "hh-bon" },
      bonKnopf, bonDatei, bonErgebnis,
      h("p", { class: "hint" }, icon("info"), " Alfred liest Laden und Summe vom Kassenbon und trägt die Ausgabe ein.")));
  const ausgabenListeKarte = h("section", { class: "card flush", "aria-label": "Letzte Ausgaben" }, h("div", { style: { padding: "18px" } }, skelett(4, 48)));
  const panelAusgaben = h("section", { class: "hh-panel", role: "tabpanel", id: "hh-panel-ausgaben", "aria-labelledby": "hh-reiter-ausgaben", hidden: true },
    h("div", { class: "hh-zwei" },
      h("div", { class: "hh-spalte" }, summeKarte, ausgabenFormKarte),
      h("div", { class: "hh-spalte" }, ausgabenListeKarte)));

  // --- Vorrat: Hinweis, Formular, Liste --------------------------------------------------------------------------------
  const nameFeld = h("input", { class: "input", id: "hh-artikel", type: "text", autocomplete: "off", enterkeyhint: "next", maxlength: 80, placeholder: "zum Beispiel Haferflocken" });
  const mengeFeld = h("input", { class: "input", id: "hh-menge", type: "text", autocomplete: "off", enterkeyhint: "next", maxlength: 40, placeholder: "zum Beispiel 2 Pack" });
  const ablaufFeld = h("input", { class: "input date", id: "hh-ablauf", type: "date" });
  const vorratKnopf = h("button", { class: "btn primary", type: "submit" }, icon("plus"), "Hinzufügen");
  const vorratHinweis = h("div", { hidden: true });
  const vorratFormKarte = h("section", { class: "card", "aria-label": "Artikel hinzufügen" },
    kartenKopf("Artikel hinzufügen", "plus"),
    h("form", { class: "hh-felder gleich", onsubmit: vorratAbsenden },
      feld("Artikel", "hh-artikel", nameFeld, "voll"),
      feld("Menge (optional)", "hh-menge", mengeFeld),
      feld("Läuft ab am (optional)", "hh-ablauf", ablaufFeld),
      vorratKnopf));
  const vorratListeKarte = h("section", { class: "card flush", "aria-label": "Im Vorrat" }, h("div", { style: { padding: "18px" } }, skelett(4, 48)));
  const panelVorrat = h("section", { class: "hh-panel", role: "tabpanel", id: "hh-panel-vorrat", "aria-labelledby": "hh-reiter-vorrat", hidden: true },
    h("div", { class: "hh-zwei" },
      h("div", { class: "hh-spalte" }, vorratFormKarte),
      h("div", { class: "hh-spalte" }, vorratHinweis, vorratListeKarte)));

  const fehlerBox = h("div", { class: "hh-fehler", hidden: true });
  const inhalt = h("div", null, leiste, panelWochenplan, panelAusgaben, panelVorrat);
  wurzel.append(seitenKopf("Haushalt", "Wochenplan, Ausgaben und Vorrat."), fehlerBox, inhalt);

  function reiterWaehlen(id, adresse = false) {
    if (!REITER.some(([r]) => r === id)) return;  // Adressen wie #/haushalt/xyz lassen den Reiter, wie er ist
    reiter = id;
    for (const [k, knopf] of Object.entries(reiterKnoepfe)) {
      knopf.setAttribute("aria-selected", String(k === id));
      knopf.tabIndex = k === id ? 0 : -1;
    }
    panelWochenplan.hidden = id !== "wochenplan";
    panelAusgaben.hidden = id !== "ausgaben";
    panelVorrat.hidden = id !== "vorrat";
    // Ohne hashchange, sonst würde die Ansicht neu betreten und neu geladen
    if (adresse) history.replaceState(null, "", `#/haushalt/${id}`);
  }

  // --- Aktionen ------------------------------------------------------------------------------------------------------------
  async function planen(ev) {
    ev.preventDefault();
    // Ein vorhandener Plan wird ersetzt: lieber einmal nachfragen, als ihn aus Versehen zu verlieren.
    if (daten?.plan?.length && !(await bestaetigen({ titel: "Neuen Plan erstellen?", text: "Der bisherige Wochenplan wird durch den neuen ersetzt.", ok: "Neu planen" }))) return;
    planBereich.classList.add("hh-arbeitet");
    try {
      await beschaeftigt(planKnopf, "Alfred plant …", () => api("wochenplan", "POST", { wuensche: wunschFeld.value.trim(), tage: 5 }));
      offen.clear();
      toast("Plan steht", "ok");
      await laden();
    } catch (e) {
      fehler(e);
    } finally {
      planBereich.classList.remove("hh-arbeitet");
    }
  }

  async function einkaufen(knopf) {
    try {
      const r = await beschaeftigt(knopf, "Setze auf die Liste …", () => api("wochenplan/einkaufen", "POST", {}));
      const n = Array.isArray(r?.hinzugefuegt) ? r.hinzugefuegt.length : 0;
      if (n) toast(`${plural(n, "Zutat", "Zutaten")} auf der Einkaufsliste`, "ok", 5200);
      else toast("Nichts Neues: Alles steht schon auf der Liste oder ist im Vorrat.", "info", 5200);
      zaehlerLaden();
    } catch (e) {
      fehler(e);
    }
  }

  async function ausgabeAbsenden(ev) {
    ev.preventDefault();
    const betrag = kommaZahl(betragFeld.value);
    if (!Number.isFinite(betrag) || betrag <= 0) {
      toast("Gib einen Betrag über 0 € ein, zum Beispiel 12,50.", "err");
      betragFeld.focus();
      return;
    }
    try {
      await beschaeftigt(ausgabeKnopf, "Speichere …", () => api("ausgaben", "POST", { betrag, kategorie: kategorieWahl.value, text: zweckFeld.value.trim() }));
      betragFeld.value = "";
      zweckFeld.value = "";
      toast(`${euro(betrag)} eingetragen`, "ok");
      laden();
    } catch (e) {
      fehler(e);
    }
  }

  function bonErgebnisZeigen(r) {
    const summe = num(r?.summe);
    const gelesen = summe > 0;
    const ladenName = String(r?.laden || "Kassenbon");
    const text = gelesen
      ? (r?.eintrag ? "Als Ausgabe eingetragen." : "Nicht eingetragen.")
      : "Es wurde nichts eingetragen. Versuch es mit einem schärferen Foto oder trag die Summe von Hand ein.";
    leeren(bonErgebnis, h("div", { class: `callout ${gelesen ? "ok" : "warn"}` }, icon(gelesen ? "receipt" : "alert-triangle"),
      h("div", null, h("div", { class: "callout-title" }, `${ladenName}: ${gelesen ? euro(summe) : "Summe nicht erkannt"}`), text)));
    bonErgebnis.hidden = false;
  }

  async function bonAbsenden() {
    const datei = bonDatei.files && bonDatei.files[0];
    bonDatei.value = "";
    if (!datei) return;
    try {
      const r = await beschaeftigt(bonKnopf, "Alfred liest den Bon …", async () => {
        const bild = await fotoVerkleinern(datei);
        return api("foto", "POST", { art: "kassenbon", bild });
      });
      bonErgebnisZeigen(r);
      laden();
    } catch (e) {
      fehler(e);
    }
  }

  async function vorratAbsenden(ev) {
    ev.preventDefault();
    const name = nameFeld.value.trim();
    if (!name) {
      toast("Wie heißt der Artikel?", "err");
      nameFeld.focus();
      return;
    }
    try {
      await beschaeftigt(vorratKnopf, "Speichere …", () => api("vorrat", "POST", { name, menge: mengeFeld.value.trim(), ablauf: ablaufFeld.value }));
      nameFeld.value = "";
      mengeFeld.value = "";
      ablaufFeld.value = "";
      toast(`„${name}“ ist im Vorrat`, "ok");
      laden();
      if (feinerZeiger()) nameFeld.focus();
    } catch (e) {
      fehler(e);
    }
  }

  async function verbraucht(artikel, zeile) {
    zeile.classList.add("weg");
    try {
      await api(`vorrat?name=${encodeURIComponent(artikel.name)}`, "DELETE");
      toast(`„${artikel.name}“ ist aus dem Vorrat`, "ok");
      laden();
    } catch (e) {
      zeile.classList.remove("weg");
      fehler(e);
    }
  }

  // --- Zeichnen: Wochenplan ---------------------------------------------------------------------------------------------------
  function tagKarte(t, i) {
    const schluessel = `${i}|${t.tag}|${t.gericht}`;
    const detailId = `hh-detail-${i}`;
    const aufgeklappt = offen.has(schluessel);
    const hatDetails = t.zutaten.length > 0 || t.schritte.length > 0;
    let karte = null;
    const knopf = h("button", {
      class: "hh-toggle", type: "button", "aria-expanded": String(aufgeklappt), "aria-controls": detailId,
      onclick: () => {
        const auf = knopf.getAttribute("aria-expanded") !== "true";
        knopf.setAttribute("aria-expanded", String(auf));
        karte.dataset.offen = String(auf);
        if (auf) offen.add(schluessel);
        else offen.delete(schluessel);
      },
    }, h("span", null, "Zutaten und Schritte"), icon("chevron-down"));
    karte = h("article", { class: "card hh-tag", "data-offen": String(aufgeklappt) },
      h("div", { class: "hh-tag-kopf" },
        h("h3", { class: "overline hh-wochentag" }, t.tag || `Tag ${i + 1}`),
        h("span", { class: "hh-tag-badges" },
          t.dauer > 0 ? h("span", { class: "badge" }, icon("clock"), `${zahl(Math.round(t.dauer))} min`) : null,
          t.kcal > 0 ? h("span", { class: "badge" }, icon("flame"), `${zahl(Math.round(t.kcal))} kcal`) : null)),
      h("p", { class: "hh-gericht" }, t.gericht || "Gericht ohne Namen"),
      hatDetails ? h("div", { class: "hh-aufklapper" },
        knopf,
        h("div", { class: "hh-detail", id: detailId },
          h("div", null,
            h("div", { class: "hh-detail-inhalt" },
              t.zutaten.length ? h("div", null, h("h4", { class: "hh-detail-titel" }, "Zutaten"), h("ul", { class: "hh-zutaten" }, ...t.zutaten.map((z) => h("li", null, z)))) : null,
              t.schritte.length ? h("div", null, h("h4", { class: "hh-detail-titel" }, "Schritte"), h("ol", { class: "hh-schritte" }, ...t.schritte.map((s) => h("li", null, s)))) : null)))) : null);
    return karte;
  }

  function planZeichnen() {
    const plan = daten.plan;
    if (!plan.length) {
      leeren(planBereich, h("div", { class: "card" }, leer("utensils", "Noch kein Plan",
        "Schreib oben deine Wünsche und tippe auf Planen. Oder sag Alfred: „Plan mir fünf Abendessen, eiweißreich.“")));
      return;
    }
    const einkaufKnopf = h("button", { class: "btn", type: "button", onclick: (ev) => einkaufen(ev.currentTarget) }, icon("shopping-cart"), "Zutaten auf die Einkaufsliste");
    leeren(planBereich,
      h("div", { class: "hh-plankopf" },
        h("div", null, h("h2", { class: "hh-titel" }, "Dein Plan"), h("p", { class: "hint" }, `${plural(plan.length, "Abendessen", "Abendessen")}. Zutaten, die im Vorrat sind, kommen nicht auf die Liste.`)),
        einkaufKnopf),
      h("div", { class: "hh-raster" }, ...plan.map(tagKarte)));
  }

  // --- Zeichnen: Ausgaben -------------------------------------------------------------------------------------------------------
  const katKlasse = (name) => {
    const rang = daten.kategorien.findIndex(([k]) => k === name);
    return `kat-${rang < 0 ? 6 : Math.min(rang, 6)}`;
  };

  function summeZeichnen() {
    const monat = new Intl.DateTimeFormat("de-DE", { month: "long" }).format(new Date());
    const kat = daten.kategorien;
    const anteil = (betrag) => {
      const p = (100 * betrag) / daten.summe;
      return p < 1 ? "<1" : String(Math.round(p));
    };
    leeren(summeKarte,
      kartenKopf(`Ausgaben im ${monat}`, "wallet"),
      daten.anzahl > 0 && daten.summe > 0
        ? [
          h("div", { class: "hh-summe" }, h("div", { class: "hh-summe-wert" }, euro(daten.summe)), h("div", { class: "hh-summe-sub" }, plural(daten.anzahl, "Ausgabe", "Ausgaben"))),
          kat.length ? h("div", { class: "stackbar hh-balken", role: "img", "aria-label": `Ausgaben nach Kategorie: ${kat.map(([name, betrag]) => `${name} ${euro(betrag)}`).join(", ")}` },
            ...kat.map(([name, betrag]) => h("i", { class: katKlasse(name), style: { "--w": betrag }, title: `${name}: ${euro(betrag)}` }))) : null,
          kat.length ? h("div", { class: "hh-legende" }, ...kat.map(([name, betrag]) => h("div", { class: `hh-leg ${katKlasse(name)}` },
            h("i", { class: "hh-punkt" }),
            h("span", { class: "hh-leg-name", title: name }, name, h("small", null, ` ${anteil(betrag)} %`)),
            h("b", null, euro(betrag))))) : null,
        ]
        : leer("wallet", "Noch keine Ausgaben in diesem Monat", "Trag unten eine Ausgabe ein oder fotografiere einen Kassenbon."));
  }

  function ausgabeZeile(a) {
    return h("div", { class: "item hh-ausgabe" },
      h("time", { class: "hh-datum", datetime: a.datum }, tagLabel(a.datum)),
      h("div", { class: "item-main" },
        h("div", { class: "item-title" }, a.text || a.kategorie),
        a.text ? h("div", { class: "hh-zeilenfuss" }, h("span", { class: `badge kat ${katKlasse(a.kategorie)}` }, a.kategorie)) : null),
      h("b", { class: "hh-betrag" }, euro(a.betrag)));
  }

  function ausgabenListeZeichnen() {
    const liste = daten.liste;
    leeren(ausgabenListeKarte,
      kartenKopf("Letzte Ausgaben", "receipt", daten.anzahl > liste.length ? h("span", { class: "card-sub" }, `die letzten ${liste.length} von ${daten.anzahl}`) : null),
      liste.length
        ? h("div", { class: "list" }, ...liste.map(ausgabeZeile))
        : leer("receipt", "Noch keine Ausgaben", "Was du einträgst oder per Kassenbon einlesen lässt, steht hier."));
  }

  // --- Zeichnen: Vorrat ---------------------------------------------------------------------------------------------------------
  function vorratZeichnen() {
    const dringendNamen = new Set(daten.dringend);
    const istDringend = (v) => dringendNamen.has(v.name) || (!!v.ablauf && tageBis(v.ablauf) < 0);
    const sortiert = [...daten.vorrat].sort((a, b) =>
      Number(!istDringend(a)) - Number(!istDringend(b))
      || (a.ablauf || "9999").localeCompare(b.ablauf || "9999")
      || a.name.localeCompare(b.name, "de"));
    const bald = sortiert.filter(istDringend);

    if (bald.length) {
      const namen = bald.slice(0, 5).map((v) => v.name).join(", ") + (bald.length > 5 ? " und mehr" : "");
      leeren(vorratHinweis, h("div", { class: "callout warn" }, icon("alert-triangle"),
        h("div", null, h("div", { class: "callout-title" }, `${plural(bald.length, "Artikel läuft", "Artikel laufen")} bald ab`), namen)));
      vorratHinweis.hidden = false;
    } else {
      leeren(vorratHinweis);
      vorratHinweis.hidden = true;
    }

    const zeile = (v) => {
      const dringend = istDringend(v);
      const ablauf = v.ablauf ? ablaufInfo(v.ablauf, dringend) : null;
      return h("div", { class: `item hh-vorrat${dringend ? " hh-dringend" : ""}` },
        h("span", { class: `lead${dringend ? " danger" : ""}` }, icon(dringend ? "alert-triangle" : "package")),
        h("div", { class: "item-main" },
          h("div", { class: "item-title" }, v.name),
          v.menge ? h("div", { class: "item-sub" }, v.menge) : null),
        ablauf ? h("span", { class: `badge ${ablauf.art}`, title: `Läuft ab am ${volleDatum(v.ablauf)}` }, icon("clock"), ablauf.text) : null,
        h("button", {
          class: "btn ghost sm hh-verbraucht", type: "button", title: `„${v.name}“ ist verbraucht`, "aria-label": `„${v.name}“ als verbraucht entfernen`,
          onclick: (ev) => verbraucht(v, ev.currentTarget.closest(".item")),
        }, icon("check"), h("span", null, "verbraucht")));
    };

    const uebrige = sortiert.filter((v) => !istDringend(v));
    const mitGruppen = bald.length > 0 && uebrige.length > 0;
    leeren(vorratListeKarte,
      kartenKopf("Im Vorrat", "package", sortiert.length ? h("span", { class: "badge" }, plural(sortiert.length, "Artikel", "Artikel")) : null),
      sortiert.length
        ? h("div", { class: "list" },
          mitGruppen ? h("div", { class: "hh-gruppe" }, "Läuft bald ab") : null, ...bald.map(zeile),
          mitGruppen ? h("div", { class: "hh-gruppe" }, "Weitere Artikel") : null, ...uebrige.map(zeile))
        : leer("package", "Der Vorrat ist leer", "Trag ein, was du zu Hause hast. Zutaten aus dem Wochenplan, die schon da sind, kommen dann nicht auf die Einkaufsliste."));
  }

  function zaehlerSetzen() {
    const stand = { wochenplan: daten.plan.length, ausgaben: daten.anzahl, vorrat: daten.vorrat.length };
    for (const [id, n] of Object.entries(stand)) {
      zaehlerAnzeigen[id].textContent = String(n);
      zaehlerAnzeigen[id].hidden = !n;
    }
  }

  // --- Laden ------------------------------------------------------------------------------------------------------------------------
  function ladeFehler(e) {
    if (!daten) {
      inhalt.hidden = true;
      leeren(fehlerBox, h("div", { class: "card" }, fehlerKarte(e, laden)));
      fehlerBox.hidden = false;
    } else if (!e.netz) {
      leeren(fehlerBox, h("div", { class: "callout danger" }, icon("alert-triangle"),
        h("div", { class: "stack", style: { "--gap": "8px" } },
          h("div", null, `Das Aktualisieren hat nicht geklappt: ${e.message || e}`),
          h("div", null, h("button", { class: "btn sm", type: "button", onclick: () => laden() }, icon("refresh-cw"), "Erneut versuchen")))));
      fehlerBox.hidden = false;
    }
  }

  async function laden() {
    const nr = ++ladeNr;
    let antwort;
    try {
      antwort = await api("haushalt");
    } catch (e) {
      if (nr === ladeNr) ladeFehler(e);
      return;
    }
    if (nr !== ladeNr) return;
    try {
      daten = normieren(antwort);
      fehlerBox.hidden = true;
      inhalt.hidden = false;
      const tag = lokalesDatum();   // „Heute“, „Gestern“ und Ablaufworte ändern sich mit dem Tag
      zaehlerSetzen();
      einmal("plan", [daten.plan], planZeichnen);
      einmal("summe", [daten.summe, daten.anzahl, daten.kategorien], summeZeichnen);
      einmal("ausgaben", [daten.liste, daten.kategorien, daten.anzahl, tag], ausgabenListeZeichnen);
      einmal("vorrat", [daten.vorrat, daten.dringend, tag], vorratZeichnen);
    } catch (e) {
      console.error(e);
      ladeFehler(e);
    }
  }

  reiterWaehlen("wochenplan");

  return {
    laden,
    zeigen(params) {
      const gewuenscht = params && params[0];
      if (gewuenscht) reiterWaehlen(gewuenscht);
    },
    takt: 60000,
  };
}
