// Sprachnachrichten: im Browser aufnehmen, als WAV (16 kHz, mono, 16 Bit) an Alfred schicken.
// Abgehört wird auf dem Server (POST api/sprachnachricht, sprachnachricht.py); dort bleibt das Audio nicht liegen.
// Aufgenommen wird nur, solange die Leiste im Chat läuft, und gesendet nur auf Knopfdruck.
import { store } from "./core.js";

export const MAX_SEKUNDEN = 600;  // wie MAX_SEKUNDEN in sprachnachricht.py
export const MIN_SEKUNDEN = 0.6;  // darunter war es ein versehentlicher Tipp
const ZIELRATE = 16000;
const STILLE = 0.01;  // Spitzenpegel (RMS), unter dem eine Aufnahme als stumm gilt: Mikrofon aus oder falsches Gerät
const FORMATE = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4", "audio/webm"];

/** Warum hier nicht aufgenommen werden kann; leer, wenn es geht. */
export function aufnahmeHindernis() {
  if (!window.isSecureContext) return "Das Mikrofon gibt der Browser nur über HTTPS (Tailscale) oder auf diesem Rechner frei.";
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return "Dieser Browser kann nicht aufnehmen.";
  if (!(window.OfflineAudioContext || window.webkitOfflineAudioContext)) return "Dieser Browser kann Aufnahmen nicht umwandeln.";
  return "";
}

function mikrofonFehler(e) {
  switch (e?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Das Mikrofon ist gesperrt. Erlaube es für diese Seite in den Einstellungen des Browsers.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "Kein Mikrofon gefunden.";
    case "NotReadableError":
    case "AbortError":
      return "Das Mikrofon lässt sich gerade nicht öffnen.";
    default:
      return `Aufnahme nicht möglich: ${e?.message || e}`;
  }
}

/** "1:07" für 67 Sekunden. */
export const minuten = (sekunden) => `${Math.floor(sekunden / 60)}:${String(Math.floor(sekunden % 60)).padStart(2, "0")}`;

/**
 * Öffnet das Mikrofon und nimmt auf. beiPegel(0…1) kommt mit jedem Bild, beiEnde() nach MAX_SEKUNDEN oder wenn das
 * Mikrofon wegfällt. Liefert { sekunden(), stoppen(), verwerfen() }; stoppen() gibt { blob, sekunden, still }.
 */
export async function aufnahmeStarten({ beiPegel = () => {}, beiEnde = () => {} } = {}) {
  // Der Audio-Kontext für den Pegel entsteht noch im Klick, vor dem ersten await: Safari lässt ihn sonst angehalten.
  let ctx = null;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    ctx.resume?.().catch(() => {});
  } catch {
    ctx = null;
  }
  let strom;
  try {
    strom = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
  } catch (e) {
    ctx?.close?.().catch(() => {});
    throw new Error(mikrofonFehler(e));
  }
  const typ = FORMATE.find((t) => window.MediaRecorder.isTypeSupported?.(t)) || "";
  let rekorder;
  try {
    rekorder = new MediaRecorder(strom, typ ? { mimeType: typ } : undefined);
  } catch (e) {
    strom.getTracks().forEach((t) => t.stop());
    throw new Error(`Aufnahme nicht möglich: ${e.message}`);
  }
  const stuecke = [];
  rekorder.ondataavailable = (ev) => { if (ev.data?.size) stuecke.push(ev.data); };

  // Der Pegel ist nur für die Anzeige und die Prüfung auf Stille; scheitert er, läuft die Aufnahme trotzdem.
  let analyse = null;
  try {
    analyse = ctx.createAnalyser();
    analyse.fftSize = 1024;
    ctx.createMediaStreamSource(strom).connect(analyse);
  } catch {
    analyse = null;
  }
  const werte = analyse ? new Float32Array(analyse.fftSize) : null;
  let spitze = 0;
  let messungen = 0;  // nur mit laufendem Kontext zählt der Pegel; ein angehaltener liefert Nullen
  let rahmen = 0;
  const messen = () => {
    if (ctx.state === "running") {
      analyse.getFloatTimeDomainData(werte);
      let summe = 0;
      for (let i = 0; i < werte.length; i++) summe += werte[i] * werte[i];
      const rms = Math.sqrt(summe / werte.length);
      spitze = Math.max(spitze, rms);
      messungen += 1;
      beiPegel(Math.min(1, rms * 5));  // Sprache liegt grob zwischen 0,02 und 0,2
    }
    rahmen = requestAnimationFrame(messen);
  };
  if (analyse) rahmen = requestAnimationFrame(messen);

  const beginn = performance.now();
  const grenze = setTimeout(() => beiEnde("grenze"), MAX_SEKUNDEN * 1000);
  strom.getAudioTracks()[0]?.addEventListener("ended", () => beiEnde("mikrofon"));
  rekorder.start(1000);

  let zu = false;
  function aufraeumen() {
    if (zu) return;
    zu = true;
    cancelAnimationFrame(rahmen);
    clearTimeout(grenze);
    strom.getTracks().forEach((t) => t.stop());  // erst damit erlischt die Mikrofon-Anzeige des Browsers
    ctx?.close?.().catch(() => {});
  }
  const sekunden = () => Math.min(MAX_SEKUNDEN, (performance.now() - beginn) / 1000);
  let fertig = null;

  return {
    sekunden,
    stoppen() {
      if (fertig) return fertig;
      const dauer = sekunden();
      fertig = new Promise((ok, fehl) => {
        rekorder.onstop = () => {
          aufraeumen();
          // Stumm heißt es nur, wenn der Pegel wirklich gemessen hat (mindestens eine halbe Sekunde lang).
          const still = messungen >= 30 && spitze < STILLE;
          ok({ blob: new Blob(stuecke, { type: rekorder.mimeType || typ || "audio/webm" }), sekunden: dauer, still });
        };
        rekorder.onerror = (ev) => { aufraeumen(); fehl(new Error(ev.error?.message || "Die Aufnahme brach ab.")); };
        try {
          if (rekorder.state === "inactive") rekorder.onstop();
          else rekorder.stop();
        } catch (e) {
          aufraeumen();
          fehl(e);
        }
      });
      return fertig;
    },
    verwerfen() {
      rekorder.ondataavailable = null;
      rekorder.onstop = null;
      try { if (rekorder.state !== "inactive") rekorder.stop(); } catch { /* schon aus */ }
      stuecke.length = 0;
      aufraeumen();
    },
  };
}

// --- Umwandeln --------------------------------------------------------------------------------------------------

function mono(puffer) {
  if (puffer.numberOfChannels === 1) return puffer.getChannelData(0);
  const summe = new Float32Array(puffer.length);
  for (let k = 0; k < puffer.numberOfChannels; k++) {
    const daten = puffer.getChannelData(k);
    for (let i = 0; i < daten.length; i++) summe[i] += daten[i] / puffer.numberOfChannels;
  }
  return summe;
}

/** Herunterrechnen durch Mitteln über jedes Ausgabeintervall; für Sprache reicht das. */
export function umtasten(werte, von, nach) {
  if (von === nach) return werte;
  const schritt = von / nach;
  const aus = new Float32Array(Math.floor(werte.length / schritt));
  for (let i = 0; i < aus.length; i++) {
    const a = Math.floor(i * schritt);
    const b = Math.max(a + 1, Math.min(werte.length, Math.floor((i + 1) * schritt)));
    let s = 0;
    for (let j = a; j < b; j++) s += werte[j];
    aus[i] = s / (b - a);
  }
  return aus;
}

/** 16-Bit-PCM-WAV aus Abtastwerten zwischen -1 und 1. */
export function pcmWav(werte, rate) {
  const puffer = new ArrayBuffer(44 + werte.length * 2);
  const v = new DataView(puffer);
  const text = (pos, s) => { for (let i = 0; i < s.length; i++) v.setUint8(pos + i, s.charCodeAt(i)); };
  text(0, "RIFF");
  v.setUint32(4, 36 + werte.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);  // PCM
  v.setUint16(22, 1, true);  // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, "data");
  v.setUint32(40, werte.length * 2, true);
  for (let i = 0, pos = 44; i < werte.length; i++, pos += 2) {
    const s = Math.max(-1, Math.min(1, werte[i]));
    v.setInt16(pos, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([puffer], { type: "audio/wav" });
}

/** Die Aufnahme (WebM, Ogg oder MP4, je nach Browser) als WAV mit 16 kHz mono: das Format, das der Server abhört. */
export async function alsWav(blob) {
  const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  let ctx;
  try {
    ctx = new Offline(1, ZIELRATE, ZIELRATE);  // decodeAudioData rechnet gleich auf die Rate des Kontexts um
  } catch {
    ctx = new Offline(1, 44100, 44100);  // ältere Safari kennen keine 16 kHz; dann rechnet umtasten
  }
  const daten = await blob.arrayBuffer();
  let puffer;
  try {
    puffer = await new Promise((ok, fehl) => {
      const p = ctx.decodeAudioData(daten, ok, fehl);  // ältere Safari kennen nur die Rückruf-Form
      p?.then?.(ok, fehl);
    });
  } catch {
    throw new Error("Die Aufnahme ließ sich nicht umwandeln.");
  }
  return pcmWav(umtasten(mono(puffer), puffer.sampleRate, ZIELRATE), ZIELRATE);
}

// --- Senden ------------------------------------------------------------------------------------------------------

/** Schickt eine WAV-Aufnahme an Alfred. Hat er sie abgehört, aber nicht beantwortet, trägt der Fehler die Abschrift (.transkript). */
export async function sprachnachrichtSenden(wav) {
  let antwort;
  try {
    antwort = await fetch("api/sprachnachricht", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
  } catch {
    store.set("online", false);
    throw Object.assign(new Error("Alfred ist nicht erreichbar"), { netz: true });
  }
  store.set("online", true);
  if (!antwort.ok) {
    let text = antwort.statusText || `Fehler ${antwort.status}`;
    let transkript = "";
    try {
      const d = (await antwort.json()).detail;
      if (typeof d === "string") text = d;
      else if (d && typeof d === "object") {
        text = d.text || text;
        transkript = d.transkript || "";
      }
    } catch { /* keine JSON-Antwort */ }
    throw Object.assign(new Error(text), { transkript });
  }
  return antwort.json();
}
