import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ThemeProvider, useTheme } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import {
  Activity, FolderOpen, Gauge, MonitorSmartphone, Moon, Sun, TerminalSquare, DatabaseZap, SlidersHorizontal, Menu, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ConsoleProvider, useConsole } from "@/state";
import { sessionAction } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import Overview from "@/views/overview";
import ScreenView from "@/views/screen";
import ControlView from "@/views/control";
import DataView from "@/views/data";
import FilesView from "@/views/files";
import TerminalView from "@/views/terminal";
import CacheView from "@/views/cache";

const NAV = [
  { id: "overview", label: "Overview", icon: Gauge },
  { id: "screen", label: "Screen", icon: MonitorSmartphone },
  { id: "control", label: "Control", icon: SlidersHorizontal },
  { id: "data", label: "Data", icon: DatabaseZap },
  { id: "files", label: "Files", icon: FolderOpen },
  { id: "terminal", label: "Terminal", icon: TerminalSquare },
  { id: "cache", label: "Cache", icon: Activity },
] as const;
type ViewId = (typeof NAV)[number]["id"];

export function StatusDot({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-block size-2 rounded-full shrink-0",
        status === "online" && "bg-emerald-500 animate-pulse",
        status === "idle" && "bg-amber-500",
        status === "offline" && "bg-red-500/70",
      )}
    />
  );
}

function TokenGate() {
  const { gateOpen, submitToken } = useConsole();
  const [val, setVal] = useState("");
  return (
    <Dialog open={gateOpen}>
      <DialogContent showCloseButton={false} className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Access token</DialogTitle>
          <DialogDescription>
            This console is token-protected. Enter the value the server was started with (<code className="font-mono text-xs">--web-token</code>).
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => { e.preventDefault(); if (val.trim()) submitToken(val.trim()); }}>
          <Input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="token" autoFocus className="font-mono" />
          <DialogFooter>
            <Button type="submit" className="w-full mt-2">Unlock console</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Topbar() {
  const { snapshot, refreshState, sseLive } = useConsole();
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const online = snapshot?.sessions.filter((s) => s.status === "online").length ?? 0;
  const srv = snapshot?.server;

  const activate = async (cid: string) => {
    try { await sessionAction("activate", cid); await refreshState(); toast.success("session activated"); }
    catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  return (
    <header className="flex h-14 items-center gap-3 border-b bg-background/80 backdrop-blur px-4 sticky top-0 z-20">
      <div className="flex-1 flex items-center gap-2 overflow-x-auto no-scrollbar">
        {!snapshot?.sessions.length && (
          <span className="text-xs text-muted-foreground font-mono py-1.5">no sessions — waiting for a beacon…</span>
        )}
        {snapshot?.sessions.map((s) => (
          <button
            key={s.cid}
            onClick={() => activate(s.cid)}
            title={`${s.model} · last seen ${s.last_seen_age}s ago · ${s.seq} beacons`}
            className={cn(
              "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-mono whitespace-nowrap transition-colors cursor-pointer",
              s.cid === snapshot.active
                ? "border-primary/60 bg-primary/10 text-foreground"
                : "border-border text-muted-foreground hover:text-foreground hover:border-primary/40",
            )}
          >
            <StatusDot status={s.status} />
            {s.tag}
            <span className="text-[10px] text-muted-foreground/70">{s.model.slice(0, 14)}</span>
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-mono text-muted-foreground">
          <span className={cn("size-1.5 rounded-full", sseLive ? "bg-emerald-500" : "bg-red-500 animate-pulse")} />
          {sseLive ? "live" : "reconnecting"}
          {srv && <span className="text-border mx-0.5">|</span>}
          {srv && <span>{srv.tls ? "TLS" : "HTTP"} · {srv.enc ? "AES-GCM" : "plain"} · {online}/{snapshot!.sessions.length} up</span>}
        </span>
        <Button variant="ghost" size="icon" onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")} title="Toggle theme">
          {mounted && resolvedTheme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>
      </div>
    </header>
  );
}

function Shell() {
  const [view, setView] = useState<ViewId>("overview");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const reduceMotion = useReducedMotion();
  const current = NAV.find((item) => item.id === view)!;

  return (
    <div className="flex h-screen overflow-hidden">
      <button
        aria-label="Close navigation"
        className={cn("fixed inset-0 z-30 bg-black/40 md:hidden", !sidebarOpen && "hidden")}
        onClick={() => setSidebarOpen(false)}
      />
      <aside className={cn(
        "fixed inset-y-0 left-0 z-40 w-64 shrink-0 border-r bg-sidebar text-sidebar-foreground flex flex-col transition-transform md:static md:w-52 md:translate-x-0",
        sidebarOpen ? "translate-x-0" : "-translate-x-full",
      )}>
        <div className="flex items-center gap-2.5 px-4 h-14 border-b">
          <MonitorSmartphone className="size-5 text-primary shrink-0" />
          <span className="font-mono text-sm tracking-tight">
            Andro<b className="text-primary font-semibold">Remote</b>
          </span>
          <Button variant="ghost" size="icon" className="ml-auto md:hidden" onClick={() => setSidebarOpen(false)} aria-label="Close navigation"><X className="size-4" /></Button>
        </div>
        <nav className="flex flex-col gap-0.5 p-2 flex-1">
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => { setView(n.id); setSidebarOpen(false); }}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-mono transition-colors cursor-pointer",
                "justify-start",
                view === n.id
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <n.icon className="size-4 shrink-0" />
              <span>{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="p-3 text-[10px] font-mono text-muted-foreground/60 border-t hidden md:block">
          <FooterMeta />
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          <div className="mx-auto mb-5 flex max-w-[1400px] items-center gap-3">
            <Button variant="outline" size="icon" className="md:hidden shrink-0" onClick={() => setSidebarOpen(true)} aria-label="Open navigation"><Menu className="size-4" /></Button>
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-primary">Operations / {current.id}</p>
              <h1 className="text-xl font-semibold tracking-tight">{current.label}</h1>
            </div>
          </div>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={view}
              initial={reduceMotion ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0, y: -5 }}
              transition={{ duration: reduceMotion ? 0 : 0.18, ease: "easeOut" }}
            >
              {view === "overview" && <Overview />}
              {view === "screen" && <ScreenView />}
              {view === "control" && <ControlView />}
              {view === "data" && <DataView />}
              {view === "files" && <FilesView />}
              {view === "terminal" && <TerminalView />}
              {view === "cache" && <CacheView />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <TokenGate />
    </div>
  );
}

function FooterMeta() {
  const { snapshot } = useConsole();
  return <>{snapshot?.server.plugins.length ?? 0} plugins · :{snapshot?.server.web_port ?? "—"}</>;
}

export default function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} storageKey="artheme">
      <ConsoleProvider>
        <Shell />
        <Toaster position="bottom-right" richColors closeButton />
      </ConsoleProvider>
    </ThemeProvider>
  );
}
