import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import { Download, ListTree, Loader2, Trash2, Upload } from "lucide-react";
import { api, getToken, postOp } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { useConsole } from "@/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

interface FileRow { name: string; dir: boolean; size?: number }

const NO_SESSION = (
  <div className="text-center py-14 text-muted-foreground">
    <p className="text-sm">No session selected.</p>
    <p className="text-xs font-mono mt-1 opacity-70">pick an agent from the strip above</p>
  </div>
);

export default function FilesView() {
  const { snapshot, activeSession } = useConsole();
  const s = activeSession();
  const cid = snapshot?.active ?? null;
  const [path, setPath] = useState("/sdcard");
  const [rows, setRows] = useState<FileRow[] | null>(null);
  const [drives, setDrives] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [delTarget, setDelTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadDrives = async () => {
    try {
      const r = await postOp<{ rows?: { path: string }[] }>("drives");
      setDrives((r.rows ?? []).map((x) => x.path));
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
  };

  const list = useCallback(async (p: string) => {
    if (!cid) return;
    setLoading(true);
    setError(null);
    try {
      const r = await postOp<{ rows?: FileRow[]; error?: string }>("ls", { path: p });
      if (r.error) { setError(r.error); return; }
      setRows(r.rows ?? []);
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setLoading(false); }
  }, [cid]);

  useEffect(() => { if (cid) list(path); }, [cid, path, list]);

  if (!cid) return NO_SESSION;

  const clean = path.replace(/\/$/, "");
  const enter = (name: string) => setPath(clean + "/" + name);
  const crumbs = path.split("/").filter(Boolean);

  const download = async (rpath: string) => {
    setBusy(rpath);
    setProgress(0);
    try {
      const q = new URLSearchParams({ cid: cid!, path: rpath, name: rpath.split("/").pop() || "file.bin" });
      const tok = getToken();
      if (tok) q.set("token", tok);
      const res = await fetch("/api/download?" + q);
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j.error || "download failed"); }
      const total = Number(res.headers.get("Content-Length") || 0);
      const reader = res.body?.getReader();
      if (reader && total) {
        const chunks: ArrayBuffer[] = [];
        let received = 0;
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (value) { chunks.push(value.buffer); received += value.length; setProgress(Math.round((received / total) * 100)); }
        }
        const blob = new Blob(chunks);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = rpath.split("/").pop() || "file.bin";
        a.click();
        URL.revokeObjectURL(a.href);
        toast.success("saved " + a.download);
      } else {
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = rpath.split("/").pop() || "file.bin";
        a.click();
        URL.revokeObjectURL(a.href);
        toast.success("saved " + a.download);
      }
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(null); setProgress(0); }
  };

  const upload = async (f: File) => {
    setBusy(f.name);
    setProgress(0);
    try {
      const buf = new Uint8Array(await f.arrayBuffer());
      let bin = "";
      const slice = 8192;
      for (let i = 0; i < buf.length; i += slice) bin += String.fromCharCode(...buf.subarray(i, i + slice));
      const b64 = btoa(bin);
      const r = await api<{ ok?: boolean; bytes?: number; error?: string }>("/api/upload", {
        method: "POST",
        body: JSON.stringify({ cid, path: `${clean}/${f.name}`, data_b64: b64 }),
      });
      if (r.error) throw new Error(r.error);
      toast.success(`uploaded ${fmtBytes(r.bytes || f.size)} → ${clean}/${f.name}`);
      list(path);
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(null); setProgress(0); }
  };

  const confirmDelete = async () => {
    if (!delTarget) return;
    const rpath = delTarget;
    setDelTarget(null);
    setBusy(rpath);
    try {
      const r = await api<{ ok?: boolean; error?: string }>("/api/delete", {
        method: "POST",
        body: JSON.stringify({ cid, path: rpath }),
      });
      if (r.error) throw new Error(r.error);
      toast.success(`deleted ${rpath.split("/").pop()}`);
      list(path);
    } catch (e) { toast.error(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(null); }
  };

  return (
    <div className="max-w-[1100px] space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Input value={path} onChange={(e) => setPath(e.target.value)} className="font-mono text-xs w-72 h-8" />
        <Button size="sm" variant="secondary" onClick={() => list(path)} disabled={loading}>
          {loading ? <Loader2 className="size-3.5 mr-1 animate-spin" /> : <ListTree className="size-3.5 mr-1" />}
          List
        </Button>
        <Button size="sm" variant="outline" onClick={loadDrives}>Available drives</Button>
        <span className="flex-1" />
        <label className="cursor-pointer">
          <input type="file" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          <span className="inline-flex items-center gap-1.5 rounded-md border px-3 h-8 text-xs font-mono hover:bg-accent cursor-pointer">
            <Upload className="size-3.5" /> Upload here
          </span>
        </label>
      </div>

      {progress > 0 && (
        <div className="space-y-1">
          <Progress value={progress} className="h-1.5" />
          <p className="text-[10px] font-mono text-muted-foreground">{busy} · {progress}%</p>
        </div>
      )}

      {drives.length > 0 && <div className="flex gap-2 flex-wrap">{drives.map((d) => <Button key={d} size="sm" variant="ghost" className="font-mono text-xs" onClick={() => setPath(d)}>{d}</Button>)}</div>}

      <div className="text-xs font-mono text-muted-foreground break-all">
        <button className="hover:text-foreground cursor-pointer" onClick={() => setPath("/")}>/</button>
        {crumbs.map((c, i) => (
          <span key={i}>
            <button className="text-primary hover:underline cursor-pointer" onClick={() => setPath("/" + crumbs.slice(0, i + 1).join("/"))}>{c}</button>
            {i < crumbs.length - 1 && " / "}
          </span>
        ))}
      </div>

      <div className="rounded-lg border bg-card overflow-x-auto">
        <AnimatePresence mode="wait">
          {error ? (
            <motion.div key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex flex-col items-center gap-3 py-10 px-6 text-center">
              <p className="text-sm text-red-500/90 max-w-md break-words">{error}</p>
              <Button size="sm" variant="outline" onClick={() => list(path)} disabled={loading}>Retry</Button>
            </motion.div>
          ) : rows === null || loading ? (
            <motion.div key="loading" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex items-center justify-center gap-2 py-10 text-xs font-mono text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> listing {path}…
            </motion.div>
          ) : rows.length === 0 ? (
            <p key="empty" className="text-xs text-muted-foreground font-mono py-8 text-center">empty directory</p>
          ) : (
            <motion.div key="table" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-16">Type</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead className="w-24 text-right">Size</TableHead>
                    <TableHead className="w-32 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.filter((r) => r.dir).map((r) => (
                    <TableRow key={r.name} className="cursor-pointer" onClick={() => enter(r.name)}>
                      <TableCell><Badge variant="secondary" className="font-mono text-[10px]">dir</Badge></TableCell>
                      <TableCell className="font-mono text-xs text-primary">{r.name}</TableCell>
                      <TableCell />
                      <TableCell />
                    </TableRow>
                  ))}
                  {rows.filter((r) => !r.dir).map((r) => {
                    const full = `${clean}/${r.name}`;
                    const isBusy = busy === full;
                    return (
                      <TableRow key={r.name}>
                        <TableCell><Badge variant="outline" className="font-mono text-[10px]">file</Badge></TableCell>
                        <TableCell className="font-mono text-xs">{r.name}</TableCell>
                        <TableCell className="font-mono text-[11px] text-muted-foreground text-right">{fmtBytes(r.size || 0)}</TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button variant="ghost" size="sm" className="h-7 px-2" disabled={!!busy} onClick={() => download(full)}>
                              {isBusy ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
                            </Button>
                            <Button variant="ghost" size="sm" className="h-7 px-2 text-destructive hover:text-destructive" disabled={!!busy} onClick={() => setDelTarget(full)}>
                              <Trash2 className="size-3.5" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {s && <p className="text-[11px] font-mono text-muted-foreground">{s.tag} · {s.model}</p>}

      <AlertDialog open={!!delTarget} onOpenChange={(o) => !o && setDelTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete file?</AlertDialogTitle>
            <AlertDialogDescription>
              <code className="font-mono text-xs">{delTarget}</code> will be removed from {s?.tag}. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
