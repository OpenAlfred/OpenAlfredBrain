// Ernährung: Tagebuch von heute, Tagesziele, Gewichtsverlauf und die Woche auf einen Blick.
// Mahlzeiten, Produktnamen und Annahmen stammen aus Modellen und dem Netz und kommen nur als Text in die Seite.
import {
  h, icon, api, leeren, toast, fehler, bestaetigen, abfragen, imChatVorbereiten,
  datumKurz, uhrzeit, zahl, plural, wochentagKurz, lokalesDatum, kommaZahl, fotoVerkleinern, beschaeftigt,
} from "../core.js";
import { seitenKopf, kartenKopf, leer, skelett, fehlerKarte } from "../teile.js";
import { ring, linie, saeulen } from "../charts.js";

const QUELLEN = {
  schaetzung: { text: "Schätzung", symbol: "sparkles", art: "outline" },
  produkt: { text: "Barcode", symbol: "scan", art: "ok" },
  foto: { text: "Foto", symbol: "camera", art: "info" },
};
const REITER = [
  ["beschreiben", "Beschreiben", "edit-3"],
  ["barcode", "Barcode", "scan"],
  ["foto", "Foto", "camera"],
];
const MINUS = "−";
const SATZ_ALLE_ZIELE = "Mein Kalorienziel ist 2200, mein Eiweißziel 120 und mein Zielgewicht 78.";

const num = (x) => { const n = Number(x); return Number.isFinite(n) ? n : 0; };
const positiv = (x) => { const n = Number(x); return Number.isFinite(n) && n > 0 ? n : null; };
const runden = (x) => zahl(Math.round(num(x)));
const tagTeile = (iso) => String(iso).slice(0, 10).split("-").map(Number);
const tagAlsDatum = (iso) => { const [j, m, t] = tagTeile(iso); return new Date(j, m - 1, t); };
const tagNummer = (iso) => { const [j, m, t] = tagTeile(iso); return Date.UTC(j, m - 1, t) / 864e5; };
const feinerZeiger = () => !!(window.matchMedia && window.matchMedia("(pointer: fine)").matches);

/** "30 g", "1 Scheibe (25 g)" oder "250 ml" → Gramm; null, wenn keine Menge zu erkennen ist. */
function portionGramm(text) {
  const treffer = String(text ?? "").match(/(\d+(?:[.,]\d+)?)\s*(?:g|ml)\b/i);
  return treffer ? positiv(kommaZahl(treffer[1])) : null;
}

const platzhalter = (zeilen, hoehe, innen = false) => (innen ? h("div", { style: { padding: "18px" } }, skelett(zeilen, hoehe)) : skelett(zeilen, hoehe));

/** Antwort von /api/ernaehrung bereinigen: fehlende Felder, null-Ziele und falsche Typen verkraften. */
function normieren(d) {
  const g = d?.gesamt || {};
  const z = d?.ziele || {};
  const mahlzeiten = (Array.isArray(d?.heute) ? d.heute : [])
    .filter((m) => m && typeof m === "object")
    .map((m) => ({
      nummer: m.nummer, zeit: String(m.zeit ?? ""), text: String(m.text ?? ""), quelle: String(m.quelle ?? "schaetzung"), annahme: String(m.annahme ?? ""),
      kcal: num(m.kcal), eiweiss: num(m.eiweiss), fett: num(m.fett), kohlenhydrate: num(m.kohlenhydrate),
    }))
    .sort((a, b) => a.zeit.localeCompare(b.zeit) || num(a.nummer) - num(b.nummer));
  return {
    heute: mahlzeiten,
    gesamt: { kcal: num(g.kcal), eiweiss: num(g.eiweiss), fett: num(g.fett), kohlenhydrate: num(g.kohlenhydrate) },
    ziele: { kcal: positiv(z.kalorienziel), eiweiss: positiv(z.eiweissziel), gewicht: positiv(z.zielgewicht) },
    woche: (Array.isArray(d?.woche) ? d.woche : []).filter((t) => t && typeof t.tag === "string").map((t) => ({ tag: t.tag, kcal: num(t.kcal) })),
    gewichte: Object.entries(d?.gewichte && typeof d.gewichte === "object" ? d.gewichte : {})
      .map(([tag, kg]) => [tag, Number(kg)])
      .filter(([, kg]) => Number.isFinite(kg) && kg > 0)
      .sort((a, b) => a[0].localeCompare(b[0])),
  };
}

export function mount(wurzel) {
  wurzel.classList.add("v-ernaehrung");

  let daten = null;        // zuletzt geladener, bereinigter Stand
  let ladeNr = 0;          // nur die jüngste Antwort zählt
  let neueNummer = null;   // gerade eingetragene Mahlzeit, kurz hervorgehoben
  let reiter = "beschreiben";
  let kamera = null;
  const gesehen = {};      // Stand je Abschnitt: Unverändertes wird nicht neu gezeichnet (keine Flackerei, Ringe animieren nur bei Änderung)

  function einmal(name, stand, zeichnen) {
    const merke = JSON.stringify(stand);
    if (gesehen[name] === merke) return;
    gesehen[name] = merke;
    zeichnen();
  }

  // --- Gerüst: Karten mit Platzhaltern, die laden() füllt ------------------------------------------------------
  const kcalKarte = h("section", { class: "card", "aria-label": "Kalorien heute" }, platzhalter(2, 64));
  const eiweissKarte = h("section", { class: "card", "aria-label": "Eiweiß heute" }, platzhalter(2, 64));
  const naehrKarte = h("section", { class: "card", "aria-label": "Fett und Kohlenhydrate heute" }, platzhalter(2, 64));
  const heuteKarte = h("section", { class: "card flush", "aria-label": "Heute gegessen" }, platzhalter(3, 52, true));
  const gewichtKopf = h("div", null, platzhalter(1, 48));
  const gewichtDiagramm = h("div", null);
  const wocheKarte = h("section", { class: "card", "aria-label": "Diese Woche" }, platzhalter(1, 170));

  // --- Eintragen: Beschreiben, Barcode, Foto ------------------------------------------------------------------------
  const reiterKnoepfe = {};
  const leiste = h("div", { class: "seg er-tabs", role: "tablist", "aria-label": "Art des Eintrags" },
    ...REITER.map(([id, text, symbol]) => {
      const knopf = h("button", {
        type: "button", role: "tab", id: `er-reiter-${id}`, "aria-controls": `er-panel-${id}`, "aria-selected": "false",
        onclick: () => reiterWaehlen(id),
      }, icon(symbol), text);
      reiterKnoepfe[id] = knopf;
      return knopf;
    }));
  leiste.addEventListener("keydown", (ev) => {
    const schritt = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
    if (!schritt) return;
    ev.preventDefault();
    const i = REITER.findIndex(([id]) => id === reiter);
    const neu = REITER[(i + schritt + REITER.length) % REITER.length][0];
    reiterWaehlen(neu);
    reiterKnoepfe[neu].focus();
  });

  // Beschreiben: ein Satz, Alfred schätzt die Nährwerte
  const textFeld = h("input", { type: "text", placeholder: "Was hast du gegessen?", autocomplete: "off", enterkeyhint: "send", maxlength: 300, "aria-label": "Was hast du gegessen?" });
  const textKnopf = h("button", { class: "btn primary", type: "submit" }, icon("plus"), "Eintragen");
  const panelBeschreiben = h("div", { class: "er-panel", role: "tabpanel", id: "er-panel-beschreiben", "aria-labelledby": "er-reiter-beschreiben" },
    h("form", { class: "composer", onsubmit: beschreibenAbsenden }, icon("utensils"), textFeld, textKnopf),
    h("p", { class: "hint" }, icon("info"), " Mengen helfen: „zwei Scheiben Vollkornbrot mit Käse“ wird genauer als „Brot“."));

  // Barcode: Nummer tippen oder mit der Kamera lesen
  const barcodeFeld = h("input", { class: "input", type: "text", inputmode: "numeric", autocomplete: "off", placeholder: "Barcode, zum Beispiel 4000417025005", "aria-label": "Barcode" });
  const grammFeld = h("input", { class: "input num", type: "text", inputmode: "decimal", autocomplete: "off", placeholder: "g", "aria-label": "Menge in Gramm" });
  const barcodeKnopf = h("button", { class: "btn primary", type: "submit" }, icon("search"), "Nachschlagen");
  const kannScannen = "BarcodeDetector" in window && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  const scanKnopf = kannScannen
    ? h("button", { class: "btn icon", type: "button", title: "Mit der Kamera lesen", "aria-label": "Barcode mit der Kamera lesen", onclick: kameraStarten }, icon("scan"))
    : null;
  const video = h("video", { playsinline: true, muted: true, autoplay: true });
  const kameraRahmen = h("div", { class: "er-kamera", hidden: true },
    video, h("div", { class: "er-kamera-rahmen" }),
    h("button", { class: "btn sm", type: "button", onclick: kameraStoppen }, icon("x"), "Abbrechen"),
    h("div", { class: "er-kamera-hinweis" }, "Halte den Strichcode in den Rahmen"));
  const panelBarcode = h("div", { class: "er-panel", role: "tabpanel", id: "er-panel-barcode", "aria-labelledby": "er-reiter-barcode", hidden: true },
    h("form", { class: "er-felder", onsubmit: (ev) => { ev.preventDefault(); barcodeSuchen(); } },
      h("div", { class: "field-row" }, barcodeFeld, scanKnopf),
      h("div", { class: "field-row" }, grammFeld, barcodeKnopf)),
    kameraRahmen,
    h("p", { class: "hint" }, icon("info"), kannScannen
      ? " Strichcode scannen oder die Nummer darunter eintippen. Gramm leer lassen: dann gilt die Portion laut Packung oder 100 g."
      : " Dieser Browser kann keine Barcodes mit der Kamera lesen. Tipp die Nummer unter dem Strichcode ein; Gramm leer lassen heißt Portion laut Packung oder 100 g."));

  // Foto: Nährwerttabelle fotografieren, Alfred liest sie
  const fotoDatei = h("input", { type: "file", accept: "image/*", capture: "environment", hidden: true, "aria-hidden": "true", tabindex: "-1" });
  const fotoKnopf = h("button", { class: "btn primary er-foto-knopf", type: "button", onclick: () => fotoDatei.click() }, icon("camera"), "Nährwerttabelle fotografieren");
  const fotoErgebnis = h("div", { class: "er-ergebnis", role: "status", hidden: true });
  fotoDatei.addEventListener("change", fotoAbsenden);
  const panelFoto = h("div", { class: "er-panel", role: "tabpanel", id: "er-panel-foto", "aria-labelledby": "er-reiter-foto", hidden: true },
    h("p", { class: "muted", style: { fontSize: "var(--fs-sm)" } }, "Fotografiere die Nährwerttabelle auf der Packung. Alfred liest die Werte und trägt sie für die Menge ein, die du gegessen hast."),
    fotoKnopf, fotoDatei, fotoErgebnis);

  const eintragenKarte = h("section", { class: "card er-eintragen", "aria-label": "Eintragen" },
    kartenKopf("Eintragen", "plus"), leiste,
    h("div", { class: "er-reiter" }, panelBeschreiben, panelBarcode, panelFoto));

  // --- Gewicht: Verlauf und Eingabe ---------------------------------------------------------------------------------
  const gewichtFeld = h("input", { class: "input", type: "text", inputmode: "decimal", autocomplete: "off", placeholder: "Heutiges Gewicht in kg", "aria-label": "Gewicht in Kilogramm" });
  const gewichtKnopf = h("button", { class: "btn primary", type: "submit" }, icon("plus"), "Eintragen");
  const gewichtKarte = h("section", { class: "card", "aria-label": "Gewicht" },
    kartenKopf("Gewicht", "activity", h("span", { class: "card-sub" }, "letzte 30 Einträge")),
    gewichtKopf, gewichtDiagramm,
    h("form", { class: "er-gew-form", onsubmit: gewichtAbsenden }, h("div", { class: "field-row" }, gewichtFeld, gewichtKnopf)));

  const fehlerBox = h("div", { class: "er-fehler", hidden: true });
  const inhalt = h("div", null,
    h("div", { class: "er-kopf" }, kcalKarte, eiweissKarte, naehrKarte),
    h("div", { class: "er-haupt" },
      h("div", { class: "er-spalte" }, eintragenKarte, heuteKarte),
      h("div", { class: "er-spalte" }, gewichtKarte, wocheKarte)));

  wurzel.append(
    seitenKopf("Ernährung", "Tagebuch, Ziele und Gewicht. Schätzungen aus Beschreibungen liegen oft 20 bis 30 Prozent daneben, Barcodes sind genauer. Keine medizinische Beratung."),
    fehlerBox, inhalt);

  // --- Reiter ----------------------------------------------------------------------------------------------------------
  function reiterWaehlen(id) {
    if (!REITER.some(([r]) => r === id)) return;  // Adressen wie #/ernaehrung/xyz lassen den Reiter, wie er ist
    reiter = id;
    for (const [k, knopf] of Object.entries(reiterKnoepfe)) {
      knopf.setAttribute("aria-selected", String(k === id));
      knopf.tabIndex = k === id ? 0 : -1;
    }
    panelBeschreiben.hidden = id !== "beschreiben";
    panelBarcode.hidden = id !== "barcode";
    panelFoto.hidden = id !== "foto";
    if (id !== "barcode") kameraStoppen();
  }

  // --- Beschreiben ------------------------------------------------------------------------------------------------------
  async function beschreibenAbsenden(ev) {
    ev.preventDefault();
    const text = textFeld.value.trim();
    if (!text) { textFeld.focus(); return; }
    textFeld.disabled = true;
    try {
      const r = await beschaeftigt(textKnopf, "Alfred schätzt …", () => api("ernaehrung", "POST", { text }));
      textFeld.value = "";
      neueNummer = r?.eintrag?.nummer ?? null;
      toast(`${runden(r?.eintrag?.kcal)} kcal – ${r?.eintrag?.annahme || "eingetragen"}`, "ok", 6000);
      laden();
    } catch (e) {
      fehler(e);
    } finally {
      textFeld.disabled = false;
      if (feinerZeiger()) textFeld.focus();
    }
  }

  // --- Barcode: Kamera --------------------------------------------------------------------------------------------------
  async function passendeFormate() {
    const wunsch = ["ean_13", "ean_8", "upc_a", "upc_e"];
    try {
      const moeglich = await BarcodeDetector.getSupportedFormats();
      const treffer = wunsch.filter((f) => moeglich.includes(f));
      return treffer.length ? treffer : wunsch;
    } catch {
      return wunsch;
    }
  }

  function kameraStoppen() {
    if (!kamera) return;
    clearInterval(kamera.takt);
    clearTimeout(kamera.frist);
    for (const spur of kamera.strom.getTracks()) spur.stop();
    video.srcObject = null;
    kameraRahmen.hidden = true;
    kamera = null;
  }

  async function kameraStarten() {
    if (kamera) return;
    let strom;
    try {
      strom = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    } catch (e) {
      toast(`Kamera nicht verfügbar: ${e.message || e.name}`, "err", 5200);
      return;
    }
    const loslassen = () => { for (const spur of strom.getTracks()) spur.stop(); };
    if (kamera || reiter !== "barcode" || document.hidden || !wurzel.classList.contains("active")) { loslassen(); return; }
    let detektor;
    try {
      detektor = new BarcodeDetector({ formats: await passendeFormate() });
    } catch (e) {
      loslassen();
      toast(`Barcodes lesen geht hier nicht: ${e.message || e.name}`, "err", 5200);
      return;
    }
    const eigene = { strom, takt: null, frist: null, beschaeftigt: false };
    kamera = eigene;
    video.srcObject = strom;
    kameraRahmen.hidden = false;
    try { await video.play(); } catch { /* Autoplay verweigert: das Bild startet beim nächsten Antippen */ }
    eigene.takt = setInterval(async () => {
      if (kamera !== eigene || eigene.beschaeftigt || video.readyState < 2) return;
      eigene.beschaeftigt = true;
      try {
        const codes = await detektor.detect(video);
        if (kamera === eigene && codes.length) {
          kameraStoppen();
          barcodeFeld.value = codes[0].rawValue;
          barcodeSuchen();
        }
      } catch { /* ein Bild ohne Treffer ist normal */ }
      eigene.beschaeftigt = false;
    }, 400);
    eigene.frist = setTimeout(() => {
      if (kamera !== eigene) return;
      kameraStoppen();
      toast("Kein Barcode erkannt. Tipp die Nummer ein.", "info", 4200);
    }, 30000);
  }

  // --- Barcode: nachschlagen, bestätigen, eintragen -----------------------------------------------------------------------
  async function barcodeSuchen() {
    const code = barcodeFeld.value.replace(/\D/g, "");
    if (code.length < 8) {
      toast("Ein Barcode hat mindestens 8 Ziffern.", "err");
      barcodeFeld.focus();
      return;
    }
    try {
      const produkt = await beschaeftigt(barcodeKnopf, "Suche …", () => api(`ernaehrung/produkt?barcode=${encodeURIComponent(code)}`));
      const kcal100 = num(produkt?.je_100g?.kcal);
      const gramm = positiv(kommaZahl(grammFeld.value)) || portionGramm(produkt?.portion) || 100;
      const ok = await bestaetigen({
        titel: String(produkt?.name || "Produkt"),
        text: `${zahl(Math.round(kcal100))} kcal je 100 g\nEintragen mit ${zahl(gramm)} g (etwa ${zahl(Math.round((kcal100 * gramm) / 100))} kcal)?`,
        ok: "Eintragen",
      });
      if (!ok) return;
      const r = await beschaeftigt(barcodeKnopf, "Trage ein …", () => api("ernaehrung", "POST", { barcode: code, gramm }));
      barcodeFeld.value = "";
      grammFeld.value = "";
      neueNummer = r?.eintrag?.nummer ?? null;
      toast(`${runden(r?.eintrag?.kcal)} kcal eingetragen`, "ok");
      laden();
    } catch (e) {
      fehler(e);
    }
  }

  // --- Foto ---------------------------------------------------------------------------------------------------------------
  function fotoErgebnisZeigen(r) {
    const kcal100 = num(r?.je_100g?.kcal);
    const lesbar = kcal100 > 0;
    const eingetragen = r?.eintrag ? num(r.eintrag.kcal) : null;
    const text = lesbar
      ? `${runden(kcal100)} kcal je 100 g${eingetragen !== null ? `, eingetragen: ${runden(eingetragen)} kcal` : ""}`
      : "Alfred konnte auf dem Foto keine Kalorien lesen. Steht ein Eintrag mit 0 kcal im Tagebuch, lösch ihn und versuch es mit einem schärferen Foto.";
    leeren(fotoErgebnis, h("div", { class: `callout ${lesbar ? "ok" : "warn"}` }, icon(lesbar ? "check-circle" : "alert-triangle"),
      h("div", null, h("div", { class: "callout-title" }, String(r?.name || "Produkt vom Foto")), text)));
    fotoErgebnis.hidden = false;
  }

  async function fotoAbsenden() {
    const datei = fotoDatei.files && fotoDatei.files[0];
    fotoDatei.value = "";
    if (!datei) return;
    const eingabe = await abfragen({
      titel: "Wie viel hast du gegessen?", text: "Leer lassen für die Portion laut Packung oder 100 g.",
      label: "Gramm", platzhalter: "zum Beispiel 40", ok: "Weiter", modus: "decimal",
    });
    if (eingabe === null) return;
    const gramm = positiv(kommaZahl(eingabe)) || 0;
    try {
      const r = await beschaeftigt(fotoKnopf, "Alfred liest das Foto …", async () => {
        const bild = await fotoVerkleinern(datei);
        return api("foto", "POST", { art: "naehrwerte", bild, gramm });
      });
      neueNummer = r?.eintrag?.nummer ?? null;
      fotoErgebnisZeigen(r);
      toast("Foto gelesen", "ok");
      laden();
    } catch (e) {
      fehler(e);
    }
  }

  // --- Gewicht eintragen ----------------------------------------------------------------------------------------------------
  async function gewichtAbsenden(ev) {
    ev.preventDefault();
    const kg = kommaZahl(gewichtFeld.value);
    if (!Number.isFinite(kg) || kg <= 0) {
      toast("Gib dein Gewicht in Kilogramm ein, zum Beispiel 81,4.", "err");
      gewichtFeld.focus();
      return;
    }
    try {
      await beschaeftigt(gewichtKnopf, "Speichere …", () => api("ernaehrung/gewicht", "POST", { kilogramm: kg }));
      gewichtFeld.value = "";
      toast(`${zahl(kg, 1)} kg eingetragen`, "ok");
      laden();
    } catch (e) {
      fehler(e);
    }
  }

  // --- Zeichnen ---------------------------------------------------------------------------------------------------------------
  function ringKarteZeichnen(karte, { titel, symbol, farbe, wert, ziel, einheit, ueberWarnt }) {
    karte.classList.add("er-ring");
    karte.dataset.farbe = farbe;
    let badge = null;
    if (ziel) {
      const rest = Math.round(ziel - wert);
      if (ueberWarnt && wert > ziel) badge = h("span", { class: "badge warn" }, icon("alert-triangle"), `${zahl(Math.round(wert - ziel))} ${einheit} über Ziel`);
      else if (wert >= ziel || rest <= 0) badge = h("span", { class: "badge ok" }, icon("check-circle"), "Ziel erreicht");
      else badge = h("span", { class: "badge accent" }, `noch ${zahl(rest)} ${einheit}`);
    }
    leeren(karte,
      h("div", { class: "er-ring-kopf" }, h("div", { class: "card-title" }, icon(symbol), titel), h("span", { class: "spacer" }), badge),
      ring({ wert, ziel, farbe, titel, einheit, ueberWarnt }),
      h("div", { class: "er-ring-fuss" }, ziel ? `${zahl(Math.round((100 * wert) / ziel))} % des Tagesziels` : "Noch kein Ziel gesetzt"));
  }

  function zielHinweis() {
    const { ziele } = daten;
    const ohneKcal = !ziele.kcal;
    const ohneEiweiss = !ziele.eiweiss;
    if (!ohneKcal && !ohneEiweiss) return null;
    let text;
    let satz;
    if (ohneKcal && ohneEiweiss) {
      text = "Ziele setzt du im Chat: „Mein Kalorienziel ist 2200“.";
      satz = SATZ_ALLE_ZIELE;
    } else if (ohneKcal) {
      text = "Das Kalorienziel fehlt noch. Sag es im Chat: „Mein Kalorienziel ist 2200“.";
      satz = "Mein Kalorienziel ist 2200.";
    } else {
      text = "Das Eiweißziel fehlt noch. Sag es im Chat: „Mein Eiweißziel ist 120“.";
      satz = "Mein Eiweißziel ist 120.";
    }
    return h("div", { class: "callout" }, icon("target"),
      h("div", { class: "stack", style: { "--gap": "10px" } },
        h("div", null, text),
        h("div", null, h("button", { class: "btn sm", type: "button", onclick: () => imChatVorbereiten(satz) }, icon("message-square"), "Im Chat setzen"))));
  }

  function naehrZeichnen() {
    const { gesamt: g } = daten;
    naehrKarte.classList.add("er-naehr");
    const energie = [["er-m-eiweiss", "Eiweiß", 4 * g.eiweiss], ["er-m-fett", "Fett", 9 * g.fett], ["er-m-kh", "Kohlenhydrate", 4 * g.kohlenhydrate]];
    const summe = energie.reduce((a, [, , e]) => a + e, 0);
    const prozent = (e) => {
      const p = (100 * e) / summe;
      return p > 0 && p < 1 ? "<1" : String(Math.round(p));
    };
    const mini = (label, wert) => h("div", { class: "er-mini" },
      h("div", { class: "stat-label" }, label), h("div", { class: "stat-value" }, runden(wert), h("small", null, "g")));
    leeren(naehrKarte,
      kartenKopf("Weitere Nährwerte", "pie-chart", h("span", { class: "card-sub" }, "heute")),
      h("div", { class: "er-minis" }, mini("Fett", g.fett), mini("Kohlenhydrate", g.kohlenhydrate)),
      summe > 0 ? h("div", { class: "er-verteilung" },
        h("div", { class: "er-verteilung-kopf" }, "Verteilung der Energie"),
        h("div", { class: "stackbar", role: "img", "aria-label": `Energie: ${energie.map(([, name, e]) => `${name} ${prozent(e)} Prozent`).join(", ")}` },
          ...energie.filter(([, , e]) => e > 0).map(([klasse, , e]) => h("i", { class: klasse, style: { "--w": e } }))),
        h("div", { class: "er-legende-reihe" },
          ...energie.map(([klasse, name, e]) => h("span", { class: klasse }, h("i", { class: "er-punkt" }), name, h("b", null, `${prozent(e)} %`)))))
        : null,
      zielHinweis());
  }

  function kopfZeichnen() {
    const { gesamt, ziele } = daten;
    einmal("kcal", [gesamt.kcal, ziele.kcal], () => ringKarteZeichnen(kcalKarte, {
      titel: "Kalorien", symbol: "flame", farbe: "accent", wert: gesamt.kcal, ziel: ziele.kcal, einheit: "kcal", ueberWarnt: true,
    }));
    einmal("eiweiss", [gesamt.eiweiss, ziele.eiweiss], () => ringKarteZeichnen(eiweissKarte, {
      titel: "Eiweiß", symbol: "zap", farbe: "ok", wert: gesamt.eiweiss, ziel: ziele.eiweiss, einheit: "g", ueberWarnt: false,
    }));
    einmal("naehr", [gesamt, ziele.kcal, ziele.eiweiss], naehrZeichnen);
  }

  async function loeschen(m) {
    const ok = await bestaetigen({
      titel: "Eintrag löschen?", text: `„${m.text}“ (${runden(m.kcal)} kcal) wird aus dem Tagebuch entfernt.`, ok: "Löschen", gefahr: true,
    });
    if (!ok) return;
    try {
      await api(`ernaehrung?nummer=${encodeURIComponent(m.nummer)}`, "DELETE");
      toast("Eintrag gelöscht", "ok");
      laden();
    } catch (e) {
      fehler(e);
    }
  }

  function mahlzeitZeile(m) {
    const quelle = QUELLEN[m.quelle] || QUELLEN.schaetzung;
    const mitAnnahme = m.annahme && m.quelle === "schaetzung";
    return h("div", { class: `item er-mahlzeit${m.nummer === neueNummer ? " er-neu" : ""}` },
      h("time", { class: "er-uhr", datetime: m.zeit }, uhrzeit(m.zeit)),
      h("div", { class: "item-main" },
        h("div", { class: "item-title" }, m.text),
        h("div", { class: "er-zeilenfuss" },
          h("span", { class: `badge ${quelle.art}`, title: m.annahme || quelle.text }, icon(quelle.symbol), quelle.text),
          mitAnnahme ? h("span", { class: "item-sub clamp-2" }, m.annahme) : null)),
      h("div", { class: "er-werte" }, h("b", null, `${runden(m.kcal)} kcal`), h("span", null, `${runden(m.eiweiss)} g Eiweiß`)),
      h("div", { class: "actions" },
        h("button", { class: "btn icon ghost sm", type: "button", title: "Löschen", "aria-label": `„${m.text}“ löschen`, onclick: () => loeschen(m) }, icon("trash-2"))));
  }

  function heuteZeichnen() {
    const liste = daten.heute;
    leeren(heuteKarte,
      kartenKopf("Heute gegessen", "utensils", liste.length ? h("span", { class: "badge" }, plural(liste.length, "Eintrag", "Einträge")) : null),
      liste.length
        ? h("div", { class: "list" }, ...liste.map(mahlzeitZeile))
        : leer("utensils", "Heute noch nichts eingetragen", "Beschreib oben, was du gegessen hast, scanne einen Barcode oder fotografiere eine Nährwerttabelle."));
    neueNummer = null;
  }

  function gewichtZeichnen() {
    const letzte = daten.gewichte.slice(-30);
    const ziel = daten.ziele.gewicht;
    if (!letzte.length) {
      gewichtKopf.className = "";
      leeren(gewichtKopf, leer("activity", "Noch kein Gewicht eingetragen", "Trag unten dein Gewicht ein, dann zeichnet Alfred den Verlauf."));
      leeren(gewichtDiagramm);
      return;
    }
    const erster = letzte[0];
    const aktuell = letzte[letzte.length - 1];
    const delta = Math.round((aktuell[1] - erster[1]) * 10) / 10;

    let veraenderung;
    if (letzte.length < 2) {
      veraenderung = h("span", { class: "er-delta" }, "Erster Eintrag");
    } else if (delta === 0) {
      veraenderung = h("span", { class: "er-delta" }, `unverändert seit ${datumKurz(erster[0])}`);
    } else {
      const naeher = ziel ? Math.abs(aktuell[1] - ziel) < Math.abs(erster[1] - ziel) : false;
      veraenderung = h("span", { class: `er-delta ${delta < 0 ? "ab" : "auf"}${naeher ? " ok" : ""}` },
        icon("trending-down"), `${delta < 0 ? MINUS : "+"}${zahl(Math.abs(delta), 1)} kg seit ${datumKurz(erster[0])}`);
    }

    let zielZeile;
    if (ziel) {
      const abstand = aktuell[1] - ziel;
      zielZeile = Math.abs(abstand) < 0.05
        ? h("span", null, "Ziel erreicht")
        : h("span", null, `Ziel ${zahl(ziel, 1)} kg · noch ${zahl(Math.abs(abstand), 1)} kg`);
    } else {
      zielZeile = h("span", { class: "dim" }, "Kein Zielgewicht ",
        h("button", { class: "btn ghost sm", type: "button", onclick: () => imChatVorbereiten("Mein Zielgewicht ist 78.") }, "Im Chat setzen"));
    }

    gewichtKopf.className = "er-gew-kopf";
    leeren(gewichtKopf,
      h("div", { class: "stack", style: { "--gap": "4px" } },
        h("div", { class: "er-gew-wert" }, h("b", null, zahl(aktuell[1], 1)), h("span", null, "kg")),
        h("div", { class: "hint" }, `Stand ${datumKurz(aktuell[0])}`)),
      h("div", { class: "er-gew-info" }, veraenderung, zielZeile));

    leeren(gewichtDiagramm, letzte.length < 2
      ? h("p", { class: "hint" }, icon("info"), " Ab zwei Einträgen zeichnet Alfred den Verlauf.")
      : linie({ punkte: letzte.map(([tag, kg]) => ({ x: datumKurz(tag), y: kg, t: tagNummer(tag) })), ziel, hoehe: 176, einheit: "kg", titel: "Gewichtsverlauf" }));
  }

  function wocheZeichnen() {
    const tage = daten.woche;
    const ziel = daten.ziele.kcal;
    const mitWerten = tage.filter((t) => t.kcal > 0);
    const heute = lokalesDatum();
    const heuteIndex = tage.findIndex((t) => t.tag === heute);
    const hervor = heuteIndex >= 0 ? heuteIndex : tage.length - 1;
    const schnitt = mitWerten.length ? Math.round(mitWerten.reduce((a, t) => a + t.kcal, 0) / mitWerten.length) : 0;
    leeren(wocheKarte,
      kartenKopf("Diese Woche", "calendar", ziel ? h("span", { class: "er-legende" }, h("i"), `Ziel ${zahl(Math.round(ziel))} kcal`) : null),
      mitWerten.length
        ? saeulen({ werte: tage.map((t, i) => ({ label: wochentagKurz(tagAlsDatum(t.tag)), wert: t.kcal, hervor: i === hervor })), ziel, hoehe: 176, titel: "Kalorien pro Tag" })
        : leer("calendar", "Noch keine Einträge in der Woche", "Sobald du etwas einträgst, siehst du hier jeden Tag als Säule."),
      mitWerten.length ? h("p", { class: "hint", style: { marginTop: "12px" } }, `Ø ${zahl(schnitt)} kcal an ${plural(mitWerten.length, "Tag", "Tagen")} mit Einträgen`) : null);
  }

  // --- Laden ------------------------------------------------------------------------------------------------------------------
  function ladeFehler(e) {
    if (!daten) {
      inhalt.hidden = true;
      leeren(fehlerBox, h("div", { class: "card" }, fehlerKarte(e, laden)));
      fehlerBox.hidden = false;
    } else if (!e.netz) {
      // Netzfehler zeigt schon die Leiste der App; andere Fehler melden wir, ohne die alten Zahlen wegzunehmen.
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
      antwort = await api("ernaehrung");
    } catch (e) {
      if (nr === ladeNr) ladeFehler(e);
      return;
    }
    if (nr !== ladeNr) return;
    try {
      daten = normieren(antwort);
      fehlerBox.hidden = true;
      inhalt.hidden = false;
      kopfZeichnen();
      einmal("heute", [daten.heute, neueNummer], heuteZeichnen);
      einmal("gewicht", [daten.gewichte.slice(-30), daten.ziele.gewicht], gewichtZeichnen);
      einmal("woche", [daten.woche, daten.ziele.kcal, lokalesDatum()], wocheZeichnen);
    } catch (e) {
      console.error(e);
      ladeFehler(e);
    }
  }

  document.addEventListener("visibilitychange", () => { if (document.hidden) kameraStoppen(); });
  reiterWaehlen("beschreiben");

  return {
    laden,
    zeigen(params) {
      const gewuenscht = params && params[0];
      if (gewuenscht) reiterWaehlen(gewuenscht);
    },
    verlassen: kameraStoppen,
    takt: 30000,
  };
}
