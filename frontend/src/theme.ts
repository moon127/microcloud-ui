import { useSyncExternalStore } from "react";

/** "system" follows the OS (prefers-color-scheme); the others override it (D60). */
export type Theme = "system" | "light" | "dark";
export const THEMES: Theme[] = ["system", "light", "dark"];

const KEY = "mcui.theme";
const EVENT = "mcui-theme";
const DARK = "(prefers-color-scheme: dark)";

export function storedTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system"; // storage blocked: follow the OS
  }
}

/** Mark the page (``<html data-theme>``) and remember the choice in this browser. */
export function applyTheme(theme: Theme, remember = true): void {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  if (remember) {
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      // storage blocked: the choice lasts until reload
    }
  }
  window.dispatchEvent(new Event(EVENT));
}

function osDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK).matches;
}

/** The theme actually shown: the user's choice, else the OS's. */
export function resolvedTheme(): "light" | "dark" {
  const t = document.documentElement.dataset.theme;
  if (t === "light" || t === "dark") return t;
  return osDark() ? "dark" : "light";
}

function subscribe(onChange: () => void): () => void {
  const mq = typeof window.matchMedia === "function" ? window.matchMedia(DARK) : null;
  mq?.addEventListener("change", onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    mq?.removeEventListener("change", onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

/** Re-renders when the shown theme changes (charts read their colours from CSS). */
export const useResolvedTheme = () => useSyncExternalStore(subscribe, resolvedTheme);

/** The user's choice, re-rendering when it changes. */
export const useThemeChoice = () => useSyncExternalStore(subscribe, () => document.documentElement.dataset.theme ?? "system") as Theme;
