// Gedächtnis: Alfreds drei Ebenen aus Textdateien (Kontext, Notizen, Protokolle) plus Profil, Regeln und Feedback.
// Sechs Reiter in einer Ansicht. Die Ansicht bleibt nach dem ersten Besuch im Dokument; ein halb bearbeiteter Kontext,
// der Suchtext und aufgeklappte Einträge bleiben deshalb beim Wechsel erhalten, und der Takt überschreibt keine Eingabe.
import {
  h, icon, api, leeren, prosa, toast, fehler, bestaetigen, imChatVorbereiten, kopieren, entprellen, zahl, plural, wochentag, tageBis,
} from "../core.js";
import { seitenKopf, kartenKopf, leer, skelett, fehlerKarte } from "../teile.js";

const TABS = [
  ["kontext", "Kontext"],
  ["profil", "Profil"],
  ["regeln", "Regeln"],
  ["notizen", "Notizen"],
  ["protokolle", "Protokolle"],
  ["feedback", "Feedback"],
];
const TAB_ALIAS = { wissen: "notizen", protokoll: "protokolle" };
const PROFIL_VORSCHLAEGE = ["wohnort", "kalorienziel", "eiweissziel", "zielgewicht"];
const PROFIL_SYMBOLE = { wohnort: "home", kalorienziel: "flame", eiweissziel: "target", zielgewicht: "trending-down" };
const IST_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

// --- Kleine Helfer ---------------------------------------------------------------------------------------------

/** Gleicher Text, Leerraum am Rand zählt nicht: Der Server liefert den Kontext ohne ihn. */
const gleich = (a, b) => a.trim() === b.trim();
const feldName = (feld) => feld.replace(/-/g, " ");

/** Markiert jeden Treffer im Text unterhalb von el. Arbeitet auf Textknoten, es entsteht nie fremdes HTML. */
function hervorheben(el, begriff) {
  const gesucht = begriff.toLowerCase();
  if (!gesucht) return 0;
  const gang = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const knoten = [];
  for (let k = gang.nextNode(); k; k = gang.nextNode()) knoten.push(k);
  let treffer = 0;
  for (const k of knoten) {
    const text = k.nodeValue;
    const klein = text.toLowerCase();
    let von = klein.length === text.length ? klein.indexOf(gesucht) : -1;  // bei Sonderfällen der Kleinschreibung lieber nichts markieren
    if (von < 0) continue;
    const teile = document.createDocumentFragment();
    let pos = 0;
    while (von >= 0) {
      if (von > pos) teile.append(text.slice(pos, von));
      teile.append(h("mark", { class: "gd-treffer" }, text.slice(von, von + gesucht.length)));
      treffer++;
      pos = von + gesucht.length;
      von = klein.indexOf(gesucht, pos);
    }
    if (pos < text.length) teile.append(text.slice(pos));
    k.replaceWith(teile);
  }
  return treffer;
}

let aufklapperNr = 0;
/** Ein Eintrag zum Auf- und Zuklappen: Kopf als Button mit aria-expanded, darunter der Inhalt. */
function aufklapper({ symbol, titel, meta = null, offen = false, inhalt, beiWechsel = null }) {
  const nr = ++aufklapperNr;
  const koerper = h("div", { class: "gd-acc-body", id: `gd-acc-b${nr}`, role: "region", "aria-labelledby": `gd-acc-k${nr}`, hidden: !offen }, inhalt);
  const knopf = h("button", {
    class: "gd-acc-btn", type: "button", id: `gd-acc-k${nr}`, "aria-expanded": String(!!offen), "aria-controls": `gd-acc-b${nr}`,
    onclick: () => {
      const auf = knopf.getAttribute("aria-expanded") !== "true";
      knopf.setAttribute("aria-expanded", String(auf));
      koerper.hidden = !auf;
      beiWechsel?.(auf);
    },
  }, symbol ? h("span", { class: "lead" }, icon(symbol)) : null, titel, meta, icon("chevron-down", "gd-acc-pfeil"));
  return h("section", { class: "card flush gd-acc" }, h("h3", { class: "gd-acc-h" }, knopf), koerper);
}

/** Bereichsname, Text ohne die erste Überschrift und Zeilenzahl einer Notizdatei. */
function notizInfo(name, text) {
  let titel = name.replace(/-/g, " ");
  titel = titel.charAt(0).toUpperCase() + titel.slice(1);
  let rest = text;
  const m = text.match(/^\s*#\s+(.+?)[ \t]*(?:\n|$)/);  // Alfred schreibt den Bereichsnamen als erste Überschrift
  if (m) {
    titel = m[1];
    rest = text.slice(m[0].length);
  }
  const zeilen = rest.split("\n").filter((z) => z.trim()).length;
  return { name, titel, rest, zeilen, suchtext: `${name}\n${titel}\n${rest}`.toLowerCase() };
}

/** "2026-10-08" → "Donnerstag, 08.10." (mit Jahr, wenn es ein anderes ist) und "heute" oder "gestern". */
function tagText(tag) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(tag);
  if (!m) return { titel: tag, relativ: "" };
  const datum = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const jahr = datum.getFullYear() === new Date().getFullYear() ? "" : m[1];
  const abstand = tageBis(tag);
  return { titel: `${wochentag(datum)}, ${m[3]}.${m[2]}.${jahr}`, relativ: abstand === 0 ? "heute" : abstand === -1 ? "gestern" : "" };
}

/** Einträge der Feedback-Datei: Überschriften zweiter Ebene außerhalb der Codeblöcke. */
function feedbackEintraege(text) {
  let n = 0;
  let code = false;
  for (const zeile of (text || "").split("\n")) {
    if (zeile.trim().startsWith("```")) code = !code;
    else if (!code && /^##\s/.test(zeile)) n++;
  }
  return n;
}

// --- Die Ansicht -------------------------------------------------------------------------------------------------

export function mount(wurzel) {
  wurzel.classList.add("gd");

  let daten = null;          // letzte Antwort von GET gedaechtnis
  let aufgebaut = false;     // sind die Reiter mit ihrem Inhalt gefüllt (oder stehen dort Platzhalter oder Fehler)?
  let signaturen = {};       // je Bereich die zuletzt gezeichnete Fassung: Der Takt zeichnet nur, was sich geändert hat
  let ladeNr = 0;            // Antworten, die vor einer eigenen Änderung losgingen, werden verworfen
  let aktiverTab = "kontext";
  let basis = null;          // der Kontext, wie er beim Laden oder letzten Speichern war
  let serverStand = null;    // der Kontext, wie ihn der Server zuletzt gemeldet hat
  let speichertGerade = false;
  let suchtext = "";
  const offeneNotizen = new Set();
  const offeneProtokolle = new Set();
  const bekannteProtokolle = new Set();

  // --- Gerüst: Reiter und Flächen ------------------------------------------------------------------------------
  const meldung = h("div", { class: "callout warn", role: "status", hidden: true }, icon("wifi"), h("div"));
  const entwurfPunkt = h("span", { class: "gd-punkt", hidden: true }, h("span", { class: "sr-only" }, " (ungespeicherte Änderungen)"));
  const tabKnoepfe = {};
  const zaehlerEl = {};
  const panele = {};
  for (const [id] of TABS) panele[id] = h("div", { class: "gd-panel", id: `gd-panel-${id}`, role: "tabpanel", "aria-labelledby": `gd-tab-${id}`, hidden: true }, h("div", { class: "card" }, skelett(3, 52)));

  const tabliste = h("div", { class: "seg gd-tabs", role: "tablist", "aria-label": "Gedächtnis" }, ...TABS.map(([id, titel]) => {
    const knopf = h("button", {
      type: "button", role: "tab", id: `gd-tab-${id}`, "aria-controls": `gd-panel-${id}`, "aria-selected": "false", tabindex: "-1",
      onclick: () => waehlen(id, true),
    }, titel);
    tabKnoepfe[id] = knopf;
    if (id === "kontext") knopf.append(entwurfPunkt);
    else knopf.append((zaehlerEl[id] = h("span", { class: "count", hidden: true })));
    return knopf;
  }));
  tabliste.addEventListener("keydown", (ev) => {
    const i = TABS.findIndex(([id]) => id === aktiverTab);
    const ziel = { ArrowRight: (i + 1) % TABS.length, ArrowLeft: (i + TABS.length - 1) % TABS.length, Home: 0, End: TABS.length - 1 }[ev.key];
    if (ziel === undefined) return;
    ev.preventDefault();
    waehlen(TABS[ziel][0], true);
    tabKnoepfe[TABS[ziel][0]].focus();
  });

  function waehlen(id, vomNutzer = false) {
    aktiverTab = id;
    for (const [tid] of TABS) {
      const an = tid === id;
      tabKnoepfe[tid].setAttribute("aria-selected", String(an));
      tabKnoepfe[tid].tabIndex = an ? 0 : -1;
      panele[tid].hidden = !an;
    }
    if (vomNutzer) history.replaceState(null, "", `#/gedaechtnis/${id}`);  // löst kein hashchange aus
    tabKnoepfe[id].scrollIntoView?.({ block: "nearest", inline: "nearest" });
    if (id === "kontext") editorScrollHerstellen();
  }

  // Ein ausgeblendetes Feld verliert seinen Scrollstand (Reiterwechsel, Wechsel der Ansicht); der Editor merkt ihn sich.
  let editorScroll = 0;
  function editorScrollHerstellen() {
    if (editor.offsetParent !== null && editor.scrollTop !== editorScroll) editor.scrollTop = editorScroll;
  }

  const neuLaden = h("button", { class: "btn ghost", type: "button", "aria-label": "Aktualisieren", title: "Vom Server neu lesen", onclick: async () => {
    neuLaden.disabled = true;
    await laden();
    neuLaden.disabled = false;
  } }, icon("refresh-cw"), h("span", { class: "hide-m" }, "Aktualisieren"));

  wurzel.append(
    seitenKopf("Gedächtnis", "Was Alfred über dich weiß: drei Ebenen aus Textdateien, die du jederzeit lesen und ändern kannst.", neuLaden),
    meldung,
    h("div", { class: "gd-reiter" }, tabliste),
    ...TABS.map(([id]) => panele[id]));

  // --- Kontext ---------------------------------------------------------------------------------------------------
  const aenderungsBadge = h("span", { class: "badge warn", hidden: true }, h("span", { class: "dot" }), "Ungespeicherte Änderungen");
  const zeichenZaehler = h("span", { class: "gd-zaehler num" });
  const editor = h("textarea", {
    class: "textarea gd-editor", id: "gd-kontext-text", autocomplete: "off", autocapitalize: "off",
    placeholder: "Was soll Alfred immer wissen? Zum Beispiel: Beruf, Arbeitszeiten, Vorlieben, was nervt, was hilft.",
    "aria-label": "Inhalt von kontext.md",
  });
  editor.setAttribute("spellcheck", "false");  // h() lässt false aus; Markdown mit Rechtschreib-Wellen ist unruhig
  editor.addEventListener("input", kontextZeichnen);
  editor.addEventListener("scroll", () => { if (editor.offsetParent !== null) editorScroll = editor.scrollTop; });  // nur im sichtbaren Zustand merken
  editor.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      ev.preventDefault();  // sonst öffnet der Browser seinen Speichern-Dialog
      kontextSpeichern();
    }
  });
  const speichernKnopf = h("button", { class: "btn primary", type: "button", onclick: kontextSpeichern });
  const verwerfenKnopf = h("button", { class: "btn ghost", type: "button", onclick: kontextVerwerfen }, icon("x"), "Verwerfen");
  const konfliktHinweis = h("div", { class: "callout warn gd-mit-knopf", role: "alert", hidden: true }, icon("alert-triangle"),
    h("div", { class: "gd-callout-text" }, "Die Datei hat sich geändert, während du bearbeitest. Speichern ersetzt die neuere Fassung."),
    h("button", { class: "btn sm", type: "button", onclick: kontextNeuLaden }, icon("refresh-cw"), "Neue Fassung laden"));

  const kontextKarte = h("section", { class: "card gd-kontext" },
    h("div", { class: "card-h" },
      h("div", { class: "card-title" }, icon("file-text"), "Kontext"), aenderungsBadge,
      h("span", { class: "spacer" }), h("span", { class: "card-sub mono" }, "kontext.md")),
    h("div", { class: "callout" }, icon("info"),
      h("div", null, "Diese Datei steht komplett in Alfreds Systemprompt, bei jedem Gespräch. Kurz halten spart Kosten und Antwortzeit.")),
    konfliktHinweis,
    editor,
    h("div", { class: "gd-fuss" }, zeichenZaehler, h("span", { class: "spacer" }), verwerfenKnopf, speichernKnopf));

  function kontextZeichnen() {
    if (basis === null) return;
    const text = editor.value;
    const schmutzig = !gleich(text, basis);
    const konflikt = schmutzig && serverStand !== null && !gleich(serverStand, basis);
    aenderungsBadge.hidden = !schmutzig;
    entwurfPunkt.hidden = !schmutzig;
    konfliktHinweis.hidden = !konflikt;
    speichernKnopf.disabled = !schmutzig || speichertGerade;
    verwerfenKnopf.disabled = !schmutzig || speichertGerade;
    if (speichernKnopf.dataset.busy !== String(speichertGerade)) {
      speichernKnopf.dataset.busy = String(speichertGerade);
      leeren(speichernKnopf, icon(speichertGerade ? "loader" : "check", speichertGerade ? "spin" : ""), speichertGerade ? "Speichert …" : "Speichern",
        h("span", { class: "kbd hide-m" }, IST_MAC ? "⌘ S" : "Strg S"));
    }
    zeichenZaehler.textContent = `${zahl(text.length)} Zeichen · rund ${zahl(Math.round(text.length / 4))} Token`;
    zeichenZaehler.title = "Grobe Schätzung: vier Zeichen sind etwa ein Token";
  }

  async function kontextSpeichern() {
    if (basis === null || speichertGerade) return;
    const text = editor.value;
    if (gleich(text, basis)) {
      toast("Keine Änderungen zu speichern", "info", 1800);
      return;
    }
    speichertGerade = true;
    kontextZeichnen();
    try {
      await api("gedaechtnis/kontext", "PUT", { text });
      ladeNr++;
      basis = serverStand = text;
      signaturen.kontext = JSON.stringify(text.trim());  // so liefert der Server ihn zurück
      toast("Gespeichert", "ok");
    } catch (e) {
      fehler(e);
    } finally {
      speichertGerade = false;
      kontextZeichnen();
    }
  }

  async function kontextVerwerfen() {
    if (basis === null || gleich(editor.value, basis)) return;
    const ja = await bestaetigen({ titel: "Änderungen verwerfen?", text: "Der Text im Editor wird auf den gespeicherten Stand zurückgesetzt.", ok: "Verwerfen", gefahr: true });
    if (!ja) return;
    editor.value = basis;
    kontextZeichnen();
  }

  async function kontextNeuLaden() {
    const ja = await bestaetigen({ titel: "Neue Fassung laden?", text: "Deine ungespeicherten Änderungen gehen verloren.", ok: "Neu laden", gefahr: true });
    if (!ja || serverStand === null) return;
    basis = serverStand;
    editor.value = serverStand;
    kontextZeichnen();
  }

  /** Übernimmt den Kontext vom Server, ohne eine Eingabe in Arbeit zu überschreiben. */
  function kontextAbgleichen(text) {
    serverStand = text;
    if (basis === null) {
      basis = text;
      editor.value = text;
    } else if (!gleich(text, basis) && gleich(editor.value, basis)) {
      basis = text;  // woanders geändert und hier nichts in Arbeit: still übernehmen
      editor.value = text;
    }
    kontextZeichnen();
  }

  // Wer die Seite mit ungespeichertem Kontext schließt, wird gefragt.
  window.addEventListener("beforeunload", (ev) => {
    if (basis !== null && !gleich(editor.value, basis)) {
      ev.preventDefault();
      ev.returnValue = "";
    }
  });

  // --- Profil ----------------------------------------------------------------------------------------------------
  const profilGitter = h("div", { class: "auto-grid gd-fakten", style: { "--min": "230px" } });
  const felderListe = h("datalist", { id: "gd-profil-felder" }, ...PROFIL_VORSCHLAEGE.map((f) => h("option", { value: f })));
  const feldEingabe = h("input", {
    class: "input", type: "text", name: "feld", placeholder: "Feld, z. B. wohnort", autocomplete: "off", autocapitalize: "off",
    "aria-label": "Feldname", list: "gd-profil-felder",
  });
  const wertEingabe = h("input", { class: "input", type: "text", name: "wert", placeholder: "Wert", autocomplete: "off", "aria-label": "Wert" });
  const profilKnopf = h("button", { class: "btn primary", type: "submit" }, icon("plus"), "Merken");
  const profilFormular = h("form", { class: "field-row gd-profil-form", onsubmit: profilMerken }, feldEingabe, felderListe, wertEingabe, profilKnopf);
  const profilFormKarte = h("section", { class: "card" },
    kartenKopf("Fakt merken", "plus", h("span", { class: "card-sub" }, "Ein vorhandenes Feld wird ersetzt.")), profilFormular);

  function profilZeigen() {
    const profil = daten?.profil || {};
    const felder = Object.entries(profil);
    leeren(profilGitter, ...(felder.length ? felder.map(([feld, wert]) => faktKarte(feld, String(wert)))
      : [h("div", { class: "card", style: { gridColumn: "1 / -1" } },
        leer("user", "Noch nichts gemerkt", "Sobald du Alfred etwas Wichtiges nennst, etwa deinen Wohnort, merkt er es sich. Unten kannst du auch selbst etwas eintragen."))]));
  }

  function faktKarte(feld, wert) {
    const name = feldName(feld);
    return h("div", { class: "card gd-fakt", "data-feld": feld },
      h("div", { class: "gd-fakt-kopf" },
        h("span", { class: "gd-fakt-name", title: feld }, icon(PROFIL_SYMBOLE[feld] || "tag"), name),
        h("span", { class: "gd-aktionen" },
          h("button", { class: "btn icon ghost sm", type: "button", title: "Bearbeiten", "aria-label": `${name} bearbeiten`, onclick: () => profilBearbeiten(feld, wert) }, icon("edit-3")),
          h("button", { class: "btn icon ghost sm", type: "button", title: "Entfernen", "aria-label": `${name} entfernen`, onclick: () => profilEntfernen(feld, wert) }, icon("trash-2")))),
      h("div", { class: "gd-fakt-wert" }, wert));
  }

  function profilBearbeiten(feld, wert) {
    feldEingabe.value = feld;
    wertEingabe.value = wert;
    wertEingabe.focus();  // scrollt das Formular bei Bedarf ins Bild
    wertEingabe.select();
  }

  function profilUebernehmen(profil) {
    ladeNr++;
    daten.profil = profil;
    signaturen.profil = JSON.stringify(profil);
    zaehlerSetzen();
    profilZeigen();
  }

  async function profilMerken(ev) {
    ev.preventDefault();
    const feld = feldEingabe.value.trim();
    const wert = wertEingabe.value.trim();
    if (!feld || !wert) {
      toast("Trag Feld und Wert ein.", "err");
      (feld ? wertEingabe : feldEingabe).focus();
      return;
    }
    profilKnopf.disabled = true;
    try {
      const r = await api("gedaechtnis/profil", "PATCH", { feld, wert });
      profilUebernehmen(r.profil || {});
      feldEingabe.value = "";
      wertEingabe.value = "";
      toast("Gemerkt", "ok");
      feldEingabe.focus();
    } catch (e) {
      fehler(/^Kein brauchbarer/.test(e.message) ? new Error("Der Feldname braucht mindestens einen Buchstaben oder eine Ziffer.") : e);
    } finally {
      profilKnopf.disabled = false;
    }
  }

  async function profilEntfernen(feld, wert) {
    const ja = await bestaetigen({
      titel: "Aus dem Profil entfernen?", text: `${feldName(feld)}: ${wert}\n\nAlfred fragt danach wieder nach, wenn ein Werkzeug den Wert braucht.`, ok: "Entfernen", gefahr: true,
    });
    if (!ja) return;
    try {
      const r = await api("gedaechtnis/profil", "PATCH", { feld, wert: "" });
      profilUebernehmen(r.profil || {});
      // Der Server räumt nur Feldnamen in seiner eigenen Schreibweise ab; von Hand angelegte Namen bleiben stehen.
      if (feld in (r.profil || {})) throw new Error("Das Feld ließ sich nicht entfernen. Lösche die Zeile in gedaechtnis/profil.yaml von Hand.");
      toast("Entfernt", "ok");
    } catch (e) {
      fehler(e);
    }
  }

  // --- Regeln ----------------------------------------------------------------------------------------------------
  const regelnHinweis = h("div", { class: "callout gd-mit-knopf" }, icon("info"),
    h("div", { class: "gd-callout-text" }, "Regeln sagst du Alfred per Stimme oder im Chat: „Merk dir als Regel: Bei Timern nicht nachfragen.“ Er legt sie erst nach deinem Ja an."),
    h("button", { class: "btn sm", type: "button", onclick: () => imChatVorbereiten("Merk dir als Regel: ") }, icon("message-square"), "Im Chat anlegen"));
  const regelnListe = h("div");
  const regelnFuss = h("p", { class: "hint", hidden: true }, "Diese Regeln gelten zusätzlich zu denen aus Alfreds Persönlichkeit.");

  function regelnZeigen() {
    const regeln = daten?.regeln || [];
    regelnFuss.hidden = !regeln.length;
    leeren(regelnListe, regeln.length
      ? h("div", { class: "card flush" }, h("div", { class: "list" }, ...regeln.map((text, i) => h("div", { class: "item" },
        h("span", { class: "lead gd-nr" }, String(i + 1)),
        h("div", { class: "item-main" }, h("div", { class: "item-title" }, String(text))),
        h("div", { class: "actions" },
          h("button", { class: "btn icon ghost sm", type: "button", title: "Löschen", "aria-label": `Regel ${i + 1} löschen`, onclick: () => regelLoeschen(String(text), i) }, icon("trash-2")))))))
      : h("div", { class: "card" }, leer("sliders", "Noch keine eigenen Regeln", "Sag Alfred, was er künftig immer oder nie tun soll. Die Regel erscheint hier, sobald du zugestimmt hast.")));
  }

  async function regelLoeschen(text, index) {
    const ja = await bestaetigen({ titel: "Regel löschen?", text: `„${text}“\n\nAlfred hält sich danach nicht mehr daran.`, ok: "Löschen", gefahr: true });
    if (!ja) return;
    try {
      // Der Server löscht nach Nummer, von oben gezählt. Hat sich die Liste inzwischen geändert (etwa per Stimme),
      // darf nicht die falsche Regel verschwinden: erst frisch lesen und die Nummer an der Regel selbst festmachen.
      const frisch = await api("gedaechtnis");
      const regeln = frisch.regeln || [];
      const i = regeln[index] === text ? index : regeln.indexOf(text);
      if (i < 0) {
        ladeNr++;
        anwenden(frisch);
        toast("Diese Regel gibt es nicht mehr.");
        return;
      }
      await api(`gedaechtnis/regel?nummer=${i + 1}`, "DELETE");
      ladeNr++;
      anwenden({ ...frisch, regeln: regeln.filter((_, j) => j !== i) });
      toast("Regel gelöscht", "ok");
    } catch (e) {
      fehler(e);
    }
  }

  // --- Notizen ---------------------------------------------------------------------------------------------------
  const suchfeld = h("input", {
    class: "input", type: "text", placeholder: "Notizen durchsuchen …", autocomplete: "off", enterkeyhint: "search", "aria-label": "Notizen durchsuchen",
  });
  const suchLeeren = h("button", { class: "btn icon ghost sm gd-such-x", type: "button", title: "Suche leeren", "aria-label": "Suche leeren", hidden: true, onclick: () => {
    suchfeld.value = "";
    suchen();
    suchfeld.focus();
  } }, icon("x"));
  const bereichsZahl = h("span", { class: "hint" });
  const notizenKopf = h("div", { class: "gd-notizen-kopf" },
    h("div", { class: "gd-suche" }, icon("search"), suchfeld, suchLeeren), bereichsZahl);
  const notizenListe = h("div", { class: "gd-liste" });
  const suchenVerzoegert = entprellen(() => suchen(), 120);
  suchfeld.addEventListener("input", () => {
    suchLeeren.hidden = !suchfeld.value;
    suchenVerzoegert();
  });
  suchfeld.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && suchfeld.value) {
      ev.preventDefault();
      suchfeld.value = "";
      suchen();
    }
  });

  function suchen() {
    suchtext = suchfeld.value;
    suchLeeren.hidden = !suchtext;
    notizenZeigen();
  }

  function notizenZeigen() {
    const q = suchtext.trim().toLowerCase();
    const alle = Object.entries(daten?.wissen || {}).map(([name, text]) => notizInfo(name, String(text)));
    if (!alle.length) {
      bereichsZahl.textContent = "";
      leeren(notizenListe, h("div", { class: "card" },
        leer("book-open", "Noch keine Notizen", "Sag Alfred, was er sich dauerhaft merken soll, etwa: „Merk dir, dass der Bericht am Freitag fällig ist.“ Er sortiert es in einen Bereich ein.")));
      return;
    }
    const treffer = q ? alle.filter((n) => n.suchtext.includes(q)) : alle;
    bereichsZahl.textContent = q ? `${treffer.length} von ${alle.length} Bereichen` : plural(alle.length, "Bereich", "Bereiche");
    if (!treffer.length) {
      leeren(notizenListe, h("div", { class: "card" }, leer("search", "Nichts gefunden", `Kein Bereich enthält „${suchtext.trim()}“.`)));
      return;
    }
    leeren(notizenListe, ...treffer.map((n) => {
      const inhalt = prosa(n.rest);
      const titel = h("span", { class: "gd-acc-titel" }, n.titel);
      const zahlTreffer = q ? hervorheben(inhalt, q) : 0;
      if (q) hervorheben(titel, q);
      return aufklapper({
        symbol: "book-open", titel, inhalt,
        meta: h("span", { class: "gd-acc-meta" }, zahlTreffer ? plural(zahlTreffer, "Treffer", "Treffer") : plural(n.zeilen, "Zeile", "Zeilen")),
        offen: !!q || offeneNotizen.has(n.name),  // bei einer Suche sind die Treffer aufgeklappt
        beiWechsel: (auf) => { if (!q) (auf ? offeneNotizen.add(n.name) : offeneNotizen.delete(n.name)); },
      });
    }));
  }

  // --- Protokolle ------------------------------------------------------------------------------------------------
  const protokolleHinweis = h("div", { class: "callout" }, icon("info"),
    h("div", null, "Alfred fasst jedes Gespräch zusammen. Stimmt darin etwas nicht, lösche die Zeile von Hand in ",
      h("code", { class: "mono" }, "gedaechtnis/protokoll/"), ", sonst wiederholt er den Fehler."));
  const protokolleListe = h("div", { class: "gd-liste" });

  function protokolleZeigen() {
    const tage = Object.entries(daten?.protokolle || {}).sort((a, b) => b[0].localeCompare(a[0]));
    if (!tage.length) {
      leeren(protokolleListe, h("div", { class: "card" }, leer("history", "Noch keine Protokolle", "Nach dem ersten Gespräch fasst Alfred es hier zusammen.")));
      return;
    }
    for (const [tag] of tage) {  // der neueste Tag ist offen, bis du ihn selbst zuklappst
      if (!bekannteProtokolle.has(tag)) {
        bekannteProtokolle.add(tag);
        if (tag === tage[0][0]) offeneProtokolle.add(tag);
      }
    }
    leeren(protokolleListe, ...tage.map(([tag, text]) => {
      const t = tagText(tag);
      const rest = String(text).replace(/^\s*#\s+\d{1,2}\.\d{1,2}\.\d{2,4}[ \t]*(?:\n|$)/, "");  // das Datum steht schon im Kopf
      const eintraege = Math.max((rest.match(/^##\s/gm) || []).length, rest.trim() ? 1 : 0);
      return aufklapper({
        symbol: "calendar", titel: h("span", { class: "gd-acc-titel" }, t.titel),
        meta: [t.relativ ? h("span", { class: "badge accent" }, t.relativ) : null, h("span", { class: "gd-acc-meta" }, plural(eintraege, "Eintrag", "Einträge"))],
        offen: offeneProtokolle.has(tag),
        inhalt: rest.trim() ? prosa(rest, "prose gd-prot") : h("p", { class: "dim" }, "Dieser Tag ist leer."),
        beiWechsel: (auf) => { if (auf) offeneProtokolle.add(tag); else offeneProtokolle.delete(tag); },
      });
    }));
  }

  // --- Feedback --------------------------------------------------------------------------------------------------
  const feedbackKarte = h("section", { class: "card" });

  function feedbackZeigen() {
    const text = (daten?.feedback || "").trim();
    leeren(feedbackKarte,
      h("div", { class: "card-h" }, h("div", { class: "card-title" }, icon("message-square"), "Feedback aus Gesprächen"),
        h("span", { class: "spacer" }), h("span", { class: "card-sub mono hide-m" }, "feedback.md"),
        text ? h("button", { class: "btn ghost sm", type: "button", onclick: () => kopieren(daten.feedback) }, icon("copy"), "Kopieren") : null),
      // Die Überschrift der Datei steht schon im Kopf der Karte; ihr Einleitungssatz bleibt.
      text ? prosa(text.replace(/^#\s+[^\n]*(?:\n|$)/, "")) : leer("message-square", "Noch kein Feedback", "Sag Alfred bei einem Fehler: „Das war falsch, ich meinte …“"));
  }

  // --- Daten ------------------------------------------------------------------------------------------------------
  function zaehlerSetzen() {
    if (!daten) return;
    const n = {
      profil: Object.keys(daten.profil || {}).length,
      regeln: (daten.regeln || []).length,
      notizen: Object.keys(daten.wissen || {}).length,
      protokolle: Object.keys(daten.protokolle || {}).length,
      feedback: feedbackEintraege(daten.feedback),
    };
    for (const [id, el] of Object.entries(zaehlerEl)) {
      el.textContent = String(n[id]);
      el.hidden = !n[id];
    }
  }

  function aufbauen() {
    leeren(panele.kontext, kontextKarte);
    leeren(panele.profil,
      h("p", { class: "gd-hinweis" }, "Fakten, die Werkzeuge brauchen, etwa der Wohnort fürs Wetter. Alfred merkt sich nebenbei Genanntes auch selbst."),
      profilGitter, profilFormKarte);
    leeren(panele.regeln, regelnHinweis, regelnListe, regelnFuss);
    leeren(panele.notizen, notizenKopf, notizenListe);
    leeren(panele.protokolle, protokolleHinweis, protokolleListe);
    leeren(panele.feedback, feedbackKarte);
    aufgebaut = true;
    signaturen = {};
  }

  /** Meldet true, wenn sich der Bereich seit dem letzten Zeichnen geändert hat. */
  function neu(name, wert) {
    const s = JSON.stringify(wert);
    if (signaturen[name] === s) return false;
    signaturen[name] = s;
    return true;
  }

  function anwenden(d) {
    daten = d;
    if (!aufgebaut) aufbauen();
    meldung.hidden = true;
    zaehlerSetzen();
    if (neu("kontext", d.kontext || "")) kontextAbgleichen(d.kontext || "");
    if (neu("profil", d.profil || {})) profilZeigen();
    if (neu("regeln", d.regeln || [])) regelnZeigen();
    if (neu("wissen", d.wissen || {})) notizenZeigen();
    if (neu("protokolle", [d.protokolle || {}, new Date().toDateString()])) protokolleZeigen();  // am Tageswechsel stimmen "heute" und "gestern" wieder
    if (neu("feedback", d.feedback || "")) feedbackZeigen();
  }

  async function laden() {
    const nr = ++ladeNr;
    try {
      const d = await api("gedaechtnis");
      if (nr === ladeNr) anwenden(d);
    } catch (e) {
      if (nr !== ladeNr) return;
      if (daten) {  // der letzte Stand bleibt stehen, nichts wird weggewischt
        meldung.hidden = false;
        meldung.lastChild.textContent = `Aktualisieren hat nicht geklappt: ${e.message}. Du siehst den letzten Stand.`;
        return;
      }
      aufgebaut = false;
      for (const [id] of TABS) leeren(panele[id], h("div", { class: "card" }, fehlerKarte(e, laden)));
    }
  }

  let sichtbar = false;
  function zeigen(params = []) {
    const neuSichtbar = !sichtbar;
    sichtbar = true;
    const wunsch = TAB_ALIAS[params[0]] || params[0];
    if (TABS.some(([id]) => id === wunsch)) waehlen(wunsch);
    else if (!params.length && aktiverTab !== "kontext") history.replaceState(null, "", `#/gedaechtnis/${aktiverTab}`);  // die Adresse folgt dem Reiter
    if (neuSichtbar && aktiverTab === "kontext") editorScrollHerstellen();
  }

  waehlen("kontext");
  return { laden, zeigen, verlassen() { sichtbar = false; }, takt: 20000 };
}
