// Hell, Dunkel oder dem System folgen. Standard ist Dunkel; die Wahl merkt sich der Browser.
import { $, store } from "./core.js";

export const DESIGNS = [
  ["dark", "Dunkel", "moon"],
  ["light", "Hell", "sun"],
  ["auto", "System", "monitor"],
];

export function designLesen() {
  try { return localStorage.getItem("design") || "dark"; } catch { return "dark"; }
}

export function designAnwenden(wahl = designLesen()) {
  const dunkel = wahl === "dark" || (wahl === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dunkel ? "dark" : "light";
  const meta = $("#meta-theme");
  if (meta) meta.content = dunkel ? "#07080d" : "#f2f3f8";
}

export function designSetzen(wahl) {
  try { localStorage.setItem("design", wahl); } catch { /* ohne Speicher gilt es bis zum Neuladen */ }
  designAnwenden(wahl);
  store.set("design", wahl);
}

export function designWeiter() {
  const i = DESIGNS.findIndex(([id]) => id === designLesen());
  designSetzen(DESIGNS[(i + 1) % DESIGNS.length][0]);
}

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (designLesen() === "auto") designAnwenden("auto");
});
