/* Global console state: snapshot polling, SSE live feed, terminal lines, token gate. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, getToken, setToken, type LogEvent, type ResultEvent, type Snapshot } from "@/lib/api";

export interface TermLine {
  ts: number;
  kind: "cmd" | "sys" | "ok" | "err";
  text: string;
  cmd?: string;
  tag?: string;
  truncated?: boolean;
}

interface ConsoleCtx {
  snapshot: Snapshot | null;
  refreshState: () => Promise<void>;
  sseLive: boolean;
  events: LogEvent[];
  termLines: TermLine[];
  pushTerm: (l: Omit<TermLine, "ts">) => void;
  gateOpen: boolean;
  submitToken: (t: string) => void;
  activeSession: () => Snapshot["sessions"][number] | null;
  tick: number; // increments on any live event — views can key off it
  liveUptime: () => number;
}

const Ctx = createContext<ConsoleCtx | null>(null);

export const useConsole = () => {
  const c = useContext(Ctx);
  if (!c) throw new Error("useConsole outside provider");
  return c;
};

export function ConsoleProvider({ children }: { children: React.ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [sseLive, setSseLive] = useState(false);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [termLines, setTermLines] = useState<TermLine[]>([]);
  const [gateOpen, setGateOpen] = useState(false);
  const [tick, setTick] = useState(0);
  const recent = useRef(new Map<string, number>());
  const snapshotAt = useRef(Date.now() / 1000);

  const refreshState = useCallback(async () => {
    try {
      const snap = await api<Snapshot>("/api/state");
      snapshotAt.current = Date.now() / 1000;
      setSnapshot(snap);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setGateOpen(true);
    }
  }, []);

  const liveUptime = useCallback((): number => {
    if (!snapshot) return 0;
    return snapshot.server.uptime + Math.max(0, Date.now() / 1000 - snapshotAt.current);
  }, [snapshot]);

  const pushTerm = useCallback((l: Omit<TermLine, "ts">) => {
    setTermLines((prev) => [...prev.slice(-499), { ts: Date.now() / 1000, ...l }]);
  }, []);

  const markResult = useCallback((key: string) => {
    const m = recent.current;
    if (m.has(key)) return true;
    m.set(key, Date.now());
    if (m.size > 200) for (const [k, t] of m) if (Date.now() - t > 8000) m.delete(k);
    return false;
  }, []);

  const submitToken = useCallback((t: string) => {
    setToken(t);
    setGateOpen(false);
    window.location.reload();
  }, []);

  // initial load
  useEffect(() => { refreshState(); }, [refreshState]);

  // periodic state refresh
  useEffect(() => {
    const iv = setInterval(() => {
      if (document.visibilityState === "visible") refreshState();
    }, 10000);
    return () => clearInterval(iv);
  }, [refreshState]);

  // SSE live feed
  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const connect = () => {
      const url = "/api/events" + (getToken() ? "?token=" + encodeURIComponent(getToken()) : "");
      es = new EventSource(url);
      es.addEventListener("hello", () => setSseLive(true));
      es.onerror = () => { setSseLive(false); es?.close(); retry = setTimeout(connect, 2500); };
      es.addEventListener("log", (e) => {
        const evt = JSON.parse((e as MessageEvent).data) as LogEvent;
        setEvents((prev) => [evt, ...prev].slice(0, 250));
        setTick((t) => t + 1);
      });
      es.addEventListener("result", (e) => {
        const evt = JSON.parse((e as MessageEvent).data) as ResultEvent;
        const key = `${evt.cid}|${evt.cmd}|${evt.result}`;
        if (!markResult(key)) {
          setTermLines((prev) => [...prev.slice(-499), {
            ts: evt.ts, kind: evt.ok ? "ok" : "err", cmd: evt.cmd, tag: evt.tag, text: evt.result, truncated: evt.truncated,
          }]);
        }
        setTick((t) => t + 1);
      });
      es.addEventListener("session", () => refreshState());
    };
    connect();
    return () => { es?.close(); if (retry) clearTimeout(retry); };
  }, [markResult, refreshState]);

  const activeSession = useMemo(() => () => {
    if (!snapshot) return null;
    return snapshot.sessions.find((s) => s.cid === snapshot.active) ?? null;
  }, [snapshot]);

  const value = useMemo<Omit<ConsoleCtx, "liveUptime">>(() => ({
    snapshot, refreshState, sseLive, events, termLines, pushTerm, gateOpen, submitToken, activeSession, tick,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [snapshot, sseLive, events, termLines, gateOpen, tick]);

  // uptime ticker keeps cards live between snapshots
  useEffect(() => {
    const iv = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(iv);
  }, []);

  return (
    <Ctx.Provider value={{ ...value, liveUptime }}>
      {children}
    </Ctx.Provider>
  );
}
