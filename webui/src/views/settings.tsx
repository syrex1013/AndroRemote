import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useTheme } from "next-themes";
import { Copy, Plug, RefreshCw, Settings } from "lucide-react";
import { api, type Snapshot } from "@/lib/api";
import { fmtUptime } from "@/lib/format";
import { useConsole } from "@/state";
import { PAGE_OPTIONS, REFRESH_OPTIONS, updatePrefs, usePrefs } from "@/lib/settings";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

function Group({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1">
      <div className="flex items-baseline gap-3">
        <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{title}</h2>
        {hint && <p className="text-[11px] text-muted-foreground/70">{hint}</p>}
      </div>
      <div className="rounded-lg border divide-y divide-border/60">{children}</div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 px-4 py-2.5">
      <span className="w-48 shrink-0 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <div className="min-w-0 flex-1 font-mono text-xs break-words">{children}</div>
    </div>
  );
}

function CopyValue({ value }: { value: string }) {
  return (
    <span className="inline-flex items-center gap-2 min-w-0">
      <span className="truncate">{value}</span>
      <button
        className="shrink-0 text-muted-foreground hover:text-foreground cursor-pointer"
        title="Copy"
        aria-label={`Copy ${value}`}
        onClick={() => navigator.clipboard.writeText(value).then(() => toast.success("copied")).catch(() => toast.error("clipboard blocked"))}
      >
        <Copy className="size-3.5" />
      </button>
    </span>
  );
}

function placeholder(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return <span className="text-muted-foreground/50">not set</span>;
  return String(value);
}

function tunnelState(srv: Snapshot["server"]) {
  if (srv.tunnel_mode === "off") return { label: "off", variant: "outline" as const };
  if (!srv.tunnel_running) return { label: "stopped", variant: "outline" as const };
  return srv.tunnel_alive
    ? { label: "live", variant: "default" as const }
    : { label: "restarting", variant: "secondary" as const };
}

export default function SettingsView() {
  const { snapshot, stateError, refreshState } = useConsole();
  const { resolvedTheme, setTheme } = useTheme();
  const prefs = usePrefs();
  const [cacheCount, setCacheCount] = useState<number | null>(null);

  useEffect(() => {
    api<{ items: unknown[] }>("/api/cache")
      .then((r) => setCacheCount(r.items.length))
      .catch(() => setCacheCount(null));
  }, []);

  if (!snapshot) {
    return stateError ? (
      <div className="rounded-lg border border-dashed border-border/70 max-w-xl mx-auto py-12 px-6 text-center">
        <Settings className="size-7 mx-auto mb-3 opacity-40" />
        <p className="text-sm text-red-500/90 break-words">{stateError}</p>
        <Button size="sm" variant="outline" className="mt-4" onClick={() => refreshState()}>Retry</Button>
      </div>
    ) : (
      <div className="rounded-lg border border-dashed border-border/70 py-12 text-center text-muted-foreground">
        <Settings className="size-7 mx-auto mb-3 opacity-40" />
        <p className="text-sm">loading settings…</p>
      </div>
    );
  }

  const srv = snapshot.server;
  const tunnel = tunnelState(srv);

  return (
    <div className="max-w-[820px] space-y-6">
      <Group title="Connection">
        <Row label="web ui port">{placeholder(srv.web_port)}</Row>
        <Row label="listener port">{srv.port} <span className="text-muted-foreground/60">· {srv.tls ? "TLS" : "cleartext"}</span></Row>
        <Row label="payload crypto">
          {srv.enc ? "AES-256-GCM" : <span className="text-muted-foreground/70">off (no pre-shared key)</span>}
        </Row>
        <Row label="key fingerprint">{srv.key_fp ? <CopyValue value={srv.key_fp} /> : placeholder(null)}</Row>
        <Row label="server uptime">{fmtUptime(srv.uptime)}</Row>
      </Group>

      <Group title="Tunnel" hint="Cloudflare Tunnel exposing the C2 listener">
        <Row label="mode">{srv.tunnel_mode}</Row>
        <Row label="state">
          <span className="inline-flex items-center gap-2">
            <Badge variant={tunnel.variant} className="font-mono text-[10px]">{tunnel.label}</Badge>
            {tunnel.label === "restarting" && <span className="text-muted-foreground/70">cloudflared exited, supervisor retrying</span>}
            {tunnel.label === "off" && <span className="text-muted-foreground/70">start the server with --tunnel named or --tunnel quick</span>}
          </span>
        </Row>
        <Row label="public url">{srv.tunnel_url ? <CopyValue value={srv.tunnel_url} /> : placeholder(null)}</Row>
        <Row label="tunnel hostname">{placeholder(srv.tunnel_host)}</Row>
      </Group>

      <Group title="Result cache" hint="agent query results reused for 60s">
        <Row label="entries">{cacheCount ?? "unavailable"}</Row>
        <Row label="purge">
          <span className="text-muted-foreground/70">cleared from the Cache view</span>
        </Row>
      </Group>

      <Group title="Plugins">
        {srv.plugins.length === 0 ? (
          <Row label="installed"><span className="text-muted-foreground/70">none loaded</span></Row>
        ) : (
          srv.plugins.map((p) => (
            <Row key={p.name} label={p.name}>
              <span className="inline-flex items-center gap-2 min-w-0">
                <Badge variant={p.enabled ? "default" : "outline"} className="font-mono text-[10px]">{p.enabled ? "enabled" : "disabled"}</Badge>
                <span className="text-muted-foreground/70">v{p.version}</span>
              </span>
              <p className="mt-1 text-[11px] font-sans text-muted-foreground normal-case tracking-normal">{p.description}</p>
            </Row>
          ))
        )}
      </Group>

      <Group title="Preferences" hint="stored in this browser only">
        <Row label="theme">
          <Select value={resolvedTheme === "light" ? "light" : "dark"} onValueChange={setTheme}>
            <SelectTrigger size="sm" className="w-40 font-mono text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="dark">dark</SelectItem>
              <SelectItem value="light">light</SelectItem>
            </SelectContent>
          </Select>
        </Row>
        <Row label="snapshot refresh">
          <Select value={String(prefs.refreshSec)} onValueChange={(v) => updatePrefs({ refreshSec: Number(v) })}>
            <SelectTrigger size="sm" className="w-40 font-mono text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              {REFRESH_OPTIONS.map((s) => <SelectItem key={s} value={String(s)}>{s}s</SelectItem>)}
            </SelectContent>
          </Select>
        </Row>
        <Row label="rows per page">
          <Select value={String(prefs.pageSize)} onValueChange={(v) => updatePrefs({ pageSize: Number(v) })}>
            <SelectTrigger size="sm" className="w-40 font-mono text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              {PAGE_OPTIONS.map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}
            </SelectContent>
          </Select>
        </Row>
      </Group>

      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => refreshState()}>
          <RefreshCw className="size-3.5 mr-1" /> Refresh state
        </Button>
        <span className="inline-flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground/70">
          <Plug className="size-3.5" /> tunnel and listener settings come from the server command line
        </span>
      </div>
    </div>
  );
}
