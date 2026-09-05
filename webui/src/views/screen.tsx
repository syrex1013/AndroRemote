import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  ChevronLeft, Circle, Home, Lock, Power, RefreshCw, MoonStar, Square, Play, Volume2, VolumeX, Bell,
} from "lucide-react";
import { postOp } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { useConsole } from "@/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const NO_SESSION = (
  <div className="text-center py-14 text-muted-foreground">
    <p className="text-sm">No session selected.</p>
    <p className="text-xs font-mono mt-1 opacity-70">pick an agent from the strip above</p>
  </div>
);

export default function ScreenView() {
  const { snapshot, activeSession } = useConsole();
  const hasActive = !!snapshot?.active;

  const [img, setImg] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [live, setLive] = useState(false);
  const [interval, setIntervalMs] = useState(5000);
  const [dims, setDims] = useState({ w: 1080, h: 2400 });
  const [text, setText] = useState("");
  const busy = useRef(false);
  const liveRef = useRef(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const downPt = useRef<{ x: number; y: number } | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  const capture = useCallback(async () => {
    const cid = snapshot?.active;
    if (!cid || busy.current) return;
    busy.current = true;
    setStatus("capturing…");
    try {
      const r = await postOp<{ png?: string; bytes?: number; error?: string }>("screen");
      if (r.error) { setStatus(r.error); }
      else {
        setImg("data:image/png;base64," + r.png);
        setStatus(`${fmtBytes(r.bytes!)} · ${new Date().toTimeString().slice(0, 8)}`);
      }
    } catch (e) { setStatus(String(e instanceof Error ? e.message : e)); }
    busy.current = false;
  }, [snapshot?.active]);

  useEffect(() => { if (hasActive) capture(); }, [hasActive, capture]);

  useEffect(() => {
    liveRef.current = live;
    if (live) {
      timer.current = setInterval(capture, interval);
      capture();
    } else if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [live, interval, capture]);

  const toDev = (e: React.PointerEvent) => {
    const r = imgRef.current!.getBoundingClientRect();
    return {
      x: Math.round(((e.clientX - r.left) / r.width) * dims.w),
      y: Math.round(((e.clientY - r.top) / r.height) * dims.h),
    };
  };

  const tapOrSwipe = async (e: React.PointerEvent) => {
    if (!downPt.current) return;
    const p = toDev(e);
    const d = downPt.current;
    downPt.current = null;
    try {
      if (Math.hypot(p.x - d.x, p.y - d.y) < 12) {
        await postOp("tap", { x: p.x, y: p.y });
        toast(`tap ${p.x},${p.y}`);
      } else {
        await postOp("swipe", { x1: d.x, y1: d.y, x2: p.x, y2: p.y, ms: 300 });
        toast(`swipe ${d.x},${d.y} → ${p.x},${p.y}`);
      }
      if (liveRef.current) setTimeout(capture, 600);
    } catch (err) { toast.error(String(err instanceof Error ? err.message : err)); }
  };

  const gaction = async (action: string) => {
    try {
      await postOp("gaction", { action });
      toast.success(action);
      if (liveRef.current) setTimeout(capture, 600);
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };
  const simple = async (op: string, args: Record<string, unknown> = {}, label = op) => {
    try { await postOp(op, args); toast.success(label); } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  if (!hasActive) return NO_SESSION;

  const s = activeSession()!;

  return (
    <div className="grid xl:grid-cols-[minmax(0,1fr)_300px] gap-4 max-w-[1400px]">
      <div className="space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Button size="sm" onClick={capture}><RefreshCw className="size-3.5" /> Capture</Button>
          <div className="flex items-center gap-2 rounded-md border px-3 h-8">
            {live ? <Square className="size-3 text-primary" /> : <Play className="size-3 text-primary" />}
            <span className="text-xs font-mono">live</span>
            <Switch checked={live} onCheckedChange={setLive} />
          </div>
          <Select value={String(interval)} onValueChange={(v) => setIntervalMs(Number(v))}>
            <SelectTrigger size="sm" className="w-20 font-mono text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="2000">2s</SelectItem>
              <SelectItem value="5000">5s</SelectItem>
              <SelectItem value="10000">10s</SelectItem>
            </SelectContent>
          </Select>
          <span className="flex-1" />
          <span className="text-[11px] font-mono text-muted-foreground">{status}</span>
        </div>

        <div className="relative rounded-2xl border bg-[#070b11] shadow-lg min-h-[420px] flex items-center justify-center p-4 overflow-hidden">
          {live && (
            <span className="absolute top-2.5 left-4 inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-emerald-500">
              <span className="size-1.5 rounded-full bg-emerald-500 animate-pulse" /> live
            </span>
          )}
          {img ? (
            <img
              ref={imgRef}
              src={img}
              alt="device screen"
              className="max-w-full max-h-[calc(100vh-260px)] rounded-lg cursor-crosshair touch-none select-none"
              onPointerDown={(e) => { downPt.current = toDev(e); e.preventDefault(); }}
              onPointerUp={tapOrSwipe}
            />
          ) : (
            <div className="text-center py-16 text-muted-foreground/50 font-mono text-xs leading-6">
              <MonitorPhone /> No capture yet.<br />
              the agent needs an active MediaProjection session —<br />launch the app once on the device, then capture.
            </div>
          )}
        </div>
      </div>

      <div className="space-y-3">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">Navigation</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <div className="grid grid-cols-3 gap-2">
              <Button variant="outline" size="sm" onClick={() => gaction("back")} title="Back"><ChevronLeft className="size-4" /></Button>
              <Button variant="outline" size="sm" onClick={() => gaction("home")} title="Home"><Home className="size-4" /></Button>
              <Button variant="outline" size="sm" onClick={() => gaction("recents")} title="Recents"><Square className="size-3.5" /></Button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Button variant="outline" size="sm" onClick={() => gaction("power")}><Power className="size-3.5 mr-1" />Pwr</Button>
              <Button variant="outline" size="sm" onClick={() => gaction("lock")}><Lock className="size-3.5 mr-1" />Lock</Button>
              <Button variant="outline" size="sm" onClick={() => gaction("notifications")}><Bell className="size-3.5 mr-1" />Notif</Button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button variant="outline" size="sm" onClick={() => simple("wake", { secs: 60 }, "screen woken")}><MoonStar className="size-3.5 mr-1" />Wake</Button>
              <Button variant="outline" size="sm" onClick={() => simple("sleep", {}, "screen locked")}>☾ Sleep</Button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button variant="outline" size="sm" onClick={() => simple("vol", { level: "up" }, "vol +")}><Volume2 className="size-3.5 mr-1" />Up</Button>
              <Button variant="outline" size="sm" onClick={() => simple("vol", { level: "down" }, "vol −")}><VolumeX className="size-3.5 mr-1" />Down</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><CardTitle className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">Text input</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="type into focused field…" className="font-mono text-xs" />
            <Button size="sm" className="w-full" disabled={!text}
              onClick={async () => { await simple("settext", { text }, "text sent"); setText(""); }}>
              Send text
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><CardTitle className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">Touch mapping</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-2">
              <Input type="number" value={dims.w} onChange={(e) => setDims((d) => ({ ...d, w: Number(e.target.value) || 1080 }))} className="h-8 font-mono text-xs" />
              <span className="text-muted-foreground text-xs">×</span>
              <Input type="number" value={dims.h} onChange={(e) => setDims((d) => ({ ...d, h: Number(e.target.value) || 2400 }))} className="h-8 font-mono text-xs" />
            </div>
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              Click the screen to tap · drag to swipe. Set the device's real resolution for accurate mapping.
            </p>
            <Badge variant="secondary" className="font-mono text-[10px]">{s.model}</Badge>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function MonitorPhone() {
  return <Circle className="size-6 mx-auto mb-2 opacity-40" />;
}
