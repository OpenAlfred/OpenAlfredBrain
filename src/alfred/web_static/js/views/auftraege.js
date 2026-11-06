// Aufträge: Recherchen und Berichte, die Alfred im Hintergrund abarbeitet. Liste links, Bericht rechts (am Handy als Vollbild).
import { h, icon, api, markdownEinsetzen, vergangen, uhrzeit, datumKurz, dauer, kopieren, bestaetigen, toast, fehler, leeren, zaehlerLaden, gehe, plural, imChatVorbereiten, juengste } from "../core.js";
import { seitenKopf, kartenKopf, leer, skelett, fehlerKarte, AUFTRAG, auftragOffen, auftragBadge } from "../teile.js";

const FILTER = [
  { id: "alle", text: "Alle", passt: () => true },
  { id: "aktiv", text: "Aktiv", passt: auftragOffen },
  { id: "fertig", text: "Fertig", passt: (a) => a.status === "fertig" },
  { id: "problem", text: "Problem", passt: (a) => a.status === "fehler" || a.status === "abgebrochen" },
];

export function mount(wurzel) {
  let liste = [];
  let sprachprozess = true;
  let gewaehlt = null;
  let filter = "alle";
  let leserStand = "";
  let geladen = false;
  const berichte = new Map();  // Nummer → Text; ein fertiger Bericht ändert sich nicht mehr

  // --- Neuer Auftrag ------------------------------------------------------------------------------------------
  const eingabe = h("textarea", { class: "textarea", rows: 2, placeholder: "Was soll Alfred ausarbeiten? Zum Beispiel: Vergleiche Wärmepumpe und Gasheizung, Bericht mit Quellen.", "aria-label": "Neuer Auftrag", enterkeyhint: "send" });
  const push = h("input", { type: "checkbox", checked: true, id: "auftrag-push" });
  const senden = h("button", { class: "btn primary", type: "submit" }, icon("send"), "Auftrag geben");
  const formular = h("form", { class: "card stack", style: { "--gap": "12px" }, onsubmit: async (ev) => {
    ev.preventDefault();
    const text = eingabe.value.trim();
    if (!text || senden.disabled) return;  // Strg+Enter ging am gesperrten Knopf vorbei und gab den Auftrag doppelt
    senden.disabled = true;
    try {
      const r = await api("auftraege", "POST", { text, aufs_handy: push.checked });
      eingabe.value = "";
      toast(r.bestaetigung, "ok", 8000);
      await laden();
      zaehlerLaden();
    } catch (e) { fehler(e); } finally { senden.disabled = false; }
  } },
    h("div", { class: "card-title" }, icon("layers"), "Neuer Auftrag"), eingabe,
    h("div", { class: "row wrap", style: { justifyContent: "space-between" } },
      h("label", { class: "schalter", for: "auftrag-push" }, push, h("span", { class: "schalter-spur" }), h("span", null, "Push aufs Handy, wenn fertig")), senden));
  eingabe.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); formular.requestSubmit(); } });

  const hinweis = h("div", { class: "callout warn", hidden: true }, icon("alert-triangle"), h("div", null, "Alfreds Sprach-Prozess läuft gerade nicht. Die Aufträge beginnen, sobald er läuft."));
  const filterLeiste = h("div", { class: "seg", role: "tablist" });
  const karten = h("div", { class: "stack", style: { "--gap": "10px" } }, skelett(3, 70));
  const leser = h("section", { class: "card leser" });
  const linke = h("div", { class: "stack auftraege-liste", style: { "--gap": "14px" } }, formular, hinweis, filterLeiste, karten);
  wurzel.append(seitenKopf("Aufträge", "Recherchen, Vergleiche und Berichte, die Alfred im Hintergrund für dich erledigt.", h("a", { class: "btn", href: "#/chat", onclick: (ev) => { ev.preventDefault(); imChatVorbereiten("Was machst du gerade?", true); } }, icon("message-square"), h("span", { class: "hide-m" }, "Alfred fragen"))),
    h("div", { class: "auftraege-grid" }, linke, leser));

  // --- Aktionen ---------------------------------------------------------------------------------------------------
  async function abbrechen(a) {
    if (!(await bestaetigen({ titel: `„${a.titel}“ abbrechen?`, text: "Alfred hört mit diesem Auftrag auf. Ein schon geschriebener Bericht geht verloren.", ok: "Abbrechen", abbrechen: "Weiterlaufen lassen", gefahr: true }))) return;
    try { await api(`auftraege?nummer=${a.nummer}`, "DELETE"); } catch (e) { fehler(e); }
    await laden();
    zaehlerLaden();
  }

  // --- Liste ----------------------------------------------------------------------------------------------------------
  let listenStand = "";
  function listeZeichnen() {
    // Nur bei Änderung neu zeichnen (einmal je Minute wegen der „vor …“-Zeiten): Sonst springt der Tastaturfokus alle sieben Sekunden weg.
    const stand = JSON.stringify([filter, gewaehlt, Math.floor(Date.now() / 60000), liste.map((x) => [x.nummer, x.status, x.titel, x.kurzfassung, x.fehler, x.begonnen, x.beendet])]);
    if (stand === listenStand) return;
    listenStand = stand;
    leeren(filterLeiste, ...FILTER.map((f) => {
      const n = liste.filter(f.passt).length;
      return h("button", { type: "button", role: "tab", "aria-selected": String(filter === f.id), onclick: () => { filter = f.id; listeZeichnen(); } }, f.text, f.id !== "alle" && n ? h("span", { class: "count" }, n) : null);
    }));
    const sichtbar = liste.filter(FILTER.find((f) => f.id === filter).passt);
    leeren(karten, ...(sichtbar.length ? sichtbar.map(karte) : [h("div", { class: "card" }, leer("layers", filter === "alle" ? "Noch keine Aufträge" : "Nichts in dieser Ansicht",
      filter === "alle" ? "Sag zum Beispiel: „Alfred, vergleich … und mach mir einen Bericht mit Quellen.“" : ""))]));
  }

  function karte(a) {
    const s = AUFTRAG[a.status] || AUFTRAG.wartet;
    const oeffnen = () => gehe(`auftraege/${a.nummer}`);
    return h("div", { class: `auftrag-karte${a.nummer === gewaehlt ? " aktiv" : ""}`, role: "button", tabindex: 0, onclick: oeffnen,
      onkeydown: (ev) => { if (ev.target === ev.currentTarget && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); oeffnen(); } } },
      h("span", { class: `lead ${s.art}` }, icon(s.symbol, a.status === "laeuft" ? "spin" : "")),
      h("div", { class: "item-main" },
        h("div", { class: "item-title clamp-2" }, a.titel),
        h("div", { class: "item-sub" }, `Nr. ${a.nummer} · ${s.text} · ${vergangen(a.beendet || a.begonnen || a.angelegt)}`),
        a.status === "fertig" && a.kurzfassung ? h("div", { class: "auftrag-kurz clamp-2" }, a.kurzfassung) : null,
        a.status === "fehler" && a.fehler ? h("div", { class: "auftrag-kurz fehl clamp-2" }, a.fehler) : null),
      auftragOffen(a)
        ? h("button", { class: "btn icon ghost sm", type: "button", title: "Auftrag abbrechen", "aria-label": `„${a.titel}“ abbrechen`, onclick: (ev) => { ev.stopPropagation(); abbrechen(a); } }, icon("x"))
        : icon("chevron-right", "dim"));
  }

  // --- Leser -------------------------------------------------------------------------------------------------------------
  function schritt(text, zeit, stand) {
    return h("div", { class: `schritt ${stand}` }, h("span", { class: "schritt-punkt" }), h("div", null, h("b", null, text), h("span", null, zeit ? `${datumKurz(zeit)} ${uhrzeit(zeit)}` : "–")));
  }

  function leserZeichnen(erzwingen = false) {
    const a = liste.find((x) => x.nummer === gewaehlt);
    const stand = a ? `${a.nummer}|${a.status}|${a.beendet}|${a.begonnen}` : `${gewaehlt}`;
    leser.classList.toggle("offen", gewaehlt != null);
    if (gewaehlt != null && !geladen) { leeren(leser, skelett(4, 28)); return; }
    if (!erzwingen && stand === leserStand) return;
    leserStand = stand;
    if (gewaehlt == null) {
      leeren(leser, leer("file-text", "Wähle einen Auftrag", "Hier steht dann der Bericht mit Kurzfassung, Ergebnis und Quellen."));
      return;
    }
    if (!a) {
      leeren(leser, h("div", { class: "leser-kopf" }, h("button", { class: "btn ghost sm", onclick: () => gehe("auftraege") }, icon("chevron-left"), "Aufträge")),
        leer("search", "Auftrag nicht gefunden", "Beendete Aufträge verschwinden nach 30 Tagen, die Berichte bleiben als Dateien liegen."));
      return;
    }
    const s = AUFTRAG[a.status] || AUFTRAG.wartet;
    const laufzeit = a.begonnen && a.beendet ? ` · Dauer ${dauer((new Date(a.beendet) - new Date(a.begonnen)) / 1000)}` : "";
    const bericht = h("div", { class: "prose" }, skelett(4, 20));
    const platz = liste.filter((x) => x.status === "wartet" && x.nummer < a.nummer).length;
    const inhalt = [
      h("div", { class: "callout" }, icon("terminal"), h("div", null, h("div", { class: "callout-title" }, "Auftrag"), h("div", { style: { overflowWrap: "anywhere" } }, a.auftrag))),
      h("div", { class: "stepper" }, schritt("Angelegt", a.angelegt, "fertig"), schritt("Begonnen", a.begonnen, a.begonnen ? "fertig" : ""),
        schritt(a.status === "fehler" ? "Gescheitert" : a.status === "abgebrochen" ? "Abgebrochen" : "Fertig", a.beendet, a.beendet ? (a.status === "fertig" ? "fertig" : a.status === "fehler" ? "fehl" : "") : "")),
    ];
    if (a.status === "laeuft") inhalt.push(h("div", { class: "arbeit" }, h("div", { class: "row" }, icon("loader", "spin"), h("b", null, "Alfred arbeitet daran"), h("span", { class: "dim" }, `seit ${vergangen(a.begonnen).replace("vor ", "")}`)),
      h("div", { class: "meter indet" }, h("i")), h("p", { class: "hint" }, "Das Ergebnis erscheint hier, sobald der Bericht fertig ist. Du kannst die Seite schließen.")));
    if (a.status === "wartet") inhalt.push(h("div", { class: "arbeit" }, h("div", { class: "row" }, icon("clock"), h("b", null, platz ? `Wartet, ${plural(platz, "Auftrag ist", "Aufträge sind")} noch davor` : "Ist als Nächstes dran")),
      h("p", { class: "hint" }, "Alfred arbeitet die Aufträge der Reihe nach ab.")));
    if (a.status === "fehler") inhalt.push(h("div", { class: "callout danger" }, icon("alert-triangle"), h("div", null, h("div", { class: "callout-title" }, "Gescheitert"), h("div", null, a.fehler || "Ohne Fehlermeldung."))));
    if (a.status === "abgebrochen") inhalt.push(h("p", { class: "muted" }, "Dieser Auftrag wurde abgebrochen."));
    if (a.status === "fertig") {
      if (a.kurzfassung) inhalt.push(h("div", { class: "callout ok" }, icon("check-circle"), h("div", null, h("div", { class: "callout-title" }, "Kurzfassung"), h("div", null, a.kurzfassung))));
      inhalt.push(bericht);
    }
    const aktionen = h("div", { class: "row", style: { "--gap": "6px" } },
      a.status === "fertig" ? h("button", { class: "btn sm", onclick: async () => { const t = berichte.get(a.nummer); if (t) kopieren(t); } }, icon("copy"), h("span", { class: "hide-m" }, "Kopieren")) : null,
      a.status === "fertig" ? h("button", { class: "btn sm", onclick: () => imChatVorbereiten(`Was steht im Bericht „${a.titel}“?`) }, icon("message-square"), h("span", { class: "hide-m" }, "Im Chat besprechen")) : null,
      auftragOffen(a) ? h("button", { class: "btn danger sm", onclick: () => abbrechen(a) }, icon("x"), "Abbrechen") : null);
    leeren(leser,
      h("div", { class: "leser-kopf" },
        h("button", { class: "btn ghost sm only-m", type: "button", onclick: () => gehe("auftraege") }, icon("chevron-left"), "Aufträge"),
        h("div", { class: "stack", style: { "--gap": "7px", flex: "1", minWidth: "0" } }, h("h2", { class: "leser-titel" }, a.titel),
          h("div", { class: "row wrap", style: { "--gap": "8px" } }, auftragBadge(a.status), h("span", { class: "dim", style: { fontSize: "var(--fs-xs)" } }, `Nr. ${a.nummer} · angelegt ${vergangen(a.angelegt)}${laufzeit}`))),
        aktionen),
      h("div", { class: "leser-body stack", style: { "--gap": "16px" } }, ...inhalt));
    if (a.status === "fertig" && a.bericht) berichtLaden(a, bericht);
    else if (a.status === "fertig") bericht.replaceChildren(h("p", { class: "muted" }, "Zu diesem Auftrag gibt es keinen Bericht."));
  }

  async function berichtLaden(a, ziel) {
    try {
      if (!berichte.has(a.nummer)) berichte.set(a.nummer, (await api(`auftraege/${a.nummer}/bericht`)).text);
      markdownEinsetzen(ziel, berichte.get(a.nummer));
    } catch (e) { ziel.replaceChildren(h("p", { class: "muted" }, `Bericht nicht lesbar: ${e.message}`)); }
  }

  // --- Laden ----------------------------------------------------------------------------------------------------------------
  const marke = juengste();
  async function laden() {
    const gilt = marke();  // Löschen und Neuladen im Takt können sich überholen: nur die jüngste Antwort zeichnet
    let d;
    try { d = await api("auftraege"); } catch (e) { if (gilt()) leeren(karten, h("div", { class: "card" }, fehlerKarte(e, laden))); return; }
    if (!gilt()) return;
    liste = d.auftraege;
    sprachprozess = d.sprachprozess;
    geladen = true;
    hinweis.hidden = sprachprozess || !liste.some(auftragOffen);
    listeZeichnen();
    leserZeichnen();
  }

  return {
    laden,
    zeigen(params) {
      const nr = Number(params?.[0]);
      gewaehlt = Number.isFinite(nr) && params?.[0] ? nr : null;
      listeZeichnen();
      leserZeichnen(true);
      if (gewaehlt != null) leser.scrollIntoView?.({ block: "nearest" });
    },
    takt: 7000,
  };
}
