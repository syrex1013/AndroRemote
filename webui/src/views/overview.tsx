import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { MoreHorizontal, Pencil, Trash2, Crosshair, Radio, Hourglass, ShieldCheck, Server, Globe, Boxes } from "lucide-react";
import { api, sessionAction, type SessionInfo } from "@/lib/api";
import { fmtAge, fmtUptime } from "@/lib/format";
import { useConsole } from "@/state";
import { StatusDot } from "@/App";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

function StatCard({ icon: Icon, label, value, sub }: { icon: React.ElementType; label: string; value: React.ReactNode; sub?: string }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 px-4 py-3.5">
        <div className="rounded-md bg-primary/10 p-2 text-primary"><Icon className="size-4" /></div>
        <div className="min-w-0">
          <div className="text-lg font-semibold font-mono leading-tight">{value}</div>
          <div className="text-[11px] text-muted-foreground font-mono uppercase tracking-wider">{sub ?? label}</div>
        </div>
      </CardContent>
    </Card>
  );
}

function statusBadge(s: SessionInfo) {
  const map = { online: "default", idle: "secondary", offline: "destructive" } as const;
  const label = { online: "online", idle: "idle", offline: "offline" } as const;
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusDot status={s.status} />
      <Badge variant={map[s.status]} className="font-mono text-[10px]">{label[s.status]}</Badge>
    </span>
  );
}

export default function Overview() {
  const { snapshot, refreshState, events, liveUptime, tick } = useConsole();
  const [renameFor, setRenameFor] = useState<SessionInfo | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const [forgetFor, setForgetFor] = useState<SessionInfo | null>(null);
  const [cacheCount, setCacheCount] = useState<number | null>(null);

  useEffect(() => {
    api<{ items: unknown[] }>("/api/cache").then((r) => setCacheCount(r.items.length)).catch(() => {});
  }, [tick]);

  const sessions = snapshot?.sessions ?? [];
  const active = snapshot?.active ?? null;
  const online = sessions.filter((s) => s.status === "online").length;
  const offline = sessions.filter((s) => s.status === "offline").length;
  const totalBeacons = sessions.reduce((a, s) => a + s.seq, 0);
  const pending = sessions.reduce((a, s) => a + s.pending, 0);
  const activity = useMemo(() => {
    const anchor = events[0]?.ts ? Math.floor(events[0].ts * 1000 / 60000) * 60000 : 0;
    const buckets = Array.from({ length: 12 }, (_, i) => ({
      time: new Date(anchor - (11 - i) * 60000).toTimeString().slice(0, 5),
      events: 0,
    }));
    for (const event of events) {
      const index = Math.floor((event.ts * 1000 - (anchor - 11 * 60000)) / 60000);
      if (index >= 0 && index < buckets.length) buckets[index].events += 1;
    }
    return buckets;
  }, [events]);
  const health = useMemo(() => ([
    { state: "online", count: online },
    { state: "idle", count: sessions.filter((s) => s.status === "idle").length },
    { state: "offline", count: offline },
  ]), [offline, online, sessions]);

  if (!snapshot) return <Empty text="loading state…" />;
  const { server } = snapshot;

  const doRename = async () => {
    if (!renameFor || !renameVal.trim()) return;
    try {
      await sessionAction("rename", renameFor.cid, renameVal.trim());
      toast.success(`renamed → ${renameVal.trim()}`);
      setRenameFor(null);
      refreshState();
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };
  const doForget = async () => {
    if (!forgetFor) return;
    try {
      await sessionAction("forget", forgetFor.cid);
      toast.success("session forgotten");
      setForgetFor(null);
      refreshState();
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };
  const doUse = async (cid: string) => {
    try { await sessionAction("activate", cid); await refreshState(); toast.success("session activated"); }
    catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  return (
    <div className="space-y-4 max-w-[1400px]">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatCard icon={Radio} label="clients" value={sessions.length} sub={`${online} online · ${offline} off`} />
        <StatCard icon={Crosshair} label="active" value={active ? sessions.find((s) => s.cid === active)?.tag ?? "?" : "—"} sub="active session" />
        <StatCard icon={Server} label="beacons" value={totalBeacons.toLocaleString()} sub="commands run" />
        <StatCard icon={Hourglass} label="pending" value={pending} sub="queued commands" />
        <StatCard icon={ShieldCheck} label="crypto" value={server.enc ? "AES-256" : "plain"} sub={server.enc ? `key ${server.key_fp}…` : "encryption off"} />
        <StatCard icon={Globe} label="uptime" value={<span key={tick}>{fmtUptime(liveUptime())}</span>} sub={`tunnel: ${server.tunnel_mode}`} />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-0"><CardTitle className="font-mono text-xs uppercase tracking-widest text-muted-foreground">Live activity</CardTitle></CardHeader>
          <CardContent className="h-56 pt-4">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={activity} margin={{ top: 5, right: 8, left: -22, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="time" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} interval={2} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
                <Line type="monotone" dataKey="events" name="events" stroke="#22c7d8" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-0"><CardTitle className="font-mono text-xs uppercase tracking-widest text-muted-foreground">Client health</CardTitle></CardHeader>
          <CardContent className="h-56 pt-4">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={health} layout="vertical" margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                <XAxis type="number" allowDecimals={false} hide />
                <YAxis type="category" dataKey="state" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={54} />
                <Tooltip cursor={{ fill: "var(--muted)" }} contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="count" name="clients" fill="#8b7cf6" radius={[0, 4, 4, 0]} barSize={18} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="font-mono text-xs uppercase tracking-widest text-muted-foreground">Clients</CardTitle>
          </CardHeader>
          <CardContent>
            {sessions.length === 0 ? (
              <Empty text="No agents yet." hint="install the agent — sessions appear on first beacon" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10"></TableHead>
                    <TableHead>Session</TableHead>
                    <TableHead>Device</TableHead>
                    <TableHead className="text-center">State</TableHead>
                    <TableHead className="text-right">Beacons</TableHead>
                    <TableHead className="text-right">Queue</TableHead>
                    <TableHead className="text-center">Enc</TableHead>
                    <TableHead className="text-right">Last seen</TableHead>
                    <TableHead className="w-10"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sessions.map((s) => (
                    <TableRow key={s.cid} className={s.cid === active ? "bg-primary/[0.04]" : ""}>
                      <TableCell>{s.cid === active && <Badge className="font-mono text-[10px]">active</Badge>}</TableCell>
                      <TableCell className="font-mono text-primary">{s.tag}</TableCell>
                      <TableCell className="text-sm">{s.model}</TableCell>
                      <TableCell className="text-center">{statusBadge(s)}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{s.seq}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{s.pending || <span className="text-muted-foreground/50">—</span>}</TableCell>
                      <TableCell className="text-center">
                        {s.enc ? <ShieldCheck className="size-3.5 inline text-emerald-500" /> : <span className="text-[10px] font-mono text-muted-foreground/50">plain</span>}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-muted-foreground">{fmtAge(s.last_seen_age)} ago</TableCell>
                      <TableCell>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-7"><MoreHorizontal className="size-4" /></Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem disabled={s.cid === active} onClick={() => doUse(s.cid)}>
                              <Crosshair /> Use session
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => { setRenameFor(s); setRenameVal(s.tag); }}>
                              <Pencil /> Rename…
                            </DropdownMenuItem>
                            <DropdownMenuItem variant="destructive" onClick={() => setForgetFor(s)}>
                              <Trash2 /> Forget
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="font-mono text-xs uppercase tracking-widest text-muted-foreground">Activity</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <ScrollArea className="h-[420px] px-4 pb-4">
              {events.length === 0 ? (
                <p className="text-xs text-muted-foreground font-mono py-2">quiet for now — operator events land here live.</p>
              ) : (
                <div className="font-mono text-xs space-y-1">
                  {events.map((e, i) => (
                    <div key={i} className="flex gap-2 py-1 border-b border-dashed last:border-0">
                      <span className="text-muted-foreground/60 shrink-0">{new Date(e.ts * 1000).toTimeString().slice(0, 8)}</span>
                      <span className={cnSym(e.sym)}>{e.sym}</span>
                      <span className="break-all">{e.msg}</span>
                    </div>
                  ))}
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>
      </div>

      <div className="grid md:grid-cols-4 gap-3">
        <Card><CardContent className="px-4 py-3 text-xs font-mono space-y-1">
          <div className="text-muted-foreground uppercase tracking-wider text-[10px]">Listener</div>
          <div>:{server.port} · {server.tls ? "TLS" : "cleartext"}</div>
        </CardContent></Card>
        <Card><CardContent className="px-4 py-3 text-xs font-mono space-y-1">
          <div className="text-muted-foreground uppercase tracking-wider text-[10px]">Tunnel</div>
          <div className="truncate">{server.tunnel_url ?? server.tunnel_mode}</div>
        </CardContent></Card>
        <Card><CardContent className="px-4 py-3 text-xs font-mono space-y-1">
          <div className="text-muted-foreground uppercase tracking-wider text-[10px]">Plugins</div>
          <div className="truncate">{server.plugins.map((p) => p.name).join(", ") || "none"}</div>
        </CardContent></Card>
        <Card><CardContent className="px-4 py-3 text-xs font-mono space-y-1">
          <div className="text-muted-foreground uppercase tracking-wider text-[10px]">Cache</div>
          <div>{cacheCount ?? "—"} entries · TTL 60s</div>
        </CardContent></Card>
      </div>

      {/* rename dialog */}
      <Dialog open={!!renameFor} onOpenChange={(o) => !o && setRenameFor(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Rename session</DialogTitle>
          </DialogHeader>
          <Input value={renameVal} onChange={(e) => setRenameVal(e.target.value)} className="font-mono" autoFocus onKeyDown={(e) => e.key === "Enter" && doRename()} />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenameFor(null)}>Cancel</Button>
            <Button onClick={doRename}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* forget confirm */}
      <AlertDialog open={!!forgetFor} onOpenChange={(o) => !o && setForgetFor(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Forget {forgetFor?.tag}?</AlertDialogTitle>
            <AlertDialogDescription>
              The session is removed from tracking. If the agent beacons again it re-registers as new.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doForget}>Forget</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

const cnSym = (sym: string) =>
  sym === "!" ? "text-amber-500" : sym === "+" ? "text-emerald-500" : sym === "✗" ? "text-red-500" : "text-muted-foreground";

function Empty({ text, hint }: { text: string; hint?: string }) {
  return (
    <div className="text-center py-14 text-muted-foreground">
      <Boxes className="size-8 mx-auto mb-3 opacity-40" />
      <p className="text-sm">{text}</p>
      {hint && <p className="text-xs font-mono mt-1 opacity-70">{hint}</p>}
    </div>
  );
}
