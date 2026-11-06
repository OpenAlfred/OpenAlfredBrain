// Chat: Schreiben mit Alfred oder ihm eine Sprachnachricht schicken, mit denselben Werkzeugen und demselben Gedächtnis
// wie beim Sprechen. Jeder Werkzeugaufruf steht als Karte vor der Antwort; wartet eine Aktion auf ein Ja, bietet die
// Antwort Ja und Nein an.
import { h, icon, api, store, orb, orbSetzen, faehigkeitenLaden, werkzeugInfo, leeren, zaehlerLaden, toast } from "../core.js";
import { seitenKopf, stufenBadge, STUFEN } from "../teile.js";
import { aufnahmeHindernis, aufnahmeStarten, alsWav, sprachnachrichtSenden, minuten, MIN_SEKUNDEN, MAX_SEKUNDEN } from "../aufnahme.js";

const STATUS = {
  ok: { text: "ausgeführt", symbol: "check", art: "ok" },
  bestaetigung: { text: "wartet auf dein Ja", symbol: "shield-check", art: "warn" },
  fehler: { text: "Fehler", symbol: "alert-triangle", art: "danger" },
};
const BALKEN = 40;  // Pegelbalken der Aufnahme-Leiste
// Länge einer fertigen Aufnahme: aufgerundet, damit eine kurze nicht „0:00“ heißt (die laufende Uhr zählt ganze Sekunden).
const laenge = (sekunden) => minuten(Math.max(1, Math.round(sekunden)));

function gruppeSymbol(schritt) {
  const f = store.get("faehigkeiten");
  return f?.gruppen.find((g) => g.id === schritt.gruppe)?.symbol || "tool";
}

function argumentText(argumente) {
  const eintraege = Object.entries(argumente || {});
  if (!eintraege.length) return "";
  return eintraege.map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ");
}

export function mount(wurzel) {
  let wartet = false;
  let signatur = "";       // Stand des Verlaufs auf dem Server (Anzahl|letzter Text); ändert er sich nicht, bleibt die Anzeige
  let serverAnzahl = 0;

  const strom = h("div", { class: "chat-strom", "aria-live": "polite" });
  const eingabe = h("textarea", { rows: 1, placeholder: "Schreib Alfred …", "aria-label": "Nachricht an Alfred", enterkeyhint: "send", autocomplete: "off" });
  const sendeKnopf = h("button", { class: "btn primary icon", type: "submit", "aria-label": "Senden" }, icon("arrow-up"));
  const mikroKnopf = h("button", { class: "btn icon ghost mikro", type: "button", "aria-label": "Sprachnachricht aufnehmen", title: "Sprachnachricht aufnehmen", onclick: () => aufnahmeBeginnen() }, icon("mic"));

  // Die Aufnahme-Leiste steht, solange eine Sprachnachricht entsteht, an der Stelle des Eingabefelds.
  const balken = Array.from({ length: BALKEN }, () => h("i"));
  const zeitAnzeige = h("span", { class: "aufnahme-zeit num" }, "0:00");
  const leistenHinweis = h("span", { class: "aufnahme-hinweis", hidden: true });
  const ansage = h("span", { class: "sr-only", "aria-live": "polite" });
  const anhoerenKnopf = h("button", { class: "btn icon ghost", type: "button", "aria-label": "Aufnahme anhören", title: "Anhören", hidden: true, onclick: () => anhoeren() }, icon("play"));
  const stoppKnopf = h("button", { class: "btn icon ghost", type: "button", "aria-label": "Aufnahme beenden, ohne zu senden", title: "Beenden und anhören", onclick: () => aufnahmeBeenden() }, icon("square"));
  const leiste = h("div", { class: "aufnahme-leiste", hidden: true },
    h("button", { class: "btn icon ghost", type: "button", "aria-label": "Aufnahme verwerfen", title: "Verwerfen", onclick: () => aufnahmeVerwerfen() }, icon("trash-2")),
    h("span", { class: "aufnahme-punkt", "aria-hidden": "true" }), zeitAnzeige,
    h("div", { class: "pegel", "aria-hidden": "true" }, ...balken), leistenHinweis, anhoerenKnopf, stoppKnopf, ansage);

  const vorschlaege = h("div", { class: "row wrap", style: { "--gap": "8px", justifyContent: "center" } });
  const formular = h("form", { class: "composer chat-composer", onsubmit: (ev) => {
    ev.preventDefault();
    if (aufnahme || entwurf) aufnahmeSenden();
    else senden(eingabe.value);
  } }, eingabe, leiste, mikroKnopf, sendeKnopf);
  const dock = h("div", { class: "chat-dock" }, formular,
    h("p", { class: "hint chat-hinweis" }, "Enter sendet, Umschalt+Enter macht eine neue Zeile, das Mikrofon nimmt eine Sprachnachricht auf. Alfred fragt bei allem, was nach außen wirkt, erst nach."));
  const kopf = seitenKopf("Chat", "Schreib mit Alfred oder sprich ihm eine Nachricht ein: dieselben Werkzeuge und dasselbe Gedächtnis wie beim Sprechen.");
  wurzel.append(kopf, strom, dock);

  function hoehe() {
    eingabe.style.height = "auto";
    eingabe.style.height = `${Math.min(eingabe.scrollHeight, 160)}px`;
  }
  eingabe.addEventListener("input", hoehe);
  eingabe.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); senden(eingabe.value); }
  });

  // --- Nachrichten ----------------------------------------------------------------------------------------------
  function schrittKarte(s) {
    const st = STATUS[s.status] || STATUS.ok;
    const info = werkzeugInfo(s.name);
    const args = argumentText(s.argumente);
    const detail = h("div", { class: "step-detail", hidden: true },
      info ? h("div", { class: "step-desc" }, info.beschreibung) : null,
      s.stufe ? h("div", { class: "row", style: { "--gap": "8px" } }, stufenBadge(s.stufe), h("span", { class: "hint" }, STUFEN[s.stufe]?.erklaerung)) : null,
      args ? h("div", { class: "step-zeile" }, h("span", null, "Argumente"), h("code", null, args)) : null,
      s.text ? h("div", { class: "step-zeile" }, h("span", null, s.status === "fehler" ? "Fehler" : s.status === "bestaetigung" ? "Rückfrage" : "Ergebnis"), h("code", null, s.text)) : null);
    const kopfZeile = h("button", { class: "step-kopf", type: "button", "aria-expanded": "false", onclick: () => {
      const offen = detail.hidden;
      detail.hidden = !offen;
      kopfZeile.setAttribute("aria-expanded", String(offen));
    } },
      h("span", { class: "step-icon" }, icon(gruppeSymbol(s))),
      h("span", { class: "step-name mono" }, s.name),
      h("span", { class: "step-kurz" }, s.status === "fehler" ? s.text : s.status === "bestaetigung" ? s.text : args || s.text),
      h("span", { class: `badge ${st.art}` }, icon(st.symbol), h("span", { class: "hide-m" }, st.text)),
      icon("chevron-down", "step-pfeil"));
    return h("div", { class: `step ${s.status}`, "data-lvl": s.stufe || null }, kopfZeile, detail);
  }

  function alfredNachricht(text, schritte = [], antwortAngebot = false) {
    const avatar = orb("xs");
    orbSetzen(avatar, { erreichbar: true, zustand: "bereit" });
    const koerper = h("div", { class: "msg-body" },
      schritte.length ? h("div", { class: "steps" }, h("div", { class: "steps-titel" }, icon("tool"), `${schritte.length === 1 ? "1 Werkzeug" : `${schritte.length} Werkzeuge`} benutzt`), ...schritte.map(schrittKarte)) : null,
      text ? h("div", { class: "bubble" }, text) : null);
    const offeneFrage = antwortAngebot && schritte.some((s) => s.status === "bestaetigung");
    if (offeneFrage) {
      // Der Klartext steht ungekürzt über den Knöpfen: Ein Tipp auf „Ja“ darf nie etwas bestätigen, das man nicht lesen konnte.
      const frage = schritte.filter((st) => st.status === "bestaetigung").at(-1);
      koerper.append(h("div", { class: "quick stack", style: { "--gap": "8px" } },
        frage?.text ? h("div", { class: "quick-frage" }, h("span", { class: "overline" }, "Wartet auf dein Ja"), h("div", null, frage.text)) : null,
        h("div", { class: "row wrap", style: { "--gap": "8px" } },
          h("button", { class: "btn primary sm", type: "button", onclick: (ev) => { entfernen(ev); senden("Ja, mach das."); } }, icon("check"), "Ja, ausführen"),
          h("button", { class: "btn sm", type: "button", onclick: (ev) => { entfernen(ev); senden("Nein."); } }, icon("x"), "Nein"))));
    }
    return h("div", { class: "msg alfred" }, h("div", { class: "msg-avatar" }, avatar), koerper);
  }
  const entfernen = (ev) => ev.currentTarget.closest(".quick")?.remove();

  const duNachricht = (text) => h("div", { class: "msg du" }, h("div", { class: "bubble" }, text));

  /** Eine Sprachnachricht im Verlauf: mit Abspielknopf, solange die Aufnahme in dieser Sitzung noch da ist, sonst mit Mikrofon. */
  function sprachNachricht(abschrift, aufnahme = null) {
    const text = h("div", { class: "sprach-text" }, abschrift ?? "");
    let knopf;
    if (aufnahme) {
      const audio = new Audio(URL.createObjectURL(aufnahme.blob));
      knopf = h("button", { class: "sprach-knopf", type: "button", "aria-label": "Sprachnachricht anhören",
        onclick: () => (audio.paused ? audio.play().catch(() => toast("Abspielen geht in diesem Browser nicht.", "err")) : audio.pause()) }, icon("play"));
      audio.onplay = () => leeren(knopf, icon("pause"));
      audio.onpause = () => leeren(knopf, icon("play"));
    } else {
      knopf = h("span", { class: "sprach-knopf", "aria-hidden": "true" }, icon("mic"));
    }
    const el = h("div", { class: "msg du" }, h("div", { class: "bubble sprach" },
      h("div", { class: "sprach-kopf" }, knopf, h("span", null, "Sprachnachricht"), aufnahme ? h("span", { class: "num" }, laenge(aufnahme.sekunden)) : null),
      text));
    if (abschrift == null) {
      text.textContent = "Alfred hört zu …";
      text.classList.add("wartet");
    }
    el.abschreiben = (t, fehl = false) => {
      text.textContent = t;
      text.classList.remove("wartet");
      text.classList.toggle("fehl", fehl);
    };
    return el;
  }

  function wartenNachricht() {
    const avatar = orb("xs");
    orbSetzen(avatar, { erreichbar: true, zustand: "denkt" });
    return h("div", { class: "msg alfred warten" }, h("div", { class: "msg-avatar" }, avatar),
      h("div", { class: "msg-body" }, h("div", { class: "bubble tippt", "aria-label": "Alfred überlegt" }, h("i"), h("i"), h("i"))));
  }

  function fehlerNachricht(e) {
    const o = orb("xs");
    orbSetzen(o, { erreichbar: false });
    return h("div", { class: "msg alfred" }, h("div", { class: "msg-avatar" }, o),
      h("div", { class: "msg-body" }, h("div", { class: "bubble fehl" }, icon("alert-triangle"), ` ${e.message}`)));
  }

  function leerZustand() {
    const o = orb("lg");
    orbSetzen(o, { erreichbar: true, zustand: "bereit" });
    return h("div", { class: "chat-leer" }, o, h("h2", null, "Womit kann ich helfen?"),
      h("p", null, "Frag etwas, gib einen Auftrag oder lass dir etwas eintragen, getippt oder als Sprachnachricht über das Mikrofon. Mit einem Klick auf einen Vorschlag landet er im Eingabefeld."), vorschlaege);
  }

  function nachUnten() {
    requestAnimationFrame(() => strom.lastElementChild?.scrollIntoView({ block: "end", behavior: "smooth" }));
  }

  // --- Senden ------------------------------------------------------------------------------------------------------------
  /** Ein Zug: die eigene Nachricht, Alfred überlegt, dann seine Antworten. anfrage() liefert die Antwort des Servers. */
  async function zug(eigene, anfrage, beiFehler = () => {}) {
    wartet = true;
    sendeKnopf.disabled = true;
    strom.querySelector(".chat-leer")?.remove();
    strom.querySelectorAll(".quick").forEach((q) => q.remove());
    strom.append(eigene);
    const warten = wartenNachricht();
    strom.append(warten);
    nachUnten();
    try {
      const r = await anfrage();
      warten.remove();
      r.antworten.forEach((a, i) => strom.append(alfredNachricht(a, i === 0 ? r.schritte : [], i === 0)));
      serverAnzahl += 1 + r.antworten.filter(Boolean).length;
      signatur = `${serverAnzahl}|${r.antworten.at(-1) ?? ""}`;
      zaehlerLaden();
    } catch (e) {
      warten.remove();
      beiFehler(e);
      strom.append(fehlerNachricht(e));
    } finally {
      wartet = false;
      sendeKnopf.disabled = false;
      nachUnten();
    }
  }

  async function senden(roh) {
    const text = roh.trim();
    if (!text || wartet) return;
    eingabe.value = "";
    hoehe();
    await zug(duNachricht(text), () => api("chat", "POST", { text }));
    eingabe.focus({ preventScroll: true });
  }

  // --- Sprachnachricht -------------------------------------------------------------------------------------------------
  let aufnahme = null;   // läuft gerade
  let entwurf = null;    // beendet, wartet auf Senden oder Verwerfen: { blob, sekunden, still }
  let uhr = 0;
  let wiedergabe = null;
  let pegelWerte = [];
  let pegelSpitze = 0;
  let pegelZeit = 0;

  function leisteZeigen(an) {
    formular.classList.toggle("nimmt-auf", an);
    leiste.hidden = !an;
    eingabe.hidden = an;
    mikroKnopf.hidden = an;
    sendeKnopf.setAttribute("aria-label", an ? "Sprachnachricht senden" : "Senden");
  }

  function balkenZeichnen() {
    balken.forEach((b, i) => { b.style.transform = `scaleY(${Math.max(0.1, pegelWerte[pegelWerte.length - BALKEN + i] ?? 0)})`; });
  }

  function pegel(wert) {
    // Ein Balken je 70 ms mit dem lautesten Wert dazwischen: So wandert die Kurve etwa im Takt der Silben.
    pegelSpitze = Math.max(pegelSpitze, wert);
    const jetzt = performance.now();
    if (jetzt - pegelZeit < 70) return;
    pegelZeit = jetzt;
    pegelWerte.push(pegelSpitze);
    if (pegelWerte.length > BALKEN) pegelWerte.shift();
    pegelSpitze = 0;
    balkenZeichnen();
  }

  async function aufnahmeBeginnen() {
    if (aufnahme || entwurf) return;
    const hindernis = aufnahmeHindernis();
    if (hindernis) { toast(hindernis, "err", 6500); return; }
    mikroKnopf.disabled = true;
    try {
      aufnahme = await aufnahmeStarten({ beiPegel: pegel, beiEnde: (grund) => aufnahmeBeenden(grund) });
    } catch (e) {
      toast(e.message, "err", 6500);
      return;
    } finally {
      mikroKnopf.disabled = false;
    }
    pegelWerte = [];
    balkenZeichnen();
    leistenHinweis.hidden = true;
    leiste.classList.remove("fertig", "stumm");
    anhoerenKnopf.hidden = true;
    stoppKnopf.hidden = false;
    zeitAnzeige.textContent = "0:00";
    leisteZeigen(true);
    ansage.textContent = "Aufnahme läuft.";
    uhr = setInterval(() => { if (aufnahme) zeitAnzeige.textContent = minuten(aufnahme.sekunden()); }, 250);
    sendeKnopf.focus({ preventScroll: true });
  }

  async function aufnahmeBeenden(grund = "") {
    if (!aufnahme) return;
    const a = aufnahme;
    aufnahme = null;
    clearInterval(uhr);
    try {
      entwurf = await a.stoppen();
    } catch (e) {
      toast(e.message, "err");
      leisteZuruecksetzen();
      return;
    }
    zeitAnzeige.textContent = laenge(entwurf.sekunden);
    leiste.classList.add("fertig");
    stoppKnopf.hidden = true;
    anhoerenKnopf.hidden = false;
    ansage.textContent = "Aufnahme beendet. Anhören, senden oder verwerfen.";
    if (grund === "grenze") toast(`Nach ${MAX_SEKUNDEN / 60} Minuten ist Schluss. Hör sie an oder schick sie ab.`, "info", 6000);
    else if (grund === "mikrofon") toast("Das Mikrofon ist weggefallen. Was bis dahin aufgenommen ist, kannst du senden.", "info", 6000);
    else if (grund === "verlassen") toast("Aufnahme angehalten. Sie wartet im Chat aufs Senden.", "info", 5000);
  }

  function anhoeren() {
    if (!entwurf) return;
    if (wiedergabe && !wiedergabe.paused) { wiedergabe.pause(); return; }
    if (!wiedergabe) {
      wiedergabe = new Audio(URL.createObjectURL(entwurf.blob));
      wiedergabe.onplay = () => leeren(anhoerenKnopf, icon("pause"));
      wiedergabe.onpause = () => leeren(anhoerenKnopf, icon("play"));
    }
    wiedergabe.play().catch(() => toast("Abspielen geht in diesem Browser nicht.", "err"));
  }

  function leisteZuruecksetzen() {
    clearInterval(uhr);
    if (wiedergabe) {
      wiedergabe.pause();
      URL.revokeObjectURL(wiedergabe.src);
      wiedergabe = null;
    }
    aufnahme = null;
    entwurf = null;
    leeren(anhoerenKnopf, icon("play"));
    ansage.textContent = "";
    leisteZeigen(false);
  }

  function aufnahmeVerwerfen() {
    aufnahme?.verwerfen();
    leisteZuruecksetzen();
    eingabe.focus({ preventScroll: true });
  }

  async function aufnahmeSenden() {
    if (wartet) { toast("Alfred antwortet noch. Schick die Aufnahme gleich.", "info"); return; }
    if (aufnahme) await aufnahmeBeenden();
    const roh = entwurf;
    if (!roh) return;
    if (roh.sekunden < MIN_SEKUNDEN) {
      leisteZuruecksetzen();
      toast("Zu kurz, um etwas zu verstehen.", "info");
      return;
    }
    if (roh.still && !roh.trotzdem) {
      // Nicht verwerfen: Vielleicht irrt die Messung. Ein zweiter Tipp schickt sie trotzdem.
      roh.trotzdem = true;
      leiste.classList.add("stumm");
      leistenHinweis.textContent = "Klingt stumm. Mikrofon aus? Senden schickt sie trotzdem.";
      leistenHinweis.hidden = false;
      ansage.textContent = leistenHinweis.textContent;
      return;
    }
    leisteZuruecksetzen();
    const blase = sprachNachricht(null, roh);
    await zug(blase, async () => {
      const r = await sprachnachrichtSenden(await alsWav(roh.blob));
      blase.abschreiben(r.transkript);
      return r;
    }, (e) => {
      if (e.transkript) {
        // Abgehört, aber nicht beantwortet: Die Abschrift kommt ins Feld, damit nichts neu gesprochen werden muss.
        blase.abschreiben(e.transkript);
        eingabe.value = e.transkript;
        hoehe();
      } else {
        blase.abschreiben("Nicht abgehört", true);
      }
    });
    eingabe.focus({ preventScroll: true });
  }

  // --- Verlauf --------------------------------------------------------------------------------------------------------------
  async function laden() {
    if (wartet) return;
    let d;
    try { d = await api("chat"); } catch (e) { if (!strom.children.length) leeren(strom, h("div", { class: "empty" }, icon("alert-circle"), h("b", null, "Chat nicht erreichbar"), h("span", null, e.message))); return; }
    if (wartet) return;  // der Nutzer hat inzwischen gesendet: Die Anzeige gehört jetzt dem Senden
    serverAnzahl = d.verlauf.length;
    const neu = `${serverAnzahl}|${d.verlauf.at(-1)?.content ?? ""}`;
    if (neu === signatur) return;
    signatur = neu;
    if (!d.verlauf.length) { leeren(strom, leerZustand()); vorschlaegeFuellen(); return; }
    // Ja und Nein nur an der letzten offenen Rückfrage, und nur, wenn danach noch nichts gesagt wurde.
    const offen = d.verlauf.findLastIndex((m) => m.role === "assistant" && m.schritte?.some((st) => st.status === "bestaetigung"));
    const beantwortet = offen === -1 || d.verlauf.slice(offen + 1).some((m) => m.role === "user");
    leeren(strom, ...d.verlauf.map((m, i) => (m.role === "user"
      ? (m.gesprochen ? sprachNachricht(m.content) : duNachricht(m.content))
      : alfredNachricht(m.content, m.schritte || [], !beantwortet && i === offen))));
    nachUnten();
  }

  async function vorschlaegeFuellen() {
    let f = store.get("faehigkeiten");
    if (!f) { try { f = await faehigkeitenLaden(); } catch { return; } }
    const gruppen = f.gruppen.filter((g) => g.im_modus && g.bereit + g.nur_stimme > 0 && g.beispiele.length);
    const auswahl = [...gruppen].sort(() => Math.random() - 0.5).slice(0, 6);
    leeren(vorschlaege, ...auswahl.map((g) => h("button", { class: "chip suggest", type: "button", onclick: () => { eingabe.value = g.beispiele[0]; hoehe(); eingabe.focus(); } },
      icon(g.symbol), g.beispiele[0])));
  }

  function entwurfPruefen() {
    const e = store.get("chatEntwurf");
    if (!e) return;
    store.set("chatEntwurf", null);
    if (e.aufnehmen) { aufnahmeBeginnen(); return; }
    if (e.senden && !wartet) senden(e.text);
    else {
      eingabe.value = e.text;
      hoehe();
      eingabe.focus();
      if (e.senden) toast("Alfred antwortet noch. Dein Text steht im Feld.", "info", 4500);
    }
  }
  store.on("chatEntwurf", () => { if (wurzel.classList.contains("active")) entwurfPruefen(); });

  return {
    laden,
    zeigen() {
      entwurfPruefen();
      setTimeout(() => { if (matchMedia("(min-width: 821px)").matches && leiste.hidden) eingabe.focus({ preventScroll: true }); }, 60);
    },
    // Wer den Chat verlässt, lässt kein offenes Mikrofon zurück: Die Aufnahme hält an und wartet aufs Senden.
    verlassen() { if (aufnahme) aufnahmeBeenden("verlassen"); },
    takt: 15000,
  };
}
