// Operator preferences, persisted in this browser only.
// Module-level store so any component can read them without a provider.
import { useSyncExternalStore } from "react";

export const REFRESH_OPTIONS = [5, 10, 30, 60] as const;
export const PAGE_OPTIONS = [10, 15, 25, 50] as const;

export interface Prefs {
  /** seconds between /api/state refreshes */
  refreshSec: number;
  /** default rows per page in data tables */
  pageSize: number;
}

export const DEFAULT_PREFS: Prefs = { refreshSec: 10, pageSize: 15 };

const KEY = "arprefs";
const oneOf = (allowed: readonly number[], value: unknown, fallback: number) =>
  allowed.includes(Number(value)) ? Number(value) : fallback;

function load(): Prefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}") as Partial<Prefs>;
    return {
      refreshSec: oneOf(REFRESH_OPTIONS, raw.refreshSec, DEFAULT_PREFS.refreshSec),
      pageSize: oneOf(PAGE_OPTIONS, raw.pageSize, DEFAULT_PREFS.pageSize),
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

let prefs: Prefs = load();
const listeners = new Set<() => void>();

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export const usePrefs = () => useSyncExternalStore(subscribe, () => prefs);

export function updatePrefs(patch: Partial<Prefs>) {
  prefs = { ...prefs, ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* storage unavailable */ }
  for (const fn of listeners) fn();
}
