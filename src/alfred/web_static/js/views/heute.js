// Heute: Wetter, Zeitleiste aus Kalender und Erinnerungen, Aufgaben, Timer, Geburtstage, Routinen.
import {
  h, icon, api, uhrzeit, datumLang, relativ, countdown, sekundentakt, faelligkeit, leeren, zaehlerLaden, fehler, tageBis, datumKurz, wochentag, juengste,
} from "../core.js";
import { seitenKopf, kartenKopf, leer, skelett, fehlerKarte, checkKnopf, wetterTeile } from "../teile.js";

const dauerText = (von, bis) => {
  const min = Math.round((new Date(bis) - new Date(von)) / 60000);
  return min >= 60 ? `${Math.floor(min / 60)} Std.${min % 60 ? ` ${min % 60} Min.` : ""}` : `${min} Min.`;
};

function tagName(iso) {
  const tage = tageBis(iso);
  if (tage === 0) return "Heute";
  if (tage === 1) return "Morgen";
  const [j, m, t] = iso.slice(0, 10).split("-").map(Number);
  return `${wochentag(new Date(j, m - 1, t))}, ${datumKurz(iso)}`;
}

export function mount(wurzel) {
  const datum = h("p", null, datumLang());
  const wetter = h("section", { class: "card wetter" }, skelett(1, 72));
  const zeitleiste = h("section", { class: "card" }, skelett(4, 54));
  const aufgaben = h("section", { class: "card" }, skelett(3));
  const erinnerungen = h("section", { class: "card" }, skelett(2));
  const geburtstage = h("section", { class: "card" }, skelett(1));
  const routinen = h("section", { class: "card" }, skelett(2));
  const aktualisieren = h("button", { class: "btn", type: "button", onclick: () => laden() }, icon("refresh-cw"), h("span", { class: "hide-m" }, "Aktualisieren"));

  const kopf = seitenKopf("Heute", "", aktualisieren);
  kopf.querySelector("h1").after(datum);
  wurzel.append(kopf, h("div", { class: "heute-grid" },
    h("div", { class: "stack", style: { "--gap": "16px" } }, wetter, zeitleiste),
    h("div", { class: "stack", style: { "--gap": "16px" } }, aufgaben, erinnerungen, geburtstage, routinen)));

  let daten = null;
  const marke = juengste();

  function wetterZeigen(text) {
    const w = wetterTeile(text);
    if (!text) {
      leeren(wetter, h("div", { class: "row" }, h("span", { class: "lead" }, icon("cloud")),
        h("div", { class: "item-main" }, h("div", { class: "item-title" }, "Kein Wetter"), h("div", { class: "item-sub" }, "Sag Alfred, wo du wohnst, dann zeigt er die Vorhersage."))));
      return;
    }
    if (!w) { leeren(wetter, h("div", { class: "row" }, h("span", { class: "lead" }, icon("cloud")), h("div", { class: "item-title" }, text))); return; }
    leeren(wetter,
      h("div", { class: "wetter-kopf" },
        h("div", { class: "wetter-symbol" }, icon(w.symbol)),
        h("div", { class: "wetter-haupt" },
          h("div", { class: "wetter-temp num" }, w.hoch != null ? `${w.hoch}°` : "–", w.tief != null ? h("small", null, ` / ${w.tief}°`) : null),
          h("div", { class: "wetter-lage" }, `${w.lage[0].toUpperCase()}${w.lage.slice(1)}`, h("span", { class: "dim" }, ` · ${w.tag === "Morgen" ? "morgen" : "heute"} in ${w.ort}`))),
        h("div", { class: "wetter-werte" },
          w.regen != null ? h("span", { class: "badge info" }, icon("droplet"), `${w.regen} % Regen`) : null,
          w.wind != null ? h("span", { class: "badge" }, icon("wind"), `${w.wind} km/h`) : null)));
  }

  function zeitleisteZeigen(d) {
    const jetzt = Date.now();
    const fehlerText = d.termine.find((t) => t.startsWith("Kalender nicht erreichbar"));
    const ganztags = d.kalender.filter((t) => t.ganztags);
    const eintraege = [
      ...d.kalender.filter((t) => !t.ganztags).map((t) => ({ art: "termin", beginn: t.beginn, ende: t.ende, titel: t.titel })),
      ...d.erinnerungen.filter((e) => !e.timer && new Date(e.faellig) - jetzt < 864e5).map((e) => ({ art: "erinnerung", beginn: e.faellig, titel: e.text, wiederholung: e.wiederholung })),
    ].sort((a, b) => new Date(a.beginn) - new Date(b.beginn));

    const reihen = [];
    let tag = null;
    let markeGesetzt = false;
    for (const e of eintraege) {
      const laeuft = e.art === "termin" && new Date(e.beginn) <= jetzt && new Date(e.ende) > jetzt;
      const name = tagName(e.beginn);
      if (name !== tag) { tag = name; reihen.push(h("div", { class: "tl-tag" }, name)); }
      // Die „Jetzt“-Marke steht vor dem ersten heutigen Eintrag, der noch bevorsteht, nach der Tagesüberschrift.
      if (!markeGesetzt && !laeuft && new Date(e.beginn) > jetzt && tageBis(e.beginn) === 0) {
        reihen.push(h("div", { class: "tl-jetzt" }, h("span", { class: "zeit num" }, new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" }).format(jetzt)),
          h("span", { class: "punkt" }), h("span", { class: "linie" }, "Jetzt")));
      }
      if (!laeuft && new Date(e.beginn) > jetzt) markeGesetzt = true;
      const naechster = !laeuft && !reihen.some((r) => r.classList?.contains("naechster")) && new Date(e.beginn) > jetzt;
      reihen.push(h("div", { class: `tl-row${laeuft ? " jetzt" : ""}${naechster ? " naechster" : ""}${e.art === "erinnerung" ? " klein" : ""}` },
        h("div", { class: "tl-zeit" }, h("b", null, uhrzeit(e.beginn)), e.ende ? uhrzeit(e.ende) : null),
        h("div", { class: "tl-schiene" }, h("span", { class: "tl-punkt" })),
        h("div", { class: "tl-karte" },
          h("div", { class: "row", style: { "--gap": "8px" } },
            e.art === "erinnerung" ? icon("bell", "dim") : null,
            h("div", { class: "item-title", style: { flex: "1" } }, e.titel),
            laeuft ? h("span", { class: "badge accent" }, h("span", { class: "dot pulse" }), "läuft") : naechster ? h("span", { class: "badge" }, relativ(e.beginn)) : null),
          e.art === "termin" ? h("div", { class: "item-sub" }, dauerText(e.beginn, e.ende)) : e.wiederholung ? h("div", { class: "item-sub" }, e.wiederholung) : null)));
    }
    leeren(zeitleiste, kartenKopf("Nächste 24 Stunden", "calendar", h("span", { class: "card-sub" }, "Kalender und Erinnerungen")),
      ganztags.length ? h("div", { class: "row wrap", style: { "--gap": "8px", marginBottom: "14px" } }, ...ganztags.map((t) => h("span", { class: "badge violet" }, icon("sun"), `${tageBis(t.beginn) > 0 ? `${tagName(t.beginn).toLowerCase()} ` : ""}ganztägig: ${t.titel}`))) : null,
      fehlerText ? h("div", { class: "callout danger" }, icon("alert-triangle"), h("div", null, fehlerText))
        : reihen.length ? h("div", { class: "tl" }, ...reihen)
          : leer("calendar", "Nichts in den nächsten 24 Stunden", "Termine aus dem Kalender und Erinnerungen erscheinen hier."));
  }

  function aufgabenZeigen(d) {
    const liste = [...d.aufgaben].sort((a, b) => (a.faellig || "9999").localeCompare(b.faellig || "9999")).slice(0, 7);
    leeren(aufgaben, kartenKopf("Aufgaben", "check-square", h("a", { class: "btn ghost sm", href: "#/aufgaben" }, "Alle", icon("chevron-right"))),
      liste.length ? h("div", { class: "list inset" }, ...liste.map((a) => {
        const f = faelligkeit(a.faellig);
        const zeile = h("div", { class: "item" },
          checkKnopf(false, async () => {
            zeile.classList.add("weg");
            try { await api("aufgaben", "PATCH", { nummer: a.nummer }); } catch (e) { fehler(e); }
            laden();
            zaehlerLaden();
          }, `„${a.text}“ erledigt`),
          h("div", { class: "item-main" }, h("div", { class: "item-title" }, a.text)),
          f.text ? h("span", { class: `badge ${f.art}` }, f.text) : null);
        return zeile;
      })) : leer("check-square", "Nichts offen", "Alle Aufgaben sind erledigt."));
  }

  function erinnerungenZeigen(d) {
    const timer = d.erinnerungen.filter((e) => e.timer);
    const normal = d.erinnerungen.filter((e) => !e.timer);
    leeren(erinnerungen, kartenKopf("Erinnerungen und Timer", "bell"),
      timer.length ? h("div", { class: "row wrap", style: { "--gap": "8px", marginBottom: "10px" } }, ...timer.map((e) =>
        h("span", { class: "timer-pill", "data-faellig": e.faellig }, icon("timer"), h("b", { class: "num" }, countdown(e.faellig)), h("span", null, e.text.replace(/^Timer: /, ""))))) : null,
      normal.length ? h("div", { class: "list inset" }, ...normal.map((e) => h("div", { class: "item" },
        h("span", { class: "lead" }, icon(e.wiederholung ? "repeat" : "bell")),
        h("div", { class: "item-main" }, h("div", { class: "item-title" }, e.text), h("div", { class: "item-sub" }, e.wiederholung || "einmalig")),
        h("span", { class: "item-meta" }, relativ(e.faellig)))))
        : timer.length ? null : leer("bell", "Keine Erinnerungen", "Sag: „Erinner mich in zwanzig Minuten an die Wäsche.“"));
  }

  function geburtstageZeigen(d) {
    leeren(geburtstage, kartenKopf("Geburtstage", "gift", h("span", { class: "card-sub" }, "nächste zwei Wochen")),
      d.geburtstage?.length ? h("div", { class: "list inset" }, ...d.geburtstage.map((g) => {
        const [name, ...rest] = g.split(": ");
        return h("div", { class: "item" }, h("span", { class: "lead violet" }, icon("gift")), h("div", { class: "item-main" }, h("div", { class: "item-title" }, name), h("div", { class: "item-sub" }, rest.join(": "))));
      })) : leer("gift", "Keine in den nächsten zwei Wochen"));
  }

  function routinenZeigen(d) {
    leeren(routinen, kartenKopf("Routinen", "repeat", h("span", { class: "card-sub" }, "führt Alfred von selbst aus")),
      d.routinen.length ? h("div", { class: "list inset" }, ...d.routinen.map((r) => h("div", { class: "item" },
        h("span", { class: "lead accent" }, icon("repeat")), h("div", { class: "item-main" }, h("div", { class: "item-title" }, r.name), h("div", { class: "item-sub" }, r.zeitplan)))))
        : leer("repeat", "Keine Routinen", "Sag: „Jeden Morgen um halb acht: Wetter und meine Termine.“"));
  }

  async function laden() {
    const gilt = marke();
    let d;
    try {
      d = await api("heute");
    } catch (e) {
      if (!gilt()) return;
      for (const k of [wetter, zeitleiste, aufgaben, erinnerungen, geburtstage, routinen]) leeren(k, fehlerKarte(e, laden));
      return;
    }
    if (!gilt()) return;  // eine neuere Anfrage läuft schon: die ältere Antwort darf sie nicht überschreiben
    daten = d;
    datum.textContent = datumLang();
    wetterZeigen(daten.wetter);
    zeitleisteZeigen(daten);
    aufgabenZeigen(daten);
    erinnerungenZeigen(daten);
    geburtstageZeigen(daten);
    routinenZeigen(daten);
  }

  sekundentakt(wurzel, () => {
    for (const pille of wurzel.querySelectorAll(".timer-pill")) pille.querySelector("b").textContent = countdown(pille.dataset.faellig);
  });

  return { laden, takt: 60000 };
}
