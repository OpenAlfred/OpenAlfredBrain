// Befehlspalette (Strg/⌘ K oder /): zu einer Ansicht springen, eine Fähigkeit finden, schnell etwas anlegen oder Alfred fragen.
import { h, icon, api, store, gehe, imChatVorbereiten, imChatAufnehmen, faehigkeitenLaden, toast, fehler, zaehlerLaden } from "./core.js";
import { designWeiter } from "./design.js";

const norm = (t) => t.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");

let oeffnet = false;  // schon unterwegs (die Übersicht lädt noch): ein zweites Strg+K öffnet keine zweite Palette

export async function paletteOeffnen({ ansichten }) {
  if (oeffnet || document.getElementById("palette")) return;
  oeffnet = true;
  let f = store.get("faehigkeiten");
  if (!f) { try { f = await faehigkeitenLaden(); } catch { f = null; } }
  oeffnet = false;

  const vorher = document.activeElement;
  const eingabe = h("input", { type: "text", placeholder: "Gehe zu, suche eine Fähigkeit oder frag Alfred …", autocomplete: "off", spellcheck: false, "aria-label": "Befehlspalette" });
  const liste = h("div", { class: "palette-list", role: "listbox" });
  let treffer = [];
  let wahl = 0;

  const taste = (...namen) => h("span", { class: "kbd" }, ...namen);
  const wrap = h("div", { class: "palette-wrap", id: "palette", onmousedown: (ev) => { if (ev.target === wrap) zu(); } },
    h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Befehlspalette" },
      h("div", { class: "palette-input" }, icon("search"), eingabe, taste("Esc")),
      liste,
      h("div", { class: "palette-foot" },
        h("span", null, taste("↑"), taste("↓"), "wählen"), h("span", null, taste("↵"), "ausführen"),
        h("span", { class: "spacer", style: { flex: "1" } }), h("span", null, "Tipp: Satz eintippen, um Alfred zu fragen"))));

  function zu() {
    wrap.remove();
    document.removeEventListener("keydown", global, true);
    vorher?.focus?.();
  }
  async function ausfuehren(t) {
    zu();
    try { await t.aktion(); } catch (e) { fehler(e); }
  }

  /** Alle Befehle, die zu q passen, in Gruppen sortiert. */
  function sammeln(q) {
    const alle = [];
    const n = norm(q.trim());
    const passt = (text) => !n || norm(text).includes(n);
    for (const a of ansichten) if (passt(`${a.titel} ${a.id}`)) alle.push({ gruppe: "Gehe zu", symbol: a.symbol, text: a.titel, aktion: () => gehe(a.id) });
    if (passt("Sprachnachricht aufnehmen Mikrofon sprechen diktieren")) alle.push({ gruppe: "Sprechen", symbol: "mic", text: "Sprachnachricht aufnehmen", sub: "im Chat, Alfred hört sie ab", aktion: imChatAufnehmen });
    if (q.trim().length >= 2) {
      const text = q.trim();
      alle.unshift(
        { gruppe: "Jetzt tun", symbol: "sparkles", text: "Alfred fragen", sub: `„${text}“`, aktion: () => imChatVorbereiten(text, true) },
        { gruppe: "Jetzt tun", symbol: "check-square", text: "Als Aufgabe anlegen", sub: `„${text}“`, aktion: async () => { await api("aufgaben", "POST", { text }); toast("Aufgabe angelegt", "ok"); zaehlerLaden(); } },
        { gruppe: "Jetzt tun", symbol: "shopping-cart", text: "Auf die Einkaufsliste", sub: `„${text}“`, aktion: async () => { const r = await api("einkauf", "POST", { text }); toast(r.hinzugefuegt.length ? `Dazu: ${r.hinzugefuegt.join(", ")}` : "Steht schon drauf", "ok"); zaehlerLaden(); } },
        { gruppe: "Jetzt tun", symbol: "layers", text: "Als Auftrag an Alfred", sub: `„${text}“`, aktion: async () => { const r = await api("auftraege", "POST", { text }); toast(r.bestaetigung, "ok", 7000); zaehlerLaden(); } },
      );
    }
    if (f) {
      for (const g of f.gruppen) {
        if (n && passt(`${g.titel} ${g.beschreibung}`)) alle.push({ gruppe: "Fähigkeiten", symbol: g.symbol, text: g.titel, sub: g.beschreibung, aktion: () => gehe(`faehigkeiten/${g.id}`) });
      }
      if (n) {
        for (const g of f.gruppen) for (const w of g.werkzeuge) {
          if (passt(`${w.name} ${w.beschreibung}`)) alle.push({ gruppe: "Werkzeuge", symbol: g.symbol, text: w.name, mono: true, sub: w.beschreibung, aktion: () => gehe(`faehigkeiten/${g.id}`) });
        }
      }
    }
    if (passt("design hell dunkel")) alle.push({ gruppe: "Einstellungen", symbol: "moon", text: "Design wechseln", sub: "Dunkel, Hell, System", aktion: designWeiter });
    return alle.slice(0, 40);
  }

  function zeichnen() {
    treffer = sammeln(eingabe.value);
    wahl = Math.min(wahl, Math.max(0, treffer.length - 1));
    liste.replaceChildren();
    if (!treffer.length) { liste.append(h("div", { class: "empty" }, icon("search"), h("b", null, "Nichts gefunden"))); return; }
    let gruppe = null;
    treffer.forEach((t, i) => {
      if (t.gruppe !== gruppe) { gruppe = t.gruppe; liste.append(h("div", { class: "palette-group" }, gruppe)); }
      liste.append(h("button", { class: "palette-item", type: "button", role: "option", tabindex: "-1", "aria-selected": String(i === wahl),
        onmousemove: () => { if (wahl !== i) { wahl = i; markieren(); } }, onclick: () => ausfuehren(t) },
        h("span", { class: "lead" }, icon(t.symbol)),
        h("span", { class: "p-main" }, h("span", { class: t.mono ? "mono" : "" }, t.text), t.sub ? h("span", { class: "p-sub" }, ` ${t.sub}`) : null),
        h("span", { class: "kbd", style: { opacity: i === wahl ? 1 : 0 } }, "↵")));
    });
  }
  function markieren() {
    [...liste.querySelectorAll(".palette-item")].forEach((el, i) => {
      el.setAttribute("aria-selected", String(i === wahl));
      el.querySelector(".kbd").style.opacity = i === wahl ? 1 : 0;
      if (i === wahl) el.scrollIntoView({ block: "nearest" });
    });
  }
  function global(ev) {
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); zu(); }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); wahl = (wahl + 1) % Math.max(1, treffer.length); markieren(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); wahl = (wahl - 1 + treffer.length) % Math.max(1, treffer.length); markieren(); }
    else if (ev.key === "Enter" && treffer[wahl]) { ev.preventDefault(); ausfuehren(treffer[wahl]); }
    else if (ev.key === "Tab") { ev.preventDefault(); eingabe.focus(); }  // Fokusfang: Das Feld bleibt der einzige Halt, Pfeile wählen
  }

  eingabe.addEventListener("input", () => { wahl = 0; zeichnen(); });
  document.addEventListener("keydown", global, true);
  document.body.append(wrap);
  zeichnen();
  eingabe.focus();
}
